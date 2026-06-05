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
DEFAULT_INDUSTRY_RECIPE_CACHE_PATH = ROOT / "cache" / "eve_industry_recipes.json"
DEFAULT_ROUTE_GRAPH_CACHE_PATH = ROOT / "cache" / "eve_route_graph.json"
DEFAULT_PORT = 8770
DEFAULT_MAX_NOTES_LENGTH = 5000
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 10.0
DEFAULT_FLIGHT_MAX_JUMPS = 5
MAX_FLIGHT_MAX_JUMPS = 25
MAX_FLIGHT_BUYER_SCAN_PRODUCTS = 40
MAX_FLIGHT_BUYER_SCAN_REGIONS = 8
MAX_FLIGHT_PROFIT_MATERIAL_TYPES = 120
MAX_FLIGHT_HAUL_MATERIAL_TYPES = 80
MAX_FLIGHT_HAUL_OPPORTUNITIES = 20
DEFAULT_HAUL_DESTINATION_SYSTEM = "Jita"
DEFAULT_HAUL_DETOUR_JUMPS = 1
MAX_HAUL_DETOUR_JUMPS = 5
DEFAULT_HAUL_CARGO_M3 = 10_000.0
MAX_HAUL_CARGO_M3 = 10_000_000.0
FLIGHT_LOCATION_SCOPE = "esi-location.read_location.v1"
FLIGHT_ASSETS_SCOPE = "esi-assets.read_assets.v1"
FLIGHT_BLUEPRINTS_SCOPE = "esi-characters.read_blueprints.v1"
FLIGHT_SKILLS_SCOPE = "esi-skills.read_skills.v1"
FLIGHT_STANDINGS_SCOPE = "esi-characters.read_standings.v1"
DEFAULT_FLIGHT_ESI_SCOPES = (
    FLIGHT_LOCATION_SCOPE,
    FLIGHT_ASSETS_SCOPE,
    FLIGHT_BLUEPRINTS_SCOPE,
    FLIGHT_SKILLS_SCOPE,
    FLIGHT_STANDINGS_SCOPE,
)
ACCOUNTING_SKILL_TYPE_ID = 16622
BASE_SALES_TAX_RATE = 0.075
ACCOUNTING_SALES_TAX_REDUCTION_PER_LEVEL = 0.11
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
class IndustryMaterial:
    type_id: int
    name: str
    quantity: int
    volume_m3: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IndustryMaterial | None":
        try:
            type_id = int(payload.get("type_id") or payload.get("typeID") or 0)
            quantity = int(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            return None
        name = str(payload.get("name") or "").strip()
        if type_id <= 0 or quantity <= 0:
            return None
        volume_m3 = clean_optional_float(payload.get("volume_m3") or payload.get("volume"))
        if volume_m3 is not None and volume_m3 <= 0:
            volume_m3 = None
        return cls(type_id=type_id, name=name or f"Type {type_id}", quantity=quantity, volume_m3=volume_m3)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type_id": self.type_id, "name": self.name, "quantity": self.quantity}
        if self.volume_m3 is not None:
            payload["volume_m3"] = self.volume_m3
        return payload


@dataclass(frozen=True)
class IndustryRecipe:
    blueprint_type_id: int
    blueprint_name: str
    product_type_id: int
    product_name: str
    product_quantity: int
    materials: tuple[IndustryMaterial, ...]
    manufacturing_time_seconds: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IndustryRecipe | None":
        try:
            blueprint_type_id = int(payload.get("blueprint_type_id") or payload.get("blueprintTypeID") or 0)
            product_type_id = int(payload.get("product_type_id") or payload.get("productTypeID") or 0)
            product_quantity = int(payload.get("product_quantity") or payload.get("quantity") or 0)
            manufacturing_time = int(payload.get("manufacturing_time_seconds") or payload.get("time") or 0)
        except (TypeError, ValueError):
            return None
        materials = tuple(
            material
            for item in payload.get("materials", [])
            if isinstance(item, dict)
            for material in [IndustryMaterial.from_dict(item)]
            if material is not None
        )
        if blueprint_type_id <= 0 or product_type_id <= 0 or product_quantity <= 0 or not materials:
            return None
        blueprint_name = str(payload.get("blueprint_name") or "").strip() or f"Blueprint {blueprint_type_id}"
        product_name = str(payload.get("product_name") or "").strip() or f"Type {product_type_id}"
        return cls(
            blueprint_type_id=blueprint_type_id,
            blueprint_name=blueprint_name,
            product_type_id=product_type_id,
            product_name=product_name,
            product_quantity=product_quantity,
            materials=materials,
            manufacturing_time_seconds=max(0, manufacturing_time),
        )


@dataclass(frozen=True)
class OwnedBlueprint:
    type_id: int
    material_efficiency: int
    time_efficiency: int
    runs: int
    quantity: int

    @property
    def is_original(self) -> bool:
        return self.quantity == -1

    @property
    def is_copy(self) -> bool:
        return self.quantity == -2

    @property
    def usable_for_one_run(self) -> bool:
        return self.is_original or self.runs > 0

    @property
    def kind(self) -> str:
        if self.is_original:
            return "Original"
        if self.is_copy:
            return "Copy"
        return "Blueprint"

    @property
    def limited_runs(self) -> int | None:
        return None if self.is_original else max(0, self.runs)


@dataclass(frozen=True)
class IndustryRecipeCache:
    path: Path
    available: bool
    build_number: int | None = None
    release_date: str = ""
    generated_at: str = ""
    recipes: dict[int, IndustryRecipe] | None = None
    error: str = ""

    @property
    def recipe_count(self) -> int:
        return len(self.recipes or {})

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "path": str(self.path),
            "build_number": self.build_number,
            "release_date": self.release_date,
            "generated_at": self.generated_at,
            "recipe_count": self.recipe_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class RouteSystem:
    solar_system_id: int
    name: str
    region_id: int | None = None
    constellation_id: int | None = None
    security_status: float | None = None
    security_class: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RouteSystem | None":
        try:
            solar_system_id = int(payload.get("solar_system_id") or payload.get("solarSystemID") or 0)
        except (TypeError, ValueError):
            return None
        if solar_system_id <= 0:
            return None
        return cls(
            solar_system_id=solar_system_id,
            name=str(payload.get("name") or f"System {solar_system_id}"),
            region_id=clean_optional_int(payload.get("region_id") or payload.get("regionID")),
            constellation_id=clean_optional_int(payload.get("constellation_id") or payload.get("constellationID")),
            security_status=clean_optional_float(payload.get("security_status") or payload.get("securityStatus")),
            security_class=str(payload.get("security_class") or payload.get("securityClass") or ""),
        )

    def to_dict(self, *, jumps: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "solar_system_id": self.solar_system_id,
            "name": self.name,
            "region_id": self.region_id,
            "constellation_id": self.constellation_id,
            "security_status": self.security_status,
            "security_class": self.security_class,
        }
        if jumps is not None:
            payload["jumps"] = jumps
        return payload


@dataclass(frozen=True)
class RouteGraphCache:
    path: Path
    available: bool
    build_number: int | None = None
    release_date: str = ""
    generated_at: str = ""
    systems: dict[int, RouteSystem] | None = None
    adjacency: dict[int, tuple[int, ...]] | None = None
    error: str = ""

    @property
    def system_count(self) -> int:
        return len(self.systems or {})

    @property
    def edge_count(self) -> int:
        return sum(len(targets) for targets in (self.adjacency or {}).values())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "path": str(self.path),
            "build_number": self.build_number,
            "release_date": self.release_date,
            "generated_at": self.generated_at,
            "system_count": self.system_count,
            "edge_count": self.edge_count,
            "error": self.error,
        }


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


def fetch_flight_skills(config: EveSsoConfig, session: FlightEsiSession) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_SKILLS_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    payload = get_json(
        f"{base_url}/characters/{session.character_id}/skills/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=flight_esi_headers(session.access_token),
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        raise CorpMarketError("ESI skills endpoint returned unexpected data.")
    return payload


def fetch_market_orders(
    config: EveSsoConfig,
    *,
    region_id: int,
    type_id: int,
    order_type: str,
) -> list[dict[str, Any]]:
    clean_order_type = str(order_type).strip().lower()
    if clean_order_type not in {"buy", "sell"}:
        raise CorpMarketError(f"Unsupported market order type: {order_type}")
    base_url = config.esi_base_url.rstrip("/")
    url = add_query_params(
        f"{base_url}/markets/{region_id}/orders/?datasource=tranquility",
        {"order_type": clean_order_type, "type_id": str(type_id)},
    )
    return get_esi_json_pages(
        url,
        headers=flight_esi_headers(),
        label=f"ESI market {clean_order_type} orders for type {type_id} in region {region_id}",
    )


def fetch_market_buy_orders(config: EveSsoConfig, *, region_id: int, type_id: int) -> list[dict[str, Any]]:
    return fetch_market_orders(config, region_id=region_id, type_id=type_id, order_type="buy")


def fetch_market_sell_orders(config: EveSsoConfig, *, region_id: int, type_id: int) -> list[dict[str, Any]]:
    return fetch_market_orders(config, region_id=region_id, type_id=type_id, order_type="sell")


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
    recipe_cache = load_industry_recipe_cache()
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "industry": summarize_flight_industry(
            config,
            blueprints=blueprints,
            assets=assets,
            recipe_cache=recipe_cache,
        ),
    }


def summarize_flight_industry(
    config: EveSsoConfig,
    *,
    blueprints: Iterable[dict[str, Any]],
    assets: Iterable[dict[str, Any]],
    recipe_cache: IndustryRecipeCache | None = None,
) -> dict[str, Any]:
    blueprint_items = [item for item in blueprints if isinstance(item, dict)]
    asset_items = [item for item in assets if isinstance(item, dict)]
    blueprint_type_counts = count_by_type_id(blueprint_items)
    best_blueprints_by_type = best_owned_blueprints_by_type(blueprint_items)
    asset_quantities = quantity_by_type_id(asset_items)
    top_blueprints = sorted(blueprint_type_counts.items(), key=lambda item: item[1], reverse=True)[:5]
    top_assets = sorted(asset_quantities.items(), key=lambda item: item[1], reverse=True)[:5]
    names = fetch_universe_names(
        config,
        [type_id for type_id, _count in top_blueprints] + [type_id for type_id, _quantity in top_assets],
    )
    cache = recipe_cache or load_industry_recipe_cache()
    recipes = cache.recipes or {}
    owned_blueprint_type_ids = set(blueprint_type_counts)
    known_recipe_type_ids = owned_blueprint_type_ids.intersection(recipes)
    buildability = build_recipe_buildability(
        blueprints=blueprint_items,
        asset_quantities=asset_quantities,
        recipes=recipes,
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
                    "name": blueprint_display_name(type_id, names=names, recipes=recipes),
                    "count": count,
                    "recipe_known": type_id in recipes,
                    "product_name": recipes[type_id].product_name if type_id in recipes else "",
                    "product_quantity": recipes[type_id].product_quantity if type_id in recipes else 0,
                    "material_count": len(recipes[type_id].materials) if type_id in recipes else 0,
                    "best_material_efficiency": (
                        best_blueprints_by_type[type_id].material_efficiency if type_id in best_blueprints_by_type else None
                    ),
                    "best_time_efficiency": (
                        best_blueprints_by_type[type_id].time_efficiency if type_id in best_blueprints_by_type else None
                    ),
                    "best_runs": (
                        best_blueprints_by_type[type_id].limited_runs if type_id in best_blueprints_by_type else None
                    ),
                    "best_kind": best_blueprints_by_type[type_id].kind if type_id in best_blueprints_by_type else "",
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
        "recipes": {
            **cache.to_public_dict(),
            "known_blueprint_types": len(known_recipe_type_ids),
            "missing_blueprint_types": max(0, len(owned_blueprint_type_ids) - len(known_recipe_type_ids)),
        },
        "buildability": buildability,
        "next_step": industry_next_step(
            cache=cache,
            owned_blueprint_type_count=len(owned_blueprint_type_ids),
            known_blueprint_type_count=len(known_recipe_type_ids),
        ),
    }


def load_industry_recipe_cache(cache_path: Path = DEFAULT_INDUSTRY_RECIPE_CACHE_PATH) -> IndustryRecipeCache:
    path = Path(cache_path)
    if not path.exists():
        return IndustryRecipeCache(path=path, available=False, error="Recipe cache file is missing.")
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return IndustryRecipeCache(path=path, available=False, error=f"Recipe cache could not be read: {exc}")
    if not isinstance(payload, dict):
        return IndustryRecipeCache(path=path, available=False, error="Recipe cache has unexpected format.")

    recipes_payload = payload.get("recipes")
    if not isinstance(recipes_payload, dict):
        return IndustryRecipeCache(path=path, available=False, error="Recipe cache is missing recipes.")
    recipes: dict[int, IndustryRecipe] = {}
    for key, item in recipes_payload.items():
        if not isinstance(item, dict):
            continue
        recipe = IndustryRecipe.from_dict(item)
        if recipe is None:
            continue
        recipes[recipe.blueprint_type_id] = recipe
    if not recipes:
        return IndustryRecipeCache(path=path, available=False, error="Recipe cache has no usable manufacturing recipes.")
    try:
        build_number = int(payload.get("build_number") or payload.get("buildNumber") or 0) or None
    except (TypeError, ValueError):
        build_number = None
    return IndustryRecipeCache(
        path=path,
        available=True,
        build_number=build_number,
        release_date=str(payload.get("release_date") or payload.get("releaseDate") or ""),
        generated_at=str(payload.get("generated_at") or ""),
        recipes=recipes,
    )


def build_recipe_buildability(
    *,
    blueprints: Iterable[dict[str, Any]],
    asset_quantities: dict[int, int],
    recipes: dict[int, IndustryRecipe],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    ready_count = 0
    targets = owned_blueprint_product_targets(blueprints=blueprints, recipes=recipes)
    for target in targets:
        if not target.get("blueprint_usable", True):
            continue
        recipe = recipes.get(int(target["blueprint_type_id"]))
        if recipe is None:
            continue
        missing_materials = []
        covered_material_types = 0
        material_rows = adjusted_recipe_materials(
            recipe,
            material_efficiency=int(target.get("blueprint_material_efficiency") or 0),
            runs=1,
        )
        for material in material_rows:
            available_quantity = asset_quantities.get(int(material["type_id"]), 0)
            required_quantity = int(material["quantity"])
            shortage = max(0, required_quantity - available_quantity)
            if shortage:
                missing_materials.append(
                    {
                        "type_id": material["type_id"],
                        "name": material["name"],
                        "base_required": material["base_quantity"],
                        "required": required_quantity,
                        "available": available_quantity,
                        "shortage": shortage,
                    }
                )
            else:
                covered_material_types += 1
        can_build = not missing_materials
        if can_build:
            ready_count += 1
        candidates.append(
            {
                "blueprint_type_id": target["blueprint_type_id"],
                "blueprint_name": recipe.blueprint_name,
                "product_type_id": recipe.product_type_id,
                "product_name": recipe.product_name,
                "product_quantity": recipe.product_quantity,
                "owned_blueprints": target["owned_blueprints"],
                "owned_originals": target.get("owned_originals", 0),
                "owned_copies": target.get("owned_copies", 0),
                "blueprint_material_efficiency": target.get("blueprint_material_efficiency", 0),
                "blueprint_time_efficiency": target.get("blueprint_time_efficiency", 0),
                "blueprint_runs": target.get("blueprint_runs"),
                "blueprint_kind": target.get("blueprint_kind", "Blueprint"),
                "can_build_one_run": can_build,
                "required_material_types": len(recipe.materials),
                "covered_material_types": covered_material_types,
                "missing_material_types": len(missing_materials),
                "missing_materials": sorted(missing_materials, key=lambda item: item["shortage"], reverse=True)[:3],
            }
        )
    candidates.sort(
        key=lambda item: (
            0 if item["can_build_one_run"] else 1,
            item["missing_material_types"],
            -int(item["owned_blueprints"]),
            item["product_name"],
        )
    )
    return {
        "known_blueprint_types": len(candidates),
        "buildable_one_run_types": ready_count,
        "top_candidates": candidates[:5],
    }


def blueprint_display_name(
    type_id: int,
    *,
    names: dict[int, str],
    recipes: dict[int, IndustryRecipe],
) -> str:
    recipe = recipes.get(type_id)
    if recipe and recipe.blueprint_name:
        return recipe.blueprint_name
    return names.get(type_id) or f"Type {type_id}"


def industry_next_step(
    *,
    cache: IndustryRecipeCache,
    owned_blueprint_type_count: int,
    known_blueprint_type_count: int,
) -> str:
    if not cache.available:
        return "Recipe cache missing; static recipe analysis is waiting."
    if owned_blueprint_type_count <= 0:
        return f"Recipe cache build {cache.build_number or 'unknown'} is ready; no owned blueprint types were returned by ESI."
    return (
        f"Recipe cache build {cache.build_number or 'unknown'} matches "
        f"{known_blueprint_type_count} of {owned_blueprint_type_count} owned blueprint types. "
        "Market pricing is the next layer before profitability ranking."
    )


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


def load_route_graph_cache(cache_path: Path = DEFAULT_ROUTE_GRAPH_CACHE_PATH) -> RouteGraphCache:
    path = Path(cache_path)
    if not path.exists():
        return RouteGraphCache(path=path, available=False, error="Route graph cache file is missing.")
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return RouteGraphCache(path=path, available=False, error=f"Route graph cache could not be read: {exc}")
    if not isinstance(payload, dict):
        return RouteGraphCache(path=path, available=False, error="Route graph cache has unexpected format.")

    systems_payload = payload.get("systems")
    adjacency_payload = payload.get("adjacency")
    if not isinstance(systems_payload, dict) or not isinstance(adjacency_payload, dict):
        return RouteGraphCache(path=path, available=False, error="Route graph cache is missing systems or adjacency.")

    systems: dict[int, RouteSystem] = {}
    for item in systems_payload.values():
        if not isinstance(item, dict):
            continue
        system = RouteSystem.from_dict(item)
        if system is not None:
            systems[system.solar_system_id] = system
    adjacency: dict[int, tuple[int, ...]] = {}
    for key, values in adjacency_payload.items():
        try:
            system_id = int(key)
        except (TypeError, ValueError):
            continue
        if not isinstance(values, list):
            continue
        neighbors = []
        for value in values:
            try:
                neighbor_id = int(value)
            except (TypeError, ValueError):
                continue
            if neighbor_id > 0:
                neighbors.append(neighbor_id)
        adjacency[system_id] = tuple(sorted(set(neighbors)))
    if not systems or not adjacency:
        return RouteGraphCache(path=path, available=False, error="Route graph cache has no usable jump data.")
    return RouteGraphCache(
        path=path,
        available=True,
        build_number=clean_optional_int(payload.get("build_number") or payload.get("buildNumber")),
        release_date=str(payload.get("release_date") or payload.get("releaseDate") or ""),
        generated_at=str(payload.get("generated_at") or ""),
        systems=systems,
        adjacency=adjacency,
    )


def build_nearby_systems_payload(
    *,
    current_solar_system_id: int,
    max_jumps: int,
    route_cache: RouteGraphCache | None = None,
) -> dict[str, Any]:
    clean_max_jumps = clamp_flight_max_jumps(max_jumps)
    cache = route_cache or load_route_graph_cache()
    payload: dict[str, Any] = {
        **cache.to_public_dict(),
        "max_jumps": clean_max_jumps,
        "current_solar_system_id": current_solar_system_id,
        "current_system_name": "",
        "reachable_system_count": 0,
        "systems": [],
    }
    if not cache.available:
        return payload
    systems = cache.systems or {}
    adjacency = cache.adjacency or {}
    current = systems.get(current_solar_system_id)
    if current is None:
        payload["available"] = False
        payload["error"] = f"Current system {current_solar_system_id} is not in the route graph cache."
        return payload
    jump_distances = jump_distances_within(
        start_system_id=current_solar_system_id,
        max_jumps=clean_max_jumps,
        adjacency=adjacency,
    )
    nearby = []
    for system_id, jumps in sorted(jump_distances.items(), key=lambda item: (item[1], systems.get(item[0], current).name)):
        system = systems.get(system_id)
        if system is not None:
            nearby.append(system.to_dict(jumps=jumps))
    payload["current_system_name"] = current.name
    payload["reachable_system_count"] = len(nearby)
    payload["systems"] = nearby[:20]
    return payload


def build_flight_buyers_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession,
    max_jumps: int = DEFAULT_FLIGHT_MAX_JUMPS,
) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_LOCATION_SCOPE, FLIGHT_BLUEPRINTS_SCOPE))
    location = fetch_flight_location(config, session)
    current_solar_system_id = int(location.get("solar_system_id") or 0)
    route_cache = load_route_graph_cache()
    nearby_systems = build_nearby_systems_payload(
        current_solar_system_id=current_solar_system_id,
        max_jumps=max_jumps,
        route_cache=route_cache,
    )
    if not nearby_systems.get("available"):
        raise CorpMarketError(str(nearby_systems.get("error") or "Route graph cache is not available."))

    recipe_cache = load_industry_recipe_cache()
    if not recipe_cache.available:
        raise CorpMarketError(recipe_cache.error or "Recipe cache is not available.")
    blueprints = fetch_flight_blueprints(config, session)
    buyers = scan_buyers_for_owned_blueprints(
        config=config,
        blueprints=blueprints,
        recipe_cache=recipe_cache,
        route_cache=route_cache,
        current_solar_system_id=current_solar_system_id,
        max_jumps=max_jumps,
    )
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "location": location,
        "nearby_systems": nearby_systems,
        "buyers": buyers,
    }


def build_flight_profitability_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession,
    max_jumps: int = DEFAULT_FLIGHT_MAX_JUMPS,
) -> dict[str, Any]:
    require_flight_scopes(
        session,
        (FLIGHT_LOCATION_SCOPE, FLIGHT_ASSETS_SCOPE, FLIGHT_BLUEPRINTS_SCOPE, FLIGHT_SKILLS_SCOPE),
    )
    location = fetch_flight_location(config, session)
    current_solar_system_id = int(location.get("solar_system_id") or 0)
    route_cache = load_route_graph_cache()
    nearby_systems = build_nearby_systems_payload(
        current_solar_system_id=current_solar_system_id,
        max_jumps=max_jumps,
        route_cache=route_cache,
    )
    if not nearby_systems.get("available"):
        raise CorpMarketError(str(nearby_systems.get("error") or "Route graph cache is not available."))

    recipe_cache = load_industry_recipe_cache()
    if not recipe_cache.available:
        raise CorpMarketError(recipe_cache.error or "Recipe cache is not available.")
    blueprints = fetch_flight_blueprints(config, session)
    assets = fetch_flight_assets(config, session)
    skills = fetch_flight_skills(config, session)
    sales_tax = build_sales_tax_profile(skills)
    profitability = rank_profitability_for_owned_blueprints(
        config=config,
        blueprints=blueprints,
        assets=assets,
        sales_tax=sales_tax,
        recipe_cache=recipe_cache,
        route_cache=route_cache,
        current_solar_system_id=current_solar_system_id,
        max_jumps=max_jumps,
    )
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "location": location,
        "nearby_systems": nearby_systems,
        "profitability": profitability,
    }


def build_flight_hauling_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession,
    destination_name: str = DEFAULT_HAUL_DESTINATION_SYSTEM,
    detour_jumps: int = DEFAULT_HAUL_DETOUR_JUMPS,
    cargo_capacity_m3: float = DEFAULT_HAUL_CARGO_M3,
) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_LOCATION_SCOPE, FLIGHT_SKILLS_SCOPE))
    location = fetch_flight_location(config, session)
    current_solar_system_id = int(location.get("solar_system_id") or 0)
    route_cache = load_route_graph_cache()
    if not route_cache.available:
        raise CorpMarketError(route_cache.error or "Route graph cache is not available.")
    recipe_cache = load_industry_recipe_cache()
    if not recipe_cache.available:
        raise CorpMarketError(recipe_cache.error or "Recipe cache is not available.")
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    origin = systems.get(current_solar_system_id)
    if origin is None:
        raise CorpMarketError(f"Current system {current_solar_system_id} is not in the route graph cache.")
    destination = resolve_route_system(route_cache, destination_name)
    if destination is None:
        raise CorpMarketError(f"Destination system {destination_name!r} was not found in the route graph cache.")
    route_path = shortest_route_path(
        start_system_id=origin.solar_system_id,
        destination_system_id=destination.solar_system_id,
        adjacency=adjacency,
    )
    if not route_path:
        raise CorpMarketError(f"No stargate route from {origin.name} to {destination.name} was found.")
    skills = fetch_flight_skills(config, session)
    sales_tax = build_sales_tax_profile(skills)
    opportunities = scan_route_hauling_opportunities(
        config=config,
        recipe_cache=recipe_cache,
        route_cache=route_cache,
        origin_solar_system_id=origin.solar_system_id,
        destination_solar_system_id=destination.solar_system_id,
        route_path=route_path,
        detour_jumps=detour_jumps,
        cargo_capacity_m3=cargo_capacity_m3,
        sales_tax=sales_tax,
    )
    route_systems = [systems[system_id].to_dict(jumps=index) for index, system_id in enumerate(route_path) if system_id in systems]
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "location": location,
        "route": {
            "origin": origin.to_dict(jumps=0),
            "destination": destination.to_dict(jumps=max(0, len(route_path) - 1)),
            "destination_query": destination_name,
            "route_jumps": max(0, len(route_path) - 1),
            "detour_jumps": clamp_haul_detour_jumps(detour_jumps),
            "cargo_capacity_m3": clamp_haul_cargo_m3(cargo_capacity_m3),
            "systems": route_systems,
        },
        "hauling": opportunities,
    }


def scan_buyers_for_owned_blueprints(
    *,
    config: EveSsoConfig,
    blueprints: Iterable[dict[str, Any]],
    recipe_cache: IndustryRecipeCache,
    route_cache: RouteGraphCache,
    current_solar_system_id: int,
    max_jumps: int,
) -> dict[str, Any]:
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    jump_distances = jump_distances_within(
        start_system_id=current_solar_system_id,
        max_jumps=clamp_flight_max_jumps(max_jumps),
        adjacency=adjacency,
    )
    region_ids = sorted(
        {
            system.region_id
            for system_id, system in systems.items()
            if system_id in jump_distances and system.region_id is not None
        }
    )
    region_truncated = len(region_ids) > MAX_FLIGHT_BUYER_SCAN_REGIONS
    scan_region_ids = region_ids[:MAX_FLIGHT_BUYER_SCAN_REGIONS]

    product_targets = owned_blueprint_product_targets(blueprints=blueprints, recipes=recipe_cache.recipes or {})
    product_truncated = len(product_targets) > MAX_FLIGHT_BUYER_SCAN_PRODUCTS
    scan_targets = product_targets[:MAX_FLIGHT_BUYER_SCAN_PRODUCTS]

    products = []
    total_order_count = 0
    scan_errors = []
    for target in scan_targets:
        product_orders = []
        for region_id in scan_region_ids:
            try:
                raw_orders = fetch_market_buy_orders(config, region_id=region_id, type_id=target["product_type_id"])
            except CorpMarketError as exc:
                scan_errors.append(
                    {
                        "product_type_id": target["product_type_id"],
                        "product_name": target["product_name"],
                        "region_id": region_id,
                        "error": str(exc),
                    }
                )
                continue
            for order in raw_orders:
                record = build_buyer_order_record(
                    order,
                    target=target,
                    systems=systems,
                    jump_distances=jump_distances,
                    region_id=region_id,
                )
                if record is not None:
                    product_orders.append(record)
        product_orders.sort(key=lambda item: (-float(item["price"]), int(item["jumps"]), -int(item["volume_remain"])))
        total_order_count += len(product_orders)
        products.append(
            {
                **target,
                "order_count": len(product_orders),
                "best_order": product_orders[0] if product_orders else None,
                "top_orders": product_orders[:3],
            }
        )
    products.sort(
        key=lambda item: (
            0 if item["best_order"] else 1,
            item["product_name"],
        )
    )
    return {
        "max_jumps": clamp_flight_max_jumps(max_jumps),
        "reachable_system_count": len(jump_distances),
        "regions_scanned": len(scan_region_ids),
        "total_regions_in_range": len(region_ids),
        "region_truncated": region_truncated,
        "scanned_products": len(scan_targets),
        "total_known_products": len(product_targets),
        "product_truncated": product_truncated,
        "order_count": total_order_count,
        "products_with_buyers": sum(1 for item in products if item["best_order"]),
        "products": products[:20],
        "errors": scan_errors[:10],
        "identity_note": "ESI market orders do not expose buyer character names; these are public buy orders.",
    }


def rank_profitability_for_owned_blueprints(
    *,
    config: EveSsoConfig,
    blueprints: Iterable[dict[str, Any]],
    assets: Iterable[dict[str, Any]],
    sales_tax: dict[str, Any],
    recipe_cache: IndustryRecipeCache,
    route_cache: RouteGraphCache,
    current_solar_system_id: int,
    max_jumps: int,
) -> dict[str, Any]:
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    jump_distances = jump_distances_within(
        start_system_id=current_solar_system_id,
        max_jumps=clamp_flight_max_jumps(max_jumps),
        adjacency=adjacency,
    )
    region_ids = sorted(
        {
            system.region_id
            for system_id, system in systems.items()
            if system_id in jump_distances and system.region_id is not None
        }
    )
    region_truncated = len(region_ids) > MAX_FLIGHT_BUYER_SCAN_REGIONS
    scan_region_ids = region_ids[:MAX_FLIGHT_BUYER_SCAN_REGIONS]

    recipes = recipe_cache.recipes or {}
    product_targets = owned_blueprint_product_targets(blueprints=blueprints, recipes=recipes)
    product_truncated = len(product_targets) > MAX_FLIGHT_BUYER_SCAN_PRODUCTS
    scan_targets = product_targets[:MAX_FLIGHT_BUYER_SCAN_PRODUCTS]
    asset_quantities = quantity_by_type_id(item for item in assets if isinstance(item, dict))

    material_type_counts: dict[int, int] = {}
    for target in scan_targets:
        recipe = recipes.get(int(target["blueprint_type_id"]))
        if recipe is None:
            continue
        for material in recipe.materials:
            material_type_counts[material.type_id] = material_type_counts.get(material.type_id, 0) + 1
    material_type_ids = sorted(material_type_counts, key=lambda type_id: (-material_type_counts[type_id], type_id))
    material_truncated = len(material_type_ids) > MAX_FLIGHT_PROFIT_MATERIAL_TYPES
    scan_material_type_ids = material_type_ids[:MAX_FLIGHT_PROFIT_MATERIAL_TYPES]

    best_buyers, product_order_count, product_errors = scan_best_reachable_market_orders(
        config=config,
        type_ids=[int(target["product_type_id"]) for target in scan_targets],
        region_ids=scan_region_ids,
        systems=systems,
        jump_distances=jump_distances,
        order_type="buy",
    )
    best_material_sells, material_order_count, material_errors = scan_best_reachable_market_orders(
        config=config,
        type_ids=scan_material_type_ids,
        region_ids=scan_region_ids,
        systems=systems,
        jump_distances=jump_distances,
        order_type="sell",
    )

    products = []
    for target in scan_targets:
        if not target.get("blueprint_usable", True):
            continue
        recipe = recipes.get(int(target["blueprint_type_id"]))
        if recipe is None:
            continue
        buyer = best_buyers.get(int(target["product_type_id"]))
        revenue = float(buyer["price"]) * int(target["product_quantity"]) if buyer else None
        material_rows = []
        missing_materials = []
        priced_material_types = 0
        missing_priced_material_types = 0
        replacement_cost = 0.0
        missing_replacement_cost = 0.0
        recipe_materials = adjusted_recipe_materials(
            recipe,
            material_efficiency=int(target.get("blueprint_material_efficiency") or 0),
            runs=1,
        )
        for material in recipe_materials:
            material_type_id = int(material["type_id"])
            available = asset_quantities.get(material_type_id, 0)
            required = int(material["quantity"])
            missing = max(0, required - available)
            sell_order = best_material_sells.get(material_type_id)
            unit_price = float(sell_order["price"]) if sell_order else None
            material_replacement_cost = required * unit_price if unit_price is not None else None
            material_missing_cost = missing * unit_price if unit_price is not None else None
            if unit_price is not None:
                priced_material_types += 1
                replacement_cost += material_replacement_cost or 0.0
                if missing > 0:
                    missing_priced_material_types += 1
                missing_replacement_cost += material_missing_cost or 0.0
            material_row = {
                "type_id": material_type_id,
                "name": material["name"],
                "base_required": material["base_quantity"],
                "required": required,
                "available": available,
                "missing": missing,
                "unit_sell_price": unit_price,
                "replacement_cost": material_replacement_cost,
                "missing_cost": material_missing_cost,
                "sell_order": sell_order,
            }
            material_rows.append(material_row)
            if missing > 0:
                missing_materials.append(material_row)

        required_material_types = len(recipe_materials)
        missing_material_types = sum(1 for item in material_rows if int(item["missing"]) > 0)
        can_build = missing_material_types == 0
        all_material_prices_known = priced_material_types == required_material_types
        missing_prices_known = all(
            int(item["missing"]) <= 0 or item["unit_sell_price"] is not None
            for item in material_rows
        )
        replacement_profit = revenue - replacement_cost if revenue is not None and all_material_prices_known else None
        cash_profit = revenue - missing_replacement_cost if revenue is not None and missing_prices_known else None
        sales_tax_rate = clean_optional_float(sales_tax.get("rate")) or 0.0
        sales_tax_amount = revenue * sales_tax_rate if revenue is not None else None
        broker_fee_amount = 0.0 if revenue is not None else None
        net_revenue = revenue - sales_tax_amount - broker_fee_amount if revenue is not None else None
        taxed_replacement_profit = (
            net_revenue - replacement_cost if net_revenue is not None and all_material_prices_known else None
        )
        taxed_cash_profit = net_revenue - missing_replacement_cost if net_revenue is not None and missing_prices_known else None
        decision = profitability_decision(
            has_buyer=buyer is not None,
            can_build=can_build,
            missing_material_types=missing_material_types,
            replacement_profit=taxed_replacement_profit,
            cash_profit=taxed_cash_profit,
            all_material_prices_known=all_material_prices_known,
            missing_prices_known=missing_prices_known,
        )
        products.append(
            {
                **target,
                "decision": decision,
                "best_buyer": buyer,
                "product_revenue": revenue,
                "sales_tax_rate": sales_tax_rate,
                "sales_tax": sales_tax_amount,
                "broker_fee": broker_fee_amount,
                "net_revenue": net_revenue,
                "replacement_cost": replacement_cost if all_material_prices_known else None,
                "missing_replacement_cost": missing_replacement_cost if missing_prices_known else None,
                "replacement_profit": replacement_profit,
                "cash_profit": cash_profit,
                "replacement_margin_percent": profit_margin_percent(replacement_profit, revenue),
                "cash_margin_percent": profit_margin_percent(cash_profit, revenue),
                "taxed_replacement_profit": taxed_replacement_profit,
                "taxed_cash_profit": taxed_cash_profit,
                "taxed_replacement_margin_percent": profit_margin_percent(taxed_replacement_profit, revenue),
                "taxed_cash_margin_percent": profit_margin_percent(taxed_cash_profit, revenue),
                "profitable": taxed_replacement_profit is not None and taxed_replacement_profit > 0,
                "can_build_one_run": can_build,
                "required_material_types": required_material_types,
                "priced_material_types": priced_material_types,
                "missing_material_types": missing_material_types,
                "missing_priced_material_types": missing_priced_material_types,
                "materials": material_rows[:10],
                "missing_materials": sorted(missing_materials, key=lambda item: int(item["missing"]), reverse=True)[:5],
                "confidence": profitability_confidence(
                    has_buyer=buyer is not None,
                    can_build=can_build,
                    all_material_prices_known=all_material_prices_known,
                    missing_prices_known=missing_prices_known,
                ),
            }
        )

    products.sort(
        key=lambda item: (
            int((item.get("decision") or {}).get("rank") or 99),
            0 if item["best_buyer"] else 1,
            0 if item["taxed_replacement_profit"] is not None else 1,
            -profit_sort_value(item),
            0 if item["can_build_one_run"] else 1,
            item["product_name"],
        )
    )
    errors = product_errors + material_errors
    decision_counts: dict[str, int] = {}
    for item in products:
        decision_code = str((item.get("decision") or {}).get("code") or "unknown")
        decision_counts[decision_code] = decision_counts.get(decision_code, 0) + 1
    return {
        "max_jumps": clamp_flight_max_jumps(max_jumps),
        "reachable_system_count": len(jump_distances),
        "regions_scanned": len(scan_region_ids),
        "total_regions_in_range": len(region_ids),
        "region_truncated": region_truncated,
        "scanned_products": len(scan_targets),
        "total_known_products": len(product_targets),
        "product_truncated": product_truncated,
        "scanned_material_types": len(scan_material_type_ids),
        "total_material_types": len(material_type_ids),
        "material_truncated": material_truncated,
        "product_order_count": product_order_count,
        "material_order_count": material_order_count,
        "ranked_products": len(products),
        "products_with_buyers": sum(1 for item in products if item["best_buyer"]),
        "profitable_products": sum(1 for item in products if item["profitable"]),
        "buildable_now_products": sum(1 for item in products if item["can_build_one_run"]),
        "replacement_priced_products": sum(1 for item in products if item["replacement_profit"] is not None),
        "cash_priced_products": sum(1 for item in products if item["cash_profit"] is not None),
        "taxed_replacement_priced_products": sum(1 for item in products if item["taxed_replacement_profit"] is not None),
        "taxed_cash_priced_products": sum(1 for item in products if item["taxed_cash_profit"] is not None),
        "decision_counts": decision_counts,
        "products": products[:20],
        "errors": errors[:12],
        "sales_tax": sales_tax,
        "pricing_note": (
            "Visible profit subtracts sales tax from the buyer revenue using your Accounting skill. "
            "Details show the before-tax true profit and wallet gain. "
            "ESI market orders do not expose buyer character names."
        ),
    }


def scan_route_hauling_opportunities(
    *,
    config: EveSsoConfig,
    recipe_cache: IndustryRecipeCache,
    route_cache: RouteGraphCache,
    origin_solar_system_id: int,
    destination_solar_system_id: int,
    route_path: list[int],
    detour_jumps: int,
    cargo_capacity_m3: float,
    sales_tax: dict[str, Any],
) -> dict[str, Any]:
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    clean_detour_jumps = clamp_haul_detour_jumps(detour_jumps)
    clean_cargo_capacity_m3 = clamp_haul_cargo_m3(cargo_capacity_m3)
    pickup_detours = route_corridor_systems(
        route_path=route_path,
        adjacency=adjacency,
        detour_jumps=clean_detour_jumps,
        destination_system_id=destination_solar_system_id,
    )
    route_jumps = max(0, len(route_path) - 1)
    origin_distances = jump_distances_from(
        start_system_id=origin_solar_system_id,
        adjacency=adjacency,
        max_jumps=route_jumps + clean_detour_jumps * 2,
    )
    destination_distances = jump_distances_from(
        start_system_id=destination_solar_system_id,
        adjacency=adjacency,
        max_jumps=route_jumps + clean_detour_jumps * 2,
    )
    pickup_jump_distances = {
        system_id: jumps
        for system_id, jumps in origin_distances.items()
        if system_id in pickup_detours and system_id in systems
    }
    pickup_region_ranks: dict[int, int] = {}
    for system_id, jumps in pickup_jump_distances.items():
        region_id = systems[system_id].region_id
        if region_id is None:
            continue
        current_rank = pickup_region_ranks.get(region_id)
        if current_rank is None or jumps < current_rank:
            pickup_region_ranks[region_id] = jumps
    pickup_region_ids = [
        region_id
        for region_id, _rank in sorted(pickup_region_ranks.items(), key=lambda item: (item[1], item[0]))
    ]
    pickup_region_truncated = len(pickup_region_ids) > MAX_FLIGHT_BUYER_SCAN_REGIONS
    scan_pickup_region_ids = pickup_region_ids[:MAX_FLIGHT_BUYER_SCAN_REGIONS]
    destination = systems.get(destination_solar_system_id)
    if destination is None or destination.region_id is None:
        raise CorpMarketError("Destination system does not have a usable market region in the route graph cache.")

    material_targets = industry_material_trade_targets(recipe_cache.recipes or {})
    material_truncated = len(material_targets) > MAX_FLIGHT_HAUL_MATERIAL_TYPES
    scan_targets = material_targets[:MAX_FLIGHT_HAUL_MATERIAL_TYPES]
    sales_tax_rate = clean_optional_float(sales_tax.get("rate")) or 0.0

    opportunities = []
    total_sell_order_count = 0
    total_buy_order_count = 0
    errors = []
    for target in scan_targets:
        type_id = int(target["type_id"])
        sell_orders = []
        for region_id in scan_pickup_region_ids:
            try:
                raw_orders = fetch_market_sell_orders(config, region_id=region_id, type_id=type_id)
            except CorpMarketError as exc:
                errors.append({"order_type": "sell", "type_id": type_id, "region_id": region_id, "error": str(exc)})
                continue
            for order in raw_orders:
                record = build_reachable_market_order_record(
                    order,
                    systems=systems,
                    jump_distances=pickup_jump_distances,
                    region_id=region_id,
                    order_type="sell",
                )
                if record is not None:
                    sell_orders.append(record)
        try:
            raw_buy_orders = fetch_market_buy_orders(config, region_id=destination.region_id, type_id=type_id)
        except CorpMarketError as exc:
            errors.append(
                {"order_type": "buy", "type_id": type_id, "region_id": destination.region_id, "error": str(exc)}
            )
            raw_buy_orders = []
        buy_orders = []
        for order in raw_buy_orders:
            record = build_reachable_market_order_record(
                order,
                systems=systems,
                jump_distances={destination_solar_system_id: route_jumps},
                region_id=destination.region_id,
                order_type="buy",
            )
            if record is not None:
                buy_orders.append(record)
        sell_orders.sort(key=lambda item: market_order_sort_key(item, order_type="sell"))
        buy_orders.sort(key=lambda item: market_order_sort_key(item, order_type="buy"))
        total_sell_order_count += len(sell_orders)
        total_buy_order_count += len(buy_orders)
        if not sell_orders or not buy_orders:
            continue
        sell_order = sell_orders[0]
        buy_order = buy_orders[0]
        units = min(int(sell_order["volume_remain"]), int(buy_order["volume_remain"]))
        volume_m3 = target.get("volume_m3")
        cargo_limited = False
        if volume_m3 is not None and float(volume_m3) > 0:
            cargo_units = max(0, int((clean_cargo_capacity_m3 + 1e-9) / float(volume_m3)))
            units = min(units, cargo_units)
            cargo_limited = cargo_units < min(int(sell_order["volume_remain"]), int(buy_order["volume_remain"]))
        if units <= 0:
            continue
        gross_spread_per_unit = float(buy_order["price"]) - float(sell_order["price"])
        net_sell_price = float(buy_order["price"]) * (1.0 - sales_tax_rate)
        net_profit_per_unit = net_sell_price - float(sell_order["price"])
        total_net_profit = net_profit_per_unit * units
        if total_net_profit <= 0:
            continue
        pickup_system_id = int(sell_order["system_id"])
        origin_to_pickup = origin_distances.get(pickup_system_id)
        pickup_to_destination = destination_distances.get(pickup_system_id)
        extra_route_jumps = None
        if origin_to_pickup is not None and pickup_to_destination is not None:
            extra_route_jumps = max(0, origin_to_pickup + pickup_to_destination - route_jumps)
        opportunities.append(
            {
                "type_id": type_id,
                "item_name": target["name"],
                "recipe_count": target["recipe_count"],
                "volume_m3": volume_m3,
                "units": units,
                "cargo_limited": cargo_limited,
                "cargo_capacity_m3": clean_cargo_capacity_m3,
                "gross_spread_per_unit": gross_spread_per_unit,
                "net_profit_per_unit": net_profit_per_unit,
                "net_profit": total_net_profit,
                "margin_percent": profit_margin_percent(net_profit_per_unit, float(sell_order["price"])),
                "sales_tax_rate": sales_tax_rate,
                "pickup_order": sell_order,
                "destination_order": buy_order,
                "pickup_detour_jumps": pickup_detours.get(pickup_system_id, 0),
                "origin_to_pickup_jumps": origin_to_pickup,
                "pickup_to_destination_jumps": pickup_to_destination,
                "extra_route_jumps": extra_route_jumps,
            }
        )

    opportunities.sort(
        key=lambda item: (
            -float(item["net_profit"]),
            int(item.get("extra_route_jumps") if item.get("extra_route_jumps") is not None else 99),
            -float(item["net_profit_per_unit"]),
            item["item_name"],
        )
    )
    return {
        "route_jumps": route_jumps,
        "detour_jumps": clean_detour_jumps,
        "cargo_capacity_m3": clean_cargo_capacity_m3,
        "pickup_system_count": len(pickup_jump_distances),
        "pickup_regions_scanned": len(scan_pickup_region_ids),
        "pickup_regions_total": len(pickup_region_ids),
        "pickup_region_truncated": pickup_region_truncated,
        "destination_region_id": destination.region_id,
        "scanned_materials": len(scan_targets),
        "total_materials": len(material_targets),
        "material_truncated": material_truncated,
        "sell_order_count": total_sell_order_count,
        "buy_order_count": total_buy_order_count,
        "profitable_opportunities": len(opportunities),
        "opportunities": opportunities[:MAX_FLIGHT_HAUL_OPPORTUNITIES],
        "errors": errors[:12],
        "sales_tax": sales_tax,
        "pricing_note": (
            "This scan compares public sell orders along the route corridor with public buy orders in the destination "
            "system. Profit is after sales tax for selling into the destination buy order. The page does not place "
            "orders or verify station docking access."
        ),
    }


def scan_best_reachable_market_orders(
    *,
    config: EveSsoConfig,
    type_ids: Iterable[int],
    region_ids: Iterable[int],
    systems: dict[int, RouteSystem],
    jump_distances: dict[int, int],
    order_type: str,
) -> tuple[dict[int, dict[str, Any]], int, list[dict[str, Any]]]:
    best_orders: dict[int, dict[str, Any]] = {}
    total_order_count = 0
    errors = []
    clean_type_ids = [int(type_id) for type_id in type_ids if int(type_id) > 0]
    fetcher = fetch_market_buy_orders if order_type == "buy" else fetch_market_sell_orders
    for type_id in clean_type_ids:
        reachable_orders = []
        for region_id in region_ids:
            try:
                raw_orders = fetcher(config, region_id=region_id, type_id=type_id)
            except CorpMarketError as exc:
                errors.append({"order_type": order_type, "type_id": type_id, "region_id": region_id, "error": str(exc)})
                continue
            for order in raw_orders:
                record = build_reachable_market_order_record(
                    order,
                    systems=systems,
                    jump_distances=jump_distances,
                    region_id=region_id,
                    order_type=order_type,
                )
                if record is not None:
                    reachable_orders.append(record)
        reachable_orders.sort(key=lambda item: market_order_sort_key(item, order_type=order_type))
        total_order_count += len(reachable_orders)
        if reachable_orders:
            best_orders[type_id] = reachable_orders[0]
    return best_orders, total_order_count, errors


def market_order_sort_key(order: dict[str, Any], *, order_type: str) -> tuple[float, int, int]:
    price = float(order.get("price") or 0.0)
    price_rank = -price if order_type == "buy" else price
    return (price_rank, int(order.get("jumps") or 0), -int(order.get("volume_remain") or 0))


def profit_sort_value(item: dict[str, Any]) -> float:
    taxed_replacement_profit = item.get("taxed_replacement_profit")
    if taxed_replacement_profit is not None:
        return float(taxed_replacement_profit)
    taxed_cash_profit = item.get("taxed_cash_profit")
    if taxed_cash_profit is not None:
        return float(taxed_cash_profit)
    replacement_profit = item.get("replacement_profit")
    if replacement_profit is not None:
        return float(replacement_profit)
    cash_profit = item.get("cash_profit")
    if cash_profit is not None:
        return float(cash_profit)
    return float("-inf")


def profitability_confidence(
    *,
    has_buyer: bool,
    can_build: bool,
    all_material_prices_known: bool,
    missing_prices_known: bool,
) -> str:
    if not has_buyer:
        return "no-buyer"
    if all_material_prices_known:
        return "strong"
    if can_build:
        return "owned-materials"
    if missing_prices_known:
        return "partial-replacement"
    return "incomplete"


def profitability_decision(
    *,
    has_buyer: bool,
    can_build: bool,
    missing_material_types: int,
    replacement_profit: float | None,
    cash_profit: float | None,
    all_material_prices_known: bool,
    missing_prices_known: bool,
) -> dict[str, Any]:
    if not has_buyer:
        return {
            "code": "skip",
            "label": "Skip",
            "rank": 60,
            "tone": "skip",
            "reason": "No nearby public buyer was found inside the jump range.",
        }
    if replacement_profit is not None and replacement_profit > 0 and can_build:
        return {
            "code": "build-now",
            "label": "Build Now",
            "rank": 10,
            "tone": "build",
            "reason": "Nearby buyer and current materials support an after-tax profitable one-run build.",
        }
    if replacement_profit is not None and replacement_profit > 0 and missing_material_types > 0 and missing_prices_known:
        return {
            "code": "source-missing",
            "label": "Buy Missing",
            "rank": 20,
            "tone": "source",
            "reason": "Still after-tax profitable after pricing the missing materials nearby.",
        }
    if cash_profit is not None and cash_profit > 0 and can_build:
        return {
            "code": "use-stock",
            "label": "Use Stock",
            "rank": 30,
            "tone": "stock",
            "reason": "Wallet-positive after tax with materials already on hand, but true profit is not positive.",
        }
    if has_buyer and not all_material_prices_known:
        return {
            "code": "price-check",
            "label": "Price Check",
            "rank": 40,
            "tone": "price",
            "reason": "A buyer exists, but one or more material prices were not found nearby.",
        }
    return {
        "code": "watch",
        "label": "Watch",
        "rank": 50,
        "tone": "watch",
        "reason": "A buyer exists, but current nearby pricing does not clear the profit threshold.",
    }


def profit_margin_percent(profit: float | None, revenue: float | None) -> float | None:
    if profit is None or revenue is None or revenue <= 0:
        return None
    return round((float(profit) / float(revenue)) * 100.0, 4)


def owned_blueprint_product_targets(
    *,
    blueprints: Iterable[dict[str, Any]],
    recipes: dict[int, IndustryRecipe],
) -> list[dict[str, Any]]:
    owned_blueprints = [
        blueprint
        for item in blueprints
        if isinstance(item, dict)
        for blueprint in [owned_blueprint_from_esi(item)]
        if blueprint is not None
    ]
    targets_by_product_id: dict[int, dict[str, Any]] = {}
    for blueprint in sorted(
        owned_blueprints,
        key=lambda item: (-blueprint_quality_rank(item)[0], -item.material_efficiency, -item.time_efficiency, item.type_id),
    ):
        recipe = recipes.get(blueprint.type_id)
        if recipe is None:
            continue
        target = targets_by_product_id.setdefault(
            recipe.product_type_id,
            {
                "product_type_id": recipe.product_type_id,
                "product_name": recipe.product_name,
                "product_quantity": recipe.product_quantity,
                "blueprint_type_id": recipe.blueprint_type_id,
                "blueprint_name": recipe.blueprint_name,
                "owned_blueprints": 0,
                "owned_originals": 0,
                "owned_copies": 0,
                "blueprint_material_efficiency": 0,
                "blueprint_time_efficiency": 0,
                "blueprint_runs": None,
                "blueprint_kind": "Blueprint",
                "blueprint_usable": False,
            },
        )
        target["owned_blueprints"] = int(target["owned_blueprints"]) + 1
        if blueprint.is_original:
            target["owned_originals"] = int(target["owned_originals"]) + 1
        if blueprint.is_copy:
            target["owned_copies"] = int(target["owned_copies"]) + 1
        current_rank = target.get("_quality_rank")
        candidate_rank = blueprint_quality_rank(blueprint)
        if current_rank is None or candidate_rank > current_rank:
            target.update(
                {
                    "blueprint_type_id": recipe.blueprint_type_id,
                    "blueprint_name": recipe.blueprint_name,
                    "product_quantity": recipe.product_quantity,
                    "blueprint_material_efficiency": blueprint.material_efficiency,
                    "blueprint_time_efficiency": blueprint.time_efficiency,
                    "blueprint_runs": blueprint.limited_runs,
                    "blueprint_kind": blueprint.kind,
                    "blueprint_usable": blueprint.usable_for_one_run,
                    "_quality_rank": candidate_rank,
                }
            )
    for target in targets_by_product_id.values():
        target.pop("_quality_rank", None)
    return sorted(targets_by_product_id.values(), key=lambda item: (-int(item["owned_blueprints"]), item["product_name"]))


def build_buyer_order_record(
    order: dict[str, Any],
    *,
    target: dict[str, Any],
    systems: dict[int, RouteSystem],
    jump_distances: dict[int, int],
    region_id: int,
) -> dict[str, Any] | None:
    record = build_reachable_market_order_record(
        order,
        systems=systems,
        jump_distances=jump_distances,
        region_id=region_id,
        order_type="buy",
    )
    if record is None:
        return None
    return {
        **record,
        "product_type_id": target["product_type_id"],
        "product_name": target["product_name"],
        "blueprint_type_id": target["blueprint_type_id"],
        "blueprint_name": target["blueprint_name"],
    }


def build_reachable_market_order_record(
    order: dict[str, Any],
    *,
    systems: dict[int, RouteSystem],
    jump_distances: dict[int, int],
    region_id: int,
    order_type: str,
) -> dict[str, Any] | None:
    try:
        system_id = int(order.get("system_id") or 0)
        price = float(order.get("price") or 0)
        volume_remain = int(order.get("volume_remain") or 0)
    except (TypeError, ValueError):
        return None
    if order_type == "buy" and order.get("is_buy_order") is False:
        return None
    if order_type == "sell" and order.get("is_buy_order") is True:
        return None
    if system_id not in jump_distances or price <= 0 or volume_remain <= 0:
        return None
    system = systems.get(system_id)
    if system is None:
        return None
    return {
        "order_id": clean_optional_int(order.get("order_id")) or 0,
        "region_id": region_id,
        "system_id": system_id,
        "system_name": system.name,
        "jumps": jump_distances[system_id],
        "location_id": clean_optional_int(order.get("location_id")) or 0,
        "price": price,
        "volume_remain": volume_remain,
        "min_volume": clean_optional_int(order.get("min_volume")) or 1,
        "range": str(order.get("range") or ""),
        "issued": str(order.get("issued") or ""),
        "duration": clean_optional_int(order.get("duration")) or 0,
    }


def industry_material_trade_targets(recipes: dict[int, IndustryRecipe]) -> list[dict[str, Any]]:
    material_counts: dict[int, int] = {}
    material_names: dict[int, str] = {}
    material_volumes: dict[int, float | None] = {}
    for recipe in recipes.values():
        for material in recipe.materials:
            material_counts[material.type_id] = material_counts.get(material.type_id, 0) + 1
            material_names[material.type_id] = material.name
            if material.volume_m3 is not None:
                material_volumes[material.type_id] = material.volume_m3
    targets = [
        {
            "type_id": type_id,
            "name": material_names.get(type_id) or f"Type {type_id}",
            "recipe_count": count,
            "volume_m3": material_volumes.get(type_id),
        }
        for type_id, count in material_counts.items()
    ]
    return sorted(targets, key=lambda item: (-int(item["recipe_count"]), str(item["name"]), int(item["type_id"])))


def resolve_route_system(route_cache: RouteGraphCache, name: str) -> RouteSystem | None:
    systems = route_cache.systems or {}
    normalized = normalize_system_name(name)
    if not normalized:
        normalized = normalize_system_name(DEFAULT_HAUL_DESTINATION_SYSTEM)
    aliases = {
        "dhira": "dihra",
        "amarrhomeworld": "amarr",
        "amarrhome": "amarr",
    }
    normalized = aliases.get(normalized, normalized)
    for system in systems.values():
        if normalize_system_name(system.name) == normalized:
            return system
    return None


def normalize_system_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def shortest_route_path(
    *,
    start_system_id: int,
    destination_system_id: int,
    adjacency: dict[int, tuple[int, ...]],
) -> list[int]:
    if start_system_id <= 0 or destination_system_id <= 0:
        return []
    if start_system_id == destination_system_id:
        return [start_system_id]
    parents: dict[int, int | None] = {start_system_id: None}
    frontier = [start_system_id]
    cursor = 0
    while cursor < len(frontier):
        system_id = frontier[cursor]
        cursor += 1
        for neighbor_id in adjacency.get(system_id, ()):
            if neighbor_id in parents:
                continue
            parents[neighbor_id] = system_id
            if neighbor_id == destination_system_id:
                path = [destination_system_id]
                parent = system_id
                while parent is not None:
                    path.append(parent)
                    parent = parents[parent]
                return list(reversed(path))
            frontier.append(neighbor_id)
    return []


def route_corridor_systems(
    *,
    route_path: list[int],
    adjacency: dict[int, tuple[int, ...]],
    detour_jumps: int,
    destination_system_id: int,
) -> dict[int, int]:
    clean_detour_jumps = clamp_haul_detour_jumps(detour_jumps)
    corridor: dict[int, int] = {}
    for route_system_id in route_path:
        nearby = jump_distances_within(
            start_system_id=route_system_id,
            max_jumps=clean_detour_jumps,
            adjacency=adjacency,
        )
        for system_id, jumps_from_route in nearby.items():
            if system_id == destination_system_id:
                continue
            previous = corridor.get(system_id)
            if previous is None or jumps_from_route < previous:
                corridor[system_id] = jumps_from_route
    return corridor


def jump_distances_from(
    *,
    start_system_id: int,
    adjacency: dict[int, tuple[int, ...]],
    max_jumps: int | None = None,
) -> dict[int, int]:
    if start_system_id <= 0:
        return {}
    distances = {start_system_id: 0}
    frontier = [start_system_id]
    cursor = 0
    while cursor < len(frontier):
        system_id = frontier[cursor]
        cursor += 1
        next_distance = distances[system_id] + 1
        if max_jumps is not None and next_distance > max_jumps:
            continue
        for neighbor_id in adjacency.get(system_id, ()):
            if neighbor_id in distances:
                continue
            distances[neighbor_id] = next_distance
            frontier.append(neighbor_id)
    return distances


def jump_distances_within(
    *,
    start_system_id: int,
    max_jumps: int,
    adjacency: dict[int, tuple[int, ...]],
) -> dict[int, int]:
    distances = {start_system_id: 0}
    frontier = [start_system_id]
    while frontier:
        system_id = frontier.pop(0)
        next_distance = distances[system_id] + 1
        if next_distance > max_jumps:
            continue
        for neighbor_id in adjacency.get(system_id, ()):
            if neighbor_id in distances:
                continue
            distances[neighbor_id] = next_distance
            frontier.append(neighbor_id)
    return distances


def clamp_flight_max_jumps(value: Any) -> int:
    try:
        jumps = int(value)
    except (TypeError, ValueError):
        jumps = DEFAULT_FLIGHT_MAX_JUMPS
    return max(0, min(MAX_FLIGHT_MAX_JUMPS, jumps))


def clamp_haul_detour_jumps(value: Any) -> int:
    try:
        jumps = int(value)
    except (TypeError, ValueError):
        jumps = DEFAULT_HAUL_DETOUR_JUMPS
    return max(0, min(MAX_HAUL_DETOUR_JUMPS, jumps))


def clamp_haul_cargo_m3(value: Any) -> float:
    try:
        cargo_m3 = float(value)
    except (TypeError, ValueError):
        cargo_m3 = DEFAULT_HAUL_CARGO_M3
    return max(1.0, min(MAX_HAUL_CARGO_M3, cargo_m3))


def clean_optional_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def clean_optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_blueprint_efficiency(value: Any) -> int:
    efficiency = clean_int(value)
    return max(-100, min(99, efficiency))


def owned_blueprint_from_esi(item: dict[str, Any]) -> OwnedBlueprint | None:
    type_id = clean_optional_int(item.get("type_id"))
    if not type_id:
        return None
    return OwnedBlueprint(
        type_id=type_id,
        material_efficiency=clean_blueprint_efficiency(item.get("material_efficiency")),
        time_efficiency=clean_blueprint_efficiency(item.get("time_efficiency")),
        runs=clean_int(item.get("runs")),
        quantity=clean_int(item.get("quantity")),
    )


def blueprint_quality_rank(blueprint: OwnedBlueprint) -> tuple[int, int, int, int, int]:
    run_rank = 1_000_000 if blueprint.limited_runs is None else blueprint.limited_runs
    return (
        1 if blueprint.usable_for_one_run else 0,
        blueprint.material_efficiency,
        blueprint.time_efficiency,
        1 if blueprint.is_original else 0,
        run_rank,
    )


def adjusted_material_quantity(base_quantity: int, material_efficiency: int, runs: int = 1) -> int:
    clean_base = max(0, int(base_quantity))
    clean_runs = max(1, int(runs))
    if clean_base <= 0:
        return 0
    if clean_base == 1:
        return clean_runs
    efficiency = clean_blueprint_efficiency(material_efficiency)
    numerator = clean_base * clean_runs * (100 - efficiency)
    return max(1, -(-numerator // 100))


def adjusted_recipe_materials(
    recipe: IndustryRecipe,
    *,
    material_efficiency: int,
    runs: int = 1,
) -> list[dict[str, Any]]:
    return [
        {
            "type_id": material.type_id,
            "name": material.name,
            "base_quantity": material.quantity * max(1, int(runs)),
            "quantity": adjusted_material_quantity(material.quantity, material_efficiency, runs=runs),
        }
        for material in recipe.materials
    ]


def best_owned_blueprints_by_type(blueprints: Iterable[dict[str, Any]]) -> dict[int, OwnedBlueprint]:
    best: dict[int, OwnedBlueprint] = {}
    for item in blueprints:
        if not isinstance(item, dict):
            continue
        blueprint = owned_blueprint_from_esi(item)
        if blueprint is None:
            continue
        current = best.get(blueprint.type_id)
        if current is None or blueprint_quality_rank(blueprint) > blueprint_quality_rank(current):
            best[blueprint.type_id] = blueprint
    return best


def clamp_skill_level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, level))


def skill_level_from_esi(skills_payload: dict[str, Any], skill_type_id: int) -> int:
    skills = skills_payload.get("skills")
    if not isinstance(skills, list):
        return 0
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        if (clean_optional_int(skill.get("skill_id")) or 0) != int(skill_type_id):
            continue
        return clamp_skill_level(skill.get("active_skill_level", skill.get("trained_skill_level")))
    return 0


def sales_tax_rate_for_accounting_level(accounting_level: int) -> float:
    clean_level = clamp_skill_level(accounting_level)
    return BASE_SALES_TAX_RATE * (1.0 - ACCOUNTING_SALES_TAX_REDUCTION_PER_LEVEL * clean_level)


def build_sales_tax_profile(skills_payload: dict[str, Any]) -> dict[str, Any]:
    accounting_level = skill_level_from_esi(skills_payload, ACCOUNTING_SKILL_TYPE_ID)
    rate = sales_tax_rate_for_accounting_level(accounting_level)
    return {
        "mode": "esi-accounting",
        "accounting_skill_type_id": ACCOUNTING_SKILL_TYPE_ID,
        "accounting_level": accounting_level,
        "rate": rate,
        "rate_percent": rate * 100.0,
        "broker_fee_rate": 0.0,
        "broker_fee_note": "Immediate sales to existing buy orders do not create a broker fee.",
        "source": FLIGHT_SKILLS_SCOPE,
    }


def build_flight_status_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession | None,
    callback_url: str,
    max_jumps: int = DEFAULT_FLIGHT_MAX_JUMPS,
) -> dict[str, Any]:
    clean_max_jumps = clamp_flight_max_jumps(max_jumps)
    required_scopes = list(config.scopes or DEFAULT_FLIGHT_ESI_SCOPES)
    granted_scopes = set(session.scopes) if session else set()
    missing_required_scopes = [scope for scope in required_scopes if scope not in granted_scopes]
    payload: dict[str, Any] = {
        "ok": True,
        "sso_configured": config.enabled,
        "connected": bool(session),
        "required_scopes": required_scopes,
        "missing_required_scopes": missing_required_scopes if session else [],
        "login_url": "/flight/login",
        "logout_url": "/flight/logout",
        "callback_url": callback_url,
        "character": session.to_public_dict() if session else None,
        "location": None,
        "nearby_systems": {
            "available": False,
            "max_jumps": clean_max_jumps,
            "current_solar_system_id": 0,
            "reachable_system_count": 0,
            "systems": [],
            "error": "Connect ESI to calculate nearby systems.",
        },
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
        location = fetch_flight_location(config, session)
        payload["location"] = location
        payload["nearby_systems"] = build_nearby_systems_payload(
            current_solar_system_id=int(location.get("solar_system_id") or 0),
            max_jumps=clean_max_jumps,
        )
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
            if path == "/api/flight/buyers":
                self._handle_flight_buyers()
                return
            if path == "/api/flight/profitability":
                self._handle_flight_profitability()
                return
            if path == "/api/flight/hauling":
                self._handle_flight_hauling()
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
            query = parse_qs(urlparse(self.path).query)
            max_jumps = clamp_flight_max_jumps((query.get("max_jumps") or [DEFAULT_FLIGHT_MAX_JUMPS])[0])
            payload = build_flight_status_payload(
                config=sso_config,
                session=session,
                callback_url=sso_config.callback_url,
                max_jumps=max_jumps,
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

        def _handle_flight_buyers(self) -> None:
            if not sso_config.enabled:
                self._send_json({"ok": False, "error": "EVE SSO is not configured."}, status=503)
                return
            session = self._flight_session()
            if session is None:
                self._send_json({"ok": False, "error": "Connect ESI before scanning buyer orders."}, status=401)
                return
            query = parse_qs(urlparse(self.path).query)
            max_jumps = clamp_flight_max_jumps((query.get("max_jumps") or [DEFAULT_FLIGHT_MAX_JUMPS])[0])
            try:
                payload = build_flight_buyers_payload(config=sso_config, session=session, max_jumps=max_jumps)
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_profitability(self) -> None:
            if not sso_config.enabled:
                self._send_json({"ok": False, "error": "EVE SSO is not configured."}, status=503)
                return
            session = self._flight_session()
            if session is None:
                self._send_json({"ok": False, "error": "Connect ESI before ranking profitability."}, status=401)
                return
            query = parse_qs(urlparse(self.path).query)
            max_jumps = clamp_flight_max_jumps((query.get("max_jumps") or [DEFAULT_FLIGHT_MAX_JUMPS])[0])
            try:
                payload = build_flight_profitability_payload(config=sso_config, session=session, max_jumps=max_jumps)
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_hauling(self) -> None:
            if not sso_config.enabled:
                self._send_json({"ok": False, "error": "EVE SSO is not configured."}, status=503)
                return
            session = self._flight_session()
            if session is None:
                self._send_json({"ok": False, "error": "Connect ESI before scanning hauler routes."}, status=401)
                return
            query = parse_qs(urlparse(self.path).query)
            destination = first_query_value(query, "destination") or DEFAULT_HAUL_DESTINATION_SYSTEM
            detour_jumps = clamp_haul_detour_jumps((query.get("detour_jumps") or [DEFAULT_HAUL_DETOUR_JUMPS])[0])
            cargo_m3 = clamp_haul_cargo_m3((query.get("cargo_m3") or [DEFAULT_HAUL_CARGO_M3])[0])
            try:
                payload = build_flight_hauling_payload(
                    config=sso_config,
                    session=session,
                    destination_name=destination,
                    detour_jumps=detour_jumps,
                    cargo_capacity_m3=cargo_m3,
                )
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
    .profit-panel {
      grid-column: 1 / -1;
      min-height: 440px;
      border-color: rgba(224, 168, 74, .48);
      background:
        linear-gradient(135deg, rgba(224, 168, 74, .1), rgba(97, 199, 217, .07)),
        rgba(17, 24, 25, .96);
    }
    .profit-panel .panel-header { align-items: center; }
    .profit-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .profit-title h2 { margin: 0; font-size: 22px; }
    .profit-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
    .profit-actions button { min-height: 44px; padding: 10px 16px; font-size: 15px; }
    .profit-summary {
      border: 1px solid rgba(63, 85, 80, .76);
      background: rgba(8, 13, 15, .42);
      border-radius: 7px;
      padding: 12px;
      color: var(--muted);
      min-width: min(100%, 560px);
    }
    .progress-status { display: flex; align-items: center; gap: 12px; }
    .progress-spinner {
      width: 28px;
      height: 28px;
      border-radius: 999px;
      border: 3px solid rgba(97, 199, 217, .22);
      border-top-color: var(--cyan);
      animation: flight-spin .9s linear infinite;
      flex: 0 0 auto;
    }
    .progress-copy { display: grid; gap: 2px; min-width: 0; }
    .progress-copy strong { color: var(--text); font-size: 14px; overflow-wrap: anywhere; }
    .progress-copy span { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .progress-bar {
      position: relative;
      overflow: hidden;
      height: 7px;
      margin-top: 10px;
      border-radius: 999px;
      background: rgba(63, 85, 80, .46);
    }
    .progress-bar span {
      position: absolute;
      inset: 0 auto 0 0;
      width: 38%;
      border-radius: inherit;
      background: linear-gradient(90deg, rgba(97, 199, 217, .18), var(--cyan), rgba(224, 168, 74, .76));
      animation: flight-progress 1.35s ease-in-out infinite;
    }
    @keyframes flight-spin {
      to { transform: rotate(360deg); }
    }
    @keyframes flight-progress {
      0% { transform: translateX(-110%); }
      100% { transform: translateX(265%); }
    }
    .profit-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 9px; }
    .profit-stat { border: 1px solid rgba(63, 85, 80, .68); background: rgba(17, 24, 25, .7); border-radius: 6px; padding: 8px; min-height: 58px; }
    .profit-stat span { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
    .profit-stat b { display: block; color: var(--text); font-size: 18px; line-height: 1.2; margin-top: 2px; }
    .decision-filters { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 14px; }
    .decision-filters button { padding: 8px 10px; font-size: 13px; min-height: 36px; }
    .decision-filters button.active { color: var(--ink); background: var(--amber); border-color: var(--amber); }
    .decision-output { min-height: 260px; }
    .decision-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 11px; margin-top: 9px; align-items: stretch; }
    .decision-row {
      border: 1px solid rgba(63, 85, 80, .82);
      background: linear-gradient(180deg, rgba(24, 35, 35, .78), rgba(8, 13, 15, .68));
      border-radius: 7px;
      padding: 12px;
    }
    .decision-head { display: flex; align-items: start; justify-content: space-between; gap: 8px; margin-bottom: 5px; }
    .decision-head strong { overflow-wrap: anywhere; font-size: 16px; line-height: 1.25; }
    .decision-lede {
      border: 1px solid rgba(224, 168, 74, .34);
      background: rgba(224, 168, 74, .08);
      border-radius: 6px;
      padding: 8px;
      margin: 8px 0;
      color: var(--text);
      font-weight: 800;
      overflow-wrap: anywhere;
    }
    .decision-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin: 9px 0; }
    .decision-metric { border: 1px solid rgba(63, 85, 80, .58); border-radius: 6px; padding: 8px; background: rgba(17, 24, 25, .62); min-height: 56px; }
    .decision-metric span { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
    .decision-metric b { display: block; color: var(--text); font-size: 13px; overflow-wrap: anywhere; margin-top: 2px; }
    .decision-metric small { display: block; color: var(--muted); margin-top: 3px; line-height: 1.25; }
    .profit-details {
      border-top: 1px solid rgba(63, 85, 80, .52);
      margin-top: 10px;
      padding-top: 9px;
      color: var(--muted);
    }
    .profit-details summary { cursor: pointer; color: var(--cyan); font-weight: 800; }
    .profit-detail-grid { display: grid; gap: 6px; margin-top: 8px; }
    .profit-detail-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      border: 1px solid rgba(63, 85, 80, .42);
      border-radius: 6px;
      padding: 7px 8px;
      background: rgba(8, 13, 15, .34);
    }
    .profit-detail-row b { color: var(--text); text-align: right; overflow-wrap: anywhere; }
    .decision-empty { border: 1px dashed rgba(63, 85, 80, .85); border-radius: 7px; padding: 24px; color: var(--muted); text-align: center; background: rgba(8, 13, 15, .34); }
    .decision-counts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .decision-build { background: rgba(100, 196, 125, .16); color: var(--green); }
    .decision-source, .decision-stock { background: rgba(97, 199, 217, .16); color: var(--cyan); }
    .decision-price, .decision-watch { background: rgba(224, 168, 74, .16); color: var(--amber); }
    .decision-skip { background: rgba(229, 116, 102, .16); color: var(--red); }
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
    @media (prefers-reduced-motion: reduce) {
      .progress-spinner, .progress-bar span { animation: none; }
      .progress-bar span { width: 100%; opacity: .72; }
    }
    @media (max-width: 1040px) {
      .market-grid, .flight-grid, .briefing { grid-template-columns: 1fr; }
      .ops-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .profit-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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
      .row, .offer-grid, .ops-strip, .profit-stats, .decision-metrics { grid-template-columns: 1fr; }
      .offer, .note-card { grid-template-columns: 1fr; }
      .profit-panel { min-height: 360px; }
      .profit-actions { display: grid; grid-template-columns: 1fr; }
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
      <button type="button" data-tab-target="hauling" aria-selected="false">Hauler Routes</button>
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
                  <h3 class="signal">Nearby Systems</h3>
                  <label>Max jumps
                    <input id="flight-max-jumps" type="number" min="0" max="25" step="1" value="5">
                  </label>
                  <div id="flight-route-summary" class="meta">Connect ESI to calculate nearby systems.</div>
                  <div id="flight-route-top" class="meta"></div>
                </div>
                <div class="module">
                  <h3 class="warning">Buyer Orders</h3>
                  <button id="flight-buyer-scan" class="ghost" type="button">Scan Buyers</button>
                  <div id="flight-buyer-summary" class="meta">Connect ESI to scan nearby public buy orders.</div>
                  <div id="flight-buyer-top" class="meta"></div>
                </div>
                <div class="module">
                  <h3 class="signal">Recipe Cache</h3>
                  <div id="flight-recipe-summary" class="meta">Connect ESI to compare owned blueprints with static recipes.</div>
                  <div id="flight-buildability-top" class="meta"></div>
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

          <section class="panel profit-panel" aria-labelledby="flight-profit-title">
            <div class="panel-header">
              <div>
                <div class="profit-title">
                  <h2 id="flight-profit-title">Profitability Ranking</h2>
                  <span class="pill reserved">Decision Board</span>
                </div>
                <div class="meta">Owned blueprints, owned materials, nearby buyers, and nearby material pricing.</div>
              </div>
            </div>
            <div class="profit-actions">
              <button id="flight-profit-scan" class="ghost" type="button">Rank Profit</button>
              <div id="flight-profit-summary" class="profit-summary">Connect ESI to rank owned blueprint profitability.</div>
            </div>
            <div id="flight-profit-filters" class="decision-filters" role="group" aria-label="Profitability decision filters">
              <button class="secondary active" type="button" data-profit-filter="all">All</button>
              <button class="secondary" type="button" data-profit-filter="build-now">Build</button>
              <button class="secondary" type="button" data-profit-filter="source-missing">Buy Missing</button>
              <button class="secondary" type="button" data-profit-filter="use-stock">Use Stock</button>
              <button class="secondary" type="button" data-profit-filter="price-check">Price Check</button>
              <button class="secondary" type="button" data-profit-filter="watch">Watch</button>
              <button class="secondary" type="button" data-profit-filter="skip">Skip</button>
            </div>
            <div id="flight-profit-top" class="decision-output"></div>
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

      <section id="tab-hauling" class="tab-panel" data-tab-panel="hauling" hidden>
        <div class="flight-grid">
          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Hauler Routes</h2>
                <div class="meta">Current ESI location to a hub, with pickup systems on or near that route.</div>
              </div>
              <span class="pill reserved">Manual Hauling</span>
            </div>
            <form id="haul-route-form" class="note-form">
              <div class="row">
                <label>Destination
                  <input id="haul-destination" name="destination" autocomplete="off" value="Jita" placeholder="Jita, Hek, Rens, Dihra">
                </label>
                <label>Cargo m3
                  <input id="haul-cargo-m3" name="cargo_m3" type="number" min="1" max="10000000" step="100" value="10000">
                </label>
              </div>
              <div class="row">
                <label>Pickup detour jumps
                  <input id="haul-detour-jumps" name="detour_jumps" type="number" min="0" max="5" step="1" value="1">
                </label>
                <label>Scan mode
                  <select disabled>
                    <option>Common build materials</option>
                  </select>
                </label>
              </div>
              <div id="haul-hub-buttons" class="filters" aria-label="Hub shortcuts">
                <button class="secondary" type="button" data-haul-destination="Jita">Jita</button>
                <button class="secondary" type="button" data-haul-destination="Amarr">Amarr</button>
                <button class="secondary" type="button" data-haul-destination="Hek">Hek</button>
                <button class="secondary" type="button" data-haul-destination="Rens">Rens</button>
                <button class="secondary" type="button" data-haul-destination="Dodixie">Dodixie</button>
                <button class="secondary" type="button" data-haul-destination="Dihra">Dihra</button>
              </div>
              <button id="haul-scan" class="ghost" type="submit">Scan Route</button>
            </form>
            <div id="haul-route-summary" class="profit-summary">Connect ESI to scan route hauling opportunities.</div>
            <div id="haul-route-path" class="meta"></div>
          </section>

          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Route Rules</h2>
                <div class="meta">Public orders only, after-tax destination buy revenue, and no automatic market action.</div>
              </div>
            </div>
            <ul class="charter-list">
              <li><strong>Pickup side:</strong> lowest public sell order in systems on the route or inside the detour radius.</li>
              <li><strong>Destination side:</strong> highest public buy order in the selected destination system.</li>
              <li><strong>Cargo:</strong> capacity limits are applied when the local static cache includes item volume.</li>
              <li><strong>Docking:</strong> the scan does not prove access to a station or structure.</li>
              <li><strong>Manual pilot:</strong> every purchase, haul, sale, and route decision stays inside EVE.</li>
            </ul>
          </section>

          <section class="panel profit-panel" aria-labelledby="haul-opportunity-title">
            <div class="panel-header">
              <div>
                <div class="profit-title">
                  <h2 id="haul-opportunity-title">Route Opportunities</h2>
                  <span class="pill reserved">Buy Low / Sell High</span>
                </div>
                <div class="meta">Materials with cheap pickup orders near your route and stronger buy orders at the destination.</div>
              </div>
            </div>
            <div id="haul-opportunity-summary" class="profit-summary">No route scan has run yet.</div>
            <div id="haul-opportunity-top" class="decision-output"></div>
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
    const flightMaxJumps = document.querySelector("#flight-max-jumps");
    const flightRouteSummary = document.querySelector("#flight-route-summary");
    const flightRouteTop = document.querySelector("#flight-route-top");
    const flightBuyerScanButton = document.querySelector("#flight-buyer-scan");
    const flightBuyerSummary = document.querySelector("#flight-buyer-summary");
    const flightBuyerTop = document.querySelector("#flight-buyer-top");
    const flightProfitScanButton = document.querySelector("#flight-profit-scan");
    const flightProfitSummary = document.querySelector("#flight-profit-summary");
    const flightProfitFilters = document.querySelector("#flight-profit-filters");
    const flightProfitTop = document.querySelector("#flight-profit-top");
    const haulRouteForm = document.querySelector("#haul-route-form");
    const haulDestination = document.querySelector("#haul-destination");
    const haulCargoM3 = document.querySelector("#haul-cargo-m3");
    const haulDetourJumps = document.querySelector("#haul-detour-jumps");
    const haulHubButtons = document.querySelector("#haul-hub-buttons");
    const haulScanButton = document.querySelector("#haul-scan");
    const haulRouteSummary = document.querySelector("#haul-route-summary");
    const haulRoutePath = document.querySelector("#haul-route-path");
    const haulOpportunitySummary = document.querySelector("#haul-opportunity-summary");
    const haulOpportunityTop = document.querySelector("#haul-opportunity-top");
    const flightRecipeSummary = document.querySelector("#flight-recipe-summary");
    const flightBuildabilityTop = document.querySelector("#flight-buildability-top");
    const flightIndustryNote = document.querySelector("#flight-industry-note");
    const notesKey = "eve-flight-attendant-notes-v1";
    const jumpsKey = "eve-flight-attendant-max-jumps-v1";
    const haulDestinationKey = "eve-flight-haul-destination-v1";
    const haulCargoKey = "eve-flight-haul-cargo-m3-v1";
    const haulDetourKey = "eve-flight-haul-detour-jumps-v1";
    const validTabs = new Set(["market", "flight", "hauling"]);
    let filterType = "";
    let includeClosed = false;
    let flightProfitFilter = "all";
    let flightProfitProducts = [];
    let flightProfitProgressTimer = null;

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

    function formatIsk(value) {
      return `${Number(value || 0).toLocaleString(undefined, {maximumFractionDigits: 2})} ISK`;
    }

    function formatSignedIsk(value) {
      if (value == null) return "unknown";
      const number = Number(value || 0);
      const sign = number > 0 ? "+" : "";
      return `${sign}${formatIsk(number)}`;
    }

    function formatPercent(value) {
      if (value == null) return "unknown";
      return `${Number(value || 0).toFixed(1)}%`;
    }

    function formatRatePercent(value) {
      if (value == null) return "unknown";
      return `${(Number(value || 0) * 100).toFixed(2)}%`;
    }

    function formatVolume(value) {
      if (value == null) return "unknown";
      return `${Number(value || 0).toLocaleString(undefined, {maximumFractionDigits: 2})} m3`;
    }

    function readMaxJumps() {
      const value = Number(window.localStorage.getItem(jumpsKey) || flightMaxJumps.value || 5);
      if (!Number.isFinite(value)) return 5;
      return Math.max(0, Math.min(25, Math.round(value)));
    }

    function writeMaxJumps(value) {
      const jumps = Math.max(0, Math.min(25, Math.round(Number(value) || 0)));
      flightMaxJumps.value = String(jumps);
      window.localStorage.setItem(jumpsKey, String(jumps));
      return jumps;
    }

    function readHaulSettings() {
      const destination = String(window.localStorage.getItem(haulDestinationKey) || haulDestination.value || "Jita").trim() || "Jita";
      const cargo = Number(window.localStorage.getItem(haulCargoKey) || haulCargoM3.value || 10000);
      const detour = Number(window.localStorage.getItem(haulDetourKey) || haulDetourJumps.value || 1);
      return {
        destination,
        cargoM3: Math.max(1, Math.min(10000000, Math.round(Number.isFinite(cargo) ? cargo : 10000))),
        detourJumps: Math.max(0, Math.min(5, Math.round(Number.isFinite(detour) ? detour : 1))),
      };
    }

    function writeHaulSettings(settings) {
      const destination = String(settings.destination || "Jita").trim() || "Jita";
      const cargoM3 = Math.max(1, Math.min(10000000, Math.round(Number(settings.cargoM3) || 10000)));
      const detourJumps = Math.max(0, Math.min(5, Math.round(Number(settings.detourJumps) || 0)));
      haulDestination.value = destination;
      haulCargoM3.value = String(cargoM3);
      haulDetourJumps.value = String(detourJumps);
      window.localStorage.setItem(haulDestinationKey, destination);
      window.localStorage.setItem(haulCargoKey, String(cargoM3));
      window.localStorage.setItem(haulDetourKey, String(detourJumps));
      return {destination, cargoM3, detourJumps};
    }

    async function loadFlightStatus() {
      try {
        const maxJumps = writeMaxJumps(readMaxJumps());
        const response = await fetch(`/api/flight/status?max_jumps=${encodeURIComponent(maxJumps)}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not read Flight Attendant status");
        renderFlightStatus(data);
      } catch (error) {
        flightSystemName.textContent = "ESI Offline";
        flightLocationLine.textContent = "Could not load Flight Attendant status.";
        flightMessage.textContent = error.message;
        resetFlightRoute("Flight Attendant route status is offline.");
        resetFlightBuyers("Flight Attendant buyer scanner is offline.");
        resetFlightProfitability("Flight Attendant profitability ranking is offline.");
        resetFlightHauling("Flight Attendant hauler route scanner is offline.");
        resetFlightIndustry("Flight Attendant ESI status is offline.");
      }
    }

    function renderFlightStatus(data) {
      const requiredScopes = data.required_scopes || [];
      const missingRequiredScopes = data.missing_required_scopes || [];
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
        resetFlightRoute("Configure EVE SSO before calculating nearby systems.");
        resetFlightBuyers("Configure EVE SSO before scanning buyer orders.");
        resetFlightProfitability("Configure EVE SSO before ranking profitability.");
        resetFlightHauling("Configure EVE SSO before scanning hauler routes.");
        resetFlightIndustry("Configure EVE SSO before scanning industry data.");
        return;
      }
      if (!data.connected) {
        flightSystemName.textContent = "Awaiting ESI";
        flightLocationLine.textContent = data.note || "Connect ESI to show your current system.";
        flightPilotName.textContent = "Not connected";
        flightTokenStatus.textContent = "Not active";
        flightMessage.textContent = "";
        resetFlightRoute("Connect ESI to calculate nearby systems.");
        resetFlightBuyers("Connect ESI to scan nearby public buy orders.");
        resetFlightProfitability("Connect ESI to rank owned blueprint profitability.");
        resetFlightHauling("Connect ESI to scan route hauling opportunities.");
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
        resetFlightRoute("Resolve the ESI error before calculating nearby systems.");
        resetFlightBuyers("Resolve the ESI error before scanning buyer orders.");
        resetFlightProfitability("Resolve the ESI error before ranking profitability.");
        resetFlightHauling("Resolve the ESI error before scanning hauler routes.");
        resetFlightIndustry("Resolve the ESI error before scanning industry data.");
        return;
      }
      flightSystemName.textContent = location.solar_system_name || "Unknown System";
      flightLocationLine.textContent = `Live ESI location ${location.updated_at || ""}`;
      if (missingRequiredScopes.length) {
        flightMessage.textContent = `${character.character_name || "Pilot"} connected, but reconnect ESI for new scopes: ${missingRequiredScopes.join(", ")}.`;
      } else {
        flightMessage.textContent = `${character.character_name || "Pilot"} connected with ${requiredScopes.length} read-only ESI scopes.`;
      }
      renderFlightRoute(data.nearby_systems || {});
      resetFlightBuyers(`Ready to scan buy orders within ${readMaxJumps()} jumps.`);
      resetFlightProfitability(`Ready to rank profitability within ${readMaxJumps()} jumps.`);
      resetFlightHauling(`Ready to scan route hauling opportunities to ${readHaulSettings().destination}.`);
      loadFlightIndustry();
    }

    function resetFlightRoute(message) {
      flightRouteSummary.textContent = message;
      flightRouteTop.textContent = "";
    }

    function renderFlightRoute(route) {
      if (!route.available) {
        resetFlightRoute(route.error || "Route graph cache missing.");
        return;
      }
      const maxJumps = formatNumber(route.max_jumps);
      flightRouteSummary.innerHTML = `
        <strong>${formatNumber(route.reachable_system_count)}</strong> systems within
        <strong>${maxJumps}</strong> jumps of ${escapeHtml(route.current_system_name || "current system")}.
      `;
      flightRouteTop.innerHTML = renderNearbySystems(route.systems || []);
    }

    function renderNearbySystems(systems) {
      if (!systems.length) return "No nearby systems returned yet.";
      return systems.slice(0, 8).map((system) => {
        const security = system.security_status == null ? "" : ` &middot; ${Number(system.security_status).toFixed(1)}`;
        return `${escapeHtml(system.name)} (${formatNumber(system.jumps)}j${security})`;
      }).join("<br>");
    }

    function resetFlightBuyers(message) {
      flightBuyerSummary.textContent = message;
      flightBuyerTop.textContent = "";
      flightBuyerScanButton.disabled = false;
    }

    async function loadFlightBuyers() {
      const maxJumps = writeMaxJumps(readMaxJumps());
      flightBuyerScanButton.disabled = true;
      flightBuyerSummary.textContent = `Scanning buyer orders within ${maxJumps} jumps...`;
      flightBuyerTop.textContent = "";
      try {
        const response = await fetch(`/api/flight/buyers?max_jumps=${encodeURIComponent(maxJumps)}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not scan buyer orders");
        renderFlightBuyers(data.buyers || {});
      } catch (error) {
        flightBuyerSummary.textContent = error.message;
        flightBuyerTop.textContent = "";
      } finally {
        flightBuyerScanButton.disabled = false;
      }
    }

    function renderFlightBuyers(buyers) {
      const productLimit = buyers.product_truncated ? ` Limited to ${formatNumber(buyers.scanned_products)} of ${formatNumber(buyers.total_known_products)} products.` : "";
      const regionLimit = buyers.region_truncated ? ` Limited to ${formatNumber(buyers.regions_scanned)} of ${formatNumber(buyers.total_regions_in_range)} regions.` : "";
      flightBuyerSummary.innerHTML = `
        <strong>${formatNumber(buyers.products_with_buyers)}</strong> products have nearby buy orders.
        <br>Scanned ${formatNumber(buyers.scanned_products)} products across ${formatNumber(buyers.regions_scanned)} regions;
        found ${formatNumber(buyers.order_count)} buy orders.${escapeHtml(productLimit + regionLimit)}
        <br>${escapeHtml(buyers.identity_note || "ESI does not expose buyer character names.")}
      `;
      flightBuyerTop.innerHTML = renderBuyerProducts(buyers.products || []);
    }

    function renderBuyerProducts(products) {
      if (!products.length) return "No buyer scan products returned yet.";
      return products.slice(0, 10).map((product) => {
        const order = product.best_order;
        if (!order) {
          return `${escapeHtml(product.product_name)}: no nearby buy orders`;
        }
        return `${escapeHtml(product.product_name)}: ${formatIsk(order.price)} &middot; ${formatNumber(order.volume_remain)} units &middot; ${escapeHtml(order.system_name)} (${formatNumber(order.jumps)}j)`;
      }).join("<br>");
    }

    function resetFlightProfitability(message) {
      stopFlightProfitProgress();
      flightProfitSummary.textContent = message;
      flightProfitTop.textContent = "";
      flightProfitScanButton.disabled = false;
      flightProfitProducts = [];
      updateProfitFilterButtons();
    }

    function stopFlightProfitProgress() {
      if (flightProfitProgressTimer) {
        window.clearInterval(flightProfitProgressTimer);
        flightProfitProgressTimer = null;
      }
    }

    function startFlightProfitProgress(maxJumps) {
      stopFlightProfitProgress();
      const startedAt = Date.now();
      const phases = [
        "Reading ESI blueprints and materials",
        "Scanning nearby buyer and material orders",
        "Pricing build inputs",
        "Ranking blueprint decisions",
      ];
      const renderProgress = () => {
        const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
        const phaseIndex = Math.min(phases.length - 1, Math.floor(elapsedSeconds / 15));
        flightProfitSummary.innerHTML = `
          <div class="progress-status" aria-live="polite">
            <span class="progress-spinner" aria-hidden="true"></span>
            <div class="progress-copy">
              <strong>${escapeHtml(phases[phaseIndex])}</strong>
              <span>Working within ${formatNumber(maxJumps)} jumps; ${formatNumber(elapsedSeconds)}s elapsed.</span>
            </div>
          </div>
          <div class="progress-bar" aria-hidden="true"><span></span></div>
        `;
      };
      renderProgress();
      flightProfitProgressTimer = window.setInterval(renderProgress, 1000);
    }

    async function loadFlightProfitability() {
      const maxJumps = writeMaxJumps(readMaxJumps());
      flightProfitScanButton.disabled = true;
      startFlightProfitProgress(maxJumps);
      flightProfitTop.innerHTML = `<div class="decision-empty">Ranking profitability. Results will appear here when the scan finishes.</div>`;
      try {
        const response = await fetch(`/api/flight/profitability?max_jumps=${encodeURIComponent(maxJumps)}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not rank profitability");
        stopFlightProfitProgress();
        renderFlightProfitability(data.profitability || {});
      } catch (error) {
        stopFlightProfitProgress();
        flightProfitSummary.textContent = error.message;
        flightProfitTop.textContent = "";
      } finally {
        flightProfitScanButton.disabled = false;
      }
    }

    function renderFlightProfitability(profitability) {
      const productLimit = profitability.product_truncated ? ` Limited to ${formatNumber(profitability.scanned_products)} of ${formatNumber(profitability.total_known_products)} products.` : "";
      const materialLimit = profitability.material_truncated ? ` Limited to ${formatNumber(profitability.scanned_material_types)} of ${formatNumber(profitability.total_material_types)} material types.` : "";
      const regionLimit = profitability.region_truncated ? ` Limited to ${formatNumber(profitability.regions_scanned)} of ${formatNumber(profitability.total_regions_in_range)} regions.` : "";
      const decisionCounts = profitability.decision_counts || {};
      const salesTax = profitability.sales_tax || {};
      flightProfitProducts = Array.isArray(profitability.products) ? profitability.products : [];
      flightProfitSummary.innerHTML = `
        <div class="profit-stats">
          <div class="profit-stat"><span>Profitable</span><b>${formatNumber(profitability.profitable_products)}</b></div>
          <div class="profit-stat"><span>Buildable</span><b>${formatNumber(profitability.buildable_now_products)}</b></div>
          <div class="profit-stat"><span>Buyers</span><b>${formatNumber(profitability.products_with_buyers)}</b></div>
          <div class="profit-stat"><span>Scanned</span><b>${formatNumber(profitability.scanned_products)}</b></div>
        </div>
        <div class="meta">
          ${formatNumber(profitability.scanned_products)} products and ${formatNumber(profitability.scanned_material_types)} material types.
          ${escapeHtml(productLimit + materialLimit + regionLimit)}
        </div>
        ${renderDecisionCounts(decisionCounts)}
        <div class="meta">Visible profits are after sales tax and immediate-sale broker fees. Accounting ${formatNumber(salesTax.accounting_level)} gives ${formatRatePercent(salesTax.rate)} sales tax; broker fee is 0% when selling to an existing buy order.</div>
        <div class="meta">${escapeHtml(profitability.pricing_note || "Profit ranking uses nearby public market orders.")}</div>
      `;
      updateProfitFilterButtons();
      renderFilteredProfitabilityProducts();
    }

    function renderDecisionCounts(counts) {
      const labels = [
        ["build-now", "Build"],
        ["source-missing", "Buy Missing"],
        ["use-stock", "Use Stock"],
        ["price-check", "Price Check"],
        ["watch", "Watch"],
        ["skip", "Skip"],
      ];
      const badges = labels
        .filter(([code]) => Number(counts[code] || 0) > 0)
        .map(([code, label]) => `<span class="pill ${decisionClassName(code)}">${escapeHtml(label)} ${formatNumber(counts[code])}</span>`)
        .join("");
      return `<div class="decision-counts">${badges || '<span class="meta">No decision buckets yet.</span>'}</div>`;
    }

    function updateProfitFilterButtons() {
      flightProfitFilters.querySelectorAll("button[data-profit-filter]").forEach((button) => {
        button.classList.toggle("active", button.dataset.profitFilter === flightProfitFilter);
      });
    }

    function filteredProfitabilityProducts() {
      if (flightProfitFilter === "all") return flightProfitProducts;
      return flightProfitProducts.filter((product) => (product.decision || {}).code === flightProfitFilter);
    }

    function renderFilteredProfitabilityProducts() {
      flightProfitTop.innerHTML = renderProfitabilityProducts(filteredProfitabilityProducts());
    }

    function renderProfitabilityProducts(products) {
      if (!products.length) return `<div class="decision-empty">No matching profitability decisions yet.</div>`;
      return `<div class="decision-list">${products.slice(0, 12).map((product) => {
        const decision = product.decision || {};
        const buyer = product.best_buyer
          ? `${escapeHtml(product.best_buyer.system_name)} (${formatNumber(product.best_buyer.jumps)}j)`
          : "no nearby buyer";
        const build = product.can_build_one_run
          ? "buildable now"
          : `missing ${formatNumber(product.missing_material_types)} material types`;
        const missing = renderMissingMaterials(product.missing_materials || []);
        const decisionClass = decisionClassName(decision.code);
        const afterTaxProfit = formatSignedIsk(product.taxed_replacement_profit);
        const afterTaxWalletGain = formatSignedIsk(product.taxed_cash_profit);
        const blueprintQuality = renderBlueprintQuality(product);
        return `
          <div class="decision-row" data-decision="${escapeHtml(decision.code || "unknown")}">
            <div class="decision-head">
              <strong>${escapeHtml(product.product_name)}</strong>
              <span class="pill ${decisionClass}">${escapeHtml(decision.label || "Review")}</span>
            </div>
            <div class="meta">${escapeHtml(decision.reason || "Review current buyer and material pricing.")}</div>
            <div class="decision-lede">Expected after tax and fees: ${afterTaxProfit} per run</div>
            <div class="decision-metrics">
              <div class="decision-metric"><span>True Profit</span><b>${afterTaxProfit} (${formatPercent(product.taxed_replacement_margin_percent)})</b><small>After sales tax; counts all materials as valuable.</small></div>
              <div class="decision-metric"><span>Wallet Gain</span><b>${afterTaxWalletGain} (${formatPercent(product.taxed_cash_margin_percent)})</b><small>After sales tax; only subtracts missing buys.</small></div>
              <div class="decision-metric"><span>Buyer</span><b>${buyer}</b></div>
              <div class="decision-metric"><span>Materials</span><b>${build}</b><small>${blueprintQuality}</small></div>
            </div>
            <div class="meta">${missing}</div>
            ${renderProfitMathDetails(product)}
          </div>
        `;
      }).join("")}</div>`;
    }

    function renderProfitMathDetails(product) {
      const detailRows = [
        ["Blueprint quality", renderBlueprintQuality(product)],
        ["Buyer revenue", formatIsk(product.product_revenue)],
        [`Sales tax (${formatRatePercent(product.sales_tax_rate)})`, `-${formatIsk(product.sales_tax)}`],
        ["Immediate-sale broker fee", formatIsk(product.broker_fee)],
        ["Net revenue after tax and fees", formatIsk(product.net_revenue)],
        ["ME-adjusted all materials value", `-${formatIsk(product.replacement_cost)}`],
        ["ME-adjusted missing materials", `-${formatIsk(product.missing_replacement_cost)}`],
        ["Before-tax true profit", formatSignedIsk(product.replacement_profit)],
        ["Before-tax wallet gain", formatSignedIsk(product.cash_profit)],
      ];
      return `
        <details class="profit-details">
          <summary>Math details</summary>
          <div class="profit-detail-grid">
            ${detailRows.map(([label, value]) => `
              <div class="profit-detail-row"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>
            `).join("")}
          </div>
          <div class="meta">Before-tax values are shown here only. The card ranking uses after-tax true profit.</div>
        </details>
      `;
    }

    function renderMissingMaterials(materials) {
      if (!materials.length) return "No missing materials for the first run.";
      return `Missing: ${materials.slice(0, 3).map((material) => `${escapeHtml(material.name)} ${formatNumber(material.missing)}`).join(", ")}`;
    }

    function decisionClassName(code) {
      const classes = {
        "build-now": "decision-build",
        "source-missing": "decision-source",
        "use-stock": "decision-stock",
        "price-check": "decision-price",
        "watch": "decision-watch",
        "skip": "decision-skip",
      };
      return classes[code] || "decision-watch";
    }

    function resetFlightHauling(message) {
      haulRouteSummary.textContent = message;
      haulRoutePath.textContent = "";
      haulOpportunitySummary.textContent = "No route scan has run yet.";
      haulOpportunityTop.textContent = "";
      haulScanButton.disabled = false;
    }

    async function loadFlightHauling() {
      const settings = writeHaulSettings({
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        detourJumps: haulDetourJumps.value,
      });
      haulScanButton.disabled = true;
      haulRouteSummary.textContent = `Scanning route to ${settings.destination} with ${formatNumber(settings.detourJumps)} detour jumps...`;
      haulRoutePath.textContent = "";
      haulOpportunitySummary.innerHTML = `
        <div class="progress-status" aria-live="polite">
          <span class="progress-spinner" aria-hidden="true"></span>
          <div class="progress-copy">
            <strong>Comparing corridor sell orders and destination buy orders</strong>
            <span>Cargo ${formatVolume(settings.cargoM3)}; destination ${escapeHtml(settings.destination)}.</span>
          </div>
        </div>
        <div class="progress-bar" aria-hidden="true"><span></span></div>
      `;
      haulOpportunityTop.innerHTML = `<div class="decision-empty">Route opportunities will appear here when the scan finishes.</div>`;
      try {
        const params = new URLSearchParams({
          destination: settings.destination,
          cargo_m3: String(settings.cargoM3),
          detour_jumps: String(settings.detourJumps),
        });
        const response = await fetch(`/api/flight/hauling?${params}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not scan hauler route");
        renderFlightHauling(data);
      } catch (error) {
        haulRouteSummary.textContent = error.message;
        haulRoutePath.textContent = "";
        haulOpportunitySummary.textContent = "Route scan failed.";
        haulOpportunityTop.textContent = "";
      } finally {
        haulScanButton.disabled = false;
      }
    }

    function renderFlightHauling(data) {
      const route = data.route || {};
      const hauling = data.hauling || {};
      const origin = route.origin || {};
      const destination = route.destination || {};
      const salesTax = hauling.sales_tax || {};
      const materialLimit = hauling.material_truncated ? ` Limited to ${formatNumber(hauling.scanned_materials)} of ${formatNumber(hauling.total_materials)} materials.` : "";
      const regionLimit = hauling.pickup_region_truncated ? ` Limited to ${formatNumber(hauling.pickup_regions_scanned)} of ${formatNumber(hauling.pickup_regions_total)} pickup regions.` : "";
      haulRouteSummary.innerHTML = `
        <strong>${escapeHtml(origin.name || "Current system")}</strong> to
        <strong>${escapeHtml(destination.name || route.destination_query || "destination")}</strong>:
        ${formatNumber(route.route_jumps)} jumps, ${formatNumber(hauling.pickup_system_count)} pickup systems,
        ${formatVolume(route.cargo_capacity_m3)} cargo.
      `;
      haulRoutePath.innerHTML = renderHaulRoutePath(route.systems || []);
      haulOpportunitySummary.innerHTML = `
        <div class="profit-stats">
          <div class="profit-stat"><span>Profitable</span><b>${formatNumber(hauling.profitable_opportunities)}</b></div>
          <div class="profit-stat"><span>Sell Orders</span><b>${formatNumber(hauling.sell_order_count)}</b></div>
          <div class="profit-stat"><span>Buy Orders</span><b>${formatNumber(hauling.buy_order_count)}</b></div>
          <div class="profit-stat"><span>Materials</span><b>${formatNumber(hauling.scanned_materials)}</b></div>
        </div>
        <div class="meta">
          Pickup detour ${formatNumber(hauling.detour_jumps)} jumps; scanned ${formatNumber(hauling.pickup_regions_scanned)}
          pickup regions.${escapeHtml(materialLimit + regionLimit)}
        </div>
        <div class="meta">Accounting ${formatNumber(salesTax.accounting_level)} gives ${formatRatePercent(salesTax.rate)} sales tax on destination buy-order sales.</div>
        <div class="meta">${escapeHtml(hauling.pricing_note || "Public market order route scan.")}</div>
      `;
      haulOpportunityTop.innerHTML = renderHaulOpportunities(hauling.opportunities || []);
    }

    function renderHaulRoutePath(systems) {
      if (!systems.length) return "No route path returned yet.";
      const visible = systems.slice(0, 18).map((system) => escapeHtml(system.name)).join(" &rarr; ");
      const hiddenCount = Math.max(0, systems.length - 18);
      return hiddenCount ? `${visible} &rarr; ${formatNumber(hiddenCount)} more` : visible;
    }

    function renderHaulOpportunities(opportunities) {
      if (!opportunities.length) return `<div class="decision-empty">No profitable route opportunities found for this destination yet.</div>`;
      return `<div class="decision-list">${opportunities.slice(0, 12).map((item) => {
        const pickup = item.pickup_order || {};
        const destination = item.destination_order || {};
        const extraJumps = item.extra_route_jumps == null ? "unknown" : `${formatNumber(item.extra_route_jumps)} extra`;
        const cargoNote = item.volume_m3 == null
          ? "volume unknown"
          : `${formatVolume(item.volume_m3)} each${item.cargo_limited ? "; cargo limited" : ""}`;
        return `
          <div class="decision-row">
            <div class="decision-head">
              <strong>${escapeHtml(item.item_name)}</strong>
              <span class="pill decision-build">${formatSignedIsk(item.net_profit)}</span>
            </div>
            <div class="decision-lede">Buy in ${escapeHtml(pickup.system_name || "pickup")} at ${formatIsk(pickup.price)}; sell in ${escapeHtml(destination.system_name || "destination")} at ${formatIsk(destination.price)}.</div>
            <div class="decision-metrics">
              <div class="decision-metric"><span>Units</span><b>${formatNumber(item.units)}</b><small>${escapeHtml(cargoNote)}</small></div>
              <div class="decision-metric"><span>After-Tax Per Unit</span><b>${formatSignedIsk(item.net_profit_per_unit)}</b><small>${formatPercent(item.margin_percent)} on pickup cost.</small></div>
              <div class="decision-metric"><span>Pickup</span><b>${escapeHtml(pickup.system_name || "unknown")}</b><small>${formatNumber(pickup.jumps)} jumps from current; ${formatNumber(item.pickup_detour_jumps)} from route.</small></div>
              <div class="decision-metric"><span>Route</span><b>${escapeHtml(extraJumps)}</b><small>Origin-pickup-destination compared with direct route.</small></div>
            </div>
            <details class="profit-details">
              <summary>Order details</summary>
              <div class="profit-detail-grid">
                <div class="profit-detail-row"><span>Pickup order remaining</span><b>${formatNumber(pickup.volume_remain)}</b></div>
                <div class="profit-detail-row"><span>Destination order remaining</span><b>${formatNumber(destination.volume_remain)}</b></div>
                <div class="profit-detail-row"><span>Gross spread per unit</span><b>${formatSignedIsk(item.gross_spread_per_unit)}</b></div>
                <div class="profit-detail-row"><span>Sales tax</span><b>${formatRatePercent(item.sales_tax_rate)}</b></div>
              </div>
            </details>
          </div>
        `;
      }).join("")}</div>`;
    }

    function resetFlightIndustry(message) {
      flightBlueprintSummary.textContent = message;
      flightBlueprintTop.textContent = "";
      flightAssetSummary.textContent = message;
      flightAssetTop.textContent = "";
      flightRecipeSummary.textContent = message;
      flightBuildabilityTop.textContent = "";
      flightIndustryNote.textContent = "No warps, orders, contracts, clicks, or client input are performed by this page.";
    }

    async function loadFlightIndustry() {
      flightBlueprintSummary.textContent = "Scanning owned blueprints...";
      flightBlueprintTop.textContent = "";
      flightAssetSummary.textContent = "Scanning owned asset stacks...";
      flightAssetTop.textContent = "";
      flightRecipeSummary.textContent = "Checking static recipe cache...";
      flightBuildabilityTop.textContent = "";
      try {
        const response = await fetch("/api/flight/industry");
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not load industry inventory");
        renderFlightIndustry(data.industry || {});
      } catch (error) {
        flightBlueprintSummary.textContent = error.message;
        flightAssetSummary.textContent = error.message;
        flightRecipeSummary.textContent = error.message;
        flightBuildabilityTop.textContent = "";
        resetFlightProfitability("Industry analysis requires a connected ESI session with blueprint and asset scopes.");
        flightIndustryNote.textContent = "Industry analysis requires a connected ESI session with blueprint and asset scopes.";
      }
    }

    function renderFlightIndustry(industry) {
      const blueprints = industry.blueprints || {};
      const assets = industry.assets || {};
      const recipes = industry.recipes || {};
      const buildability = industry.buildability || {};
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
      flightRecipeSummary.innerHTML = renderRecipeSummary(recipes, buildability);
      flightBuildabilityTop.innerHTML = renderBuildabilityList(buildability.top_candidates || []);
      flightIndustryNote.textContent = industry.next_step || "Recipe cache and market orders are needed before profitability ranking.";
    }

    function renderTopList(items, valueKey) {
      if (!items.length) return "No top items returned yet.";
      return items.map((item) => {
        const value = formatNumber(item[valueKey]);
        const product = item.product_name ? ` to ${escapeHtml(item.product_name)}` : "";
        const recipe = item.recipe_known === true ? " &middot; recipe" : item.recipe_known === false ? " &middot; no recipe" : "";
        const quality = item.best_material_efficiency != null
          ? ` &middot; ME ${formatNumber(item.best_material_efficiency)} / TE ${formatNumber(item.best_time_efficiency)}`
          : "";
        return `${escapeHtml(item.name)}${product} (${value})${recipe}${quality}`;
      }).join("<br>");
    }

    function renderRecipeSummary(recipes, buildability) {
      if (!recipes.available) {
        return `${escapeHtml(recipes.error || "Recipe cache missing.")}`;
      }
      const build = recipes.build_number ? ` build <strong>${escapeHtml(recipes.build_number)}</strong>` : "";
      return `
        Static recipes${build}: <strong>${formatNumber(recipes.known_blueprint_types)}</strong> known,
        <strong>${formatNumber(recipes.missing_blueprint_types)}</strong> missing.
        <br>One-run material coverage: <strong>${formatNumber(buildability.buildable_one_run_types)}</strong>
        of <strong>${formatNumber(buildability.known_blueprint_types)}</strong> known types, using owned blueprint ME.
      `;
    }

    function renderBlueprintQuality(item) {
      const runs = item.blueprint_runs == null ? "unlimited runs" : `${formatNumber(item.blueprint_runs)} runs`;
      return `${escapeHtml(item.blueprint_kind || "Blueprint")} | ME ${formatNumber(item.blueprint_material_efficiency)} / TE ${formatNumber(item.blueprint_time_efficiency)} | ${runs}`;
    }

    function renderBuildabilityList(items) {
      if (!items.length) return "No recipe candidates ready yet.";
      return items.map((item) => {
        const quality = renderBlueprintQuality(item);
        if (item.can_build_one_run) {
          return `${escapeHtml(item.product_name)}: ME-adjusted one-run materials covered (${quality})`;
        }
        const missing = (item.missing_materials || [])
          .map((material) => `${escapeHtml(material.name)} ${formatNumber(material.shortage)}`)
          .join(", ");
        return `${escapeHtml(item.product_name)}: missing ${missing || "materials"} (${quality})`;
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

    flightBuyerScanButton.addEventListener("click", () => {
      loadFlightBuyers();
    });

    flightProfitScanButton.addEventListener("click", () => {
      loadFlightProfitability();
    });

    haulRouteForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadFlightHauling();
    });

    haulHubButtons.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-haul-destination]");
      if (!button) return;
      haulDestination.value = button.dataset.haulDestination || "Jita";
      writeHaulSettings({
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        detourJumps: haulDetourJumps.value,
      });
    });

    flightProfitFilters.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-profit-filter]");
      if (!button) return;
      flightProfitFilter = button.dataset.profitFilter || "all";
      updateProfitFilterButtons();
      renderFilteredProfitabilityProducts();
    });

    flightMaxJumps.addEventListener("change", () => {
      writeMaxJumps(flightMaxJumps.value);
      resetFlightBuyers(`Ready to scan buy orders within ${readMaxJumps()} jumps.`);
      resetFlightProfitability(`Ready to rank profitability within ${readMaxJumps()} jumps.`);
      loadFlightStatus();
    });

    function alertDiscordSyncProblem(data) {
      if (data.discord_sync_error) {
        window.alert(`Updated locally, but Discord did not sync: ${data.discord_sync_error}`);
      }
    }

    writeMaxJumps(readMaxJumps());
    writeHaulSettings(readHaulSettings());
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
