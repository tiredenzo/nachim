"""Select a YouTube caption track and turn its timed phrases into plain text cues."""
import html
import json
import re

from commands import command


def without_region(tag):
    base, _, region = tag.rpartition("-")
    return base if re.fullmatch(r"[a-z]{2}|[0-9]{3}", region) else ""


def supported_tracks(tracks):
    for key, formats in tracks.items():
        if re.fullmatch(r"[A-Za-z0-9-]{1,48}", key):
            for fmt in formats:
                if fmt.get("ext") in ("srt", "vtt"):
                    yield key, fmt.get("name") or ""
                    break


def match_language(tracks, language):
    if language in tracks:
        return tracks[language]
    base = without_region(language)
    matches = [track for tag, track in tracks.items() if tag == base or without_region(tag) == language]
    return matches[0] if len(matches) == 1 else None


def original_track(info, manual, auto):
    native = {tag[:-5]: key for tag, key in auto.items() if tag.endswith("-orig")}
    # Each dubbed audio can have its own "-orig" transcript. yt-dlp marks the
    # original audio formats with preference 10, distinct from the default (5).
    audio = [fmt for fmt in info.get("formats", []) if fmt.get("language") and fmt.get("acodec") != "none"]
    marked = {fmt["language"].lower() for fmt in audio if fmt.get("language_preference") == 10}
    if len(marked) > 1:
        return None
    if marked:
        language = marked.pop()
    elif len({fmt["language"].lower() for fmt in audio}) > 1:
        return None
    elif len(native) == 1:
        language = next(iter(native))
    elif native:
        return None
    else:
        language = (info.get("language") or "").lower()
        if not language:
            return next(iter(manual.values())) if len(manual) == 1 else None

    track = match_language(manual, language) or match_language(native, language)
    if track:
        return track
    # A plain automatic track can be a translation (tlang in its URL).
    plain = auto.get(language)
    if plain and not any(re.search(r"[?&]tlang=", fmt.get("url", ""))
                         for fmt in info["automatic_captions"][plain]):
        return plain
    return None


def choose_track(info, choice):
    manual_names = dict(supported_tracks(info.get("subtitles") or {}))
    manual = {key.lower(): key for key in manual_names}
    auto, translated = {}, {}
    for key, name in supported_tracks(info.get("automatic_captions") or {}):
        # yt-dlp labels creator-track translations "<target> from <source>"
        # and appends the creator track ID to the target language code.
        source = next((s for s, label in manual_names.items()
                       if key.endswith("-" + s) and name.endswith(" from " + label)), None)
        if source:
            target = key[:-(len(source) + 1)].lower()
            translated.setdefault(target, key)
        else:
            auto[key.lower()] = key
    if choice == "original":
        return original_track(info, manual, auto)
    language = choice.lower()
    return manual.get(language) or auto.get(language + "-orig") or auto.get(language) or translated.get(language)


def milliseconds(stamp):
    hours, minutes, seconds, ms = map(int, re.split("[:,]", stamp))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + ms


def caption_metadata_args(choice):
    if choice == "off":
        return []
    # List translations for explicit language choices, without downloading any subtitles yet.
    discover = [] if choice == "original" else ["--write-auto-subs", "--sub-langs=-all"]
    return ["--write-info-json", *discover]


def download_track(url, choice, directory, metadata=None):
    args = ["yt-dlp", "--ignore-config", "--no-playlist", "--skip-download", "--no-progress"]
    if metadata is None:  # Standalone Apply has no video download to supply the caption URLs.
        command([*args, *caption_metadata_args(choice), "-o", str(directory / "info.%(ext)s"), "--", url],
                last_error_line=True)
        metadata = directory / "info.info.json"
    track = choose_track(json.loads(metadata.read_text()), choice)
    if not track:
        raise ValueError(f"No {choice} YouTube caption track is available for this video.")
    command([*args, "--load-info-json", str(metadata), "--write-subs", "--write-auto-subs",
             "--sub-langs", re.escape(track), "--sub-format", "srt/vtt", "--convert-subs", "srt",
             "-o", str(directory / "caption.%(ext)s")], last_error_line=True)
    data = (directory / f"caption.{track}.srt").read_text(encoding="utf-8-sig")
    cues = []
    for block in re.split(r"\n\s*\n", data.strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        times = re.fullmatch(r"(\d+:\d{2}:\d{2},\d{3}) --> (\d+:\d{2}:\d{2},\d{3})", lines[1])
        if times:
            text = re.sub(r"</?(?:b|i|u|font)(?:\s[^<>]*)?>", "", "\n".join(lines[2:]), flags=re.I)
            cues.append([milliseconds(times[1]), milliseconds(times[2]), html.unescape(text)])
    if not cues:
        raise ValueError("YouTube returned an empty caption track.")
    cues.sort(key=lambda cue: cue[0])
    path = directory / (track + ".json")
    path.write_text(json.dumps(cues, ensure_ascii=False, separators=(",", ":")))
    return path
