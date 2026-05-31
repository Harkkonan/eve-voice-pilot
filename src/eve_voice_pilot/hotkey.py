from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
from typing import Callable

from .input_sender import parse_key_chord


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 4401

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def hotkey_to_register_args(hotkey: str) -> tuple[int, int]:
    parsed = parse_key_chord(hotkey)
    modifiers = MOD_NOREPEAT
    for modifier in parsed.modifiers:
        if modifier == "ALT":
            modifiers |= MOD_ALT
        elif modifier == "CTRL":
            modifiers |= MOD_CONTROL
        elif modifier == "SHIFT":
            modifiers |= MOD_SHIFT
        elif modifier == "WIN":
            modifiers |= MOD_WIN
    return modifiers, parsed.key_vk


class GlobalHotkey:
    def __init__(self, hotkey: str, callback: Callable[[], None], on_error: Callable[[str], None]):
        self.hotkey = hotkey
        self.callback = callback
        self.on_error = on_error
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._stop = threading.Event()

    def start(self) -> None:
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hotkey-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=1.5)
        self._thread = None
        self._thread_id = 0

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            modifiers, vk = hotkey_to_register_args(self.hotkey)
        except ValueError as exc:
            self.on_error(str(exc))
            return

        if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, vk):
            self.on_error(f"Could not register hotkey {self.hotkey}. Try a different key.")
            return

        try:
            msg = MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

