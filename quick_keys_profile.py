"""Describe the Video Player controls for Xencelabs Quick Keys."""

from pathlib import Path
import xml.etree.ElementTree as ET


REPORT_ID = 0x02
REPORT_SIZE = 32

# Physical key order is K1–K4 on one side and K5–K8 on the other.
# With the dial at the top, K4/K8 are nearest the dial and K1/K5 are lowest.
REPLAY_LABELS = (
    "PanDown", "PanLeft", "Zoom Out", "-5s",
    "PanUp", "PanRight", "Zoom In", "+5s",
)

DRIVER_SHORTCUTS = (
    ("Shift+Option+F19", "16777248:56+16777251:58+16777282:80"),
    ("Shift+Option+F16", "16777248:56+16777251:58+16777279:106"),
    ("F19", "16777282:80"),
    ("Shift+F19", "16777248:56+16777282:80"),
    ("Shift+Option+F18", "16777248:56+16777251:58+16777281:79"),
    ("Shift+Option+F17", "16777248:56+16777251:58+16777280:64"),
    ("F20", "16777283:90"),
    ("Shift+F20", "16777248:56+16777283:90"),
)


def driver_uses_replay(path=None):
    """Whether shared access can coexist with the installed driver profile.

    A driver with the same shortcuts can share the remote because Video Player
    suppresses its duplicate shortcuts. Conflicting or unsupported profiles
    require exclusive access to keep their actions out of the foreground app.
    """
    config = Path(path) if path is not None else Path.home() / ".Xencelabs/config.xml"
    try:
        root = ET.fromstring(config.read_bytes())
    except FileNotFoundError:
        return True
    except (OSError, ET.ParseError):
        return False

    remotes = root.findall("./PenTableLists/*/Remote")
    if not remotes:
        return False
    for remote in remotes:
        apps = remote.find("Apps")
        if apps is not None and len(apps):
            return False
        profile = remote.find("CommonAPP")
        if profile is None:
            return False
        sets = profile.findall("./K/Set")
        if any(keys.get("enable") not in ("0", "1") for keys in sets):
            return False
        enabled = [keys for keys in sets if keys.get("enable") == "1"]
        if len(enabled) != 1 or enabled[0].get("id") != "0":
            return False
        keys = enabled[0]
        if {button.tag for button in keys} != {f"K{i}" for i in range(1, 9)}:
            return False
        for index, expected in enumerate(DRIVER_SHORTCUTS, start=1):
            buttons = keys.findall(f"K{index}")
            if len(buttons) != 1:
                return False
            button = buttons[0]
            parts = (button.text or "").split("|")
            if (
                button.get("id") != "2" or button.get("Actid") != "2"
                or len(parts) != 4 or parts[0] != "2"
                or tuple(parts[2:]) != expected
            ):
                return False

        dial = profile.find("R")
        if dial is None or dial.get("wid") != "1":
            return False
        modes = list(dial)
        if any(mode.get("enable") not in ("0", "1") for mode in modes):
            return False
        active_modes = [mode for mode in modes if mode.get("enable") == "1"]
        if len(active_modes) != 1 or active_modes[0].tag != "W1":
            return False
        first = active_modes[0]
        parts = (first.text or "").split("|")
        if (
            first.get("id") != "4" or first.get("speed") != "3"
            or len(parts) != 6 or parts[0] != "4"
            or parts[2:] != ["F16", "F18", "16777279:106", "16777281:79"]
        ):
            return False
    return True


def replay_reports():
    """Return initialization reports for the built-in native remote controls.

    The key event subscription is also needed after a receiver or cable
    reconnect. Labels fit the device's eight UTF-16 character fields.
    """
    reports = [
        bytes((REPORT_ID, 0xB0, 0x04)).ljust(REPORT_SIZE, b"\0"),
        bytes((REPORT_ID, 0xB4, 0x04, 0x01, 0x01, 0x03)).ljust(REPORT_SIZE, b"\0"),
    ]
    for key, label in enumerate(REPLAY_LABELS, start=1):
        text = label.encode("utf-16-le")
        packet = bytearray(REPORT_SIZE)
        packet[:6] = bytes((REPORT_ID, 0xB1, 0x00, key, 0x00, len(text)))
        packet[16:16 + len(text)] = text
        reports.append(bytes(packet))
    return tuple(reports)
