from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import re
import time


INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

CHORD_SPLIT_RE = re.compile(r"\s*\+\s*")
CHORD_AND_RE = re.compile(r"\s+(?:AND|&)\s+", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")

MODIFIER_ALIASES = {
    "CTRL": "LEFT CTRL",
    "CONTROL": "LEFT CTRL",
    "LEFT CTRL": "LEFT CTRL",
    "LEFT CONTROL": "LEFT CTRL",
    "LCTRL": "LEFT CTRL",
    "LCONTROL": "LEFT CTRL",
    "RIGHT CTRL": "RIGHT CTRL",
    "RIGHT CONTROL": "RIGHT CTRL",
    "RCTRL": "RIGHT CTRL",
    "RCONTROL": "RIGHT CTRL",
    "SHIFT": "LEFT SHIFT",
    "LEFT SHIFT": "LEFT SHIFT",
    "LSHIFT": "LEFT SHIFT",
    "RIGHT SHIFT": "RIGHT SHIFT",
    "RSHIFT": "RIGHT SHIFT",
    "ALT": "LEFT ALT",
    "MENU": "LEFT ALT",
    "LEFT ALT": "LEFT ALT",
    "LEFT MENU": "LEFT ALT",
    "LALT": "LEFT ALT",
    "RIGHT ALT": "RIGHT ALT",
    "RIGHT MENU": "RIGHT ALT",
    "RALT": "RIGHT ALT",
    "WIN": "LEFT WIN",
    "WINDOWS": "LEFT WIN",
    "LEFT WIN": "LEFT WIN",
    "LEFT WINDOWS": "LEFT WIN",
    "LWIN": "LEFT WIN",
    "RIGHT WIN": "RIGHT WIN",
    "RIGHT WINDOWS": "RIGHT WIN",
    "RWIN": "RIGHT WIN",
}

VK_CODES = {
    "LEFT CTRL": 0xA2,
    "RIGHT CTRL": 0xA3,
    "LEFT SHIFT": 0xA0,
    "RIGHT SHIFT": 0xA1,
    "LEFT ALT": 0xA4,
    "RIGHT ALT": 0xA5,
    "LEFT WIN": 0x5B,
    "RIGHT WIN": 0x5C,
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
    "PLUS": 0xBB,
    "EQUALS": 0xBB,
    "MINUS": 0xBD,
    "COMMA": 0xBC,
    "PERIOD": 0xBE,
    "DOT": 0xBE,
    "SLASH": 0xBF,
    "FORWARD SLASH": 0xBF,
    "BACKSLASH": 0xDC,
    "SEMICOLON": 0xBA,
    "APOSTROPHE": 0xDE,
    "QUOTE": 0xDE,
    "GRAVE": 0xC0,
    "BACKTICK": 0xC0,
    "LEFT BRACKET": 0xDB,
    "RIGHT BRACKET": 0xDD,
    "LBRACKET": 0xDB,
    "RBRACKET": 0xDD,
}

for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_CODES[char] = ord(char)
for char in "0123456789":
    VK_CODES[char] = ord(char)
for index in range(1, 25):
    VK_CODES[f"F{index}"] = 0x70 + index - 1

EXTENDED_VKS = {
    0x21,
    0x22,
    0x23,
    0x24,
    0x25,
    0x26,
    0x27,
    0x28,
    0x2D,
    0x2E,
    0x5B,
    0x5C,
    0xA3,
    0xA5,
}


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
class ParsedKey:
    name: str
    vk: int
    is_modifier: bool


@dataclass(frozen=True)
class ParsedKeyChord:
    keys: tuple[ParsedKey, ...]

    @property
    def modifiers(self) -> tuple[str, ...]:
        modifiers: list[str] = []
        for key in self.keys:
            if not key.is_modifier:
                continue
            generic = _generic_modifier_name(key.name)
            if generic not in modifiers:
                modifiers.append(generic)
        return tuple(modifiers)

    @property
    def trigger_key(self) -> ParsedKey | None:
        for key in self.keys:
            if not key.is_modifier:
                return key
        return None

    @property
    def key_name(self) -> str:
        trigger = self.trigger_key or self.keys[-1]
        return trigger.name

    @property
    def key_vk(self) -> int:
        trigger = self.trigger_key or self.keys[-1]
        return trigger.vk


def parse_key_chord(chord: str, require_trigger_key: bool = False) -> ParsedKeyChord:
    chord = CHORD_AND_RE.sub("+", chord.strip())
    parts = [part.strip() for part in CHORD_SPLIT_RE.split(chord) if part.strip()]
    if not parts:
        raise ValueError("Enter a keybind, like F1, LEFT SHIFT+P, or CTRL+SPACE.")

    keys: list[ParsedKey] = []
    seen: set[str] = set()
    for part in parts:
        canonical = _canonical_key_name(part)
        key_vk = VK_CODES.get(canonical)
        if key_vk is None:
            raise ValueError(f"Unsupported key '{canonical}'. Try F1, LEFT SHIFT+P, SPACE, ENTER, or similar.")
        if canonical in seen:
            continue
        seen.add(canonical)
        keys.append(ParsedKey(canonical, key_vk, canonical in MODIFIER_ALIASES.values()))

    parsed = ParsedKeyChord(tuple(keys))
    if require_trigger_key and not parsed.trigger_key:
        raise ValueError("A global hotkey needs one normal key, like F9 or CTRL+SPACE.")
    return parsed


def _canonical_key_name(value: str) -> str:
    value = value.strip().upper().replace("_", " ").replace("-", " ")
    value = SPACE_RE.sub(" ", value)
    return MODIFIER_ALIASES.get(value, value)


def _generic_modifier_name(value: str) -> str:
    if "CTRL" in value:
        return "CTRL"
    if "SHIFT" in value:
        return "SHIFT"
    if "ALT" in value:
        return "ALT"
    if "WIN" in value:
        return "WIN"
    return value


def _send_vk(vk: int, keyup: bool = False) -> None:
    scan_code = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    if not scan_code:
        raise OSError(0, f"Could not map virtual key {vk} to a scan code")

    flags = KEYEVENTF_SCANCODE
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
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

    for key in parsed.keys:
        _send_vk(key.vk)
    time.sleep(press_seconds)
    for key in reversed(parsed.keys):
        _send_vk(key.vk, keyup=True)


def active_window_title() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value
