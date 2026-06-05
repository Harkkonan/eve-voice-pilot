import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

import eve_voice_pilot.corp_market as corp_market
from eve_voice_pilot.corp_market import (
    CorpMarketError,
    FlightEsiSession,
    FlightEsiSessionStore,
    IndustryMaterial,
    IndustryRecipe,
    IndustryRecipeCache,
    IndustrySkill,
    MarketStore,
    ReprocessingCache,
    ReprocessingMaterial,
    ReprocessingOre,
    ReprocessingStation,
    RouteGraphCache,
    RouteSystem,
    analyze_trade_pnl_transactions,
    build_discord_webhook_payload,
    build_flight_reprocessing_locations_payload,
    build_flight_reprocessing_payload,
    build_flight_buyers_payload,
    build_flight_acquisition_payload,
    build_flight_hauling_payload,
    build_flight_industry_payload,
    build_flight_profitability_payload,
    build_flight_status_payload,
    build_flight_trade_pnl_payload,
    build_mail_draft,
    clean_multiline,
    edit_discord_webhook_message,
    fetch_flight_skills,
    fetch_flight_location,
    format_isk,
    load_reprocessing_cache,
    parse_isk_amount,
    parse_fit_note,
    parse_forum_tag_map,
    post_discord_webhook,
    render_dashboard,
    render_offer_page,
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
    assert listing.fit_image_url == ""
    assert listing.discord_message_id == ""
    assert listing.discord_thread_id == ""


def test_market_store_records_discord_sync_metadata(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    listing = store.create_listing(
        {
            "listing_type": "sell",
            "item_name": "Venture",
            "quantity": 1,
            "location": "Amarr",
            "owner": "Seller Example",
        }
    )

    synced = store.record_discord_sync(
        listing.listing_id,
        message_id="123456789012345678",
        thread_id="223456789012345678",
        error="",
    )

    assert synced.discord_message_id == "123456789012345678"
    assert synced.discord_thread_id == "223456789012345678"
    assert synced.discord_synced_at.endswith("Z")
    assert synced.discord_sync_error == ""


def test_dashboard_includes_flight_attendant_tab_and_safety_charter():
    page = render_dashboard()

    assert "data-tab-target=\"flight\"" in page
    assert "Flight Attendant" in page
    assert "Captain's Notes" in page
    assert "Read-only ESI" in page
    assert "No EVE client control" in page
    assert "OCR-driven reactions" in page
    assert "No token file yet" in page


def test_dashboard_keeps_market_offer_workflow_controls():
    page = render_dashboard()

    assert "id=\"offer-form\"" in page
    assert "data-tab-target=\"market\"" in page
    assert "Post Offer" in page
    assert "/api/offers" in page
    assert "Mail draft" in page


def test_dashboard_includes_flight_esi_hooks():
    page = render_dashboard()

    assert "/api/flight/status" in page
    assert "/api/flight/industry" in page
    assert "/api/flight/buyers" in page
    assert "/api/flight/buyers/progress" in page
    assert "/api/flight/profitability" in page
    assert "/api/flight/hauling" in page
    assert "/api/flight/hauling/progress" in page
    assert "/api/flight/acquisition" in page
    assert "/api/flight/trade-pnl" in page
    assert "/flight/login" in page
    assert "id=\"flight-system-name\"" in page
    assert "id=\"flight-login-link\"" in page
    assert "id=\"flight-blueprint-summary\"" in page
    assert "id=\"flight-asset-summary\"" in page
    assert "id=\"flight-recipe-summary\"" in page
    assert "id=\"flight-max-jumps\"" in page
    assert "id=\"flight-route-summary\"" in page
    assert "id=\"flight-buyer-scan\"" in page
    assert "id=\"flight-buyer-summary\"" in page
    assert "id=\"flight-buyer-progress-log\"" in page
    assert "id=\"flight-profit-scan\"" in page
    assert "id=\"flight-profit-summary\"" in page
    assert "id=\"flight-profit-filters\"" in page
    assert "class=\"panel profit-panel\"" in page
    assert "id=\"flight-profit-top\" class=\"decision-output\"" in page
    assert "Why This App Requests ESI Scopes" in page
    assert "It cannot buy, sell, contract, move assets, send mail, place market orders" in page
    assert "esi-wallet.read_character_wallet.v1" in page
    assert "data-tab-target=\"hauling\"" in page
    assert "data-tab-target=\"acquisition\"" in page
    assert "id=\"haul-route-form\"" in page
    assert "id=\"haul-origin\"" in page
    assert "Start system" in page
    assert "Leave blank to start from your live ESI system." in page
    assert "id=\"haul-destination\"" in page
    assert "id=\"haul-cargo-m3\" name=\"cargo_m3\" type=\"number\" min=\"1\" max=\"10000000\" step=\"any\"" in page
    assert "id=\"haul-route-preference\"" in page
    assert "Prefer safer" in page
    assert "id=\"haul-avoid-pod-kills\"" in page
    assert "Avoid recent pod kills" in page
    assert "Warning: if no route can avoid recent pod kills, the scan falls back to the shortest route." in page
    assert "id=\"haul-min-margin\"" in page
    assert "id=\"haul-min-margin-value\"" in page
    assert "id=\"haul-common-materials\"" in page
    assert "id=\"haul-market-groups\"" in page
    assert "Items to search" in page
    assert "Common materials" in page
    assert "Market categories" in page
    assert "Ships" in page
    assert "Blueprints &amp; Reactions" in page
    assert "Ammunition &amp; Charges" in page
    assert "Scanning more item types increases route calculation time." in page
    assert "id=\"haul-progress-log\"" in page
    assert "id=\"haul-scan\"" in page
    assert "id=\"haul-route-summary\"" in page
    assert "id=\"haul-opportunity-top\" class=\"decision-output\"" in page
    assert "id=\"acquisition-form\"" in page
    assert "id=\"acq-budget\"" in page
    assert "id=\"acq-broker-fee\"" in page
    assert "id=\"acq-results\" class=\"decision-output\"" in page
    assert "Market Acquisition Planner" in page
    assert "Possible trap" in page
    assert "Market history can reveal" in page
    assert "writeAcquisitionSettings" in page
    assert "renderAcquisitionOpportunities" in page
    assert "data-tab-target=\"trade-pnl\"" in page
    assert "id=\"trade-pnl-form\"" in page
    assert "id=\"trade-pnl-window-hours\"" in page
    assert "id=\"trade-pnl-lens\"" in page
    assert "id=\"trade-pnl-exclude\"" in page
    assert "id=\"trade-pnl-show-matches\"" in page
    assert "id=\"trade-pnl-results\" class=\"decision-output\"" in page
    assert "Trade Profit And Loss" in page
    assert "Considered Result" in page
    assert "renderTradePnlMatches" in page
    assert "renderTradePnlItems" in page
    assert page.count("id=\"tab-trade-pnl\"") == 1
    assert page.count("id=\"tab-reprocessing\"") == 1
    assert "https://images.evetech.net/types/" in page
    assert "reprocess-field-desk" in page
    assert "ore-specimen" in page
    assert "mineral-card" in page
    assert "Selected Ore" in page
    assert "Awaiting calculation" in page
    assert "Recovered Minerals" in page
    assert "/static/corp-market/hauler-background.png" in page
    assert "document.body.dataset.activeTab = targetTab" in page
    assert "formatElapsedDuration" in page
    assert "startHaulProgressTimer" in page
    assert "Elapsed ${escapeHtml(elapsed)}" in page
    assert "Scan duration:" in page
    assert "data-profit-filter=\"build-now\"" in page
    assert "data-profit-filter=\"source-missing\"" in page
    assert "data-profit-filter=\"price-check\"" in page
    assert "progress-spinner" in page
    assert "flight-progress" in page
    assert "startFlightProfitProgress" in page
    assert "True Profit" in page
    assert "Wallet Gain" in page
    assert "True Profit / Hour" in page
    assert "TE-adjusted job time" in page
    assert "max copy runs" in page
    assert "skills not in cache yet" in page
    assert "Market cache" in page
    assert "Math details" in page
    assert "Expected after tax and fees" in page
    assert "ME-adjusted all materials value" in page
    assert "Jita raw materials value" in page
    assert "Jita raw value coverage" in page
    assert "ME-adjusted one-run materials covered" in page


def test_static_asset_resolver_serves_only_tracked_static_files():
    asset_path = corp_market.resolve_static_asset_path("/static/corp-market/hauler-background.png")

    assert asset_path == corp_market.STATIC_ASSET_ROOT / "corp-market" / "hauler-background.png"
    assert asset_path.is_file()
    assert asset_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert corp_market.resolve_static_asset_path("/static/../AGENTS.md") is None
    assert corp_market.resolve_static_asset_path("/static/") is None


def test_flight_status_reports_missing_sso_configuration():
    payload = build_flight_status_payload(
        config=corp_market.EveSsoConfig(callback_url="http://127.0.0.1:8770/flight/callback"),
        session=None,
        callback_url="http://127.0.0.1:8770/flight/callback",
    )

    assert payload["ok"] is True
    assert payload["sso_configured"] is False
    assert payload["connected"] is False
    assert payload["callback_url"] == "http://127.0.0.1:8770/flight/callback"
    assert "esi-skills.read_skills.v1" in payload["required_scopes"]
    assert "esi-characters.read_standings.v1" in payload["required_scopes"]
    assert "esi-wallet.read_character_wallet.v1" in payload["required_scopes"]
    assert payload["membership"]["required"] is False
    assert payload["hosting"]["token_storage"] == "server-memory-only"


def test_flight_status_rejects_non_allowlisted_session_before_esi_fetch():
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Guest Pilot",
        corporation_id=9999,
        corporation_name="Other Corp",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1",),
        access_token="access-token",
        connected_at="2026-06-05T00:00:00Z",
        expires_at=9999999999,
        membership_ok=False,
    )

    payload = build_flight_status_payload(
        config=corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.test/flight/callback",
            allowed_corporation_ids=(1001,),
        ),
        session=session,
        callback_url="https://market.test/flight/callback",
        public_base_url="https://market.test",
        public_hosting_mode=True,
        secure_cookies=True,
    )

    assert payload["connected"] is True
    assert payload["membership"]["required"] is True
    assert payload["membership"]["allowed"] is False
    assert payload["error"] == "This EVE character is not in the configured corp/alliance allowlist."
    assert payload["location"] is None


def test_flight_esi_session_store_keeps_access_token_in_memory():
    pilot = corp_market.VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1",),
        membership_ok=True,
    )
    store = FlightEsiSessionStore()

    session_id = store.create(pilot, access_token="access-token", expires_in=600)
    session = store.get(session_id)

    assert session is not None
    assert session.character_name == "Scout Pilot"
    assert session.access_token == "access-token"
    assert session.membership_ok is True
    assert session.to_public_dict()["membership_ok"] is True
    assert session.expires_in_seconds > 0


def test_trade_pnl_fifo_matches_expected_spread_and_fee_gap():
    transactions = [
        {
            "transaction_id": 101,
            "date": "2026-06-02T12:00:00Z",
            "type_id": 34,
            "quantity": 60,
            "unit_price": 15,
            "is_buy": False,
        },
        {
            "transaction_id": 100,
            "date": "2026-06-01T12:00:00Z",
            "type_id": 34,
            "quantity": 100,
            "unit_price": 10,
            "is_buy": True,
        },
        {
            "transaction_id": 102,
            "date": "2026-06-01T13:00:00Z",
            "type_id": 35,
            "quantity": 10,
            "unit_price": 100,
            "is_buy": True,
        },
        {
            "transaction_id": 103,
            "date": "2026-06-03T12:00:00Z",
            "type_id": 35,
            "quantity": 10,
            "unit_price": 80,
            "is_buy": False,
        },
        {
            "transaction_id": 104,
            "date": "2026-06-03T13:00:00Z",
            "type_id": 36,
            "quantity": 5,
            "unit_price": 50,
            "is_buy": False,
        },
    ]
    journal_entries = [
        {
            "id": 501,
            "date": "2026-06-02T12:00:01Z",
            "ref_type": "transaction_tax",
            "amount": -5,
            "context_id": 101,
            "context_id_type": "market_transaction_id",
        },
        {
            "id": 502,
            "date": "2026-06-03T12:00:01Z",
            "ref_type": "brokers_fee",
            "amount": -3,
            "context_id": 103,
            "context_id_type": "market_transaction_id",
        },
        {
            "id": 503,
            "date": "2026-06-03T12:00:02Z",
            "ref_type": "transaction_tax",
            "amount": -2,
            "context_id": 999999,
            "context_id_type": "market_transaction_id",
        },
    ]

    pnl = analyze_trade_pnl_transactions(
        transactions,
        journal_entries=journal_entries,
        type_names={34: "Tritanium", 35: "Pyerite", 36: "Mexallon"},
        include_matches=True,
        now=datetime(2026, 6, 5, tzinfo=timezone.utc),
    )

    totals = pnl["totals"]
    assert totals["expected_profit_isk"] == pytest.approx(100)
    assert totals["actual_profit_isk"] == pytest.approx(92)
    assert totals["market_fee_isk"] == pytest.approx(-10)
    assert totals["unallocated_fee_isk"] == pytest.approx(-2)
    assert totals["wallet_fee_adjusted_profit_isk"] == pytest.approx(90)
    assert totals["open_inventory_cost_isk"] == pytest.approx(400)
    assert totals["unmatched_sell_revenue_isk"] == pytest.approx(250)

    items = {item["type_id"]: item for item in pnl["items"]}
    assert items[34]["item_name"] == "Tritanium"
    assert items[34]["status"] == "profit"
    assert items[34]["actual_profit_isk"] == pytest.approx(295)
    assert items[34]["open_quantity"] == 40
    assert items[34]["matches"] == [
        {
            "buy_transaction_id": 100,
            "buy_date": "2026-06-01T12:00:00Z",
            "sell_transaction_id": 101,
            "sell_date": "2026-06-02T12:00:00Z",
            "quantity": 60,
            "buy_unit_price": 10,
            "sell_unit_price": 15,
            "buy_cost_isk": 600,
            "sell_revenue_isk": 900,
            "fee_isk": -5,
            "expected_profit_isk": 300,
            "actual_profit_isk": 295,
        }
    ]
    assert items[35]["status"] == "loss"
    assert items[35]["actual_profit_isk"] == pytest.approx(-203)
    assert items[35]["matches"][0]["actual_profit_isk"] == pytest.approx(-203)
    assert items[36]["status"] == "needs-older-buys"
    assert items[36]["unmatched_sell_quantity"] == 5


def test_trade_pnl_window_and_exclusions_adjust_considered_result():
    transactions = [
        {
            "transaction_id": 200,
            "date": "2026-06-05T00:10:00Z",
            "type_id": 35,
            "quantity": 10,
            "unit_price": 100,
            "is_buy": True,
        },
        {
            "transaction_id": 201,
            "date": "2026-06-05T00:20:00Z",
            "type_id": 35,
            "quantity": 10,
            "unit_price": 80,
            "is_buy": False,
        },
        {
            "transaction_id": 202,
            "date": "2026-06-04T22:00:00Z",
            "type_id": 34,
            "quantity": 10,
            "unit_price": 10,
            "is_buy": True,
        },
        {
            "transaction_id": 203,
            "date": "2026-06-04T22:30:00Z",
            "type_id": 34,
            "quantity": 10,
            "unit_price": 15,
            "is_buy": False,
        },
    ]
    journal_entries = [
        {
            "id": 601,
            "date": "2026-06-05T00:20:01Z",
            "ref_type": "transaction_tax",
            "amount": -2,
            "context_id": 201,
            "context_id_type": "market_transaction_id",
        }
    ]

    pnl = analyze_trade_pnl_transactions(
        transactions,
        journal_entries=journal_entries,
        type_names={34: "Tritanium", 35: "Pyerite"},
        window_hours=1,
        excluded_tokens=("pyrite",),
        include_matches=True,
        now=datetime(2026, 6, 5, 0, 30, tzinfo=timezone.utc),
    )

    totals = pnl["totals"]
    items = {item["type_id"]: item for item in pnl["items"]}
    assert pnl["window_hours"] == 1
    assert pnl["window_label"] == "1 hour"
    assert pnl["transaction_count"] == 2
    assert set(items) == {35}
    assert items[35]["status"] == "excluded-loss"
    assert items[35]["excluded"] is True
    assert items[35]["excluded_reason"] == "Excluded by pyrite"
    assert items[35]["actual_profit_isk"] == pytest.approx(-202)
    assert items[35]["matches"][0]["actual_profit_isk"] == pytest.approx(-202)
    assert totals["actual_profit_isk"] == pytest.approx(-202)
    assert totals["considered_result_isk"] == pytest.approx(0)
    assert totals["excluded_actual_profit_isk"] == pytest.approx(-202)
    assert totals["excluded_item_count"] == 1
    assert totals["considered_item_count"] == 0
    assert totals["loss_item_count"] == 1


def test_build_flight_trade_pnl_payload_uses_wallet_scope(monkeypatch):
    current_time = datetime.now(timezone.utc)
    buy_date = (current_time - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    sell_date = (current_time - timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Trader Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-wallet.read_character_wallet.v1",),
        access_token="access-token",
        connected_at="2026-06-05T00:00:00Z",
        expires_at=9999999999,
    )

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_wallet_transactions",
        lambda config, session: [
            {
                "transaction_id": 100,
                "date": buy_date,
                "type_id": 34,
                "quantity": 10,
                "unit_price": 100,
                "is_buy": True,
            },
            {
                "transaction_id": 101,
                "date": sell_date,
                "type_id": 34,
                "quantity": 10,
                "unit_price": 130,
                "is_buy": False,
            },
        ],
    )
    monkeypatch.setattr(corp_market, "fetch_flight_wallet_journal", lambda config, session: [])
    monkeypatch.setattr(corp_market, "fetch_universe_names", lambda config, type_ids: {34: "Tritanium"})

    payload = build_flight_trade_pnl_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        window_hours=720,
        include_matches=True,
    )

    assert payload["ok"] is True
    assert payload["trade_pnl"]["window_hours"] == 720
    assert payload["trade_pnl"]["items"][0]["item_name"] == "Tritanium"
    assert payload["trade_pnl"]["items"][0]["matches"][0]["buy_transaction_id"] == 100
    assert payload["trade_pnl"]["totals"]["actual_profit_isk"] == pytest.approx(300)

    missing_scope_session = FlightEsiSession(
        character_id=123456789,
        character_name="Trader Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(),
        access_token="access-token",
        connected_at="2026-06-05T00:00:00Z",
        expires_at=9999999999,
    )
    with pytest.raises(CorpMarketError, match="esi-wallet.read_character_wallet.v1"):
        build_flight_trade_pnl_payload(
            config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
            session=missing_scope_session,
        )


def test_public_hosting_config_requires_https_sso_and_member_allowlist():
    unsafe_config = corp_market.EveSsoConfig(callback_url="http://127.0.0.1:8770/flight/callback")

    errors = corp_market.public_hosting_config_errors(
        public_base_url="http://127.0.0.1:8770",
        sso_config=unsafe_config,
        public_hosting_mode=True,
    )

    assert "--public-base-url must be an https URL" in errors[0]
    assert "EVE SSO client id" in errors[1]
    assert "allowed-corporation" in errors[2]

    safe_config = corp_market.EveSsoConfig(
        client_id="client-id",
        client_secret="client-secret",
        callback_url="https://market.test/flight/callback",
        allowed_corporation_ids=(1001,),
    )
    assert (
        corp_market.public_hosting_config_errors(
            public_base_url="https://market.test",
            sso_config=safe_config,
            public_hosting_mode=True,
        )
        == []
    )


def test_public_hosting_helpers_tighten_callbacks_cookies_and_writes():
    assert (
        corp_market.default_flight_callback_url(
            public_base_url="https://market.test",
            url_host="127.0.0.1",
            port=8770,
        )
        == "https://market.test/flight/callback"
    )
    assert "Secure" in corp_market.flight_session_cookie_header("session-id", secure=True)
    assert "Secure" in corp_market.clear_flight_session_cookie_header(secure=True)
    assert not corp_market.market_write_access_allowed(
        is_loopback=True,
        public_hosting_mode=True,
        admin_token="",
        auth_header="",
        token_header="",
    )
    assert corp_market.market_write_access_allowed(
        is_loopback=False,
        public_hosting_mode=True,
        admin_token="secret",
        auth_header="Bearer secret",
        token_header="",
    )
    assert corp_market.market_write_access_allowed(
        is_loopback=False,
        public_hosting_mode=True,
        admin_token="",
        auth_header="",
        token_header="",
        trusted_member=True,
    )
    assert corp_market.market_write_access_allowed(
        is_loopback=False,
        public_hosting_mode=False,
        admin_token="",
        auth_header="",
        token_header="",
    )


def test_fetch_flight_location_uses_read_only_esi_scope(monkeypatch):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1",),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )
    calls = []

    def fake_get_json(url, *, timeout_seconds=30.0, headers=None):
        calls.append((url, headers or {}))
        if "/location/" in url:
            return {"solar_system_id": 30000142}
        if "/universe/systems/30000142/" in url:
            return {"name": "Jita", "constellation_id": 20000020}
        raise AssertionError(url)

    monkeypatch.setattr(corp_market, "get_json", fake_get_json)

    location = fetch_flight_location(corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"), session)

    assert location["solar_system_id"] == 30000142
    assert location["solar_system_name"] == "Jita"
    assert location["source"] == "esi-location.read_location.v1"
    assert calls[0][0] == "https://esi.test/latest/characters/123456789/location/?datasource=tranquility"
    assert calls[0][1]["Authorization"] == "Bearer access-token"
    assert "X-Compatibility-Date" in calls[0][1]


def test_fetch_flight_skills_uses_read_skills_scope(monkeypatch):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-skills.read_skills.v1",),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )
    calls = []

    def fake_get_json(url, *, timeout_seconds=30.0, headers=None):
        calls.append((url, headers or {}))
        return {
            "skills": [
                {
                    "skill_id": corp_market.ACCOUNTING_SKILL_TYPE_ID,
                    "active_skill_level": 5,
                    "trained_skill_level": 5,
                }
            ]
        }

    monkeypatch.setattr(corp_market, "get_json", fake_get_json)

    skills = fetch_flight_skills(corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"), session)

    assert skills["skills"][0]["skill_id"] == corp_market.ACCOUNTING_SKILL_TYPE_ID
    assert calls[0][0] == "https://esi.test/latest/characters/123456789/skills/?datasource=tranquility"
    assert calls[0][1]["Authorization"] == "Bearer access-token"
    assert "X-Compatibility-Date" in calls[0][1]


def test_sales_tax_profile_uses_accounting_skill_level():
    profile = corp_market.build_sales_tax_profile(
        {
            "skills": [
                {
                    "skill_id": corp_market.ACCOUNTING_SKILL_TYPE_ID,
                    "active_skill_level": 5,
                    "trained_skill_level": 5,
                }
            ]
        }
    )

    assert profile["accounting_level"] == 5
    assert profile["rate"] == pytest.approx(0.03375)
    assert profile["broker_fee_rate"] == 0.0


def test_adjusted_material_quantity_applies_blueprint_me_to_whole_job():
    assert corp_market.adjusted_material_quantity(1000, 10) == 900
    assert corp_market.adjusted_material_quantity(500, 10) == 450
    assert corp_market.adjusted_material_quantity(333, 10) == 300
    assert corp_market.adjusted_material_quantity(1, 10, runs=10) == 10
    assert corp_market.adjusted_material_quantity(1000, -2) == 1020


def test_adjusted_job_time_uses_blueprint_te():
    assert corp_market.adjusted_job_time_seconds(600, 20) == 480
    assert corp_market.adjusted_job_time_seconds(333, 20) == 267
    assert corp_market.adjusted_job_time_seconds(600, 20, runs=3) == 1440
    assert corp_market.isk_per_hour(5600, 480) == pytest.approx(42000)


def test_owned_blueprint_parser_treats_unlimited_runs_as_original():
    blueprint = corp_market.owned_blueprint_from_esi(
        {"type_id": 681, "quantity": 1, "runs": -1, "material_efficiency": 10, "time_efficiency": 20}
    )

    assert blueprint is not None
    assert blueprint.is_original is True
    assert blueprint.usable_for_one_run is True
    assert blueprint.kind == "Original"
    assert blueprint.limited_runs is None


def test_haul_cargo_capacity_preserves_decimal_values():
    assert corp_market.clamp_haul_cargo_m3("1234.56") == pytest.approx(1234.56)
    assert corp_market.clamp_haul_cargo_m3("0") == 1.0
    assert corp_market.clamp_haul_cargo_m3("20000000") == 10_000_000.0


def test_haul_detour_margin_clamps_to_slider_range():
    assert corp_market.clamp_haul_min_detour_margin_percent("12.5") == pytest.approx(12.5)
    assert corp_market.clamp_haul_min_detour_margin_percent("-1") == 0.0
    assert corp_market.clamp_haul_min_detour_margin_percent("999") == 500.0


def test_haul_route_preference_normalizes_eve_route_terms():
    assert corp_market.normalize_haul_route_preference("Prefer safer") == "safer"
    assert corp_market.normalize_haul_route_preference("secure") == "safer"
    assert corp_market.normalize_haul_route_preference("shortest") == "shorter"
    assert corp_market.normalize_haul_route_preference("LessSecure") == "less_secure"
    assert corp_market.normalize_haul_route_preference("???") == "safer"


def test_haul_market_group_ids_parse_and_dedupe():
    assert corp_market.clean_haul_market_group_ids(["4,11", "4", "bad", " 19 "]) == (4, 11, 19)


def test_haul_item_targets_combine_common_materials_and_market_groups(monkeypatch, tmp_path):
    recipe_cache = IndustryRecipeCache(
        path=tmp_path / "recipes.json",
        available=True,
        recipes={
            681: IndustryRecipe(
                blueprint_type_id=681,
                blueprint_name="Hobgoblin I Blueprint",
                product_type_id=165,
                product_name="Hobgoblin I",
                product_quantity=1,
                materials=(IndustryMaterial(type_id=34, name="Tritanium", quantity=1000, volume_m3=0.01),),
            )
        },
    )

    def fake_market_group_targets(config, group_ids):
        assert tuple(group_ids) == (4,)
        return (
            [
                {
                    "type_id": 34,
                    "name": "Tritanium",
                    "recipe_count": 0,
                    "volume_m3": 0.01,
                    "source_label": "Ships",
                },
                {
                    "type_id": 603,
                    "name": "Merlin",
                    "recipe_count": 0,
                    "volume_m3": 2500.0,
                    "source_label": "Ships",
                },
            ],
            {
                "source": "test-market-groups",
                "selected_market_group_ids": [4],
                "selected_market_groups": [{"market_group_id": 4, "name": "Ships"}],
                "selected_market_group_count": 1,
                "market_group_item_types": 2,
            },
        )

    monkeypatch.setattr(corp_market, "build_market_group_targets", fake_market_group_targets)

    targets, scope = corp_market.build_haul_item_targets(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        recipe_cache=recipe_cache,
        include_common_materials=True,
        market_group_ids=(4,),
    )

    assert [target["type_id"] for target in targets] == [34, 603]
    assert targets[0]["source_labels"] == ["Common materials", "Ships"]
    assert scope["include_common_materials"] is True
    assert scope["selected_market_group_ids"] == [4]
    assert scope["market_group_item_types"] == 2
    assert scope["total_item_types"] == 2


def test_haul_route_plan_retries_to_avoid_recent_pod_kills(monkeypatch):
    route_calls = []

    def fake_recent_pods(config):
        return (2,)

    def fake_route(config, *, origin_solar_system_id, destination_solar_system_id, route_preference, avoid_system_ids=()):
        avoid_tuple = tuple(sorted(avoid_system_ids))
        route_calls.append((route_preference, avoid_tuple))
        return [1, 4, 3] if 2 in avoid_tuple else [1, 2, 3]

    monkeypatch.setattr(corp_market, "fetch_recent_pod_kill_system_ids", fake_recent_pods)
    monkeypatch.setattr(corp_market, "fetch_esi_route_path", fake_route)

    plan = corp_market.build_haul_route_plan(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        origin_solar_system_id=1,
        destination_solar_system_id=3,
        route_preference="Prefer safer",
        avoid_recent_pod_kills=True,
        adjacency={1: (2, 4), 2: (1, 3), 3: (2, 4), 4: (1, 3)},
    )

    assert plan["path"] == [1, 4, 3]
    assert plan["preference"] == "safer"
    assert plan["avoid_recent_pod_kills"] is True
    assert plan["avoided_pod_kill_system_ids"] == [2]
    assert plan["route_pod_kill_system_ids"] == []
    assert route_calls == [("safer", ()), ("safer", (2,))]


def test_build_flight_industry_payload_summarizes_blueprints_and_assets(monkeypatch, tmp_path):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(
            "esi-location.read_location.v1",
            "esi-assets.read_assets.v1",
            "esi-characters.read_blueprints.v1",
            "esi-skills.read_skills.v1",
            "esi-characters.read_standings.v1",
        ),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_blueprints",
        lambda config, session: [
            {"type_id": 681, "quantity": -1, "runs": -1, "material_efficiency": 10, "time_efficiency": 20},
            {"type_id": 681, "quantity": -2},
            {"type_id": 983, "quantity": -1},
        ],
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_assets",
        lambda config, session: [
            {"type_id": 34, "quantity": 5000, "location_id": 60008494},
            {"type_id": 34, "quantity": 3000, "location_id": 60008494},
            {"type_id": 35, "quantity": 1000, "location_id": 60003760},
        ],
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_universe_names",
        lambda config, ids: {681: "Hobgoblin I Blueprint", 983: "Badger Blueprint", 34: "Tritanium", 35: "Pyerite"},
    )
    monkeypatch.setattr(
        corp_market,
        "load_industry_recipe_cache",
        lambda: IndustryRecipeCache(
            path=tmp_path / "eve_industry_recipes.json",
            available=True,
            build_number=3374020,
            release_date="2026-06-03T12:42:22Z",
            generated_at="2026-06-04T00:00:00Z",
            recipes={
                681: IndustryRecipe(
                    blueprint_type_id=681,
                    blueprint_name="Hobgoblin I Blueprint",
                    product_type_id=165,
                    product_name="Hobgoblin I",
                    product_quantity=1,
                    materials=(
                        IndustryMaterial(type_id=34, name="Tritanium", quantity=5000),
                        IndustryMaterial(type_id=35, name="Pyerite", quantity=500),
                    ),
                    manufacturing_time_seconds=600,
                    max_production_limit=1500,
                    skills=(IndustrySkill(type_id=3380, name="Industry", level=1),),
                )
            },
        ),
    )

    payload = build_flight_industry_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
    )

    assert payload["ok"] is True
    assert payload["industry"]["blueprints"]["total"] == 3
    assert payload["industry"]["blueprints"]["unique_types"] == 2
    assert payload["industry"]["blueprints"]["originals"] == 2
    assert payload["industry"]["blueprints"]["copies"] == 1
    assert payload["industry"]["blueprints"]["top_types"][0]["name"] == "Hobgoblin I Blueprint"
    assert payload["industry"]["blueprints"]["top_types"][0]["product_name"] == "Hobgoblin I"
    assert payload["industry"]["recipes"]["available"] is True
    assert payload["industry"]["recipes"]["known_blueprint_types"] == 1
    assert payload["industry"]["recipes"]["missing_blueprint_types"] == 1
    assert payload["industry"]["buildability"]["buildable_one_run_types"] == 1
    assert payload["industry"]["buildability"]["top_candidates"][0]["can_build_one_run"] is True
    assert payload["industry"]["buildability"]["top_candidates"][0]["blueprint_material_efficiency"] == 10
    assert payload["industry"]["buildability"]["top_candidates"][0]["base_time_seconds"] == 600
    assert payload["industry"]["buildability"]["top_candidates"][0]["adjusted_time_seconds"] == 480
    assert payload["industry"]["buildability"]["top_candidates"][0]["max_production_limit"] == 1500
    assert payload["industry"]["buildability"]["top_candidates"][0]["required_skills"][0]["name"] == "Industry"
    assert payload["industry"]["blueprints"]["top_types"][0]["best_material_efficiency"] == 10
    assert payload["industry"]["blueprints"]["top_types"][0]["best_time_efficiency"] == 20
    assert payload["industry"]["blueprints"]["top_types"][0]["base_time_seconds"] == 600
    assert payload["industry"]["blueprints"]["top_types"][0]["max_production_limit"] == 1500
    assert payload["industry"]["blueprints"]["top_types"][0]["required_skills"][0]["level"] == 1
    assert payload["industry"]["assets"]["stacks"] == 3
    assert payload["industry"]["assets"]["unique_types"] == 2
    assert payload["industry"]["assets"]["total_units"] == 9000
    assert payload["industry"]["assets"]["locations"] == 2
    assert payload["industry"]["assets"]["top_types"][0]["name"] == "Tritanium"


def test_load_industry_recipe_cache_reads_compact_cache(tmp_path):
    cache_path = tmp_path / "eve_industry_recipes.json"
    cache_path.write_text(
        json.dumps(
            {
                "schema": "eve_voice_pilot.industry_recipes.v1",
                "build_number": 3374020,
                "release_date": "2026-06-03T12:42:22Z",
                "generated_at": "2026-06-04T00:00:00Z",
                "recipes": {
                    "681": {
                        "blueprint_type_id": 681,
                        "blueprint_name": "Hobgoblin I Blueprint",
                        "product_type_id": 165,
                        "product_name": "Hobgoblin I",
                        "product_quantity": 1,
                        "max_production_limit": 1500,
                        "manufacturing_time_seconds": 600,
                        "materials": [{"type_id": 34, "name": "Tritanium", "quantity": 5000}],
                        "skills": [{"type_id": 3380, "name": "Industry", "level": 1}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    cache = corp_market.load_industry_recipe_cache(cache_path)

    assert cache.available is True
    assert cache.build_number == 3374020
    assert cache.recipe_count == 1
    assert cache.recipes[681].product_name == "Hobgoblin I"
    assert cache.recipes[681].max_production_limit == 1500
    assert cache.recipes[681].manufacturing_time_seconds == 600
    assert cache.recipes[681].materials[0].name == "Tritanium"
    assert cache.recipes[681].skills[0].name == "Industry"


def test_load_route_graph_cache_reads_compact_cache(tmp_path):
    cache_path = tmp_path / "eve_route_graph.json"
    cache_path.write_text(
        json.dumps(
            {
                "schema": "eve_voice_pilot.route_graph.v1",
                "build_number": 3374020,
                "release_date": "2026-06-03T12:42:22Z",
                "generated_at": "2026-06-04T00:00:00Z",
                "systems": {
                    "30000142": {
                        "solar_system_id": 30000142,
                        "name": "Jita",
                        "region_id": 10000002,
                        "security_status": 0.945,
                    }
                },
                "adjacency": {"30000142": [30000144]},
            }
        ),
        encoding="utf-8",
    )

    cache = corp_market.load_route_graph_cache(cache_path)

    assert cache.available is True
    assert cache.build_number == 3374020
    assert cache.system_count == 1
    assert cache.adjacency[30000142] == (30000144,)
    assert cache.systems[30000142].name == "Jita"


def test_load_reprocessing_cache_reads_ore_and_station_data(tmp_path):
    cache_path = tmp_path / "eve_reprocessing.json"
    cache_path.write_text(
        json.dumps(
            {
                "schema": "eve_voice_pilot.reprocessing.v1",
                "build_number": 3374020,
                "release_date": "2026-06-03T12:42:22Z",
                "generated_at": "2026-06-04T00:00:00Z",
                "ores": {
                    "1230": {
                        "type_id": 1230,
                        "name": "Veldspar",
                        "group_id": 462,
                        "group_name": "Veldspar",
                        "portion_size": 100,
                        "volume_m3": 0.1,
                        "specialization_skill_type_id": 60377,
                        "specialization_skill_name": "Simple Ore Processing",
                        "materials": [
                            {"type_id": 34, "name": "Tritanium", "quantity": 400, "volume_m3": 0.01}
                        ],
                    }
                },
                "stations": {
                    "60000004": {
                        "station_id": 60000004,
                        "owner_id": 1000002,
                        "owner_name": "CBD Corporation",
                        "owner_faction_id": 500001,
                        "solar_system_id": 30002780,
                        "reprocessing_efficiency": 0.5,
                        "reprocessing_tax_rate": 0.05,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    cache = load_reprocessing_cache(cache_path)

    assert cache.available is True
    assert cache.build_number == 3374020
    assert cache.ore_count == 1
    assert cache.station_count == 1
    assert cache.ores[1230].name == "Veldspar"
    assert cache.ores[1230].materials[0].name == "Tritanium"
    assert cache.stations[60000004].reprocessing_efficiency == 0.5


def test_build_flight_reprocessing_payload_uses_esi_skills_standing_and_implant(monkeypatch, tmp_path):
    market_calls = []
    cache = ReprocessingCache(
        path=tmp_path / "eve_reprocessing.json",
        available=True,
        build_number=3374020,
        ores={
            1230: ReprocessingOre(
                type_id=1230,
                name="Veldspar",
                group_id=462,
                group_name="Veldspar",
                portion_size=100,
                volume_m3=0.1,
                specialization_skill_type_id=60377,
                specialization_skill_name="Simple Ore Processing",
                materials=(
                    ReprocessingMaterial(type_id=34, name="Tritanium", quantity=400, volume_m3=0.01),
                ),
            )
        },
        stations={
            60000004: ReprocessingStation(
                station_id=60000004,
                owner_id=1000002,
                owner_name="CBD Corporation",
                owner_faction_id=500001,
                solar_system_id=30002780,
                reprocessing_efficiency=0.5,
                reprocessing_tax_rate=0.05,
            ),
            60000007: ReprocessingStation(
                station_id=60000007,
                owner_id=1000002,
                owner_name="CBD Corporation",
                owner_faction_id=500001,
                solar_system_id=30002779,
                reprocessing_efficiency=0.5,
                reprocessing_tax_rate=0.05,
            ),
        },
    )
    route_cache = RouteGraphCache(
        path=tmp_path / "eve_route_graph.json",
        available=True,
        build_number=3374020,
        systems={
            30000142: RouteSystem(
                solar_system_id=30000142,
                name="Jita",
                region_id=200,
                security_status=0.9,
            )
        },
        adjacency={},
    )
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(
            "esi-location.read_location.v1",
            "esi-skills.read_skills.v1",
            "esi-characters.read_standings.v1",
            "esi-clones.read_implants.v1",
        ),
        access_token="access-token",
        connected_at="2026-06-05T00:00:00Z",
        expires_at=9999999999,
    )
    monkeypatch.setattr(corp_market, "load_reprocessing_cache", lambda: cache)
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_skills",
        lambda config, session: {
            "skills": [
                {"skill_id": corp_market.REPROCESSING_SKILL_TYPE_ID, "active_skill_level": 5},
                {"skill_id": corp_market.REPROCESSING_EFFICIENCY_SKILL_TYPE_ID, "active_skill_level": 4},
                {"skill_id": 60377, "active_skill_level": 3},
            ]
        },
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_standings",
        lambda config, session: [{"from_id": 1000002, "from_type": "npc_corp", "standing": 4.0}],
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_location",
        lambda config, session: {
            "solar_system_id": 30002780,
            "solar_system_name": "Airaken",
            "station_id": 60000004,
            "structure_id": None,
            "updated_at": "2026-06-05T00:00:00Z",
        },
    )
    monkeypatch.setattr(corp_market, "fetch_flight_implants", lambda config, session: [27174])
    monkeypatch.setattr(
        corp_market,
        "fetch_universe_station",
        lambda config, station_id: {"name": f"Selected Station {station_id}", "owner": 1000002},
    )
    monkeypatch.setattr(corp_market, "load_route_graph_cache", lambda: route_cache)
    monkeypatch.setattr(
        corp_market,
        "fetch_market_prices",
        lambda config: {
            34: {"type_id": 34, "average_price": 5.0, "adjusted_price": 4.0},
            1230: {"type_id": 1230, "average_price": 11.0, "adjusted_price": 10.0},
        },
    )

    def fake_fetch_market_buy_orders(config, *, region_id, type_id):
        market_calls.append((region_id, type_id))
        prices = {34: 6.0, 1230: 12.0}
        price = prices.get(type_id)
        if price is None:
            return []
        return [
            {
                "order_id": type_id + 1000,
                "is_buy_order": True,
                "system_id": 30000142,
                "location_id": 60003760,
                "price": price,
                "volume_remain": 100000,
                "min_volume": 1,
            }
        ]

    monkeypatch.setattr(corp_market, "fetch_market_buy_orders", fake_fetch_market_buy_orders)

    payload = build_flight_reprocessing_payload(
        config=corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.test/flight/callback",
        ),
        session=session,
        ore_type_id=1230,
        quantity=1000,
        reprocessing_station_id=60000007,
    )

    assert payload["ore"]["name"] == "Veldspar"
    assert payload["input"]["portions"] == 10
    assert payload["skills"]["reprocessing_level"] == 5
    assert payload["skills"]["reprocessing_efficiency_level"] == 4
    assert payload["skills"]["specialization_level"] == 3
    assert payload["implant"]["bonus_percent"] == 4.0
    assert payload["facility"]["location_id"] == 60000007
    assert payload["facility"]["location_name"] == "Selected Station 60000007"
    assert payload["facility"]["source"] == "selected-sde-npc-station"
    assert payload["facility"]["facility_yield_percent"] == 50.0
    assert payload["facility"]["base_station_tax_percent"] == pytest.approx(5.0)
    assert payload["facility"]["station_tax_percent"] == pytest.approx(2.0)
    assert payload["facility"]["adjusted_station_tax_percent"] == pytest.approx(2.0)
    assert payload["facility"]["standing_source"] == "owner-corporation"
    assert payload["facility"]["standing_row"]["from_id"] == 1000002
    assert payload["facility"]["standing_row"]["from_type"] == "npc_corp"
    assert payload["facility"]["standing_row"]["standing"] == pytest.approx(4.0)
    assert payload["yield"]["gross_yield_percent"] == pytest.approx(68.45904)
    assert payload["yield"]["net_yield_percent"] == pytest.approx(67.0898592)
    assert payload["yield"]["breakdown"]["facility_yield_percent"] == pytest.approx(50.0)
    assert payload["yield"]["breakdown"]["reprocessing_multiplier"] == pytest.approx(1.15)
    assert payload["yield"]["breakdown"]["reprocessing_efficiency_multiplier"] == pytest.approx(1.08)
    assert payload["yield"]["breakdown"]["specialization_multiplier"] == pytest.approx(1.06)
    assert payload["yield"]["breakdown"]["implant_multiplier"] == pytest.approx(1.04)
    assert payload["yield"]["breakdown"]["structure_multiplier"] == pytest.approx(1.0)
    assert payload["yield"]["breakdown"]["processing_fee_percent"] == pytest.approx(2.0)
    assert payload["materials"][0]["base_quantity"] == 4000
    assert payload["materials"][0]["gross_quantity"] == 2738
    assert payload["materials"][0]["station_tax_quantity"] == 55
    assert payload["materials"][0]["net_quantity"] == 2683
    assert payload["materials"][0]["eve_estimate_unit_price"] == pytest.approx(5.0)
    assert payload["materials"][0]["eve_estimate_price_source"] == "average_price"
    assert payload["materials"][0]["eve_estimate_value"] == pytest.approx(13415.0)
    assert market_calls == [(200, 34), (200, 1230)]
    assert payload["materials"][0]["jita_buy_price"] == 6.0
    assert payload["materials"][0]["jita_value"] == pytest.approx(16098.0)
    assert payload["materials"][0]["jita_complete"] is True
    valuation = payload["jita_valuation"]
    assert valuation["system"]["name"] == "Jita"
    assert valuation["processed_material_value"] == pytest.approx(16098.0)
    assert valuation["processed_partial_material_value"] == pytest.approx(16098.0)
    assert valuation["ore_value"] == pytest.approx(12000.0)
    assert valuation["ore_partial_value"] == pytest.approx(12000.0)
    assert valuation["value_delta"] == pytest.approx(4098.0)
    assert valuation["processed_complete"] is True
    assert valuation["ore_complete"] is True
    assert valuation["eve_estimate"]["source"] == "ESI /markets/prices average_price with adjusted_price fallback"
    assert valuation["eve_estimate"]["processed_material_value"] == pytest.approx(13415.0)
    assert valuation["eve_estimate"]["ore_value"] == pytest.approx(11000.0)
    assert valuation["eve_estimate"]["value_delta"] == pytest.approx(2415.0)
    assert valuation["eve_estimate"]["processed_complete"] is True
    assert valuation["eve_estimate"]["ore_price_source"] == "average_price"


def test_reprocessing_location_profile_warns_when_standing_missing(monkeypatch, tmp_path):
    cache = ReprocessingCache(
        path=tmp_path / "eve_reprocessing.json",
        available=True,
        build_number=3374020,
        ores={},
        stations={
            60000004: ReprocessingStation(
                station_id=60000004,
                owner_id=1000002,
                owner_name="CBD Corporation",
                owner_faction_id=500001,
                solar_system_id=30002780,
                reprocessing_efficiency=0.5,
                reprocessing_tax_rate=0.05,
            )
        },
    )
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1", "esi-skills.read_skills.v1", "esi-characters.read_standings.v1"),
        access_token="access-token",
        connected_at="2026-06-05T00:00:00Z",
        expires_at=9999999999,
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_universe_station",
        lambda config, station_id: {"name": "CBD Station", "owner": 1000002},
    )

    profile = corp_market.build_reprocessing_location_profile(
        corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.test/flight/callback",
        ),
        session,
        cache=cache,
        location={"station_id": 60000004},
        standings=[],
        selected_station_id=None,
        facility_yield_percent=None,
        station_tax_percent=None,
    )

    assert profile["location_kind"] == "npc-station"
    assert profile["base_station_tax_percent"] == pytest.approx(5.0)
    assert profile["adjusted_station_tax_percent"] == pytest.approx(5.0)
    assert profile["standing"] is None
    assert profile["standing_row"]["standing"] is None
    assert any("no ESI npc_corp or faction standing matched" in note for note in profile["notes"])


def test_reprocessing_location_profile_warns_for_upwell_manual_overrides(tmp_path):
    cache = ReprocessingCache(
        path=tmp_path / "eve_reprocessing.json",
        available=True,
        build_number=3374020,
        ores={},
        stations={},
    )
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1", "esi-skills.read_skills.v1", "esi-characters.read_standings.v1"),
        access_token="access-token",
        connected_at="2026-06-05T00:00:00Z",
        expires_at=9999999999,
    )

    profile = corp_market.build_reprocessing_location_profile(
        corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.test/flight/callback",
        ),
        session,
        cache=cache,
        location={"structure_id": 1020304050607},
        standings=[],
        selected_station_id=None,
        facility_yield_percent=None,
        station_tax_percent=None,
    )

    warning = " ".join(profile["notes"])
    assert profile["location_kind"] == "structure"
    assert profile["location_name"] == "Structure 1020304050607"
    assert profile["station_tax_percent"] == pytest.approx(0.0)
    assert "reprocessing rigs" in warning
    assert "facility tax" in warning
    assert "service settings" in warning
    assert "structure bonus" in warning
    assert "manual overrides" in warning


def test_build_flight_reprocessing_locations_payload_filters_and_ranks_standing_stations(monkeypatch, tmp_path):
    cache = ReprocessingCache(
        path=tmp_path / "eve_reprocessing.json",
        available=True,
        build_number=3374020,
        ores={
            1230: ReprocessingOre(
                type_id=1230,
                name="Veldspar",
                group_id=462,
                group_name="Veldspar",
                portion_size=100,
                volume_m3=0.1,
                specialization_skill_type_id=60377,
                specialization_skill_name="Simple Ore Processing",
                materials=(
                    ReprocessingMaterial(type_id=34, name="Tritanium", quantity=400, volume_m3=0.01),
                ),
            )
        },
        stations={
            60000004: ReprocessingStation(
                station_id=60000004,
                owner_id=1000002,
                owner_name="CBD Corporation",
                owner_faction_id=500001,
                solar_system_id=30002780,
                reprocessing_efficiency=0.5,
                reprocessing_tax_rate=0.05,
            ),
            60000007: ReprocessingStation(
                station_id=60000007,
                owner_id=1000003,
                owner_name="Expert Distribution",
                owner_faction_id=500001,
                solar_system_id=30002779,
                reprocessing_efficiency=0.5,
                reprocessing_tax_rate=0.05,
            ),
            60000010: ReprocessingStation(
                station_id=60000010,
                owner_id=1000004,
                owner_name="No Standing Corp",
                owner_faction_id=500002,
                solar_system_id=30002781,
                reprocessing_efficiency=0.5,
                reprocessing_tax_rate=0.05,
            ),
            60000013: ReprocessingStation(
                station_id=60000013,
                owner_id=1000005,
                owner_name="Boundary Standing Corp",
                owner_faction_id=500003,
                solar_system_id=30002782,
                reprocessing_efficiency=0.5,
                reprocessing_tax_rate=0.05,
            ),
            60000016: ReprocessingStation(
                station_id=60000016,
                owner_id=1000006,
                owner_name="Low Fee Corp",
                owner_faction_id=500004,
                solar_system_id=30002783,
                reprocessing_efficiency=0.45,
                reprocessing_tax_rate=0.01,
            ),
            60000019: ReprocessingStation(
                station_id=60000019,
                owner_id=1000007,
                owner_name="High Standing Corp",
                owner_faction_id=500005,
                solar_system_id=30002784,
                reprocessing_efficiency=0.4,
                reprocessing_tax_rate=0.05,
            ),
        },
    )
    route_cache = RouteGraphCache(
        path=tmp_path / "eve_route_graph.json",
        available=True,
        build_number=3374020,
        systems={
            30002779: RouteSystem(
                solar_system_id=30002779,
                name="Inaro",
                region_id=200,
                security_status=0.7,
            ),
            30002780: RouteSystem(
                solar_system_id=30002780,
                name="Airaken",
                region_id=200,
                security_status=0.5,
            ),
            30002781: RouteSystem(
                solar_system_id=30002781,
                name="Unlisted",
                region_id=200,
                security_status=0.6,
            ),
            30002782: RouteSystem(
                solar_system_id=30002782,
                name="Boundary",
                region_id=200,
                security_status=0.6,
            ),
            30002783: RouteSystem(
                solar_system_id=30002783,
                name="Lowfee",
                region_id=200,
                security_status=0.6,
            ),
            30002784: RouteSystem(
                solar_system_id=30002784,
                name="Highstand",
                region_id=200,
                security_status=0.6,
            ),
        },
        adjacency={},
    )
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(
            "esi-location.read_location.v1",
            "esi-skills.read_skills.v1",
            "esi-characters.read_standings.v1",
            "esi-clones.read_implants.v1",
        ),
        access_token="access-token",
        connected_at="2026-06-05T00:00:00Z",
        expires_at=9999999999,
    )
    monkeypatch.setattr(corp_market, "load_reprocessing_cache", lambda: cache)
    monkeypatch.setattr(corp_market, "load_route_graph_cache", lambda: route_cache)
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_skills",
        lambda config, session: {
            "skills": [
                {"skill_id": corp_market.REPROCESSING_SKILL_TYPE_ID, "active_skill_level": 5},
                {"skill_id": corp_market.REPROCESSING_EFFICIENCY_SKILL_TYPE_ID, "active_skill_level": 4},
                {"skill_id": 60377, "active_skill_level": 3},
            ]
        },
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_standings",
        lambda config, session: [
            {"from_id": 1000002, "from_type": "corporation", "standing": 0.0},
            {"from_id": 1000003, "from_type": "npc_corp", "standing": 5.0},
            {"from_id": 1000005, "from_type": "npc_corp", "standing": 1.5},
            {"from_id": 1000006, "from_type": "npc_corp", "standing": 2.0},
            {"from_id": 1000007, "from_type": "npc_corp", "standing": 8.0},
        ],
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_location",
        lambda config, session: {
            "solar_system_id": 30002780,
            "solar_system_name": "Airaken",
            "station_id": 60000004,
            "structure_id": None,
            "updated_at": "2026-06-05T00:00:00Z",
        },
    )
    monkeypatch.setattr(corp_market, "fetch_flight_implants", lambda config, session: [27174])
    monkeypatch.setattr(
        corp_market,
        "fetch_universe_station",
        lambda config, station_id: {"name": f"Station {station_id}", "owner": 1000002},
    )

    payload = build_flight_reprocessing_locations_payload(
        config=corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.test/flight/callback",
        ),
        session=session,
        ore_type_id=1230,
    )

    assert payload["current_location"]["location_id"] == 60000004
    assert payload["minimum_standing"] == 1.5
    assert payload["sort_mode"] == "net_yield"
    assert payload["sort_label"] == "best net yield"
    assert payload["station_count"] == 3
    assert payload["total_matching_stations"] == 3
    assert [station["station_id"] for station in payload["stations"]] == [60000007, 60000016, 60000019]
    assert {station["station_id"] for station in payload["stations"]}.isdisjoint({60000004, 60000010, 60000013})
    assert payload["stations"][0]["solar_system_name"] == "Inaro"
    assert payload["stations"][0]["processing_fee_percent"] == pytest.approx(1.25)
    assert payload["stations"][0]["standing_row"]["from_id"] == 1000003
    assert payload["stations"][0]["standing_row"]["from_type"] == "npc_corp"
    assert payload["stations"][0]["standing_row"]["standing"] == pytest.approx(5.0)
    assert "standing 5.00" in payload["stations"][0]["label"]
    assert "processing fee 1.25%" in payload["stations"][0]["label"]

    fee_payload = build_flight_reprocessing_locations_payload(
        config=corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.test/flight/callback",
        ),
        session=session,
        ore_type_id=1230,
        sort_mode="processing_fee",
    )

    assert fee_payload["sort_mode"] == "processing_fee"
    assert fee_payload["sort_label"] == "lowest processing fee"
    assert fee_payload["stations"][0]["station_id"] == 60000019
    assert fee_payload["stations"][0]["processing_fee_percent"] == pytest.approx(0.0)

    standing_payload = build_flight_reprocessing_locations_payload(
        config=corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.test/flight/callback",
        ),
        session=session,
        ore_type_id=1230,
        sort_mode="standing",
    )

    assert standing_payload["sort_mode"] == "standing"
    assert standing_payload["sort_label"] == "highest standing"
    assert standing_payload["stations"][0]["station_id"] == 60000019
    assert standing_payload["stations"][0]["standing"] == pytest.approx(8.0)


def test_nearby_systems_payload_uses_jump_range():
    route_cache = RouteGraphCache(
        path=Path("route.json"),
        available=True,
        build_number=3374020,
        systems={
            1: RouteSystem(solar_system_id=1, name="Start", security_status=0.9),
            2: RouteSystem(solar_system_id=2, name="One", security_status=0.8),
            3: RouteSystem(solar_system_id=3, name="Two", security_status=0.7),
            4: RouteSystem(solar_system_id=4, name="Too Far", security_status=0.6),
        },
        adjacency={1: (2,), 2: (1, 3), 3: (2, 4), 4: (3,)},
    )

    nearby = corp_market.build_nearby_systems_payload(
        current_solar_system_id=1,
        max_jumps=2,
        route_cache=route_cache,
    )

    assert nearby["available"] is True
    assert nearby["current_system_name"] == "Start"
    assert nearby["reachable_system_count"] == 3
    assert [system["name"] for system in nearby["systems"]] == ["Start", "One", "Two"]
    assert nearby["systems"][-1]["jumps"] == 2


def test_flight_status_includes_jump_aware_route(monkeypatch):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1",),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )
    route_cache = RouteGraphCache(
        path=Path("route.json"),
        available=True,
        build_number=3374020,
        systems={
            30000142: RouteSystem(solar_system_id=30000142, name="Jita", security_status=0.9),
            30000144: RouteSystem(solar_system_id=30000144, name="Perimeter", security_status=0.9),
        },
        adjacency={30000142: (30000144,), 30000144: (30000142,)},
    )

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_location",
        lambda config, session: {
            "solar_system_id": 30000142,
            "solar_system_name": "Jita",
            "updated_at": "2026-06-04T00:00:00Z",
        },
    )
    monkeypatch.setattr(corp_market, "load_route_graph_cache", lambda: route_cache)

    payload = build_flight_status_payload(
        config=corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="secret",
            callback_url="http://127.0.0.1:8770/flight/callback",
        ),
        session=session,
        callback_url="http://127.0.0.1:8770/flight/callback",
        max_jumps=5,
    )

    assert payload["nearby_systems"]["available"] is True
    assert payload["nearby_systems"]["max_jumps"] == 5
    assert payload["nearby_systems"]["reachable_system_count"] == 2
    assert payload["nearby_systems"]["systems"][1]["name"] == "Perimeter"


def test_build_flight_buyers_payload_scans_nearby_owned_blueprint_products(monkeypatch, tmp_path):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(
            "esi-location.read_location.v1",
            "esi-characters.read_blueprints.v1",
        ),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )
    route_cache = RouteGraphCache(
        path=tmp_path / "route.json",
        available=True,
        build_number=3374020,
        systems={
            1: RouteSystem(solar_system_id=1, name="Start", region_id=100, security_status=0.9),
            2: RouteSystem(solar_system_id=2, name="One Jump", region_id=100, security_status=0.8),
            3: RouteSystem(solar_system_id=3, name="Too Far", region_id=200, security_status=0.7),
        },
        adjacency={1: (2,), 2: (1, 3), 3: (2,)},
    )
    recipe_cache = IndustryRecipeCache(
        path=tmp_path / "recipes.json",
        available=True,
        build_number=3374020,
        recipes={
            681: IndustryRecipe(
                blueprint_type_id=681,
                blueprint_name="Hobgoblin I Blueprint",
                product_type_id=165,
                product_name="Hobgoblin I",
                product_quantity=1,
                materials=(IndustryMaterial(type_id=34, name="Tritanium", quantity=5000),),
            )
        },
    )
    calls = []

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_location",
        lambda config, session: {
            "solar_system_id": 1,
            "solar_system_name": "Start",
            "updated_at": "2026-06-04T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_blueprints",
        lambda config, session: [
            {
                "type_id": 681,
                "quantity": -1,
                "runs": -1,
                "material_efficiency": 10,
                "time_efficiency": 20,
            }
        ],
    )
    monkeypatch.setattr(corp_market, "load_route_graph_cache", lambda: route_cache)
    monkeypatch.setattr(corp_market, "load_industry_recipe_cache", lambda: recipe_cache)

    def fake_fetch_market_buy_orders(config, *, region_id, type_id):
        calls.append((region_id, type_id))
        return [
            {
                "order_id": 10,
                "is_buy_order": True,
                "system_id": 2,
                "location_id": 60008494,
                "price": 1200.5,
                "volume_remain": 300,
                "min_volume": 1,
            },
            {
                "order_id": 11,
                "is_buy_order": True,
                "system_id": 3,
                "location_id": 60003760,
                "price": 9999.0,
                "volume_remain": 999,
                "min_volume": 1,
            },
        ]

    monkeypatch.setattr(corp_market, "fetch_market_buy_orders", fake_fetch_market_buy_orders)

    progress_events = []
    payload = build_flight_buyers_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        max_jumps=1,
        progress=lambda event, event_payload: progress_events.append((event, event_payload)),
    )

    assert payload["ok"] is True
    assert calls == [(100, 165)]
    assert payload["buyers"]["scanned_products"] == 1
    assert payload["buyers"]["order_count"] == 1
    assert payload["buyers"]["products_with_buyers"] == 1
    product = payload["buyers"]["products"][0]
    assert product["product_name"] == "Hobgoblin I"
    assert product["best_order"]["system_name"] == "One Jump"
    assert product["best_order"]["price"] == 1200.5
    assert product["best_order"]["jumps"] == 1
    event_names = [event for event, _payload in progress_events]
    assert event_names[:3] == ["scan_start", "blueprints", "scan_scope"]
    assert "orders" in event_names
    assert "product_done" in event_names
    assert all(0 <= event_payload["percent"] <= 100 for _event, event_payload in progress_events if "percent" in event_payload)


def test_build_flight_profitability_payload_ranks_owned_blueprint_products(monkeypatch, tmp_path):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(
            "esi-location.read_location.v1",
            "esi-assets.read_assets.v1",
            "esi-characters.read_blueprints.v1",
            "esi-skills.read_skills.v1",
            "esi-characters.read_standings.v1",
        ),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )
    route_cache = RouteGraphCache(
        path=tmp_path / "route.json",
        available=True,
        build_number=3374020,
        systems={
            1: RouteSystem(solar_system_id=1, name="Start", region_id=100, security_status=0.9),
            2: RouteSystem(solar_system_id=2, name="One Jump", region_id=100, security_status=0.8),
            3: RouteSystem(solar_system_id=3, name="Too Far", region_id=100, security_status=0.7),
            30000142: RouteSystem(solar_system_id=30000142, name="Jita", region_id=200, security_status=0.9),
        },
        adjacency={1: (2,), 2: (1, 3), 3: (2,)},
    )
    recipe_cache = IndustryRecipeCache(
        path=tmp_path / "recipes.json",
        available=True,
        build_number=3374020,
        recipes={
            681: IndustryRecipe(
                blueprint_type_id=681,
                blueprint_name="Hobgoblin I Blueprint",
                product_type_id=165,
                product_name="Hobgoblin I",
                product_quantity=1,
                materials=(
                    IndustryMaterial(type_id=34, name="Tritanium", quantity=1000),
                    IndustryMaterial(type_id=35, name="Pyerite", quantity=500),
                ),
                manufacturing_time_seconds=600,
                max_production_limit=1500,
                skills=(IndustrySkill(type_id=3380, name="Industry", level=1),),
            )
        },
    )
    buy_calls = []
    sell_calls = []

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_location",
        lambda config, session: {
            "solar_system_id": 1,
            "solar_system_name": "Start",
            "updated_at": "2026-06-04T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_blueprints",
        lambda config, session: [
            {
                "type_id": 681,
                "quantity": -1,
                "runs": -1,
                "material_efficiency": 10,
                "time_efficiency": 20,
            }
        ],
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_assets",
        lambda config, session: [
            {"type_id": 34, "quantity": 1000, "location_id": 60008494},
            {"type_id": 35, "quantity": 100, "location_id": 60008494},
        ],
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_skills",
        lambda config, session: {
            "skills": [
                {
                    "skill_id": corp_market.ACCOUNTING_SKILL_TYPE_ID,
                    "active_skill_level": 5,
                    "trained_skill_level": 5,
                }
            ]
        },
    )
    monkeypatch.setattr(corp_market, "load_route_graph_cache", lambda: route_cache)
    monkeypatch.setattr(corp_market, "load_industry_recipe_cache", lambda: recipe_cache)

    def fake_fetch_market_buy_orders(config, *, region_id, type_id):
        buy_calls.append((region_id, type_id))
        if region_id == 200:
            jita_prices = {34: 1.5, 35: 4.0}
            price = jita_prices.get(type_id)
            if price is None:
                return []
            return [
                {
                    "order_id": type_id + 1000,
                    "is_buy_order": True,
                    "system_id": 30000142,
                    "location_id": 60003760,
                    "price": price,
                    "volume_remain": 100000,
                    "min_volume": 1,
                }
            ]
        return [
            {
                "order_id": 10,
                "is_buy_order": True,
                "system_id": 2,
                "location_id": 60008494,
                "price": 10000.0,
                "volume_remain": 300,
                "min_volume": 1,
            },
            {
                "order_id": 11,
                "is_buy_order": True,
                "system_id": 3,
                "location_id": 60003760,
                "price": 99999.0,
                "volume_remain": 999,
                "min_volume": 1,
            },
        ]

    def fake_fetch_market_sell_orders(config, *, region_id, type_id):
        sell_calls.append((region_id, type_id))
        prices = {34: 2.0, 35: 5.0}
        return [
            {
                "order_id": type_id,
                "is_buy_order": False,
                "system_id": 2,
                "location_id": 60008494,
                "price": prices[type_id],
                "volume_remain": 100000,
                "min_volume": 1,
            }
        ]

    monkeypatch.setattr(corp_market, "fetch_market_buy_orders", fake_fetch_market_buy_orders)
    monkeypatch.setattr(corp_market, "fetch_market_sell_orders", fake_fetch_market_sell_orders)

    payload = build_flight_profitability_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        max_jumps=1,
    )

    assert payload["ok"] is True
    assert buy_calls == [(100, 165), (200, 34), (200, 35)]
    assert sell_calls == [(100, 34), (100, 35)]
    profitability = payload["profitability"]
    assert profitability["ranked_products"] == 1
    assert profitability["profitable_products"] == 1
    assert profitability["buildable_now_products"] == 0
    assert profitability["decision_counts"] == {"source-missing": 1}
    assert profitability["sales_tax"]["accounting_level"] == 5
    assert profitability["sales_tax"]["rate"] == pytest.approx(0.03375)
    product = profitability["products"][0]
    assert product["product_name"] == "Hobgoblin I"
    assert product["decision"]["code"] == "source-missing"
    assert product["decision"]["label"] == "Buy Missing"
    assert product["best_buyer"]["system_name"] == "One Jump"
    assert product["product_revenue"] == 10000.0
    assert product["sales_tax_rate"] == pytest.approx(0.03375)
    assert product["sales_tax"] == pytest.approx(337.5)
    assert product["broker_fee"] == 0.0
    assert product["net_revenue"] == pytest.approx(9662.5)
    assert product["blueprint_material_efficiency"] == 10
    assert product["blueprint_time_efficiency"] == 20
    assert product["blueprint_kind"] == "Original"
    assert product["blueprint_runs"] is None
    assert product["base_time_seconds"] == 600
    assert product["adjusted_time_seconds"] == 480
    assert product["true_profit_per_hour"] == pytest.approx(42093.75)
    assert product["wallet_gain_per_hour"] == pytest.approx(59343.75)
    assert product["max_production_limit"] == 1500
    assert product["required_skills"][0]["name"] == "Industry"
    assert profitability["jita_material_order_count"] == 2
    assert profitability["jita_raw_system"]["name"] == "Jita"
    assert product["jita_raw_system_name"] == "Jita"
    assert product["jita_raw_material_value"] == pytest.approx(3150.0)
    assert product["jita_partial_raw_material_value"] == pytest.approx(3150.0)
    assert product["jita_raw_material_types"] == 2
    assert product["jita_partial_raw_material_types"] == 2
    assert product["jita_raw_required_material_types"] == 2
    assert product["materials"][0]["jita_raw_value"] == pytest.approx(1350.0)
    assert product["materials"][1]["jita_raw_value"] == pytest.approx(1800.0)
    assert product["replacement_cost"] == 4050.0
    assert product["replacement_profit"] == 5950.0
    assert product["replacement_margin_percent"] == 59.5
    assert product["taxed_replacement_profit"] == pytest.approx(5612.5)
    assert product["taxed_replacement_margin_percent"] == pytest.approx(56.125)
    assert product["missing_replacement_cost"] == 1750.0
    assert product["cash_profit"] == 8250.0
    assert product["cash_margin_percent"] == 82.5
    assert product["taxed_cash_profit"] == pytest.approx(7912.5)
    assert product["taxed_cash_margin_percent"] == pytest.approx(79.125)
    assert product["can_build_one_run"] is False
    assert product["missing_material_types"] == 1
    assert product["missing_materials"][0]["name"] == "Pyerite"
    assert product["missing_materials"][0]["base_required"] == 500
    assert product["missing_materials"][0]["required"] == 450
    assert product["missing_materials"][0]["missing"] == 350
    assert product["confidence"] == "strong"


def test_build_flight_acquisition_payload_flags_history_spike_as_possible_trap(monkeypatch, tmp_path):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Quartermaster",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(
            "esi-location.read_location.v1",
            "esi-skills.read_skills.v1",
        ),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )
    route_cache = RouteGraphCache(
        path=tmp_path / "route.json",
        available=True,
        build_number=3374020,
        systems={
            1: RouteSystem(solar_system_id=1, name="Start", region_id=100, security_status=0.9),
            2: RouteSystem(solar_system_id=2, name="One Jump", region_id=100, security_status=0.8),
            30000142: RouteSystem(solar_system_id=30000142, name="Jita", region_id=200, security_status=0.9),
        },
        adjacency={1: (2,), 2: (1,), 30000142: ()},
    )
    recipe_cache = IndustryRecipeCache(
        path=tmp_path / "recipes.json",
        available=True,
        build_number=3374020,
        recipes={
            681: IndustryRecipe(
                blueprint_type_id=681,
                blueprint_name="Hobgoblin I Blueprint",
                product_type_id=165,
                product_name="Hobgoblin I",
                product_quantity=1,
                materials=(IndustryMaterial(type_id=34, name="Tritanium", quantity=1000),),
                manufacturing_time_seconds=600,
                max_production_limit=1500,
                skills=(),
            )
        },
    )

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_location",
        lambda config, session: {
            "solar_system_id": 1,
            "solar_system_name": "Start",
            "updated_at": "2026-06-04T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_skills",
        lambda config, session: {
            "skills": [
                {
                    "skill_id": corp_market.ACCOUNTING_SKILL_TYPE_ID,
                    "active_skill_level": 5,
                    "trained_skill_level": 5,
                }
            ]
        },
    )
    monkeypatch.setattr(corp_market, "load_route_graph_cache", lambda: route_cache)
    monkeypatch.setattr(corp_market, "load_industry_recipe_cache", lambda: recipe_cache)
    monkeypatch.setattr(
        corp_market,
        "build_haul_route_plan",
        lambda **kwargs: {
            "path": [1, 30000142],
            "preference": "safer",
            "preference_label": "Safer",
            "source": "test-route",
            "warning": "",
            "avoid_recent_pod_kills": False,
            "recent_pod_kill_system_count": 0,
            "avoided_pod_kill_system_ids": [],
            "route_pod_kill_system_ids": [],
        },
    )

    def fake_fetch_market_buy_orders(config, *, region_id, type_id):
        if region_id == 100:
            return [
                {
                    "order_id": 10,
                    "is_buy_order": True,
                    "system_id": 2,
                    "location_id": 60008494,
                    "price": 50.0,
                    "volume_remain": 1000,
                    "min_volume": 1,
                }
            ]
        if region_id == 200:
            return [
                {
                    "order_id": 20,
                    "is_buy_order": True,
                    "system_id": 30000142,
                    "location_id": 60003760,
                    "price": 500.0,
                    "volume_remain": 10,
                    "min_volume": 1,
                }
            ]
        return []

    def fake_fetch_market_sell_orders(config, *, region_id, type_id):
        return [
            {
                "order_id": 30,
                "is_buy_order": False,
                "system_id": 2,
                "location_id": 60008494,
                "price": 80.0,
                "volume_remain": 1000,
                "min_volume": 1,
            }
        ]

    def fake_fetch_market_history(config, *, region_id, type_id):
        average = 80.0 if region_id == 100 else 100.0
        return [
            {
                "date": f"2026-05-{day:02d}",
                "average": average,
                "highest": average * 1.1,
                "lowest": average * 0.9,
                "order_count": 4,
                "volume": 1000,
            }
            for day in range(1, 31)
        ]

    monkeypatch.setattr(corp_market, "fetch_market_buy_orders", fake_fetch_market_buy_orders)
    monkeypatch.setattr(corp_market, "fetch_market_sell_orders", fake_fetch_market_sell_orders)
    monkeypatch.setattr(corp_market, "fetch_market_history", fake_fetch_market_history)

    payload = build_flight_acquisition_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        destination_name="Jita",
        budget_isk=1_000_000,
        pickup_jumps=1,
        min_margin_percent=10,
        broker_fee_percent=3,
        target_days=3,
    )

    assert payload["ok"] is True
    acquisition = payload["acquisition"]
    assert acquisition["opportunity_count"] == 1
    assert acquisition["possible_trap_count"] == 1
    opportunity = acquisition["opportunities"][0]
    assert opportunity["item_name"] == "Tritanium"
    assert opportunity["risk_level"] == "possible-trap"
    assert opportunity["decision"]["label"] == "Verify before posting"
    assert opportunity["range_recommendation"]["range"] == "station"
    assert opportunity["recommended_units"] == 10
    assert opportunity["suggested_bid"] == pytest.approx(68.0)
    assert opportunity["history_flags"][0]["label"] == "Possible trap: price spike"
    assert "possible trap" in acquisition["pricing_note"]


def test_build_flight_hauling_payload_ranks_route_corridor_opportunities(monkeypatch, tmp_path):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Hauler Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=(
            "esi-location.read_location.v1",
            "esi-skills.read_skills.v1",
        ),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )
    route_cache = RouteGraphCache(
        path=tmp_path / "route.json",
        available=True,
        build_number=3374020,
        systems={
            1: RouteSystem(solar_system_id=1, name="Start", region_id=100, security_status=0.9),
            2: RouteSystem(solar_system_id=2, name="Middle", region_id=100, security_status=0.8),
            3: RouteSystem(solar_system_id=3, name="Jita", region_id=200, security_status=0.9),
            4: RouteSystem(solar_system_id=4, name="Side Pickup", region_id=100, security_status=0.7),
            5: RouteSystem(solar_system_id=5, name="Too Far", region_id=100, security_status=0.6),
            6: RouteSystem(solar_system_id=6, name="Other Destination", region_id=200, security_status=0.9),
        },
        adjacency={
            1: (2,),
            2: (1, 3, 4),
            3: (2,),
            4: (2, 5),
            5: (4,),
            6: (),
        },
    )
    recipe_cache = IndustryRecipeCache(
        path=tmp_path / "recipes.json",
        available=True,
        build_number=3374020,
        recipes={
            681: IndustryRecipe(
                blueprint_type_id=681,
                blueprint_name="Hobgoblin I Blueprint",
                product_type_id=165,
                product_name="Hobgoblin I",
                product_quantity=1,
                materials=(
                    IndustryMaterial(type_id=34, name="Tritanium", quantity=1000, volume_m3=0.01),
                    IndustryMaterial(type_id=35, name="Pyerite", quantity=500, volume_m3=0.01),
                ),
            )
        },
    )
    sell_calls = []
    buy_calls = []

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_location",
        lambda config, session: {
            "solar_system_id": 1,
            "solar_system_name": "Start",
            "updated_at": "2026-06-04T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        corp_market,
        "fetch_flight_skills",
        lambda config, session: {
            "skills": [
                {
                    "skill_id": corp_market.ACCOUNTING_SKILL_TYPE_ID,
                    "active_skill_level": 5,
                    "trained_skill_level": 5,
                }
            ]
        },
    )
    monkeypatch.setattr(corp_market, "load_route_graph_cache", lambda: route_cache)
    monkeypatch.setattr(corp_market, "load_industry_recipe_cache", lambda: recipe_cache)

    def fake_fetch_market_sell_orders(config, *, region_id, type_id):
        sell_calls.append((region_id, type_id))
        if type_id == 34:
            return [
                {
                    "order_id": 20,
                    "is_buy_order": False,
                    "system_id": 4,
                    "location_id": 60008494,
                    "price": 2.0,
                    "volume_remain": 5000,
                    "min_volume": 1,
                },
                {
                    "order_id": 21,
                    "is_buy_order": False,
                    "system_id": 5,
                    "location_id": 60008495,
                    "price": 1.0,
                    "volume_remain": 9999,
                    "min_volume": 1,
                },
            ]
        return [
            {
                "order_id": 22,
                "is_buy_order": False,
                "system_id": 2,
                "location_id": 60008496,
                "price": 7.0,
                "volume_remain": 2000,
                "min_volume": 1,
            }
        ]

    def fake_fetch_market_buy_orders(config, *, region_id, type_id):
        buy_calls.append((region_id, type_id))
        if type_id == 34:
            return [
                {
                    "order_id": 30,
                    "is_buy_order": True,
                    "system_id": 3,
                    "location_id": 60003760,
                    "price": 8.0,
                    "volume_remain": 2000,
                    "min_volume": 1,
                },
                {
                    "order_id": 31,
                    "is_buy_order": True,
                    "system_id": 6,
                    "location_id": 60003761,
                    "price": 99.0,
                    "volume_remain": 9999,
                    "min_volume": 1,
                },
            ]
        return [
            {
                "order_id": 32,
                "is_buy_order": True,
                "system_id": 3,
                "location_id": 60003760,
                "price": 7.1,
                "volume_remain": 2000,
                "min_volume": 1,
            }
        ]

    monkeypatch.setattr(corp_market, "fetch_market_sell_orders", fake_fetch_market_sell_orders)
    monkeypatch.setattr(corp_market, "fetch_market_buy_orders", fake_fetch_market_buy_orders)
    route_calls = []

    def fake_fetch_esi_route_path(
        config,
        *,
        origin_solar_system_id,
        destination_solar_system_id,
        route_preference,
        avoid_system_ids=(),
    ):
        route_calls.append(
            (
                origin_solar_system_id,
                destination_solar_system_id,
                route_preference,
                tuple(sorted(avoid_system_ids)),
            )
        )
        if origin_solar_system_id == 1 and destination_solar_system_id == 3:
            return [1, 2, 3]
        if origin_solar_system_id == 2 and destination_solar_system_id == 3:
            return [2, 3]
        return []

    monkeypatch.setattr(corp_market, "fetch_esi_route_path", fake_fetch_esi_route_path)
    monkeypatch.setattr(corp_market, "fetch_recent_pod_kill_system_ids", lambda config: ())

    progress_events = []
    payload = build_flight_hauling_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        destination_name="Jita",
        detour_jumps=1,
        cargo_capacity_m3=10,
        min_detour_margin_percent=10,
        progress=lambda event, progress_payload: progress_events.append((event, progress_payload)),
    )

    assert payload["ok"] is True
    assert payload["route"]["origin"]["name"] == "Start"
    assert payload["route"]["origin_query"] == ""
    assert payload["route"]["origin_source"] == "esi-location"
    assert payload["route"]["destination"]["name"] == "Jita"
    assert payload["route"]["route_jumps"] == 2
    assert payload["route"]["route_preference"] == "safer"
    assert payload["route"]["avoid_recent_pod_kills"] is False
    assert route_calls[0] == (1, 3, "safer", ())
    assert sorted(sell_calls) == [(100, 34), (100, 35)]
    assert sorted(buy_calls) == [(200, 34), (200, 35)]
    hauling = payload["hauling"]
    assert hauling["profitable_opportunities"] == 1
    assert hauling["sales_tax"]["accounting_level"] == 5
    assert hauling["min_detour_margin_percent"] == 10
    assert hauling["detour_margin_rejected_count"] == 0
    opportunity = hauling["opportunities"][0]
    assert opportunity["item_name"] == "Tritanium"
    assert opportunity["units"] == 1000
    assert opportunity["cargo_limited"] is True
    assert opportunity["pickup_order"]["system_name"] == "Side Pickup"
    assert opportunity["destination_order"]["system_name"] == "Jita"
    assert opportunity["pickup_detour_jumps"] == 1
    assert opportunity["extra_route_jumps"] == 2
    assert opportunity["gross_spread_per_unit"] == 6.0
    assert opportunity["net_profit_per_unit"] == pytest.approx(5.73)
    assert opportunity["net_profit"] == pytest.approx(5730.0)
    event_names = [event for event, _payload in progress_events]
    assert "scan_start" in event_names
    assert "route_step" in event_names
    assert "nearby_system" in event_names
    assert "orders" in event_names
    assert any(payload["message"].startswith("Scanning route stop") for event, payload in progress_events if event == "route_step")
    assert any(payload["message"].startswith("Checking nearby") for event, payload in progress_events if event == "nearby_system")

    sell_calls.clear()
    buy_calls.clear()
    route_calls.clear()
    override_payload = build_flight_hauling_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        origin_name="Middle",
        destination_name="Jita",
        detour_jumps=1,
        cargo_capacity_m3=10,
        min_detour_margin_percent=10,
    )

    assert override_payload["location"]["solar_system_name"] == "Start"
    assert override_payload["route"]["origin"]["name"] == "Middle"
    assert override_payload["route"]["origin_query"] == "Middle"
    assert override_payload["route"]["origin_source"] == "manual"
    assert override_payload["route"]["route_jumps"] == 1
    assert route_calls[0] == (2, 3, "safer", ())

    with pytest.raises(CorpMarketError, match="Starting system 'Missing'"):
        build_flight_hauling_payload(
            config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
            session=session,
            origin_name="Missing",
            destination_name="Jita",
        )

    filtered = build_flight_hauling_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        destination_name="Jita",
        detour_jumps=1,
        cargo_capacity_m3=10,
        min_detour_margin_percent=500,
    )

    assert filtered["hauling"]["profitable_opportunities"] == 0
    assert filtered["hauling"]["detour_margin_rejected_count"] == 1

    def fake_build_market_group_targets(config, group_ids):
        assert tuple(group_ids) == (4,)
        return (
            [
                {
                    "type_id": 34,
                    "name": "Tritanium",
                    "recipe_count": 0,
                    "volume_m3": 0.01,
                    "source_label": "Ships",
                }
            ],
            {
                "source": "test-market-groups",
                "selected_market_group_ids": [4],
                "selected_market_groups": [{"market_group_id": 4, "name": "Ships"}],
                "selected_market_group_count": 1,
                "market_group_item_types": 1,
            },
        )

    monkeypatch.setattr(corp_market, "build_market_group_targets", fake_build_market_group_targets)
    sell_calls.clear()
    buy_calls.clear()

    category_only = build_flight_hauling_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        destination_name="Jita",
        detour_jumps=1,
        cargo_capacity_m3=10,
        min_detour_margin_percent=10,
        include_common_materials=False,
        market_group_ids=(4,),
    )

    assert sorted(sell_calls) == [(100, 34)]
    assert sorted(buy_calls) == [(200, 34)]
    assert category_only["hauling"]["item_scope"]["include_common_materials"] is False
    assert category_only["hauling"]["item_scope"]["selected_market_group_ids"] == [4]
    assert category_only["hauling"]["scanned_item_types"] == 1
    assert category_only["hauling"]["opportunities"][0]["item_name"] == "Tritanium"


def test_fetch_market_buy_orders_uses_public_market_endpoint(monkeypatch):
    corp_market.clear_market_order_cache()
    requests = []

    class FakeResponse:
        headers = {"X-Pages": "1"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps([{"order_id": 10, "is_buy_order": True}]).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(corp_market, "urlopen", fake_urlopen)

    orders = corp_market.fetch_market_buy_orders(
        corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        region_id=10000002,
        type_id=165,
    )
    sell_orders = corp_market.fetch_market_sell_orders(
        corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        region_id=10000002,
        type_id=34,
    )

    assert orders == [{"order_id": 10, "is_buy_order": True}]
    assert sell_orders == [{"order_id": 10, "is_buy_order": True}]
    url = requests[0].full_url
    assert url.startswith("https://esi.test/latest/markets/10000002/orders/?")
    assert "order_type=buy" in url
    assert "type_id=165" in url
    assert "page=1" in url
    sell_url = requests[1].full_url
    assert sell_url.startswith("https://esi.test/latest/markets/10000002/orders/?")
    assert "order_type=sell" in sell_url
    assert "type_id=34" in sell_url
    assert "page=1" in sell_url
    assert requests[0].headers["Accept"] == "application/json"
    assert "Authorization" not in requests[0].headers
    assert "Authorization" not in requests[1].headers
    corp_market.clear_market_order_cache()


def test_fetch_market_prices_uses_public_estimate_endpoint_and_cache(monkeypatch):
    corp_market.clear_market_price_cache()
    calls = []

    def fake_get_json(url, *, timeout_seconds, headers):
        calls.append((url, timeout_seconds, dict(headers)))
        return [
            {"type_id": 34, "average_price": 5.5, "adjusted_price": 4.4},
            {"type_id": 1230, "adjusted_price": 10.0},
            {"type_id": None, "average_price": 99.0},
        ]

    monkeypatch.setattr(corp_market, "get_json", fake_get_json)

    config = corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest")
    first = corp_market.fetch_market_prices(config)
    first[34]["average_price"] = 999.0
    second = corp_market.fetch_market_prices(config)

    assert len(calls) == 1
    assert calls[0][0] == "https://esi.test/latest/markets/prices/?datasource=tranquility"
    assert calls[0][1] == 45.0
    assert calls[0][2]["User-Agent"] == "EveVoicePilot-FlightAttendant/0.1"
    assert "Authorization" not in calls[0][2]
    assert second[34]["average_price"] == pytest.approx(5.5)
    assert second[34]["adjusted_price"] == pytest.approx(4.4)
    assert second[1230]["average_price"] is None
    assert second[1230]["adjusted_price"] == pytest.approx(10.0)
    assert None not in second
    corp_market.clear_market_price_cache()


def test_fetch_market_orders_reuses_local_cache(monkeypatch):
    corp_market.clear_market_order_cache()
    requests = []

    class FakeResponse:
        headers = {"X-Pages": "1"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps([{"order_id": 10, "is_buy_order": True}]).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(corp_market, "urlopen", fake_urlopen)

    first = corp_market.fetch_market_buy_orders(
        corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        region_id=10000002,
        type_id=165,
    )
    first[0]["order_id"] = 999
    second = corp_market.fetch_market_buy_orders(
        corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        region_id=10000002,
        type_id=165,
    )

    assert len(requests) == 1
    assert second == [{"order_id": 10, "is_buy_order": True}]
    assert corp_market.market_order_cache_status()["ttl_seconds"] == 300
    corp_market.clear_market_order_cache()


def test_fetch_market_history_uses_public_history_endpoint_and_cache(monkeypatch):
    corp_market.clear_market_history_cache()
    requests = []

    class FakeResponse:
        headers = {"Expires": "Fri, 05 Jun 2099 11:05:00 GMT"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                [
                    {
                        "date": "2026-06-01",
                        "average": 100.0,
                        "highest": 120.0,
                        "lowest": 90.0,
                        "order_count": 5,
                        "volume": 500,
                    }
                ]
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(corp_market, "urlopen", fake_urlopen)

    first = corp_market.fetch_market_history(
        corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        region_id=10000002,
        type_id=34,
    )
    first[0]["average"] = 999
    second = corp_market.fetch_market_history(
        corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        region_id=10000002,
        type_id=34,
    )

    assert len(requests) == 1
    assert second[0]["average"] == 100.0
    url = requests[0].full_url
    assert url.startswith("https://esi.test/latest/markets/10000002/history/?")
    assert "type_id=34" in url
    assert requests[0].headers["Accept"] == "application/json"
    assert "Authorization" not in requests[0].headers
    assert corp_market.market_history_cache_status()["entries"] == 1
    corp_market.clear_market_history_cache()


def test_flight_industry_payload_requires_blueprint_and_asset_scopes(monkeypatch):
    session = FlightEsiSession(
        character_id=123456789,
        character_name="Industry Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1",),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )

    with pytest.raises(CorpMarketError, match="esi-assets.read_assets.v1"):
        build_flight_industry_payload(
            config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
            session=session,
        )


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
            "fit_image_url": "https://cdn.discordapp.com/attachments/123/456/hawk.png",
        }
    )

    draft = build_mail_draft(listing, actor="Builder Example")

    assert "Fit note:\n[Hawk, Hawkaw T0 blitz dark abyss]" in draft.body
    assert "Fit image: https://cdn.discordapp.com/attachments/123/456/hawk.png" in draft.body
    assert "Small Bay Loading Accelerator II\n\nScourge Rage Rocket x4772" in draft.body
    assert "Tranquil Dark Filament x97" in draft.body


def test_invalid_fit_image_url_is_rejected(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")

    with pytest.raises(ValueError, match="full http or https URL"):
        store.create_listing(
            {
                "listing_type": "sell",
                "item_name": "Hawk screenshot",
                "quantity": 1,
                "location": "Jita",
                "owner": "Seller Example",
                "fit_image_url": "not-a-url",
            }
        )


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
    assert payload["embeds"][0]["fields"][0] == {"name": "Status", "value": "Open", "inline": True}
    assert payload["embeds"][0]["fields"][1] == {"name": "Category", "value": "Ships", "inline": True}
    assert payload["embeds"][0]["fields"][3]["value"] == format_isk(1_000_000)
    assert payload["embeds"][0]["fields"][6]["name"] == "Seller"
    assert "thread_name" not in payload


def test_discord_payload_marks_reserved_listing_status(tmp_path):
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
    reserved = store.reserve_listing(listing.listing_id, reserved_by="Buyer Example", hours=6)

    payload = build_discord_webhook_payload(reserved, public_base_url="http://market.test")
    embed = payload["embeds"][0]

    assert embed["title"] == "RESERVED - WTS Venture x1"
    assert embed["color"] == 0xF0BA57
    assert embed["fields"][0]["name"] == "Status"
    assert "Reserved by Buyer Example" in embed["fields"][0]["value"]
    assert "Until " in embed["fields"][0]["value"]


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
            "fit_image_url": "https://cdn.discordapp.com/attachments/123/456/hawk.png",
        }
    )

    payload = build_discord_webhook_payload(listing, public_base_url="http://market.test")
    embed = payload["embeds"][0]

    assert embed["description"] == "Fit note detected. Open the listing for the full copy/paste block."
    assert embed["image"] == {"url": "https://cdn.discordapp.com/attachments/123/456/hawk.png"}
    fit_field = next(field for field in embed["fields"] if field["name"] == "Fit Note")
    image_field = next(field for field in embed["fields"] if field["name"] == "Fit Image")
    assert "Hawk - Hawkaw T0 blitz dark abyss" in fit_field["value"]
    assert "13 fitted lines, 1 empty slot; 6 cargo stacks" in fit_field["value"]
    assert "Scourge Rage Rocket x4772" in fit_field["value"]
    assert "Rocket Launcher II\nRocket Launcher II" not in fit_field["value"]
    assert image_field["value"] == "[Open screenshot](https://cdn.discordapp.com/attachments/123/456/hawk.png)"


def test_offer_page_has_copy_fit_block_and_screenshot_link(tmp_path):
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
            "fit_image_url": "https://cdn.discordapp.com/attachments/123/456/hawk.png",
        }
    )

    page = render_offer_page(listing, build_mail_draft(listing))

    assert "Fitting Block" in page
    assert "Copy Fit" in page
    assert "Fit Screenshot" in page
    assert "https://cdn.discordapp.com/attachments/123/456/hawk.png" in page
    assert "[Hawk, Hawkaw T0 blitz dark abyss]" in page


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


class FakeDiscordResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_post_discord_webhook_requests_message_response(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        return FakeDiscordResponse(
            {
                "id": "123456789012345678",
                "channel_id": "223456789012345678",
            }
        )

    monkeypatch.setattr(corp_market, "urlopen", fake_urlopen)

    result = post_discord_webhook(
        "https://discord.com/api/webhooks/123456789012345678/token",
        {"content": "test"},
        timeout_seconds=3,
    )

    assert captured == {
        "url": "https://discord.com/api/webhooks/123456789012345678/token?wait=true",
        "method": "POST",
        "timeout": 3,
    }
    assert result.message_id == "123456789012345678"
    assert result.channel_id == "223456789012345678"


def test_edit_discord_webhook_message_patches_message_in_thread(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeDiscordResponse(
            {
                "id": "123456789012345678",
                "channel_id": "223456789012345678",
            }
        )

    monkeypatch.setattr(corp_market, "urlopen", fake_urlopen)

    result = edit_discord_webhook_message(
        "https://discord.com/api/webhooks/123456789012345678/token",
        "123456789012345678",
        {
            "content": "updated",
            "embeds": [{"title": "updated"}],
            "allowed_mentions": {"parse": []},
            "thread_name": "do not send on edit",
            "applied_tags": ["999"],
        },
        timeout_seconds=3,
        thread_id="223456789012345678",
    )

    assert captured["url"] == (
        "https://discord.com/api/webhooks/123456789012345678/token/messages/"
        "123456789012345678?thread_id=223456789012345678"
    )
    assert captured["method"] == "PATCH"
    assert captured["body"] == {
        "content": "updated",
        "embeds": [{"title": "updated"}],
        "allowed_mentions": {"parse": []},
    }
    assert result.message_id == "123456789012345678"
