from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import time


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

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
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT


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


def _send_vk(vk: int, keyup: bool = False) -> None:
    scan_code = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    if not scan_code:
        raise OSError(0, f"Could not map virtual key {vk} to a scan code")

    flags = KEYEVENTF_SCANCODE
    if keyup:
        flags |= KEYEVENTF_KEYUP

    event = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(ki=KEYBDINPUT(0, scan_code, flags, 0, 0)),
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
    _send_vk(parsed.key_vk, keyup=True)
    for vk in reversed(modifier_vks):
        _send_vk(vk, keyup=True)


def active_window_title() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value
