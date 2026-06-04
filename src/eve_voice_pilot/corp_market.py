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
import sqlite3
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen
import uuid
import webbrowser


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKET_DB_PATH = ROOT / "profiles" / "corp_market.sqlite3"
DEFAULT_PORT = 8770
DEFAULT_MAX_NOTES_LENGTH = 1200
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 10.0
LISTING_TYPES = {"sell", "want"}
LISTING_STATUSES = {"open", "reserved", "sold", "cancelled"}
SPACE_RE = re.compile(r"\s+")
ISK_AMOUNT_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmMbB]?)\s*$")


class CorpMarketError(RuntimeError):
    pass


@dataclass(frozen=True)
class MailDraft:
    subject: str
    body: str

    def to_dict(self) -> dict[str, str]:
        return {"subject": self.subject, "body": self.body, "combined": f"Subject: {self.subject}\n\n{self.body}"}


@dataclass(frozen=True)
class MarketListing:
    listing_id: str
    listing_type: str
    status: str
    item_name: str
    quantity: int
    unit_price_isk: float | None
    location: str
    owner: str
    notes: str
    delivery: str
    reserved_by: str = ""
    reserved_until: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def label(self) -> str:
        return "WTS" if self.listing_type == "sell" else "WTB"

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
            item_name=str(row["item_name"]),
            quantity=int(row["quantity"]),
            unit_price_isk=float(unit_price) if unit_price is not None else None,
            location=str(row["location"] or ""),
            owner=str(row["owner"] or ""),
            notes=str(row["notes"] or ""),
            delivery=str(row["delivery"] or ""),
            reserved_by=str(row["reserved_by"] or ""),
            reserved_until=str(row["reserved_until"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def to_dict(self, *, public_base_url: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.listing_id,
            "listing_type": self.listing_type,
            "label": self.label,
            "status": self.status,
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
            "reserved_by": self.reserved_by,
            "reserved_until": self.reserved_until,
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
                    item_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price_isk REAL,
                    location TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    delivery TEXT NOT NULL,
                    reserved_by TEXT NOT NULL DEFAULT '',
                    reserved_until TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_corp_market_status ON corp_market_listings(status)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_corp_market_type_status ON corp_market_listings(listing_type, status)"
            )

    def create_listing(self, payload: dict[str, Any]) -> MarketListing:
        listing_type = clean_choice(payload.get("listing_type") or payload.get("type") or "sell", LISTING_TYPES, "listing_type")
        item_name = clean_text(payload.get("item_name") or payload.get("item"), "item_name", max_length=120, required=True)
        quantity = clean_positive_int(payload.get("quantity"), "quantity")
        unit_price = clean_optional_isk(payload.get("unit_price_isk") or payload.get("unit_price") or payload.get("price"))
        location = clean_text(payload.get("location"), "location", max_length=160, required=True)
        owner = clean_text(payload.get("owner") or payload.get("seller") or payload.get("buyer"), "owner", max_length=80, required=True)
        notes = clean_multiline(payload.get("notes"), "notes", max_length=DEFAULT_MAX_NOTES_LENGTH)
        delivery = clean_text(payload.get("delivery"), "delivery", max_length=160)
        timestamp = now_iso()
        listing = MarketListing(
            listing_id=str(payload.get("id") or uuid.uuid4().hex[:12]),
            listing_type=listing_type,
            status="open",
            item_name=item_name,
            quantity=quantity,
            unit_price_isk=unit_price,
            location=location,
            owner=owner,
            notes=notes,
            delivery=delivery,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO corp_market_listings (
                    listing_id, listing_type, status, item_name, quantity, unit_price_isk,
                    location, owner, notes, delivery, reserved_by, reserved_until, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing.listing_id,
                    listing.listing_type,
                    listing.status,
                    listing.item_name,
                    listing.quantity,
                    listing.unit_price_isk,
                    listing.location,
                    listing.owner,
                    listing.notes,
                    listing.delivery,
                    listing.reserved_by,
                    listing.reserved_until,
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
        f"Unit price: {format_isk(listing.unit_price_isk) if listing.unit_price_isk is not None else 'Quote requested'}",
        f"Total: {format_isk(listing.total_price_isk) if listing.total_price_isk is not None else 'Quote requested'}",
        f"Location: {listing.location}",
    ]
    if listing.delivery:
        lines.append(f"Delivery: {listing.delivery}")
    if actor:
        lines.append(f"{actor_label}: {actor}")
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


def build_discord_webhook_payload(listing: MarketListing, *, public_base_url: str) -> dict[str, Any]:
    url = listing_public_url(listing.listing_id, public_base_url)
    color = 0x2E7D32 if listing.listing_type == "sell" else 0x1565C0
    title = f"{listing.label} {listing.item_name}"
    fields = [
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
        {"name": "Contact", "value": listing.owner, "inline": True},
    ]
    if listing.delivery:
        fields.append({"name": "Delivery", "value": listing.delivery, "inline": True})
    embed: dict[str, Any] = {
        "title": title,
        "url": url,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Corp Market {listing.listing_id}"},
        "timestamp": listing.created_at,
    }
    if listing.notes:
        embed["description"] = shorten(listing.notes, 700)
    return {
        "content": f"{title} - copy EVE mail draft: {url}",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }


def post_discord_webhook(webhook_url: str, payload: dict[str, Any], *, timeout_seconds: float) -> None:
    if not webhook_url:
        return
    request = Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "EveVoicePilot-CorpMarket/0.1"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise CorpMarketError(f"Discord webhook returned HTTP {response.status}.")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CorpMarketError(f"Discord webhook returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CorpMarketError(f"Discord webhook failed: {exc.reason}") from exc


def build_http_server(
    host: str,
    port: int,
    store: MarketStore,
    *,
    public_base_url: str,
    discord_webhook_url: str = "",
    discord_timeout_seconds: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
    admin_token: str = "",
) -> ThreadingHTTPServer:
    public_base_url = public_base_url.rstrip("/")

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
                discord_payload = build_discord_webhook_payload(listing, public_base_url=public_base_url)
                posted = False
                if discord_webhook_url:
                    post_discord_webhook(
                        discord_webhook_url,
                        discord_payload,
                        timeout_seconds=discord_timeout_seconds,
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
            except (ValueError, CorpMarketError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, "offer": listing.to_dict(public_base_url=public_base_url)})

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
            except (ValueError, CorpMarketError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, "offer": listing.to_dict(public_base_url=public_base_url)})

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

    return ThreadingHTTPServer((host, port), CorpMarketHandler)


def render_dashboard() -> str:
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
    textarea { min-height: 88px; resize: vertical; }
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
          <label>Quantity
            <input name="quantity" type="number" min="1" step="1" value="1">
          </label>
        </div>
        <label>Item
          <input name="item_name" autocomplete="off" placeholder="Venture, Water, 10MN Afterburner I">
        </label>
        <div class="row">
          <label>Unit Price
            <input name="unit_price" autocomplete="off" placeholder="12.5m or blank">
          </label>
          <label>Contact
            <input name="owner" autocomplete="off" placeholder="EVE character">
          </label>
        </div>
        <label>Location
          <input name="location" autocomplete="off" placeholder="Station, structure, or system">
        </label>
        <label>Delivery
          <input name="delivery" autocomplete="off" placeholder="Pickup, delivery available, high-sec only">
        </label>
        <label>Notes
          <textarea name="notes" placeholder="Fit notes, contract details, timing, limits"></textarea>
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
      offersEl.innerHTML = offers.map((offer) => `
        <article class="offer">
          <div>
            <h3><span class="pill ${offer.listing_type}">${offer.label}</span> ${escapeHtml(offer.item_name)}</h3>
            <div class="meta">
              ${escapeHtml(offer.quantity.toLocaleString())} units · ${escapeHtml(offer.unit_price_display)} each · ${escapeHtml(offer.total_price_display)} total
            </div>
            <div class="meta">${escapeHtml(offer.location)} · ${escapeHtml(offer.owner)}${offer.delivery ? ` · ${escapeHtml(offer.delivery)}` : ""}</div>
            ${offer.status !== "open" ? `<div class="meta"><span class="pill ${offer.status}">${escapeHtml(offer.status)}</span>${offer.reserved_by ? ` by ${escapeHtml(offer.reserved_by)}` : ""}</div>` : ""}
          </div>
          <div class="actions">
            <a href="${escapeHtml(offer.url)}" title="Mail draft">Mail</a>
            ${offer.status === "open" ? `<button type="button" data-reserve="${escapeHtml(offer.id)}">Reserve</button>` : ""}
          </div>
        </article>
      `).join("");
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
      const button = event.target.closest("button[data-reserve]");
      if (!button) return;
      const reservedBy = window.prompt("Reserve for which character?");
      if (!reservedBy) return;
      const response = await fetch(`/api/offers/${button.dataset.reserve}/reserve`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reserved_by: reservedBy, hours: 24}),
      });
      const data = await response.json();
      if (!data.ok) {
        window.alert(data.error || "Could not reserve offer");
        return;
      }
      await loadOffers();
    });

    loadOffers().catch((error) => {
      offersEl.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
      statusEl.textContent = "Load failed";
    });
  </script>
</body>
</html>
"""


def render_offer_page(listing: MarketListing, draft: MailDraft) -> str:
    listing_json = html.escape(json.dumps(listing.to_dict()), quote=True)
    draft_json = html.escape(json.dumps(draft.to_dict()), quote=True)
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
    @media (max-width: 620px) {{
      dl {{ grid-template-columns: 1fr; }}
      dt {{ margin-top: 8px; }}
    }}
  </style>
</head>
<body>
  <main data-listing="{listing_json}" data-draft="{draft_json}">
    <h1>{escape_html(listing.label)} {escape_html(listing.item_name)}</h1>
    <div class="meta">{escape_html(listing.location)} · {escape_html(listing.owner)} · {escape_html(listing.status)}</div>
    <section>
      <dl>
        <dt>Quantity</dt><dd>{listing.quantity:,}</dd>
        <dt>Unit price</dt><dd>{escape_html(format_isk(listing.unit_price_isk) if listing.unit_price_isk is not None else "Quote")}</dd>
        <dt>Total</dt><dd>{escape_html(format_isk(listing.total_price_isk) if listing.total_price_isk is not None else "Quote")}</dd>
        <dt>Delivery</dt><dd>{escape_html(listing.delivery or "Not specified")}</dd>
        <dt>Offer ID</dt><dd>{escape_html(listing.listing_id)}</dd>
      </dl>
    </section>
    <section>
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


def run_server(args: argparse.Namespace) -> int:
    store = MarketStore(args.market_db_path)
    url_host = url_host_for_bind(args.host)
    public_base_url = (args.public_base_url or f"http://{url_host}:{args.port}").rstrip("/")
    server = build_http_server(
        args.host,
        args.port,
        store,
        public_base_url=public_base_url,
        discord_webhook_url=args.discord_webhook_url,
        discord_timeout_seconds=args.discord_timeout,
        admin_token=args.admin_token,
    )
    url = f"http://{url_host}:{args.port}/"
    print(f"Corp market concierge listening at {url}")
    print(f"Market database: {args.market_db_path}")
    print(f"Public offer URL base: {public_base_url}")
    if args.discord_webhook_url:
        print("Discord webhook posting is enabled.")
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
        "--public-base-url",
        default=os.environ.get("CORP_MARKET_PUBLIC_BASE_URL", ""),
        help="Base URL placed in Discord offer links. Use a LAN or tunnel URL for corp access.",
    )
    serve.add_argument(
        "--admin-token",
        default=os.environ.get("CORP_MARKET_ADMIN_TOKEN", ""),
        help="Optional token for remote offer creation and status changes.",
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
    lines = [SPACE_RE.sub(" ", line).strip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    text = "\n".join(line for line in lines if line)
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


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def future_iso(*, hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    host = str(handler.client_address[0])
    return host == "::1" or host.startswith("127.")


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
