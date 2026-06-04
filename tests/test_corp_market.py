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
    clean_multiline,
    format_isk,
    parse_isk_amount,
    parse_fit_note,
    parse_forum_tag_map,
    post_discord_webhook,
)


HAWK_FIT = """[Hawk, Hawkaw T0 blitz dark abyss]
Ballistic Control System II
Ballistic Control System II

1MN Afterburner II
Small Shield Booster II
Federation Navy Stasis Webifier
Cap Recharger II
Republic Fleet Small Cap Battery

Rocket Launcher II
Rocket Launcher II
[Empty High slot]
Rocket Launcher II
Rocket Launcher II

Small EM Shield Reinforcer II
Small Bay Loading Accelerator II


Scourge Rage Rocket x4772
Tranquil Gamma Filament x3
Calm Gamma Filament x1
Tranquil Electrical Filament x3
Tranquil Dark Filament x97
Tranquil Firestorm Filament x3"""


def test_parse_isk_amount_accepts_eve_shorthand():
    assert parse_isk_amount("750k") == 750_000
    assert parse_isk_amount("12.5m") == 12_500_000
    assert parse_isk_amount("1.2b") == 1_200_000_000
    assert parse_isk_amount("1,250") == 1250


def test_clean_multiline_preserves_fit_block_sections():
    cleaned = clean_multiline(f" \n{HAWK_FIT}\n\n", "notes", max_length=5000)

    assert cleaned.startswith("[Hawk, Hawkaw T0 blitz dark abyss]")
    assert "Ballistic Control System II\n\n1MN Afterburner II" in cleaned
    assert "Small Bay Loading Accelerator II\n\nScourge Rage Rocket x4772" in cleaned
    assert not cleaned.endswith("\n")


def test_parse_fit_note_reads_eft_clipboard_format():
    fit_note = parse_fit_note(HAWK_FIT)

    assert fit_note is not None
    assert fit_note.hull == "Hawk"
    assert fit_note.fit_name == "Hawkaw T0 blitz dark abyss"
    assert len(fit_note.fitted_lines) == 13
    assert fit_note.empty_slots == 1
    assert fit_note.cargo_lines[0] == "Scourge Rage Rocket x4772"
    assert fit_note.cargo_lines[-1] == "Tranquil Firestorm Filament x3"


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


def test_mail_draft_includes_full_fit_note_block(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "want",
            "item_name": "Hawk abyss fit",
            "category": "ships",
            "quantity": 1,
            "location": "Jita",
            "owner": "Buyer Example",
            "notes": HAWK_FIT,
        }
    )

    draft = build_mail_draft(listing, actor="Builder Example")

    assert "Fit note:\n[Hawk, Hawkaw T0 blitz dark abyss]" in draft.body
    assert "Small Bay Loading Accelerator II\n\nScourge Rage Rocket x4772" in draft.body
    assert "Tranquil Dark Filament x97" in draft.body


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


def test_discord_payload_summarizes_fit_note_without_dumping_full_block(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "want",
            "item_name": "Hawk abyss fit",
            "category": "ships",
            "quantity": 1,
            "location": "Jita",
            "owner": "Buyer Example",
            "notes": HAWK_FIT,
        }
    )

    payload = build_discord_webhook_payload(listing, public_base_url="http://market.test")
    embed = payload["embeds"][0]

    assert embed["description"] == "Fit note detected. Open the listing for the full copy/paste block."
    fit_field = next(field for field in embed["fields"] if field["name"] == "Fit Note")
    assert "Hawk - Hawkaw T0 blitz dark abyss" in fit_field["value"]
    assert "13 fitted lines, 1 empty slot; 6 cargo stacks" in fit_field["value"]
    assert "Scourge Rage Rocket x4772" in fit_field["value"]
    assert "Rocket Launcher II\nRocket Launcher II" not in fit_field["value"]


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
