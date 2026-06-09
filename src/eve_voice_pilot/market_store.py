from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
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

