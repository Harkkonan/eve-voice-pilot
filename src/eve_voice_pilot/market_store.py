from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid


@dataclass(frozen=True)
class MarketStoreDependencies:
    default_path: Path
    listing_types: set[str] | frozenset[str]
    listing_statuses: set[str] | frozenset[str]
    listing_categories: Mapping[str, str]
    shared_fitting_statuses: set[str] | frozenset[str]
    max_notes_length: int
    max_trade_pnl_expectations: int
    max_trade_pnl_transactions: int
    market_error: Callable[[str], Exception]
    market_listing_factory: Callable[..., Any]
    market_listing_from_row: Callable[[sqlite3.Row], Any]
    shared_fitting_factory: Callable[..., Any]
    shared_fitting_from_row: Callable[[sqlite3.Row], Any]
    clean_listing_id: Callable[[Any], str]
    clean_choice: Callable[[Any, set[str] | frozenset[str], str], str]
    clean_text: Callable[..., str]
    clean_positive_int: Callable[[Any, str], int]
    clean_optional_isk: Callable[[Any], float | None]
    clean_multiline: Callable[..., str]
    clean_optional_url: Callable[[Any, str], str]
    clean_fitting_text: Callable[[Any], str]
    clean_discord_snowflake: Callable[[Any, str], str]
    clean_optional_int: Callable[[Any], int | None]
    clean_optional_float: Callable[[Any], float | None]
    parse_fit_note: Callable[[str], Any]
    now_iso: Callable[[], str]
    future_iso: Callable[..., str]
    shorten: Callable[[str, int], str]
    acquisition_expectation_from_opportunity: Callable[..., dict[str, Any] | None]
    trade_ledger_transaction_record: Callable[..., dict[str, Any] | None]


class MarketStore:
    def __init__(self, path: Path, *, deps: MarketStoreDependencies):
        self.path = Path(path or deps.default_path)
        self.deps = deps
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        self._configure_connection(connection)
        return connection

    def _configure_connection(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        connection.execute("PRAGMA synchronous = NORMAL")

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_fittings (
                    fitting_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    hull TEXT NOT NULL,
                    fit_name TEXT NOT NULL,
                    fitting_text TEXT NOT NULL,
                    website_url TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    submitted_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_shared_fittings_status ON shared_fittings(status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_shared_fittings_hull ON shared_fittings(hull)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS flight_acquisition_expectations (
                    expectation_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    character_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    type_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    suggested_bid REAL,
                    max_safe_bid REAL,
                    planned_units INTEGER,
                    expected_unit_profit_isk REAL,
                    expected_total_profit_isk REAL,
                    expected_net_sell_unit_price REAL,
                    expected_gross_sell_unit_price REAL,
                    expected_isk_committed REAL,
                    expected_broker_fee_isk REAL,
                    expected_sales_tax_isk REAL,
                    risk_level TEXT NOT NULL DEFAULT '',
                    origin_system TEXT NOT NULL DEFAULT '',
                    destination_system TEXT NOT NULL DEFAULT '',
                    placement_system TEXT NOT NULL DEFAULT '',
                    target_days INTEGER,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flight_acquisition_expectations_character_type_created
                ON flight_acquisition_expectations(character_id, type_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS flight_trade_ledger_transactions (
                    character_id INTEGER NOT NULL,
                    transaction_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    type_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price REAL NOT NULL,
                    is_buy INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (character_id, transaction_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flight_trade_ledger_character_type_date
                ON flight_trade_ledger_transactions(character_id, type_id, date)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS flight_trade_learning_signals (
                    character_id INTEGER NOT NULL,
                    type_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    matched_quantity INTEGER NOT NULL DEFAULT 0,
                    open_quantity INTEGER NOT NULL DEFAULT 0,
                    profitable_count INTEGER NOT NULL DEFAULT 0,
                    sold_below_target_count INTEGER NOT NULL DEFAULT 0,
                    net_return_below_plan_count INTEGER NOT NULL DEFAULT 0,
                    fees_higher_than_expected_count INTEGER NOT NULL DEFAULT 0,
                    fills_too_slowly_count INTEGER NOT NULL DEFAULT 0,
                    loss_vs_plan_count INTEGER NOT NULL DEFAULT 0,
                    actual_profit_isk REAL NOT NULL DEFAULT 0,
                    actual_vs_plan_profit_isk REAL NOT NULL DEFAULT 0,
                    gross_sell_delta_total_isk REAL NOT NULL DEFAULT 0,
                    net_sell_delta_total_isk REAL NOT NULL DEFAULT 0,
                    fee_gap_total_isk REAL NOT NULL DEFAULT 0,
                    signal_keys_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (character_id, type_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flight_trade_learning_character_updated
                ON flight_trade_learning_signals(character_id, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS flight_trade_learning_events (
                    character_id INTEGER NOT NULL,
                    evidence_key TEXT NOT NULL,
                    type_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    matched_quantity INTEGER NOT NULL DEFAULT 0,
                    open_quantity INTEGER NOT NULL DEFAULT 0,
                    profitable_count INTEGER NOT NULL DEFAULT 0,
                    sold_below_target_count INTEGER NOT NULL DEFAULT 0,
                    net_return_below_plan_count INTEGER NOT NULL DEFAULT 0,
                    fees_higher_than_expected_count INTEGER NOT NULL DEFAULT 0,
                    fills_too_slowly_count INTEGER NOT NULL DEFAULT 0,
                    loss_vs_plan_count INTEGER NOT NULL DEFAULT 0,
                    actual_profit_isk REAL NOT NULL DEFAULT 0,
                    actual_vs_plan_profit_isk REAL NOT NULL DEFAULT 0,
                    gross_sell_delta_total_isk REAL NOT NULL DEFAULT 0,
                    net_sell_delta_total_isk REAL NOT NULL DEFAULT 0,
                    fee_gap_total_isk REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (character_id, evidence_key)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flight_trade_learning_events_character_type
                ON flight_trade_learning_events(character_id, type_id, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS flight_decision_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    character_id INTEGER NOT NULL,
                    workflow_key TEXT NOT NULL,
                    source_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT '',
                    target_item_name TEXT NOT NULL DEFAULT '',
                    target_type_id INTEGER,
                    expected_outcome_json TEXT NOT NULL DEFAULT '{}',
                    redacted_summary_json TEXT NOT NULL DEFAULT '{}',
                    source_keys_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flight_decision_snapshots_character_workflow_created
                ON flight_decision_snapshots(character_id, workflow_key, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flight_decision_snapshots_character_type_created
                ON flight_decision_snapshots(character_id, target_type_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS flight_decision_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    character_id INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actual_outcome_json TEXT NOT NULL DEFAULT '{}',
                    delta_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flight_decision_outcomes_character_snapshot_recorded
                ON flight_decision_outcomes(character_id, snapshot_id, recorded_at DESC)
                """
            )

    def create_listing(self, payload: dict[str, Any]) -> Any:
        listing_id = self.deps.clean_listing_id(payload.get("id") or uuid.uuid4().hex[:12])
        listing_type = self.deps.clean_choice(payload.get("listing_type") or payload.get("type") or "sell", self.deps.listing_types, "listing_type")
        category = self.deps.clean_choice(payload.get("category") or "general", set(self.deps.listing_categories), "category")
        item_name = self.deps.clean_text(payload.get("item_name") or payload.get("item"), "item_name", max_length=120, required=True)
        quantity = self.deps.clean_positive_int(payload.get("quantity"), "quantity")
        unit_price = self.deps.clean_optional_isk(payload.get("unit_price_isk") or payload.get("unit_price") or payload.get("price"))
        location = self.deps.clean_text(payload.get("location"), "location", max_length=160, required=True)
        owner = self.deps.clean_text(payload.get("owner") or payload.get("seller") or payload.get("buyer"), "owner", max_length=80, required=True)
        notes = self.deps.clean_multiline(payload.get("notes"), "notes", max_length=self.deps.max_notes_length)
        delivery = self.deps.clean_text(payload.get("delivery"), "delivery", max_length=160)
        fit_image_url = self.deps.clean_optional_url(payload.get("fit_image_url") or payload.get("image_url") or payload.get("screenshot_url"), "fit_image_url")
        timestamp = self.deps.now_iso()
        listing = self.deps.market_listing_factory(
            listing_id=listing_id,
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
    ) -> list[Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(self.deps.clean_choice(status, self.deps.listing_statuses, "status"))
        elif not include_closed:
            clauses.append("status IN ('open', 'reserved')")
        if listing_type:
            clauses.append("listing_type = ?")
            params.append(self.deps.clean_choice(listing_type, self.deps.listing_types, "listing_type"))
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
        return [self.deps.market_listing_from_row(row) for row in rows]

    def get_listing(self, listing_id: str) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM corp_market_listings WHERE listing_id = ?",
                (self.deps.clean_listing_id(listing_id),),
            ).fetchone()
        if row is None:
            raise self.deps.market_error(f"Listing {listing_id!r} was not found.")
        return self.deps.market_listing_from_row(row)

    def reserve_listing(self, listing_id: str, *, reserved_by: str, hours: float = 24.0) -> Any:
        listing = self.get_listing(listing_id)
        if listing.status not in {"open", "reserved"}:
            raise self.deps.market_error(f"Listing is {listing.status}; it cannot be reserved.")
        reserved_by = self.deps.clean_text(reserved_by, "reserved_by", max_length=80, required=True)
        reserved_until = self.deps.future_iso(hours=max(0.25, min(hours, 72.0)))
        timestamp = self.deps.now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE corp_market_listings
                SET status = 'reserved', reserved_by = ?, reserved_until = ?, updated_at = ?
                WHERE listing_id = ?
                """,
                (reserved_by, reserved_until, timestamp, self.deps.clean_listing_id(listing_id)),
            )
        return self.get_listing(listing_id)

    def set_status(self, listing_id: str, status: str) -> Any:
        status = self.deps.clean_choice(status, self.deps.listing_statuses, "status")
        timestamp = self.deps.now_iso()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE corp_market_listings
                SET status = ?, updated_at = ?,
                    reserved_by = CASE WHEN ? = 'open' THEN '' ELSE reserved_by END,
                    reserved_until = CASE WHEN ? = 'open' THEN '' ELSE reserved_until END
                WHERE listing_id = ?
                """,
                (status, timestamp, status, status, self.deps.clean_listing_id(listing_id)),
            )
        if result.rowcount == 0:
            raise self.deps.market_error(f"Listing {listing_id!r} was not found.")
        return self.get_listing(listing_id)

    def record_discord_sync(
        self,
        listing_id: str,
        *,
        message_id: str | None = None,
        thread_id: str | None = None,
        error: str = "",
    ) -> Any:
        assignments = ["discord_synced_at = ?", "discord_sync_error = ?"]
        params: list[Any] = [self.deps.now_iso(), self.deps.shorten(str(error or ""), 500)]
        if message_id is not None:
            assignments.append("discord_message_id = ?")
            params.append(self.deps.clean_discord_snowflake(message_id, "discord_message_id"))
        if thread_id is not None:
            assignments.append("discord_thread_id = ?")
            params.append(self.deps.clean_discord_snowflake(thread_id, "discord_thread_id"))
        params.append(self.deps.clean_listing_id(listing_id))
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
            raise self.deps.market_error(f"Listing {listing_id!r} was not found.")
        return self.get_listing(listing_id)

    def create_shared_fitting(self, payload: dict[str, Any]) -> Any:
        fitting_text = self.deps.clean_fitting_text(
            payload.get("fitting_text") or payload.get("fit") or payload.get("fit_block")
        )
        fit_note = self.deps.parse_fit_note(fitting_text)
        if fit_note is None:
            raise ValueError("fitting_text must start with an EVE fitting header like [Hawk, Abyss fit].")
        website_url = self.deps.clean_optional_url(
            payload.get("website_url") or payload.get("fitting_url") or payload.get("link"),
            "website_url",
        )
        tags = self.deps.clean_text(payload.get("tags"), "tags", max_length=160)
        submitted_by = self.deps.clean_text(
            payload.get("submitted_by") or payload.get("pilot") or payload.get("owner"),
            "submitted_by",
            max_length=80,
        )
        timestamp = self.deps.now_iso()
        fitting = self.deps.shared_fitting_factory(
            fitting_id=str(payload.get("id") or uuid.uuid4().hex[:12]),
            status="active",
            hull=fit_note.hull,
            fit_name=fit_note.fit_name,
            fitting_text=fitting_text,
            website_url=website_url,
            tags=tags,
            submitted_by=submitted_by,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO shared_fittings (
                    fitting_id, status, hull, fit_name, fitting_text, website_url,
                    tags, submitted_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fitting.fitting_id,
                    fitting.status,
                    fitting.hull,
                    fitting.fit_name,
                    fitting.fitting_text,
                    fitting.website_url,
                    fitting.tags,
                    fitting.submitted_by,
                    fitting.created_at,
                    fitting.updated_at,
                ),
            )
        return fitting

    def list_shared_fittings(
        self,
        *,
        status: str | None = None,
        include_archived: bool = False,
        query: str = "",
        limit: int = 100,
    ) -> list[Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(self.deps.clean_choice(status, self.deps.shared_fitting_statuses, "status"))
        elif not include_archived:
            clauses.append("status = 'active'")
        clean_query = self.deps.clean_text(query, "query", max_length=120)
        if clean_query:
            clauses.append("(hull LIKE ? OR fit_name LIKE ? OR tags LIKE ? OR submitted_by LIKE ?)")
            like_query = f"%{clean_query}%"
            params.extend([like_query, like_query, like_query, like_query])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            clean_limit = int(limit)
        except (TypeError, ValueError):
            clean_limit = 100
        params.append(max(1, min(clean_limit, 500)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM shared_fittings
                {where}
                ORDER BY
                    CASE status WHEN 'active' THEN 0 ELSE 1 END,
                    updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self.deps.shared_fitting_from_row(row) for row in rows]

    def get_shared_fitting(self, fitting_id: str) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM shared_fittings WHERE fitting_id = ?",
                (self.deps.clean_listing_id(fitting_id),),
            ).fetchone()
        if row is None:
            raise self.deps.market_error(f"Shared fitting {fitting_id!r} was not found.")
        return self.deps.shared_fitting_from_row(row)

    def set_shared_fitting_status(self, fitting_id: str, status: str) -> Any:
        clean_status = self.deps.clean_choice(status, self.deps.shared_fitting_statuses, "status")
        timestamp = self.deps.now_iso()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE shared_fittings
                SET status = ?, updated_at = ?
                WHERE fitting_id = ?
                """,
                (clean_status, timestamp, self.deps.clean_listing_id(fitting_id)),
            )
        if result.rowcount == 0:
            raise self.deps.market_error(f"Shared fitting {fitting_id!r} was not found.")
        return self.get_shared_fitting(fitting_id)

    def save_acquisition_expectations(
        self,
        *,
        character_id: int,
        acquisition: dict[str, Any],
        generated_at: str,
    ) -> dict[str, Any]:
        clean_character_id = int(character_id or 0)
        if clean_character_id <= 0:
            return {"saved": 0, "snapshot_id": ""}
        opportunities = [item for item in acquisition.get("opportunities") or [] if isinstance(item, dict)]
        if not opportunities:
            return {"saved": 0, "snapshot_id": ""}
        snapshot_id = uuid.uuid4().hex[:12]
        created_at = str(generated_at or self.deps.now_iso())
        origin = acquisition.get("origin_system") if isinstance(acquisition.get("origin_system"), dict) else {}
        destination = acquisition.get("destination_system") if isinstance(acquisition.get("destination_system"), dict) else {}
        rows: list[tuple[Any, ...]] = []
        for opportunity in opportunities[:self.deps.max_trade_pnl_expectations]:
            expectation = self.deps.acquisition_expectation_from_opportunity(
                opportunity,
                snapshot_id=snapshot_id,
                character_id=clean_character_id,
                created_at=created_at,
                origin_system=str(origin.get("name") or ""),
                destination_system=str(destination.get("name") or ""),
            )
            if expectation is None:
                continue
            rows.append(
                (
                    expectation["expectation_id"],
                    expectation["snapshot_id"],
                    expectation["character_id"],
                    expectation["created_at"],
                    expectation["type_id"],
                    expectation["item_name"],
                    expectation.get("suggested_bid"),
                    expectation.get("max_safe_bid"),
                    expectation.get("planned_units"),
                    expectation.get("expected_unit_profit_isk"),
                    expectation.get("expected_total_profit_isk"),
                    expectation.get("expected_net_sell_unit_price"),
                    expectation.get("expected_gross_sell_unit_price"),
                    expectation.get("expected_isk_committed"),
                    expectation.get("expected_broker_fee_isk"),
                    expectation.get("expected_sales_tax_isk"),
                    expectation.get("risk_level") or "",
                    expectation.get("origin_system") or "",
                    expectation.get("destination_system") or "",
                    expectation.get("placement_system") or "",
                    expectation.get("target_days"),
                    json.dumps(expectation, sort_keys=True),
                )
            )
        if not rows:
            return {"saved": 0, "snapshot_id": snapshot_id}
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO flight_acquisition_expectations (
                    expectation_id, snapshot_id, character_id, created_at, type_id, item_name,
                    suggested_bid, max_safe_bid, planned_units, expected_unit_profit_isk,
                    expected_total_profit_isk, expected_net_sell_unit_price, expected_gross_sell_unit_price,
                    expected_isk_committed, expected_broker_fee_isk, expected_sales_tax_isk,
                    risk_level, origin_system, destination_system, placement_system, target_days, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return {"saved": len(rows), "snapshot_id": snapshot_id}

    def latest_acquisition_expectations(
        self,
        *,
        character_id: int,
        type_ids: Iterable[int],
        cutoff: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clean_character_id = int(character_id or 0)
        clean_type_ids = sorted({type_id for type_id in (self.deps.clean_optional_int(value) for value in type_ids) if type_id is not None})
        if clean_character_id <= 0 or not clean_type_ids:
            return []
        placeholders = ",".join("?" for _ in clean_type_ids)
        clauses = [f"character_id = ?", f"type_id IN ({placeholders})"]
        params: list[Any] = [clean_character_id, *clean_type_ids]
        if cutoff is not None:
            clauses.append("created_at >= ?")
            params.append(cutoff.isoformat().replace("+00:00", "Z"))
        clean_limit = self.deps.max_trade_pnl_expectations if limit is None else int(limit)
        params.append(max(1, min(clean_limit, self.deps.max_trade_pnl_expectations * 4)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM flight_acquisition_expectations
                WHERE {" AND ".join(clauses)}
                ORDER BY type_id ASC, created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        latest_by_type: dict[int, dict[str, Any]] = {}
        for row in rows:
            type_id = self.deps.clean_optional_int(row["type_id"])
            if type_id is None or type_id in latest_by_type:
                continue
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update(
                {
                    "expectation_id": str(row["expectation_id"]),
                    "snapshot_id": str(row["snapshot_id"]),
                    "character_id": int(row["character_id"]),
                    "created_at": str(row["created_at"]),
                    "type_id": type_id,
                    "item_name": str(row["item_name"] or payload.get("item_name") or f"Type {type_id}"),
                    "suggested_bid": self.deps.clean_optional_float(row["suggested_bid"]),
                    "max_safe_bid": self.deps.clean_optional_float(row["max_safe_bid"]),
                    "planned_units": self.deps.clean_optional_int(row["planned_units"]),
                    "expected_unit_profit_isk": self.deps.clean_optional_float(row["expected_unit_profit_isk"]),
                    "expected_total_profit_isk": self.deps.clean_optional_float(row["expected_total_profit_isk"]),
                    "expected_net_sell_unit_price": self.deps.clean_optional_float(row["expected_net_sell_unit_price"]),
                    "expected_gross_sell_unit_price": self.deps.clean_optional_float(row["expected_gross_sell_unit_price"]),
                    "expected_isk_committed": self.deps.clean_optional_float(row["expected_isk_committed"]),
                    "expected_broker_fee_isk": self.deps.clean_optional_float(row["expected_broker_fee_isk"]),
                    "expected_sales_tax_isk": self.deps.clean_optional_float(row["expected_sales_tax_isk"]),
                    "risk_level": str(row["risk_level"] or ""),
                    "origin_system": str(row["origin_system"] or ""),
                    "destination_system": str(row["destination_system"] or ""),
                    "placement_system": str(row["placement_system"] or ""),
                    "target_days": self.deps.clean_optional_int(row["target_days"]),
                }
            )
            latest_by_type[type_id] = payload
        return list(latest_by_type.values())

    def save_decision_snapshot(
        self,
        *,
        character_id: int,
        workflow_key: str,
        title: str,
        goal: str = "",
        source_key: str = "",
        target_item_name: str = "",
        target_type_id: int | None = None,
        expected_outcome: Mapping[str, Any] | None = None,
        redacted_summary: Mapping[str, Any] | None = None,
        source_keys: Iterable[Any] = (),
        payload: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        clean_character_id = int(character_id or 0)
        if clean_character_id <= 0:
            return {"saved": 0, "snapshot_id": ""}
        clean_workflow_key = _clean_decision_text(
            self.deps,
            workflow_key or "general",
            "workflow_key",
            max_length=80,
            fallback="general",
        )
        clean_title = _clean_decision_text(
            self.deps,
            title or clean_workflow_key.replace("-", " ").title(),
            "title",
            max_length=180,
            fallback="Decision snapshot",
        )
        clean_source_keys = _clean_decision_source_keys(self.deps, source_keys)
        clean_source_key = _clean_decision_text(
            self.deps,
            source_key or (clean_source_keys[0] if clean_source_keys else ""),
            "source_key",
            max_length=80,
        )
        if _decision_key_is_sensitive(clean_source_key):
            clean_source_key = ""
        clean_target_type_id = self.deps.clean_optional_int(target_type_id)
        if clean_target_type_id is not None and clean_target_type_id <= 0:
            clean_target_type_id = None
        snapshot_id = uuid.uuid4().hex[:12]
        timestamp = str(created_at or self.deps.now_iso())
        expected = _safe_decision_json_mapping(expected_outcome)
        summary = _safe_decision_json_mapping(redacted_summary)
        extra_payload = _safe_decision_json_mapping(payload)
        row = (
            snapshot_id,
            clean_character_id,
            clean_workflow_key,
            clean_source_key,
            timestamp,
            clean_title,
            _clean_decision_text(self.deps, goal, "goal", max_length=180),
            _clean_decision_text(self.deps, target_item_name, "target_item_name", max_length=180),
            clean_target_type_id,
            json.dumps(expected, sort_keys=True),
            json.dumps(summary, sort_keys=True),
            json.dumps(clean_source_keys),
            json.dumps(extra_payload, sort_keys=True),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO flight_decision_snapshots (
                    snapshot_id, character_id, workflow_key, source_key, created_at, title, goal,
                    target_item_name, target_type_id, expected_outcome_json, redacted_summary_json,
                    source_keys_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        snapshots = self.latest_decision_snapshots(character_id=clean_character_id, limit=5)
        snapshot = next((item for item in snapshots if item.get("snapshot_id") == snapshot_id), {})
        return {"saved": 1, "snapshot_id": snapshot_id, "snapshot": snapshot}

    def record_decision_outcome(
        self,
        *,
        snapshot_id: str,
        character_id: int,
        status: str,
        actual_outcome: Mapping[str, Any] | None = None,
        delta: Mapping[str, Any] | None = None,
        notes: str = "",
        payload: Mapping[str, Any] | None = None,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        clean_character_id = int(character_id or 0)
        clean_snapshot_id = _clean_decision_text(
            self.deps,
            snapshot_id,
            "snapshot_id",
            max_length=80,
        )
        if clean_character_id <= 0 or not clean_snapshot_id:
            return {"saved": 0, "outcome_id": ""}
        outcome_id = uuid.uuid4().hex[:12]
        timestamp = str(recorded_at or self.deps.now_iso())
        row = (
            outcome_id,
            clean_snapshot_id,
            clean_character_id,
            timestamp,
            _clean_decision_text(self.deps, status or "recorded", "status", max_length=60, fallback="recorded"),
            json.dumps(_safe_decision_json_mapping(actual_outcome), sort_keys=True),
            json.dumps(_safe_decision_json_mapping(delta), sort_keys=True),
            _clean_decision_note(self.deps, notes),
            json.dumps(_safe_decision_json_mapping(payload), sort_keys=True),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO flight_decision_outcomes (
                    outcome_id, snapshot_id, character_id, recorded_at, status,
                    actual_outcome_json, delta_json, notes, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        return {"saved": 1, "outcome_id": outcome_id, "snapshot_id": clean_snapshot_id}

    def latest_decision_snapshots(
        self,
        *,
        character_id: int,
        workflow_key: str = "",
        target_type_ids: Iterable[int] = (),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_character_id = int(character_id or 0)
        if clean_character_id <= 0:
            return []
        clauses = ["character_id = ?"]
        params: list[Any] = [clean_character_id]
        clean_workflow_key = _clean_decision_text(
            self.deps,
            workflow_key,
            "workflow_key",
            max_length=80,
        )
        if clean_workflow_key:
            clauses.append("workflow_key = ?")
            params.append(clean_workflow_key)
        clean_type_ids = sorted(
            {
                type_id
                for type_id in (self.deps.clean_optional_int(value) for value in target_type_ids)
                if type_id is not None and type_id > 0
            }
        )
        if clean_type_ids:
            placeholders = ",".join("?" for _ in clean_type_ids)
            clauses.append(f"target_type_id IN ({placeholders})")
            params.extend(clean_type_ids)
        try:
            clean_limit = int(limit)
        except (TypeError, ValueError):
            clean_limit = 20
        clean_limit = max(1, min(clean_limit, 100))
        params.append(clean_limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM flight_decision_snapshots
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, snapshot_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            snapshots = [_decision_snapshot_from_row(row) for row in rows]
            if snapshots:
                snapshot_ids = [str(snapshot["snapshot_id"]) for snapshot in snapshots]
                placeholders = ",".join("?" for _ in snapshot_ids)
                outcome_rows = connection.execute(
                    f"""
                    SELECT * FROM flight_decision_outcomes
                    WHERE character_id = ? AND snapshot_id IN ({placeholders})
                    ORDER BY recorded_at DESC, outcome_id DESC
                    """,
                    (clean_character_id, *snapshot_ids),
                ).fetchall()
            else:
                outcome_rows = []
        outcomes_by_snapshot: dict[str, list[dict[str, Any]]] = {}
        for row in outcome_rows:
            outcome = _decision_outcome_from_row(row)
            outcomes_by_snapshot.setdefault(str(outcome["snapshot_id"]), []).append(outcome)
        for snapshot in snapshots:
            outcomes = outcomes_by_snapshot.get(str(snapshot["snapshot_id"]), [])
            snapshot["outcomes"] = outcomes
            snapshot["latest_outcome"] = outcomes[0] if outcomes else None
        return snapshots

    def export_decision_history(
        self,
        *,
        character_id: int | None = None,
        workflow_key: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        clean_character_id = self.deps.clean_optional_int(character_id)
        if clean_character_id is not None and clean_character_id > 0:
            clauses.append("character_id = ?")
            params.append(clean_character_id)
        clean_workflow_key = _clean_decision_text(
            self.deps,
            workflow_key,
            "workflow_key",
            max_length=80,
        )
        if clean_workflow_key:
            clauses.append("workflow_key = ?")
            params.append(clean_workflow_key)
        try:
            clean_limit = int(limit)
        except (TypeError, ValueError):
            clean_limit = 500
        clean_limit = max(1, min(clean_limit, 2000))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM flight_decision_snapshots
                {where_sql}
                ORDER BY created_at DESC, snapshot_id DESC
                LIMIT ?
                """,
                (*params, clean_limit),
            ).fetchall()
            snapshots = [_decision_snapshot_from_row(row) for row in rows]
            outcome_rows: list[sqlite3.Row] = []
            if snapshots:
                snapshot_ids = [str(snapshot["snapshot_id"]) for snapshot in snapshots]
                for chunk in _chunks(snapshot_ids, 300):
                    placeholders = ",".join("?" for _ in chunk)
                    outcome_rows.extend(
                        connection.execute(
                            f"""
                            SELECT * FROM flight_decision_outcomes
                            WHERE snapshot_id IN ({placeholders})
                            ORDER BY recorded_at DESC, outcome_id DESC
                            """,
                            tuple(chunk),
                        ).fetchall()
                    )
        outcomes_by_snapshot: dict[str, list[dict[str, Any]]] = {}
        for row in outcome_rows:
            outcome = _decision_outcome_from_row(row)
            outcomes_by_snapshot.setdefault(str(outcome["snapshot_id"]), []).append(outcome)
        for snapshot in snapshots:
            outcomes = outcomes_by_snapshot.get(str(snapshot["snapshot_id"]), [])
            snapshot["outcomes"] = outcomes
            snapshot["latest_outcome"] = outcomes[0] if outcomes else None
        return {
            "ok": True,
            "generated_at": self.deps.now_iso(),
            "snapshot_count": len(snapshots),
            "filters": {
                "character_id": clean_character_id if clean_character_id and clean_character_id > 0 else None,
                "workflow_key": clean_workflow_key,
                "limit": clean_limit,
            },
            "retention": {
                "storage": "ignored local SQLite",
                "clearable": True,
                "prunable": True,
            },
            "snapshots": snapshots,
        }

    def prune_decision_history(
        self,
        *,
        retention_days: int = 90,
        max_snapshots_per_character: int = 500,
    ) -> dict[str, Any]:
        try:
            clean_retention_days = int(retention_days)
        except (TypeError, ValueError):
            clean_retention_days = 90
        clean_retention_days = max(1, min(clean_retention_days, 3650))
        try:
            clean_max_per_character = int(max_snapshots_per_character)
        except (TypeError, ValueError):
            clean_max_per_character = 500
        clean_max_per_character = max(1, min(clean_max_per_character, 10000))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=clean_retention_days)).replace(microsecond=0)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            old_ids = {
                str(row["snapshot_id"])
                for row in connection.execute(
                    "SELECT snapshot_id FROM flight_decision_snapshots WHERE created_at < ?",
                    (cutoff_text,),
                ).fetchall()
            }
            overflow_ids: set[str] = set()
            counts_by_character: dict[int, int] = {}
            rows = connection.execute(
                """
                SELECT snapshot_id, character_id FROM flight_decision_snapshots
                ORDER BY character_id ASC, created_at DESC, snapshot_id DESC
                """
            ).fetchall()
            for row in rows:
                character_id = int(row["character_id"])
                counts_by_character[character_id] = counts_by_character.get(character_id, 0) + 1
                if counts_by_character[character_id] > clean_max_per_character:
                    overflow_ids.add(str(row["snapshot_id"]))
            deleted = self._delete_decision_snapshots(connection, old_ids | overflow_ids)
            orphaned_outcomes = connection.execute(
                """
                DELETE FROM flight_decision_outcomes
                WHERE snapshot_id NOT IN (SELECT snapshot_id FROM flight_decision_snapshots)
                """
            ).rowcount
        return {
            "ok": True,
            "retention_days": clean_retention_days,
            "max_snapshots_per_character": clean_max_per_character,
            "cutoff": cutoff_text,
            "deleted_snapshots": deleted["deleted_snapshots"],
            "deleted_outcomes": deleted["deleted_outcomes"] + max(0, int(orphaned_outcomes or 0)),
        }

    def clear_decision_history(
        self,
        *,
        character_id: int | None = None,
        workflow_key: str = "",
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        clean_character_id = self.deps.clean_optional_int(character_id)
        if clean_character_id is not None and clean_character_id > 0:
            clauses.append("character_id = ?")
            params.append(clean_character_id)
        clean_workflow_key = _clean_decision_text(
            self.deps,
            workflow_key,
            "workflow_key",
            max_length=80,
        )
        if clean_workflow_key:
            clauses.append("workflow_key = ?")
            params.append(clean_workflow_key)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            snapshot_ids = [
                str(row["snapshot_id"])
                for row in connection.execute(
                    f"SELECT snapshot_id FROM flight_decision_snapshots {where_sql}",
                    tuple(params),
                ).fetchall()
            ]
            deleted = self._delete_decision_snapshots(connection, snapshot_ids)
        return {
            "ok": True,
            "deleted_snapshots": deleted["deleted_snapshots"],
            "deleted_outcomes": deleted["deleted_outcomes"],
            "filters": {
                "character_id": clean_character_id if clean_character_id and clean_character_id > 0 else None,
                "workflow_key": clean_workflow_key,
            },
        }

    def _delete_decision_snapshots(self, connection: sqlite3.Connection, snapshot_ids: Iterable[str]) -> dict[str, int]:
        clean_ids = [str(snapshot_id) for snapshot_id in dict.fromkeys(snapshot_ids) if str(snapshot_id or "").strip()]
        deleted_outcomes = 0
        deleted_snapshots = 0
        for chunk in _chunks(clean_ids, 300):
            placeholders = ",".join("?" for _ in chunk)
            deleted_outcomes += max(
                0,
                int(
                    connection.execute(
                        f"DELETE FROM flight_decision_outcomes WHERE snapshot_id IN ({placeholders})",
                        tuple(chunk),
                    ).rowcount
                    or 0
                ),
            )
            deleted_snapshots += max(
                0,
                int(
                    connection.execute(
                        f"DELETE FROM flight_decision_snapshots WHERE snapshot_id IN ({placeholders})",
                        tuple(chunk),
                    ).rowcount
                    or 0
                ),
            )
        return {"deleted_snapshots": deleted_snapshots, "deleted_outcomes": deleted_outcomes}

    def save_trade_ledger_transactions(self, *, character_id: int, transactions: Iterable[dict[str, Any]]) -> dict[str, Any]:
        clean_character_id = int(character_id or 0)
        if clean_character_id <= 0:
            return {"saved": 0}
        rows: list[tuple[Any, ...]] = []
        for transaction in transactions:
            if not isinstance(transaction, dict):
                continue
            clean = self.deps.trade_ledger_transaction_record(transaction, character_id=clean_character_id)
            if clean is None:
                continue
            rows.append(
                (
                    clean["character_id"],
                    clean["transaction_id"],
                    clean["date"],
                    clean["type_id"],
                    clean["quantity"],
                    clean["unit_price"],
                    1 if clean["is_buy"] else 0,
                    json.dumps(clean, sort_keys=True),
                )
            )
        if not rows:
            return {"saved": 0}
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO flight_trade_ledger_transactions (
                    character_id, transaction_id, date, type_id, quantity, unit_price, is_buy, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return {"saved": len(rows)}

    def historical_trade_ledger_transactions(
        self,
        *,
        character_id: int,
        type_ids: Iterable[int],
        before: datetime,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clean_character_id = int(character_id or 0)
        clean_type_ids = sorted({type_id for type_id in (self.deps.clean_optional_int(value) for value in type_ids) if type_id is not None})
        if clean_character_id <= 0 or not clean_type_ids:
            return []
        placeholders = ",".join("?" for _ in clean_type_ids)
        before_iso = before.isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM flight_trade_ledger_transactions
                WHERE character_id = ?
                  AND type_id IN ({placeholders})
                  AND date < ?
                ORDER BY date ASC, transaction_id ASC
                LIMIT ?
                """,
                (
                    clean_character_id,
                    *clean_type_ids,
                    before_iso,
                    max(1, min(self.deps.max_trade_pnl_transactions if limit is None else int(limit), self.deps.max_trade_pnl_transactions)),
                ),
            ).fetchall()
        transactions: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update(
                {
                    "transaction_id": int(row["transaction_id"]),
                    "date": str(row["date"]),
                    "type_id": int(row["type_id"]),
                    "quantity": int(row["quantity"]),
                    "unit_price": float(row["unit_price"]),
                    "is_buy": bool(row["is_buy"]),
                    "ledger_backfill": True,
                }
            )
            transactions.append(payload)
        return transactions

    def save_trade_learning_signals(
        self,
        *,
        character_id: int,
        signals: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        clean_character_id = int(character_id or 0)
        if clean_character_id <= 0:
            return {"saved": 0, "signals": []}
        rows: list[tuple[Any, ...]] = []
        touched_type_ids: list[int] = []
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            type_id = self.deps.clean_optional_int(signal.get("type_id"))
            if type_id is None:
                continue
            item_name = self.deps.clean_text(
                signal.get("item_name") or f"Type {type_id}",
                "item_name",
                max_length=160,
            )
            updated_at = str(signal.get("updated_at") or self.deps.now_iso())
            payload = signal.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            evidence_key = self.deps.clean_text(
                signal.get("evidence_key") or _trade_learning_evidence_key(clean_character_id, type_id, signal),
                "evidence_key",
                max_length=240,
            )
            row = (
                clean_character_id,
                evidence_key,
                type_id,
                item_name,
                updated_at,
                _nonnegative_int(signal.get("sample_count"), default=1),
                _nonnegative_int(signal.get("matched_quantity")),
                _nonnegative_int(signal.get("open_quantity")),
                _nonnegative_int(signal.get("profitable_count")),
                _nonnegative_int(signal.get("sold_below_target_count")),
                _nonnegative_int(signal.get("net_return_below_plan_count")),
                _nonnegative_int(signal.get("fees_higher_than_expected_count")),
                _nonnegative_int(signal.get("fills_too_slowly_count")),
                _nonnegative_int(signal.get("loss_vs_plan_count")),
                _float_value(signal.get("actual_profit_isk")),
                _float_value(signal.get("actual_vs_plan_profit_isk")),
                _float_value(signal.get("gross_sell_delta_total_isk")),
                _float_value(signal.get("net_sell_delta_total_isk")),
                _float_value(signal.get("fee_gap_total_isk")),
                json.dumps(payload, sort_keys=True),
            )
            rows.append(row)
            touched_type_ids.append(type_id)
        if not rows:
            return {"saved": 0, "signals": []}
        inserted_count = 0
        with self._connect() as connection:
            cursor = connection.executemany(
                """
                INSERT OR IGNORE INTO flight_trade_learning_events (
                    character_id, evidence_key, type_id, item_name, updated_at, sample_count, matched_quantity,
                    open_quantity, profitable_count, sold_below_target_count, net_return_below_plan_count,
                    fees_higher_than_expected_count, fills_too_slowly_count, loss_vs_plan_count,
                    actual_profit_isk, actual_vs_plan_profit_isk, gross_sell_delta_total_isk,
                    net_sell_delta_total_isk, fee_gap_total_isk, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            inserted_count = max(0, int(cursor.rowcount or 0))
            placeholders = ",".join("?" for _ in sorted(set(touched_type_ids)))
            aggregate_rows = connection.execute(
                f"""
                SELECT
                    type_id,
                    MAX(item_name) AS item_name,
                    MAX(updated_at) AS updated_at,
                    SUM(sample_count) AS sample_count,
                    SUM(matched_quantity) AS matched_quantity,
                    MAX(open_quantity) AS open_quantity,
                    SUM(profitable_count) AS profitable_count,
                    SUM(sold_below_target_count) AS sold_below_target_count,
                    SUM(net_return_below_plan_count) AS net_return_below_plan_count,
                    SUM(fees_higher_than_expected_count) AS fees_higher_than_expected_count,
                    SUM(fills_too_slowly_count) AS fills_too_slowly_count,
                    SUM(loss_vs_plan_count) AS loss_vs_plan_count,
                    SUM(actual_profit_isk) AS actual_profit_isk,
                    SUM(actual_vs_plan_profit_isk) AS actual_vs_plan_profit_isk,
                    SUM(gross_sell_delta_total_isk) AS gross_sell_delta_total_isk,
                    SUM(net_sell_delta_total_isk) AS net_sell_delta_total_isk,
                    SUM(fee_gap_total_isk) AS fee_gap_total_isk
                FROM flight_trade_learning_events
                WHERE character_id = ? AND type_id IN ({placeholders})
                GROUP BY type_id
                """,
                (clean_character_id, *sorted(set(touched_type_ids))),
            ).fetchall()
            latest_payloads = {
                int(row["type_id"]): str(row["payload_json"] or "{}")
                for row in connection.execute(
                    f"""
                    SELECT event.type_id, event.payload_json
                    FROM flight_trade_learning_events event
                    JOIN (
                        SELECT type_id, MAX(updated_at) AS updated_at
                        FROM flight_trade_learning_events
                        WHERE character_id = ? AND type_id IN ({placeholders})
                        GROUP BY type_id
                    ) latest
                    ON latest.type_id = event.type_id AND latest.updated_at = event.updated_at
                    WHERE event.character_id = ?
                    """,
                    (clean_character_id, *sorted(set(touched_type_ids)), clean_character_id),
                ).fetchall()
            }
            for row in aggregate_rows:
                keys = _trade_learning_signal_keys(row)
                connection.execute(
                    """
                    INSERT INTO flight_trade_learning_signals (
                        character_id, type_id, item_name, updated_at, sample_count, matched_quantity,
                        open_quantity, profitable_count, sold_below_target_count, net_return_below_plan_count,
                        fees_higher_than_expected_count, fills_too_slowly_count, loss_vs_plan_count,
                        actual_profit_isk, actual_vs_plan_profit_isk, gross_sell_delta_total_isk,
                        net_sell_delta_total_isk, fee_gap_total_isk, signal_keys_json, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(character_id, type_id) DO UPDATE SET
                        item_name = excluded.item_name,
                        updated_at = excluded.updated_at,
                        sample_count = excluded.sample_count,
                        matched_quantity = excluded.matched_quantity,
                        open_quantity = excluded.open_quantity,
                        profitable_count = excluded.profitable_count,
                        sold_below_target_count = excluded.sold_below_target_count,
                        net_return_below_plan_count = excluded.net_return_below_plan_count,
                        fees_higher_than_expected_count = excluded.fees_higher_than_expected_count,
                        fills_too_slowly_count = excluded.fills_too_slowly_count,
                        loss_vs_plan_count = excluded.loss_vs_plan_count,
                        actual_profit_isk = excluded.actual_profit_isk,
                        actual_vs_plan_profit_isk = excluded.actual_vs_plan_profit_isk,
                        gross_sell_delta_total_isk = excluded.gross_sell_delta_total_isk,
                        net_sell_delta_total_isk = excluded.net_sell_delta_total_isk,
                        fee_gap_total_isk = excluded.fee_gap_total_isk,
                        signal_keys_json = excluded.signal_keys_json,
                        payload_json = excluded.payload_json
                    """,
                    (
                        clean_character_id,
                        int(row["type_id"]),
                        str(row["item_name"] or f"Type {int(row['type_id'])}"),
                        str(row["updated_at"] or self.deps.now_iso()),
                        int(row["sample_count"] or 0),
                        int(row["matched_quantity"] or 0),
                        int(row["open_quantity"] or 0),
                        int(row["profitable_count"] or 0),
                        int(row["sold_below_target_count"] or 0),
                        int(row["net_return_below_plan_count"] or 0),
                        int(row["fees_higher_than_expected_count"] or 0),
                        int(row["fills_too_slowly_count"] or 0),
                        int(row["loss_vs_plan_count"] or 0),
                        float(row["actual_profit_isk"] or 0.0),
                        float(row["actual_vs_plan_profit_isk"] or 0.0),
                        float(row["gross_sell_delta_total_isk"] or 0.0),
                        float(row["net_sell_delta_total_isk"] or 0.0),
                        float(row["fee_gap_total_isk"] or 0.0),
                        json.dumps(keys),
                        latest_payloads.get(int(row["type_id"]), "{}"),
                    ),
                )
        return {
            "saved": inserted_count,
            "signals": self.latest_trade_learning_signals(
                character_id=clean_character_id,
                type_ids=sorted(set(touched_type_ids)),
            ),
        }

    def latest_trade_learning_signals(
        self,
        *,
        character_id: int,
        type_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        clean_character_id = int(character_id or 0)
        clean_type_ids = sorted({type_id for type_id in (self.deps.clean_optional_int(value) for value in type_ids) if type_id is not None})
        if clean_character_id <= 0 or not clean_type_ids:
            return []
        placeholders = ",".join("?" for _ in clean_type_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM flight_trade_learning_signals
                WHERE character_id = ? AND type_id IN ({placeholders})
                ORDER BY item_name ASC
                """,
                (clean_character_id, *clean_type_ids),
            ).fetchall()
        return [_trade_learning_signal_from_row(row) for row in rows]


def _nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, number)


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _trade_learning_evidence_key(character_id: int, type_id: int, signal: Mapping[str, Any]) -> str:
    payload = signal.get("payload")
    fingerprint = {
        "character_id": character_id,
        "type_id": type_id,
        "updated_at": str(signal.get("updated_at") or ""),
        "matched_quantity": _nonnegative_int(signal.get("matched_quantity")),
        "open_quantity": _nonnegative_int(signal.get("open_quantity")),
        "profitable_count": _nonnegative_int(signal.get("profitable_count")),
        "sold_below_target_count": _nonnegative_int(signal.get("sold_below_target_count")),
        "net_return_below_plan_count": _nonnegative_int(signal.get("net_return_below_plan_count")),
        "fees_higher_than_expected_count": _nonnegative_int(signal.get("fees_higher_than_expected_count")),
        "fills_too_slowly_count": _nonnegative_int(signal.get("fills_too_slowly_count")),
        "loss_vs_plan_count": _nonnegative_int(signal.get("loss_vs_plan_count")),
        "payload": payload if isinstance(payload, dict) else {},
    }
    digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"trade-pnl:{type_id}:{digest[:32]}"


def _trade_learning_signal_keys(row: sqlite3.Row) -> list[str]:
    keys: list[str] = []
    if int(row["profitable_count"] or 0) > 0:
        keys.append("worked_before")
    if int(row["sold_below_target_count"] or 0) > 1:
        keys.append("sold_below_target")
    if int(row["net_return_below_plan_count"] or 0) > 1:
        keys.append("net_return_below_plan")
    if int(row["fees_higher_than_expected_count"] or 0) > 1:
        keys.append("fees_higher_than_expected")
    if int(row["fills_too_slowly_count"] or 0) > 1:
        keys.append("fills_too_slowly")
    if int(row["loss_vs_plan_count"] or 0) > 1:
        keys.append("loss_vs_plan")
    return keys


def _trade_learning_signal_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        signal_keys = json.loads(str(row["signal_keys_json"] or "[]"))
    except json.JSONDecodeError:
        signal_keys = []
    if not isinstance(signal_keys, list):
        signal_keys = []
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "type_id": int(row["type_id"]),
        "item_name": str(row["item_name"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "sample_count": int(row["sample_count"] or 0),
        "matched_quantity": int(row["matched_quantity"] or 0),
        "open_quantity": int(row["open_quantity"] or 0),
        "profitable_count": int(row["profitable_count"] or 0),
        "sold_below_target_count": int(row["sold_below_target_count"] or 0),
        "net_return_below_plan_count": int(row["net_return_below_plan_count"] or 0),
        "fees_higher_than_expected_count": int(row["fees_higher_than_expected_count"] or 0),
        "fills_too_slowly_count": int(row["fills_too_slowly_count"] or 0),
        "loss_vs_plan_count": int(row["loss_vs_plan_count"] or 0),
        "actual_profit_isk": float(row["actual_profit_isk"] or 0.0),
        "actual_vs_plan_profit_isk": float(row["actual_vs_plan_profit_isk"] or 0.0),
        "gross_sell_delta_total_isk": float(row["gross_sell_delta_total_isk"] or 0.0),
        "net_sell_delta_total_isk": float(row["net_sell_delta_total_isk"] or 0.0),
        "fee_gap_total_isk": float(row["fee_gap_total_isk"] or 0.0),
        "signal_keys": [str(key) for key in signal_keys if str(key or "").strip()],
        "payload": payload,
    }


def _chunks(values: Iterable[Any], size: int) -> Iterable[list[Any]]:
    chunk: list[Any] = []
    clean_size = max(1, int(size or 1))
    for value in values:
        chunk.append(value)
        if len(chunk) >= clean_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


_DECISION_SENSITIVE_KEY_FRAGMENTS = (
    "raw",
    "paste",
    "auth",
    "bearer",
    "cookie",
    "session",
    "token",
    "secret",
    "webhook",
    "authorization",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "private",
    "api_key",
    "transaction_id",
    "structure_id",
)

_DECISION_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[^\s<>'\"]+", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
)


def _clean_decision_text(
    deps: MarketStoreDependencies,
    value: Any,
    field: str,
    *,
    max_length: int,
    fallback: str = "",
) -> str:
    text = " ".join(str(value if value is not None else fallback).replace("\x00", " ").split())
    if not text and fallback:
        text = fallback
    if len(text) > max_length:
        text = text[:max_length].rstrip()
    return deps.clean_text(text, field, max_length=max_length)


def _decision_key_is_sensitive(key: str) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in _DECISION_SENSITIVE_KEY_FRAGMENTS)


def _clean_decision_note(deps: MarketStoreDependencies, value: Any) -> str:
    note = _clean_decision_text(deps, value, "notes", max_length=deps.max_notes_length)
    normalized = note.lower().replace("-", "_")
    if any(fragment in normalized for fragment in _DECISION_SENSITIVE_KEY_FRAGMENTS):
        return "[redacted sensitive note]"
    return note


def _clean_decision_source_keys(deps: MarketStoreDependencies, source_keys: Iterable[Any]) -> list[str]:
    clean_keys: list[str] = []
    seen: set[str] = set()
    for value in source_keys or ():
        key = _clean_decision_text(deps, value, "source_key", max_length=80)
        if not key or _decision_key_is_sensitive(key) or key in seen:
            continue
        clean_keys.append(key)
        seen.add(key)
        if len(clean_keys) >= 40:
            break
    return clean_keys


def _safe_decision_json_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    safe = _safe_decision_json_value(value)
    return safe if isinstance(safe, dict) else {}


def _safe_decision_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:80]:
            key = " ".join(str(raw_key or "").replace("\x00", " ").split())[:120].strip()
            if not key or _decision_key_is_sensitive(key):
                continue
            clean[key] = _safe_decision_json_value(raw_value, depth=depth + 1)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_safe_decision_json_value(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
        return _safe_decision_json_string(text)
    if isinstance(value, str):
        return _safe_decision_json_string(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    return _safe_decision_json_string(str(value))


def _safe_decision_json_string(value: str) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    for pattern in _DECISION_SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub("[redacted sensitive value]", text)
    normalized = text.lower().replace("-", "_")
    if any(fragment in normalized for fragment in _DECISION_SENSITIVE_KEY_FRAGMENTS):
        return "[redacted sensitive value]"
    if len(text) > 600:
        return text[:600].rstrip()
    return text


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        parsed = []
    return parsed if isinstance(parsed, list) else []


def _decision_snapshot_from_row(row: sqlite3.Row) -> dict[str, Any]:
    source_keys = [str(key) for key in _json_list(row["source_keys_json"]) if str(key or "").strip()]
    return {
        "snapshot_id": str(row["snapshot_id"] or ""),
        "character_id": int(row["character_id"] or 0),
        "workflow_key": str(row["workflow_key"] or ""),
        "source_key": str(row["source_key"] or ""),
        "created_at": str(row["created_at"] or ""),
        "title": str(row["title"] or ""),
        "goal": str(row["goal"] or ""),
        "target_item_name": str(row["target_item_name"] or ""),
        "target_type_id": row["target_type_id"] if row["target_type_id"] is None else int(row["target_type_id"]),
        "expected_outcome": _json_dict(row["expected_outcome_json"]),
        "redacted_summary": _json_dict(row["redacted_summary_json"]),
        "source_keys": source_keys,
        "payload": _json_dict(row["payload_json"]),
    }


def _decision_outcome_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "outcome_id": str(row["outcome_id"] or ""),
        "snapshot_id": str(row["snapshot_id"] or ""),
        "character_id": int(row["character_id"] or 0),
        "recorded_at": str(row["recorded_at"] or ""),
        "status": str(row["status"] or ""),
        "actual_outcome": _json_dict(row["actual_outcome_json"]),
        "delta": _json_dict(row["delta_json"]),
        "notes": str(row["notes"] or ""),
        "payload": _json_dict(row["payload_json"]),
    }

