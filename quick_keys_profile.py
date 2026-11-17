"""Describe the Video Player controls for Xencelabs Quick Keys."""

REPORT_ID = 0x02
REPORT_SIZE = 32

# Physical key order is K1–K4 on one side and K5–K8 on the other.
# With the dial at the top, K4/K8 are nearest the dial and K1/K5 are lowest.
REPLAY_LABELS = (
    "PanDown", "PanLeft", "Zoom Out", "-5s",
    "PanUp", "PanRight", "Zoom In", "+5s",
)


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
