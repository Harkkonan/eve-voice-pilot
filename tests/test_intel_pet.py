from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.corp_intel import ChannelFilter, ChatMessage, EveSsoConfig, watch_chat_logs
import eve_voice_pilot.intel_pet as intel_pet_module
from eve_voice_pilot.intel_pet import (
    ALERT_SPRITE_SEQUENCE,
    BEHAVIOR_ALERT,
    BEHAVIOR_COMBAT,
    BEHAVIOR_HAPPY,
    BEHAVIOR_IDLE,
    BEHAVIOR_LONG_COMBAT,
    BEHAVIOR_LONG_COMBO,
    BEHAVIOR_LONG_MOVE,
    BEHAVIOR_NONE,
    DEFAULT_ALERT_SECONDS,
    DEFAULT_ALERT_BEHAVIORS,
    DEFAULT_INPUT_DEVICE_LABEL,
    DEFAULT_PET_SPEECH_ENGINE,
    DEFAULT_VOICE_PROFILE,
    DEFAULT_VOICE_ENGINE,
    DEFAULT_VOICE_MODEL_LABEL,
    DEFAULT_VOICE_PREVIEW_TEXT,
    DEFAULT_VOICE_TARGET_TITLE,
    RECOMMENDED_VOICE_MODEL_LABEL,
    USER_VOICE_PROFILE,
    IDLE_SPRITE_SEQUENCE,
    KILL_SPRITE_STEPS,
    LONG_COMBAT_SPRITE_STEPS,
    LONG_COMBO_SPRITE_STEPS,
    LONG_MOVE_SPRITE_STEPS,
    LOCATION_SCOPE,
    SHIP_FRAME_COUNT,
    GameLogState,
    IntelPetCombatCheer,
    IntelPetHistoryItem,
    IntelPetLocationCheer,
    IntelPetLocationSession,
    IntelPetEngine,
    IntelPetSettings,
    IntelPetVoiceStatus,
    alert_behavior_key,
    alert_with_local_system_fallback,
    behavior_for_alert,
    behavior_for_kind,
    behavior_key_from_label,
    behavior_label,
    clean_alert_behaviors,
    clean_voice_command_phrases,
    clean_voice_engine,
    clean_voice_input_device,
    clean_voice_model_path,
    clean_voice_preview_text,
    clean_voice_training_phrase,
    clean_voice_target_title,
    clean_user_terms,
    combat_cheer_from_game_log_line,
    closest_voice_phrase_suggestions,
    display_message_from_alert,
    display_message_from_alerts,
    display_message_from_cheer,
    display_message_from_combat_cheer,
    display_message_from_mission_cheer,
    display_message_from_voice_status,
    editable_voice_profile_path,
    execute_voice_command,
    fetch_pet_location,
    highest_severity_alert,
    history_item_from_alert,
    history_item_from_cheer,
    history_item_from_combat_cheer,
    history_item_from_mission_cheer,
    history_item_from_status,
    history_item_from_voice_status,
    is_kill_event_text,
    is_happy_system,
    listener_filter_from_args,
    load_sprite_frames,
    mission_action_from_text,
    mission_cheer_from_game_log_line,
    load_settings,
    pet_voice_preset_for_style,
    pet_voice_preset_names,
    pet_voice_preview_for_preset,
    pet_voice_style_for_preset,
    read_new_combat_cheers,
    recent_voice_training_phrases,
    recognition_diagnostic_report,
    read_new_game_log_cheers,
    read_new_mission_cheers,
    replace_alert_behaviors,
    replace_alert_terms,
    replace_extra_keywords,
    replace_voice_settings,
    save_settings,
    ship_sprite_frame_paths,
    spoken_pet_text,
    trim_history,
    voice_input_device_display,
    voice_model_display,
    voice_model_path,
    voice_model_status,
    voice_phrase_analysis_lines,
    voice_command_from_fields,
    voice_command_with_added_phrase,
    voice_training_phrase_from_detail,
    voice_status_from_transcript,
)
from eve_voice_pilot.commands import VoiceCommand
from eve_voice_pilot.local_transcription import DEFAULT_MODEL_PATH, RECOMMENDED_MODEL_PATH, LocalRecognitionDiagnostic
from eve_voice_pilot.speech_responses import RESPONSE_ENGINE_OPENAI, RESPONSE_ENGINE_WINDOWS


def make_message(text: str, *, speaker: str = "Alice Example", channel: str = "Corp") -> ChatMessage:
    return ChatMessage(
        log_path="corp.txt",
        channel=channel,
        timestamp="2026.06.05 18:15:00",
        speaker=speaker,
        text=text,
    )


def test_name_mention_alerts_when_another_pilot_mentions_you():
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin Ridderston",)))

    alert = engine.analyze(make_message("Dandin Ridderston can you scout the gate?"))

    assert alert is not None
    assert alert.title == "Your name was mentioned in Corp"
    assert alert.severity == "high"
    assert "self-mention" in alert.categories
    assert "name: Dandin Ridderston" in alert.keywords
    assert alert.message == "Dandin Ridderston can you scout the gate?"


def test_name_mention_does_not_alert_on_your_own_line_by_itself():
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin Ridderston",)))

    alert = engine.analyze(
        make_message("Dandin Ridderston checking in", speaker="Dandin Ridderston"),
    )

    assert alert is None


def test_help_call_uses_existing_intel_parser_and_is_critical():
    engine = IntelPetEngine(IntelPetSettings())

    alert = engine.analyze(make_message("need help on the Tama gate"))

    assert alert is not None
    assert alert.title == "Help call in Corp"
    assert alert.severity == "critical"
    assert "aid" in alert.categories
    assert "help" in alert.keywords


def test_extra_keyword_alerts_without_shared_board():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("buy order",)))

    alert = engine.analyze(make_message("that buy order finally showed up"))

    assert alert is not None
    assert alert.title == "Keyword match in Corp"
    assert alert.severity == "medium"
    assert "watchlist-keyword" in alert.categories
    assert "keyword: buy order" in alert.keywords


def test_settings_can_hide_message_text():
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin Ridderston",), show_message_text=False))

    alert = engine.analyze(make_message("Dandin Ridderston ping"))

    assert alert is not None
    assert alert.message == ""


def test_load_settings_merges_local_file_and_cli_overrides(tmp_path):
    settings_path = tmp_path / "intel_pet_settings.json"
    settings_path.write_text(
        """
{
  "pilot_names": ["Dandin Ridderston"],
  "extra_keywords": ["buy order"],
  "help_phrases": ["need evac"],
  "show_message_text": false,
  "alert_seconds": 12,
  "alert_behaviors": {
    "mention": "happy",
    "help": "combat",
    "hostile": "none",
    "keyword": "idle",
    "location": "alert",
    "mission": "long_combo",
    "combat": "not-a-real-behavior"
  }
}
""".strip(),
        encoding="utf-8",
    )

    class Args:
        pilot_name = ("Second Pilot",)
        keyword = ("PLEX sale",)
        help_phrase = ()
        no_message_text = False
        alert_seconds = None

    settings = load_settings(Path(settings_path), overrides=Args())

    assert settings.pilot_names == ("Dandin Ridderston", "Second Pilot")
    assert settings.extra_keywords == ("buy order", "PLEX sale")
    assert settings.help_phrases == ("need evac",)
    assert settings.show_message_text is False
    assert settings.alert_seconds == 12
    assert settings.alert_behaviors["mention"] == BEHAVIOR_HAPPY
    assert settings.alert_behaviors["help"] == BEHAVIOR_COMBAT
    assert settings.alert_behaviors["hostile"] == BEHAVIOR_NONE
    assert settings.alert_behaviors["keyword"] == BEHAVIOR_IDLE
    assert settings.alert_behaviors["location"] == BEHAVIOR_ALERT
    assert settings.alert_behaviors["mission"] == BEHAVIOR_LONG_COMBO
    assert settings.alert_behaviors["combat"] == DEFAULT_ALERT_BEHAVIORS["combat"]


def test_clean_user_terms_splits_commas_and_dedupes_case_insensitively():
    assert clean_user_terms(("buy order, gate camp", "Buy Order", "  need evac  ")) == (
        "buy order",
        "gate camp",
        "need evac",
    )


def test_save_settings_persists_keywords_for_later_load(tmp_path):
    settings_path = tmp_path / "intel_pet_settings.json"
    settings = IntelPetSettings(
        pilot_names=("Dandin Ridderston",),
        extra_keywords=("buy order", "gate camp"),
        help_phrases=("need evac",),
        show_message_text=False,
        alert_seconds=9,
        alert_behaviors=clean_alert_behaviors(
            {
                "mention": BEHAVIOR_HAPPY,
                "combat": BEHAVIOR_NONE,
            }
        ),
    )

    save_settings(settings_path, settings)
    loaded = load_settings(settings_path)

    assert loaded == settings


def test_pet_voice_settings_persist_and_clean_values(tmp_path):
    settings_path = tmp_path / "intel_pet_settings.json"
    settings = replace_voice_settings(
        IntelPetSettings(),
        speak_alerts=True,
        response_engine=RESPONSE_ENGINE_OPENAI,
        response_voice="nova",
        response_style="Short and calm.",
        voice_preview_text="  Systems are green.\nDocking path is clear.  ",
        enable_voice_listener=True,
        voice_engine="OpenAI realtime",
        voice_model_path=str(RECOMMENDED_MODEL_PATH),
        voice_input_device="3: Headset (48000 Hz)",
        voice_call_sign="Aura",
        allow_voice_command_sending=True,
        require_voice_target_window=True,
        voice_target_title="EVE - Dandin",
    )

    save_settings(settings_path, settings)
    loaded = load_settings(settings_path)

    assert loaded.speak_alerts is True
    assert loaded.response_engine == RESPONSE_ENGINE_OPENAI
    assert loaded.response_voice == "nova"
    assert loaded.response_style == "Short and calm."
    assert loaded.voice_preview_text == "Systems are green. Docking path is clear."
    assert loaded.enable_voice_listener is True
    assert loaded.voice_engine == "OpenAI realtime"
    assert loaded.voice_model_path == str(RECOMMENDED_MODEL_PATH)
    assert loaded.voice_input_device == "3: Headset (48000 Hz)"
    assert loaded.voice_call_sign == "Aura"
    assert loaded.allow_voice_command_sending is True
    assert loaded.require_voice_target_window is True
    assert loaded.voice_target_title == "EVE - Dandin"
    cleaned = replace_voice_settings(
        loaded,
        speak_alerts=False,
        response_engine="not-real",
        response_voice="",
        response_style="",
        voice_preview_text="",
        enable_voice_listener=False,
        voice_engine="not-real",
        voice_model_path=DEFAULT_VOICE_MODEL_LABEL,
        voice_input_device=DEFAULT_INPUT_DEVICE_LABEL,
        voice_call_sign="",
        allow_voice_command_sending=False,
        require_voice_target_window=False,
        voice_target_title="",
    )

    assert cleaned.response_engine == DEFAULT_PET_SPEECH_ENGINE == RESPONSE_ENGINE_WINDOWS
    assert cleaned.voice_engine == DEFAULT_VOICE_ENGINE
    assert cleaned.voice_model_path == ""
    assert cleaned.voice_preview_text == DEFAULT_VOICE_PREVIEW_TEXT
    assert cleaned.voice_input_device == ""
    assert cleaned.voice_call_sign == "merlin"
    assert cleaned.allow_voice_command_sending is False
    assert cleaned.require_voice_target_window is False
    assert cleaned.voice_target_title == DEFAULT_VOICE_TARGET_TITLE


def test_spoken_pet_text_collapses_bubble_newlines():
    assert spoken_pet_text("18:15:00Z | Amarr\nbuy order appeared") == "18:15:00Z | Amarr. buy order appeared"


def test_voice_input_device_display_uses_system_default_label():
    assert clean_voice_input_device(DEFAULT_INPUT_DEVICE_LABEL) == ""
    assert voice_input_device_display("") == DEFAULT_INPUT_DEVICE_LABEL
    assert clean_voice_engine("not-real") == DEFAULT_VOICE_ENGINE
    assert clean_voice_target_title("") == DEFAULT_VOICE_TARGET_TITLE


def test_voice_model_helpers_use_default_and_recommended_paths():
    assert clean_voice_model_path(DEFAULT_VOICE_MODEL_LABEL) == ""
    assert voice_model_path("") == DEFAULT_MODEL_PATH
    assert clean_voice_model_path(RECOMMENDED_VOICE_MODEL_LABEL) == str(RECOMMENDED_MODEL_PATH)
    assert voice_model_display(str(RECOMMENDED_MODEL_PATH)) == RECOMMENDED_VOICE_MODEL_LABEL
    assert str(DEFAULT_MODEL_PATH) in voice_model_status("")


def test_pet_voice_presets_provide_style_and_preview_text():
    assert "Power ballad" in pet_voice_preset_names()
    style = pet_voice_style_for_preset("Clear comms")

    assert "clear fleet channel" in style
    assert pet_voice_preset_for_style(style) == "Clear comms"
    assert pet_voice_preview_for_preset("Tiny scout").startswith("Scout ship online.")
    assert clean_voice_preview_text("") == DEFAULT_VOICE_PREVIEW_TEXT


def test_voice_lab_saves_personal_copy_when_source_is_default_profile():
    assert editable_voice_profile_path(DEFAULT_VOICE_PROFILE) == USER_VOICE_PROFILE
    custom_path = ROOT / "profiles" / "custom_commands.json"
    assert editable_voice_profile_path(custom_path) == custom_path


def test_clean_voice_command_phrases_splits_commas_newlines_and_dedupes():
    assert clean_voice_command_phrases("Warp, warp\nDock up,  dock up ") == ["Warp", "Dock up"]


def test_voice_command_from_fields_validates_and_cleans_values():
    command = voice_command_from_fields(
        name="  Orbit Target ",
        phrases=" orbit, circle them ",
        key=" alt + q ",
        hold_seconds="0.25",
        press_count="2",
        repeat_gap_seconds="0.30",
        response_suffix="Ballad",
        response_text="Spinning up.",
    )

    assert command.name == "Orbit Target"
    assert command.phrases == ["orbit", "circle them"]
    assert command.key == "ALT + Q"
    assert command.hold_seconds == 0.25
    assert command.press_count == 2
    assert command.repeat_gap_seconds == 0.30
    assert command.response_suffix == "Ballad"
    assert command.response_text == "Spinning up."


def test_voice_command_from_fields_rejects_bad_keybind():
    try:
        voice_command_from_fields(name="Bad", phrases="bad", key="NOPEKEY")
    except ValueError as exc:
        assert "Unsupported key" in str(exc)
    else:
        raise AssertionError("Bad keybind should fail validation")


def test_voice_training_phrase_strips_response_call_sign():
    assert clean_voice_training_phrase("Aura warp now", response_call_sign="Aura") == "warp now"
    assert voice_training_phrase_from_detail("Heard: Aura dock up\nNo exact command matched.", response_call_sign="Aura") == "dock up"


def test_voice_command_with_added_phrase_dedupes_existing_phrases():
    command = VoiceCommand("Warp to", ["warp"], "S")

    updated = voice_command_with_added_phrase(command, "Aura warp now", response_call_sign="Aura")
    duplicate = voice_command_with_added_phrase(updated, "warp now", response_call_sign="Aura")

    assert updated.phrases == ["warp", "warp now"]
    assert duplicate.phrases == updated.phrases
    assert duplicate is updated


def test_recent_voice_training_phrases_reads_in_memory_voice_history():
    items = [
        IntelPetHistoryItem("Other", "No voice here.", "Local watcher", "info", "1"),
        IntelPetHistoryItem("Voice heard", "Heard: Aura dock up\nNo exact command matched.", "Voice practice listener", "info", "2"),
        IntelPetHistoryItem("Voice heard", "Heard: Aura warp now\nNo exact command matched.", "Voice practice listener", "info", "3"),
        IntelPetHistoryItem("Voice heard", "Heard: Aura dock up\nNo exact command matched.", "Voice practice listener", "info", "4"),
    ]

    phrases = recent_voice_training_phrases(items, response_call_sign="Aura")

    assert phrases == ("dock up", "warp now")


def test_recognition_diagnostic_report_shows_volume_and_match_context():
    command = VoiceCommand("Warp to", ["warp now"], "S")
    diagnostic = LocalRecognitionDiagnostic(
        transcript="warp now",
        partial_transcript="warp",
        reason="auto-stop silence",
        speech_started=True,
        max_rms=900,
        speech_threshold=450,
        duration_seconds=1.25,
        capture_rate=48000,
        block_size=960,
        input_device_index=3,
        model_path="models/vosk-model-small-en-us-0.15",
        grammar_size=42,
    )

    report = recognition_diagnostic_report(
        diagnostic,
        [command],
        input_device_label="3: Headset (48000 Hz)",
        response_call_sign="Aura",
    )

    assert "Transcript: warp now" in report
    assert "Volume: max RMS 900 / threshold 450 (usable)" in report
    assert "Capture: 48000 Hz, block 960, mic 3: Headset (48000 Hz)" in report
    assert "Matched: Warp to -> S for 0.10s" in report
    assert "Practice only. No key sent." in report
    assert "Nearest command phrases:" in report
    assert "Warp to: warp now -> S for 0.10s" in report


def test_recognition_diagnostic_report_warns_when_too_quiet():
    diagnostic = LocalRecognitionDiagnostic(
        transcript="",
        partial_transcript="",
        reason="initial silence",
        speech_started=False,
        max_rms=100,
        speech_threshold=450,
        duration_seconds=4.0,
        capture_rate=48000,
        block_size=960,
        input_device_index=None,
        model_path="models/vosk-model-small-en-us-0.15",
        grammar_size=1,
    )

    report = recognition_diagnostic_report(diagnostic, [])

    assert "Transcript: (empty)" in report
    assert "very quiet" in report
    assert "selected microphone and input level" in report
    assert "start speaking after the lab says it is recording" in report


def test_recognition_diagnostic_report_suggests_close_unmatched_phrases():
    commands = [
        VoiceCommand("Dock", ["dock up"], "D"),
        VoiceCommand("Warp to", ["warp now"], "S"),
    ]
    diagnostic = LocalRecognitionDiagnostic(
        transcript="doc cup",
        partial_transcript="doc",
        reason="auto-stop silence",
        speech_started=True,
        max_rms=900,
        speech_threshold=450,
        duration_seconds=1.25,
        capture_rate=48000,
        block_size=960,
        input_device_index=3,
        model_path="models/vosk-model-en-us-0.22-lgraph",
        grammar_size=42,
    )

    report = recognition_diagnostic_report(diagnostic, commands, response_call_sign="Aura")

    assert "No exact command matched." in report
    assert "Nearest command phrases:" in report
    assert "Dock: dock up -> D for 0.10s" in report
    assert "Analysis: close to a configured phrase, but exact command matching rejected it." in report


def test_voice_phrase_analysis_warns_when_top_matches_are_ambiguous():
    commands = [
        VoiceCommand("Dock", ["dock up"], "D"),
        VoiceCommand("Undock", ["dock out"], "CTRL+D"),
    ]

    suggestions = closest_voice_phrase_suggestions("dock in", commands)
    lines = voice_phrase_analysis_lines("dock in", commands)

    assert suggestions[0].command_name == "Dock"
    assert any("Nearest command phrases:" in line for line in lines)
    assert any("distinct phrase" in line for line in lines)


def test_voice_status_from_transcript_matches_command_without_sending_keys():
    command = VoiceCommand("Warp to", ["warp", "warp now"], "S", response_suffix="Aura")

    status = voice_status_from_transcript("Aura warp now", [command], response_call_sign="Aura")

    assert status is not None
    assert status.title == "Voice command matched"
    assert "Heard: Aura warp now" in status.detail
    assert "Matched: Warp to -> S for 0.10s" in status.detail
    assert "Practice only. No key sent." in status.detail
    assert display_message_from_voice_status(status) == status.detail


def test_execute_voice_command_blocks_without_sending_permission():
    command = VoiceCommand("Warp to", ["warp"], "S")
    sent: list[tuple[str, float]] = []

    result, severity = execute_voice_command(
        command,
        allow_command_sending=False,
        require_target_window=True,
        target_title="EVE",
        key_sender=lambda key, seconds: sent.append((key, seconds)),
    )

    assert result == "Practice only. No key sent."
    assert severity == "info"
    assert sent == []


def test_execute_voice_command_blocks_when_active_window_does_not_match():
    command = VoiceCommand("Warp to", ["warp"], "S")
    sent: list[tuple[str, float]] = []

    result, severity = execute_voice_command(
        command,
        allow_command_sending=True,
        require_target_window=True,
        target_title="EVE",
        active_window_lookup=lambda: "Notepad",
        key_sender=lambda key, seconds: sent.append((key, seconds)),
    )

    assert result == "Did not send S; active window is 'Notepad'."
    assert severity == "high"
    assert sent == []


def test_voice_status_from_transcript_sends_when_explicitly_allowed_and_window_matches():
    command = VoiceCommand("Warp to", ["warp"], "S")
    sent: list[tuple[str, float]] = []

    status = voice_status_from_transcript(
        "warp",
        [command],
        allow_command_sending=True,
        require_target_window=True,
        target_title="EVE",
        active_window_lookup=lambda: "EVE - Dandin Ridderston",
        key_sender=lambda key, seconds: sent.append((key, seconds)),
    )

    assert status is not None
    assert status.title == "Voice command sent"
    assert "Sent S for 0.10s." in status.detail
    assert sent == [("S", 0.1)]


def test_voice_status_from_transcript_reports_unmatched_phrase():
    command = VoiceCommand("Warp to", ["warp"], "S")

    status = voice_status_from_transcript("open market", [command])

    assert status is not None
    assert status.title == "Voice heard"
    assert "No exact command matched." in status.detail


def test_history_item_from_voice_status_records_practice_listener_context():
    status = IntelPetVoiceStatus(
        title="Voice command matched",
        detail="Practice only. No key sent.",
        severity="info",
        recorded_at="2026-06-06T20:00:00Z",
    )

    item = history_item_from_voice_status(status)

    assert item.title == "Voice command matched"
    assert item.detail == "Practice only. No key sent."
    assert item.meta == "Voice practice listener"
    assert item.recorded_at == "2026-06-06T20:00:00Z"


def test_default_alert_duration_is_fifteen_seconds():
    assert IntelPetSettings().alert_seconds == DEFAULT_ALERT_SECONDS == 15.0


def test_default_alert_behaviors_preserve_existing_pet_actions():
    settings = IntelPetSettings()

    assert settings.alert_behaviors == DEFAULT_ALERT_BEHAVIORS
    assert behavior_for_kind("mention", settings) == BEHAVIOR_ALERT
    assert behavior_for_kind("location", settings) == BEHAVIOR_HAPPY
    assert behavior_for_kind("combat", settings) == BEHAVIOR_COMBAT


def test_replace_alert_behaviors_cleans_unknown_values():
    settings = replace_alert_behaviors(
        IntelPetSettings(),
        {
            "mention": BEHAVIOR_HAPPY,
            "help": "not-real",
            "combat": BEHAVIOR_NONE,
            "mission": BEHAVIOR_LONG_MOVE,
            "unknown": BEHAVIOR_COMBAT,
        },
    )

    assert settings.alert_behaviors["mention"] == BEHAVIOR_HAPPY
    assert settings.alert_behaviors["help"] == DEFAULT_ALERT_BEHAVIORS["help"]
    assert settings.alert_behaviors["combat"] == BEHAVIOR_NONE
    assert settings.alert_behaviors["mission"] == BEHAVIOR_LONG_MOVE
    assert "unknown" not in settings.alert_behaviors


def test_behavior_labels_round_trip_for_options_ui():
    assert behavior_key_from_label(behavior_label(BEHAVIOR_COMBAT)) == BEHAVIOR_COMBAT
    assert behavior_key_from_label(behavior_label(BEHAVIOR_LONG_MOVE)) == BEHAVIOR_LONG_MOVE
    assert behavior_key_from_label(behavior_label(BEHAVIOR_LONG_COMBAT)) == BEHAVIOR_LONG_COMBAT
    assert behavior_key_from_label(behavior_label(BEHAVIOR_LONG_COMBO)) == BEHAVIOR_LONG_COMBO
    assert behavior_key_from_label("not a label") == BEHAVIOR_ALERT


def test_engine_update_settings_changes_keyword_matches_without_restarting():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("buy order",)))
    assert engine.analyze(make_message("contract alert")) is None

    updated = replace_extra_keywords(engine.current_settings(), ("contract alert",))
    engine.update_settings(updated)

    alert = engine.analyze(make_message("contract alert is up"))

    assert alert is not None
    assert alert.title == "Keyword match in Corp"
    assert alert.keywords == ("keyword: contract alert",)


def test_replace_alert_terms_updates_all_local_alert_lists():
    settings = IntelPetSettings(
        pilot_names=("Dandin Ridderston",),
        extra_keywords=("buy order",),
        help_phrases=("need help",),
        show_message_text=False,
        alert_seconds=12,
    )

    updated = replace_alert_terms(
        settings,
        pilot_names=("Dandin Ridderston, Second Pilot", "second pilot"),
        extra_keywords=("gate camp, Buy Order",),
        help_phrases=("need evac", "Need Evac"),
    )

    assert updated.pilot_names == ("Dandin Ridderston", "Second Pilot")
    assert updated.extra_keywords == ("gate camp", "Buy Order")
    assert updated.help_phrases == ("need evac",)
    assert updated.show_message_text is False
    assert updated.alert_seconds == 12


def test_replace_alert_terms_without_updates_returns_same_settings():
    settings = IntelPetSettings(extra_keywords=("buy order",))

    assert replace_alert_terms(settings) is settings


def test_is_happy_system_matches_configured_systems_case_insensitively():
    assert is_happy_system("jita", ("Dihra", "Amarr", "Jita"))
    assert is_happy_system("Dihra", ("dihra",))
    assert not is_happy_system("Perimeter", ("Dihra", "Amarr", "Jita"))


def test_alert_behavior_key_routes_chat_alert_types():
    settings = replace_alert_behaviors(
        IntelPetSettings(
            pilot_names=("Dandin Ridderston",),
            extra_keywords=("buy order",),
        ),
        {
            "mention": BEHAVIOR_HAPPY,
            "help": BEHAVIOR_COMBAT,
            "keyword": BEHAVIOR_NONE,
        },
    )
    engine = IntelPetEngine(settings)

    mention = engine.analyze(make_message("Dandin Ridderston can you scout?"))
    help_call = engine.analyze(make_message("need help on the gate"))
    keyword = engine.analyze(make_message("that buy order is up"))

    assert mention is not None
    assert alert_behavior_key(mention) == "mention"
    assert behavior_for_alert(mention, settings) == BEHAVIOR_HAPPY
    assert help_call is not None
    assert alert_behavior_key(help_call) == "help"
    assert behavior_for_alert(help_call, settings) == BEHAVIOR_COMBAT
    assert keyword is not None
    assert alert_behavior_key(keyword) == "keyword"
    assert behavior_for_alert(keyword, settings) == BEHAVIOR_NONE


def test_alert_keeps_timestamp_and_detected_system_context():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp",)), system_names=("Tama", "Amarr"))

    alert = engine.analyze(make_message("gate camp on Tama", speaker="Scout Pilot"))

    assert alert is not None
    assert alert.observed_at == "2026-06-05T18:15:00Z"
    assert alert.systems == ("Tama",)


def test_mention_alert_detects_system_even_without_other_intel_terms():
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin",)), system_names=("Tama",))

    alert = engine.analyze(make_message("Dandin can you check Tama?", speaker="Scout Pilot"))

    assert alert is not None
    assert alert.systems == ("Tama",)


def test_fetch_pet_location_uses_read_only_esi_scope(monkeypatch):
    calls = []

    def fake_get_json(url, *, timeout_seconds=30.0, headers=None):
        calls.append((url, headers or {}))
        if "/location/" in url:
            return {"solar_system_id": 30000142}
        if "/universe/systems/30000142/" in url:
            return {"name": "Jita"}
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("eve_voice_pilot.intel_pet.get_json", fake_get_json)
    session = IntelPetLocationSession(
        character_id=123456789,
        character_name="Scout Pilot",
        scopes=(LOCATION_SCOPE,),
        access_token="access-token",
        expires_at=9999999999,
    )

    location = fetch_pet_location(EveSsoConfig(esi_base_url="https://esi.test/latest"), session)

    assert location.solar_system_id == 30000142
    assert location.solar_system_name == "Jita"
    assert calls[0][0] == "https://esi.test/latest/characters/123456789/location/?datasource=tranquility"
    assert calls[0][1]["Authorization"] == "Bearer access-token"
    assert "Authorization" not in calls[1][1]


def test_fetch_pet_location_requires_location_scope():
    session = IntelPetLocationSession(
        character_id=123456789,
        character_name="Scout Pilot",
        scopes=(),
        access_token="access-token",
        expires_at=9999999999,
    )

    try:
        fetch_pet_location(EveSsoConfig(esi_base_url="https://esi.test/latest"), session)
    except Exception as exc:
        assert LOCATION_SCOPE in str(exc)
    else:
        raise AssertionError("expected missing location scope to fail")


def test_listener_filter_defaults_to_location_sso_character():
    class Args:
        listener_name = ()
        all_listeners = False

    session = IntelPetLocationSession(
        character_id=123456789,
        character_name="Dandin Ridderston",
        scopes=(LOCATION_SCOPE,),
        access_token="access-token",
        expires_at=9999999999,
    )

    assert listener_filter_from_args(Args(), location_session=session) == ("Dandin Ridderston",)


def test_listener_filter_all_listeners_overrides_sso_character():
    class Args:
        listener_name = ()
        all_listeners = True

    session = IntelPetLocationSession(
        character_id=123456789,
        character_name="Dandin Ridderston",
        scopes=(LOCATION_SCOPE,),
        access_token="access-token",
        expires_at=9999999999,
    )

    assert listener_filter_from_args(Args(), location_session=session) == ()


def test_listener_filter_explicit_names_work_without_sso():
    class Args:
        listener_name = ("Dandin Ridderston", "Other Pilot")
        all_listeners = False

    assert listener_filter_from_args(Args(), location_session=None) == ("Dandin Ridderston", "Other Pilot")


def test_history_item_from_alert_keeps_message_context():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp",)), system_names=("Amarr",))
    alert = engine.analyze(make_message("gate camp on the Amarr undock", speaker="Scout Pilot"))

    assert alert is not None
    item = history_item_from_alert(alert)

    assert item.title == alert.title
    assert item.detail == "Scout Pilot: gate camp on the Amarr undock"
    assert "18:15:00Z | Amarr" in item.meta
    assert "keyword: gate camp" in item.meta
    assert item.severity == alert.severity


def test_history_item_from_cheer_records_location_arrival():
    cheer = IntelPetLocationCheer(system_name="Amarr", character_name="Scout Pilot", updated_at="2026-06-06T10:45:00Z")

    item = history_item_from_cheer(cheer)

    assert item.title == "Happy arrival: Amarr"
    assert item.detail == "Scout Pilot reached Amarr."
    assert LOCATION_SCOPE in item.meta
    assert item.recorded_at == "2026-06-06T10:45:00Z"


def test_combat_cheer_parses_destroyed_game_log_line():
    line = (
        "[ 2026.06.06 19:15:31 ] (combat) "
        "<color=0xff00ffff><b>Guristas Wrecker has been destroyed</b>"
    )

    cheer = combat_cheer_from_game_log_line(line, log_path="game.txt")

    assert cheer is not None
    assert cheer.message == "Guristas Wrecker has been destroyed"
    assert cheer.observed_at == "2026-06-06T19:15:31Z"
    assert cheer.log_path == "game.txt"


def test_combat_cheer_ignores_your_own_loss_line():
    line = "[ 2026.06.06 19:15:31 ] (notify) Your ship has been destroyed"

    assert combat_cheer_from_game_log_line(line) is None
    assert not is_kill_event_text("Your capsule has been destroyed")


def test_read_new_combat_cheers_updates_game_log_offset(tmp_path):
    path = tmp_path / "GameLog_20260606_191531.txt"
    path.write_text(
        "\n".join(
            (
                "[ 2026.06.06 19:15:31 ] (combat) Your blaster hits a pirate",
                "[ 2026.06.06 19:15:33 ] (combat) Serpentis Spy has been destroyed",
            )
        ),
        encoding="utf-8",
    )
    state = GameLogState(path=path, encoding="utf-8", offset=0)

    cheers = read_new_combat_cheers(state)

    assert [cheer.message for cheer in cheers] == ["Serpentis Spy has been destroyed"]
    assert state.offset > 0
    assert read_new_combat_cheers(state) == []


def test_mission_cheer_parses_accepted_game_log_line():
    line = "[ 2026.06.06 19:20:31 ] (notify) Mission accepted: Worlds Collide"

    cheer = mission_cheer_from_game_log_line(line, log_path="game.txt")

    assert cheer is not None
    assert cheer.action == "accepted"
    assert cheer.message == "Mission accepted: Worlds Collide"
    assert cheer.comment in {
        "Agent contract accepted. Engines warm, capsuleer.",
        "Mission logged. The agent's clock is ticking.",
        "Orders received. Let the void keep pace.",
    }
    assert cheer.observed_at == "2026-06-06T19:20:31Z"
    assert cheer.log_path == "game.txt"


def test_mission_cheer_parses_completed_game_log_line():
    line = "[ 2026.06.06 20:10:01 ] (notify) Mission objectives complete"

    cheer = mission_cheer_from_game_log_line(line)

    assert cheer is not None
    assert cheer.action == "completed"
    assert cheer.comment in {
        "Mission complete. The agent will want this report.",
        "Objective secured. Another entry for the capsuleer ledger.",
        "Contract fulfilled. The cluster owes you a quieter minute.",
    }


def test_mission_cheer_ignores_failed_or_incomplete_lines():
    assert mission_action_from_text("Mission failed") == ""
    assert mission_action_from_text("Mission is not complete") == ""
    assert mission_cheer_from_game_log_line("[ 2026.06.06 20:10:01 ] (notify) Mission declined") is None


def test_read_new_mission_cheers_updates_game_log_offset(tmp_path):
    path = tmp_path / "GameLog_20260606_201001.txt"
    path.write_text(
        "\n".join(
            (
                "[ 2026.06.06 20:10:01 ] (notify) Mission accepted: Recon",
                "[ 2026.06.06 20:12:01 ] (notify) Mission is not complete",
                "[ 2026.06.06 20:20:01 ] (notify) Mission completed",
            )
        ),
        encoding="utf-8",
    )
    state = GameLogState(path=path, encoding="utf-8", offset=0)

    cheers = read_new_mission_cheers(state)

    assert [cheer.action for cheer in cheers] == ["accepted", "completed"]
    assert state.offset > 0
    assert read_new_mission_cheers(state) == []


def test_read_new_game_log_cheers_reads_combat_and_mission_once(tmp_path):
    path = tmp_path / "GameLog_20260606_201001.txt"
    path.write_text(
        "\n".join(
            (
                "[ 2026.06.06 20:10:01 ] (notify) Mission accepted: Recon",
                "[ 2026.06.06 20:15:01 ] (combat) Serpentis Spy has been destroyed",
            )
        ),
        encoding="utf-8",
    )
    state = GameLogState(path=path, encoding="utf-8", offset=0)

    combat_cheers, mission_cheers = read_new_game_log_cheers(state)

    assert [cheer.message for cheer in combat_cheers] == ["Serpentis Spy has been destroyed"]
    assert [cheer.action for cheer in mission_cheers] == ["accepted"]
    assert read_new_game_log_cheers(state) == ([], [])


def test_display_message_from_alert_includes_time_system_and_message_text():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp",)), system_names=("Amarr",))
    alert = engine.analyze(make_message("gate camp on the Amarr undock", speaker="Scout Pilot"))

    assert alert is not None
    assert display_message_from_alert(alert) == "18:15:00Z | Amarr\ngate camp on the Amarr undock"


def test_display_message_from_alert_uses_no_system_fallback():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp",)), system_names=("Tama",))
    alert = engine.analyze(make_message("gate camp on the undock", speaker="Scout Pilot"))

    assert alert is not None
    assert display_message_from_alert(alert) == "18:15:00Z | No system\ngate camp on the undock"


def test_display_message_from_alerts_keeps_multiple_alert_texts():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp", "buy order")), system_names=("Tama", "Amarr"))
    first = engine.analyze(make_message("gate camp on Tama", speaker="Scout Pilot"))
    second = engine.analyze(make_message("buy order in Amarr", speaker="Trader Pilot"))

    assert first is not None
    assert second is not None
    assert display_message_from_alerts((first, second)) == "\n".join(
        (
            "18:15:00Z | Tama | gate camp on Tama",
            "18:15:00Z | Amarr | buy order in Amarr",
        )
    )
    assert highest_severity_alert((second, first)) is first


def test_local_alert_can_use_current_esi_system_as_fallback():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp",)), system_names=("Tama",))
    local_alert = engine.analyze(make_message("gate camp on the undock", channel="Local", speaker="Scout Pilot"))
    corp_alert = engine.analyze(make_message("gate camp on the undock", channel="Corp", speaker="Scout Pilot"))

    assert local_alert is not None
    assert alert_with_local_system_fallback(local_alert, "Amarr").systems == ("Amarr",)
    assert corp_alert is not None
    assert alert_with_local_system_fallback(corp_alert, "Amarr") is corp_alert


def test_display_message_from_cheer_is_short_arrival_text():
    cheer = IntelPetLocationCheer(system_name="Amarr", character_name="Scout Pilot", updated_at="2026-06-06T10:45:00Z")

    assert display_message_from_cheer(cheer) == "Arrived in Amarr."


def test_display_message_from_combat_cheer_is_game_log_message_only():
    cheer = IntelPetCombatCheer(
        message="Guristas Wrecker has been destroyed",
        observed_at="2026-06-06T10:45:00Z",
        reported_at="2026-06-06T10:45:01Z",
    )

    assert display_message_from_combat_cheer(cheer) == "Guristas Wrecker has been destroyed"


def test_history_item_from_combat_cheer_records_local_context():
    cheer = IntelPetCombatCheer(
        message="Guristas Wrecker has been destroyed",
        observed_at="2026-06-06T10:45:00Z",
        reported_at="2026-06-06T10:45:01Z",
    )

    item = history_item_from_combat_cheer(cheer)

    assert item.title == "Kill cheer"
    assert item.detail == "Guristas Wrecker has been destroyed"
    assert "Local game log" in item.meta
    assert item.severity == "high"
    assert item.recorded_at == "2026-06-06T10:45:01Z"


def test_mission_cheer_display_and_history_use_lore_comment():
    cheer = mission_cheer_from_game_log_line("[ 2026.06.06 20:10:01 ] (notify) Mission completed")

    assert cheer is not None
    item = history_item_from_mission_cheer(cheer)

    assert display_message_from_mission_cheer(cheer) == cheer.comment
    assert item.title == "Mission completed"
    assert cheer.comment in item.detail
    assert "Mission completed" in item.detail
    assert item.severity == "info"


def test_history_item_from_status_surfaces_watcher_failures():
    item = history_item_from_status("Watcher stopped: Chat log folder does not exist")

    assert item.title == "Pet watcher status"
    assert item.severity == "high"
    assert "Watcher stopped" in item.detail
    assert item.meta == "Local watcher"


def test_chat_log_watcher_alerts_on_new_appended_name_mention(tmp_path):
    chat_path = tmp_path / "Corp_20260606_193742_123.txt"
    chat_path.write_text(
        """
        ---------------------------------------------------------------

          Channel ID:      corp
          Channel Name:    Corp
          Listener:        Liet-kynes Ridderston
          Session started: 2026.06.06 19:37:42
        ---------------------------------------------------------------
""".lstrip(),
        encoding="utf-8",
    )
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin",)))
    stop_event = threading.Event()
    alert_seen = threading.Event()
    sharing_seen = threading.Event()
    alerts = []

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            alerts.append(alert)
            alert_seen.set()

    watcher = threading.Thread(
        target=watch_chat_logs,
        kwargs={
            "log_dir": tmp_path,
            "channel_filter": ChannelFilter(("Corp", "Local")),
            "on_message": on_message,
            "poll_seconds": 0.05,
            "read_existing": False,
            "stop_event": stop_event,
            "log": lambda text: sharing_seen.set() if "Sharing channel" in text else None,
        },
        daemon=True,
    )
    watcher.start()
    try:
        assert sharing_seen.wait(2.0)
        with chat_path.open("a", encoding="utf-8") as handle:
            handle.write("\ufeff[ 2026.06.06 19:38:31 ] Liet-kynes Ridderston > Dandin\n")
        assert alert_seen.wait(2.0)
    finally:
        stop_event.set()
        watcher.join(timeout=2.0)

    assert alerts[-1].title == "Your name was mentioned in Corp"
    assert alerts[-1].keywords == ("name: Dandin",)


def test_native_window_drag_noops_off_windows(monkeypatch):
    monkeypatch.setattr(intel_pet_module.os, "name", "posix")

    assert not intel_pet_module.start_native_window_drag(object())


def test_raise_tk_widget_uses_widget_raise_for_canvas_like_widgets():
    calls = []

    class FakeTk:
        def call(self, *args):
            calls.append(args)

    class FakeWidget:
        tk = FakeTk()
        _w = ".!frame.!canvas3"

    intel_pet_module.raise_tk_widget(FakeWidget())

    assert calls == [("raise", ".!frame.!canvas3")]


def test_trim_history_keeps_most_recent_items():
    items = tuple(
        history_item_from_cheer(
            IntelPetLocationCheer(system_name=f"System {index}", character_name="Scout", updated_at=str(index))
        )
        for index in range(5)
    )

    trimmed = trim_history(items, limit=3)

    assert [item.title for item in trimmed] == [
        "Happy arrival: System 2",
        "Happy arrival: System 3",
        "Happy arrival: System 4",
    ]


def test_ship_sprite_frame_paths_point_to_committed_assets():
    paths = ship_sprite_frame_paths()

    assert len(paths) == SHIP_FRAME_COUNT
    assert all(path.exists() for path in paths)
    assert all(path.name == f"ship-frame-{index:02d}.png" for index, path in enumerate(paths))


def test_sprite_sequences_only_reference_existing_frames():
    valid_indexes = set(range(SHIP_FRAME_COUNT))

    assert set(IDLE_SPRITE_SEQUENCE) <= valid_indexes
    assert set(ALERT_SPRITE_SEQUENCE) <= valid_indexes
    assert {step[0] for step in KILL_SPRITE_STEPS} <= valid_indexes
    assert {step[0] for step in LONG_MOVE_SPRITE_STEPS} <= valid_indexes
    assert {step[0] for step in LONG_COMBAT_SPRITE_STEPS} <= valid_indexes
    assert {step[0] for step in LONG_COMBO_SPRITE_STEPS} <= valid_indexes
    assert IDLE_SPRITE_SEQUENCE[-1] == 0
    assert ALERT_SPRITE_SEQUENCE[-1] == 0
    assert KILL_SPRITE_STEPS[-1][0] == 0
    assert LONG_MOVE_SPRITE_STEPS[-1][0] == 1
    assert LONG_COMBAT_SPRITE_STEPS[-1][0] == 0
    assert LONG_COMBO_SPRITE_STEPS[-1][0] == 1


def test_load_sprite_frames_returns_empty_when_any_frame_is_missing(tmp_path):
    class FakeTk:
        class PhotoImage:
            def __init__(self, *, file, master):
                self.file = file
                self.master = master

    assert len(load_sprite_frames(FakeTk, object())) == SHIP_FRAME_COUNT
    assert load_sprite_frames(FakeTk, object(), paths=ship_sprite_frame_paths(tmp_path)) == ()
