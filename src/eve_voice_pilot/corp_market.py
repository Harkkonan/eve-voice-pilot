from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse
from urllib.request import Request, urlopen
import uuid
import webbrowser

from eve_voice_pilot.corp_intel import (
    AuthStateStore,
    CorpIntelError,
    DEFAULT_ESI_BASE_URL,
    EveSsoConfig,
    VerifiedPilot,
    build_sso_authorization_url,
    exchange_sso_code,
    get_json,
    verify_sso_character,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKET_DB_PATH = ROOT / "profiles" / "corp_market.sqlite3"
DEFAULT_PORT = 8770
DEFAULT_MAX_NOTES_LENGTH = 5000
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 10.0
FLIGHT_LOCATION_SCOPE = "esi-location.read_location.v1"
FLIGHT_ASSETS_SCOPE = "esi-assets.read_assets.v1"
FLIGHT_BLUEPRINTS_SCOPE = "esi-characters.read_blueprints.v1"
DEFAULT_FLIGHT_ESI_SCOPES = (
    FLIGHT_LOCATION_SCOPE,
    FLIGHT_ASSETS_SCOPE,
    FLIGHT_BLUEPRINTS_SCOPE,
)
FLIGHT_SESSION_COOKIE_NAME = "corp_market_flight_session"
DISCORD_THREAD_NAME_MAX_LENGTH = 100
LISTING_TYPES = {"sell", "want"}
LISTING_STATUSES = {"open", "reserved", "sold", "cancelled"}
LISTING_CATEGORIES = {
    "general": "General",
    "ships": "Ships",
    "modules": "Modules",
    "ammo": "Ammo",
    "ore": "Ore",
    "minerals": "Minerals",
    "pi": "PI",
    "salvage": "Salvage",
    "blueprints": "Blueprints",
    "hauling": "Hauling",
}
SPACE_RE = re.compile(r"\s+")
ISK_AMOUNT_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmMbB]?)\s*$")
DISCORD_WEBHOOK_PATH_RE = re.compile(r"^/api/(?:v\d+/)?webhooks/\d+/[^/]+/?$")
DISCORD_SNOWFLAKE_RE = re.compile(r"^\d{5,25}$")
FIT_HEADER_RE = re.compile(r"^\[(?P<hull>[^,\]]+),\s*(?P<name>[^\]]+)\]\s*$")
FIT_QUANTITY_RE = re.compile(r"\sx[\d,]+\s*$", re.IGNORECASE)


class CorpMarketError(RuntimeError):
    pass


@dataclass(frozen=True)
class MailDraft:
    subject: str
    body: str

    def to_dict(self) -> dict[str, str]:
        return {"subject": self.subject, "body": self.body, "combined": f"Subject: {self.subject}\n\n{self.body}"}


@dataclass(frozen=True)
class FitNoteSummary:
    hull: str
    fit_name: str
    fitted_lines: tuple[str, ...]
    cargo_lines: tuple[str, ...]
    empty_slots: int

    @property
    def display_name(self) -> str:
        return f"{self.hull} - {self.fit_name}"


@dataclass(frozen=True)
class DiscordPostResult:
    message_id: str = ""
    channel_id: str = ""
    thread_id: str = ""


@dataclass(frozen=True)
class FlightEsiSession:
    character_id: int
    character_name: str
    corporation_id: int
    corporation_name: str
    alliance_id: int | None
    alliance_name: str
    scopes: tuple[str, ...]
    access_token: str
    connected_at: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return self.expires_at <= time.time()

    @property
    def expires_in_seconds(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "character_name": self.character_name,
            "corporation_id": self.corporation_id,
            "corporation_name": self.corporation_name,
            "alliance_id": self.alliance_id,
            "alliance_name": self.alliance_name,
            "scopes": list(self.scopes),
            "connected_at": self.connected_at,
            "expires_in_seconds": self.expires_in_seconds,
        }


class FlightEsiSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, FlightEsiSession] = {}
        self._lock = threading.Lock()

    def create(self, pilot: VerifiedPilot, *, access_token: str, expires_in: Any = None) -> str:
        ttl_seconds = clean_token_ttl_seconds(expires_in)
        session_id = secrets.token_urlsafe(32)
        session = FlightEsiSession(
            character_id=pilot.character_id,
            character_name=pilot.character_name,
            corporation_id=pilot.corporation_id,
            corporation_name=pilot.corporation_name,
            alliance_id=pilot.alliance_id,
            alliance_name=pilot.alliance_name,
            scopes=pilot.scopes,
            access_token=access_token,
            connected_at=now_iso(),
            expires_at=time.time() + ttl_seconds,
        )
        with self._lock:
            self._sessions[session_id] = session
            self._prune_locked()
        return session_id

    def get(self, session_id: str) -> FlightEsiSession | None:
        if not session_id:
            return None
        with self._lock:
            self._prune_locked()
            session = self._sessions.get(session_id)
        return session

    def delete(self, session_id: str) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def _prune_locked(self) -> None:
        expired = [session_id for session_id, session in self._sessions.items() if session.expired]
        for session_id in expired:
            self._sessions.pop(session_id, None)


@dataclass(frozen=True)
class MarketListing:
    listing_id: str
    listing_type: str
    status: str
    category: str
    item_name: str
    quantity: int
    unit_price_isk: float | None
    location: str
    owner: str
    notes: str
    delivery: str
    fit_image_url: str = ""
    reserved_by: str = ""
    reserved_until: str = ""
    discord_message_id: str = ""
    discord_thread_id: str = ""
    discord_synced_at: str = ""
    discord_sync_error: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def label(self) -> str:
        return "WTS" if self.listing_type == "sell" else "WTB"

    @property
    def category_label(self) -> str:
        return LISTING_CATEGORIES.get(self.category, self.category.title())

    @property
    def total_price_isk(self) -> float | None:
        if self.unit_price_isk is None:
            return None
        return self.unit_price_isk * self.quantity

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MarketListing":
        unit_price = row["unit_price_isk"]
        return cls(
            listing_id=str(row["listing_id"]),
            listing_type=str(row["listing_type"]),
            status=str(row["status"]),
            category=str(row["category"] or "general"),
            item_name=str(row["item_name"]),
            quantity=int(row["quantity"]),
            unit_price_isk=float(unit_price) if unit_price is not None else None,
            location=str(row["location"] or ""),
            owner=str(row["owner"] or ""),
            notes=str(row["notes"] or ""),
            delivery=str(row["delivery"] or ""),
            fit_image_url=str(row["fit_image_url"] or ""),
            reserved_by=str(row["reserved_by"] or ""),
            reserved_until=str(row["reserved_until"] or ""),
            discord_message_id=str(row["discord_message_id"] or ""),
            discord_thread_id=str(row["discord_thread_id"] or ""),
            discord_synced_at=str(row["discord_synced_at"] or ""),
            discord_sync_error=str(row["discord_sync_error"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def to_dict(self, *, public_base_url: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.listing_id,
            "listing_type": self.listing_type,
            "label": self.label,
            "status": self.status,
            "category": self.category,
            "category_label": self.category_label,
            "item_name": self.item_name,
            "quantity": self.quantity,
            "unit_price_isk": self.unit_price_isk,
            "unit_price_display": format_isk(self.unit_price_isk) if self.unit_price_isk is not None else "quote",
            "total_price_isk": self.total_price_isk,
            "total_price_display": format_isk(self.total_price_isk) if self.total_price_isk is not None else "quote",
            "location": self.location,
            "owner": self.owner,
            "notes": self.notes,
            "delivery": self.delivery,
            "fit_image_url": self.fit_image_url,
            "reserved_by": self.reserved_by,
            "reserved_until": self.reserved_until,
            "discord_message_id": self.discord_message_id,
            "discord_thread_id": self.discord_thread_id,
            "discord_synced_at": self.discord_synced_at,
            "discord_sync_error": self.discord_sync_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if public_base_url:
            payload["url"] = listing_public_url(self.listing_id, public_base_url)
        return payload


class MarketStore:
    def __init__(self, path: Path = DEFAULT_MARKET_DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS corp_market_listings (
                    listing_id TEXT PRIMARY KEY,
                    listing_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    item_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price_isk REAL,
                    location TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    delivery TEXT NOT NULL,
                    fit_image_url TEXT NOT NULL DEFAULT '',
                    reserved_by TEXT NOT NULL DEFAULT '',
                    reserved_until TEXT NOT NULL DEFAULT '',
                    discord_message_id TEXT NOT NULL DEFAULT '',
                    discord_thread_id TEXT NOT NULL DEFAULT '',
                    discord_synced_at TEXT NOT NULL DEFAULT '',
                    discord_sync_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(corp_market_listings)").fetchall()}
            if "category" not in columns:
                connection.execute(
                    "ALTER TABLE corp_market_listings ADD COLUMN category TEXT NOT NULL DEFAULT 'general'"
                )
            if "fit_image_url" not in columns:
                connection.execute(
                    "ALTER TABLE corp_market_listings ADD COLUMN fit_image_url TEXT NOT NULL DEFAULT ''"
                )
            if "discord_message_id" not in columns:
                connection.execute(
                    "ALTER TABLE corp_market_listings ADD COLUMN discord_message_id TEXT NOT NULL DEFAULT ''"
                )
            if "discord_thread_id" not in columns:
                connection.execute(
                    "ALTER TABLE corp_market_listings ADD COLUMN discord_thread_id TEXT NOT NULL DEFAULT ''"
                )
            if "discord_synced_at" not in columns:
                connection.execute(
                    "ALTER TABLE corp_market_listings ADD COLUMN discord_synced_at TEXT NOT NULL DEFAULT ''"
                )
            if "discord_sync_error" not in columns:
                connection.execute(
                    "ALTER TABLE corp_market_listings ADD COLUMN discord_sync_error TEXT NOT NULL DEFAULT ''"
                )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_corp_market_status ON corp_market_listings(status)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_corp_market_type_status ON corp_market_listings(listing_type, status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_corp_market_category ON corp_market_listings(category)"
            )

    def create_listing(self, payload: dict[str, Any]) -> MarketListing:
        listing_type = clean_choice(payload.get("listing_type") or payload.get("type") or "sell", LISTING_TYPES, "listing_type")
        category = clean_choice(payload.get("category") or "general", set(LISTING_CATEGORIES), "category")
        item_name = clean_text(payload.get("item_name") or payload.get("item"), "item_name", max_length=120, required=True)
        quantity = clean_positive_int(payload.get("quantity"), "quantity")
        unit_price = clean_optional_isk(payload.get("unit_price_isk") or payload.get("unit_price") or payload.get("price"))
        location = clean_text(payload.get("location"), "location", max_length=160, required=True)
        owner = clean_text(payload.get("owner") or payload.get("seller") or payload.get("buyer"), "owner", max_length=80, required=True)
        notes = clean_multiline(payload.get("notes"), "notes", max_length=DEFAULT_MAX_NOTES_LENGTH)
        delivery = clean_text(payload.get("delivery"), "delivery", max_length=160)
        fit_image_url = clean_optional_url(payload.get("fit_image_url") or payload.get("image_url") or payload.get("screenshot_url"), "fit_image_url")
        timestamp = now_iso()
        listing = MarketListing(
            listing_id=str(payload.get("id") or uuid.uuid4().hex[:12]),
            listing_type=listing_type,
            status="open",
            category=category,
            item_name=item_name,
            quantity=quantity,
            unit_price_isk=unit_price,
            location=location,
            owner=owner,
            notes=notes,
            delivery=delivery,
            fit_image_url=fit_image_url,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO corp_market_listings (
                    listing_id, listing_type, status, category, item_name, quantity, unit_price_isk,
                    location, owner, notes, delivery, fit_image_url, reserved_by, reserved_until,
                    discord_message_id, discord_thread_id, discord_synced_at, discord_sync_error,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing.listing_id,
                    listing.listing_type,
                    listing.status,
                    listing.category,
                    listing.item_name,
                    listing.quantity,
                    listing.unit_price_isk,
                    listing.location,
                    listing.owner,
                    listing.notes,
                    listing.delivery,
                    listing.fit_image_url,
                    listing.reserved_by,
                    listing.reserved_until,
                    listing.discord_message_id,
                    listing.discord_thread_id,
                    listing.discord_synced_at,
                    listing.discord_sync_error,
                    listing.created_at,
                    listing.updated_at,
                ),
            )
        return listing

    def list_listings(
        self,
        *,
        status: str | None = None,
        listing_type: str | None = None,
        include_closed: bool = False,
        limit: int = 100,
    ) -> list[MarketListing]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(clean_choice(status, LISTING_STATUSES, "status"))
        elif not include_closed:
            clauses.append("status IN ('open', 'reserved')")
        if listing_type:
            clauses.append("listing_type = ?")
            params.append(clean_choice(listing_type, LISTING_TYPES, "listing_type"))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT * FROM corp_market_listings
            {where}
            ORDER BY
                CASE status WHEN 'open' THEN 0 WHEN 'reserved' THEN 1 WHEN 'sold' THEN 2 ELSE 3 END,
                updated_at DESC
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [MarketListing.from_row(row) for row in rows]

    def get_listing(self, listing_id: str) -> MarketListing:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM corp_market_listings WHERE listing_id = ?",
                (clean_listing_id(listing_id),),
            ).fetchone()
        if row is None:
            raise CorpMarketError(f"Listing {listing_id!r} was not found.")
        return MarketListing.from_row(row)

    def reserve_listing(self, listing_id: str, *, reserved_by: str, hours: float = 24.0) -> MarketListing:
        listing = self.get_listing(listing_id)
        if listing.status not in {"open", "reserved"}:
            raise CorpMarketError(f"Listing is {listing.status}; it cannot be reserved.")
        reserved_by = clean_text(reserved_by, "reserved_by", max_length=80, required=True)
        reserved_until = future_iso(hours=max(0.25, min(hours, 72.0)))
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE corp_market_listings
                SET status = 'reserved', reserved_by = ?, reserved_until = ?, updated_at = ?
                WHERE listing_id = ?
                """,
                (reserved_by, reserved_until, timestamp, clean_listing_id(listing_id)),
            )
        return self.get_listing(listing_id)

    def set_status(self, listing_id: str, status: str) -> MarketListing:
        status = clean_choice(status, LISTING_STATUSES, "status")
        timestamp = now_iso()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE corp_market_listings
                SET status = ?, updated_at = ?,
                    reserved_by = CASE WHEN ? = 'open' THEN '' ELSE reserved_by END,
                    reserved_until = CASE WHEN ? = 'open' THEN '' ELSE reserved_until END
                WHERE listing_id = ?
                """,
                (status, timestamp, status, status, clean_listing_id(listing_id)),
            )
        if result.rowcount == 0:
            raise CorpMarketError(f"Listing {listing_id!r} was not found.")
        return self.get_listing(listing_id)

    def record_discord_sync(
        self,
        listing_id: str,
        *,
        message_id: str | None = None,
        thread_id: str | None = None,
        error: str = "",
    ) -> MarketListing:
        assignments = ["discord_synced_at = ?", "discord_sync_error = ?"]
        params: list[Any] = [now_iso(), shorten(str(error or ""), 500)]
        if message_id is not None:
            assignments.append("discord_message_id = ?")
            params.append(clean_discord_snowflake(message_id, "discord_message_id"))
        if thread_id is not None:
            assignments.append("discord_thread_id = ?")
            params.append(clean_discord_snowflake(thread_id, "discord_thread_id"))
        params.append(clean_listing_id(listing_id))
        with self._connect() as connection:
            result = connection.execute(
                f"""
                UPDATE corp_market_listings
                SET {", ".join(assignments)}
                WHERE listing_id = ?
                """,
                tuple(params),
            )
        if result.rowcount == 0:
            raise CorpMarketError(f"Listing {listing_id!r} was not found.")
        return self.get_listing(listing_id)


def build_mail_draft(listing: MarketListing, *, actor: str = "") -> MailDraft:
    actor = clean_text(actor, "actor", max_length=80)
    if listing.listing_type == "sell":
        subject = f"Corp market buy request - {listing.item_name}"
        opening = f"Hi {listing.owner},"
        action = "I want to buy your corp market listing."
        actor_label = "Buyer"
    else:
        subject = f"Corp market fulfillment offer - {listing.item_name}"
        opening = f"Hi {listing.owner},"
        action = "I can help fill your corp market request."
        actor_label = "Seller"

    lines = [
        opening,
        "",
        action,
        "",
        f"Item: {listing.item_name}",
        f"Quantity: {listing.quantity:,}",
        f"Category: {listing.category_label}",
        f"Unit price: {format_isk(listing.unit_price_isk) if listing.unit_price_isk is not None else 'Quote requested'}",
        f"Total: {format_isk(listing.total_price_isk) if listing.total_price_isk is not None else 'Quote requested'}",
        f"Location: {listing.location}",
    ]
    if listing.delivery:
        lines.append(f"Delivery: {listing.delivery}")
    if listing.fit_image_url:
        lines.append(f"Fit image: {listing.fit_image_url}")
    if actor:
        lines.append(f"{actor_label}: {actor}")
    if listing.notes:
        fit_note = parse_fit_note(listing.notes)
        lines.extend(["", "Fit note:" if fit_note else "Notes:", listing.notes])
    lines.extend(
        [
            f"Offer ID: {listing.listing_id}",
            "",
            "Please reply with contract, trade, or delivery details.",
            "",
            "Thanks.",
        ]
    )
    return MailDraft(subject=subject, body="\n".join(lines))


def build_discord_webhook_payload(
    listing: MarketListing,
    *,
    public_base_url: str,
    forum_post: bool = False,
    forum_tag_ids: Iterable[str] = (),
    forum_tag_map: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    url = listing_public_url(listing.listing_id, public_base_url)
    color = discord_embed_color(listing)
    title = discord_listing_title(listing)
    contact_label = "Seller" if listing.listing_type == "sell" else "Buyer"
    fit_note = parse_fit_note(listing.notes)
    fields = [
        {"name": "Status", "value": discord_status_label(listing), "inline": True},
        {"name": "Category", "value": listing.category_label, "inline": True},
        {"name": "Quantity", "value": f"{listing.quantity:,}", "inline": True},
        {
            "name": "Unit",
            "value": format_isk(listing.unit_price_isk) if listing.unit_price_isk is not None else "Quote",
            "inline": True,
        },
        {
            "name": "Total",
            "value": format_isk(listing.total_price_isk) if listing.total_price_isk is not None else "Quote",
            "inline": True,
        },
        {"name": "Location", "value": listing.location or "Not specified", "inline": False},
        {"name": contact_label, "value": listing.owner, "inline": True},
    ]
    if fit_note:
        fields.append({"name": "Fit Note", "value": discord_fit_summary(fit_note), "inline": False})
    if listing.delivery:
        fields.append({"name": "Delivery", "value": listing.delivery, "inline": True})
    if listing.fit_image_url:
        fields.append({"name": "Fit Image", "value": f"[Open screenshot]({listing.fit_image_url})", "inline": True})
    embed: dict[str, Any] = {
        "title": title,
        "url": url,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Offer {listing.listing_id} · manual EVE mail · {listing.status}"},
        "timestamp": listing.updated_at or listing.created_at,
    }
    if fit_note:
        embed["description"] = "Fit note detected. Open the listing for the full copy/paste block."
    elif listing.notes:
        embed["description"] = shorten(listing.notes, 700)
    if listing.fit_image_url:
        embed["image"] = {"url": listing.fit_image_url}
    payload: dict[str, Any] = {
        "content": f"Open the listing to copy an EVE mail draft:\n{url}",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    if forum_post:
        payload["thread_name"] = discord_thread_name(listing)
        tag_ids = resolve_forum_tag_ids(
            listing,
            default_tag_ids=forum_tag_ids,
            tag_map=forum_tag_map or {},
        )
        if tag_ids:
            payload["applied_tags"] = list(tag_ids)
    return payload


def post_discord_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> DiscordPostResult | None:
    if not webhook_url:
        return None
    validate_discord_webhook_url(webhook_url)
    request = Request(
        add_query_params(webhook_url, {"wait": "true"}),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "EveVoicePilot-CorpMarket/0.1"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise CorpMarketError(f"Discord webhook returned HTTP {response.status}.")
            return parse_discord_message_response(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CorpMarketError(f"Discord webhook returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CorpMarketError(f"Discord webhook failed: {exc.reason}") from exc


def edit_discord_webhook_message(
    webhook_url: str,
    message_id: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    thread_id: str = "",
) -> DiscordPostResult | None:
    validate_discord_webhook_url(webhook_url)
    url = build_discord_message_edit_url(webhook_url, message_id, thread_id=thread_id)
    request = Request(
        url,
        data=json.dumps(discord_message_edit_payload(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "EveVoicePilot-CorpMarket/0.1"},
        method="PATCH",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise CorpMarketError(f"Discord webhook edit returned HTTP {response.status}.")
            return parse_discord_message_response(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CorpMarketError(f"Discord webhook edit returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CorpMarketError(f"Discord webhook edit failed: {exc.reason}") from exc


def build_discord_message_edit_url(webhook_url: str, message_id: str, *, thread_id: str = "") -> str:
    validate_discord_webhook_url(webhook_url)
    message_id = clean_discord_snowflake(message_id, "discord_message_id")
    parsed = urlparse(webhook_url)
    base_url = parsed._replace(path=f"{parsed.path.rstrip('/')}/messages/{message_id}", query="", fragment="").geturl()
    if thread_id:
        base_url = add_query_params(base_url, {"thread_id": clean_discord_snowflake(thread_id, "discord_thread_id")})
    return base_url


def discord_message_edit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in ("content", "embeds", "allowed_mentions") if key in payload}


def parse_discord_message_response(body: bytes) -> DiscordPostResult:
    if not body.strip():
        return DiscordPostResult()
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpMarketError(f"Discord returned an unreadable message response: {exc}") from exc
    if not isinstance(payload, dict):
        return DiscordPostResult()
    message_id = clean_discord_snowflake(payload.get("id", ""), "discord_message_id")
    channel_id = clean_discord_snowflake(payload.get("channel_id", ""), "discord_channel_id")
    thread_id = clean_discord_snowflake(payload.get("thread_id", ""), "discord_thread_id")
    return DiscordPostResult(message_id=message_id, channel_id=channel_id, thread_id=thread_id)


def add_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    replacements = {key: value for key, value in params.items() if value}
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in replacements]
    query.extend(replacements.items())
    return parsed._replace(query=urlencode(query), fragment="").geturl()


def validate_discord_webhook_url(webhook_url: str) -> None:
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or parsed.netloc not in {"discord.com", "discordapp.com"}:
        raise CorpMarketError("Discord webhook URL must start with https://discord.com/api/webhooks/...")
    if not DISCORD_WEBHOOK_PATH_RE.match(parsed.path):
        raise CorpMarketError(
            "Discord webhook URL looks wrong. Copy it from Channel Settings > Integrations > Webhooks > Copy "
            "Webhook URL; do not use the Discord channel or forum post link."
        )


def parse_fit_note(notes: str) -> FitNoteSummary | None:
    lines = [line.strip() for line in notes.splitlines()]
    first_line_index = next((index for index, line in enumerate(lines) if line), None)
    if first_line_index is None:
        return None
    header = FIT_HEADER_RE.match(lines[first_line_index])
    if not header:
        return None
    fitted_lines: list[str] = []
    cargo_lines: list[str] = []
    empty_slots = 0
    for line in lines[first_line_index + 1 :]:
        if not line:
            continue
        if FIT_QUANTITY_RE.search(line):
            cargo_lines.append(line)
            continue
        if line.lower().startswith("[empty "):
            empty_slots += 1
            continue
        fitted_lines.append(line)
    return FitNoteSummary(
        hull=header.group("hull").strip(),
        fit_name=header.group("name").strip(),
        fitted_lines=tuple(fitted_lines),
        cargo_lines=tuple(cargo_lines),
        empty_slots=empty_slots,
    )


def discord_fit_summary(fit_note: FitNoteSummary) -> str:
    slot_text = f"{len(fit_note.fitted_lines)} fitted lines"
    if fit_note.empty_slots:
        slot_text += f", {fit_note.empty_slots} empty slot{'s' if fit_note.empty_slots != 1 else ''}"
    cargo_text = f"{len(fit_note.cargo_lines)} cargo stack{'s' if len(fit_note.cargo_lines) != 1 else ''}"
    lines = [fit_note.display_name, f"{slot_text}; {cargo_text}"]
    if fit_note.cargo_lines:
        lines.append("Cargo: " + shorten("; ".join(fit_note.cargo_lines[:4]), 220))
    return shorten("\n".join(lines), 1000)


def discord_thread_name(listing: MarketListing) -> str:
    name = discord_listing_title(listing)
    name = SPACE_RE.sub(" ", name).strip()
    if len(name) <= DISCORD_THREAD_NAME_MAX_LENGTH:
        return name
    return name[: DISCORD_THREAD_NAME_MAX_LENGTH - 3].rstrip() + "..."


def discord_listing_title(listing: MarketListing) -> str:
    title = f"{listing.label} {listing.item_name} x{listing.quantity:,}"
    if listing.status == "open":
        return title
    return f"{listing.status.upper()} - {title}"


def discord_status_label(listing: MarketListing) -> str:
    if listing.status == "open":
        return "Open"
    if listing.status == "reserved":
        details = "Reserved"
        if listing.reserved_by:
            details += f" by {listing.reserved_by}"
        if listing.reserved_until:
            details += f"\nUntil {listing.reserved_until}"
        return details
    if listing.status == "sold":
        return "Sold"
    if listing.status == "cancelled":
        return "Cancelled"
    return listing.status.title()


def discord_embed_color(listing: MarketListing) -> int:
    if listing.status == "reserved":
        return 0xF0BA57
    if listing.status == "sold":
        return 0x6B7280
    if listing.status == "cancelled":
        return 0xE36F6F
    return 0x2E7D32 if listing.listing_type == "sell" else 0x1565C0


def resolve_forum_tag_ids(
    listing: MarketListing,
    *,
    default_tag_ids: Iterable[str],
    tag_map: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    tag_ids: list[str] = []
    for raw_id in default_tag_ids:
        tag_id = raw_id.strip()
        if tag_id and tag_id not in tag_ids:
            tag_ids.append(tag_id)
    keys = (
        listing.listing_type,
        listing.label.lower(),
        listing.category,
        listing.category_label.lower(),
    )
    for key in keys:
        for tag_id in tag_map.get(key, ()):
            if tag_id and tag_id not in tag_ids:
                tag_ids.append(tag_id)
    return tuple(tag_ids)


def sync_listing_to_discord(
    store: MarketStore,
    listing: MarketListing,
    *,
    public_base_url: str,
    webhook_url: str,
    timeout_seconds: float,
) -> tuple[MarketListing, bool, str]:
    if not webhook_url:
        return listing, False, ""
    if not listing.discord_message_id:
        return listing, False, "No Discord message ID is recorded for this listing yet."
    try:
        payload = build_discord_webhook_payload(listing, public_base_url=public_base_url)
        result = edit_discord_webhook_message(
            webhook_url,
            listing.discord_message_id,
            payload,
            timeout_seconds=timeout_seconds,
            thread_id=listing.discord_thread_id,
        )
        synced_message_id = result.message_id if result and result.message_id else listing.discord_message_id
        synced_thread_id = result.thread_id if result and result.thread_id else listing.discord_thread_id
        listing = store.record_discord_sync(
            listing.listing_id,
            message_id=synced_message_id,
            thread_id=synced_thread_id,
            error="",
        )
        return listing, True, ""
    except (CorpMarketError, ValueError) as exc:
        listing = store.record_discord_sync(listing.listing_id, error=str(exc))
        return listing, False, str(exc)


def fetch_flight_location(config: EveSsoConfig, session: FlightEsiSession) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_LOCATION_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    headers = flight_esi_headers(session.access_token)
    location = get_json(
        f"{base_url}/characters/{session.character_id}/location/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=headers,
    )
    if not isinstance(location, dict):
        raise CorpMarketError("ESI location endpoint returned unexpected data.")
    solar_system_id = int(location.get("solar_system_id") or 0)
    if solar_system_id <= 0:
        raise CorpMarketError("ESI location endpoint did not return a solar system id.")
    system_payload = get_json(
        f"{base_url}/universe/systems/{solar_system_id}/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=flight_esi_headers(),
    )
    system_name = ""
    constellation_id = None
    if isinstance(system_payload, dict):
        system_name = str(system_payload.get("name") or "")
        constellation_id = system_payload.get("constellation_id")
    return {
        "solar_system_id": solar_system_id,
        "solar_system_name": system_name or f"System {solar_system_id}",
        "station_id": location.get("station_id"),
        "structure_id": location.get("structure_id"),
        "constellation_id": constellation_id,
        "source": "esi-location.read_location.v1",
        "updated_at": now_iso(),
    }


def require_flight_scopes(session: FlightEsiSession, required_scopes: Iterable[str]) -> None:
    granted = set(session.scopes)
    missing = [scope for scope in required_scopes if scope not in granted]
    if missing:
        raise CorpMarketError(f"Flight Attendant needs these ESI scopes: {', '.join(missing)}.")


def fetch_flight_blueprints(config: EveSsoConfig, session: FlightEsiSession) -> list[dict[str, Any]]:
    require_flight_scopes(session, (FLIGHT_BLUEPRINTS_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    return get_esi_json_pages(
        f"{base_url}/characters/{session.character_id}/blueprints/?datasource=tranquility",
        headers=flight_esi_headers(session.access_token),
        label="ESI character blueprints",
    )


def fetch_flight_assets(config: EveSsoConfig, session: FlightEsiSession) -> list[dict[str, Any]]:
    require_flight_scopes(session, (FLIGHT_ASSETS_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    return get_esi_json_pages(
        f"{base_url}/characters/{session.character_id}/assets/?datasource=tranquility",
        headers=flight_esi_headers(session.access_token),
        label="ESI character assets",
    )


def get_esi_json_pages(url: str, *, headers: dict[str, str], label: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page = 1
    page_count = 1
    while page <= page_count:
        request = Request(
            add_query_params(url, {"page": str(page)}),
            headers={"Accept": "application/json", **headers},
            method="GET",
        )
        try:
            with urlopen(request, timeout=45.0) as response:
                raw = response.read().decode("utf-8")
                page_count = max(1, int(response.headers.get("X-Pages") or "1"))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise CorpMarketError(f"{label} request failed: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CorpMarketError(f"{label} returned non-JSON data: {raw[:200]!r}") from exc
        if not isinstance(payload, list):
            raise CorpMarketError(f"{label} returned unexpected data.")
        for item in payload:
            if isinstance(item, dict):
                results.append(item)
        page += 1
    return results


def fetch_universe_names(config: EveSsoConfig, ids: Iterable[int]) -> dict[int, str]:
    unique_ids = sorted({int(item) for item in ids if int(item) > 0})
    if not unique_ids:
        return {}
    base_url = config.esi_base_url.rstrip("/")
    names: dict[int, str] = {}
    for index in range(0, len(unique_ids), 1000):
        chunk = unique_ids[index : index + 1000]
        request = Request(
            f"{base_url}/universe/names/?datasource=tranquility",
            data=json.dumps(chunk).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                **flight_esi_headers(),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CorpMarketError(f"ESI universe name lookup failed: {exc}") from exc
        if not isinstance(payload, list):
            raise CorpMarketError("ESI universe name lookup returned unexpected data.")
        for item in payload:
            if isinstance(item, dict):
                try:
                    item_id = int(item.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                name = str(item.get("name") or "").strip()
                if item_id > 0 and name:
                    names[item_id] = name
    return names


def build_flight_industry_payload(*, config: EveSsoConfig, session: FlightEsiSession) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_ASSETS_SCOPE, FLIGHT_BLUEPRINTS_SCOPE))
    blueprints = fetch_flight_blueprints(config, session)
    assets = fetch_flight_assets(config, session)
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "industry": summarize_flight_industry(config, blueprints=blueprints, assets=assets),
    }


def summarize_flight_industry(
    config: EveSsoConfig,
    *,
    blueprints: Iterable[dict[str, Any]],
    assets: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    blueprint_items = [item for item in blueprints if isinstance(item, dict)]
    asset_items = [item for item in assets if isinstance(item, dict)]
    blueprint_type_counts = count_by_type_id(blueprint_items)
    asset_quantities = quantity_by_type_id(asset_items)
    top_blueprints = sorted(blueprint_type_counts.items(), key=lambda item: item[1], reverse=True)[:5]
    top_assets = sorted(asset_quantities.items(), key=lambda item: item[1], reverse=True)[:5]
    names = fetch_universe_names(
        config,
        [type_id for type_id, _count in top_blueprints] + [type_id for type_id, _quantity in top_assets],
    )
    original_count = sum(1 for item in blueprint_items if int(item.get("quantity") or 0) == -1)
    copy_count = sum(1 for item in blueprint_items if int(item.get("quantity") or 0) == -2)
    return {
        "blueprints": {
            "total": len(blueprint_items),
            "unique_types": len(blueprint_type_counts),
            "originals": original_count,
            "copies": copy_count,
            "top_types": [
                {
                    "type_id": type_id,
                    "name": names.get(type_id) or f"Type {type_id}",
                    "count": count,
                }
                for type_id, count in top_blueprints
            ],
        },
        "assets": {
            "stacks": len(asset_items),
            "unique_types": len(asset_quantities),
            "total_units": sum(asset_quantities.values()),
            "locations": len({int(item.get("location_id") or 0) for item in asset_items if item.get("location_id")}),
            "top_types": [
                {
                    "type_id": type_id,
                    "name": names.get(type_id) or f"Type {type_id}",
                    "quantity": quantity,
                }
                for type_id, quantity in top_assets
            ],
        },
        "next_step": "Recipe cache and market orders are needed before profitability ranking.",
    }


def count_by_type_id(items: Iterable[dict[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for item in items:
        try:
            type_id = int(item.get("type_id") or 0)
        except (TypeError, ValueError):
            continue
        if type_id > 0:
            counts[type_id] = counts.get(type_id, 0) + 1
    return counts


def quantity_by_type_id(items: Iterable[dict[str, Any]]) -> dict[int, int]:
    quantities: dict[int, int] = {}
    for item in items:
        try:
            type_id = int(item.get("type_id") or 0)
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if type_id > 0 and quantity > 0:
            quantities[type_id] = quantities.get(type_id, 0) + quantity
    return quantities


def build_flight_status_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession | None,
    callback_url: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "sso_configured": config.enabled,
        "connected": bool(session),
        "required_scopes": list(config.scopes or DEFAULT_FLIGHT_ESI_SCOPES),
        "login_url": "/flight/login",
        "logout_url": "/flight/logout",
        "callback_url": callback_url,
        "character": session.to_public_dict() if session else None,
        "location": None,
        "error": "",
        "note": "",
    }
    if not config.enabled:
        payload["note"] = "EVE SSO is not configured for this local market server yet."
        return payload
    if not session:
        payload["note"] = "Connect ESI to show your current system."
        return payload
    try:
        payload["location"] = fetch_flight_location(config, session)
    except (CorpIntelError, CorpMarketError, ValueError) as exc:
        payload["error"] = str(exc)
    return payload


def flight_esi_headers(access_token: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": "EveVoicePilot-FlightAttendant/0.1",
        "X-Compatibility-Date": esi_compatibility_date(),
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def esi_compatibility_date() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=11)).date().isoformat()


def build_http_server(
    host: str,
    port: int,
    store: MarketStore,
    *,
    public_base_url: str,
    discord_webhook_url: str = "",
    discord_timeout_seconds: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
    discord_forum_posts: bool = False,
    discord_forum_tag_ids: Iterable[str] = (),
    discord_forum_tag_map: dict[str, tuple[str, ...]] | None = None,
    admin_token: str = "",
    sso_config: EveSsoConfig | None = None,
    auth_state_store: AuthStateStore | None = None,
    flight_session_store: FlightEsiSessionStore | None = None,
) -> ThreadingHTTPServer:
    public_base_url = public_base_url.rstrip("/")
    sso_config = sso_config or EveSsoConfig()
    auth_state_store = auth_state_store or AuthStateStore()
    flight_session_store = flight_session_store or FlightEsiSessionStore()

    class CorpMarketHandler(BaseHTTPRequestHandler):
        server_version = "CorpMarketConcierge/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._send_html(render_dashboard())
                return
            if path == "/api/offers":
                self._handle_offer_list()
                return
            if path == "/api/flight/status":
                self._handle_flight_status()
                return
            if path == "/api/flight/industry":
                self._handle_flight_industry()
                return
            if path == "/flight/login":
                self._handle_flight_login()
                return
            if path == "/flight/callback":
                self._handle_flight_callback()
                return
            if path == "/flight/logout":
                self._handle_flight_logout()
                return
            if path.startswith("/api/offers/") and path.endswith("/mail"):
                self._handle_mail_api(path)
                return
            if path.startswith("/api/offers/"):
                self._handle_offer_api(path)
                return
            if path.startswith("/offers/"):
                self._handle_offer_page(path)
                return
            if path == "/api/health":
                self._send_json({"ok": True, "generated_at": now_iso()})
                return
            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/offers":
                if not self._require_write_access():
                    return
                self._handle_offer_create()
                return
            if path.startswith("/api/offers/") and path.endswith("/reserve"):
                self._handle_offer_reserve(path)
                return
            if path.startswith("/api/offers/") and path.endswith("/status"):
                if not self._require_write_access():
                    return
                self._handle_offer_status(path)
                return
            if path == "/flight/logout":
                self._handle_flight_logout()
                return
            self.send_error(404, "Not found")

        def _handle_offer_list(self) -> None:
            params = parse_qs(urlparse(self.path).query)
            status = first_query_value(params, "status") or None
            listing_type = first_query_value(params, "type") or first_query_value(params, "listing_type") or None
            include_closed = first_query_value(params, "include_closed").lower() in {"1", "true", "yes"}
            try:
                listings = store.list_listings(status=status, listing_type=listing_type, include_closed=include_closed)
            except (ValueError, CorpMarketError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(
                {
                    "ok": True,
                    "generated_at": now_iso(),
                    "offers": [listing.to_dict(public_base_url=public_base_url) for listing in listings],
                }
            )

        def _handle_flight_status(self) -> None:
            session = self._flight_session()
            payload = build_flight_status_payload(
                config=sso_config,
                session=session,
                callback_url=sso_config.callback_url,
            )
            self._send_json(payload)

        def _handle_flight_industry(self) -> None:
            if not sso_config.enabled:
                self._send_json({"ok": False, "error": "EVE SSO is not configured."}, status=503)
                return
            session = self._flight_session()
            if session is None:
                self._send_json({"ok": False, "error": "Connect ESI before loading industry analysis."}, status=401)
                return
            try:
                payload = build_flight_industry_payload(config=sso_config, session=session)
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_login(self) -> None:
            if not sso_config.enabled:
                self._send_html(
                    render_flight_auth_result(
                        "EVE SSO is not configured for this local market server yet.",
                        ok=False,
                        details=[
                            f"Register this callback URL in the EVE Developers portal: {sso_config.callback_url or 'not set'}",
                            "Then start the server with --sso-client-id and --sso-client-secret.",
                        ],
                    ),
                    status=503,
                )
                return
            try:
                state = auth_state_store.create()
                url = build_sso_authorization_url(sso_config, state)
            except (CorpIntelError, CorpMarketError) as exc:
                self._send_html(render_flight_auth_result(str(exc), ok=False), status=502)
                return
            self._redirect(url)

        def _handle_flight_callback(self) -> None:
            if not sso_config.enabled:
                self._send_html(render_flight_auth_result("EVE SSO is not configured.", ok=False), status=503)
                return
            params = parse_qs(urlparse(self.path).query)
            state = first_query_value(params, "state")
            code = first_query_value(params, "code")
            error = first_query_value(params, "error")
            if error:
                self._send_html(render_flight_auth_result("EVE SSO declined the Flight Attendant login.", ok=False))
                return
            if not state or not auth_state_store.consume(state):
                self._send_html(render_flight_auth_result("Invalid or expired ESI login state.", ok=False), status=400)
                return
            if not code:
                self._send_html(render_flight_auth_result("Missing ESI authorization code.", ok=False), status=400)
                return
            try:
                token_response = exchange_sso_code(sso_config, code)
                access_token = str(token_response["access_token"])
                pilot = verify_sso_character(sso_config, access_token=access_token)
                session_id = flight_session_store.create(
                    pilot,
                    access_token=access_token,
                    expires_in=token_response.get("expires_in"),
                )
            except (CorpIntelError, CorpMarketError, ValueError) as exc:
                self._send_html(render_flight_auth_result(str(exc), ok=False), status=502)
                return
            self.send_response(302)
            self.send_header("Location", "/#flight")
            self.send_header("Set-Cookie", flight_session_cookie_header(session_id))
            self.end_headers()

        def _handle_flight_logout(self) -> None:
            session_id = request_cookie(self, FLIGHT_SESSION_COOKIE_NAME)
            flight_session_store.delete(session_id)
            self.send_response(302)
            self.send_header("Location", "/#flight")
            self.send_header("Set-Cookie", clear_flight_session_cookie_header())
            self.end_headers()

        def _flight_session(self) -> FlightEsiSession | None:
            return flight_session_store.get(request_cookie(self, FLIGHT_SESSION_COOKIE_NAME))

        def _handle_offer_api(self, path: str) -> None:
            listing_id = path.removeprefix("/api/offers/").split("/", 1)[0]
            try:
                listing = store.get_listing(listing_id)
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self._send_json({"ok": True, "offer": listing.to_dict(public_base_url=public_base_url)})

        def _handle_mail_api(self, path: str) -> None:
            listing_id = path.removeprefix("/api/offers/").removesuffix("/mail")
            params = parse_qs(urlparse(self.path).query)
            actor = first_query_value(params, "actor")
            try:
                listing = store.get_listing(listing_id)
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            draft = build_mail_draft(listing, actor=actor)
            self._send_json({"ok": True, "offer": listing.to_dict(public_base_url=public_base_url), "mail": draft.to_dict()})

        def _handle_offer_page(self, path: str) -> None:
            listing_id = path.removeprefix("/offers/").split("/", 1)[0]
            params = parse_qs(urlparse(self.path).query)
            actor = first_query_value(params, "actor")
            try:
                listing = store.get_listing(listing_id)
            except CorpMarketError as exc:
                self._send_html(render_not_found(str(exc)), status=404)
                return
            self._send_html(render_offer_page(listing, build_mail_draft(listing, actor=actor)))

        def _handle_offer_create(self) -> None:
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json({"ok": False, "error": f"Invalid JSON: {exc}"}, status=400)
                return
            if not isinstance(payload, dict):
                self._send_json({"ok": False, "error": "Offer payload must be a JSON object."}, status=400)
                return
            try:
                listing = store.create_listing(payload)
                discord_payload = build_discord_webhook_payload(
                    listing,
                    public_base_url=public_base_url,
                    forum_post=discord_forum_posts,
                    forum_tag_ids=discord_forum_tag_ids,
                    forum_tag_map=discord_forum_tag_map,
                )
                posted = False
                if discord_webhook_url:
                    result = post_discord_webhook(
                        discord_webhook_url,
                        discord_payload,
                        timeout_seconds=discord_timeout_seconds,
                    )
                    if result:
                        thread_id = result.thread_id or (result.channel_id if discord_forum_posts else "")
                        listing = store.record_discord_sync(
                            listing.listing_id,
                            message_id=result.message_id,
                            thread_id=thread_id,
                            error="",
                        )
                    posted = True
            except (ValueError, CorpMarketError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(
                {
                    "ok": True,
                    "posted_to_discord": posted,
                    "offer": listing.to_dict(public_base_url=public_base_url),
                    "discord_payload": discord_payload if not posted else None,
                },
                status=201,
            )

        def _handle_offer_reserve(self, path: str) -> None:
            listing_id = path.removeprefix("/api/offers/").removesuffix("/reserve")
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json({"ok": False, "error": f"Invalid JSON: {exc}"}, status=400)
                return
            if not isinstance(payload, dict):
                self._send_json({"ok": False, "error": "Reserve payload must be a JSON object."}, status=400)
                return
            try:
                hours = float(payload.get("hours") or 24)
                listing = store.reserve_listing(listing_id, reserved_by=str(payload.get("reserved_by") or ""), hours=hours)
                listing, discord_synced, discord_sync_error = sync_listing_to_discord(
                    store,
                    listing,
                    public_base_url=public_base_url,
                    webhook_url=discord_webhook_url,
                    timeout_seconds=discord_timeout_seconds,
                )
            except (ValueError, CorpMarketError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(
                {
                    "ok": True,
                    "discord_synced": discord_synced,
                    "discord_sync_error": discord_sync_error,
                    "offer": listing.to_dict(public_base_url=public_base_url),
                }
            )

        def _handle_offer_status(self, path: str) -> None:
            listing_id = path.removeprefix("/api/offers/").removesuffix("/status")
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json({"ok": False, "error": f"Invalid JSON: {exc}"}, status=400)
                return
            if not isinstance(payload, dict):
                self._send_json({"ok": False, "error": "Status payload must be a JSON object."}, status=400)
                return
            try:
                listing = store.set_status(listing_id, str(payload.get("status") or ""))
                listing, discord_synced, discord_sync_error = sync_listing_to_discord(
                    store,
                    listing,
                    public_base_url=public_base_url,
                    webhook_url=discord_webhook_url,
                    timeout_seconds=discord_timeout_seconds,
                )
            except (ValueError, CorpMarketError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(
                {
                    "ok": True,
                    "discord_synced": discord_synced,
                    "discord_sync_error": discord_sync_error,
                    "offer": listing.to_dict(public_base_url=public_base_url),
                }
            )

        def _read_json_body(self) -> Any:
            body = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
            if not body:
                raise ValueError("Request body is empty.")
            return json.loads(body.decode("utf-8"))

        def _require_write_access(self) -> bool:
            if request_is_loopback(self):
                return True
            if not admin_token:
                return True
            auth = self.headers.get("Authorization", "")
            token = self.headers.get("X-Admin-Token", "") or self.headers.get("X-Market-Token", "")
            if auth == f"Bearer {admin_token}" or token == admin_token:
                return True
            self._send_json({"ok": False, "error": "Write access requires the market admin token."}, status=403)
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

    return ThreadingHTTPServer((host, port), CorpMarketHandler)


def render_dashboard() -> str:
    return _render_flight_attendant_dashboard()


def _render_legacy_market_dashboard() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Corp Market Concierge</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101318;
      --panel: #171c23;
      --panel-2: #1f2630;
      --text: #eef3f8;
      --muted: #a7b3c2;
      --line: #303946;
      --green: #58b66b;
      --blue: #64a8ff;
      --amber: #f0ba57;
      --red: #e36f6f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Segoe UI, system-ui, sans-serif;
      font-size: 15px;
      line-height: 1.45;
    }
    header, main { width: min(1180px, calc(100vw - 32px)); margin: 0 auto; }
    header { padding: 28px 0 16px; display: flex; align-items: end; justify-content: space-between; gap: 20px; }
    h1 { margin: 0; font-size: 30px; font-weight: 650; letter-spacing: 0; }
    h2 { margin: 0 0 14px; font-size: 18px; font-weight: 650; letter-spacing: 0; }
    .status { color: var(--muted); font-size: 13px; text-align: right; }
    main { display: grid; grid-template-columns: 360px minmax(0, 1fr); gap: 18px; padding-bottom: 32px; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    form { display: grid; gap: 12px; }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 13px; }
    input, select, textarea, button {
      font: inherit;
      border-radius: 6px;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      background: #0d1117;
      color: var(--text);
      padding: 9px 10px;
    }
    textarea { min-height: 150px; resize: vertical; }
    button {
      border: 0;
      background: var(--blue);
      color: #07111f;
      font-weight: 700;
      padding: 10px 12px;
      cursor: pointer;
    }
    button.secondary { background: var(--panel-2); color: var(--text); border: 1px solid var(--line); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
    .filters button { padding: 7px 10px; font-size: 13px; }
    .offers { display: grid; gap: 10px; }
    .offer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      padding: 12px;
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .offer h3 { margin: 0 0 4px; font-size: 16px; letter-spacing: 0; }
    .meta { color: var(--muted); font-size: 13px; }
    .pill { display: inline-flex; align-items: center; min-height: 24px; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }
    .sell { background: rgba(88, 182, 107, .16); color: var(--green); }
    .want { background: rgba(100, 168, 255, .16); color: var(--blue); }
    .reserved { background: rgba(240, 186, 87, .16); color: var(--amber); }
    .sold, .cancelled { background: rgba(227, 111, 111, .16); color: var(--red); }
    .sync-warning { color: var(--amber); margin-top: 4px; }
    .actions { display: flex; gap: 8px; align-items: start; }
    .actions a, .actions button {
      min-width: 38px;
      text-align: center;
      text-decoration: none;
      border: 1px solid var(--line);
      background: #0d1117;
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 13px;
      font-weight: 700;
    }
    .empty, .error { color: var(--muted); padding: 18px 0; }
    .error { color: var(--red); }
    @media (max-width: 860px) {
      header { align-items: start; flex-direction: column; }
      .status { text-align: left; }
      main { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Corp Market Concierge</h1>
      <div class="meta">Offers, requests, reserves, and copyable EVE mail drafts.</div>
    </div>
    <div id="status" class="status">Loading offers...</div>
  </header>
  <main>
    <section>
      <h2>Create Offer</h2>
      <form id="offer-form">
        <div class="row">
          <label>Type
            <select name="listing_type">
              <option value="sell">For sale</option>
              <option value="want">Want to buy</option>
            </select>
          </label>
          <label>Category
            <select name="category">
              <option value="general">General</option>
              <option value="ships">Ships</option>
              <option value="modules">Modules</option>
              <option value="ammo">Ammo</option>
              <option value="ore">Ore</option>
              <option value="minerals">Minerals</option>
              <option value="pi">PI</option>
              <option value="salvage">Salvage</option>
              <option value="blueprints">Blueprints</option>
              <option value="hauling">Hauling</option>
            </select>
          </label>
        </div>
        <div class="row">
          <label>Quantity
            <input name="quantity" type="number" min="1" step="1" value="1">
          </label>
          <label>Unit Price
            <input name="unit_price" autocomplete="off" placeholder="12.5m or blank">
          </label>
        </div>
        <label>Item
          <input name="item_name" autocomplete="off" placeholder="Venture, Water, 10MN Afterburner I">
        </label>
        <div class="row">
          <label>Contact
            <input name="owner" autocomplete="off" placeholder="EVE character">
          </label>
          <label>Location
            <input name="location" autocomplete="off" placeholder="Station, structure, or system">
          </label>
        </div>
        <label>Delivery
          <input name="delivery" autocomplete="off" placeholder="Pickup, delivery available, high-sec only">
        </label>
        <label>Fit Image URL
          <input name="fit_image_url" autocomplete="off" placeholder="Optional Discord/CDN screenshot URL">
        </label>
        <label>Notes
          <textarea name="notes" placeholder="[Hawk, Fit name]\nPaste EFT fit blocks, contract details, timing, limits"></textarea>
        </label>
        <button type="submit">Post Offer</button>
        <div id="form-error" class="error" hidden></div>
      </form>
    </section>
    <section>
      <div class="filters">
        <button class="secondary" type="button" data-filter="">Open</button>
        <button class="secondary" type="button" data-filter="sell">For sale</button>
        <button class="secondary" type="button" data-filter="want">Want to buy</button>
        <button class="secondary" type="button" data-closed="1">All statuses</button>
      </div>
      <div id="offers" class="offers"></div>
    </section>
  </main>
  <script>
    const offersEl = document.querySelector("#offers");
    const statusEl = document.querySelector("#status");
    const errorEl = document.querySelector("#form-error");
    let filterType = "";
    let includeClosed = false;

    function fmt(value) {
      return value || "";
    }

    function escapeHtml(value) {
      const replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      };
      return String(value ?? "").replace(/[&<>"']/g, (char) => replacements[char]);
    }

    async function loadOffers() {
      const params = new URLSearchParams();
      if (filterType) params.set("type", filterType);
      if (includeClosed) params.set("include_closed", "1");
      const response = await fetch(`/api/offers?${params}`);
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || "Could not load offers");
      renderOffers(data.offers);
      statusEl.textContent = `${data.offers.length} offer${data.offers.length === 1 ? "" : "s"}`;
    }

    function renderOffers(offers) {
      if (!offers.length) {
        offersEl.innerHTML = `<div class="empty">No matching offers.</div>`;
        return;
      }
      offersEl.innerHTML = offers.map((offer) => {
        const canClose = offer.status === "open" || offer.status === "reserved";
        return `
        <article class="offer">
          <div>
            <h3><span class="pill ${offer.listing_type}">${offer.label}</span> ${escapeHtml(offer.item_name)}</h3>
            <div class="meta">
              ${escapeHtml(offer.quantity.toLocaleString())} units · ${escapeHtml(offer.unit_price_display)} each · ${escapeHtml(offer.total_price_display)} total
            </div>
            <div class="meta">${escapeHtml(offer.category_label)} · ${escapeHtml(offer.location)} · ${escapeHtml(offer.owner)}${offer.delivery ? ` · ${escapeHtml(offer.delivery)}` : ""}</div>
            ${offer.status !== "open" ? `<div class="meta"><span class="pill ${offer.status}">${escapeHtml(offer.status)}</span>${offer.reserved_by ? ` by ${escapeHtml(offer.reserved_by)}` : ""}</div>` : ""}
            ${offer.discord_sync_error ? `<div class="meta sync-warning">Discord sync: ${escapeHtml(offer.discord_sync_error)}</div>` : ""}
          </div>
          <div class="actions">
            <a href="${escapeHtml(offer.url)}" title="Mail draft">Mail</a>
            ${offer.status === "open" ? `<button type="button" data-reserve="${escapeHtml(offer.id)}">Reserve</button>` : ""}
            ${offer.status === "reserved" ? `<button type="button" data-status-id="${escapeHtml(offer.id)}" data-status="open">Reopen</button>` : ""}
            ${canClose ? `<button type="button" data-status-id="${escapeHtml(offer.id)}" data-status="sold">Sold</button>` : ""}
            ${canClose ? `<button type="button" data-status-id="${escapeHtml(offer.id)}" data-status="cancelled">Cancel</button>` : ""}
          </div>
        </article>
      `;
      }).join("");
    }

    document.querySelector("#offer-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      errorEl.hidden = true;
      const formEl = event.currentTarget;
      const form = new FormData(formEl);
      const payload = Object.fromEntries(form.entries());
      try {
        const response = await fetch("/api/offers", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Offer was not created");
        formEl.reset();
        formEl.quantity.value = "1";
        await loadOffers();
      } catch (error) {
        errorEl.textContent = error.message;
        errorEl.hidden = false;
      }
    });

    document.querySelector(".filters").addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.closed) {
        includeClosed = !includeClosed;
      } else {
        filterType = button.dataset.filter || "";
      }
      await loadOffers();
    });

    offersEl.addEventListener("click", async (event) => {
      const reserveButton = event.target.closest("button[data-reserve]");
      if (reserveButton) {
      const reservedBy = window.prompt("Reserve for which character?");
      if (!reservedBy) return;
      const response = await fetch(`/api/offers/${reserveButton.dataset.reserve}/reserve`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reserved_by: reservedBy, hours: 24}),
      });
      const data = await response.json();
      if (!data.ok) {
        window.alert(data.error || "Could not reserve offer");
        return;
      }
      alertDiscordSyncProblem(data);
      await loadOffers();
      return;
      }

      const statusButton = event.target.closest("button[data-status-id]");
      if (!statusButton) return;
      const nextStatus = statusButton.dataset.status;
      if (!window.confirm(`Set this listing to ${nextStatus}?`)) return;
      const response = await fetch(`/api/offers/${statusButton.dataset.statusId}/status`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({status: nextStatus}),
      });
      const data = await response.json();
      if (!data.ok) {
        window.alert(data.error || "Could not update listing");
        return;
      }
      alertDiscordSyncProblem(data);
      await loadOffers();
    });

    function alertDiscordSyncProblem(data) {
      if (data.discord_sync_error) {
        window.alert(`Updated locally, but Discord did not sync: ${data.discord_sync_error}`);
      }
    }

loadOffers().catch((error) => {
      offersEl.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
      statusEl.textContent = "Load failed";
    });
  </script>
</body>
</html>
"""


def _render_flight_attendant_dashboard() -> str:
    category_options = "\n".join(
        f'                    <option value="{html.escape(key)}">{html.escape(label)}</option>'
        for key, label in LISTING_CATEGORIES.items()
    )
    markup = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Corp Market Concierge</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #080b0d;
      --panel: #111819;
      --panel-2: #172021;
      --text: #edf4ef;
      --muted: #95a59d;
      --line: #2c3a38;
      --line-bright: #3f5550;
      --green: #64c47d;
      --cyan: #61c7d9;
      --amber: #e0a84a;
      --red: #e57466;
      --ink: #081012;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(180deg, rgba(19, 29, 28, .88), rgba(8, 11, 13, .94) 260px),
        repeating-linear-gradient(90deg, rgba(97, 199, 217, .05) 0 1px, transparent 1px 64px),
        var(--bg);
      color: var(--text);
      font-family: Segoe UI, system-ui, sans-serif;
      font-size: 15px;
      line-height: 1.45;
      overflow-x: hidden;
    }
    .shell { width: min(1360px, calc(100vw - 32px)); margin: 0 auto; padding-bottom: 34px; min-width: 0; }
    header { padding: 24px 0 14px; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: end; }
    h1 { margin: 0; font-size: 30px; font-weight: 700; letter-spacing: 0; }
    h2 { margin: 0 0 14px; font-size: 18px; font-weight: 680; letter-spacing: 0; }
    h3 { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: 0; }
    .brand { display: flex; align-items: center; gap: 13px; min-width: 0; }
    .brand-mark {
      width: 42px;
      height: 42px;
      border: 1px solid rgba(224, 168, 74, .55);
      border-radius: 8px;
      display: grid;
      place-items: center;
      color: var(--amber);
      background: linear-gradient(135deg, rgba(224, 168, 74, .16), rgba(97, 199, 217, .09));
      font-weight: 800;
    }
    .brand > div:last-child { min-width: 0; }
    .deck { color: var(--muted); font-size: 13px; margin-top: 2px; overflow-wrap: anywhere; }
    .status {
      color: var(--muted);
      font-size: 13px;
      text-align: right;
      border: 1px solid var(--line);
      background: rgba(17, 24, 25, .86);
      border-radius: 8px;
      padding: 9px 11px;
      min-width: 148px;
    }
    .tabbar {
      display: flex;
      gap: 6px;
      border-top: 1px solid rgba(97, 199, 217, .2);
      border-bottom: 1px solid var(--line);
      padding: 10px 0;
      margin-bottom: 16px;
      overflow-x: auto;
    }
    .tabbar button {
      min-height: 36px;
      border: 1px solid var(--line);
      color: var(--muted);
      background: rgba(17, 24, 25, .7);
      border-radius: 7px;
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 750;
      white-space: nowrap;
    }
    .tabbar button[aria-selected="true"] {
      color: #061113;
      border-color: rgba(97, 199, 217, .75);
      background: linear-gradient(180deg, #75d6e2, #4baebe);
    }
    .tab-panel[hidden] { display: none; }
    .market-grid { display: grid; grid-template-columns: minmax(0, 382px) minmax(0, 1fr); gap: 16px; min-width: 0; }
    .flight-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 16px; min-width: 0; }
    .panel {
      background: rgba(17, 24, 25, .94);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 18px 44px rgba(0, 0, 0, .22);
      min-width: 0;
    }
    .panel-header { display: flex; align-items: start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
    .panel-header > div { min-width: 0; max-width: 100%; }
    .panel-header .meta { max-width: 620px; }
    form { display: grid; gap: 12px; }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 13px; }
    input, select, textarea, button { font: inherit; border-radius: 7px; }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      background: #090d0f;
      color: var(--text);
      padding: 9px 10px;
    }
    input:focus, select:focus, textarea:focus {
      outline: 2px solid rgba(97, 199, 217, .28);
      border-color: rgba(97, 199, 217, .72);
    }
    textarea { min-height: 150px; resize: vertical; }
    button {
      border: 0;
      background: var(--cyan);
      color: var(--ink);
      font-weight: 800;
      padding: 10px 12px;
      cursor: pointer;
    }
    button.secondary { background: var(--panel-2); color: var(--text); border: 1px solid var(--line); }
    button.ghost { background: transparent; color: var(--cyan); border: 1px solid rgba(97, 199, 217, .45); }
    button[disabled] { opacity: .58; cursor: not-allowed; }
    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 39px;
      border-radius: 7px;
      padding: 9px 12px;
      background: var(--cyan);
      color: var(--ink);
      font-weight: 800;
      text-decoration: none;
    }
    .button-link.secondary {
      background: transparent;
      color: var(--cyan);
      border: 1px solid rgba(97, 199, 217, .45);
    }
    .button-link[hidden] { display: none; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
    .filters button { padding: 7px 10px; font-size: 13px; }
    .filters button.active { color: var(--ink); background: var(--amber); border-color: var(--amber); }
    .offers { display: grid; gap: 9px; }
    .offer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      padding: 12px;
      background: linear-gradient(180deg, rgba(28, 40, 40, .92), rgba(18, 26, 27, .92));
      border: 1px solid var(--line);
      border-radius: 7px;
    }
    .offer h3 { margin: 0 0 5px; font-size: 16px; letter-spacing: 0; }
    .offer-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; margin-top: 8px; }
    .readout {
      border: 1px solid rgba(63, 85, 80, .78);
      background: rgba(8, 13, 15, .42);
      border-radius: 6px;
      padding: 7px 8px;
      min-height: 45px;
    }
    .readout b { display: block; color: var(--text); font-size: 13px; line-height: 1.2; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .pill { display: inline-flex; align-items: center; min-height: 23px; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 800; }
    .sell { background: rgba(100, 196, 125, .16); color: var(--green); }
    .want { background: rgba(97, 199, 217, .16); color: var(--cyan); }
    .reserved { background: rgba(224, 168, 74, .16); color: var(--amber); }
    .sold, .cancelled { background: rgba(229, 116, 102, .16); color: var(--red); }
    .actions { display: flex; gap: 8px; align-items: start; flex-wrap: wrap; justify-content: flex-end; max-width: 260px; }
    .actions a, .actions button {
      min-width: 38px;
      text-align: center;
      text-decoration: none;
      border: 1px solid var(--line);
      background: #090d0f;
      color: var(--text);
      border-radius: 7px;
      padding: 8px 10px;
      font-size: 13px;
      font-weight: 800;
    }
    .actions button { color: var(--ink); background: var(--amber); border-color: var(--amber); }
    .ops-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; margin-bottom: 12px; }
    .ops-tile {
      border: 1px solid var(--line);
      background: rgba(8, 13, 15, .44);
      border-radius: 7px;
      padding: 10px;
      min-height: 76px;
    }
    .ops-tile strong { display: block; color: var(--text); font-size: 16px; margin-top: 3px; overflow-wrap: anywhere; }
    .ops-tile span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .briefing { display: grid; grid-template-columns: minmax(0, 1fr) minmax(230px, .42fr); gap: 14px; }
    .system-board {
      border: 1px solid rgba(224, 168, 74, .42);
      background: linear-gradient(135deg, rgba(224, 168, 74, .12), rgba(97, 199, 217, .05)), rgba(8, 13, 15, .52);
      border-radius: 8px;
      padding: 16px;
      min-height: 260px;
    }
    .system-name { font-size: 34px; font-weight: 800; letter-spacing: 0; margin: 4px 0 6px; }
    .constellation-line { color: var(--muted); margin-bottom: 16px; }
    .flight-actions { display: flex; gap: 9px; flex-wrap: wrap; margin-top: 16px; }
    .module-stack { display: grid; gap: 10px; }
    .module { border: 1px solid var(--line); background: rgba(17, 24, 25, .78); border-radius: 7px; padding: 11px; }
    .module h3 { margin-bottom: 5px; }
    .signal { color: var(--cyan); }
    .warning { color: var(--amber); }
    .danger { color: var(--red); }
    .note-form textarea { min-height: 112px; }
    .note-list { display: grid; gap: 9px; margin-top: 12px; }
    .note-card {
      border: 1px solid var(--line);
      background: rgba(8, 13, 15, .5);
      border-radius: 7px;
      padding: 10px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
    }
    .note-card strong { display: block; margin-bottom: 3px; overflow-wrap: anywhere; }
    .note-card p { margin: 0; color: var(--muted); overflow-wrap: anywhere; white-space: pre-wrap; }
    .note-card button {
      align-self: start;
      padding: 6px 8px;
      background: transparent;
      color: var(--red);
      border: 1px solid rgba(229, 116, 102, .45);
      font-size: 12px;
    }
    .charter-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 9px; }
    .charter-list li { border-left: 3px solid var(--line-bright); padding-left: 9px; color: var(--muted); }
    .charter-list strong { color: var(--text); }
    .empty, .error { color: var(--muted); padding: 18px 0; }
    .error { color: var(--red); }
    @media (max-width: 1040px) {
      .market-grid, .flight-grid, .briefing { grid-template-columns: 1fr; }
      .ops-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 720px) {
      .shell { width: auto; margin: 0 10px; }
      header { grid-template-columns: 1fr; align-items: start; }
      .status { text-align: left; }
      .brand { align-items: start; display: grid; grid-template-columns: 42px minmax(0, 1fr); }
      .deck { display: none; }
      .panel-header { display: block; }
      .panel-header .pill { margin-top: 8px; }
      .panel-header .meta { max-width: 100%; }
      h1 { font-size: 24px; }
      .row, .offer-grid, .ops-strip { grid-template-columns: 1fr; }
      .offer, .note-card { grid-template-columns: 1fr; }
      .actions { justify-content: stretch; max-width: none; }
      .actions a, .actions button { flex: 1; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand">
        <div class="brand-mark">CM</div>
        <div>
          <h1>Corp Market Concierge</h1>
          <div class="deck">Quartermaster board, flight briefing, and manual capsuleer handoffs.</div>
        </div>
      </div>
      <div id="status" class="status">Loading offers...</div>
    </header>

    <nav class="tabbar" aria-label="Dashboard tabs">
      <button type="button" data-tab-target="market" aria-selected="true">Market Board</button>
      <button type="button" data-tab-target="flight" aria-selected="false">Flight Attendant</button>
    </nav>

    <main>
      <section id="tab-market" class="tab-panel" data-tab-panel="market">
        <div class="market-grid">
          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Create Offer</h2>
                <div class="meta">Create Discord-ready offers and mail drafts.</div>
              </div>
            </div>
            <form id="offer-form">
              <div class="row">
                <label>Type
                  <select name="listing_type">
                    <option value="sell">For sale</option>
                    <option value="want">Want to buy</option>
                  </select>
                </label>
                <label>Category
                  <select name="category">
@@CATEGORY_OPTIONS@@
                  </select>
                </label>
              </div>
              <div class="row">
                <label>Quantity
                  <input name="quantity" type="number" min="1" step="1" value="1">
                </label>
                <label>Unit Price
                  <input name="unit_price" autocomplete="off" placeholder="12.5m or blank">
                </label>
              </div>
              <label>Item
                <input name="item_name" autocomplete="off" placeholder="Venture, Water, 10MN Afterburner I">
              </label>
              <div class="row">
                <label>Contact
                  <input name="owner" autocomplete="off" placeholder="EVE character">
                </label>
                <label>Location
                  <input name="location" autocomplete="off" placeholder="Station or system">
                </label>
              </div>
              <label>Delivery
                <input name="delivery" autocomplete="off" placeholder="Pickup, delivery available, high-sec only">
              </label>
              <label>Fit Image URL
                <input name="fit_image_url" autocomplete="off" placeholder="Optional Discord/CDN screenshot URL">
              </label>
              <label>Notes
                <textarea name="notes" placeholder="[Hawk, Fit name]\nPaste EFT fit blocks, contract details, timing, limits"></textarea>
              </label>
              <button type="submit">Post Offer</button>
              <div id="form-error" class="error" hidden></div>
            </form>
          </section>

          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Market Board</h2>
                <div class="meta">Scan open requests, reserve manually, then use the mail draft page for in-game contact.</div>
              </div>
            </div>
            <div class="ops-strip">
              <div class="ops-tile"><span>Mode</span><strong>Manual Trade</strong></div>
              <div class="ops-tile"><span>Mail</span><strong>Copy Drafts</strong></div>
              <div class="ops-tile"><span>Discord</span><strong>Webhook Ready</strong></div>
              <div class="ops-tile"><span>Safety</span><strong>No Client Control</strong></div>
            </div>
            <div class="filters">
              <button class="secondary active" type="button" data-filter="">Open</button>
              <button class="secondary" type="button" data-filter="sell">For sale</button>
              <button class="secondary" type="button" data-filter="want">Want to buy</button>
              <button class="secondary" type="button" data-closed="1">All statuses</button>
            </div>
            <div id="offers" class="offers"></div>
          </section>
        </div>
      </section>

      <section id="tab-flight" class="tab-panel" data-tab-panel="flight" hidden>
        <div class="flight-grid">
          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Flight Attendant</h2>
                <div class="meta">A lore-friendly briefing console for local notes, future read-only ESI context, and human decisions.</div>
              </div>
              <span class="pill reserved">Preview</span>
            </div>
            <div class="briefing">
              <div class="system-board">
                <div class="meta">Current system briefing</div>
                <div id="flight-system-name" class="system-name">Awaiting ESI</div>
                <div id="flight-location-line" class="constellation-line">Checking Flight Attendant ESI status...</div>
                <div class="offer-grid">
                  <div class="readout"><span class="meta">Pilot</span><b id="flight-pilot-name">Not connected</b></div>
                  <div class="readout"><span class="meta">Scope</span><b id="flight-scope-name">Location</b></div>
                  <div class="readout"><span class="meta">Token</span><b id="flight-token-status">Not active</b></div>
                </div>
                <div class="flight-actions">
                  <a id="flight-login-link" class="button-link" href="/flight/login">Connect ESI</a>
                  <a id="flight-logout-link" class="button-link secondary" href="/flight/logout" hidden>Disconnect</a>
                  <button id="flight-refresh" class="ghost" type="button">Refresh</button>
                  <button class="ghost" type="button" disabled>Generate Briefing</button>
                </div>
                <div id="flight-esi-message" class="meta"></div>
              </div>
              <div class="module-stack">
                <div class="module">
                  <h3 class="signal">Blueprint Library</h3>
                  <div id="flight-blueprint-summary" class="meta">Connect ESI to scan owned blueprints.</div>
                  <div id="flight-blueprint-top" class="meta"></div>
                </div>
                <div class="module">
                  <h3 class="warning">Materials And Assets</h3>
                  <div id="flight-asset-summary" class="meta">Connect ESI to scan owned asset stacks.</div>
                  <div id="flight-asset-top" class="meta"></div>
                </div>
                <div class="module">
                  <h3 class="danger">Pilot Still Acts</h3>
                  <div id="flight-industry-note" class="meta">No warps, orders, contracts, clicks, or client input are performed by this page.</div>
                </div>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Captain's Notes</h2>
                <div class="meta">Saved in this browser only. Good for system reminders while backend storage is still planned.</div>
              </div>
            </div>
            <form id="flight-note-form" class="note-form">
              <div class="row">
                <label>System
                  <input name="system" autocomplete="off" placeholder="Jita, Amarr, Hek">
                </label>
                <label>Priority
                  <select name="priority">
                    <option value="normal">Normal</option>
                    <option value="asset">Asset</option>
                    <option value="market">Market</option>
                    <option value="warning">Warning</option>
                  </select>
                </label>
              </div>
              <label>Note
                <textarea name="note" placeholder="Fuel cache, doctrine hulls, avoid undock, cheap robotics nearby"></textarea>
              </label>
              <button type="submit">Save Local Note</button>
            </form>
            <div id="flight-notes" class="note-list"></div>
          </section>

          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Safety Charter</h2>
                <div class="meta">The Flight Attendant should advise like a crew member, not fly the ship.</div>
              </div>
            </div>
            <ul class="charter-list">
              <li><strong>Read-only ESI:</strong> location, assets, and blueprints only; market context uses public order data later.</li>
              <li><strong>No token file yet:</strong> this first ESI slice keeps the access token in server memory only.</li>
              <li><strong>Local notes:</strong> pilot-authored reminders can be stored without touching the EVE client.</li>
              <li><strong>No EVE client control:</strong> no keypresses, clicks, warps, contract creation, order placement, packet reading, OCR-driven reactions, or cache scraping.</li>
              <li><strong>Human confirmation:</strong> every trade, route, and market action remains a pilot decision inside EVE.</li>
            </ul>
          </section>
        </div>
      </section>
    </main>
  </div>
  <script>
    const offersEl = document.querySelector("#offers");
    const statusEl = document.querySelector("#status");
    const errorEl = document.querySelector("#form-error");
    const tabButtons = document.querySelectorAll("[data-tab-target]");
    const tabPanels = document.querySelectorAll("[data-tab-panel]");
    const notesForm = document.querySelector("#flight-note-form");
    const notesList = document.querySelector("#flight-notes");
    const flightSystemName = document.querySelector("#flight-system-name");
    const flightLocationLine = document.querySelector("#flight-location-line");
    const flightPilotName = document.querySelector("#flight-pilot-name");
    const flightScopeName = document.querySelector("#flight-scope-name");
    const flightTokenStatus = document.querySelector("#flight-token-status");
    const flightMessage = document.querySelector("#flight-esi-message");
    const flightLoginLink = document.querySelector("#flight-login-link");
    const flightLogoutLink = document.querySelector("#flight-logout-link");
    const flightRefreshButton = document.querySelector("#flight-refresh");
    const flightBlueprintSummary = document.querySelector("#flight-blueprint-summary");
    const flightBlueprintTop = document.querySelector("#flight-blueprint-top");
    const flightAssetSummary = document.querySelector("#flight-asset-summary");
    const flightAssetTop = document.querySelector("#flight-asset-top");
    const flightIndustryNote = document.querySelector("#flight-industry-note");
    const notesKey = "eve-flight-attendant-notes-v1";
    const validTabs = new Set(["market", "flight"]);
    let filterType = "";
    let includeClosed = false;

    function escapeHtml(value) {
      const replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      };
      return String(value ?? "").replace(/[&<>"']/g, (char) => replacements[char]);
    }

    function showTab(tabName) {
      const targetTab = validTabs.has(tabName) ? tabName : "market";
      tabButtons.forEach((button) => {
        const selected = button.dataset.tabTarget === targetTab;
        button.setAttribute("aria-selected", selected ? "true" : "false");
      });
      tabPanels.forEach((panel) => {
        panel.hidden = panel.dataset.tabPanel !== targetTab;
      });
    }

    function initialTab() {
      const requested = window.location.hash.replace("#", "");
      return validTabs.has(requested) ? requested : "market";
    }

    function updateFilterButtons() {
      document.querySelectorAll(".filters button").forEach((button) => {
        const isClosed = Boolean(button.dataset.closed);
        const isActive = isClosed ? includeClosed : button.dataset.filter === filterType;
        button.classList.toggle("active", isActive);
      });
    }

    async function loadOffers() {
      const params = new URLSearchParams();
      if (filterType) params.set("type", filterType);
      if (includeClosed) params.set("include_closed", "1");
      const response = await fetch(`/api/offers?${params}`);
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || "Could not load offers");
      renderOffers(data.offers);
      statusEl.textContent = `${data.offers.length} offer${data.offers.length === 1 ? "" : "s"}`;
    }

    function statusControls(offer) {
      const controls = [];
      if (offer.status === "open") {
        controls.push(`<button type="button" data-reserve="${escapeHtml(offer.id)}">Reserve</button>`);
      }
      if (offer.status !== "sold") {
        controls.push(`<button type="button" data-status-id="${escapeHtml(offer.id)}" data-status="sold">Sold</button>`);
      }
      if (offer.status !== "cancelled") {
        controls.push(`<button type="button" data-status-id="${escapeHtml(offer.id)}" data-status="cancelled">Cancel</button>`);
      }
      if (offer.status !== "open") {
        controls.push(`<button type="button" data-status-id="${escapeHtml(offer.id)}" data-status="open">Reopen</button>`);
      }
      return controls.join("");
    }

    function renderOffers(offers) {
      if (!offers.length) {
        offersEl.innerHTML = `<div class="empty">No matching offers.</div>`;
        return;
      }
      offersEl.innerHTML = offers.map((offer) => `
        <article class="offer">
          <div>
            <h3><span class="pill ${offer.listing_type}">${offer.label}</span> ${escapeHtml(offer.item_name)}</h3>
            <div class="meta">
              ${escapeHtml(offer.quantity.toLocaleString())} units &middot; ${escapeHtml(offer.unit_price_display)} each &middot; ${escapeHtml(offer.total_price_display)} total
            </div>
            <div class="meta">${escapeHtml(offer.category_label)} &middot; ${escapeHtml(offer.location)} &middot; ${escapeHtml(offer.owner)}${offer.delivery ? ` &middot; ${escapeHtml(offer.delivery)}` : ""}</div>
            <div class="offer-grid">
              <div class="readout"><span class="meta">Location</span><b>${escapeHtml(offer.location)}</b></div>
              <div class="readout"><span class="meta">Contact</span><b>${escapeHtml(offer.owner)}</b></div>
              <div class="readout"><span class="meta">Total</span><b>${escapeHtml(offer.total_price_display)}</b></div>
            </div>
            ${offer.status !== "open" ? `<div class="meta"><span class="pill ${offer.status}">${escapeHtml(offer.status)}</span>${offer.reserved_by ? ` by ${escapeHtml(offer.reserved_by)}` : ""}</div>` : ""}
            ${offer.fit_image_url ? `<div class="meta">Fit screenshot attached.</div>` : ""}
          </div>
          <div class="actions">
            <a href="${escapeHtml(offer.url)}" title="Mail draft">Mail</a>
            ${statusControls(offer)}
          </div>
        </article>
      `).join("");
    }

    function readNotes() {
      try {
        const notes = JSON.parse(window.localStorage.getItem(notesKey) || "[]");
        return Array.isArray(notes) ? notes : [];
      } catch (_error) {
        return [];
      }
    }

    function writeNotes(notes) {
      window.localStorage.setItem(notesKey, JSON.stringify(notes.slice(0, 30)));
    }

    function renderNotes() {
      const notes = readNotes();
      if (!notes.length) {
        notesList.innerHTML = `<div class="empty">No local Flight Attendant notes yet.</div>`;
        return;
      }
      notesList.innerHTML = notes.map((note, index) => `
        <article class="note-card">
          <div>
            <strong>${escapeHtml(note.system)} <span class="pill ${note.priority === "warning" ? "reserved" : note.priority === "market" ? "want" : "sell"}">${escapeHtml(note.priority)}</span></strong>
            <p>${escapeHtml(note.note)}</p>
          </div>
          <button type="button" data-delete-note="${index}">Remove</button>
        </article>
      `).join("");
    }

    function formatNumber(value) {
      return Number(value || 0).toLocaleString();
    }

    async function loadFlightStatus() {
      try {
        const response = await fetch("/api/flight/status");
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not read Flight Attendant status");
        renderFlightStatus(data);
      } catch (error) {
        flightSystemName.textContent = "ESI Offline";
        flightLocationLine.textContent = "Could not load Flight Attendant status.";
        flightMessage.textContent = error.message;
        resetFlightIndustry("Flight Attendant ESI status is offline.");
      }
    }

    function renderFlightStatus(data) {
      const requiredScopes = data.required_scopes || [];
      const scopeLabel = requiredScopes.join(", ") || "esi-location.read_location.v1";
      flightLoginLink.href = data.login_url || "/flight/login";
      flightLogoutLink.href = data.logout_url || "/flight/logout";
      flightLogoutLink.hidden = !data.connected;
      flightScopeName.textContent = requiredScopes.length > 1 ? `${requiredScopes.length} scopes` : "Location only";
      if (!data.sso_configured) {
        flightSystemName.textContent = "ESI Setup Needed";
        flightLocationLine.textContent = "Register the callback URL, then restart with SSO credentials.";
        flightPilotName.textContent = "No app key";
        flightTokenStatus.textContent = "Not configured";
        flightMessage.textContent = `Scope: ${scopeLabel} | Callback: ${data.callback_url || "not set"}`;
        resetFlightIndustry("Configure EVE SSO before scanning industry data.");
        return;
      }
      if (!data.connected) {
        flightSystemName.textContent = "Awaiting ESI";
        flightLocationLine.textContent = data.note || "Connect ESI to show your current system.";
        flightPilotName.textContent = "Not connected";
        flightTokenStatus.textContent = "Not active";
        flightMessage.textContent = "";
        resetFlightIndustry("Connect ESI to scan owned blueprints and materials.");
        return;
      }
      const character = data.character || {};
      const location = data.location || {};
      flightPilotName.textContent = character.character_name || "Connected pilot";
      flightTokenStatus.textContent = `${Math.ceil((character.expires_in_seconds || 0) / 60)} min`;
      if (data.error) {
        flightSystemName.textContent = "ESI Error";
        flightLocationLine.textContent = data.error;
        flightMessage.textContent = "Try reconnecting ESI if the token expired.";
        resetFlightIndustry("Resolve the ESI error before scanning industry data.");
        return;
      }
      flightSystemName.textContent = location.solar_system_name || "Unknown System";
      flightLocationLine.textContent = `Live ESI location ${location.updated_at || ""}`;
      flightMessage.textContent = `${character.character_name || "Pilot"} connected with ${requiredScopes.length} read-only ESI scopes.`;
      loadFlightIndustry();
    }

    function resetFlightIndustry(message) {
      flightBlueprintSummary.textContent = message;
      flightBlueprintTop.textContent = "";
      flightAssetSummary.textContent = message;
      flightAssetTop.textContent = "";
      flightIndustryNote.textContent = "No warps, orders, contracts, clicks, or client input are performed by this page.";
    }

    async function loadFlightIndustry() {
      flightBlueprintSummary.textContent = "Scanning owned blueprints...";
      flightBlueprintTop.textContent = "";
      flightAssetSummary.textContent = "Scanning owned asset stacks...";
      flightAssetTop.textContent = "";
      try {
        const response = await fetch("/api/flight/industry");
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not load industry inventory");
        renderFlightIndustry(data.industry || {});
      } catch (error) {
        flightBlueprintSummary.textContent = error.message;
        flightAssetSummary.textContent = error.message;
        flightIndustryNote.textContent = "Industry analysis requires a connected ESI session with blueprint and asset scopes.";
      }
    }

    function renderFlightIndustry(industry) {
      const blueprints = industry.blueprints || {};
      const assets = industry.assets || {};
      flightBlueprintSummary.innerHTML = `
        <strong>${formatNumber(blueprints.total)}</strong> blueprints across
        <strong>${formatNumber(blueprints.unique_types)}</strong> types.
        <br>Originals: ${formatNumber(blueprints.originals)} &middot; Copies: ${formatNumber(blueprints.copies)}
      `;
      flightBlueprintTop.innerHTML = renderTopList(blueprints.top_types || [], "count");
      flightAssetSummary.innerHTML = `
        <strong>${formatNumber(assets.stacks)}</strong> asset stacks across
        <strong>${formatNumber(assets.unique_types)}</strong> item types.
        <br>Total units: ${formatNumber(assets.total_units)} &middot; Locations: ${formatNumber(assets.locations)}
      `;
      flightAssetTop.innerHTML = renderTopList(assets.top_types || [], "quantity");
      flightIndustryNote.textContent = industry.next_step || "Recipe cache and market orders are needed before profitability ranking.";
    }

    function renderTopList(items, valueKey) {
      if (!items.length) return "No top items returned yet.";
      return items.map((item) => {
        const value = formatNumber(item[valueKey]);
        return `${escapeHtml(item.name)} (${value})`;
      }).join("<br>");
    }

    tabButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const tabName = button.dataset.tabTarget;
        showTab(tabName);
        window.history.replaceState(null, "", `#${tabName}`);
      });
    });

    document.querySelector("#offer-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      errorEl.hidden = true;
      const formEl = event.currentTarget;
      const form = new FormData(formEl);
      const payload = Object.fromEntries(form.entries());
      try {
        const response = await fetch("/api/offers", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Offer was not created");
        formEl.reset();
        formEl.quantity.value = "1";
        alertDiscordSyncProblem(data);
        await loadOffers();
      } catch (error) {
        errorEl.textContent = error.message;
        errorEl.hidden = false;
      }
    });

    document.querySelector(".filters").addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.closed) {
        includeClosed = !includeClosed;
      } else {
        filterType = button.dataset.filter || "";
      }
      updateFilterButtons();
      await loadOffers();
    });

    offersEl.addEventListener("click", async (event) => {
      const reserveButton = event.target.closest("button[data-reserve]");
      if (reserveButton) {
        const reservedBy = window.prompt("Reserve for which character?");
        if (!reservedBy) return;
        const response = await fetch(`/api/offers/${reserveButton.dataset.reserve}/reserve`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({reserved_by: reservedBy, hours: 24}),
        });
        const data = await response.json();
        if (!data.ok) {
          window.alert(data.error || "Could not reserve offer");
          return;
        }
        alertDiscordSyncProblem(data);
        await loadOffers();
        return;
      }

      const statusButton = event.target.closest("button[data-status-id]");
      if (!statusButton) return;
      const nextStatus = statusButton.dataset.status;
      if (!window.confirm(`Set this listing to ${nextStatus}?`)) return;
      const response = await fetch(`/api/offers/${statusButton.dataset.statusId}/status`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({status: nextStatus}),
      });
      const data = await response.json();
      if (!data.ok) {
        window.alert(data.error || "Could not update listing");
        return;
      }
      alertDiscordSyncProblem(data);
      await loadOffers();
    });

    notesForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const form = new FormData(notesForm);
      const system = String(form.get("system") || "").trim();
      const note = String(form.get("note") || "").trim();
      const priority = String(form.get("priority") || "normal");
      if (!system || !note) return;
      const notes = readNotes();
      notes.unshift({system, note, priority});
      writeNotes(notes);
      notesForm.reset();
      renderNotes();
    });

    notesList.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-delete-note]");
      if (!button) return;
      const index = Number(button.dataset.deleteNote);
      const notes = readNotes();
      notes.splice(index, 1);
      writeNotes(notes);
      renderNotes();
    });

    flightRefreshButton.addEventListener("click", () => {
      loadFlightStatus();
    });

    function alertDiscordSyncProblem(data) {
      if (data.discord_sync_error) {
        window.alert(`Updated locally, but Discord did not sync: ${data.discord_sync_error}`);
      }
    }

    showTab(initialTab());
    updateFilterButtons();
    renderNotes();
    loadFlightStatus();
    loadOffers().catch((error) => {
      offersEl.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
      statusEl.textContent = "Load failed";
    });
  </script>
</body>
</html>
"""
    return markup.replace("@@CATEGORY_OPTIONS@@", category_options)


def render_offer_page(listing: MarketListing, draft: MailDraft) -> str:
    listing_json = html.escape(json.dumps(listing.to_dict()), quote=True)
    draft_json = html.escape(json.dumps(draft.to_dict()), quote=True)
    fit_note = parse_fit_note(listing.notes)
    fit_json = html.escape(json.dumps({"fit": listing.notes if fit_note else ""}), quote=True)
    fit_section = ""
    if fit_note:
        fit_section = f"""
    <section>
      <h2>Fitting Block</h2>
      <div class="meta">Copy this block into EVE's fitting import/simulator workflow.</div>
      <textarea id="fit-block" spellcheck="false">{escape_html(listing.notes)}</textarea>
      <div class="actions">
        <button id="copy-fit" type="button">Copy Fit</button>
      </div>
      <div id="fit-copy-status" class="copied"></div>
    </section>
"""
    image_section = ""
    if listing.fit_image_url:
        image_section = f"""
    <section>
      <h2>Fit Screenshot</h2>
      <a href="{escape_html(listing.fit_image_url)}" target="_blank" rel="noreferrer">Open screenshot</a>
      <img class="fit-image" src="{escape_html(listing.fit_image_url)}" alt="Fit screenshot">
    </section>
"""
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(listing.item_name)} - Corp Market</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101318;
      --panel: #171c23;
      --text: #eef3f8;
      --muted: #a7b3c2;
      --line: #303946;
      --blue: #64a8ff;
      --green: #58b66b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Segoe UI, system-ui, sans-serif;
      font-size: 15px;
      line-height: 1.45;
    }}
    main {{ width: min(860px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; letter-spacing: 0; }}
    .meta {{ color: var(--muted); margin-bottom: 18px; }}
    section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-top: 14px; }}
    dl {{ display: grid; grid-template-columns: 140px 1fr; gap: 8px 12px; margin: 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; }}
    textarea {{
      width: 100%;
      min-height: 320px;
      resize: vertical;
      background: #0d1117;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
      font: 14px Consolas, monospace;
      line-height: 1.45;
    }}
    button, a {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      border-radius: 6px;
      border: 0;
      padding: 9px 12px;
      background: var(--blue);
      color: #07111f;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }}
    .copied {{ color: var(--green); min-height: 22px; margin-top: 8px; }}
    .fit-image {{ display: block; max-width: 100%; margin-top: 12px; border: 1px solid var(--line); border-radius: 6px; }}
    @media (max-width: 620px) {{
      dl {{ grid-template-columns: 1fr; }}
      dt {{ margin-top: 8px; }}
    }}
  </style>
</head>
<body>
  <main data-listing="{listing_json}" data-draft="{draft_json}" data-fit="{fit_json}">
    <h1>{escape_html(listing.label)} {escape_html(listing.item_name)}</h1>
    <div class="meta">{escape_html(listing.location)} · {escape_html(listing.owner)} · {escape_html(listing.status)}</div>
    <section>
      <dl>
        <dt>Quantity</dt><dd>{listing.quantity:,}</dd>
        <dt>Category</dt><dd>{escape_html(listing.category_label)}</dd>
        <dt>Unit price</dt><dd>{escape_html(format_isk(listing.unit_price_isk) if listing.unit_price_isk is not None else "Quote")}</dd>
        <dt>Total</dt><dd>{escape_html(format_isk(listing.total_price_isk) if listing.total_price_isk is not None else "Quote")}</dd>
        <dt>Delivery</dt><dd>{escape_html(listing.delivery or "Not specified")}</dd>
        <dt>Fit image</dt><dd>{('<a href="' + escape_html(listing.fit_image_url) + '" target="_blank" rel="noreferrer">Open screenshot</a>') if listing.fit_image_url else 'Not provided'}</dd>
        <dt>Offer ID</dt><dd>{escape_html(listing.listing_id)}</dd>
      </dl>
    </section>
{image_section}
{fit_section}
    <section>
      <h2>EVE Mail Draft</h2>
      <textarea id="mail-draft" spellcheck="false">{escape_html(draft.to_dict()["combined"])}</textarea>
      <div class="actions">
        <button id="copy-mail" type="button">Copy Mail</button>
        <a href="/">Market Board</a>
      </div>
      <div id="copy-status" class="copied"></div>
    </section>
  </main>
  <script>
    const draft = JSON.parse(document.querySelector("main").dataset.draft);
    const fitData = JSON.parse(document.querySelector("main").dataset.fit);
    const textarea = document.querySelector("#mail-draft");
    const status = document.querySelector("#copy-status");
    document.querySelector("#copy-mail").addEventListener("click", async () => {{
      textarea.value = draft.combined;
      textarea.focus();
      textarea.select();
      try {{
        await navigator.clipboard.writeText(draft.combined);
        status.textContent = "Copied.";
      }} catch (error) {{
        document.execCommand("copy");
        status.textContent = "Selected and copied if your browser allowed it.";
      }}
    }});
    const copyFitButton = document.querySelector("#copy-fit");
    if (copyFitButton && fitData.fit) {{
      const fitTextarea = document.querySelector("#fit-block");
      const fitStatus = document.querySelector("#fit-copy-status");
      copyFitButton.addEventListener("click", async () => {{
        fitTextarea.focus();
        fitTextarea.select();
        try {{
          await navigator.clipboard.writeText(fitData.fit);
          fitStatus.textContent = "Fit copied.";
        }} catch (error) {{
          document.execCommand("copy");
          fitStatus.textContent = "Selected and copied if your browser allowed it.";
        }}
      }});
    }}
  </script>
</body>
</html>
"""


def render_not_found(message: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Offer not found</title></head>
<body><h1>Offer not found</h1><p>{escape_html(message)}</p><p><a href="/">Market Board</a></p></body>
</html>
"""


def render_flight_auth_result(message: str, *, ok: bool, details: Iterable[str] = ()) -> str:
    color = "#64c47d" if ok else "#e57466"
    detail_items = "\n".join(f"<li>{escape_html(item)}</li>" for item in details if item)
    details_block = f"<ul>{detail_items}</ul>" if detail_items else ""
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Flight Attendant ESI</title>
</head>
<body style="font-family: Segoe UI, system-ui, sans-serif; background:#080b0d; color:#edf4ef; margin:32px;">
  <main style="max-width:720px;">
    <h1 style="color:{color};">Flight Attendant ESI</h1>
    <p>{escape_html(message)}</p>
    {details_block}
    <p><a style="color:#61c7d9;" href="/#flight">Return to Flight Attendant</a></p>
  </main>
</body>
</html>
"""


def run_server(args: argparse.Namespace) -> int:
    store = MarketStore(args.market_db_path)
    url_host = url_host_for_bind(args.host)
    public_base_url = (args.public_base_url or f"http://{url_host}:{args.port}").rstrip("/")
    sso_callback_url = args.sso_callback_url or f"http://{url_host}:{args.port}/flight/callback"
    sso_config = EveSsoConfig(
        client_id=args.sso_client_id,
        client_secret=args.sso_client_secret,
        callback_url=sso_callback_url,
        scopes=tuple(parse_csv(args.sso_scopes)) or DEFAULT_FLIGHT_ESI_SCOPES,
        esi_base_url=args.esi_base_url,
    )
    server = build_http_server(
        args.host,
        args.port,
        store,
        public_base_url=public_base_url,
        discord_webhook_url=args.discord_webhook_url,
        discord_timeout_seconds=args.discord_timeout,
        discord_forum_posts=args.discord_forum_posts,
        discord_forum_tag_ids=parse_csv(args.discord_forum_tag_ids),
        discord_forum_tag_map=parse_forum_tag_map(args.discord_forum_tag_map),
        admin_token=args.admin_token,
        sso_config=sso_config,
        auth_state_store=AuthStateStore(),
        flight_session_store=FlightEsiSessionStore(),
    )
    url = f"http://{url_host}:{args.port}/"
    print(f"Corp market concierge listening at {url}")
    print(f"Market database: {args.market_db_path}")
    print(f"Public offer URL base: {public_base_url}")
    if sso_config.enabled:
        print(f"Flight Attendant ESI enabled. Callback URL: {sso_config.callback_url}")
        print(f"Flight Attendant ESI scopes: {', '.join(sso_config.scopes)}")
        print("Flight Attendant access tokens are kept in server memory only.")
    else:
        print("Flight Attendant ESI is not configured.")
        print(f"Register callback URL: {sso_callback_url}")
        print("Then start with --sso-client-id and --sso-client-secret.")
    if args.discord_webhook_url:
        print("Discord webhook posting is enabled.")
        if args.discord_forum_posts:
            print("Discord forum mode is enabled; each offer will create a forum post/thread.")
    else:
        print("Discord webhook posting is disabled. Set --discord-webhook-url to post new offers.")
    if args.admin_token:
        print("Remote offer creation/status writes require the market admin token.")
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the EVE corp market concierge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start the local corp market web app.")
    serve.add_argument("--host", default="127.0.0.1", help="Bind address. Use 0.0.0.0 for LAN sharing.")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Web app port.")
    serve.add_argument(
        "--market-db-path",
        type=Path,
        default=DEFAULT_MARKET_DB_PATH,
        help="Local SQLite file used to persist corp market listings.",
    )
    serve.add_argument(
        "--discord-webhook-url",
        default=os.environ.get("CORP_MARKET_DISCORD_WEBHOOK_URL", ""),
        help="Discord channel webhook URL used to post new offers.",
    )
    serve.add_argument(
        "--discord-timeout",
        type=float,
        default=DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
        help="Seconds to wait for Discord webhook responses.",
    )
    serve.add_argument(
        "--discord-forum-posts",
        action="store_true",
        help="Use Discord forum/media webhook mode by creating a new post/thread for each offer.",
    )
    serve.add_argument(
        "--discord-forum-tag-ids",
        default=os.environ.get("CORP_MARKET_DISCORD_FORUM_TAG_IDS", ""),
        help="Optional comma-separated Discord forum tag ids to apply to created posts.",
    )
    serve.add_argument(
        "--discord-forum-tag-map",
        default=os.environ.get("CORP_MARKET_DISCORD_FORUM_TAG_MAP", ""),
        help=(
            "Optional tag mapping like sell:TAGID,want:TAGID,ships:TAGID,pi:TAGID. "
            "Keys can be listing types, WTS/WTB, or category names."
        ),
    )
    serve.add_argument(
        "--public-base-url",
        default=os.environ.get("CORP_MARKET_PUBLIC_BASE_URL", ""),
        help="Base URL placed in Discord offer links. Use a LAN or tunnel URL for corp access.",
    )
    serve.add_argument(
        "--admin-token",
        default=os.environ.get("CORP_MARKET_ADMIN_TOKEN", ""),
        help="Optional token for remote offer creation and status changes.",
    )
    serve.add_argument(
        "--sso-client-id",
        default=os.environ.get("CORP_MARKET_SSO_CLIENT_ID", os.environ.get("EVE_SSO_CLIENT_ID", "")),
        help="EVE SSO application client ID for Flight Attendant ESI login.",
    )
    serve.add_argument(
        "--sso-client-secret",
        default=os.environ.get("CORP_MARKET_SSO_CLIENT_SECRET", os.environ.get("EVE_SSO_CLIENT_SECRET", "")),
        help="EVE SSO application client secret for Flight Attendant ESI login.",
    )
    serve.add_argument(
        "--sso-callback-url",
        default=os.environ.get("CORP_MARKET_SSO_CALLBACK_URL", ""),
        help="Registered EVE SSO callback URL. Defaults to this board's /flight/callback URL.",
    )
    serve.add_argument(
        "--sso-scopes",
        default=os.environ.get("CORP_MARKET_SSO_SCOPES", " ".join(DEFAULT_FLIGHT_ESI_SCOPES)),
        help="Space or comma-separated EVE SSO scopes for Flight Attendant.",
    )
    serve.add_argument(
        "--esi-base-url",
        default=os.environ.get("CORP_MARKET_ESI_BASE_URL", DEFAULT_ESI_BASE_URL),
        help="Base ESI URL.",
    )
    serve.add_argument("--open-browser", action="store_true", help="Open the market board in your default browser.")
    serve.set_defaults(func=run_server)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except CorpMarketError as exc:
        print(f"Corp market error: {exc}", file=sys.stderr)
        return 1


def clean_text(value: Any, field: str, *, max_length: int, required: bool = False) -> str:
    text = SPACE_RE.sub(" ", str(value or "").strip())
    if required and not text:
        raise ValueError(f"{field} is required.")
    if len(text) > max_length:
        raise ValueError(f"{field} must be {max_length} characters or less.")
    return text


def clean_multiline(value: Any, field: str, *, max_length: int) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [SPACE_RE.sub(" ", line).strip() for line in raw.split("\n")]
    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line)
        previous_blank = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    text = "\n".join(cleaned)
    if len(text) > max_length:
        raise ValueError(f"{field} must be {max_length} characters or less.")
    return text


def clean_choice(value: Any, allowed: set[str], field: str) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}.")
    return text


def clean_positive_int(value: Any, field: str) -> int:
    try:
        number = int(str(value or "").replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be a whole number.") from exc
    if number <= 0:
        raise ValueError(f"{field} must be greater than zero.")
    return number


def clean_optional_isk(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    amount = parse_isk_amount(value)
    if amount < 0:
        raise ValueError("unit_price_isk cannot be negative.")
    return amount


def clean_optional_url(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be a full http or https URL.")
    if len(text) > 1000:
        raise ValueError(f"{field} must be 1000 characters or less.")
    return text


def parse_isk_amount(value: Any) -> float:
    raw = str(value or "").replace(",", "").strip()
    match = ISK_AMOUNT_RE.match(raw)
    if not match:
        raise ValueError("ISK amounts can be plain numbers or use k, m, or b suffixes.")
    number = float(match.group("number"))
    suffix = match.group("suffix").lower()
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]
    return number * multiplier


def clean_listing_id(value: Any) -> str:
    listing_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", listing_id):
        raise ValueError("listing_id is invalid.")
    return listing_id


def clean_discord_snowflake(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not DISCORD_SNOWFLAKE_RE.fullmatch(text):
        raise CorpMarketError(f"{field} must be a Discord numeric ID.")
    return text


def format_isk(value: float | None) -> str:
    if value is None:
        return "quote"
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}b ISK"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}m ISK"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}k ISK"
    return f"{value:,.0f} ISK"


def listing_public_url(listing_id: str, public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/offers/{quote(clean_listing_id(listing_id))}"


def first_query_value(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0] if values else ""


def parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in re.split(r"[,\s]+", value) if item.strip())


def parse_forum_tag_map(value: str | None) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for item in parse_csv(value):
        if ":" not in item:
            raise CorpMarketError("Forum tag map entries must look like key:tag_id.")
        key, tag_id = item.split(":", 1)
        normalized_key = key.strip().lower()
        normalized_tag_id = tag_id.strip()
        if not normalized_key or not normalized_tag_id:
            raise CorpMarketError("Forum tag map entries must include both key and tag id.")
        result.setdefault(normalized_key, [])
        if normalized_tag_id not in result[normalized_key]:
            result[normalized_key].append(normalized_tag_id)
    return {key: tuple(values) for key, values in result.items()}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def future_iso(*, hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    host = str(handler.client_address[0])
    return host == "::1" or host.startswith("127.")


def request_cookie(handler: BaseHTTPRequestHandler, name: str) -> str:
    raw_cookie = handler.headers.get("Cookie", "")
    for part in raw_cookie.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return ""


def flight_session_cookie_header(session_id: str) -> str:
    return (
        f"{FLIGHT_SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={60 * 60}"
    )


def clear_flight_session_cookie_header() -> str:
    return f"{FLIGHT_SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def clean_token_ttl_seconds(value: Any) -> int:
    try:
        ttl = int(float(value or 20 * 60))
    except (TypeError, ValueError):
        ttl = 20 * 60
    ttl = max(60, min(ttl, 12 * 60 * 60))
    return max(60, ttl - 30)


def url_host_for_bind(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", ""} else host


def shorten(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def escape_html(value: Any) -> str:
    return html.escape(str(value), quote=True)


if __name__ == "__main__":
    raise SystemExit(main())
