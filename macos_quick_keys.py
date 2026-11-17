"""Read Xencelabs Quick Keys over USB or its wireless receiver."""

import ctypes as ct
import logging
import plistlib
from time import monotonic

from PyQt6.QtCore import QTimer

from quick_keys_profile import driver_uses_replay, replay_reports


LOG = logging.getLogger("video_player")
VENDOR_ID = 0x28BD
PRODUCT_IDS = (0x5202, 0x5203)
USAGE_PAGE = 0xFF0A
USAGE = 1
REPORT_ID = 0x02
KEY_REPORT = 0xF0
SEIZE_DEVICE = 1
# The center press is bit 1 of the second button byte.
PLAY_PAUSE_BUTTON = 0x200
# Side buttons with the dial at the top, in rows from the dial down.
SKIP_BACK_BUTTON = 0x08
SKIP_FORWARD_BUTTON = 0x80
ZOOM_OUT_BUTTON = 0x04
ZOOM_IN_BUTTON = 0x40
PAN_LEFT_BUTTON = 0x02
PAN_RIGHT_BUTTON = 0x20
PAN_DOWN_BUTTON = 0x01
PAN_UP_BUTTON = 0x10

DeviceCallback = ct.CFUNCTYPE(None, ct.c_void_p, ct.c_int32, ct.c_void_p, ct.c_void_p)
ReportCallback = ct.CFUNCTYPE(
    None, ct.c_void_p, ct.c_int32, ct.c_void_p, ct.c_int32,
    ct.c_uint32, ct.POINTER(ct.c_uint8), ct.c_long,
)


class QuickKeys:
    """Receive dial steps and button presses on the main run loop."""

    def __init__(self, jog, play_pause, zoom, pan, skip):
        self._jog = jog
        self._button_actions = {
            PAN_LEFT_BUTTON: lambda: pan(-1, 0),
            PAN_RIGHT_BUTTON: lambda: pan(1, 0),
            ZOOM_OUT_BUTTON: lambda: zoom(-1),
            ZOOM_IN_BUTTON: lambda: zoom(1),
            PAN_UP_BUTTON: lambda: pan(0, -1),
            PAN_DOWN_BUTTON: lambda: pan(0, 1),
            PLAY_PAUSE_BUTTON: play_pause,
            SKIP_BACK_BUTTON: lambda: skip(-5000),
            SKIP_FORWARD_BUTTON: lambda: skip(5000),
        }
        self._devices = set()
        self._held_buttons = {}
        self._last_input = {}
        self._setup_reports = {}
        self._setup_timer = QTimer()
        self._setup_timer.setInterval(12)
        self._setup_timer.timeout.connect(self._send_setup_report)
        self._matched = DeviceCallback(self._device_matched)
        self._removed = DeviceCallback(self._device_removed)
        self._reported = ReportCallback(self._handle_report)
        self._manager = None
        self._scheduled = False
        self._open_options = 0 if driver_uses_replay() else SEIZE_DEVICE

        self._cf = cf = ct.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self._hid = hid = ct.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        ptr = ct.c_void_p
        options = ct.c_uint32
        cf.CFRunLoopGetMain.argtypes = []
        cf.CFRunLoopGetMain.restype = ptr
        cf.CFDataCreate.argtypes = [ptr, ct.c_char_p, ct.c_long]
        cf.CFDataCreate.restype = ptr
        cf.CFPropertyListCreateWithData.argtypes = [ptr, ptr, ct.c_ulong, ptr, ptr]
        cf.CFPropertyListCreateWithData.restype = ptr
        cf.CFRelease.argtypes = [ptr]
        cf.CFRelease.restype = None
        hid.IOHIDManagerCreate.argtypes = [ptr, options]
        hid.IOHIDManagerCreate.restype = ptr
        hid.IOHIDManagerSetDeviceMatchingMultiple.argtypes = [ptr, ptr]
        hid.IOHIDManagerSetDeviceMatchingMultiple.restype = None
        hid.IOHIDManagerRegisterDeviceMatchingCallback.argtypes = [ptr, DeviceCallback, ptr]
        hid.IOHIDManagerRegisterDeviceMatchingCallback.restype = None
        hid.IOHIDManagerRegisterDeviceRemovalCallback.argtypes = [ptr, DeviceCallback, ptr]
        hid.IOHIDManagerRegisterDeviceRemovalCallback.restype = None
        hid.IOHIDManagerRegisterInputReportCallback.argtypes = [ptr, ReportCallback, ptr]
        hid.IOHIDManagerRegisterInputReportCallback.restype = None
        hid.IOHIDManagerScheduleWithRunLoop.argtypes = [ptr, ptr, ptr]
        hid.IOHIDManagerScheduleWithRunLoop.restype = None
        hid.IOHIDManagerUnscheduleFromRunLoop.argtypes = [ptr, ptr, ptr]
        hid.IOHIDManagerUnscheduleFromRunLoop.restype = None
        hid.IOHIDManagerOpen.argtypes = [ptr, options]
        hid.IOHIDManagerOpen.restype = ct.c_int32
        hid.IOHIDManagerClose.argtypes = [ptr, options]
        hid.IOHIDManagerClose.restype = ct.c_int32
        hid.IOHIDDeviceSetReport.argtypes = [
            ptr, ct.c_int32, ct.c_uint32, ct.POINTER(ct.c_uint8), ct.c_long,
        ]
        hid.IOHIDDeviceSetReport.restype = ct.c_int32

        self._run_loop = cf.CFRunLoopGetMain()
        self._mode = ptr.in_dll(cf, "kCFRunLoopCommonModes")
        self._manager = hid.IOHIDManagerCreate(None, 0)
        if not self._manager:
            raise OSError("Could not create a Quick Keys HID manager")

        try:
            self._set_matching()
            hid.IOHIDManagerRegisterDeviceMatchingCallback(self._manager, self._matched, None)
            hid.IOHIDManagerRegisterDeviceRemovalCallback(self._manager, self._removed, None)
            hid.IOHIDManagerRegisterInputReportCallback(self._manager, self._reported, None)
            hid.IOHIDManagerScheduleWithRunLoop(self._manager, self._run_loop, self._mode)
            self._scheduled = True
            # Share a compatible Replay profile with Xencelabs Settings.
            # Conflicting profiles need exclusive access to prevent their
            # shortcuts from affecting the foreground stats application.
            status = hid.IOHIDManagerOpen(self._manager, self._open_options)
            if status:
                raise OSError(f"Could not open Quick Keys HID manager (0x{status & 0xffffffff:08x})")
        except Exception:
            self.close()
            raise

    def _set_matching(self):
        matching = plistlib.dumps([{
            "VendorID": VENDOR_ID,
            "ProductID": product_id,
            "PrimaryUsagePage": USAGE_PAGE,
            "PrimaryUsage": USAGE,
        } for product_id in PRODUCT_IDS], fmt=plistlib.FMT_BINARY)
        data = self._cf.CFDataCreate(None, matching, len(matching))
        if not data:
            raise OSError("Could not encode Quick Keys HID matching properties")
        try:
            devices = self._cf.CFPropertyListCreateWithData(None, data, 0, None, None)
        finally:
            self._cf.CFRelease(data)
        if not devices:
            raise OSError("Could not create Quick Keys HID matching properties")
        try:
            self._hid.IOHIDManagerSetDeviceMatchingMultiple(self._manager, devices)
        finally:
            self._cf.CFRelease(devices)

    @property
    def connected(self):
        return bool(self._devices)

    def _device_matched(self, _context, status, _sender, device):
        if self._manager and status == 0 and device:
            self._devices.add(device)
            self._prepare_device(device)
            LOG.debug("Quick Keys USB interface connected")

    def _prepare_device(self, device):
        self._setup_reports[device] = iter(replay_reports())
        self._setup_timer.start()

    def _send_setup_report(self):
        for device, reports in tuple(self._setup_reports.items()):
            if device not in self._devices:
                self._setup_reports.pop(device, None)
                continue
            packet = next(reports, None)
            if packet is None:
                self._setup_reports.pop(device, None)
                continue
            data = (ct.c_uint8 * len(packet)).from_buffer_copy(packet)
            result = self._hid.IOHIDDeviceSetReport(device, 1, REPORT_ID, data, len(packet))
            if result:
                LOG.warning("Quick Keys setup report failed (0x%08x)", result & 0xffffffff)
                self._setup_reports.pop(device, None)
        if not self._setup_reports:
            self._setup_timer.stop()

    def _device_removed(self, _context, _status, _sender, device):
        self._devices.discard(device)
        self._held_buttons.pop(device, None)
        self._last_input.pop(device, None)
        self._setup_reports.pop(device, None)
        LOG.debug("Quick Keys USB interface disconnected")

    def _handle_report(self, _context, status, sender, _type, report_id, report, length):
        if not self._manager or status or report_id != REPORT_ID or not report or length < 7:
            return
        offset = 1 if report[0] == REPORT_ID else 0
        if length <= offset + 6 or report[offset] != KEY_REPORT:
            return

        try:
            now = monotonic()
            previous_input = self._last_input.get(sender)
            self._last_input[sender] = now
            if previous_input is None or now - previous_input > 30:
                self._held_buttons.pop(sender, None)
                self._prepare_device(sender)
            wheel = report[offset + 6]
            if wheel & 1:
                self._jog(1)
            elif wheel & 2:
                self._jog(-1)
            elif wheel == 0:
                buttons = report[offset + 1] | (report[offset + 2] << 8)
                pressed = buttons & ~self._held_buttons.get(sender, 0)
                self._held_buttons[sender] = buttons
                for bit, action in self._button_actions.items():
                    if pressed & bit:
                        action()
        except Exception:
            LOG.exception("Quick Keys replay action failed")

    def close(self):
        self._setup_timer.stop()
        self._setup_reports.clear()
        manager, self._manager = self._manager, None
        if manager:
            if self._scheduled:
                self._hid.IOHIDManagerUnscheduleFromRunLoop(manager, self._run_loop, self._mode)
                self._scheduled = False
            self._hid.IOHIDManagerClose(manager, self._open_options)
            self._cf.CFRelease(manager)
        self._devices.clear()
        self._held_buttons.clear()
        self._last_input.clear()
