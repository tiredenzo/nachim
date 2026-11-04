"""Download one YouTube video and hand it to Nachim on an authorized phone."""
import json
import argparse
from pathlib import Path
import re
from select import select as wait_readable
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import xml.etree.ElementTree as ET

from captions import caption_metadata_args, download_track
from commands import command
from transport import PACKAGE, select


ORIGINAL_CHECK_VERSION = 2  # Older builds marked even a dub's captions as verified (True).


def report(label, percent=None):
    print(json.dumps({"label": label, "percent": percent}), flush=True)


def video_url(text):
    text = text.strip()
    url = urlsplit(text if "://" in text else "https://" + text)
    video_id = ""
    if url.scheme in ("http", "https"):
        host = url.netloc.lower()
        path = url.path.strip("/").split("/")
        if host == "youtu.be" and len(path) == 1:
            video_id = path[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            if path == ["watch"]:
                video_id = parse_qs(url.query).get("v", [""])[0]
            elif len(path) == 2 and path[0] in ("shorts", "live", "embed"):
                video_id = path[1]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
        raise ValueError("Enter a YouTube video link, such as youtube.com/watch?v=… or youtu.be/…")
    return "https://www.youtube.com/watch?v=" + video_id


def download(url, directory, choice):
    args = ["yt-dlp", "--ignore-config", "--no-playlist", "--no-colors", "--newline",
            "--progress", "--progress-delta", "0.2", "--match-filter", "!is_live",
            "--progress-template", "download:PROGRESS %(progress._percent_str)s",
            "-f", "bv[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a]",
            "--merge-output-format", "mp4", *caption_metadata_args(choice),
            "-o", str(directory / "video.%(ext)s"), "--", url]
    report("Downloading from YouTube…")
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace") as process:
        last_line = ""
        for line in process.stdout:
            line = line.strip()
            if line.startswith("PROGRESS "):
                value = re.search(r"([\d.]+)%", line)
                report("Downloading from YouTube…", float(value[1]) if value else None)
            elif line:
                last_line = line
                if line.startswith("[Merger]"):
                    report("Combining video and audio…")
        if process.wait():
            raise RuntimeError(last_line or "yt-dlp could not download this video.")
    video = directory / "video.mp4"
    if not video.is_file() or not video.stat().st_size:
        raise RuntimeError("No video downloaded. Choose a finished YouTube video.")
    return video


def stream_file(device, file, receiver, progress=False):
    size = file.stat().st_size
    argv = device.shell(receiver, stream=True)
    with file.open("rb") as source, tempfile.TemporaryFile() as log:
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log) as process:
            # Only the initial acknowledgement has a deadline, not the upload.
            if not wait_readable([process.stdout], [], [], 8)[0]:
                process.kill()
                raise RuntimeError("The phone did not acknowledge the transfer. Reconnect and retry.")
            ready = process.stdout.read(1) == b"\x06"
            if not ready and process.poll() is None:
                process.kill()
            sent, last_percent = 0, -1
            try:
                if ready:
                    while chunk := source.read(256 * 1024):
                        process.stdin.write(chunk)
                        sent += len(chunk)
                        percent = min(99, 100 * sent // size)
                        if progress and percent != last_percent:
                            report("Transferring to the phone…", percent)
                            last_percent = percent
                process.stdin.close()
            except BrokenPipeError:
                pass
            reply = process.stdout.read().decode(errors="replace").strip()
            if process.wait() or not ready or sent != size:
                log.seek(0)
                reason = log.read().decode(errors="replace").strip()
                raise RuntimeError(reason or reply or "Transfer to the phone was interrupted.")
    if progress:
        report("Transferred to the phone", 100)
    return reply


def transfer(device, file, folder, name=None, progress=False):
    # Even the movie must start in the original upload's private UUID directory.
    script = (
        f"set -e; umask 077; cd {folder}; part={uuid.uuid4().hex}.part; "
        """trap 'rm -f "$part"' EXIT; exec 3> "$part"; printf '\\006'; cat >&3; """
        f'[ "$(wc -c < "$part")" -eq {file.stat().st_size} ]; mv "$part" {shlex.quote(name or file.name)}'
    )
    stream_file(device, file, f"run-as {PACKAGE} sh -c " + shlex.quote(script), progress)


def start_activity(device, activity, *extras, ready_file=None):
    launch = shlex.join(["am", "start", "-W", "-n", f"{PACKAGE}/.{activity}", *extras])
    if ready_file:
        launch = f"run-as {PACKAGE} test -f {ready_file} && " + launch
    output = command(device.shell(launch))
    # Android can report a failed launch even when am exits successfully.
    if "Error" in output or "Exception" in output or "Status: timeout" in output:
        raise RuntimeError(output)


def connect(apply):
    device = select(reconnect=True)
    report("Connected via " + device.label)
    package = command(device.shell("dumpsys package " + PACKAGE))
    version = re.search(r"\bversionCode=(\d+)", package)
    if not version or int(version[1]) < 13:
        report("Installing the updated Nachim on the phone…")
        was_open = version and apply and PACKAGE in next(
            (line for line in command(device.shell("dumpsys activity activities")).splitlines()
             if "topResumedActivity=" in line or "mResumedActivity:" in line), "")
        apk = Path(__file__).with_name("player.apk")
        result = stream_file(device, apk, f"printf '\\006'; exec pm install -r -S {apk.stat().st_size}")
        if "Success" not in result.splitlines():
            raise RuntimeError(result or "Nachim installation failed.")
        if was_open:
            start_activity(device, "PlayerActivity")
    return device


def private(device, script):
    return command(device.shell(f"run-as {PACKAGE} sh -c " + shlex.quote(script)), stdout_only=True)


def settings(device, folder, current, choice, size, tmp, metadata=None):
    updated = dict(current, enabled=choice != "off", size=size)
    recheck_original = choice == "original" and current.get("original_verified") != ORIGINAL_CHECK_VERSION
    if choice != "off" and (choice != current.get("choice") or recheck_original):
        source = current.get("source")
        if not source:
            raise ValueError("Paste the original YouTube link for this video, then apply captions.")
        report("Fetching YouTube captions…")
        track = download_track(video_url(source), choice, tmp, metadata)
        transfer(device, track, folder)
        updated.update(choice=choice, track=track.stem, original_verified=ORIGINAL_CHECK_VERSION if choice == "original" else 0)
    file = tmp / "settings.json"
    file.write_text(json.dumps(updated))
    transfer(device, file, folder)
    return updated


def current_video_id(device):
    # Android briefly replaces playback.xml while saving the position.
    for attempt in range(2):
        if attempt:
            time.sleep(0.1)
        try:
            playback = private(device, "if [ -f shared_prefs/playback.xml ]; then cat shared_prefs/playback.xml; fi")
            if playback:
                token = ET.fromstring(playback).findtext("string[@name='id']")
                if re.fullmatch(r"[0-9a-f]{32}", token or ""):
                    return token
                break
        except (ET.ParseError, RuntimeError):
            if attempt:
                raise
    raise ValueError("There is no current video on the phone.")


def apply_current(device, link, choice, size, tmp):
    token = current_video_id(device)
    folder = Path("files") / ("cc-" + token)
    raw = private(device, f"test -f files/{token}.mp4 && mkdir -p {folder} && "
                  f"if [ -f {folder}/settings.json ]; then cat {folder}/settings.json; fi")
    current = json.loads(raw) if raw else {}
    if choice != "off" and not current.get("source") and link.strip():
        current["source"] = video_url(link)
    updated = settings(device, folder, current, choice, size, tmp)
    report(f"Captions applied · {updated['track']} · {size} sp" if updated["enabled"]
           else "Captions turned off on the phone.")


def send(device, link, choice, size, tmp):
    url = video_url(link)
    token = uuid.uuid4().hex
    folder = Path("cache") / ("cc-" + token)
    movie = Path("cache") / ("ready-" + token + ".mp4")
    private(device, f"mkdir -p cache; rm -rf cache/cc-*; rm -f cache/ready-*.mp4; mkdir {folder}")
    try:
        video = download(url, tmp, choice)
        # Fetch captions after the video finishes, using the track URLs from that download.
        settings(device, folder, {"source": url}, choice, size, tmp, metadata=video.with_suffix(".info.json"))
        transfer(device, video, folder, "../" + movie.name, progress=True)
        video.unlink()
        report("Opening Nachim on the phone…")
        start_activity(device, "Receive", "--es", "id", token, ready_file=movie)
    except Exception:
        try:
            private(device, f"rm -rf {folder} {movie}")
        except (OSError, ValueError, RuntimeError):
            pass  # A lost route is cleaned up on the next send to that phone.
        raise
    report("Sent. Nachim opened on your phone.", 100)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("link")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--captions", default="off")
    parser.add_argument("--size", type=int, default=22, choices=range(12, 49))
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,48}", args.captions):
            raise ValueError("Enter a YouTube language code such as en or fr-CA.")
        if not args.apply:
            video_url(args.link)
        for name in (("adb",) if args.apply else ("adb", "yt-dlp", "ffmpeg")):
            if not shutil.which(name):
                raise RuntimeError(f"Missing {name}. See the project README for installation.")
        device = connect(args.apply)
        with tempfile.TemporaryDirectory(prefix="usb-player-") as tmp:
            action = apply_current if args.apply else send
            action(device, args.link, args.captions, args.size, Path(tmp))
    except (ValueError, OSError, RuntimeError, ET.ParseError) as error:
        report(str(error))
        sys.exit(1)
