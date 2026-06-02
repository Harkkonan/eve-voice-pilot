from pathlib import Path
import sys
import ctypes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.commands import (
    CommandProfile,
    VoiceCommand,
    find_command_match,
    find_exact_phrase_match,
    normalize_phrase,
    response_call_signs,
    strip_response_call_sign,
)
from eve_voice_pilot.input_sender import INPUT, INPUT_UNION, KEYBDINPUT, parse_key_chord
from eve_voice_pilot.local_transcription import command_phrases_for_grammar
from eve_voice_pilot.speech_responses import (
    DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    DEFAULT_RESPONSE_ENGINE,
    RESPONSE_ENGINE_OPENAI,
    RESPONSE_ENGINE_WINDOWS,
    normalize_wav_bytes,
    response_cache_path,
    response_enabled,
    response_text_for_command,
)
from eve_voice_pilot.transcription import audio_rms, block_size_for_rate, resample_pcm, resample_pcm_to_24k


def test_normalize_phrase_removes_punctuation():
    assert normalize_phrase("D-Scan!") == "d scan"


def test_voice_command_round_trips_response_fields():
    command = VoiceCommand.from_dict({
        "name": "Map",
        "phrases": ["open map"],
        "key": "F10",
        "press_count": 2,
        "repeat_gap_seconds": 0.1,
        "response_suffix": "Aura",
        "response_text": "Map open.",
    })
    assert command.press_count == 2
    assert command.repeat_gap_seconds == 0.1
    assert command.action_summary == "F10 x2, hold 0.10s, gap 0.10s"
    assert command.response_suffix == "Aura"
    assert command.response_text == "Map open."
    assert command.to_dict()["press_count"] == 2
    assert command.to_dict()["response_suffix"] == "Aura"


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


def test_find_exact_phrase_match_rejects_single_word_inside_sentence():
    command = VoiceCommand("Open map", ["map"], "F10")
    assert find_exact_phrase_match("show the map", [command]) is None


def test_find_exact_phrase_match_allows_single_word_as_whole_command():
    command = VoiceCommand("Open map", ["map"], "F10")
    match = find_exact_phrase_match("map", [command])
    assert match is not None
    assert match.phrase == "map"


def test_strip_response_call_sign_allows_suffix_or_prefix():
    call_signs = response_call_signs("Merlin, Aura")
    assert strip_response_call_sign("warp merlin", call_signs) == ("warp", True)
    assert strip_response_call_sign("aura open map", call_signs) == ("open map", True)
    assert strip_response_call_sign("open map", call_signs) == ("open map", False)


def test_parse_key_chord_allows_modifier_and_key():
    parsed = parse_key_chord("CTRL+SPACE")
    assert parsed.modifiers == ("CTRL",)
    assert parsed.key_name == "SPACE"


def test_parse_key_chord_allows_catalog_hyphen_shortcut():
    parsed = parse_key_chord("Ctrl-Shift-Page Down")
    assert parsed.modifiers == ("CTRL", "SHIFT")
    assert parsed.key_name == "PAGE DOWN"


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


def test_parse_key_chord_allows_catalog_special_keys():
    assert parse_key_chord("Num +").key_name == "NUM +"
    assert parse_key_chord("Num 9").key_name == "NUM 9"
    assert parse_key_chord("\\").key_name == "\\"
    assert parse_key_chord("Sys Req").key_name == "SYS REQ"


def test_parse_key_chord_allows_mouse_side_button():
    parsed = parse_key_chord("MOUSE4")
    assert parsed.key_name == "MOUSE4"
    assert parsed.trigger_key is not None
    assert parsed.trigger_key.kind == "mouse"


def test_parse_key_chord_allows_pause_hotkey():
    parsed = parse_key_chord("PAUSE", require_trigger_key=True)
    assert parsed.key_name == "PAUSE"


def test_audio_rms_detects_louder_audio():
    quiet = (0).to_bytes(2, "little", signed=True) * 20
    loud = (1000).to_bytes(2, "little", signed=True) * 20
    assert audio_rms(quiet) == 0
    assert audio_rms(loud) > 900


def test_resample_pcm_to_24k_downsamples_48k_audio():
    raw = (1000).to_bytes(2, "little", signed=True) * 48
    assert len(resample_pcm_to_24k(raw, 48000)) == 48


def test_resample_pcm_to_24k_keeps_24k_audio_length():
    raw = (1000).to_bytes(2, "little", signed=True) * 48
    assert len(resample_pcm_to_24k(raw, 24000)) == len(raw)


def test_resample_pcm_can_target_16k_audio():
    raw = (1000).to_bytes(2, "little", signed=True) * 48
    assert len(resample_pcm(raw, 48000, 16000)) == 32


def test_block_size_for_rate_has_reasonable_minimum():
    assert block_size_for_rate(8000) == 160


def test_voice_standard_profile_keys_parse():
    profile = CommandProfile.load(ROOT / "profiles" / "eve_voice_standard.json")
    assert len(profile.commands) == 178
    for command in profile.commands:
        parse_key_chord(command.key)


def test_voice_standard_avoids_alt_f4_medium_slot():
    profile = CommandProfile.load(ROOT / "profiles" / "eve_voice_standard.json")
    assert all(command.key not in {"ALT+F4", "ALT+SHIFT+F4"} for command in profile.commands)
    shortcuts = {command.name: command.key for command in profile.commands}
    assert shortcuts["Toggle Overload on Medium Power Slot 4"] == "ALT+SHIFT+4"


def test_voice_standard_includes_initial_aura_responses():
    profile = CommandProfile.load(ROOT / "profiles" / "eve_voice_standard.json")
    recall = next(command for command in profile.commands if command.name == "All Drones: Return to Drone Bay")
    assert response_enabled(recall)
    assert response_text_for_command(recall) == "Drones returning."


def test_response_cache_separates_openai_and_windows_clips():
    command = VoiceCommand("Map", ["open map"], "F10", response_suffix="Aura", response_text="Map open.")
    windows_path = response_cache_path(command, engine=RESPONSE_ENGINE_WINDOWS)
    openai_path = response_cache_path(
        command,
        engine=RESPONSE_ENGINE_OPENAI,
        voice="ballad",
        instructions=DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    )
    assert DEFAULT_RESPONSE_ENGINE == RESPONSE_ENGINE_OPENAI
    assert windows_path != openai_path


def test_voice_standard_includes_added_catalog_shortcuts():
    profile = CommandProfile.load(ROOT / "profiles" / "eve_voice_standard.json")
    shortcuts = {command.name: command.key for command in profile.commands}
    assert shortcuts["Autopilot"] == "CTRL+S"
    assert shortcuts["Contracts"] == "CTRL+ALT+C"
    assert shortcuts["Open Drone Bay Of Active Ship"] == "ALT+SHIFT+D"
    assert shortcuts["Open Fighter Bay Of Active Ship"] == "ALT+SHIFT+F"


def test_voice_standard_orbit_uses_double_press():
    profile = CommandProfile.load(ROOT / "profiles" / "eve_voice_standard.json")
    orbit = next(command for command in profile.commands if command.name == "Orbit")
    assert orbit.key == "W"
    assert orbit.press_count == 2
    assert orbit.repeat_gap_seconds == 0.1


def test_local_grammar_uses_normalized_unique_phrases():
    commands = [
        VoiceCommand("Map", ["Open Map!", "open map"], "F10", response_suffix="Aura"),
        VoiceCommand("Recall", ["recall drones"], "SHIFT+R"),
    ]
    assert command_phrases_for_grammar(commands) == ["open map", "recall drones"]
    assert command_phrases_for_grammar(commands, ["Merlin"]) == [
        "merlin open map",
        "open map",
        "open map merlin",
        "recall drones",
    ]


def test_normalize_wav_bytes_fixes_streaming_sizes():
    audio = (
        b"RIFF\xff\xff\xff\xffWAVE"
        b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00"
        b"data" + (0xFFFFFFFF).to_bytes(4, "little") + b"\x00\x00\x01\x00"
    )
    normalized = normalize_wav_bytes(audio)
    assert int.from_bytes(normalized[4:8], "little") == len(normalized) - 8
    assert int.from_bytes(normalized[40:44], "little") == 4


def test_windows_input_union_has_full_size():
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(INPUT_UNION) >= ctypes.sizeof(KEYBDINPUT)
    assert ctypes.sizeof(INPUT) >= 40
