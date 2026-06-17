from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse
import webbrowser

from eve_voice_pilot.corp_intel import (
    COMMON_SYSTEM_NAMES,
    DEFAULT_CHANNELS,
    DEFAULT_ESI_BASE_URL,
    DEFAULT_POLL_SECONDS,
    ROOT,
    ChannelFilter,
    ChatMessage,
    CorpIntelError,
    EveSsoConfig,
    IntelParser,
    IntelWatchlist,
    WatchlistStore,
    build_sso_authorization_url,
    character_id_from_sso_payload,
    clean_watchlist_terms,
    compile_phrase_pattern,
    decode_eve_access_token,
    detect_encoding,
    default_chat_log_dir,
    exchange_sso_code,
    eve_timestamp_to_iso,
    file_end_offset,
    get_json,
    higher_severity,
    now_iso,
    parse_csv,
    scopes_from_sso_payload,
    watch_chat_logs,
)
from eve_voice_pilot.discord_posting import (
    DiscordAlertEvent,
    DiscordAlertRoute,
    DiscordAlertRule,
    build_discord_alert_webhook_payload,
    validate_discord_webhook_url,
)
from eve_voice_pilot.commands import (
    DEFAULT_HOLD_SECONDS,
    DEFAULT_PRESS_COUNT,
    DEFAULT_RESPONSE_CALL_SIGN,
    DEFAULT_REPEAT_GAP_SECONDS,
    CommandProfile,
    VoiceCommand,
    find_exact_phrase_match,
    normalize_phrase,
    response_call_signs,
    strip_response_call_sign,
)
from eve_voice_pilot.config import load_settings as load_app_settings
from eve_voice_pilot.input_sender import active_window_title, parse_key_chord, send_key_chord
from eve_voice_pilot.local_transcription import (
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_PATH,
    RECOMMENDED_MODEL_NAME,
    RECOMMENDED_MODEL_PATH,
    LocalRecognitionDiagnostic,
    LocalVoskTranscriber,
)
from eve_voice_pilot.local_whisper import DEFAULT_LOCAL_WHISPER_MODEL, LOCAL_WHISPER_MODELS, LocalWhisperTranscriber
from eve_voice_pilot.mission_library import (
    MissionLibraryEntry,
    MissionReadOptions,
    USER_MISSION_LIBRARY_PATH,
    delete_user_mission_entry,
    find_mission_entries,
    grouped_missions_by_giver,
    load_mission_library,
    mission_detail_text,
    mission_entry_from_dict,
    mission_library_path,
    mission_matches_query,
    mission_read_aloud_text,
    mission_read_options_from_dict,
    mission_read_options_to_dict,
    upsert_user_mission_entry,
)
from eve_voice_pilot.speech_responses import (
    DEFAULT_ELEVENLABS_TTS_MODEL,
    DEFAULT_ELEVENLABS_TTS_VOICE_ID,
    DEFAULT_OPENAI_TTS_MODEL,
    DEFAULT_OPENAI_TTS_VOICE,
    DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    OPENAI_TTS_VOICES,
    RESPONSE_ENGINE_ELEVENLABS,
    RESPONSE_ENGINE_WINDOWS,
    RESPONSE_ENGINES,
    SpeechResponseManager,
    elevenlabs_voice_id,
    normalize_response_text,
)
from eve_voice_pilot.transcription import RealtimeTranscriber, list_input_devices, resolve_input_device_label


DEFAULT_SETTINGS_PATH = ROOT / "profiles" / "intel_pet_settings.json"
DEFAULT_SPRITE_DIR = ROOT / "src" / "eve_voice_pilot" / "static" / "intel-pet"
DEFAULT_ALERT_SECONDS = 15.0
DEFAULT_LOCATION_CALLBACK_URL = "http://127.0.0.1:8788/intel-pet/callback"
DEFAULT_LOCATION_POLL_SECONDS = 15.0
DEFAULT_HAPPY_SYSTEMS = ("Dihra", "Amarr", "Jita")
DEFAULT_HISTORY_LIMIT = 25
DEFAULT_PET_SPEECH_ENGINE = RESPONSE_ENGINE_WINDOWS
DEFAULT_PET_SPEECH_STYLE = DEFAULT_POWER_BALLAD_INSTRUCTIONS
DEFAULT_VOICE_PREVIEW_TEXT = "Intel Pet voice online. Systems are green."
DEFAULT_VOICE_PROFILE = ROOT / "profiles" / "eve_sample.json"
USER_VOICE_PROFILE = ROOT / "profiles" / "my_eve_commands.json"
VOICE_ENGINE_LOCAL = "Local (offline)"
VOICE_ENGINE_WHISPER = "Whisper local dictation"
VOICE_ENGINE_OPENAI = "OpenAI realtime"
VOICE_ENGINES = (VOICE_ENGINE_LOCAL, VOICE_ENGINE_WHISPER, VOICE_ENGINE_OPENAI)
DEFAULT_VOICE_ENGINE = VOICE_ENGINE_LOCAL
DEFAULT_INPUT_DEVICE_LABEL = "System default"
DEFAULT_VOICE_TARGET_TITLE = "EVE"
DEFAULT_VOICE_MODEL_LABEL = f"Default small ({DEFAULT_MODEL_NAME})"
RECOMMENDED_VOICE_MODEL_LABEL = f"Recommended lgraph ({RECOMMENDED_MODEL_NAME})"
DEFAULT_DISCORD_NOTE_SETTINGS_PATH = ROOT / "profiles" / "intel_pet_discord_notes.json"
DEFAULT_DISCORD_NOTE_SENDER = "IntelPet Notes"
DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR = "INTEL_PET_DISCORD_ALERT_WEBHOOK_URL"
DEFAULT_DISCORD_ALERT_KINDS = ("help", "hostile")
DEFAULT_DISCORD_ALERT_MIN_SECONDS = 30.0
DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES = ("take a note", "take note", "note", "remember this")
DEFAULT_DISCORD_NOTE_CLOSE_PHRASES = ("send note", "end note", "finish note", "note done")
DEFAULT_DISCORD_NOTE_CANCEL_PHRASES = ("cancel note", "never mind", "forget note")
DISCORD_NOTE_IDLE_SEND_SECONDS = 2.0
MAX_DISCORD_NOTE_TEXT_LENGTH = 1500
MISSION_VOICE_GRAMMAR_LIMIT = 200
MISSION_READ_PREFIXES = (
    "read mission",
    "read quest",
    "read briefing",
    "mission briefing",
    "quest briefing",
    "tell me mission",
    "tell me quest",
)
MISSION_CACHE_PREFIXES = (
    "cache mission",
    "cache quest",
    "cache briefing",
)
COMMAND_PHRASE_SPLIT_RE = re.compile(r"[\n,]+")
PET_VOICE_STYLE_PRESETS = (
    (
        "Power ballad",
        DEFAULT_POWER_BALLAD_INSTRUCTIONS,
        "Intel Pet voice online. Engines bright, warnings sharp.",
    ),
    (
        "Clear comms",
        "Speak as a calm starship AI on a clear fleet channel: concise, steady, and easy to understand.",
        "Intel Pet online. Clear comms, clean signal.",
    ),
    (
        "Mission control",
        "Speak as a professional mission-control operator: warm, precise, and lightly dramatic.",
        "Mission control confirms. The route is open.",
    ),
    (
        "Tiny scout",
        "Speak as a small loyal scout ship AI: upbeat, quick, and helpful without being childish.",
        "Scout ship online. I found a signal for you.",
    ),
)
LOCATION_SCOPE = "esi-location.read_location.v1"
OVERLAY_IDLE_WIDTH = 220
OVERLAY_ALERT_WIDTH = 430
OVERLAY_HEIGHT = 176
AURA_BUBBLE_SCAN_X = (34, 66, 98, 130, 162, 194, 226, 258)
AURA_BUBBLE_NODE_COUNT = 7
BEHAVIOR_ALERT = "alert"
BEHAVIOR_HAPPY = "happy"
BEHAVIOR_COMBAT = "combat"
BEHAVIOR_IDLE = "idle"
BEHAVIOR_NONE = "none"
BEHAVIOR_LONG_MOVE = "long_move"
BEHAVIOR_LONG_COMBAT = "long_combat"
BEHAVIOR_LONG_COMBO = "long_combo"
BEHAVIOR_ROBOT_MINER = "robot_miner"
BEHAVIOR_OPTIONS = (
    (BEHAVIOR_ALERT, "Alert pulse", "quick turret and engine pulse"),
    (BEHAVIOR_HAPPY, "Happy flight", "small cheerful loop"),
    (BEHAVIOR_COMBAT, "Combat burst", "fast flight with laser shots"),
    (BEHAVIOR_LONG_MOVE, "Long flight", "extended movement loop"),
    (BEHAVIOR_LONG_COMBAT, "Long shooting", "extended laser volley"),
    (BEHAVIOR_LONG_COMBO, "Long combo", "extended flight and shooting"),
    (BEHAVIOR_ROBOT_MINER, "Stout robot miner", "morph, pickaxe, and eye lasers"),
    (BEHAVIOR_IDLE, "Calm wiggle", "quiet idle flutter"),
    (BEHAVIOR_NONE, "No animation", "message only"),
)
ALERT_BEHAVIOR_KINDS = (
    ("mention", "Pilot mention", "When someone says one of your pilot names."),
    ("help", "Help call", "When a help phrase or aid call is detected."),
    ("hostile", "Hostile intel", "When hostile, red, neutral, or war-target intel matches."),
    ("keyword", "Keyword match", "When an extra local keyword matches."),
    ("location", "System arrival", "When location cheer sees a happy system."),
    ("combat", "Kill cheer", "When a local game-log kill line appears."),
    ("mission", "Agent mission", "When a local game-log mission acceptance or completion appears."),
)
SPOKEN_ALERT_KINDS = ALERT_BEHAVIOR_KINDS
DEFAULT_ALERT_BEHAVIORS = {
    "mention": BEHAVIOR_ALERT,
    "help": BEHAVIOR_ALERT,
    "hostile": BEHAVIOR_ALERT,
    "keyword": BEHAVIOR_ALERT,
    "location": BEHAVIOR_HAPPY,
    "combat": BEHAVIOR_COMBAT,
    "mission": BEHAVIOR_HAPPY,
}
SHIP_FRAME_COUNT = 8
ROBOT_MINER_FRAME_COUNT = 12
SHIP_FRAME_MS = 150
IDLE_ANIMATION_MS = 5 * 60 * 1000
IDLE_SPRITE_SEQUENCE = (0, 1, 2, 3, 4, 5, 6, 7, 0)
ALERT_SPRITE_SEQUENCE = (0, 7, 6, 5, 4, 3, 2, 1, 0)
HAPPY_SPRITE_STEPS = (
    (0, 0, 0),
    (1, 10, -8),
    (2, 18, 0),
    (3, 10, 8),
    (4, 0, 0),
    (5, -10, -8),
    (6, -18, 0),
    (7, -10, 8),
    (0, 0, 0),
)
LONG_MOVE_SPRITE_STEPS = (
    (0, 0, 0),
    (1, 12, -12),
    (2, 26, -16),
    (3, 34, -4),
    (4, 28, 12),
    (5, 12, 18),
    (6, -8, 14),
    (7, -24, 4),
    (0, -30, -10),
    (1, -16, -18),
    (2, 4, -12),
    (3, 22, 0),
    (4, 34, 12),
    (5, 16, 18),
    (6, -4, 12),
    (7, -22, -2),
    (0, -10, -12),
    (1, 0, 0),
)
KILL_SPRITE_STEPS = (
    (0, 0, 0, 148, 44),
    (7, 18, -12, 156, 28),
    (6, 20, 2, 154, 70),
    (5, 16, 16, 150, 96),
    (4, -8, 8, 144, 76),
    (3, -18, -10, 136, 40),
    (2, -8, -18, 150, 26),
    (1, 18, -4, 158, 54),
    (0, 0, 0, 148, 64),
)
LONG_COMBAT_SPRITE_STEPS = (
    (0, 0, 0, 152, 28),
    (7, 6, -4, 158, 42),
    (6, 10, 0, 154, 58),
    (5, 8, 4, 158, 74),
    (4, 2, 8, 150, 92),
    (3, -4, 4, 144, 36),
    (2, -8, -2, 156, 52),
    (1, -4, -8, 148, 76),
    (0, 4, -10, 158, 24),
    (7, 12, -4, 154, 64),
    (6, 8, 6, 150, 96),
    (5, -2, 10, 156, 44),
    (4, -10, 4, 148, 60),
    (3, -12, -6, 158, 80),
    (2, -4, -12, 144, 32),
    (1, 8, -6, 156, 70),
    (0, 0, 0, 148, 64),
)
LONG_COMBO_SPRITE_STEPS = (
    (0, 0, 0, 148, 36),
    (1, 14, -12, 156, 24),
    (2, 28, -16, 158, 48),
    (3, 36, -4, 154, 72),
    (4, 28, 12, 150, 96),
    (5, 10, 18, 156, 58),
    (6, -10, 14, 146, 38),
    (7, -26, 2, 158, 82),
    (0, -32, -12, 148, 100),
    (1, -18, -20, 154, 26),
    (2, 4, -14, 158, 54),
    (3, 24, -2, 150, 86),
    (4, 34, 12, 156, 42),
    (5, 18, 20, 146, 70),
    (6, -4, 14, 158, 96),
    (7, -24, 0, 150, 34),
    (0, -10, -12, 156, 62),
    (1, 0, 0, 148, 64),
)
ROBOT_MINER_STEPS = (
    (0, 0, 0, "none"),
    (1, 0, -2, "spark"),
    (2, 0, 0, "spark"),
    (3, 0, 0, "none"),
    (4, -2, -2, "spark"),
    (5, 4, 4, "spark"),
    (6, 0, -2, "laser"),
    (7, -2, 0, "laser"),
    (8, 4, 3, "laser"),
    (4, -2, -2, "spark"),
    (5, 4, 4, "spark"),
    (6, 0, -2, "laser"),
    (8, 4, 2, "laser"),
    (9, 0, 0, "none"),
    (10, 0, 2, "spark"),
    (11, 0, 0, "none"),
)
GAME_LOG_LINE_RE = re.compile(
    r"^\s*\[\s*(?P<timestamp>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*\]\s*(?P<body>.*)$"
)
GAME_LOG_PREFIX_RE = re.compile(r"^\s*\((?:combat|notify|info|warning|question)\)\s*", re.IGNORECASE)
GAME_LOG_TAG_RE = re.compile(r"<[^>]+>")
KILL_EVENT_PATTERNS = (
    re.compile(r"\byou (?:have )?(?:destroyed|killed)\b", re.IGNORECASE),
    re.compile(r"\b(?:destroyed|killed) by you\b", re.IGNORECASE),
    re.compile(r"\bfinal blow\b", re.IGNORECASE),
    re.compile(r"\bhas been destroyed\b", re.IGNORECASE),
    re.compile(r"\bwas destroyed\b", re.IGNORECASE),
)
SELF_LOSS_PATTERNS = (
    re.compile(r"\byou (?:have )?been destroyed\b", re.IGNORECASE),
    re.compile(r"\byour (?:ship|capsule|pod)\b.*\bhas been destroyed\b", re.IGNORECASE),
)
MISSION_ACCEPT_PATTERNS = (
    re.compile(r"\bmission\b.*\baccepted\b", re.IGNORECASE),
    re.compile(r"\baccepted\b.*\bmission\b", re.IGNORECASE),
)
MISSION_COMPLETE_PATTERNS = (
    re.compile(r"\bmission\b.*\b(?:complete|completed|finished|accomplished)\b", re.IGNORECASE),
    re.compile(r"\b(?:complete|completed|finished|accomplished)\b.*\bmission\b", re.IGNORECASE),
    re.compile(r"\bobjectives?\s+complete\b", re.IGNORECASE),
)
MISSION_NEGATIVE_PATTERNS = (
    re.compile(r"\b(?:not|isn't|is not|cannot|can't)\b.*\b(?:complete|completed|finish|finished)\b", re.IGNORECASE),
    re.compile(r"\bmission\b.*\b(?:failed|expired|declined)\b", re.IGNORECASE),
)
MISSION_COMMENTS = {
    "accepted": (
        "Agent contract accepted. Engines warm, capsuleer.",
        "Mission logged. The agent's clock is ticking.",
        "Orders received. Let the void keep pace.",
    ),
    "completed": (
        "Mission complete. The agent will want this report.",
        "Objective secured. Another entry for the capsuleer ledger.",
        "Contract fulfilled. The cluster owes you a quieter minute.",
    ),
}


def default_alert_behaviors() -> dict[str, str]:
    return dict(DEFAULT_ALERT_BEHAVIORS)


def default_spoken_alert_kinds() -> dict[str, bool]:
    return {kind: True for kind, _label, _description in SPOKEN_ALERT_KINDS}


def clean_response_engine(value: Any) -> str:
    engine = str(value or "").strip()
    return engine if engine in RESPONSE_ENGINES else DEFAULT_PET_SPEECH_ENGINE


def clean_response_voice(value: Any) -> str:
    voice = str(value or "").strip()
    return voice or DEFAULT_OPENAI_TTS_VOICE


def clean_response_style(value: Any) -> str:
    style = str(value or "").strip()
    return style or DEFAULT_PET_SPEECH_STYLE


def pet_voice_preset_names() -> tuple[str, ...]:
    return tuple(name for name, _style, _preview in PET_VOICE_STYLE_PRESETS)


def pet_voice_style_for_preset(name: Any) -> str:
    selected = str(name or "").strip()
    for preset_name, style, _preview in PET_VOICE_STYLE_PRESETS:
        if preset_name.casefold() == selected.casefold():
            return style
    return DEFAULT_PET_SPEECH_STYLE


def pet_voice_preview_for_preset(name: Any) -> str:
    selected = str(name or "").strip()
    for preset_name, _style, preview in PET_VOICE_STYLE_PRESETS:
        if preset_name.casefold() == selected.casefold():
            return preview
    return DEFAULT_VOICE_PREVIEW_TEXT


def pet_voice_preset_for_style(style: Any) -> str:
    cleaned = clean_response_style(style)
    for preset_name, preset_style, _preview in PET_VOICE_STYLE_PRESETS:
        if preset_style == cleaned:
            return preset_name
    return "Custom"


def clean_voice_preview_text(value: Any) -> str:
    text = normalize_response_text(str(value or "").replace("\n", ". "))
    text = re.sub(r"\.\s*\.", ".", text)
    return text[:240].strip() or DEFAULT_VOICE_PREVIEW_TEXT


def clean_mission_read_opener(value: Any) -> str:
    text = normalize_response_text(str(value or "").replace("\n", " "))
    return text[:80].strip() or MissionReadOptions.opener


def mission_read_options_from_settings(settings: "IntelPetSettings") -> MissionReadOptions:
    return MissionReadOptions(
        opener=clean_mission_read_opener(settings.mission_read_opener),
        include_giver=bool(settings.mission_read_include_giver),
        include_level=bool(settings.mission_read_include_level),
        include_rewards=bool(settings.mission_read_include_rewards),
        include_reward_notes=bool(settings.mission_read_include_reward_notes),
        include_source=bool(settings.mission_read_include_source),
        include_completion=bool(settings.mission_read_include_completion),
        include_briefing=bool(settings.mission_read_include_briefing),
    )


def clean_voice_engine(value: Any) -> str:
    engine = str(value or "").strip()
    return engine if engine in VOICE_ENGINES else DEFAULT_VOICE_ENGINE


def clean_voice_whisper_model(value: Any) -> str:
    model = str(value or "").strip()
    return model if model in LOCAL_WHISPER_MODELS else DEFAULT_LOCAL_WHISPER_MODEL


def clean_voice_model_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text == DEFAULT_VOICE_MODEL_LABEL:
        return ""
    if text == RECOMMENDED_VOICE_MODEL_LABEL:
        return str(RECOMMENDED_MODEL_PATH)
    if text.startswith("Installed: "):
        text = text.split("Installed: ", maxsplit=1)[1].strip()
    return text


def voice_model_path(settings_value: Any) -> Path:
    cleaned = clean_voice_model_path(settings_value)
    if not cleaned:
        return DEFAULT_MODEL_PATH
    path = Path(cleaned).expanduser()
    return path if path.is_absolute() else ROOT / path


def voice_model_display(settings_value: Any) -> str:
    cleaned = clean_voice_model_path(settings_value)
    if not cleaned:
        return DEFAULT_VOICE_MODEL_LABEL
    path = voice_model_path(cleaned)
    try:
        if path.resolve() == RECOMMENDED_MODEL_PATH.resolve():
            return RECOMMENDED_VOICE_MODEL_LABEL
    except OSError:
        pass
    return cleaned


def installed_voice_model_labels() -> tuple[str, ...]:
    labels = [DEFAULT_VOICE_MODEL_LABEL, RECOMMENDED_VOICE_MODEL_LABEL]
    models_root = DEFAULT_MODEL_PATH.parent
    if models_root.exists():
        for path in sorted(models_root.iterdir()):
            if path.is_dir() and (path / "conf" / "model.conf").exists():
                label = DEFAULT_VOICE_MODEL_LABEL if path == DEFAULT_MODEL_PATH else f"Installed: {path}"
                if label not in labels:
                    labels.append(label)
    return tuple(labels)


def voice_model_status(settings_value: Any) -> str:
    path = voice_model_path(settings_value)
    config = path / "conf" / "model.conf"
    if config.exists():
        return f"Model ready: {path}"
    return f"Model missing: {path}. Run .\\scripts\\download-vosk-model.ps1 -ModelName {path.name}"


def clean_voice_input_device(value: Any) -> str:
    label = str(value or "").strip()
    return "" if label == DEFAULT_INPUT_DEVICE_LABEL else label


def voice_input_device_display(value: Any) -> str:
    return clean_voice_input_device(value) or DEFAULT_INPUT_DEVICE_LABEL


def clean_voice_call_sign(value: Any) -> str:
    call_sign = str(value or "").strip()
    return call_sign or DEFAULT_RESPONSE_CALL_SIGN


def clean_voice_target_title(value: Any) -> str:
    title = str(value or "").strip()
    return title or DEFAULT_VOICE_TARGET_TITLE


def clean_discord_note_text(value: Any) -> str:
    text = normalize_response_text(str(value or "").replace("\n", " "))
    return text[:MAX_DISCORD_NOTE_TEXT_LENGTH].strip()


def join_discord_note_parts(parts: Iterable[str]) -> str:
    return clean_discord_note_text(" ".join(part for part in parts if clean_discord_note_text(part)))


def clean_discord_note_sender(value: Any) -> str:
    sender = " ".join(str(value or "").strip().split())
    return sender[:80] or DEFAULT_DISCORD_NOTE_SENDER


def clean_discord_note_phrases(value: Any, *, default: Iterable[str]) -> tuple[str, ...]:
    phrases = clean_voice_command_phrases(value if value is not None else default)
    normalized: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        clean_phrase = normalize_phrase(phrase)
        if clean_phrase and clean_phrase not in seen:
            normalized.append(clean_phrase)
            seen.add(clean_phrase)
    return tuple(normalized) or tuple(normalize_phrase(item) for item in default)


def validate_discord_note_webhook_url(webhook_url: str) -> None:
    from eve_voice_pilot.corp_market import validate_discord_webhook_url

    validate_discord_webhook_url(webhook_url)


def clean_discord_note_webhook_url(value: Any, *, allow_blank: bool = True) -> str:
    webhook_url = str(value or "").strip()
    if not webhook_url:
        if allow_blank:
            return ""
        raise CorpIntelError("Discord note webhook URL is required.")
    try:
        validate_discord_note_webhook_url(webhook_url)
    except Exception as exc:
        raise CorpIntelError(str(exc)) from exc
    return webhook_url


def post_discord_note_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 10.0,
) -> Any:
    from eve_voice_pilot.corp_market import post_discord_webhook

    return post_discord_webhook(webhook_url, payload, timeout_seconds=timeout_seconds)


def pet_openai_api_key() -> str:
    for name in ("INTEL_PET_OPENAI_API_KEY", "OPENAI_API_KEY", "EVE_VOICE_OPENAI_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        return str(load_app_settings().get("api_key", "")).strip()
    except Exception:
        return ""


def pet_elevenlabs_api_key() -> str:
    for name in ("INTEL_PET_ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY", "ELEVEN_LABS_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def pet_response_api_key(engine: str) -> str:
    if clean_response_engine(engine) == RESPONSE_ENGINE_ELEVENLABS:
        return pet_elevenlabs_api_key()
    return pet_openai_api_key()


def pet_response_model(engine: str) -> str:
    if clean_response_engine(engine) == RESPONSE_ENGINE_ELEVENLABS:
        return DEFAULT_ELEVENLABS_TTS_MODEL
    return DEFAULT_OPENAI_TTS_MODEL


def pet_response_voice(engine: str, voice: str) -> str:
    if clean_response_engine(engine) == RESPONSE_ENGINE_ELEVENLABS:
        return elevenlabs_voice_id(voice)
    return clean_response_voice(voice)


def spoken_pet_text(text: str) -> str:
    return normalize_response_text(str(text or "").replace("\n", ". "))


def load_voice_profile() -> tuple[CommandProfile, Path]:
    try:
        app_settings = load_app_settings()
    except Exception:
        app_settings = {}
    candidates = (
        Path(str(app_settings.get("profile_path", "")).strip()) if str(app_settings.get("profile_path", "")).strip() else None,
        USER_VOICE_PROFILE,
        DEFAULT_VOICE_PROFILE,
    )
    for candidate in candidates:
        if candidate and candidate.exists():
            return CommandProfile.load(candidate), candidate
    raise CorpIntelError("No EVE Voice Pilot command profile was found.")


def editable_voice_profile_path(source_path: Path) -> Path:
    try:
        if Path(source_path).resolve() == DEFAULT_VOICE_PROFILE.resolve():
            return USER_VOICE_PROFILE
    except OSError:
        pass
    return Path(source_path)


def load_editable_voice_profile() -> tuple[CommandProfile, Path, Path]:
    profile, source_path = load_voice_profile()
    return profile, editable_voice_profile_path(source_path), source_path


def clean_voice_command_phrases(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = COMMAND_PHRASE_SPLIT_RE.split(value)
    else:
        raw_items = [str(item) for item in value]
    phrases: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        phrase = " ".join(str(item).strip().split())
        folded = phrase.casefold()
        if phrase and folded not in seen:
            phrases.append(phrase)
            seen.add(folded)
    return phrases


def clean_voice_training_phrase(value: Any, *, response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN) -> str:
    phrase = normalize_response_text(str(value or ""))
    if response_call_sign:
        cleaned, _response_requested = strip_response_call_sign(
            phrase,
            response_call_signs(clean_voice_call_sign(response_call_sign)),
        )
        phrase = cleaned or phrase
    return normalize_response_text(phrase)


def voice_command_with_added_phrase(
    command: VoiceCommand,
    phrase: Any,
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
) -> VoiceCommand:
    cleaned_phrase = clean_voice_training_phrase(phrase, response_call_sign=response_call_sign)
    phrases = clean_voice_command_phrases((*command.phrases, cleaned_phrase))
    if phrases == command.phrases:
        return command
    return replace(command, phrases=phrases)


def voice_command_from_fields(
    *,
    name: Any,
    phrases: Any,
    key: Any,
    hold_seconds: Any = DEFAULT_HOLD_SECONDS,
    press_count: Any = DEFAULT_PRESS_COUNT,
    repeat_gap_seconds: Any = DEFAULT_REPEAT_GAP_SECONDS,
    response_suffix: Any = "",
    response_text: Any = "",
) -> VoiceCommand:
    clean_name = " ".join(str(name or "").strip().split())
    clean_phrases = clean_voice_command_phrases(phrases)
    clean_key = str(key or "").strip().upper()
    if not clean_name:
        raise ValueError("Give the command a short name.")
    if not clean_phrases:
        raise ValueError("Add at least one spoken phrase.")
    try:
        parse_key_chord(clean_key)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    try:
        clean_hold_seconds = float(hold_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Hold seconds should be a number, like 0.10.") from exc
    if not 0.01 <= clean_hold_seconds <= 2.0:
        raise ValueError("Hold seconds should be between 0.01 and 2.0.")
    try:
        clean_press_count = int(press_count)
    except (TypeError, ValueError) as exc:
        raise ValueError("Press count should be a whole number, like 1 or 2.") from exc
    if clean_press_count != 1:
        raise ValueError("Voice commands must send one key or key chord one time. Use 1.")
    try:
        clean_repeat_gap_seconds = float(repeat_gap_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Delay between presses should be a number, like 0.10.") from exc
    if not 0.0 <= clean_repeat_gap_seconds <= 2.0:
        raise ValueError("Delay between presses should be between 0.00 and 2.0.")
    return VoiceCommand(
        name=clean_name,
        phrases=clean_phrases,
        key=clean_key,
        hold_seconds=clean_hold_seconds,
        press_count=clean_press_count,
        repeat_gap_seconds=clean_repeat_gap_seconds,
        response_suffix=str(response_suffix or "").strip(),
        response_text=str(response_text or "").strip(),
    )


def voice_command_matches_filter(command: VoiceCommand, query: Any) -> bool:
    tokens = normalize_response_text(str(query or "")).casefold().split()
    if not tokens:
        return True
    haystack = normalize_response_text(
        " ".join((command.name, command.key, command.action_summary, " ".join(command.phrases)))
    ).casefold()
    return all(token in haystack for token in tokens)


def filtered_voice_command_indices(commands: list[VoiceCommand], query: Any) -> tuple[int, ...]:
    return tuple(index for index, command in enumerate(commands) if voice_command_matches_filter(command, query))


def next_voice_command_copy_name(name: str, existing_names: Iterable[str]) -> str:
    clean_name = " ".join(str(name or "Command").split()) or "Command"
    folded_existing = {str(item).casefold() for item in existing_names}
    first = f"{clean_name} Copy"
    if first.casefold() not in folded_existing:
        return first
    for index in range(2, 100):
        candidate = f"{clean_name} Copy {index}"
        if candidate.casefold() not in folded_existing:
            return candidate
    return f"{clean_name} Copy {int(time.time())}"


def duplicate_voice_command(command: VoiceCommand, existing_names: Iterable[str]) -> VoiceCommand:
    return replace(command, name=next_voice_command_copy_name(command.name, existing_names))


def voice_command_preview_text(command: VoiceCommand | None) -> str:
    if command is None:
        return "No command selected."
    phrases = ", ".join(command.phrases) if command.phrases else "(no phrases)"
    lines = [
        f"{command.name}",
        f"Keybind: {command.action_summary}",
        f"Phrases: {phrases}",
    ]
    if command.response_suffix:
        lines.append(f"Voice label: {command.response_suffix}")
    if command.response_text:
        lines.append(f"Response text: {command.response_text}")
    return "\n".join(lines)


def voice_input_device_index(label: str) -> int | None:
    return resolve_input_device_label(clean_voice_input_device(label))


def voice_command_signature(commands: Iterable[VoiceCommand]) -> tuple[tuple[str, tuple[str, ...], str, float, int, float], ...]:
    return tuple(
        (command.name, tuple(command.phrases), command.key, command.hold_seconds, command.press_count, command.repeat_gap_seconds)
        for command in commands
    )


def execute_voice_command(
    command: VoiceCommand,
    *,
    allow_command_sending: bool,
    require_target_window: bool,
    target_title: str,
    active_window_lookup: Callable[[], str] = active_window_title,
    key_sender: Callable[[str, float], None] = send_key_chord,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    if not allow_command_sending:
        return "Practice only. No key sent.", "info"

    if require_target_window:
        title = active_window_lookup()
        required = clean_voice_target_title(target_title).casefold()
        if required not in title.casefold():
            return f"Did not send {command.key}; active window is {title!r}.", "high"

    try:
        for press_index in range(command.press_count):
            key_sender(command.key, command.hold_seconds)
            if press_index < command.press_count - 1:
                sleeper(command.repeat_gap_seconds)
    except Exception as exc:
        return f"Could not send {command.key}: {exc}", "high"
    return f"Sent {command.action_summary}.", "info"


def voice_status_from_transcript(
    transcript: str,
    commands: list[VoiceCommand],
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
    allow_command_sending: bool = False,
    require_target_window: bool = True,
    target_title: str = DEFAULT_VOICE_TARGET_TITLE,
    voice_engine: str = "",
    active_window_lookup: Callable[[], str] = active_window_title,
    key_sender: Callable[[str, float], None] = send_key_chord,
    sleeper: Callable[[float], None] = time.sleep,
) -> "IntelPetVoiceStatus | None":
    heard = normalize_response_text(transcript)
    if not heard:
        return None
    cleaned, _response_requested = strip_response_call_sign(heard, response_call_signs(response_call_sign))
    match = find_exact_phrase_match(cleaned, commands)
    if match:
        result, severity = execute_voice_command(
            match.command,
            allow_command_sending=allow_command_sending,
            require_target_window=require_target_window,
            target_title=target_title,
            active_window_lookup=active_window_lookup,
            key_sender=key_sender,
            sleeper=sleeper,
        )
        detail = "\n".join(
            (
                f"Heard: {heard}",
                f"Matched: {match.command.name} -> {match.command.action_summary}",
                result,
            )
        )
        if result.startswith("Sent "):
            title = "Voice command sent"
        elif result.startswith("Practice only"):
            title = "Voice command matched"
        else:
            title = "Voice command blocked"
        return IntelPetVoiceStatus(
            title=title,
            detail=detail,
            severity=severity,
            recorded_at=now_iso(),
            heard=heard,
            engine=clean_voice_engine(voice_engine) if voice_engine else "",
            active_window_check=active_window_check_summary(
                allow_command_sending=allow_command_sending,
                require_target_window=require_target_window,
                target_title=target_title,
            ),
        )
    return IntelPetVoiceStatus(
        title="Voice heard",
        detail=f"Heard: {heard}\nNo exact command matched.",
        severity="info",
        recorded_at=now_iso(),
        heard=heard,
        engine=clean_voice_engine(voice_engine) if voice_engine else "",
        active_window_check=active_window_check_summary(
            allow_command_sending=allow_command_sending,
            require_target_window=require_target_window,
            target_title=target_title,
        ),
    )


def split_mission_voice_request(cleaned_transcript: str, prefixes: Iterable[str]) -> tuple[str, bool]:
    cleaned = normalize_phrase(cleaned_transcript)
    if not cleaned:
        return "", False
    for prefix in prefixes:
        normalized_prefix = normalize_phrase(prefix)
        if cleaned == normalized_prefix:
            return "", True
        prefix_with_space = f"{normalized_prefix} "
        if cleaned.startswith(prefix_with_space):
            return cleaned[len(prefix_with_space) :].strip(), True
    return "", False


def mission_voice_grammar_commands(
    entries: Iterable[MissionLibraryEntry],
    *,
    limit: int = MISSION_VOICE_GRAMMAR_LIMIT,
) -> list[VoiceCommand]:
    commands: list[VoiceCommand] = []
    for entry in list(entries)[: max(0, limit)]:
        phrases = [
            f"{prefix} {entry.title}"
            for prefix in (*MISSION_READ_PREFIXES, *MISSION_CACHE_PREFIXES)
        ]
        commands.append(
            VoiceCommand(
                name=f"Mission briefing: {entry.title}",
                phrases=phrases,
                key="",
            )
        )
    return commands


def mission_voice_status_from_transcript(
    transcript: str,
    entries: Iterable[MissionLibraryEntry],
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
    voice_engine: str = "",
    read_options: MissionReadOptions | None = None,
    play_text: Callable[[str, str], None] | None = None,
    prepare_text: Callable[[str, str, bool], None] | None = None,
) -> "IntelPetVoiceStatus | None":
    heard = normalize_response_text(transcript)
    if not heard:
        return None
    cleaned, _response_requested = strip_response_call_sign(heard, response_call_signs(response_call_sign))
    query, read_requested = split_mission_voice_request(cleaned, MISSION_READ_PREFIXES)
    if not read_requested:
        query, cache_requested = split_mission_voice_request(cleaned, MISSION_CACHE_PREFIXES)
    else:
        cache_requested = False
    if not read_requested and not cache_requested:
        return None

    recorded_at = now_iso()
    mission_entries = tuple(entries)
    engine_label = clean_voice_engine(voice_engine) if voice_engine else ""
    if not mission_entries:
        return IntelPetVoiceStatus(
            title="Mission library empty",
            detail="No local mission library entries are loaded. Add missions to profiles\\intel_pet_missions.json or the bundled starter data.",
            severity="high",
            recorded_at=recorded_at,
            heard=heard,
            engine=engine_label,
        )
    if not query:
        return IntelPetVoiceStatus(
            title="Mission name needed",
            detail=f"Heard: {heard}\nSay a mission name after the request, such as: {clean_voice_call_sign(response_call_sign)} read mission Cash Flow for Capsuleers.",
            severity="info",
            recorded_at=recorded_at,
            heard=heard,
            engine=engine_label,
        )

    matches = find_mission_entries(query, mission_entries, limit=4)
    if not matches:
        return IntelPetVoiceStatus(
            title="Mission not found",
            detail=f"Heard: {heard}\nNo mission matched: {query}",
            severity="info",
            recorded_at=recorded_at,
            heard=heard,
            engine=engine_label,
        )

    entry = matches[0]
    spoken_text = mission_read_aloud_text(entry, read_options)
    alternatives = ", ".join(match.title for match in matches[1:])
    if cache_requested:
        if prepare_text is not None:
            prepare_text(spoken_text, f"mission briefing for {entry.title}", False)
        action = "Queued cached voice for"
        title = "Mission voice cache queued"
    else:
        if play_text is not None:
            play_text(spoken_text, f"mission briefing for {entry.title}")
        action = "Reading"
        title = "Mission briefing"
    detail_lines = [
        f"Heard: {heard}",
        f"{action}: {entry.title}",
        f"Mission giver: {entry.giver_label}",
        f"Rewards: {entry.reward_summary}",
    ]
    if alternatives:
        detail_lines.append(f"Other close matches: {alternatives}")
    return IntelPetVoiceStatus(
        title=title,
        detail="\n".join(detail_lines),
        severity="info",
        recorded_at=recorded_at,
        heard=heard,
        engine=engine_label,
    )


def discord_note_intent_from_transcript(
    transcript: str,
    settings: IntelPetDiscordNoteSettings,
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
) -> IntelPetDiscordNoteIntent | None:
    heard = normalize_response_text(transcript)
    if not heard:
        return None
    cleaned, _response_requested = strip_response_call_sign(heard, response_call_signs(response_call_sign))
    if not cleaned:
        return None

    cancel_phrases = clean_discord_note_phrases(
        settings.cancel_phrases,
        default=DEFAULT_DISCORD_NOTE_CANCEL_PHRASES,
    )
    for phrase in cancel_phrases:
        if cleaned == phrase:
            return IntelPetDiscordNoteIntent(action="cancel")

    trigger_phrases = clean_discord_note_phrases(
        settings.trigger_phrases,
        default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
    )
    for phrase in trigger_phrases:
        if cleaned == phrase:
            return IntelPetDiscordNoteIntent(action="arm")
        prefix = f"{phrase} "
        if cleaned.startswith(prefix):
            note_text = clean_discord_note_text(cleaned[len(prefix) :])
            if note_text:
                return IntelPetDiscordNoteIntent(action="send", note_text=note_text)
            return IntelPetDiscordNoteIntent(action="arm")
    return None


def split_discord_note_close_phrase(
    note_text: str,
    settings: IntelPetDiscordNoteSettings,
) -> tuple[str, bool]:
    note = clean_discord_note_text(note_text)
    if not note:
        return "", False
    close_phrases = clean_discord_note_phrases(
        settings.close_phrases,
        default=DEFAULT_DISCORD_NOTE_CLOSE_PHRASES,
    )
    for phrase in close_phrases:
        if note == phrase:
            return "", True
        suffix = f" {phrase}"
        if note.endswith(suffix):
            return clean_discord_note_text(note[: -len(suffix)]), True
    return note, False


def build_discord_note_payload(
    note_text: str,
    settings: IntelPetDiscordNoteSettings,
    *,
    pilot_name: str = "",
    recorded_at: str | None = None,
) -> dict[str, Any]:
    note = clean_discord_note_text(note_text)
    if not note:
        raise CorpIntelError("Discord note text is empty.")
    timestamp = recorded_at or now_iso()
    lines = [
        "**Intel Pet Note**",
        note,
        "",
        f"Recorded: {timestamp}",
    ]
    clean_pilot = " ".join(str(pilot_name or "").split())
    if clean_pilot:
        lines.append(f"Pilot: {clean_pilot[:120]}")
    return {
        "username": clean_discord_note_sender(settings.sender_name),
        "content": "\n".join(lines)[:2000],
        "allowed_mentions": {"parse": []},
    }


def send_discord_note(
    note_text: str,
    settings: IntelPetDiscordNoteSettings,
    *,
    pilot_name: str = "",
    poster: Callable[..., Any] = post_discord_note_webhook,
) -> IntelPetVoiceStatus:
    recorded_at = now_iso()
    note = clean_discord_note_text(note_text)
    if not note:
        return IntelPetVoiceStatus(
            title="Discord note skipped",
            detail="No note text heard.",
            severity="info",
            recorded_at=recorded_at,
        )
    if not settings.enabled:
        return IntelPetVoiceStatus(
            title="Discord notes disabled",
            detail="Discord notes are off. Enable them in Options > Notes before sending voice notes.",
            severity="high",
            recorded_at=recorded_at,
        )
    if not settings.webhook_url:
        return IntelPetVoiceStatus(
            title="Discord note blocked",
            detail="No Discord note webhook is configured.",
            severity="high",
            recorded_at=recorded_at,
        )
    try:
        payload = build_discord_note_payload(note, settings, pilot_name=pilot_name, recorded_at=recorded_at)
        poster(settings.webhook_url, payload, timeout_seconds=10.0)
    except Exception as exc:
        return IntelPetVoiceStatus(
            title="Discord note failed",
            detail=f"Could not send note: {exc}",
            severity="high",
            recorded_at=recorded_at,
        )
    return IntelPetVoiceStatus(
        title="Discord note sent",
        detail=f"Note sent to Discord notes: {note}",
        severity="info",
        recorded_at=recorded_at,
    )


def discord_note_recording_status(
    state: IntelPetDiscordNoteCaptureState,
    settings: IntelPetDiscordNoteSettings,
    *,
    heard: str = "",
    engine: str = "",
    active_window_check: str = "",
) -> IntelPetVoiceStatus:
    close = first_discord_note_close_phrase(settings)
    cancel = first_discord_note_cancel_phrase(settings)
    note = state.note_text
    detail = f'Note capture is recording. Pause for 2 seconds to send, say "{close}" to send now, or say "{cancel}" to cancel.'
    if note:
        detail = f"{detail}\nBuffered: {note}"
    return IntelPetVoiceStatus(
        title="Discord note recording",
        detail=detail,
        severity="info",
        recorded_at=now_iso(),
        heard=heard,
        engine=engine,
        active_window_check=active_window_check,
    )


def discord_note_initial_silence_seconds(
    state: IntelPetDiscordNoteCaptureState,
    *,
    now_seconds: float | None = None,
) -> float | None:
    if not state.active or not state.parts:
        return None
    now_value = time.monotonic() if now_seconds is None else float(now_seconds)
    elapsed = max(0.0, now_value - state.last_note_at)
    return max(0.05, DISCORD_NOTE_IDLE_SEND_SECONDS - elapsed)


def discord_note_capture_status_from_transcript(
    transcript: str,
    settings: IntelPetDiscordNoteSettings,
    *,
    state: IntelPetDiscordNoteCaptureState,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
    pilot_name: str = "",
    voice_engine: str = "",
    active_window_check: str = "",
    poster: Callable[..., Any] = post_discord_note_webhook,
    now_seconds: float | None = None,
) -> tuple[IntelPetVoiceStatus | None, IntelPetDiscordNoteCaptureState]:
    now_value = time.monotonic() if now_seconds is None else float(now_seconds)
    heard = normalize_response_text(transcript)
    cleaned, _response_requested = strip_response_call_sign(heard, response_call_signs(response_call_sign))
    clean_engine = clean_voice_engine(voice_engine) if voice_engine else ""

    def with_voice_context(status: IntelPetVoiceStatus) -> IntelPetVoiceStatus:
        return replace(
            status,
            heard=heard,
            engine=clean_engine,
            active_window_check=active_window_check,
        )

    def inactive() -> IntelPetDiscordNoteCaptureState:
        return IntelPetDiscordNoteCaptureState()

    def send_state(note_state: IntelPetDiscordNoteCaptureState) -> tuple[IntelPetVoiceStatus, IntelPetDiscordNoteCaptureState]:
        return (
            with_voice_context(send_discord_note(note_state.note_text, settings, pilot_name=pilot_name, poster=poster)),
            inactive(),
        )

    def append_text(note_state: IntelPetDiscordNoteCaptureState, note_text: str) -> IntelPetDiscordNoteCaptureState:
        text = clean_discord_note_text(note_text)
        if not text:
            return note_state
        return IntelPetDiscordNoteCaptureState(
            active=True,
            parts=(*note_state.parts, text),
            last_note_at=now_value,
        )

    intent = discord_note_intent_from_transcript(
        transcript,
        settings,
        response_call_sign=response_call_sign,
    )

    if state.active:
        if intent and intent.action == "cancel":
            return (
                with_voice_context(
                    IntelPetVoiceStatus(
                        title="Discord note canceled",
                        detail="Note capture canceled.",
                        severity="info",
                        recorded_at=now_iso(),
                    )
                ),
                inactive(),
            )
        if not cleaned:
            if state.parts and now_value - state.last_note_at >= DISCORD_NOTE_IDLE_SEND_SECONDS:
                return send_state(state)
            return None, state
        note_text = intent.note_text if intent and intent.action == "send" else cleaned
        note_text, should_send = split_discord_note_close_phrase(note_text, settings)
        updated = append_text(state, note_text)
        if should_send:
            return send_state(updated)
        if updated is not state:
            return (
                discord_note_recording_status(
                    updated,
                    settings,
                    heard=heard,
                    engine=clean_engine,
                    active_window_check=active_window_check,
                ),
                updated,
            )
        return None, updated

    if intent is None:
        return None, inactive()
    if intent.action == "cancel":
        return (
            with_voice_context(
                IntelPetVoiceStatus(
                    title="Discord note canceled",
                    detail="No note capture was active.",
                    severity="info",
                    recorded_at=now_iso(),
                )
            ),
            inactive(),
        )
    if intent.action == "arm":
        return (
            with_voice_context(
                IntelPetVoiceStatus(
                    title="Discord note ready",
                    detail=discord_note_ready_detail(settings),
                    severity="info",
                    recorded_at=now_iso(),
                )
            ),
            IntelPetDiscordNoteCaptureState(active=True, last_note_at=now_value),
        )
    note_text, should_send = split_discord_note_close_phrase(intent.note_text, settings)
    updated = append_text(IntelPetDiscordNoteCaptureState(active=True, last_note_at=now_value), note_text)
    if should_send:
        return send_state(updated)
    return (
        discord_note_recording_status(
            updated,
            settings,
            heard=heard,
            engine=clean_engine,
            active_window_check=active_window_check,
        ),
        updated,
    )


def discord_note_status_from_transcript(
    transcript: str,
    settings: IntelPetDiscordNoteSettings,
    *,
    pending_capture: bool,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
    pilot_name: str = "",
    voice_engine: str = "",
    active_window_check: str = "",
    poster: Callable[..., Any] = post_discord_note_webhook,
) -> tuple[IntelPetVoiceStatus | None, bool]:
    heard = normalize_response_text(transcript)
    cleaned, _response_requested = strip_response_call_sign(heard, response_call_signs(response_call_sign))
    clean_engine = clean_voice_engine(voice_engine) if voice_engine else ""

    def with_voice_context(status: IntelPetVoiceStatus) -> IntelPetVoiceStatus:
        return replace(
            status,
            heard=heard,
            engine=clean_engine,
            active_window_check=active_window_check,
        )

    intent = discord_note_intent_from_transcript(
        transcript,
        settings,
        response_call_sign=response_call_sign,
    )
    if pending_capture:
        if intent and intent.action == "cancel":
            return (
                with_voice_context(
                    IntelPetVoiceStatus(
                        title="Discord note canceled",
                        detail="Note capture canceled.",
                        severity="info",
                        recorded_at=now_iso(),
                    )
                ),
                False,
            )
        note_text = clean_discord_note_text(cleaned)
        if not note_text:
            return (
                with_voice_context(
                    IntelPetVoiceStatus(
                        title="Discord note waiting",
                        detail="Still waiting for note text.",
                        severity="info",
                        recorded_at=now_iso(),
                    )
                ),
                True,
            )
        return with_voice_context(send_discord_note(note_text, settings, pilot_name=pilot_name, poster=poster)), False

    if intent is None:
        return None, False
    if intent.action == "cancel":
        return (
            with_voice_context(
                IntelPetVoiceStatus(
                    title="Discord note canceled",
                    detail="No note capture was active.",
                    severity="info",
                    recorded_at=now_iso(),
                )
            ),
            False,
        )
    if intent.action == "arm":
        return (
            with_voice_context(
                IntelPetVoiceStatus(
                    title="Discord note ready",
                    detail=discord_note_ready_detail(settings),
                    severity="info",
                    recorded_at=now_iso(),
                )
            ),
            True,
        )
    return with_voice_context(send_discord_note(intent.note_text, settings, pilot_name=pilot_name, poster=poster)), False


@dataclass(frozen=True)
class VoicePhraseSuggestion:
    command_name: str
    phrase: str
    action_summary: str
    score: float


@dataclass(frozen=True)
class VoicePhraseQualityIssue:
    severity: str
    title: str
    detail: str
    score: float = 0.0


def closest_voice_phrase_suggestions(
    transcript: str,
    commands: list[VoiceCommand],
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
    limit: int = 5,
) -> tuple[VoicePhraseSuggestion, ...]:
    heard = normalize_response_text(transcript)
    if not heard:
        return ()
    cleaned, _response_requested = strip_response_call_sign(heard, response_call_signs(response_call_sign))
    normalized_heard = normalize_phrase(cleaned or heard)
    if not normalized_heard:
        return ()

    suggestions: list[VoicePhraseSuggestion] = []
    padded_heard = f" {normalized_heard} "
    for command in commands:
        for phrase in command.phrases:
            normalized_phrase = normalize_phrase(phrase)
            if not normalized_phrase:
                continue
            score = SequenceMatcher(None, normalized_heard, normalized_phrase).ratio()
            if f" {normalized_phrase} " in padded_heard or f" {normalized_heard} " in f" {normalized_phrase} ":
                score = max(score, 0.92)
            suggestions.append(
                VoicePhraseSuggestion(
                    command_name=command.name,
                    phrase=phrase,
                    action_summary=command.action_summary,
                    score=score,
                )
            )

    suggestions.sort(key=lambda item: (item.score, len(normalize_phrase(item.phrase))), reverse=True)
    deduped: list[VoicePhraseSuggestion] = []
    seen: set[tuple[str, str]] = set()
    for suggestion in suggestions:
        key = (suggestion.command_name.casefold(), normalize_phrase(suggestion.phrase))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(suggestion)
        if len(deduped) >= max(1, limit):
            break
    return tuple(deduped)


def voice_phrase_analysis_lines(
    transcript: str,
    commands: list[VoiceCommand],
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
    exact_match: bool = False,
) -> list[str]:
    suggestions = closest_voice_phrase_suggestions(transcript, commands, response_call_sign=response_call_sign)
    if not suggestions:
        return []

    lines = ["Nearest command phrases:"]
    for suggestion in suggestions[:3]:
        lines.append(
            f"- {suggestion.command_name}: {suggestion.phrase} -> {suggestion.action_summary} "
            f"({suggestion.score:.0%})"
        )

    if len(suggestions) > 1 and suggestions[0].score - suggestions[1].score < 0.08:
        lines.append(
            "Analysis: the top phrase is close to another command phrase. Use a more distinct phrase or remove one synonym."
        )
    elif not exact_match and suggestions[0].score >= 0.78:
        lines.append("Analysis: close to a configured phrase, but exact command matching rejected it.")
    elif not exact_match:
        lines.append("Analysis: not close to the configured command phrases. Add the heard phrase or choose clearer wording.")
    return lines


def voice_phrase_quality_issues(commands: list[VoiceCommand], *, limit: int = 12) -> tuple[VoicePhraseQualityIssue, ...]:
    issues: list[VoicePhraseQualityIssue] = []
    phrase_entries: list[tuple[VoiceCommand, str, str]] = []
    by_phrase: dict[str, list[tuple[VoiceCommand, str]]] = {}
    for command in commands:
        for phrase in command.phrases:
            normalized = normalize_phrase(phrase)
            if not normalized:
                continue
            phrase_entries.append((command, phrase, normalized))
            by_phrase.setdefault(normalized, []).append((command, phrase))

            words = normalized.split()
            if len(words) == 1 and len(normalized) <= 5:
                issues.append(
                    VoicePhraseQualityIssue(
                        severity="medium",
                        title=f"Short single-word phrase: {phrase}",
                        detail=(
                            f"{command.name} uses a short one-word phrase. Short phrases are easier for Vosk to confuse; "
                            "consider a two-word synonym."
                        ),
                        score=0.70,
                    )
                )

    for normalized, entries in by_phrase.items():
        command_names = sorted({command.name for command, _phrase in entries})
        if len(command_names) > 1:
            issues.append(
                VoicePhraseQualityIssue(
                    severity="high",
                    title=f"Duplicate phrase across commands: {normalized}",
                    detail=f"Used by {', '.join(command_names)}. Exact matching can reject competing commands.",
                    score=1.0,
                )
            )

    for index, (left_command, left_phrase, left_normalized) in enumerate(phrase_entries):
        for right_command, right_phrase, right_normalized in phrase_entries[index + 1:]:
            if left_command.name == right_command.name:
                continue
            if left_normalized == right_normalized:
                continue
            score = SequenceMatcher(None, left_normalized, right_normalized).ratio()
            if score < 0.84:
                continue
            issues.append(
                VoicePhraseQualityIssue(
                    severity="high" if score >= 0.92 else "medium",
                    title=f"Similar phrases: {left_phrase} / {right_phrase}",
                    detail=(
                        f"{left_command.name} and {right_command.name} sound close ({score:.0%}). "
                        "Use more distinct wording before enabling command sending."
                    ),
                    score=score,
                )
            )

    severity_rank = {"high": 2, "medium": 1, "low": 0}
    issues.sort(key=lambda item: (severity_rank.get(item.severity, 0), item.score, item.title), reverse=True)
    return tuple(issues[: max(1, limit)])


def voice_phrase_quality_report(commands: list[VoiceCommand], *, limit: int = 12) -> str:
    issues = voice_phrase_quality_issues(commands, limit=limit)
    phrase_count = sum(len(command.phrases) for command in commands)
    lines = [
        "Phrase quality report",
        f"Commands: {len(commands)}",
        f"Phrases: {phrase_count}",
    ]
    if not issues:
        lines.append("No high-risk phrase collisions found.")
        return "\n".join(lines)

    lines.append(f"Issues shown: {len(issues)}")
    lines.append("")
    for issue in issues:
        lines.append(f"[{issue.severity.upper()}] {issue.title}")
        lines.append(issue.detail)
    return "\n".join(lines)


def recognition_diagnostic_report(
    diagnostic: LocalRecognitionDiagnostic,
    commands: list[VoiceCommand],
    *,
    input_device_label: str = DEFAULT_INPUT_DEVICE_LABEL,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
) -> str:
    transcript = diagnostic.transcript.strip()
    partial = diagnostic.partial_transcript.strip()
    lines = [
        "Local recognition diagnostic",
        f"Transcript: {transcript or '(empty)'}",
        f"Partial: {partial or '(none)'}",
        f"Stop reason: {diagnostic.reason}",
        f"Speech started: {'yes' if diagnostic.speech_started else 'no'}",
        (
            f"Volume: max RMS {diagnostic.max_rms:.0f} / threshold {diagnostic.speech_threshold:.0f} "
            f"({diagnostic.volume_state})"
        ),
        f"Timing: {diagnostic.duration_seconds:.2f}s",
        f"Capture: {diagnostic.capture_rate} Hz, block {diagnostic.block_size}, mic {input_device_label}",
        f"Model: {diagnostic.model_path}",
        f"Grammar phrases: {diagnostic.grammar_size}",
    ]
    if transcript:
        status = voice_status_from_transcript(
            transcript,
            commands,
            response_call_sign=response_call_sign,
            allow_command_sending=False,
        )
        exact_match = bool(status and status.title == "Voice command matched")
        if status is not None:
            lines.append("")
            lines.append(status.detail)
        analysis_lines = voice_phrase_analysis_lines(
            transcript,
            commands,
            response_call_sign=response_call_sign,
            exact_match=exact_match,
        )
        if analysis_lines:
            lines.append("")
            lines.extend(analysis_lines)
    else:
        lines.append("")
        lines.append("No transcript. Check the selected microphone and input level.")

    if diagnostic.volume_state in {"very quiet", "below threshold"}:
        lines.append("Suggestion: move the mic closer, pick the headset mic explicitly, or raise input gain.")
    elif diagnostic.volume_state == "possibly clipped":
        lines.append("Suggestion: lower input gain or move the mic back; clipped audio can confuse recognition.")
    if diagnostic.reason == "initial silence":
        lines.append("Suggestion: start speaking after the lab says it is recording.")
    elif diagnostic.reason in {"auto-stop silence", "max duration"} and not transcript:
        lines.append("Suggestion: use a shorter phrase and speak in one steady burst.")
    if diagnostic.grammar_size > 250:
        lines.append("Suggestion: a large command grammar can increase confusion; test with fewer command phrases later.")
    return "\n".join(lines)


@dataclass(frozen=True)
class IntelPetSettings:
    pilot_names: tuple[str, ...] = ()
    extra_keywords: tuple[str, ...] = ()
    help_phrases: tuple[str, ...] = ()
    show_message_text: bool = True
    alert_seconds: float = DEFAULT_ALERT_SECONDS
    alert_behaviors: dict[str, str] = field(default_factory=default_alert_behaviors)
    speak_alerts: bool = False
    spoken_alert_kinds: dict[str, bool] = field(default_factory=default_spoken_alert_kinds)
    response_engine: str = DEFAULT_PET_SPEECH_ENGINE
    response_voice: str = DEFAULT_OPENAI_TTS_VOICE
    response_style: str = DEFAULT_PET_SPEECH_STYLE
    voice_preview_text: str = DEFAULT_VOICE_PREVIEW_TEXT
    enable_voice_listener: bool = False
    voice_engine: str = DEFAULT_VOICE_ENGINE
    voice_whisper_model: str = DEFAULT_LOCAL_WHISPER_MODEL
    voice_model_path: str = ""
    voice_input_device: str = ""
    voice_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN
    allow_voice_command_sending: bool = False
    require_voice_target_window: bool = True
    voice_target_title: str = DEFAULT_VOICE_TARGET_TITLE
    mission_read_opener: str = MissionReadOptions.opener
    mission_read_include_giver: bool = True
    mission_read_include_level: bool = True
    mission_read_include_rewards: bool = True
    mission_read_include_reward_notes: bool = True
    mission_read_include_source: bool = False
    mission_read_include_completion: bool = True
    mission_read_include_briefing: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntelPetSettings":
        mission_read_options = mission_read_options_from_dict(payload.get("mission_read_options"))
        return cls(
            pilot_names=clean_watchlist_terms(payload.get("pilot_names")),
            extra_keywords=clean_watchlist_terms(payload.get("extra_keywords")),
            help_phrases=clean_watchlist_terms(payload.get("help_phrases")),
            show_message_text=bool(payload.get("show_message_text", True)),
            alert_seconds=safe_float(payload.get("alert_seconds"), DEFAULT_ALERT_SECONDS),
            alert_behaviors=clean_alert_behaviors(payload.get("alert_behaviors")),
            speak_alerts=bool(payload.get("speak_alerts", False)),
            spoken_alert_kinds=clean_spoken_alert_kinds(payload.get("spoken_alert_kinds")),
            response_engine=clean_response_engine(payload.get("response_engine")),
            response_voice=clean_response_voice(payload.get("response_voice")),
            response_style=clean_response_style(payload.get("response_style")),
            voice_preview_text=clean_voice_preview_text(payload.get("voice_preview_text")),
            enable_voice_listener=bool(payload.get("enable_voice_listener", False)),
            voice_engine=clean_voice_engine(payload.get("voice_engine")),
            voice_whisper_model=clean_voice_whisper_model(payload.get("voice_whisper_model")),
            voice_model_path=clean_voice_model_path(payload.get("voice_model_path")),
            voice_input_device=clean_voice_input_device(payload.get("voice_input_device")),
            voice_call_sign=clean_voice_call_sign(payload.get("voice_call_sign")),
            allow_voice_command_sending=bool(payload.get("allow_voice_command_sending", False)),
            require_voice_target_window=bool(payload.get("require_voice_target_window", True)),
            voice_target_title=clean_voice_target_title(payload.get("voice_target_title")),
            mission_read_opener=clean_mission_read_opener(payload.get("mission_read_opener", mission_read_options.opener)),
            mission_read_include_giver=bool(
                payload.get("mission_read_include_giver", mission_read_options.include_giver)
            ),
            mission_read_include_level=bool(
                payload.get("mission_read_include_level", mission_read_options.include_level)
            ),
            mission_read_include_rewards=bool(
                payload.get("mission_read_include_rewards", mission_read_options.include_rewards)
            ),
            mission_read_include_reward_notes=bool(
                payload.get("mission_read_include_reward_notes", mission_read_options.include_reward_notes)
            ),
            mission_read_include_source=bool(
                payload.get("mission_read_include_source", mission_read_options.include_source)
            ),
            mission_read_include_completion=bool(
                payload.get("mission_read_include_completion", mission_read_options.include_completion)
            ),
            mission_read_include_briefing=bool(
                payload.get("mission_read_include_briefing", mission_read_options.include_briefing)
            ),
        )

    def to_watchlist(self) -> IntelWatchlist:
        return IntelWatchlist(
            help_phrases=self.help_phrases,
            keywords=self.extra_keywords,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pilot_names": list(self.pilot_names),
            "extra_keywords": list(self.extra_keywords),
            "help_phrases": list(self.help_phrases),
            "show_message_text": self.show_message_text,
            "alert_seconds": self.alert_seconds,
            "alert_behaviors": clean_alert_behaviors(self.alert_behaviors),
            "speak_alerts": bool(self.speak_alerts),
            "spoken_alert_kinds": clean_spoken_alert_kinds(self.spoken_alert_kinds),
            "response_engine": clean_response_engine(self.response_engine),
            "response_voice": clean_response_voice(self.response_voice),
            "response_style": clean_response_style(self.response_style),
            "voice_preview_text": clean_voice_preview_text(self.voice_preview_text),
            "enable_voice_listener": bool(self.enable_voice_listener),
            "voice_engine": clean_voice_engine(self.voice_engine),
            "voice_whisper_model": clean_voice_whisper_model(self.voice_whisper_model),
            "voice_model_path": clean_voice_model_path(self.voice_model_path),
            "voice_input_device": clean_voice_input_device(self.voice_input_device),
            "voice_call_sign": clean_voice_call_sign(self.voice_call_sign),
            "allow_voice_command_sending": bool(self.allow_voice_command_sending),
            "require_voice_target_window": bool(self.require_voice_target_window),
            "voice_target_title": clean_voice_target_title(self.voice_target_title),
            "mission_read_opener": clean_mission_read_opener(self.mission_read_opener),
            "mission_read_include_giver": bool(self.mission_read_include_giver),
            "mission_read_include_level": bool(self.mission_read_include_level),
            "mission_read_include_rewards": bool(self.mission_read_include_rewards),
            "mission_read_include_reward_notes": bool(self.mission_read_include_reward_notes),
            "mission_read_include_source": bool(self.mission_read_include_source),
            "mission_read_include_completion": bool(self.mission_read_include_completion),
            "mission_read_include_briefing": bool(self.mission_read_include_briefing),
            "mission_read_options": mission_read_options_to_dict(mission_read_options_from_settings(self)),
        }


INTEL_PET_SETTINGS_KEYS = frozenset(IntelPetSettings.__dataclass_fields__)
INTEL_PET_SETTINGS_EXPORT_KIND = "eve-voice-pilot.intel-pet-settings.v1"


@dataclass(frozen=True)
class IntelPetDiscordNoteSettings:
    enabled: bool = False
    webhook_url: str = ""
    sender_name: str = DEFAULT_DISCORD_NOTE_SENDER
    trigger_phrases: tuple[str, ...] = DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES
    close_phrases: tuple[str, ...] = DEFAULT_DISCORD_NOTE_CLOSE_PHRASES
    cancel_phrases: tuple[str, ...] = DEFAULT_DISCORD_NOTE_CANCEL_PHRASES

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntelPetDiscordNoteSettings":
        return cls(
            enabled=bool(payload.get("enabled", False)),
            webhook_url=clean_discord_note_webhook_url(payload.get("webhook_url")),
            sender_name=clean_discord_note_sender(payload.get("sender_name")),
            trigger_phrases=clean_discord_note_phrases(
                payload.get("trigger_phrases"),
                default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
            ),
            close_phrases=clean_discord_note_phrases(
                payload.get("close_phrases"),
                default=DEFAULT_DISCORD_NOTE_CLOSE_PHRASES,
            ),
            cancel_phrases=clean_discord_note_phrases(
                payload.get("cancel_phrases"),
                default=DEFAULT_DISCORD_NOTE_CANCEL_PHRASES,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "webhook_url": self.webhook_url,
            "sender_name": clean_discord_note_sender(self.sender_name),
            "trigger_phrases": list(
                clean_discord_note_phrases(self.trigger_phrases, default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES)
            ),
            "close_phrases": list(
                clean_discord_note_phrases(self.close_phrases, default=DEFAULT_DISCORD_NOTE_CLOSE_PHRASES)
            ),
            "cancel_phrases": list(
                clean_discord_note_phrases(self.cancel_phrases, default=DEFAULT_DISCORD_NOTE_CANCEL_PHRASES)
            ),
        }

    def safe_summary(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "webhook_configured": bool(self.webhook_url),
            "sender_name": clean_discord_note_sender(self.sender_name),
            "trigger_phrases": list(self.trigger_phrases),
            "close_phrases": list(self.close_phrases),
            "cancel_phrases": list(self.cancel_phrases),
        }


@dataclass(frozen=True)
class IntelPetDiscordNoteIntent:
    action: str
    note_text: str = ""


@dataclass(frozen=True)
class IntelPetDiscordNoteCaptureState:
    active: bool = False
    parts: tuple[str, ...] = ()
    last_note_at: float = 0.0

    @property
    def note_text(self) -> str:
        return join_discord_note_parts(self.parts)


def first_discord_note_trigger_phrase(settings: IntelPetDiscordNoteSettings) -> str:
    phrases = clean_discord_note_phrases(
        settings.trigger_phrases,
        default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
    )
    return phrases[0]


def first_discord_note_close_phrase(settings: IntelPetDiscordNoteSettings) -> str:
    phrases = clean_discord_note_phrases(
        settings.close_phrases,
        default=DEFAULT_DISCORD_NOTE_CLOSE_PHRASES,
    )
    return phrases[0]


def first_discord_note_cancel_phrase(settings: IntelPetDiscordNoteSettings) -> str:
    phrases = clean_discord_note_phrases(
        settings.cancel_phrases,
        default=DEFAULT_DISCORD_NOTE_CANCEL_PHRASES,
    )
    return phrases[0]


def discord_note_ready_detail(settings: IntelPetDiscordNoteSettings) -> str:
    trigger = first_discord_note_trigger_phrase(settings)
    close = first_discord_note_close_phrase(settings)
    cancel = first_discord_note_cancel_phrase(settings)
    return f'Note capture armed after "{trigger}". Speak the note, pause for 2 seconds, say "{close}" to send, or say "{cancel}" to cancel.'


def voice_listener_ready_detail(
    settings: IntelPetSettings,
    note_settings: IntelPetDiscordNoteSettings | None = None,
) -> str:
    engine = clean_voice_engine(settings.voice_engine)
    call_sign = clean_voice_call_sign(settings.voice_call_sign)
    if settings.allow_voice_command_sending:
        if settings.require_voice_target_window:
            mode = f"Sending enabled; active-window guard requires {clean_voice_target_title(settings.voice_target_title)!r}."
        else:
            mode = "Sending enabled; active-window guard is off."
    else:
        mode = "Practice-only mode; no keys will be sent."

    parts = [
        f"Voice listener ready with {engine}.",
        mode,
        f"Call sign: {call_sign}.",
    ]
    if note_settings is not None:
        trigger = first_discord_note_trigger_phrase(note_settings)
        close = first_discord_note_close_phrase(note_settings)
        cancel = first_discord_note_cancel_phrase(note_settings)
        parts.append(f'Discord note trigger: "{trigger}"; close: "{close}"; cancel: "{cancel}".')
    return " ".join(parts)


def active_window_check_summary(
    *,
    allow_command_sending: bool,
    require_target_window: bool,
    target_title: str,
) -> str:
    if not allow_command_sending:
        return "practice-only; no active-window check"
    if require_target_window:
        return f"requires active window containing {clean_voice_target_title(target_title)!r}"
    return "guard off"


@dataclass(frozen=True)
class IntelPetAlert:
    title: str
    severity: str
    channel: str
    speaker: str
    message: str
    systems: tuple[str, ...]
    observed_at: str
    reported_at: str
    categories: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class IntelPetLocationSession:
    character_id: int
    character_name: str
    scopes: tuple[str, ...]
    access_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return self.expires_at <= time.time()


@dataclass(frozen=True)
class IntelPetLocation:
    solar_system_id: int
    solar_system_name: str
    station_id: int | None = None
    structure_id: int | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class IntelPetLocationCheer:
    system_name: str
    character_name: str
    updated_at: str


@dataclass(frozen=True)
class IntelPetCombatCheer:
    message: str
    observed_at: str
    reported_at: str
    log_path: str = ""


@dataclass(frozen=True)
class IntelPetMissionCheer:
    action: str
    message: str
    comment: str
    observed_at: str
    reported_at: str
    log_path: str = ""


@dataclass(frozen=True)
class IntelPetVoiceStatus:
    title: str
    detail: str
    severity: str
    recorded_at: str
    heard: str = ""
    engine: str = ""
    active_window_check: str = ""


@dataclass(frozen=True)
class IntelPetHistoryItem:
    title: str
    detail: str
    meta: str
    severity: str
    recorded_at: str


@dataclass
class DiscordChannelAlertState:
    last_sent_at: float = 0.0
    sent_keys: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class IntelPetVoiceReliabilityRow:
    recorded_at: str
    heard: str
    outcome: str
    command: str
    blocked_reason: str
    active_window_check: str
    engine: str


def _detail_line_value(detail: str, prefix: str) -> str:
    folded_prefix = prefix.casefold()
    for line in str(detail or "").splitlines():
        if line.casefold().startswith(folded_prefix):
            return line.split(":", maxsplit=1)[1].strip()
    return ""


def voice_reliability_outcome(title: str) -> str:
    clean_title = " ".join(str(title or "").split())
    folded = clean_title.casefold()
    if folded == "voice practice listener":
        return "ready"
    if folded == "voice command sent":
        return "sent"
    if folded == "voice command matched":
        return "matched"
    if folded == "voice command blocked":
        return "blocked"
    if folded == "voice heard":
        return "no match"
    if folded == "discord note sent":
        return "note sent"
    if folded == "discord note ready":
        return "note armed"
    if folded == "discord note canceled":
        return "note canceled"
    if folded.startswith("discord note"):
        return clean_title.removeprefix("Discord ").casefold()
    return clean_title or "voice event"


def voice_reliability_blocked_reason(item: IntelPetHistoryItem) -> str:
    if item.title in {"Voice practice listener", "Voice command sent", "Discord note sent", "Discord note ready", "Discord note canceled"}:
        return ""
    ignored_prefixes = ("Heard:", "Matched:", "Engine:", "Active-window check:")
    for line in str(item.detail or "").splitlines():
        text = line.strip()
        if not text or any(text.startswith(prefix) for prefix in ignored_prefixes):
            continue
        return text
    return ""


def voice_reliability_rows(
    history_items: Iterable[IntelPetHistoryItem],
    settings: IntelPetSettings,
    *,
    limit: int = 20,
) -> tuple[IntelPetVoiceReliabilityRow, ...]:
    rows: list[IntelPetVoiceReliabilityRow] = []
    fallback_engine = clean_voice_engine(settings.voice_engine)
    fallback_check = active_window_check_summary(
        allow_command_sending=settings.allow_voice_command_sending,
        require_target_window=settings.require_voice_target_window,
        target_title=settings.voice_target_title,
    )
    for item in reversed(tuple(history_items)):
        if item.meta != "Voice practice listener":
            continue
        heard = _detail_line_value(item.detail, "Heard:")
        command = _detail_line_value(item.detail, "Matched:")
        engine = _detail_line_value(item.detail, "Engine:") or fallback_engine
        active_check = _detail_line_value(item.detail, "Active-window check:") or fallback_check
        rows.append(
            IntelPetVoiceReliabilityRow(
                recorded_at=item.recorded_at,
                heard=heard,
                outcome=voice_reliability_outcome(item.title),
                command=command,
                blocked_reason=voice_reliability_blocked_reason(item),
                active_window_check=active_check,
                engine=engine,
            )
        )
        if len(rows) >= max(1, limit):
            break
    return tuple(rows)


def voice_training_phrase_from_detail(
    detail: str,
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
) -> str:
    for line in str(detail or "").splitlines():
        if not line.casefold().startswith("heard:"):
            continue
        phrase = line.split(":", maxsplit=1)[1].strip()
        return clean_voice_training_phrase(phrase, response_call_sign=response_call_sign)
    return ""


def recent_voice_training_phrases(
    history_items: Iterable[IntelPetHistoryItem],
    *,
    response_call_sign: str = DEFAULT_RESPONSE_CALL_SIGN,
    limit: int = 8,
) -> tuple[str, ...]:
    phrases: list[str] = []
    seen: set[str] = set()
    for item in reversed(tuple(history_items)):
        if item.meta != "Voice practice listener":
            continue
        phrase = voice_training_phrase_from_detail(item.detail, response_call_sign=response_call_sign)
        folded = phrase.casefold()
        if not phrase or folded in seen:
            continue
        phrases.append(phrase)
        seen.add(folded)
        if len(phrases) >= limit:
            break
    return tuple(phrases)


@dataclass
class GameLogState:
    path: Path
    encoding: str
    offset: int


class IntelPetEngine:
    def __init__(self, settings: IntelPetSettings, *, system_names: Iterable[str] = COMMON_SYSTEM_NAMES):
        self._system_names = tuple(system_names)
        self._lock = threading.Lock()
        self.settings = settings
        self.parser = self._build_parser(settings)

    def analyze(self, message: ChatMessage) -> IntelPetAlert | None:
        with self._lock:
            settings = self.settings
            parser = self.parser

        event = parser.analyze(message, source="intel pet")
        mentions = find_matching_terms(settings.pilot_names, message.text)
        self_mentioned_by_other = bool(mentions) and not speaker_matches_any(
            message.speaker,
            settings.pilot_names,
        )

        systems = event.systems if event else parser.system_matcher.find(message.text)
        categories = set(event.categories if event else ())
        keywords = list(event.keywords if event else ())
        severity = event.severity if event else "info"

        if self_mentioned_by_other:
            categories.add("self-mention")
            severity = higher_severity(severity, "high")
            for mention in mentions:
                keywords.append(f"name: {mention}")

        if not categories:
            return None

        title = alert_title(categories, message.channel)
        return IntelPetAlert(
            title=title,
            severity=severity,
            channel=message.channel,
            speaker=message.speaker,
            message=message.text if settings.show_message_text else "",
            systems=systems,
            observed_at=message.observed_at,
            reported_at=now_iso(),
            categories=tuple(sorted(categories)),
            keywords=tuple(dedupe_preserve_order(keywords)),
        )

    def current_settings(self) -> IntelPetSettings:
        with self._lock:
            return self.settings

    def update_settings(self, settings: IntelPetSettings) -> IntelPetSettings:
        parser = self._build_parser(settings)
        with self._lock:
            self.settings = settings
            self.parser = parser
        return settings

    def _build_parser(self, settings: IntelPetSettings) -> IntelParser:
        return IntelParser(
            self._system_names,
            watchlist_store=WatchlistStore(watchlist=settings.to_watchlist()),
        )


def load_settings(path: Path | None, *, overrides: argparse.Namespace | None = None) -> IntelPetSettings:
    payload: dict[str, Any] = {}
    if path and path.expanduser().exists():
        try:
            loaded = json.loads(path.expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpIntelError(f"Could not read intel pet settings {path}: {exc}") from exc
        if isinstance(loaded, dict):
            payload = loaded
    settings = IntelPetSettings.from_dict(payload)
    if overrides is None:
        return settings
    if overrides.pilot_name:
        settings = replace(
            settings,
            pilot_names=merge_terms(settings.pilot_names, overrides.pilot_name),
        )
    if overrides.keyword:
        settings = replace(
            settings,
            extra_keywords=merge_terms(settings.extra_keywords, overrides.keyword),
        )
    if overrides.help_phrase:
        settings = replace(
            settings,
            help_phrases=merge_terms(settings.help_phrases, overrides.help_phrase),
        )
    if overrides.no_message_text:
        settings = replace(settings, show_message_text=False)
    if overrides.alert_seconds is not None:
        settings = replace(settings, alert_seconds=max(3.0, float(overrides.alert_seconds)))
    if getattr(overrides, "speak_alerts", None) is not None:
        settings = replace(settings, speak_alerts=bool(overrides.speak_alerts))
    if getattr(overrides, "response_engine", ""):
        settings = replace(settings, response_engine=clean_response_engine(overrides.response_engine))
    if getattr(overrides, "response_voice", ""):
        settings = replace(settings, response_voice=clean_response_voice(overrides.response_voice))
    if getattr(overrides, "response_style", ""):
        settings = replace(settings, response_style=clean_response_style(overrides.response_style))
    if getattr(overrides, "voice_preview_text", ""):
        settings = replace(settings, voice_preview_text=clean_voice_preview_text(overrides.voice_preview_text))
    if getattr(overrides, "enable_voice_listener", None) is not None:
        settings = replace(settings, enable_voice_listener=bool(overrides.enable_voice_listener))
    if getattr(overrides, "voice_engine", ""):
        settings = replace(settings, voice_engine=clean_voice_engine(overrides.voice_engine))
    if getattr(overrides, "voice_whisper_model", ""):
        settings = replace(settings, voice_whisper_model=clean_voice_whisper_model(overrides.voice_whisper_model))
    if getattr(overrides, "voice_model_path", ""):
        settings = replace(settings, voice_model_path=clean_voice_model_path(overrides.voice_model_path))
    if getattr(overrides, "voice_input_device", ""):
        settings = replace(settings, voice_input_device=clean_voice_input_device(overrides.voice_input_device))
    if getattr(overrides, "voice_call_sign", ""):
        settings = replace(settings, voice_call_sign=clean_voice_call_sign(overrides.voice_call_sign))
    if getattr(overrides, "allow_voice_command_sending", None) is not None:
        settings = replace(settings, allow_voice_command_sending=bool(overrides.allow_voice_command_sending))
    if getattr(overrides, "require_voice_target_window", None) is not None:
        settings = replace(settings, require_voice_target_window=bool(overrides.require_voice_target_window))
    if getattr(overrides, "voice_target_title", ""):
        settings = replace(settings, voice_target_title=clean_voice_target_title(overrides.voice_target_title))
    return settings


def save_settings(path: Path, settings: IntelPetSettings) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(settings.to_dict(), indent=2) + "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8")
    tmp_path.replace(path)


def load_discord_note_settings(
    path: Path | None,
    *,
    overrides: argparse.Namespace | None = None,
) -> IntelPetDiscordNoteSettings:
    payload: dict[str, Any] = {}
    if path and path.expanduser().exists():
        try:
            loaded = json.loads(path.expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpIntelError(f"Could not read Intel Pet Discord note settings {path}: {exc}") from exc
        if isinstance(loaded, dict):
            payload = loaded
    settings = IntelPetDiscordNoteSettings.from_dict(payload)
    if overrides is None:
        return settings
    if getattr(overrides, "discord_note_webhook_url", ""):
        settings = replace(
            settings,
            webhook_url=clean_discord_note_webhook_url(overrides.discord_note_webhook_url),
        )
    if getattr(overrides, "enable_discord_notes", None) is not None:
        settings = replace(settings, enabled=bool(overrides.enable_discord_notes))
    return settings


def save_discord_note_settings(path: Path, settings: IntelPetDiscordNoteSettings) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(settings.to_dict(), indent=2) + "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8")
    tmp_path.replace(path)


def export_settings_payload(settings: IntelPetSettings, *, exported_at: str | None = None) -> dict[str, Any]:
    return {
        "kind": INTEL_PET_SETTINGS_EXPORT_KIND,
        "exported_at": exported_at or now_iso(),
        "settings": settings.to_dict(),
    }


def settings_from_import_payload(payload: Any) -> IntelPetSettings:
    if not isinstance(payload, dict):
        raise CorpIntelError("Intel Pet settings import must be a JSON object.")

    settings_payload = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not any(key in settings_payload for key in INTEL_PET_SETTINGS_KEYS):
        raise CorpIntelError("Import file does not look like Intel Pet settings.")
    return IntelPetSettings.from_dict(settings_payload)


def export_settings(path: Path, settings: IntelPetSettings) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(export_settings_payload(settings), indent=2) + "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8")
    tmp_path.replace(path)


def import_settings(path: Path) -> IntelPetSettings:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpIntelError(f"Could not read intel pet settings import {path}: {exc}") from exc
    return settings_from_import_payload(payload)


def replace_alert_terms(
    settings: IntelPetSettings,
    *,
    pilot_names: Iterable[str] | None = None,
    extra_keywords: Iterable[str] | None = None,
    help_phrases: Iterable[str] | None = None,
) -> IntelPetSettings:
    updates: dict[str, tuple[str, ...]] = {}
    if pilot_names is not None:
        updates["pilot_names"] = clean_user_terms(pilot_names)
    if extra_keywords is not None:
        updates["extra_keywords"] = clean_user_terms(extra_keywords)
    if help_phrases is not None:
        updates["help_phrases"] = clean_user_terms(help_phrases)
    if not updates:
        return settings
    return replace(settings, **updates)


def replace_extra_keywords(settings: IntelPetSettings, keywords: Iterable[str]) -> IntelPetSettings:
    return replace_alert_terms(settings, extra_keywords=keywords)


def replace_alert_behaviors(settings: IntelPetSettings, behaviors: dict[str, str]) -> IntelPetSettings:
    return replace(settings, alert_behaviors=clean_alert_behaviors(behaviors))


def replace_spoken_alert_kinds(settings: IntelPetSettings, spoken_kinds: dict[str, bool]) -> IntelPetSettings:
    return replace(settings, spoken_alert_kinds=clean_spoken_alert_kinds(spoken_kinds))


def replace_voice_settings(
    settings: IntelPetSettings,
    *,
    speak_alerts: bool,
    response_engine: str,
    response_voice: str,
    response_style: str,
    spoken_alert_kinds: dict[str, bool] | None = None,
    voice_preview_text: str | None = None,
    enable_voice_listener: bool | None = None,
    voice_engine: str | None = None,
    voice_whisper_model: str | None = None,
    voice_model_path: str | None = None,
    voice_input_device: str | None = None,
    voice_call_sign: str | None = None,
    allow_voice_command_sending: bool | None = None,
    require_voice_target_window: bool | None = None,
    voice_target_title: str | None = None,
) -> IntelPetSettings:
    return replace(
        settings,
        speak_alerts=bool(speak_alerts),
        spoken_alert_kinds=(
            settings.spoken_alert_kinds if spoken_alert_kinds is None else clean_spoken_alert_kinds(spoken_alert_kinds)
        ),
        response_engine=clean_response_engine(response_engine),
        response_voice=clean_response_voice(response_voice),
        response_style=clean_response_style(response_style),
        voice_preview_text=(
            settings.voice_preview_text if voice_preview_text is None else clean_voice_preview_text(voice_preview_text)
        ),
        enable_voice_listener=settings.enable_voice_listener if enable_voice_listener is None else bool(enable_voice_listener),
        voice_engine=settings.voice_engine if voice_engine is None else clean_voice_engine(voice_engine),
        voice_whisper_model=(
            settings.voice_whisper_model if voice_whisper_model is None else clean_voice_whisper_model(voice_whisper_model)
        ),
        voice_model_path=settings.voice_model_path if voice_model_path is None else clean_voice_model_path(voice_model_path),
        voice_input_device=settings.voice_input_device if voice_input_device is None else clean_voice_input_device(voice_input_device),
        voice_call_sign=settings.voice_call_sign if voice_call_sign is None else clean_voice_call_sign(voice_call_sign),
        allow_voice_command_sending=(
            settings.allow_voice_command_sending
            if allow_voice_command_sending is None
            else bool(allow_voice_command_sending)
        ),
        require_voice_target_window=(
            settings.require_voice_target_window if require_voice_target_window is None else bool(require_voice_target_window)
        ),
        voice_target_title=settings.voice_target_title if voice_target_title is None else clean_voice_target_title(voice_target_title),
    )


def replace_mission_read_settings(
    settings: IntelPetSettings,
    *,
    opener: str,
    include_giver: bool,
    include_level: bool,
    include_rewards: bool,
    include_reward_notes: bool,
    include_source: bool,
    include_completion: bool,
    include_briefing: bool,
) -> IntelPetSettings:
    return replace(
        settings,
        mission_read_opener=clean_mission_read_opener(opener),
        mission_read_include_giver=bool(include_giver),
        mission_read_include_level=bool(include_level),
        mission_read_include_rewards=bool(include_rewards),
        mission_read_include_reward_notes=bool(include_reward_notes),
        mission_read_include_source=bool(include_source),
        mission_read_include_completion=bool(include_completion),
        mission_read_include_briefing=bool(include_briefing),
    )


def merge_terms(existing: tuple[str, ...], additions: Iterable[str]) -> tuple[str, ...]:
    return tuple(dedupe_preserve_order((*existing, *clean_user_terms(additions))))


def clean_user_terms(values: Iterable[str]) -> tuple[str, ...]:
    terms: list[str] = []
    for value in values:
        terms.extend(clean_watchlist_terms(value))
    return tuple(dedupe_preserve_order(terms))


def clean_alert_behaviors(value: Any) -> dict[str, str]:
    valid_behaviors = {key for key, _label, _description in BEHAVIOR_OPTIONS}
    cleaned = default_alert_behaviors()
    if not isinstance(value, dict):
        return cleaned
    for kind, _label, _description in ALERT_BEHAVIOR_KINDS:
        behavior = str(value.get(kind) or "").strip()
        if behavior in valid_behaviors:
            cleaned[kind] = behavior
    return cleaned


def clean_spoken_alert_kinds(value: Any) -> dict[str, bool]:
    cleaned = default_spoken_alert_kinds()
    if not isinstance(value, dict):
        return cleaned
    for kind, _label, _description in SPOKEN_ALERT_KINDS:
        if kind in value:
            cleaned[kind] = bool(value[kind])
    return cleaned


def should_speak_alert_kind(kind: str, settings: IntelPetSettings) -> bool:
    if not settings.speak_alerts:
        return False
    spoken_kinds = clean_spoken_alert_kinds(settings.spoken_alert_kinds)
    return bool(spoken_kinds.get(kind, True))


def behavior_label(behavior: str) -> str:
    for key, label, _description in BEHAVIOR_OPTIONS:
        if behavior == key:
            return label
    return behavior_label(BEHAVIOR_ALERT)


def behavior_key_from_label(label: str) -> str:
    for key, option_label, _description in BEHAVIOR_OPTIONS:
        if label == option_label:
            return key
    return BEHAVIOR_ALERT


def behavior_test_status(alert_kind_label: str, behavior: str) -> str:
    clean_label = str(alert_kind_label or "").strip() or "Alert"
    return f"Testing {clean_label} behavior: {behavior_label(behavior)}."


def alert_behavior_key(alert: IntelPetAlert) -> str:
    categories = set(alert.categories)
    if "self-mention" in categories:
        return "mention"
    if "aid" in categories:
        return "help"
    if "hostile" in categories:
        return "hostile"
    return "keyword"


def clean_discord_alert_kinds(values: Iterable[str] | str | None) -> tuple[str, ...]:
    raw_values: Iterable[str]
    if isinstance(values, str):
        raw_values = re.split(r"[\s,]+", values)
    else:
        raw_values = values or DEFAULT_DISCORD_ALERT_KINDS
    allowed = {kind for kind, _label, _description in ALERT_BEHAVIOR_KINDS[:4]}
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        kind = str(raw_value or "").strip().casefold().replace("_", "-")
        if kind == "pilot-mention":
            kind = "mention"
        if kind == "aid":
            kind = "help"
        if kind not in allowed or kind in seen:
            continue
        seen.add(kind)
        result.append(kind)
    return tuple(result) or DEFAULT_DISCORD_ALERT_KINDS


def discord_channel_event_type_for_alert(alert: IntelPetAlert) -> str:
    kind = alert_behavior_key(alert)
    if kind == "help":
        return "help"
    return "intel"


def discord_channel_alert_event_from_alert(alert: IntelPetAlert) -> DiscordAlertEvent:
    kind = alert_behavior_key(alert)
    system_label = ", ".join(alert.systems)
    context = [alert.title]
    if system_label:
        context.append(f"System: {system_label}")
    if alert.channel:
        context.append(f"Channel: {alert.channel}")
    summary = " | ".join(context)
    if kind == "mention":
        summary = f"Pilot mention | {summary}"
    return DiscordAlertEvent(
        event_type=discord_channel_event_type_for_alert(alert),
        severity=alert.severity,
        summary=summary,
        source="local opt-in Intel Pet",
        channel=alert.channel,
        system_name=system_label,
        matched_text=alert.message,
        observed_at=alert.observed_at,
    )


def discord_channel_alert_rule_for_alert(alert: IntelPetAlert, *, include_matched_text: bool = False) -> DiscordAlertRule:
    kind = alert_behavior_key(alert)
    return DiscordAlertRule(
        name=f"Intel Pet {kind} alert",
        event_type=discord_channel_event_type_for_alert(alert),
        severity=alert.severity,
        phrases=alert.keywords,
        route_name="Intel Pet channel webhook",
        include_matched_text=include_matched_text,
        enabled=True,
        source="intel_pet",
    )


def discord_channel_alert_route(*, sender_name: str = "IntelPet") -> DiscordAlertRoute:
    return DiscordAlertRoute(
        name="Intel Pet channel webhook",
        destination="Configured Discord alert channel",
        webhook_env_var=DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR,
        enabled=True,
        route_type="webhook",
        sender_name=sender_name,
    )


def build_discord_channel_alert_payload(
    alert: IntelPetAlert,
    *,
    include_matched_text: bool = False,
    sender_name: str = "IntelPet",
) -> dict[str, Any]:
    return build_discord_alert_webhook_payload(
        discord_channel_alert_event_from_alert(alert),
        discord_channel_alert_rule_for_alert(alert, include_matched_text=include_matched_text),
        discord_channel_alert_route(sender_name=sender_name),
    )


def discord_channel_alert_key(alert: IntelPetAlert) -> str:
    return "|".join(
        (
            alert.observed_at,
            alert.channel,
            alert.speaker,
            alert.title,
            alert.message,
            ",".join(alert.systems),
        )
    )


def send_discord_channel_alert(
    alert: IntelPetAlert,
    *,
    enabled: bool,
    webhook_url: str = "",
    dry_run: bool = True,
    kinds: Iterable[str] = DEFAULT_DISCORD_ALERT_KINDS,
    include_matched_text: bool = False,
    sender_name: str = "IntelPet",
    state: DiscordChannelAlertState | None = None,
    min_seconds: float = DEFAULT_DISCORD_ALERT_MIN_SECONDS,
    poster: Callable[..., Any] | None = None,
    now_seconds: float | None = None,
) -> IntelPetHistoryItem | None:
    if not enabled:
        return None
    clean_kinds = clean_discord_alert_kinds(kinds)
    kind = alert_behavior_key(alert)
    if kind not in clean_kinds:
        return None
    recorded_at = now_iso()
    payload = build_discord_channel_alert_payload(
        alert,
        include_matched_text=include_matched_text,
        sender_name=sender_name,
    )
    route_label = "Discord channel alert"
    detail = f"Prepared {kind} alert for Discord: {payload['content']}"
    if dry_run:
        return IntelPetHistoryItem(
            title="Discord alert dry run",
            detail=detail,
            meta="Discord channel alerts | dry run | mentions disabled",
            severity="info",
            recorded_at=recorded_at,
        )
    if not webhook_url:
        return IntelPetHistoryItem(
            title="Discord alert blocked",
            detail="No Discord alert webhook is configured.",
            meta="Discord channel alerts",
            severity="high",
            recorded_at=recorded_at,
        )
    try:
        validate_discord_webhook_url(webhook_url)
    except Exception as exc:
        return IntelPetHistoryItem(
            title="Discord alert blocked",
            detail=f"Discord alert webhook is invalid: {exc}",
            meta="Discord channel alerts",
            severity="high",
            recorded_at=recorded_at,
        )
    clean_state = state or DiscordChannelAlertState()
    key = discord_channel_alert_key(alert)
    if key in clean_state.sent_keys:
        return None
    now_value = time.monotonic() if now_seconds is None else float(now_seconds)
    if clean_state.last_sent_at and now_value - clean_state.last_sent_at < max(0.0, min_seconds):
        return IntelPetHistoryItem(
            title="Discord alert rate-limited",
            detail=f"Skipped {kind} alert; minimum send gap is {max(0.0, min_seconds):.0f} seconds.",
            meta=route_label,
            severity="info",
            recorded_at=recorded_at,
        )
    try:
        send = poster or post_discord_note_webhook
        send(webhook_url, payload, timeout_seconds=10.0)
    except Exception as exc:
        return IntelPetHistoryItem(
            title="Discord alert failed",
            detail=f"Could not send Discord alert: {exc}",
            meta=route_label,
            severity="high",
            recorded_at=recorded_at,
        )
    clean_state.sent_keys.add(key)
    clean_state.last_sent_at = now_value
    return IntelPetHistoryItem(
        title="Discord alert sent",
        detail=detail,
        meta=f"{route_label} | mentions disabled",
        severity="info",
        recorded_at=recorded_at,
    )


def behavior_for_alert(alert: IntelPetAlert, settings: IntelPetSettings) -> str:
    behaviors = clean_alert_behaviors(settings.alert_behaviors)
    return behaviors[alert_behavior_key(alert)]


def behavior_for_kind(kind: str, settings: IntelPetSettings) -> str:
    behaviors = clean_alert_behaviors(settings.alert_behaviors)
    return behaviors.get(kind, DEFAULT_ALERT_BEHAVIORS.get(kind, BEHAVIOR_ALERT))


def find_matching_terms(terms: Iterable[str], text: str) -> tuple[str, ...]:
    matches: list[str] = []
    for term in terms:
        if compile_phrase_pattern(term).search(text):
            matches.append(term)
    return tuple(matches)


def speaker_matches_any(speaker: str, names: Iterable[str]) -> bool:
    folded = speaker.strip().casefold()
    return bool(folded) and any(folded == name.strip().casefold() for name in names if name.strip())


def alert_title(categories: set[str], channel: str) -> str:
    if "aid" in categories:
        return f"Help call in {channel}"
    if "self-mention" in categories:
        return f"Your name was mentioned in {channel}"
    if "hostile" in categories:
        return f"Intel in {channel}"
    return f"Keyword match in {channel}"


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(value)
    return result


def safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def alert_time_label(alert: IntelPetAlert) -> str:
    observed_at = str(alert.observed_at or "")
    if "T" in observed_at:
        return observed_at.split("T", 1)[1].replace("+00:00", "Z")
    return observed_at or "unknown time"


def alert_system_label(alert: IntelPetAlert) -> str:
    return ", ".join(alert.systems) if alert.systems else "No system"


def alert_context_label(alert: IntelPetAlert) -> str:
    return f"{alert_time_label(alert)} | {alert_system_label(alert)}"


def format_alert(alert: IntelPetAlert) -> str:
    message = f" | {alert.message}" if alert.message else ""
    keywords = f" | {', '.join(alert.keywords)}" if alert.keywords else ""
    return f"[{alert.severity.upper()}] {alert.title} | {alert_context_label(alert)} | {alert.speaker}{message}{keywords}"


def history_item_from_alert(alert: IntelPetAlert) -> IntelPetHistoryItem:
    message = alert.message or "Message text hidden by settings."
    meta = f"{alert.channel} | {alert_context_label(alert)} | {', '.join(alert.keywords) or 'matched chat'}"
    return IntelPetHistoryItem(
        title=alert.title,
        detail=f"{alert.speaker}: {message}",
        meta=meta,
        severity=alert.severity,
        recorded_at=alert.reported_at,
    )


def history_item_from_cheer(cheer: IntelPetLocationCheer) -> IntelPetHistoryItem:
    return IntelPetHistoryItem(
        title=f"Happy arrival: {cheer.system_name}",
        detail=f"{cheer.character_name} reached {cheer.system_name}.",
        meta=f"ESI location cheer | {LOCATION_SCOPE}",
        severity="info",
        recorded_at=cheer.updated_at,
    )


def trim_history(items: Iterable[IntelPetHistoryItem], limit: int = DEFAULT_HISTORY_LIMIT) -> tuple[IntelPetHistoryItem, ...]:
    clean_limit = max(1, int(limit))
    return tuple(items)[-clean_limit:]


def display_message_from_alert(alert: IntelPetAlert) -> str:
    message = alert.message or "Message text hidden by settings."
    return f"{alert_context_label(alert)}\n{message}"


def display_message_from_alerts(alerts: Iterable[IntelPetAlert]) -> str:
    clean_alerts = tuple(alerts)
    if not clean_alerts:
        return ""
    if len(clean_alerts) == 1:
        return display_message_from_alert(clean_alerts[0])
    lines = []
    for alert in clean_alerts:
        message = alert.message or "Message text hidden by settings."
        lines.append(f"{alert_context_label(alert)} | {message}")
    return "\n".join(lines)


def highest_severity_alert(alerts: Iterable[IntelPetAlert]) -> IntelPetAlert | None:
    highest: IntelPetAlert | None = None
    for alert in alerts:
        if highest is None or higher_severity(highest.severity, alert.severity) == alert.severity:
            highest = alert
    return highest


def alert_with_local_system_fallback(alert: IntelPetAlert, system_name: str) -> IntelPetAlert:
    clean_system_name = system_name.strip()
    if alert.systems or not clean_system_name or alert.channel.casefold() != "local":
        return alert
    return replace(alert, systems=(clean_system_name,))


def display_message_from_cheer(cheer: IntelPetLocationCheer) -> str:
    return f"Arrived in {cheer.system_name}."


def history_item_from_combat_cheer(cheer: IntelPetCombatCheer) -> IntelPetHistoryItem:
    return IntelPetHistoryItem(
        title="Kill cheer",
        detail=cheer.message,
        meta="Local game log | kill-looking combat line",
        severity="high",
        recorded_at=cheer.reported_at,
    )


def history_item_from_status(message: str) -> IntelPetHistoryItem:
    clean_message = " ".join(str(message or "").split()) or "Intel Pet status update."
    lowered = clean_message.casefold()
    severity = "high" if "stopped" in lowered or "error" in lowered or "does not exist" in lowered else "info"
    return IntelPetHistoryItem(
        title="Pet watcher status",
        detail=clean_message,
        meta="Local watcher",
        severity=severity,
        recorded_at=now_iso(),
    )


def history_item_from_voice_status(status: IntelPetVoiceStatus) -> IntelPetHistoryItem:
    detail_lines = [status.detail]
    if status.heard and not _detail_line_value(status.detail, "Heard:"):
        detail_lines.append(f"Heard: {status.heard}")
    if status.engine:
        detail_lines.append(f"Engine: {status.engine}")
    if status.active_window_check:
        detail_lines.append(f"Active-window check: {status.active_window_check}")
    return IntelPetHistoryItem(
        title=status.title,
        detail="\n".join(line for line in detail_lines if line),
        meta="Voice practice listener",
        severity=status.severity,
        recorded_at=status.recorded_at,
    )


def display_message_from_voice_status(status: IntelPetVoiceStatus) -> str:
    return status.detail


def display_message_from_combat_cheer(cheer: IntelPetCombatCheer) -> str:
    return cheer.message


def history_item_from_mission_cheer(cheer: IntelPetMissionCheer) -> IntelPetHistoryItem:
    title = "Mission accepted" if cheer.action == "accepted" else "Mission completed"
    return IntelPetHistoryItem(
        title=title,
        detail=f"{cheer.comment} ({cheer.message})",
        meta="Local game log | mission progress line",
        severity="info",
        recorded_at=cheer.reported_at,
    )


def display_message_from_mission_cheer(cheer: IntelPetMissionCheer) -> str:
    return cheer.comment


def on_off(value: bool) -> str:
    return "on" if value else "off"


def diagnostic_count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


@dataclass(frozen=True)
class IntelPetOptionsSummaryCard:
    key: str
    title: str
    value: str
    detail: str
    state: str = "muted"


def discord_note_example_phrases(
    settings: IntelPetDiscordNoteSettings,
    *,
    call_sign: str,
    sample_note: str = "gate camp near Amarr",
) -> tuple[str, str]:
    trigger_phrases = clean_discord_note_phrases(
        settings.trigger_phrases,
        default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
    )
    trigger = trigger_phrases[0]
    prefix = f"{clean_voice_call_sign(call_sign)} {trigger}".strip()
    note = clean_discord_note_text(sample_note) or "gate camp near Amarr"
    return f"{prefix} {note}", prefix


def intel_pet_options_summary_cards(
    *,
    settings: IntelPetSettings,
    note_settings: IntelPetDiscordNoteSettings,
    location_session: IntelPetLocationSession | None = None,
    current_system: str = "",
    history_count: int = 0,
) -> tuple[IntelPetOptionsSummaryCard, ...]:
    alert_count = len(settings.pilot_names) + len(settings.help_phrases) + len(settings.extra_keywords)
    alert_state = "good" if alert_count else "warn"
    alert_detail = (
        f"{diagnostic_count_label(len(settings.pilot_names), 'pilot name')}, "
        f"{diagnostic_count_label(len(settings.help_phrases), 'help phrase')}, "
        f"{diagnostic_count_label(len(settings.extra_keywords), 'keyword')}"
    )

    if settings.enable_voice_listener:
        if settings.allow_voice_command_sending:
            guard = "guard on" if settings.require_voice_target_window else "guard off"
            voice_state = "warn" if settings.require_voice_target_window else "danger"
            voice_detail = f"Exact matches can send keys; {guard}."
        else:
            voice_state = "good"
            voice_detail = "Practice listener only; no keys sent."
        voice_value = clean_voice_engine(settings.voice_engine)
    else:
        voice_state = "muted"
        voice_value = "Listener off"
        voice_detail = "Voice commands are idle."

    note_trigger = clean_discord_note_phrases(
        note_settings.trigger_phrases,
        default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
    )[0]
    if note_settings.enabled and note_settings.webhook_url:
        note_state = "good"
        note_value = "Discord notes on"
        note_detail = f"Webhook configured; trigger: {note_trigger}."
    elif note_settings.enabled:
        note_state = "warn"
        note_value = "Notes need webhook"
        note_detail = f"Trigger: {note_trigger}; webhook missing."
    else:
        note_state = "muted"
        note_value = "Notes off"
        note_detail = f"Saved trigger: {note_trigger}."

    if location_session is not None:
        location_value = location_session.character_name
        location_detail = f"Current system: {current_system or 'waiting for ESI'}."
        location_state = "good"
    else:
        location_value = "Location cheer off"
        location_detail = "No ESI location session active."
        location_state = "muted"

    history_state = "good" if history_count else "muted"
    return (
        IntelPetOptionsSummaryCard("alerts", "Alerts", diagnostic_count_label(alert_count, "watch term"), alert_detail, alert_state),
        IntelPetOptionsSummaryCard("voice", "Voice", voice_value, voice_detail, voice_state),
        IntelPetOptionsSummaryCard("notes", "Notes", note_value, note_detail, note_state),
        IntelPetOptionsSummaryCard("location", "Location", location_value, location_detail, location_state),
        IntelPetOptionsSummaryCard("history", "History", diagnostic_count_label(history_count, "event"), "In-memory for this pet run.", history_state),
    )


def latest_history_detail(items: Iterable[IntelPetHistoryItem], *, meta: str) -> str:
    for item in reversed(tuple(items)):
        if item.meta == meta:
            return f"{item.detail} ({item.recorded_at})"
    return "none reported yet"


def count_history_details(items: Iterable[IntelPetHistoryItem], *, meta: str, prefix: str) -> int:
    return sum(1 for item in items if item.meta == meta and item.detail.startswith(prefix))


def intel_pet_diagnostics_report(
    *,
    settings: IntelPetSettings,
    settings_path: Path,
    chat_log_dir: Path,
    game_log_dir: Path,
    channel_filter: ChannelFilter,
    listener_filter: Iterable[str],
    poll_seconds: float,
    read_existing: bool,
    combat_cheer_enabled: bool,
    mission_cheer_enabled: bool,
    location_enabled: bool,
    location_poll_seconds: float,
    happy_systems: Iterable[str],
    history_items: Iterable[IntelPetHistoryItem],
    voice_profile_path: Path | str,
    location_session: IntelPetLocationSession | None = None,
    current_system: str = "",
) -> str:
    history_snapshot = tuple(history_items)
    listeners = tuple(listener_filter)
    happy_system_tuple = tuple(happy_systems)
    spoken_kinds = clean_spoken_alert_kinds(settings.spoken_alert_kinds)
    muted_spoken = tuple(
        label for kind, label, _description in SPOKEN_ALERT_KINDS if not spoken_kinds.get(kind, True)
    )
    alert_behaviors = clean_alert_behaviors(settings.alert_behaviors)
    behavior_summary = ", ".join(
        f"{label}: {behavior_label(alert_behaviors[kind])}" for kind, label, _description in ALERT_BEHAVIOR_KINDS
    )
    severity_counts = {
        severity: sum(1 for item in history_snapshot if item.severity == severity)
        for severity in ("critical", "high", "medium", "info")
    }
    location_status = "off"
    if location_session is not None:
        location_status = f"connected as {location_session.character_name}"
        if current_system:
            location_status += f"; current system {current_system}"
    elif location_enabled:
        location_status = "enabled, not connected"

    command_mode = "practice only"
    if settings.allow_voice_command_sending:
        guard = "with active-window guard" if settings.require_voice_target_window else "without active-window guard"
        command_mode = f"exact matches can send keys {guard}"

    lines = [
        "Intel Pet Diagnostics",
        f"Generated: {now_iso()}",
        "",
        "Settings",
        f"- Settings file: {settings_path}",
        f"- Alert terms: {diagnostic_count_label(len(settings.pilot_names), 'pilot name')}, "
        f"{diagnostic_count_label(len(settings.help_phrases), 'help phrase')}, "
        f"{diagnostic_count_label(len(settings.extra_keywords), 'extra keyword')}",
        f"- Message text in bubbles: {on_off(settings.show_message_text)}",
        f"- Alert duration: {settings.alert_seconds:g}s",
        f"- Alert animations: {behavior_summary}",
        "",
        "Watchers",
        f"- Chat log folder: {chat_log_dir}",
        f"- Channels: {channel_filter.describe()}",
        f"- Listener filter: {', '.join(listeners) if listeners else 'all matching local listeners'}",
        f"- Poll interval: {poll_seconds:g}s; read existing lines: {on_off(read_existing)}",
        f"- Watched chat files reported: {count_history_details(history_snapshot, meta='Local watcher', prefix='Sharing channel')}",
        f"- Latest local watcher status: {latest_history_detail(history_snapshot, meta='Local watcher')}",
        f"- Game log folder: {game_log_dir}",
        f"- Kill cheer: {on_off(combat_cheer_enabled)}; mission comments: {on_off(mission_cheer_enabled)}",
        f"- Watched game files reported: {count_history_details(history_snapshot, meta='Local watcher', prefix='Watching game log')}",
        "",
        "Location",
        f"- Location cheer: {location_status}",
        f"- Location poll interval: {location_poll_seconds:g}s",
        f"- Happy systems: {', '.join(happy_system_tuple) if happy_system_tuple else 'none'}",
        f"- Required ESI scope when enabled: {LOCATION_SCOPE}",
        "",
        "Voice",
        f"- Spoken pet messages: {on_off(settings.speak_alerts)}",
        f"- Muted spoken alert types: {', '.join(muted_spoken) if muted_spoken else 'none'}",
        f"- Spoken response engine: {clean_response_engine(settings.response_engine)}",
        f"- Voice listener: {on_off(settings.enable_voice_listener)}; engine: {clean_voice_engine(settings.voice_engine)}",
        f"- Whisper model: {clean_voice_whisper_model(settings.voice_whisper_model)}",
        f"- Voice model: {voice_model_display(settings.voice_model_path)} ({voice_model_status(settings.voice_model_path)})",
        f"- Microphone: {voice_input_device_display(settings.voice_input_device)}",
        f"- Voice command profile: {voice_profile_path}",
        f"- Voice command mode: {command_mode}",
        "",
        "History",
        f"- In-memory history: {diagnostic_count_label(len(history_snapshot), 'item')}",
        f"- Severity counts: critical {severity_counts['critical']}, high {severity_counts['high']}, "
        f"medium {severity_counts['medium']}, info {severity_counts['info']}",
        "",
        "Privacy",
        "- This diagnostics report is local only and does not include raw chat lines, alert text history, tokens, or webhooks.",
    ]
    return "\n".join(lines)


def start_native_window_drag(window: Any) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.ReleaseCapture()
        user32.SendMessageW(int(window.winfo_id()), 0x00A1, 2, 0)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def raise_tk_widget(widget: Any) -> None:
    widget.tk.call("raise", widget._w)


def ship_sprite_frame_paths(asset_dir: Path = DEFAULT_SPRITE_DIR) -> tuple[Path, ...]:
    return tuple(asset_dir / f"ship-frame-{index:02d}.png" for index in range(SHIP_FRAME_COUNT))


def robot_miner_sprite_frame_paths(asset_dir: Path = DEFAULT_SPRITE_DIR) -> tuple[Path, ...]:
    return tuple(asset_dir / f"robot-miner-frame-{index:02d}.png" for index in range(ROBOT_MINER_FRAME_COUNT))


def trigger_robot_miner_animation(reason: str = "") -> str:
    _reason = str(reason or "").strip()
    return BEHAVIOR_ROBOT_MINER


def aura_bubble_phase_state(phase: int, *, node_count: int = AURA_BUBBLE_NODE_COUNT) -> tuple[int, tuple[int, ...]]:
    scan_x = AURA_BUBBLE_SCAN_X[phase % len(AURA_BUBBLE_SCAN_X)]
    if node_count <= 0:
        return scan_x, ()
    active_nodes = {phase % node_count, (phase + max(1, node_count // 2)) % node_count}
    return scan_x, tuple(sorted(active_nodes))


def location_sso_config_from_args(args: argparse.Namespace) -> EveSsoConfig:
    happy_systems = clean_user_terms(args.happy_system or DEFAULT_HAPPY_SYSTEMS)
    if not happy_systems:
        raise CorpIntelError("Choose at least one happy system for location cheer.")
    return EveSsoConfig(
        client_id=str(args.sso_client_id or ""),
        client_secret=str(args.sso_client_secret or ""),
        callback_url=str(args.sso_callback_url or DEFAULT_LOCATION_CALLBACK_URL),
        scopes=(LOCATION_SCOPE,),
        esi_base_url=str(args.esi_base_url or DEFAULT_ESI_BASE_URL),
    )


def validate_location_sso_config(config: EveSsoConfig) -> None:
    if not config.enabled:
        raise CorpIntelError(
            "Location cheer needs EVE SSO client values. Start with --sso-client-id and --sso-client-secret."
        )
    validate_location_esi_base_url(config.esi_base_url)
    callback = urlparse(config.callback_url)
    if callback.scheme != "http" or callback.hostname not in {"127.0.0.1", "localhost"}:
        raise CorpIntelError(
            "Intel Pet location cheer uses a localhost HTTP callback, "
            "like http://127.0.0.1:8788/intel-pet/callback."
        )
    if not callback.port:
        raise CorpIntelError("Intel Pet location cheer callback URL must include a localhost port.")


def validate_location_esi_base_url(base_url: str) -> None:
    expected = urlparse(DEFAULT_ESI_BASE_URL)
    parsed = urlparse(str(base_url or ""))
    if parsed.scheme != expected.scheme or parsed.netloc.lower() != expected.netloc.lower():
        raise CorpIntelError("Intel Pet location cheer only sends SSO bearer tokens to the official ESI host.")


def login_location_session(
    config: EveSsoConfig,
    *,
    timeout_seconds: float = 180.0,
    open_browser: bool = True,
) -> IntelPetLocationSession:
    validate_location_sso_config(config)
    state = os.urandom(24).hex()
    code_result: dict[str, str] = {}
    ready = threading.Event()
    callback = urlparse(config.callback_url)
    callback_path = callback.path or "/"

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name.
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path != callback_path:
                self.send_error(404)
                return
            if params.get("state", [""])[0] != state:
                code_result["error"] = "Invalid EVE SSO callback state."
            elif params.get("error", [""])[0]:
                code_result["error"] = "EVE SSO declined the location login."
            else:
                code_result["code"] = params.get("code", [""])[0]
                if not code_result["code"]:
                    code_result["error"] = "EVE SSO callback did not include an authorization code."
            ready.set()
            body = (
                "<!doctype html><meta charset='utf-8'>"
                "<title>EVE Intel Pet</title>"
                "<body style='font-family:Segoe UI,Arial,sans-serif;padding:24px'>"
                "<h1>EVE Intel Pet connected</h1>"
                "<p>You can close this tab and return to the pet.</p>"
                "</body>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    authorize_url = build_sso_authorization_url(config, state)
    try:
        server = HTTPServer((str(callback.hostname), int(callback.port)), CallbackHandler)
    except OSError as exc:
        raise CorpIntelError(f"Could not start Intel Pet SSO callback on {config.callback_url}: {exc}") from exc
    server.timeout = max(1.0, timeout_seconds)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()
    try:
        if open_browser:
            webbrowser.open(authorize_url)
        if not ready.wait(timeout_seconds):
            raise CorpIntelError("Timed out waiting for EVE SSO location login.")
    finally:
        server.server_close()
    if code_result.get("error"):
        raise CorpIntelError(code_result["error"])
    token_response = exchange_sso_code(config, str(code_result.get("code") or ""))
    access_token = str(token_response.get("access_token") or "")
    token_payload = decode_eve_access_token(access_token, client_id=config.client_id)
    scopes = scopes_from_sso_payload(token_payload)
    if LOCATION_SCOPE not in scopes:
        raise CorpIntelError(f"EVE SSO token did not include {LOCATION_SCOPE}.")
    return IntelPetLocationSession(
        character_id=character_id_from_sso_payload(token_payload),
        character_name=str(token_payload.get("name") or "Connected pilot"),
        scopes=scopes,
        access_token=access_token,
        expires_at=time.time() + safe_float(token_response.get("expires_in"), 1200.0),
    )


def fetch_pet_location(config: EveSsoConfig, session: IntelPetLocationSession) -> IntelPetLocation:
    validate_location_esi_base_url(config.esi_base_url)
    if session.expired:
        raise CorpIntelError("EVE SSO location token expired. Restart location cheer to reconnect.")
    if LOCATION_SCOPE not in session.scopes:
        raise CorpIntelError(f"Location cheer needs {LOCATION_SCOPE}.")
    base_url = config.esi_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {session.access_token}"}
    location = get_json(
        f"{base_url}/characters/{session.character_id}/location/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=headers,
    )
    if not isinstance(location, dict):
        raise CorpIntelError("ESI location endpoint returned unexpected data.")
    solar_system_id = int(location.get("solar_system_id") or 0)
    if solar_system_id <= 0:
        raise CorpIntelError("ESI location endpoint did not return a solar system id.")
    system_payload = get_json(
        f"{base_url}/universe/systems/{solar_system_id}/?datasource=tranquility",
        timeout_seconds=30.0,
    )
    system_name = ""
    if isinstance(system_payload, dict):
        system_name = str(system_payload.get("name") or "")
    return IntelPetLocation(
        solar_system_id=solar_system_id,
        solar_system_name=system_name or f"System {solar_system_id}",
        station_id=optional_int(location.get("station_id")),
        structure_id=optional_int(location.get("structure_id")),
        updated_at=now_iso(),
    )


def optional_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def is_happy_system(system_name: str, happy_systems: Iterable[str]) -> bool:
    folded = system_name.strip().casefold()
    return bool(folded) and folded in {system.strip().casefold() for system in happy_systems if system.strip()}


def default_game_log_dir() -> Path:
    candidates = [
        Path.home() / "Documents" / "EVE" / "logs" / "Gamelogs",
        Path.home() / "OneDrive" / "Documents" / "EVE" / "logs" / "Gamelogs",
        default_chat_log_dir().parent / "Gamelogs",
    ]
    seen: set[Path] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        expanded = candidate.expanduser()
        if expanded in seen:
            continue
        seen.add(expanded)
        unique_candidates.append(expanded)
    for candidate in unique_candidates:
        if candidate.exists():
            return candidate
    return unique_candidates[0]


def clean_game_log_text(text: str) -> str:
    line = text.lstrip("\ufeff").rstrip("\r\n")
    match = GAME_LOG_LINE_RE.match(line)
    if match:
        line = match.group("body")
    line = GAME_LOG_TAG_RE.sub("", line)
    line = html.unescape(line)
    line = GAME_LOG_PREFIX_RE.sub("", line)
    return " ".join(line.split())


def is_kill_event_text(text: str) -> bool:
    message = clean_game_log_text(text)
    if not message:
        return False
    if any(pattern.search(message) for pattern in SELF_LOSS_PATTERNS):
        return False
    return any(pattern.search(message) for pattern in KILL_EVENT_PATTERNS)


def mission_action_from_text(text: str) -> str:
    message = clean_game_log_text(text)
    if not message or any(pattern.search(message) for pattern in MISSION_NEGATIVE_PATTERNS):
        return ""
    if any(pattern.search(message) for pattern in MISSION_ACCEPT_PATTERNS):
        return "accepted"
    if any(pattern.search(message) for pattern in MISSION_COMPLETE_PATTERNS):
        return "completed"
    return ""


def mission_comment(action: str, *, seed_text: str = "") -> str:
    options = MISSION_COMMENTS.get(action) or MISSION_COMMENTS["accepted"]
    seed = sum(ord(character) for character in seed_text)
    return options[seed % len(options)]


def combat_cheer_from_game_log_line(line: str, *, log_path: str = "") -> IntelPetCombatCheer | None:
    clean_line = line.lstrip("\ufeff").rstrip("\r\n")
    match = GAME_LOG_LINE_RE.match(clean_line)
    timestamp = match.group("timestamp") if match else ""
    message = clean_game_log_text(clean_line)
    if not is_kill_event_text(message):
        return None
    return IntelPetCombatCheer(
        message=message,
        observed_at=eve_timestamp_to_iso(timestamp) if timestamp else now_iso(),
        reported_at=now_iso(),
        log_path=log_path,
    )


def mission_cheer_from_game_log_line(line: str, *, log_path: str = "") -> IntelPetMissionCheer | None:
    clean_line = line.lstrip("\ufeff").rstrip("\r\n")
    match = GAME_LOG_LINE_RE.match(clean_line)
    timestamp = match.group("timestamp") if match else ""
    message = clean_game_log_text(clean_line)
    action = mission_action_from_text(message)
    if not action:
        return None
    return IntelPetMissionCheer(
        action=action,
        message=message,
        comment=mission_comment(action, seed_text=f"{timestamp}:{message}"),
        observed_at=eve_timestamp_to_iso(timestamp) if timestamp else now_iso(),
        reported_at=now_iso(),
        log_path=log_path,
    )


def watch_game_logs(
    *,
    log_dir: Path,
    on_kill: Callable[[IntelPetCombatCheer], None],
    on_mission: Callable[[IntelPetMissionCheer], None] | None = None,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    read_existing: bool = False,
    stop_event: threading.Event | None = None,
    log: Callable[[str], None] = print,
) -> None:
    states: dict[Path, GameLogState] = {}
    stop_event = stop_event or threading.Event()
    log_dir = log_dir.expanduser()

    if not log_dir.exists():
        raise CorpIntelError(f"Game log folder does not exist: {log_dir}")

    log(f"Watching EVE game logs in {log_dir}")
    if not read_existing:
        log("Starting at the end of existing game logs. New kill-looking and mission-looking lines will be processed.")

    while not stop_event.is_set():
        discover_game_log_files(log_dir, states, read_existing=read_existing, log=log)
        for state in list(states.values()):
            combat_cheers, mission_cheers = read_new_game_log_cheers(state)
            for cheer in combat_cheers:
                on_kill(cheer)
            if on_mission is not None:
                for cheer in mission_cheers:
                    on_mission(cheer)
        stop_event.wait(poll_seconds)


def discover_game_log_files(
    log_dir: Path,
    states: dict[Path, GameLogState],
    *,
    read_existing: bool,
    log: Callable[[str], None] = print,
) -> None:
    for path in sorted(log_dir.glob("*.txt")):
        if path in states:
            continue
        encoding = detect_encoding(path)
        offset = 0 if read_existing else file_end_offset(path, encoding)
        states[path] = GameLogState(path=path, encoding=encoding, offset=offset)
        log(f"Watching game log {path.name}")


def read_new_combat_cheers(state: GameLogState) -> list[IntelPetCombatCheer]:
    cheers: list[IntelPetCombatCheer] = []
    try:
        with state.path.open("r", encoding=state.encoding, errors="replace") as handle:
            handle.seek(state.offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                cheer = combat_cheer_from_game_log_line(line, log_path=str(state.path))
                if cheer:
                    cheers.append(cheer)
            state.offset = handle.tell()
    except OSError:
        return []
    return cheers


def read_new_mission_cheers(state: GameLogState) -> list[IntelPetMissionCheer]:
    cheers: list[IntelPetMissionCheer] = []
    try:
        with state.path.open("r", encoding=state.encoding, errors="replace") as handle:
            handle.seek(state.offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                cheer = mission_cheer_from_game_log_line(line, log_path=str(state.path))
                if cheer:
                    cheers.append(cheer)
            state.offset = handle.tell()
    except OSError:
        return []
    return cheers


def read_new_game_log_cheers(state: GameLogState) -> tuple[list[IntelPetCombatCheer], list[IntelPetMissionCheer]]:
    combat_cheers: list[IntelPetCombatCheer] = []
    mission_cheers: list[IntelPetMissionCheer] = []
    try:
        with state.path.open("r", encoding=state.encoding, errors="replace") as handle:
            handle.seek(state.offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                combat_cheer = combat_cheer_from_game_log_line(line, log_path=str(state.path))
                if combat_cheer:
                    combat_cheers.append(combat_cheer)
                mission_cheer = mission_cheer_from_game_log_line(line, log_path=str(state.path))
                if mission_cheer:
                    mission_cheers.append(mission_cheer)
            state.offset = handle.tell()
    except OSError:
        return [], []
    return combat_cheers, mission_cheers


def run_console(args: argparse.Namespace, engine: IntelPetEngine) -> None:
    channel_filter = channel_filter_from_args(args)
    listener_filter = listener_filter_from_args(args, location_session=None)
    discord_alert_state = DiscordChannelAlertState()

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            print(format_alert(alert), flush=True)
            discord_status = send_discord_channel_alert(
                alert,
                enabled=args.discord_channel_alerts,
                webhook_url=args.discord_alert_webhook_url,
                dry_run=args.discord_alert_dry_run,
                kinds=args.discord_alert_kind,
                include_matched_text=args.discord_alert_include_matched_text,
                sender_name=args.discord_alert_sender_name,
                state=discord_alert_state,
                min_seconds=args.discord_alert_min_seconds,
            )
            if discord_status:
                print(f"{discord_status.title}: {discord_status.detail}", flush=True)

    watch_chat_logs(
        log_dir=args.log_dir,
        channel_filter=channel_filter,
        on_message=on_message,
        poll_seconds=args.poll_seconds,
        read_existing=args.read_existing,
        listener_filter=listener_filter,
    )


def run_overlay(
    args: argparse.Namespace,
    engine: IntelPetEngine,
    *,
    location_config: EveSsoConfig | None = None,
    location_session: IntelPetLocationSession | None = None,
) -> None:
    import tkinter as tk
    from tkinter import filedialog, ttk

    alert_queue: queue.Queue[
        IntelPetAlert
        | IntelPetLocationCheer
        | IntelPetCombatCheer
        | IntelPetMissionCheer
        | IntelPetVoiceStatus
        | IntelPetHistoryItem
        | str
    ] = queue.Queue()
    stop_event = threading.Event()
    channel_filter = channel_filter_from_args(args)
    listener_filter = listener_filter_from_args(args, location_session=location_session)
    settings_path = args.settings_path.expanduser()
    discord_note_settings_path = args.discord_note_settings_path.expanduser()
    discord_note_settings_lock = threading.Lock()
    discord_note_settings = load_discord_note_settings(discord_note_settings_path, overrides=args)
    discord_alert_state = DiscordChannelAlertState()
    happy_systems = clean_user_terms(args.happy_system or DEFAULT_HAPPY_SYSTEMS)
    location_poll_seconds = max(5.0, safe_float(args.location_poll_seconds, DEFAULT_LOCATION_POLL_SECONDS))
    current_system_lock = threading.Lock()
    current_system_name = ""
    pet_speech = SpeechResponseManager(lambda text: alert_queue.put(f"Pet voice: {text}"))
    mission_entries_lock = threading.Lock()
    mission_entries: tuple[MissionLibraryEntry, ...] = ()

    def reload_mission_entries() -> tuple[MissionLibraryEntry, ...]:
        nonlocal mission_entries
        try:
            loaded = load_mission_library()
        except Exception as exc:
            loaded = ()
            alert_queue.put(f"Mission library failed to load: {exc}")
        with mission_entries_lock:
            mission_entries = loaded
        return loaded

    def current_mission_entries() -> tuple[MissionLibraryEntry, ...]:
        with mission_entries_lock:
            return mission_entries

    reload_mission_entries()

    def configure_pet_speech(settings: IntelPetSettings) -> None:
        pet_speech.configure(
            engine=settings.response_engine,
            api_key=pet_response_api_key(settings.response_engine),
            model=pet_response_model(settings.response_engine),
            voice=pet_response_voice(settings.response_engine, settings.response_voice),
            instructions=settings.response_style,
        )

    configure_pet_speech(engine.current_settings())

    def current_local_system() -> str:
        with current_system_lock:
            return current_system_name

    def set_current_local_system(system_name: str) -> None:
        nonlocal current_system_name
        with current_system_lock:
            current_system_name = system_name

    def current_discord_note_settings() -> IntelPetDiscordNoteSettings:
        with discord_note_settings_lock:
            return discord_note_settings

    def update_discord_note_settings(settings: IntelPetDiscordNoteSettings) -> IntelPetDiscordNoteSettings:
        nonlocal discord_note_settings
        with discord_note_settings_lock:
            discord_note_settings = settings
        return settings

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            alert = alert_with_local_system_fallback(alert, current_local_system())
            alert_queue.put(alert)
            discord_status = send_discord_channel_alert(
                alert,
                enabled=args.discord_channel_alerts,
                webhook_url=args.discord_alert_webhook_url,
                dry_run=args.discord_alert_dry_run,
                kinds=args.discord_alert_kind,
                include_matched_text=args.discord_alert_include_matched_text,
                sender_name=args.discord_alert_sender_name,
                state=discord_alert_state,
                min_seconds=args.discord_alert_min_seconds,
            )
            if discord_status:
                alert_queue.put(discord_status)

    def on_kill(cheer: IntelPetCombatCheer) -> None:
        alert_queue.put(cheer)

    def on_mission(cheer: IntelPetMissionCheer) -> None:
        alert_queue.put(cheer)

    def watcher() -> None:
        try:
            watch_chat_logs(
                log_dir=args.log_dir,
                channel_filter=channel_filter,
                on_message=on_message,
                poll_seconds=args.poll_seconds,
                read_existing=args.read_existing,
                listener_filter=listener_filter,
                stop_event=stop_event,
                log=lambda text: alert_queue.put(text),
            )
        except Exception as exc:  # pragma: no cover - surfaced in the UI.
            alert_queue.put(f"Watcher stopped: {exc}")

    thread = threading.Thread(target=watcher, daemon=True)
    thread.start()

    def combat_watcher() -> None:
        try:
            watch_game_logs(
                log_dir=args.game_log_dir,
                on_kill=on_kill if not args.no_combat_cheer else lambda _cheer: None,
                on_mission=on_mission if not args.no_mission_cheer else None,
                poll_seconds=args.poll_seconds,
                read_existing=args.read_existing,
                stop_event=stop_event,
                log=lambda text: alert_queue.put(text),
            )
        except Exception as exc:  # pragma: no cover - surfaced in the UI.
            alert_queue.put(f"Combat cheer stopped: {exc}")

    if not args.no_combat_cheer or not args.no_mission_cheer:
        threading.Thread(target=combat_watcher, daemon=True).start()

    def location_watcher() -> None:
        if location_config is None or location_session is None:
            return
        alert_queue.put(f"ESI location connected as {location_session.character_name}.")
        last_system_name = ""
        last_cheered_system = ""
        while not stop_event.is_set():
            try:
                location = fetch_pet_location(location_config, location_session)
            except Exception as exc:  # pragma: no cover - surfaced in the UI.
                alert_queue.put(f"Location cheer stopped: {exc}")
                return
            set_current_local_system(location.solar_system_name)
            if location.solar_system_name != last_system_name:
                alert_queue.put(f"ESI location: {location.solar_system_name}")
                last_system_name = location.solar_system_name
                if not is_happy_system(location.solar_system_name, happy_systems):
                    last_cheered_system = ""
            if is_happy_system(location.solar_system_name, happy_systems):
                folded = location.solar_system_name.casefold()
                if folded != last_cheered_system:
                    alert_queue.put(
                        IntelPetLocationCheer(
                            system_name=location.solar_system_name,
                            character_name=location_session.character_name,
                            updated_at=location.updated_at,
                        )
                    )
                    last_cheered_system = folded
            stop_event.wait(location_poll_seconds)

    if location_session is not None:
        threading.Thread(target=location_watcher, daemon=True).start()

    def voice_practice_watcher() -> None:
        transcriber: LocalVoskTranscriber | LocalWhisperTranscriber | RealtimeTranscriber | None = None
        transcriber_signature: tuple[Any, ...] | None = None
        note_capture_state = IntelPetDiscordNoteCaptureState()
        ready_announced = False
        last_error = ""
        while not stop_event.is_set():
            settings = engine.current_settings()
            if not settings.enable_voice_listener:
                if transcriber is not None:
                    transcriber.close()
                    transcriber = None
                    transcriber_signature = None
                    ready_announced = False
                    note_capture_state = IntelPetDiscordNoteCaptureState()
                stop_event.wait(0.5)
                continue
            try:
                profile, profile_path = load_voice_profile()
                commands = list(profile.commands)
                if not commands:
                    raise CorpIntelError(f"Voice profile has no commands: {profile_path}")
                mission_voice_commands = mission_voice_grammar_commands(current_mission_entries())
                grammar_commands = [*commands, *mission_voice_commands]
                voice_engine = clean_voice_engine(settings.voice_engine)
                api_key = pet_openai_api_key()
                if voice_engine == VOICE_ENGINE_OPENAI and not api_key:
                    raise CorpIntelError("OpenAI voice listener needs an API key.")
                input_device_index = voice_input_device_index(settings.voice_input_device)
                selected_model_path = voice_model_path(settings.voice_model_path)
                whisper_model = clean_voice_whisper_model(settings.voice_whisper_model)
                call_sign = clean_voice_call_sign(settings.voice_call_sign)
                active_check = active_window_check_summary(
                    allow_command_sending=settings.allow_voice_command_sending,
                    require_target_window=settings.require_voice_target_window,
                    target_title=settings.voice_target_title,
                )
                signature = (
                    voice_engine,
                    input_device_index,
                    str(selected_model_path) if voice_engine == VOICE_ENGINE_LOCAL else "",
                    whisper_model if voice_engine == VOICE_ENGINE_WHISPER else "",
                    call_sign,
                    voice_command_signature(grammar_commands),
                    bool(api_key) if voice_engine == VOICE_ENGINE_OPENAI else False,
                )
                if transcriber is None or signature != transcriber_signature:
                    if transcriber is not None:
                        transcriber.close()
                    if voice_engine == VOICE_ENGINE_OPENAI:
                        transcriber = RealtimeTranscriber(
                            api_key,
                            lambda text: alert_queue.put(f"Voice listener: {text}"),
                            input_device_index=input_device_index,
                        )
                    elif voice_engine == VOICE_ENGINE_WHISPER:
                        transcriber = LocalWhisperTranscriber(
                            lambda text: alert_queue.put(f"Voice listener: {text}"),
                            input_device_index=input_device_index,
                            model_name=whisper_model,
                        )
                    else:
                        transcriber = LocalVoskTranscriber(
                            grammar_commands,
                            lambda text: alert_queue.put(f"Voice listener: {text}"),
                            input_device_index=input_device_index,
                            model_path=selected_model_path,
                            response_call_signs=response_call_signs(call_sign),
                        )
                    transcriber_signature = signature
                    ready_announced = False

                def on_ready() -> None:
                    nonlocal ready_announced
                    if ready_announced:
                        return
                    ready_announced = True
                    alert_queue.put(
                        IntelPetVoiceStatus(
                            title="Voice practice listener",
                            detail=voice_listener_ready_detail(settings, current_discord_note_settings()),
                            severity="info",
                            recorded_at=now_iso(),
                            engine=voice_engine,
                            active_window_check=active_check,
                        )
                    )

                transcript = transcriber.record_until_stopped(
                    stop_event,
                    on_ready=on_ready,
                    initial_silence_seconds=discord_note_initial_silence_seconds(note_capture_state),
                )
                note_status, note_capture_state = discord_note_capture_status_from_transcript(
                    transcript,
                    current_discord_note_settings(),
                    state=note_capture_state,
                    response_call_sign=call_sign,
                    pilot_name=location_session.character_name if location_session is not None else "",
                    voice_engine=voice_engine,
                    active_window_check=active_check,
                )
                if note_status:
                    alert_queue.put(note_status)
                    last_error = ""
                    continue
                mission_status = mission_voice_status_from_transcript(
                    transcript,
                    current_mission_entries(),
                    response_call_sign=call_sign,
                    voice_engine=voice_engine,
                    read_options=mission_read_options_from_settings(settings),
                    play_text=lambda text, label: pet_speech.play_text(text, label=label),
                    prepare_text=lambda text, label, force: pet_speech.prepare_text_async(text, label=label, force=force),
                )
                if mission_status:
                    alert_queue.put(mission_status)
                    last_error = ""
                    continue
                status = voice_status_from_transcript(
                    transcript,
                    commands,
                    response_call_sign=call_sign,
                    allow_command_sending=settings.allow_voice_command_sending,
                    require_target_window=settings.require_voice_target_window,
                    target_title=settings.voice_target_title,
                    voice_engine=voice_engine,
                )
                if status:
                    alert_queue.put(status)
                last_error = ""
            except Exception as exc:  # pragma: no cover - surfaced in the UI.
                if transcriber is not None:
                    transcriber.close()
                    transcriber = None
                    transcriber_signature = None
                message = f"Voice listener stopped: {exc}"
                if message != last_error:
                    alert_queue.put(
                        IntelPetVoiceStatus(
                            title="Voice listener problem",
                            detail=message,
                            severity="high",
                            recorded_at=now_iso(),
                        )
                    )
                    last_error = message
                stop_event.wait(5.0)
        if transcriber is not None:
            transcriber.close()

    threading.Thread(target=voice_practice_watcher, daemon=True).start()

    root = tk.Tk()
    root.title("EVE Intel Pet")
    desktop_left = root.winfo_vrootx()
    desktop_width = root.winfo_vrootwidth()
    start_x = max(desktop_left + 20, desktop_left + desktop_width - OVERLAY_ALERT_WIDTH - 40)
    root.geometry(f"{OVERLAY_IDLE_WIDTH}x{OVERLAY_HEIGHT}+{start_x}+80")
    root.attributes("-topmost", True)
    root.overrideredirect(True)

    transparent_color = "#ff00ff"
    bubble_fill = "#07121c"
    colors = {
        "idle": "#5f7f96",
        "info": "#4bb4ff",
        "medium": "#e1a23a",
        "high": "#ff8c2b",
        "critical": "#ff5757",
    }
    root.configure(bg=transparent_color)
    try:
        root.attributes("-transparentcolor", transparent_color)
    except tk.TclError:
        transparent_color = "#111827"
        root.configure(bg=transparent_color)

    sprite_frames = load_sprite_frames(tk, root)
    robot_miner_frames = load_sprite_frames(tk, root, paths=robot_miner_sprite_frame_paths())
    sprite_after_id: str | None = None
    idle_cycle_after_id: str | None = None
    aura_after_id: str | None = None
    current_aura_color = colors["idle"]
    shot_item_ids: list[int] = []
    history_items: list[IntelPetHistoryItem] = []

    pet_frame = tk.Frame(root, bg=transparent_color)
    pet_frame.pack(fill="both", expand=True)

    sprite_canvas = tk.Canvas(pet_frame, width=160, height=128, bg=transparent_color, highlightthickness=0)
    sprite_canvas.place(x=0, y=32)
    sprite_image_id = None
    if sprite_frames:
        sprite_image_id = sprite_canvas.create_image(80, 64, image=sprite_frames[0], tags=("drag_handle",))

    bubble_canvas = tk.Canvas(pet_frame, width=300, height=152, bg=transparent_color, highlightthickness=0)
    control_canvas = tk.Canvas(pet_frame, width=76, height=30, bg=transparent_color, highlightthickness=0)
    control_canvas.place(x=132, y=118)

    def draw_round_rectangle(
        canvas: Any,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        radius: int,
        fill: str,
        outline: str,
        width: int = 2,
        tags: tuple[str, ...] = (),
    ) -> tuple[int, ...]:
        items = (
            canvas.create_arc(
                x1,
                y1,
                x1 + radius * 2,
                y1 + radius * 2,
                start=90,
                extent=90,
                fill=fill,
                outline=outline,
                width=width,
                tags=tags,
            ),
            canvas.create_arc(
                x2 - radius * 2,
                y1,
                x2,
                y1 + radius * 2,
                start=0,
                extent=90,
                fill=fill,
                outline=outline,
                width=width,
                tags=tags,
            ),
            canvas.create_arc(
                x2 - radius * 2,
                y2 - radius * 2,
                x2,
                y2,
                start=270,
                extent=90,
                fill=fill,
                outline=outline,
                width=width,
                tags=tags,
            ),
            canvas.create_arc(
                x1,
                y2 - radius * 2,
                x1 + radius * 2,
                y2,
                start=180,
                extent=90,
                fill=fill,
                outline=outline,
                width=width,
                tags=tags,
            ),
            canvas.create_rectangle(
                x1 + radius,
                y1,
                x2 - radius,
                y2,
                fill=fill,
                outline=outline,
                width=width,
                tags=tags,
            ),
            canvas.create_rectangle(
                x1,
                y1 + radius,
                x2,
                y2 - radius,
                fill=fill,
                outline=outline,
                width=width,
                tags=tags,
            ),
        )
        return items

    def draw_aura_bubble_background(canvas: Any) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], int]:
        base_tags = ("bubble", "aura_sim")
        static_items: list[int] = []
        accent_items: list[int] = []
        node_items: list[int] = []

        static_items.append(
            canvas.create_line(
                30,
                18,
                62,
                18,
                78,
                28,
                116,
                28,
                fill="#12323f",
                width=1,
                tags=base_tags,
            )
        )
        static_items.append(
            canvas.create_line(
                130,
                24,
                164,
                24,
                184,
                38,
                226,
                38,
                244,
                24,
                fill="#10303b",
                width=1,
                tags=base_tags,
            )
        )
        static_items.append(
            canvas.create_line(
                38,
                84,
                56,
                72,
                78,
                84,
                98,
                78,
                120,
                90,
                fill="#12313b",
                width=1,
                tags=base_tags,
            )
        )
        static_items.append(
            canvas.create_line(
                148,
                84,
                178,
                70,
                208,
                86,
                238,
                72,
                260,
                82,
                fill="#12313b",
                width=1,
                tags=base_tags,
            )
        )
        static_items.append(
            canvas.create_line(
                26,
                98,
                52,
                98,
                58,
                92,
                66,
                104,
                74,
                88,
                82,
                98,
                116,
                98,
                fill="#184654",
                width=1,
                tags=base_tags,
            )
        )
        for index in range(4):
            y = 64 + index * 8
            static_items.append(canvas.create_line(30, y, 48, y + 5, fill="#0f2a35", width=1, tags=base_tags))
            static_items.append(canvas.create_line(30, y + 5, 48, y, fill="#0f2a35", width=1, tags=base_tags))
        for x, y, radius in ((70, 52, 13), (206, 58, 17), (246, 84, 10)):
            static_items.append(
                canvas.create_oval(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    outline="#0d2b35",
                    width=1,
                    tags=base_tags,
                )
            )
        for x, y in ((44, 28), (82, 28), (122, 46), (164, 24), (204, 38), (244, 24), (252, 82)):
            node_items.append(
                canvas.create_oval(
                    x - 2,
                    y - 2,
                    x + 2,
                    y + 2,
                    fill="#1a4652",
                    outline="",
                    tags=base_tags,
                )
            )
        accent_items.append(
            canvas.create_line(
                132,
                98,
                156,
                98,
                162,
                92,
                170,
                104,
                178,
                88,
                188,
                98,
                226,
                98,
                fill=colors["idle"],
                width=1,
                tags=base_tags,
            )
        )
        scan_id = canvas.create_line(
            AURA_BUBBLE_SCAN_X[0],
            14,
            AURA_BUBBLE_SCAN_X[0] + 9,
            104,
            fill=colors["idle"],
            width=1,
            dash=(2, 3),
            tags=base_tags,
        )
        return tuple(static_items), tuple(accent_items), tuple(node_items), scan_id

    bubble_border_items = draw_round_rectangle(
        bubble_canvas,
        8,
        4,
        294,
        112,
        radius=18,
        fill=bubble_fill,
        outline=colors["idle"],
        width=2,
        tags=("bubble",),
    )
    bubble_tail_id = bubble_canvas.create_polygon(
        12,
        48,
        0,
        58,
        12,
        68,
        fill=bubble_fill,
        outline=colors["idle"],
        width=2,
        tags=("bubble",),
    )
    aura_static_item_ids, aura_accent_item_ids, aura_node_item_ids, aura_scan_item_id = draw_aura_bubble_background(
        bubble_canvas
    )

    message_id = bubble_canvas.create_text(
        24,
        22,
        anchor="nw",
        fill="#f8fafc",
        font=("Segoe UI", 10),
        text="",
        width=250,
        tags=("bubble",),
    )
    options_rect_id = control_canvas.create_rectangle(
        2,
        2,
        74,
        28,
        fill="#1f2937",
        outline="#64748b",
        width=1,
        tags=("options_button",),
    )
    options_text_id = control_canvas.create_text(
        38,
        15,
        fill="#f8fafc",
        font=("Segoe UI", 8, "bold"),
        text="Options",
        tags=("options_button",),
    )
    bubble_item_ids = (
        *bubble_border_items,
        bubble_tail_id,
        *aura_static_item_ids,
        *aura_accent_item_ids,
        *aura_node_item_ids,
        aura_scan_item_id,
        message_id,
    )
    for item_id in bubble_item_ids:
        bubble_canvas.itemconfigure(item_id, state="hidden")

    class CanvasTextVar:
        def __init__(self, canvas: Any, item_id: int, value: str = "") -> None:
            self.canvas = canvas
            self.item_id = item_id
            self.value = value

        def set(self, value: str) -> None:
            self.value = value
            self.canvas.itemconfigure(self.item_id, text=value)

        def get(self) -> str:
            return self.value

    message_var = CanvasTextVar(bubble_canvas, message_id, "")

    drag_start: dict[str, int | bool] = {"x": 0, "y": 0, "moved": False}

    def begin_drag(event: Any) -> None:
        drag_start["x"] = int(event.x_root)
        drag_start["y"] = int(event.y_root)
        drag_start["moved"] = False

    def drag_overlay(event: Any) -> None:
        dx = int(event.x_root) - int(drag_start["x"])
        dy = int(event.y_root) - int(drag_start["y"])
        if dx or dy:
            drag_start["moved"] = True
        drag_start["x"] = int(event.x_root)
        drag_start["y"] = int(event.y_root)
        root.geometry(f"{root.winfo_width()}x{root.winfo_height()}+{root.winfo_x() + dx}+{root.winfo_y() + dy}")

    def release_options(_event: Any) -> None:
        if not bool(drag_start["moved"]):
            open_options()

    for widget in (sprite_canvas,):
        widget.bind("<ButtonPress-1>", begin_drag)
        widget.bind("<B1-Motion>", drag_overlay)
    sprite_canvas.tag_bind("drag_handle", "<ButtonPress-1>", begin_drag)
    sprite_canvas.tag_bind("drag_handle", "<B1-Motion>", drag_overlay)
    bubble_canvas.tag_bind("bubble", "<ButtonPress-1>", begin_drag)
    bubble_canvas.tag_bind("bubble", "<B1-Motion>", drag_overlay)
    control_canvas.tag_bind("options_button", "<ButtonPress-1>", begin_drag)
    control_canvas.tag_bind("options_button", "<B1-Motion>", drag_overlay)
    control_canvas.tag_bind("options_button", "<ButtonRelease-1>", release_options)

    history_refreshers: list[Callable[[], None]] = []

    def open_options() -> None:
        editor = tk.Toplevel(root)
        editor.title("Intel Pet Options")
        editor.geometry("980x820+80+80")
        editor.minsize(780, 620)
        editor.resizable(True, True)
        editor.transient(root)
        editor.attributes("-topmost", True)

        ui_colors = {
            "bg": "#0b1120",
            "surface": "#111827",
            "panel": "#162033",
            "panel_edge": "#334155",
            "text": "#e5e7eb",
            "muted": "#94a3b8",
            "good": "#86efac",
            "warn": "#fde68a",
            "danger": "#fca5a5",
            "accent": "#7dd3fc",
            "field": "#f8fafc",
        }

        def apply_options_style() -> None:
            style = ttk.Style(editor)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure(".", font=("Segoe UI", 9))
            style.configure("TFrame", background=ui_colors["surface"])
            style.configure("TLabel", background=ui_colors["surface"], foreground=ui_colors["text"])
            style.configure("TCheckbutton", background=ui_colors["surface"], foreground=ui_colors["text"])
            style.map(
                "TCheckbutton",
                background=[("active", ui_colors["surface"])],
                foreground=[("disabled", ui_colors["muted"])],
            )
            style.configure("TButton", padding=(10, 6))
            style.configure("TEntry", fieldbackground=ui_colors["field"])
            style.configure("TCombobox", fieldbackground=ui_colors["field"], arrowsize=14)
            style.configure(
                "Treeview",
                background=ui_colors["panel"],
                fieldbackground=ui_colors["panel"],
                foreground=ui_colors["text"],
                rowheight=24,
                bordercolor=ui_colors["panel_edge"],
            )
            style.configure(
                "Treeview.Heading",
                background="#1f2937",
                foreground=ui_colors["text"],
                font=("Segoe UI", 9, "bold"),
            )
            style.map("Treeview", background=[("selected", "#2563eb")], foreground=[("selected", "#ffffff")])
            style.configure(
                "TLabelframe",
                background=ui_colors["surface"],
                bordercolor=ui_colors["panel_edge"],
                relief="solid",
            )
            style.configure(
                "TLabelframe.Label",
                background=ui_colors["surface"],
                foreground=ui_colors["accent"],
                font=("Segoe UI", 9, "bold"),
            )
            style.configure("IntelPet.Root.TFrame", background=ui_colors["bg"])
            style.configure("IntelPet.Header.TFrame", background=ui_colors["bg"])
            style.configure("IntelPet.Surface.TFrame", background=ui_colors["surface"])
            style.configure("IntelPet.Card.TFrame", background=ui_colors["panel"], relief="solid", borderwidth=1)
            style.configure("IntelPet.Title.TLabel", background=ui_colors["bg"], foreground="#f8fafc", font=("Segoe UI", 16, "bold"))
            style.configure("IntelPet.Subtitle.TLabel", background=ui_colors["bg"], foreground=ui_colors["muted"])
            style.configure("IntelPet.CardTitle.TLabel", background=ui_colors["panel"], foreground=ui_colors["muted"], font=("Segoe UI", 8, "bold"))
            style.configure("IntelPet.CardDetail.TLabel", background=ui_colors["panel"], foreground="#cbd5e1")
            style.configure("IntelPet.CardValueMuted.TLabel", background=ui_colors["panel"], foreground=ui_colors["muted"], font=("Segoe UI", 10, "bold"))
            style.configure("IntelPet.CardValueGood.TLabel", background=ui_colors["panel"], foreground=ui_colors["good"], font=("Segoe UI", 10, "bold"))
            style.configure("IntelPet.CardValueWarn.TLabel", background=ui_colors["panel"], foreground=ui_colors["warn"], font=("Segoe UI", 10, "bold"))
            style.configure("IntelPet.CardValueDanger.TLabel", background=ui_colors["panel"], foreground=ui_colors["danger"], font=("Segoe UI", 10, "bold"))
            style.configure("IntelPet.Phrase.TLabel", background=ui_colors["panel"], foreground="#f8fafc", font=("Segoe UI", 10, "bold"))
            style.configure("IntelPet.Muted.TLabel", background=ui_colors["surface"], foreground=ui_colors["muted"])
            style.configure("IntelPet.TNotebook", background=ui_colors["bg"], borderwidth=0)
            style.configure(
                "IntelPet.TNotebook.Tab",
                padding=(12, 7),
                background=ui_colors["panel"],
                foreground=ui_colors["muted"],
            )
            style.map(
                "IntelPet.TNotebook.Tab",
                background=[("selected", ui_colors["surface"]), ("active", "#1f2937")],
                foreground=[("selected", ui_colors["accent"]), ("active", ui_colors["text"])],
            )

        apply_options_style()
        editor.configure(background=ui_colors["bg"])
        editor_status_var = tk.StringVar(value="Saved locally only.")

        def scrollable_tab(parent: ttk.Notebook) -> tuple[ttk.Frame, ttk.Frame]:
            tab_frame = ttk.Frame(parent, style="IntelPet.Surface.TFrame")
            tab_frame.columnconfigure(0, weight=1)
            tab_frame.rowconfigure(0, weight=1)
            canvas = tk.Canvas(tab_frame, borderwidth=0, highlightthickness=0, background=ui_colors["surface"])
            scrollbar = ttk.Scrollbar(tab_frame, orient="vertical", command=canvas.yview)
            content = ttk.Frame(canvas, padding=14, style="IntelPet.Surface.TFrame")
            content_id = canvas.create_window((0, 0), window=content, anchor="nw")

            def update_scrollregion(_event: Any | None = None) -> None:
                canvas.configure(scrollregion=canvas.bbox("all"))

            def resize_content(event: Any) -> None:
                canvas.itemconfigure(content_id, width=event.width)

            def scroll_mousewheel(event: Any) -> None:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

            content.bind("<Configure>", update_scrollregion)
            canvas.bind("<Configure>", resize_content)
            canvas.bind("<MouseWheel>", scroll_mousewheel)
            content.bind("<MouseWheel>", scroll_mousewheel)
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            scrollbar.grid(row=0, column=1, sticky="ns")
            return tab_frame, content

        editor_frame = ttk.Frame(editor, padding=12, style="IntelPet.Root.TFrame")
        editor_frame.pack(fill="both", expand=True)

        header = ttk.Frame(editor_frame, padding=(16, 14), style="IntelPet.Header.TFrame")
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Intel Pet Ops", style="IntelPet.Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Local overlay controls, voice readiness, Discord note routing, and run history.",
            style="IntelPet.Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        summary_frame = ttk.Frame(header, style="IntelPet.Header.TFrame")
        summary_frame.pack(fill="x")
        summary_card_widgets: dict[str, dict[str, Any]] = {}
        summary_value_styles = {
            "good": "IntelPet.CardValueGood.TLabel",
            "warn": "IntelPet.CardValueWarn.TLabel",
            "danger": "IntelPet.CardValueDanger.TLabel",
            "muted": "IntelPet.CardValueMuted.TLabel",
        }

        def refresh_option_summary() -> None:
            cards = intel_pet_options_summary_cards(
                settings=engine.current_settings(),
                note_settings=current_discord_note_settings(),
                location_session=location_session,
                current_system=current_local_system(),
                history_count=len(history_items),
            )
            for card in cards:
                widgets = summary_card_widgets.get(card.key)
                if not widgets:
                    continue
                widgets["value"].set(card.value)
                widgets["detail"].set(card.detail)
                widgets["value_label"].configure(style=summary_value_styles.get(card.state, "IntelPet.CardValueMuted.TLabel"))

        for column, card in enumerate(
            intel_pet_options_summary_cards(
                settings=engine.current_settings(),
                note_settings=current_discord_note_settings(),
                location_session=location_session,
                current_system=current_local_system(),
                history_count=len(history_items),
            )
        ):
            summary_frame.columnconfigure(column, weight=1, uniform="summary")
            card_frame = ttk.Frame(summary_frame, padding=(10, 8), style="IntelPet.Card.TFrame")
            card_frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            value_var = tk.StringVar(value=card.value)
            detail_var = tk.StringVar(value=card.detail)
            ttk.Label(card_frame, text=card.title.upper(), style="IntelPet.CardTitle.TLabel").pack(anchor="w")
            value_label = ttk.Label(
                card_frame,
                textvariable=value_var,
                style=summary_value_styles.get(card.state, "IntelPet.CardValueMuted.TLabel"),
                wraplength=150,
            )
            value_label.pack(anchor="w", pady=(4, 2))
            ttk.Label(card_frame, textvariable=detail_var, style="IntelPet.CardDetail.TLabel", wraplength=150).pack(anchor="w")
            summary_card_widgets[card.key] = {"value": value_var, "detail": detail_var, "value_label": value_label}

        notebook = ttk.Notebook(editor_frame, style="IntelPet.TNotebook")
        notebook.pack(fill="both", expand=True)
        settings_tab, settings_frame = scrollable_tab(notebook)
        behavior_tab, behavior_frame = scrollable_tab(notebook)
        voice_tab, voice_frame = scrollable_tab(notebook)
        notes_tab, notes_frame = scrollable_tab(notebook)
        missions_tab, missions_frame = scrollable_tab(notebook)
        reliability_tab, reliability_frame = scrollable_tab(notebook)
        voice_lab_tab, voice_lab_frame = scrollable_tab(notebook)
        diagnostics_tab, diagnostics_frame = scrollable_tab(notebook)
        history_tab, history_frame = scrollable_tab(notebook)
        notebook.add(settings_tab, text="Alerts")
        notebook.add(behavior_tab, text="Behaviors")
        notebook.add(voice_tab, text="Voice")
        notebook.add(notes_tab, text="Notes")
        notebook.add(missions_tab, text="Missions")
        notebook.add(reliability_tab, text="Reliability")
        notebook.add(voice_lab_tab, text="Voice Lab")
        notebook.add(diagnostics_tab, text="Diagnostics")
        notebook.add(history_tab, text="History")

        ttk.Label(settings_frame, text="Local alert settings", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            settings_frame,
            text="These match new chat lines on this computer and are saved to your ignored profile settings.",
            wraplength=480,
        ).pack(anchor="w", pady=(2, 8))

        term_lists: dict[str, Any] = {}
        term_vars: dict[str, Any] = {}

        def refresh_list(name: str, terms: Iterable[str]) -> None:
            term_list = term_lists[name]
            term_list.delete(0, tk.END)
            for term in terms:
                term_list.insert(tk.END, term)

        def current_terms(name: str) -> tuple[str, ...]:
            return tuple(str(item) for item in term_lists[name].get(0, tk.END))

        def persist_terms(action: str) -> None:
            try:
                settings = replace_alert_terms(
                    engine.current_settings(),
                    pilot_names=current_terms("pilot_names"),
                    extra_keywords=current_terms("extra_keywords"),
                    help_phrases=current_terms("help_phrases"),
                )
                save_settings(settings_path, settings)
                engine.update_settings(settings)
            except Exception as exc:
                editor_status_var.set(f"Save failed: {exc}")
                return
            refresh_list("pilot_names", settings.pilot_names)
            refresh_list("help_phrases", settings.help_phrases)
            refresh_list("extra_keywords", settings.extra_keywords)
            counts = (
                f"{len(settings.pilot_names)} name{'s' if len(settings.pilot_names) != 1 else ''}",
                f"{len(settings.help_phrases)} help phrase{'s' if len(settings.help_phrases) != 1 else ''}",
                f"{len(settings.extra_keywords)} keyword{'s' if len(settings.extra_keywords) != 1 else ''}",
            )
            editor_status_var.set(f"{action}. {', '.join(counts)} saved.")
            refresh_option_summary()

        def add_term(name: str) -> None:
            term_var = term_vars[name]
            merged = clean_user_terms((*current_terms(name), term_var.get()))
            if not merged:
                editor_status_var.set("Enter a term first.")
                return
            refresh_list(name, merged)
            term_var.set("")
            persist_terms("Added")

        def selected_index(name: str) -> int | None:
            selection = term_lists[name].curselection()
            return int(selection[0]) if selection else None

        def change_term(name: str) -> None:
            index = selected_index(name)
            term_var = term_vars[name]
            replacement = term_var.get().strip()
            if index is None:
                editor_status_var.set("Select a term to change.")
                return
            if not replacement:
                editor_status_var.set("Enter the replacement term.")
                return
            terms = list(current_terms(name))
            terms[index] = replacement
            refresh_list(name, clean_user_terms(terms))
            term_var.set("")
            persist_terms("Changed")

        def remove_term(name: str) -> None:
            index = selected_index(name)
            if index is None:
                editor_status_var.set("Select a term to remove.")
                return
            terms = list(current_terms(name))
            del terms[index]
            refresh_list(name, terms)
            term_vars[name].set("")
            persist_terms("Removed")

        def fill_entry(name: str) -> None:
            index = selected_index(name)
            if index is not None:
                term_vars[name].set(term_lists[name].get(index))

        sections = (
            (
                "pilot_names",
                "Your pilot names",
                "High alerts when someone else mentions one of these names.",
                engine.current_settings().pilot_names,
            ),
            (
                "help_phrases",
                "Help phrases",
                "Critical alerts for calls that sound like someone needs help.",
                engine.current_settings().help_phrases,
            ),
            (
                "extra_keywords",
                "Extra keywords",
                "Medium alerts for local watch terms like market or intel phrases.",
                engine.current_settings().extra_keywords,
            ),
        )

        first_entry = None
        for section_name, title, description, initial_terms in sections:
            section = ttk.LabelFrame(settings_frame, text=title, padding=8)
            section.pack(fill="both", expand=True, pady=(0, 8))
            ttk.Label(section, text=description, wraplength=460).pack(anchor="w", pady=(0, 6))

            list_frame = ttk.Frame(section)
            list_frame.pack(fill="both", expand=True)
            term_list = tk.Listbox(
                list_frame,
                height=4,
                exportselection=False,
                bg=ui_colors["panel"],
                fg=ui_colors["text"],
                selectbackground="#2563eb",
                selectforeground="#ffffff",
                highlightthickness=1,
                highlightbackground=ui_colors["panel_edge"],
                relief="flat",
            )
            term_list.pack(side="left", fill="both", expand=True)
            scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=term_list.yview)
            scrollbar.pack(side="right", fill="y")
            term_list.configure(yscrollcommand=scrollbar.set)

            term_var = tk.StringVar()
            term_lists[section_name] = term_list
            term_vars[section_name] = term_var
            refresh_list(section_name, initial_terms)
            term_list.bind("<<ListboxSelect>>", lambda _event, name=section_name: fill_entry(name))

            entry_row = ttk.Frame(section)
            entry_row.pack(fill="x", pady=(8, 6))
            term_entry = ttk.Entry(entry_row, textvariable=term_var)
            term_entry.pack(side="left", fill="x", expand=True)
            term_entry.bind("<Return>", lambda _event, name=section_name: add_term(name))
            if first_entry is None:
                first_entry = term_entry

            action_row = ttk.Frame(section)
            action_row.pack(fill="x")
            ttk.Button(action_row, text="Add", command=lambda name=section_name: add_term(name)).pack(side="left")
            ttk.Button(action_row, text="Change", command=lambda name=section_name: change_term(name)).pack(
                side="left",
                padx=(6, 0),
            )
            ttk.Button(action_row, text="Remove", command=lambda name=section_name: remove_term(name)).pack(
                side="left",
                padx=(6, 0),
            )

        ttk.Label(behavior_frame, text="Alert behaviors", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            behavior_frame,
            text="Choose the ship animation for each kind of pet alert. These are saved locally with your alert terms.",
            wraplength=520,
        ).pack(anchor="w", pady=(2, 10))

        behavior_labels = tuple(label for _key, label, _description in BEHAVIOR_OPTIONS)
        behavior_vars: dict[str, Any] = {}
        preview_canvases: dict[str, Any] = {}

        def current_behavior_choices() -> dict[str, str]:
            choices = clean_alert_behaviors(engine.current_settings().alert_behaviors)
            for kind, var in behavior_vars.items():
                choices[kind] = behavior_key_from_label(var.get())
            return choices

        def refresh_behavior_vars(settings: IntelPetSettings) -> None:
            choices = clean_alert_behaviors(settings.alert_behaviors)
            for kind, var in behavior_vars.items():
                var.set(behavior_label(choices[kind]))

        def persist_behavior(kind: str) -> None:
            try:
                settings = replace_alert_behaviors(engine.current_settings(), current_behavior_choices())
                save_settings(settings_path, settings)
                engine.update_settings(settings)
            except Exception as exc:
                editor_status_var.set(f"Behavior save failed: {exc}")
                return
            refresh_behavior_vars(settings)
            label = next((item_label for item_kind, item_label, _description in ALERT_BEHAVIOR_KINDS if item_kind == kind), kind)
            editor_status_var.set(f"{label} behavior saved as {behavior_label(settings.alert_behaviors[kind])}.")
            refresh_option_summary()

        def test_behavior(kind: str) -> None:
            label = next((item_label for item_kind, item_label, _description in ALERT_BEHAVIOR_KINDS if item_kind == kind), kind)
            behavior = behavior_key_from_label(behavior_vars[kind].get())
            start_behavior_cycle(trigger_robot_miner_animation("options test") if behavior == BEHAVIOR_ROBOT_MINER else behavior)
            editor_status_var.set(behavior_test_status(label, behavior))

        def draw_behavior_preview(canvas: Any, behavior: str, step: int) -> None:
            canvas.delete("preview")
            canvas.create_rectangle(0, 0, 96, 52, fill="#0f172a", outline="#334155", tags=("preview",))
            canvas.create_oval(14, 9, 16, 11, fill="#e2e8f0", outline="", tags=("preview",))
            canvas.create_oval(72, 10, 74, 12, fill="#e2e8f0", outline="", tags=("preview",))
            canvas.create_oval(84, 34, 86, 36, fill="#e2e8f0", outline="", tags=("preview",))

            if behavior == BEHAVIOR_ROBOT_MINER:
                bob = (0, -2, 0, 2)[step % 4]
                x = 44
                y = 26 + bob
                laser = step % 6 in {2, 3}
                swing = step % 6 in {0, 1, 4}
                canvas.create_rectangle(x - 13, y - 7, x + 13, y + 13, fill="#475b7a", outline="#93c5fd", tags=("preview",))
                canvas.create_rectangle(x - 10, y - 18, x + 10, y - 7, fill="#64748b", outline="#0f172a", tags=("preview",))
                canvas.create_rectangle(x - 7, y - 15, x + 7, y - 10, fill="#1e293b", outline="#38bdf8", tags=("preview",))
                canvas.create_rectangle(x - 5, y - 13, x - 3, y - 11, fill="#fde68a", outline="", tags=("preview",))
                canvas.create_rectangle(x + 3, y - 13, x + 5, y - 11, fill="#fde68a", outline="", tags=("preview",))
                canvas.create_rectangle(x - 2, y + 1, x + 2, y + 10, fill="#f97316", outline="", tags=("preview",))
                canvas.create_line(x - 8, y + 13, x - 10, y + 23, fill="#64748b", width=3, tags=("preview",))
                canvas.create_line(x + 8, y + 13, x + 10, y + 23, fill="#64748b", width=3, tags=("preview",))
                canvas.create_rectangle(x - 16, y + 21, x - 5, y + 24, fill="#64748b", outline="#0f172a", tags=("preview",))
                canvas.create_rectangle(x + 5, y + 21, x + 16, y + 24, fill="#64748b", outline="#0f172a", tags=("preview",))
                if laser:
                    canvas.create_line(x - 4, y - 12, 94, 10 + (step % 3) * 10, fill="#50ebff", width=2, tags=("preview",))
                    canvas.create_line(x + 4, y - 12, 94, 24 + (step % 2) * 10, fill="#ff5f78", width=2, tags=("preview",))
                if swing:
                    canvas.create_line(x + 13, y + 1, x + 29, y - 14, fill="#a57238", width=2, tags=("preview",))
                    canvas.create_line(x + 23, y - 17, x + 34, y - 8, fill="#e2e8f0", width=2, tags=("preview",))
                    if step % 6 == 4:
                        canvas.create_line(82, 41, 92, 41, fill="#fde68a", width=2, tags=("preview",))
                        canvas.create_line(87, 36, 87, 46, fill="#f97316", width=2, tags=("preview",))
                return

            if behavior == BEHAVIOR_HAPPY:
                offsets = ((0, 0), (6, -5), (10, 0), (6, 5), (0, 0), (-6, -5), (-10, 0), (-6, 5))
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#38bdf8"
            elif behavior == BEHAVIOR_COMBAT:
                offsets = ((0, 0), (10, -6), (16, 2), (8, 8), (-8, -5), (0, 0))
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#f97316"
            elif behavior == BEHAVIOR_LONG_MOVE:
                offsets = (
                    (0, 0),
                    (10, -8),
                    (18, -10),
                    (22, 0),
                    (16, 8),
                    (2, 10),
                    (-14, 4),
                    (-22, -6),
                    (-8, -10),
                    (0, 0),
                )
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#22d3ee"
            elif behavior == BEHAVIOR_LONG_COMBAT:
                offsets = ((0, 0), (6, -3), (8, 2), (4, 5), (-3, 4), (-6, -2), (0, -5), (0, 0))
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#fb923c"
            elif behavior == BEHAVIOR_LONG_COMBO:
                offsets = (
                    (0, 0),
                    (12, -8),
                    (22, -6),
                    (20, 8),
                    (4, 12),
                    (-14, 6),
                    (-24, -6),
                    (-10, -12),
                    (0, 0),
                )
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#facc15"
            elif behavior == BEHAVIOR_IDLE:
                offsets = ((0, 0), (2, -2), (0, 0), (-2, 2))
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#94a3b8"
            else:
                offset_x, offset_y = (0, 0)
                accent = "#64748b" if behavior == BEHAVIOR_NONE else "#f59e0b"

            x = 44 + offset_x
            y = 26 + offset_y
            canvas.create_polygon(
                x - 22,
                y,
                x - 4,
                y - 10,
                x + 20,
                y - 4,
                x + 24,
                y,
                x + 20,
                y + 4,
                x - 4,
                y + 10,
                fill="#334155",
                outline="#93c5fd",
                width=1,
                tags=("preview",),
            )
            canvas.create_rectangle(x - 2, y - 5, x + 10, y + 5, fill="#1e293b", outline="#64748b", tags=("preview",))
            if behavior != BEHAVIOR_NONE and step % 2 == 0:
                canvas.create_polygon(
                    x - 24,
                    y - 4,
                    x - 34,
                    y,
                    x - 24,
                    y + 4,
                    fill=accent,
                    outline="",
                    tags=("preview",),
                )
            if behavior == BEHAVIOR_ALERT and step % 2 == 0:
                canvas.create_line(x + 9, y - 8, x + 22, y - 15, fill="#fde68a", width=2, tags=("preview",))
            elif behavior == BEHAVIOR_COMBAT:
                canvas.create_line(x + 16, y - 3, 92, 10 + (step % 3) * 10, fill="#fbbf24", width=2, tags=("preview",))
                canvas.create_line(x + 14, y + 5, 88, 22 + (step % 2) * 12, fill="#ef4444", width=1, tags=("preview",))
            elif behavior == BEHAVIOR_LONG_COMBAT:
                for index in range(3):
                    canvas.create_line(
                        x + 12,
                        y - 4 + index * 4,
                        88,
                        8 + ((step + index) % 5) * 8,
                        fill=("#fbbf24" if index % 2 == 0 else "#ef4444"),
                        width=2 if index == 0 else 1,
                        tags=("preview",),
                    )
            elif behavior == BEHAVIOR_LONG_COMBO:
                canvas.create_line(x + 16, y - 5, 92, 8 + (step % 4) * 10, fill="#fde68a", width=2, tags=("preview",))
                canvas.create_line(x + 12, y + 5, 88, 38 - (step % 3) * 8, fill="#ef4444", width=1, tags=("preview",))
                canvas.create_oval(8 + (step % 7) * 10, 44, 10 + (step % 7) * 10, 46, fill="#facc15", outline="", tags=("preview",))

        for kind, title, description in ALERT_BEHAVIOR_KINDS:
            row = ttk.Frame(behavior_frame)
            row.pack(fill="x", pady=(0, 10))
            text_frame = ttk.Frame(row)
            text_frame.pack(side="left", fill="x", expand=True)
            ttk.Label(text_frame, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w")
            ttk.Label(text_frame, text=description, wraplength=320).pack(anchor="w", pady=(1, 4))

            choice_row = ttk.Frame(text_frame)
            choice_row.pack(anchor="w")
            behavior_var = tk.StringVar(value=behavior_label(behavior_for_kind(kind, engine.current_settings())))
            behavior_vars[kind] = behavior_var
            behavior_box = ttk.Combobox(
                choice_row,
                textvariable=behavior_var,
                values=behavior_labels,
                state="readonly",
                width=18,
            )
            behavior_box.pack(side="left")
            behavior_box.bind("<<ComboboxSelected>>", lambda _event, item_kind=kind: persist_behavior(item_kind))
            ttk.Button(choice_row, text="Test", command=lambda item_kind=kind: test_behavior(item_kind)).pack(
                side="left",
                padx=(6, 0),
            )

            preview = tk.Canvas(row, width=96, height=52, bg="#0f172a", highlightthickness=0)
            preview.pack(side="right", padx=(10, 0))
            preview_canvases[kind] = preview

        def animate_behavior_previews(step: int = 0) -> None:
            try:
                if not editor.winfo_exists():
                    return
            except tk.TclError:
                return
            for kind, canvas in preview_canvases.items():
                draw_behavior_preview(canvas, behavior_key_from_label(behavior_vars[kind].get()), step)
            root.after(180, lambda: animate_behavior_previews(step + 1))

        animate_behavior_previews()

        ttk.Label(voice_frame, text="Pet voice", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            voice_frame,
            text="Speak the same local alert messages shown in the pet bubble. This does not listen for commands or send keys.",
            wraplength=520,
        ).pack(anchor="w", pady=(2, 10))

        voice_settings = engine.current_settings()
        speak_alerts_var = tk.BooleanVar(value=voice_settings.speak_alerts)
        spoken_alert_kind_vars = {
            kind: tk.BooleanVar(value=clean_spoken_alert_kinds(voice_settings.spoken_alert_kinds)[kind])
            for kind, _label, _description in SPOKEN_ALERT_KINDS
        }
        response_engine_var = tk.StringVar(value=clean_response_engine(voice_settings.response_engine))
        response_voice_var = tk.StringVar(value=clean_response_voice(voice_settings.response_voice))
        response_style_var = tk.StringVar(value=clean_response_style(voice_settings.response_style))
        response_preset_var = tk.StringVar(value=pet_voice_preset_for_style(voice_settings.response_style))
        voice_preview_text_var = tk.StringVar(value=clean_voice_preview_text(voice_settings.voice_preview_text))
        voice_listener_var = tk.BooleanVar(value=voice_settings.enable_voice_listener)
        speech_engine_var = tk.StringVar(value=clean_voice_engine(voice_settings.voice_engine))
        voice_whisper_model_var = tk.StringVar(value=clean_voice_whisper_model(voice_settings.voice_whisper_model))
        voice_model_var = tk.StringVar(value=voice_model_display(voice_settings.voice_model_path))
        voice_model_status_var = tk.StringVar(value=voice_model_status(voice_settings.voice_model_path))
        voice_call_sign_var = tk.StringVar(value=clean_voice_call_sign(voice_settings.voice_call_sign))
        voice_listener_summary_var = tk.StringVar()
        try:
            input_device_labels = [DEFAULT_INPUT_DEVICE_LABEL, *(device.label for device in list_input_devices())]
        except Exception:
            input_device_labels = [DEFAULT_INPUT_DEVICE_LABEL]
        voice_input_device_var = tk.StringVar(value=voice_input_device_display(voice_settings.voice_input_device))
        allow_command_sending_var = tk.BooleanVar(value=voice_settings.allow_voice_command_sending)
        require_target_window_var = tk.BooleanVar(value=voice_settings.require_voice_target_window)
        voice_target_title_var = tk.StringVar(value=clean_voice_target_title(voice_settings.voice_target_title))

        def refresh_voice_listener_summary(settings: IntelPetSettings | None = None) -> None:
            current = settings or engine.current_settings()
            voice_listener_summary_var.set(voice_listener_ready_detail(current, current_discord_note_settings()))

        def persist_voice_settings(action: str = "Voice settings saved") -> None:
            try:
                settings = replace_voice_settings(
                    engine.current_settings(),
                    speak_alerts=speak_alerts_var.get(),
                    spoken_alert_kinds={kind: var.get() for kind, var in spoken_alert_kind_vars.items()},
                    response_engine=response_engine_var.get(),
                    response_voice=response_voice_var.get(),
                    response_style=response_style_var.get(),
                    voice_preview_text=voice_preview_text_var.get(),
                    enable_voice_listener=voice_listener_var.get(),
                    voice_engine=speech_engine_var.get(),
                    voice_whisper_model=voice_whisper_model_var.get(),
                    voice_model_path=voice_model_var.get(),
                    voice_input_device=voice_input_device_var.get(),
                    voice_call_sign=voice_call_sign_var.get(),
                    allow_voice_command_sending=allow_command_sending_var.get(),
                    require_voice_target_window=require_target_window_var.get(),
                    voice_target_title=voice_target_title_var.get(),
                )
                save_settings(settings_path, settings)
                engine.update_settings(settings)
                configure_pet_speech(settings)
                voice_model_var.set(voice_model_display(settings.voice_model_path))
                voice_model_status_var.set(voice_model_status(settings.voice_model_path))
                for kind, var in spoken_alert_kind_vars.items():
                    var.set(clean_spoken_alert_kinds(settings.spoken_alert_kinds)[kind])
                refresh_voice_listener_summary(settings)
            except Exception as exc:
                editor_status_var.set(f"Voice save failed: {exc}")
                return
            state = "on" if settings.speak_alerts else "off"
            command_state = "enabled" if settings.allow_voice_command_sending else "practice only"
            editor_status_var.set(f"{action}. Spoken pet messages are {state}; commands are {command_state}.")
            refresh_option_summary()

        def set_all_spoken_kinds(value: bool) -> None:
            for var in spoken_alert_kind_vars.values():
                var.set(value)
            persist_voice_settings("Spoken alert types saved")

        def apply_voice_preset(_event: Any | None = None) -> None:
            preset_name = response_preset_var.get()
            if preset_name == "Custom":
                persist_voice_settings("Custom voice style saved")
                return
            response_style_var.set(pet_voice_style_for_preset(preset_name))
            voice_preview_text_var.set(pet_voice_preview_for_preset(preset_name))
            persist_voice_settings(f"{preset_name} preset applied")

        def cache_voice_preview(*, force: bool = False, play: bool = False) -> None:
            persist_voice_settings("Voice preview saved")
            text = clean_voice_preview_text(voice_preview_text_var.get())
            voice_preview_text_var.set(text)
            configure_pet_speech(engine.current_settings())
            if play:
                pet_speech.play_text(text, label="voice preview")
                editor_status_var.set("Preview requested. It will play when cached.")
                return
            pet_speech.prepare_text_async(text, label="voice preview", force=force)
            if force:
                editor_status_var.set("Voice preview regeneration queued.")
            elif pet_speech.text_cached(text):
                editor_status_var.set(f"Voice preview already cached: {pet_speech.text_cache_path(text).name}")
            else:
                editor_status_var.set("Voice preview cache queued.")

        def refresh_voice_model_choices() -> None:
            voice_model_box.configure(values=installed_voice_model_labels())
            voice_model_status_var.set(voice_model_status(engine.current_settings().voice_model_path))
            editor_status_var.set("Voice model list refreshed.")

        refresh_voice_listener_summary(voice_settings)

        voice_grid = ttk.Frame(voice_frame)
        voice_grid.pack(fill="x")
        voice_grid.columnconfigure(1, weight=1)

        ttk.Label(voice_grid, text="Spoken pet replies", font=("Segoe UI", 10, "bold")).grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Checkbutton(
            voice_grid,
            text="Speak pet messages",
            variable=speak_alerts_var,
            command=lambda: persist_voice_settings("Pet voice toggled"),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        spoken_kind_frame = ttk.LabelFrame(voice_grid, text="Spoken alert types", padding=8)
        spoken_kind_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        spoken_kind_frame.columnconfigure(1, weight=1)
        for row_index, (kind, label, description) in enumerate(SPOKEN_ALERT_KINDS):
            ttk.Checkbutton(
                spoken_kind_frame,
                text=label,
                variable=spoken_alert_kind_vars[kind],
                command=lambda: persist_voice_settings("Spoken alert types saved"),
            ).grid(row=row_index, column=0, sticky="w", pady=2)
            ttk.Label(spoken_kind_frame, text=description, wraplength=360).grid(
                row=row_index,
                column=1,
                sticky="ew",
                padx=(10, 0),
                pady=2,
            )
        spoken_kind_buttons = ttk.Frame(spoken_kind_frame)
        spoken_kind_buttons.grid(row=len(SPOKEN_ALERT_KINDS), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(spoken_kind_buttons, text="Speak All", command=lambda: set_all_spoken_kinds(True)).pack(side="left")
        ttk.Button(spoken_kind_buttons, text="Mute All Types", command=lambda: set_all_spoken_kinds(False)).pack(
            side="left",
            padx=(6, 0),
        )

        ttk.Label(voice_grid, text="Voice engine").grid(row=3, column=0, sticky="w", pady=5)
        response_engine_box = ttk.Combobox(
            voice_grid,
            textvariable=response_engine_var,
            values=RESPONSE_ENGINES,
            state="readonly",
        )
        response_engine_box.grid(row=3, column=1, sticky="ew", pady=5)
        response_engine_box.bind("<<ComboboxSelected>>", lambda _event: persist_voice_settings())

        ttk.Label(voice_grid, text="Voice / voice id").grid(row=4, column=0, sticky="w", pady=5)
        response_voice_box = ttk.Combobox(
            voice_grid,
            textvariable=response_voice_var,
            values=(*OPENAI_TTS_VOICES, DEFAULT_ELEVENLABS_TTS_VOICE_ID),
        )
        response_voice_box.grid(row=4, column=1, sticky="ew", pady=5)
        response_voice_box.bind("<<ComboboxSelected>>", lambda _event: persist_voice_settings())

        ttk.Label(voice_grid, text="Voice preset").grid(row=5, column=0, sticky="w", pady=5)
        response_preset_box = ttk.Combobox(
            voice_grid,
            textvariable=response_preset_var,
            values=(*pet_voice_preset_names(), "Custom"),
            state="readonly",
        )
        response_preset_box.grid(row=5, column=1, sticky="ew", pady=5)
        response_preset_box.bind("<<ComboboxSelected>>", apply_voice_preset)

        ttk.Label(voice_grid, text="Voice style").grid(row=6, column=0, sticky="w", pady=5)
        ttk.Entry(voice_grid, textvariable=response_style_var).grid(row=6, column=1, sticky="ew", pady=5)

        ttk.Label(voice_grid, text="Preview text").grid(row=7, column=0, sticky="w", pady=5)
        ttk.Entry(voice_grid, textvariable=voice_preview_text_var).grid(row=7, column=1, sticky="ew", pady=5)

        voice_studio_buttons = ttk.Frame(voice_grid)
        voice_studio_buttons.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        ttk.Button(voice_studio_buttons, text="Preview Voice", command=lambda: cache_voice_preview(play=True)).pack(side="left")
        ttk.Button(voice_studio_buttons, text="Cache Preview", command=cache_voice_preview).pack(side="left", padx=(6, 0))
        ttk.Button(
            voice_studio_buttons,
            text="Regenerate Preview",
            command=lambda: cache_voice_preview(force=True),
        ).pack(side="left", padx=(6, 0))

        ttk.Separator(voice_grid).grid(row=9, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Label(voice_grid, text="Command listener", font=("Segoe UI", 10, "bold")).grid(
            row=10,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Label(
            voice_grid,
            textvariable=voice_listener_summary_var,
            wraplength=500,
        ).grid(row=11, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(
            voice_grid,
            text="Listen for voice commands",
            variable=voice_listener_var,
            command=lambda: persist_voice_settings("Voice listener toggled"),
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(voice_grid, text="Speech engine").grid(row=13, column=0, sticky="w", pady=5)
        speech_engine_box = ttk.Combobox(
            voice_grid,
            textvariable=speech_engine_var,
            values=VOICE_ENGINES,
            state="readonly",
        )
        speech_engine_box.grid(row=13, column=1, sticky="ew", pady=5)
        speech_engine_box.bind("<<ComboboxSelected>>", lambda _event: persist_voice_settings())

        ttk.Label(voice_grid, text="Whisper model").grid(row=14, column=0, sticky="w", pady=5)
        whisper_model_box = ttk.Combobox(
            voice_grid,
            textvariable=voice_whisper_model_var,
            values=LOCAL_WHISPER_MODELS,
            state="readonly",
        )
        whisper_model_box.grid(row=14, column=1, sticky="ew", pady=5)
        whisper_model_box.bind("<<ComboboxSelected>>", lambda _event: persist_voice_settings("Whisper model selected"))

        ttk.Label(voice_grid, text="Local model").grid(row=15, column=0, sticky="w", pady=5)
        voice_model_box = ttk.Combobox(
            voice_grid,
            textvariable=voice_model_var,
            values=installed_voice_model_labels(),
        )
        voice_model_box.grid(row=15, column=1, sticky="ew", pady=5)
        voice_model_box.bind("<<ComboboxSelected>>", lambda _event: persist_voice_settings("Voice model selected"))
        ttk.Label(voice_grid, textvariable=voice_model_status_var, wraplength=500).grid(
            row=16,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 8),
        )

        ttk.Label(voice_grid, text="Microphone").grid(row=17, column=0, sticky="w", pady=5)
        voice_input_box = ttk.Combobox(
            voice_grid,
            textvariable=voice_input_device_var,
            values=input_device_labels,
            state="readonly",
        )
        voice_input_box.grid(row=17, column=1, sticky="ew", pady=5)
        voice_input_box.bind("<<ComboboxSelected>>", lambda _event: persist_voice_settings())

        ttk.Label(voice_grid, text="Response call sign").grid(row=18, column=0, sticky="w", pady=5)
        ttk.Entry(voice_grid, textvariable=voice_call_sign_var).grid(row=18, column=1, sticky="ew", pady=5)

        ttk.Checkbutton(
            voice_grid,
            text="Allow command sending",
            variable=allow_command_sending_var,
            command=lambda: persist_voice_settings("Command sending setting saved"),
        ).grid(row=19, column=0, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Label(
            voice_grid,
            text="Leave this off for practice. When on, only exact Voice Pilot command matches can send their configured keybind.",
            wraplength=500,
        ).grid(row=20, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(
            voice_grid,
            text="Only send when active window title matches",
            variable=require_target_window_var,
            command=lambda: persist_voice_settings("Window guard saved"),
        ).grid(row=21, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Entry(voice_grid, textvariable=voice_target_title_var).grid(row=22, column=0, columnspan=2, sticky="ew", pady=(0, 5))

        voice_buttons = ttk.Frame(voice_frame)
        voice_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(voice_buttons, text="Save Voice Settings", command=persist_voice_settings).pack(side="left")
        ttk.Button(voice_buttons, text="Refresh Models", command=refresh_voice_model_choices).pack(side="left", padx=(6, 0))
        ttk.Button(
            voice_buttons,
            text="Test Pet Voice",
            command=lambda: (
                persist_voice_settings("Voice test saved"),
                pet_speech.play_text(clean_voice_preview_text(voice_preview_text_var.get()), label="pet voice test"),
            ),
        ).pack(side="left", padx=(6, 0))

        ttk.Label(notes_frame, text="Discord voice notes", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            notes_frame,
            text=(
                "Send deliberate voice notes to a Discord notes channel. The webhook is stored only in a separate "
                "ignored local notes file and is not included in Intel Pet settings export."
            ),
            wraplength=620,
        ).pack(anchor="w", pady=(2, 10))

        discord_note_form = ttk.Frame(notes_frame)
        discord_note_form.pack(fill="x")
        discord_note_form.columnconfigure(1, weight=1)

        note_settings = current_discord_note_settings()
        note_enabled_var = tk.BooleanVar(value=note_settings.enabled)
        note_webhook_var = tk.StringVar(value=note_settings.webhook_url)
        note_sender_var = tk.StringVar(value=clean_discord_note_sender(note_settings.sender_name))
        note_trigger_var = tk.StringVar(value=", ".join(note_settings.trigger_phrases))
        note_close_var = tk.StringVar(value=", ".join(note_settings.close_phrases))
        note_cancel_var = tk.StringVar(value=", ".join(note_settings.cancel_phrases))
        note_test_var = tk.StringVar(value="gate camp near the Amarr undock")
        note_status_var = tk.StringVar(value=f"Notes settings file: {discord_note_settings_path}")
        note_inline_example_var = tk.StringVar()
        note_armed_example_var = tk.StringVar()

        def discord_note_preview_settings_from_form() -> IntelPetDiscordNoteSettings:
            return IntelPetDiscordNoteSettings(
                enabled=note_enabled_var.get(),
                webhook_url="",
                sender_name=clean_discord_note_sender(note_sender_var.get()),
                trigger_phrases=clean_discord_note_phrases(
                    note_trigger_var.get(),
                    default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
                ),
                close_phrases=clean_discord_note_phrases(
                    note_close_var.get(),
                    default=DEFAULT_DISCORD_NOTE_CLOSE_PHRASES,
                ),
                cancel_phrases=clean_discord_note_phrases(
                    note_cancel_var.get(),
                    default=DEFAULT_DISCORD_NOTE_CANCEL_PHRASES,
                ),
            )

        def discord_note_settings_from_form() -> IntelPetDiscordNoteSettings:
            return IntelPetDiscordNoteSettings(
                enabled=note_enabled_var.get(),
                webhook_url=clean_discord_note_webhook_url(note_webhook_var.get()),
                sender_name=clean_discord_note_sender(note_sender_var.get()),
                trigger_phrases=clean_discord_note_phrases(
                    note_trigger_var.get(),
                    default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
                ),
                close_phrases=clean_discord_note_phrases(
                    note_close_var.get(),
                    default=DEFAULT_DISCORD_NOTE_CLOSE_PHRASES,
                ),
                cancel_phrases=clean_discord_note_phrases(
                    note_cancel_var.get(),
                    default=DEFAULT_DISCORD_NOTE_CANCEL_PHRASES,
                ),
            )

        def refresh_note_phrase_preview(
            *,
            settings_override: IntelPetSettings | None = None,
            note_settings_override: IntelPetDiscordNoteSettings | None = None,
        ) -> None:
            preview_settings = settings_override or engine.current_settings()
            preview_note_settings = note_settings_override or discord_note_preview_settings_from_form()
            inline, armed = discord_note_example_phrases(
                preview_note_settings,
                call_sign=preview_settings.voice_call_sign,
                sample_note=note_test_var.get(),
            )
            note_inline_example_var.set(inline)
            note_armed_example_var.set(armed)

        def refresh_discord_note_fields(settings: IntelPetDiscordNoteSettings) -> None:
            note_enabled_var.set(settings.enabled)
            note_webhook_var.set(settings.webhook_url)
            note_sender_var.set(clean_discord_note_sender(settings.sender_name))
            note_trigger_var.set(", ".join(settings.trigger_phrases))
            note_close_var.set(", ".join(settings.close_phrases))
            note_cancel_var.set(", ".join(settings.cancel_phrases))
            refresh_note_phrase_preview(note_settings_override=settings)

        def persist_discord_note_settings(action: str = "Discord note settings saved") -> IntelPetDiscordNoteSettings | None:
            try:
                settings = discord_note_settings_from_form()
                save_discord_note_settings(discord_note_settings_path, settings)
                update_discord_note_settings(settings)
                refresh_discord_note_fields(settings)
            except Exception as exc:
                note_status_var.set(f"Save failed: {exc}")
                editor_status_var.set(f"Discord note save failed: {exc}")
                return None
            configured = "configured" if settings.webhook_url else "missing"
            enabled = "on" if settings.enabled else "off"
            note_status_var.set(f"{action}. Notes are {enabled}; webhook is {configured}.")
            editor_status_var.set(note_status_var.get())
            refresh_note_phrase_preview(note_settings_override=settings)
            refresh_voice_listener_summary()
            refresh_option_summary()
            return settings

        def send_test_discord_note() -> None:
            settings = persist_discord_note_settings("Discord note test settings saved")
            if settings is None:
                return
            status = send_discord_note(
                note_test_var.get(),
                settings,
                pilot_name=location_session.character_name if location_session is not None else "",
            )
            alert_queue.put(status)
            note_status_var.set(status.detail)
            editor_status_var.set(status.title)

        def use_tap_tap_trigger() -> None:
            triggers = clean_discord_note_phrases(
                note_trigger_var.get(),
                default=DEFAULT_DISCORD_NOTE_TRIGGER_PHRASES,
            )
            promoted = ("tap tap", *(trigger for trigger in triggers if trigger != "tap tap"))
            note_trigger_var.set(", ".join(promoted))
            persist_discord_note_settings("Tap tap trigger saved")

        for preview_var in (note_trigger_var, note_close_var, note_test_var, voice_call_sign_var):
            preview_var.trace_add("write", lambda *_args: refresh_note_phrase_preview())

        phrase_frame = ttk.LabelFrame(notes_frame, text="Current voice phrase", padding=10)
        phrase_frame.pack(fill="x", pady=(0, 12))
        phrase_frame.columnconfigure(1, weight=1)
        ttk.Label(phrase_frame, text="Inline note").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Label(phrase_frame, textvariable=note_inline_example_var, style="IntelPet.Phrase.TLabel", wraplength=520).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(10, 0),
            pady=3,
        )
        ttk.Label(phrase_frame, text="Arm capture").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Label(phrase_frame, textvariable=note_armed_example_var, style="IntelPet.Phrase.TLabel", wraplength=520).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(10, 0),
            pady=3,
        )
        ttk.Button(phrase_frame, text="Use Tap Tap Trigger", command=use_tap_tap_trigger).grid(
            row=2,
            column=1,
            sticky="w",
            padx=(10, 0),
            pady=(8, 0),
        )
        refresh_note_phrase_preview(note_settings_override=note_settings)

        ttk.Checkbutton(
            discord_note_form,
            text="Enable Discord voice notes",
            variable=note_enabled_var,
            command=lambda: persist_discord_note_settings("Discord note toggle saved"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(discord_note_form, text="Note webhook URL").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(discord_note_form, textvariable=note_webhook_var).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(
            discord_note_form,
            text="Use a webhook from the dedicated Discord notes channel. This local file is ignored by git.",
            wraplength=560,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(discord_note_form, text="Sender name").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(discord_note_form, textvariable=note_sender_var).grid(row=3, column=1, sticky="ew", pady=5)

        ttk.Label(discord_note_form, text="Trigger phrases").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(discord_note_form, textvariable=note_trigger_var).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Label(
            discord_note_form,
            text="Use the live phrase preview above; uncommon trigger pairs such as tap tap are easier to distinguish.",
            wraplength=560,
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(discord_note_form, text="Close phrases").grid(row=6, column=0, sticky="w", pady=5)
        ttk.Entry(discord_note_form, textvariable=note_close_var).grid(row=6, column=1, sticky="ew", pady=5)
        ttk.Label(
            discord_note_form,
            text="A close phrase sends the buffered note immediately. Otherwise the note sends after 2 seconds without new words.",
            wraplength=560,
        ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(discord_note_form, text="Cancel phrases").grid(row=8, column=0, sticky="w", pady=5)
        ttk.Entry(discord_note_form, textvariable=note_cancel_var).grid(row=8, column=1, sticky="ew", pady=5)

        test_frame = ttk.LabelFrame(notes_frame, text="Manual test", padding=8)
        test_frame.pack(fill="x", pady=(12, 0))
        test_frame.columnconfigure(1, weight=1)
        ttk.Label(test_frame, text="Test note").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(test_frame, textvariable=note_test_var).grid(row=0, column=1, sticky="ew", pady=5)
        note_buttons = ttk.Frame(test_frame)
        note_buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(note_buttons, text="Save Notes", command=persist_discord_note_settings).pack(side="left")
        ttk.Button(note_buttons, text="Send Test Note", command=send_test_discord_note).pack(side="left", padx=(6, 0))

        note_status_frame = ttk.LabelFrame(notes_frame, text="Status", padding=8)
        note_status_frame.pack(fill="x", pady=(12, 0))
        ttk.Label(note_status_frame, textvariable=note_status_var, wraplength=620).pack(anchor="w", fill="x")

        ttk.Label(missions_frame, text="Mission Library", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            missions_frame,
            text=(
                "Browse local mission entries by giver and read a selected briefing with the configured pet voice. "
                "Cloud voice cache buttons use your selected voice engine and may consume API quota."
            ),
            wraplength=700,
        ).pack(anchor="w", pady=(2, 10))

        mission_query_var = tk.StringVar()
        mission_status_var = tk.StringVar()
        mission_visible_entries: tuple[MissionLibraryEntry, ...] = ()
        mission_tree_index: dict[str, MissionLibraryEntry] = {}
        mission_form_id_var = tk.StringVar()
        mission_form_title_var = tk.StringVar()
        mission_form_giver_var = tk.StringVar()
        mission_form_corp_var = tk.StringVar()
        mission_form_faction_var = tk.StringVar()
        mission_form_level_var = tk.StringVar()
        mission_form_type_var = tk.StringVar()
        mission_form_objective_var = tk.StringVar()
        mission_form_isk_var = tk.StringVar()
        mission_form_bonus_var = tk.StringVar()
        mission_form_lp_var = tk.StringVar()
        mission_form_items_var = tk.StringVar()
        mission_form_standings_var = tk.StringVar()
        mission_form_source_var = tk.StringVar()
        mission_form_source_url_var = tk.StringVar()
        mission_form_tags_var = tk.StringVar()
        mission_read_settings = engine.current_settings()
        mission_read_opener_var = tk.StringVar(value=clean_mission_read_opener(mission_read_settings.mission_read_opener))
        mission_read_giver_var = tk.BooleanVar(value=mission_read_settings.mission_read_include_giver)
        mission_read_level_var = tk.BooleanVar(value=mission_read_settings.mission_read_include_level)
        mission_read_rewards_var = tk.BooleanVar(value=mission_read_settings.mission_read_include_rewards)
        mission_read_reward_notes_var = tk.BooleanVar(value=mission_read_settings.mission_read_include_reward_notes)
        mission_read_source_var = tk.BooleanVar(value=mission_read_settings.mission_read_include_source)
        mission_read_completion_var = tk.BooleanVar(value=mission_read_settings.mission_read_include_completion)
        mission_read_briefing_var = tk.BooleanVar(value=mission_read_settings.mission_read_include_briefing)

        mission_search_frame = ttk.Frame(missions_frame)
        mission_search_frame.pack(fill="x", pady=(0, 8))
        mission_search_frame.columnconfigure(1, weight=1)
        ttk.Label(mission_search_frame, text="Search").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(mission_search_frame, textvariable=mission_query_var).grid(row=0, column=1, sticky="ew")

        mission_body = ttk.Frame(missions_frame)
        mission_body.pack(fill="both", expand=True)
        mission_body.columnconfigure(0, weight=1)
        mission_body.columnconfigure(1, weight=1)
        mission_body.rowconfigure(0, weight=1)

        mission_tree_frame = ttk.Frame(mission_body)
        mission_tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        mission_tree_frame.columnconfigure(0, weight=1)
        mission_tree_frame.rowconfigure(0, weight=1)
        mission_columns = ("level", "type", "rewards")
        mission_tree = ttk.Treeview(
            mission_tree_frame,
            columns=mission_columns,
            show="tree headings",
            height=16,
        )
        mission_tree.heading("#0", text="Mission giver / mission")
        mission_tree.heading("level", text="Level")
        mission_tree.heading("type", text="Type")
        mission_tree.heading("rewards", text="Rewards")
        mission_tree.column("#0", width=260, minwidth=180)
        mission_tree.column("level", width=80, minwidth=70, stretch=False)
        mission_tree.column("type", width=130, minwidth=90)
        mission_tree.column("rewards", width=220, minwidth=140)
        mission_tree_scroll = ttk.Scrollbar(mission_tree_frame, orient="vertical", command=mission_tree.yview)
        mission_tree.configure(yscrollcommand=mission_tree_scroll.set)
        mission_tree.grid(row=0, column=0, sticky="nsew")
        mission_tree_scroll.grid(row=0, column=1, sticky="ns")

        mission_detail_frame = ttk.LabelFrame(mission_body, text="Selected mission", padding=8)
        mission_detail_frame.grid(row=0, column=1, sticky="nsew")
        mission_detail_frame.columnconfigure(0, weight=1)
        mission_detail_frame.rowconfigure(0, weight=1)
        mission_detail = tk.Text(
            mission_detail_frame,
            wrap="word",
            height=16,
            bg=ui_colors["panel"],
            fg=ui_colors["text"],
            insertbackground=ui_colors["text"],
            relief="flat",
            padx=8,
            pady=8,
        )
        mission_detail_scroll = ttk.Scrollbar(mission_detail_frame, orient="vertical", command=mission_detail.yview)
        mission_detail.configure(yscrollcommand=mission_detail_scroll.set, state="disabled")
        mission_detail.grid(row=0, column=0, sticky="nsew")
        mission_detail_scroll.grid(row=0, column=1, sticky="ns")

        mission_editor_frame = ttk.LabelFrame(missions_frame, text="Edit local mission entry", padding=8)
        mission_editor_frame.pack(fill="x", pady=(12, 0))
        for column in (1, 3):
            mission_editor_frame.columnconfigure(column, weight=1)

        def add_mission_field(row: int, label: str, variable: tk.StringVar, column: int = 0) -> None:
            ttk.Label(mission_editor_frame, text=label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(mission_editor_frame, textvariable=variable).grid(
                row=row,
                column=column + 1,
                sticky="ew",
                padx=(0 if column == 0 else 8, 0),
                pady=3,
            )

        add_mission_field(0, "ID", mission_form_id_var)
        add_mission_field(0, "Title", mission_form_title_var, column=2)
        add_mission_field(1, "Mission giver", mission_form_giver_var)
        add_mission_field(1, "Corporation", mission_form_corp_var, column=2)
        add_mission_field(2, "Faction", mission_form_faction_var)
        add_mission_field(2, "Level", mission_form_level_var, column=2)
        add_mission_field(3, "Type", mission_form_type_var)
        add_mission_field(3, "Tags", mission_form_tags_var, column=2)
        add_mission_field(4, "Objective", mission_form_objective_var)
        add_mission_field(4, "ISK reward", mission_form_isk_var, column=2)
        add_mission_field(5, "Bonus ISK", mission_form_bonus_var)
        add_mission_field(5, "LP reward", mission_form_lp_var, column=2)
        add_mission_field(6, "Item rewards", mission_form_items_var)
        add_mission_field(6, "Standing rewards", mission_form_standings_var, column=2)
        add_mission_field(7, "Source", mission_form_source_var)
        add_mission_field(7, "Source URL", mission_form_source_url_var, column=2)

        ttk.Label(mission_editor_frame, text="Completion steps").grid(row=8, column=0, sticky="nw", padx=(0, 8), pady=3)
        mission_form_completion_steps = tk.Text(
            mission_editor_frame,
            height=5,
            wrap="word",
            bg=ui_colors["field"],
            fg="#111827",
            insertbackground="#111827",
        )
        mission_form_completion_steps.grid(row=8, column=1, columnspan=3, sticky="ew", pady=3)

        ttk.Label(mission_editor_frame, text="Completion notes").grid(row=9, column=0, sticky="nw", padx=(0, 8), pady=3)
        mission_form_completion_notes = tk.Text(
            mission_editor_frame,
            height=3,
            wrap="word",
            bg=ui_colors["field"],
            fg="#111827",
            insertbackground="#111827",
        )
        mission_form_completion_notes.grid(row=9, column=1, columnspan=3, sticky="ew", pady=3)

        ttk.Label(mission_editor_frame, text="Reward notes").grid(row=10, column=0, sticky="nw", padx=(0, 8), pady=3)
        mission_form_reward_notes = tk.Text(
            mission_editor_frame,
            height=3,
            wrap="word",
            bg=ui_colors["field"],
            fg="#111827",
            insertbackground="#111827",
        )
        mission_form_reward_notes.grid(row=10, column=1, columnspan=3, sticky="ew", pady=3)

        ttk.Label(mission_editor_frame, text="Briefing text").grid(row=11, column=0, sticky="nw", padx=(0, 8), pady=3)
        mission_form_briefing = tk.Text(
            mission_editor_frame,
            height=6,
            wrap="word",
            bg=ui_colors["field"],
            fg="#111827",
            insertbackground="#111827",
        )
        mission_form_briefing.grid(row=11, column=1, columnspan=3, sticky="ew", pady=3)

        mission_read_frame = ttk.LabelFrame(missions_frame, text="Read-aloud format", padding=8)
        mission_read_frame.pack(fill="x", pady=(12, 0))
        mission_read_frame.columnconfigure(1, weight=1)
        ttk.Label(mission_read_frame, text="Opening phrase").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(mission_read_frame, textvariable=mission_read_opener_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Checkbutton(mission_read_frame, text="Mission giver", variable=mission_read_giver_var).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(mission_read_frame, text="Level/type", variable=mission_read_level_var).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Checkbutton(mission_read_frame, text="Rewards", variable=mission_read_rewards_var).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(mission_read_frame, text="Reward notes", variable=mission_read_reward_notes_var).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Checkbutton(mission_read_frame, text="Source", variable=mission_read_source_var).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Checkbutton(mission_read_frame, text="Completion details", variable=mission_read_completion_var).grid(
            row=3,
            column=1,
            sticky="w",
            pady=2,
        )
        ttk.Checkbutton(mission_read_frame, text="Briefing text", variable=mission_read_briefing_var).grid(row=4, column=0, sticky="w", pady=2)

        def set_mission_detail(text: str) -> None:
            mission_detail.configure(state="normal")
            mission_detail.delete("1.0", tk.END)
            mission_detail.insert("1.0", text)
            mission_detail.configure(state="disabled")

        def set_text_widget(widget: tk.Text, text: str) -> None:
            widget.delete("1.0", tk.END)
            widget.insert("1.0", text)

        def get_text_widget(widget: tk.Text) -> str:
            return normalize_response_text(widget.get("1.0", "end").strip())

        def mission_form_terms(value: str) -> tuple[str, ...]:
            terms: list[str] = []
            for line in str(value or "").splitlines():
                for item in line.split(","):
                    text = normalize_response_text(item)
                    if text:
                        terms.append(text)
            return tuple(dedupe_preserve_order(terms))

        def mission_entry_from_form() -> MissionLibraryEntry:
            payload = {
                "id": mission_form_id_var.get(),
                "title": mission_form_title_var.get(),
                "mission_giver": mission_form_giver_var.get(),
                "agent_corporation": mission_form_corp_var.get(),
                "faction": mission_form_faction_var.get(),
                "level": mission_form_level_var.get(),
                "mission_type": mission_form_type_var.get(),
                "objective_text": mission_form_objective_var.get(),
                "completion_steps": mission_form_terms(mission_form_completion_steps.get("1.0", "end").strip()),
                "completion_notes": get_text_widget(mission_form_completion_notes),
                "briefing_text": get_text_widget(mission_form_briefing),
                "standing_rewards": mission_form_terms(mission_form_standings_var.get()),
                "isk_reward": mission_form_isk_var.get(),
                "bonus_isk_reward": mission_form_bonus_var.get(),
                "item_rewards": mission_form_terms(mission_form_items_var.get()),
                "lp_reward": mission_form_lp_var.get(),
                "reward_notes": get_text_widget(mission_form_reward_notes),
                "source": mission_form_source_var.get(),
                "source_url": mission_form_source_url_var.get(),
                "tags": mission_form_terms(mission_form_tags_var.get()),
            }
            return mission_entry_from_dict(payload)

        def clear_mission_form() -> None:
            for variable in (
                mission_form_id_var,
                mission_form_title_var,
                mission_form_giver_var,
                mission_form_corp_var,
                mission_form_faction_var,
                mission_form_level_var,
                mission_form_type_var,
                mission_form_objective_var,
                mission_form_isk_var,
                mission_form_bonus_var,
                mission_form_lp_var,
                mission_form_items_var,
                mission_form_standings_var,
                mission_form_source_var,
                mission_form_source_url_var,
                mission_form_tags_var,
            ):
                variable.set("")
            set_text_widget(mission_form_reward_notes, "")
            set_text_widget(mission_form_completion_steps, "")
            set_text_widget(mission_form_completion_notes, "")
            set_text_widget(mission_form_briefing, "")
            mission_status_var.set(f"New local mission entry. Saves to {USER_MISSION_LIBRARY_PATH}.")

        def load_mission_form(entry: MissionLibraryEntry) -> None:
            mission_form_id_var.set(entry.id)
            mission_form_title_var.set(entry.title)
            mission_form_giver_var.set(entry.mission_giver)
            mission_form_corp_var.set(entry.agent_corporation)
            mission_form_faction_var.set(entry.faction)
            mission_form_level_var.set(entry.level)
            mission_form_type_var.set(entry.mission_type)
            mission_form_objective_var.set(entry.objective_text)
            mission_form_isk_var.set(entry.isk_reward)
            mission_form_bonus_var.set(entry.bonus_isk_reward)
            mission_form_lp_var.set(entry.lp_reward)
            mission_form_items_var.set(", ".join(entry.item_rewards))
            mission_form_standings_var.set(", ".join(entry.standing_rewards))
            mission_form_source_var.set(entry.source)
            mission_form_source_url_var.set(entry.source_url)
            mission_form_tags_var.set(", ".join(entry.tags))
            set_text_widget(mission_form_completion_steps, "\n".join(entry.completion_steps))
            set_text_widget(mission_form_completion_notes, entry.completion_notes)
            set_text_widget(mission_form_reward_notes, entry.reward_notes)
            set_text_widget(mission_form_briefing, entry.briefing_text)
            mission_status_var.set(f"Loaded {entry.title} for local editing.")

        def mission_read_options_from_form() -> MissionReadOptions:
            return MissionReadOptions(
                opener=clean_mission_read_opener(mission_read_opener_var.get()),
                include_giver=mission_read_giver_var.get(),
                include_level=mission_read_level_var.get(),
                include_rewards=mission_read_rewards_var.get(),
                include_reward_notes=mission_read_reward_notes_var.get(),
                include_source=mission_read_source_var.get(),
                include_completion=mission_read_completion_var.get(),
                include_briefing=mission_read_briefing_var.get(),
            )

        def persist_mission_read_settings(action: str = "Mission read-aloud settings saved") -> IntelPetSettings | None:
            try:
                settings = replace_mission_read_settings(
                    engine.current_settings(),
                    opener=mission_read_opener_var.get(),
                    include_giver=mission_read_giver_var.get(),
                    include_level=mission_read_level_var.get(),
                    include_rewards=mission_read_rewards_var.get(),
                    include_reward_notes=mission_read_reward_notes_var.get(),
                    include_source=mission_read_source_var.get(),
                    include_completion=mission_read_completion_var.get(),
                    include_briefing=mission_read_briefing_var.get(),
                )
                save_settings(settings_path, settings)
                engine.update_settings(settings)
            except Exception as exc:
                mission_status_var.set(f"Mission read settings failed: {exc}")
                editor_status_var.set(mission_status_var.get())
                return None
            mission_status_var.set(action)
            editor_status_var.set(action)
            return settings

        def mission_reward_label(entry: MissionLibraryEntry) -> str:
            if entry.isk_reward or entry.lp_reward:
                return ", ".join(item for item in (entry.isk_reward, entry.lp_reward) if item)
            if entry.item_rewards:
                return ", ".join(entry.item_rewards[:2])
            return "not recorded"

        def selected_mission_entry() -> MissionLibraryEntry | None:
            selection = mission_tree.selection()
            if not selection:
                return None
            return mission_tree_index.get(selection[0])

        def refresh_mission_status() -> None:
            loaded = current_mission_entries()
            path = mission_library_path()
            visible = len(mission_visible_entries)
            mission_status_var.set(f"{visible} shown / {len(loaded)} loaded from {path}")

        def refresh_mission_tree(*_args: Any) -> None:
            nonlocal mission_visible_entries
            query = mission_query_var.get()
            mission_tree_index.clear()
            mission_tree.delete(*mission_tree.get_children())
            loaded = current_mission_entries()
            mission_visible_entries = tuple(entry for entry in loaded if mission_matches_query(entry, query))
            for giver_index, (giver, entries_for_giver) in enumerate(grouped_missions_by_giver(mission_visible_entries).items()):
                giver_id = f"giver:{giver_index}"
                mission_tree.insert("", tk.END, iid=giver_id, text=giver, open=True, values=("", "", ""))
                for entry_index, entry in enumerate(entries_for_giver):
                    item_id = f"mission:{giver_index}:{entry_index}:{entry.id}"
                    mission_tree_index[item_id] = entry
                    mission_tree.insert(
                        giver_id,
                        tk.END,
                        iid=item_id,
                        text=entry.title,
                        values=(entry.level, entry.mission_type, mission_reward_label(entry)),
                    )
            if mission_visible_entries:
                first_child = mission_tree.get_children(mission_tree.get_children()[0])[0]
                mission_tree.selection_set(first_child)
                mission_tree.focus(first_child)
                set_mission_detail(mission_detail_text(mission_visible_entries[0]))
            else:
                set_mission_detail("No missions match the current search.")
            refresh_mission_status()

        def refresh_selected_mission_detail(_event: Any | None = None) -> None:
            entry = selected_mission_entry()
            if entry is None:
                return
            set_mission_detail(mission_detail_text(entry))

        def edit_selected_mission() -> None:
            entry = selected_mission_entry()
            if entry is None:
                mission_status_var.set("Select a mission first.")
                return
            load_mission_form(entry)

        def save_mission_form() -> None:
            try:
                entry = mission_entry_from_form()
                upsert_user_mission_entry(entry)
                reload_mission_entries()
                mission_query_var.set(entry.title)
                refresh_mission_tree()
                for item_id, item_entry in mission_tree_index.items():
                    if item_entry.id == entry.id:
                        mission_tree.selection_set(item_id)
                        mission_tree.focus(item_id)
                        break
            except Exception as exc:
                mission_status_var.set(f"Mission save failed: {exc}")
                editor_status_var.set(mission_status_var.get())
                return
            mission_status_var.set(f"Saved local mission entry: {entry.title}")
            editor_status_var.set(mission_status_var.get())

        def delete_mission_form() -> None:
            entry_id = normalize_response_text(mission_form_id_var.get())
            if not entry_id:
                mission_status_var.set("Load or enter a mission ID before deleting a local entry.")
                return
            try:
                delete_user_mission_entry(entry_id)
                reload_mission_entries()
                refresh_mission_tree()
                clear_mission_form()
            except Exception as exc:
                mission_status_var.set(f"Mission delete failed: {exc}")
                editor_status_var.set(mission_status_var.get())
                return
            mission_status_var.set(f"Deleted local mission override: {entry_id}")
            editor_status_var.set(mission_status_var.get())

        def preview_mission_spoken_text() -> None:
            entry = selected_mission_entry()
            if entry is None:
                try:
                    entry = mission_entry_from_form()
                except Exception:
                    mission_status_var.set("Select a mission or fill in a valid mission title first.")
                    return
            set_mission_detail(mission_read_aloud_text(entry, mission_read_options_from_form()))
            mission_status_var.set("Showing spoken read-aloud text preview.")

        def read_selected_mission() -> None:
            entry = selected_mission_entry()
            if entry is None:
                mission_status_var.set("Select a mission first.")
                return
            persist_mission_read_settings()
            configure_pet_speech(engine.current_settings())
            pet_speech.play_text(
                mission_read_aloud_text(entry, mission_read_options_from_settings(engine.current_settings())),
                label=f"mission briefing for {entry.title}",
            )
            mission_status_var.set(f"Reading mission briefing: {entry.title}")
            editor_status_var.set(mission_status_var.get())

        def cache_mission_entries(entries: Iterable[MissionLibraryEntry], *, force: bool = False) -> None:
            selected_entries = tuple(entries)
            if not selected_entries:
                mission_status_var.set("No mission briefings to cache.")
                return
            persist_mission_read_settings()
            configure_pet_speech(engine.current_settings())
            read_options = mission_read_options_from_settings(engine.current_settings())
            for entry in selected_entries:
                pet_speech.prepare_text_async(
                    mission_read_aloud_text(entry, read_options),
                    label=f"mission briefing for {entry.title}",
                    force=force,
                )
            mission_status_var.set(f"Queued {len(selected_entries)} mission briefing cache job(s).")
            editor_status_var.set(mission_status_var.get())

        def cache_selected_mission() -> None:
            entry = selected_mission_entry()
            if entry is None:
                mission_status_var.set("Select a mission first.")
                return
            cache_mission_entries((entry,))

        def cache_visible_missions() -> None:
            cache_mission_entries(mission_visible_entries)

        def cache_all_missions() -> None:
            cache_mission_entries(current_mission_entries())

        def reload_mission_library_ui() -> None:
            reload_mission_entries()
            refresh_mission_tree()
            editor_status_var.set(mission_status_var.get())

        mission_tree.bind("<<TreeviewSelect>>", refresh_selected_mission_detail)
        mission_query_var.trace_add("write", refresh_mission_tree)

        mission_editor_buttons = ttk.Frame(mission_editor_frame)
        mission_editor_buttons.grid(row=12, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(mission_editor_buttons, text="New Mission", command=clear_mission_form).pack(side="left")
        ttk.Button(mission_editor_buttons, text="Edit Selected", command=edit_selected_mission).pack(side="left", padx=(6, 0))
        ttk.Button(mission_editor_buttons, text="Save Local Mission", command=save_mission_form).pack(side="left", padx=(6, 0))
        ttk.Button(mission_editor_buttons, text="Delete Local Override", command=delete_mission_form).pack(side="left", padx=(6, 0))

        mission_read_buttons = ttk.Frame(mission_read_frame)
        mission_read_buttons.grid(row=5, column=1, sticky="w", pady=(8, 0))
        ttk.Button(mission_read_buttons, text="Save Read Format", command=persist_mission_read_settings).pack(side="left")
        ttk.Button(mission_read_buttons, text="Preview Spoken Text", command=preview_mission_spoken_text).pack(
            side="left",
            padx=(6, 0),
        )

        mission_buttons = ttk.Frame(missions_frame)
        mission_buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(mission_buttons, text="Read Selected", command=read_selected_mission).pack(side="left")
        ttk.Button(mission_buttons, text="Cache Selected", command=cache_selected_mission).pack(side="left", padx=(6, 0))
        ttk.Button(mission_buttons, text="Cache Visible", command=cache_visible_missions).pack(side="left", padx=(6, 0))
        ttk.Button(mission_buttons, text="Cache All", command=cache_all_missions).pack(side="left", padx=(6, 0))
        ttk.Button(mission_buttons, text="Reload Library", command=reload_mission_library_ui).pack(side="left", padx=(6, 0))

        mission_hint = ttk.LabelFrame(missions_frame, text="Voice phrases", padding=8)
        mission_hint.pack(fill="x", pady=(12, 0))
        ttk.Label(
            mission_hint,
            text=(
                f'Say "{clean_voice_call_sign(engine.current_settings().voice_call_sign)} read mission Cash Flow for Capsuleers" '
                'or "cache mission The Blood-Stained Stars". Add full licensed mission data to '
                "profiles\\intel_pet_missions.json when you are ready to expand beyond the starter library."
            ),
            wraplength=700,
        ).pack(anchor="w")

        mission_status_frame = ttk.LabelFrame(missions_frame, text="Status", padding=8)
        mission_status_frame.pack(fill="x", pady=(12, 0))
        ttk.Label(mission_status_frame, textvariable=mission_status_var, wraplength=700).pack(anchor="w", fill="x")
        refresh_mission_tree()

        ttk.Label(reliability_frame, text="Voice Reliability", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        reliability_status_var = tk.StringVar(value="No voice attempts yet.")
        reliability_body = ttk.Frame(reliability_frame)
        reliability_body.pack(fill="both", expand=True, pady=(8, 0))
        reliability_body.columnconfigure(0, weight=1)
        reliability_body.rowconfigure(0, weight=1)
        reliability_columns = ("recorded_at", "engine", "heard", "result", "command", "blocked", "guard")
        reliability_tree = ttk.Treeview(
            reliability_body,
            columns=reliability_columns,
            show="headings",
            height=14,
        )
        reliability_tree.heading("recorded_at", text="Time")
        reliability_tree.heading("engine", text="Engine")
        reliability_tree.heading("heard", text="Heard")
        reliability_tree.heading("result", text="Result")
        reliability_tree.heading("command", text="Command")
        reliability_tree.heading("blocked", text="Blocked Reason")
        reliability_tree.heading("guard", text="Active Window")
        reliability_tree.column("recorded_at", width=138, minwidth=110, stretch=False)
        reliability_tree.column("engine", width=120, minwidth=95, stretch=False)
        reliability_tree.column("heard", width=210, minwidth=140, stretch=True)
        reliability_tree.column("result", width=92, minwidth=80, stretch=False)
        reliability_tree.column("command", width=170, minwidth=120, stretch=True)
        reliability_tree.column("blocked", width=210, minwidth=130, stretch=True)
        reliability_tree.column("guard", width=190, minwidth=130, stretch=True)
        reliability_tree.grid(row=0, column=0, sticky="nsew")
        reliability_y = ttk.Scrollbar(reliability_body, orient="vertical", command=reliability_tree.yview)
        reliability_y.grid(row=0, column=1, sticky="ns")
        reliability_x = ttk.Scrollbar(reliability_body, orient="horizontal", command=reliability_tree.xview)
        reliability_x.grid(row=1, column=0, sticky="ew")
        reliability_tree.configure(yscrollcommand=reliability_y.set, xscrollcommand=reliability_x.set)

        def current_voice_reliability_rows() -> tuple[IntelPetVoiceReliabilityRow, ...]:
            return voice_reliability_rows(history_items, engine.current_settings(), limit=20)

        def refresh_voice_reliability() -> None:
            rows = current_voice_reliability_rows()
            reliability_tree.delete(*reliability_tree.get_children())
            for row in rows:
                reliability_tree.insert(
                    "",
                    "end",
                    values=(
                        row.recorded_at,
                        row.engine,
                        row.heard or "-",
                        row.outcome,
                        row.command or "-",
                        row.blocked_reason or "-",
                        row.active_window_check,
                    ),
                )
            reliability_status_var.set(
                f"{diagnostic_count_label(len(rows), 'recent voice attempt')} shown."
                if rows
                else "No voice attempts yet."
            )

        def copy_voice_reliability_summary() -> None:
            rows = current_voice_reliability_rows()
            lines = ["Intel Pet Voice Reliability"]
            if not rows:
                lines.append("No voice attempts yet.")
            for row in rows:
                lines.append(
                    " | ".join(
                        (
                            row.recorded_at,
                            row.outcome,
                            row.engine,
                            f"heard={row.heard or '-'}",
                            f"command={row.command or '-'}",
                            f"blocked={row.blocked_reason or '-'}",
                            f"active_window={row.active_window_check}",
                        )
                    )
                )
            editor.clipboard_clear()
            editor.clipboard_append("\n".join(lines))
            editor_status_var.set("Voice reliability summary copied.")

        reliability_buttons = ttk.Frame(reliability_frame)
        reliability_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(reliability_buttons, text="Refresh", command=refresh_voice_reliability).pack(side="left")
        ttk.Button(reliability_buttons, text="Copy Summary", command=copy_voice_reliability_summary).pack(
            side="left",
            padx=(6, 0),
        )
        ttk.Label(reliability_frame, textvariable=reliability_status_var, wraplength=620).pack(anchor="w", pady=(8, 0))
        refresh_voice_reliability()
        history_refreshers.append(refresh_voice_reliability)

        ttk.Label(voice_lab_frame, text="Voice Lab", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            voice_lab_frame,
            text="Edit the local Voice Pilot command profile and test phrases without sending keys.",
            wraplength=520,
        ).pack(anchor="w", pady=(2, 8))

        voice_lab_state: dict[str, Any] = {}
        voice_profile_path_var = tk.StringVar(value="Loading voice profile...")
        voice_lab_status_var = tk.StringVar(value="Voice Lab tests never send keys.")
        voice_command_filter_var = tk.StringVar()
        voice_command_count_var = tk.StringVar(value="0 commands")
        ttk.Label(voice_lab_frame, textvariable=voice_profile_path_var, wraplength=520).pack(anchor="w", pady=(0, 8))

        voice_command_body = ttk.Frame(voice_lab_frame)
        voice_command_body.pack(fill="both", expand=True)
        voice_command_body.columnconfigure(0, weight=3)
        voice_command_body.columnconfigure(1, weight=2)
        voice_command_body.rowconfigure(0, weight=1)

        voice_command_list_frame = ttk.Frame(voice_command_body)
        voice_command_list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        voice_command_list_frame.rowconfigure(1, weight=1)
        voice_command_list_frame.columnconfigure(0, weight=1)

        voice_command_filter_frame = ttk.Frame(voice_command_list_frame)
        voice_command_filter_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        voice_command_filter_frame.columnconfigure(1, weight=1)
        ttk.Label(voice_command_filter_frame, text="Search").grid(row=0, column=0, sticky="w")
        voice_command_filter_entry = ttk.Entry(voice_command_filter_frame, textvariable=voice_command_filter_var)
        voice_command_filter_entry.grid(row=0, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(voice_command_filter_frame, text="Clear", command=lambda: clear_voice_command_filter()).grid(
            row=0,
            column=2,
            sticky="e",
        )
        ttk.Label(voice_command_filter_frame, textvariable=voice_command_count_var).grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(3, 0),
        )

        voice_command_tree = ttk.Treeview(
            voice_command_list_frame,
            columns=("phrases", "key"),
            show="tree headings",
            height=10,
        )
        voice_command_tree.heading("#0", text="Command")
        voice_command_tree.heading("phrases", text="Phrases")
        voice_command_tree.heading("key", text="Keybind")
        voice_command_tree.column("#0", width=150, minwidth=110, stretch=True)
        voice_command_tree.column("phrases", width=230, minwidth=160, stretch=True)
        voice_command_tree.column("key", width=130, minwidth=110, stretch=False)
        voice_command_scroll = ttk.Scrollbar(voice_command_list_frame, orient="vertical", command=voice_command_tree.yview)
        voice_command_tree.configure(yscrollcommand=voice_command_scroll.set)
        voice_command_tree.grid(row=1, column=0, sticky="nsew")
        voice_command_scroll.grid(row=1, column=1, sticky="ns")

        voice_edit_frame = ttk.LabelFrame(voice_command_body, text="Command", padding=8)
        voice_edit_frame.grid(row=0, column=1, sticky="nsew")
        voice_edit_frame.columnconfigure(1, weight=1)
        command_name_var = tk.StringVar()
        command_phrases_var = tk.StringVar()
        command_key_var = tk.StringVar()
        command_hold_var = tk.StringVar(value=f"{DEFAULT_HOLD_SECONDS:.2f}")
        command_press_count_var = tk.StringVar(value=str(DEFAULT_PRESS_COUNT))
        command_repeat_gap_var = tk.StringVar(value=f"{DEFAULT_REPEAT_GAP_SECONDS:.2f}")
        command_response_suffix_var = tk.StringVar()
        command_response_text_var = tk.StringVar()
        command_preview_var = tk.StringVar(value=voice_command_preview_text(None))

        ttk.Label(voice_edit_frame, text="Name").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_name_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(voice_edit_frame, text="Phrases").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_phrases_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(voice_edit_frame, text="Keybind").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_key_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(voice_edit_frame, text="Hold").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_hold_var, width=10).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(voice_edit_frame, text="Presses").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_press_count_var, width=10).grid(row=4, column=1, sticky="w", pady=3)
        ttk.Label(voice_edit_frame, text="Gap").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_repeat_gap_var, width=10).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Label(voice_edit_frame, text="Voice label").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_response_suffix_var).grid(row=6, column=1, sticky="ew", pady=3)
        ttk.Label(voice_edit_frame, text="Response text").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Entry(voice_edit_frame, textvariable=command_response_text_var).grid(row=7, column=1, sticky="ew", pady=3)
        command_preview_frame = ttk.LabelFrame(voice_edit_frame, text="Selected preview", padding=6)
        command_preview_frame.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(command_preview_frame, textvariable=command_preview_var, wraplength=320, justify="left").pack(
            anchor="w",
            fill="x",
        )

        def current_voice_lab_profile() -> CommandProfile:
            profile = voice_lab_state.get("profile")
            if isinstance(profile, CommandProfile):
                return profile
            profile = CommandProfile()
            voice_lab_state["profile"] = profile
            return profile

        def current_voice_lab_save_path() -> Path:
            path = voice_lab_state.get("save_path")
            return Path(path) if path else USER_VOICE_PROFILE

        def refresh_voice_command_tree(select_index: int | None = None) -> None:
            profile = current_voice_lab_profile()
            visible_indices = filtered_voice_command_indices(profile.commands, voice_command_filter_var.get())
            voice_command_tree.delete(*voice_command_tree.get_children())
            for index in visible_indices:
                command = profile.commands[index]
                voice_command_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    text=command.name,
                    values=(", ".join(command.phrases), command.key),
                )
            if voice_command_filter_var.get().strip():
                voice_command_count_var.set(f"{len(visible_indices)} shown / {len(profile.commands)} commands")
            else:
                voice_command_count_var.set(f"{len(profile.commands)} commands")
            if select_index is not None and select_index in visible_indices:
                item_id = str(select_index)
                voice_command_tree.selection_set(item_id)
                voice_command_tree.see(item_id)

        def selected_voice_command_index() -> int | None:
            selection = voice_command_tree.selection()
            if not selection:
                return None
            return int(selection[0])

        def set_voice_command_fields(command: VoiceCommand | None = None) -> None:
            command_name_var.set(command.name if command else "")
            command_phrases_var.set(", ".join(command.phrases) if command else "")
            command_key_var.set(command.key if command else "")
            command_hold_var.set(f"{command.hold_seconds:.2f}" if command else f"{DEFAULT_HOLD_SECONDS:.2f}")
            command_press_count_var.set(str(command.press_count) if command else str(DEFAULT_PRESS_COUNT))
            command_repeat_gap_var.set(
                f"{command.repeat_gap_seconds:.2f}" if command else f"{DEFAULT_REPEAT_GAP_SECONDS:.2f}"
            )
            command_response_suffix_var.set(command.response_suffix if command else "")
            command_response_text_var.set(command.response_text if command else "")
            command_preview_var.set(voice_command_preview_text(command))

        def fill_selected_voice_command(_event: Any | None = None) -> None:
            index = selected_voice_command_index()
            profile = current_voice_lab_profile()
            if index is not None and 0 <= index < len(profile.commands):
                set_voice_command_fields(profile.commands[index])

        def apply_voice_command_filter(_event: Any | None = None) -> None:
            refresh_voice_command_tree(select_index=selected_voice_command_index())

        def clear_voice_command_filter() -> None:
            voice_command_filter_var.set("")
            refresh_voice_command_tree(select_index=selected_voice_command_index())
            voice_lab_status_var.set("Command filter cleared.")

        def load_voice_lab_profile() -> None:
            try:
                profile, save_path, source_path = load_editable_voice_profile()
            except Exception as exc:
                profile = CommandProfile()
                save_path = USER_VOICE_PROFILE
                source_path = USER_VOICE_PROFILE
                voice_lab_status_var.set(f"Could not load voice profile: {exc}")
            voice_lab_state["profile"] = profile
            voice_lab_state["save_path"] = save_path
            voice_lab_state["source_path"] = source_path
            source_note = "" if Path(source_path) == Path(save_path) else f" copied from {source_path}"
            voice_profile_path_var.set(f"Saving commands to {save_path}{source_note}")
            refresh_voice_command_tree(select_index=0 if profile.commands else None)
            if profile.commands:
                set_voice_command_fields(profile.commands[0])
            else:
                set_voice_command_fields()

        def save_voice_lab_profile(action: str, select_index: int | None = None) -> None:
            profile = current_voice_lab_profile()
            save_path = current_voice_lab_save_path()
            try:
                profile.save(save_path)
            except Exception as exc:
                voice_lab_status_var.set(f"Save failed: {exc}")
                return
            voice_lab_state["save_path"] = save_path
            voice_lab_state["source_path"] = save_path
            voice_profile_path_var.set(f"Saving commands to {save_path}")
            refresh_voice_command_tree(select_index=select_index)
            voice_lab_status_var.set(f"{action}. {len(profile.commands)} command{'s' if len(profile.commands) != 1 else ''} saved.")

        def command_from_voice_lab_fields() -> VoiceCommand | None:
            try:
                return voice_command_from_fields(
                    name=command_name_var.get(),
                    phrases=command_phrases_var.get(),
                    key=command_key_var.get(),
                    hold_seconds=command_hold_var.get(),
                    press_count=command_press_count_var.get(),
                    repeat_gap_seconds=command_repeat_gap_var.get(),
                    response_suffix=command_response_suffix_var.get(),
                    response_text=command_response_text_var.get(),
                )
            except ValueError as exc:
                voice_lab_status_var.set(str(exc))
                return None

        def new_voice_command() -> None:
            voice_command_tree.selection_remove(voice_command_tree.selection())
            set_voice_command_fields()
            voice_lab_status_var.set("Enter a command, then Save Command.")

        def save_voice_command() -> None:
            command = command_from_voice_lab_fields()
            if command is None:
                return
            profile = current_voice_lab_profile()
            index = selected_voice_command_index()
            if index is None or not 0 <= index < len(profile.commands):
                profile.commands.append(command)
                index = len(profile.commands) - 1
                action = "Added command"
            else:
                profile.commands[index] = command
                action = "Changed command"
            if not voice_command_matches_filter(command, voice_command_filter_var.get()):
                voice_command_filter_var.set("")
            save_voice_lab_profile(action, select_index=index)
            refresh_phrase_quality()

        def duplicate_selected_voice_command() -> None:
            index = selected_voice_command_index()
            profile = current_voice_lab_profile()
            if index is None or not 0 <= index < len(profile.commands):
                voice_lab_status_var.set("Select a command to duplicate.")
                return
            duplicate = duplicate_voice_command(
                profile.commands[index],
                (command.name for command in profile.commands),
            )
            profile.commands.insert(index + 1, duplicate)
            save_voice_lab_profile("Duplicated command", select_index=index + 1)
            set_voice_command_fields(duplicate)
            refresh_phrase_quality()

        def delete_voice_command() -> None:
            index = selected_voice_command_index()
            profile = current_voice_lab_profile()
            if index is None or not 0 <= index < len(profile.commands):
                voice_lab_status_var.set("Select a command to delete.")
                return
            deleted = profile.commands[index].name
            del profile.commands[index]
            next_index = min(index, len(profile.commands) - 1) if profile.commands else None
            save_voice_lab_profile(f"Deleted {deleted}", select_index=next_index)
            if next_index is not None:
                set_voice_command_fields(profile.commands[next_index])
            else:
                set_voice_command_fields()
            refresh_phrase_quality()

        voice_command_tree.bind("<<TreeviewSelect>>", fill_selected_voice_command)

        voice_command_buttons = ttk.Frame(voice_lab_frame)
        voice_command_buttons.pack(fill="x", pady=(8, 12))
        ttk.Button(voice_command_buttons, text="New", command=new_voice_command).pack(side="left")
        ttk.Button(voice_command_buttons, text="Save Command", command=save_voice_command).pack(side="left", padx=(6, 0))
        ttk.Button(voice_command_buttons, text="Duplicate", command=duplicate_selected_voice_command).pack(side="left", padx=(6, 0))
        ttk.Button(voice_command_buttons, text="Delete", command=delete_voice_command).pack(side="left", padx=(6, 0))
        ttk.Button(
            voice_command_buttons,
            text="Reload",
            command=lambda: (load_voice_lab_profile(), refresh_phrase_quality()),
        ).pack(side="left", padx=(6, 0))
        voice_command_filter_entry.bind("<KeyRelease>", apply_voice_command_filter)

        phrase_quality_frame = ttk.LabelFrame(voice_lab_frame, text="Phrase Quality", padding=8)
        phrase_quality_frame.pack(fill="both", expand=False, pady=(0, 12))
        phrase_quality_frame.columnconfigure(0, weight=1)
        phrase_quality_result = tk.Text(phrase_quality_frame, height=8, wrap="word", state="disabled")
        phrase_quality_result.grid(row=0, column=0, sticky="ew")
        phrase_quality_scroll = ttk.Scrollbar(phrase_quality_frame, orient="vertical", command=phrase_quality_result.yview)
        phrase_quality_scroll.grid(row=0, column=1, sticky="ns")
        phrase_quality_result.configure(yscrollcommand=phrase_quality_scroll.set)

        def set_phrase_quality_result(text: str) -> None:
            phrase_quality_result.configure(state="normal")
            phrase_quality_result.delete("1.0", tk.END)
            phrase_quality_result.insert(tk.END, text)
            phrase_quality_result.configure(state="disabled")

        def refresh_phrase_quality() -> None:
            report = voice_phrase_quality_report(list(current_voice_lab_profile().commands))
            set_phrase_quality_result(report)
            voice_lab_status_var.set("Phrase quality refreshed. Review before enabling command sending.")

        ttk.Button(phrase_quality_frame, text="Refresh Phrase Quality", command=refresh_phrase_quality).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        voice_test_frame = ttk.LabelFrame(voice_lab_frame, text="Dry-run phrase test", padding=8)
        voice_test_frame.pack(fill="both", expand=False)
        voice_test_frame.columnconfigure(1, weight=1)
        voice_test_phrase_var = tk.StringVar()
        ttk.Label(voice_test_frame, text="Phrase").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(voice_test_frame, textvariable=voice_test_phrase_var).grid(row=0, column=1, sticky="ew", pady=3)
        voice_test_result = tk.Text(voice_test_frame, height=5, wrap="word", state="disabled")
        voice_test_result.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        heard_phrase_frame = ttk.LabelFrame(voice_lab_frame, text="Recent heard phrases", padding=8)
        heard_phrase_frame.pack(fill="both", expand=False, pady=(8, 0))
        heard_phrase_frame.columnconfigure(0, weight=1)
        heard_phrase_list = tk.Listbox(heard_phrase_frame, height=4, exportselection=False)
        heard_phrase_list.grid(row=0, column=0, sticky="ew")
        heard_phrase_scroll = ttk.Scrollbar(heard_phrase_frame, orient="vertical", command=heard_phrase_list.yview)
        heard_phrase_scroll.grid(row=0, column=1, sticky="ns")
        heard_phrase_list.configure(yscrollcommand=heard_phrase_scroll.set)

        def set_voice_test_result(text: str) -> None:
            voice_test_result.configure(state="normal")
            voice_test_result.delete("1.0", tk.END)
            voice_test_result.insert(tk.END, text)
            voice_test_result.configure(state="disabled")

        recognition_lab_frame = ttk.LabelFrame(voice_lab_frame, text="Recognition Lab", padding=8)
        recognition_lab_frame.pack(fill="both", expand=False, pady=(8, 0))
        recognition_lab_frame.columnconfigure(0, weight=1)
        recognition_lab_status_var = tk.StringVar(value="Record one local phrase to inspect volume, transcript, and matching.")
        ttk.Label(recognition_lab_frame, textvariable=recognition_lab_status_var, wraplength=520).grid(
            row=0,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(0, 6),
        )
        recognition_lab_result = tk.Text(recognition_lab_frame, height=9, wrap="word", state="disabled")
        recognition_lab_result.grid(row=1, column=0, columnspan=3, sticky="ew")
        recognition_lab_queue: queue.Queue[tuple[str, str]] = queue.Queue()

        def set_recognition_lab_result(text: str) -> None:
            recognition_lab_result.configure(state="normal")
            recognition_lab_result.delete("1.0", tk.END)
            recognition_lab_result.insert(tk.END, text)
            recognition_lab_result.configure(state="disabled")

        def stop_recognition_diagnostic() -> None:
            stop_capture = voice_lab_state.get("recognition_stop")
            if isinstance(stop_capture, threading.Event):
                stop_capture.set()
                recognition_lab_status_var.set("Stopping recognition diagnostic...")

        def use_recognition_transcript() -> None:
            transcript = str(voice_lab_state.get("last_recognition_transcript", "")).strip()
            if not transcript:
                recognition_lab_status_var.set("No diagnostic transcript to copy yet.")
                return
            voice_test_phrase_var.set(transcript)
            recognition_lab_status_var.set("Diagnostic transcript copied into the dry-run tester.")

        def poll_recognition_lab_queue() -> None:
            try:
                if not editor.winfo_exists():
                    return
            except tk.TclError:
                return
            while True:
                try:
                    kind, message = recognition_lab_queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "status":
                    recognition_lab_status_var.set(message)
                elif kind == "result":
                    set_recognition_lab_result(message)
                elif kind == "transcript":
                    voice_lab_state["last_recognition_transcript"] = message
                elif kind == "done":
                    voice_lab_state["recognition_running"] = False
                    voice_lab_state.pop("recognition_stop", None)
                    recognition_lab_status_var.set(message or "Recognition diagnostic complete.")
            root.after(100, poll_recognition_lab_queue)

        def start_recognition_diagnostic() -> None:
            if bool(voice_lab_state.get("recognition_running")):
                recognition_lab_status_var.set("Recognition diagnostic is already recording.")
                return
            settings = engine.current_settings()
            if settings.enable_voice_listener:
                recognition_lab_status_var.set("Turn off Listen for voice commands before running Recognition Lab.")
                return
            try:
                profile, _profile_path = load_voice_profile()
                commands = list(profile.commands)
                if not commands:
                    raise CorpIntelError("Voice profile has no commands.")
                input_label = voice_input_device_display(settings.voice_input_device)
                input_device_index = voice_input_device_index(settings.voice_input_device)
                selected_model_path = voice_model_path(settings.voice_model_path)
                call_sign = clean_voice_call_sign(settings.voice_call_sign)
            except Exception as exc:
                recognition_lab_status_var.set(f"Recognition Lab setup failed: {exc}")
                return
            stop_capture = threading.Event()
            voice_lab_state["recognition_stop"] = stop_capture
            voice_lab_state["recognition_running"] = True
            voice_lab_state["last_recognition_transcript"] = ""
            set_recognition_lab_result("Recording local diagnostic. Speak one command phrase now.")
            recognition_lab_status_var.set(f"Loading {voice_model_display(settings.voice_model_path)} and opening microphone...")

            def run_diagnostic() -> None:
                try:
                    transcriber = LocalVoskTranscriber(
                        commands,
                        lambda text: recognition_lab_queue.put(("status", text)),
                        input_device_index=input_device_index,
                        model_path=selected_model_path,
                        response_call_signs=response_call_signs(call_sign),
                    )
                    diagnostic = transcriber.record_diagnostic(
                        stop_capture,
                        on_ready=lambda: recognition_lab_queue.put(("status", "Recording now. Speak one phrase.")),
                    )
                    report = recognition_diagnostic_report(
                        diagnostic,
                        commands,
                        input_device_label=input_label,
                        response_call_sign=call_sign,
                    )
                    recognition_lab_queue.put(("transcript", diagnostic.transcript))
                    recognition_lab_queue.put(("result", report))
                    recognition_lab_queue.put(("done", "Recognition diagnostic complete."))
                except Exception as exc:
                    recognition_lab_queue.put(("result", f"Recognition Lab failed: {exc}"))
                    recognition_lab_queue.put(("done", "Recognition diagnostic failed."))

            threading.Thread(target=run_diagnostic, name="intel-pet-recognition-lab", daemon=True).start()

        recognition_lab_buttons = ttk.Frame(recognition_lab_frame)
        recognition_lab_buttons.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(recognition_lab_buttons, text="Record Local Diagnostic", command=start_recognition_diagnostic).pack(side="left")
        ttk.Button(recognition_lab_buttons, text="Stop", command=stop_recognition_diagnostic).pack(side="left", padx=(6, 0))
        ttk.Button(recognition_lab_buttons, text="Use Transcript In Test", command=use_recognition_transcript).pack(
            side="left",
            padx=(6, 0),
        )
        poll_recognition_lab_queue()

        def selected_heard_phrase() -> str:
            selection = heard_phrase_list.curselection()
            if not selection:
                return ""
            return str(heard_phrase_list.get(selection[0])).strip()

        def refresh_heard_phrases() -> None:
            previous = selected_heard_phrase()
            phrases = recent_voice_training_phrases(
                history_items,
                response_call_sign=clean_voice_call_sign(engine.current_settings().voice_call_sign),
            )
            heard_phrase_list.delete(0, tk.END)
            for phrase in phrases:
                heard_phrase_list.insert(tk.END, phrase)
            if previous:
                for index, phrase in enumerate(phrases):
                    if phrase == previous:
                        heard_phrase_list.selection_set(index)
                        break

        def use_heard_phrase() -> None:
            phrase = selected_heard_phrase()
            if not phrase:
                voice_lab_status_var.set("Select a heard phrase first.")
                return
            voice_test_phrase_var.set(phrase)
            voice_lab_status_var.set("Heard phrase copied into the dry-run tester.")

        def add_phrase_to_selected_command(phrase: str) -> None:
            phrase = clean_voice_training_phrase(
                phrase,
                response_call_sign=clean_voice_call_sign(engine.current_settings().voice_call_sign),
            )
            if not phrase:
                voice_lab_status_var.set("Choose or enter a phrase first.")
                return
            index = selected_voice_command_index()
            profile = current_voice_lab_profile()
            if index is None or not 0 <= index < len(profile.commands):
                voice_lab_status_var.set("Select the command that should learn this phrase.")
                return
            before_count = len(profile.commands[index].phrases)
            profile.commands[index] = voice_command_with_added_phrase(
                profile.commands[index],
                phrase,
                response_call_sign=clean_voice_call_sign(engine.current_settings().voice_call_sign),
            )
            after_count = len(profile.commands[index].phrases)
            set_voice_command_fields(profile.commands[index])
            if after_count == before_count:
                voice_lab_status_var.set(f"{phrase!r} is already on {profile.commands[index].name}.")
                return
            save_voice_lab_profile(f"Added phrase {phrase!r}", select_index=index)

        def test_voice_lab_phrase() -> None:
            phrase = voice_test_phrase_var.get().strip()
            if not phrase:
                index = selected_voice_command_index()
                profile = current_voice_lab_profile()
                if index is not None and 0 <= index < len(profile.commands) and profile.commands[index].phrases:
                    phrase = profile.commands[index].phrases[0]
                    voice_test_phrase_var.set(phrase)
            if not phrase:
                voice_lab_status_var.set("Enter a phrase to test.")
                return
            status = voice_status_from_transcript(
                phrase,
                list(current_voice_lab_profile().commands),
                response_call_sign=clean_voice_call_sign(engine.current_settings().voice_call_sign),
                allow_command_sending=False,
            )
            if status is None:
                set_voice_test_result("No speech recognized.")
                voice_lab_status_var.set("No speech recognized.")
                return
            set_voice_test_result(status.detail)
            voice_lab_status_var.set(f"Dry run: {status.title}. No keys sent.")

        def test_selected_voice_command() -> None:
            index = selected_voice_command_index()
            profile = current_voice_lab_profile()
            if index is None or not 0 <= index < len(profile.commands):
                voice_lab_status_var.set("Select a command to test.")
                return
            command = profile.commands[index]
            if not command.phrases:
                voice_lab_status_var.set("Selected command has no phrases.")
                return
            voice_test_phrase_var.set(command.phrases[0])
            test_voice_lab_phrase()

        ttk.Button(voice_test_frame, text="Test Phrase", command=test_voice_lab_phrase).grid(
            row=0,
            column=2,
            sticky="e",
            padx=(6, 0),
        )
        ttk.Button(voice_test_frame, text="Test Selected", command=test_selected_voice_command).grid(
            row=2,
            column=2,
            sticky="e",
            pady=(6, 0),
        )
        heard_phrase_buttons = ttk.Frame(heard_phrase_frame)
        heard_phrase_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(heard_phrase_buttons, text="Refresh Heard", command=refresh_heard_phrases).pack(side="left")
        ttk.Button(heard_phrase_buttons, text="Use In Test", command=use_heard_phrase).pack(side="left", padx=(6, 0))
        ttk.Button(
            heard_phrase_buttons,
            text="Add Heard To Selected",
            command=lambda: add_phrase_to_selected_command(selected_heard_phrase()),
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            heard_phrase_buttons,
            text="Add Test To Selected",
            command=lambda: add_phrase_to_selected_command(voice_test_phrase_var.get()),
        ).pack(side="left", padx=(6, 0))
        ttk.Label(voice_lab_frame, textvariable=voice_lab_status_var, wraplength=520).pack(anchor="w", pady=(8, 0))
        history_refreshers.append(refresh_heard_phrases)
        load_voice_lab_profile()
        refresh_heard_phrases()
        refresh_phrase_quality()

        ttk.Label(diagnostics_frame, text="Diagnostics", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            diagnostics_frame,
            text="Local runtime summary for troubleshooting. This does not include raw chat lines or alert message text.",
            wraplength=520,
        ).pack(anchor="w", pady=(2, 8))
        diagnostics_text = tk.Text(
            diagnostics_frame,
            height=26,
            wrap="word",
            state="disabled",
            bg=ui_colors["panel"],
            fg=ui_colors["text"],
            insertbackground=ui_colors["text"],
            relief="flat",
            padx=10,
            pady=8,
        )
        diagnostics_text.pack(fill="both", expand=True)

        def voice_profile_path_for_diagnostics() -> Path | str:
            try:
                _profile, editable_path, _source_path = load_editable_voice_profile()
            except Exception as exc:
                return f"unavailable: {exc}"
            return editable_path

        def current_diagnostics_report() -> str:
            return intel_pet_diagnostics_report(
                settings=engine.current_settings(),
                settings_path=settings_path,
                chat_log_dir=args.log_dir.expanduser(),
                game_log_dir=args.game_log_dir.expanduser(),
                channel_filter=channel_filter,
                listener_filter=listener_filter,
                poll_seconds=max(0.1, safe_float(args.poll_seconds, DEFAULT_POLL_SECONDS)),
                read_existing=args.read_existing,
                combat_cheer_enabled=not args.no_combat_cheer,
                mission_cheer_enabled=not args.no_mission_cheer,
                location_enabled=location_config is not None,
                location_poll_seconds=location_poll_seconds,
                happy_systems=happy_systems,
                history_items=history_items,
                voice_profile_path=voice_profile_path_for_diagnostics(),
                location_session=location_session,
                current_system=current_local_system(),
            )

        def refresh_diagnostics_text() -> None:
            diagnostics_text.configure(state="normal")
            diagnostics_text.delete("1.0", tk.END)
            diagnostics_text.insert(tk.END, current_diagnostics_report())
            diagnostics_text.configure(state="disabled")

        def copy_diagnostics_text() -> None:
            editor.clipboard_clear()
            editor.clipboard_append(current_diagnostics_report())
            editor_status_var.set("Diagnostics copied to clipboard.")

        diagnostics_buttons = ttk.Frame(diagnostics_frame)
        diagnostics_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(diagnostics_buttons, text="Refresh Diagnostics", command=refresh_diagnostics_text).pack(side="left")
        ttk.Button(diagnostics_buttons, text="Copy Diagnostics", command=copy_diagnostics_text).pack(
            side="left",
            padx=(6, 0),
        )
        refresh_diagnostics_text()
        history_refreshers.append(refresh_diagnostics_text)

        ttk.Label(history_frame, text="Alert history", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            history_frame,
            text="This keeps the recent alerts in memory only while the pet is running.",
            wraplength=520,
        ).pack(anchor="w", pady=(2, 8))
        history_body = ttk.Frame(history_frame)
        history_body.pack(fill="both", expand=True)
        history_text = tk.Text(
            history_body,
            height=20,
            wrap="word",
            state="disabled",
            bg=ui_colors["panel"],
            fg=ui_colors["text"],
            insertbackground=ui_colors["text"],
            relief="flat",
            padx=10,
            pady=8,
        )
        history_text.pack(side="left", fill="both", expand=True)
        history_scrollbar = ttk.Scrollbar(history_body, orient="vertical", command=history_text.yview)
        history_scrollbar.pack(side="right", fill="y")
        history_text.configure(yscrollcommand=history_scrollbar.set)

        def refresh_history_text() -> None:
            history_text.configure(state="normal")
            history_text.delete("1.0", tk.END)
            if not history_items:
                history_text.insert(tk.END, "No alert history yet.")
            else:
                for item in reversed(history_items):
                    history_text.insert(tk.END, f"[{item.severity.upper()}] {item.title}\n")
                    history_text.insert(tk.END, f"{item.detail}\n")
                    history_text.insert(tk.END, f"{item.meta} | {item.recorded_at}\n\n")
            history_text.configure(state="disabled")

        def clear_history() -> None:
            history_items.clear()
            refresh_history_text()
            refresh_heard_phrases()
            refresh_voice_reliability()
            refresh_option_summary()

        history_buttons = ttk.Frame(history_frame)
        history_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(history_buttons, text="Refresh", command=refresh_history_text).pack(side="left")
        ttk.Button(history_buttons, text="Clear History", command=clear_history).pack(side="left", padx=(6, 0))
        refresh_history_text()
        history_refreshers.append(refresh_history_text)

        def refresh_editor_settings(settings: IntelPetSettings) -> None:
            refresh_list("pilot_names", settings.pilot_names)
            refresh_list("help_phrases", settings.help_phrases)
            refresh_list("extra_keywords", settings.extra_keywords)
            for term_var in term_vars.values():
                term_var.set("")

            refresh_behavior_vars(settings)

            speak_alerts_var.set(settings.speak_alerts)
            for kind, var in spoken_alert_kind_vars.items():
                var.set(clean_spoken_alert_kinds(settings.spoken_alert_kinds)[kind])
            response_engine_var.set(clean_response_engine(settings.response_engine))
            response_voice_var.set(clean_response_voice(settings.response_voice))
            response_style_var.set(clean_response_style(settings.response_style))
            response_preset_var.set(pet_voice_preset_for_style(settings.response_style))
            voice_preview_text_var.set(clean_voice_preview_text(settings.voice_preview_text))
            voice_listener_var.set(settings.enable_voice_listener)
            speech_engine_var.set(clean_voice_engine(settings.voice_engine))
            voice_whisper_model_var.set(clean_voice_whisper_model(settings.voice_whisper_model))
            voice_model_var.set(voice_model_display(settings.voice_model_path))
            voice_model_status_var.set(voice_model_status(settings.voice_model_path))
            voice_input_device_var.set(voice_input_device_display(settings.voice_input_device))
            voice_call_sign_var.set(clean_voice_call_sign(settings.voice_call_sign))
            allow_command_sending_var.set(settings.allow_voice_command_sending)
            require_target_window_var.set(settings.require_voice_target_window)
            voice_target_title_var.set(clean_voice_target_title(settings.voice_target_title))
            mission_read_opener_var.set(clean_mission_read_opener(settings.mission_read_opener))
            mission_read_giver_var.set(settings.mission_read_include_giver)
            mission_read_level_var.set(settings.mission_read_include_level)
            mission_read_rewards_var.set(settings.mission_read_include_rewards)
            mission_read_reward_notes_var.set(settings.mission_read_include_reward_notes)
            mission_read_source_var.set(settings.mission_read_include_source)
            mission_read_completion_var.set(settings.mission_read_include_completion)
            mission_read_briefing_var.set(settings.mission_read_include_briefing)
            refresh_voice_listener_summary(settings)
            refresh_heard_phrases()
            refresh_voice_reliability()
            refresh_note_phrase_preview(settings_override=settings)
            refresh_mission_tree()
            refresh_option_summary()

        def export_pet_settings() -> None:
            default_name = f"intel_pet_settings_{time.strftime('%Y%m%d_%H%M%S')}.json"
            export_path = filedialog.asksaveasfilename(
                parent=editor,
                title="Export Intel Pet Settings",
                initialdir=str(settings_path.parent),
                initialfile=default_name,
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not export_path:
                return
            try:
                export_settings(Path(export_path), engine.current_settings())
            except Exception as exc:
                editor_status_var.set(f"Export failed: {exc}")
                return
            editor_status_var.set(f"Exported settings to {Path(export_path).name}.")

        def import_pet_settings() -> None:
            import_path = filedialog.askopenfilename(
                parent=editor,
                title="Import Intel Pet Settings",
                initialdir=str(settings_path.parent),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not import_path:
                return
            try:
                settings = import_settings(Path(import_path))
                save_settings(settings_path, settings)
                engine.update_settings(settings)
                configure_pet_speech(settings)
                refresh_editor_settings(settings)
            except Exception as exc:
                editor_status_var.set(f"Import failed: {exc}")
                return
            editor_status_var.set(f"Imported settings from {Path(import_path).name}. Saved to local profile.")

        history_refreshers.append(refresh_option_summary)

        def forget_history_refresher(event: Any) -> None:
            if event.widget is not editor:
                return
            for refresher in (
                refresh_history_text,
                refresh_heard_phrases,
                refresh_voice_reliability,
                refresh_diagnostics_text,
                refresh_option_summary,
            ):
                if refresher in history_refreshers:
                    history_refreshers.remove(refresher)

        editor.bind("<Destroy>", forget_history_refresher, add="+")

        footer = ttk.Frame(editor_frame, style="IntelPet.Root.TFrame")
        footer.pack(fill="x", pady=(10, 0))
        ttk.Label(footer, textvariable=editor_status_var, style="IntelPet.Subtitle.TLabel", wraplength=420).pack(side="left", anchor="w")
        ttk.Button(footer, text="Quit Pet", command=on_close).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="Close", command=editor.destroy).pack(side="right")
        ttk.Button(footer, text="Import Settings", command=import_pet_settings).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="Export Settings", command=export_pet_settings).pack(side="right")
        if first_entry is not None:
            first_entry.focus_set()

    idle_after_id: str | None = None

    def set_sprite_frame(index: int, *, offset_x: int = 0, offset_y: int = 0) -> None:
        if not sprite_frames or sprite_image_id is None:
            return
        clean_index = max(0, min(index, len(sprite_frames) - 1))
        sprite_canvas.itemconfigure(sprite_image_id, image=sprite_frames[clean_index])
        sprite_canvas.coords(sprite_image_id, 80 + offset_x, 64 + offset_y)

    def set_robot_miner_frame(index: int, *, offset_x: int = 0, offset_y: int = 0) -> None:
        if not robot_miner_frames or sprite_image_id is None:
            return
        clean_index = max(0, min(index, len(robot_miner_frames) - 1))
        sprite_canvas.itemconfigure(sprite_image_id, image=robot_miner_frames[clean_index])
        sprite_canvas.coords(sprite_image_id, 80 + offset_x, 64 + offset_y)

    def clear_combat_shots() -> None:
        for item_id in shot_item_ids:
            sprite_canvas.delete(item_id)
        shot_item_ids.clear()

    def cancel_sprite_cycle() -> None:
        nonlocal sprite_after_id
        if sprite_after_id is not None:
            root.after_cancel(sprite_after_id)
            sprite_after_id = None
        clear_combat_shots()

    def cancel_idle_sprite_cycle() -> None:
        nonlocal idle_cycle_after_id
        if idle_cycle_after_id is not None:
            root.after_cancel(idle_cycle_after_id)
            idle_cycle_after_id = None

    def schedule_idle_sprite_cycle() -> None:
        nonlocal idle_cycle_after_id
        if not sprite_frames:
            return
        cancel_idle_sprite_cycle()
        idle_cycle_after_id = root.after(IDLE_ANIMATION_MS, run_idle_sprite_cycle)

    def start_sprite_cycle(sequence: tuple[int, ...], *, reschedule_idle: bool = True) -> None:
        nonlocal sprite_after_id
        if not sprite_frames:
            return
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()

        def advance(position: int = 0) -> None:
            nonlocal sprite_after_id
            set_sprite_frame(sequence[position])
            next_position = position + 1
            if next_position < len(sequence):
                sprite_after_id = root.after(SHIP_FRAME_MS, lambda: advance(next_position))
            else:
                sprite_after_id = None
                set_sprite_frame(0)
                if reschedule_idle:
                    schedule_idle_sprite_cycle()

        advance()

    def start_sprite_motion_cycle(sequence: tuple[tuple[int, int, int], ...], *, reschedule_idle: bool = True) -> None:
        nonlocal sprite_after_id
        if not sprite_frames:
            return
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()

        def advance(position: int = 0) -> None:
            nonlocal sprite_after_id
            frame_index, offset_x, offset_y = sequence[position]
            set_sprite_frame(frame_index, offset_x=offset_x, offset_y=offset_y)
            next_position = position + 1
            if next_position < len(sequence):
                sprite_after_id = root.after(SHIP_FRAME_MS, lambda: advance(next_position))
            else:
                sprite_after_id = None
                set_sprite_frame(0)
                if reschedule_idle:
                    schedule_idle_sprite_cycle()

        advance()

    def start_combat_sprite_cycle(
        sequence: tuple[tuple[int, int, int, int, int], ...],
        *,
        reschedule_idle: bool = True,
        frame_ms: int = 90,
    ) -> None:
        nonlocal sprite_after_id
        if not sprite_frames:
            return
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()

        def draw_shots(offset_x: int, offset_y: int, target_x: int, target_y: int) -> None:
            clear_combat_shots()
            origin_x = 108 + offset_x
            origin_y = 58 + offset_y
            shot_item_ids.append(
                sprite_canvas.create_line(origin_x, origin_y, target_x, target_y, fill="#ffd166", width=3)
            )
            shot_item_ids.append(
                sprite_canvas.create_line(origin_x - 8, origin_y + 8, target_x - 18, target_y + 6, fill="#ff5f56", width=2)
            )

        def advance(position: int = 0) -> None:
            nonlocal sprite_after_id
            frame_index, offset_x, offset_y, target_x, target_y = sequence[position]
            set_sprite_frame(frame_index, offset_x=offset_x, offset_y=offset_y)
            draw_shots(offset_x, offset_y, target_x, target_y)
            next_position = position + 1
            if next_position < len(sequence):
                sprite_after_id = root.after(frame_ms, lambda: advance(next_position))
            else:
                sprite_after_id = None
                clear_combat_shots()
                set_sprite_frame(0)
                if reschedule_idle:
                    schedule_idle_sprite_cycle()

        advance()

    def start_robot_miner_cycle(*, reschedule_idle: bool = True, frame_ms: int = 120) -> None:
        nonlocal sprite_after_id
        if not robot_miner_frames or sprite_image_id is None:
            start_combat_sprite_cycle(LONG_COMBO_SPRITE_STEPS, reschedule_idle=reschedule_idle, frame_ms=140)
            return
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()

        def draw_robot_effect(effect: str, offset_x: int, offset_y: int, position: int) -> None:
            clear_combat_shots()
            if effect == "laser":
                left_eye_x = 68 + offset_x
                right_eye_x = 88 + offset_x
                eye_y = 42 + offset_y
                target_x = 160
                target_y = 28 + (position % 4) * 14
                shot_item_ids.append(
                    sprite_canvas.create_line(left_eye_x, eye_y, target_x, target_y, fill="#50ebff", width=3)
                )
                shot_item_ids.append(
                    sprite_canvas.create_line(right_eye_x, eye_y, target_x, target_y + 16, fill="#ff5f78", width=2)
                )
            elif effect == "spark":
                spark_x = 132 + offset_x + (position % 3) * 4
                spark_y = 94 + offset_y - (position % 2) * 8
                shot_item_ids.append(
                    sprite_canvas.create_line(spark_x - 5, spark_y, spark_x + 5, spark_y, fill="#ffd552", width=2)
                )
                shot_item_ids.append(
                    sprite_canvas.create_line(spark_x, spark_y - 5, spark_x, spark_y + 5, fill="#ff8f2a", width=2)
                )

        def advance(position: int = 0) -> None:
            nonlocal sprite_after_id
            frame_index, offset_x, offset_y, effect = ROBOT_MINER_STEPS[position]
            set_robot_miner_frame(frame_index, offset_x=offset_x, offset_y=offset_y)
            draw_robot_effect(effect, offset_x, offset_y, position)
            next_position = position + 1
            if next_position < len(ROBOT_MINER_STEPS):
                sprite_after_id = root.after(frame_ms, lambda: advance(next_position))
            else:
                sprite_after_id = None
                clear_combat_shots()
                set_sprite_frame(0)
                if reschedule_idle:
                    schedule_idle_sprite_cycle()

        advance()

    def run_idle_sprite_cycle() -> None:
        nonlocal idle_cycle_after_id
        idle_cycle_after_id = None
        start_sprite_cycle(IDLE_SPRITE_SEQUENCE)

    def start_behavior_cycle(behavior: str) -> None:
        if behavior == BEHAVIOR_HAPPY:
            start_sprite_motion_cycle(HAPPY_SPRITE_STEPS)
        elif behavior == BEHAVIOR_COMBAT:
            start_combat_sprite_cycle(KILL_SPRITE_STEPS)
        elif behavior == BEHAVIOR_LONG_MOVE:
            start_sprite_motion_cycle(LONG_MOVE_SPRITE_STEPS)
        elif behavior == BEHAVIOR_LONG_COMBAT:
            start_combat_sprite_cycle(LONG_COMBAT_SPRITE_STEPS, frame_ms=140)
        elif behavior == BEHAVIOR_LONG_COMBO:
            start_combat_sprite_cycle(LONG_COMBO_SPRITE_STEPS, frame_ms=140)
        elif behavior == BEHAVIOR_ROBOT_MINER:
            start_robot_miner_cycle()
        elif behavior == BEHAVIOR_IDLE:
            start_sprite_cycle(IDLE_SPRITE_SEQUENCE)
        elif behavior == BEHAVIOR_NONE:
            cancel_sprite_cycle()
            cancel_idle_sprite_cycle()
            set_sprite_frame(0)
            schedule_idle_sprite_cycle()
        else:
            start_sprite_cycle(ALERT_SPRITE_SEQUENCE)

    def render_aura_bubble_phase(phase: int) -> None:
        scan_x, active_nodes = aura_bubble_phase_state(phase, node_count=len(aura_node_item_ids))
        bubble_canvas.coords(aura_scan_item_id, scan_x, 14, scan_x + 9, 104)
        bubble_canvas.itemconfigure(aura_scan_item_id, fill=current_aura_color)
        for index, item_id in enumerate(aura_node_item_ids):
            bubble_canvas.itemconfigure(item_id, fill=current_aura_color if index in active_nodes else "#1a4652")
        pulse_width = 2 if phase % 4 == 0 else 1
        for item_id in aura_accent_item_ids:
            bubble_canvas.itemconfigure(item_id, fill=current_aura_color, width=pulse_width)

    def run_aura_bubble_animation(phase: int) -> None:
        nonlocal aura_after_id
        render_aura_bubble_phase(phase)
        aura_after_id = root.after(320, lambda: run_aura_bubble_animation(phase + 1))

    def start_aura_bubble_animation() -> None:
        stop_aura_bubble_animation()
        run_aura_bubble_animation(0)

    def stop_aura_bubble_animation() -> None:
        nonlocal aura_after_id
        if aura_after_id is not None:
            root.after_cancel(aura_after_id)
            aura_after_id = None

    def apply_severity(severity: str) -> None:
        nonlocal current_aura_color
        color = colors.get(severity, colors["info"])
        current_aura_color = color
        for item_id in bubble_border_items:
            bubble_canvas.itemconfigure(item_id, outline=color)
        bubble_canvas.itemconfigure(bubble_tail_id, outline=color)
        control_canvas.itemconfigure(options_rect_id, outline=color)
        render_aura_bubble_phase(0)

    def resize_overlay(width: int) -> None:
        desktop_left = root.winfo_vrootx()
        desktop_width = root.winfo_vrootwidth()
        max_x = desktop_left + max(0, desktop_width - width - 8)
        clean_x = min(max(root.winfo_x(), desktop_left), max_x)
        root.geometry(f"{width}x{OVERLAY_HEIGHT}+{clean_x}+{root.winfo_y()}")

    def show_message_bubble(message: str, *, severity: str) -> None:
        resize_overlay(OVERLAY_ALERT_WIDTH)
        bubble_canvas.place(x=128, y=6)
        raise_tk_widget(control_canvas)
        apply_severity(severity)
        message_var.set(message)
        for item_id in bubble_item_ids:
            bubble_canvas.itemconfigure(item_id, state="normal")
        start_aura_bubble_animation()

    def hide_message_bubble() -> None:
        stop_aura_bubble_animation()
        message_var.set("")
        for item_id in bubble_item_ids:
            bubble_canvas.itemconfigure(item_id, state="hidden")
        bubble_canvas.place_forget()
        resize_overlay(OVERLAY_IDLE_WIDTH)
        apply_severity("idle")

    def speak_pet_message(message: str, *, label: str, kind: str) -> None:
        if not should_speak_alert_kind(kind, engine.current_settings()):
            return
        clean_text = spoken_pet_text(message)
        if clean_text:
            pet_speech.play_text(clean_text, label=label)

    def remember_history(item: IntelPetHistoryItem) -> None:
        history_items.append(item)
        del history_items[:-DEFAULT_HISTORY_LIMIT]
        for refresh_history_text in tuple(history_refreshers):
            try:
                refresh_history_text()
            except tk.TclError:
                if refresh_history_text in history_refreshers:
                    history_refreshers.remove(refresh_history_text)

    def set_idle() -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
            idle_after_id = None
        cancel_sprite_cycle()
        set_sprite_frame(0)
        schedule_idle_sprite_cycle()
        hide_message_bubble()

    def show_alert(alert: IntelPetAlert) -> None:
        show_alert_batch((alert,))

    def show_alert_batch(alerts: Iterable[IntelPetAlert]) -> None:
        nonlocal idle_after_id
        clean_alerts = tuple(alerts)
        if not clean_alerts:
            return
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        display_alert = highest_severity_alert(clean_alerts) or clean_alerts[-1]
        message = display_message_from_alerts(clean_alerts)
        show_message_bubble(message, severity=display_alert.severity)
        speak_pet_message(message, label="pet chat alert", kind=alert_behavior_key(display_alert))
        for alert in clean_alerts:
            remember_history(history_item_from_alert(alert))
        start_behavior_cycle(behavior_for_alert(display_alert, settings))
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def show_location_cheer(cheer: IntelPetLocationCheer) -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        message = display_message_from_cheer(cheer)
        show_message_bubble(message, severity="info")
        speak_pet_message(message, label="pet location cheer", kind="location")
        remember_history(history_item_from_cheer(cheer))
        start_behavior_cycle(behavior_for_kind("location", settings))
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def show_combat_cheer(cheer: IntelPetCombatCheer) -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        message = display_message_from_combat_cheer(cheer)
        show_message_bubble(message, severity="high")
        speak_pet_message(message, label="pet combat cheer", kind="combat")
        remember_history(history_item_from_combat_cheer(cheer))
        start_behavior_cycle(behavior_for_kind("combat", settings))
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def show_mission_cheer(cheer: IntelPetMissionCheer) -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        message = display_message_from_mission_cheer(cheer)
        show_message_bubble(message, severity="info")
        speak_pet_message(message, label="pet mission cheer", kind="mission")
        remember_history(history_item_from_mission_cheer(cheer))
        start_behavior_cycle(behavior_for_kind("mission", settings))
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def show_voice_status(status: IntelPetVoiceStatus) -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        show_message_bubble(display_message_from_voice_status(status), severity=status.severity)
        remember_history(history_item_from_voice_status(status))
        start_behavior_cycle(BEHAVIOR_ALERT if status.severity != "high" else BEHAVIOR_IDLE)
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def poll_queue() -> None:
        chat_alerts: list[IntelPetAlert] = []
        location_cheers: list[IntelPetLocationCheer] = []
        combat_cheers: list[IntelPetCombatCheer] = []
        mission_cheers: list[IntelPetMissionCheer] = []
        voice_statuses: list[IntelPetVoiceStatus] = []
        high_statuses: list[IntelPetHistoryItem] = []
        while True:
            try:
                item = alert_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, IntelPetAlert):
                chat_alerts.append(item)
            elif isinstance(item, IntelPetLocationCheer):
                location_cheers.append(item)
            elif isinstance(item, IntelPetCombatCheer):
                combat_cheers.append(item)
            elif isinstance(item, IntelPetMissionCheer):
                mission_cheers.append(item)
            elif isinstance(item, IntelPetVoiceStatus):
                voice_statuses.append(item)
            elif isinstance(item, IntelPetHistoryItem):
                remember_history(item)
                if item.severity == "high":
                    high_statuses.append(item)
            elif isinstance(item, str):
                status_item = history_item_from_status(item)
                remember_history(status_item)
                if status_item.severity == "high":
                    high_statuses.append(status_item)
        if chat_alerts:
            show_alert_batch(chat_alerts)
        for cheer in location_cheers:
            show_location_cheer(cheer)
        for cheer in combat_cheers:
            show_combat_cheer(cheer)
        for cheer in mission_cheers:
            show_mission_cheer(cheer)
        for status in voice_statuses:
            show_voice_status(status)
        for status_item in high_statuses:
            show_message_bubble(status_item.detail, severity=status_item.severity)
        root.after(250, poll_queue)

    def on_close() -> None:
        stop_event.set()
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()
        pet_speech.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    schedule_idle_sprite_cycle()
    poll_queue()
    root.mainloop()
    stop_event.set()
    pet_speech.stop()


def load_sprite_frames(tk_module: Any, root: Any, paths: Iterable[Path] | None = None) -> tuple[Any, ...]:
    frames: list[Any] = []
    for path in paths or ship_sprite_frame_paths():
        if not path.exists():
            return ()
        frames.append(tk_module.PhotoImage(file=str(path), master=root))
    return tuple(frames)


def channel_filter_from_args(args: argparse.Namespace) -> ChannelFilter:
    channels = parse_csv(args.channels)
    if args.all_channels:
        return ChannelFilter(all_channels=True)
    if not channels:
        raise CorpIntelError("Choose channels with --channels or explicitly pass --all-channels.")
    return ChannelFilter(channels)


def listener_filter_from_args(
    args: argparse.Namespace,
    *,
    location_session: IntelPetLocationSession | None = None,
) -> tuple[str, ...]:
    if args.all_listeners:
        return ()
    names = clean_user_terms(args.listener_name)
    if names:
        return names
    if location_session is not None and location_session.character_name:
        return (location_session.character_name,)
    return ()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local-only EVE chat and combat alert pet overlay.",
    )
    parser.add_argument("--log-dir", type=Path, default=default_chat_log_dir(), help="EVE Chatlogs folder.")
    parser.add_argument("--game-log-dir", type=Path, default=default_game_log_dir(), help="EVE Gamelogs folder.")
    parser.add_argument(
        "--channels",
        default=DEFAULT_CHANNELS,
        help="Comma-separated channel allowlist. Wildcards are allowed, like *Intel*.",
    )
    parser.add_argument("--all-channels", action="store_true", help="Allow all chat log channels. Use carefully.")
    parser.add_argument(
        "--listener-name",
        action="append",
        default=(),
        help="Only watch chat logs whose EVE log Listener header matches this character name.",
    )
    parser.add_argument(
        "--all-listeners",
        action="store_true",
        help="Watch matching channels for every local EVE character log. With location cheer, the default is the SSO character only.",
    )
    parser.add_argument("--settings-path", type=Path, default=DEFAULT_SETTINGS_PATH, help="Local intel pet settings JSON.")
    parser.add_argument(
        "--discord-note-settings-path",
        type=Path,
        default=DEFAULT_DISCORD_NOTE_SETTINGS_PATH,
        help="Local JSON file for Discord voice note settings.",
    )
    parser.add_argument(
        "--discord-note-webhook-url",
        default=os.environ.get("INTEL_PET_DISCORD_NOTE_WEBHOOK_URL", ""),
        help="Discord channel webhook URL for voice notes. Stored only if saved from Options.",
    )
    parser.add_argument(
        "--enable-discord-notes",
        action="store_true",
        default=None,
        help="Enable voice notes to the configured Discord note channel.",
    )
    parser.add_argument(
        "--no-discord-notes",
        action="store_false",
        dest="enable_discord_notes",
        help="Disable Discord voice notes even if saved settings enable them.",
    )
    parser.add_argument(
        "--discord-channel-alerts",
        action="store_true",
        help="Prepare selected chat alerts for a Discord channel webhook. Dry-run is on unless --discord-alert-live is set.",
    )
    parser.add_argument(
        "--discord-alert-webhook-url",
        default=os.environ.get(DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR, ""),
        help=f"Discord channel webhook URL for selected chat alerts. Defaults to ${DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR}.",
    )
    parser.add_argument(
        "--discord-alert-kind",
        action="append",
        default=(),
        help="Alert kind to route to Discord: mention, help, hostile, or keyword. Defaults to help and hostile.",
    )
    parser.add_argument(
        "--discord-alert-live",
        action="store_false",
        dest="discord_alert_dry_run",
        default=True,
        help="Actually send selected Discord channel alerts. Without this flag, alerts are previewed in History only.",
    )
    parser.add_argument(
        "--discord-alert-include-matched-text",
        action="store_true",
        help="Include matched chat text in Discord alert payloads. Off by default.",
    )
    parser.add_argument(
        "--discord-alert-sender-name",
        default="IntelPet",
        help="Discord webhook display name for channel alerts.",
    )
    parser.add_argument(
        "--discord-alert-min-seconds",
        type=float,
        default=DEFAULT_DISCORD_ALERT_MIN_SECONDS,
        help="Minimum seconds between live Discord channel alert sends.",
    )
    parser.add_argument("--pilot-name", action="append", default=(), help="Your character name for mention alerts.")
    parser.add_argument("--keyword", action="append", default=(), help="Extra keyword to alert on.")
    parser.add_argument("--help-phrase", action="append", default=(), help="Extra help phrase to treat as critical.")
    parser.add_argument("--no-message-text", action="store_true", help="Hide the matched message text in the overlay.")
    parser.add_argument("--alert-seconds", type=float, default=None, help="Seconds before the overlay returns to idle.")
    parser.add_argument(
        "--speak-alerts",
        action="store_true",
        default=None,
        help="Speak pet alert, location, combat, and mission messages.",
    )
    parser.add_argument(
        "--no-speak-alerts",
        action="store_false",
        dest="speak_alerts",
        help="Disable spoken pet messages even if saved settings enable them.",
    )
    parser.add_argument(
        "--response-engine",
        default="",
        choices=RESPONSE_ENGINES,
        help="Voice engine for spoken pet messages.",
    )
    parser.add_argument("--response-voice", default="", help="TTS voice name or ElevenLabs voice id for spoken pet messages.")
    parser.add_argument("--response-style", default="", help="TTS style instructions for spoken pet messages.")
    parser.add_argument("--voice-preview-text", default="", help="Sample text used by the pet voice preview cache.")
    parser.add_argument(
        "--enable-voice-listener",
        action="store_true",
        default=None,
        help="Listen for EVE Voice Pilot commands using the saved command-sending safety settings.",
    )
    parser.add_argument(
        "--no-voice-listener",
        action="store_false",
        dest="enable_voice_listener",
        help="Disable the practice voice listener even if saved settings enable it.",
    )
    parser.add_argument("--voice-engine", default="", choices=VOICE_ENGINES, help="Speech engine for the practice listener.")
    parser.add_argument(
        "--voice-whisper-model",
        default="",
        choices=LOCAL_WHISPER_MODELS,
        help="Local Whisper model used when --voice-engine is Whisper local dictation.",
    )
    parser.add_argument("--voice-model-path", default="", help="Local Vosk model path for offline voice recognition.")
    parser.add_argument("--voice-input-device", default="", help="Microphone label for the practice listener.")
    parser.add_argument("--voice-call-sign", default="", help="Response call sign for voice commands, like Merlin or Aura.")
    parser.add_argument(
        "--allow-voice-command-sending",
        action="store_true",
        default=None,
        help="Allow exact voice command matches to send their configured keybind.",
    )
    parser.add_argument(
        "--no-voice-command-sending",
        action="store_false",
        dest="allow_voice_command_sending",
        help="Keep voice commands in practice mode even if saved settings allow sending.",
    )
    parser.add_argument(
        "--require-voice-target-window",
        action="store_true",
        default=None,
        help="Require the active window title to match before sending voice command keybinds.",
    )
    parser.add_argument(
        "--no-voice-target-window",
        action="store_false",
        dest="require_voice_target_window",
        help="Disable the active-window guard for voice command key sending.",
    )
    parser.add_argument(
        "--voice-target-title",
        default="",
        help="Active window title text required before sending voice command keybinds. Defaults to EVE.",
    )
    parser.add_argument("--no-combat-cheer", action="store_true", help="Do not watch local game logs for kill cheers.")
    parser.add_argument("--no-mission-cheer", action="store_true", help="Do not watch local game logs for mission comments.")
    parser.add_argument(
        "--enable-location-cheer",
        action="store_true",
        help="Use read-only ESI location to cheer in target systems.",
    )
    parser.add_argument(
        "--happy-system",
        action="append",
        default=(),
        help="System name that makes the ship fly happily. Defaults to Dihra, Amarr, and Jita.",
    )
    parser.add_argument(
        "--location-poll-seconds",
        type=float,
        default=DEFAULT_LOCATION_POLL_SECONDS,
        help="Seconds between ESI location checks when location cheer is enabled.",
    )
    parser.add_argument(
        "--sso-client-id",
        default=os.environ.get("INTEL_PET_SSO_CLIENT_ID", os.environ.get("EVE_SSO_CLIENT_ID", "")),
        help="EVE SSO client id for optional location cheer.",
    )
    parser.add_argument(
        "--sso-client-secret",
        default=os.environ.get("INTEL_PET_SSO_CLIENT_SECRET", os.environ.get("EVE_SSO_CLIENT_SECRET", "")),
        help="EVE SSO client secret for optional location cheer.",
    )
    parser.add_argument(
        "--sso-callback-url",
        default=os.environ.get("INTEL_PET_SSO_CALLBACK_URL", DEFAULT_LOCATION_CALLBACK_URL),
        help="Local EVE SSO callback URL for optional location cheer.",
    )
    parser.add_argument(
        "--esi-base-url",
        default=os.environ.get("INTEL_PET_ESI_BASE_URL", DEFAULT_ESI_BASE_URL),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-open-sso-browser",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="Log polling interval.")
    parser.add_argument("--read-existing", action="store_true", help="Process existing log lines instead of new lines only.")
    parser.add_argument("--console", action="store_true", help="Print alerts to the console instead of opening the overlay.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.settings_path, overrides=args)
    engine = IntelPetEngine(settings)
    location_config = None
    location_session = None
    if args.enable_location_cheer:
        if args.console:
            raise CorpIntelError("Location cheer needs overlay mode. Remove --console to use the ship animation.")
        location_config = location_sso_config_from_args(args)
        location_session = login_location_session(location_config, open_browser=not args.no_open_sso_browser)
    if args.console:
        run_console(args, engine)
    else:
        run_overlay(args, engine, location_config=location_config, location_session=location_session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
