from pathlib import Path
import sys
import ctypes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.commands import VoiceCommand, find_command_match, find_exact_phrase_match, normalize_phrase
from eve_voice_pilot.input_sender import INPUT, INPUT_UNION, KEYBDINPUT, parse_key_chord
from eve_voice_pilot.transcription import audio_rms


def test_normalize_phrase_removes_punctuation():
    assert normalize_phrase("D-Scan!") == "d scan"


def test_find_command_match_exact_phrase():
    command = VoiceCommand("Stop ship", ["stop ship"], "CTRL+SPACE")
    match = find_command_match("stop ship", [command])
    assert match is not None
    assert match.command.name == "Stop ship"


def test_find_command_match_rejects_weak_match():
    command = VoiceCommand("Stop ship", ["stop ship"], "CTRL+SPACE")
    assert find_command_match("open map", [command]) is None


def test_find_exact_phrase_match_prefers_longest_phrase():
    command = VoiceCommand("Open map", ["map", "open map"], "F10")
    match = find_exact_phrase_match("please open map now", [command])
    assert match is not None
    assert match.phrase == "open map"


def test_find_exact_phrase_match_rejects_partial_word():
    command = VoiceCommand("Open map", ["map"], "F10")
    assert find_exact_phrase_match("mapped route", [command]) is None


def test_parse_key_chord_allows_modifier_and_key():
    parsed = parse_key_chord("CTRL+SPACE")
    assert parsed.modifiers == ("CTRL",)
    assert parsed.key_name == "SPACE"


def test_parse_key_chord_allows_left_shift_letter():
    parsed = parse_key_chord("left shift and p")
    assert [key.name for key in parsed.keys] == ["LEFT SHIFT", "P"]
    assert parsed.modifiers == ("SHIFT",)
    assert parsed.key_name == "P"


def test_parse_key_chord_allows_modifier_only_command():
    parsed = parse_key_chord("Left Ctrl+Left Shift")
    assert [key.name for key in parsed.keys] == ["LEFT CTRL", "LEFT SHIFT"]
    assert parsed.trigger_key is None


def test_parse_key_chord_requires_trigger_for_global_hotkey():
    try:
        parse_key_chord("LEFT SHIFT", require_trigger_key=True)
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def test_parse_key_chord_allows_multi_key_command():
    parsed = parse_key_chord("F1+F2")
    assert [key.name for key in parsed.keys] == ["F1", "F2"]


def test_audio_rms_detects_louder_audio():
    quiet = (0).to_bytes(2, "little", signed=True) * 20
    loud = (1000).to_bytes(2, "little", signed=True) * 20
    assert audio_rms(quiet) == 0
    assert audio_rms(loud) > 900


def test_windows_input_union_has_full_size():
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(INPUT_UNION) >= ctypes.sizeof(KEYBDINPUT)
    assert ctypes.sizeof(INPUT) >= 40
