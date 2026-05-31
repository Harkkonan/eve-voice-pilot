from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import time


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

MODIFIER_VKS = {
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "WIN": 0x5B,
}

VK_CODES = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
}

for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_CODES[char] = ord(char)
for char in "0123456789":
    VK_CODES[char] = ord(char)
for index in range(1, 25):
    VK_CODES[f"F{index}"] = 0x70 + index - 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


user32 = ctypes.WinDLL("user32", use_last_error=True)


@dataclass(frozen=True)
class ParsedKeyChord:
    modifiers: tuple[str, ...]
    key_name: str
    key_vk: int


def parse_key_chord(chord: str) -> ParsedKeyChord:
    parts = [part.strip().upper() for part in chord.replace("-", "+").split("+") if part.strip()]
    if not parts:
        raise ValueError("Enter a key, like F1 or CTRL+SPACE.")

    modifiers: list[str] = []
    key_names: list[str] = []
    for part in parts:
        canonical = "CTRL" if part == "CONTROL" else part
        if canonical in {"CTRL", "SHIFT", "ALT", "WIN"}:
            if canonical not in modifiers:
                modifiers.append(canonical)
        else:
            key_names.append(canonical)

    if len(key_names) != 1:
        raise ValueError("Use one normal key plus optional CTRL, ALT, SHIFT, or WIN.")

    key_name = key_names[0]
    key_vk = VK_CODES.get(key_name)
    if key_vk is None:
        raise ValueError(f"Unsupported key '{key_name}'. Try F1, A, SPACE, ENTER, or similar.")
    return ParsedKeyChord(tuple(modifiers), key_name, key_vk)


def _send_vk(vk: int, flags: int = 0) -> None:
    extra = ctypes.c_ulong(0)
    event = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(ki=KEYBDINPUT(vk, 0, flags, 0, ctypes.pointer(extra))),
    )
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
    if sent != 1:
        raise OSError(ctypes.get_last_error(), "SendInput failed")


def send_key_chord(chord: str, press_seconds: float = 0.04) -> None:
    parsed = parse_key_chord(chord)
    modifier_vks = [MODIFIER_VKS[name] for name in parsed.modifiers]

    for vk in modifier_vks:
        _send_vk(vk)
    _send_vk(parsed.key_vk)
    time.sleep(press_seconds)
    _send_vk(parsed.key_vk, KEYEVENTF_KEYUP)
    for vk in reversed(modifier_vks):
        _send_vk(vk, KEYEVENTF_KEYUP)


def active_window_title() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value

