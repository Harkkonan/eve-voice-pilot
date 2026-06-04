import json
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
    MarketStore,
    RouteGraphCache,
    RouteSystem,
    build_discord_webhook_payload,
    build_flight_buyers_payload,
    build_flight_industry_payload,
    build_flight_status_payload,
    build_mail_draft,
    clean_multiline,
    edit_discord_webhook_message,
    fetch_flight_location,
    format_isk,
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


def test_flight_esi_session_store_keeps_access_token_in_memory():
    pilot = corp_market.VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        alliance_id=None,
        alliance_name="",
        scopes=("esi-location.read_location.v1",),
    )
    store = FlightEsiSessionStore()

    session_id = store.create(pilot, access_token="access-token", expires_in=600)
    session = store.get(session_id)

    assert session is not None
    assert session.character_name == "Scout Pilot"
    assert session.access_token == "access-token"
    assert session.expires_in_seconds > 0


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
        ),
        access_token="access-token",
        connected_at="2026-06-04T00:00:00Z",
        expires_at=9999999999,
    )

    monkeypatch.setattr(
        corp_market,
        "fetch_flight_blueprints",
        lambda config, session: [
            {"type_id": 681, "quantity": -1},
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
                        "materials": [{"type_id": 34, "name": "Tritanium", "quantity": 5000}],
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
    assert cache.recipes[681].materials[0].name == "Tritanium"


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
    monkeypatch.setattr(corp_market, "fetch_flight_blueprints", lambda config, session: [{"type_id": 681, "quantity": -1}])
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

    payload = build_flight_buyers_payload(
        config=corp_market.EveSsoConfig(esi_base_url="https://esi.test/latest"),
        session=session,
        max_jumps=1,
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


def test_fetch_market_buy_orders_uses_public_market_endpoint(monkeypatch):
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

    assert orders == [{"order_id": 10, "is_buy_order": True}]
    url = requests[0].full_url
    assert url.startswith("https://esi.test/latest/markets/10000002/orders/?")
    assert "order_type=buy" in url
    assert "type_id=165" in url
    assert "page=1" in url
    assert requests[0].headers["Accept"] == "application/json"
    assert "Authorization" not in requests[0].headers


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
