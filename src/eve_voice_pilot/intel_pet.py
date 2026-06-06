from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
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


DEFAULT_SETTINGS_PATH = ROOT / "profiles" / "intel_pet_settings.json"
DEFAULT_SPRITE_DIR = ROOT / "src" / "eve_voice_pilot" / "static" / "intel-pet"
DEFAULT_ALERT_SECONDS = 15.0
DEFAULT_LOCATION_CALLBACK_URL = "http://127.0.0.1:8788/intel-pet/callback"
DEFAULT_LOCATION_POLL_SECONDS = 15.0
DEFAULT_HAPPY_SYSTEMS = ("Dihra", "Amarr", "Jita")
DEFAULT_HISTORY_LIMIT = 25
LOCATION_SCOPE = "esi-location.read_location.v1"
OVERLAY_IDLE_WIDTH = 220
OVERLAY_ALERT_WIDTH = 430
OVERLAY_HEIGHT = 176
BEHAVIOR_ALERT = "alert"
BEHAVIOR_HAPPY = "happy"
BEHAVIOR_COMBAT = "combat"
BEHAVIOR_IDLE = "idle"
BEHAVIOR_NONE = "none"
BEHAVIOR_OPTIONS = (
    (BEHAVIOR_ALERT, "Alert pulse", "quick turret and engine pulse"),
    (BEHAVIOR_HAPPY, "Happy flight", "small cheerful loop"),
    (BEHAVIOR_COMBAT, "Combat burst", "fast flight with laser shots"),
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
)
DEFAULT_ALERT_BEHAVIORS = {
    "mention": BEHAVIOR_ALERT,
    "help": BEHAVIOR_ALERT,
    "hostile": BEHAVIOR_ALERT,
    "keyword": BEHAVIOR_ALERT,
    "location": BEHAVIOR_HAPPY,
    "combat": BEHAVIOR_COMBAT,
}
SHIP_FRAME_COUNT = 8
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


def default_alert_behaviors() -> dict[str, str]:
    return dict(DEFAULT_ALERT_BEHAVIORS)


@dataclass(frozen=True)
class IntelPetSettings:
    pilot_names: tuple[str, ...] = ()
    extra_keywords: tuple[str, ...] = ()
    help_phrases: tuple[str, ...] = ()
    show_message_text: bool = True
    alert_seconds: float = DEFAULT_ALERT_SECONDS
    alert_behaviors: dict[str, str] = field(default_factory=default_alert_behaviors)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntelPetSettings":
        return cls(
            pilot_names=clean_watchlist_terms(payload.get("pilot_names")),
            extra_keywords=clean_watchlist_terms(payload.get("extra_keywords")),
            help_phrases=clean_watchlist_terms(payload.get("help_phrases")),
            show_message_text=bool(payload.get("show_message_text", True)),
            alert_seconds=safe_float(payload.get("alert_seconds"), DEFAULT_ALERT_SECONDS),
            alert_behaviors=clean_alert_behaviors(payload.get("alert_behaviors")),
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
        }


@dataclass(frozen=True)
class IntelPetAlert:
    title: str
    severity: str
    channel: str
    speaker: str
    message: str
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
class IntelPetHistoryItem:
    title: str
    detail: str
    meta: str
    severity: str
    recorded_at: str


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
    return settings


def save_settings(path: Path, settings: IntelPetSettings) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(settings.to_dict(), indent=2) + "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8")
    tmp_path.replace(path)


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


def alert_behavior_key(alert: IntelPetAlert) -> str:
    categories = set(alert.categories)
    if "self-mention" in categories:
        return "mention"
    if "aid" in categories:
        return "help"
    if "hostile" in categories:
        return "hostile"
    return "keyword"


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


def format_alert(alert: IntelPetAlert) -> str:
    message = f" | {alert.message}" if alert.message else ""
    keywords = f" | {', '.join(alert.keywords)}" if alert.keywords else ""
    return f"[{alert.severity.upper()}] {alert.title} | {alert.speaker}{message}{keywords}"


def history_item_from_alert(alert: IntelPetAlert) -> IntelPetHistoryItem:
    message = alert.message or "Message text hidden by settings."
    meta = f"{alert.channel} | {', '.join(alert.keywords) or 'matched chat'}"
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
    return alert.message or "Message text hidden by settings."


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


def display_message_from_combat_cheer(cheer: IntelPetCombatCheer) -> str:
    return cheer.message


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


def ship_sprite_frame_paths(asset_dir: Path = DEFAULT_SPRITE_DIR) -> tuple[Path, ...]:
    return tuple(asset_dir / f"ship-frame-{index:02d}.png" for index in range(SHIP_FRAME_COUNT))


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
    callback = urlparse(config.callback_url)
    if callback.scheme != "http" or callback.hostname not in {"127.0.0.1", "localhost"}:
        raise CorpIntelError(
            "Intel Pet location cheer uses a localhost HTTP callback, "
            "like http://127.0.0.1:8788/intel-pet/callback."
        )
    if not callback.port:
        raise CorpIntelError("Intel Pet location cheer callback URL must include a localhost port.")


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


def watch_game_logs(
    *,
    log_dir: Path,
    on_kill: Callable[[IntelPetCombatCheer], None],
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
        log("Starting at the end of existing game logs. New kill-looking lines will be processed.")

    while not stop_event.is_set():
        discover_game_log_files(log_dir, states, read_existing=read_existing, log=log)
        for state in list(states.values()):
            for cheer in read_new_combat_cheers(state):
                on_kill(cheer)
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


def run_console(args: argparse.Namespace, engine: IntelPetEngine) -> None:
    channel_filter = channel_filter_from_args(args)

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            print(format_alert(alert), flush=True)

    watch_chat_logs(
        log_dir=args.log_dir,
        channel_filter=channel_filter,
        on_message=on_message,
        poll_seconds=args.poll_seconds,
        read_existing=args.read_existing,
    )


def run_overlay(
    args: argparse.Namespace,
    engine: IntelPetEngine,
    *,
    location_config: EveSsoConfig | None = None,
    location_session: IntelPetLocationSession | None = None,
) -> None:
    import tkinter as tk
    from tkinter import ttk

    alert_queue: queue.Queue[IntelPetAlert | IntelPetLocationCheer | IntelPetCombatCheer | str] = queue.Queue()
    stop_event = threading.Event()
    channel_filter = channel_filter_from_args(args)
    settings_path = args.settings_path.expanduser()
    happy_systems = clean_user_terms(args.happy_system or DEFAULT_HAPPY_SYSTEMS)
    location_poll_seconds = max(5.0, safe_float(args.location_poll_seconds, DEFAULT_LOCATION_POLL_SECONDS))

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            alert_queue.put(alert)

    def on_kill(cheer: IntelPetCombatCheer) -> None:
        alert_queue.put(cheer)

    def watcher() -> None:
        try:
            watch_chat_logs(
                log_dir=args.log_dir,
                channel_filter=channel_filter,
                on_message=on_message,
                poll_seconds=args.poll_seconds,
                read_existing=args.read_existing,
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
                on_kill=on_kill,
                poll_seconds=args.poll_seconds,
                read_existing=args.read_existing,
                stop_event=stop_event,
                log=lambda text: alert_queue.put(text),
            )
        except Exception as exc:  # pragma: no cover - surfaced in the UI.
            alert_queue.put(f"Combat cheer stopped: {exc}")

    if not args.no_combat_cheer:
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

    root = tk.Tk()
    root.title("EVE Intel Pet")
    desktop_left = root.winfo_vrootx()
    desktop_width = root.winfo_vrootwidth()
    start_x = max(desktop_left + 20, desktop_left + desktop_width - OVERLAY_ALERT_WIDTH - 40)
    root.geometry(f"{OVERLAY_IDLE_WIDTH}x{OVERLAY_HEIGHT}+{start_x}+80")
    root.attributes("-topmost", True)
    root.overrideredirect(True)

    transparent_color = "#ff00ff"
    bubble_fill = "#101820"
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
    sprite_after_id: str | None = None
    idle_cycle_after_id: str | None = None
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
    bubble_item_ids = (*bubble_border_items, bubble_tail_id, message_id)
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

    def open_options() -> None:
        editor = tk.Toplevel(root)
        editor.title("Intel Pet Options")
        editor.geometry("620x640+80+80")
        editor.minsize(500, 520)
        editor.transient(root)
        editor.attributes("-topmost", True)

        editor_frame = ttk.Frame(editor, padding=12)
        editor_frame.pack(fill="both", expand=True)
        notebook = ttk.Notebook(editor_frame)
        notebook.pack(fill="both", expand=True)
        settings_frame = ttk.Frame(notebook, padding=12)
        behavior_frame = ttk.Frame(notebook, padding=12)
        history_frame = ttk.Frame(notebook, padding=12)
        notebook.add(settings_frame, text="Alerts")
        notebook.add(behavior_frame, text="Behaviors")
        notebook.add(history_frame, text="History")

        editor_status_var = tk.StringVar(value="Saved locally only.")

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
            term_list = tk.Listbox(list_frame, height=4, exportselection=False)
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

        def draw_behavior_preview(canvas: Any, behavior: str, step: int) -> None:
            canvas.delete("preview")
            canvas.create_rectangle(0, 0, 96, 52, fill="#0f172a", outline="#334155", tags=("preview",))
            canvas.create_oval(14, 9, 16, 11, fill="#e2e8f0", outline="", tags=("preview",))
            canvas.create_oval(72, 10, 74, 12, fill="#e2e8f0", outline="", tags=("preview",))
            canvas.create_oval(84, 34, 86, 36, fill="#e2e8f0", outline="", tags=("preview",))

            if behavior == BEHAVIOR_HAPPY:
                offsets = ((0, 0), (6, -5), (10, 0), (6, 5), (0, 0), (-6, -5), (-10, 0), (-6, 5))
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#38bdf8"
            elif behavior == BEHAVIOR_COMBAT:
                offsets = ((0, 0), (10, -6), (16, 2), (8, 8), (-8, -5), (0, 0))
                offset_x, offset_y = offsets[step % len(offsets)]
                accent = "#f97316"
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

        ttk.Label(history_frame, text="Alert history", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            history_frame,
            text="This keeps the recent alerts in memory only while the pet is running.",
            wraplength=520,
        ).pack(anchor="w", pady=(2, 8))
        history_body = ttk.Frame(history_frame)
        history_body.pack(fill="both", expand=True)
        history_text = tk.Text(history_body, height=20, wrap="word", state="disabled")
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

        history_buttons = ttk.Frame(history_frame)
        history_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(history_buttons, text="Refresh", command=refresh_history_text).pack(side="left")
        ttk.Button(history_buttons, text="Clear History", command=clear_history).pack(side="left", padx=(6, 0))
        refresh_history_text()

        footer = ttk.Frame(editor_frame)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=editor_status_var, wraplength=380).pack(side="left", anchor="w")
        ttk.Button(footer, text="Quit Pet", command=on_close).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="Close", command=editor.destroy).pack(side="right")
        if first_entry is not None:
            first_entry.focus_set()

    idle_after_id: str | None = None

    def set_sprite_frame(index: int, *, offset_x: int = 0, offset_y: int = 0) -> None:
        if not sprite_frames or sprite_image_id is None:
            return
        clean_index = max(0, min(index, len(sprite_frames) - 1))
        sprite_canvas.itemconfigure(sprite_image_id, image=sprite_frames[clean_index])
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
                sprite_after_id = root.after(90, lambda: advance(next_position))
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
        elif behavior == BEHAVIOR_IDLE:
            start_sprite_cycle(IDLE_SPRITE_SEQUENCE)
        elif behavior == BEHAVIOR_NONE:
            cancel_sprite_cycle()
            cancel_idle_sprite_cycle()
            set_sprite_frame(0)
            schedule_idle_sprite_cycle()
        else:
            start_sprite_cycle(ALERT_SPRITE_SEQUENCE)

    def apply_severity(severity: str) -> None:
        color = colors.get(severity, colors["info"])
        for item_id in bubble_border_items:
            bubble_canvas.itemconfigure(item_id, outline=color)
        bubble_canvas.itemconfigure(bubble_tail_id, outline=color)
        control_canvas.itemconfigure(options_rect_id, outline=color)

    def resize_overlay(width: int) -> None:
        desktop_left = root.winfo_vrootx()
        desktop_width = root.winfo_vrootwidth()
        max_x = desktop_left + max(0, desktop_width - width - 8)
        clean_x = min(max(root.winfo_x(), desktop_left), max_x)
        root.geometry(f"{width}x{OVERLAY_HEIGHT}+{clean_x}+{root.winfo_y()}")

    def show_message_bubble(message: str, *, severity: str) -> None:
        resize_overlay(OVERLAY_ALERT_WIDTH)
        bubble_canvas.place(x=128, y=6)
        control_canvas.lift()
        apply_severity(severity)
        message_var.set(message)
        for item_id in bubble_item_ids:
            bubble_canvas.itemconfigure(item_id, state="normal")

    def hide_message_bubble() -> None:
        message_var.set("")
        for item_id in bubble_item_ids:
            bubble_canvas.itemconfigure(item_id, state="hidden")
        bubble_canvas.place_forget()
        resize_overlay(OVERLAY_IDLE_WIDTH)
        apply_severity("idle")

    def remember_history(item: IntelPetHistoryItem) -> None:
        history_items.append(item)
        del history_items[:-DEFAULT_HISTORY_LIMIT]

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
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        show_message_bubble(display_message_from_alert(alert), severity=alert.severity)
        remember_history(history_item_from_alert(alert))
        start_behavior_cycle(behavior_for_alert(alert, settings))
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def show_location_cheer(cheer: IntelPetLocationCheer) -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        show_message_bubble(display_message_from_cheer(cheer), severity="info")
        remember_history(history_item_from_cheer(cheer))
        start_behavior_cycle(behavior_for_kind("location", settings))
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def show_combat_cheer(cheer: IntelPetCombatCheer) -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        settings = engine.current_settings()
        show_message_bubble(display_message_from_combat_cheer(cheer), severity="high")
        remember_history(history_item_from_combat_cheer(cheer))
        start_behavior_cycle(behavior_for_kind("combat", settings))
        idle_after_id = root.after(int(settings.alert_seconds * 1000), set_idle)

    def poll_queue() -> None:
        while True:
            try:
                item = alert_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, IntelPetAlert):
                show_alert(item)
            elif isinstance(item, IntelPetLocationCheer):
                show_location_cheer(item)
            elif isinstance(item, IntelPetCombatCheer):
                show_combat_cheer(item)
            elif isinstance(item, str):
                status_item = history_item_from_status(item)
                remember_history(status_item)
                if status_item.severity == "high":
                    show_message_bubble(status_item.detail, severity=status_item.severity)
        root.after(250, poll_queue)

    def on_close() -> None:
        stop_event.set()
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    schedule_idle_sprite_cycle()
    poll_queue()
    root.mainloop()
    stop_event.set()


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
    parser.add_argument("--settings-path", type=Path, default=DEFAULT_SETTINGS_PATH, help="Local intel pet settings JSON.")
    parser.add_argument("--pilot-name", action="append", default=(), help="Your character name for mention alerts.")
    parser.add_argument("--keyword", action="append", default=(), help="Extra keyword to alert on.")
    parser.add_argument("--help-phrase", action="append", default=(), help="Extra help phrase to treat as critical.")
    parser.add_argument("--no-message-text", action="store_true", help="Hide the matched message text in the overlay.")
    parser.add_argument("--alert-seconds", type=float, default=None, help="Seconds before the overlay returns to idle.")
    parser.add_argument("--no-combat-cheer", action="store_true", help="Do not watch local game logs for kill cheers.")
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
