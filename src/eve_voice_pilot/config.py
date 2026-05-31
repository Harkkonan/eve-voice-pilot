from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "EveVoicePilot"
CONFIG_PATH = APP_DIR / "settings.json"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _raise_last_error(prefix: str) -> None:
    raise OSError(ctypes.get_last_error(), prefix)


def protect_text(value: str) -> str:
    raw = value.encode("utf-8")
    in_buffer = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        _raise_last_error("Could not protect secret")
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def unprotect_text(value: str) -> str:
    encrypted = base64.b64decode(value.encode("ascii"))
    in_buffer = ctypes.create_string_buffer(encrypted)
    in_blob = DATA_BLOB(len(encrypted), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        _raise_last_error("Could not read saved secret")
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw.decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def load_settings() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    protected_key = data.get("api_key_protected")
    if protected_key:
        try:
            data["api_key"] = unprotect_text(protected_key)
        except OSError:
            data["api_key"] = ""
    return data


def save_settings(settings: dict, remember_key: bool) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(settings)
    api_key = str(data.pop("api_key", "")).strip()
    data.pop("api_key_protected", None)
    if remember_key and api_key:
        data["api_key_protected"] = protect_text(api_key)
    CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

