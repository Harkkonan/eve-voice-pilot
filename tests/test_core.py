from pathlib import Path
import sys
import ctypes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.commands import VoiceCommand, find_command_match, normalize_phrase
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


def test_parse_key_chord_allows_modifier_and_key():
    parsed = parse_key_chord("CTRL+SPACE")
    assert parsed.modifiers == ("CTRL",)
    assert parsed.key_name == "SPACE"


def test_parse_key_chord_rejects_two_normal_keys():
    try:
        parse_key_chord("F1+F2")
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def test_audio_rms_detects_louder_audio():
    quiet = (0).to_bytes(2, "little", signed=True) * 20
    loud = (1000).to_bytes(2, "little", signed=True) * 20
    assert audio_rms(quiet) == 0
    assert audio_rms(loud) > 900


def test_windows_input_union_has_full_size():
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(INPUT_UNION) >= ctypes.sizeof(KEYBDINPUT)
    assert ctypes.sizeof(INPUT) >= 40
