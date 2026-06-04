from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from eve_voice_pilot.corp_market import (
    CorpMarketError,
    MarketStore,
    build_discord_webhook_payload,
    build_mail_draft,
    format_isk,
    parse_isk_amount,
    parse_forum_tag_map,
    post_discord_webhook,
)


def test_parse_isk_amount_accepts_eve_shorthand():
    assert parse_isk_amount("750k") == 750_000
    assert parse_isk_amount("12.5m") == 12_500_000
    assert parse_isk_amount("1.2b") == 1_200_000_000
    assert parse_isk_amount("1,250") == 1250


def test_market_store_creates_and_lists_offer(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")

    listing = store.create_listing(
        {
            "listing_type": "sell",
            "item_name": "Venture",
            "category": "ships",
            "quantity": "3",
            "unit_price": "1.5m",
            "location": "Amarr VIII (Oris) - Emperor Family Academy",
            "owner": "Brian Example",
            "delivery": "Pickup only",
            "notes": "Starter mining hulls.",
        }
    )

    assert listing.status == "open"
    assert listing.category == "ships"
    assert listing.category_label == "Ships"
    assert listing.unit_price_isk == 1_500_000
    assert listing.total_price_isk == 4_500_000
    assert store.list_listings()[0].listing_id == listing.listing_id


def test_market_store_migrates_existing_database_without_category(tmp_path):
    db_path = tmp_path / "market.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE corp_market_listings (
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
        connection.execute(
            """
            INSERT INTO corp_market_listings (
                listing_id, listing_type, status, item_name, quantity, unit_price_isk,
                location, owner, notes, delivery, reserved_by, reserved_until, created_at, updated_at
            )
            VALUES ('old1', 'sell', 'open', 'Venture', 1, 1000000, 'Amarr', 'Seller', '', '', '', '', 'now', 'now')
            """
        )

    listing = MarketStore(db_path).get_listing("old1")

    assert listing.category == "general"
    assert listing.category_label == "General"


def test_reserve_listing_marks_buyer_and_expiry(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "want",
            "item_name": "Water",
            "quantity": 5000,
            "location": "Hek",
            "owner": "Buyer Example",
        }
    )

    reserved = store.reserve_listing(listing.listing_id, reserved_by="Seller Example", hours=6)

    assert reserved.status == "reserved"
    assert reserved.reserved_by == "Seller Example"
    assert reserved.reserved_until.endswith("Z")


def test_sold_listing_cannot_be_reserved(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "sell",
            "item_name": "Robotics",
            "quantity": 100,
            "location": "Jita",
            "owner": "Seller Example",
        }
    )
    store.set_status(listing.listing_id, "sold")

    with pytest.raises(CorpMarketError):
        store.reserve_listing(listing.listing_id, reserved_by="Buyer Example")


def test_mail_draft_for_sell_listing_is_manual_buy_request(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "sell",
            "item_name": "10MN Afterburner I",
            "quantity": 4,
            "unit_price": "80k",
            "location": "Dodixie",
            "owner": "Seller Example",
        }
    )

    draft = build_mail_draft(listing, actor="Buyer Example")

    assert draft.subject == "Corp market buy request - 10MN Afterburner I"
    assert "I want to buy your corp market listing." in draft.body
    assert "Category: General" in draft.body
    assert "Buyer: Buyer Example" in draft.body
    assert f"Offer ID: {listing.listing_id}" in draft.body


def test_mail_draft_for_want_listing_is_fulfillment_offer(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "want",
            "item_name": "Liquid Ozone",
            "category": "pi",
            "quantity": 5000,
            "location": "KBP7-G - Heir of Athra",
            "owner": "Buyer Example",
        }
    )

    draft = build_mail_draft(listing, actor="Seller Example")

    assert draft.subject == "Corp market fulfillment offer - Liquid Ozone"
    assert "I can help fill your corp market request." in draft.body
    assert "Category: PI" in draft.body
    assert "Seller: Seller Example" in draft.body


def test_discord_payload_contains_copy_mail_link_and_no_mentions(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "sell",
            "item_name": "Venture",
            "category": "ships",
            "quantity": 1,
            "unit_price": "1m",
            "location": "Amarr",
            "owner": "Seller Example",
        }
    )

    payload = build_discord_webhook_payload(listing, public_base_url="http://market.test")

    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["content"] == f"Open the listing to copy an EVE mail draft:\nhttp://market.test/offers/{listing.listing_id}"
    assert payload["embeds"][0]["title"] == "WTS Venture x1"
    assert payload["embeds"][0]["fields"][0] == {"name": "Category", "value": "Ships", "inline": True}
    assert payload["embeds"][0]["fields"][2]["value"] == format_isk(1_000_000)
    assert payload["embeds"][0]["fields"][5]["name"] == "Seller"
    assert "thread_name" not in payload


def test_discord_payload_for_forum_channel_includes_thread_name_and_tags(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "want",
            "item_name": "Tritanium",
            "category": "minerals",
            "quantity": 1_000_000,
            "unit_price": "3",
            "location": "Dhira",
            "owner": "Dandin Ridderston",
        }
    )

    payload = build_discord_webhook_payload(
        listing,
        public_base_url="http://market.test",
        forum_post=True,
        forum_tag_ids=("123", "456"),
        forum_tag_map=parse_forum_tag_map("want:789,minerals:999"),
    )

    assert payload["thread_name"] == "WTB Tritanium x1,000,000"
    assert payload["applied_tags"] == ["123", "456", "789", "999"]


def test_parse_forum_tag_map_normalizes_and_groups_ids():
    assert parse_forum_tag_map("sell:111,wts:222,ships:333,sell:111") == {
        "sell": ("111",),
        "wts": ("222",),
        "ships": ("333",),
    }


def test_post_discord_webhook_rejects_channel_links_before_network():
    with pytest.raises(CorpMarketError, match="Copy it from Channel Settings"):
        post_discord_webhook(
            "https://discord.com/channels/123/456",
            {"content": "test"},
            timeout_seconds=1,
        )
