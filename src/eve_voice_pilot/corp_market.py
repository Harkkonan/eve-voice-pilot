from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse
from urllib.request import Request, urlopen
import uuid
import webbrowser
import zipfile

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
from eve_voice_pilot.planetary_industry import (
    PlanetaryIndustryError,
    PlanetaryMarketPrice,
    PlanetaryTaxProfile,
    load_planetary_industry_cache,
    rank_planetary_opportunities,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKET_DB_PATH = ROOT / "profiles" / "corp_market.sqlite3"
DEFAULT_INDUSTRY_RECIPE_CACHE_PATH = ROOT / "cache" / "eve_industry_recipes.json"
DEFAULT_ROUTE_GRAPH_CACHE_PATH = ROOT / "cache" / "eve_route_graph.json"
DEFAULT_REPROCESSING_CACHE_PATH = ROOT / "cache" / "eve_reprocessing.json"
DEFAULT_PLANETARY_CACHE_PATH = ROOT / "cache" / "eve_planetary_industry.json"
DEFAULT_STATIC_DATA_ZIP_PATH = ROOT / "cache" / "eve-online-static-data-3374020-jsonl.zip"
STATIC_ASSET_ROOT = ROOT / "src" / "eve_voice_pilot" / "static"
DEFAULT_PORT = 8770
DEFAULT_MAX_NOTES_LENGTH = 5000
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 10.0
DEFAULT_FLIGHT_MAX_JUMPS = 5
MAX_FLIGHT_MAX_JUMPS = 25
MAX_FLIGHT_BUYER_SCAN_PRODUCTS = 40
MAX_FLIGHT_BUYER_SCAN_REGIONS = 8
MAX_FLIGHT_PROFIT_MATERIAL_TYPES = 120
MAX_FLIGHT_HAUL_MATERIAL_TYPES = 80
MAX_FLIGHT_HAUL_SCAN_TYPES = 240
MAX_FLIGHT_ACQUISITION_SCAN_TYPES = 100
MAX_HAUL_MARKET_GROUP_IDS = 32
MAX_FLIGHT_HAUL_OPPORTUNITIES = 20
MAX_FLIGHT_ACQUISITION_OPPORTUNITIES = 20
DEFAULT_FLIGHT_TRADE_PNL_DAYS = 30
MAX_FLIGHT_TRADE_PNL_DAYS = 30
DEFAULT_FLIGHT_TRADE_PNL_WINDOW_HOURS = 720
MAX_FLIGHT_TRADE_PNL_WINDOW_HOURS = 720
MAX_FLIGHT_TRADE_PNL_PAGES = 6
MAX_FLIGHT_TRADE_JOURNAL_PAGES = 6
MAX_FLIGHT_TRADE_PNL_TRANSACTIONS = 10_000
MAX_FLIGHT_TRADE_PNL_ITEMS = 40
DEFAULT_PLANETARY_HUB_SYSTEM = "Jita"
DEFAULT_PLANETARY_OUTPUT_TIER = "P2"
PLANETARY_OUTPUT_TIERS = frozenset({"P1", "P2", "P3", "P4"})
MAX_PLANETARY_OPPORTUNITIES = 30
DEFAULT_PLANETARY_OWNER_TAX_PERCENT = 5.0
DEFAULT_PLANETARY_NPC_TAX_PERCENT = 10.0
DEFAULT_PLANETARY_CUSTOMS_CODE_EXPERTISE_LEVEL = 0
DEFAULT_PLANETARY_SALES_TAX_PERCENT = 3.0
DEFAULT_PLANETARY_BROKER_FEE_PERCENT = 0.0
MAX_PLANETARY_TAX_PERCENT = 100.0
DEFAULT_HAUL_DESTINATION_SYSTEM = "Jita"
DEFAULT_HAUL_DETOUR_JUMPS = 1
MAX_HAUL_DETOUR_JUMPS = 5
DEFAULT_HAUL_CARGO_M3 = 10_000.0
MAX_HAUL_CARGO_M3 = 10_000_000.0
DEFAULT_HAUL_MIN_DETOUR_MARGIN_PERCENT = 10.0
MAX_HAUL_MIN_DETOUR_MARGIN_PERCENT = 500.0
DEFAULT_ACQUISITION_BUDGET_ISK = 50_000_000.0
MAX_ACQUISITION_BUDGET_ISK = 1_000_000_000_000.0
DEFAULT_ACQUISITION_PICKUP_JUMPS = 2
MAX_ACQUISITION_PICKUP_JUMPS = 10
DEFAULT_ACQUISITION_BROKER_FEE_PERCENT = 3.0
MAX_ACQUISITION_BROKER_FEE_PERCENT = 20.0
DEFAULT_ACQUISITION_TARGET_DAYS = 3
MAX_ACQUISITION_TARGET_DAYS = 30
DEFAULT_HAUL_ROUTE_PREFERENCE = "safer"
HAUL_ROUTE_PREFERENCES = {"shorter", "safer", "less_secure"}
HAUL_ROUTE_ESI_PREFERENCES = {"shorter": "Shorter", "safer": "Safer", "less_secure": "LessSecure"}
DEFAULT_HAUL_AVOID_RECENT_POD_KILLS = False
MAX_HAUL_ROUTE_AVOID_SYSTEMS = 100
MARKET_ORDER_CACHE_TTL_SECONDS = 300.0
SYSTEM_KILLS_CACHE_TTL_SECONDS = 300.0
JITA_SYSTEM_NAME = "Jita"
JITA_SOLAR_SYSTEM_ID = 30000142
JITA_REGION_ID = 10000002
FLIGHT_LOCATION_SCOPE = "esi-location.read_location.v1"
FLIGHT_ASSETS_SCOPE = "esi-assets.read_assets.v1"
FLIGHT_BLUEPRINTS_SCOPE = "esi-characters.read_blueprints.v1"
FLIGHT_SKILLS_SCOPE = "esi-skills.read_skills.v1"
FLIGHT_STANDINGS_SCOPE = "esi-characters.read_standings.v1"
FLIGHT_IMPLANTS_SCOPE = "esi-clones.read_implants.v1"
FLIGHT_STRUCTURES_SCOPE = "esi-universe.read_structures.v1"
FLIGHT_WALLET_SCOPE = "esi-wallet.read_character_wallet.v1"
DEFAULT_FLIGHT_ESI_SCOPES = (
    FLIGHT_LOCATION_SCOPE,
    FLIGHT_ASSETS_SCOPE,
    FLIGHT_BLUEPRINTS_SCOPE,
    FLIGHT_SKILLS_SCOPE,
    FLIGHT_STANDINGS_SCOPE,
    FLIGHT_IMPLANTS_SCOPE,
    FLIGHT_STRUCTURES_SCOPE,
    FLIGHT_WALLET_SCOPE,
)
ACCOUNTING_SKILL_TYPE_ID = 16622
REPROCESSING_SKILL_TYPE_ID = 3385
REPROCESSING_EFFICIENCY_SKILL_TYPE_ID = 3389
REPROCESSING_IMPLANT_BONUSES = {
    27175: 1.0,  # Zainou 'Beancounter' Reprocessing RX-801
    27169: 2.0,  # Zainou 'Beancounter' Reprocessing RX-802
    27174: 4.0,  # Zainou 'Beancounter' Reprocessing RX-804
}
DEFAULT_REPROCESSING_FACILITY_YIELD_PERCENT = 50.0
MAX_REPROCESSING_ORE_UNITS = 1_000_000_000_000
MAX_REPROCESSING_FACILITY_YIELD_PERCENT = 100.0
MAX_REPROCESSING_BONUS_PERCENT = 100.0
MAX_REPROCESSING_TAX_PERCENT = 100.0
MAX_REPROCESSING_STATION_OPTIONS = 200
MIN_REPROCESSING_STATION_STANDING = 1.5
DEFAULT_REPROCESSING_STATION_SORT = "net_yield"
REPROCESSING_STATION_SORT_MODES = frozenset({"net_yield", "processing_fee", "standing"})
BASE_SALES_TAX_RATE = 0.075
ACCOUNTING_SALES_TAX_REDUCTION_PER_LEVEL = 0.11
TRADE_PNL_MARKET_FEE_REF_TYPES = frozenset(
    {
        "brokers_fee",
        "market_fine_paid",
        "market_provider_tax",
        "transaction_tax",
    }
)
TRADE_PNL_LENSES = {"inventory", "realized", "cashflow"}
TRADE_PNL_TOKEN_ALIASES = {"pyrite": "pyerite"}
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
HAUL_MARKET_GROUP_ROOTS = (
    (11, "Ammunition & Charges"),
    (1396, "Apparel"),
    (2, "Blueprints & Reactions"),
    (157, "Drones"),
    (24, "Implants & Boosters"),
    (475, "Manufacture & Research"),
    (3628, "Personalization"),
    (1922, "Pilot's Services"),
    (1320, "Planetary Infrastructure"),
    (9, "Ship Equipment"),
    (1954, "Ship SKINs"),
    (955, "Ship and Module Modifications"),
    (4, "Ships"),
    (150, "Skills"),
    (1659, "Special Edition Assets"),
    (2202, "Structure Equipment"),
    (2203, "Structure Modifications"),
    (477, "Structures"),
    (19, "Trade Goods"),
)
HAUL_MARKET_GROUP_CHILDREN = {
    11: (
        (1015, "Bombs"),
        (3725, "Breacher Pods"),
        (139, "Cap Booster Charges"),
        (2297, "Command Burst Charges"),
        (2728, "Condenser Packs"),
        (2462, "Exotic Plasma Charges"),
        (101, "Frequency Crystals"),
        (100, "Hybrid Charges"),
        (593, "Mining Crystals"),
        (114, "Missiles"),
        (1103, "Nanite Repair Paste"),
        (120, "Probes"),
        (99, "Projectile Ammo"),
        (1094, "Scripts"),
        (2820, "Structure Area Denial Ammunition"),
        (2198, "Structure Guided Bombs"),
    ),
    1396: ((1407, "Accessories"), (1397, "Men's Clothing"), (3846, "Portrait Backgrounds"), (1402, "Women's Clothing")),
    2: (
        (211, "Ammunition & Charges"),
        (357, "Drones"),
        (1041, "Manufacture & Research"),
        (1849, "Reaction Formulas"),
        (209, "Ship Equipment"),
        (943, "Ship Modifications"),
        (204, "Ships"),
        (2158, "Structure Equipment"),
        (2157, "Structure Modifications"),
        (1338, "Structures"),
    ),
    157: (
        (159, "Combat Drones"),
        (843, "Combat Utility Drones"),
        (841, "Electronic Warfare Drones"),
        (2236, "Fighters"),
        (842, "Logistic Drones"),
        (158, "Mining Drones"),
        (1646, "Salvage Drones"),
    ),
    24: ((977, "Booster"), (2487, "Cerebral Accelerators"), (27, "Implants")),
    475: ((1035, "Components"), (533, "Materials"), (1872, "Research Equipment")),
    3628: ((3630, "Design Elements"), (3629, "Sequencing Binders")),
    1922: ((3745, "Expert Systems"), (2701, "HyperNet Relay"), (1923, "PLEX"), (1942, "Pilot's Services"), (2358, "Skill Trading")),
    1320: ((1322, "Command Centers"), (1410, "Orbital Infrastructure")),
    9: (
        (2815, "Compressors"),
        (938, "Drone Upgrades"),
        (657, "Electronic Warfare"),
        (656, "Electronics and Sensor Upgrades"),
        (655, "Engineering Equipment"),
        (779, "Fleet Assistance Modules"),
        (1713, "Harvest Equipment"),
        (14, "Hull & Armor"),
        (52, "Propulsion"),
        (1708, "Scanning Equipment"),
        (554, "Shield"),
        (141, "Smartbombs"),
        (10, "Turrets & Launchers"),
    ),
    1954: (
        (1955, "Battlecruisers"),
        (1960, "Battleships"),
        (1968, "Capital Ships"),
        (2283, "Capsules"),
        (2306, "Corvettes"),
        (1988, "Cruisers"),
        (1989, "Destroyers"),
        (1998, "Frigates"),
        (2006, "Haulers and Industrial Ships"),
        (2011, "Mining Barges"),
        (2119, "Multiple Hull SKINs"),
        (2315, "Shuttles"),
    ),
    955: ((2436, "Mutaplasmids"), (1111, "Rigs"), (1112, "Subsystems")),
    4: (
        (1374, "Battlecruisers"),
        (1376, "Battleships"),
        (1381, "Capital Ships"),
        (1815, "Corvettes"),
        (1367, "Cruisers"),
        (1372, "Destroyers"),
        (1361, "Frigates"),
        (1382, "Haulers and Industrial Ships"),
        (1384, "Mining Barges"),
        (391, "Shuttles"),
        (1612, "Special Edition Ships"),
    ),
    150: (
        (1745, "Armor"),
        (365, "Corporation Management"),
        (366, "Drones"),
        (367, "Electronic Systems"),
        (368, "Engineering"),
        (370, "Fleet Support"),
        (364, "Gunnery"),
        (373, "Missiles"),
        (374, "Navigation"),
        (1746, "Neural Enhancement"),
        (1823, "Planet Management"),
        (369, "Production"),
        (1323, "Resource Processing"),
        (372, "Rigging"),
        (1110, "Scanning"),
        (375, "Science"),
        (3656, "Sequencing"),
        (1747, "Shields"),
        (376, "Social"),
        (377, "Spaceship Command"),
        (2152, "Structure Management"),
        (1824, "Subsystems"),
        (1748, "Targeting"),
        (378, "Trade"),
    ),
    1659: (
        (1662, "Special Edition Apparel"),
        (1661, "Special Edition Commodities"),
        (1663, "Special Edition Festival Assets"),
        (1814, "Special Edition Implants"),
        (1660, "Special Edition Tournament Cards"),
    ),
    2202: (
        (2206, "Electronic Warfare"),
        (2207, "Electronics and Sensor Upgrades"),
        (2208, "Engineering Equipment"),
        (2749, "Quantum Cores"),
        (2210, "Service Modules"),
        (2209, "Structure Weapons"),
    ),
    2203: ((2205, "Structure Combat Rigs"), (2340, "Structure Engineering Rigs"), (2204, "Structure Resource Processing Rigs")),
    477: (
        (2199, "Citadels"),
        (404, "Deployable Structures"),
        (2324, "Engineering Complexes"),
        (3624, "FLEX Structures"),
        (2327, "Refineries"),
        (1272, "Sovereignty Structures"),
        (1285, "Starbase Structures"),
    ),
    19: (
        (2801, "AEGIS Databases"),
        (3732, "Acceleration Gate Keys"),
        (1427, "Aurum Tokens"),
        (1846, "Bounty Encrypted Bonds"),
        (492, "Consumer Products"),
        (1840, "Covert Research Tools"),
        (739, "Criminal Dog Tags"),
        (614, "Criminal Evidence"),
        (2456, "Filaments"),
        (20, "Industrial Goods"),
        (616, "Insignias"),
        (3737, "Limited Rarities"),
        (491, "Narcotics"),
        (738, "Nexus Chips"),
        (23, "Passengers"),
        (754, "Political Paraphernalia"),
        (22, "Radioactive Goods"),
        (2799, "Rogue Drone Data"),
        (1700, "Security Tags"),
        (1109, "Sleeper Components"),
        (940, "Starbase Charters"),
        (2317, "Strong Boxes"),
        (2480, "Triglavian Data"),
        (2013, "Unknown Components"),
    ),
}
STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
SPACE_RE = re.compile(r"\s+")
ISK_AMOUNT_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmMbB]?)\s*$")
DISCORD_WEBHOOK_PATH_RE = re.compile(r"^/api/(?:v\d+/)?webhooks/\d+/[^/]+/?$")
DISCORD_SNOWFLAKE_RE = re.compile(r"^\d{5,25}$")
FIT_HEADER_RE = re.compile(r"^\[(?P<hull>[^,\]]+),\s*(?P<name>[^\]]+)\]\s*$")
FIT_QUANTITY_RE = re.compile(r"\sx[\d,]+\s*$", re.IGNORECASE)
MARKET_ORDER_CACHE_LOCK = threading.Lock()
MARKET_ORDER_CACHE: dict[tuple[str, int, int, str], tuple[float, list[dict[str, Any]]]] = {}
MARKET_PRICE_CACHE_LOCK = threading.Lock()
MARKET_PRICE_CACHE: dict[str, tuple[float, dict[int, dict[str, Any]]]] = {}
MARKET_HISTORY_CACHE_LOCK = threading.Lock()
MARKET_HISTORY_CACHE: dict[tuple[str, int, int], tuple[float, list[dict[str, Any]]]] = {}
SYSTEM_KILLS_CACHE_LOCK = threading.Lock()
SYSTEM_KILLS_CACHE: dict[str, tuple[float, tuple[int, ...]]] = {}
STATIC_MARKET_DATA_LOCK = threading.Lock()
STATIC_MARKET_DATA_CACHE: dict[tuple[str, float, int], "StaticMarketData"] = {}
REPROCESSING_CACHE_LOCK = threading.Lock()
REPROCESSING_CACHE_CACHE: dict[tuple[str, float, int], "ReprocessingCache"] = {}
MARKET_GROUP_DETAIL_CACHE_LOCK = threading.Lock()
MARKET_GROUP_DETAIL_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
MARKET_TYPE_DETAIL_CACHE_LOCK = threading.Lock()
MARKET_TYPE_DETAIL_CACHE: dict[tuple[str, int], dict[str, Any]] = {}


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
    membership_ok: bool = True

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
            "membership_ok": self.membership_ok,
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
            membership_ok=pilot.membership_ok,
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
class IndustrySkill:
    type_id: int
    name: str
    level: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IndustrySkill | None":
        try:
            type_id = int(payload.get("type_id") or payload.get("typeID") or 0)
            level = int(payload.get("level") or 0)
        except (TypeError, ValueError):
            return None
        name = str(payload.get("name") or "").strip()
        if type_id <= 0 or level <= 0:
            return None
        return cls(type_id=type_id, name=name or f"Skill {type_id}", level=level)

    def to_dict(self) -> dict[str, Any]:
        return {"type_id": self.type_id, "name": self.name, "level": self.level}


@dataclass(frozen=True)
class IndustryRecipe:
    blueprint_type_id: int
    blueprint_name: str
    product_type_id: int
    product_name: str
    product_quantity: int
    materials: tuple[IndustryMaterial, ...]
    manufacturing_time_seconds: int = 0
    max_production_limit: int = 0
    skills: tuple[IndustrySkill, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IndustryRecipe | None":
        try:
            blueprint_type_id = int(payload.get("blueprint_type_id") or payload.get("blueprintTypeID") or 0)
            product_type_id = int(payload.get("product_type_id") or payload.get("productTypeID") or 0)
            product_quantity = int(payload.get("product_quantity") or payload.get("quantity") or 0)
            manufacturing_time = int(payload.get("manufacturing_time_seconds") or payload.get("time") or 0)
            max_production_limit = int(payload.get("max_production_limit") or payload.get("maxProductionLimit") or 0)
        except (TypeError, ValueError):
            return None
        materials = tuple(
            material
            for item in payload.get("materials", [])
            if isinstance(item, dict)
            for material in [IndustryMaterial.from_dict(item)]
            if material is not None
        )
        skills = tuple(
            skill
            for item in payload.get("skills", [])
            if isinstance(item, dict)
            for skill in [IndustrySkill.from_dict(item)]
            if skill is not None
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
            max_production_limit=max(0, max_production_limit),
            skills=skills,
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
        return self.quantity == -1 or self.runs == -1

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
class ReprocessingMaterial:
    type_id: int
    name: str
    quantity: int
    volume_m3: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReprocessingMaterial | None":
        try:
            type_id = int(payload.get("type_id") or payload.get("typeID") or 0)
            quantity = int(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            return None
        if type_id <= 0 or quantity <= 0:
            return None
        volume_m3 = clean_optional_float(payload.get("volume_m3") or payload.get("volume"))
        if volume_m3 is not None and volume_m3 <= 0:
            volume_m3 = None
        name = str(payload.get("name") or "").strip()
        return cls(type_id=type_id, name=name or f"Type {type_id}", quantity=quantity, volume_m3=volume_m3)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type_id": self.type_id, "name": self.name, "quantity": self.quantity}
        if self.volume_m3 is not None:
            payload["volume_m3"] = self.volume_m3
        return payload


@dataclass(frozen=True)
class ReprocessingOre:
    type_id: int
    name: str
    group_id: int
    group_name: str
    portion_size: int
    specialization_skill_type_id: int
    specialization_skill_name: str
    materials: tuple[ReprocessingMaterial, ...]
    market_group_id: int | None = None
    volume_m3: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReprocessingOre | None":
        try:
            type_id = int(payload.get("type_id") or payload.get("typeID") or 0)
            group_id = int(payload.get("group_id") or payload.get("groupID") or 0)
            portion_size = int(payload.get("portion_size") or payload.get("portionSize") or 0)
            skill_type_id = int(
                payload.get("specialization_skill_type_id") or payload.get("specializationSkillTypeID") or 0
            )
        except (TypeError, ValueError):
            return None
        materials = tuple(
            material
            for item in payload.get("materials", [])
            if isinstance(item, dict)
            for material in [ReprocessingMaterial.from_dict(item)]
            if material is not None
        )
        if type_id <= 0 or group_id <= 0 or portion_size <= 0 or skill_type_id <= 0 or not materials:
            return None
        volume_m3 = clean_optional_float(payload.get("volume_m3") or payload.get("volume"))
        if volume_m3 is not None and volume_m3 <= 0:
            volume_m3 = None
        return cls(
            type_id=type_id,
            name=str(payload.get("name") or "").strip() or f"Ore {type_id}",
            group_id=group_id,
            group_name=str(payload.get("group_name") or payload.get("groupName") or "").strip()
            or f"Group {group_id}",
            market_group_id=clean_optional_int(payload.get("market_group_id") or payload.get("marketGroupID")),
            portion_size=portion_size,
            volume_m3=volume_m3,
            specialization_skill_type_id=skill_type_id,
            specialization_skill_name=str(
                payload.get("specialization_skill_name") or payload.get("specializationSkillName") or ""
            ).strip()
            or f"Skill {skill_type_id}",
            materials=materials,
        )

    def to_option_dict(self) -> dict[str, Any]:
        return {
            "type_id": self.type_id,
            "name": self.name,
            "group_name": self.group_name,
            "portion_size": self.portion_size,
            "volume_m3": self.volume_m3,
            "specialization_skill_type_id": self.specialization_skill_type_id,
            "specialization_skill_name": self.specialization_skill_name,
        }


@dataclass(frozen=True)
class ReprocessingStation:
    station_id: int
    owner_id: int | None = None
    owner_name: str = ""
    owner_faction_id: int | None = None
    solar_system_id: int | None = None
    type_id: int | None = None
    reprocessing_efficiency: float | None = None
    reprocessing_tax_rate: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReprocessingStation | None":
        try:
            station_id = int(payload.get("station_id") or payload.get("stationID") or 0)
        except (TypeError, ValueError):
            return None
        if station_id <= 0:
            return None
        reprocessing_efficiency = payload.get("reprocessing_efficiency")
        if reprocessing_efficiency is None:
            reprocessing_efficiency = payload.get("reprocessingEfficiency")
        reprocessing_tax_rate = payload.get("reprocessing_tax_rate")
        if reprocessing_tax_rate is None:
            reprocessing_tax_rate = payload.get("reprocessingStationsTake")
        return cls(
            station_id=station_id,
            owner_id=clean_optional_int(payload.get("owner_id") or payload.get("ownerID")),
            owner_name=str(payload.get("owner_name") or payload.get("ownerName") or ""),
            owner_faction_id=clean_optional_int(payload.get("owner_faction_id") or payload.get("ownerFactionID")),
            solar_system_id=clean_optional_int(payload.get("solar_system_id") or payload.get("solarSystemID")),
            type_id=clean_optional_int(payload.get("type_id") or payload.get("typeID")),
            reprocessing_efficiency=clean_optional_float(reprocessing_efficiency),
            reprocessing_tax_rate=clean_optional_float(reprocessing_tax_rate),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "owner_faction_id": self.owner_faction_id,
            "solar_system_id": self.solar_system_id,
            "type_id": self.type_id,
            "reprocessing_efficiency": self.reprocessing_efficiency,
            "reprocessing_tax_rate": self.reprocessing_tax_rate,
        }


@dataclass(frozen=True)
class ReprocessingCache:
    path: Path
    available: bool
    build_number: int | None = None
    release_date: str = ""
    generated_at: str = ""
    ores: dict[int, ReprocessingOre] | None = None
    stations: dict[int, ReprocessingStation] | None = None
    error: str = ""

    @property
    def ore_count(self) -> int:
        return len(self.ores or {})

    @property
    def station_count(self) -> int:
        return len(self.stations or {})

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "path": str(self.path),
            "build_number": self.build_number,
            "release_date": self.release_date,
            "generated_at": self.generated_at,
            "ore_count": self.ore_count,
            "station_count": self.station_count,
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
class StaticMarketData:
    path: Path
    groups: dict[int, dict[str, Any]]
    children: dict[int, tuple[int, ...]]
    types_by_group: dict[int, tuple[dict[str, Any], ...]]


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


def fetch_flight_wallet_transactions(
    config: EveSsoConfig,
    session: FlightEsiSession,
    *,
    max_pages: int = MAX_FLIGHT_TRADE_PNL_PAGES,
    max_transactions: int = MAX_FLIGHT_TRADE_PNL_TRANSACTIONS,
) -> list[dict[str, Any]]:
    require_flight_scopes(session, (FLIGHT_WALLET_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    transactions: list[dict[str, Any]] = []
    seen_transaction_ids: set[int] = set()
    from_id: int | None = None
    clean_max_pages = max(1, int(max_pages))
    clean_max_transactions = max(1, int(max_transactions))
    for _page_index in range(clean_max_pages):
        params = {"datasource": "tranquility"}
        if from_id:
            params["from_id"] = str(from_id)
        request = Request(
            add_query_params(f"{base_url}/characters/{session.character_id}/wallet/transactions/", params),
            headers={"Accept": "application/json", **flight_esi_headers(session.access_token)},
            method="GET",
        )
        try:
            with urlopen(request, timeout=45.0) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise CorpMarketError(f"ESI wallet transactions request failed: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CorpMarketError(f"ESI wallet transactions returned non-JSON data: {raw[:200]!r}") from exc
        if not isinstance(payload, list):
            raise CorpMarketError("ESI wallet transactions returned unexpected data.")
        page_transactions = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            transaction_id = clean_optional_int(item.get("transaction_id"))
            if transaction_id is None or transaction_id <= 0 or transaction_id in seen_transaction_ids:
                continue
            seen_transaction_ids.add(transaction_id)
            page_transactions.append(item)
            transactions.append(item)
            if len(transactions) >= clean_max_transactions:
                return transactions
        if not page_transactions or len(payload) < 2500:
            break
        next_from_id = clean_optional_int(page_transactions[-1].get("transaction_id"))
        if next_from_id is None or next_from_id <= 0 or next_from_id == from_id:
            break
        from_id = next_from_id
    return transactions


def fetch_flight_wallet_journal(
    config: EveSsoConfig,
    session: FlightEsiSession,
    *,
    max_pages: int = MAX_FLIGHT_TRADE_JOURNAL_PAGES,
) -> list[dict[str, Any]]:
    require_flight_scopes(session, (FLIGHT_WALLET_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    entries: list[dict[str, Any]] = []
    page = 1
    page_count = 1
    clean_max_pages = max(1, int(max_pages))
    while page <= page_count and page <= clean_max_pages:
        request = Request(
            add_query_params(
                f"{base_url}/characters/{session.character_id}/wallet/journal/",
                {"datasource": "tranquility", "page": str(page)},
            ),
            headers={"Accept": "application/json", **flight_esi_headers(session.access_token)},
            method="GET",
        )
        try:
            with urlopen(request, timeout=45.0) as response:
                raw = response.read().decode("utf-8")
                page_count = max(1, int(response.headers.get("X-Pages") or "1"))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise CorpMarketError(f"ESI wallet journal request failed: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CorpMarketError(f"ESI wallet journal returned non-JSON data: {raw[:200]!r}") from exc
        if not isinstance(payload, list):
            raise CorpMarketError("ESI wallet journal returned unexpected data.")
        entries.extend(item for item in payload if isinstance(item, dict))
        page += 1
    return entries


def build_flight_trade_pnl_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession,
    days: int = DEFAULT_FLIGHT_TRADE_PNL_DAYS,
    window_hours: Any = None,
    accounting_lens: str = "inventory",
    excluded_tokens: Iterable[str] = (),
    include_matches: bool = False,
) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_WALLET_SCOPE,))
    clean_window_hours = clamp_trade_pnl_window_hours(
        window_hours if window_hours is not None and str(window_hours).strip() else clamp_trade_pnl_days(days) * 24
    )
    transactions = fetch_flight_wallet_transactions(config, session)
    journal_entries = fetch_flight_wallet_journal(config, session)
    type_ids = {
        type_id
        for transaction in transactions
        for type_id in [clean_optional_int(transaction.get("type_id"))]
        if type_id is not None and type_id > 0
    }
    type_names = resolve_trade_type_names(config, type_ids)
    trade_pnl = analyze_trade_pnl_transactions(
        transactions,
        journal_entries=journal_entries,
        type_names=type_names,
        window_hours=clean_window_hours,
        accounting_lens=accounting_lens,
        excluded_tokens=excluded_tokens,
        include_matches=include_matches,
    )
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "trade_pnl": trade_pnl,
    }


def resolve_trade_type_names(config: EveSsoConfig, type_ids: Iterable[int]) -> dict[int, str]:
    clean_type_ids = sorted({int(type_id) for type_id in type_ids if int(type_id) > 0})
    if not clean_type_ids:
        return {}
    try:
        return fetch_universe_names(config, clean_type_ids)
    except CorpMarketError:
        return {}


def analyze_trade_pnl_transactions(
    transactions: Iterable[dict[str, Any]],
    *,
    journal_entries: Iterable[dict[str, Any]] = (),
    type_names: dict[int, str] | None = None,
    days: int = DEFAULT_FLIGHT_TRADE_PNL_DAYS,
    window_hours: Any = None,
    accounting_lens: str = "inventory",
    excluded_tokens: Iterable[str] = (),
    include_matches: bool = False,
    now: datetime | None = None,
    item_limit: int = MAX_FLIGHT_TRADE_PNL_ITEMS,
) -> dict[str, Any]:
    clean_window_hours = clamp_trade_pnl_window_hours(
        window_hours if window_hours is not None and str(window_hours).strip() else clamp_trade_pnl_days(days) * 24
    )
    clean_lens = normalize_trade_pnl_lens(accounting_lens)
    clean_excluded_tokens = clean_trade_pnl_excluded_tokens(excluded_tokens)
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(hours=clean_window_hours)
    visible_transactions = [
        transaction
        for transaction in transactions
        if trade_record_datetime(transaction.get("date")) is not None
        and trade_record_datetime(transaction.get("date")) >= cutoff
    ]
    visible_journal_entries = [
        entry
        for entry in journal_entries
        if trade_record_datetime(entry.get("date")) is not None and trade_record_datetime(entry.get("date")) >= cutoff
    ]
    fee_by_transaction_id: dict[int, float] = {}
    market_fee_total = 0.0
    market_transaction_journal_total = 0.0
    fee_ref_counts: dict[str, int] = {}
    for entry in visible_journal_entries:
        ref_type = str(entry.get("ref_type") or "").strip()
        amount = clean_optional_float(entry.get("amount"))
        if amount is None:
            continue
        if ref_type == "market_transaction":
            market_transaction_journal_total += amount
        if ref_type not in TRADE_PNL_MARKET_FEE_REF_TYPES:
            continue
        market_fee_total += amount
        fee_ref_counts[ref_type] = fee_ref_counts.get(ref_type, 0) + 1
        context_id = clean_optional_int(entry.get("context_id"))
        context_type = str(entry.get("context_id_type") or "").strip()
        if context_id is not None and context_type == "market_transaction_id":
            fee_by_transaction_id[context_id] = fee_by_transaction_id.get(context_id, 0.0) + amount

    names = type_names or {}
    summaries: dict[int, dict[str, Any]] = {}
    open_lots: dict[int, list[dict[str, Any]]] = {}
    ignored_transactions = 0
    for transaction in sorted(
        visible_transactions,
        key=lambda item: (
            trade_record_datetime(item.get("date")) or datetime.min.replace(tzinfo=timezone.utc),
            clean_optional_int(item.get("transaction_id")) or 0,
        ),
    ):
        type_id = clean_optional_int(transaction.get("type_id"))
        transaction_id = clean_optional_int(transaction.get("transaction_id"))
        quantity = clean_optional_int(transaction.get("quantity"))
        unit_price = clean_optional_float(transaction.get("unit_price"))
        if type_id is None or transaction_id is None or quantity is None or quantity <= 0 or unit_price is None or unit_price < 0:
            ignored_transactions += 1
            continue
        summary = trade_pnl_summary_for_type(summaries, type_id=type_id, type_name=names.get(type_id) or f"Type {type_id}")
        transaction_date = str(transaction.get("date") or "")
        summary["transaction_count"] += 1
        summary["first_date"] = min_trade_date(summary.get("first_date"), transaction_date)
        summary["last_date"] = max_trade_date(summary.get("last_date"), transaction_date)
        transaction_total = float(quantity) * float(unit_price)
        transaction_fee = fee_by_transaction_id.get(transaction_id, 0.0)
        summary["allocated_fee_isk"] += transaction_fee
        if bool(transaction.get("is_buy")):
            summary["buy_quantity"] += quantity
            summary["buy_total_isk"] += transaction_total
            open_lots.setdefault(type_id, []).append(
                {
                    "quantity_remaining": quantity,
                    "unit_cost": float(unit_price),
                    "transaction_id": transaction_id,
                    "date": transaction_date,
                }
            )
            continue
        summary["sell_quantity"] += quantity
        summary["sell_total_isk"] += transaction_total
        remaining_to_match = quantity
        lots = open_lots.setdefault(type_id, [])
        while remaining_to_match > 0 and lots:
            lot = lots[0]
            lot_quantity = int(lot.get("quantity_remaining") or 0)
            if lot_quantity <= 0:
                lots.pop(0)
                continue
            matched_quantity = min(remaining_to_match, lot_quantity)
            buy_cost = float(lot.get("unit_cost") or 0.0) * matched_quantity
            sell_revenue = float(unit_price) * matched_quantity
            match_fee = transaction_fee * (matched_quantity / quantity) if quantity > 0 else 0.0
            summary["matched_quantity"] += matched_quantity
            summary["matched_buy_cost_isk"] += buy_cost
            summary["matched_sell_revenue_isk"] += sell_revenue
            if include_matches:
                summary["matches"].append(
                    {
                        "buy_transaction_id": lot.get("transaction_id"),
                        "buy_date": lot.get("date") or "",
                        "sell_transaction_id": transaction_id,
                        "sell_date": transaction_date,
                        "quantity": matched_quantity,
                        "buy_unit_price": float(lot.get("unit_cost") or 0.0),
                        "sell_unit_price": float(unit_price),
                        "buy_cost_isk": buy_cost,
                        "sell_revenue_isk": sell_revenue,
                        "fee_isk": match_fee,
                        "expected_profit_isk": sell_revenue - buy_cost,
                        "actual_profit_isk": sell_revenue - buy_cost + match_fee,
                    }
                )
            lot["quantity_remaining"] = lot_quantity - matched_quantity
            remaining_to_match -= matched_quantity
            if int(lot.get("quantity_remaining") or 0) <= 0:
                lots.pop(0)
        if remaining_to_match > 0:
            summary["unmatched_sell_quantity"] += remaining_to_match
            summary["unmatched_sell_revenue_isk"] += float(unit_price) * remaining_to_match

    for type_id, lots in open_lots.items():
        summary = summaries.get(type_id)
        if summary is None:
            continue
        for lot in lots:
            quantity_remaining = int(lot.get("quantity_remaining") or 0)
            if quantity_remaining <= 0:
                continue
            summary["open_quantity"] += quantity_remaining
            summary["open_inventory_cost_isk"] += quantity_remaining * float(lot.get("unit_cost") or 0.0)

    items = []
    totals = trade_pnl_empty_totals()
    for summary in summaries.values():
        excluded, excluded_reason = trade_pnl_item_excluded(summary, clean_excluded_tokens)
        summary["excluded"] = excluded
        summary["excluded_reason"] = excluded_reason
        expected_profit = summary["matched_sell_revenue_isk"] - summary["matched_buy_cost_isk"]
        actual_profit = expected_profit + summary["allocated_fee_isk"]
        summary["expected_profit_isk"] = round(expected_profit, 4)
        summary["actual_profit_isk"] = round(actual_profit, 4)
        summary["fee_gap_isk"] = round(summary["allocated_fee_isk"], 4)
        summary["net_cashflow_isk"] = round(
            summary["sell_total_isk"] - summary["buy_total_isk"] + summary["allocated_fee_isk"],
            4,
        )
        summary["margin_percent"] = profit_margin_percent(actual_profit, summary["matched_buy_cost_isk"])
        summary["status"] = trade_pnl_item_status(summary)
        for key in totals:
            totals[key] += float(summary.get(key) or 0.0)
        items.append(summary)

    allocated_fee_total = sum(float(item.get("allocated_fee_isk") or 0.0) for item in items)
    unallocated_fee_total = market_fee_total - allocated_fee_total
    visible_actual_profit = totals["expected_profit_isk"] + allocated_fee_total
    wallet_fee_adjusted_profit = totals["expected_profit_isk"] + market_fee_total
    considered = trade_pnl_considered_totals(items, lens=clean_lens, unallocated_fee_total=unallocated_fee_total)
    items.sort(key=lambda item: (-abs(float(item.get("actual_profit_isk") or item.get("expected_profit_isk") or 0.0)), str(item["item_name"])))
    limited_items = items[: max(1, int(item_limit))]
    return {
        "days": max(1, int(round(clean_window_hours / 24.0))),
        "window_hours": clean_window_hours,
        "window_label": trade_pnl_window_label(clean_window_hours),
        "accounting_lens": clean_lens,
        "accounting_lens_label": trade_pnl_lens_label(clean_lens),
        "excluded_tokens": list(clean_excluded_tokens),
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "transaction_count": len(visible_transactions),
        "ignored_transaction_count": ignored_transactions,
        "journal_entry_count": len(visible_journal_entries),
        "item_count": len(items),
        "item_limit": max(1, int(item_limit)),
        "items_truncated": len(items) > max(1, int(item_limit)),
        "items": [trade_pnl_public_item(item) for item in limited_items],
        "totals": {
            "buy_total_isk": round(totals["buy_total_isk"], 4),
            "sell_total_isk": round(totals["sell_total_isk"], 4),
            "matched_quantity": int(totals["matched_quantity"]),
            "matched_buy_cost_isk": round(totals["matched_buy_cost_isk"], 4),
            "matched_sell_revenue_isk": round(totals["matched_sell_revenue_isk"], 4),
            "expected_profit_isk": round(totals["expected_profit_isk"], 4),
            "allocated_fee_isk": round(allocated_fee_total, 4),
            "unallocated_fee_isk": round(unallocated_fee_total, 4),
            "market_fee_isk": round(market_fee_total, 4),
            "actual_profit_isk": round(visible_actual_profit, 4),
            "wallet_fee_adjusted_profit_isk": round(wallet_fee_adjusted_profit, 4),
            "net_cashflow_isk": round(totals["net_cashflow_isk"] + unallocated_fee_total, 4),
            "open_inventory_cost_isk": round(totals["open_inventory_cost_isk"], 4),
            "open_quantity": int(totals["open_quantity"]),
            "unmatched_sell_revenue_isk": round(totals["unmatched_sell_revenue_isk"], 4),
            "unmatched_sell_quantity": int(totals["unmatched_sell_quantity"]),
            "market_transaction_journal_total_isk": round(market_transaction_journal_total, 4),
            "considered_result_isk": round(considered["considered_result_isk"], 4),
            "excluded_actual_profit_isk": round(considered["excluded_actual_profit_isk"], 4),
            "excluded_cashflow_isk": round(considered["excluded_cashflow_isk"], 4),
            "excluded_item_count": int(considered["excluded_item_count"]),
            "considered_item_count": int(considered["considered_item_count"]),
            "loss_item_count": int(considered["loss_item_count"]),
        },
        "fee_ref_counts": dict(sorted(fee_ref_counts.items())),
        "expectation_source": "FIFO buy-vs-sell spread before wallet fees. Saved planner expectation snapshots are not stored yet.",
        "limits_note": (
            "Only wallet transactions visible through recent ESI history are matched. "
            "Sells without visible buy lots are excluded from realized P&L and shown as unmatched."
        ),
    }


def trade_pnl_summary_for_type(summaries: dict[int, dict[str, Any]], *, type_id: int, type_name: str) -> dict[str, Any]:
    return summaries.setdefault(
        type_id,
        {
            "type_id": type_id,
            "item_name": type_name,
            "transaction_count": 0,
            "buy_quantity": 0,
            "sell_quantity": 0,
            "matched_quantity": 0,
            "unmatched_sell_quantity": 0,
            "open_quantity": 0,
            "buy_total_isk": 0.0,
            "sell_total_isk": 0.0,
            "matched_buy_cost_isk": 0.0,
            "matched_sell_revenue_isk": 0.0,
            "allocated_fee_isk": 0.0,
            "unmatched_sell_revenue_isk": 0.0,
            "open_inventory_cost_isk": 0.0,
            "expected_profit_isk": 0.0,
            "actual_profit_isk": 0.0,
            "net_cashflow_isk": 0.0,
            "fee_gap_isk": 0.0,
            "margin_percent": None,
            "first_date": "",
            "last_date": "",
            "status": "no-match",
            "excluded": False,
            "excluded_reason": "",
            "matches": [],
        },
    )


def trade_pnl_empty_totals() -> dict[str, float]:
    return {
        "buy_total_isk": 0.0,
        "sell_total_isk": 0.0,
        "matched_quantity": 0.0,
        "matched_buy_cost_isk": 0.0,
        "matched_sell_revenue_isk": 0.0,
        "expected_profit_isk": 0.0,
        "net_cashflow_isk": 0.0,
        "open_inventory_cost_isk": 0.0,
        "open_quantity": 0.0,
        "unmatched_sell_revenue_isk": 0.0,
        "unmatched_sell_quantity": 0.0,
    }


def trade_pnl_public_item(item: dict[str, Any]) -> dict[str, Any]:
    public_item = dict(item)
    public_item["matches"] = [trade_pnl_public_match(match) for match in public_item.get("matches") or []]
    for key in (
        "buy_total_isk",
        "sell_total_isk",
        "matched_buy_cost_isk",
        "matched_sell_revenue_isk",
        "allocated_fee_isk",
        "unmatched_sell_revenue_isk",
        "open_inventory_cost_isk",
        "expected_profit_isk",
        "actual_profit_isk",
        "net_cashflow_isk",
        "fee_gap_isk",
    ):
        public_item[key] = round(float(public_item.get(key) or 0.0), 4)
    return public_item


def trade_pnl_public_match(match: dict[str, Any]) -> dict[str, Any]:
    public_match = dict(match)
    for key in (
        "buy_unit_price",
        "sell_unit_price",
        "buy_cost_isk",
        "sell_revenue_isk",
        "fee_isk",
        "expected_profit_isk",
        "actual_profit_isk",
    ):
        public_match[key] = round(float(public_match.get(key) or 0.0), 4)
    public_match["quantity"] = int(public_match.get("quantity") or 0)
    return public_match


def trade_pnl_item_status(item: dict[str, Any]) -> str:
    if bool(item.get("excluded")):
        if int(item.get("matched_quantity") or 0) > 0:
            actual_profit = float(item.get("actual_profit_isk") or 0.0)
            if actual_profit > 0:
                return "excluded-profit"
            if actual_profit < 0:
                return "excluded-loss"
        return "excluded"
    if int(item.get("matched_quantity") or 0) <= 0:
        if int(item.get("unmatched_sell_quantity") or 0) > 0:
            return "needs-older-buys"
        if int(item.get("open_quantity") or 0) > 0:
            return "open-stock"
        return "no-match"
    actual_profit = float(item.get("actual_profit_isk") or 0.0)
    if actual_profit > 0:
        return "profit"
    if actual_profit < 0:
        return "loss"
    return "break-even"


def trade_pnl_considered_totals(items: Iterable[dict[str, Any]], *, lens: str, unallocated_fee_total: float) -> dict[str, Any]:
    considered_result = 0.0
    excluded_actual = 0.0
    excluded_cashflow = 0.0
    excluded_count = 0
    considered_count = 0
    loss_count = 0
    for item in items:
        actual_profit = float(item.get("actual_profit_isk") or 0.0)
        cashflow = float(item.get("net_cashflow_isk") or 0.0)
        if actual_profit < 0 and int(item.get("matched_quantity") or 0) > 0:
            loss_count += 1
        if bool(item.get("excluded")):
            excluded_count += 1
            excluded_actual += actual_profit
            excluded_cashflow += cashflow
            continue
        considered_count += 1
        if lens == "cashflow":
            considered_result += cashflow
        else:
            considered_result += actual_profit
    if lens == "cashflow":
        considered_result += float(unallocated_fee_total or 0.0)
    return {
        "considered_result_isk": considered_result,
        "excluded_actual_profit_isk": excluded_actual,
        "excluded_cashflow_isk": excluded_cashflow,
        "excluded_item_count": excluded_count,
        "considered_item_count": considered_count,
        "loss_item_count": loss_count,
    }


def trade_pnl_item_excluded(item: dict[str, Any], excluded_tokens: Iterable[str]) -> tuple[bool, str]:
    name = str(item.get("item_name") or "")
    normalized_name = normalize_trade_pnl_token(name)
    type_id_text = str(item.get("type_id") or "")
    for token in excluded_tokens:
        normalized = normalize_trade_pnl_token(token)
        if not normalized:
            continue
        if normalized == type_id_text or normalized in normalized_name:
            return True, f"Excluded by {token}"
    return False, ""


def clean_trade_pnl_excluded_tokens(tokens: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for token in tokens:
        for part in str(token or "").split(","):
            clean = SPACE_RE.sub(" ", part.strip())
            if clean and clean not in cleaned:
                cleaned.append(clean[:80])
    return tuple(cleaned[:30])


def normalize_trade_pnl_token(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    return TRADE_PNL_TOKEN_ALIASES.get(normalized, normalized)


def normalize_trade_pnl_lens(value: Any) -> str:
    normalized = normalize_trade_pnl_token(value)
    aliases = {
        "stock": "inventory",
        "inventorymode": "inventory",
        "realizedtrading": "realized",
        "realizedtradingpnl": "realized",
        "cash": "cashflow",
        "walletcashflow": "cashflow",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in TRADE_PNL_LENSES:
        return "inventory"
    return normalized


def trade_pnl_lens_label(lens: str) -> str:
    if lens == "cashflow":
        return "Wallet Cashflow"
    if lens == "realized":
        return "Realized Trading P&L"
    return "Inventory Mode"


def trade_pnl_window_label(window_hours: int) -> str:
    if window_hours < 24:
        return f"{window_hours} hour" if window_hours == 1 else f"{window_hours} hours"
    days = window_hours / 24.0
    if days.is_integer():
        clean_days = int(days)
        return f"{clean_days} day" if clean_days == 1 else f"{clean_days} days"
    return f"{round(days, 1)} days"


def trade_record_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def min_trade_date(current: Any, candidate: Any) -> str:
    if not current:
        return str(candidate or "")
    current_dt = trade_record_datetime(current)
    candidate_dt = trade_record_datetime(candidate)
    if candidate_dt is None:
        return str(current or "")
    if current_dt is None or candidate_dt < current_dt:
        return str(candidate or "")
    return str(current or "")


def max_trade_date(current: Any, candidate: Any) -> str:
    if not current:
        return str(candidate or "")
    current_dt = trade_record_datetime(current)
    candidate_dt = trade_record_datetime(candidate)
    if candidate_dt is None:
        return str(current or "")
    if current_dt is None or candidate_dt > current_dt:
        return str(candidate or "")
    return str(current or "")


def fetch_flight_standings(config: EveSsoConfig, session: FlightEsiSession) -> list[dict[str, Any]]:
    require_flight_scopes(session, (FLIGHT_STANDINGS_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    payload = get_json(
        f"{base_url}/characters/{session.character_id}/standings/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=flight_esi_headers(session.access_token),
    )
    if not isinstance(payload, list):
        raise CorpMarketError("ESI standings endpoint returned unexpected data.")
    return [item for item in payload if isinstance(item, dict)]


def fetch_flight_implants(config: EveSsoConfig, session: FlightEsiSession) -> list[int]:
    require_flight_scopes(session, (FLIGHT_IMPLANTS_SCOPE,))
    base_url = config.esi_base_url.rstrip("/")
    payload = get_json(
        f"{base_url}/characters/{session.character_id}/implants/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=flight_esi_headers(session.access_token),
    )
    if not isinstance(payload, list):
        raise CorpMarketError("ESI implants endpoint returned unexpected data.")
    implant_ids: list[int] = []
    for item in payload:
        try:
            type_id = int(item)
        except (TypeError, ValueError):
            continue
        if type_id > 0:
            implant_ids.append(type_id)
    return implant_ids


def fetch_universe_structure(config: EveSsoConfig, session: FlightEsiSession, structure_id: int) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_STRUCTURES_SCOPE,))
    if structure_id <= 0:
        raise CorpMarketError("Structure id is required.")
    base_url = config.esi_base_url.rstrip("/")
    payload = get_json(
        f"{base_url}/universe/structures/{structure_id}/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=flight_esi_headers(session.access_token),
    )
    if not isinstance(payload, dict):
        raise CorpMarketError("ESI structure endpoint returned unexpected data.")
    return payload


def fetch_universe_station(config: EveSsoConfig, station_id: int) -> dict[str, Any]:
    if station_id <= 0:
        raise CorpMarketError("Station id is required.")
    base_url = config.esi_base_url.rstrip("/")
    payload = get_json(
        f"{base_url}/universe/stations/{station_id}/?datasource=tranquility",
        timeout_seconds=30.0,
        headers=flight_esi_headers(),
    )
    if not isinstance(payload, dict):
        raise CorpMarketError("ESI station endpoint returned unexpected data.")
    return payload


def localized_name(payload: Any, fallback: str = "") -> str:
    if isinstance(payload, dict):
        return str(payload.get("en") or payload.get("en-us") or next(iter(payload.values()), fallback) or fallback)
    if payload is None:
        return fallback
    return str(payload)


def clean_haul_market_group_ids(raw_values: Iterable[Any]) -> tuple[int, ...]:
    group_ids: list[int] = []
    for raw_value in raw_values:
        if raw_value is None:
            continue
        for part in re.split(r"[,|\s]+", str(raw_value)):
            if not part:
                continue
            try:
                group_id = int(part)
            except (TypeError, ValueError):
                continue
            if group_id > 0 and group_id not in group_ids:
                group_ids.append(group_id)
            if len(group_ids) >= MAX_HAUL_MARKET_GROUP_IDS:
                return tuple(group_ids)
    return tuple(group_ids)


def load_static_market_data(path: Path = DEFAULT_STATIC_DATA_ZIP_PATH) -> StaticMarketData | None:
    if not path.exists():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    cache_key = (str(path.resolve()), stat.st_mtime, stat.st_size)
    with STATIC_MARKET_DATA_LOCK:
        cached = STATIC_MARKET_DATA_CACHE.get(cache_key)
        if cached is not None:
            return cached
    groups: dict[int, dict[str, Any]] = {}
    children: dict[int, list[int]] = {}
    types_by_group: dict[int, list[dict[str, Any]]] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open("marketGroups.jsonl") as market_groups:
                for raw_line in market_groups:
                    payload = json.loads(raw_line)
                    group_id = int(payload.get("_key") or 0)
                    if group_id <= 0:
                        continue
                    parent_id = clean_optional_int(payload.get("parentGroupID"))
                    groups[group_id] = {
                        "market_group_id": group_id,
                        "name": localized_name(payload.get("name"), f"Market Group {group_id}"),
                        "parent_group_id": parent_id,
                    }
                    if parent_id is not None and parent_id > 0:
                        children.setdefault(parent_id, []).append(group_id)
            with archive.open("types.jsonl") as types:
                for raw_line in types:
                    payload = json.loads(raw_line)
                    market_group_id = clean_optional_int(payload.get("marketGroupID"))
                    if market_group_id is None or market_group_id <= 0:
                        continue
                    if payload.get("published") is False:
                        continue
                    type_id = int(payload.get("_key") or 0)
                    if type_id <= 0:
                        continue
                    volume_m3 = clean_optional_float(payload.get("volume"))
                    if volume_m3 is not None and volume_m3 <= 0:
                        volume_m3 = None
                    types_by_group.setdefault(market_group_id, []).append(
                        {
                            "type_id": type_id,
                            "name": localized_name(payload.get("name"), f"Type {type_id}"),
                            "volume_m3": volume_m3,
                            "market_group_id": market_group_id,
                            "market_group_name": groups.get(market_group_id, {}).get(
                                "name",
                                f"Market Group {market_group_id}",
                            ),
                        }
                    )
    except (OSError, KeyError, ValueError, json.JSONDecodeError, zipfile.BadZipFile):
        return None
    static_data = StaticMarketData(
        path=path,
        groups=groups,
        children={group_id: tuple(sorted(child_ids)) for group_id, child_ids in children.items()},
        types_by_group={
            group_id: tuple(sorted(type_infos, key=lambda item: (str(item["name"]), int(item["type_id"]))))
            for group_id, type_infos in types_by_group.items()
        },
    )
    with STATIC_MARKET_DATA_LOCK:
        STATIC_MARKET_DATA_CACHE.clear()
        STATIC_MARKET_DATA_CACHE[cache_key] = static_data
    return static_data


def market_group_descendant_ids(static_data: StaticMarketData, group_ids: Iterable[int]) -> set[int]:
    selected: set[int] = set()
    stack = [int(group_id) for group_id in group_ids if int(group_id) > 0]
    while stack:
        group_id = stack.pop()
        if group_id in selected:
            continue
        selected.add(group_id)
        stack.extend(static_data.children.get(group_id, ()))
    return selected


def fetch_market_group_detail(config: EveSsoConfig, market_group_id: int) -> dict[str, Any]:
    base_url = config.esi_base_url.rstrip("/")
    cache_key = (base_url, int(market_group_id))
    with MARKET_GROUP_DETAIL_CACHE_LOCK:
        cached = MARKET_GROUP_DETAIL_CACHE.get(cache_key)
        if cached is not None:
            return dict(cached)
    url = add_query_params(f"{base_url}/markets/groups/{int(market_group_id)}/", {"datasource": "tranquility"})
    try:
        payload = get_json(url, timeout_seconds=30.0, headers=flight_esi_headers())
    except (CorpIntelError, ValueError) as exc:
        raise CorpMarketError(f"ESI market group {market_group_id} lookup failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise CorpMarketError(f"ESI market group {market_group_id} returned unexpected data.")
    with MARKET_GROUP_DETAIL_CACHE_LOCK:
        MARKET_GROUP_DETAIL_CACHE[cache_key] = dict(payload)
    return payload


def fetch_universe_type_detail(config: EveSsoConfig, type_id: int) -> dict[str, Any]:
    base_url = config.esi_base_url.rstrip("/")
    cache_key = (base_url, int(type_id))
    with MARKET_TYPE_DETAIL_CACHE_LOCK:
        cached = MARKET_TYPE_DETAIL_CACHE.get(cache_key)
        if cached is not None:
            return dict(cached)
    url = add_query_params(f"{base_url}/universe/types/{int(type_id)}/", {"datasource": "tranquility"})
    try:
        payload = get_json(url, timeout_seconds=30.0, headers=flight_esi_headers())
    except (CorpIntelError, ValueError) as exc:
        raise CorpMarketError(f"ESI type {type_id} lookup failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise CorpMarketError(f"ESI type {type_id} returned unexpected data.")
    with MARKET_TYPE_DETAIL_CACHE_LOCK:
        MARKET_TYPE_DETAIL_CACHE[cache_key] = dict(payload)
    return payload


def build_market_group_targets_from_static(group_ids: Iterable[int]) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    static_data = load_static_market_data()
    if static_data is None:
        return None
    clean_group_ids = clean_haul_market_group_ids(group_ids)
    descendant_group_ids = market_group_descendant_ids(static_data, clean_group_ids)
    targets_by_type_id: dict[int, dict[str, Any]] = {}
    for group_id in sorted(descendant_group_ids):
        group_name = str(static_data.groups.get(group_id, {}).get("name") or f"Market Group {group_id}")
        for type_info in static_data.types_by_group.get(group_id, ()):
            type_id = int(type_info["type_id"])
            targets_by_type_id.setdefault(
                type_id,
                {
                    "type_id": type_id,
                    "name": str(type_info["name"]),
                    "recipe_count": 0,
                    "volume_m3": type_info.get("volume_m3"),
                    "source": "market_group",
                    "source_label": group_name,
                    "market_group_id": group_id,
                    "market_group_name": group_name,
                },
            )
    selected_groups = [
        {
            "market_group_id": group_id,
            "name": str(static_data.groups.get(group_id, {}).get("name") or f"Market Group {group_id}"),
        }
        for group_id in clean_group_ids
    ]
    return (
        sorted(targets_by_type_id.values(), key=lambda item: (str(item["name"]), int(item["type_id"]))),
        {
            "source": "static-sde",
            "selected_market_groups": selected_groups,
            "selected_market_group_ids": list(clean_group_ids),
            "selected_market_group_count": len(clean_group_ids),
            "market_group_item_types": len(targets_by_type_id),
        },
    )


def build_market_group_targets_from_esi(
    config: EveSsoConfig,
    group_ids: Iterable[int],
    *,
    detail_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    clean_group_ids = clean_haul_market_group_ids(group_ids)
    visited_group_ids: set[int] = set()
    type_group_names: dict[int, str] = {}
    selected_groups = []
    stack = list(reversed(clean_group_ids))
    while stack:
        group_id = stack.pop()
        if group_id in visited_group_ids:
            continue
        detail = fetch_market_group_detail(config, group_id)
        visited_group_ids.add(group_id)
        group_name = str(detail.get("name") or f"Market Group {group_id}")
        if group_id in clean_group_ids:
            selected_groups.append({"market_group_id": group_id, "name": group_name})
        for raw_type_id in detail.get("types") or []:
            try:
                type_id = int(raw_type_id)
            except (TypeError, ValueError):
                continue
            if type_id > 0:
                type_group_names.setdefault(type_id, group_name)
        child_ids = []
        for raw_child_id in detail.get("child_market_group_ids") or []:
            try:
                child_id = int(raw_child_id)
            except (TypeError, ValueError):
                continue
            if child_id > 0 and child_id not in visited_group_ids:
                child_ids.append(child_id)
        stack.extend(reversed(sorted(child_ids)))
    targets = []
    for type_id in sorted(type_group_names)[: max(0, detail_limit)]:
        detail = fetch_universe_type_detail(config, type_id)
        volume_m3 = clean_optional_float(detail.get("volume"))
        if volume_m3 is not None and volume_m3 <= 0:
            volume_m3 = None
        targets.append(
            {
                "type_id": type_id,
                "name": str(detail.get("name") or f"Type {type_id}"),
                "recipe_count": 0,
                "volume_m3": volume_m3,
                "source": "market_group",
                "source_label": type_group_names[type_id],
                "market_group_id": None,
                "market_group_name": type_group_names[type_id],
            }
        )
    return (
        sorted(targets, key=lambda item: (str(item["name"]), int(item["type_id"]))),
        {
            "source": "esi-market-groups",
            "selected_market_groups": selected_groups,
            "selected_market_group_ids": list(clean_group_ids),
            "selected_market_group_count": len(clean_group_ids),
            "market_group_item_types": len(type_group_names),
        },
    )


def build_market_group_targets(config: EveSsoConfig, group_ids: Iterable[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    static_result = build_market_group_targets_from_static(group_ids)
    if static_result is not None:
        return static_result
    return build_market_group_targets_from_esi(config, group_ids, detail_limit=MAX_FLIGHT_HAUL_SCAN_TYPES)


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
    cache_key = (base_url, int(region_id), int(type_id), clean_order_type)
    now = time.monotonic()
    with MARKET_ORDER_CACHE_LOCK:
        cached = MARKET_ORDER_CACHE.get(cache_key)
        if cached is not None:
            cached_at, cached_orders = cached
            if now - cached_at < MARKET_ORDER_CACHE_TTL_SECONDS:
                return [dict(order) for order in cached_orders]
            MARKET_ORDER_CACHE.pop(cache_key, None)
    url = add_query_params(
        f"{base_url}/markets/{region_id}/orders/?datasource=tranquility",
        {"order_type": clean_order_type, "type_id": str(type_id)},
    )
    orders = get_esi_json_pages(
        url,
        headers=flight_esi_headers(),
        label=f"ESI market {clean_order_type} orders for type {type_id} in region {region_id}",
    )
    with MARKET_ORDER_CACHE_LOCK:
        MARKET_ORDER_CACHE[cache_key] = (time.monotonic(), [dict(order) for order in orders])
    return orders


def clear_market_order_cache() -> None:
    with MARKET_ORDER_CACHE_LOCK:
        MARKET_ORDER_CACHE.clear()


def fetch_market_prices(config: EveSsoConfig) -> dict[int, dict[str, Any]]:
    base_url = config.esi_base_url.rstrip("/")
    now = time.monotonic()
    with MARKET_PRICE_CACHE_LOCK:
        cached = MARKET_PRICE_CACHE.get(base_url)
        if cached is not None:
            cached_at, cached_prices = cached
            if now - cached_at < MARKET_ORDER_CACHE_TTL_SECONDS:
                return {type_id: dict(price) for type_id, price in cached_prices.items()}
            MARKET_PRICE_CACHE.pop(base_url, None)
    url = add_query_params(f"{base_url}/markets/prices/", {"datasource": "tranquility"})
    try:
        payload = get_json(url, timeout_seconds=45.0, headers=flight_esi_headers())
    except (CorpIntelError, ValueError) as exc:
        raise CorpMarketError(f"ESI market prices lookup failed: {exc}") from exc
    if not isinstance(payload, list):
        raise CorpMarketError("ESI market prices endpoint returned unexpected data.")
    prices: dict[int, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        type_id = clean_optional_int(item.get("type_id"))
        if not type_id:
            continue
        prices[type_id] = {
            "type_id": type_id,
            "average_price": clean_optional_float(item.get("average_price")),
            "adjusted_price": clean_optional_float(item.get("adjusted_price")),
        }
    with MARKET_PRICE_CACHE_LOCK:
        MARKET_PRICE_CACHE[base_url] = (time.monotonic(), {type_id: dict(price) for type_id, price in prices.items()})
    return prices


def clear_market_price_cache() -> None:
    with MARKET_PRICE_CACHE_LOCK:
        MARKET_PRICE_CACHE.clear()


def market_order_cache_status() -> dict[str, Any]:
    now = time.monotonic()
    with MARKET_ORDER_CACHE_LOCK:
        expired_keys = [
            key for key, (cached_at, _orders) in MARKET_ORDER_CACHE.items() if now - cached_at >= MARKET_ORDER_CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            MARKET_ORDER_CACHE.pop(key, None)
        entry_count = len(MARKET_ORDER_CACHE)
    return {
        "ttl_seconds": int(MARKET_ORDER_CACHE_TTL_SECONDS),
        "entries": entry_count,
        "note": "Public market orders are cached locally for 5 minutes to respect ESI market-order cache and rate limits.",
    }


def fetch_market_history(config: EveSsoConfig, *, region_id: int, type_id: int) -> list[dict[str, Any]]:
    base_url = config.esi_base_url.rstrip("/")
    cache_key = (base_url, int(region_id), int(type_id))
    now = time.time()
    with MARKET_HISTORY_CACHE_LOCK:
        cached = MARKET_HISTORY_CACHE.get(cache_key)
        if cached is not None:
            expires_at, cached_history = cached
            if now < expires_at:
                return [dict(item) for item in cached_history]
            MARKET_HISTORY_CACHE.pop(cache_key, None)
    url = add_query_params(
        f"{base_url}/markets/{region_id}/history/?datasource=tranquility",
        {"type_id": str(type_id)},
    )
    request = Request(url, headers={"Accept": "application/json", **flight_esi_headers()}, method="GET")
    try:
        with urlopen(request, timeout=45.0) as response:
            raw = response.read().decode("utf-8")
            expires_at = market_history_expiry_timestamp(response.headers.get("Expires"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise CorpMarketError(f"ESI market history for type {type_id} in region {region_id} request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorpMarketError(f"ESI market history returned non-JSON data: {raw[:200]!r}") from exc
    if not isinstance(payload, list):
        raise CorpMarketError("ESI market history returned unexpected data.")
    history = [item for item in payload if isinstance(item, dict)]
    with MARKET_HISTORY_CACHE_LOCK:
        MARKET_HISTORY_CACHE[cache_key] = (expires_at, [dict(item) for item in history])
    return history


def market_history_expiry_timestamp(expires_header: str | None) -> float:
    fallback = time.time() + 6 * 3600
    if not expires_header:
        return fallback
    try:
        expires_at = parsedate_to_datetime(expires_header)
    except (TypeError, ValueError, IndexError, OverflowError):
        return fallback
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    timestamp = expires_at.timestamp()
    if timestamp <= time.time():
        return fallback
    return timestamp


def clear_market_history_cache() -> None:
    with MARKET_HISTORY_CACHE_LOCK:
        MARKET_HISTORY_CACHE.clear()


def market_history_cache_status() -> dict[str, Any]:
    now = time.time()
    with MARKET_HISTORY_CACHE_LOCK:
        expired_keys = [key for key, (expires_at, _history) in MARKET_HISTORY_CACHE.items() if now >= expires_at]
        for key in expired_keys:
            MARKET_HISTORY_CACHE.pop(key, None)
        entry_count = len(MARKET_HISTORY_CACHE)
    return {
        "entries": entry_count,
        "note": "Public market history is cached until the ESI response expiry when available.",
    }


def fetch_esi_route_path(
    config: EveSsoConfig,
    *,
    origin_solar_system_id: int,
    destination_solar_system_id: int,
    route_preference: str,
    avoid_system_ids: Iterable[int] = (),
) -> list[int]:
    clean_preference = normalize_haul_route_preference(route_preference)
    flag = {
        "shorter": "shortest",
        "safer": "secure",
        "less_secure": "insecure",
    }[clean_preference]
    avoid_ids_set: set[int] = set()
    for raw_system_id in avoid_system_ids:
        try:
            system_id = int(raw_system_id)
        except (TypeError, ValueError):
            continue
        if system_id > 0 and system_id not in {origin_solar_system_id, destination_solar_system_id}:
            avoid_ids_set.add(system_id)
    avoid_ids = sorted(avoid_ids_set)[:MAX_HAUL_ROUTE_AVOID_SYSTEMS]
    params = {
        "datasource": "tranquility",
        "flag": flag,
    }
    if avoid_ids:
        params["avoid"] = ",".join(str(system_id) for system_id in avoid_ids)
    base_url = config.esi_base_url.rstrip("/")
    url = add_query_params(f"{base_url}/route/{origin_solar_system_id}/{destination_solar_system_id}/", params)
    try:
        payload = get_json(url, timeout_seconds=30.0, headers=flight_esi_headers())
    except (CorpIntelError, ValueError) as exc:
        raise CorpMarketError(f"ESI route planner failed: {exc}") from exc
    route_payload = payload.get("route") if isinstance(payload, dict) else payload
    if not isinstance(route_payload, list):
        raise CorpMarketError("ESI route planner returned unexpected data.")
    route_path = []
    for value in route_payload:
        try:
            system_id = int(value)
        except (TypeError, ValueError):
            continue
        if system_id > 0:
            route_path.append(system_id)
    if not route_path:
        raise CorpMarketError("ESI route planner returned no route.")
    return route_path


def fetch_recent_pod_kill_system_ids(config: EveSsoConfig) -> tuple[int, ...]:
    base_url = config.esi_base_url.rstrip("/")
    now = time.monotonic()
    with SYSTEM_KILLS_CACHE_LOCK:
        cached = SYSTEM_KILLS_CACHE.get(base_url)
        if cached is not None:
            cached_at, cached_system_ids = cached
            if now - cached_at < SYSTEM_KILLS_CACHE_TTL_SECONDS:
                return cached_system_ids
            SYSTEM_KILLS_CACHE.pop(base_url, None)
    url = add_query_params(f"{base_url}/universe/system_kills/", {"datasource": "tranquility"})
    try:
        payload = get_json(url, timeout_seconds=30.0, headers=flight_esi_headers())
    except (CorpIntelError, ValueError) as exc:
        raise CorpMarketError(f"Recent pod-kill lookup failed: {exc}") from exc
    if not isinstance(payload, list):
        raise CorpMarketError("Recent pod-kill lookup returned unexpected data.")
    system_ids = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            system_id = int(item.get("system_id") or 0)
            pod_kills = int(item.get("pod_kills") or 0)
        except (TypeError, ValueError):
            continue
        if system_id > 0 and pod_kills > 0:
            system_ids.append(system_id)
    cached_system_ids = tuple(sorted(set(system_ids)))
    with SYSTEM_KILLS_CACHE_LOCK:
        SYSTEM_KILLS_CACHE[base_url] = (time.monotonic(), cached_system_ids)
    return cached_system_ids


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
    parsed_blueprints = [
        blueprint
        for item in blueprint_items
        for blueprint in [owned_blueprint_from_esi(item)]
        if blueprint is not None
    ]
    original_count = sum(1 for blueprint in parsed_blueprints if blueprint.is_original)
    copy_count = sum(1 for blueprint in parsed_blueprints if blueprint.is_copy)
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
                    "base_time_seconds": recipes[type_id].manufacturing_time_seconds if type_id in recipes else 0,
                    "max_production_limit": recipes[type_id].max_production_limit if type_id in recipes else 0,
                    "required_skills": (
                        [skill.to_dict() for skill in recipes[type_id].skills] if type_id in recipes else []
                    ),
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


def load_reprocessing_cache(cache_path: Path = DEFAULT_REPROCESSING_CACHE_PATH) -> ReprocessingCache:
    path = Path(cache_path)
    if not path.exists():
        return ReprocessingCache(path=path, available=False, error="Reprocessing cache file is missing.")
    try:
        stat = path.stat()
    except OSError as exc:
        return ReprocessingCache(path=path, available=False, error=f"Reprocessing cache could not be read: {exc}")
    cache_key = (str(path.resolve()), stat.st_mtime, stat.st_size)
    with REPROCESSING_CACHE_LOCK:
        cached = REPROCESSING_CACHE_CACHE.get(cache_key)
        if cached is not None:
            return cached
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return ReprocessingCache(path=path, available=False, error=f"Reprocessing cache could not be read: {exc}")
    if not isinstance(payload, dict):
        return ReprocessingCache(path=path, available=False, error="Reprocessing cache has unexpected format.")

    ores_payload = payload.get("ores")
    if not isinstance(ores_payload, dict):
        return ReprocessingCache(path=path, available=False, error="Reprocessing cache is missing ores.")
    ores: dict[int, ReprocessingOre] = {}
    for item in ores_payload.values():
        if not isinstance(item, dict):
            continue
        ore = ReprocessingOre.from_dict(item)
        if ore is not None:
            ores[ore.type_id] = ore
    if not ores:
        return ReprocessingCache(path=path, available=False, error="Reprocessing cache has no usable ore records.")

    stations: dict[int, ReprocessingStation] = {}
    stations_payload = payload.get("stations")
    if isinstance(stations_payload, dict):
        for item in stations_payload.values():
            if not isinstance(item, dict):
                continue
            station = ReprocessingStation.from_dict(item)
            if station is not None:
                stations[station.station_id] = station
    try:
        build_number = int(payload.get("build_number") or payload.get("buildNumber") or 0) or None
    except (TypeError, ValueError):
        build_number = None
    cache = ReprocessingCache(
        path=path,
        available=True,
        build_number=build_number,
        release_date=str(payload.get("release_date") or payload.get("releaseDate") or ""),
        generated_at=str(payload.get("generated_at") or ""),
        ores=ores,
        stations=stations,
    )
    with REPROCESSING_CACHE_LOCK:
        REPROCESSING_CACHE_CACHE.clear()
        REPROCESSING_CACHE_CACHE[cache_key] = cache
    return cache


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
                "base_time_seconds": recipe.manufacturing_time_seconds,
                "adjusted_time_seconds": adjusted_job_time_seconds(
                    recipe.manufacturing_time_seconds,
                    int(target.get("blueprint_time_efficiency") or 0),
                    runs=1,
                ),
                "max_production_limit": recipe.max_production_limit,
                "required_skills": [skill.to_dict() for skill in recipe.skills],
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
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_LOCATION_SCOPE, FLIGHT_BLUEPRINTS_SCOPE))
    max_jumps = clamp_flight_max_jumps(max_jumps)
    if progress is not None:
        progress(
            "scan_start",
            {
                "message": f"Reading ESI location and owned blueprints for a buyer scan within {max_jumps} jumps.",
                "max_jumps": max_jumps,
                "percent": 0,
            },
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
    if progress is not None:
        progress(
            "blueprints",
            {
                "message": f"Loaded {len(blueprints)} owned blueprint records from ESI.",
                "blueprint_records": len(blueprints),
                "percent": 8,
            },
        )
    buyers = scan_buyers_for_owned_blueprints(
        config=config,
        blueprints=blueprints,
        recipe_cache=recipe_cache,
        route_cache=route_cache,
        current_solar_system_id=current_solar_system_id,
        max_jumps=max_jumps,
        progress=progress,
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
    origin_name: str = "",
    destination_name: str = DEFAULT_HAUL_DESTINATION_SYSTEM,
    detour_jumps: int = DEFAULT_HAUL_DETOUR_JUMPS,
    cargo_capacity_m3: float = DEFAULT_HAUL_CARGO_M3,
    min_detour_margin_percent: float = DEFAULT_HAUL_MIN_DETOUR_MARGIN_PERCENT,
    route_preference: str = DEFAULT_HAUL_ROUTE_PREFERENCE,
    avoid_recent_pod_kills: bool = DEFAULT_HAUL_AVOID_RECENT_POD_KILLS,
    include_common_materials: bool = True,
    market_group_ids: Iterable[int] = (),
    progress: Callable[[str, dict[str, Any]], None] | None = None,
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
    clean_origin_name = str(origin_name or "").strip()
    if clean_origin_name:
        origin = resolve_route_system(route_cache, clean_origin_name)
        if origin is None:
            raise CorpMarketError(f"Starting system {clean_origin_name!r} was not found in the route graph cache.")
        origin_source = "manual"
    else:
        origin = systems.get(current_solar_system_id)
        origin_source = "esi-location"
    if origin is None:
        raise CorpMarketError(f"Current system {current_solar_system_id} is not in the route graph cache.")
    destination = resolve_route_system(route_cache, destination_name)
    if destination is None:
        raise CorpMarketError(f"Destination system {destination_name!r} was not found in the route graph cache.")
    route_plan = build_haul_route_plan(
        config=config,
        origin_solar_system_id=origin.solar_system_id,
        destination_solar_system_id=destination.solar_system_id,
        route_preference=route_preference,
        avoid_recent_pod_kills=bool(avoid_recent_pod_kills),
        adjacency=adjacency,
    )
    route_path = route_plan["path"]
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
        min_detour_margin_percent=min_detour_margin_percent,
        sales_tax=sales_tax,
        include_common_materials=include_common_materials,
        market_group_ids=market_group_ids,
        progress=progress,
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
            "origin_query": clean_origin_name,
            "origin_source": origin_source,
            "destination_query": destination_name,
            "route_jumps": max(0, len(route_path) - 1),
            "detour_jumps": clamp_haul_detour_jumps(detour_jumps),
            "cargo_capacity_m3": clamp_haul_cargo_m3(cargo_capacity_m3),
            "min_detour_margin_percent": clamp_haul_min_detour_margin_percent(min_detour_margin_percent),
            "route_preference": route_plan["preference"],
            "route_preference_label": route_plan["preference_label"],
            "route_source": route_plan["source"],
            "avoid_recent_pod_kills": route_plan["avoid_recent_pod_kills"],
            "recent_pod_kill_system_count": route_plan["recent_pod_kill_system_count"],
            "avoided_pod_kill_system_count": len(route_plan["avoided_pod_kill_system_ids"]),
            "route_pod_kill_system_count": len(route_plan["route_pod_kill_system_ids"]),
            "route_warning": route_plan["warning"],
            "systems": route_systems,
        },
        "hauling": opportunities,
    }


def build_flight_acquisition_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession,
    origin_name: str = "",
    destination_name: str = DEFAULT_HAUL_DESTINATION_SYSTEM,
    budget_isk: float = DEFAULT_ACQUISITION_BUDGET_ISK,
    pickup_jumps: int = DEFAULT_ACQUISITION_PICKUP_JUMPS,
    min_margin_percent: float = DEFAULT_HAUL_MIN_DETOUR_MARGIN_PERCENT,
    broker_fee_percent: float = DEFAULT_ACQUISITION_BROKER_FEE_PERCENT,
    target_days: int = DEFAULT_ACQUISITION_TARGET_DAYS,
    route_preference: str = DEFAULT_HAUL_ROUTE_PREFERENCE,
    include_common_materials: bool = True,
    market_group_ids: Iterable[int] = (),
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
    clean_origin_name = str(origin_name or "").strip()
    if clean_origin_name:
        origin = resolve_route_system(route_cache, clean_origin_name)
        if origin is None:
            raise CorpMarketError(f"Starting system {clean_origin_name!r} was not found in the route graph cache.")
        origin_source = "manual"
    else:
        origin = systems.get(current_solar_system_id)
        origin_source = "esi-location"
    if origin is None:
        raise CorpMarketError(f"Current system {current_solar_system_id} is not in the route graph cache.")
    destination = resolve_route_system(route_cache, destination_name)
    if destination is None:
        raise CorpMarketError(f"Destination system {destination_name!r} was not found in the route graph cache.")
    route_plan = build_haul_route_plan(
        config=config,
        origin_solar_system_id=origin.solar_system_id,
        destination_solar_system_id=destination.solar_system_id,
        route_preference=route_preference,
        avoid_recent_pod_kills=False,
        adjacency=adjacency,
    )
    skills = fetch_flight_skills(config, session)
    sales_tax = build_sales_tax_profile(skills)
    acquisition = scan_market_acquisition_opportunities(
        config=config,
        recipe_cache=recipe_cache,
        route_cache=route_cache,
        origin=origin,
        destination=destination,
        budget_isk=budget_isk,
        pickup_jumps=pickup_jumps,
        min_margin_percent=min_margin_percent,
        broker_fee_percent=broker_fee_percent,
        target_days=target_days,
        sales_tax=sales_tax,
        include_common_materials=include_common_materials,
        market_group_ids=market_group_ids,
    )
    route_path = route_plan["path"]
    route_systems = [systems[system_id].to_dict(jumps=index) for index, system_id in enumerate(route_path) if system_id in systems]
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "location": location,
        "route": {
            "origin": origin.to_dict(jumps=0),
            "destination": destination.to_dict(jumps=max(0, len(route_path) - 1)),
            "origin_query": clean_origin_name,
            "origin_source": origin_source,
            "destination_query": destination_name,
            "route_jumps": max(0, len(route_path) - 1),
            "route_preference": route_plan["preference"],
            "route_preference_label": route_plan["preference_label"],
            "route_source": route_plan["source"],
            "route_warning": route_plan["warning"],
            "systems": route_systems,
        },
        "acquisition": acquisition,
    }


def scan_buyers_for_owned_blueprints(
    *,
    config: EveSsoConfig,
    blueprints: Iterable[dict[str, Any]],
    recipe_cache: IndustryRecipeCache,
    route_cache: RouteGraphCache,
    current_solar_system_id: int,
    max_jumps: int,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    max_jumps = clamp_flight_max_jumps(max_jumps)
    jump_distances = jump_distances_within(
        start_system_id=current_solar_system_id,
        max_jumps=max_jumps,
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
    region_count = len(scan_region_ids)
    product_count = len(scan_targets)
    total_steps = product_count * max(1, region_count)

    def progress_percent(completed_steps: int) -> int:
        if total_steps <= 0:
            return 100
        return max(10, min(99, round(10 + (completed_steps / total_steps) * 88)))

    if progress is not None:
        progress(
            "scan_scope",
            {
                "message": (
                    f"Scanning {product_count} blueprint products across {region_count} nearby market regions. "
                    f"{len(jump_distances)} systems are inside {max_jumps} jumps."
                ),
                "product_count": product_count,
                "region_count": region_count,
                "reachable_system_count": len(jump_distances),
                "total_steps": total_steps,
                "percent": 10 if total_steps else 100,
            },
        )

    products = []
    total_order_count = 0
    scan_errors = []
    for product_index, target in enumerate(scan_targets, start=1):
        product_orders = []
        if progress is not None:
            progress(
                "product_start",
                {
                    "message": f"Checking buyer orders for {target['product_name']} ({product_index}/{product_count}).",
                    "product_index": product_index,
                    "product_count": product_count,
                    "product_name": target["product_name"],
                    "percent": progress_percent((product_index - 1) * max(1, region_count)),
                },
            )
        for region_index, region_id in enumerate(scan_region_ids, start=1):
            completed_steps = ((product_index - 1) * max(1, region_count)) + region_index
            before_region_order_count = len(product_orders)
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
                if progress is not None:
                    progress(
                        "scan_warning",
                        {
                            "message": (
                                f"{target['product_name']}: region {region_index}/{region_count} failed: {exc}"
                            ),
                            "product_index": product_index,
                            "product_count": product_count,
                            "product_name": target["product_name"],
                            "region_index": region_index,
                            "region_count": region_count,
                            "percent": progress_percent(completed_steps),
                        },
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
            if progress is not None:
                nearby_order_count = len(product_orders) - before_region_order_count
                progress(
                    "orders",
                    {
                        "message": (
                            f"{target['product_name']}: region {region_index}/{region_count} returned "
                            f"{len(raw_orders)} buy orders; {nearby_order_count} are inside the jump range."
                        ),
                        "product_index": product_index,
                        "product_count": product_count,
                        "product_name": target["product_name"],
                        "region_index": region_index,
                        "region_count": region_count,
                        "raw_order_count": len(raw_orders),
                        "nearby_order_count": nearby_order_count,
                        "step": completed_steps,
                        "total_steps": total_steps,
                        "percent": progress_percent(completed_steps),
                    },
                )
        product_orders.sort(key=lambda item: (-float(item["price"]), int(item["jumps"]), -int(item["volume_remain"])))
        total_order_count += len(product_orders)
        if progress is not None:
            best_order = product_orders[0] if product_orders else None
            if best_order:
                message = (
                    f"{target['product_name']}: best nearby buyer is {best_order['system_name']} "
                    f"at {best_order['price']:.2f} ISK."
                )
            else:
                message = f"{target['product_name']}: no nearby buy orders found."
            progress(
                "product_done",
                {
                    "message": message,
                    "product_index": product_index,
                    "product_count": product_count,
                    "product_name": target["product_name"],
                    "order_count": len(product_orders),
                    "percent": progress_percent(product_index * max(1, region_count)),
                },
            )
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
        "max_jumps": max_jumps,
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
        "market_cache": market_order_cache_status(),
        "identity_note": (
            "ESI market orders do not expose buyer character names; these are public buy orders. "
            "Public market orders are cached locally for 5 minutes."
        ),
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
    jita_system = resolve_jita_market_system(systems)
    jita_material_buys, jita_material_order_count, jita_material_errors = scan_system_market_orders(
        config=config,
        type_ids=scan_material_type_ids,
        system=jita_system,
        order_type="buy",
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
        jita_raw_partial_value = 0.0
        jita_raw_full_material_types = 0
        jita_raw_partial_material_types = 0
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
            jita_liquidation = liquidation_value_from_orders(
                jita_material_buys.get(material_type_id, ()),
                quantity=required,
            )
            if jita_liquidation["priced_quantity"] > 0:
                jita_raw_partial_value += float(jita_liquidation["value"])
                jita_raw_partial_material_types += 1
                if jita_liquidation["complete"]:
                    jita_raw_full_material_types += 1
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
                "jita_buy_price": (
                    float(jita_material_buys[material_type_id][0]["price"])
                    if jita_material_buys.get(material_type_id)
                    else None
                ),
                "jita_raw_value": jita_liquidation["value"] if jita_liquidation["priced_quantity"] > 0 else None,
                "jita_raw_priced_quantity": jita_liquidation["priced_quantity"],
                "jita_raw_required_quantity": jita_liquidation["required_quantity"],
                "jita_raw_complete": jita_liquidation["complete"],
                "jita_buy_order": jita_material_buys[material_type_id][0] if jita_material_buys.get(material_type_id) else None,
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
        jita_raw_value_known = jita_raw_full_material_types == required_material_types
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
        base_time_seconds = int(target.get("manufacturing_time_seconds") or recipe.manufacturing_time_seconds or 0)
        adjusted_time_seconds = adjusted_job_time_seconds(
            base_time_seconds,
            int(target.get("blueprint_time_efficiency") or 0),
            runs=1,
        )
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
                "base_time_seconds": base_time_seconds,
                "adjusted_time_seconds": adjusted_time_seconds,
                "true_profit_per_hour": isk_per_hour(taxed_replacement_profit, adjusted_time_seconds),
                "wallet_gain_per_hour": isk_per_hour(taxed_cash_profit, adjusted_time_seconds),
                "max_production_limit": target.get("max_production_limit", 0),
                "required_skills": target.get("required_skills", []),
                "jita_raw_system_name": jita_system.name,
                "jita_raw_material_value": jita_raw_partial_value if jita_raw_value_known else None,
                "jita_partial_raw_material_value": jita_raw_partial_value if jita_raw_partial_material_types > 0 else None,
                "jita_raw_material_types": jita_raw_full_material_types,
                "jita_partial_raw_material_types": jita_raw_partial_material_types,
                "jita_raw_required_material_types": required_material_types,
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
    errors = product_errors + material_errors + jita_material_errors
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
        "jita_material_order_count": jita_material_order_count,
        "jita_raw_system": jita_system.to_dict(jumps=0),
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
        "market_cache": market_order_cache_status(),
        "pricing_note": (
            "Visible profit subtracts sales tax from the buyer revenue using your Accounting skill. "
            "Details show the before-tax true profit, wallet gain, TE-adjusted profit per hour, and Jita raw material value. "
            "Public market orders are cached locally for 5 minutes; ESI market orders do not expose buyer character names."
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
    min_detour_margin_percent: float,
    sales_tax: dict[str, Any],
    include_common_materials: bool,
    market_group_ids: Iterable[int],
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    clean_detour_jumps = clamp_haul_detour_jumps(detour_jumps)
    clean_cargo_capacity_m3 = clamp_haul_cargo_m3(cargo_capacity_m3)
    clean_min_detour_margin_percent = clamp_haul_min_detour_margin_percent(min_detour_margin_percent)
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

    item_targets, item_scope = build_haul_item_targets(
        config=config,
        recipe_cache=recipe_cache,
        include_common_materials=include_common_materials,
        market_group_ids=market_group_ids,
    )
    item_truncated = len(item_targets) > MAX_FLIGHT_HAUL_SCAN_TYPES
    scan_targets = item_targets[:MAX_FLIGHT_HAUL_SCAN_TYPES]
    if not scan_targets:
        raise CorpMarketError("Choose Common materials or at least one market category before scanning hauler routes.")
    sales_tax_rate = clean_optional_float(sales_tax.get("rate")) or 0.0

    opportunities = []
    total_sell_order_count = 0
    total_buy_order_count = 0
    detour_margin_rejected_count = 0
    errors = []
    emit_haul_route_progress(
        progress=progress,
        route_path=route_path,
        pickup_detours=pickup_detours,
        systems=systems,
        adjacency=adjacency,
        detour_jumps=clean_detour_jumps,
        destination_system_id=destination_solar_system_id,
        pickup_jump_distances=pickup_jump_distances,
        scanned_materials=len(scan_targets),
        min_detour_margin_percent=clean_min_detour_margin_percent,
    )
    for target_index, target in enumerate(scan_targets, start=1):
        type_id = int(target["type_id"])
        if progress is not None:
            progress(
                "orders",
                {
                    "type_id": type_id,
                    "item_name": target["name"],
                    "material_index": target_index,
                    "scanned_materials": len(scan_targets),
                    "message": f"Pricing {target['name']}",
                },
            )
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
        margin_percent = profit_margin_percent(net_profit_per_unit, float(sell_order["price"]))
        pickup_detour_jumps = pickup_detours.get(pickup_system_id, 0)
        if pickup_detour_jumps > 0 and (margin_percent is None or margin_percent < clean_min_detour_margin_percent):
            detour_margin_rejected_count += 1
            continue
        opportunities.append(
            {
                "type_id": type_id,
                "item_name": target["name"],
                "recipe_count": int(target.get("recipe_count") or 0),
                "source_labels": target.get("source_labels", []),
                "volume_m3": volume_m3,
                "units": units,
                "cargo_limited": cargo_limited,
                "cargo_capacity_m3": clean_cargo_capacity_m3,
                "gross_spread_per_unit": gross_spread_per_unit,
                "net_profit_per_unit": net_profit_per_unit,
                "net_profit": total_net_profit,
                "margin_percent": margin_percent,
                "sales_tax_rate": sales_tax_rate,
                "pickup_order": sell_order,
                "destination_order": buy_order,
                "pickup_detour_jumps": pickup_detour_jumps,
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
        "min_detour_margin_percent": clean_min_detour_margin_percent,
        "pickup_system_count": len(pickup_jump_distances),
        "pickup_regions_scanned": len(scan_pickup_region_ids),
        "pickup_regions_total": len(pickup_region_ids),
        "pickup_region_truncated": pickup_region_truncated,
        "destination_region_id": destination.region_id,
        "scanned_materials": len(scan_targets),
        "total_materials": len(item_targets),
        "material_truncated": item_truncated,
        "scanned_item_types": len(scan_targets),
        "total_item_types": len(item_targets),
        "item_truncated": item_truncated,
        "item_scope": {
            **item_scope,
            "scanned_item_types": len(scan_targets),
            "total_item_types": len(item_targets),
            "item_truncated": item_truncated,
        },
        "sell_order_count": total_sell_order_count,
        "buy_order_count": total_buy_order_count,
        "detour_margin_rejected_count": detour_margin_rejected_count,
        "profitable_opportunities": len(opportunities),
        "opportunities": opportunities[:MAX_FLIGHT_HAUL_OPPORTUNITIES],
        "errors": errors[:12],
        "sales_tax": sales_tax,
        "market_cache": market_order_cache_status(),
        "pricing_note": (
            "This scan compares public sell orders along the route corridor with public buy orders in the destination "
            "system. Profit is after sales tax for selling into the destination buy order. Public market orders are "
            "cached locally for 5 minutes. The page does not place orders or verify station docking access."
        ),
    }


def scan_market_acquisition_opportunities(
    *,
    config: EveSsoConfig,
    recipe_cache: IndustryRecipeCache,
    route_cache: RouteGraphCache,
    origin: RouteSystem,
    destination: RouteSystem,
    budget_isk: float,
    pickup_jumps: int,
    min_margin_percent: float,
    broker_fee_percent: float,
    target_days: int,
    sales_tax: dict[str, Any],
    include_common_materials: bool,
    market_group_ids: Iterable[int],
) -> dict[str, Any]:
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    clean_budget = clamp_acquisition_budget_isk(budget_isk)
    clean_pickup_jumps = clamp_acquisition_pickup_jumps(pickup_jumps)
    clean_min_margin_percent = clamp_haul_min_detour_margin_percent(min_margin_percent)
    clean_broker_fee_percent = clamp_acquisition_broker_fee_percent(broker_fee_percent)
    clean_target_days = clamp_acquisition_target_days(target_days)
    broker_fee_rate = clean_broker_fee_percent / 100.0
    sales_tax_rate = clean_optional_float(sales_tax.get("rate")) or 0.0

    pickup_distances = jump_distances_from(
        start_system_id=origin.solar_system_id,
        adjacency=adjacency,
        max_jumps=clean_pickup_jumps,
    )
    pickup_region_ranks: dict[int, int] = {}
    for system_id, jumps in pickup_distances.items():
        system = systems.get(system_id)
        if system is None or system.region_id is None:
            continue
        current_rank = pickup_region_ranks.get(system.region_id)
        if current_rank is None or jumps < current_rank:
            pickup_region_ranks[system.region_id] = jumps
    pickup_region_ids = [
        region_id
        for region_id, _rank in sorted(pickup_region_ranks.items(), key=lambda item: (item[1], item[0]))
    ]
    pickup_region_truncated = len(pickup_region_ids) > MAX_FLIGHT_BUYER_SCAN_REGIONS
    scan_pickup_region_ids = pickup_region_ids[:MAX_FLIGHT_BUYER_SCAN_REGIONS]
    if destination.region_id is None:
        raise CorpMarketError("Destination system does not have a usable market region in the route graph cache.")

    item_targets, item_scope = build_haul_item_targets(
        config=config,
        recipe_cache=recipe_cache,
        include_common_materials=include_common_materials,
        market_group_ids=market_group_ids,
    )
    item_truncated = len(item_targets) > MAX_FLIGHT_ACQUISITION_SCAN_TYPES
    scan_targets = item_targets[:MAX_FLIGHT_ACQUISITION_SCAN_TYPES]
    if not scan_targets:
        raise CorpMarketError("Choose Common materials or at least one market category before planning acquisitions.")

    opportunities = []
    total_source_buy_order_count = 0
    total_source_sell_order_count = 0
    total_destination_buy_order_count = 0
    history_regions = set()
    trap_signal_count = 0
    caution_signal_count = 0
    errors = []
    destination_distances = {destination.solar_system_id: 0}

    for target in scan_targets:
        type_id = int(target["type_id"])
        source_buy_orders = []
        source_sell_orders = []
        for region_id in scan_pickup_region_ids:
            try:
                raw_source_buys = fetch_market_buy_orders(config, region_id=region_id, type_id=type_id)
            except CorpMarketError as exc:
                errors.append({"order_type": "buy", "type_id": type_id, "region_id": region_id, "error": str(exc)})
                raw_source_buys = []
            for order in raw_source_buys:
                record = build_reachable_market_order_record(
                    order,
                    systems=systems,
                    jump_distances=pickup_distances,
                    region_id=region_id,
                    order_type="buy",
                )
                if record is not None:
                    source_buy_orders.append(record)
            try:
                raw_source_sells = fetch_market_sell_orders(config, region_id=region_id, type_id=type_id)
            except CorpMarketError as exc:
                errors.append({"order_type": "sell", "type_id": type_id, "region_id": region_id, "error": str(exc)})
                raw_source_sells = []
            for order in raw_source_sells:
                record = build_reachable_market_order_record(
                    order,
                    systems=systems,
                    jump_distances=pickup_distances,
                    region_id=region_id,
                    order_type="sell",
                )
                if record is not None:
                    source_sell_orders.append(record)
        try:
            raw_destination_buys = fetch_market_buy_orders(config, region_id=destination.region_id, type_id=type_id)
        except CorpMarketError as exc:
            errors.append(
                {"order_type": "buy", "type_id": type_id, "region_id": destination.region_id, "error": str(exc)}
            )
            raw_destination_buys = []
        destination_buy_orders = []
        for order in raw_destination_buys:
            record = build_reachable_market_order_record(
                order,
                systems=systems,
                jump_distances=destination_distances,
                region_id=destination.region_id,
                order_type="buy",
            )
            if record is not None:
                destination_buy_orders.append(record)

        source_buy_orders.sort(key=lambda item: market_order_sort_key(item, order_type="buy"))
        source_sell_orders.sort(key=lambda item: market_order_sort_key(item, order_type="sell"))
        destination_buy_orders.sort(key=lambda item: market_order_sort_key(item, order_type="buy"))
        total_source_buy_order_count += len(source_buy_orders)
        total_source_sell_order_count += len(source_sell_orders)
        total_destination_buy_order_count += len(destination_buy_orders)
        if not destination_buy_orders:
            continue

        source_region_id = origin.region_id or (scan_pickup_region_ids[0] if scan_pickup_region_ids else None)
        if source_region_id is None:
            continue
        source_history = []
        destination_history = []
        try:
            source_history = fetch_market_history(config, region_id=source_region_id, type_id=type_id)
            history_regions.add(source_region_id)
        except CorpMarketError as exc:
            errors.append({"history": "source", "type_id": type_id, "region_id": source_region_id, "error": str(exc)})
        if destination.region_id != source_region_id:
            try:
                destination_history = fetch_market_history(config, region_id=destination.region_id, type_id=type_id)
                history_regions.add(destination.region_id)
            except CorpMarketError as exc:
                errors.append(
                    {"history": "destination", "type_id": type_id, "region_id": destination.region_id, "error": str(exc)}
                )
        else:
            destination_history = source_history

        best_destination_buy = destination_buy_orders[0]
        best_source_buy = source_buy_orders[0] if source_buy_orders else None
        best_source_sell = source_sell_orders[0] if source_sell_orders else None
        source_stats = market_history_stats(source_history)
        destination_stats = market_history_stats(destination_history)
        net_destination_price = float(best_destination_buy["price"]) * (1.0 - sales_tax_rate)
        target_margin_rate = clean_min_margin_percent / 100.0
        max_safe_bid = net_destination_price / max((1.0 + broker_fee_rate) * (1.0 + target_margin_rate), 0.0001)
        if max_safe_bid <= 0:
            continue
        suggested_bid = acquisition_suggested_bid(
            max_safe_bid=max_safe_bid,
            best_source_buy=best_source_buy,
            best_source_sell=best_source_sell,
            source_history=source_stats,
        )
        if suggested_bid <= 0:
            continue
        estimated_unit_cost = suggested_bid * (1.0 + broker_fee_rate)
        budget_units = int(clean_budget // max(estimated_unit_cost, 0.01))
        history_units = acquisition_history_unit_cap(source_stats, target_days=clean_target_days)
        destination_units = int(best_destination_buy.get("volume_remain") or 0)
        units = min(limit for limit in (budget_units, destination_units, history_units) if limit >= 0)
        if units <= 0:
            continue
        broker_fee_total = suggested_bid * units * broker_fee_rate
        bid_total = suggested_bid * units
        net_revenue = net_destination_price * units
        net_profit = net_revenue - bid_total - broker_fee_total
        if net_profit <= 0:
            continue
        margin_percent = profit_margin_percent(net_profit, bid_total + broker_fee_total)
        flags = acquisition_history_flags(
            source_stats=source_stats,
            destination_stats=destination_stats,
            best_source_buy=best_source_buy,
            best_source_sell=best_source_sell,
            best_destination_buy=best_destination_buy,
            max_safe_bid=max_safe_bid,
            suggested_bid=suggested_bid,
            units=units,
            target_days=clean_target_days,
        )
        risk_level = acquisition_risk_level(flags)
        if risk_level == "possible-trap":
            trap_signal_count += 1
        elif risk_level == "caution":
            caution_signal_count += 1
        range_recommendation = acquisition_range_recommendation(
            risk_level=risk_level,
            source_stats=source_stats,
            units=units,
            pickup_jumps=clean_pickup_jumps,
            margin_percent=margin_percent,
        )
        opportunity = {
            "type_id": type_id,
            "item_name": target["name"],
            "recipe_count": int(target.get("recipe_count") or 0),
            "source_labels": target.get("source_labels", []),
            "volume_m3": target.get("volume_m3"),
            "decision": acquisition_decision(risk_level=risk_level, margin_percent=margin_percent),
            "risk_level": risk_level,
            "range_recommendation": range_recommendation,
            "placement_system": origin.name,
            "pickup_jumps": clean_pickup_jumps,
            "target_days": clean_target_days,
            "suggested_bid": suggested_bid,
            "max_safe_bid": max_safe_bid,
            "recommended_units": units,
            "estimated_bid_total": bid_total,
            "estimated_broker_fee": broker_fee_total,
            "estimated_isk_committed": bid_total + broker_fee_total,
            "net_destination_price": net_destination_price,
            "net_profit": net_profit,
            "net_profit_per_unit": net_profit / units,
            "margin_percent": margin_percent,
            "sales_tax_rate": sales_tax_rate,
            "broker_fee_rate": broker_fee_rate,
            "best_source_buy": best_source_buy,
            "best_source_sell": best_source_sell,
            "best_destination_buy": best_destination_buy,
            "source_history": source_stats,
            "destination_history": destination_stats,
            "history_flags": flags,
        }
        opportunities.append(opportunity)

    opportunities.sort(
        key=lambda item: (
            acquisition_risk_sort_rank(str(item["risk_level"])),
            -float(item.get("net_profit") or 0.0),
            -float(item.get("margin_percent") or 0.0),
            item["item_name"],
        )
    )
    return {
        "origin_system": origin.to_dict(jumps=0),
        "destination_system": destination.to_dict(jumps=0),
        "budget_isk": clean_budget,
        "pickup_jumps": clean_pickup_jumps,
        "min_margin_percent": clean_min_margin_percent,
        "broker_fee_percent": clean_broker_fee_percent,
        "target_days": clean_target_days,
        "pickup_system_count": len(pickup_distances),
        "pickup_regions_scanned": len(scan_pickup_region_ids),
        "pickup_regions_total": len(pickup_region_ids),
        "pickup_region_truncated": pickup_region_truncated,
        "destination_region_id": destination.region_id,
        "scanned_item_types": len(scan_targets),
        "total_item_types": len(item_targets),
        "item_truncated": item_truncated,
        "item_scope": {
            **item_scope,
            "scanned_item_types": len(scan_targets),
            "total_item_types": len(item_targets),
            "item_truncated": item_truncated,
        },
        "source_buy_order_count": total_source_buy_order_count,
        "source_sell_order_count": total_source_sell_order_count,
        "destination_buy_order_count": total_destination_buy_order_count,
        "history_region_count": len(history_regions),
        "opportunity_count": len(opportunities),
        "possible_trap_count": trap_signal_count,
        "caution_count": caution_signal_count,
        "opportunities": opportunities[:MAX_FLIGHT_ACQUISITION_OPPORTUNITIES],
        "errors": errors[:12],
        "sales_tax": sales_tax,
        "market_cache": market_order_cache_status(),
        "history_cache": market_history_cache_status(),
        "pricing_note": (
            "This planner suggests public buy-order ceilings from public regional orders and market history. "
            "A possible trap flag means recent history does not support the apparent spread or fill volume; "
            "verify the item in EVE before posting an order. The page does not place orders."
        ),
    }


def market_history_stats(history: Iterable[dict[str, Any]], *, days: int = 30) -> dict[str, Any]:
    rows = sorted((item for item in history if isinstance(item, dict)), key=lambda item: str(item.get("date") or ""))
    recent = rows[-max(1, int(days)) :]
    volumes = [float(item.get("volume") or 0.0) for item in recent]
    order_counts = [float(item.get("order_count") or 0.0) for item in recent]
    averages = [float(item.get("average") or 0.0) for item in recent if clean_optional_float(item.get("average"))]
    lows = [float(item.get("lowest") or 0.0) for item in recent if clean_optional_float(item.get("lowest"))]
    highs = [float(item.get("highest") or 0.0) for item in recent if clean_optional_float(item.get("highest"))]
    latest = recent[-1] if recent else {}
    return {
        "days": len(recent),
        "latest_date": str(latest.get("date") or ""),
        "latest_volume": clean_optional_int(latest.get("volume")) or 0,
        "latest_order_count": clean_optional_int(latest.get("order_count")) or 0,
        "latest_average": clean_optional_float(latest.get("average")),
        "avg_daily_volume": (sum(volumes) / len(volumes)) if volumes else 0.0,
        "avg_daily_order_count": (sum(order_counts) / len(order_counts)) if order_counts else 0.0,
        "median_average": median_number(averages),
        "median_low": median_number(lows),
        "median_high": median_number(highs),
    }


def median_number(values: Iterable[float]) -> float | None:
    clean_values = sorted(float(value) for value in values if value is not None and float(value) > 0)
    count = len(clean_values)
    if not count:
        return None
    middle = count // 2
    if count % 2:
        return clean_values[middle]
    return (clean_values[middle - 1] + clean_values[middle]) / 2.0


def acquisition_suggested_bid(
    *,
    max_safe_bid: float,
    best_source_buy: dict[str, Any] | None,
    best_source_sell: dict[str, Any] | None,
    source_history: dict[str, Any],
) -> float:
    candidates = []
    if best_source_buy is not None:
        candidates.append(float(best_source_buy.get("price") or 0.0) * 1.01)
    median_average = clean_optional_float(source_history.get("median_average"))
    if median_average:
        candidates.append(median_average * 0.85)
    if best_source_sell is not None:
        candidates.append(float(best_source_sell.get("price") or 0.0) * 0.85)
    if not candidates:
        candidates.append(max_safe_bid * 0.75)
    positive_candidates = [value for value in candidates if value > 0]
    if not positive_candidates:
        return 0.0
    return min(max_safe_bid, max(positive_candidates))


def acquisition_history_unit_cap(source_stats: dict[str, Any], *, target_days: int) -> int:
    avg_daily_volume = clean_optional_float(source_stats.get("avg_daily_volume")) or 0.0
    if avg_daily_volume <= 0:
        return 1
    return max(1, int(avg_daily_volume * clamp_acquisition_target_days(target_days)))


def acquisition_history_flags(
    *,
    source_stats: dict[str, Any],
    destination_stats: dict[str, Any],
    best_source_buy: dict[str, Any] | None,
    best_source_sell: dict[str, Any] | None,
    best_destination_buy: dict[str, Any],
    max_safe_bid: float,
    suggested_bid: float,
    units: int,
    target_days: int,
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    source_days = int(source_stats.get("days") or 0)
    destination_days = int(destination_stats.get("days") or 0)
    if source_days < 7:
        flags.append(
            {
                "severity": "caution",
                "label": "Limited source history",
                "detail": "This item has less than a week of source-region history, so fill speed is uncertain.",
            }
        )
    source_daily_volume = clean_optional_float(source_stats.get("avg_daily_volume")) or 0.0
    if source_daily_volume > 0 and units > source_daily_volume * max(1, int(target_days)):
        flags.append(
            {
                "severity": "caution",
                "label": "Thin fill volume",
                "detail": "Recommended units are above the recent source-region volume window.",
            }
        )
    source_daily_orders = clean_optional_float(source_stats.get("avg_daily_order_count")) or 0.0
    if source_days and source_daily_orders < 1.5:
        flags.append(
            {
                "severity": "caution",
                "label": "Sparse source orders",
                "detail": "Recent history shows few daily market orders, so a buy order may sit for a long time.",
            }
        )
    destination_median = clean_optional_float(destination_stats.get("median_average"))
    destination_price = float(best_destination_buy.get("price") or 0.0)
    if destination_days >= 7 and destination_median and destination_price > destination_median * 1.75:
        flags.append(
            {
                "severity": "trap",
                "label": "Possible trap: price spike",
                "detail": "The best destination buy order is far above the recent market-history average; verify this is real demand before posting.",
            }
        )
    if best_source_buy is not None and float(best_source_buy.get("price") or 0.0) >= max_safe_bid:
        flags.append(
            {
                "severity": "trap",
                "label": "Possible trap: competition too high",
                "detail": "The current competing buy order is already at or above the safe bid ceiling.",
            }
        )
    if best_source_sell is not None and suggested_bid >= float(best_source_sell.get("price") or 0.0):
        flags.append(
            {
                "severity": "caution",
                "label": "Near instant-buy price",
                "detail": "The suggested bid is close to the lowest sell order; a buy order may not add much advantage.",
            }
        )
    if int(best_destination_buy.get("volume_remain") or 0) <= max(1, units // 3):
        flags.append(
            {
                "severity": "caution",
                "label": "Low destination depth",
                "detail": "The downstream buy order has limited remaining volume; recheck it before committing ISK.",
            }
        )
    if not flags:
        flags.append(
            {
                "severity": "clear",
                "label": "History supports a cautious test",
                "detail": "Recent history and current orders do not show an obvious trap signal.",
            }
        )
    return flags


def acquisition_risk_level(flags: Iterable[dict[str, str]]) -> str:
    severities = {str(flag.get("severity") or "") for flag in flags}
    if "trap" in severities:
        return "possible-trap"
    if "caution" in severities:
        return "caution"
    return "clear"


def acquisition_risk_sort_rank(risk_level: str) -> int:
    return {"clear": 0, "caution": 1, "possible-trap": 2}.get(risk_level, 3)


def acquisition_decision(*, risk_level: str, margin_percent: float | None) -> dict[str, str]:
    if risk_level == "possible-trap":
        return {
            "code": "verify",
            "tone": "price",
            "label": "Verify before posting",
            "reason": "Market history shows a possible trap signal. Treat this as a lead, not a trade.",
        }
    if margin_percent is not None and margin_percent >= 25:
        return {
            "code": "place-small",
            "tone": "build",
            "label": "Place a small order",
            "reason": "The spread survives fees and the history does not show a major warning.",
        }
    if margin_percent is not None and margin_percent > 0:
        return {
            "code": "test",
            "tone": "source",
            "label": "Test with low volume",
            "reason": "The margin is positive, but keep the first order small until fills prove demand.",
        }
    return {
        "code": "watch",
        "tone": "watch",
        "label": "Watch only",
        "reason": "The current spread is not strong enough after estimated fees.",
    }


def acquisition_range_recommendation(
    *,
    risk_level: str,
    source_stats: dict[str, Any],
    units: int,
    pickup_jumps: int,
    margin_percent: float | None,
) -> dict[str, str]:
    avg_daily_volume = clean_optional_float(source_stats.get("avg_daily_volume")) or 0.0
    if risk_level == "possible-trap" or pickup_jumps <= 0:
        return {
            "range": "station",
            "reason": "Keep the order narrow until the history signal is verified.",
        }
    if avg_daily_volume >= max(units * 2, 10) and pickup_jumps >= 5 and (margin_percent or 0.0) >= 20:
        return {
            "range": "5 jumps",
            "reason": "History shows enough volume to justify a wider collection range, but plan for scattered deliveries.",
        }
    if avg_daily_volume >= max(units, 5) and pickup_jumps >= 1:
        return {
            "range": "solar system",
            "reason": "Use a system range first so fills stay easy to collect.",
        }
    return {
        "range": "station",
        "reason": "Thin history favors a narrow order that is easy to monitor.",
    }


def emit_haul_route_progress(
    *,
    progress: Callable[[str, dict[str, Any]], None] | None,
    route_path: list[int],
    pickup_detours: dict[int, int],
    systems: dict[int, RouteSystem],
    adjacency: dict[int, tuple[int, ...]],
    detour_jumps: int,
    destination_system_id: int,
    pickup_jump_distances: dict[int, int],
    scanned_materials: int,
    min_detour_margin_percent: float,
) -> None:
    if progress is None:
        return
    route_systems = [systems[system_id] for system_id in route_path if system_id in systems]
    progress(
        "scan_start",
        {
            "route_stop_count": len(route_systems),
            "route_jumps": max(0, len(route_path) - 1),
            "detour_jumps": detour_jumps,
            "pickup_system_count": len(pickup_jump_distances),
            "scanned_materials": scanned_materials,
            "min_detour_margin_percent": min_detour_margin_percent,
            "message": (
                f"Walking {len(route_systems)} route stops and {len(pickup_jump_distances)} pickup systems "
                f"before pricing {scanned_materials} item types."
            ),
        },
    )
    route_stop_count = len(route_systems)
    for route_index, route_system in enumerate(route_systems, start=1):
        nearby_distances = jump_distances_within(
            start_system_id=route_system.solar_system_id,
            max_jumps=detour_jumps,
            adjacency=adjacency,
        )
        nearby = []
        for system_id, jumps_from_route in sorted(
            nearby_distances.items(),
            key=lambda item: (item[1], systems.get(item[0], route_system).name),
        ):
            if system_id == destination_system_id:
                continue
            system = systems.get(system_id)
            if system is None or system_id not in pickup_detours:
                continue
            nearby.append(
                {
                    **system.to_dict(jumps=pickup_jump_distances.get(system_id, 0)),
                    "jumps_from_route": jumps_from_route,
                }
            )
        progress(
            "route_step",
            {
                "route_index": route_index,
                "route_stop_count": route_stop_count,
                "system": route_system.to_dict(jumps=route_index - 1),
                "nearby_system_count": len(nearby),
                "nearby_systems": nearby[:12],
                "nearby_truncated": len(nearby) > 12,
                "message": f"Scanning route stop {route_index}/{route_stop_count}: {route_system.name}",
            },
        )
        for nearby_system in nearby[:12]:
            if int(nearby_system.get("solar_system_id") or 0) == route_system.solar_system_id:
                continue
            progress(
                "nearby_system",
                {
                    "route_index": route_index,
                    "route_stop_count": route_stop_count,
                    "route_system_name": route_system.name,
                    "system": nearby_system,
                    "message": (
                        f"Checking nearby {nearby_system['name']} "
                        f"({nearby_system['jumps_from_route']}j from {route_system.name})"
                    ),
                },
            )


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


def resolve_jita_market_system(systems: dict[int, RouteSystem]) -> RouteSystem:
    for system in systems.values():
        if normalize_system_name(system.name) == normalize_system_name(JITA_SYSTEM_NAME) and system.region_id is not None:
            return system
    return RouteSystem(
        solar_system_id=JITA_SOLAR_SYSTEM_ID,
        name=JITA_SYSTEM_NAME,
        region_id=JITA_REGION_ID,
        security_status=0.9,
    )


def scan_system_market_orders(
    *,
    config: EveSsoConfig,
    type_ids: Iterable[int],
    system: RouteSystem,
    order_type: str,
) -> tuple[dict[int, list[dict[str, Any]]], int, list[dict[str, Any]]]:
    if system.region_id is None:
        return {}, 0, [{"order_type": order_type, "system_id": system.solar_system_id, "error": "System region is unknown."}]
    orders_by_type: dict[int, list[dict[str, Any]]] = {}
    total_order_count = 0
    errors = []
    fetcher = fetch_market_buy_orders if order_type == "buy" else fetch_market_sell_orders
    market_systems = {system.solar_system_id: system}
    jump_distances = {system.solar_system_id: 0}
    for type_id in [int(type_id) for type_id in type_ids if int(type_id) > 0]:
        system_orders = []
        try:
            raw_orders = fetcher(config, region_id=system.region_id, type_id=type_id)
        except CorpMarketError as exc:
            errors.append({"order_type": order_type, "type_id": type_id, "region_id": system.region_id, "error": str(exc)})
            continue
        for order in raw_orders:
            record = build_reachable_market_order_record(
                order,
                systems=market_systems,
                jump_distances=jump_distances,
                region_id=system.region_id,
                order_type=order_type,
            )
            if record is not None:
                system_orders.append(record)
        system_orders.sort(key=lambda item: market_order_sort_key(item, order_type=order_type))
        total_order_count += len(system_orders)
        if system_orders:
            orders_by_type[type_id] = system_orders
    return orders_by_type, total_order_count, errors


def liquidation_value_from_orders(orders: Iterable[dict[str, Any]], *, quantity: int) -> dict[str, Any]:
    required_quantity = max(0, int(quantity))
    remaining = required_quantity
    value = 0.0
    priced_quantity = 0
    used_orders = 0
    for order in orders:
        if remaining <= 0:
            break
        try:
            price = float(order.get("price") or 0.0)
            volume = int(order.get("volume_remain") or 0)
            min_volume = int(order.get("min_volume") or 1)
        except (TypeError, ValueError):
            continue
        if price <= 0 or volume <= 0 or required_quantity < max(1, min_volume):
            continue
        filled = min(remaining, volume)
        value += filled * price
        priced_quantity += filled
        remaining -= filled
        used_orders += 1
    return {
        "value": round(value, 4),
        "priced_quantity": priced_quantity,
        "required_quantity": required_quantity,
        "complete": required_quantity == 0 or priced_quantity >= required_quantity,
        "order_count": used_orders,
    }


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


def adjusted_job_time_seconds(base_seconds: int, time_efficiency: int, runs: int = 1) -> int:
    clean_base = max(0, int(base_seconds))
    clean_runs = max(1, int(runs))
    if clean_base <= 0:
        return 0
    efficiency = clean_blueprint_efficiency(time_efficiency)
    numerator = clean_base * clean_runs * (100 - efficiency)
    return max(1, -(-numerator // 100))


def isk_per_hour(profit: float | None, job_time_seconds: int) -> float | None:
    if profit is None or job_time_seconds <= 0:
        return None
    return round((float(profit) / float(job_time_seconds)) * 3600.0, 4)


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
                "manufacturing_time_seconds": recipe.manufacturing_time_seconds,
                "max_production_limit": recipe.max_production_limit,
                "required_skills": [skill.to_dict() for skill in recipe.skills],
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
                    "manufacturing_time_seconds": recipe.manufacturing_time_seconds,
                    "max_production_limit": recipe.max_production_limit,
                    "required_skills": [skill.to_dict() for skill in recipe.skills],
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


def build_haul_item_targets(
    *,
    config: EveSsoConfig,
    recipe_cache: IndustryRecipeCache,
    include_common_materials: bool,
    market_group_ids: Iterable[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_market_group_ids = clean_haul_market_group_ids(market_group_ids)
    common_targets = []
    if include_common_materials:
        common_targets = industry_material_trade_targets(recipe_cache.recipes or {})[:MAX_FLIGHT_HAUL_MATERIAL_TYPES]
        for target in common_targets:
            target["source"] = "common_material"
            target["source_label"] = "Common materials"
    market_group_targets: list[dict[str, Any]] = []
    market_group_meta: dict[str, Any] = {
        "source": "",
        "selected_market_groups": [],
        "selected_market_group_ids": list(selected_market_group_ids),
        "selected_market_group_count": len(selected_market_group_ids),
        "market_group_item_types": 0,
    }
    if selected_market_group_ids:
        market_group_targets, market_group_meta = build_market_group_targets(config, selected_market_group_ids)

    targets_by_type_id: dict[int, dict[str, Any]] = {}
    for target in common_targets:
        type_id = int(target["type_id"])
        targets_by_type_id[type_id] = {
            **target,
            "recipe_count": int(target.get("recipe_count") or 0),
            "source_rank": 0,
            "source_labels": ["Common materials"],
        }
    for target in market_group_targets:
        type_id = int(target["type_id"])
        existing = targets_by_type_id.get(type_id)
        if existing is None:
            targets_by_type_id[type_id] = {
                **target,
                "recipe_count": int(target.get("recipe_count") or 0),
                "source_rank": 1,
                "source_labels": [str(target.get("source_label") or "Market category")],
            }
            continue
        source_label = str(target.get("source_label") or "Market category")
        if source_label not in existing["source_labels"]:
            existing["source_labels"].append(source_label)
        if not existing.get("volume_m3") and target.get("volume_m3"):
            existing["volume_m3"] = target.get("volume_m3")
        existing["market_group_id"] = target.get("market_group_id")
        existing["market_group_name"] = target.get("market_group_name")

    targets = sorted(
        targets_by_type_id.values(),
        key=lambda item: (
            int(item["source_rank"]) if item.get("source_rank") is not None else 9,
            str(item.get("name") or ""),
            int(item.get("type_id") or 0),
        ),
    )
    return targets, {
        "include_common_materials": bool(include_common_materials),
        "common_material_scan_limit": MAX_FLIGHT_HAUL_MATERIAL_TYPES,
        "common_material_item_types": len(common_targets),
        "selected_market_group_ids": market_group_meta.get("selected_market_group_ids", list(selected_market_group_ids)),
        "selected_market_groups": market_group_meta.get("selected_market_groups", []),
        "selected_market_group_count": int(market_group_meta.get("selected_market_group_count") or 0),
        "market_group_source": market_group_meta.get("source") or "",
        "market_group_item_types": int(market_group_meta.get("market_group_item_types") or 0),
        "total_item_types": len(targets),
        "scan_type_limit": MAX_FLIGHT_HAUL_SCAN_TYPES,
    }


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


def build_haul_route_plan(
    *,
    config: EveSsoConfig,
    origin_solar_system_id: int,
    destination_solar_system_id: int,
    route_preference: str,
    avoid_recent_pod_kills: bool,
    adjacency: dict[int, tuple[int, ...]],
) -> dict[str, Any]:
    clean_preference = normalize_haul_route_preference(route_preference)
    recent_pod_kill_system_ids: tuple[int, ...] = ()
    warning = ""
    if avoid_recent_pod_kills:
        try:
            recent_pod_kill_system_ids = fetch_recent_pod_kill_system_ids(config)
        except CorpMarketError as exc:
            warning = str(exc)
    recent_pod_kill_set = set(recent_pod_kill_system_ids)
    avoided_system_ids: set[int] = set()
    route_path: list[int] = []
    source = "esi-route"
    route_error = ""
    for _attempt in range(4):
        try:
            candidate_path = fetch_esi_route_path(
                config,
                origin_solar_system_id=origin_solar_system_id,
                destination_solar_system_id=destination_solar_system_id,
                route_preference=clean_preference,
                avoid_system_ids=avoided_system_ids,
            )
        except CorpMarketError as exc:
            route_error = str(exc)
            break
        route_path = candidate_path
        if not avoid_recent_pod_kills or not recent_pod_kill_set:
            break
        route_pod_kill_ids = [
            system_id
            for system_id in route_path
            if system_id in recent_pod_kill_set
            and system_id not in {origin_solar_system_id, destination_solar_system_id}
        ]
        new_avoids = [system_id for system_id in route_pod_kill_ids if system_id not in avoided_system_ids]
        if not new_avoids:
            break
        remaining_slots = MAX_HAUL_ROUTE_AVOID_SYSTEMS - len(avoided_system_ids)
        if remaining_slots <= 0:
            warning = warning or f"Route avoid list reached {MAX_HAUL_ROUTE_AVOID_SYSTEMS} systems."
            break
        avoided_system_ids.update(new_avoids[:remaining_slots])
        if len(new_avoids) > remaining_slots:
            warning = warning or f"Route avoid list reached {MAX_HAUL_ROUTE_AVOID_SYSTEMS} systems."
            break
    if not route_path:
        route_path = shortest_route_path(
            start_system_id=origin_solar_system_id,
            destination_system_id=destination_solar_system_id,
            adjacency=adjacency,
        )
        source = "local-shortest-fallback"
        warning = warning or route_error or "ESI route planner was unavailable; using local shortest route."
    route_pod_kill_ids = [
        system_id
        for system_id in route_path
        if system_id in recent_pod_kill_set and system_id not in {origin_solar_system_id, destination_solar_system_id}
    ]
    if avoid_recent_pod_kills and route_pod_kill_ids and not warning:
        warning = "Some recent pod-kill systems could not be avoided without losing the route."
    return {
        "path": route_path,
        "source": source,
        "preference": clean_preference,
        "preference_label": HAUL_ROUTE_ESI_PREFERENCES[clean_preference],
        "avoid_recent_pod_kills": bool(avoid_recent_pod_kills),
        "recent_pod_kill_system_count": len(recent_pod_kill_system_ids),
        "avoided_pod_kill_system_ids": sorted(avoided_system_ids),
        "route_pod_kill_system_ids": route_pod_kill_ids,
        "warning": warning,
    }


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


def clamp_haul_min_detour_margin_percent(value: Any) -> float:
    try:
        margin = float(value)
    except (TypeError, ValueError):
        margin = DEFAULT_HAUL_MIN_DETOUR_MARGIN_PERCENT
    return max(0.0, min(MAX_HAUL_MIN_DETOUR_MARGIN_PERCENT, margin))


def clamp_acquisition_budget_isk(value: Any) -> float:
    try:
        budget = float(value)
    except (TypeError, ValueError):
        budget = DEFAULT_ACQUISITION_BUDGET_ISK
    return max(1.0, min(MAX_ACQUISITION_BUDGET_ISK, budget))


def clamp_acquisition_pickup_jumps(value: Any) -> int:
    try:
        jumps = int(value)
    except (TypeError, ValueError):
        jumps = DEFAULT_ACQUISITION_PICKUP_JUMPS
    return max(0, min(MAX_ACQUISITION_PICKUP_JUMPS, jumps))


def clamp_acquisition_broker_fee_percent(value: Any) -> float:
    try:
        broker_fee = float(value)
    except (TypeError, ValueError):
        broker_fee = DEFAULT_ACQUISITION_BROKER_FEE_PERCENT
    return max(0.0, min(MAX_ACQUISITION_BROKER_FEE_PERCENT, broker_fee))


def clamp_acquisition_target_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = DEFAULT_ACQUISITION_TARGET_DAYS
    return max(1, min(MAX_ACQUISITION_TARGET_DAYS, days))


def clamp_trade_pnl_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = DEFAULT_FLIGHT_TRADE_PNL_DAYS
    return max(1, min(MAX_FLIGHT_TRADE_PNL_DAYS, days))


def clamp_trade_pnl_window_hours(value: Any) -> int:
    try:
        hours = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        hours = DEFAULT_FLIGHT_TRADE_PNL_WINDOW_HOURS
    return max(1, min(MAX_FLIGHT_TRADE_PNL_WINDOW_HOURS, hours))


def normalize_haul_route_preference(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "shortest": "shorter",
        "short": "shorter",
        "prefer_shorter": "shorter",
        "secure": "safer",
        "safe": "safer",
        "prefer_safer": "safer",
        "lesssecure": "less_secure",
        "insecure": "less_secure",
        "prefer_less_secure": "less_secure",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in HAUL_ROUTE_PREFERENCES:
        return DEFAULT_HAUL_ROUTE_PREFERENCE
    return normalized


def query_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


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


def build_flight_planetary_payload(
    *,
    config: EveSsoConfig,
    hub_name: str = DEFAULT_PLANETARY_HUB_SYSTEM,
    output_tier: str = DEFAULT_PLANETARY_OUTPUT_TIER,
    owner_tax_percent: float = DEFAULT_PLANETARY_OWNER_TAX_PERCENT,
    npc_tax_percent: float = DEFAULT_PLANETARY_NPC_TAX_PERCENT,
    customs_code_expertise_level: int = DEFAULT_PLANETARY_CUSTOMS_CODE_EXPERTISE_LEVEL,
    sales_tax_percent: float = DEFAULT_PLANETARY_SALES_TAX_PERCENT,
    broker_fee_percent: float = DEFAULT_PLANETARY_BROKER_FEE_PERCENT,
    top: int = MAX_PLANETARY_OPPORTUNITIES,
) -> dict[str, Any]:
    cache = load_planetary_industry_cache(DEFAULT_PLANETARY_CACHE_PATH)
    if not cache.available:
        raise CorpMarketError(cache.error or "Planetary industry cache is not available.")
    route_cache = load_route_graph_cache()
    hub_system = resolve_planetary_market_hub(route_cache, hub_name)
    clean_tier = normalize_planetary_output_tier(output_tier)
    clean_top = clean_planetary_top(top)
    clean_owner_tax_percent = clamp_planetary_percent(
        owner_tax_percent,
        "owner tax percent",
        default=DEFAULT_PLANETARY_OWNER_TAX_PERCENT,
    )
    clean_npc_tax_percent = clamp_planetary_percent(
        npc_tax_percent,
        "NPC tax percent",
        default=DEFAULT_PLANETARY_NPC_TAX_PERCENT,
    )
    clean_customs_code_expertise_level = clamp_customs_code_expertise_level(customs_code_expertise_level)
    clean_sales_tax_percent = clamp_planetary_percent(
        sales_tax_percent,
        "sales tax percent",
        default=DEFAULT_PLANETARY_SALES_TAX_PERCENT,
    )
    clean_broker_fee_percent = clamp_planetary_percent(
        broker_fee_percent,
        "broker fee percent",
        default=DEFAULT_PLANETARY_BROKER_FEE_PERCENT,
    )
    schematics = [
        schematic
        for schematic in cache.schematics.values()
        if planetary_schematic_output_tier(schematic) == clean_tier
    ]
    if not schematics:
        raise CorpMarketError(f"No {clean_tier} planetary schematics were found in the static cache.")

    input_type_ids = sorted({item.type_id for schematic in schematics for item in schematic.inputs})
    output_type_ids = sorted({item.type_id for schematic in schematics for item in schematic.outputs})
    input_sell_orders, input_order_count, input_errors = scan_system_market_orders(
        config=config,
        type_ids=input_type_ids,
        system=hub_system,
        order_type="sell",
    )
    output_buy_orders, output_order_count, output_errors = scan_system_market_orders(
        config=config,
        type_ids=output_type_ids,
        system=hub_system,
        order_type="buy",
    )
    price_map = build_planetary_price_map(
        input_sell_orders=input_sell_orders,
        output_buy_orders=output_buy_orders,
    )
    tax_profile = PlanetaryTaxProfile(
        owner_export_tax_rate=clean_owner_tax_percent / 100.0,
        npc_export_tax_rate=clean_npc_tax_percent / 100.0,
        customs_code_expertise_level=clean_customs_code_expertise_level,
        sales_tax_rate=clean_sales_tax_percent / 100.0,
        broker_fee_rate=clean_broker_fee_percent / 100.0,
    )
    try:
        opportunities = rank_planetary_opportunities(
            cache,
            price_map,
            tax_profile=tax_profile,
            output_tiers=(clean_tier,),
            top=clean_top,
        )
    except PlanetaryIndustryError as exc:
        raise CorpMarketError(str(exc)) from exc
    return {
        "ok": True,
        "generated_at": now_iso(),
        "hub": hub_system.to_dict(jumps=0),
        "settings": {
            "hub_name": hub_system.name,
            "requested_hub_name": hub_name,
            "output_tier": clean_tier,
            "top": clean_top,
            "owner_tax_percent": clean_owner_tax_percent,
            "npc_tax_percent": clean_npc_tax_percent,
            "customs_code_expertise_level": clean_customs_code_expertise_level,
            "sales_tax_percent": clean_sales_tax_percent,
            "broker_fee_percent": clean_broker_fee_percent,
        },
        "tax_profile": planetary_tax_profile_to_dict(tax_profile),
        "planetary": {
            "cache": planetary_cache_public_dict(cache),
            "schematic_count": len(schematics),
            "input_type_count": len(input_type_ids),
            "output_type_count": len(output_type_ids),
            "priced_input_type_count": sum(
                1 for type_id in input_type_ids if price_map.get(type_id) and price_map[type_id].sell_price is not None
            ),
            "priced_output_type_count": sum(
                1 for type_id in output_type_ids if price_map.get(type_id) and price_map[type_id].buy_price is not None
            ),
            "input_sell_order_count": input_order_count,
            "output_buy_order_count": output_order_count,
            "opportunity_count": len(opportunities),
            "opportunities": [planetary_opportunity_to_dict(opportunity) for opportunity in opportunities],
            "errors": (input_errors + output_errors)[:10],
            "pricing_note": (
                "Uses hub public sell orders for imported inputs and hub public buy orders for outputs. "
                "Customs transfer cost from import and export is subtracted before profit."
            ),
        },
        "market_cache": market_order_cache_status(),
        "notes": [
            "Planetary planning is advisory only; no colony, market, customs office, or EVE client action is performed.",
            "Taxes are manual assumptions because customs office owner rates and colony routing are not exposed here.",
        ],
    }


def resolve_planetary_market_hub(route_cache: RouteGraphCache, hub_name: str) -> RouteSystem:
    requested = str(hub_name or "").strip() or DEFAULT_PLANETARY_HUB_SYSTEM
    system = resolve_route_system(route_cache, requested) if route_cache.available else None
    if system is not None:
        return system
    if normalize_system_name(requested) == normalize_system_name(JITA_SYSTEM_NAME):
        return resolve_jita_market_system(route_cache.systems or {})
    if not route_cache.available:
        raise CorpMarketError("Route graph cache is required to use a non-Jita planetary hub.")
    raise CorpMarketError(f"Planetary hub system {requested!r} was not found in the route graph cache.")


def planetary_schematic_output_tier(schematic: Any) -> str:
    tiers = {str(item.tier or "").strip().upper() for item in getattr(schematic, "outputs", ()) if getattr(item, "tier", "")}
    for tier in ("P4", "P3", "P2", "P1"):
        if tier in tiers:
            return tier
    return "UNKNOWN"


def build_planetary_price_map(
    *,
    input_sell_orders: dict[int, list[dict[str, Any]]],
    output_buy_orders: dict[int, list[dict[str, Any]]],
) -> dict[int, PlanetaryMarketPrice]:
    prices: dict[int, PlanetaryMarketPrice] = {}
    for type_id, orders in input_sell_orders.items():
        price = first_market_order_price(orders)
        if price is None:
            continue
        existing = prices.get(type_id)
        prices[type_id] = PlanetaryMarketPrice(
            type_id=type_id,
            buy_price=existing.buy_price if existing else None,
            sell_price=price,
        )
    for type_id, orders in output_buy_orders.items():
        price = first_market_order_price(orders)
        if price is None:
            continue
        existing = prices.get(type_id)
        prices[type_id] = PlanetaryMarketPrice(
            type_id=type_id,
            buy_price=price,
            sell_price=existing.sell_price if existing else None,
        )
    return prices


def first_market_order_price(orders: Iterable[dict[str, Any]]) -> float | None:
    for order in orders:
        price = clean_optional_float(order.get("price") if isinstance(order, dict) else None)
        if price is not None and price > 0:
            return price
    return None


def planetary_tax_profile_to_dict(tax_profile: PlanetaryTaxProfile) -> dict[str, Any]:
    return {
        "owner_export_tax_rate": tax_profile.owner_export_tax_rate,
        "owner_export_tax_percent": tax_profile.owner_export_tax_rate * 100.0,
        "npc_export_tax_rate": tax_profile.npc_export_tax_rate,
        "npc_export_tax_percent": tax_profile.npc_export_tax_rate * 100.0,
        "effective_npc_export_tax_rate": tax_profile.effective_npc_export_tax_rate,
        "effective_npc_export_tax_percent": tax_profile.effective_npc_export_tax_rate * 100.0,
        "effective_export_tax_rate": tax_profile.effective_export_tax_rate,
        "effective_export_tax_percent": tax_profile.effective_export_tax_rate * 100.0,
        "effective_import_tax_rate": tax_profile.effective_import_tax_rate,
        "effective_import_tax_percent": tax_profile.effective_import_tax_rate * 100.0,
        "customs_code_expertise_level": tax_profile.customs_code_expertise_level,
        "sales_tax_rate": tax_profile.sales_tax_rate,
        "sales_tax_percent": tax_profile.sales_tax_rate * 100.0,
        "broker_fee_rate": tax_profile.broker_fee_rate,
        "broker_fee_percent": tax_profile.broker_fee_rate * 100.0,
    }


def planetary_cache_public_dict(cache: Any) -> dict[str, Any]:
    return {
        "available": bool(getattr(cache, "available", False)),
        "path": str(getattr(cache, "path", "")),
        "build_number": getattr(cache, "build_number", None),
        "release_date": getattr(cache, "release_date", ""),
        "schematic_count": len(getattr(cache, "schematics", {}) or {}),
        "commodity_count": len(getattr(cache, "commodities", {}) or {}),
        "error": getattr(cache, "error", ""),
    }


def planetary_item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "type_id": item.type_id,
        "name": item.name,
        "tier": item.tier,
        "quantity": item.quantity,
        "volume_m3": item.volume_m3,
        "total_volume_m3": item.total_volume_m3,
        "export_tax_base_per_unit": item.export_tax_base_per_unit,
        "import_tax_base_per_unit": item.import_tax_base_per_unit,
    }


def planetary_opportunity_to_dict(opportunity: Any) -> dict[str, Any]:
    break_even_rate = opportunity.break_even_export_tax_rate
    return {
        "schematic_id": opportunity.schematic_id,
        "schematic_name": opportunity.schematic_name,
        "output_name": opportunity.output_name,
        "output_tier": opportunity.output_tier,
        "cycle_time_seconds": opportunity.cycle_time_seconds,
        "input_value": opportunity.input_value,
        "output_value": opportunity.output_value,
        "import_customs_cost": opportunity.import_customs_cost,
        "export_customs_cost": opportunity.export_customs_cost,
        "customs_transfer_cost": opportunity.customs_transfer_cost,
        "sales_tax": opportunity.sales_tax,
        "broker_fee": opportunity.broker_fee,
        "net_profit": opportunity.net_profit,
        "profit_per_hour": opportunity.profit_per_hour,
        "profit_per_day": opportunity.profit_per_day,
        "break_even_export_tax_rate": break_even_rate,
        "break_even_export_tax_percent": break_even_rate * 100.0 if break_even_rate is not None else None,
        "price_complete": opportunity.price_complete,
        "missing_price_type_ids": list(opportunity.missing_price_type_ids),
        "profitable": opportunity.profitable,
        "inputs": [planetary_item_to_dict(item) for item in opportunity.inputs],
        "outputs": [planetary_item_to_dict(item) for item in opportunity.outputs],
    }


def normalize_planetary_output_tier(value: Any) -> str:
    tier = str(value or "").strip().upper()
    if tier not in PLANETARY_OUTPUT_TIERS:
        return DEFAULT_PLANETARY_OUTPUT_TIER
    return tier


def clamp_planetary_percent(value: Any, field: str, *, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        percent = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise CorpMarketError(f"{field} must be a number.") from exc
    if percent < 0:
        raise CorpMarketError(f"{field} cannot be negative.")
    return min(percent, MAX_PLANETARY_TAX_PERCENT)


def clamp_customs_code_expertise_level(value: Any) -> int:
    if value is None or str(value).strip() == "":
        return DEFAULT_PLANETARY_CUSTOMS_CODE_EXPERTISE_LEVEL
    try:
        level = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise CorpMarketError("customs_code_expertise_level must be a whole number.") from exc
    return max(0, min(5, level))


def clean_planetary_top(value: Any) -> int:
    try:
        top = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return MAX_PLANETARY_OPPORTUNITIES
    return max(1, min(MAX_PLANETARY_OPPORTUNITIES, top))


def build_flight_reprocessing_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession,
    ore_type_id: int,
    quantity: int,
    reprocessing_station_id: int | None = None,
    facility_yield_percent: float | None = None,
    station_tax_percent: float | None = None,
    implant_bonus_percent: float | None = None,
    structure_bonus_percent: float = 0.0,
) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_LOCATION_SCOPE, FLIGHT_SKILLS_SCOPE, FLIGHT_STANDINGS_SCOPE))
    cache = load_reprocessing_cache()
    if not cache.available or not cache.ores:
        raise CorpMarketError(cache.error or "Reprocessing cache is not available.")
    ore = cache.ores.get(int(ore_type_id))
    if ore is None:
        raise CorpMarketError("Choose an ore type from the reprocessing cache.")

    clean_quantity = clean_reprocessing_quantity(quantity)
    skills = fetch_flight_skills(config, session)
    standings = fetch_flight_standings(config, session)
    location = fetch_flight_location(config, session)
    implant_profile = build_reprocessing_implant_profile(
        config,
        session,
        implant_bonus_percent=implant_bonus_percent,
    )
    location_profile = build_reprocessing_location_profile(
        config,
        session,
        cache=cache,
        location=location,
        standings=standings,
        selected_station_id=reprocessing_station_id,
        facility_yield_percent=facility_yield_percent,
        station_tax_percent=station_tax_percent,
    )
    skill_profile = build_reprocessing_skill_profile(skills, ore)
    clean_structure_bonus_percent = clamp_reprocessing_percent(
        structure_bonus_percent,
        "structure bonus percent",
        maximum=MAX_REPROCESSING_BONUS_PERCENT,
    )

    yield_rates = build_reprocessing_yield_rates(
        facility_yield_percent=location_profile["facility_yield_percent"],
        station_tax_percent=location_profile["station_tax_percent"],
        skill_profile=skill_profile,
        implant_profile=implant_profile,
        structure_bonus_percent=clean_structure_bonus_percent,
    )
    raw_yield_rate = yield_rates["raw_yield_rate"]
    gross_yield_rate = yield_rates["gross_yield_rate"]
    tax_rate = yield_rates["station_tax_rate"]
    net_yield_rate = yield_rates["net_yield_rate"]
    portions = clean_quantity // ore.portion_size
    leftover_units = clean_quantity % ore.portion_size
    output_materials = []
    for material in ore.materials:
        base_quantity = material.quantity * portions
        gross_quantity = int(base_quantity * gross_yield_rate)
        net_quantity = int(base_quantity * net_yield_rate)
        output_materials.append(
            {
                "type_id": material.type_id,
                "name": material.name,
                "base_quantity": base_quantity,
                "gross_quantity": gross_quantity,
                "station_tax_quantity": max(0, gross_quantity - net_quantity),
                "net_quantity": net_quantity,
                "volume_m3": material.volume_m3,
            }
        )
    jita_valuation = build_reprocessing_jita_valuation(
        config=config,
        ore=ore,
        input_quantity=clean_quantity,
        output_materials=output_materials,
    )

    notes = [
        "Uses ESI location, skills, standings, and implants when the connected session has those scopes.",
        "Uses CCP static data for ore portions, material outputs, and NPC station reprocessing values.",
        "Jita values use public Jita buy orders for immediate liquidation estimates.",
    ]
    notes.extend(location_profile.get("notes", []))
    notes.extend(implant_profile.get("notes", []))
    if raw_yield_rate > 1.0:
        notes.append("Calculated yield exceeded 100%, so output was capped at 100%.")
    if leftover_units:
        notes.append(f"{leftover_units:,} ore unit(s) are below the full reprocessing portion and stay unprocessed.")

    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "location": location,
        "cache": cache.to_public_dict(),
        "ore": {
            "type_id": ore.type_id,
            "name": ore.name,
            "group_id": ore.group_id,
            "group_name": ore.group_name,
            "portion_size": ore.portion_size,
            "volume_m3": ore.volume_m3,
            "specialization_skill_type_id": ore.specialization_skill_type_id,
            "specialization_skill_name": ore.specialization_skill_name,
        },
        "input": {
            "quantity": clean_quantity,
            "portion_size": ore.portion_size,
            "portions": portions,
            "leftover_units": leftover_units,
        },
        "skills": skill_profile,
        "implant": implant_profile,
        "facility": {
            **location_profile,
            "structure_bonus_percent": clean_structure_bonus_percent,
        },
        "yield": {
            "raw_yield_rate": raw_yield_rate,
            "gross_yield_rate": gross_yield_rate,
            "gross_yield_percent": gross_yield_rate * 100.0,
            "station_tax_rate": tax_rate,
            "station_tax_percent": tax_rate * 100.0,
            "net_yield_rate": net_yield_rate,
            "net_yield_percent": net_yield_rate * 100.0,
            "capped": raw_yield_rate > gross_yield_rate,
            "breakdown": yield_rates["breakdown"],
        },
        "materials": output_materials,
        "jita_valuation": jita_valuation,
        "notes": notes,
    }


def build_flight_reprocessing_locations_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession,
    ore_type_id: int,
    sort_mode: str = DEFAULT_REPROCESSING_STATION_SORT,
    implant_bonus_percent: float | None = None,
    structure_bonus_percent: float = 0.0,
) -> dict[str, Any]:
    require_flight_scopes(session, (FLIGHT_LOCATION_SCOPE, FLIGHT_SKILLS_SCOPE, FLIGHT_STANDINGS_SCOPE))
    cache = load_reprocessing_cache()
    if not cache.available or not cache.ores:
        raise CorpMarketError(cache.error or "Reprocessing cache is not available.")
    ore = cache.ores.get(int(ore_type_id))
    if ore is None:
        raise CorpMarketError("Choose an ore type from the reprocessing cache.")
    skills = fetch_flight_skills(config, session)
    standings = fetch_flight_standings(config, session)
    location = fetch_flight_location(config, session)
    implant_profile = build_reprocessing_implant_profile(
        config,
        session,
        implant_bonus_percent=implant_bonus_percent,
    )
    skill_profile = build_reprocessing_skill_profile(skills, ore)
    clean_structure_bonus_percent = clamp_reprocessing_percent(
        structure_bonus_percent,
        "structure bonus percent",
        maximum=MAX_REPROCESSING_BONUS_PERCENT,
    )
    clean_sort_mode = normalize_reprocessing_station_sort(sort_mode)
    current_location = build_reprocessing_location_profile(
        config,
        session,
        cache=cache,
        location=location,
        standings=standings,
        selected_station_id=None,
        facility_yield_percent=None,
        station_tax_percent=None,
    )
    station_options, total_matching = build_reprocessing_station_options(
        cache=cache,
        route_cache=load_route_graph_cache(),
        standings=standings,
        skill_profile=skill_profile,
        implant_profile=implant_profile,
        structure_bonus_percent=clean_structure_bonus_percent,
        sort_mode=clean_sort_mode,
    )
    return {
        "ok": True,
        "generated_at": now_iso(),
        "character": session.to_public_dict(),
        "ore": {
            "type_id": ore.type_id,
            "name": ore.name,
            "group_name": ore.group_name,
            "specialization_skill_name": ore.specialization_skill_name,
        },
        "implant": implant_profile,
        "skills": skill_profile,
        "structure_bonus_percent": clean_structure_bonus_percent,
        "current_location": current_location,
        "stations": station_options,
        "station_count": len(station_options),
        "total_matching_stations": total_matching,
        "truncated": total_matching > len(station_options),
        "limit": MAX_REPROCESSING_STATION_OPTIONS,
        "minimum_standing": MIN_REPROCESSING_STATION_STANDING,
        "sort_mode": clean_sort_mode,
        "sort_label": reprocessing_station_sort_label(clean_sort_mode),
        "notes": [
            "Station options are NPC stations from the local SDE cache whose owner corporation or faction standing is greater than 1.5.",
            "Options include standings-adjusted processing fees and can be sorted by net yield, processing fee, or standing.",
        ],
    }


def build_reprocessing_station_options(
    *,
    cache: ReprocessingCache,
    route_cache: RouteGraphCache,
    standings: Iterable[dict[str, Any]],
    skill_profile: dict[str, Any],
    implant_profile: dict[str, Any],
    structure_bonus_percent: float,
    sort_mode: str = DEFAULT_REPROCESSING_STATION_SORT,
) -> tuple[list[dict[str, Any]], int]:
    systems = route_cache.systems or {} if route_cache.available else {}
    clean_sort_mode = normalize_reprocessing_station_sort(sort_mode)
    options = []
    for station in (cache.stations or {}).values():
        standing_profile = reprocessing_station_standing_profile(
            standings,
            station=station,
            owner_id=station.owner_id,
        )
        standing_value = standing_profile["standing"]
        standing_source = standing_profile["standing_source"]
        if standing_value is None or standing_value <= MIN_REPROCESSING_STATION_STANDING:
            continue
        base_yield_percent = station_reprocessing_yield_percent(station)
        base_station_tax_rate = station.reprocessing_tax_rate if station.reprocessing_tax_rate is not None else 0.0
        station_tax_rate = npc_reprocessing_station_tax_rate(base_station_tax_rate, standing_value)
        rates = build_reprocessing_yield_rates(
            facility_yield_percent=base_yield_percent,
            station_tax_percent=station_tax_rate * 100.0,
            skill_profile=skill_profile,
            implant_profile=implant_profile,
            structure_bonus_percent=structure_bonus_percent,
        )
        solar_system_id = station.solar_system_id or 0
        system = systems.get(solar_system_id)
        solar_system_name = system.name if system else (f"System {solar_system_id}" if solar_system_id else "")
        options.append(
            {
                "station_id": station.station_id,
                "label": reprocessing_station_option_label(
                    station=station,
                    solar_system_name=solar_system_name,
                    net_yield_percent=rates["net_yield_percent"],
                    station_tax_percent=station_tax_rate * 100.0,
                    standing=standing_value,
                ),
                "owner_id": station.owner_id,
                "owner_name": station.owner_name,
                "owner_faction_id": station.owner_faction_id,
                "solar_system_id": station.solar_system_id,
                "solar_system_name": solar_system_name,
                "station_type_id": station.type_id,
                "facility_yield_percent": base_yield_percent,
                "base_station_tax_percent": base_station_tax_rate * 100.0,
                "station_tax_percent": station_tax_rate * 100.0,
                "processing_fee_percent": station_tax_rate * 100.0,
                "standing": standing_value,
                "standing_source": standing_source,
                "standing_row": standing_profile,
                "gross_yield_percent": rates["gross_yield_percent"],
                "net_yield_percent": rates["net_yield_percent"],
                "capped": rates["capped"],
            }
        )
    options.sort(key=lambda item: reprocessing_station_sort_key(item, clean_sort_mode))
    return options[:MAX_REPROCESSING_STATION_OPTIONS], len(options)


def normalize_reprocessing_station_sort(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "net": "net_yield",
        "yield": "net_yield",
        "best_yield": "net_yield",
        "fee": "processing_fee",
        "fees": "processing_fee",
        "tax": "processing_fee",
        "station_tax": "processing_fee",
        "processing_fees": "processing_fee",
        "highest_standing": "standing",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in REPROCESSING_STATION_SORT_MODES else DEFAULT_REPROCESSING_STATION_SORT


def reprocessing_station_sort_label(sort_mode: str) -> str:
    return {
        "processing_fee": "lowest processing fee",
        "standing": "highest standing",
        "net_yield": "best net yield",
    }.get(normalize_reprocessing_station_sort(sort_mode), "best net yield")


def reprocessing_station_sort_key(item: dict[str, Any], sort_mode: str) -> tuple[Any, ...]:
    common = (
        str(item.get("solar_system_name") or ""),
        str(item.get("owner_name") or ""),
        int(item["station_id"]),
    )
    if sort_mode == "processing_fee":
        return (
            float(item["processing_fee_percent"]),
            -float(item["standing"]),
            -float(item["net_yield_percent"]),
            *common,
        )
    if sort_mode == "standing":
        return (
            -float(item["standing"]),
            float(item["processing_fee_percent"]),
            -float(item["net_yield_percent"]),
            *common,
        )
    return (
        -float(item["net_yield_percent"]),
        float(item["processing_fee_percent"]),
        -float(item["standing"]),
        *common,
    )


def reprocessing_station_option_label(
    *,
    station: ReprocessingStation,
    solar_system_name: str,
    net_yield_percent: float,
    station_tax_percent: float,
    standing: float,
) -> str:
    owner = station.owner_name or f"Owner {station.owner_id or 'unknown'}"
    system = solar_system_name or f"System {station.solar_system_id or 'unknown'}"
    return (
        f"{net_yield_percent:.2f}% net - {system} - {owner} "
        f"(standing {standing:.2f}, processing fee {station_tax_percent:.2f}%, station {station.station_id})"
    )


def build_reprocessing_jita_valuation(
    *,
    config: EveSsoConfig,
    ore: ReprocessingOre,
    input_quantity: int,
    output_materials: list[dict[str, Any]],
) -> dict[str, Any]:
    route_cache = load_route_graph_cache()
    jita_system = resolve_jita_market_system(route_cache.systems or {} if route_cache.available else {})
    type_ids = {ore.type_id}
    type_ids.update(int(material["type_id"]) for material in output_materials if int(material.get("net_quantity") or 0) > 0)
    market_prices: dict[int, dict[str, Any]] = {}
    market_price_error = ""
    try:
        market_prices = fetch_market_prices(config)
    except CorpMarketError as exc:
        market_price_error = str(exc)
    orders_by_type, order_count, errors = scan_system_market_orders(
        config=config,
        type_ids=sorted(type_ids),
        system=jita_system,
        order_type="buy",
    )
    ore_orders = orders_by_type.get(ore.type_id, ())
    ore_liquidation = liquidation_value_from_orders(ore_orders, quantity=input_quantity)
    ore_estimate = eve_market_price_estimate(market_prices, type_id=ore.type_id, quantity=input_quantity)

    processed_estimate_partial_value = 0.0
    processed_estimate_priced_types = 0
    processed_estimate_required_types = 0
    processed_partial_value = 0.0
    processed_full_material_types = 0
    processed_partial_material_types = 0
    required_material_types = 0
    for material in output_materials:
        material_type_id = int(material["type_id"])
        quantity = int(material.get("net_quantity") or 0)
        material_orders = orders_by_type.get(material_type_id, ())
        material_liquidation = liquidation_value_from_orders(material_orders, quantity=quantity)
        material_estimate = eve_market_price_estimate(market_prices, type_id=material_type_id, quantity=quantity)
        if quantity > 0:
            required_material_types += 1
            processed_estimate_required_types += 1
        if material_liquidation["priced_quantity"] > 0:
            processed_partial_value += float(material_liquidation["value"])
            processed_partial_material_types += 1
            if material_liquidation["complete"]:
                processed_full_material_types += 1
        if material_estimate["value"] is not None:
            processed_estimate_partial_value += float(material_estimate["value"])
            processed_estimate_priced_types += 1
        material["jita_buy_price"] = float(material_orders[0]["price"]) if material_orders else None
        material["jita_value"] = (
            material_liquidation["value"] if material_liquidation["priced_quantity"] > 0 else None
        )
        material["jita_priced_quantity"] = material_liquidation["priced_quantity"]
        material["jita_required_quantity"] = material_liquidation["required_quantity"]
        material["jita_complete"] = material_liquidation["complete"]
        material["jita_buy_order"] = material_orders[0] if material_orders else None
        material["eve_estimate_unit_price"] = material_estimate["unit_price"]
        material["eve_estimate_price_source"] = material_estimate["price_source"]
        material["eve_estimate_value"] = material_estimate["value"]

    processed_complete = required_material_types == 0 or processed_full_material_types >= required_material_types
    processed_estimate_complete = processed_estimate_priced_types >= processed_estimate_required_types
    ore_complete = ore_liquidation["complete"]
    processed_value = processed_partial_value if processed_complete else None
    processed_partial_value_payload = processed_partial_value if processed_partial_material_types > 0 else None
    processed_estimate_value = processed_estimate_partial_value if processed_estimate_complete else None
    processed_estimate_partial_value_payload = (
        processed_estimate_partial_value if processed_estimate_priced_types > 0 else None
    )
    ore_value = ore_liquidation["value"] if ore_complete else None
    ore_partial_value = ore_liquidation["value"] if ore_liquidation["priced_quantity"] > 0 else None
    value_delta = processed_value - ore_value if processed_value is not None and ore_value is not None else None
    eve_estimate_value_delta = (
        processed_estimate_value - ore_estimate["value"]
        if processed_estimate_value is not None and ore_estimate["value"] is not None
        else None
    )
    eve_estimate_partial_value_delta = (
        processed_estimate_partial_value_payload - ore_estimate["value"]
        if processed_estimate_partial_value_payload is not None and ore_estimate["value"] is not None
        else None
    )
    partial_value_delta = (
        processed_partial_value_payload - ore_partial_value
        if processed_partial_value_payload is not None and ore_partial_value is not None
        else None
    )
    notes = []
    if not route_cache.available:
        notes.append("Route graph cache was unavailable; using the built-in Jita system and region ids.")
    if errors:
        notes.append("Some Jita market lookups failed; shown values may be partial.")
    if not processed_complete:
        notes.append("Processed-material value is partial because Jita buy depth did not cover every output material.")
    if not ore_complete:
        notes.append("Unprocessed ore value is partial because Jita buy depth did not cover the full ore stack.")
    if market_price_error:
        notes.append(f"ESI market estimate prices were unavailable: {market_price_error}")
    elif not processed_estimate_complete or ore_estimate["value"] is None:
        notes.append("ESI market estimate values are partial because some type prices were missing.")
    return {
        "system": jita_system.to_dict(jumps=0),
        "order_type": "buy",
        "order_count": order_count,
        "eve_estimate": {
            "source": "ESI /markets/prices average_price with adjusted_price fallback",
            "note": "This is a cluster market estimate, not a Jita buy-order liquidation value.",
            "processed_material_value": processed_estimate_value,
            "processed_partial_material_value": processed_estimate_partial_value_payload,
            "processed_complete": processed_estimate_complete,
            "processed_material_types": processed_estimate_priced_types,
            "processed_required_material_types": processed_estimate_required_types,
            "ore_value": ore_estimate["value"],
            "ore_unit_price": ore_estimate["unit_price"],
            "ore_price_source": ore_estimate["price_source"],
            "ore_complete": ore_estimate["value"] is not None,
            "value_delta": eve_estimate_value_delta,
            "partial_value_delta": eve_estimate_partial_value_delta,
            "error": market_price_error,
        },
        "processed_material_value": processed_value,
        "processed_partial_material_value": processed_partial_value_payload,
        "processed_complete": processed_complete,
        "processed_material_types": processed_full_material_types,
        "processed_partial_material_types": processed_partial_material_types,
        "processed_required_material_types": required_material_types,
        "ore_value": ore_value,
        "ore_partial_value": ore_partial_value,
        "ore_complete": ore_complete,
        "ore_buy_price": float(ore_orders[0]["price"]) if ore_orders else None,
        "ore_priced_quantity": ore_liquidation["priced_quantity"],
        "ore_required_quantity": ore_liquidation["required_quantity"],
        "value_delta": value_delta,
        "partial_value_delta": partial_value_delta,
        "errors": errors[:8],
        "notes": notes,
        "market_cache": market_order_cache_status(),
    }


def eve_market_price_estimate(
    market_prices: dict[int, dict[str, Any]],
    *,
    type_id: int,
    quantity: int,
) -> dict[str, Any]:
    record = market_prices.get(int(type_id)) or {}
    unit_price = clean_optional_float(record.get("average_price"))
    price_source = "average_price" if unit_price is not None else ""
    if unit_price is None:
        unit_price = clean_optional_float(record.get("adjusted_price"))
        price_source = "adjusted_price" if unit_price is not None else ""
    clean_quantity = max(0, int(quantity or 0))
    return {
        "type_id": int(type_id),
        "quantity": clean_quantity,
        "unit_price": unit_price,
        "price_source": price_source,
        "value": unit_price * clean_quantity if unit_price is not None else None,
    }


def station_reprocessing_yield_percent(station: ReprocessingStation | None) -> float:
    if station and station.reprocessing_efficiency is not None:
        return station.reprocessing_efficiency * 100.0
    return DEFAULT_REPROCESSING_FACILITY_YIELD_PERCENT


def build_reprocessing_yield_rates(
    *,
    facility_yield_percent: float,
    station_tax_percent: float,
    skill_profile: dict[str, Any],
    implant_profile: dict[str, Any],
    structure_bonus_percent: float,
) -> dict[str, Any]:
    facility_yield_rate = max(0.0, min(1.0, float(facility_yield_percent or 0.0) / 100.0))
    station_tax_rate = max(0.0, min(1.0, float(station_tax_percent or 0.0) / 100.0))
    reprocessing_multiplier = float(skill_profile.get("reprocessing_multiplier") or 1.0)
    reprocessing_efficiency_multiplier = float(skill_profile.get("reprocessing_efficiency_multiplier") or 1.0)
    specialization_multiplier = float(skill_profile.get("specialization_multiplier") or 1.0)
    implant_multiplier = 1.0 + float(implant_profile.get("bonus_percent") or 0.0) / 100.0
    structure_multiplier = 1.0 + float(structure_bonus_percent or 0.0) / 100.0
    raw_yield_rate = (
        facility_yield_rate
        * reprocessing_multiplier
        * reprocessing_efficiency_multiplier
        * specialization_multiplier
        * implant_multiplier
        * structure_multiplier
    )
    gross_yield_rate = min(max(raw_yield_rate, 0.0), 1.0)
    net_yield_rate = gross_yield_rate * (1.0 - station_tax_rate)
    return {
        "raw_yield_rate": raw_yield_rate,
        "gross_yield_rate": gross_yield_rate,
        "gross_yield_percent": gross_yield_rate * 100.0,
        "station_tax_rate": station_tax_rate,
        "station_tax_percent": station_tax_rate * 100.0,
        "net_yield_rate": net_yield_rate,
        "net_yield_percent": net_yield_rate * 100.0,
        "capped": raw_yield_rate > gross_yield_rate,
        "breakdown": {
            "facility_yield_percent": facility_yield_percent,
            "facility_multiplier": facility_yield_rate,
            "reprocessing_level": skill_profile.get("reprocessing_level"),
            "reprocessing_multiplier": reprocessing_multiplier,
            "reprocessing_efficiency_level": skill_profile.get("reprocessing_efficiency_level"),
            "reprocessing_efficiency_multiplier": reprocessing_efficiency_multiplier,
            "specialization_skill_name": skill_profile.get("specialization_skill_name"),
            "specialization_level": skill_profile.get("specialization_level"),
            "specialization_multiplier": specialization_multiplier,
            "implant_bonus_percent": implant_profile.get("bonus_percent"),
            "implant_multiplier": implant_multiplier,
            "structure_bonus_percent": structure_bonus_percent,
            "structure_multiplier": structure_multiplier,
            "raw_yield_percent": raw_yield_rate * 100.0,
            "gross_yield_percent": gross_yield_rate * 100.0,
            "processing_fee_percent": station_tax_rate * 100.0,
            "net_yield_percent": net_yield_rate * 100.0,
        },
    }


def build_reprocessing_skill_profile(skills_payload: dict[str, Any], ore: ReprocessingOre) -> dict[str, Any]:
    reprocessing_level = skill_level_from_esi(skills_payload, REPROCESSING_SKILL_TYPE_ID)
    efficiency_level = skill_level_from_esi(skills_payload, REPROCESSING_EFFICIENCY_SKILL_TYPE_ID)
    specialization_level = skill_level_from_esi(skills_payload, ore.specialization_skill_type_id)
    return {
        "reprocessing_skill_type_id": REPROCESSING_SKILL_TYPE_ID,
        "reprocessing_level": reprocessing_level,
        "reprocessing_multiplier": 1.0 + 0.03 * reprocessing_level,
        "reprocessing_efficiency_skill_type_id": REPROCESSING_EFFICIENCY_SKILL_TYPE_ID,
        "reprocessing_efficiency_level": efficiency_level,
        "reprocessing_efficiency_multiplier": 1.0 + 0.02 * efficiency_level,
        "specialization_skill_type_id": ore.specialization_skill_type_id,
        "specialization_skill_name": ore.specialization_skill_name,
        "specialization_level": specialization_level,
        "specialization_multiplier": 1.0 + 0.02 * specialization_level,
        "source": FLIGHT_SKILLS_SCOPE,
    }


def build_reprocessing_implant_profile(
    config: EveSsoConfig,
    session: FlightEsiSession,
    *,
    implant_bonus_percent: float | None,
) -> dict[str, Any]:
    if implant_bonus_percent is not None:
        bonus = clamp_reprocessing_percent(
            implant_bonus_percent,
            "implant bonus percent",
            maximum=MAX_REPROCESSING_BONUS_PERCENT,
        )
        return {
            "source": "manual-override",
            "implant_type_id": None,
            "bonus_percent": bonus,
            "notes": ["Implant bonus was supplied as a manual override."],
        }
    if FLIGHT_IMPLANTS_SCOPE not in session.scopes:
        return {
            "source": "not-scoped",
            "implant_type_id": None,
            "bonus_percent": 0.0,
            "notes": [f"Reconnect ESI with {FLIGHT_IMPLANTS_SCOPE} to read a reprocessing implant automatically."],
        }
    implants = fetch_flight_implants(config, session)
    best_implant_id = None
    best_bonus = 0.0
    for implant_id in implants:
        bonus = REPROCESSING_IMPLANT_BONUSES.get(implant_id, 0.0)
        if bonus > best_bonus:
            best_bonus = bonus
            best_implant_id = implant_id
    return {
        "source": FLIGHT_IMPLANTS_SCOPE,
        "implant_type_id": best_implant_id,
        "bonus_percent": best_bonus,
        "notes": [] if best_implant_id else ["No known reprocessing implant was returned by ESI."],
    }


def build_reprocessing_location_profile(
    config: EveSsoConfig,
    session: FlightEsiSession,
    *,
    cache: ReprocessingCache,
    location: dict[str, Any],
    standings: Iterable[dict[str, Any]],
    selected_station_id: int | None,
    facility_yield_percent: float | None,
    station_tax_percent: float | None,
) -> dict[str, Any]:
    notes: list[str] = []
    station_id = selected_station_id if selected_station_id and selected_station_id > 0 else clean_optional_int(location.get("station_id"))
    structure_id = None if selected_station_id else clean_optional_int(location.get("structure_id"))
    station = (cache.stations or {}).get(station_id or 0)
    if selected_station_id and station is None:
        raise CorpMarketError("Selected reprocessing station is not available in the local SDE cache.")
    station_detail: dict[str, Any] = {}
    structure_detail: dict[str, Any] = {}
    owner_id = station.owner_id if station else None
    owner_name = station.owner_name if station else ""
    location_name = ""
    location_kind = "space"
    source = "default"
    base_yield_percent = DEFAULT_REPROCESSING_FACILITY_YIELD_PERCENT
    base_station_tax_rate = 0.0
    standing_value = None
    standing_source = ""
    standing_profile = reprocessing_empty_standing_profile()

    if station_id:
        location_kind = "npc-station"
        source = "selected-sde-npc-station" if selected_station_id else "sde-npc-station"
        try:
            station_detail = fetch_universe_station(config, station_id)
        except CorpMarketError as exc:
            notes.append(f"Could not read ESI station name/details: {exc}")
        location_name = str(station_detail.get("name") or f"Station {station_id}")
        if owner_id is None:
            owner_id = clean_optional_int(station_detail.get("owner") or station_detail.get("owner_id"))
        base_yield_percent = station_reprocessing_yield_percent(station)
        if not station or station.reprocessing_efficiency is None:
            notes.append("NPC station reprocessing efficiency was not found in the static cache; using 50%.")
        if station and station.reprocessing_tax_rate is not None:
            base_station_tax_rate = station.reprocessing_tax_rate
        if not owner_name and owner_id:
            try:
                owner_name = fetch_universe_names(config, [owner_id]).get(owner_id, "")
            except CorpMarketError:
                owner_name = ""
        standing_profile = reprocessing_station_standing_profile(standings, station=station, owner_id=owner_id)
        standing_value = standing_profile["standing"]
        standing_source = standing_profile["standing_source"]
        if selected_station_id:
            notes.append("Using the selected NPC station instead of the pilot's current ESI location.")
    elif structure_id:
        location_kind = "structure"
        source = "structure-default"
        location_name = f"Structure {structure_id}"
        if FLIGHT_STRUCTURES_SCOPE in session.scopes:
            try:
                structure_detail = fetch_universe_structure(config, session, structure_id)
                location_name = str(structure_detail.get("name") or location_name)
                owner_id = clean_optional_int(structure_detail.get("owner_id"))
                if owner_id:
                    owner_name = fetch_universe_names(config, [owner_id]).get(owner_id, "")
            except CorpMarketError as exc:
                notes.append(f"Could not resolve ESI structure details: {exc}")
        else:
            notes.append(f"Reconnect ESI with {FLIGHT_STRUCTURES_SCOPE} to resolve the current Upwell structure name/owner.")
        notes.append(
            "Warning: ESI does not expose this structure's reprocessing rigs, facility tax, service settings, "
            "or structure bonus here; set those manual overrides to match the in-game window."
        )
    else:
        notes.append("ESI location did not report a station or structure; using default facility assumptions.")

    if facility_yield_percent is not None:
        base_yield_percent = clamp_reprocessing_percent(
            facility_yield_percent,
            "facility yield percent",
            maximum=MAX_REPROCESSING_FACILITY_YIELD_PERCENT,
        )
        source = "manual-facility-yield"
        notes.append("Facility yield was supplied as a manual override.")
    else:
        base_yield_percent = clamp_reprocessing_percent(
            base_yield_percent,
            "facility yield percent",
            maximum=MAX_REPROCESSING_FACILITY_YIELD_PERCENT,
        )

    if station_tax_percent is not None:
        tax_percent = clamp_reprocessing_percent(
            station_tax_percent,
            "station tax percent",
            maximum=MAX_REPROCESSING_TAX_PERCENT,
        )
        tax_source = "manual-override"
        notes.append("Station tax was supplied as a manual override.")
    elif location_kind == "npc-station":
        tax_rate = npc_reprocessing_station_tax_rate(base_station_tax_rate, standing_value)
        tax_percent = tax_rate * 100.0
        tax_source = "esi-standings-and-sde-station-take"
        if standing_value is None and base_station_tax_rate > 0:
            notes.append(
                "Warning: no ESI npc_corp or faction standing matched this station owner; using the station base processing fee."
            )
    else:
        tax_percent = 0.0
        tax_source = "default-no-tax"

    return {
        "location_kind": location_kind,
        "location_id": station_id or structure_id,
        "location_name": location_name,
        "source": source,
        "facility_yield_percent": base_yield_percent,
        "station_tax_percent": tax_percent,
        "station_tax_source": tax_source,
        "base_station_tax_percent": base_station_tax_rate * 100.0,
        "adjusted_station_tax_percent": tax_percent,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "standing": standing_value,
        "standing_source": standing_source,
        "standing_row": standing_profile,
        "selected_station_id": selected_station_id,
        "station": station.to_dict() if station else None,
        "structure": {
            "name": structure_detail.get("name"),
            "owner_id": structure_detail.get("owner_id"),
            "solar_system_id": structure_detail.get("solar_system_id"),
            "type_id": structure_detail.get("type_id"),
        }
        if structure_detail
        else None,
        "notes": notes,
    }


def reprocessing_empty_standing_profile() -> dict[str, Any]:
    return {
        "standing": None,
        "standing_source": "",
        "from_id": None,
        "from_type": "",
        "raw_from_type": "",
    }


def reprocessing_station_standing_profile(
    standings: Iterable[dict[str, Any]],
    *,
    station: ReprocessingStation | None,
    owner_id: int | None,
) -> dict[str, Any]:
    owner_id = owner_id or (station.owner_id if station else None)
    if owner_id:
        corporation_row = standing_row_for_entity(standings, from_id=owner_id, from_type="npc_corp")
        if corporation_row is not None:
            return {
                **corporation_row,
                "standing_source": "owner-corporation",
            }
    if station and station.owner_faction_id:
        faction_row = standing_row_for_entity(standings, from_id=station.owner_faction_id, from_type="faction")
        if faction_row is not None:
            return {
                **faction_row,
                "standing_source": "owner-faction",
            }
    return reprocessing_empty_standing_profile()


def reprocessing_station_standing(
    standings: Iterable[dict[str, Any]],
    *,
    station: ReprocessingStation | None,
    owner_id: int | None,
) -> tuple[float | None, str]:
    profile = reprocessing_station_standing_profile(standings, station=station, owner_id=owner_id)
    return profile["standing"], profile["standing_source"]


def standing_for_entity(standings: Iterable[dict[str, Any]], *, from_id: int, from_type: str) -> float | None:
    row = standing_row_for_entity(standings, from_id=from_id, from_type=from_type)
    return clean_optional_float(row.get("standing")) if row else None


def standing_row_for_entity(standings: Iterable[dict[str, Any]], *, from_id: int, from_type: str) -> dict[str, Any] | None:
    expected_types = standing_from_type_aliases(from_type)
    for item in standings:
        if not isinstance(item, dict):
            continue
        if clean_optional_int(item.get("from_id")) != int(from_id):
            continue
        normalized_type = normalize_standing_from_type(item.get("from_type"))
        if normalized_type not in expected_types:
            continue
        standing = clean_optional_float(item.get("standing"))
        if standing is not None:
            return {
                "standing": standing,
                "from_id": int(from_id),
                "from_type": normalized_type,
                "raw_from_type": str(item.get("from_type") or ""),
            }
    return None


def normalize_standing_from_type(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return "npc_corp" if normalized == "corporation" else normalized


def standing_from_type_aliases(from_type: str) -> set[str]:
    normalized = normalize_standing_from_type(from_type)
    aliases = {normalized}
    if normalized == "npc_corp":
        aliases.add("corporation")
    return aliases


def npc_reprocessing_station_tax_rate(base_tax_rate: float, standing: float | None) -> float:
    clean_base = max(0.0, min(1.0, float(base_tax_rate or 0.0)))
    if standing is None:
        return clean_base
    return max(0.0, min(1.0, clean_base - (0.0075 * float(standing))))


def clean_reprocessing_quantity(value: Any) -> int:
    try:
        quantity = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise CorpMarketError("Ore quantity must be a whole number.") from exc
    if quantity <= 0:
        raise CorpMarketError("Ore quantity must be greater than zero.")
    return min(quantity, MAX_REPROCESSING_ORE_UNITS)


def parse_optional_reprocessing_percent(value: Any, field: str, *, maximum: float) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return clamp_reprocessing_percent(value, field, maximum=maximum)


def clamp_reprocessing_percent(value: Any, field: str, *, maximum: float) -> float:
    try:
        percent = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise CorpMarketError(f"{field} must be a number.") from exc
    if percent < 0:
        raise CorpMarketError(f"{field} cannot be negative.")
    return min(percent, float(maximum))


def flight_session_has_member_access(config: EveSsoConfig, session: FlightEsiSession | None) -> bool:
    if session is None:
        return False
    if not config.membership_restricted:
        return True
    return bool(session.membership_ok)


def verified_pilot_has_member_access(config: EveSsoConfig, pilot: VerifiedPilot) -> bool:
    if not config.membership_restricted:
        return True
    return bool(pilot.membership_ok)


def flight_membership_status(config: EveSsoConfig, session: FlightEsiSession | None) -> dict[str, Any]:
    required = config.membership_restricted
    allowed = flight_session_has_member_access(config, session) if session else None
    if not required:
        message = "No corp or alliance allowlist is configured."
    elif session is None:
        message = "Sign in with an allowlisted corporation or alliance character."
    elif allowed:
        message = "Signed-in character is in the configured corp/alliance allowlist."
    else:
        message = "Signed-in character is not in the configured corp/alliance allowlist."
    return {
        "required": required,
        "allowed": allowed,
        "corporation_allowlist_count": len(config.allowed_corporation_ids),
        "alliance_allowlist_count": len(config.allowed_alliance_ids),
        "trusted_members_can_write_market": bool(config.trusted_members_can_edit),
        "message": message,
    }


def flight_member_access_error(config: EveSsoConfig, session: FlightEsiSession | None) -> str:
    if session is None:
        return "Connect ESI before using Flight Attendant."
    if config.membership_restricted and not session.membership_ok:
        return "This EVE character is not in the configured corp/alliance allowlist."
    return ""


def is_https_url(url: str) -> bool:
    return urlparse(str(url or "")).scheme.lower() == "https"


def should_secure_flight_cookie(public_base_url: str) -> bool:
    return is_https_url(public_base_url)


def default_flight_callback_url(*, public_base_url: str, url_host: str, port: int) -> str:
    if public_base_url and urlparse(public_base_url).scheme.lower() in {"http", "https"}:
        return f"{public_base_url.rstrip('/')}/flight/callback"
    return f"http://{url_host}:{port}/flight/callback"


def public_hosting_config_errors(*, public_base_url: str, sso_config: EveSsoConfig, public_hosting_mode: bool) -> list[str]:
    if not public_hosting_mode:
        return []
    errors: list[str] = []
    if not is_https_url(public_base_url):
        errors.append("--public-base-url must be an https URL in public hosting mode")
    if not sso_config.enabled:
        errors.append("EVE SSO client id, client secret, and callback URL are required")
    elif not is_https_url(sso_config.callback_url):
        errors.append("--sso-callback-url must be an https URL in public hosting mode")
    if not sso_config.membership_restricted:
        errors.append("configure --allowed-corporation-ids or --allowed-alliance-ids for member-only access")
    return errors


def market_write_access_allowed(
    *,
    is_loopback: bool,
    public_hosting_mode: bool,
    admin_token: str,
    auth_header: str,
    token_header: str,
    trusted_member: bool = False,
) -> bool:
    if trusted_member:
        return True
    if admin_token:
        return auth_header == f"Bearer {admin_token}" or token_header == admin_token or (is_loopback and not public_hosting_mode)
    if public_hosting_mode:
        return False
    return True


def build_flight_status_payload(
    *,
    config: EveSsoConfig,
    session: FlightEsiSession | None,
    callback_url: str,
    public_base_url: str = "",
    public_hosting_mode: bool = False,
    secure_cookies: bool = False,
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
        "membership": flight_membership_status(config, session),
        "hosting": {
            "public_hosting_mode": bool(public_hosting_mode),
            "public_base_url": public_base_url,
            "secure_cookies": bool(secure_cookies),
            "token_storage": "server-memory-only",
            "market_order_cache_ttl_seconds": int(MARKET_ORDER_CACHE_TTL_SECONDS),
        },
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
    if not flight_session_has_member_access(config, session):
        payload["error"] = flight_member_access_error(config, session)
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


def build_flight_hosting_diagnostics(
    *,
    public_base_url: str,
    sso_config: EveSsoConfig,
    public_hosting_mode: bool,
    secure_cookies: bool,
) -> dict[str, Any]:
    recipe_cache = load_industry_recipe_cache()
    route_cache = load_route_graph_cache()
    reprocessing_cache = load_reprocessing_cache()
    planetary_cache = load_planetary_industry_cache(DEFAULT_PLANETARY_CACHE_PATH)
    checks = [
        {
            "name": "HTTPS public URL",
            "ok": (not public_hosting_mode) or is_https_url(public_base_url),
            "detail": "Public hosting mode should use an HTTPS tunnel or domain.",
        },
        {
            "name": "EVE SSO configured",
            "ok": sso_config.enabled,
            "detail": "Flight Attendant needs an EVE SSO app client id, secret, and callback URL.",
        },
        {
            "name": "Member allowlist",
            "ok": (not public_hosting_mode) or sso_config.membership_restricted,
            "detail": "Use allowed corporation or alliance ids before sharing a public link.",
        },
        {
            "name": "Secure cookies",
            "ok": (not public_hosting_mode) or secure_cookies,
            "detail": "HTTPS public URLs receive Secure session cookies.",
        },
        {
            "name": "Recipe cache",
            "ok": recipe_cache.available,
            "detail": recipe_cache.error or "Blueprint recipe cache is ready.",
        },
        {
            "name": "Route graph cache",
            "ok": route_cache.available,
            "detail": route_cache.error or "Jump-aware route graph cache is ready.",
        },
        {
            "name": "Reprocessing cache",
            "ok": reprocessing_cache.available,
            "detail": reprocessing_cache.error or "Ore reprocessing cache is ready.",
        },
        {
            "name": "Planetary industry cache",
            "ok": planetary_cache.available,
            "detail": planetary_cache.error or "Planetary industry cache is ready.",
        },
    ]
    return {
        "ok": True,
        "generated_at": now_iso(),
        "hosting": {
            "public_hosting_mode": bool(public_hosting_mode),
            "public_base_url": public_base_url,
            "public_base_url_https": is_https_url(public_base_url),
            "secure_cookies": bool(secure_cookies),
            "token_storage": "server-memory-only",
            "market_write_policy": (
                "admin-token-or-trusted-member"
                if public_hosting_mode
                else "local-loopback-or-admin-token"
            ),
        },
        "sso": {
            "configured": sso_config.enabled,
            "callback_url": sso_config.callback_url,
            "callback_url_https": is_https_url(sso_config.callback_url),
            "scopes": list(sso_config.scopes or DEFAULT_FLIGHT_ESI_SCOPES),
            "membership_restricted": sso_config.membership_restricted,
            "corporation_allowlist_count": len(sso_config.allowed_corporation_ids),
            "alliance_allowlist_count": len(sso_config.allowed_alliance_ids),
            "trusted_members_can_write_market": bool(sso_config.trusted_members_can_edit),
        },
        "esi": {
            "market_order_cache": market_order_cache_status(),
            "market_history_cache": market_history_cache_status(),
            "compatibility_date": esi_compatibility_date(),
        },
        "static_caches": {
            "recipe_cache_available": recipe_cache.available,
            "route_graph_available": route_cache.available,
            "reprocessing_cache_available": reprocessing_cache.available,
            "planetary_cache_available": planetary_cache.available,
        },
        "checks": checks,
        "notes": [
            "Flight Attendant is advisory only; pilots perform all EVE actions manually.",
            "No EVE refresh token or token file is stored by this process.",
        ],
    }


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


def resolve_static_asset_path(request_path: str) -> Path | None:
    relative_path = request_path.removeprefix("/static/").strip("/")
    if not relative_path or "\\" in relative_path:
        return None
    asset_root = STATIC_ASSET_ROOT.resolve()
    asset_path = (asset_root / relative_path).resolve()
    try:
        asset_path.relative_to(asset_root)
    except ValueError:
        return None
    if not asset_path.is_file():
        return None
    return asset_path


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
    public_hosting_mode: bool = False,
) -> ThreadingHTTPServer:
    public_base_url = public_base_url.rstrip("/")
    sso_config = sso_config or EveSsoConfig()
    auth_state_store = auth_state_store or AuthStateStore()
    flight_session_store = flight_session_store or FlightEsiSessionStore()
    secure_flight_cookies = should_secure_flight_cookie(public_base_url)

    class CorpMarketHandler(BaseHTTPRequestHandler):
        server_version = "CorpMarketConcierge/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._send_html(render_dashboard())
                return
            if path.startswith("/static/"):
                self._handle_static_asset(path)
                return
            if path == "/api/offers":
                self._handle_offer_list()
                return
            if path == "/api/flight/status":
                self._handle_flight_status()
                return
            if path == "/api/flight/diagnostics":
                self._handle_flight_diagnostics()
                return
            if path == "/api/flight/industry":
                self._handle_flight_industry()
                return
            if path == "/api/flight/buyers":
                self._handle_flight_buyers()
                return
            if path == "/api/flight/buyers/progress":
                self._handle_flight_buyers_progress()
                return
            if path == "/api/flight/profitability":
                self._handle_flight_profitability()
                return
            if path == "/api/flight/hauling":
                self._handle_flight_hauling()
                return
            if path == "/api/flight/hauling/progress":
                self._handle_flight_hauling_progress()
                return
            if path == "/api/flight/acquisition":
                self._handle_flight_acquisition()
                return
            if path == "/api/flight/trade-pnl":
                self._handle_flight_trade_pnl()
                return
            if path == "/api/flight/planetary":
                self._handle_flight_planetary()
                return
            if path == "/api/flight/reprocessing":
                self._handle_flight_reprocessing()
                return
            if path == "/api/flight/reprocessing-locations":
                self._handle_flight_reprocessing_locations()
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
                if not self._require_write_access():
                    return
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
                public_base_url=public_base_url,
                public_hosting_mode=public_hosting_mode,
                secure_cookies=secure_flight_cookies,
                max_jumps=max_jumps,
            )
            self._send_json(payload)

        def _handle_flight_diagnostics(self) -> None:
            self._send_json(
                build_flight_hosting_diagnostics(
                    public_base_url=public_base_url,
                    sso_config=sso_config,
                    public_hosting_mode=public_hosting_mode,
                    secure_cookies=secure_flight_cookies,
                )
            )

        def _handle_flight_industry(self) -> None:
            session = self._require_flight_session("loading industry analysis")
            if session is None:
                return
            try:
                payload = build_flight_industry_payload(config=sso_config, session=session)
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_buyers(self) -> None:
            session = self._require_flight_session("scanning buyer orders")
            if session is None:
                return
            query = parse_qs(urlparse(self.path).query)
            max_jumps = clamp_flight_max_jumps((query.get("max_jumps") or [DEFAULT_FLIGHT_MAX_JUMPS])[0])
            try:
                payload = build_flight_buyers_payload(config=sso_config, session=session, max_jumps=max_jumps)
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_buyers_progress(self) -> None:
            self._send_sse_headers()

            def emit(event: str, payload: dict[str, Any]) -> None:
                self._send_sse_event(event, payload)

            if not sso_config.enabled:
                emit("scan_error", {"ok": False, "error": "EVE SSO is not configured."})
                return
            session = self._flight_session()
            if session is None:
                emit("scan_error", {"ok": False, "error": "Connect ESI before scanning buyer orders."})
                return
            access_error = flight_member_access_error(sso_config, session)
            if access_error:
                emit("scan_error", {"ok": False, "error": access_error})
                return
            query = parse_qs(urlparse(self.path).query)
            max_jumps = clamp_flight_max_jumps((query.get("max_jumps") or [DEFAULT_FLIGHT_MAX_JUMPS])[0])
            try:
                payload = build_flight_buyers_payload(
                    config=sso_config,
                    session=session,
                    max_jumps=max_jumps,
                    progress=emit,
                )
            except (BrokenPipeError, ConnectionResetError):
                return
            except CorpMarketError as exc:
                emit("scan_error", {"ok": False, "error": str(exc)})
                return
            try:
                emit("result", payload)
                emit("done", {"ok": True, "generated_at": now_iso()})
            except (BrokenPipeError, ConnectionResetError):
                return

        def _handle_flight_profitability(self) -> None:
            session = self._require_flight_session("ranking profitability")
            if session is None:
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
            session = self._require_flight_session("scanning hauler routes")
            if session is None:
                return
            query = parse_qs(urlparse(self.path).query)
            origin = first_query_value(query, "origin_name") or ""
            destination = first_query_value(query, "destination") or DEFAULT_HAUL_DESTINATION_SYSTEM
            detour_jumps = clamp_haul_detour_jumps((query.get("detour_jumps") or [DEFAULT_HAUL_DETOUR_JUMPS])[0])
            cargo_m3 = clamp_haul_cargo_m3((query.get("cargo_m3") or [DEFAULT_HAUL_CARGO_M3])[0])
            min_detour_margin = clamp_haul_min_detour_margin_percent(
                (query.get("min_detour_margin_percent") or [DEFAULT_HAUL_MIN_DETOUR_MARGIN_PERCENT])[0]
            )
            route_preference = normalize_haul_route_preference(
                (query.get("route_preference") or [DEFAULT_HAUL_ROUTE_PREFERENCE])[0]
            )
            avoid_recent_pod_kills = query_bool(
                (query.get("avoid_recent_pod_kills") or [str(int(DEFAULT_HAUL_AVOID_RECENT_POD_KILLS))])[0],
                default=DEFAULT_HAUL_AVOID_RECENT_POD_KILLS,
            )
            include_common_materials = query_bool(
                (query.get("common_materials") or ["1"])[0],
                default=True,
            )
            market_group_ids = clean_haul_market_group_ids(query.get("market_group_ids") or [])
            try:
                payload = build_flight_hauling_payload(
                    config=sso_config,
                    session=session,
                    origin_name=origin,
                    destination_name=destination,
                    detour_jumps=detour_jumps,
                    cargo_capacity_m3=cargo_m3,
                    min_detour_margin_percent=min_detour_margin,
                    route_preference=route_preference,
                    avoid_recent_pod_kills=avoid_recent_pod_kills,
                    include_common_materials=include_common_materials,
                    market_group_ids=market_group_ids,
                )
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_hauling_progress(self) -> None:
            self._send_sse_headers()

            def emit(event: str, payload: dict[str, Any]) -> None:
                self._send_sse_event(event, payload)

            if not sso_config.enabled:
                emit("scan_error", {"ok": False, "error": "EVE SSO is not configured."})
                return
            session = self._flight_session()
            if session is None:
                emit("scan_error", {"ok": False, "error": "Connect ESI before scanning hauler routes."})
                return
            access_error = flight_member_access_error(sso_config, session)
            if access_error:
                emit("scan_error", {"ok": False, "error": access_error})
                return
            query = parse_qs(urlparse(self.path).query)
            origin = first_query_value(query, "origin_name") or ""
            destination = first_query_value(query, "destination") or DEFAULT_HAUL_DESTINATION_SYSTEM
            detour_jumps = clamp_haul_detour_jumps((query.get("detour_jumps") or [DEFAULT_HAUL_DETOUR_JUMPS])[0])
            cargo_m3 = clamp_haul_cargo_m3((query.get("cargo_m3") or [DEFAULT_HAUL_CARGO_M3])[0])
            min_detour_margin = clamp_haul_min_detour_margin_percent(
                (query.get("min_detour_margin_percent") or [DEFAULT_HAUL_MIN_DETOUR_MARGIN_PERCENT])[0]
            )
            route_preference = normalize_haul_route_preference(
                (query.get("route_preference") or [DEFAULT_HAUL_ROUTE_PREFERENCE])[0]
            )
            avoid_recent_pod_kills = query_bool(
                (query.get("avoid_recent_pod_kills") or [str(int(DEFAULT_HAUL_AVOID_RECENT_POD_KILLS))])[0],
                default=DEFAULT_HAUL_AVOID_RECENT_POD_KILLS,
            )
            include_common_materials = query_bool(
                (query.get("common_materials") or ["1"])[0],
                default=True,
            )
            market_group_ids = clean_haul_market_group_ids(query.get("market_group_ids") or [])
            try:
                payload = build_flight_hauling_payload(
                    config=sso_config,
                    session=session,
                    origin_name=origin,
                    destination_name=destination,
                    detour_jumps=detour_jumps,
                    cargo_capacity_m3=cargo_m3,
                    min_detour_margin_percent=min_detour_margin,
                    route_preference=route_preference,
                    avoid_recent_pod_kills=avoid_recent_pod_kills,
                    include_common_materials=include_common_materials,
                    market_group_ids=market_group_ids,
                    progress=emit,
                )
            except (BrokenPipeError, ConnectionResetError):
                return
            except CorpMarketError as exc:
                emit("scan_error", {"ok": False, "error": str(exc)})
                return
            try:
                emit("result", payload)
                emit("done", {"ok": True, "generated_at": now_iso()})
            except (BrokenPipeError, ConnectionResetError):
                return

        def _handle_flight_acquisition(self) -> None:
            session = self._require_flight_session("planning market acquisitions")
            if session is None:
                return
            query = parse_qs(urlparse(self.path).query)
            origin = first_query_value(query, "origin_name") or ""
            destination = first_query_value(query, "destination") or DEFAULT_HAUL_DESTINATION_SYSTEM
            budget_isk = clamp_acquisition_budget_isk(
                (query.get("budget_isk") or [DEFAULT_ACQUISITION_BUDGET_ISK])[0]
            )
            pickup_jumps = clamp_acquisition_pickup_jumps(
                (query.get("pickup_jumps") or [DEFAULT_ACQUISITION_PICKUP_JUMPS])[0]
            )
            min_margin = clamp_haul_min_detour_margin_percent(
                (query.get("min_margin_percent") or [DEFAULT_HAUL_MIN_DETOUR_MARGIN_PERCENT])[0]
            )
            broker_fee = clamp_acquisition_broker_fee_percent(
                (query.get("broker_fee_percent") or [DEFAULT_ACQUISITION_BROKER_FEE_PERCENT])[0]
            )
            target_days = clamp_acquisition_target_days(
                (query.get("target_days") or [DEFAULT_ACQUISITION_TARGET_DAYS])[0]
            )
            route_preference = normalize_haul_route_preference(
                (query.get("route_preference") or [DEFAULT_HAUL_ROUTE_PREFERENCE])[0]
            )
            include_common_materials = query_bool(
                (query.get("common_materials") or ["1"])[0],
                default=True,
            )
            market_group_ids = clean_haul_market_group_ids(query.get("market_group_ids") or [])
            try:
                payload = build_flight_acquisition_payload(
                    config=sso_config,
                    session=session,
                    origin_name=origin,
                    destination_name=destination,
                    budget_isk=budget_isk,
                    pickup_jumps=pickup_jumps,
                    min_margin_percent=min_margin,
                    broker_fee_percent=broker_fee,
                    target_days=target_days,
                    route_preference=route_preference,
                    include_common_materials=include_common_materials,
                    market_group_ids=market_group_ids,
                )
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_trade_pnl(self) -> None:
            session = self._require_flight_session("analyzing trade profit and loss")
            if session is None:
                return
            query = parse_qs(urlparse(self.path).query)
            window_value = first_query_value(query, "window_hours")
            if window_value:
                window_hours = clamp_trade_pnl_window_hours(window_value)
            else:
                window_hours = clamp_trade_pnl_days(first_query_value(query, "days") or DEFAULT_FLIGHT_TRADE_PNL_DAYS) * 24
            lens = normalize_trade_pnl_lens(first_query_value(query, "lens") or "inventory")
            excluded_tokens = clean_trade_pnl_excluded_tokens(query.get("exclude") or [])
            include_matches = query_bool(first_query_value(query, "include_matches"), default=False)
            try:
                payload = build_flight_trade_pnl_payload(
                    config=sso_config,
                    session=session,
                    window_hours=window_hours,
                    accounting_lens=lens,
                    excluded_tokens=excluded_tokens,
                    include_matches=include_matches,
                )
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_planetary(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            try:
                payload = build_flight_planetary_payload(
                    config=sso_config,
                    hub_name=first_query_value(query, "hub") or DEFAULT_PLANETARY_HUB_SYSTEM,
                    output_tier=first_query_value(query, "output_tier") or DEFAULT_PLANETARY_OUTPUT_TIER,
                    owner_tax_percent=clamp_planetary_percent(
                        first_query_value(query, "owner_tax_percent"),
                        "owner tax percent",
                        default=DEFAULT_PLANETARY_OWNER_TAX_PERCENT,
                    ),
                    npc_tax_percent=clamp_planetary_percent(
                        first_query_value(query, "npc_tax_percent"),
                        "NPC tax percent",
                        default=DEFAULT_PLANETARY_NPC_TAX_PERCENT,
                    ),
                    customs_code_expertise_level=clamp_customs_code_expertise_level(
                        first_query_value(query, "customs_code_expertise_level")
                    ),
                    sales_tax_percent=clamp_planetary_percent(
                        first_query_value(query, "sales_tax_percent"),
                        "sales tax percent",
                        default=DEFAULT_PLANETARY_SALES_TAX_PERCENT,
                    ),
                    broker_fee_percent=clamp_planetary_percent(
                        first_query_value(query, "broker_fee_percent"),
                        "broker fee percent",
                        default=DEFAULT_PLANETARY_BROKER_FEE_PERCENT,
                    ),
                    top=clean_planetary_top(first_query_value(query, "top")),
                )
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_reprocessing(self) -> None:
            session = self._require_flight_session("calculating ore reprocessing")
            if session is None:
                return
            query = parse_qs(urlparse(self.path).query)
            try:
                ore_type_id = clean_positive_int(first_query_value(query, "ore_type_id"), "ore_type_id")
                quantity = clean_reprocessing_quantity(first_query_value(query, "quantity"))
                reprocessing_station_id = clean_optional_int(
                    first_query_value(query, "reprocessing_station_id")
                    or first_query_value(query, "station_id")
                )
                facility_yield = parse_optional_reprocessing_percent(
                    first_query_value(query, "facility_yield_percent"),
                    "facility yield percent",
                    maximum=MAX_REPROCESSING_FACILITY_YIELD_PERCENT,
                )
                station_tax = parse_optional_reprocessing_percent(
                    first_query_value(query, "station_tax_percent"),
                    "station tax percent",
                    maximum=MAX_REPROCESSING_TAX_PERCENT,
                )
                implant_bonus = parse_optional_reprocessing_percent(
                    first_query_value(query, "implant_bonus_percent"),
                    "implant bonus percent",
                    maximum=MAX_REPROCESSING_BONUS_PERCENT,
                )
                structure_bonus = clamp_reprocessing_percent(
                    first_query_value(query, "structure_bonus_percent") or 0,
                    "structure bonus percent",
                    maximum=MAX_REPROCESSING_BONUS_PERCENT,
                )
                payload = build_flight_reprocessing_payload(
                    config=sso_config,
                    session=session,
                    ore_type_id=ore_type_id,
                    quantity=quantity,
                    reprocessing_station_id=reprocessing_station_id,
                    facility_yield_percent=facility_yield,
                    station_tax_percent=station_tax,
                    implant_bonus_percent=implant_bonus,
                    structure_bonus_percent=structure_bonus,
                )
            except CorpMarketError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json(payload)

        def _handle_flight_reprocessing_locations(self) -> None:
            session = self._require_flight_session("loading reprocessing locations")
            if session is None:
                return
            query = parse_qs(urlparse(self.path).query)
            try:
                ore_type_id = clean_positive_int(first_query_value(query, "ore_type_id"), "ore_type_id")
                sort_mode = normalize_reprocessing_station_sort(
                    first_query_value(query, "sort_mode")
                    or first_query_value(query, "sort")
                    or DEFAULT_REPROCESSING_STATION_SORT
                )
                implant_bonus = parse_optional_reprocessing_percent(
                    first_query_value(query, "implant_bonus_percent"),
                    "implant bonus percent",
                    maximum=MAX_REPROCESSING_BONUS_PERCENT,
                )
                structure_bonus = clamp_reprocessing_percent(
                    first_query_value(query, "structure_bonus_percent") or 0,
                    "structure bonus percent",
                    maximum=MAX_REPROCESSING_BONUS_PERCENT,
                )
                payload = build_flight_reprocessing_locations_payload(
                    config=sso_config,
                    session=session,
                    ore_type_id=ore_type_id,
                    sort_mode=sort_mode,
                    implant_bonus_percent=implant_bonus,
                    structure_bonus_percent=structure_bonus,
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
                if not verified_pilot_has_member_access(sso_config, pilot):
                    self._send_html(
                        render_flight_auth_result(
                            "This EVE character is not in the configured corp/alliance allowlist.",
                            ok=False,
                            details=[
                                "Try signing in with a corporation or alliance character that is allowed for this site.",
                                "Ask the site operator to update the Flight Attendant allowlist if this looks wrong.",
                            ],
                        ),
                        status=403,
                    )
                    return
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
            self.send_header("Set-Cookie", flight_session_cookie_header(session_id, secure=secure_flight_cookies))
            self.end_headers()

        def _handle_flight_logout(self) -> None:
            session_id = request_cookie(self, FLIGHT_SESSION_COOKIE_NAME)
            flight_session_store.delete(session_id)
            self.send_response(302)
            self.send_header("Location", "/#flight")
            self.send_header("Set-Cookie", clear_flight_session_cookie_header(secure=secure_flight_cookies))
            self.end_headers()

        def _flight_session(self) -> FlightEsiSession | None:
            return flight_session_store.get(request_cookie(self, FLIGHT_SESSION_COOKIE_NAME))

        def _require_flight_session(self, action: str) -> FlightEsiSession | None:
            if not sso_config.enabled:
                self._send_json({"ok": False, "error": "EVE SSO is not configured."}, status=503)
                return None
            session = self._flight_session()
            if session is None:
                self._send_json({"ok": False, "error": f"Connect ESI before {action}."}, status=401)
                return None
            access_error = flight_member_access_error(sso_config, session)
            if access_error:
                self._send_json({"ok": False, "error": access_error}, status=403)
                return None
            return session

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
            auth = self.headers.get("Authorization", "")
            token = self.headers.get("X-Admin-Token", "") or self.headers.get("X-Market-Token", "")
            trusted_member = (
                sso_config.trusted_members_can_edit
                and sso_config.membership_restricted
                and flight_session_has_member_access(sso_config, self._flight_session())
            )
            if market_write_access_allowed(
                is_loopback=request_is_loopback(self),
                public_hosting_mode=public_hosting_mode,
                admin_token=admin_token,
                auth_header=auth,
                token_header=token,
                trusted_member=trusted_member,
            ):
                return True
            error = (
                "Public hosting mode requires the market admin token or trusted SSO member write access."
                if public_hosting_mode
                else "Write access requires the market admin token."
            )
            self._send_json({"ok": False, "error": error}, status=403)
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

        def _send_sse_headers(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

        def _send_sse_event(self, event: str, payload: dict[str, Any]) -> None:
            body = (
                f"event: {event}\n"
                f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            ).encode("utf-8")
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                raise

        def _send_html(self, markup: str, *, status: int = 200) -> None:
            body = markup.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_static_asset(self, path: str) -> None:
            asset_path = resolve_static_asset_path(path)
            if asset_path is None:
                self.send_error(404, "Not found")
                return
            body = asset_path.read_bytes()
            content_type = STATIC_CONTENT_TYPES.get(asset_path.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
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


def render_reprocessing_ore_options() -> str:
    cache = load_reprocessing_cache()
    if not cache.available or not cache.ores:
        return '<option value="">Run scripts\\update_industry_recipe_cache.py first</option>'
    ores = sorted(cache.ores.values(), key=lambda ore: (ore.group_name.casefold(), ore.name.casefold(), ore.type_id))
    options = []
    for ore in ores:
        label = f"{ore.group_name} - {ore.name} (portion {ore.portion_size:,})"
        options.append(f'                    <option value="{ore.type_id}">{html.escape(label)}</option>')
    return "\n".join(options)


def _render_flight_attendant_dashboard() -> str:
    category_options = "\n".join(
        f'                    <option value="{html.escape(key)}">{html.escape(label)}</option>'
        for key, label in LISTING_CATEGORIES.items()
    )
    haul_market_group_options = "\n".join(
        f"""
                  <details class="haul-market-group">
                    <summary>
                      <span>{html.escape(root_name)}</span>
                      <label class="mini-check" title="Scan this whole market category">
                        <input type="checkbox" data-haul-market-group="{root_id}" data-haul-market-root="{root_id}">
                        All
                      </label>
                    </summary>
                    <div class="haul-market-children">
{chr(10).join(
    f'                      <label class="checkline compact"><input type="checkbox" data-haul-market-group="{child_id}" data-haul-market-parent="{root_id}"><span>{html.escape(child_name)}</span></label>'
    for child_id, child_name in HAUL_MARKET_GROUP_CHILDREN.get(root_id, ())
)}
                    </div>
                  </details>"""
        for root_id, root_name in HAUL_MARKET_GROUP_ROOTS
    )
    reprocessing_ore_options = render_reprocessing_ore_options()
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
    body[data-active-tab="hauling"] {
      background:
        linear-gradient(90deg, rgba(5, 9, 11, .78) 0%, rgba(5, 9, 11, .48) 44%, rgba(5, 9, 11, .1) 100%),
        linear-gradient(180deg, rgba(8, 14, 16, .18), rgba(6, 9, 12, .76) 82%),
        url("/static/corp-market/hauler-background.png") center top / cover no-repeat fixed,
        var(--bg);
    }
    body[data-active-tab="flight"] {
      background:
        linear-gradient(90deg, rgba(5, 9, 11, .78) 0%, rgba(5, 9, 11, .48) 44%, rgba(5, 9, 11, .1) 100%),
        linear-gradient(180deg, rgba(8, 14, 16, .18), rgba(6, 9, 12, .76) 82%),
        url("/static/corp-market/flight-attendant-background.png") center top / cover no-repeat fixed,
        var(--bg);
    }
    body[data-active-tab="reprocessing"] {
      background:
        radial-gradient(circle at 12% 20%, rgba(224, 168, 74, .16), transparent 26%),
        radial-gradient(circle at 86% 8%, rgba(97, 199, 217, .1), transparent 28%),
        linear-gradient(90deg, rgba(11, 15, 12, .96), rgba(9, 13, 13, .72) 48%, rgba(7, 10, 12, .96)),
        repeating-linear-gradient(90deg, rgba(224, 168, 74, .045) 0 1px, transparent 1px 54px),
        repeating-linear-gradient(0deg, rgba(97, 199, 217, .035) 0 1px, transparent 1px 54px),
        #080b0b;
      background-size: auto, auto, auto, 54px 54px, 54px 54px, auto;
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
    body[data-active-tab="reprocessing"] header {
      border-bottom: 1px solid rgba(224, 168, 74, .2);
      margin-bottom: 2px;
    }
    body[data-active-tab="reprocessing"] .brand-mark {
      color: #1c1609;
      border-color: rgba(224, 168, 74, .82);
      background:
        radial-gradient(circle at 40% 32%, rgba(255, 245, 189, .92), transparent 34%),
        linear-gradient(135deg, #d7aa53, #7d5b26);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .3), 0 8px 18px rgba(0, 0, 0, .3);
    }
    body[data-active-tab="reprocessing"] .status {
      border-color: rgba(224, 168, 74, .44);
      background:
        linear-gradient(180deg, rgba(25, 26, 21, .96), rgba(7, 10, 11, .88)),
        rgba(8, 13, 15, .9);
    }
    body[data-active-tab="reprocessing"] .tabbar {
      border-top-color: rgba(224, 168, 74, .24);
      border-bottom-color: rgba(224, 168, 74, .34);
    }
    body[data-active-tab="reprocessing"] .tabbar button {
      border-color: rgba(111, 98, 66, .58);
      background:
        linear-gradient(180deg, rgba(30, 32, 26, .92), rgba(8, 12, 13, .82));
    }
    body[data-active-tab="reprocessing"] .tabbar button[aria-selected="true"] {
      color: #17140d;
      border-color: rgba(224, 168, 74, .88);
      background:
        linear-gradient(180deg, #d6b265, #a17635);
    }
    .tab-panel[hidden] { display: none; }
    .market-grid { display: grid; grid-template-columns: minmax(0, 382px) minmax(0, 1fr); gap: 16px; min-width: 0; }
    .flight-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 16px; min-width: 0; }
    .reprocess-page {
      display: grid;
      grid-template-columns: minmax(360px, .92fr) minmax(300px, .58fr);
      grid-template-areas:
        "setup notes"
        "summary summary"
        "output output";
      gap: 14px;
      min-width: 0;
      position: relative;
    }
    .reprocess-page::before {
      content: "";
      position: absolute;
      inset: -12px 0;
      pointer-events: none;
      border: 1px solid rgba(224, 168, 74, .18);
      border-radius: 10px;
      background:
        linear-gradient(120deg, transparent 0 62%, rgba(224, 168, 74, .08) 63%, transparent 66%),
        linear-gradient(34deg, transparent 0 44%, rgba(97, 199, 217, .045) 45%, transparent 48%);
    }
    .reprocess-page > .panel {
      position: relative;
      z-index: 1;
    }
    .reprocess-setup-panel { grid-area: setup; }
    .reprocess-knowledge-panel { grid-area: notes; }
    .reprocess-summary-panel { grid-area: summary; }
    .reprocess-output-panel { grid-area: output; }
    .panel {
      background: rgba(17, 24, 25, .94);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 18px 44px rgba(0, 0, 0, .22);
      min-width: 0;
    }
    body[data-active-tab="hauling"] #tab-hauling .panel,
    body[data-active-tab="acquisition"] #tab-acquisition .panel,
    body[data-active-tab="reprocessing"] #tab-reprocessing .panel {
      background: linear-gradient(180deg, rgba(11, 18, 20, .8), rgba(7, 11, 13, .72));
      border-color: rgba(97, 199, 217, .34);
      box-shadow: 0 22px 58px rgba(0, 0, 0, .34);
      backdrop-filter: blur(3px);
    }
    body[data-active-tab="hauling"] #tab-hauling .panel:first-child,
    body[data-active-tab="acquisition"] #tab-acquisition .panel:first-child,
    body[data-active-tab="reprocessing"] #tab-reprocessing .panel:first-child {
      background: linear-gradient(180deg, rgba(11, 18, 20, .84), rgba(7, 11, 13, .78));
    }
    body[data-active-tab="hauling"] #tab-hauling input,
    body[data-active-tab="hauling"] #tab-hauling select,
    body[data-active-tab="hauling"] #tab-hauling textarea,
    body[data-active-tab="acquisition"] #tab-acquisition input,
    body[data-active-tab="acquisition"] #tab-acquisition select,
    body[data-active-tab="acquisition"] #tab-acquisition textarea,
    body[data-active-tab="reprocessing"] #tab-reprocessing input,
    body[data-active-tab="reprocessing"] #tab-reprocessing select,
    body[data-active-tab="reprocessing"] #tab-reprocessing textarea {
      background: rgba(5, 9, 11, .84);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .panel {
      border-color: rgba(169, 137, 74, .56);
      background:
        linear-gradient(90deg, rgba(224, 168, 74, .045) 1px, transparent 1px),
        linear-gradient(0deg, rgba(224, 168, 74, .035) 1px, transparent 1px),
        linear-gradient(180deg, rgba(21, 25, 19, .93), rgba(7, 11, 12, .9));
      background-size: 28px 28px, 28px 28px, auto;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .035), 0 22px 54px rgba(0, 0, 0, .38);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .panel-header {
      border-bottom: 1px solid rgba(224, 168, 74, .24);
      margin: -2px 0 13px;
      padding-bottom: 11px;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing h2 {
      color: #f4efe0;
      font-size: 19px;
      letter-spacing: .015em;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .meta,
    body[data-active-tab="reprocessing"] #tab-reprocessing .input-note {
      color: #b9c0b4;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing label {
      color: #c6cabf;
      font-weight: 750;
      letter-spacing: .025em;
      text-transform: uppercase;
      font-size: 11px;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing input,
    body[data-active-tab="reprocessing"] #tab-reprocessing select,
    body[data-active-tab="reprocessing"] #tab-reprocessing textarea {
      border-color: rgba(169, 137, 74, .42);
      background:
        linear-gradient(180deg, rgba(237, 244, 239, .035), transparent),
        rgba(5, 9, 11, .88);
      color: #f2f0e8;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing input:focus,
    body[data-active-tab="reprocessing"] #tab-reprocessing select:focus,
    body[data-active-tab="reprocessing"] #tab-reprocessing textarea:focus {
      outline-color: rgba(224, 168, 74, .3);
      border-color: rgba(224, 168, 74, .78);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing button {
      border-color: rgba(169, 137, 74, .62);
    }
    body[data-active-tab="flight"] #tab-flight .panel {
      background: linear-gradient(180deg, rgba(11, 18, 20, .8), rgba(7, 11, 13, .72));
      border-color: rgba(97, 199, 217, .34);
      box-shadow: 0 22px 58px rgba(0, 0, 0, .34);
      backdrop-filter: blur(3px);
    }
    body[data-active-tab="flight"] #tab-flight .panel:first-child {
      background: linear-gradient(180deg, rgba(11, 18, 20, .84), rgba(7, 11, 13, .78));
    }
    body[data-active-tab="flight"] #tab-flight input,
    body[data-active-tab="flight"] #tab-flight select,
    body[data-active-tab="flight"] #tab-flight textarea {
      background: rgba(5, 9, 11, .84);
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
    input[type="checkbox"] { width: auto; accent-color: var(--cyan); }
    .checkline { display: flex; align-items: flex-start; gap: 10px; min-height: 42px; }
    .checkline input { margin-top: 3px; }
    .checkline span { color: var(--muted); }
    .checkline small { display: block; color: var(--amber); font-size: 12px; line-height: 1.35; margin-top: 3px; }
    .input-note { color: var(--amber); font-size: 12px; line-height: 1.35; }
    .checkline.compact { min-height: 28px; font-size: 13px; }
    .haul-item-filter {
      border: 1px solid rgba(63, 85, 80, .68);
      background: rgba(8, 13, 15, .36);
      border-radius: 7px;
      padding: 10px;
      margin: 4px 0 10px;
    }
    .haul-filter-head { display: flex; align-items: start; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
    .haul-filter-head strong { color: var(--text); }
    .haul-market-groups {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 8px;
      margin-top: 8px;
    }
    .haul-market-group {
      border: 1px solid rgba(63, 85, 80, .52);
      border-radius: 6px;
      background: rgba(17, 24, 25, .48);
      overflow: hidden;
    }
    .haul-market-group summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      cursor: pointer;
      padding: 8px;
      color: var(--text);
      font-weight: 800;
    }
    .haul-market-children { display: grid; gap: 4px; padding: 0 8px 8px; }
    .mini-check { display: inline-flex; align-items: center; gap: 5px; color: var(--amber); font-size: 12px; font-weight: 800; }
    .mini-check input { margin: 0; }
    .scope-warning { color: var(--amber); }
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
    .sso-scope-justification {
      margin-top: 12px;
      border: 1px solid rgba(97, 199, 217, .28);
      border-radius: 7px;
      background: rgba(5, 9, 11, .42);
      padding: 10px;
    }
    .sso-scope-justification summary {
      cursor: pointer;
      color: var(--cyan);
      font-weight: 800;
      overflow-wrap: anywhere;
    }
    .sso-scope-justification p {
      margin: 9px 0;
      color: var(--muted);
      font-size: 13px;
    }
    .sso-scope-list {
      display: grid;
      gap: 8px;
      margin: 10px 0;
    }
    .sso-scope-list dt {
      color: var(--amber);
      font-family: Consolas, monospace;
      font-size: 12px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }
    .sso-scope-list dd {
      margin: 2px 0 0;
      color: var(--muted);
      font-size: 13px;
    }
    .progress-bar.is-meter span {
      width: var(--progress-percent, 0%);
      animation: none;
      transition: width .25s ease;
    }
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
    .flight-console-panel { grid-column: 1 / -1; }
    .briefing { display: grid; grid-template-columns: minmax(360px, .95fr) minmax(340px, .8fr); gap: 14px; align-items: start; }
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
    .module-stack { display: grid; gap: 9px; align-content: start; }
    .module { border: 1px solid var(--line); background: rgba(17, 24, 25, .78); border-radius: 7px; padding: 10px; }
    .module summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      cursor: pointer;
      list-style: none;
    }
    .module summary::-webkit-details-marker { display: none; }
    .module summary::after {
      content: "Collapse";
      flex: 0 0 auto;
      color: var(--muted);
      border: 1px solid rgba(63, 85, 80, .78);
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 800;
    }
    .module:not([open]) summary::after { content: "Expand"; color: var(--cyan); }
    .module[open] summary { margin-bottom: 6px; }
    .module h3 { margin-bottom: 0; }
    .module-content { display: grid; gap: 6px; min-width: 0; }
    .flight-output-module .module-content { max-height: 360px; overflow: auto; padding-right: 2px; }
    .note-module {
      border-color: rgba(224, 168, 74, .46);
      background: rgba(8, 13, 15, .5);
      margin-bottom: 12px;
    }
    .note-module .module-content { gap: 8px; }
    .note-module form { gap: 8px; }
    .note-module textarea { min-height: 62px; max-height: 126px; }
    .note-module .note-list {
      max-height: 170px;
      overflow: auto;
      margin-top: 0;
      padding-right: 2px;
    }
    .blueprint-list { display: grid; gap: 8px; margin-top: 6px; }
    .blueprint-item {
      display: grid;
      gap: 2px;
      border: 1px solid rgba(63, 85, 80, .52);
      border-radius: 6px;
      padding: 8px;
      background: rgba(8, 13, 15, .28);
    }
    .blueprint-item strong, .blueprint-item span, .blueprint-item small { overflow-wrap: anywhere; }
    .blueprint-item span { color: var(--text); }
    .blueprint-item small { color: var(--muted); line-height: 1.3; }
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
    .output-details {
      border: 1px solid rgba(63, 85, 80, .76);
      background: rgba(8, 13, 15, .32);
      border-radius: 7px;
      overflow: hidden;
    }
    .output-details summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      cursor: pointer;
      list-style: none;
      padding: 9px 11px;
      color: var(--cyan);
      font-weight: 800;
    }
    .output-details summary::-webkit-details-marker { display: none; }
    .output-details summary::after {
      content: "Collapse";
      flex: 0 0 auto;
      color: var(--muted);
      border: 1px solid rgba(63, 85, 80, .78);
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 800;
    }
    .output-details:not([open]) summary::after { content: "Expand"; color: var(--cyan); }
    .output-details[open] summary { border-bottom: 1px solid rgba(63, 85, 80, .48); }
    .output-details-body {
      display: grid;
      gap: 10px;
      padding: 10px;
      min-width: 0;
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
    .progress-copy span strong { color: var(--amber); font-size: 13px; }
    .progress-log {
      display: grid;
      gap: 6px;
      max-height: 240px;
      overflow: auto;
      margin-top: 10px;
      border: 1px solid rgba(63, 85, 80, .58);
      border-radius: 7px;
      background: rgba(8, 13, 15, .34);
      padding: 8px;
    }
    .progress-entry {
      display: grid;
      grid-template-columns: 86px 58px minmax(0, 1fr);
      gap: 8px;
      align-items: start;
      border-bottom: 1px solid rgba(63, 85, 80, .28);
      padding-bottom: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    .progress-entry:last-child { border-bottom: 0; padding-bottom: 0; }
    .progress-entry b { color: var(--cyan); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
    .progress-entry time { color: var(--amber); font-size: 12px; font-weight: 800; }
    .progress-entry span { overflow-wrap: anywhere; }
    .range-readout { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    input[type="range"] { padding: 0; accent-color: var(--cyan); }
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
    body[data-active-tab="reprocessing"] #tab-reprocessing .profit-panel {
      position: relative;
      overflow: hidden;
      background:
        linear-gradient(90deg, rgba(97, 199, 217, .05) 1px, transparent 1px),
        linear-gradient(0deg, rgba(224, 168, 74, .045) 1px, transparent 1px),
        linear-gradient(135deg, rgba(224, 168, 74, .13), rgba(97, 199, 217, .08) 42%, rgba(8, 13, 15, .96)),
        #111819;
      background-size: 34px 34px, 34px 34px, auto, auto;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-page > .panel.reprocess-setup-panel {
      border-color: rgba(224, 168, 74, .58);
      background:
        radial-gradient(circle at 82% 18%, rgba(97, 199, 217, .12), transparent 32%),
        linear-gradient(180deg, rgba(35, 36, 28, .94), rgba(9, 12, 12, .9));
      background-size: auto;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel {
      color: #2b2b22;
      border-color: rgba(224, 168, 74, .54);
      background:
        repeating-linear-gradient(0deg, rgba(88, 74, 50, .13) 0 1px, transparent 1px 25px),
        repeating-linear-gradient(90deg, rgba(88, 74, 50, .08) 0 1px, transparent 1px 25px),
        linear-gradient(180deg, #d8ceb3, #bcae8e);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel::before {
      content: "";
      position: absolute;
      top: 11px;
      right: 18px;
      width: 76px;
      height: 18px;
      border-radius: 2px;
      background: rgba(80, 69, 47, .25);
      transform: rotate(2deg);
      box-shadow: 0 1px 4px rgba(0, 0, 0, .16);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel h2,
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel .meta,
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel .charter-list li,
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel .charter-list strong {
      color: inherit;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel .panel-header {
      border-color: rgba(61, 51, 34, .24);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-knowledge-panel .charter-list li {
      border-left-color: rgba(45, 107, 52, .72);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel {
      padding: 0;
      overflow: hidden;
      border-color: rgba(224, 168, 74, .44);
      background:
        radial-gradient(circle at 92% 0%, rgba(224, 168, 74, .12), transparent 30%),
        linear-gradient(90deg, rgba(224, 168, 74, .05) 1px, transparent 1px),
        linear-gradient(0deg, rgba(97, 199, 217, .035) 1px, transparent 1px),
        linear-gradient(180deg, rgba(17, 24, 25, .97), rgba(7, 11, 13, .92)),
        rgba(8, 13, 15, .92);
      background-size: auto, 28px 28px, 28px 28px, auto, auto;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel .panel-header {
      margin: 0;
      padding: 12px 14px;
      border-bottom-color: rgba(224, 168, 74, .28);
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel .output-details {
      border: 0;
      border-radius: 0;
      background: transparent;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel .output-details summary {
      display: none;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel .output-details-body {
      display: grid;
      gap: 10px;
      padding: 12px 14px 14px;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel .profit-summary {
      min-width: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      padding: 0;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel #reprocess-location-detail {
      min-width: 0;
      color: inherit;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-summary-panel #reprocess-location-detail:empty {
      display: none;
    }
    .assay-status-ledger {
      display: grid;
      gap: 10px;
    }
    .assay-status-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .assay-status-metric {
      min-width: 0;
      min-height: 74px;
      border: 1px solid rgba(85, 111, 103, .74);
      border-radius: 7px;
      background:
        linear-gradient(135deg, rgba(224, 168, 74, .08), transparent 42%),
        rgba(5, 9, 11, .58);
      padding: 10px 11px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .035);
    }
    .assay-status-metric span,
    .assay-status-row > span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 850;
      letter-spacing: .1em;
      text-transform: uppercase;
    }
    .assay-status-metric b {
      display: block;
      color: var(--text);
      font-size: 22px;
      line-height: 1.12;
      margin-top: 5px;
      overflow-wrap: anywhere;
    }
    .assay-status-metric small {
      display: block;
      color: var(--muted);
      line-height: 1.25;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }
    .assay-status-metric.is-green b { color: var(--green); }
    .assay-status-metric.is-amber b { color: var(--amber); }
    .assay-status-metric.is-pending b { color: var(--muted); }
    .assay-status-sheet,
    .assay-status-facility-card {
      position: relative;
      display: grid;
      gap: 8px;
      color: #25281f;
      border: 1px solid rgba(224, 168, 74, .5);
      border-radius: 7px;
      padding: 13px 14px 13px 36px;
      background:
        repeating-linear-gradient(0deg, rgba(88, 74, 50, .17) 0 1px, transparent 1px 26px),
        repeating-linear-gradient(90deg, rgba(88, 74, 50, .1) 0 1px, transparent 1px 26px),
        linear-gradient(180deg, #d8ceb3, #bfb191);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .28), 0 12px 30px rgba(0, 0, 0, .24);
    }
    .assay-status-sheet::before,
    .assay-status-facility-card::before {
      content: "";
      position: absolute;
      left: 12px;
      top: 12px;
      bottom: 12px;
      width: 10px;
      border-left: 2px solid rgba(50, 39, 24, .35);
      background:
        radial-gradient(circle, rgba(35, 29, 20, .82) 0 3px, transparent 4px) 0 9px / 10px 28px repeat-y;
      opacity: .72;
    }
    .assay-status-sheet > *,
    .assay-status-facility-card > * {
      position: relative;
      z-index: 1;
    }
    .assay-status-sheet > .notebook-ribbon,
    .assay-status-facility-card > .notebook-ribbon {
      justify-self: start;
      width: fit-content;
      max-width: 100%;
    }
    .assay-status-row {
      display: grid;
      grid-template-columns: minmax(118px, .26fr) minmax(0, 1fr);
      gap: 8px 13px;
      align-items: baseline;
      border-top: 1px solid rgba(61, 51, 34, .24);
      padding-top: 7px;
      color: #25281f;
      font-size: 13px;
      line-height: 1.35;
    }
    .assay-status-row:first-of-type {
      border-top: 0;
      padding-top: 0;
    }
    .assay-status-row > span { color: #514735; }
    .assay-status-row b {
      color: #1b1d17;
      font-size: 14px;
      font-weight: 850;
      overflow-wrap: anywhere;
    }
    .assay-status-row strong { color: #1b1d17; }
    .assay-status-row small {
      grid-column: 2;
      color: #453c2c;
      font-size: 12px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .assay-status-row.is-total b { color: #2d6b34; }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-output-panel {
      padding: 14px;
      border-color: rgba(224, 168, 74, .54);
      background:
        linear-gradient(90deg, rgba(224, 168, 74, .055) 1px, transparent 1px),
        linear-gradient(0deg, rgba(97, 199, 217, .04) 1px, transparent 1px),
        linear-gradient(135deg, rgba(224, 168, 74, .12), rgba(97, 199, 217, .06) 36%, rgba(8, 13, 15, .97)),
        #111819;
      background-size: 34px 34px, 34px 34px, auto, auto;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-output-panel .output-details {
      border: 0;
      background: transparent;
      overflow: visible;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-output-panel .output-details summary {
      display: none;
    }
    body[data-active-tab="reprocessing"] #tab-reprocessing .reprocess-output-panel .output-details-body {
      padding: 0;
    }
    .reprocess-field-desk {
      display: grid;
      grid-template-columns: minmax(240px, .72fr) minmax(320px, .9fr) minmax(520px, 1.38fr);
      grid-template-areas:
        "rail rail rail"
        "sample notebook tray"
        "footer footer footer";
      gap: 13px;
      position: relative;
    }
    .reprocess-field-desk::before {
      content: "";
      position: absolute;
      inset: -18px;
      pointer-events: none;
      background:
        linear-gradient(120deg, transparent 0 42%, rgba(224, 168, 74, .08) 43%, transparent 46%),
        linear-gradient(22deg, transparent 0 58%, rgba(97, 199, 217, .05) 59%, transparent 61%);
      opacity: .8;
    }
    .ore-specimen,
    .reprocess-status-rail,
    .field-notebook,
    .mineral-tray,
    .reprocess-desk-footer {
      position: relative;
      z-index: 1;
      border: 1px solid rgba(85, 111, 103, .78);
      border-radius: 8px;
      background: rgba(8, 13, 15, .66);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .035), 0 12px 32px rgba(0, 0, 0, .24);
    }
    .ore-specimen {
      grid-area: sample;
      display: grid;
      align-content: start;
      gap: 12px;
      padding: 14px;
      border-color: rgba(224, 168, 74, .5);
      background:
        linear-gradient(180deg, rgba(224, 168, 74, .12), rgba(8, 13, 15, .72)),
        rgba(17, 24, 25, .84);
    }
    .reprocess-status-rail {
      grid-area: rail;
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      border-color: rgba(63, 85, 80, .9);
      background:
        linear-gradient(180deg, rgba(17, 24, 25, .96), rgba(7, 11, 13, .86)),
        rgba(8, 13, 15, .9);
      padding: 0;
      overflow: hidden;
    }
    .status-rail-cell {
      min-height: 70px;
      padding: 11px 14px;
      border-right: 1px solid rgba(63, 85, 80, .62);
    }
    .status-rail-cell:last-child { border-right: 0; }
    .status-rail-cell span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 850;
      letter-spacing: .1em;
      text-transform: uppercase;
    }
    .status-rail-cell b {
      display: block;
      color: var(--text);
      font-size: 17px;
      line-height: 1.18;
      margin-top: 5px;
      overflow-wrap: anywhere;
    }
    .status-rail-cell small {
      display: block;
      color: var(--muted);
      line-height: 1.25;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }
    .status-rail-cell .green { color: var(--green); }
    .assay-ledger {
      grid-area: ledger;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      padding: 10px;
    }
    .assay-cell {
      border: 1px solid rgba(63, 85, 80, .58);
      border-radius: 7px;
      background: rgba(17, 24, 25, .68);
      padding: 8px;
      min-height: 64px;
    }
    .assay-cell span,
    .bench-label {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .assay-cell b {
      display: block;
      color: var(--text);
      font-size: 15px;
      line-height: 1.2;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }
    .assay-cell small {
      display: block;
      color: var(--muted);
      line-height: 1.25;
      margin-top: 3px;
    }
    .field-notebook {
      grid-area: notebook;
      display: grid;
      gap: 10px;
      align-content: start;
      color: #1f231d;
      border-color: rgba(224, 168, 74, .52);
      padding: 14px 14px 14px 38px;
      background:
        repeating-linear-gradient(0deg, rgba(88, 74, 50, .18) 0 1px, transparent 1px 27px),
        repeating-linear-gradient(90deg, rgba(88, 74, 50, .12) 0 1px, transparent 1px 27px),
        linear-gradient(180deg, #d8ceb3, #bfb191);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .3), 0 16px 38px rgba(0, 0, 0, .32);
    }
    .field-notebook::before {
      content: "";
      position: absolute;
      left: 13px;
      top: 12px;
      bottom: 12px;
      width: 10px;
      border-left: 2px solid rgba(50, 39, 24, .35);
      background:
        radial-gradient(circle, rgba(35, 29, 20, .9) 0 3px, transparent 4px) 0 10px / 10px 30px repeat-y;
      opacity: .7;
    }
    .notebook-section {
      position: relative;
      z-index: 1;
      border-top: 1px solid rgba(61, 51, 34, .28);
      padding-top: 9px;
    }
    .notebook-section:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .notebook-ribbon {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      margin-bottom: 7px;
      padding: 4px 10px;
      border-radius: 3px;
      color: #e8e0c7;
      background: linear-gradient(180deg, #2b2c28, #141615);
      box-shadow: 0 2px 0 rgba(0, 0, 0, .28);
      font-size: 11px;
      font-weight: 900;
      letter-spacing: .12em;
      text-transform: uppercase;
    }
    .notebook-math-row,
    .notebook-value-row,
    .notebook-standing-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: baseline;
      color: #25281f;
      font-size: 13px;
      line-height: 1.35;
    }
    .notebook-math-row b,
    .notebook-value-row b,
    .notebook-standing-row b { color: #1b1d17; }
    .notebook-math-row.is-total,
    .notebook-value-row.is-total {
      margin-top: 5px;
      padding-top: 5px;
      border-top: 1px solid rgba(61, 51, 34, .32);
      color: #2d6b34;
      font-weight: 900;
    }
    .notebook-fee-card {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 28px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      border: 1px solid rgba(61, 51, 34, .22);
      border-radius: 4px;
      background: rgba(255, 248, 224, .24);
      padding: 9px;
    }
    .notebook-fee-card div { display: grid; gap: 2px; }
    .notebook-fee-card span { color: #514735; font-size: 11px; font-weight: 800; text-transform: uppercase; }
    .notebook-fee-card b { color: #1b1d17; font-size: 18px; }
    .notebook-fee-card .fee-arrow { color: #2d6b34; font-size: 22px; text-align: center; }
    .notebook-value-grid { display: grid; gap: 4px; }
    .notebook-note {
      color: #453c2c;
      font-size: 12px;
      line-height: 1.35;
    }
    .mineral-tray {
      grid-area: tray;
      padding: 13px;
      background:
        linear-gradient(180deg, rgba(14, 22, 22, .9), rgba(6, 10, 12, .72)),
        rgba(17, 24, 25, .86);
    }
    .tray-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }
    .tray-heading strong { color: var(--text); font-size: 17px; }
    .tray-heading span { color: var(--amber); font-size: 12px; font-weight: 800; }
    .minerals-total {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      margin-top: 12px;
      border: 1px solid rgba(224, 168, 74, .4);
      border-radius: 6px;
      background: rgba(5, 9, 11, .48);
      padding: 10px 12px;
    }
    .minerals-total span { color: var(--muted); }
    .minerals-total b { color: var(--amber); font-size: 18px; }
    .mineral-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
      gap: 10px;
    }
    .mineral-card {
      --sample-accent: var(--cyan);
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
      position: relative;
      min-width: 0;
      border: 1px solid color-mix(in srgb, var(--sample-accent) 42%, rgba(63, 85, 80, .82));
      border-radius: 8px;
      background:
        linear-gradient(135deg, color-mix(in srgb, var(--sample-accent) 14%, transparent), transparent 48%),
        rgba(8, 13, 15, .74);
      padding: 12px;
      overflow: hidden;
    }
    .mineral-card.is-empty {
      border-style: dashed;
      opacity: .78;
      min-height: 118px;
      align-items: center;
    }
    .mineral-card.is-empty .specimen-image-frame {
      opacity: .58;
    }
    .mineral-card.is-empty strong {
      color: var(--muted);
    }
    .mineral-card::before,
    .mineral-card::after {
      content: "";
      position: absolute;
      width: 18px;
      height: 18px;
      border-color: color-mix(in srgb, var(--sample-accent) 72%, var(--amber));
      opacity: .7;
    }
    .mineral-card::before { top: 6px; left: 6px; border-top: 2px solid; border-left: 2px solid; }
    .mineral-card::after { right: 6px; bottom: 6px; border-right: 2px solid; border-bottom: 2px solid; }
    .specimen-image-frame {
      --sample-accent: var(--cyan);
      display: grid;
      place-items: center;
      position: relative;
      border: 1px solid color-mix(in srgb, var(--sample-accent) 54%, rgba(63, 85, 80, .82));
      border-radius: 8px;
      background:
        radial-gradient(circle at 45% 34%, color-mix(in srgb, var(--sample-accent) 18%, transparent), transparent 42%),
        linear-gradient(180deg, rgba(237, 244, 239, .07), rgba(5, 9, 11, .42));
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .025);
      overflow: hidden;
      flex: 0 0 auto;
    }
    .specimen-image-frame::after {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(90deg, rgba(237, 244, 239, .22), transparent 8px, transparent calc(100% - 8px), rgba(237, 244, 239, .16)),
        linear-gradient(180deg, rgba(237, 244, 239, .18), transparent 10px, transparent calc(100% - 10px), rgba(5, 9, 11, .25));
      mix-blend-mode: screen;
      opacity: .18;
    }
    .specimen-image-frame::before {
      content: "";
      position: absolute;
      inset: 7px;
      border: 1px dashed rgba(237, 244, 239, .12);
      border-radius: 6px;
    }
    .specimen-image-frame.ore-image {
      width: 100%;
      min-height: 162px;
    }
    .specimen-image-frame.mineral-image {
      width: 100%;
      height: 118px;
    }
    .specimen-image-frame img {
      position: relative;
      z-index: 1;
      width: 66%;
      height: 66%;
      object-fit: contain;
      object-position: center center;
      transform: translateY(-2%);
      filter: drop-shadow(0 10px 13px rgba(0, 0, 0, .45)) saturate(1.12) contrast(1.06);
    }
    .specimen-image-frame.ore-image img {
      width: min(132px, 70%);
      height: min(132px, 70%);
      transform: translateY(-1%);
    }
    .specimen-image-frame.mineral-image img {
      width: 58%;
      height: 58%;
      transform: translateY(-5%);
    }
    .specimen-image-frame.image-missing::after {
      content: "No image";
      position: relative;
      inset: auto;
      background: none;
      mix-blend-mode: normal;
      opacity: 1;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      z-index: 1;
    }
    .ore-copy { display: grid; gap: 5px; min-width: 0; }
    .ore-copy strong { color: var(--text); font-size: 20px; line-height: 1.15; overflow-wrap: anywhere; }
    .ore-copy p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.35; }
    .ore-readouts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
    .ore-readouts .assay-cell { min-height: 54px; padding: 7px; }
    .mineral-copy {
      display: grid;
      gap: 7px;
      min-width: 0;
    }
    .mineral-card-head { display: flex; align-items: start; justify-content: space-between; gap: 8px; }
    .mineral-card-head strong {
      color: var(--text);
      font-size: 16px;
      line-height: 1.18;
      overflow-wrap: anywhere;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .sample-qty {
      border: 1px solid color-mix(in srgb, var(--sample-accent) 55%, rgba(63, 85, 80, .8));
      background: color-mix(in srgb, var(--sample-accent) 16%, rgba(8, 13, 15, .74));
      color: var(--text);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
    }
    .mineral-card .sample-qty {
      justify-self: end;
      color: #d7ffd6;
      font-size: 17px;
      border-color: color-mix(in srgb, var(--sample-accent) 60%, rgba(63, 85, 80, .8));
      background: color-mix(in srgb, var(--sample-accent) 20%, rgba(8, 13, 15, .86));
    }
    .sample-ledger {
      display: grid;
      grid-template-columns: 1fr;
      gap: 6px;
    }
    .sample-ledger .decision-metric {
      min-height: 50px;
      padding: 7px;
      background: rgba(17, 24, 25, .56);
    }
    .sample-ledger .decision-metric {
      display: grid;
      grid-template-columns: minmax(0, .85fr) minmax(0, 1fr);
      gap: 8px;
      align-items: baseline;
    }
    .sample-ledger .decision-metric span { color: color-mix(in srgb, var(--sample-accent) 62%, var(--muted)); }
    .sample-ledger .decision-metric b { font-size: 13px; text-align: right; }
    .sample-ledger .decision-metric small { grid-column: 1 / -1; }
    .sample-flow {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
    }
    .sample-flow span {
      border: 1px solid rgba(63, 85, 80, .5);
      border-radius: 999px;
      padding: 2px 7px;
      background: rgba(5, 9, 11, .36);
    }
    .reprocess-notes {
      position: relative;
      z-index: 1;
      margin-bottom: 10px;
      border: 1px solid rgba(224, 168, 74, .34);
      border-radius: 7px;
      background: rgba(224, 168, 74, .08);
      padding: 9px 10px;
    }
    .reprocess-desk-footer {
      grid-area: footer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      padding: 10px 12px;
      background: rgba(8, 13, 15, .62);
    }
    .reprocess-desk-footer span {
      color: var(--muted);
      font-size: 12px;
    }
    .reprocess-desk-footer b { color: var(--text); }
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
    .note-module .row { grid-template-columns: minmax(0, 1fr) minmax(112px, .5fr); }
    .note-module .note-form textarea { min-height: 62px; max-height: 126px; }
    .note-module .note-list { max-height: 170px; overflow: auto; margin-top: 0; padding-right: 2px; }
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
      .reprocess-page {
        grid-template-columns: 1fr;
        grid-template-areas:
          "setup"
          "notes"
          "summary"
          "output";
      }
      .ops-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .profit-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .reprocess-field-desk {
        grid-template-columns: 1fr;
        grid-template-areas:
          "rail"
          "sample"
          "notebook"
          "tray"
          "footer";
      }
      .reprocess-status-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .assay-status-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .status-rail-cell { border-bottom: 1px solid rgba(63, 85, 80, .5); }
    }
    @media (max-width: 720px) {
      .shell { width: auto; margin: 0 10px; }
      .reprocess-page::before { inset: -6px 0; }
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
      .reprocess-status-rail, .assay-status-strip, .ore-readouts, .sample-ledger, .mineral-grid { grid-template-columns: 1fr; }
      .assay-status-sheet, .assay-status-facility-card { padding-left: 32px; }
      .assay-status-row { grid-template-columns: 1fr; }
      .assay-status-row small { grid-column: 1; }
      .field-notebook { padding-left: 32px; }
      .notebook-fee-card { grid-template-columns: 1fr; }
      .notebook-fee-card .fee-arrow { transform: rotate(90deg); }
      .specimen-image-frame.mineral-image { height: 96px; }
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
      <button type="button" data-tab-target="acquisition" aria-selected="false">Acquisition Planner</button>
      <button type="button" data-tab-target="trade-pnl" aria-selected="false">Trade P&amp;L</button>
      <button type="button" data-tab-target="planetary" aria-selected="false">Planetary Industry</button>
      <button type="button" data-tab-target="reprocessing" aria-selected="false">Reprocessing</button>
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
          <section class="panel flight-console-panel">
            <div class="panel-header">
              <div>
                <h2>Flight Attendant</h2>
                <div class="meta">A lore-friendly briefing console for local notes, future read-only ESI context, and human decisions.</div>
              </div>
              <span class="pill reserved">Preview</span>
            </div>
            <details class="module note-module">
              <summary><h3 class="warning">Captain's Notes</h3></summary>
              <div class="module-content">
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
                    <textarea name="note" placeholder="Fuel cache, doctrine hulls, avoid undock"></textarea>
                  </label>
                  <button type="submit">Save Local Note</button>
                </form>
                <div id="flight-notes" class="note-list"></div>
              </div>
            </details>
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
                <details class="sso-scope-justification" open>
                  <summary>Why This App Requests ESI Scopes</summary>
                  <p>Corp Market Concierge uses EVE Online SSO so pilots can choose exactly what character data they share. The app only requests <strong>read-only</strong> ESI scopes. It cannot buy, sell, contract, move assets, send mail, place market orders, control your ship, or perform any in-game action for you.</p>
                  <p>We use these scopes to make trade, hauling, and industry recommendations more useful:</p>
                  <dl class="sso-scope-list">
                    <div>
                      <dt>esi-location.read_location.v1</dt>
                      <dd>Used to find your current system so routes can start from where you actually are.</dd>
                    </div>
                    <div>
                      <dt>esi-assets.read_assets.v1</dt>
                      <dd>Used to see what materials/items you already own, so blueprint and hauling suggestions can account for stock you already have.</dd>
                    </div>
                    <div>
                      <dt>esi-characters.read_blueprints.v1</dt>
                      <dd>Used to identify owned blueprints and recommend profitable production options.</dd>
                    </div>
                    <div>
                      <dt>esi-skills.read_skills.v1</dt>
                      <dd>Used for industry and market math, such as tax/efficiency assumptions where relevant.</dd>
                    </div>
                    <div>
                      <dt>esi-characters.read_standings.v1</dt>
                      <dd>Used for character context and future access/standing-aware recommendations. This is read-only.</dd>
                    </div>
                    <div>
                      <dt>esi-clones.read_implants.v1</dt>
                      <dd>Used for future skill/implant-aware calculations where implants affect planning assumptions.</dd>
                    </div>
                    <div>
                      <dt>esi-universe.read_structures.v1</dt>
                      <dd>Used to resolve private/upwell structure names when ESI returns a structure ID instead of a normal station.</dd>
                    </div>
                    <div>
                      <dt>esi-wallet.read_character_wallet.v1</dt>
                      <dd>Used for wallet-aware affordability planning, so the app can avoid recommending purchases beyond available ISK. It is read-only and does not expose wallet transactions unless we request a separate transaction scope, which we do not.</dd>
                    </div>
                  </dl>
                  <p>Public market prices and public market orders come from public ESI data. The app does not need market order write access, contract write access, mail access, or any gameplay-control permissions. Users can revoke access any time from EVE Online's authorized applications page.</p>
                </details>
                <div id="flight-esi-message" class="meta"></div>
              </div>
              <div class="module-stack">
                <details class="module flight-output-module" open>
                  <summary><h3 class="signal">Blueprint Library</h3></summary>
                  <div class="module-content">
                    <div id="flight-blueprint-summary" class="meta">Connect ESI to scan owned blueprints.</div>
                    <div id="flight-blueprint-top" class="meta"></div>
                  </div>
                </details>
                <details class="module flight-output-module" open>
                  <summary><h3 class="warning">Materials And Assets</h3></summary>
                  <div class="module-content">
                    <div id="flight-asset-summary" class="meta">Connect ESI to scan owned asset stacks.</div>
                    <div id="flight-asset-top" class="meta"></div>
                  </div>
                </details>
                <details class="module flight-output-module" open>
                  <summary><h3 class="signal">Nearby Systems</h3></summary>
                  <div class="module-content">
                    <label>Max jumps
                      <input id="flight-max-jumps" type="number" min="0" max="25" step="1" value="5">
                    </label>
                    <div id="flight-route-summary" class="meta">Connect ESI to calculate nearby systems.</div>
                    <div id="flight-route-top" class="meta"></div>
                  </div>
                </details>
                <details class="module flight-output-module" open>
                  <summary><h3 class="warning">Buyer Orders</h3></summary>
                  <div class="module-content">
                    <button id="flight-buyer-scan" class="ghost" type="button">Scan Buyers</button>
                    <div id="flight-buyer-summary" class="meta">Connect ESI to scan nearby public buy orders.</div>
                    <div id="flight-buyer-progress-log" class="progress-log" hidden></div>
                    <div id="flight-buyer-top" class="meta"></div>
                  </div>
                </details>
                <details class="module flight-output-module" open>
                  <summary><h3 class="signal">Recipe Cache</h3></summary>
                  <div class="module-content">
                    <div id="flight-recipe-summary" class="meta">Connect ESI to compare owned blueprints with static recipes.</div>
                    <div id="flight-buildability-top" class="meta"></div>
                  </div>
                </details>
                <details class="module flight-output-module" open>
                  <summary><h3 class="danger">Pilot Still Acts</h3></summary>
                  <div class="module-content">
                    <div id="flight-industry-note" class="meta">No warps, orders, contracts, clicks, or client input are performed by this page.</div>
                  </div>
                </details>
              </div>
            </div>
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
            <details class="output-details" open>
              <summary>Profitability Results</summary>
              <div class="output-details-body">
                <div id="flight-profit-top" class="decision-output"></div>
              </div>
            </details>
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
                <div class="meta">Live ESI location or chosen start system to a hub, with pickup systems on or near that route.</div>
              </div>
              <span class="pill reserved">Manual Hauling</span>
            </div>
            <form id="haul-route-form" class="note-form">
              <div class="row">
                <label>Start system
                  <input id="haul-origin" name="origin_name" autocomplete="off" value="" placeholder="Current ESI location">
                  <small class="input-note">Leave blank to start from your live ESI system.</small>
                </label>
                <label>Destination
                  <input id="haul-destination" name="destination" autocomplete="off" value="Jita" placeholder="Jita, Hek, Rens, Dihra">
                </label>
              </div>
              <div class="row">
                <label>Cargo m3
                  <input id="haul-cargo-m3" name="cargo_m3" type="number" min="1" max="10000000" step="any" inputmode="decimal" value="10000">
                </label>
                <label>Route preference
                  <select id="haul-route-preference" name="route_preference">
                    <option value="safer" selected>Prefer safer</option>
                    <option value="shorter">Prefer shorter</option>
                    <option value="less_secure">Prefer less secure</option>
                  </select>
                </label>
                <label class="checkline">
                  <input id="haul-avoid-pod-kills" name="avoid_recent_pod_kills" type="checkbox">
                  <span>
                    Avoid recent pod kills
                    <small>Warning: if no route can avoid recent pod kills, the scan falls back to the shortest route.</small>
                  </span>
                </label>
              </div>
              <div class="row">
                <label>Profit to allow detour
                  <input id="haul-min-margin" name="min_detour_margin_percent" type="range" min="0" max="100" step="1" value="10">
                  <span class="range-readout"><span class="meta">Minimum after-tax margin</span><strong id="haul-min-margin-value">10%</strong></span>
                </label>
                <label>Pickup detour jumps
                  <input id="haul-detour-jumps" name="detour_jumps" type="number" min="0" max="5" step="1" value="1">
                </label>
              </div>
              <div class="haul-item-filter" aria-label="Hauling item search filter">
                <div class="haul-filter-head">
                  <strong>Items to search</strong>
                  <span class="meta scope-warning">Scanning more item types increases route calculation time.</span>
                </div>
                <label class="checkline">
                  <input id="haul-common-materials" name="common_materials" type="checkbox" checked>
                  <span>
                    Common materials
                    <small>Default: the current top 80 industry materials used by blueprint profit checks.</small>
                  </span>
                </label>
                <details>
                  <summary>Market categories</summary>
                  <div id="haul-market-groups" class="haul-market-groups">
@@HAUL_MARKET_GROUP_OPTIONS@@
                  </div>
                </details>
                <div id="haul-item-scope-summary" class="meta">Scanning common materials only.</div>
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
            <details class="output-details" open>
              <summary>Route Output</summary>
              <div class="output-details-body">
                <div id="haul-route-summary" class="profit-summary">Connect ESI to scan route hauling opportunities.</div>
                <div id="haul-route-path" class="meta"></div>
                <div id="haul-progress-log" class="progress-log" hidden></div>
              </div>
            </details>
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
            <details class="output-details" open>
              <summary>Route Opportunity Results</summary>
              <div class="output-details-body">
                <div id="haul-opportunity-summary" class="profit-summary">No route scan has run yet.</div>
                <div id="haul-opportunity-top" class="decision-output"></div>
              </div>
            </details>
          </section>
        </div>
      </section>

      <section id="tab-acquisition" class="tab-panel" data-tab-panel="acquisition" hidden>
        <div class="flight-grid">
          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Market Acquisition Planner</h2>
                <div class="meta">Public buy-order advice from current orders, market history, budget, estimated broker fee, and downstream demand.</div>
              </div>
              <span class="pill reserved">Advisory Only</span>
            </div>
            <form id="acquisition-form" class="note-form">
              <div class="row">
                <label>Buy order system
                  <input id="acq-origin" name="origin_name" autocomplete="off" value="" placeholder="Current ESI location">
                  <small class="input-note">Leave blank to use your live ESI system as the suggested placement area.</small>
                </label>
                <label>Downstream demand
                  <input id="acq-destination" name="destination" autocomplete="off" value="Jita" placeholder="Jita, Amarr, Hek, Rens">
                  <small class="input-note">The planner checks public buy orders in this destination system.</small>
                </label>
              </div>
              <div class="row">
                <label>Budget ISK
                  <input id="acq-budget" name="budget_isk" type="number" min="1" max="1000000000000" step="1000000" inputmode="decimal" value="50000000">
                </label>
                <label>Broker fee estimate
                  <input id="acq-broker-fee" name="broker_fee_percent" type="number" min="0" max="20" step="0.1" inputmode="decimal" value="3">
                  <small class="input-note">Adjust this to your character and structure. This version does not read wallet/order skills.</small>
                </label>
              </div>
              <div class="row">
                <label>Collection range
                  <input id="acq-pickup-jumps" name="pickup_jumps" type="number" min="0" max="10" step="1" value="2">
                  <small class="input-note">Wider ranges can create scattered deliveries that must be hauled manually.</small>
                </label>
                <label>Target fill window
                  <input id="acq-target-days" name="target_days" type="number" min="1" max="30" step="1" value="3">
                  <small class="input-note">History volume caps the first order size to this many days of recent volume.</small>
                </label>
                <label>Minimum margin
                  <input id="acq-min-margin" name="min_margin_percent" type="range" min="0" max="100" step="1" value="10">
                  <span class="range-readout"><span class="meta">After-fee target margin</span><strong id="acq-min-margin-value">10%</strong></span>
                </label>
              </div>
              <div class="haul-item-filter" aria-label="Acquisition item search filter">
                <div class="haul-filter-head">
                  <strong>Items to search</strong>
                  <span class="meta scope-warning">Market history is checked per item type, so broad scans take longer.</span>
                </div>
                <label class="checkline">
                  <input id="acq-common-materials" name="common_materials" type="checkbox" checked>
                  <span>
                    Common materials
                    <small>Default: practical industry inputs where buy orders can help a quartermaster stock up.</small>
                  </span>
                </label>
                <details>
                  <summary>Market categories</summary>
                  <div id="acq-market-groups" class="haul-market-groups">
@@HAUL_MARKET_GROUP_OPTIONS@@
                  </div>
                </details>
                <div id="acq-item-scope-summary" class="meta">Scanning common materials only.</div>
              </div>
              <button id="acq-scan" class="ghost" type="submit">Plan Buy Orders</button>
            </form>
            <details class="output-details" open>
              <summary>Planner Output</summary>
              <div class="output-details-body">
                <div id="acq-summary" class="profit-summary">Connect ESI to plan market acquisitions.</div>
                <div id="acq-route" class="meta"></div>
              </div>
            </details>
          </section>

          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>History Trap Signals</h2>
                <div class="meta">Market history can reveal when the apparent spread is too thin, too spiky, or too slow to trust.</div>
              </div>
            </div>
            <ul class="charter-list">
              <li><strong>Possible trap:</strong> the current top order is far away from recent history or competing buy orders already consume the safe margin.</li>
              <li><strong>Caution:</strong> recent volume or order count is thin, so the order may sit or fill too slowly.</li>
              <li><strong>Clear:</strong> history supports a small test order, not a guarantee.</li>
              <li><strong>Manual action:</strong> the planner never places orders, updates orders, creates contracts, or touches the EVE client.</li>
            </ul>
          </section>

          <section class="panel profit-panel" aria-labelledby="acq-results-title">
            <div class="panel-header">
              <div>
                <div class="profit-title">
                  <h2 id="acq-results-title">Acquisition Recommendations</h2>
                  <span class="pill reserved">Buy Order Plan</span>
                </div>
                <div class="meta">Suggested bid ceilings, range, units, and market-history warnings for public buy orders.</div>
              </div>
            </div>
            <details class="output-details" open>
              <summary>Recommendation Results</summary>
              <div class="output-details-body">
                <div id="acq-results" class="decision-output"></div>
              </div>
            </details>
          </section>
        </div>
      </section>

      <section id="tab-trade-pnl" class="tab-panel" data-tab-panel="trade-pnl" hidden>
        <div class="flight-grid">
          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Trade Profit And Loss</h2>
                <div class="meta">Recent wallet transactions matched into visible buy lots, sell lots, and wallet fee drag.</div>
              </div>
              <span class="pill reserved">Wallet History</span>
            </div>
            <form id="trade-pnl-form" class="note-form">
              <div class="row">
                <label>History window
                  <select id="trade-pnl-window-hours" name="window_hours">
                    <option value="1">1 hour</option>
                    <option value="6">6 hours</option>
                    <option value="24">24 hours</option>
                    <option value="168">7 days</option>
                    <option value="336">14 days</option>
                    <option value="720" selected>30 days</option>
                  </select>
                  <small class="input-note">Only transactions inside this window are considered.</small>
                </label>
                <label>Accounting lens
                  <select id="trade-pnl-lens" name="lens">
                    <option value="inventory" selected>Inventory mode</option>
                    <option value="realized">Realized trading P&amp;L</option>
                    <option value="cashflow">Wallet cashflow</option>
                  </select>
                  <small class="input-note">Inventory mode treats buys as stock until matched to a sell.</small>
                </label>
              </div>
              <div class="row">
                <label>Exclude from considered income
                  <input id="trade-pnl-exclude" name="exclude" autocomplete="off" placeholder="Pyerite, Tritanium, 35">
                  <small class="input-note">Excluded items stay visible, but do not change the considered result.</small>
                </label>
                <label class="checkline">
                  <input id="trade-pnl-show-matches" name="include_matches" type="checkbox">
                  <span>
                    Show matched transactions
                    <small>Expands each item with buy/sell lot matches inside the selected window.</small>
                  </span>
                </label>
              </div>
              <button id="trade-pnl-analyze" class="ghost" type="submit">Analyze Trade History</button>
            </form>
            <details class="output-details" open>
              <summary>P&L Summary</summary>
              <div class="output-details-body">
                <div id="trade-pnl-summary" class="profit-summary">Connect ESI to analyze recent wallet transactions.</div>
                <div id="trade-pnl-fees" class="meta"></div>
              </div>
            </details>
          </section>

          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Accounting Boundaries</h2>
                <div class="meta">This view is honest about the difference between expected spread and actual wallet result.</div>
              </div>
            </div>
            <ul class="charter-list">
              <li><strong>Expected:</strong> the FIFO buy-vs-sell spread before wallet fees.</li>
              <li><strong>Actual:</strong> matched spread after wallet fees that ESI can tie to market transactions.</li>
              <li><strong>Considered:</strong> the number after your lens and exclusions are applied.</li>
              <li><strong>Unmatched:</strong> sells whose buys are outside the visible wallet-history window.</li>
              <li><strong>Open stock:</strong> visible buys that have not been matched to a later sell yet.</li>
              <li><strong>Manual action:</strong> the tab never places, edits, cancels, or updates market orders.</li>
            </ul>
          </section>

          <section class="panel profit-panel" aria-labelledby="trade-pnl-results-title">
            <div class="panel-header">
              <div>
                <div class="profit-title">
                  <h2 id="trade-pnl-results-title">Item Results</h2>
                  <span class="pill reserved">Largest Impact</span>
                </div>
                <div class="meta">Profit, loss, open inventory, and unmatched sells by item type.</div>
              </div>
            </div>
            <details class="output-details" open>
              <summary>Item P&L</summary>
              <div class="output-details-body">
                <div id="trade-pnl-results" class="decision-output"></div>
              </div>
            </details>
          </section>
        </div>
      </section>

      <section id="tab-planetary" class="tab-panel" data-tab-panel="planetary" hidden>
        <div class="flight-grid">
          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Planetary Industry Planner</h2>
                <div class="meta">Hub order pricing, schematic inputs, customs transfer cost, and after-tax profit.</div>
              </div>
              <span class="pill reserved">Public Data</span>
            </div>
            <form id="planetary-form" class="note-form">
              <div class="row">
                <label>Market hub
                  <input id="planetary-hub" name="hub" autocomplete="off" value="Jita" placeholder="Jita, Amarr, Hek">
                </label>
                <label>Output tier
                  <select id="planetary-output-tier" name="output_tier">
                    <option value="P1">P1</option>
                    <option value="P2" selected>P2</option>
                    <option value="P3">P3</option>
                    <option value="P4">P4</option>
                  </select>
                </label>
                <label>Results
                  <input id="planetary-top" name="top" type="number" min="1" max="30" step="1" value="20">
                </label>
              </div>
              <div class="row">
                <label>Owner tax %
                  <input id="planetary-owner-tax" name="owner_tax_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" value="5">
                </label>
                <label>NPC tax %
                  <input id="planetary-npc-tax" name="npc_tax_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" value="10">
                </label>
                <label>Customs Code Expertise
                  <select id="planetary-cce" name="customs_code_expertise_level">
                    <option value="0" selected>0</option>
                    <option value="1">1</option>
                    <option value="2">2</option>
                    <option value="3">3</option>
                    <option value="4">4</option>
                    <option value="5">5</option>
                  </select>
                </label>
              </div>
              <div class="row">
                <label>Sales tax %
                  <input id="planetary-sales-tax" name="sales_tax_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" value="3">
                </label>
                <label>Broker fee %
                  <input id="planetary-broker-fee" name="broker_fee_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" value="0">
                </label>
              </div>
              <button id="planetary-rank" class="ghost" type="submit">Rank PI Chains</button>
            </form>
          </section>

          <section class="panel">
            <div class="panel-header">
              <div>
                <h2>Planning Rules</h2>
                <div class="meta">The planner ranks station-style PI by marketable schematics and manual tax assumptions.</div>
              </div>
            </div>
            <ul class="charter-list">
              <li><strong>Buy-side inputs:</strong> imported materials use hub public sell orders.</li>
              <li><strong>Sell-side outputs:</strong> finished commodities use hub public buy orders.</li>
              <li><strong>Customs transfer:</strong> import and export customs are subtracted before profit.</li>
              <li><strong>Manual action:</strong> the tab never edits colonies, places orders, or touches the EVE client.</li>
            </ul>
          </section>

          <section class="panel profit-panel" aria-labelledby="planetary-results-title">
            <div class="panel-header">
              <div>
                <div class="profit-title">
                  <h2 id="planetary-results-title">PI Opportunities</h2>
                  <span class="pill reserved">After Customs</span>
                </div>
                <div class="meta">Net profit per schematic cycle with customs transfer cost displayed in the math.</div>
              </div>
            </div>
            <details class="output-details" open>
              <summary>Opportunity Ranking</summary>
              <div class="output-details-body">
                <div id="planetary-summary" class="profit-summary">Choose PI settings and rank chains.</div>
                <div id="planetary-results" class="decision-output"></div>
              </div>
            </details>
          </section>
        </div>
      </section>

      <section id="tab-reprocessing" class="tab-panel" data-tab-panel="reprocessing" hidden>
        <div class="reprocess-page">
          <section class="panel reprocess-setup-panel">
            <div class="panel-header">
              <div>
                <h2>Ore Reprocessing Calculator</h2>
                <div class="meta">ESI-backed character skills, standings, implants, and location with SDE ore output tables.</div>
              </div>
              <span class="pill reserved">Advisory Only</span>
            </div>
            <form id="reprocessing-form" class="note-form">
              <div class="row">
                <label>Ore
                  <select id="reprocess-ore" name="ore_type_id">
@@REPROCESSING_ORE_OPTIONS@@
                  </select>
                </label>
                <label>Ore units
                  <input id="reprocess-quantity" name="quantity" type="number" min="1" step="1" inputmode="numeric" value="100">
                  <small class="input-note">Only full ore portions reprocess; leftovers are shown in the result.</small>
                </label>
              </div>
              <div class="row">
                <label>Reprocessing location
                  <select id="reprocess-station-select" name="reprocessing_station_id">
                    <option value="current">Use current ESI location</option>
                  </select>
                  <small id="reprocess-location-status" class="input-note">Connect ESI to rank stations with standing over 1.5.</small>
                </label>
                <label>Station list
                  <button id="reprocess-refresh-locations" class="secondary" type="button">Refresh Locations</button>
                  <small class="input-note">NPC stations above 1.5 standing include the standings-adjusted processing fee.</small>
                </label>
                <label>Sort locations
                  <select id="reprocess-station-sort" name="station_sort">
                    <option value="net_yield">Best net yield</option>
                    <option value="processing_fee">Lowest processing fee</option>
                    <option value="standing">Highest standing</option>
                  </select>
                  <small class="input-note">Changes the order of the reprocessing location dropdown.</small>
                </label>
              </div>
              <details class="output-details">
                <summary>Manual Overrides</summary>
                <div class="output-details-body">
                  <div class="row">
                    <label>Facility yield override
                      <input id="reprocess-facility-yield" name="facility_yield_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" placeholder="Auto or selected station">
                      <small class="input-note">Leave blank to use the selected/current station value.</small>
                    </label>
                    <label>Processing fee override
                      <input id="reprocess-station-tax" name="station_tax_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" placeholder="Auto">
                      <small class="input-note">Leave blank to estimate the NPC processing fee from ESI standings and station owner.</small>
                    </label>
                  </div>
                  <div class="row">
                    <label>Implant override
                      <input id="reprocess-implant-bonus" name="implant_bonus_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" placeholder="Auto">
                      <small class="input-note">Leave blank to read known RX-801, RX-802, or RX-804 implants from ESI.</small>
                    </label>
                    <label>Structure bonus override
                      <input id="reprocess-structure-bonus" name="structure_bonus_percent" type="number" min="0" max="100" step="0.1" inputmode="decimal" value="0">
                      <small class="input-note">Use this for Upwell rigs/security/structure bonuses ESI cannot expose here.</small>
                    </label>
                  </div>
                </div>
              </details>
              <button id="reprocess-calculate" class="ghost" type="submit">Calculate Reprocessing</button>
            </form>
          </section>

          <section class="panel reprocess-knowledge-panel">
            <div class="panel-header">
              <div>
                <h2>What ESI Knows</h2>
                <div class="meta">The calculator uses only official API/static data plus local overrides.</div>
              </div>
            </div>
            <ul class="charter-list">
              <li><strong>Automatic:</strong> current system/station, trained skill levels, standings, known reprocessing implants, and NPC station base yield/tax.</li>
              <li><strong>Manual:</strong> Upwell rig, service, security, and owner tax settings when ESI does not expose them.</li>
              <li><strong>Static data:</strong> ore portions and mineral outputs come from CCP's SDE cache.</li>
              <li><strong>No client control:</strong> this page never starts a reprocess job or touches the EVE client.</li>
            </ul>
          </section>

          <section class="panel reprocess-summary-panel">
            <div class="panel-header">
              <div>
                <h2>Assay Status</h2>
                <div class="meta">Current inputs, selected facility, and value deltas.</div>
              </div>
            </div>
            <details class="output-details" open>
              <summary>Calculation Summary</summary>
              <div class="output-details-body">
                <div id="reprocess-summary" class="profit-summary">Connect ESI, choose ore, and enter the amount to calculate.</div>
                <div id="reprocess-location-detail" class="meta"></div>
              </div>
            </details>
          </section>

          <section class="panel profit-panel reprocess-output-panel" aria-labelledby="reprocess-results-title">
            <div class="panel-header">
              <div>
                <div class="profit-title">
                  <h2 id="reprocess-results-title">Mineral Output</h2>
                  <span class="pill reserved">Net After Tax</span>
                </div>
                <div class="meta">Gross output, estimated station take, and final material stacks.</div>
              </div>
            </div>
            <details class="output-details" open>
              <summary>Output Materials</summary>
              <div class="output-details-body">
                <div id="reprocess-results" class="decision-output"></div>
              </div>
            </details>
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
    const flightBuyerProgressLog = document.querySelector("#flight-buyer-progress-log");
    const flightBuyerTop = document.querySelector("#flight-buyer-top");
    const flightProfitScanButton = document.querySelector("#flight-profit-scan");
    const flightProfitSummary = document.querySelector("#flight-profit-summary");
    const flightProfitFilters = document.querySelector("#flight-profit-filters");
    const flightProfitTop = document.querySelector("#flight-profit-top");
    const haulRouteForm = document.querySelector("#haul-route-form");
    const haulOrigin = document.querySelector("#haul-origin");
    const haulDestination = document.querySelector("#haul-destination");
    const haulCargoM3 = document.querySelector("#haul-cargo-m3");
    const haulRoutePreference = document.querySelector("#haul-route-preference");
    const haulAvoidPodKills = document.querySelector("#haul-avoid-pod-kills");
    const haulDetourJumps = document.querySelector("#haul-detour-jumps");
    const haulMinMargin = document.querySelector("#haul-min-margin");
    const haulMinMarginValue = document.querySelector("#haul-min-margin-value");
    const haulCommonMaterials = document.querySelector("#haul-common-materials");
    const haulMarketGroups = document.querySelector("#haul-market-groups");
    const haulMarketGroupInputs = Array.from(haulMarketGroups.querySelectorAll("input[data-haul-market-group]"));
    const haulItemScopeSummary = document.querySelector("#haul-item-scope-summary");
    const haulHubButtons = document.querySelector("#haul-hub-buttons");
    const haulScanButton = document.querySelector("#haul-scan");
    const haulRouteSummary = document.querySelector("#haul-route-summary");
    const haulRoutePath = document.querySelector("#haul-route-path");
    const haulProgressLog = document.querySelector("#haul-progress-log");
    const haulOpportunitySummary = document.querySelector("#haul-opportunity-summary");
    const haulOpportunityTop = document.querySelector("#haul-opportunity-top");
    const acquisitionForm = document.querySelector("#acquisition-form");
    const acqOrigin = document.querySelector("#acq-origin");
    const acqDestination = document.querySelector("#acq-destination");
    const acqBudget = document.querySelector("#acq-budget");
    const acqBrokerFee = document.querySelector("#acq-broker-fee");
    const acqPickupJumps = document.querySelector("#acq-pickup-jumps");
    const acqTargetDays = document.querySelector("#acq-target-days");
    const acqMinMargin = document.querySelector("#acq-min-margin");
    const acqMinMarginValue = document.querySelector("#acq-min-margin-value");
    const acqCommonMaterials = document.querySelector("#acq-common-materials");
    const acqMarketGroups = document.querySelector("#acq-market-groups");
    const acqMarketGroupInputs = Array.from(acqMarketGroups.querySelectorAll("input[data-haul-market-group]"));
    const acqItemScopeSummary = document.querySelector("#acq-item-scope-summary");
    const acqScanButton = document.querySelector("#acq-scan");
    const acqSummary = document.querySelector("#acq-summary");
    const acqRoute = document.querySelector("#acq-route");
    const acqResults = document.querySelector("#acq-results");
    const tradePnlForm = document.querySelector("#trade-pnl-form");
    const tradePnlWindowHours = document.querySelector("#trade-pnl-window-hours");
    const tradePnlLens = document.querySelector("#trade-pnl-lens");
    const tradePnlExclude = document.querySelector("#trade-pnl-exclude");
    const tradePnlShowMatches = document.querySelector("#trade-pnl-show-matches");
    const tradePnlAnalyzeButton = document.querySelector("#trade-pnl-analyze");
    const tradePnlSummary = document.querySelector("#trade-pnl-summary");
    const tradePnlFees = document.querySelector("#trade-pnl-fees");
    const tradePnlResults = document.querySelector("#trade-pnl-results");
    const planetaryForm = document.querySelector("#planetary-form");
    const planetaryHub = document.querySelector("#planetary-hub");
    const planetaryOutputTier = document.querySelector("#planetary-output-tier");
    const planetaryTop = document.querySelector("#planetary-top");
    const planetaryOwnerTax = document.querySelector("#planetary-owner-tax");
    const planetaryNpcTax = document.querySelector("#planetary-npc-tax");
    const planetaryCce = document.querySelector("#planetary-cce");
    const planetarySalesTax = document.querySelector("#planetary-sales-tax");
    const planetaryBrokerFee = document.querySelector("#planetary-broker-fee");
    const planetaryRankButton = document.querySelector("#planetary-rank");
    const planetarySummary = document.querySelector("#planetary-summary");
    const planetaryResults = document.querySelector("#planetary-results");
    const reprocessingForm = document.querySelector("#reprocessing-form");
    const reprocessOre = document.querySelector("#reprocess-ore");
    const reprocessQuantity = document.querySelector("#reprocess-quantity");
    const reprocessStationSelect = document.querySelector("#reprocess-station-select");
    const reprocessLocationStatus = document.querySelector("#reprocess-location-status");
    const reprocessRefreshLocations = document.querySelector("#reprocess-refresh-locations");
    const reprocessStationSort = document.querySelector("#reprocess-station-sort");
    const reprocessFacilityYield = document.querySelector("#reprocess-facility-yield");
    const reprocessStationTax = document.querySelector("#reprocess-station-tax");
    const reprocessImplantBonus = document.querySelector("#reprocess-implant-bonus");
    const reprocessStructureBonus = document.querySelector("#reprocess-structure-bonus");
    const reprocessCalculateButton = document.querySelector("#reprocess-calculate");
    const reprocessSummary = document.querySelector("#reprocess-summary");
    const reprocessLocationDetail = document.querySelector("#reprocess-location-detail");
    const reprocessResults = document.querySelector("#reprocess-results");
    const flightRecipeSummary = document.querySelector("#flight-recipe-summary");
    const flightBuildabilityTop = document.querySelector("#flight-buildability-top");
    const flightIndustryNote = document.querySelector("#flight-industry-note");
    const notesKey = "eve-flight-attendant-notes-v1";
    const jumpsKey = "eve-flight-attendant-max-jumps-v1";
    const haulOriginKey = "eve-flight-haul-origin-v1";
    const haulDestinationKey = "eve-flight-haul-destination-v1";
    const haulCargoKey = "eve-flight-haul-cargo-m3-v1";
    const haulDetourKey = "eve-flight-haul-detour-jumps-v1";
    const haulMinMarginKey = "eve-flight-haul-min-margin-v1";
    const haulRoutePreferenceKey = "eve-flight-haul-route-preference-v1";
    const haulAvoidPodKillsKey = "eve-flight-haul-avoid-pod-kills-v1";
    const haulCommonMaterialsKey = "eve-flight-haul-common-materials-v1";
    const haulMarketGroupIdsKey = "eve-flight-haul-market-group-ids-v1";
    const acqOriginKey = "eve-flight-acq-origin-v1";
    const acqDestinationKey = "eve-flight-acq-destination-v1";
    const acqBudgetKey = "eve-flight-acq-budget-v1";
    const acqBrokerFeeKey = "eve-flight-acq-broker-fee-v1";
    const acqPickupJumpsKey = "eve-flight-acq-pickup-jumps-v1";
    const acqTargetDaysKey = "eve-flight-acq-target-days-v1";
    const acqMinMarginKey = "eve-flight-acq-min-margin-v1";
    const acqCommonMaterialsKey = "eve-flight-acq-common-materials-v1";
    const acqMarketGroupIdsKey = "eve-flight-acq-market-group-ids-v1";
    const tradePnlWindowHoursKey = "eve-flight-trade-pnl-window-hours-v1";
    const tradePnlLensKey = "eve-flight-trade-pnl-lens-v1";
    const tradePnlExcludeKey = "eve-flight-trade-pnl-exclude-v1";
    const tradePnlShowMatchesKey = "eve-flight-trade-pnl-show-matches-v1";
    const planetaryHubKey = "eve-flight-planetary-hub-v1";
    const planetaryTierKey = "eve-flight-planetary-tier-v1";
    const planetaryTopKey = "eve-flight-planetary-top-v1";
    const planetaryOwnerTaxKey = "eve-flight-planetary-owner-tax-v1";
    const planetaryNpcTaxKey = "eve-flight-planetary-npc-tax-v1";
    const planetaryCceKey = "eve-flight-planetary-cce-v1";
    const planetarySalesTaxKey = "eve-flight-planetary-sales-tax-v1";
    const planetaryBrokerFeeKey = "eve-flight-planetary-broker-fee-v1";
    const reprocessOreKey = "eve-flight-reprocess-ore-type-v1";
    const reprocessQuantityKey = "eve-flight-reprocess-quantity-v1";
    const reprocessLocationKey = "eve-flight-reprocess-location-v1";
    const reprocessStationSortKey = "eve-flight-reprocess-station-sort-v1";
    const reprocessFacilityYieldKey = "eve-flight-reprocess-facility-yield-v1";
    const reprocessStationTaxKey = "eve-flight-reprocess-station-tax-v1";
    const reprocessImplantBonusKey = "eve-flight-reprocess-implant-bonus-v1";
    const reprocessStructureBonusKey = "eve-flight-reprocess-structure-bonus-v1";
    const validTabs = new Set(["market", "flight", "hauling", "acquisition", "trade-pnl", "planetary", "reprocessing"]);
    let filterType = "";
    let includeClosed = false;
    let flightProfitFilter = "all";
    let flightProfitProducts = [];
    let buyerEventSource = null;
    let buyerScanFinished = false;
    let buyerProgressTimer = null;
    let buyerProgressStartedAt = 0;
    let buyerProgressMaxJumps = 0;
    let buyerProgressPercent = 0;
    let buyerProgressMessage = "";
    let flightProfitProgressTimer = null;
    let haulEventSource = null;
    let haulScanFinished = false;
    let haulProgressTimer = null;
    let haulProgressStartedAt = 0;

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
      document.body.dataset.activeTab = targetTab;
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

    function formatDuration(seconds) {
      const total = Math.max(0, Math.round(Number(seconds || 0)));
      if (!total) return "unknown";
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      if (hours) return `${hours}h ${minutes}m`;
      if (minutes) return `${minutes}m ${secs}s`;
      return `${secs}s`;
    }

    function formatElapsedDuration(seconds) {
      const total = Math.max(0, Math.floor(Number(seconds || 0)));
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      if (hours) return `${hours}h ${minutes}m ${secs}s`;
      if (minutes) return `${minutes}m ${secs}s`;
      return `${secs}s`;
    }

    function formatIskPerHour(value) {
      if (value == null) return "unknown";
      return `${formatSignedIsk(value)}/h`;
    }

    function renderJitaRawValue(product) {
      const systemName = product.jita_raw_system_name || "Jita";
      if (product.jita_raw_material_value != null) {
        return `${formatIsk(product.jita_raw_material_value)} in ${systemName}`;
      }
      if (product.jita_partial_raw_material_value != null) {
        return `${formatIsk(product.jita_partial_raw_material_value)} partial in ${systemName}`;
      }
      return `unknown in ${systemName}`;
    }

    function renderJitaRawCoverage(product) {
      const total = Number(product.jita_raw_required_material_types || product.required_material_types || 0);
      const full = Number(product.jita_raw_material_types || 0);
      const partial = Number(product.jita_partial_raw_material_types || 0);
      if (!total) return "unknown";
      if (full >= total) return `${formatNumber(full)}/${formatNumber(total)} material types fully covered`;
      if (partial > full) {
        return `${formatNumber(full)}/${formatNumber(total)} fully covered; ${formatNumber(partial)} have some Jita buy depth`;
      }
      return `${formatNumber(full)}/${formatNumber(total)} material types covered`;
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

    function clampHaulCargoM3(value) {
      const cargo = Number(value);
      if (!Number.isFinite(cargo)) return 10000;
      return Math.max(1, Math.min(10000000, cargo));
    }

    function clampHaulMinMargin(value) {
      const margin = Number(value);
      if (!Number.isFinite(margin)) return 10;
      return Math.max(0, Math.min(500, margin));
    }

    function normalizeHaulRoutePreference(value) {
      const preference = String(value || "").trim();
      if (["shorter", "safer", "less_secure"].includes(preference)) return preference;
      if (preference === "shortest") return "shorter";
      if (preference === "secure") return "safer";
      if (preference === "insecure" || preference === "lesssecure") return "less_secure";
      return "safer";
    }

    function readStoredHaulMarketGroupIds() {
      try {
        const parsed = JSON.parse(window.localStorage.getItem(haulMarketGroupIdsKey) || "[]");
        if (!Array.isArray(parsed)) return [];
        return parsed.map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0);
      } catch (_error) {
        return [];
      }
    }

    function readHaulMarketGroupIdsFromInputs() {
      return haulMarketGroupInputs
        .filter((input) => input.checked)
        .map((input) => Number(input.dataset.haulMarketGroup))
        .filter((value) => Number.isFinite(value) && value > 0);
    }

    function applyHaulMarketGroupIds(groupIds) {
      const selected = new Set((groupIds || []).map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0));
      haulMarketGroupInputs.forEach((input) => {
        input.checked = selected.has(Number(input.dataset.haulMarketGroup));
      });
    }

    function haulItemScopeLabel(settings) {
      const parts = [];
      if (settings.includeCommonMaterials) parts.push("common materials");
      const groupCount = (settings.marketGroupIds || []).length;
      if (groupCount) parts.push(`${formatNumber(groupCount)} market categor${groupCount === 1 ? "y" : "ies"}`);
      return parts.length ? parts.join(" + ") : "no item scope selected";
    }

    function haulStartLabel(settings) {
      return String(settings.originName || "").trim() || "current ESI system";
    }

    function haulRouteLabel(settings) {
      return `${haulStartLabel(settings)} to ${settings.destination || "destination"}`;
    }

    function updateHaulItemScopeSummary(settings = null) {
      const activeSettings = settings || {
        includeCommonMaterials: haulCommonMaterials.checked,
        marketGroupIds: readHaulMarketGroupIdsFromInputs(),
      };
      const scopeLabel = haulItemScopeLabel(activeSettings);
      haulItemScopeSummary.textContent = `${scopeLabel}. More item types means a longer route calculation.`;
    }

    function readHaulSettings() {
      const originName = String(window.localStorage.getItem(haulOriginKey) || haulOrigin.value || "").trim();
      const destination = String(window.localStorage.getItem(haulDestinationKey) || haulDestination.value || "Jita").trim() || "Jita";
      const cargo = Number(window.localStorage.getItem(haulCargoKey) || haulCargoM3.value || 10000);
      const detour = Number(window.localStorage.getItem(haulDetourKey) || haulDetourJumps.value || 1);
      const minMargin = Number(window.localStorage.getItem(haulMinMarginKey) || haulMinMargin.value || 10);
      const routePreference = normalizeHaulRoutePreference(window.localStorage.getItem(haulRoutePreferenceKey) || haulRoutePreference.value || "safer");
      const avoidStored = window.localStorage.getItem(haulAvoidPodKillsKey);
      const commonStored = window.localStorage.getItem(haulCommonMaterialsKey);
      return {
        originName,
        destination,
        cargoM3: clampHaulCargoM3(Number.isFinite(cargo) ? cargo : 10000),
        detourJumps: Math.max(0, Math.min(5, Math.round(Number.isFinite(detour) ? detour : 1))),
        minDetourMarginPercent: clampHaulMinMargin(Number.isFinite(minMargin) ? minMargin : 10),
        routePreference,
        avoidRecentPodKills: avoidStored == null ? haulAvoidPodKills.checked : avoidStored !== "0",
        includeCommonMaterials: commonStored == null ? haulCommonMaterials.checked : commonStored !== "0",
        marketGroupIds: readStoredHaulMarketGroupIds(),
      };
    }

    function writeHaulSettings(settings) {
      const originName = String(settings.originName || "").trim();
      const destination = String(settings.destination || "Jita").trim() || "Jita";
      const cargoM3 = clampHaulCargoM3(settings.cargoM3);
      const detourJumps = Math.max(0, Math.min(5, Math.round(Number(settings.detourJumps) || 0)));
      const minDetourMarginPercent = clampHaulMinMargin(settings.minDetourMarginPercent);
      const routePreference = normalizeHaulRoutePreference(settings.routePreference || haulRoutePreference.value || "safer");
      const avoidRecentPodKills = settings.avoidRecentPodKills == null ? haulAvoidPodKills.checked : Boolean(settings.avoidRecentPodKills);
      const includeCommonMaterials = settings.includeCommonMaterials == null ? haulCommonMaterials.checked : Boolean(settings.includeCommonMaterials);
      const marketGroupIds = Array.isArray(settings.marketGroupIds) ? settings.marketGroupIds : readHaulMarketGroupIdsFromInputs();
      haulOrigin.value = originName;
      haulDestination.value = destination;
      haulCargoM3.value = String(cargoM3);
      haulDetourJumps.value = String(detourJumps);
      haulMinMargin.value = String(minDetourMarginPercent);
      haulRoutePreference.value = routePreference;
      haulAvoidPodKills.checked = avoidRecentPodKills;
      haulCommonMaterials.checked = includeCommonMaterials;
      applyHaulMarketGroupIds(marketGroupIds);
      haulMinMarginValue.textContent = `${formatNumber(minDetourMarginPercent)}%`;
      updateHaulItemScopeSummary({includeCommonMaterials, marketGroupIds});
      window.localStorage.setItem(haulOriginKey, originName);
      window.localStorage.setItem(haulDestinationKey, destination);
      window.localStorage.setItem(haulCargoKey, String(cargoM3));
      window.localStorage.setItem(haulDetourKey, String(detourJumps));
      window.localStorage.setItem(haulMinMarginKey, String(minDetourMarginPercent));
      window.localStorage.setItem(haulRoutePreferenceKey, routePreference);
      window.localStorage.setItem(haulAvoidPodKillsKey, avoidRecentPodKills ? "1" : "0");
      window.localStorage.setItem(haulCommonMaterialsKey, includeCommonMaterials ? "1" : "0");
      window.localStorage.setItem(haulMarketGroupIdsKey, JSON.stringify(marketGroupIds));
      return {originName, destination, cargoM3, detourJumps, minDetourMarginPercent, routePreference, avoidRecentPodKills, includeCommonMaterials, marketGroupIds};
    }

    function clampAcquisitionBudget(value) {
      const budget = Number(value);
      if (!Number.isFinite(budget)) return 50000000;
      return Math.max(1, Math.min(1000000000000, budget));
    }

    function clampAcquisitionBrokerFee(value) {
      const fee = Number(value);
      if (!Number.isFinite(fee)) return 3;
      return Math.max(0, Math.min(20, fee));
    }

    function clampAcquisitionPickupJumps(value) {
      const jumps = Number(value);
      if (!Number.isFinite(jumps)) return 2;
      return Math.max(0, Math.min(10, Math.round(jumps)));
    }

    function clampAcquisitionTargetDays(value) {
      const days = Number(value);
      if (!Number.isFinite(days)) return 3;
      return Math.max(1, Math.min(30, Math.round(days)));
    }

    function readAcqMarketGroupIdsFromInputs() {
      return acqMarketGroupInputs
        .filter((input) => input.checked)
        .map((input) => Number(input.dataset.haulMarketGroup))
        .filter((value) => Number.isFinite(value) && value > 0);
    }

    function readStoredAcqMarketGroupIds() {
      try {
        const parsed = JSON.parse(window.localStorage.getItem(acqMarketGroupIdsKey) || "[]");
        if (!Array.isArray(parsed)) return [];
        return parsed.map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0);
      } catch (_error) {
        return [];
      }
    }

    function applyAcqMarketGroupIds(groupIds) {
      const selected = new Set((groupIds || []).map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0));
      acqMarketGroupInputs.forEach((input) => {
        input.checked = selected.has(Number(input.dataset.haulMarketGroup));
      });
    }

    function acquisitionItemScopeLabel(settings) {
      const parts = [];
      if (settings.includeCommonMaterials) parts.push("common materials");
      const groupCount = (settings.marketGroupIds || []).length;
      if (groupCount) parts.push(`${formatNumber(groupCount)} market categor${groupCount === 1 ? "y" : "ies"}`);
      return parts.length ? parts.join(" + ") : "no item scope selected";
    }

    function updateAcquisitionItemScopeSummary(settings = null) {
      const activeSettings = settings || {
        includeCommonMaterials: acqCommonMaterials.checked,
        marketGroupIds: readAcqMarketGroupIdsFromInputs(),
      };
      acqItemScopeSummary.textContent = `${acquisitionItemScopeLabel(activeSettings)}. Market history is checked for every scanned item type.`;
    }

    function acquisitionStartLabel(settings) {
      return String(settings.originName || "").trim() || "current ESI system";
    }

    function readAcquisitionSettings() {
      const originName = String(window.localStorage.getItem(acqOriginKey) || acqOrigin.value || "").trim();
      const destination = String(window.localStorage.getItem(acqDestinationKey) || acqDestination.value || "Jita").trim() || "Jita";
      const budget = Number(window.localStorage.getItem(acqBudgetKey) || acqBudget.value || 50000000);
      const brokerFee = Number(window.localStorage.getItem(acqBrokerFeeKey) || acqBrokerFee.value || 3);
      const pickupJumps = Number(window.localStorage.getItem(acqPickupJumpsKey) || acqPickupJumps.value || 2);
      const targetDays = Number(window.localStorage.getItem(acqTargetDaysKey) || acqTargetDays.value || 3);
      const minMargin = Number(window.localStorage.getItem(acqMinMarginKey) || acqMinMargin.value || 10);
      const commonStored = window.localStorage.getItem(acqCommonMaterialsKey);
      return {
        originName,
        destination,
        budgetIsk: clampAcquisitionBudget(Number.isFinite(budget) ? budget : 50000000),
        brokerFeePercent: clampAcquisitionBrokerFee(Number.isFinite(brokerFee) ? brokerFee : 3),
        pickupJumps: clampAcquisitionPickupJumps(Number.isFinite(pickupJumps) ? pickupJumps : 2),
        targetDays: clampAcquisitionTargetDays(Number.isFinite(targetDays) ? targetDays : 3),
        minMarginPercent: clampHaulMinMargin(Number.isFinite(minMargin) ? minMargin : 10),
        includeCommonMaterials: commonStored == null ? acqCommonMaterials.checked : commonStored !== "0",
        marketGroupIds: readStoredAcqMarketGroupIds(),
      };
    }

    function writeAcquisitionSettings(settings) {
      const originName = String(settings.originName || "").trim();
      const destination = String(settings.destination || "Jita").trim() || "Jita";
      const budgetIsk = clampAcquisitionBudget(settings.budgetIsk);
      const brokerFeePercent = clampAcquisitionBrokerFee(settings.brokerFeePercent);
      const pickupJumps = clampAcquisitionPickupJumps(settings.pickupJumps);
      const targetDays = clampAcquisitionTargetDays(settings.targetDays);
      const minMarginPercent = clampHaulMinMargin(settings.minMarginPercent);
      const includeCommonMaterials = settings.includeCommonMaterials == null ? acqCommonMaterials.checked : Boolean(settings.includeCommonMaterials);
      const marketGroupIds = Array.isArray(settings.marketGroupIds) ? settings.marketGroupIds : readAcqMarketGroupIdsFromInputs();
      acqOrigin.value = originName;
      acqDestination.value = destination;
      acqBudget.value = String(budgetIsk);
      acqBrokerFee.value = String(brokerFeePercent);
      acqPickupJumps.value = String(pickupJumps);
      acqTargetDays.value = String(targetDays);
      acqMinMargin.value = String(minMarginPercent);
      acqCommonMaterials.checked = includeCommonMaterials;
      applyAcqMarketGroupIds(marketGroupIds);
      acqMinMarginValue.textContent = `${formatNumber(minMarginPercent)}%`;
      updateAcquisitionItemScopeSummary({includeCommonMaterials, marketGroupIds});
      window.localStorage.setItem(acqOriginKey, originName);
      window.localStorage.setItem(acqDestinationKey, destination);
      window.localStorage.setItem(acqBudgetKey, String(budgetIsk));
      window.localStorage.setItem(acqBrokerFeeKey, String(brokerFeePercent));
      window.localStorage.setItem(acqPickupJumpsKey, String(pickupJumps));
      window.localStorage.setItem(acqTargetDaysKey, String(targetDays));
      window.localStorage.setItem(acqMinMarginKey, String(minMarginPercent));
      window.localStorage.setItem(acqCommonMaterialsKey, includeCommonMaterials ? "1" : "0");
      window.localStorage.setItem(acqMarketGroupIdsKey, JSON.stringify(marketGroupIds));
      return {originName, destination, budgetIsk, brokerFeePercent, pickupJumps, targetDays, minMarginPercent, includeCommonMaterials, marketGroupIds};
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
        resetMarketAcquisition("Market acquisition planner is offline.");
        resetTradePnl("Trade P&L analyzer is offline.");
        resetReprocessing("Ore reprocessing calculator is offline.");
        clearReprocessingLocations("Ore reprocessing calculator is offline.", true);
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
        resetMarketAcquisition("Configure EVE SSO before planning market acquisitions.");
        resetTradePnl("Configure EVE SSO before analyzing trade history.");
        resetReprocessing("Configure EVE SSO before calculating ore reprocessing.");
        clearReprocessingLocations("Configure EVE SSO before ranking reprocessing stations over 1.5 standing.", true);
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
        resetMarketAcquisition("Connect ESI to plan public buy orders.");
        resetTradePnl("Connect ESI to analyze recent wallet transactions.");
        resetReprocessing("Connect ESI to calculate ore reprocessing.");
        clearReprocessingLocations("Connect ESI to rank reprocessing stations over 1.5 standing.", true);
        resetFlightIndustry("Connect ESI to scan owned blueprints and materials.");
        return;
      }
      const character = data.character || {};
      const location = data.location || {};
      const membership = data.membership || {};
      flightPilotName.textContent = character.character_name || "Connected pilot";
      flightTokenStatus.textContent = `${Math.ceil((character.expires_in_seconds || 0) / 60)} min`;
      if (membership.required && membership.allowed === false) {
        flightSystemName.textContent = "Member Check Failed";
        flightLocationLine.textContent = membership.message || "This character is not allowed for this Flight Attendant.";
        flightMessage.textContent = "Sign out, then reconnect with an allowlisted corporation or alliance character.";
        resetFlightRoute("Use an allowlisted EVE character before calculating nearby systems.");
        resetFlightBuyers("Use an allowlisted EVE character before scanning buyer orders.");
        resetFlightProfitability("Use an allowlisted EVE character before ranking profitability.");
        resetFlightHauling("Use an allowlisted EVE character before scanning hauler routes.");
        resetMarketAcquisition("Use an allowlisted EVE character before planning market acquisitions.");
        resetTradePnl("Use an allowlisted EVE character before analyzing trade history.");
        resetReprocessing("Use an allowlisted EVE character before calculating ore reprocessing.");
        clearReprocessingLocations("Use an allowlisted EVE character before ranking reprocessing stations over 1.5 standing.", true);
        resetFlightIndustry("Use an allowlisted EVE character before scanning industry data.");
        return;
      }
      if (data.error) {
        flightSystemName.textContent = "ESI Error";
        flightLocationLine.textContent = data.error;
        flightMessage.textContent = "Try reconnecting ESI if the token expired.";
        resetFlightRoute("Resolve the ESI error before calculating nearby systems.");
        resetFlightBuyers("Resolve the ESI error before scanning buyer orders.");
        resetFlightProfitability("Resolve the ESI error before ranking profitability.");
        resetFlightHauling("Resolve the ESI error before scanning hauler routes.");
        resetMarketAcquisition("Resolve the ESI error before planning market acquisitions.");
        resetTradePnl("Resolve the ESI error before analyzing trade history.");
        resetReprocessing("Resolve the ESI error before calculating ore reprocessing.");
        clearReprocessingLocations("Resolve the ESI error before ranking reprocessing stations over 1.5 standing.", true);
        resetFlightIndustry("Resolve the ESI error before scanning industry data.");
        return;
      }
      flightSystemName.textContent = location.solar_system_name || "Unknown System";
      flightLocationLine.textContent = `Live ESI location ${location.updated_at || ""}`;
      haulOrigin.placeholder = location.solar_system_name ? `Current ESI: ${location.solar_system_name}` : "Current ESI location";
      if (missingRequiredScopes.length) {
        flightMessage.textContent = `${character.character_name || "Pilot"} connected, but reconnect ESI for new scopes: ${missingRequiredScopes.join(", ")}.`;
      } else {
        flightMessage.textContent = `${character.character_name || "Pilot"} connected with ${requiredScopes.length} read-only ESI scopes.`;
      }
      renderFlightRoute(data.nearby_systems || {});
      resetFlightBuyers(`Ready to scan buy orders within ${readMaxJumps()} jumps.`);
      resetFlightProfitability(`Ready to rank profitability within ${readMaxJumps()} jumps.`);
      const haulSettings = readHaulSettings();
      resetFlightHauling(`Ready to scan route hauling opportunities from ${haulStartLabel(haulSettings)} to ${haulSettings.destination}.`);
      const acqSettings = readAcquisitionSettings();
      resetMarketAcquisition(`Ready to plan buy orders from ${acquisitionStartLabel(acqSettings)} toward ${acqSettings.destination}.`);
      const tradePnlSettings = readTradePnlSettings();
      resetTradePnl(`Ready to analyze ${tradePnlWindowLabel(tradePnlSettings.windowHours)} of trade history.`);
      const reprocessSettings = readReprocessingSettings();
      resetReprocessing(`Ready to calculate ${formatNumber(reprocessSettings.quantity)} ore units.`);
      loadReprocessingLocations();
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
      closeBuyerEventSource();
      stopBuyerProgressTimer();
      flightBuyerSummary.textContent = message;
      flightBuyerProgressLog.hidden = true;
      flightBuyerProgressLog.innerHTML = "";
      flightBuyerTop.textContent = "";
      flightBuyerScanButton.disabled = false;
    }

    function closeBuyerEventSource() {
      if (buyerEventSource) {
        buyerEventSource.close();
        buyerEventSource = null;
      }
    }

    function currentBuyerElapsedSeconds() {
      if (!buyerProgressStartedAt) return 0;
      return Math.max(0, Math.floor((Date.now() - buyerProgressStartedAt) / 1000));
    }

    function stopBuyerProgressTimer() {
      const elapsedSeconds = currentBuyerElapsedSeconds();
      if (buyerProgressTimer) {
        window.clearInterval(buyerProgressTimer);
        buyerProgressTimer = null;
      }
      buyerProgressStartedAt = 0;
      return elapsedSeconds;
    }

    function renderBuyerProgressSummary() {
      const elapsed = formatElapsedDuration(currentBuyerElapsedSeconds());
      const percent = Math.max(0, Math.min(100, Math.round(Number(buyerProgressPercent || 0))));
      const meterPercent = Math.max(4, percent);
      flightBuyerSummary.innerHTML = `
        <div class="progress-status" aria-live="polite">
          <span class="progress-spinner" aria-hidden="true"></span>
          <div class="progress-copy">
            <strong>${escapeHtml(buyerProgressMessage || "Scanning nearby buyer orders")}</strong>
            <span><strong>${formatNumber(percent)}%</strong> complete &middot; within ${formatNumber(buyerProgressMaxJumps)} jumps; elapsed ${escapeHtml(elapsed)}.</span>
          </div>
        </div>
        <div class="progress-bar is-meter" aria-label="${formatNumber(percent)}% complete" style="--progress-percent: ${meterPercent}%"><span></span></div>
      `;
    }

    function startBuyerProgressTimer(maxJumps) {
      stopBuyerProgressTimer();
      buyerProgressStartedAt = Date.now();
      buyerProgressMaxJumps = maxJumps;
      buyerProgressPercent = 0;
      buyerProgressMessage = `Starting buyer scan within ${maxJumps} jumps`;
      renderBuyerProgressSummary();
      buyerProgressTimer = window.setInterval(renderBuyerProgressSummary, 1000);
    }

    async function loadFlightBuyers() {
      const maxJumps = writeMaxJumps(readMaxJumps());
      closeBuyerEventSource();
      stopBuyerProgressTimer();
      buyerScanFinished = false;
      flightBuyerScanButton.disabled = true;
      flightBuyerProgressLog.hidden = false;
      flightBuyerProgressLog.innerHTML = "";
      startBuyerProgressTimer(maxJumps);
      flightBuyerTop.innerHTML = `<div class="decision-empty">Buyer scan results will appear here when the scan finishes.</div>`;
      if (typeof EventSource === "undefined") {
        try {
          const response = await fetch(`/api/flight/buyers?max_jumps=${encodeURIComponent(maxJumps)}`);
          const data = await response.json();
          if (!data.ok) throw new Error(data.error || "Could not scan buyer orders");
          stopBuyerProgressTimer();
          renderFlightBuyers(data.buyers || {});
        } catch (error) {
          stopBuyerProgressTimer();
          flightBuyerSummary.textContent = error.message;
          flightBuyerTop.textContent = "";
        } finally {
          flightBuyerScanButton.disabled = false;
        }
        return;
      }
      try {
        buyerEventSource = new EventSource(`/api/flight/buyers/progress?max_jumps=${encodeURIComponent(maxJumps)}`);
        buyerEventSource.addEventListener("scan_start", (event) => {
          const payload = parseBuyerProgressEvent(event);
          updateBuyerProgressFromPayload(payload);
          appendBuyerProgress("Start", payload);
        });
        buyerEventSource.addEventListener("blueprints", (event) => {
          const payload = parseBuyerProgressEvent(event);
          updateBuyerProgressFromPayload(payload);
          appendBuyerProgress("Blueprints", payload);
        });
        buyerEventSource.addEventListener("scan_scope", (event) => {
          const payload = parseBuyerProgressEvent(event);
          updateBuyerProgressFromPayload(payload);
          appendBuyerProgress("Scope", payload);
        });
        buyerEventSource.addEventListener("product_start", (event) => {
          const payload = parseBuyerProgressEvent(event);
          updateBuyerProgressFromPayload(payload);
          appendBuyerProgress(`Product ${formatNumber(payload.product_index)}`, payload);
        });
        buyerEventSource.addEventListener("orders", (event) => {
          const payload = parseBuyerProgressEvent(event);
          updateBuyerProgressFromPayload(payload);
          appendBuyerProgress(`Orders ${formatNumber(payload.step)}/${formatNumber(payload.total_steps)}`, payload);
        });
        buyerEventSource.addEventListener("scan_warning", (event) => {
          const payload = parseBuyerProgressEvent(event);
          updateBuyerProgressFromPayload(payload);
          appendBuyerProgress("Warning", payload);
        });
        buyerEventSource.addEventListener("product_done", (event) => {
          const payload = parseBuyerProgressEvent(event);
          updateBuyerProgressFromPayload(payload);
          appendBuyerProgress(`Done ${formatNumber(payload.product_index)}`, payload);
        });
        buyerEventSource.addEventListener("scan_error", (event) => {
          const payload = parseBuyerProgressEvent(event);
          buyerScanFinished = true;
          const elapsedSeconds = stopBuyerProgressTimer();
          closeBuyerEventSource();
          appendBuyerProgress("Stopped", {message: payload.error || "Buyer scan failed.", elapsed_seconds: elapsedSeconds});
          flightBuyerSummary.textContent = `${payload.error || "Buyer scan failed."} Elapsed ${formatElapsedDuration(elapsedSeconds)}.`;
          flightBuyerTop.textContent = "";
          flightBuyerScanButton.disabled = false;
        });
        buyerEventSource.addEventListener("result", (event) => {
          buyerScanFinished = true;
          const data = parseBuyerProgressEvent(event);
          const elapsedSeconds = stopBuyerProgressTimer();
          buyerProgressPercent = 100;
          buyerProgressMessage = "Buyer scan complete";
          renderFlightBuyers(data.buyers || {});
          appendBuyerProgress("Done", {message: `Buyer scan complete in ${formatElapsedDuration(elapsedSeconds)}.`, elapsed_seconds: elapsedSeconds});
          closeBuyerEventSource();
          flightBuyerScanButton.disabled = false;
        });
        buyerEventSource.onerror = () => {
          if (buyerScanFinished) return;
          const elapsedSeconds = stopBuyerProgressTimer();
          closeBuyerEventSource();
          appendBuyerProgress("Stopped", {message: "Buyer scan connection closed before results arrived.", elapsed_seconds: elapsedSeconds});
          flightBuyerSummary.textContent = `Buyer scan connection closed before results arrived. Elapsed ${formatElapsedDuration(elapsedSeconds)}.`;
          flightBuyerTop.textContent = "";
          flightBuyerScanButton.disabled = false;
        };
      } catch (error) {
        closeBuyerEventSource();
        stopBuyerProgressTimer();
        flightBuyerSummary.textContent = error.message;
        flightBuyerTop.textContent = "";
        flightBuyerScanButton.disabled = false;
      }
    }

    function parseBuyerProgressEvent(event) {
      try {
        return JSON.parse(event.data || "{}");
      } catch (_error) {
        return {message: "Unreadable progress update."};
      }
    }

    function updateBuyerProgressFromPayload(payload) {
      if (payload.percent != null) {
        buyerProgressPercent = Math.max(0, Math.min(100, Number(payload.percent) || 0));
      }
      if (payload.message) {
        buyerProgressMessage = payload.message;
      }
      renderBuyerProgressSummary();
    }

    function appendBuyerProgress(label, payload) {
      const message = payload.message || "";
      if (!message) return;
      const elapsedSeconds = payload.elapsed_seconds == null ? currentBuyerElapsedSeconds() : Number(payload.elapsed_seconds);
      flightBuyerProgressLog.hidden = false;
      const entry = document.createElement("div");
      entry.className = "progress-entry";
      entry.innerHTML = `<b>${escapeHtml(label)}</b><time>${escapeHtml(formatElapsedDuration(elapsedSeconds))}</time><span>${escapeHtml(message)}</span>`;
      flightBuyerProgressLog.appendChild(entry);
      while (flightBuyerProgressLog.children.length > 80) {
        flightBuyerProgressLog.removeChild(flightBuyerProgressLog.firstElementChild);
      }
      flightBuyerProgressLog.scrollTop = flightBuyerProgressLog.scrollHeight;
    }

    function renderFlightBuyers(buyers) {
      const productLimit = buyers.product_truncated ? ` Limited to ${formatNumber(buyers.scanned_products)} of ${formatNumber(buyers.total_known_products)} products.` : "";
      const regionLimit = buyers.region_truncated ? ` Limited to ${formatNumber(buyers.regions_scanned)} of ${formatNumber(buyers.total_regions_in_range)} regions.` : "";
      const marketCache = buyers.market_cache || {};
      flightBuyerSummary.innerHTML = `
        <strong>${formatNumber(buyers.products_with_buyers)}</strong> products have nearby buy orders.
        <br>Scanned ${formatNumber(buyers.scanned_products)} products across ${formatNumber(buyers.regions_scanned)} regions;
        found ${formatNumber(buyers.order_count)} buy orders.${escapeHtml(productLimit + regionLimit)}
        <br>Market cache: ${formatNumber(marketCache.entries)} entries, ${formatNumber(marketCache.ttl_seconds || 300)}s reuse window.
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
      const marketCache = profitability.market_cache || {};
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
        <div class="meta">Market cache: ${formatNumber(marketCache.entries)} entries, ${formatNumber(marketCache.ttl_seconds || 300)}s reuse window.</div>
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
        const jobTime = product.adjusted_time_seconds ? formatDuration(product.adjusted_time_seconds) : "unknown";
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
              <div class="decision-metric"><span>True Profit / Hour</span><b>${formatIskPerHour(product.true_profit_per_hour)}</b><small>Uses TE-adjusted one-run job time.</small></div>
              <div class="decision-metric"><span>Job Time</span><b>${jobTime}</b><small>Base ${formatDuration(product.base_time_seconds)} with selected TE.</small></div>
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
        ["Base manufacturing time", formatDuration(product.base_time_seconds)],
        ["TE-adjusted job time", formatDuration(product.adjusted_time_seconds)],
        ["After-tax true profit per hour", formatIskPerHour(product.true_profit_per_hour)],
        ["After-tax wallet gain per hour", formatIskPerHour(product.wallet_gain_per_hour)],
        ["Max blueprint copy runs", product.max_production_limit ? formatNumber(product.max_production_limit) : "unknown"],
        ["Required skills", renderSkillSummary(product.required_skills || [])],
        ["Buyer revenue", formatIsk(product.product_revenue)],
        [`Sales tax (${formatRatePercent(product.sales_tax_rate)})`, `-${formatIsk(product.sales_tax)}`],
        ["Immediate-sale broker fee", formatIsk(product.broker_fee)],
        ["Net revenue after tax and fees", formatIsk(product.net_revenue)],
        ["ME-adjusted all materials value", `-${formatIsk(product.replacement_cost)}`],
        ["Jita raw materials value", renderJitaRawValue(product)],
        ["Jita raw value coverage", renderJitaRawCoverage(product)],
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
      closeHaulEventSource();
      stopHaulProgressTimer();
      haulRouteSummary.textContent = message;
      haulRoutePath.textContent = "";
      haulProgressLog.hidden = true;
      haulProgressLog.innerHTML = "";
      haulOpportunitySummary.textContent = "No route scan has run yet.";
      haulOpportunityTop.textContent = "";
      haulScanButton.disabled = false;
    }

    function closeHaulEventSource() {
      if (haulEventSource) {
        haulEventSource.close();
        haulEventSource = null;
      }
    }

    function currentHaulElapsedSeconds() {
      if (!haulProgressStartedAt) return 0;
      return Math.max(0, Math.floor((Date.now() - haulProgressStartedAt) / 1000));
    }

    function stopHaulProgressTimer() {
      const elapsedSeconds = currentHaulElapsedSeconds();
      if (haulProgressTimer) {
        window.clearInterval(haulProgressTimer);
        haulProgressTimer = null;
      }
      haulProgressStartedAt = 0;
      return elapsedSeconds;
    }

    function renderHaulProgressSummary(settings, routeRule, podRule) {
      const elapsed = formatElapsedDuration(currentHaulElapsedSeconds());
      const itemScope = haulItemScopeLabel(settings);
      haulOpportunitySummary.innerHTML = `
        <div class="progress-status" aria-live="polite">
          <span class="progress-spinner" aria-hidden="true"></span>
          <div class="progress-copy">
            <strong>Comparing corridor sell orders and destination buy orders</strong>
            <span><strong>Elapsed ${escapeHtml(elapsed)}</strong> &middot; ${escapeHtml(itemScope)}; route ${escapeHtml(haulRouteLabel(settings))}; cargo ${formatVolume(settings.cargoM3)}; ${escapeHtml(routeRule)}; ${escapeHtml(podRule)}; detour margin ${formatNumber(settings.minDetourMarginPercent)}%.</span>
          </div>
        </div>
        <div class="progress-bar" aria-hidden="true"><span></span></div>
      `;
    }

    function startHaulProgressTimer(settings, routeRule, podRule) {
      stopHaulProgressTimer();
      haulProgressStartedAt = Date.now();
      renderHaulProgressSummary(settings, routeRule, podRule);
      haulProgressTimer = window.setInterval(() => {
        renderHaulProgressSummary(settings, routeRule, podRule);
      }, 1000);
    }

    async function loadFlightHauling() {
      const settings = writeHaulSettings({
        originName: haulOrigin.value,
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        routePreference: haulRoutePreference.value,
        avoidRecentPodKills: haulAvoidPodKills.checked,
        detourJumps: haulDetourJumps.value,
        minDetourMarginPercent: haulMinMargin.value,
        includeCommonMaterials: haulCommonMaterials.checked,
        marketGroupIds: readHaulMarketGroupIdsFromInputs(),
      });
      closeHaulEventSource();
      stopHaulProgressTimer();
      haulScanFinished = false;
      haulScanButton.disabled = true;
      const routeRule = settings.routePreference === "shorter" ? "Prefer shorter" : settings.routePreference === "less_secure" ? "Prefer less secure" : "Prefer safer";
      const podRule = settings.avoidRecentPodKills ? "avoiding recent pod kills" : "not avoiding recent pod kills";
      const itemScope = haulItemScopeLabel(settings);
      haulRouteSummary.textContent = `Scanning ${routeRule.toLowerCase()} route from ${haulStartLabel(settings)} to ${settings.destination}, ${podRule}, with ${formatNumber(settings.detourJumps)} detour jumps and ${itemScope}...`;
      haulRoutePath.textContent = "";
      haulProgressLog.hidden = false;
      haulProgressLog.innerHTML = "";
      startHaulProgressTimer(settings, routeRule, podRule);
      haulOpportunityTop.innerHTML = `<div class="decision-empty">Route opportunities will appear here when the scan finishes.</div>`;
      const params = new URLSearchParams({
        origin_name: settings.originName,
        destination: settings.destination,
        cargo_m3: String(settings.cargoM3),
        route_preference: settings.routePreference,
        avoid_recent_pod_kills: settings.avoidRecentPodKills ? "1" : "0",
        common_materials: settings.includeCommonMaterials ? "1" : "0",
        market_group_ids: settings.marketGroupIds.join(","),
        detour_jumps: String(settings.detourJumps),
        min_detour_margin_percent: String(settings.minDetourMarginPercent),
      });
      haulEventSource = new EventSource(`/api/flight/hauling/progress?${params}`);
      haulEventSource.addEventListener("scan_start", (event) => {
        appendHaulProgress("Start", parseHaulProgressEvent(event));
      });
      haulEventSource.addEventListener("route_step", (event) => {
        const payload = parseHaulProgressEvent(event);
        appendHaulProgress(`Stop ${formatNumber(payload.route_index)}`, payload);
      });
      haulEventSource.addEventListener("nearby_system", (event) => {
        appendHaulProgress("Nearby", parseHaulProgressEvent(event));
      });
      haulEventSource.addEventListener("orders", (event) => {
        const payload = parseHaulProgressEvent(event);
        appendHaulProgress(`Item ${formatNumber(payload.material_index)}/${formatNumber(payload.scanned_materials)}`, payload);
      });
      haulEventSource.addEventListener("scan_error", (event) => {
        const payload = parseHaulProgressEvent(event);
        haulScanFinished = true;
        const elapsedSeconds = stopHaulProgressTimer();
        closeHaulEventSource();
        appendHaulProgress("Stopped", {message: payload.error || "Route scan failed.", elapsed_seconds: elapsedSeconds});
        haulRouteSummary.textContent = `${payload.error || "Route scan failed."} Elapsed ${formatElapsedDuration(elapsedSeconds)}.`;
        haulOpportunitySummary.textContent = `Route scan failed after ${formatElapsedDuration(elapsedSeconds)}.`;
        haulOpportunityTop.textContent = "";
        haulScanButton.disabled = false;
      });
      haulEventSource.addEventListener("result", (event) => {
        haulScanFinished = true;
        const data = parseHaulProgressEvent(event);
        const elapsedSeconds = stopHaulProgressTimer();
        renderFlightHauling(data, elapsedSeconds);
        appendHaulProgress("Done", {message: `Route opportunity scan complete in ${formatElapsedDuration(elapsedSeconds)}.`, elapsed_seconds: elapsedSeconds});
        closeHaulEventSource();
        haulScanButton.disabled = false;
      });
      haulEventSource.onerror = () => {
        if (haulScanFinished) return;
        const elapsedSeconds = stopHaulProgressTimer();
        closeHaulEventSource();
        appendHaulProgress("Stopped", {message: "Route scan connection closed before results arrived.", elapsed_seconds: elapsedSeconds});
        haulRouteSummary.textContent = `Route scan connection closed before results arrived. Elapsed ${formatElapsedDuration(elapsedSeconds)}.`;
        haulOpportunitySummary.textContent = `Route scan failed after ${formatElapsedDuration(elapsedSeconds)}.`;
        haulOpportunityTop.textContent = "";
        haulScanButton.disabled = false;
      };
    }

    function parseHaulProgressEvent(event) {
      try {
        return JSON.parse(event.data || "{}");
      } catch (_error) {
        return {message: "Unreadable progress update."};
      }
    }

    function appendHaulProgress(label, payload) {
      const message = payload.message || "";
      if (!message) return;
      const elapsedSeconds = payload.elapsed_seconds == null ? currentHaulElapsedSeconds() : Number(payload.elapsed_seconds);
      haulProgressLog.hidden = false;
      const entry = document.createElement("div");
      entry.className = "progress-entry";
      entry.innerHTML = `<b>${escapeHtml(label)}</b><time>${escapeHtml(formatElapsedDuration(elapsedSeconds))}</time><span>${escapeHtml(message)}</span>`;
      haulProgressLog.appendChild(entry);
      while (haulProgressLog.children.length > 80) {
        haulProgressLog.removeChild(haulProgressLog.firstElementChild);
      }
      haulProgressLog.scrollTop = haulProgressLog.scrollHeight;
    }

    function renderFlightHauling(data, elapsedSeconds = null) {
      const route = data.route || {};
      const hauling = data.hauling || {};
      const origin = route.origin || {};
      const destination = route.destination || {};
      const salesTax = hauling.sales_tax || {};
      const marketCache = hauling.market_cache || {};
      const itemScope = hauling.item_scope || {};
      const materialLimit = hauling.item_truncated ? ` Limited to ${formatNumber(hauling.scanned_item_types || hauling.scanned_materials)} of ${formatNumber(hauling.total_item_types || hauling.total_materials)} selected item types.` : "";
      const regionLimit = hauling.pickup_region_truncated ? ` Limited to ${formatNumber(hauling.pickup_regions_scanned)} of ${formatNumber(hauling.pickup_regions_total)} pickup regions.` : "";
      const selectedGroups = (itemScope.selected_market_groups || []).map((group) => group.name).filter(Boolean);
      const groupText = selectedGroups.length ? ` Market categories: ${selectedGroups.slice(0, 4).map((name) => escapeHtml(name)).join(", ")}${selectedGroups.length > 4 ? `, +${formatNumber(selectedGroups.length - 4)} more` : ""}.` : "";
      const commonText = itemScope.include_common_materials ? "Common materials included." : "Common materials not included.";
      const routeLabel = route.route_preference_label || "Safer";
      const avoidLabel = route.avoid_recent_pod_kills ? "avoiding recent pod kills" : "not avoiding recent pod kills";
      const sourceLabel = route.route_source === "local-shortest-fallback" ? "local fallback" : "ESI route planner";
      const originSourceLabel = route.origin_source === "manual" ? "manual start override" : "live ESI start";
      const routeWarning = route.route_warning ? `<div class="meta">${escapeHtml(route.route_warning)}</div>` : "";
      const scanDuration = elapsedSeconds == null ? "" : `<div class="meta">Scan duration: ${escapeHtml(formatElapsedDuration(elapsedSeconds))}.</div>`;
      haulRouteSummary.innerHTML = `
        <strong>${escapeHtml(origin.name || "Current system")}</strong> to
        <strong>${escapeHtml(destination.name || route.destination_query || "destination")}</strong>:
        ${formatNumber(route.route_jumps)} jumps, ${formatNumber(hauling.pickup_system_count)} pickup systems,
        ${formatVolume(route.cargo_capacity_m3)} cargo.
        <div class="meta">Start: ${escapeHtml(originSourceLabel)}. ${escapeHtml(routeLabel)} route from ${escapeHtml(sourceLabel)}; ${escapeHtml(avoidLabel)}. Avoided ${formatNumber(route.avoided_pod_kill_system_count)} recent pod-kill systems; ${formatNumber(route.route_pod_kill_system_count)} remain on this path.</div>
        ${scanDuration}
        ${routeWarning}
      `;
      haulRoutePath.innerHTML = renderHaulRoutePath(route.systems || []);
      haulOpportunitySummary.innerHTML = `
        <div class="profit-stats">
          <div class="profit-stat"><span>Profitable</span><b>${formatNumber(hauling.profitable_opportunities)}</b></div>
          <div class="profit-stat"><span>Sell Orders</span><b>${formatNumber(hauling.sell_order_count)}</b></div>
          <div class="profit-stat"><span>Buy Orders</span><b>${formatNumber(hauling.buy_order_count)}</b></div>
          <div class="profit-stat"><span>Item Types</span><b>${formatNumber(hauling.scanned_item_types || hauling.scanned_materials)}</b></div>
        </div>
        <div class="meta">
          Pickup detour ${formatNumber(hauling.detour_jumps)} jumps; scanned ${formatNumber(hauling.pickup_regions_scanned)}
          pickup regions; detour threshold ${formatNumber(hauling.min_detour_margin_percent)}%.${escapeHtml(materialLimit + regionLimit)}
        </div>
        <div class="meta">${escapeHtml(commonText)}${groupText}</div>
        <div class="meta">${formatNumber(hauling.detour_margin_rejected_count)} detour candidates were below the selected profit threshold.</div>
        <div class="meta">Accounting ${formatNumber(salesTax.accounting_level)} gives ${formatRatePercent(salesTax.rate)} sales tax on destination buy-order sales.</div>
        <div class="meta">Market cache: ${formatNumber(marketCache.entries)} entries, ${formatNumber(marketCache.ttl_seconds || 300)}s reuse window.</div>
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

    function resetMarketAcquisition(message) {
      acqSummary.textContent = message;
      acqRoute.textContent = "";
      acqResults.innerHTML = "";
      acqScanButton.disabled = false;
    }

    async function loadMarketAcquisition() {
      const settings = writeAcquisitionSettings({
        originName: acqOrigin.value,
        destination: acqDestination.value,
        budgetIsk: acqBudget.value,
        brokerFeePercent: acqBrokerFee.value,
        pickupJumps: acqPickupJumps.value,
        targetDays: acqTargetDays.value,
        minMarginPercent: acqMinMargin.value,
        includeCommonMaterials: acqCommonMaterials.checked,
        marketGroupIds: readAcqMarketGroupIdsFromInputs(),
      });
      acqScanButton.disabled = true;
      acqSummary.textContent = `Checking public orders and market history for ${acquisitionItemScopeLabel(settings)}...`;
      acqRoute.textContent = "";
      acqResults.innerHTML = `<div class="decision-empty">Recommendations will appear here when the scan finishes.</div>`;
      const params = new URLSearchParams({
        origin_name: settings.originName,
        destination: settings.destination,
        budget_isk: String(settings.budgetIsk),
        broker_fee_percent: String(settings.brokerFeePercent),
        pickup_jumps: String(settings.pickupJumps),
        target_days: String(settings.targetDays),
        min_margin_percent: String(settings.minMarginPercent),
        common_materials: settings.includeCommonMaterials ? "1" : "0",
        market_group_ids: settings.marketGroupIds.join(","),
      });
      try {
        const response = await fetch(`/api/flight/acquisition?${params}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not plan market acquisitions");
        renderMarketAcquisition(data);
      } catch (error) {
        acqSummary.textContent = error.message;
        acqRoute.textContent = "";
        acqResults.textContent = "";
      } finally {
        acqScanButton.disabled = false;
      }
    }

    function renderMarketAcquisition(data) {
      const route = data.route || {};
      const acquisition = data.acquisition || {};
      const origin = route.origin || acquisition.origin_system || {};
      const destination = route.destination || acquisition.destination_system || {};
      const salesTax = acquisition.sales_tax || {};
      const marketCache = acquisition.market_cache || {};
      const historyCache = acquisition.history_cache || {};
      const itemScope = acquisition.item_scope || {};
      const selectedGroups = (itemScope.selected_market_groups || []).map((group) => group.name).filter(Boolean);
      const groupText = selectedGroups.length ? ` Market categories: ${selectedGroups.slice(0, 4).map((name) => escapeHtml(name)).join(", ")}${selectedGroups.length > 4 ? `, +${formatNumber(selectedGroups.length - 4)} more` : ""}.` : "";
      const commonText = itemScope.include_common_materials ? "Common materials included." : "Common materials not included.";
      const itemLimit = acquisition.item_truncated ? ` Limited to ${formatNumber(acquisition.scanned_item_types)} of ${formatNumber(acquisition.total_item_types)} selected item types.` : "";
      const regionLimit = acquisition.pickup_region_truncated ? ` Limited to ${formatNumber(acquisition.pickup_regions_scanned)} of ${formatNumber(acquisition.pickup_regions_total)} pickup regions.` : "";
      const routeWarning = route.route_warning ? `<div class="meta">${escapeHtml(route.route_warning)}</div>` : "";
      acqRoute.innerHTML = `
        <strong>${escapeHtml(origin.name || "Current system")}</strong> buy-order area toward
        <strong>${escapeHtml(destination.name || route.destination_query || "destination")}</strong>.
        <div class="meta">Budget ${formatIsk(acquisition.budget_isk)}; collection ${formatNumber(acquisition.pickup_jumps)} jumps; broker fee estimate ${formatNumber(acquisition.broker_fee_percent)}%; target margin ${formatNumber(acquisition.min_margin_percent)}%; target fill window ${formatNumber(acquisition.target_days)} days.</div>
        ${routeWarning}
      `;
      acqSummary.innerHTML = `
        <div class="profit-stats">
          <div class="profit-stat"><span>Recommendations</span><b>${formatNumber(acquisition.opportunity_count)}</b></div>
          <div class="profit-stat"><span>Possible Traps</span><b>${formatNumber(acquisition.possible_trap_count)}</b></div>
          <div class="profit-stat"><span>Source Buys</span><b>${formatNumber(acquisition.source_buy_order_count)}</b></div>
          <div class="profit-stat"><span>History Regions</span><b>${formatNumber(acquisition.history_region_count)}</b></div>
        </div>
        <div class="meta">Scanned ${formatNumber(acquisition.scanned_item_types)} item types across ${formatNumber(acquisition.pickup_regions_scanned)} pickup regions.${escapeHtml(itemLimit + regionLimit)}</div>
        <div class="meta">${escapeHtml(commonText)}${groupText}</div>
        <div class="meta">Accounting ${formatNumber(salesTax.accounting_level)} gives ${formatRatePercent(salesTax.rate)} sales tax on downstream buy-order sales.</div>
        <div class="meta">Order cache: ${formatNumber(marketCache.entries)} entries. History cache: ${formatNumber(historyCache.entries)} entries.</div>
        <div class="meta">${escapeHtml(acquisition.pricing_note || "Planner is advisory only; verify in EVE before posting buy orders.")}</div>
      `;
      acqResults.innerHTML = renderAcquisitionOpportunities(acquisition.opportunities || []);
    }

    function acquisitionRiskClass(riskLevel) {
      if (riskLevel === "possible-trap") return "decision-price";
      if (riskLevel === "caution") return "decision-watch";
      return "decision-build";
    }

    function acquisitionRiskLabel(riskLevel) {
      if (riskLevel === "possible-trap") return "Possible trap";
      if (riskLevel === "caution") return "Caution";
      return "Clear";
    }

    function renderAcquisitionOpportunities(opportunities) {
      if (!opportunities.length) {
        return `<div class="decision-empty">No buy-order acquisition recommendations cleared the current filters.</div>`;
      }
      return `<div class="decision-list">${opportunities.slice(0, 12).map((item) => {
        const decision = item.decision || {};
        const range = item.range_recommendation || {};
        const sourceBuy = item.best_source_buy || {};
        const sourceSell = item.best_source_sell || {};
        const destinationBuy = item.best_destination_buy || {};
        const riskClass = acquisitionRiskClass(item.risk_level);
        return `
          <div class="decision-row">
            <div class="decision-head">
              <strong>${escapeHtml(item.item_name)}</strong>
              <span class="pill ${riskClass}">${escapeHtml(acquisitionRiskLabel(item.risk_level))}</span>
            </div>
            <div class="decision-lede">${escapeHtml(decision.label || "Review")} in ${escapeHtml(item.placement_system || "source")} at ${formatIsk(item.suggested_bid)} or less; ${escapeHtml(range.range || "station")} range.</div>
            <div class="decision-metrics">
              <div class="decision-metric"><span>Suggested Bid</span><b>${formatIsk(item.suggested_bid)}</b><small>Safe ceiling ${formatIsk(item.max_safe_bid)}.</small></div>
              <div class="decision-metric"><span>Units</span><b>${formatNumber(item.recommended_units)}</b><small>Estimated ISK committed ${formatIsk(item.estimated_isk_committed)}.</small></div>
              <div class="decision-metric"><span>After-Fee Profit</span><b>${formatSignedIsk(item.net_profit)}</b><small>${formatPercent(item.margin_percent)} on bid plus broker fee.</small></div>
              <div class="decision-metric"><span>Destination Demand</span><b>${escapeHtml(destinationBuy.system_name || "unknown")}</b><small>${formatIsk(destinationBuy.price)} buy; ${formatNumber(destinationBuy.volume_remain)} units left.</small></div>
            </div>
            <div class="decision-lede">${escapeHtml(decision.reason || "Verify current orders in EVE before posting.")}</div>
            ${renderAcquisitionHistoryFlags(item.history_flags || [])}
            <details class="profit-details">
              <summary>Market details</summary>
              <div class="profit-detail-grid">
                <div class="profit-detail-row"><span>Recommended range</span><b>${escapeHtml(range.range || "station")}</b></div>
                <div class="profit-detail-row"><span>Range reason</span><b>${escapeHtml(range.reason || "Keep the first order easy to monitor.")}</b></div>
                <div class="profit-detail-row"><span>Best competing source buy</span><b>${sourceBuy.price == null ? "none nearby" : `${formatIsk(sourceBuy.price)} in ${escapeHtml(sourceBuy.system_name || "source")}`}</b></div>
                <div class="profit-detail-row"><span>Lowest nearby source sell</span><b>${sourceSell.price == null ? "none nearby" : `${formatIsk(sourceSell.price)} in ${escapeHtml(sourceSell.system_name || "source")}`}</b></div>
                <div class="profit-detail-row"><span>Broker fee estimate</span><b>${formatIsk(item.estimated_broker_fee)}</b></div>
                <div class="profit-detail-row"><span>Source history</span><b>${renderHistoryStats(item.source_history || {})}</b></div>
                <div class="profit-detail-row"><span>Destination history</span><b>${renderHistoryStats(item.destination_history || {})}</b></div>
              </div>
            </details>
          </div>
        `;
      }).join("")}</div>`;
    }

    function renderAcquisitionHistoryFlags(flags) {
      if (!flags.length) return "";
      return `
        <div class="decision-counts">
          ${flags.map((flag) => {
            const cls = flag.severity === "trap" ? "decision-price" : flag.severity === "caution" ? "decision-watch" : "decision-build";
            return `<span class="pill ${cls}" title="${escapeHtml(flag.detail || "")}">${escapeHtml(flag.label || "History signal")}</span>`;
          }).join("")}
        </div>
      `;
    }

    function renderHistoryStats(stats) {
      const days = formatNumber(stats.days || 0);
      const volume = formatNumber(stats.avg_daily_volume || 0);
      const orderCount = Number(stats.avg_daily_order_count || 0).toLocaleString(undefined, {maximumFractionDigits: 1});
      const median = stats.median_average == null ? "unknown" : formatIsk(stats.median_average);
      return `${days}d; ${volume}/day; ${orderCount} orders/day; median ${median}`;
    }

    function clampTradePnlWindowHours(value) {
      const hours = Number(value);
      if (!Number.isFinite(hours)) return 720;
      return Math.max(1, Math.min(720, Math.round(hours)));
    }

    function normalizeTradePnlLens(value) {
      const lens = String(value || "").trim();
      return ["inventory", "realized", "cashflow"].includes(lens) ? lens : "inventory";
    }

    function tradePnlWindowLabel(hours) {
      const cleanHours = clampTradePnlWindowHours(hours);
      if (cleanHours < 24) return cleanHours === 1 ? "1 hour" : `${formatNumber(cleanHours)} hours`;
      const days = cleanHours / 24;
      return Number.isInteger(days) ? `${formatNumber(days)} day${days === 1 ? "" : "s"}` : `${days.toFixed(1)} days`;
    }

    function readTradePnlSettings() {
      const showStored = window.localStorage.getItem(tradePnlShowMatchesKey);
      return {
        windowHours: clampTradePnlWindowHours(window.localStorage.getItem(tradePnlWindowHoursKey) || tradePnlWindowHours.value || 720),
        lens: normalizeTradePnlLens(window.localStorage.getItem(tradePnlLensKey) || tradePnlLens.value || "inventory"),
        exclude: String(window.localStorage.getItem(tradePnlExcludeKey) || tradePnlExclude.value || "").trim(),
        showMatches: showStored == null ? tradePnlShowMatches.checked : showStored !== "0",
      };
    }

    function writeTradePnlSettings(settings) {
      const windowHours = clampTradePnlWindowHours(settings.windowHours);
      const lens = normalizeTradePnlLens(settings.lens);
      const exclude = String(settings.exclude || "").trim();
      const showMatches = Boolean(settings.showMatches);
      tradePnlWindowHours.value = String(windowHours);
      tradePnlLens.value = lens;
      tradePnlExclude.value = exclude;
      tradePnlShowMatches.checked = showMatches;
      window.localStorage.setItem(tradePnlWindowHoursKey, String(windowHours));
      window.localStorage.setItem(tradePnlLensKey, lens);
      window.localStorage.setItem(tradePnlExcludeKey, exclude);
      window.localStorage.setItem(tradePnlShowMatchesKey, showMatches ? "1" : "0");
      return {windowHours, lens, exclude, showMatches};
    }

    function resetTradePnl(message) {
      tradePnlSummary.textContent = message;
      tradePnlFees.textContent = "";
      tradePnlResults.textContent = "";
      tradePnlAnalyzeButton.disabled = false;
    }

    async function loadTradePnl() {
      const settings = writeTradePnlSettings({
        windowHours: tradePnlWindowHours.value,
        lens: tradePnlLens.value,
        exclude: tradePnlExclude.value,
        showMatches: tradePnlShowMatches.checked,
      });
      tradePnlAnalyzeButton.disabled = true;
      tradePnlSummary.textContent = `Reading ${tradePnlWindowLabel(settings.windowHours)} of wallet transactions...`;
      tradePnlFees.textContent = "";
      tradePnlResults.innerHTML = `<div class="decision-empty">Item P&L will appear here when the wallet scan finishes.</div>`;
      const params = new URLSearchParams({
        window_hours: String(settings.windowHours),
        lens: settings.lens,
        exclude: settings.exclude,
        include_matches: settings.showMatches ? "1" : "0",
      });
      try {
        const response = await fetch(`/api/flight/trade-pnl?${params}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not analyze trade profit and loss");
        renderTradePnl(data);
      } catch (error) {
        tradePnlSummary.textContent = error.message;
        tradePnlFees.textContent = "";
        tradePnlResults.textContent = "";
      } finally {
        tradePnlAnalyzeButton.disabled = false;
      }
    }

    function renderTradePnl(data) {
      const pnl = data.trade_pnl || {};
      const totals = pnl.totals || {};
      const itemLimit = pnl.items_truncated ? ` Showing largest ${formatNumber(pnl.item_limit)} of ${formatNumber(pnl.item_count)} item types.` : "";
      const excludedText = totals.excluded_item_count
        ? ` Excluded ${formatNumber(totals.excluded_item_count)} item types; raw excluded actual ${formatSignedIsk(totals.excluded_actual_profit_isk)}.`
        : "";
      tradePnlSummary.innerHTML = `
        <div class="profit-stats">
          <div class="profit-stat"><span>Considered Result</span><b>${formatSignedIsk(totals.considered_result_isk)}</b></div>
          <div class="profit-stat"><span>All Actual P&L</span><b>${formatSignedIsk(totals.actual_profit_isk)}</b></div>
          <div class="profit-stat"><span>Fee Gap</span><b>${formatSignedIsk(totals.market_fee_isk)}</b></div>
          <div class="profit-stat"><span>Open Stock Cost</span><b>${formatIsk(totals.open_inventory_cost_isk)}</b></div>
        </div>
        <div class="meta">${escapeHtml(pnl.accounting_lens_label || "Inventory Mode")} across ${escapeHtml(pnl.window_label || `${formatNumber(pnl.days)} days`)}. ${formatNumber(pnl.transaction_count)} market transactions and ${formatNumber(pnl.journal_entry_count)} wallet journal rows.${escapeHtml(itemLimit + excludedText)}</div>
        <div class="meta">${escapeHtml(pnl.expectation_source || "Expected spread is before wallet fees.")}</div>
        <div class="meta">${escapeHtml(pnl.limits_note || "Unmatched sells can mean the buy happened before the visible ESI history window.")}</div>
      `;
      tradePnlFees.innerHTML = `
        Wallet-level net cashflow ${formatSignedIsk(totals.net_cashflow_isk)}.
        Unmatched sell revenue ${formatIsk(totals.unmatched_sell_revenue_isk)} across ${formatNumber(totals.unmatched_sell_quantity)} units.
        Allocated fees ${formatSignedIsk(totals.allocated_fee_isk)}; unallocated fees ${formatSignedIsk(totals.unallocated_fee_isk)}.
        Loss-making matched item types ${formatNumber(totals.loss_item_count)}.
        ${renderTradeFeeRefs(pnl.fee_ref_counts || {})}
      `;
      tradePnlResults.innerHTML = renderTradePnlItems(pnl.items || []);
    }

    function renderTradeFeeRefs(counts) {
      const entries = Object.entries(counts || {});
      if (!entries.length) return "";
      return `Fee rows: ${entries.map(([refType, count]) => `${escapeHtml(refType)} ${formatNumber(count)}`).join(", ")}.`;
    }

    function tradePnlStatusClass(status) {
      if (status === "profit") return "decision-build";
      if (status === "loss") return "decision-skip";
      if (status === "excluded-loss") return "decision-price";
      if (status === "excluded-profit") return "decision-price";
      if (status === "excluded") return "decision-price";
      if (status === "open-stock") return "decision-stock";
      if (status === "needs-older-buys") return "decision-price";
      return "decision-watch";
    }

    function tradePnlStatusLabel(status) {
      if (status === "profit") return "Profit";
      if (status === "loss") return "Loss";
      if (status === "excluded-loss") return "Excluded loss";
      if (status === "excluded-profit") return "Excluded profit";
      if (status === "excluded") return "Excluded";
      if (status === "open-stock") return "Open stock";
      if (status === "needs-older-buys") return "Needs older buys";
      if (status === "break-even") return "Break-even";
      return "No match";
    }

    function renderTradePnlItems(items) {
      if (!items.length) return `<div class="decision-empty">No market wallet transactions matched inside the selected history window.</div>`;
      return `<div class="decision-list">${items.map((item) => {
        const status = item.status || "no-match";
        const statusClass = tradePnlStatusClass(status);
        const excludedNote = item.excluded ? `<div class="meta">${escapeHtml(item.excluded_reason || "Excluded from considered result.")}</div>` : "";
        const matches = item.matches || [];
        return `
          <div class="decision-row">
            <div class="decision-head">
              <strong>${escapeHtml(item.item_name)}</strong>
              <span class="pill ${statusClass}">${escapeHtml(tradePnlStatusLabel(status))}</span>
            </div>
            ${excludedNote}
            <div class="decision-lede">Actual ${formatSignedIsk(item.actual_profit_isk)} vs expected ${formatSignedIsk(item.expected_profit_isk)}; fee gap ${formatSignedIsk(item.fee_gap_isk)}.</div>
            <div class="decision-metrics">
              <div class="decision-metric"><span>Matched Units</span><b>${formatNumber(item.matched_quantity)}</b><small>Bought ${formatNumber(item.buy_quantity)}; sold ${formatNumber(item.sell_quantity)}.</small></div>
              <div class="decision-metric"><span>Buy Cost</span><b>${formatIsk(item.matched_buy_cost_isk)}</b><small>Total buys ${formatIsk(item.buy_total_isk)}.</small></div>
              <div class="decision-metric"><span>Sell Revenue</span><b>${formatIsk(item.matched_sell_revenue_isk)}</b><small>Total sells ${formatIsk(item.sell_total_isk)}.</small></div>
              <div class="decision-metric"><span>Open / Unmatched</span><b>${formatNumber(item.open_quantity)} / ${formatNumber(item.unmatched_sell_quantity)}</b><small>Open cost ${formatIsk(item.open_inventory_cost_isk)}.</small></div>
            </div>
            <details class="profit-details">
              <summary>Accounting details</summary>
              <div class="profit-detail-grid">
                <div class="profit-detail-row"><span>Net wallet cashflow</span><b>${formatSignedIsk(item.net_cashflow_isk)}</b></div>
                <div class="profit-detail-row"><span>Allocated wallet fees</span><b>${formatSignedIsk(item.allocated_fee_isk)}</b></div>
                <div class="profit-detail-row"><span>Margin on matched cost</span><b>${formatPercent(item.margin_percent)}</b></div>
                <div class="profit-detail-row"><span>Unmatched sell revenue</span><b>${formatIsk(item.unmatched_sell_revenue_isk)}</b></div>
                <div class="profit-detail-row"><span>Visible dates</span><b>${escapeHtml(item.first_date || "unknown")} to ${escapeHtml(item.last_date || "unknown")}</b></div>
              </div>
              ${renderTradePnlMatches(matches)}
            </details>
          </div>
        `;
      }).join("")}</div>`;
    }

    function renderTradePnlMatches(matches) {
      if (!matches.length) return "";
      return `
        <div class="meta">Matched transactions</div>
        <div class="profit-detail-grid">
          ${matches.map((match) => `
            <div class="profit-detail-row">
              <span>Buy #${escapeHtml(match.buy_transaction_id)} -> Sell #${escapeHtml(match.sell_transaction_id)}<br><small>${escapeHtml(match.buy_date || "unknown")} to ${escapeHtml(match.sell_date || "unknown")}; ${formatNumber(match.quantity)} units @ ${formatIsk(match.buy_unit_price)} -> ${formatIsk(match.sell_unit_price)}</small></span>
              <b>${formatSignedIsk(match.actual_profit_isk)}</b>
            </div>
          `).join("")}
        </div>
      `;
    }

    function clampPlanetaryPercentValue(value, defaultValue) {
      const number = Number(value);
      if (!Number.isFinite(number)) return defaultValue;
      return Math.max(0, Math.min(100, number));
    }

    function clampPlanetaryTopValue(value) {
      const number = Math.floor(Number(value));
      if (!Number.isFinite(number)) return 20;
      return Math.max(1, Math.min(30, number));
    }

    function normalizePlanetaryTier(value) {
      const tier = String(value || "").trim().toUpperCase();
      return ["P1", "P2", "P3", "P4"].includes(tier) ? tier : "P2";
    }

    function clampPlanetaryCceValue(value) {
      const number = Math.floor(Number(value));
      if (!Number.isFinite(number)) return 0;
      return Math.max(0, Math.min(5, number));
    }

    function readPlanetarySettings() {
      return {
        hub: String(window.localStorage.getItem(planetaryHubKey) || planetaryHub.value || "Jita").trim() || "Jita",
        outputTier: normalizePlanetaryTier(window.localStorage.getItem(planetaryTierKey) || planetaryOutputTier.value),
        top: clampPlanetaryTopValue(window.localStorage.getItem(planetaryTopKey) || planetaryTop.value || 20),
        ownerTaxPercent: clampPlanetaryPercentValue(window.localStorage.getItem(planetaryOwnerTaxKey) || planetaryOwnerTax.value || 5, 5),
        npcTaxPercent: clampPlanetaryPercentValue(window.localStorage.getItem(planetaryNpcTaxKey) || planetaryNpcTax.value || 10, 10),
        customsCodeExpertiseLevel: clampPlanetaryCceValue(window.localStorage.getItem(planetaryCceKey) || planetaryCce.value || 0),
        salesTaxPercent: clampPlanetaryPercentValue(window.localStorage.getItem(planetarySalesTaxKey) || planetarySalesTax.value || 3, 3),
        brokerFeePercent: clampPlanetaryPercentValue(window.localStorage.getItem(planetaryBrokerFeeKey) || planetaryBrokerFee.value || 0, 0),
      };
    }

    function writePlanetarySettings(settings) {
      const clean = {
        hub: String(settings.hub || "Jita").trim() || "Jita",
        outputTier: normalizePlanetaryTier(settings.outputTier),
        top: clampPlanetaryTopValue(settings.top),
        ownerTaxPercent: clampPlanetaryPercentValue(settings.ownerTaxPercent, 5),
        npcTaxPercent: clampPlanetaryPercentValue(settings.npcTaxPercent, 10),
        customsCodeExpertiseLevel: clampPlanetaryCceValue(settings.customsCodeExpertiseLevel),
        salesTaxPercent: clampPlanetaryPercentValue(settings.salesTaxPercent, 3),
        brokerFeePercent: clampPlanetaryPercentValue(settings.brokerFeePercent, 0),
      };
      planetaryHub.value = clean.hub;
      planetaryOutputTier.value = clean.outputTier;
      planetaryTop.value = String(clean.top);
      planetaryOwnerTax.value = String(clean.ownerTaxPercent);
      planetaryNpcTax.value = String(clean.npcTaxPercent);
      planetaryCce.value = String(clean.customsCodeExpertiseLevel);
      planetarySalesTax.value = String(clean.salesTaxPercent);
      planetaryBrokerFee.value = String(clean.brokerFeePercent);
      window.localStorage.setItem(planetaryHubKey, clean.hub);
      window.localStorage.setItem(planetaryTierKey, clean.outputTier);
      window.localStorage.setItem(planetaryTopKey, String(clean.top));
      window.localStorage.setItem(planetaryOwnerTaxKey, String(clean.ownerTaxPercent));
      window.localStorage.setItem(planetaryNpcTaxKey, String(clean.npcTaxPercent));
      window.localStorage.setItem(planetaryCceKey, String(clean.customsCodeExpertiseLevel));
      window.localStorage.setItem(planetarySalesTaxKey, String(clean.salesTaxPercent));
      window.localStorage.setItem(planetaryBrokerFeeKey, String(clean.brokerFeePercent));
      return clean;
    }

    function resetPlanetary(message) {
      planetarySummary.textContent = message;
      planetaryResults.innerHTML = "";
      planetaryRankButton.disabled = false;
    }

    async function loadPlanetaryRanking() {
      const settings = writePlanetarySettings({
        hub: planetaryHub.value,
        outputTier: planetaryOutputTier.value,
        top: planetaryTop.value,
        ownerTaxPercent: planetaryOwnerTax.value,
        npcTaxPercent: planetaryNpcTax.value,
        customsCodeExpertiseLevel: planetaryCce.value,
        salesTaxPercent: planetarySalesTax.value,
        brokerFeePercent: planetaryBrokerFee.value,
      });
      planetaryRankButton.disabled = true;
      planetarySummary.textContent = `Pricing ${settings.outputTier} planetary chains in ${settings.hub}...`;
      planetaryResults.innerHTML = `<div class="decision-empty">PI ranking will appear here when pricing finishes.</div>`;
      const params = new URLSearchParams({
        hub: settings.hub,
        output_tier: settings.outputTier,
        top: String(settings.top),
        owner_tax_percent: String(settings.ownerTaxPercent),
        npc_tax_percent: String(settings.npcTaxPercent),
        customs_code_expertise_level: String(settings.customsCodeExpertiseLevel),
        sales_tax_percent: String(settings.salesTaxPercent),
        broker_fee_percent: String(settings.brokerFeePercent),
      });
      try {
        const response = await fetch(`/api/flight/planetary?${params}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not rank planetary industry");
        renderPlanetaryRanking(data);
      } catch (error) {
        planetarySummary.textContent = error.message;
        planetaryResults.innerHTML = "";
      } finally {
        planetaryRankButton.disabled = false;
      }
    }

    function renderPlanetaryRanking(data) {
      const planetary = data.planetary || {};
      const hub = data.hub || {};
      const tax = data.tax_profile || {};
      const cache = planetary.cache || {};
      const opportunities = planetary.opportunities || [];
      const profitableCount = opportunities.filter((item) => item.profitable).length;
      const completeCount = opportunities.filter((item) => item.price_complete).length;
      planetarySummary.innerHTML = `
        <div class="profit-stats">
          <div class="profit-stat"><span>Profitable</span><b>${formatNumber(profitableCount)}</b></div>
          <div class="profit-stat"><span>Priced</span><b>${formatNumber(completeCount)} / ${formatNumber(opportunities.length)}</b></div>
          <div class="profit-stat"><span>Input Types</span><b>${formatNumber(planetary.priced_input_type_count)} / ${formatNumber(planetary.input_type_count)}</b></div>
          <div class="profit-stat"><span>Output Types</span><b>${formatNumber(planetary.priced_output_type_count)} / ${formatNumber(planetary.output_type_count)}</b></div>
        </div>
        <div class="meta">Hub ${escapeHtml(hub.name || "Jita")}; output tier ${escapeHtml((data.settings || {}).output_tier || "P2")}; ${formatNumber(planetary.schematic_count)} schematics scanned.</div>
        <div class="meta">Customs export rate ${formatPercent(tax.effective_export_tax_percent)} after CCE ${formatNumber(tax.customs_code_expertise_level)}; sales tax ${formatPercent(tax.sales_tax_percent)}; broker fee ${formatPercent(tax.broker_fee_percent)}.</div>
        <div class="meta">Static PI cache: ${cache.available ? `build ${escapeHtml(cache.build_number || "unknown")}` : escapeHtml(cache.error || "missing")}; ${formatNumber(cache.schematic_count)} schematics, ${formatNumber(cache.commodity_count)} commodities.</div>
        <div class="meta">${escapeHtml(planetary.pricing_note || "Customs transfer cost is included before profit.")}</div>
      `;
      planetaryResults.innerHTML = renderPlanetaryOpportunities(opportunities);
    }

    function renderPlanetaryOpportunities(opportunities) {
      if (!opportunities.length) return `<div class="decision-empty">No planetary opportunities matched these settings.</div>`;
      return `<div class="decision-list">${opportunities.map((item) => {
        const statusClass = planetaryStatusClass(item);
        const statusLabel = item.price_complete ? (item.profitable ? "Profitable" : "Below Cost") : "Price Check";
        const missing = item.price_complete
          ? ""
          : `<div class="meta">Missing prices for type ids: ${escapeHtml((item.missing_price_type_ids || []).join(", ") || "unknown")}.</div>`;
        return `
          <div class="decision-row">
            <div class="decision-head">
              <strong>${escapeHtml(item.output_name || item.schematic_name)}</strong>
              <span class="pill ${statusClass}">${escapeHtml(statusLabel)}</span>
            </div>
            <div class="decision-lede">Net ${formatSignedIsk(item.net_profit)} per ${escapeHtml(item.schematic_name || "schematic")} cycle after customs transfer.</div>
            ${missing}
            <div class="decision-metrics">
              <div class="decision-metric"><span>Net Profit</span><b>${formatSignedIsk(item.net_profit)}</b><small>Output minus inputs, customs, sales tax, and broker fee.</small></div>
              <div class="decision-metric"><span>Profit / Day</span><b>${formatSignedIsk(item.profit_per_day)}</b><small>Cycle time ${formatDuration(item.cycle_time_seconds)}.</small></div>
              <div class="decision-metric"><span>Customs Transfer</span><b>-${formatIsk(item.customs_transfer_cost)}</b><small>Import ${formatIsk(item.import_customs_cost)}; export ${formatIsk(item.export_customs_cost)}.</small></div>
              <div class="decision-metric"><span>Market Values</span><b>${formatIsk(item.output_value)}</b><small>Inputs ${formatIsk(item.input_value)}.</small></div>
            </div>
            <div class="meta">${renderPlanetaryMaterialSummary(item)}</div>
            ${renderPlanetaryMathDetails(item)}
          </div>
        `;
      }).join("")}</div>`;
    }

    function planetaryStatusClass(item) {
      if (!item.price_complete) return "decision-price";
      return item.profitable ? "decision-build" : "decision-skip";
    }

    function renderPlanetaryMaterialSummary(item) {
      const inputs = (item.inputs || []).map((material) => `${escapeHtml(material.name)} x${formatNumber(material.quantity)}`).join(", ");
      const outputs = (item.outputs || []).map((material) => `${escapeHtml(material.name)} x${formatNumber(material.quantity)}`).join(", ");
      return `Inputs: ${inputs || "unknown"}; outputs: ${outputs || "unknown"}.`;
    }

    function renderPlanetaryMathDetails(item) {
      const rows = [
        ["Output buy-order value", formatIsk(item.output_value)],
        ["Imported input sell-order value", `-${formatIsk(item.input_value)}`],
        ["Import customs", `-${formatIsk(item.import_customs_cost)}`],
        ["Export customs", `-${formatIsk(item.export_customs_cost)}`],
        ["Customs transfer cost", `-${formatIsk(item.customs_transfer_cost)}`],
        ["Sales tax", `-${formatIsk(item.sales_tax)}`],
        ["Broker fee", `-${formatIsk(item.broker_fee)}`],
        ["Break-even customs rate", formatPercent(item.break_even_export_tax_percent)],
        ["Profit per hour", formatSignedIsk(item.profit_per_hour)],
      ];
      return `
        <details class="profit-details">
          <summary>PI math details</summary>
          <div class="profit-detail-grid">
            ${rows.map(([label, value]) => `
              <div class="profit-detail-row"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>
            `).join("")}
          </div>
        </details>
      `;
    }

    function clampReprocessQuantity(value) {
      const quantity = Number(value);
      if (!Number.isFinite(quantity)) return 100;
      return Math.max(1, Math.min(1000000000000, Math.floor(quantity)));
    }

    function readOptionalPercentInput(input) {
      const text = String(input.value || "").trim();
      if (!text) return "";
      const percent = Number(text);
      if (!Number.isFinite(percent)) return "";
      return String(Math.max(0, Math.min(100, percent)));
    }

    function normalizeReprocessingStationSort(value) {
      const clean = String(value || "").trim();
      return ["net_yield", "processing_fee", "standing"].includes(clean) ? clean : "net_yield";
    }

    function reprocessingStationSortLabel(value) {
      const sort = normalizeReprocessingStationSort(value);
      if (sort === "processing_fee") return "lowest processing fee";
      if (sort === "standing") return "highest standing";
      return "best net yield";
    }

    function readReprocessingSettings() {
      return {
        oreTypeId: String(window.localStorage.getItem(reprocessOreKey) || reprocessOre.value || "").trim(),
        quantity: clampReprocessQuantity(window.localStorage.getItem(reprocessQuantityKey) || reprocessQuantity.value || 100),
        locationId: String(window.localStorage.getItem(reprocessLocationKey) || reprocessStationSelect.value || "current").trim() || "current",
        stationSort: normalizeReprocessingStationSort(window.localStorage.getItem(reprocessStationSortKey) || reprocessStationSort.value),
        facilityYieldPercent: String(window.localStorage.getItem(reprocessFacilityYieldKey) || reprocessFacilityYield.value || "").trim(),
        stationTaxPercent: String(window.localStorage.getItem(reprocessStationTaxKey) || reprocessStationTax.value || "").trim(),
        implantBonusPercent: String(window.localStorage.getItem(reprocessImplantBonusKey) || reprocessImplantBonus.value || "").trim(),
        structureBonusPercent: String(window.localStorage.getItem(reprocessStructureBonusKey) || reprocessStructureBonus.value || "0").trim() || "0",
      };
    }

    function writeReprocessingSettings(settings) {
      const availableOreValues = Array.from(reprocessOre.options).map((option) => option.value).filter(Boolean);
      const oreTypeId = availableOreValues.includes(String(settings.oreTypeId || ""))
        ? String(settings.oreTypeId || "")
        : String(reprocessOre.value || availableOreValues[0] || "");
      const quantity = clampReprocessQuantity(settings.quantity);
      const availableLocationValues = Array.from(reprocessStationSelect.options).map((option) => option.value).filter(Boolean);
      const requestedLocationId = String(settings.locationId || "current").trim() || "current";
      const locationId = availableLocationValues.includes(requestedLocationId)
        ? requestedLocationId
        : (requestedLocationId !== "current" && availableLocationValues.length <= 1 ? requestedLocationId : "current");
      const stationSort = normalizeReprocessingStationSort(settings.stationSort || reprocessStationSort.value);
      const facilityYieldPercent = String(settings.facilityYieldPercent || "").trim();
      const stationTaxPercent = String(settings.stationTaxPercent || "").trim();
      const implantBonusPercent = String(settings.implantBonusPercent || "").trim();
      const structureBonusPercent = String(settings.structureBonusPercent || "0").trim() || "0";
      reprocessOre.value = oreTypeId;
      reprocessQuantity.value = String(quantity);
      reprocessStationSelect.value = availableLocationValues.includes(locationId) ? locationId : "current";
      reprocessStationSort.value = stationSort;
      reprocessFacilityYield.value = facilityYieldPercent;
      reprocessStationTax.value = stationTaxPercent;
      reprocessImplantBonus.value = implantBonusPercent;
      reprocessStructureBonus.value = structureBonusPercent;
      window.localStorage.setItem(reprocessOreKey, oreTypeId);
      window.localStorage.setItem(reprocessQuantityKey, String(quantity));
      window.localStorage.setItem(reprocessLocationKey, locationId);
      window.localStorage.setItem(reprocessStationSortKey, stationSort);
      window.localStorage.setItem(reprocessFacilityYieldKey, facilityYieldPercent);
      window.localStorage.setItem(reprocessStationTaxKey, stationTaxPercent);
      window.localStorage.setItem(reprocessImplantBonusKey, implantBonusPercent);
      window.localStorage.setItem(reprocessStructureBonusKey, structureBonusPercent);
      return {
        oreTypeId,
        quantity,
        locationId,
        stationSort,
        facilityYieldPercent,
        stationTaxPercent,
        implantBonusPercent,
        structureBonusPercent,
      };
    }

    function resetReprocessing(message) {
      reprocessSummary.innerHTML = renderReprocessingPendingAssay(message);
      reprocessLocationDetail.innerHTML = "";
      reprocessResults.innerHTML = renderReprocessingEmptyWorkbench(message);
      reprocessLocationStatus.textContent = message;
      reprocessCalculateButton.disabled = false;
      reprocessRefreshLocations.disabled = false;
    }

    function clearReprocessingLocations(message, disabled = false) {
      reprocessStationSelect.innerHTML = `<option value="current">Use current ESI location</option>`;
      reprocessStationSelect.value = "current";
      window.localStorage.setItem(reprocessLocationKey, "current");
      reprocessLocationStatus.textContent = message;
      reprocessRefreshLocations.disabled = disabled;
    }

    async function loadReprocessingLocations() {
      const settings = writeReprocessingSettings({
        oreTypeId: reprocessOre.value,
        quantity: reprocessQuantity.value,
        locationId: String(window.localStorage.getItem(reprocessLocationKey) || reprocessStationSelect.value || "current"),
        stationSort: reprocessStationSort.value,
        facilityYieldPercent: readOptionalPercentInput(reprocessFacilityYield),
        stationTaxPercent: readOptionalPercentInput(reprocessStationTax),
        implantBonusPercent: readOptionalPercentInput(reprocessImplantBonus),
        structureBonusPercent: readOptionalPercentInput(reprocessStructureBonus) || "0",
      });
      if (!settings.oreTypeId) {
        clearReprocessingLocations("Run the cache refresh before ranking reprocessing stations over 1.5 standing.");
        return;
      }
      reprocessRefreshLocations.disabled = true;
      reprocessLocationStatus.textContent = "Loading stations over 1.5 standing from ESI standings and local SDE data...";
      const params = new URLSearchParams({
        ore_type_id: settings.oreTypeId,
        sort_mode: settings.stationSort,
        structure_bonus_percent: settings.structureBonusPercent || "0",
      });
      if (settings.implantBonusPercent) params.set("implant_bonus_percent", settings.implantBonusPercent);
      try {
        const response = await fetch(`/api/flight/reprocessing-locations?${params}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not load reprocessing locations");
        renderReprocessingLocations(data, settings.locationId);
      } catch (error) {
        reprocessLocationStatus.textContent = error.message;
      } finally {
        reprocessRefreshLocations.disabled = false;
      }
    }

    function renderReprocessingLocations(data, selectedLocationId) {
      const current = data.current_location || {};
      const stations = Array.isArray(data.stations) ? data.stations : [];
      reprocessStationSelect.innerHTML = "";
      const currentOption = document.createElement("option");
      currentOption.value = "current";
      currentOption.textContent = current.location_name
        ? `Use current ESI location - ${current.location_name}`
        : "Use current ESI location";
      reprocessStationSelect.appendChild(currentOption);
      for (const station of stations) {
        const option = document.createElement("option");
        option.value = String(station.station_id || "");
        option.textContent = station.label || `${formatPercent(station.net_yield_percent)} net - Station ${station.station_id}`;
        reprocessStationSelect.appendChild(option);
      }
      const availableValues = Array.from(reprocessStationSelect.options).map((option) => option.value);
      reprocessStationSelect.value = availableValues.includes(String(selectedLocationId || "current"))
        ? String(selectedLocationId || "current")
        : "current";
      window.localStorage.setItem(reprocessLocationKey, reprocessStationSelect.value);
      const total = Number(data.total_matching_stations || stations.length || 0);
      const sortLabel = data.sort_label || reprocessingStationSortLabel(data.sort_mode || reprocessStationSort.value);
      if (!stations.length) {
        reprocessLocationStatus.textContent = "No NPC reprocessing stations matched standings over 1.5.";
      } else if (data.truncated) {
        reprocessLocationStatus.textContent = `Showing top ${formatNumber(stations.length)} of ${formatNumber(total)} NPC stations over 1.5 standing, sorted by ${sortLabel}.`;
      } else {
        reprocessLocationStatus.textContent = `Loaded ${formatNumber(stations.length)} NPC stations over 1.5 standing, sorted by ${sortLabel}.`;
      }
    }

    async function loadReprocessingCalculation() {
      const settings = writeReprocessingSettings({
        oreTypeId: reprocessOre.value,
        quantity: reprocessQuantity.value,
        locationId: String(window.localStorage.getItem(reprocessLocationKey) || reprocessStationSelect.value || "current"),
        stationSort: reprocessStationSort.value,
        facilityYieldPercent: readOptionalPercentInput(reprocessFacilityYield),
        stationTaxPercent: readOptionalPercentInput(reprocessStationTax),
        implantBonusPercent: readOptionalPercentInput(reprocessImplantBonus),
        structureBonusPercent: readOptionalPercentInput(reprocessStructureBonus) || "0",
      });
      if (!settings.oreTypeId) {
        resetReprocessing("Reprocessing cache is missing ore options. Run the cache refresh first.");
        return;
      }
      reprocessCalculateButton.disabled = true;
      reprocessSummary.innerHTML = renderReprocessingPendingAssay(`Calculating ${formatNumber(settings.quantity)} ore units...`);
      reprocessLocationDetail.innerHTML = "";
      reprocessResults.innerHTML = renderReprocessingEmptyWorkbench("Mineral output will appear here when the calculation finishes.");
      const params = new URLSearchParams({
        ore_type_id: settings.oreTypeId,
        quantity: String(settings.quantity),
        structure_bonus_percent: settings.structureBonusPercent || "0",
      });
      if (settings.locationId && settings.locationId !== "current") {
        params.set("reprocessing_station_id", settings.locationId);
      }
      if (settings.facilityYieldPercent) params.set("facility_yield_percent", settings.facilityYieldPercent);
      if (settings.stationTaxPercent) params.set("station_tax_percent", settings.stationTaxPercent);
      if (settings.implantBonusPercent) params.set("implant_bonus_percent", settings.implantBonusPercent);
      try {
        const response = await fetch(`/api/flight/reprocessing?${params}`);
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Could not calculate ore reprocessing");
        renderReprocessingCalculation(data);
      } catch (error) {
        reprocessSummary.innerHTML = renderReprocessingPendingAssay(error.message);
        reprocessLocationDetail.innerHTML = "";
        reprocessResults.innerHTML = renderReprocessingEmptyWorkbench(error.message);
      } finally {
        reprocessCalculateButton.disabled = false;
      }
    }

    function renderAssayStatusMetric(label, valueHtml, note, className = "") {
      return `
        <div class="assay-status-metric ${escapeHtml(className)}">
          <span>${escapeHtml(label)}</span>
          <b>${valueHtml}</b>
          ${note ? `<small>${escapeHtml(note)}</small>` : ""}
        </div>
      `;
    }

    function renderAssayStatusRow(label, valueHtml, noteHtml = "") {
      return `
        <div class="assay-status-row">
          <span>${escapeHtml(label)}</span>
          <b>${valueHtml}</b>
          ${noteHtml ? `<small>${noteHtml}</small>` : ""}
        </div>
      `;
    }

    function renderReprocessingPendingAssay(message) {
      const ore = selectedReprocessingOreSummary();
      const cleanMessage = message || "Connect ESI, choose ore, and enter the amount to calculate.";
      return `
        <div class="assay-status-ledger is-pending">
          <div class="assay-status-strip">
            ${renderAssayStatusMetric("Net Yield", "Pending", "Awaiting assay", "is-pending")}
            ${renderAssayStatusMetric("Processing Fee", "Pending", "Standing not checked yet", "is-pending")}
            ${renderAssayStatusMetric("Jita Buy Delta", "Pending", "Live buy-order value", "is-pending")}
            ${renderAssayStatusMetric("EVE Est. Delta", "Pending", "Cluster estimate value", "is-pending")}
          </div>
          <div class="assay-status-sheet">
            <span class="notebook-ribbon">Assay Log</span>
            ${renderAssayStatusRow("Sample", escapeHtml(ore.name || "Ore sample"), escapeHtml(cleanMessage))}
            ${renderAssayStatusRow("Skills", "Pending ESI character data", "The calculator reads reprocessing skills when the connected session has the needed scopes.")}
            ${renderAssayStatusRow("Values", "Pending market check", "Jita buy-order and EVE estimated-price comparisons appear after calculation.")}
          </div>
        </div>
      `;
    }

    function renderReprocessingCalculation(data) {
      const valuation = data.jita_valuation || {};
      const notes = Array.isArray(data.notes) ? data.notes : [];
      const valuationNotes = Array.isArray(valuation.notes) ? valuation.notes : [];
      reprocessSummary.innerHTML = renderReprocessingAssayStatus(data);
      reprocessLocationDetail.innerHTML = renderReprocessingFacilityStatus(data);
      reprocessResults.innerHTML = renderReprocessingMaterials(data, notes.concat(valuationNotes));
    }

    function renderReprocessingAssayStatus(data) {
      const ore = data.ore || {};
      const input = data.input || {};
      const skills = data.skills || {};
      const yieldData = data.yield || {};
      const valuation = data.jita_valuation || {};
      const eveEstimate = valuation.eve_estimate || {};
      const specializationName = skills.specialization_skill_name || "specialization";
      const oreName = ore.name || "Ore";
      const sampleLine = `${escapeHtml(oreName)} x${formatNumber(input.quantity)}`;
      const sampleNote = `Portion ${formatNumber(input.portion_size)}; leftovers ${formatNumber(input.leftover_units)}; ${formatNumber(input.portions)} full portion${Number(input.portions || 0) === 1 ? "" : "s"} processed.`;
      const skillsLine = `Reprocessing ${formatNumber(skills.reprocessing_level)}, Reprocessing Efficiency ${formatNumber(skills.reprocessing_efficiency_level)}, ${escapeHtml(specializationName)} ${formatNumber(skills.specialization_level)}`;
      const systemName = escapeHtml((valuation.system || {}).name || "Jita");
      return `
        <div class="assay-status-ledger">
          <div class="assay-status-strip">
            ${renderAssayStatusMetric("Net Yield", formatPercent(yieldData.net_yield_percent), "after processing fee", "is-green")}
            ${renderAssayStatusMetric("Processing Fee", formatPercent(yieldData.station_tax_percent), "station tax taken as minerals", "is-amber")}
            ${renderAssayStatusMetric("Jita Buy Delta", renderReprocessingJitaDelta(valuation), "liquidation value", "is-amber")}
            ${renderAssayStatusMetric("EVE Est. Delta", renderReprocessingEveEstimateDelta(eveEstimate), "cluster estimate", "")}
          </div>
          <div class="assay-status-sheet">
            <span class="notebook-ribbon">Assay Log</span>
            ${renderAssayStatusRow("Sample", sampleLine, escapeHtml(sampleNote))}
            ${renderAssayStatusRow("Skills", skillsLine, "ESI character skills plus any manual override values used for this calculation.")}
            ${renderAssayStatusRow("Why this number", renderReprocessingYieldSentence(yieldData))}
            ${renderAssayStatusRow(
              "EVE estimated price",
              `processed ${renderReprocessingJitaValue(eveEstimate.processed_material_value, eveEstimate.processed_partial_material_value)}; ore ${renderReprocessingJitaValue(eveEstimate.ore_value, null)}; delta ${renderReprocessingEveEstimateDelta(eveEstimate)}.`,
              escapeHtml(eveEstimate.note || "This is a cluster market estimate, not Jita buy-order liquidation value.")
            )}
            ${renderAssayStatusRow(
              "Jita buy-order value",
              `processed ${renderReprocessingJitaValue(valuation.processed_material_value, valuation.processed_partial_material_value)}; ore ${renderReprocessingJitaValue(valuation.ore_value, valuation.ore_partial_value)}; delta ${renderReprocessingJitaDelta(valuation)}.`,
              `Public buy orders in ${systemName}; ${escapeHtml(renderReprocessingJitaCoverage(valuation))}.`
            )}
          </div>
        </div>
      `;
    }

    function renderReprocessingFacilityStatus(data) {
      const facility = data.facility || {};
      const yieldData = data.yield || {};
      const cache = data.cache || {};
      const owner = facility.owner_name || (facility.owner_id ? `Owner ${facility.owner_id}` : "unknown owner");
      const standing = facility.standing == null ? "unknown" : Number(facility.standing).toFixed(2);
      const standingSource = facility.standing_source ? ` (${escapeHtml(facility.standing_source)})` : "";
      const cacheValue = cache.available ? `build ${escapeHtml(cache.build_number || "unknown")}` : escapeHtml(cache.error || "missing");
      return `
        <div class="assay-status-facility-card">
          <span class="notebook-ribbon">Facility Row</span>
          ${renderAssayStatusRow(
            "Location",
            `<strong>${escapeHtml(facility.location_name || "current location")}</strong> (${escapeHtml(facility.location_kind || "unknown")})`,
            `Owner ${escapeHtml(owner)}; standing ${escapeHtml(standing)}${standingSource}.`
          )}
          ${renderAssayStatusRow("Processing fee", renderReprocessingFeeBreakdown(facility, yieldData))}
          ${renderAssayStatusRow("Standing row used", renderReprocessingStandingRow(facility))}
          ${renderAssayStatusRow("Static cache", cacheValue)}
        </div>
      `;
    }

    function renderReprocessingYieldSentence(yieldData) {
      const breakdown = yieldData.breakdown || {};
      const specializationName = breakdown.specialization_skill_name || "ore specialization";
      return `
        ${formatPercent(breakdown.facility_yield_percent)} facility
        ${formatMultiplier(breakdown.reprocessing_multiplier)} Reprocessing
        ${formatMultiplier(breakdown.reprocessing_efficiency_multiplier)} Reprocessing Efficiency
        ${formatMultiplier(breakdown.specialization_multiplier)} ${escapeHtml(specializationName)}
        ${formatMultiplier(breakdown.implant_multiplier)} implant
        ${formatMultiplier(breakdown.structure_multiplier)} structure
        = ${formatPercent(breakdown.gross_yield_percent)} gross; after
        ${formatPercent(breakdown.processing_fee_percent)} processing fee = ${formatPercent(breakdown.net_yield_percent)} net.
      `;
    }

    function formatMultiplier(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return "x?";
      return `x${number.toFixed(4)}`;
    }

    function renderReprocessingYieldBreakdown(yieldData) {
      const breakdown = yieldData.breakdown || {};
      const specializationName = breakdown.specialization_skill_name || "ore specialization";
      return `
        <div class="meta"><strong>Why this number:</strong>
          ${formatPercent(breakdown.facility_yield_percent)} facility
          ${formatMultiplier(breakdown.reprocessing_multiplier)} Reprocessing
          ${formatMultiplier(breakdown.reprocessing_efficiency_multiplier)} Reprocessing Efficiency
          ${formatMultiplier(breakdown.specialization_multiplier)} ${escapeHtml(specializationName)}
          ${formatMultiplier(breakdown.implant_multiplier)} implant
          ${formatMultiplier(breakdown.structure_multiplier)} structure
          = ${formatPercent(breakdown.gross_yield_percent)} gross; after
          ${formatPercent(breakdown.processing_fee_percent)} processing fee = ${formatPercent(breakdown.net_yield_percent)} net.
        </div>
      `;
    }

    function renderReprocessingFeeBreakdown(facility, yieldData) {
      const base = Number(facility.base_station_tax_percent);
      const adjusted = Number(facility.adjusted_station_tax_percent ?? yieldData.station_tax_percent);
      const reduction = Number.isFinite(base) && Number.isFinite(adjusted) ? Math.max(0, base - adjusted) : null;
      const reductionText = reduction == null ? "" : `; standing reduction ${formatPercent(reduction)}`;
      return `Processing fee: <strong>${formatPercent(adjusted)}</strong> adjusted from ${formatPercent(base)} base${reductionText}; source ${escapeHtml(facility.station_tax_source || "unknown")}.`;
    }

    function renderReprocessingStandingRow(facility) {
      const row = facility.standing_row || {};
      const owner = facility.owner_name || (facility.owner_id ? `Owner ${facility.owner_id}` : "unknown owner");
      if (row.standing == null) {
        return `Standing row used: none found for owner ${escapeHtml(owner)} (${escapeHtml(facility.owner_id || "unknown")}); base processing fee is used.`;
      }
      return `Standing row used: ${escapeHtml(row.from_type || row.raw_from_type || "unknown")} ${escapeHtml(row.from_id || "")} (${escapeHtml(owner)}), standing ${Number(row.standing).toFixed(2)}.`;
    }

    function renderReprocessingJitaValue(value, partialValue) {
      if (value != null) return formatIsk(value);
      if (partialValue != null) return `${formatIsk(partialValue)} partial`;
      return "unknown";
    }

    function renderReprocessingJitaDelta(valuation) {
      if (valuation.value_delta != null) return formatSignedIsk(valuation.value_delta);
      if (valuation.partial_value_delta != null) return `${formatSignedIsk(valuation.partial_value_delta)} partial`;
      return "unknown";
    }

    function renderReprocessingEveEstimateDelta(eveEstimate) {
      if (eveEstimate.value_delta != null) return formatSignedIsk(eveEstimate.value_delta);
      if (eveEstimate.partial_value_delta != null) return `${formatSignedIsk(eveEstimate.partial_value_delta)} partial`;
      return "unknown";
    }

    function renderReprocessingPriceComparison(valuation) {
      const eveEstimate = valuation.eve_estimate || {};
      return `
        <div class="meta"><strong>EVE estimated price:</strong>
          processed ${renderReprocessingJitaValue(eveEstimate.processed_material_value, eveEstimate.processed_partial_material_value)};
          ore ${renderReprocessingJitaValue(eveEstimate.ore_value, null)};
          delta ${renderReprocessingEveEstimateDelta(eveEstimate)}.
          <small>${escapeHtml(eveEstimate.note || "Uses ESI market estimate prices, not live buy orders.")}</small>
        </div>
        <div class="meta"><strong>Jita buy-order value:</strong>
          processed ${renderReprocessingJitaValue(valuation.processed_material_value, valuation.processed_partial_material_value)};
          ore ${renderReprocessingJitaValue(valuation.ore_value, valuation.ore_partial_value)};
          delta ${renderReprocessingJitaDelta(valuation)}.
          <small>Public buy orders in ${escapeHtml((valuation.system || {}).name || "Jita")}; ${renderReprocessingJitaCoverage(valuation)}.</small>
        </div>
      `;
    }

    function renderReprocessingJitaCoverage(valuation) {
      const full = Number(valuation.processed_material_types || 0);
      const partial = Number(valuation.processed_partial_material_types || 0);
      const total = Number(valuation.processed_required_material_types || 0);
      const orePriced = Number(valuation.ore_priced_quantity || 0);
      const oreRequired = Number(valuation.ore_required_quantity || 0);
      const materialCoverage = total
        ? `${formatNumber(full)}/${formatNumber(total)} output material types covered`
        : "no output materials to price";
      const oreCoverage = oreRequired
        ? `${formatNumber(orePriced)}/${formatNumber(oreRequired)} ore units priced`
        : "ore quantity unknown";
      if (partial > full) return `${materialCoverage}, ${formatNumber(partial)} with partial depth; ${oreCoverage}`;
      return `${materialCoverage}; ${oreCoverage}`;
    }

    function eveTypeIconUrl(typeId, size = 64) {
      const cleanTypeId = Number(typeId || 0);
      const cleanSize = [32, 64, 128, 256].includes(Number(size)) ? Number(size) : 64;
      if (!Number.isFinite(cleanTypeId) || cleanTypeId <= 0) return "";
      return `https://images.evetech.net/types/${cleanTypeId}/icon?size=${cleanSize}`;
    }

    function reprocessingSampleAccent(typeId) {
      const accents = {
        34: "#c8ac78",
        35: "#d9865f",
        36: "#d56f86",
        37: "#7fcca1",
        38: "#b78be6",
        39: "#65d5c7",
        40: "#e3d383",
        11399: "#9fb2ff",
      };
      const cleanTypeId = Number(typeId || 0);
      if (accents[cleanTypeId]) return accents[cleanTypeId];
      const palette = ["#61c7d9", "#e0a84a", "#64c47d", "#d56f86", "#b78be6"];
      return palette[Math.abs(cleanTypeId || 0) % palette.length];
    }

    function renderEveTypeIcon(typeId, name, className, size = 64) {
      const url = eveTypeIconUrl(typeId, size);
      const accent = reprocessingSampleAccent(typeId);
      const safeName = escapeHtml(name || "EVE item");
      const missing = `<span class="specimen-image-frame ${escapeHtml(className)} image-missing" style="--sample-accent: ${escapeHtml(accent)}"></span>`;
      if (!url) return missing;
      return `
        <span class="specimen-image-frame ${escapeHtml(className)}" style="--sample-accent: ${escapeHtml(accent)}">
          <img src="${escapeHtml(url)}" alt="${safeName}" loading="lazy" decoding="async" onerror="this.closest('.specimen-image-frame').classList.add('image-missing'); this.remove();">
        </span>
      `;
    }

    function selectedReprocessingOreSummary() {
      const selected = reprocessOre.options[reprocessOre.selectedIndex];
      const label = selected ? String(selected.textContent || "").trim() : "";
      const oreName = label ? label.split(" - ")[0].trim() : "Ore sample";
      return {
        typeId: Number(reprocessOre.value || 0),
        name: oreName || "Ore sample",
      };
    }

    function renderReprocessingEmptyWorkbench(message) {
      const ore = selectedReprocessingOreSummary();
      const accents = ["#61c7d9", "#e0a84a", "#64c47d"];
      const emptyCards = ["Crush", "Separate", "Assay"].map((label, index) => `
        <article class="mineral-card is-empty" style="--sample-accent: ${escapeHtml(accents[index])}">
          <span class="specimen-image-frame mineral-image image-missing" style="--sample-accent: ${escapeHtml(accents[index])}"></span>
          <div class="mineral-copy">
            <div class="mineral-card-head">
              <strong>${escapeHtml(label)}</strong>
              <span class="sample-qty">Pending</span>
            </div>
            <div class="sample-flow"><span>Awaiting calculation</span></div>
          </div>
        </article>
      `).join("");
      return `
        <div class="reprocess-field-desk">
          ${renderReprocessingStatusRail({})}
          <aside class="ore-specimen">
            ${renderEveTypeIcon(ore.typeId, ore.name, "ore-image", 128)}
            <div class="ore-copy">
              <span class="bench-label">Selected Ore</span>
              <strong>${escapeHtml(ore.name)}</strong>
              <p>${escapeHtml(message || "Run the calculation to inspect recovered minerals.")}</p>
            </div>
          </aside>
          ${renderReprocessingEmptyNotebook(message)}
          <div class="mineral-tray">
            <div class="tray-heading">
              <strong>Recovered Minerals</strong>
              <span>waiting</span>
            </div>
            <div class="mineral-grid">${emptyCards}</div>
          </div>
          <div class="reprocess-desk-footer">
            <span><b>Images:</b> EVE Image Server type icons.</span>
            <span><b>Status:</b> Awaiting a completed assay.</span>
          </div>
        </div>
      `;
    }

    function renderReprocessingStatusRail(data) {
      const facility = data.facility || {};
      const yieldData = data.yield || {};
      const owner = facility.owner_name || (facility.owner_id ? `Owner ${facility.owner_id}` : "pending");
      const standing = facility.standing == null ? "unknown" : Number(facility.standing).toFixed(2);
      const baseFee = facility.base_station_tax_percent == null ? "pending" : formatPercent(facility.base_station_tax_percent);
      const adjustedTax = facility.adjusted_station_tax_percent ?? facility.station_tax_percent ?? yieldData.station_tax_percent;
      const adjustedFee = facility.adjusted_station_tax_percent == null && yieldData.station_tax_percent == null
        ? "pending"
        : formatPercent(facility.adjusted_station_tax_percent ?? yieldData.station_tax_percent);
      return `
        <div class="reprocess-status-rail">
          <div class="status-rail-cell"><span>Location</span><b>${escapeHtml(facility.location_name || "Pending location")}</b><small>${escapeHtml(facility.location_kind || "current ESI location")}</small></div>
          <div class="status-rail-cell"><span>Facility</span><b>${escapeHtml(owner)}</b><small>${escapeHtml(facility.source || "waiting for ESI")}</small></div>
          <div class="status-rail-cell"><span>Standing</span><b>${escapeHtml(standing)}</b><small>${adjustedTax == null ? "tax pending" : `${formatPercent(adjustedTax)} tax`}</small></div>
          <div class="status-rail-cell"><span>Total Efficiency</span><b class="green">${formatPercent(yieldData.net_yield_percent)}</b><small>Net yield</small></div>
          <div class="status-rail-cell"><span>Processing Fee</span><b>${escapeHtml(adjustedFee)}</b><small>Base ${escapeHtml(baseFee)}</small></div>
        </div>
      `;
    }

    function renderReprocessingEmptyNotebook(message) {
      return `
        <section class="field-notebook">
          <div class="notebook-section">
            <span class="notebook-ribbon">Yield Breakdown</span>
            <div class="notebook-note">${escapeHtml(message || "Run the calculation to fill the assay notebook.")}</div>
          </div>
          <div class="notebook-section">
            <span class="notebook-ribbon">Processing Fee</span>
            <div class="notebook-fee-card">
              <div><span>Base Fee</span><b>Pending</b></div>
              <div class="fee-arrow">-></div>
              <div><span>Adjusted Fee</span><b>Pending</b></div>
            </div>
          </div>
          <div class="notebook-section">
            <span class="notebook-ribbon">Value Comparison</span>
            <div class="notebook-note">Jita buy-order and EVE estimated-price values appear here after the calculation.</div>
          </div>
        </section>
      `;
    }

    function renderReprocessingOreSpecimen(data) {
      const ore = data.ore || {};
      const input = data.input || {};
      const yieldData = data.yield || {};
      const valuation = data.jita_valuation || {};
      const eveEstimate = valuation.eve_estimate || {};
      return `
        <aside class="ore-specimen">
          ${renderEveTypeIcon(ore.type_id, ore.name || "Ore", "ore-image", 128)}
          <div class="ore-copy">
            <span class="bench-label">Input Sample</span>
            <strong>${escapeHtml(ore.name || "Ore")}</strong>
            <p>${escapeHtml(ore.group_name || "Ore")} assay; ${formatNumber(input.portions)} full portion${Number(input.portions || 0) === 1 ? "" : "s"} processed.</p>
          </div>
          <div class="ore-readouts">
            <div class="assay-cell"><span>Units</span><b>${formatNumber(input.quantity)}</b><small>${formatNumber(input.leftover_units)} leftover</small></div>
            <div class="assay-cell"><span>Net Yield</span><b>${formatPercent(yieldData.net_yield_percent)}</b><small>${formatPercent(yieldData.station_tax_percent)} fee</small></div>
            <div class="assay-cell"><span>Ore Jita Buy</span><b>${renderReprocessingJitaValue(valuation.ore_value, valuation.ore_partial_value)}</b><small>${formatNumber(valuation.ore_priced_quantity || 0)} priced</small></div>
            <div class="assay-cell"><span>Ore EVE Est.</span><b>${renderReprocessingJitaValue(eveEstimate.ore_value, null)}</b><small>${escapeHtml(eveEstimate.ore_price_source || "estimate")}</small></div>
          </div>
        </aside>
      `;
    }

    function renderReprocessingNotebook(data, notes) {
      const facility = data.facility || {};
      const yieldData = data.yield || {};
      const breakdown = yieldData.breakdown || {};
      const valuation = data.jita_valuation || {};
      const eveEstimate = valuation.eve_estimate || {};
      const specializationName = breakdown.specialization_skill_name || "Ore specialization";
      const baseFee = facility.base_station_tax_percent == null ? yieldData.station_tax_percent : facility.base_station_tax_percent;
      const adjustedFee = facility.adjusted_station_tax_percent ?? yieldData.station_tax_percent;
      const noteRows = (notes || []).slice(0, 4).map((note) => `<div class="notebook-note">${escapeHtml(note)}</div>`).join("");
      const row = facility.standing_row || {};
      const owner = facility.owner_name || (facility.owner_id ? `Owner ${facility.owner_id}` : "unknown owner");
      const standingText = row.standing == null
        ? `No standing row found for ${owner}; base fee is used.`
        : `${row.from_type || row.raw_from_type || "unknown"} ${row.from_id || ""} (${owner}), standing ${Number(row.standing).toFixed(2)}.`;
      return `
        <section class="field-notebook">
          <div class="notebook-section">
            <span class="notebook-ribbon">Yield Breakdown</span>
            <div class="notebook-math-row"><span>Facility base yield</span><b>${formatPercent(breakdown.facility_yield_percent)}</b></div>
            <div class="notebook-math-row"><span>Reprocessing</span><b>${formatMultiplier(breakdown.reprocessing_multiplier)}</b></div>
            <div class="notebook-math-row"><span>Reprocessing Efficiency</span><b>${formatMultiplier(breakdown.reprocessing_efficiency_multiplier)}</b></div>
            <div class="notebook-math-row"><span>${escapeHtml(specializationName)}</span><b>${formatMultiplier(breakdown.specialization_multiplier)}</b></div>
            <div class="notebook-math-row"><span>Implant</span><b>${formatMultiplier(breakdown.implant_multiplier)}</b></div>
            <div class="notebook-math-row"><span>Structure bonus</span><b>${formatMultiplier(breakdown.structure_multiplier)}</b></div>
            <div class="notebook-math-row"><span>Gross yield</span><b>${formatPercent(breakdown.gross_yield_percent)}</b></div>
            <div class="notebook-math-row"><span>Processing fee</span><b>-${formatPercent(breakdown.processing_fee_percent)}</b></div>
            <div class="notebook-math-row is-total"><span>Net yield</span><b>${formatPercent(breakdown.net_yield_percent)}</b></div>
          </div>
          <div class="notebook-section">
            <span class="notebook-ribbon">Processing Fee</span>
            <div class="notebook-fee-card">
              <div><span>Base Fee</span><b>${formatPercent(baseFee)}</b></div>
              <div class="fee-arrow">-></div>
              <div><span>Adjusted Fee</span><b>${formatPercent(adjustedFee)}</b></div>
            </div>
          </div>
          <div class="notebook-section">
            <span class="notebook-ribbon">Value Comparison</span>
            <div class="notebook-value-grid">
              <div class="notebook-value-row"><span>EVE estimate: ore</span><b>${renderReprocessingJitaValue(eveEstimate.ore_value, null)}</b></div>
              <div class="notebook-value-row"><span>EVE estimate: processed</span><b>${renderReprocessingJitaValue(eveEstimate.processed_material_value, eveEstimate.processed_partial_material_value)}</b></div>
              <div class="notebook-value-row is-total"><span>EVE estimate delta</span><b>${renderReprocessingEveEstimateDelta(eveEstimate)}</b></div>
              <div class="notebook-value-row"><span>Jita buy: ore</span><b>${renderReprocessingJitaValue(valuation.ore_value, valuation.ore_partial_value)}</b></div>
              <div class="notebook-value-row"><span>Jita buy: processed</span><b>${renderReprocessingJitaValue(valuation.processed_material_value, valuation.processed_partial_material_value)}</b></div>
              <div class="notebook-value-row is-total"><span>Jita buy delta</span><b>${renderReprocessingJitaDelta(valuation)}</b></div>
            </div>
          </div>
          <div class="notebook-section">
            <span class="notebook-ribbon">Standing Row Used</span>
            <div class="notebook-standing-row"><span>${escapeHtml(standingText)}</span><b>${escapeHtml(facility.standing_source || "")}</b></div>
          </div>
          ${noteRows ? `<div class="notebook-section"><span class="notebook-ribbon">Notes</span>${noteRows}</div>` : ""}
        </section>
      `;
    }

    function renderReprocessingMaterials(data, notes) {
      const materials = data.materials || [];
      if (!materials.length) {
        return `
          <div class="reprocess-field-desk">
            ${renderReprocessingStatusRail(data)}
            ${renderReprocessingOreSpecimen(data)}
            ${renderReprocessingNotebook(data, (notes || []).concat(["No output materials were calculated."]))}
            <div class="mineral-tray">
              <div class="tray-heading">
                <strong>Recovered Minerals</strong>
                <span>0 stacks</span>
              </div>
              <div class="decision-empty">No output materials were calculated.</div>
              <div class="minerals-total">
                <span>Minerals Total (Jita Buy)</span>
                <b>unknown</b>
              </div>
            </div>
            <div class="reprocess-desk-footer">
              <span><b>Skills:</b> Reprocessing ${formatNumber((data.skills || {}).reprocessing_level)}, Reprocessing Efficiency ${formatNumber((data.skills || {}).reprocessing_efficiency_level)}</span>
              <span><b>Cache:</b> ${escapeHtml((data.cache || {}).build_number || "unknown")}</span>
            </div>
          </div>
        `;
      }
      const rows = materials.map((material) => `
        <article class="mineral-card" style="--sample-accent: ${escapeHtml(reprocessingSampleAccent(material.type_id))}">
          ${renderEveTypeIcon(material.type_id, material.name, "mineral-image", 64)}
          <div class="mineral-copy">
            <div class="mineral-card-head">
              <strong>${escapeHtml(material.name)}</strong>
              <span class="sample-qty">${formatNumber(material.net_quantity)}</span>
            </div>
            <div class="sample-ledger">
              <div class="decision-metric"><span>EVE Est.</span><b>${renderReprocessingJitaValue(material.eve_estimate_value, null)}</b><small>${renderReprocessingEveMaterialEstimate(material)}</small></div>
              <div class="decision-metric"><span>Jita Buy</span><b>${renderReprocessingJitaValue(material.jita_value, null)}</b><small>${renderReprocessingMaterialJitaDepth(material)}</small></div>
            </div>
            <div class="sample-flow">
              <span>Base ${formatNumber(material.base_quantity)}</span>
              <span>Gross ${formatNumber(material.gross_quantity)}</span>
              <span>Fee ${formatNumber(material.station_tax_quantity)}</span>
              <span>Net ${formatNumber(material.net_quantity)}</span>
            </div>
          </div>
        </article>
      `).join("");
      return `
        <div class="reprocess-field-desk">
          ${renderReprocessingStatusRail(data)}
          ${renderReprocessingOreSpecimen(data)}
          ${renderReprocessingNotebook(data, notes)}
          <div class="mineral-tray">
            <div class="tray-heading">
              <strong>Recovered Minerals</strong>
              <span>${formatNumber(materials.length)} stack${materials.length === 1 ? "" : "s"}</span>
            </div>
            <div class="mineral-grid">${rows}</div>
            <div class="minerals-total">
              <span>Minerals Total (Jita Buy)</span>
              <b>${renderReprocessingJitaValue((data.jita_valuation || {}).processed_material_value, (data.jita_valuation || {}).processed_partial_material_value)}</b>
            </div>
          </div>
          <div class="reprocess-desk-footer">
            <span><b>Skills:</b> Reprocessing ${formatNumber((data.skills || {}).reprocessing_level)}, Reprocessing Efficiency ${formatNumber((data.skills || {}).reprocessing_efficiency_level)}</span>
            <span><b>Cache:</b> ${escapeHtml((data.cache || {}).build_number || "unknown")}</span>
          </div>
        </div>
      `;
    }

    function renderReprocessingMaterialJitaDepth(material) {
      if (material.jita_buy_price == null) return "No Jita buy depth found.";
      const price = `${formatIsk(material.jita_buy_price)} top buy`;
      if (material.jita_complete) return price;
      return `${price}; ${formatNumber(material.jita_priced_quantity)} of ${formatNumber(material.jita_required_quantity)} priced`;
    }

    function renderReprocessingEveMaterialEstimate(material) {
      if (material.eve_estimate_unit_price == null) return "No ESI market estimate found.";
      return `${formatIsk(material.eve_estimate_unit_price)} ${escapeHtml(material.eve_estimate_price_source || "estimate")} unit price`;
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
      flightBlueprintTop.innerHTML = renderBlueprintList(blueprints.top_types || []);
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

    function renderBlueprintList(items) {
      if (!items.length) return "No top blueprints returned yet.";
      return `<div class="blueprint-list">${items.map((item) => {
        const skills = renderSkillSummary(item.required_skills || []);
        const maxRuns = item.max_production_limit ? `${formatNumber(item.max_production_limit)} max copy runs` : "copy runs unknown";
        const time = item.base_time_seconds ? `${formatDuration(item.base_time_seconds)} base` : "base time unknown";
        const product = item.product_name ? `${escapeHtml(item.product_name)} x${formatNumber(item.product_quantity)}` : "recipe product unknown";
        const quality = item.best_material_efficiency != null
          ? `ME ${formatNumber(item.best_material_efficiency)} / TE ${formatNumber(item.best_time_efficiency)}`
          : "ME/TE unknown";
        return `
          <div class="blueprint-item">
            <strong>${escapeHtml(item.name)}</strong>
            <span>${product}</span>
            <small>${formatNumber(item.count)} owned &middot; ${quality} &middot; ${time} &middot; ${maxRuns}</small>
            <small>${skills}</small>
          </div>
        `;
      }).join("")}</div>`;
    }

    function renderSkillSummary(skills) {
      if (!skills.length) return "skills not in cache yet";
      const visible = skills.slice(0, 3).map((skill) => `${escapeHtml(skill.name)} ${formatNumber(skill.level)}`).join(", ");
      const more = skills.length > 3 ? `, +${formatNumber(skills.length - 3)} more` : "";
      return `skills: ${visible}${more}`;
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
        const timing = item.adjusted_time_seconds
          ? `; ${formatDuration(item.adjusted_time_seconds)} with TE from ${formatDuration(item.base_time_seconds)} base`
          : "";
        if (item.can_build_one_run) {
          return `${escapeHtml(item.product_name)}: ME-adjusted one-run materials covered (${quality}${timing})`;
        }
        const missing = (item.missing_materials || [])
          .map((material) => `${escapeHtml(material.name)} ${formatNumber(material.shortage)}`)
          .join(", ");
        return `${escapeHtml(item.product_name)}: missing ${missing || "materials"} (${quality}${timing})`;
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
        originName: haulOrigin.value,
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        routePreference: haulRoutePreference.value,
        avoidRecentPodKills: haulAvoidPodKills.checked,
        detourJumps: haulDetourJumps.value,
        minDetourMarginPercent: haulMinMargin.value,
      });
      const settings = readHaulSettings();
      resetFlightHauling(`Ready to scan route hauling opportunities from ${haulStartLabel(settings)} to ${settings.destination}.`);
    });

    haulMinMargin.addEventListener("input", () => {
      writeHaulSettings({
        originName: haulOrigin.value,
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        routePreference: haulRoutePreference.value,
        avoidRecentPodKills: haulAvoidPodKills.checked,
        detourJumps: haulDetourJumps.value,
        minDetourMarginPercent: haulMinMargin.value,
      });
    });

    haulRoutePreference.addEventListener("change", () => {
      writeHaulSettings({
        originName: haulOrigin.value,
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        routePreference: haulRoutePreference.value,
        avoidRecentPodKills: haulAvoidPodKills.checked,
        detourJumps: haulDetourJumps.value,
        minDetourMarginPercent: haulMinMargin.value,
      });
      const settings = readHaulSettings();
      resetFlightHauling(`Ready to scan route hauling opportunities from ${haulStartLabel(settings)} to ${settings.destination}.`);
    });

    haulAvoidPodKills.addEventListener("change", () => {
      writeHaulSettings({
        originName: haulOrigin.value,
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        routePreference: haulRoutePreference.value,
        avoidRecentPodKills: haulAvoidPodKills.checked,
        detourJumps: haulDetourJumps.value,
        minDetourMarginPercent: haulMinMargin.value,
      });
      const settings = readHaulSettings();
      resetFlightHauling(`Ready to scan route hauling opportunities from ${haulStartLabel(settings)} to ${settings.destination}.`);
    });

    function updateHaulScopeAndReset() {
      writeHaulSettings({
        originName: haulOrigin.value,
        destination: haulDestination.value,
        cargoM3: haulCargoM3.value,
        routePreference: haulRoutePreference.value,
        avoidRecentPodKills: haulAvoidPodKills.checked,
        detourJumps: haulDetourJumps.value,
        minDetourMarginPercent: haulMinMargin.value,
        includeCommonMaterials: haulCommonMaterials.checked,
        marketGroupIds: readHaulMarketGroupIdsFromInputs(),
      });
      const settings = readHaulSettings();
      resetFlightHauling(`Ready to scan route hauling opportunities from ${haulStartLabel(settings)} to ${settings.destination}.`);
    }

    haulOrigin.addEventListener("change", updateHaulScopeAndReset);
    haulDestination.addEventListener("change", updateHaulScopeAndReset);
    haulCommonMaterials.addEventListener("change", updateHaulScopeAndReset);
    haulMarketGroups.addEventListener("click", (event) => {
      if (event.target.closest("input[data-haul-market-group], .mini-check")) {
        event.stopPropagation();
      }
    });
    haulMarketGroups.addEventListener("change", (event) => {
      if (!event.target.closest("input[data-haul-market-group]")) return;
      updateHaulScopeAndReset();
    });

    function updateAcquisitionScopeAndReset() {
      writeAcquisitionSettings({
        originName: acqOrigin.value,
        destination: acqDestination.value,
        budgetIsk: acqBudget.value,
        brokerFeePercent: acqBrokerFee.value,
        pickupJumps: acqPickupJumps.value,
        targetDays: acqTargetDays.value,
        minMarginPercent: acqMinMargin.value,
        includeCommonMaterials: acqCommonMaterials.checked,
        marketGroupIds: readAcqMarketGroupIdsFromInputs(),
      });
      const settings = readAcquisitionSettings();
      resetMarketAcquisition(`Ready to plan buy orders from ${acquisitionStartLabel(settings)} toward ${settings.destination}.`);
    }

    acquisitionForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadMarketAcquisition();
    });
    acqOrigin.addEventListener("change", updateAcquisitionScopeAndReset);
    acqDestination.addEventListener("change", updateAcquisitionScopeAndReset);
    acqBudget.addEventListener("change", updateAcquisitionScopeAndReset);
    acqBrokerFee.addEventListener("change", updateAcquisitionScopeAndReset);
    acqPickupJumps.addEventListener("change", updateAcquisitionScopeAndReset);
    acqTargetDays.addEventListener("change", updateAcquisitionScopeAndReset);
    acqMinMargin.addEventListener("input", () => {
      acqMinMarginValue.textContent = `${formatNumber(clampHaulMinMargin(acqMinMargin.value))}%`;
    });
    acqMinMargin.addEventListener("change", updateAcquisitionScopeAndReset);
    acqCommonMaterials.addEventListener("change", updateAcquisitionScopeAndReset);
    acqMarketGroups.addEventListener("click", (event) => {
      if (event.target.closest("input[data-haul-market-group], .mini-check")) {
        event.stopPropagation();
      }
    });
    acqMarketGroups.addEventListener("change", (event) => {
      if (!event.target.closest("input[data-haul-market-group]")) return;
      updateAcquisitionScopeAndReset();
    });

    function updateTradePnlAndReset() {
      const settings = writeTradePnlSettings({
        windowHours: tradePnlWindowHours.value,
        lens: tradePnlLens.value,
        exclude: tradePnlExclude.value,
        showMatches: tradePnlShowMatches.checked,
      });
      resetTradePnl(`Ready to analyze ${tradePnlWindowLabel(settings.windowHours)} of trade history.`);
    }

    tradePnlForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadTradePnl();
    });
    tradePnlWindowHours.addEventListener("change", updateTradePnlAndReset);
    tradePnlLens.addEventListener("change", updateTradePnlAndReset);
    tradePnlExclude.addEventListener("change", updateTradePnlAndReset);
    tradePnlShowMatches.addEventListener("change", updateTradePnlAndReset);

    function updatePlanetaryAndReset() {
      const settings = writePlanetarySettings({
        hub: planetaryHub.value,
        outputTier: planetaryOutputTier.value,
        top: planetaryTop.value,
        ownerTaxPercent: planetaryOwnerTax.value,
        npcTaxPercent: planetaryNpcTax.value,
        customsCodeExpertiseLevel: planetaryCce.value,
        salesTaxPercent: planetarySalesTax.value,
        brokerFeePercent: planetaryBrokerFee.value,
      });
      resetPlanetary(`Ready to rank ${settings.outputTier} planetary chains in ${settings.hub}.`);
    }

    planetaryForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadPlanetaryRanking();
    });
    planetaryHub.addEventListener("change", updatePlanetaryAndReset);
    planetaryOutputTier.addEventListener("change", updatePlanetaryAndReset);
    planetaryTop.addEventListener("change", updatePlanetaryAndReset);
    planetaryOwnerTax.addEventListener("change", updatePlanetaryAndReset);
    planetaryNpcTax.addEventListener("change", updatePlanetaryAndReset);
    planetaryCce.addEventListener("change", updatePlanetaryAndReset);
    planetarySalesTax.addEventListener("change", updatePlanetaryAndReset);
    planetaryBrokerFee.addEventListener("change", updatePlanetaryAndReset);

    function updateReprocessingAndReset() {
      const settings = writeReprocessingSettings({
        oreTypeId: reprocessOre.value,
        quantity: reprocessQuantity.value,
        locationId: reprocessStationSelect.value,
        stationSort: reprocessStationSort.value,
        facilityYieldPercent: readOptionalPercentInput(reprocessFacilityYield),
        stationTaxPercent: readOptionalPercentInput(reprocessStationTax),
        implantBonusPercent: readOptionalPercentInput(reprocessImplantBonus),
        structureBonusPercent: readOptionalPercentInput(reprocessStructureBonus) || "0",
      });
      resetReprocessing(`Ready to calculate ${formatNumber(settings.quantity)} ore units.`);
    }

    function updateReprocessingStationsAndReset() {
      updateReprocessingAndReset();
      loadReprocessingLocations();
    }

    reprocessingForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadReprocessingCalculation();
    });
    reprocessOre.addEventListener("change", updateReprocessingStationsAndReset);
    reprocessQuantity.addEventListener("change", updateReprocessingAndReset);
    reprocessStationSelect.addEventListener("change", updateReprocessingAndReset);
    reprocessStationSort.addEventListener("change", updateReprocessingStationsAndReset);
    reprocessRefreshLocations.addEventListener("click", loadReprocessingLocations);
    reprocessFacilityYield.addEventListener("change", updateReprocessingAndReset);
    reprocessStationTax.addEventListener("change", updateReprocessingAndReset);
    reprocessImplantBonus.addEventListener("change", updateReprocessingStationsAndReset);
    reprocessStructureBonus.addEventListener("change", updateReprocessingStationsAndReset);

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
    writeAcquisitionSettings(readAcquisitionSettings());
    writeTradePnlSettings(readTradePnlSettings());
    writePlanetarySettings(readPlanetarySettings());
    writeReprocessingSettings(readReprocessingSettings());
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
    return markup.replace("@@CATEGORY_OPTIONS@@", category_options).replace(
        "@@HAUL_MARKET_GROUP_OPTIONS@@",
        haul_market_group_options,
    ).replace(
        "@@REPROCESSING_ORE_OPTIONS@@",
        reprocessing_ore_options,
    )


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
    sso_callback_url = args.sso_callback_url or default_flight_callback_url(
        public_base_url=public_base_url,
        url_host=url_host,
        port=args.port,
    )
    sso_config = EveSsoConfig(
        client_id=args.sso_client_id,
        client_secret=args.sso_client_secret,
        callback_url=sso_callback_url,
        scopes=tuple(parse_csv(args.sso_scopes)) or DEFAULT_FLIGHT_ESI_SCOPES,
        allowed_corporation_ids=parse_int_csv(args.allowed_corporation_ids),
        allowed_alliance_ids=parse_int_csv(args.allowed_alliance_ids),
        trusted_members_can_edit=args.trusted_members_can_write_market,
        esi_base_url=args.esi_base_url,
    )
    hosting_errors = public_hosting_config_errors(
        public_base_url=public_base_url,
        sso_config=sso_config,
        public_hosting_mode=args.public_hosting_mode,
    )
    if hosting_errors:
        raise CorpMarketError("Public hosting mode needs: " + "; ".join(hosting_errors) + ".")
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
        public_hosting_mode=args.public_hosting_mode,
    )
    url = f"http://{url_host}:{args.port}/"
    print(f"Corp market concierge listening at {url}")
    print(f"Market database: {args.market_db_path}")
    print(f"Public offer URL base: {public_base_url}")
    if sso_config.enabled:
        print(f"Flight Attendant ESI enabled. Callback URL: {sso_config.callback_url}")
        print(f"Flight Attendant ESI scopes: {', '.join(sso_config.scopes)}")
        print("Flight Attendant access tokens are kept in server memory only.")
        if sso_config.membership_restricted:
            print(
                "Flight Attendant member allowlist: "
                f"corps={list(sso_config.allowed_corporation_ids)}, "
                f"alliances={list(sso_config.allowed_alliance_ids)}"
            )
        else:
            print("Flight Attendant member allowlist is not configured; any EVE-authenticated character can sign in.")
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
    if args.trusted_members_can_write_market:
        print("Trusted allowlisted SSO members can create and update market listings.")
    if args.public_hosting_mode:
        print("Public hosting mode is enabled: HTTPS, EVE SSO, and a corp/alliance allowlist are required.")
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
        "--public-hosting-mode",
        action="store_true",
        default=env_bool("CORP_MARKET_PUBLIC_HOSTING_MODE"),
        help="Require HTTPS, EVE SSO, and a corp/alliance allowlist for public Flight Attendant hosting.",
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
        "--allowed-corporation-ids",
        default=os.environ.get("CORP_MARKET_ALLOWED_CORPORATION_IDS", ""),
        help="Comma-separated corporation ids allowed to use the hosted Flight Attendant.",
    )
    serve.add_argument(
        "--allowed-alliance-ids",
        default=os.environ.get("CORP_MARKET_ALLOWED_ALLIANCE_IDS", ""),
        help="Comma-separated alliance ids allowed to use the hosted Flight Attendant.",
    )
    serve.add_argument(
        "--trusted-members-can-write-market",
        action="store_true",
        default=env_bool("CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET"),
        help="Allow allowlisted EVE SSO members to create, reserve, and update market listings.",
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


def parse_int_csv(value: str | None) -> tuple[int, ...]:
    ids: list[int] = []
    for item in parse_csv(value):
        try:
            item_id = int(item)
        except ValueError as exc:
            raise CorpMarketError(f"Expected numeric id, got {item!r}.") from exc
        if item_id > 0 and item_id not in ids:
            ids.append(item_id)
    return tuple(ids)


def env_bool(name: str, *, default: bool = False) -> bool:
    if name not in os.environ:
        return default
    return query_bool(os.environ.get(name), default=default)


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


def flight_session_cookie_header(session_id: str, *, secure: bool = False) -> str:
    header = (
        f"{FLIGHT_SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={60 * 60}"
    )
    return f"{header}; Secure" if secure else header


def clear_flight_session_cookie_header(*, secure: bool = False) -> str:
    header = f"{FLIGHT_SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    return f"{header}; Secure" if secure else header


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
