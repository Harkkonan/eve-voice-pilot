from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import fnmatch
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import sqlite3
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import webbrowser

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_CACHE_PATH = ROOT / "cache" / "eve_solar_systems.json"
DEFAULT_ESI_BASE_URL = "https://esi.evetech.net/latest"
DEFAULT_PORT = 8765
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_MAX_EVENTS = 500
DEFAULT_CHANNELS = "Corp,Corporation,Fleet,Alliance,Local,*Intel*"
DEFAULT_WATCHLIST_PATH = ROOT / "profiles" / "corp_intel_watchlist.json"
DEFAULT_EVENT_DB_PATH = ROOT / "profiles" / "corp_intel_events.sqlite3"
DEFAULT_PILOT_REGISTRY_PATH = ROOT / "profiles" / "corp_intel_pilots.sqlite3"
DEFAULT_EVE_SSO_WELL_KNOWN_URL = "https://login.eveonline.com/.well-known/oauth-authorization-server"
DEFAULT_WATCHLIST_REFRESH_SECONDS = 60.0
DEFAULT_EVENT_RETENTION_DAYS = 7
EXPECTED_EVE_SSO_AUDIENCE = "EVE Online"
EVE_SSO_CLOCK_SKEW_LEEWAY_SECONDS = 120
ACCEPTED_EVE_SSO_ISSUERS = {
    "login.eveonline.com",
    "https://login.eveonline.com",
    "https://login.eveonline.com/",
}
MAX_WATCHLIST_ITEMS = 200
MAX_WATCHLIST_TERM_LENGTH = 96
CHAT_LINE_RE = re.compile(
    r"^\[\s*(?P<timestamp>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*\]\s*"
    r"(?P<speaker>.*?)\s*>\s*(?P<message>.*)$"
)
CHANNEL_NAME_RE = re.compile(r"^\s*Channel\s+Name\s*:\s*(?P<channel>.+?)\s*$", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
TEXT_BOUNDARY_LEFT = r"(?<![A-Za-z0-9])"
TEXT_BOUNDARY_RIGHT = r"(?![A-Za-z0-9])"

COMMON_SYSTEM_NAMES = (
    "Ahbazon",
    "Amarr",
    "Dodixie",
    "Hek",
    "Jita",
    "Old Man Star",
    "Perimeter",
    "Rens",
    "Tama",
    "Uedama",
)

AID_KEYWORDS = (
    "help",
    "need help",
    "need reps",
    "need logi",
    "need extraction",
    "tackled",
    "pointed",
    "scrammed",
    "dying",
    "under attack",
    "save me",
)

HOSTILE_KEYWORDS = (
    "hostile",
    "enemy",
    "war target",
    "wt",
    "red",
    "neut",
    "neutral",
    "camp",
    "gate camp",
    "bubble",
    "cyno",
    "dictor",
    "tackle",
    "smartbomb",
    "bombers",
    "dreads",
)

SEVERITY_RANK = {
    "info": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

WATCHLIST_FIELDS = (
    "hostile_pilots",
    "hostile_corporations",
    "help_phrases",
    "keywords",
)


class CorpIntelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatMessage:
    log_path: str
    channel: str
    timestamp: str
    speaker: str
    text: str

    @property
    def observed_at(self) -> str:
        return eve_timestamp_to_iso(self.timestamp)


@dataclass(frozen=True)
class KeywordRule:
    keyword: str
    category: str
    severity: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class IntelEvent:
    source: str
    channel: str
    speaker: str
    message: str
    categories: tuple[str, ...]
    severity: str
    systems: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    observed_at: str = ""
    reported_at: str = ""
    log_path: str = ""
    verified_character_id: int | None = None
    verified_character_name: str = ""
    verified_corporation_id: int | None = None
    verified_corporation_name: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.event_id,
            "source": self.source,
            "channel": self.channel,
            "speaker": self.speaker,
            "message": self.message,
            "categories": list(self.categories),
            "severity": self.severity,
            "systems": list(self.systems),
            "keywords": list(self.keywords),
            "observed_at": self.observed_at,
            "reported_at": self.reported_at,
        }
        if self.verified_character_id is not None:
            payload["verified_character_id"] = self.verified_character_id
            payload["verified_character_name"] = self.verified_character_name
        if self.verified_corporation_id is not None:
            payload["verified_corporation_id"] = self.verified_corporation_id
            payload["verified_corporation_name"] = self.verified_corporation_name
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntelEvent":
        verified_character_id = payload.get("verified_character_id")
        verified_corporation_id = payload.get("verified_corporation_id")
        return cls(
            event_id=str(payload.get("id") or payload.get("event_id") or uuid.uuid4().hex),
            source=str(payload.get("source") or "unknown"),
            channel=str(payload.get("channel") or "unknown"),
            speaker=str(payload.get("speaker") or ""),
            message=str(payload.get("message") or ""),
            categories=tuple(str(item) for item in payload.get("categories") or ()),
            severity=str(payload.get("severity") or "info"),
            systems=tuple(str(item) for item in payload.get("systems") or ()),
            keywords=tuple(str(item) for item in payload.get("keywords") or ()),
            observed_at=str(payload.get("observed_at") or ""),
            reported_at=str(payload.get("reported_at") or now_iso()),
            log_path=str(payload.get("log_path") or ""),
            verified_character_id=int(verified_character_id) if verified_character_id is not None else None,
            verified_character_name=str(payload.get("verified_character_name") or ""),
            verified_corporation_id=int(verified_corporation_id) if verified_corporation_id is not None else None,
            verified_corporation_name=str(payload.get("verified_corporation_name") or ""),
        )


@dataclass(frozen=True)
class EveSsoConfig:
    client_id: str = ""
    client_secret: str = ""
    callback_url: str = ""
    scopes: tuple[str, ...] = ()
    allowed_corporation_ids: tuple[int, ...] = ()
    allowed_alliance_ids: tuple[int, ...] = ()
    trusted_members_can_edit: bool = False
    well_known_url: str = DEFAULT_EVE_SSO_WELL_KNOWN_URL
    esi_base_url: str = DEFAULT_ESI_BASE_URL

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.callback_url)

    @property
    def membership_restricted(self) -> bool:
        return bool(self.allowed_corporation_ids or self.allowed_alliance_ids)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "scopes": list(self.scopes),
            "allowed_corporation_ids": list(self.allowed_corporation_ids),
            "allowed_alliance_ids": list(self.allowed_alliance_ids),
            "membership_restricted": self.membership_restricted,
            "trusted_members_can_edit": self.trusted_members_can_edit,
        }


@dataclass(frozen=True)
class VerifiedPilot:
    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str = ""
    alliance_id: int | None = None
    alliance_name: str = ""
    owner_hash: str = ""
    scopes: tuple[str, ...] = ()
    membership_ok: bool = False
    verified_at: str = ""
    last_login_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "character_name": self.character_name,
            "corporation_id": self.corporation_id,
            "corporation_name": self.corporation_name,
            "alliance_id": self.alliance_id,
            "alliance_name": self.alliance_name,
            "scopes": list(self.scopes),
            "membership_ok": self.membership_ok,
            "verified_at": self.verified_at,
            "last_login_at": self.last_login_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "VerifiedPilot":
        scopes_raw = str(row["scopes"] or "")
        return cls(
            character_id=int(row["character_id"]),
            character_name=str(row["character_name"] or ""),
            corporation_id=int(row["corporation_id"]),
            corporation_name=str(row["corporation_name"] or ""),
            alliance_id=int(row["alliance_id"]) if row["alliance_id"] is not None else None,
            alliance_name=str(row["alliance_name"] or ""),
            owner_hash=str(row["owner_hash"] or ""),
            scopes=tuple(item for item in scopes_raw.split(" ") if item),
            membership_ok=bool(row["membership_ok"]),
            verified_at=str(row["verified_at"] or ""),
            last_login_at=str(row["last_login_at"] or ""),
        )


@dataclass(frozen=True)
class DashboardAccess:
    ok: bool
    status: int
    message: str


@dataclass(frozen=True)
class AgentTokenRecord:
    token_id: str
    character_id: int
    label: str
    created_at: str
    last_used_at: str = ""
    revoked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "character_id": self.character_id,
            "label": self.label,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AgentTokenRecord":
        return cls(
            token_id=str(row["token_id"]),
            character_id=int(row["character_id"]),
            label=str(row["label"] or ""),
            created_at=str(row["created_at"] or ""),
            last_used_at=str(row["last_used_at"] or ""),
            revoked_at=str(row["revoked_at"] or ""),
        )


@dataclass
class ChatLogState:
    path: Path
    channel: str
    encoding: str
    offset: int


class ChannelFilter:
    def __init__(self, patterns: Iterable[str] = (), *, all_channels: bool = False):
        self.all_channels = all_channels
        self.patterns = tuple(pattern.strip().casefold() for pattern in patterns if pattern.strip())

    def allows(self, channel: str) -> bool:
        if self.all_channels:
            return True
        folded = channel.casefold()
        return any(fnmatch.fnmatch(folded, pattern) for pattern in self.patterns)

    def describe(self) -> str:
        if self.all_channels:
            return "all channels"
        return ", ".join(self.patterns) if self.patterns else "no channels"


class SystemMatcher:
    def __init__(self, system_names: Iterable[str]):
        names = sorted({name.strip() for name in system_names if name.strip()}, key=lambda item: (-len(item), item))
        self._canonical = {name.casefold(): name for name in names}
        if not names:
            self._pattern = None
            return
        body = "|".join(re.escape(name) for name in names)
        self._pattern = re.compile(f"{TEXT_BOUNDARY_LEFT}(?:{body}){TEXT_BOUNDARY_RIGHT}", re.IGNORECASE)

    def find(self, text: str) -> tuple[str, ...]:
        if self._pattern is None:
            return ()
        found: list[str] = []
        seen: set[str] = set()
        for match in self._pattern.finditer(text):
            folded = match.group(0).casefold()
            canonical = self._canonical.get(folded)
            if canonical and canonical.casefold() not in seen:
                seen.add(canonical.casefold())
                found.append(canonical)
        return tuple(found)


@dataclass(frozen=True)
class WatchlistMatch:
    term: str
    keyword: str
    categories: tuple[str, ...]
    severity: str


@dataclass(frozen=True)
class CompiledWatchTerm:
    term: str
    keyword: str
    categories: tuple[str, ...]
    severity: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class IntelWatchlist:
    hostile_pilots: tuple[str, ...] = ()
    hostile_corporations: tuple[str, ...] = ()
    help_phrases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    updated_at: str = ""
    updated_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostile_pilots": list(self.hostile_pilots),
            "hostile_corporations": list(self.hostile_corporations),
            "help_phrases": list(self.help_phrases),
            "keywords": list(self.keywords),
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    def counts(self) -> dict[str, int]:
        return {
            "hostile_pilots": len(self.hostile_pilots),
            "hostile_corporations": len(self.hostile_corporations),
            "help_phrases": len(self.help_phrases),
            "keywords": len(self.keywords),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntelWatchlist":
        return cls(
            hostile_pilots=clean_watchlist_terms(payload.get("hostile_pilots")),
            hostile_corporations=clean_watchlist_terms(payload.get("hostile_corporations")),
            help_phrases=clean_watchlist_terms(payload.get("help_phrases")),
            keywords=clean_watchlist_terms(payload.get("keywords")),
            updated_at=str(payload.get("updated_at") or ""),
            updated_by=str(payload.get("updated_by") or ""),
        )


class WatchlistStore:
    def __init__(self, path: Path | None = None, *, watchlist: IntelWatchlist | None = None):
        self.path = path.expanduser() if path else None
        self._lock = threading.Lock()
        self._watchlist = watchlist or IntelWatchlist()
        self._compiled = compile_watchlist_terms(self._watchlist)
        if self.path:
            self.load()

    def load(self) -> IntelWatchlist:
        if not self.path or not self.path.exists():
            return self.snapshot()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpIntelError(f"Could not read watchlist {self.path}: {exc}") from exc
        watchlist = IntelWatchlist.from_dict(payload if isinstance(payload, dict) else {})
        self.replace(watchlist)
        return watchlist

    def replace(self, watchlist: IntelWatchlist) -> IntelWatchlist:
        compiled = compile_watchlist_terms(watchlist)
        with self._lock:
            self._watchlist = watchlist
            self._compiled = compiled
        return watchlist

    def update(self, payload: dict[str, Any], *, updated_by: str = "dashboard") -> IntelWatchlist:
        clean_payload = dict(payload)
        clean_payload["updated_at"] = now_iso()
        clean_payload["updated_by"] = updated_by[:64]
        watchlist = IntelWatchlist.from_dict(clean_payload)
        self.replace(watchlist)
        self.save()
        return watchlist

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(self.snapshot().to_dict(), indent=2) + "\n"
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(body, encoding="utf-8")
        tmp_path.replace(self.path)

    def snapshot(self) -> IntelWatchlist:
        with self._lock:
            return self._watchlist

    def to_dict(self) -> dict[str, Any]:
        watchlist = self.snapshot()
        payload = watchlist.to_dict()
        payload["counts"] = watchlist.counts()
        return payload

    def match(self, text: str, *, speaker: str = "") -> tuple[WatchlistMatch, ...]:
        with self._lock:
            compiled = tuple(self._compiled)
        matches: list[WatchlistMatch] = []
        for item in compiled:
            matched_text = item.pattern.search(text)
            matched_speaker = (
                bool(speaker)
                and "watchlist-pilot" in item.categories
                and item.pattern.search(speaker)
            )
            if matched_text or matched_speaker:
                matches.append(
                    WatchlistMatch(
                        term=item.term,
                        keyword=item.keyword,
                        categories=item.categories,
                        severity=item.severity,
                    )
                )
        return tuple(matches)


class PilotRegistry:
    def __init__(self, path: Path):
        self.path = path.expanduser()
        self._lock = threading.Lock()
        self.initialize()

    def initialize(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS verified_pilots (
                        character_id INTEGER PRIMARY KEY,
                        character_name TEXT NOT NULL,
                        corporation_id INTEGER NOT NULL,
                        corporation_name TEXT NOT NULL,
                        alliance_id INTEGER,
                        alliance_name TEXT NOT NULL,
                        owner_hash TEXT NOT NULL,
                        scopes TEXT NOT NULL,
                        membership_ok INTEGER NOT NULL,
                        verified_at TEXT NOT NULL,
                        last_login_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_verified_pilots_corporation ON verified_pilots(corporation_id)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_tokens (
                        token_id TEXT PRIMARY KEY,
                        character_id INTEGER NOT NULL,
                        label TEXT NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL,
                        revoked_at TEXT NOT NULL,
                        FOREIGN KEY(character_id) REFERENCES verified_pilots(character_id)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_tokens_character ON agent_tokens(character_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_tokens_hash ON agent_tokens(token_hash)"
                )
                connection.commit()

    def upsert(self, pilot: VerifiedPilot) -> VerifiedPilot:
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO verified_pilots (
                    character_id, character_name, corporation_id, corporation_name, alliance_id, alliance_name,
                    owner_hash, scopes, membership_ok, verified_at, last_login_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    character_name = excluded.character_name,
                    corporation_id = excluded.corporation_id,
                    corporation_name = excluded.corporation_name,
                    alliance_id = excluded.alliance_id,
                    alliance_name = excluded.alliance_name,
                    owner_hash = excluded.owner_hash,
                    scopes = excluded.scopes,
                    membership_ok = excluded.membership_ok,
                    verified_at = excluded.verified_at,
                    last_login_at = excluded.last_login_at
                """,
                (
                    pilot.character_id,
                    pilot.character_name,
                    pilot.corporation_id,
                    pilot.corporation_name,
                    pilot.alliance_id,
                    pilot.alliance_name,
                    pilot.owner_hash,
                    " ".join(pilot.scopes),
                    1 if pilot.membership_ok else 0,
                    pilot.verified_at,
                    pilot.last_login_at,
                ),
            )
            connection.commit()
        return pilot

    def get(self, character_id: int) -> VerifiedPilot | None:
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM verified_pilots WHERE character_id = ?",
                (character_id,),
            ).fetchone()
        return VerifiedPilot.from_row(row) if row else None

    def list_recent(self, *, limit: int = 50) -> list[VerifiedPilot]:
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT *
                FROM verified_pilots
                ORDER BY last_login_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [VerifiedPilot.from_row(row) for row in rows]

    def create_agent_token(self, character_id: int, *, label: str = "") -> tuple[str, AgentTokenRecord]:
        pilot = self.get(character_id)
        if pilot is None:
            raise CorpIntelError("Only verified pilots can create agent tokens.")
        if not pilot.membership_ok:
            raise CorpIntelError("Only allowlisted verified pilots can create agent tokens.")
        token = f"cit_{secrets.token_urlsafe(32)}"
        token_id = uuid.uuid4().hex
        timestamp = now_iso()
        clean_label = SPACE_RE.sub(" ", label).strip()[:80] or "Chatlog agent"
        record = AgentTokenRecord(
            token_id=token_id,
            character_id=character_id,
            label=clean_label,
            created_at=timestamp,
        )
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO agent_tokens (
                    token_id, character_id, label, token_hash, created_at, last_used_at, revoked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.token_id,
                    record.character_id,
                    record.label,
                    hash_agent_token(token),
                    record.created_at,
                    record.last_used_at,
                    record.revoked_at,
                ),
            )
            connection.commit()
        return token, record

    def list_agent_tokens(self, character_id: int) -> list[AgentTokenRecord]:
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT token_id, character_id, label, created_at, last_used_at, revoked_at
                FROM agent_tokens
                WHERE character_id = ?
                ORDER BY created_at DESC
                """,
                (character_id,),
            ).fetchall()
        return [AgentTokenRecord.from_row(row) for row in rows]

    def revoke_agent_token(self, *, character_id: int, token_id: str) -> bool:
        timestamp = now_iso()
        with self._lock, sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                """
                UPDATE agent_tokens
                SET revoked_at = ?
                WHERE character_id = ? AND token_id = ? AND revoked_at = ''
                """,
                (timestamp, character_id, token_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def resolve_agent_token(self, token: str) -> VerifiedPilot | None:
        token_hash = hash_agent_token(token)
        timestamp = now_iso()
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT p.*
                FROM agent_tokens AS t
                JOIN verified_pilots AS p ON p.character_id = t.character_id
                WHERE t.token_hash = ? AND t.revoked_at = ''
                """,
                (token_hash,),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE agent_tokens SET last_used_at = ? WHERE token_hash = ?",
                    (timestamp, token_hash),
                )
                connection.commit()
        if not row:
            return None
        pilot = VerifiedPilot.from_row(row)
        return pilot if pilot.membership_ok else None


class AuthStateStore:
    def __init__(self, *, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._states: dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        state = secrets.token_urlsafe(32)
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            self._states[state] = expires_at
            self._prune_locked()
        return state

    def consume(self, state: str) -> bool:
        with self._lock:
            self._prune_locked()
            expires_at = self._states.pop(state, None)
        return bool(expires_at and expires_at >= time.time())

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [state for state, expires_at in self._states.items() if expires_at < now]
        for state in expired:
            self._states.pop(state, None)


class SessionStore:
    def __init__(self, *, ttl_seconds: int = 12 * 60 * 60):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def create(self, character_id: int) -> str:
        session_id = secrets.token_urlsafe(32)
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            self._sessions[session_id] = (character_id, expires_at)
            self._prune_locked()
        return session_id

    def get(self, session_id: str) -> int | None:
        with self._lock:
            self._prune_locked()
            item = self._sessions.get(session_id)
        if not item:
            return None
        character_id, expires_at = item
        return character_id if expires_at >= time.time() else None

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [session_id for session_id, (_, expires_at) in self._sessions.items() if expires_at < now]
        for session_id in expired:
            self._sessions.pop(session_id, None)


class EventDatabase:
    def __init__(self, path: Path):
        self.path = path.expanduser()
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS intel_events (
                        event_id TEXT PRIMARY KEY,
                        observed_at TEXT NOT NULL,
                        reported_at TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        source TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_intel_events_observed_at ON intel_events(observed_at)"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS idx_intel_events_severity ON intel_events(severity)")
                connection.commit()
            self._initialized = True

    def load_recent(self, *, max_events: int) -> tuple[IntelEvent, ...]:
        self.initialize()
        with self._lock, sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM intel_events
                ORDER BY observed_at DESC, reported_at DESC
                LIMIT ?
                """,
                (max_events,),
            ).fetchall()
        events: list[IntelEvent] = []
        for (payload_json,) in reversed(rows):
            try:
                payload = json.loads(str(payload_json))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(IntelEvent.from_dict(payload))
        return tuple(events)

    def add(self, event: IntelEvent) -> None:
        self.initialize()
        payload = event.to_dict()
        observed_at = event.observed_at or event.reported_at or now_iso()
        reported_at = event.reported_at or now_iso()
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO intel_events (
                    event_id, observed_at, reported_at, severity, source, channel, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    observed_at,
                    reported_at,
                    event.severity,
                    event.source,
                    event.channel,
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
            connection.commit()

    def prune(self, *, retention_days: int, max_events: int) -> None:
        if retention_days <= 0:
            return
        self.initialize()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).replace(microsecond=0)
        cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM intel_events WHERE observed_at < ?", (cutoff_iso,))
            connection.execute(
                """
                DELETE FROM intel_events
                WHERE event_id NOT IN (
                    SELECT event_id
                    FROM intel_events
                    ORDER BY observed_at DESC, reported_at DESC
                    LIMIT ?
                )
                """,
                (max_events,),
            )
            connection.commit()


class IntelParser:
    def __init__(self, system_names: Iterable[str], *, watchlist_store: WatchlistStore | None = None):
        self.system_matcher = SystemMatcher(system_names)
        self.keyword_rules = build_keyword_rules()
        self.watchlist_store = watchlist_store or WatchlistStore()

    def analyze(self, message: ChatMessage, *, source: str = "local") -> IntelEvent | None:
        categories: set[str] = set()
        keywords: list[str] = []
        severity = "info"
        systems = self.system_matcher.find(message.text)

        for rule in self.keyword_rules:
            if rule.pattern.search(message.text):
                categories.add(rule.category)
                keywords.append(rule.keyword)
                severity = higher_severity(severity, rule.severity)

        for match in self.watchlist_store.match(message.text, speaker=message.speaker):
            categories.update(match.categories)
            keywords.append(match.keyword)
            severity = higher_severity(severity, match.severity)

        if not categories:
            return None

        if systems and "hostile" in categories:
            severity = higher_severity(severity, "high")
        if "aid" in categories:
            severity = higher_severity(severity, "critical")

        return IntelEvent(
            source=source,
            channel=message.channel,
            speaker=message.speaker,
            message=message.text,
            categories=tuple(sorted(categories)),
            severity=severity,
            systems=systems,
            keywords=tuple(dedupe_preserve_order(keywords)),
            observed_at=message.observed_at,
            reported_at=now_iso(),
            log_path=message.log_path,
        )


class IntelEventStore:
    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        database: EventDatabase | None = None,
        retention_days: int = DEFAULT_EVENT_RETENTION_DAYS,
    ):
        self.max_events = max_events
        self.database = database
        self.retention_days = retention_days
        if self.database:
            self.database.prune(retention_days=self.retention_days, max_events=self.max_events)
        self._events: list[IntelEvent] = list(database.load_recent(max_events=max_events)) if database else []
        self._lock = threading.Lock()

    def add(self, event: IntelEvent) -> IntelEvent:
        if self.database:
            self.database.add(event)
            self.database.prune(retention_days=self.retention_days, max_events=self.max_events)
        with self._lock:
            self._events.append(event)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]
            return event

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)

        newest_first = list(reversed(events))
        return {
            "generated_at": now_iso(),
            "events": [event.to_dict() for event in newest_first],
            "systems": summarize_systems(events),
            "counts": summarize_counts(events),
        }


def default_chat_log_dir() -> Path:
    candidates = [
        Path.home() / "Documents" / "EVE" / "logs" / "Chatlogs",
        Path.home() / "OneDrive" / "Documents" / "EVE" / "logs" / "Chatlogs",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def build_keyword_rules() -> tuple[KeywordRule, ...]:
    rules: list[KeywordRule] = []
    for keyword in AID_KEYWORDS:
        rules.append(
            KeywordRule(
                keyword=keyword,
                category="aid",
                severity="critical",
                pattern=compile_phrase_pattern(keyword),
            )
        )
    for keyword in HOSTILE_KEYWORDS:
        severity = "high" if keyword in {"hostile", "enemy", "war target", "wt", "red", "neut"} else "medium"
        rules.append(
            KeywordRule(
                keyword=keyword,
                category="hostile",
                severity=severity,
                pattern=compile_phrase_pattern(keyword),
            )
        )
    return tuple(rules)


def clean_watchlist_terms(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values: Iterable[Any] = re.split(r"[\r\n,]+", values)
    elif isinstance(values, (list, tuple)):
        raw_values = values
    else:
        raw_values = ()

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        value = "".join(char for char in str(raw_value) if char.isprintable())
        value = SPACE_RE.sub(" ", value).strip()
        value = value[:MAX_WATCHLIST_TERM_LENGTH].strip()
        if not value:
            continue
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        cleaned.append(value)
        if len(cleaned) >= MAX_WATCHLIST_ITEMS:
            break
    return tuple(cleaned)


def compile_watchlist_terms(watchlist: IntelWatchlist) -> tuple[CompiledWatchTerm, ...]:
    terms: list[CompiledWatchTerm] = []
    terms.extend(
        build_compiled_watch_terms(
            watchlist.hostile_pilots,
            keyword_prefix="pilot",
            categories=("hostile", "watchlist-pilot"),
            severity="high",
        )
    )
    terms.extend(
        build_compiled_watch_terms(
            watchlist.hostile_corporations,
            keyword_prefix="corp",
            categories=("hostile", "watchlist-corporation"),
            severity="high",
        )
    )
    terms.extend(
        build_compiled_watch_terms(
            watchlist.help_phrases,
            keyword_prefix="help",
            categories=("aid", "watchlist-help"),
            severity="critical",
        )
    )
    terms.extend(
        build_compiled_watch_terms(
            watchlist.keywords,
            keyword_prefix="keyword",
            categories=("watchlist-keyword",),
            severity="medium",
        )
    )
    return tuple(terms)


def build_compiled_watch_terms(
    terms: Iterable[str],
    *,
    keyword_prefix: str,
    categories: tuple[str, ...],
    severity: str,
) -> list[CompiledWatchTerm]:
    compiled: list[CompiledWatchTerm] = []
    for term in terms:
        compiled.append(
            CompiledWatchTerm(
                term=term,
                keyword=f"{keyword_prefix}: {term}",
                categories=categories,
                severity=severity,
                pattern=compile_phrase_pattern(term),
            )
        )
    return compiled


def compile_phrase_pattern(phrase: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in SPACE_RE.split(phrase.strip()) if word]
    body = r"\s+".join(words)
    return re.compile(f"{TEXT_BOUNDARY_LEFT}{body}{TEXT_BOUNDARY_RIGHT}", re.IGNORECASE)


def higher_severity(left: str, right: str) -> str:
    return right if SEVERITY_RANK.get(right, 0) > SEVERITY_RANK.get(left, 0) else left


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


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def eve_timestamp_to_iso(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return now_iso()
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_chat_line(line: str, *, channel: str, log_path: str = "") -> ChatMessage | None:
    match = CHAT_LINE_RE.match(line.lstrip("\ufeff").rstrip("\r\n"))
    if not match:
        return None
    return ChatMessage(
        log_path=log_path,
        channel=channel,
        timestamp=match.group("timestamp"),
        speaker=match.group("speaker").strip(),
        text=match.group("message").strip(),
    )


def parse_channel_name_from_text(text: str) -> str | None:
    for line in text.splitlines()[:80]:
        match = CHANNEL_NAME_RE.match(line)
        if match:
            return match.group("channel").strip()
    return None


def fallback_channel_name(path: Path) -> str:
    name = re.sub(r"_\d{8}.*$", "", path.stem)
    return name.replace("_", " ").strip() or path.stem


def detect_encoding(path: Path) -> str:
    try:
        raw = path.read_bytes()[:4]
    except OSError:
        return "utf-8"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def read_channel_name(path: Path, encoding: str) -> str:
    try:
        with path.open("r", encoding=encoding, errors="replace") as handle:
            text = "".join(handle.readline() for _ in range(80))
    except OSError:
        return fallback_channel_name(path)
    return parse_channel_name_from_text(text) or fallback_channel_name(path)


def file_end_offset(path: Path, encoding: str) -> int:
    try:
        with path.open("r", encoding=encoding, errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            return handle.tell()
    except OSError:
        return 0


def watch_chat_logs(
    *,
    log_dir: Path,
    channel_filter: ChannelFilter,
    on_message: Callable[[ChatMessage], None],
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    read_existing: bool = False,
    stop_event: threading.Event | None = None,
    log: Callable[[str], None] = print,
) -> None:
    states: dict[Path, ChatLogState] = {}
    stop_event = stop_event or threading.Event()
    log_dir = log_dir.expanduser()

    if not log_dir.exists():
        raise CorpIntelError(f"Chat log folder does not exist: {log_dir}")

    log(f"Watching EVE chat logs in {log_dir}")
    log(f"Channel allowlist: {channel_filter.describe()}")
    if not read_existing:
        log("Starting at the end of existing files. New chat lines will be processed.")

    while not stop_event.is_set():
        discover_chat_log_files(log_dir, states, channel_filter, read_existing=read_existing, log=log)
        for state in list(states.values()):
            for message in read_new_messages(state):
                on_message(message)
        stop_event.wait(poll_seconds)


def discover_chat_log_files(
    log_dir: Path,
    states: dict[Path, ChatLogState],
    channel_filter: ChannelFilter,
    *,
    read_existing: bool,
    log: Callable[[str], None] = print,
) -> None:
    for path in sorted(log_dir.glob("*.txt")):
        if path in states:
            continue
        encoding = detect_encoding(path)
        channel = read_channel_name(path, encoding)
        if not channel_filter.allows(channel):
            continue
        offset = 0 if read_existing else file_end_offset(path, encoding)
        states[path] = ChatLogState(path=path, channel=channel, encoding=encoding, offset=offset)
        log(f"Sharing channel {channel!r} from {path.name}")


def read_new_messages(state: ChatLogState) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    try:
        with state.path.open("r", encoding=state.encoding, errors="replace") as handle:
            handle.seek(state.offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                parsed = parse_chat_line(line, channel=state.channel, log_path=str(state.path))
                if parsed:
                    messages.append(parsed)
            state.offset = handle.tell()
    except OSError:
        return []
    return messages


def load_system_names(
    *,
    cache_path: Path = DEFAULT_SYSTEM_CACHE_PATH,
    base_url: str = DEFAULT_ESI_BASE_URL,
    refresh: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[str, ...]:
    if not refresh:
        cached = read_system_cache(cache_path)
        if cached:
            return cached

    try:
        names = fetch_system_names_from_esi(base_url=base_url)
    except CorpIntelError as exc:
        log(f"Could not refresh ESI system names: {exc}")
        cached = read_system_cache(cache_path)
        if cached:
            return cached
        log("Using a small built-in system list until ESI is reachable.")
        return COMMON_SYSTEM_NAMES

    write_system_cache(cache_path, names)
    return names


def read_system_cache(cache_path: Path) -> tuple[str, ...]:
    if not cache_path.exists():
        return ()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    systems = payload.get("systems") if isinstance(payload, dict) else None
    if not isinstance(systems, list):
        return ()
    names = tuple(str(item.get("name") or "") for item in systems if isinstance(item, dict))
    return tuple(name for name in names if name)


def write_system_cache(cache_path: Path, names: Iterable[str]) -> None:
    systems = [{"name": name} for name in sorted({name for name in names if name})]
    payload = {"fetched_at": now_iso(), "systems": systems}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def fetch_system_names_from_esi(*, base_url: str = DEFAULT_ESI_BASE_URL) -> tuple[str, ...]:
    ids = get_json(f"{base_url.rstrip('/')}/universe/systems/?datasource=tranquility")
    if not isinstance(ids, list):
        raise CorpIntelError("ESI universe systems endpoint returned unexpected data.")

    names: list[str] = []
    for chunk in chunked([int(item) for item in ids], 1000):
        payload = post_json(f"{base_url.rstrip('/')}/universe/names/?datasource=tranquility", chunk)
        if not isinstance(payload, list):
            raise CorpIntelError("ESI universe names endpoint returned unexpected data.")
        for item in payload:
            if isinstance(item, dict) and item.get("category") == "solar_system":
                name = str(item.get("name") or "").strip()
                if name:
                    names.append(name)
    if not names:
        raise CorpIntelError("ESI returned no solar system names.")
    return tuple(sorted(set(names)))


def get_json(url: str, *, timeout_seconds: float = 30.0, headers: dict[str, str] | None = None) -> Any:
    request_headers = {"Accept": "application/json", "User-Agent": "EVE Voice Pilot Corp Intel Board"}
    if headers:
        request_headers.update(headers)
    request = Request(
        url,
        headers=request_headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CorpIntelError(f"GET {url} failed: {exc}") from exc


def post_json(url: str, body: Any, *, token: str = "", timeout_seconds: float = 30.0) -> Any:
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "EVE Voice Pilot Corp Intel Board",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CorpIntelError(f"POST {url} returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise CorpIntelError(f"POST {url} failed: {exc}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorpIntelError(f"POST {url} returned non-JSON data: {raw[:200]!r}") from exc


def post_form(
    url: str,
    fields: dict[str, str],
    *,
    basic_auth: tuple[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> Any:
    data = urlencode(fields).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "EVE Voice Pilot Corp Intel Board",
    }
    if basic_auth:
        username, password = basic_auth
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CorpIntelError(f"POST {url} returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise CorpIntelError(f"POST {url} failed: {exc}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorpIntelError(f"POST {url} returned non-JSON data: {raw[:200]!r}") from exc


def fetch_sso_metadata(config: EveSsoConfig) -> dict[str, Any]:
    payload = get_json(config.well_known_url, timeout_seconds=15.0)
    if not isinstance(payload, dict):
        raise CorpIntelError("EVE SSO metadata endpoint returned unexpected data.")
    return payload


def build_sso_authorization_url(config: EveSsoConfig, state: str, metadata: dict[str, Any] | None = None) -> str:
    if not config.enabled:
        raise CorpIntelError("EVE SSO is not configured.")
    metadata = metadata or fetch_sso_metadata(config)
    authorize_url = str(metadata.get("authorization_endpoint") or "https://login.eveonline.com/v2/oauth/authorize")
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.callback_url,
        "state": state,
    }
    if config.scopes:
        params["scope"] = " ".join(config.scopes)
    return f"{authorize_url}?{urlencode(params)}"


def exchange_sso_code(config: EveSsoConfig, code: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or fetch_sso_metadata(config)
    token_url = str(metadata.get("token_endpoint") or "https://login.eveonline.com/v2/oauth/token")
    payload = post_form(
        token_url,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": config.callback_url},
        basic_auth=(config.client_id, config.client_secret),
        timeout_seconds=30.0,
    )
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise CorpIntelError("EVE SSO token endpoint did not return an access token.")
    return payload


def jwks_uri_from_metadata(metadata: dict[str, Any]) -> str:
    jwks_uri = str(metadata.get("jwks_uri") or "").strip()
    if not jwks_uri:
        raise CorpIntelError("EVE SSO metadata did not include a JWKS endpoint.")
    return jwks_uri


def jwk_client_for_uri(jwks_uri: str) -> PyJWKClient:
    return PyJWKClient(
        jwks_uri,
        headers={
            "Accept": "application/json",
            "User-Agent": "EVE Voice Pilot Corp Intel Board",
        },
    )


def validate_eve_access_token_claims(payload: dict[str, Any], *, client_id: str) -> None:
    issuer = str(payload.get("iss") or "")
    if issuer not in ACCEPTED_EVE_SSO_ISSUERS:
        raise CorpIntelError(f"EVE SSO token had unexpected issuer: {issuer}")

    audience = payload.get("aud")
    audiences = {str(audience)} if isinstance(audience, str) else {str(item) for item in audience or ()}
    if EXPECTED_EVE_SSO_AUDIENCE not in audiences:
        raise CorpIntelError("EVE SSO token audience does not include EVE Online.")
    if client_id not in audiences:
        raise CorpIntelError("EVE SSO token audience does not include this application.")

    subject = str(payload.get("sub") or "")
    if not subject.startswith("CHARACTER:EVE:"):
        raise CorpIntelError("EVE SSO token subject was not an EVE character.")


def decode_eve_access_token(
    access_token: str,
    *,
    client_id: str,
    metadata: dict[str, Any] | None = None,
    jwk_client: Any | None = None,
) -> dict[str, Any]:
    if access_token.count(".") < 2:
        raise CorpIntelError("EVE SSO access token is not a JWT.")
    if jwk_client is None:
        metadata = metadata or fetch_sso_metadata(EveSsoConfig())
        jwk_client = jwk_client_for_uri(jwks_uri_from_metadata(metadata))
    try:
        signing_key = jwk_client.get_signing_key_from_jwt(access_token)
        payload = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=EXPECTED_EVE_SSO_AUDIENCE,
            leeway=EVE_SSO_CLOCK_SKEW_LEEWAY_SECONDS,
            options={
                "require": ["aud", "exp", "iss", "sub"],
                "verify_iss": False,
            },
        )
    except (InvalidTokenError, PyJWKClientError) as exc:
        raise CorpIntelError(f"EVE SSO access token failed verification: {exc}") from exc
    if not isinstance(payload, dict):
        raise CorpIntelError("EVE SSO access token payload was not a JSON object.")
    validate_eve_access_token_claims(payload, client_id=client_id)
    return payload


def character_id_from_sso_payload(payload: dict[str, Any]) -> int:
    subject = str(payload.get("sub") or "")
    try:
        return int(subject.split(":")[-1])
    except ValueError as exc:
        raise CorpIntelError("EVE SSO token did not contain a valid character id.") from exc


def scopes_from_sso_payload(payload: dict[str, Any]) -> tuple[str, ...]:
    scopes = payload.get("scp") or payload.get("scope") or ()
    if isinstance(scopes, str):
        return tuple(item for item in scopes.split(" ") if item)
    if isinstance(scopes, list):
        return tuple(str(item) for item in scopes if str(item))
    return ()


def fetch_esi_character(config: EveSsoConfig, character_id: int) -> dict[str, Any]:
    url = f"{config.esi_base_url.rstrip('/')}/characters/{character_id}/?datasource=tranquility"
    payload = get_json(url, timeout_seconds=30.0)
    if not isinstance(payload, dict):
        raise CorpIntelError("ESI character endpoint returned unexpected data.")
    return payload


def fetch_esi_corporation(config: EveSsoConfig, corporation_id: int) -> dict[str, Any]:
    url = f"{config.esi_base_url.rstrip('/')}/corporations/{corporation_id}/?datasource=tranquility"
    payload = get_json(url, timeout_seconds=30.0)
    return payload if isinstance(payload, dict) else {}


def fetch_esi_alliance(config: EveSsoConfig, alliance_id: int) -> dict[str, Any]:
    url = f"{config.esi_base_url.rstrip('/')}/alliances/{alliance_id}/?datasource=tranquility"
    payload = get_json(url, timeout_seconds=30.0)
    return payload if isinstance(payload, dict) else {}


def membership_allowed(config: EveSsoConfig, *, corporation_id: int, alliance_id: int | None = None) -> bool:
    if not config.membership_restricted:
        return True
    if corporation_id in config.allowed_corporation_ids:
        return True
    return bool(alliance_id and alliance_id in config.allowed_alliance_ids)


def verify_sso_character(
    config: EveSsoConfig,
    *,
    access_token: str,
    token_payload: dict[str, Any] | None = None,
) -> VerifiedPilot:
    token_payload = token_payload or decode_eve_access_token(
        access_token,
        client_id=config.client_id,
        metadata=fetch_sso_metadata(config),
    )
    character_id = character_id_from_sso_payload(token_payload)
    character_info = fetch_esi_character(config, character_id)
    corporation_id = int(character_info.get("corporation_id") or 0)
    if corporation_id <= 0:
        raise CorpIntelError("ESI character endpoint did not return a corporation id.")
    alliance_id = int(character_info["alliance_id"]) if character_info.get("alliance_id") else None
    corporation_info = fetch_esi_corporation(config, corporation_id)
    alliance_info = fetch_esi_alliance(config, alliance_id) if alliance_id else {}
    timestamp = now_iso()
    return VerifiedPilot(
        character_id=character_id,
        character_name=str(token_payload.get("name") or character_info.get("name") or f"Character {character_id}"),
        corporation_id=corporation_id,
        corporation_name=str(corporation_info.get("name") or ""),
        alliance_id=alliance_id,
        alliance_name=str(alliance_info.get("name") or ""),
        owner_hash=str(token_payload.get("owner") or ""),
        scopes=scopes_from_sso_payload(token_payload),
        membership_ok=membership_allowed(config, corporation_id=corporation_id, alliance_id=alliance_id),
        verified_at=timestamp,
        last_login_at=timestamp,
    )


def fetch_remote_watchlist(server_url: str, *, token: str = "", timeout_seconds: float = 10.0) -> IntelWatchlist:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    payload = get_json(f"{server_url.rstrip('/')}/api/watchlist", timeout_seconds=timeout_seconds, headers=headers)
    if not isinstance(payload, dict):
        raise CorpIntelError("Remote watchlist endpoint returned unexpected data.")
    return IntelWatchlist.from_dict(payload)


def refresh_remote_watchlist(
    *,
    server_url: str,
    watchlist_store: WatchlistStore,
    token: str = "",
    timeout_seconds: float,
    log: Callable[[str], None] = print,
) -> None:
    try:
        watchlist = fetch_remote_watchlist(server_url, token=token, timeout_seconds=timeout_seconds)
    except CorpIntelError as exc:
        log(f"Watchlist refresh failed: {exc}")
        return
    watchlist_store.replace(watchlist)
    counts = watchlist.counts()
    log(
        "Loaded watchlist: "
        f"{counts['hostile_pilots']} pilots, "
        f"{counts['hostile_corporations']} corps, "
        f"{counts['help_phrases']} help phrases, "
        f"{counts['keywords']} keywords"
    )


def start_remote_watchlist_refresh_thread(
    *,
    server_url: str,
    watchlist_store: WatchlistStore,
    token: str = "",
    interval_seconds: float,
    timeout_seconds: float,
) -> threading.Thread:
    interval_seconds = max(10.0, interval_seconds)

    def refresh_loop() -> None:
        while True:
            time.sleep(interval_seconds)
            refresh_remote_watchlist(
                server_url=server_url,
                watchlist_store=watchlist_store,
                token=token,
                timeout_seconds=timeout_seconds,
            )

    thread = threading.Thread(target=refresh_loop, daemon=True)
    thread.start()
    return thread


def chunked(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def summarize_counts(events: list[IntelEvent]) -> dict[str, int]:
    counts = {"events": len(events), "critical": 0, "high": 0, "aid": 0, "hostile": 0, "watchlist": 0}
    for event in events:
        if event.severity in {"critical", "high"}:
            counts[event.severity] += 1
        watchlist_hit = False
        for category in event.categories:
            if category in {"aid", "hostile"}:
                counts[category] += 1
            if category.startswith("watchlist-"):
                watchlist_hit = True
        if watchlist_hit:
            counts["watchlist"] += 1
    return counts


def summarize_systems(events: list[IntelEvent]) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for event in events:
        for system in event.systems:
            item = summary.setdefault(
                system,
                {
                    "system": system,
                    "count": 0,
                    "latest_at": "",
                    "severity": "info",
                    "categories": set(),
                    "keywords": set(),
                    "sources": set(),
                },
            )
            item["count"] += 1
            item["latest_at"] = max(str(item["latest_at"]), event.observed_at or event.reported_at)
            item["severity"] = higher_severity(str(item["severity"]), event.severity)
            item["categories"].update(event.categories)
            item["keywords"].update(event.keywords)
            item["sources"].add(event.source)

    result: list[dict[str, Any]] = []
    for item in summary.values():
        result.append(
            {
                "system": item["system"],
                "count": item["count"],
                "latest_at": item["latest_at"],
                "severity": item["severity"],
                "categories": sorted(item["categories"]),
                "keywords": sorted(item["keywords"]),
                "sources": sorted(item["sources"]),
            }
        )
    result.sort(key=lambda item: (SEVERITY_RANK.get(str(item["severity"]), 0), str(item["latest_at"])), reverse=True)
    return result


def start_local_watcher_thread(
    *,
    log_dir: Path,
    channel_filter: ChannelFilter,
    intel_parser: IntelParser,
    store: IntelEventStore,
    source: str,
    poll_seconds: float,
    read_existing: bool,
) -> threading.Thread:
    def on_message(message: ChatMessage) -> None:
        event = intel_parser.analyze(message, source=source)
        if event:
            store.add(event)
            print(format_event_line(event))

    thread = threading.Thread(
        target=watch_chat_logs,
        kwargs={
            "log_dir": log_dir,
            "channel_filter": channel_filter,
            "on_message": on_message,
            "poll_seconds": poll_seconds,
            "read_existing": read_existing,
        },
        daemon=True,
    )
    thread.start()
    return thread


def run_agent(args: argparse.Namespace) -> int:
    channel_filter = channel_filter_from_args(args)
    watchlist_store = WatchlistStore()
    upload_token = args.agent_token or args.token
    if not args.disable_remote_watchlist:
        refresh_remote_watchlist(
            server_url=args.server,
            watchlist_store=watchlist_store,
            token=upload_token,
            timeout_seconds=args.post_timeout,
        )
        start_remote_watchlist_refresh_thread(
            server_url=args.server,
            watchlist_store=watchlist_store,
            token=upload_token,
            interval_seconds=args.watchlist_refresh,
            timeout_seconds=args.post_timeout,
        )
    system_names = load_system_names(refresh=args.refresh_systems)
    intel_parser = IntelParser(system_names, watchlist_store=watchlist_store)
    endpoint = args.server.rstrip("/") + "/api/ingest"

    print("Corp intel agent is read-only.")
    print(f"Pilot/source label: {args.pilot}")
    print(f"Server endpoint: {endpoint}")
    print(f"Channel allowlist: {channel_filter.describe()}")
    if args.disable_remote_watchlist:
        print("Remote watchlist refresh is disabled.")
    else:
        print(f"Remote watchlist refresh: every {max(10.0, args.watchlist_refresh):.0f} seconds.")
    if args.dry_run:
        print("Dry run is on. Matching intel events will be printed but not uploaded.")

    def on_message(message: ChatMessage) -> None:
        event = intel_parser.analyze(message, source=args.pilot)
        if event is None:
            return
        if args.dry_run:
            print(format_event_line(event))
            return
        try:
            post_json(endpoint, event.to_dict(), token=upload_token, timeout_seconds=args.post_timeout)
            print(format_event_line(event))
        except CorpIntelError as exc:
            print(f"Upload failed: {exc}", file=sys.stderr)

    try:
        watch_chat_logs(
            log_dir=args.log_dir,
            channel_filter=channel_filter,
            on_message=on_message,
            poll_seconds=args.poll,
            read_existing=args.read_existing,
        )
    except CorpIntelError as exc:
        print(f"Corp intel error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_server(args: argparse.Namespace) -> int:
    channel_filter = channel_filter_from_args(args)
    watchlist_store = WatchlistStore(args.watchlist_path)
    system_names = load_system_names(refresh=args.refresh_systems)
    event_database = None if args.no_event_db else EventDatabase(args.event_db_path)
    store = IntelEventStore(
        max_events=args.max_events,
        database=event_database,
        retention_days=args.retention_days,
    )
    intel_parser = IntelParser(system_names, watchlist_store=watchlist_store)
    url_host = url_host_for_bind(args.host)
    callback_url = args.sso_callback_url or f"http://{url_host}:{args.port}/auth/callback"
    sso_config = EveSsoConfig(
        client_id=args.sso_client_id,
        client_secret=args.sso_client_secret,
        callback_url=callback_url,
        scopes=parse_csv(args.sso_scopes),
        allowed_corporation_ids=parse_int_csv(args.allowed_corporation_ids),
        allowed_alliance_ids=parse_int_csv(args.allowed_alliance_ids),
        trusted_members_can_edit=args.trusted_members_can_edit_watchlist,
    )
    pilot_registry = PilotRegistry(args.pilot_registry_path)
    auth_state_store = AuthStateStore()
    session_store = SessionStore()

    if args.watch_local:
        start_local_watcher_thread(
            log_dir=args.log_dir,
            channel_filter=channel_filter,
            intel_parser=intel_parser,
            store=store,
            source=args.source,
            poll_seconds=args.poll,
            read_existing=args.read_existing,
        )

    server = build_http_server(
        args.host,
        args.port,
        store,
        ingest_token=args.ingest_token,
        watchlist_store=watchlist_store,
        admin_token=args.admin_token,
        sso_config=sso_config,
        pilot_registry=pilot_registry,
        auth_state_store=auth_state_store,
        session_store=session_store,
        require_verified_ingest=args.require_verified_ingest,
        require_sso_dashboard=args.require_sso_dashboard,
    )
    url = f"http://{url_host}:{args.port}/"
    print(f"Corp intel board listening at {url}")
    print(f"Watchlist file: {args.watchlist_path}")
    if event_database:
        print(f"Event database: {args.event_db_path}")
        print(f"Event retention: {args.retention_days} days, newest {args.max_events} events")
    else:
        print("Event database is disabled; events will be memory-only.")
    if args.ingest_token:
        print("Remote agent uploads require the shared ingest token.")
    if args.admin_token:
        print("Remote watchlist edits require the admin token.")
    else:
        print("Watchlist edits are limited to the host browser unless --admin-token is set.")
    if sso_config.enabled:
        print(f"EVE SSO enabled. Callback URL: {sso_config.callback_url}")
        if sso_config.membership_restricted:
            print(
                "Membership allowlist: "
                f"corps={list(sso_config.allowed_corporation_ids)}, "
                f"alliances={list(sso_config.allowed_alliance_ids)}"
            )
        else:
            print("No corp/alliance allowlist set; any EVE-authenticated character can sign in.")
    else:
        print("EVE SSO is not configured.")
    if args.require_sso_dashboard:
        print("Dashboard/API access requires an EVE SSO verified member session.")
        if not sso_config.enabled:
            print("Warning: --require-sso-dashboard is enabled, but EVE SSO is not configured.")
        elif not sso_config.membership_restricted:
            print(
                "Warning: no corp/alliance allowlist is set; any EVE-authenticated character can view the board."
            )
    if args.host == "0.0.0.0":
        print("LAN mode is enabled. Share your computer's LAN IP and port with opted-in corp members.")
    if args.open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


def build_http_server(
    host: str,
    port: int,
    store: IntelEventStore,
    *,
    ingest_token: str = "",
    watchlist_store: WatchlistStore | None = None,
    admin_token: str = "",
    sso_config: EveSsoConfig | None = None,
    pilot_registry: PilotRegistry | None = None,
    auth_state_store: AuthStateStore | None = None,
    session_store: SessionStore | None = None,
    require_verified_ingest: bool = False,
    require_sso_dashboard: bool = False,
) -> ThreadingHTTPServer:
    watchlist_store = watchlist_store or WatchlistStore()
    sso_config = sso_config or EveSsoConfig()
    pilot_registry = pilot_registry or PilotRegistry(DEFAULT_PILOT_REGISTRY_PATH)
    auth_state_store = auth_state_store or AuthStateStore()
    session_store = session_store or SessionStore()

    class CorpIntelHandler(BaseHTTPRequestHandler):
        server_version = "CorpIntelBoard/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                if require_sso_dashboard:
                    access, pilot = self._dashboard_access()
                    if not access.ok:
                        self._send_html(render_dashboard_access_gate(access, sso_config, pilot), status=access.status)
                        return
                self._send_html(DASHBOARD_HTML)
                return
            if path == "/api/state":
                if not self._require_dashboard_access_for_json():
                    return
                self._send_json(store.snapshot())
                return
            if path == "/api/watchlist":
                if not self._require_dashboard_access_for_json(allow_watchlist_read_token=True):
                    return
                payload = watchlist_store.to_dict()
                pilot = get_request_pilot(self, session_store, pilot_registry)
                payload["can_write"] = request_has_admin_access(
                    self,
                    admin_token,
                    sso_config=sso_config,
                    pilot=pilot,
                )
                self._send_json(payload)
                return
            if path == "/api/me":
                self._send_json(auth_status_payload(self, sso_config, session_store, pilot_registry))
                return
            if path == "/api/agent-tokens":
                if not self._require_dashboard_access_for_json():
                    return
                self._handle_agent_tokens_list()
                return
            if path == "/auth/login":
                self._handle_auth_login()
                return
            if path == "/auth/callback":
                self._handle_auth_callback()
                return
            if path == "/auth/logout":
                self._handle_auth_logout()
                return
            if path == "/api/health":
                self._send_json({"ok": True, "generated_at": now_iso()})
                return
            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/ingest":
                self._handle_ingest()
                return
            if path == "/api/watchlist":
                if not self._require_dashboard_access_for_json():
                    return
                self._handle_watchlist_update()
                return
            if path == "/api/agent-tokens":
                if not self._require_dashboard_access_for_json():
                    return
                self._handle_agent_token_create()
                return
            if path == "/api/agent-tokens/revoke":
                if not self._require_dashboard_access_for_json():
                    return
                self._handle_agent_token_revoke()
                return
            if path == "/auth/logout":
                self._handle_auth_logout()
                return
            self.send_error(404, "Not found")

        def _handle_ingest(self) -> None:
            allowed, pilot = resolve_ingest_pilot(
                self,
                ingest_token=ingest_token,
                pilot_registry=pilot_registry,
                require_verified=require_verified_ingest,
            )
            if not allowed:
                self.send_error(401, "Missing or invalid ingest token")
                return
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_error(400, f"Invalid JSON: {exc}")
                return

            try:
                added = ingest_payload(payload, store, verified_pilot=pilot)
            except (TypeError, ValueError) as exc:
                self.send_error(400, f"Invalid event payload: {exc}")
                return
            self._send_json({"ok": True, "added": added})

        def _handle_agent_tokens_list(self) -> None:
            pilot = get_request_pilot(self, session_store, pilot_registry)
            if not pilot:
                self.send_error(401, "Sign in with EVE SSO first")
                return
            self._send_json(
                {
                    "ok": True,
                    "can_create": pilot.membership_ok,
                    "tokens": [record.to_dict() for record in pilot_registry.list_agent_tokens(pilot.character_id)],
                }
            )

        def _handle_agent_token_create(self) -> None:
            pilot = get_request_pilot(self, session_store, pilot_registry)
            if not pilot:
                self.send_error(401, "Sign in with EVE SSO first")
                return
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_error(400, f"Invalid JSON: {exc}")
                return
            if not isinstance(payload, dict):
                self.send_error(400, "Agent token payload must be a JSON object")
                return
            try:
                token, record = pilot_registry.create_agent_token(
                    pilot.character_id,
                    label=str(payload.get("label") or ""),
                )
            except CorpIntelError as exc:
                self.send_error(403, str(exc))
                return
            self._send_json({"ok": True, "token": token, "record": record.to_dict()})

        def _handle_agent_token_revoke(self) -> None:
            pilot = get_request_pilot(self, session_store, pilot_registry)
            if not pilot:
                self.send_error(401, "Sign in with EVE SSO first")
                return
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_error(400, f"Invalid JSON: {exc}")
                return
            token_id = str(payload.get("token_id") or "") if isinstance(payload, dict) else ""
            if not token_id:
                self.send_error(400, "token_id is required")
                return
            revoked = pilot_registry.revoke_agent_token(character_id=pilot.character_id, token_id=token_id)
            self._send_json({"ok": True, "revoked": revoked})

        def _handle_watchlist_update(self) -> None:
            pilot = get_request_pilot(self, session_store, pilot_registry)
            if not request_has_admin_access(self, admin_token, sso_config=sso_config, pilot=pilot):
                self.send_error(403, "Watchlist edits require local access, admin token, or trusted SSO membership")
                return
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_error(400, f"Invalid JSON: {exc}")
                return
            if not isinstance(payload, dict):
                self.send_error(400, "Watchlist payload must be a JSON object")
                return

            try:
                watchlist = watchlist_store.update(payload, updated_by="dashboard")
            except (OSError, ValueError) as exc:
                self.send_error(500, f"Could not save watchlist: {exc}")
                return
            response = watchlist.to_dict()
            response["counts"] = watchlist.counts()
            response["can_write"] = True
            self._send_json(response)

        def _handle_auth_login(self) -> None:
            if not sso_config.enabled:
                self.send_error(503, "EVE SSO is not configured")
                return
            try:
                state = auth_state_store.create()
                url = build_sso_authorization_url(sso_config, state)
            except CorpIntelError as exc:
                self.send_error(502, str(exc))
                return
            self._redirect(url)

        def _handle_auth_callback(self) -> None:
            if not sso_config.enabled:
                self.send_error(503, "EVE SSO is not configured")
                return
            params = parse_qs(urlparse(self.path).query)
            state = first_query_value(params, "state")
            code = first_query_value(params, "code")
            error = first_query_value(params, "error")
            if error:
                self._send_html(render_auth_result("EVE SSO declined the login request.", ok=False))
                return
            if not state or not auth_state_store.consume(state):
                self.send_error(400, "Invalid or expired SSO state")
                return
            if not code:
                self.send_error(400, "Missing SSO authorization code")
                return
            try:
                token_payload = exchange_sso_code(sso_config, code)
                pilot = verify_sso_character(
                    sso_config,
                    access_token=str(token_payload["access_token"]),
                )
                pilot_registry.upsert(pilot)
                session_id = session_store.create(pilot.character_id)
            except CorpIntelError as exc:
                self._send_html(render_auth_result(str(exc), ok=False))
                return
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", session_cookie_header(session_id))
            self.end_headers()

        def _handle_auth_logout(self) -> None:
            session_id = request_cookie(self, "corp_intel_session")
            if session_id:
                session_store.delete(session_id)
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", clear_session_cookie_header())
            self.end_headers()

        def _read_json_body(self) -> Any:
            body = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
            return json.loads(body.decode("utf-8"))

        def _dashboard_access(self) -> tuple[DashboardAccess, VerifiedPilot | None]:
            pilot = get_request_pilot(self, session_store, pilot_registry)
            return dashboard_access_status(sso_config, pilot), pilot

        def _require_dashboard_access_for_json(self, *, allow_watchlist_read_token: bool = False) -> bool:
            if not require_sso_dashboard:
                return True
            access, _pilot = self._dashboard_access()
            if access.ok:
                return True
            if allow_watchlist_read_token and request_has_watchlist_read_token(
                self,
                ingest_token=ingest_token,
                pilot_registry=pilot_registry,
                require_verified=require_verified_ingest,
            ):
                return True
            self._send_json({"ok": False, "error": access.message}, status=access.status)
            return False

        def log_message(self, format: str, *args: Any) -> None:
            print(f"{self.address_string()} - {format % args}")

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, markup: str, *, status: int = 200) -> None:
            body = markup.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, url: str) -> None:
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()

    return ThreadingHTTPServer((host, port), CorpIntelHandler)


def first_query_value(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0] if values else ""


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_auth_token(handler: BaseHTTPRequestHandler) -> str:
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return handler.headers.get("X-Intel-Token", "").strip()


def request_cookie(handler: BaseHTTPRequestHandler, name: str) -> str:
    raw_cookie = handler.headers.get("Cookie", "")
    for part in raw_cookie.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return ""


def session_cookie_header(session_id: str) -> str:
    return f"corp_intel_session={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={12 * 60 * 60}"


def clear_session_cookie_header() -> str:
    return "corp_intel_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def get_request_pilot(
    handler: BaseHTTPRequestHandler,
    session_store: SessionStore,
    pilot_registry: PilotRegistry,
) -> VerifiedPilot | None:
    session_id = request_cookie(handler, "corp_intel_session")
    if not session_id:
        return None
    character_id = session_store.get(session_id)
    if character_id is None:
        return None
    return pilot_registry.get(character_id)


def auth_status_payload(
    handler: BaseHTTPRequestHandler,
    sso_config: EveSsoConfig,
    session_store: SessionStore,
    pilot_registry: PilotRegistry,
) -> dict[str, Any]:
    pilot = get_request_pilot(handler, session_store, pilot_registry)
    return {
        "generated_at": now_iso(),
        "sso": sso_config.to_public_dict(),
        "authenticated": pilot is not None,
        "pilot": pilot.to_dict() if pilot else None,
    }


def dashboard_access_status(sso_config: EveSsoConfig, pilot: VerifiedPilot | None) -> DashboardAccess:
    if not sso_config.enabled:
        return DashboardAccess(
            ok=False,
            status=503,
            message="EVE SSO is not configured for this intel board.",
        )
    if pilot is None:
        return DashboardAccess(
            ok=False,
            status=401,
            message="Sign in with EVE SSO to view this intel board.",
        )
    if not pilot.membership_ok:
        return DashboardAccess(
            ok=False,
            status=403,
            message="This character is not in the configured corp or alliance allowlist.",
        )
    return DashboardAccess(ok=True, status=200, message="ok")


def render_dashboard_access_gate(
    access: DashboardAccess,
    sso_config: EveSsoConfig,
    pilot: VerifiedPilot | None,
) -> str:
    if not sso_config.enabled:
        detail = (
            "Start the server with EVE SSO client values before enabling "
            "--require-sso-dashboard."
        )
        action = ""
    elif pilot is None:
        detail = "Use your EVE character to prove who is viewing the shared board."
        action = '<p><a class="button" href="/auth/login">Sign in with EVE SSO</a></p>'
    else:
        detail = (
            f"Signed in as {html.escape(pilot.character_name)} from "
            f"{html.escape(pilot.corporation_name or str(pilot.corporation_id))}."
        )
        action = '<p><a class="button secondary" href="/auth/logout">Sign out</a></p>'

    if sso_config.membership_restricted:
        rule = "Access is limited to the configured corporation or alliance ids."
    else:
        rule = "No corp or alliance allowlist is configured; any EVE-authenticated character can pass SSO."

    title = "Corp Intel Board Access"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 0; color: #1f2528; background: #eef2f1; }}
    main {{ max-width: 720px; margin: 48px auto; background: #fff; border: 1px solid #d8ddd8; border-radius: 8px; padding: 24px; }}
    h1 {{ margin-top: 0; font-size: 1.6rem; }}
    p {{ line-height: 1.5; }}
    .status {{ color: #8b321f; font-weight: 700; }}
    .rule {{ color: #566167; }}
    .button {{ display: inline-block; padding: 10px 14px; border-radius: 6px; background: #224e5f; color: #fff; text-decoration: none; font-weight: 700; }}
    .secondary {{ background: #5b6367; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p class="status">{html.escape(access.message)}</p>
    <p>{detail}</p>
    <p class="rule">{rule}</p>
    {action}
  </main>
</body>
</html>"""


def render_auth_result(message: str, *, ok: bool) -> str:
    title = "EVE SSO Login" if ok else "EVE SSO Login Failed"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 32px; color: #1f2528; background: #eef2f1; }}
    main {{ max-width: 680px; background: #fff; border: 1px solid #d8ddd8; border-radius: 8px; padding: 20px; }}
    a {{ color: #224e5f; font-weight: 700; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(message)}</p>
    <p><a href="/">Return to the intel board</a></p>
  </main>
</body>
</html>"""


def request_has_token(handler: BaseHTTPRequestHandler, expected: str) -> bool:
    return bool(expected and request_auth_token(handler) == expected)


def resolve_ingest_pilot(
    handler: BaseHTTPRequestHandler,
    *,
    ingest_token: str,
    pilot_registry: PilotRegistry,
    require_verified: bool,
) -> tuple[bool, VerifiedPilot | None]:
    token = request_auth_token(handler)
    if token:
        pilot = pilot_registry.resolve_agent_token(token)
        if pilot:
            return True, pilot
        if ingest_token and token == ingest_token and not require_verified:
            return True, None
        return False, None
    if require_verified:
        return False, None
    if ingest_token:
        return False, None
    return True, None


def request_has_watchlist_read_token(
    handler: BaseHTTPRequestHandler,
    *,
    ingest_token: str,
    pilot_registry: PilotRegistry,
    require_verified: bool,
) -> bool:
    token = request_auth_token(handler)
    if not token:
        return False
    if pilot_registry.resolve_agent_token(token):
        return True
    return bool(ingest_token and token == ingest_token and not require_verified)


def request_has_admin_access(
    handler: BaseHTTPRequestHandler,
    admin_token: str,
    *,
    sso_config: EveSsoConfig | None = None,
    pilot: VerifiedPilot | None = None,
) -> bool:
    if request_is_loopback(handler):
        return True
    if not admin_token:
        return bool(sso_config and sso_config.trusted_members_can_edit and pilot and pilot.membership_ok)
    auth = handler.headers.get("Authorization", "")
    token = handler.headers.get("X-Admin-Token", "") or handler.headers.get("X-Intel-Token", "")
    if auth == f"Bearer {admin_token}" or token == admin_token:
        return True
    return bool(sso_config and sso_config.trusted_members_can_edit and pilot and pilot.membership_ok)


def request_is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    host = str(handler.client_address[0])
    return host == "::1" or host.startswith("127.")


def ingest_payload(payload: Any, store: IntelEventStore, *, verified_pilot: VerifiedPilot | None = None) -> int:
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        events = payload["events"]
    else:
        events = [payload]
    added = 0
    for item in events:
        if not isinstance(item, dict):
            raise TypeError("Event must be a JSON object.")
        event = IntelEvent.from_dict(item)
        if not event.message:
            raise ValueError("Event message is required.")
        if verified_pilot:
            event = replace(
                event,
                source=verified_pilot.character_name,
                verified_character_id=verified_pilot.character_id,
                verified_character_name=verified_pilot.character_name,
                verified_corporation_id=verified_pilot.corporation_id,
                verified_corporation_name=verified_pilot.corporation_name,
            )
        store.add(event)
        print(format_event_line(event))
        added += 1
    return added


def format_event_line(event: IntelEvent) -> str:
    systems = ", ".join(event.systems) if event.systems else "no system"
    keywords = ", ".join(event.keywords) if event.keywords else "no keywords"
    speaker = f"{event.speaker}: " if event.speaker else ""
    return f"[{event.severity.upper()}] {systems} | {event.channel} | {event.source} | {keywords} | {speaker}{event.message}"


def channel_filter_from_args(args: argparse.Namespace) -> ChannelFilter:
    channels = parse_csv(args.channels)
    if not args.all_channels and not channels:
        raise CorpIntelError("Choose channels with --channels or explicitly pass --all-channels.")
    return ChannelFilter(channels, all_channels=args.all_channels)


def parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_int_csv(value: str | None) -> tuple[int, ...]:
    result: list[int] = []
    for item in parse_csv(value):
        try:
            result.append(int(item))
        except ValueError as exc:
            raise CorpIntelError(f"Expected a numeric id, got {item!r}.") from exc
    return tuple(result)


def url_host_for_bind(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", ""} else host


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a read-only EVE corp intel board from opt-in chat logs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start the web dashboard and optional local log watcher.")
    add_common_watch_args(serve)
    serve.add_argument("--host", default="127.0.0.1", help="Bind address. Use 0.0.0.0 for LAN sharing.")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Dashboard port.")
    serve.add_argument("--watch-local", action="store_true", help="Watch this computer's chat logs too.")
    serve.add_argument("--source", default=os.environ.get("USERNAME", "local"), help="Source label for local events.")
    serve.add_argument("--ingest-token", default="", help="Shared token required for remote agent uploads.")
    serve.add_argument(
        "--require-verified-ingest",
        action="store_true",
        help="Require remote uploads to use a valid SSO-created agent token.",
    )
    serve.add_argument(
        "--admin-token",
        default=os.environ.get("CORP_INTEL_ADMIN_TOKEN", ""),
        help="Token required for remote watchlist edits. The host browser can edit without it.",
    )
    serve.add_argument(
        "--watchlist-path",
        type=Path,
        default=DEFAULT_WATCHLIST_PATH,
        help="Local JSON file used to persist dashboard watchlist settings.",
    )
    serve.add_argument(
        "--event-db-path",
        type=Path,
        default=DEFAULT_EVENT_DB_PATH,
        help="Local SQLite file used to persist recent intel events.",
    )
    serve.add_argument(
        "--no-event-db",
        action="store_true",
        help="Disable event persistence and keep intel events in memory only.",
    )
    serve.add_argument(
        "--pilot-registry-path",
        type=Path,
        default=DEFAULT_PILOT_REGISTRY_PATH,
        help="Local SQLite file used to store verified EVE SSO pilot records.",
    )
    serve.add_argument(
        "--sso-client-id",
        default=os.environ.get("CORP_INTEL_SSO_CLIENT_ID", ""),
        help="EVE SSO application client id. Can also be set with CORP_INTEL_SSO_CLIENT_ID.",
    )
    serve.add_argument(
        "--sso-client-secret",
        default=os.environ.get("CORP_INTEL_SSO_CLIENT_SECRET", ""),
        help="EVE SSO application secret. Can also be set with CORP_INTEL_SSO_CLIENT_SECRET.",
    )
    serve.add_argument(
        "--sso-callback-url",
        default=os.environ.get("CORP_INTEL_SSO_CALLBACK_URL", ""),
        help="Registered EVE SSO callback URL. Defaults to this board's /auth/callback URL.",
    )
    serve.add_argument(
        "--sso-scopes",
        default=os.environ.get("CORP_INTEL_SSO_SCOPES", ""),
        help="Comma-separated EVE SSO scopes. Leave empty for character identity only.",
    )
    serve.add_argument(
        "--allowed-corporation-ids",
        default=os.environ.get("CORP_INTEL_ALLOWED_CORPORATION_IDS", ""),
        help="Comma-separated corporation ids allowed to sign in as trusted corp members.",
    )
    serve.add_argument(
        "--allowed-alliance-ids",
        default=os.environ.get("CORP_INTEL_ALLOWED_ALLIANCE_IDS", ""),
        help="Comma-separated alliance ids allowed to sign in as trusted members.",
    )
    serve.add_argument(
        "--trusted-members-can-edit-watchlist",
        action="store_true",
        help="Allow SSO-verified members in the configured corp/alliance allowlist to edit watchlists.",
    )
    serve.add_argument(
        "--require-sso-dashboard",
        action="store_true",
        help=(
            "Require EVE SSO sign-in for dashboard/API views. Use allowed corp/alliance ids "
            "to make this member-only."
        ),
    )
    serve.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_EVENT_RETENTION_DAYS,
        help="Days of persisted intel events to keep.",
    )
    serve.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS, help="Maximum events retained by the board.")
    serve.add_argument("--open-browser", action="store_true", help="Open the dashboard in your default browser.")
    serve.set_defaults(func=run_server)

    agent = subparsers.add_parser("agent", help="Watch local logs and upload matching intel to a dashboard server.")
    add_common_watch_args(agent)
    agent.add_argument("--server", required=True, help="Dashboard server URL, like http://1.2.3.4:8765")
    agent.add_argument("--token", default="", help="Shared ingest token from the dashboard server.")
    agent.add_argument(
        "--agent-token",
        default=os.environ.get("CORP_INTEL_AGENT_TOKEN", ""),
        help="Per-pilot upload token generated after EVE SSO login. Can also be set with CORP_INTEL_AGENT_TOKEN.",
    )
    agent.add_argument("--pilot", default=os.environ.get("USERNAME", "pilot"), help="Pilot/source label shown on events.")
    agent.add_argument("--dry-run", action="store_true", help="Print matching events without uploading.")
    agent.add_argument("--post-timeout", type=float, default=10.0, help="Seconds to wait for upload responses.")
    agent.add_argument(
        "--watchlist-refresh",
        type=float,
        default=DEFAULT_WATCHLIST_REFRESH_SECONDS,
        help="Seconds between shared watchlist refreshes from the dashboard server.",
    )
    agent.add_argument(
        "--disable-remote-watchlist",
        action="store_true",
        help="Do not fetch the shared watchlist; only built-in hostile/help phrases are matched.",
    )
    agent.set_defaults(func=run_agent)

    return parser


def add_common_watch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-dir", type=Path, default=default_chat_log_dir(), help="EVE Chatlogs folder.")
    parser.add_argument(
        "--channels",
        default=DEFAULT_CHANNELS,
        help="Comma-separated channel allowlist. Wildcards are allowed, like *Intel*.",
    )
    parser.add_argument("--all-channels", action="store_true", help="Allow all chat log channels. Use carefully.")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS, help="Seconds between log checks.")
    parser.add_argument("--read-existing", action="store_true", help="Process existing lines instead of only new lines.")
    parser.add_argument("--refresh-systems", action="store_true", help="Refresh solar system names from public ESI.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except CorpIntelError as exc:
        print(f"Corp intel error: {exc}", file=sys.stderr)
        return 1


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Corp Intel Board</title>
  <style>
    :root {
      --bg: #eef2f1;
      --panel: #ffffff;
      --ink: #1f2528;
      --muted: #667078;
      --line: #d8ddd8;
      --line-strong: #b8c1bd;
      --critical: #b42318;
      --high: #b05d00;
      --medium: #276a73;
      --info: #4d6678;
      --green: #2f6f54;
      --blue: #285f91;
      --accent: #224e5f;
      --accent-soft: #e4eef1;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      padding: 16px 22px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfc;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      line-height: 1.2;
      font-weight: 700;
    }
    .subtitle {
      margin-top: 3px;
      color: var(--muted);
      font-size: 13px;
    }
    .status {
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    .header-actions {
      display: grid;
      gap: 8px;
      justify-items: end;
    }
    .auth-panel {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
      color: var(--muted);
      font-size: 13px;
      text-align: right;
      flex-wrap: wrap;
    }
    .auth-panel a {
      color: var(--accent);
      font-weight: 700;
      text-decoration: none;
    }
    .auth-panel button {
      min-height: 30px;
      padding: 5px 9px;
      font-size: 12px;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--green);
      display: inline-block;
    }
    main {
      display: grid;
      grid-template-columns: minmax(260px, 360px) 1fr;
      gap: 18px;
      padding: 18px;
    }
    section {
      min-width: 0;
    }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric, .panel, .event {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric {
      padding: 12px;
      min-height: 76px;
    }
    .metric strong {
      display: block;
      font-size: 24px;
      line-height: 1.2;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }
    .panel {
      overflow: hidden;
    }
    .panel h2 {
      margin: 0;
      padding: 12px 14px;
      font-size: 15px;
      border-bottom: 1px solid var(--line);
      background: #f8faf9;
    }
    .panel-body {
      padding: 14px;
    }
    .panel + .panel {
      margin-top: 16px;
    }
    .field {
      display: grid;
      gap: 6px;
      margin-bottom: 12px;
    }
    .field label, label.field {
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
    }
    input, select, textarea, button {
      font: inherit;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line-strong);
      border-radius: 7px;
      background: #fff;
      color: var(--ink);
      min-height: 36px;
      padding: 8px 10px;
      font-size: 13px;
      line-height: 1.3;
    }
    textarea {
      min-height: 76px;
      resize: vertical;
    }
    input:focus, select:focus, textarea:focus {
      outline: 2px solid var(--accent-soft);
      border-color: var(--accent);
    }
    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 36px;
      border: 1px solid var(--accent);
      border-radius: 7px;
      background: var(--accent);
      color: #fff;
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary {
      background: #fff;
      color: var(--accent);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.65;
    }
    .filter-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .filter-grid .wide {
      grid-column: 1 / -1;
    }
    .button-row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 2px;
    }
    .save-status {
      color: var(--muted);
      font-size: 12px;
    }
    .signal-list, .trust-list {
      display: grid;
    }
    .signal-item, .trust-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 10px 14px;
      border-bottom: 1px solid var(--line);
    }
    .signal-item:last-child, .trust-item:last-child {
      border-bottom: 0;
    }
    .signal-label, .trust-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .signal-value, .trust-value {
      margin-top: 3px;
      font-size: 17px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .signal-detail, .trust-detail {
      color: var(--muted);
      font-size: 12px;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }
    .panel-heading {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }
    .panel-heading span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .watchlist-counts {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      margin-bottom: 12px;
    }
    .watchlist-counts span {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 7px;
      background: #f8faf9;
      color: var(--muted);
      font-size: 12px;
      text-align: center;
    }
    .token-list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .token-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px;
      background: #f8faf9;
    }
    .token-output {
      margin-top: 10px;
    }
    .system-list {
      display: grid;
    }
    .system {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 11px 14px;
      border-bottom: 1px solid var(--line);
    }
    .system > div {
      min-width: 0;
    }
    .system:last-child {
      border-bottom: 0;
    }
    .system-name {
      font-weight: 700;
    }
    .system-topline {
      display: flex;
      gap: 8px;
      align-items: baseline;
      flex-wrap: wrap;
    }
    .system-count {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .details {
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      color: #fff;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      justify-self: start;
      white-space: nowrap;
    }
    .system .badge {
      justify-self: end;
      align-self: start;
    }
    .badge.fresh { background: var(--green); }
    .badge.aging { background: var(--high); }
    .badge.stale { background: var(--critical); }
    .badge.verified { background: var(--green); }
    .badge.label { background: var(--info); }
    .critical { background: var(--critical); }
    .high { background: var(--high); }
    .medium { background: var(--medium); }
    .info { background: var(--info); }
    .event-list {
      display: grid;
      gap: 10px;
    }
    .event {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
      padding: 12px;
    }
    .event-badges {
      display: flex;
      gap: 6px;
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .event-message {
      font-size: 15px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .event-meta-row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 6px;
    }
    .event-meta {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .event-meta-row + .event-meta, .pill-list {
      margin-top: 5px;
    }
    .event-side {
      display: grid;
      justify-items: end;
      gap: 6px;
      min-width: 120px;
    }
    .event-time {
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .pill-list {
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
    }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #f8faf9;
      color: var(--muted);
      padding: 3px 7px;
      font-size: 12px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }
    .empty {
      padding: 18px;
      color: var(--muted);
      font-size: 14px;
    }
    @media (max-width: 860px) {
      header {
        align-items: flex-start;
        flex-direction: column;
      }
      main {
        grid-template-columns: 1fr;
      }
      .event {
        grid-template-columns: 1fr;
      }
      .event-side {
        justify-items: start;
        min-width: 0;
      }
    }
    @media (max-width: 520px) {
      .metric-row {
        grid-template-columns: 1fr;
      }
      .filter-grid, .watchlist-counts {
        grid-template-columns: 1fr;
      }
      .signal-item, .trust-item {
        grid-template-columns: 1fr;
      }
      .system {
        grid-template-columns: 1fr;
      }
      .system .badge {
        justify-self: start;
      }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Corp Intel Board</h1>
      <div class="subtitle">Read-only chat intel from opted-in pilots</div>
    </div>
    <div class="header-actions">
      <div class="status"><span class="dot"></span><span id="status">Connecting</span></div>
      <div class="auth-panel" id="auth-panel">Checking identity</div>
    </div>
  </header>
  <main>
    <section>
      <div class="metric-row">
        <div class="metric"><strong id="count-critical">0</strong><span>critical aid calls</span></div>
        <div class="metric"><strong id="count-high">0</strong><span>high hostile reports</span></div>
        <div class="metric"><strong id="count-aid">0</strong><span>aid matches</span></div>
        <div class="metric"><strong id="count-hostile">0</strong><span>hostile matches</span></div>
        <div class="metric"><strong id="count-watchlist">0</strong><span>watchlist hits</span></div>
      </div>
      <div class="panel">
        <h2 class="panel-heading">Freshness <span id="freshness-summary">Waiting for snapshot</span></h2>
        <div class="signal-list">
          <div class="signal-item">
            <div>
              <div class="signal-label">Latest intel</div>
              <div class="signal-value" id="fresh-latest">No retained intel</div>
              <div class="signal-detail" id="fresh-latest-detail">Waiting for hostile reports or aid calls.</div>
            </div>
            <span class="badge info" id="fresh-badge">idle</span>
          </div>
          <div class="signal-item">
            <div>
              <div class="signal-label">Snapshot</div>
              <div class="signal-value" id="fresh-snapshot">Connecting</div>
              <div class="signal-detail" id="fresh-snapshot-detail">Polling /api/state.</div>
            </div>
          </div>
          <div class="signal-item">
            <div>
              <div class="signal-label">Retained window</div>
              <div class="signal-value" id="fresh-retained">0 events</div>
              <div class="signal-detail" id="fresh-retained-detail">Recent operational intel only.</div>
            </div>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>Filters</h2>
        <div class="panel-body">
          <div class="filter-grid">
            <label class="field wide">Search
              <input id="filter-search" type="search" autocomplete="off">
            </label>
            <label class="field">Severity
              <select id="filter-severity">
                <option value="">Any</option>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="info">Info</option>
              </select>
            </label>
            <label class="field">Category
              <select id="filter-category">
                <option value="">Any</option>
                <option value="aid">Aid</option>
                <option value="hostile">Hostile</option>
                <option value="watchlist-pilot">Watched pilot</option>
                <option value="watchlist-corporation">Watched corporation</option>
                <option value="watchlist-help">Watched help phrase</option>
                <option value="watchlist-keyword">Watched keyword</option>
              </select>
            </label>
          </div>
          <div class="button-row">
            <button class="secondary" id="clear-filters" type="button">Clear filters</button>
            <span class="save-status" id="filter-status"></span>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2 class="panel-heading">Hot Systems <span id="systems-summary">0 systems</span></h2>
        <div class="system-list" id="systems"></div>
      </div>
      <div class="panel">
        <h2 class="panel-heading">Source Trust <span id="trust-summary">Pending</span></h2>
        <div class="trust-list">
          <div class="trust-item">
            <div>
              <div class="trust-label">Verified ingest</div>
              <div class="trust-value" id="trust-verified">0 events</div>
              <div class="trust-detail" id="trust-verified-detail">No verified pilot events in the retained snapshot.</div>
            </div>
            <span class="badge label" id="trust-badge">labels</span>
          </div>
          <div class="trust-item">
            <div>
              <div class="trust-label">Source labels</div>
              <div class="trust-value" id="trust-labels">0 labels</div>
              <div class="trust-detail" id="trust-labels-detail">Uploader/source labels are shown without local log paths.</div>
            </div>
          </div>
          <div class="trust-item">
            <div>
              <div class="trust-label">Channels</div>
              <div class="trust-value" id="trust-channels">0 channels</div>
              <div class="trust-detail" id="trust-channels-detail">No channel-linked events yet.</div>
            </div>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>Watchlist</h2>
        <div class="panel-body">
          <div class="watchlist-counts">
            <span><strong id="watch-count-pilots">0</strong> pilots</span>
            <span><strong id="watch-count-corps">0</strong> corps</span>
            <span><strong id="watch-count-help">0</strong> help</span>
            <span><strong id="watch-count-keywords">0</strong> keywords</span>
          </div>
          <label class="field">Admin token
            <input id="admin-token" type="password" autocomplete="off">
          </label>
          <label class="field">Hostile pilots
            <textarea id="watch-hostile-pilots" spellcheck="false"></textarea>
          </label>
          <label class="field">Hostile corporations
            <textarea id="watch-hostile-corporations" spellcheck="false"></textarea>
          </label>
          <label class="field">Help callouts
            <textarea id="watch-help-phrases" spellcheck="false"></textarea>
          </label>
          <label class="field">Extra keywords
            <textarea id="watch-keywords" spellcheck="false"></textarea>
          </label>
          <div class="button-row">
            <button id="save-watchlist" type="button">Save watchlist</button>
            <span class="save-status" id="watchlist-status"></span>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>Agent Upload</h2>
        <div class="panel-body">
          <label class="field">Token label
            <input id="agent-token-label" type="text" autocomplete="off">
          </label>
          <div class="button-row">
            <button id="create-agent-token" type="button">Create token</button>
            <span class="save-status" id="agent-token-status"></span>
          </div>
          <textarea class="token-output" id="new-agent-token" readonly spellcheck="false"></textarea>
          <div class="token-list" id="agent-token-list"></div>
        </div>
      </div>
    </section>
    <section>
      <div class="panel">
        <h2 class="panel-heading">Live Intel <span id="events-summary">Waiting for events</span></h2>
        <div class="event-list" id="events"></div>
      </div>
    </section>
  </main>
  <script>
    const state = {
      events: [],
      watchlist: null
    };

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }

    function ageLabel(value) {
      const time = Date.parse(value);
      if (!Number.isFinite(time)) return "unknown age";
      const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
      if (seconds < 60) return `${seconds}s ago`;
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      return `${Math.floor(minutes / 60)}h ago`;
    }

    function timestampIso(value) {
      const time = Date.parse(value);
      if (!Number.isFinite(time)) return "unknown timestamp";
      return new Date(time).toISOString();
    }

    function plural(count, singular, pluralLabel) {
      return `${count} ${count === 1 ? singular : (pluralLabel || `${singular}s`)}`;
    }

    function listPreview(values, fallback, limit = 4) {
      const clean = (values || []).map(value => String(value || "").trim()).filter(Boolean);
      if (!clean.length) return fallback;
      const visible = clean.slice(0, limit).join(", ");
      const hidden = clean.length - limit;
      return hidden > 0 ? `${visible} +${hidden} more` : visible;
    }

    const severityClasses = new Set(["critical", "high", "medium", "info"]);

    function severityClass(value) {
      return severityClasses.has(value) ? value : "info";
    }

    function eventTimestamp(event) {
      return event.observed_at || event.reported_at || "";
    }

    function newestEvent(events) {
      let newest = null;
      let newestTime = -Infinity;
      for (const event of events || []) {
        const time = Date.parse(eventTimestamp(event));
        if (Number.isFinite(time) && time > newestTime) {
          newest = event;
          newestTime = time;
        }
      }
      return newest;
    }

    function freshnessFor(value) {
      const time = Date.parse(value);
      if (!Number.isFinite(time)) return { label: "idle", className: "info" };
      const minutes = Math.floor(Math.max(0, Date.now() - time) / 60000);
      if (minutes < 10) return { label: "fresh", className: "fresh" };
      if (minutes < 60) return { label: "aging", className: "aging" };
      return { label: "stale", className: "stale" };
    }

    function isVerifiedEvent(event) {
      return Boolean(event.verified_character_id || event.verified_corporation_id);
    }

    function sourceDisplay(event) {
      if (event.verified_character_name) {
        return event.verified_corporation_name
          ? `${event.verified_character_name} / ${event.verified_corporation_name}`
          : event.verified_character_name;
      }
      return event.source || "unknown source";
    }

    function sourceTrustLabel(event) {
      return isVerifiedEvent(event) ? "verified" : "label";
    }

    function renderCounts(counts) {
      document.getElementById("count-critical").textContent = counts.critical ?? 0;
      document.getElementById("count-high").textContent = counts.high ?? 0;
      document.getElementById("count-aid").textContent = counts.aid ?? 0;
      document.getElementById("count-hostile").textContent = counts.hostile ?? 0;
      document.getElementById("count-watchlist").textContent = counts.watchlist ?? 0;
    }

    function renderFreshness(payload) {
      const events = payload.events || [];
      const counts = payload.counts || {};
      const latest = newestEvent(events);
      const badge = document.getElementById("fresh-badge");
      const retained = counts.events ?? events.length;
      document.getElementById("fresh-retained").textContent = plural(retained, "event");
      document.getElementById("fresh-retained-detail").textContent =
        `${counts.critical ?? 0} critical, ${counts.high ?? 0} high, ${counts.watchlist ?? 0} watchlist hits`;

      if (latest) {
        const timestamp = eventTimestamp(latest);
        const freshness = freshnessFor(timestamp);
        document.getElementById("fresh-latest").textContent = ageLabel(timestamp);
        document.getElementById("fresh-latest-detail").textContent =
          `${listPreview(latest.systems, "No system")} - ${sourceDisplay(latest)} - ${timestampIso(timestamp)}`;
        document.getElementById("freshness-summary").textContent = `${freshness.label} - ${ageLabel(timestamp)}`;
        badge.textContent = freshness.label;
        badge.className = `badge ${freshness.className}`;
      } else {
        document.getElementById("fresh-latest").textContent = "No retained intel";
        document.getElementById("fresh-latest-detail").textContent = "Waiting for hostile reports or aid calls.";
        document.getElementById("freshness-summary").textContent = "No retained intel";
        badge.textContent = "idle";
        badge.className = "badge info";
      }

      document.getElementById("fresh-snapshot").textContent = payload.generated_at
        ? ageLabel(payload.generated_at)
        : "unknown age";
      document.getElementById("fresh-snapshot-detail").textContent = payload.generated_at
        ? `${timestampIso(payload.generated_at)} from /api/state`
        : "Snapshot timestamp unavailable.";
    }

    function renderTrust(events) {
      events = events || [];
      const verifiedEvents = events.filter(isVerifiedEvent);
      const sources = new Set(events.map(event => event.source).filter(Boolean));
      const channels = new Set(events.map(event => event.channel).filter(Boolean));
      const coverage = events.length ? Math.round((verifiedEvents.length / events.length) * 100) : 0;
      const badge = document.getElementById("trust-badge");

      document.getElementById("trust-summary").textContent = events.length
        ? `${coverage}% verified`
        : "No events";
      document.getElementById("trust-verified").textContent = plural(verifiedEvents.length, "event");
      document.getElementById("trust-verified-detail").textContent = events.length
        ? `${coverage}% of retained events include verified EVE identity fields.`
        : "No verified pilot events in the retained snapshot.";
      document.getElementById("trust-labels").textContent = plural(sources.size, "label");
      document.getElementById("trust-labels-detail").textContent = sources.size
        ? listPreview([...sources], "No source labels yet.", 3)
        : "Uploader/source labels are shown without local log paths.";
      document.getElementById("trust-channels").textContent = plural(channels.size, "channel");
      document.getElementById("trust-channels-detail").textContent = channels.size
        ? listPreview([...channels], "No channel-linked events yet.", 3)
        : "No channel-linked events yet.";
      badge.textContent = verifiedEvents.length ? "verified" : "labels";
      badge.className = verifiedEvents.length ? "badge verified" : "badge label";
    }

    function renderSystems(systems) {
      const target = document.getElementById("systems");
      document.getElementById("systems-summary").textContent = plural(systems.length, "system");
      if (!systems.length) {
        target.innerHTML = `<div class="empty">No system-linked intel yet.</div>`;
        return;
      }
      target.innerHTML = systems.slice(0, 20).map(item => `
        <div class="system">
          <div>
            <div class="system-topline">
              <span class="system-name">${escapeHtml(item.system)}</span>
              <span class="system-count">${plural(item.count ?? 0, "event")}</span>
            </div>
            <div class="details">Matched: ${escapeHtml(listPreview(item.keywords, "No keywords", 5))}</div>
            <div class="details">Sources: ${escapeHtml(listPreview(item.sources, "No source labels", 4))} - latest ${ageLabel(item.latest_at)}</div>
          </div>
          <span class="badge ${severityClass(item.severity)}">${escapeHtml(item.severity || "info")}</span>
        </div>
      `).join("");
    }

    function activeFilters() {
      return {
        search: document.getElementById("filter-search").value.trim().toLowerCase(),
        severity: document.getElementById("filter-severity").value,
        category: document.getElementById("filter-category").value
      };
    }

    function eventMatchesFilters(event, filters) {
      if (filters.severity && event.severity !== filters.severity) return false;
      const categories = event.categories || [];
      if (filters.category && !categories.includes(filters.category)) return false;
      if (!filters.search) return true;
      const haystack = [
        event.message,
        event.channel,
        event.source,
        event.speaker,
        event.verified_character_name,
        event.verified_corporation_name,
        ...(event.systems || []),
        ...(event.keywords || []),
        ...categories
      ].join(" ").toLowerCase();
      return haystack.includes(filters.search);
    }

    function renderEvents(events) {
      const target = document.getElementById("events");
      const filters = activeFilters();
      const filtered = events.filter(event => eventMatchesFilters(event, filters));
      document.getElementById("filter-status").textContent =
        events.length ? `${filtered.length} of ${events.length} shown` : "";
      document.getElementById("events-summary").textContent = events.length
        ? `${filtered.length} shown / ${events.length} retained`
        : "Waiting for events";
      if (!filtered.length) {
        const label = events.length ? "No intel matches current filters." : "Waiting for hostile reports or aid calls.";
        target.innerHTML = `<div class="empty">${label}</div>`;
        return;
      }
      target.innerHTML = filtered.slice(0, 100).map(event => {
        const timestamp = eventTimestamp(event);
        const systems = listPreview(event.systems, "No system", 4);
        const keywords = listPreview(event.keywords, "No matched keyword", 5);
        const categories = (event.categories || []).map(category =>
          `<span class="pill">${escapeHtml(category)}</span>`
        ).join("") || `<span class="pill">uncategorized</span>`;
        const trust = sourceTrustLabel(event);
        return `
          <div class="event">
            <div class="event-badges">
              <span class="badge ${severityClass(event.severity)}">${escapeHtml(event.severity || "info")}</span>
              <span class="badge ${trust}">${trust}</span>
            </div>
            <div>
              <div class="event-message">${escapeHtml(event.message)}</div>
              <div class="event-meta-row">
                <span class="event-meta">${escapeHtml(systems)}</span>
                <span class="event-meta">${escapeHtml(event.channel || "unknown channel")}</span>
                <span class="event-meta">${escapeHtml(sourceDisplay(event))}</span>
              </div>
              <div class="event-meta">Matched: ${escapeHtml(keywords)}</div>
              <div class="pill-list">${categories}</div>
            </div>
            <div class="event-side">
              <div class="event-time">${ageLabel(timestamp)}</div>
              <div class="event-meta">${escapeHtml(timestampIso(timestamp))}</div>
            </div>
          </div>
        `;
      }).join("");
    }

    function linesFromTextarea(id) {
      return document.getElementById(id).value
        .split(/\r?\n/)
        .map(value => value.trim())
        .filter(Boolean);
    }

    function textareaText(values) {
      return (values || []).join("\n");
    }

    function renderWatchlist(payload) {
      state.watchlist = payload;
      document.getElementById("watch-hostile-pilots").value = textareaText(payload.hostile_pilots);
      document.getElementById("watch-hostile-corporations").value = textareaText(payload.hostile_corporations);
      document.getElementById("watch-help-phrases").value = textareaText(payload.help_phrases);
      document.getElementById("watch-keywords").value = textareaText(payload.keywords);
      const counts = payload.counts || {};
      document.getElementById("watch-count-pilots").textContent = counts.hostile_pilots ?? 0;
      document.getElementById("watch-count-corps").textContent = counts.hostile_corporations ?? 0;
      document.getElementById("watch-count-help").textContent = counts.help_phrases ?? 0;
      document.getElementById("watch-count-keywords").textContent = counts.keywords ?? 0;
      document.getElementById("watchlist-status").textContent = payload.updated_at
        ? `Updated ${ageLabel(payload.updated_at)}`
        : "No saved watchlist yet";
      const saveButton = document.getElementById("save-watchlist");
      saveButton.disabled = payload.can_write === false;
      if (payload.can_write === false) {
        document.getElementById("watchlist-status").textContent = "Sign in or use an admin token to edit";
      }
    }

    function renderAuth(payload) {
      const target = document.getElementById("auth-panel");
      const sso = payload.sso || {};
      if (!sso.enabled) {
        target.textContent = "EVE SSO not configured";
        return;
      }
      if (!payload.authenticated) {
        target.innerHTML = `<a href="/auth/login">Log in with EVE</a>`;
        return;
      }
      const pilot = payload.pilot || {};
      const trust = pilot.membership_ok ? "trusted" : "not in allowlist";
      target.innerHTML = `
        <span>${escapeHtml(pilot.character_name || "EVE pilot")} - ${escapeHtml(pilot.corporation_name || String(pilot.corporation_id || ""))} - ${trust}</span>
        <button class="secondary" type="button" id="logout-button">Log out</button>
      `;
      document.getElementById("logout-button").addEventListener("click", () => {
        window.location.href = "/auth/logout";
      });
    }

    function renderAgentTokens(payload) {
      const status = document.getElementById("agent-token-status");
      const createButton = document.getElementById("create-agent-token");
      const list = document.getElementById("agent-token-list");
      if (!payload || payload.ok !== true) {
        status.textContent = "Sign in with EVE to create upload tokens";
        createButton.disabled = true;
        list.innerHTML = "";
        return;
      }
      createButton.disabled = payload.can_create === false;
      status.textContent = payload.can_create === false ? "Your character is not in the allowlist" : "";
      const tokens = payload.tokens || [];
      if (!tokens.length) {
        list.innerHTML = `<div class="empty">No agent tokens yet.</div>`;
        return;
      }
      list.innerHTML = tokens.map(token => `
        <div class="token-row">
          <div>
            <div class="system-name">${escapeHtml(token.label || "Chatlog agent")}</div>
            <div class="details">created ${ageLabel(token.created_at)}${token.last_used_at ? ` - used ${ageLabel(token.last_used_at)}` : ""}${token.revoked_at ? ` - revoked ${ageLabel(token.revoked_at)}` : ""}</div>
          </div>
          <button class="secondary revoke-token" type="button" data-token-id="${escapeHtml(token.token_id)}" ${token.revoked_at ? "disabled" : ""}>Revoke</button>
        </div>
      `).join("");
      for (const button of document.querySelectorAll(".revoke-token")) {
        button.addEventListener("click", () => revokeAgentToken(button.dataset.tokenId));
      }
    }

    function authHeaders() {
      const token = document.getElementById("admin-token").value.trim();
      return token ? { "Authorization": `Bearer ${token}` } : {};
    }

    async function loadWatchlist() {
      try {
        const response = await fetch("/api/watchlist", {
          cache: "no-store",
          headers: authHeaders()
        });
        const payload = await response.json();
        renderWatchlist(payload);
      } catch (error) {
        document.getElementById("watchlist-status").textContent = "Watchlist unavailable";
      }
    }

    async function loadAuth() {
      try {
        const response = await fetch("/api/me", {
          cache: "no-store",
          headers: authHeaders()
        });
        renderAuth(await response.json());
      } catch (error) {
        document.getElementById("auth-panel").textContent = "Identity unavailable";
      }
    }

    async function loadAgentTokens() {
      try {
        const response = await fetch("/api/agent-tokens", {
          cache: "no-store",
          headers: authHeaders()
        });
        if (!response.ok) {
          renderAgentTokens(null);
          return;
        }
        renderAgentTokens(await response.json());
      } catch (error) {
        document.getElementById("agent-token-status").textContent = "Agent tokens unavailable";
      }
    }

    async function createAgentToken() {
      const button = document.getElementById("create-agent-token");
      button.disabled = true;
      document.getElementById("agent-token-status").textContent = "Creating";
      try {
        const response = await fetch("/api/agent-tokens", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...authHeaders()
          },
          body: JSON.stringify({
            label: document.getElementById("agent-token-label").value.trim()
          })
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const payload = await response.json();
        document.getElementById("new-agent-token").value = payload.token || "";
        document.getElementById("agent-token-status").textContent = "Token created";
        await loadAgentTokens();
      } catch (error) {
        document.getElementById("agent-token-status").textContent = "Create failed";
      } finally {
        button.disabled = false;
      }
    }

    async function revokeAgentToken(tokenId) {
      document.getElementById("agent-token-status").textContent = "Revoking";
      try {
        const response = await fetch("/api/agent-tokens/revoke", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...authHeaders()
          },
          body: JSON.stringify({ token_id: tokenId })
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
        document.getElementById("agent-token-status").textContent = "Token revoked";
        await loadAgentTokens();
      } catch (error) {
        document.getElementById("agent-token-status").textContent = "Revoke failed";
      }
    }

    async function saveWatchlist() {
      const button = document.getElementById("save-watchlist");
      button.disabled = true;
      document.getElementById("watchlist-status").textContent = "Saving";
      const payload = {
        hostile_pilots: linesFromTextarea("watch-hostile-pilots"),
        hostile_corporations: linesFromTextarea("watch-hostile-corporations"),
        help_phrases: linesFromTextarea("watch-help-phrases"),
        keywords: linesFromTextarea("watch-keywords")
      };
      try {
        const response = await fetch("/api/watchlist", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...authHeaders()
          },
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
        renderWatchlist(await response.json());
      } catch (error) {
        document.getElementById("watchlist-status").textContent = "Save failed";
      } finally {
        button.disabled = state.watchlist?.can_write === false;
      }
    }

    async function refresh() {
      try {
        const response = await fetch("/api/state", { cache: "no-store" });
        const payload = await response.json();
        state.events = payload.events || [];
        renderCounts(payload.counts || {});
        renderFreshness(payload);
        renderTrust(state.events);
        renderSystems(payload.systems || []);
        renderEvents(state.events);
        document.getElementById("status").textContent = `Live - ${ageLabel(payload.generated_at)}`;
      } catch (error) {
        document.getElementById("status").textContent = "Connection lost";
        document.getElementById("freshness-summary").textContent = "Snapshot unavailable";
        document.getElementById("fresh-snapshot").textContent = "Connection lost";
        document.getElementById("fresh-snapshot-detail").textContent = "Could not reach /api/state.";
      }
    }

    document.getElementById("filter-search").addEventListener("input", () => renderEvents(state.events));
    document.getElementById("filter-severity").addEventListener("change", () => renderEvents(state.events));
    document.getElementById("filter-category").addEventListener("change", () => renderEvents(state.events));
    document.getElementById("clear-filters").addEventListener("click", () => {
      document.getElementById("filter-search").value = "";
      document.getElementById("filter-severity").value = "";
      document.getElementById("filter-category").value = "";
      renderEvents(state.events);
    });
    document.getElementById("save-watchlist").addEventListener("click", saveWatchlist);
    document.getElementById("admin-token").addEventListener("change", loadWatchlist);
    document.getElementById("create-agent-token").addEventListener("click", createAgentToken);

    loadAuth();
    loadAgentTokens();
    loadWatchlist();
    refresh();
    setInterval(refresh, 1500);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
