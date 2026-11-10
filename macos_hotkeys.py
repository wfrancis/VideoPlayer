"""Register Video Player's remote shortcuts with macOS."""

import ctypes as ct
import logging


LOG = logging.getLogger("video_player")

# Virtual key codes from the macOS Events.h header.
F16 = 0x6A
F17 = 0x40
F18 = 0x4F
HOTKEY_EXCLUSIVE = 1
EVENT_NOT_HANDLED = -9874


def fourcc(value):
    return int.from_bytes(value, "big")


HOTKEY_SIGNATURE = fourcc(b"VPRR")


class EventType(ct.Structure):
    _fields_ = [("event_class", ct.c_uint32), ("event_kind", ct.c_uint32)]


class HotKeyID(ct.Structure):
    _fields_ = [("signature", ct.c_uint32), ("identifier", ct.c_uint32)]


EventHandler = ct.CFUNCTYPE(ct.c_int32, ct.c_void_p, ct.c_void_p, ct.c_void_p)


class GlobalHotkeys:
    def __init__(self, bindings):
        self._carbon = ct.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self._refs = []
        self._handler_ref = ct.c_void_p()
        self._handler = EventHandler(self._handle_event)
        self._actions = {}

        carbon = self._carbon
        carbon.GetApplicationEventTarget.argtypes = []
        carbon.GetApplicationEventTarget.restype = ct.c_void_p
        carbon.InstallEventHandler.argtypes = [
            ct.c_void_p, EventHandler, ct.c_ulong, ct.POINTER(EventType),
            ct.c_void_p, ct.POINTER(ct.c_void_p),
        ]
        carbon.RegisterEventHotKey.argtypes = [
            ct.c_uint32, ct.c_uint32, HotKeyID, ct.c_void_p,
            ct.c_uint32, ct.POINTER(ct.c_void_p),
        ]
        carbon.GetEventParameter.argtypes = [
            ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.POINTER(ct.c_uint32),
            ct.c_ulong, ct.POINTER(ct.c_ulong), ct.c_void_p,
        ]
        carbon.UnregisterEventHotKey.argtypes = [ct.c_void_p]
        carbon.RemoveEventHandler.argtypes = [ct.c_void_p]

        target = carbon.GetApplicationEventTarget()
        # kEventClassKeyboard / kEventHotKeyPressed.
        event_type = EventType(fourcc(b"keyb"), 5)
        status = carbon.InstallEventHandler(
            target, self._handler, 1, ct.byref(event_type), None, ct.byref(self._handler_ref)
        )
        if status:
            raise OSError(f"Could not install the replay remote event handler ({status})")

        try:
            for identifier, (key_code, action) in enumerate(bindings, start=1):
                ref = ct.c_void_p()
                status = carbon.RegisterEventHotKey(
                    key_code, 0, HotKeyID(HOTKEY_SIGNATURE, identifier), target,
                    HOTKEY_EXCLUSIVE, ct.byref(ref),
                )
                if status:
                    raise OSError(f"Could not register a replay remote shortcut ({status})")
                self._refs.append(ref)
                self._actions[identifier] = action
        except OSError:
            self.close()
            raise

    def _handle_event(self, _handler, event, _user_data):
        hotkey = HotKeyID()
        status = self._carbon.GetEventParameter(
            event, fourcc(b"----"), fourcc(b"hkid"), None,
            ct.sizeof(hotkey), None, ct.byref(hotkey),
        )
        if status or hotkey.signature != HOTKEY_SIGNATURE:
            return EVENT_NOT_HANDLED
        action = self._actions.get(hotkey.identifier)
        if action is None:
            return EVENT_NOT_HANDLED
        try:
            action()
        except Exception:
            LOG.exception("Replay remote action failed")
        return 0

    def close(self):
        for ref in self._refs:
            self._carbon.UnregisterEventHotKey(ref)
        self._refs.clear()
        if self._handler_ref:
            self._carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ct.c_void_p()
        self._actions.clear()
