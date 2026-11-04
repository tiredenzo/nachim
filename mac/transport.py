"""Select an authorized USB phone or its explicitly configured hotspot connection."""
import argparse
from dataclasses import dataclass, replace
import errno
from ipaddress import IPv4Address
import json
from pathlib import Path
import re
import shlex
import socket
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time

from commands import command

PACKAGE = "dev.usbvideo.player"
SETTINGS = Path.home() / "Library/Application Support" / "dev.usbvideo.controller" / "hotspot.json"
SETUP_HINT = "Plug in your unlocked phone and allow USB debugging. For Wi-Fi, join its hotspot on the Mac and click Set up hotspot."
RECONNECT_HINT = "Join your phone’s hotspot on the Mac and unlock the phone. If it restarted, plug in USB and click Set up hotspot again."


@dataclass(frozen=True)
class Device:
    serial: str
    transport: int
    model: str
    usb: bool
    hardware: str = ""  # USB hardware serial expected on this hotspot connection.

    def route(self):
        current = devices().get(self.serial)
        if not current or current.transport != self.transport:
            raise ValueError("The phone connection changed. Retry the operation.")
        # A restarted ADB server can assign the old transport number to another route.
        return ["adb", "-s", self.serial]

    def shell(self, script, *, stream=False):
        if self.hardware:
            # Check on the same connection that will actually run the command.
            script = (f'[ "$(getprop ro.serialno)" = {shlex.quote(self.hardware)} ] || '
                      '{ echo "The hotspot phone changed." >&2; exit 1; }; ' + script)
        return [*self.route(), "shell", *(["-T", "-e", "none"] if stream else []), script]

    @property
    def label(self):
        return f"{'USB' if self.usb else 'Hotspot'} · {self.model}"


def devices():
    result = {}
    for line in command(["adb", "devices", "-l"], stdout_only=True, timeout=5).splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1] != "device":
            continue
        tags = dict(f.split(":", 1) for f in fields[2:] if ":" in f)
        if tags.get("transport_id", "").isdigit():
            serial = fields[0]
            result[serial] = Device(serial, int(tags["transport_id"]), tags.get("model", serial), "usb" in tags)
    return result


def usb_phone(online):
    usb = [d for d in online.values() if d.usb]
    if len(usb) > 1:
        raise ValueError("More than one USB phone is connected. Leave only the intended phone plugged in.")
    if usb:
        return usb[0]
    # Some ADB backends omit the USB path in devices -l.
    if online:
        try:
            serial = command(["adb", "-d", "get-serialno"], stdout_only=True, timeout=5)
        except RuntimeError as error:
            if "more than one" in str(error).lower():
                raise ValueError("Leave only one USB phone connected.") from error
        else:
            if serial in online:
                d = online[serial]
                return Device(d.serial, d.transport, d.model, True)
    return None


def identity(device):
    serial = command([*device.route(), "shell", "getprop", "ro.serialno"], stdout_only=True, timeout=5)
    if not serial or serial.lower() == "unknown":
        raise ValueError("Could not identify the phone. Reconnect and authorize it over USB.")
    return serial


def saved():
    try:
        return json.loads(SETTINGS.read_text())
    except FileNotFoundError:
        return {}


def save(serial, endpoint=None):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=SETTINGS.parent, delete=False) as temp:
        json.dump({"serial": serial, "endpoint": endpoint}, temp)
    Path(temp.name).replace(SETTINGS)


def verified(device, serial):
    if not device or identity(device) != serial:
        raise ValueError("This connection does not match the configured phone. Join its hotspot or plug it in over USB.")
    return replace(device, hardware=serial)


def select(*, reconnect=False):
    online = devices()
    usb = usb_phone(online)
    if usb:
        return usb
    config = saved()
    if not config.get("serial"):
        raise ValueError(SETUP_HINT)
    if reconnect:
        return connect_hotspot(config, wifi_router() + ":5555", online)
    wifi = online.get(config.get("endpoint"))
    if not wifi:
        raise ValueError("Join the configured phone’s hotspot, then click Send to reconnect, or plug in USB.")
    return replace(wifi, hardware=config["serial"])  # Device.shell checks it before the broadcast.


def wifi_router():
    ports = command(["/usr/sbin/networksetup", "-listallhardwareports"])
    match = re.search(r"^Hardware Port: (?:Wi-Fi|AirPort)\nDevice: (\S+)", ports, re.M)
    if match:
        try:
            return str(IPv4Address(command(["/usr/sbin/ipconfig", "getoption", match[1], "router"])))
        except (RuntimeError, ValueError):
            pass
    raise ValueError("Join the phone’s hotspot in the Mac’s Wi-Fi menu, then try again.")


def connect_hotspot(config, endpoint, online, *, starting=False):
    wifi = online.get(endpoint)
    if not wifi:
        # tcpip restarts adbd. Give the listener a short startup window during setup.
        deadline = time.monotonic() + (4 if starting else 0)
        try:
            while True:
                reply = command(["adb", "connect", endpoint], timeout=6)
                wifi = devices().get(endpoint)
                if not wifi and "connected to " in reply.lower():
                    command(["adb", "-s", endpoint, "wait-for-device"], timeout=5)
                    wifi = devices().get(endpoint)
                if wifi or time.monotonic() >= deadline:
                    break
                time.sleep(0.4)
        except RuntimeError as error:
            raise ValueError(f"{RECONNECT_HINT} {error}") from error
        if not wifi:
            raise ValueError(f"{RECONNECT_HINT} {reply}")
    try:
        device = verified(wifi, config["serial"])
    except (ValueError, RuntimeError):
        if endpoint not in online:
            command(["adb", "disconnect", endpoint], timeout=5)
        raise
    if config.get("endpoint") != endpoint:
        save(device.hardware, endpoint)
    return device


def turn_off(device, serial):
    adb = device.route()
    if device.usb:
        command([*adb, "usb"], timeout=8)
        return
    # ADB binds a forward to one live transport and removes it on disconnect.
    # Connecting to this private socket invokes the same adbd service as "adb usb".
    with TemporaryDirectory(prefix="usb-player-", dir="/tmp") as directory:
        path = str(Path(directory) / "off")
        local = "localfilesystem:" + path
        command([*adb, "forward", "--no-rebind", local, "usb:"], timeout=5)
        try:
            verified(device, serial)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(8)
                client.connect(path)
                with client.makefile("rb") as reply:
                    if reply.readline(128).strip() != b"restarting in USB mode":
                        raise ValueError("Could not confirm Turn Off. Reconnect the configured phone and retry.")
        finally:
            try:
                command([*adb, "forward", "--remove", local], timeout=5)
            except (OSError, RuntimeError):
                pass  # A successful shutdown can already have removed the transport and forward.


def hotspot(action):
    online = devices()
    config = saved()
    if action == "setup":
        usb = usb_phone(online)
        if not usb:
            raise ValueError("Plug in the phone, unlock it, and allow USB debugging on this Mac first.")
        serial = identity(usb)
        if config and config.get("serial") != serial:
            raise ValueError("Turn Off hotspot debugging on the configured phone before enabling another phone.")
        router = wifi_router()
        endpoint = router + ":5555"
        if endpoint not in online:
            addresses = command(usb.shell("ip -4 addr show"), stdout_only=True, timeout=5)
            if router not in re.findall(r"\binet\s+([\d.]+)/", addresses):
                raise ValueError("Turn on Mobile Hotspot on this phone and join it from the Mac before running setup.")
            enable_listener(usb, serial, config)
        wifi = connect_hotspot(config or {"serial": serial}, endpoint, online, starting=True)
        return f"Hotspot verified · {wifi.model}. You can unplug USB."
    if not config.get("serial"):
        raise ValueError("Hotspot debugging has not been set up on this Mac.")
    device = usb_phone(online)
    if not device or identity(device) != config["serial"]:
        device = connect_hotspot(config, wifi_router() + ":5555", online)
    turn_off(device, config["serial"])
    SETTINGS.unlink()
    return "Hotspot debugging turned off. Connect by USB to continue."


def enable_listener(usb, serial, config):
    adb = usb.route()
    if not config:
        # Record which phone to turn off even if tcpip opens the port but loses its reply.
        save(serial)
    try:
        command([*adb, "tcpip", "5555"], timeout=8)
    except (OSError, RuntimeError) as error:
        # ADB reports selector refusals before dispatching tcpip. A lost
        # reply, on the other hand, may mean the listener is already open.
        reason = str(error).partition("\n")[0].removeprefix("adb: ").removeprefix("error: ").rstrip(".")
        refused = reason in {f"device '{usb.serial}' not found", "device offline",
                             "device unauthorized", "device still authorizing"}
        cannot_start = isinstance(error, OSError) and error.errno in (
            errno.ENOENT, errno.EACCES, errno.ENOEXEC, errno.ENOTDIR, errno.EPERM)
        if not config and (cannot_start or refused):
            SETTINGS.unlink()
        raise


def timeline(args):
    device = select()
    broadcast = ["am", "broadcast", "--user", "current", "--receiver-registered-only",
                 "-a", PACKAGE + ".TIMELINE", "-p", PACKAGE]
    if args.action != "status":
        if ((device.serial, device.transport) != (args.route, args.transport)
                or not re.fullmatch(r"[0-9a-f]{32}", args.video or "")):
            raise ValueError("The phone connection changed. Use the refreshed timeline.")
        broadcast += ["--es", "command", args.action, "--es", "id", args.video]
        if args.action == "seek":
            if args.position is None or args.position < 0:
                raise ValueError("Invalid seek position.")
            broadcast += ["--ei", "position", str(args.position)]
    output = command(device.shell(shlex.join(broadcast)), timeout=5)
    match = re.search(r'result=-1, data="([0-9a-f]{32}) (\d+) (\d+) ([01])"', output)
    playback = None
    if match and int(match[3]) > 0:
        duration = int(match[3])
        playback = dict(target=dict(id=match[1], transport=device.transport, route=device.serial), position=min(int(match[2]), duration),
                        duration=duration, playing=match[4] == "1")
    return json.dumps(dict(connection=device.label, playback=playback))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "disable", "status", "play", "pause", "seek"])
    parser.add_argument("--transport", type=int)
    parser.add_argument("--route")
    parser.add_argument("--video")
    parser.add_argument("--position", type=int)
    args = parser.parse_args()
    try:
        print(hotspot(args.action) if args.action in ("setup", "disable") else timeline(args))
    except (OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
