import json
import http.client
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import eve_voice_pilot.corp_market as corp_market
from eve_voice_pilot.corp_market import MarketStore, build_discord_webhook_payload, render_dashboard


WEBHOOK_URL = "https://discord.com/api/webhooks/111111111111111111/test-token-value"
DEFAULT_TAG_ID = "222222222222222222"
WTS_TAG_ID = "333333333333333333"
MINERALS_TAG_ID = "444444444444444444"


def _start_public_discord_post_server(tmp_path):
    store = MarketStore(tmp_path / "market.sqlite3")
    session_store = corp_market.FlightEsiSessionStore()
    pilot = corp_market.VerifiedPilot(
        character_id=12345,
        character_name="Dandin Ridderston",
        corporation_id=98811080,
        corporation_name="Test Corp",
        membership_ok=True,
    )
    session_id = session_store.create(pilot, access_token="test-access-token")
    server = corp_market.build_http_server(
        "127.0.0.1",
        0,
        store,
        public_base_url="https://market.example.test",
        public_hosting_mode=True,
        sso_config=corp_market.EveSsoConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://market.example.test/flight/callback",
            allowed_corporation_ids=(98811080,),
        ),
        flight_session_store=session_store,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, session_id


def _post_json(server, path, payload, *, session_id=""):
    host, port = server.server_address
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Cookie"] = f"{corp_market.FLIGHT_SESSION_COOKIE_NAME}={session_id}"
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("POST", path, body=json.dumps(payload), headers=headers)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
    finally:
        connection.close()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = {}
    return response.status, data, body


def test_discord_post_settings_round_trip_redacts_webhook_from_response(tmp_path):
    settings_path = tmp_path / "corp_discord_post_settings.json"
    settings = corp_market.clean_discord_post_settings_payload(
        {
            "webhook_url": WEBHOOK_URL,
            "destination_label": "Corp buy-or-sell forum",
            "sender_name": "Quartermaster",
            "public_base_url": "https://market.example.test",
            "forum_posts": True,
            "forum_tag_ids": [DEFAULT_TAG_ID],
            "forum_tag_map": f"wts:{WTS_TAG_ID},minerals:{MINERALS_TAG_ID}",
        }
    )

    saved = corp_market.save_discord_post_settings(settings, settings_path)
    loaded = corp_market.load_discord_post_settings(settings_path)
    response = corp_market.build_discord_post_settings_response(
        loaded,
        effective_settings=loaded,
        settings_path=settings_path,
    )
    response_text = json.dumps(response)

    assert saved.updated_at
    assert loaded.webhook_url == WEBHOOK_URL
    assert loaded.destination_label == "Corp buy-or-sell forum"
    assert loaded.sender_name == "Quartermaster"
    assert loaded.public_base_url == "https://market.example.test"
    assert loaded.forum_posts is True
    assert loaded.forum_tag_ids == (DEFAULT_TAG_ID,)
    assert loaded.forum_tag_map == {"minerals": (MINERALS_TAG_ID,), "wts": (WTS_TAG_ID,)}
    assert response["webhook_configured"] is True
    assert response["webhook_url_preview"] == "https://discord.com/api/webhooks/111111111111111111/..."
    assert response["safety"]["webhook_url_stored_locally"] is True
    assert "test-token-value" not in response_text
    assert "webhook_url" not in response["settings"]


def test_discord_post_settings_partial_update_preserves_existing_values():
    existing = corp_market.clean_discord_post_settings_payload(
        {
            "webhook_url": WEBHOOK_URL,
            "public_base_url": "https://market.example.test",
            "forum_posts": True,
            "forum_tag_ids": [DEFAULT_TAG_ID],
        }
    )

    updated = corp_market.clean_discord_post_settings_payload(
        {"sender_name": "Market Desk"},
        existing=existing,
    )

    assert updated.webhook_url == WEBHOOK_URL
    assert updated.public_base_url == "https://market.example.test"
    assert updated.forum_posts is True
    assert updated.forum_tag_ids == (DEFAULT_TAG_ID,)
    assert updated.sender_name == "Market Desk"


def test_direct_discord_post_payload_uses_forum_tags_and_blocks_mentions():
    settings = corp_market.DiscordPostSettings(
        destination_label="Corp buy-or-sell forum",
        sender_name="Market Desk",
        forum_posts=True,
        forum_tag_ids=(DEFAULT_TAG_ID,),
        forum_tag_map={
            "wts": (WTS_TAG_ID,),
            "minerals": (MINERALS_TAG_ID,),
        },
    )
    post = corp_market.clean_direct_discord_post_payload(
        {
            "post_type": "wts",
            "category": "minerals",
            "item_name": "Tritanium @here",
            "quantity": "1,000,000",
            "price_text": "95% Jita Buy",
            "location": "Amarr",
            "contact": "Dandin Ridderston",
            "details": "@everyone Janice appraisal available; contract manually in EVE.",
        }
    )

    payload = corp_market.build_direct_discord_post_payload(post, settings)
    payload_text = json.dumps(payload)

    assert payload["username"] == "Market Desk"
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["thread_name"] == "WTS Tritanium @ here x1,000,000 at Amarr | 95% Jita Buy"
    assert payload["applied_tags"] == [DEFAULT_TAG_ID, WTS_TAG_ID, MINERALS_TAG_ID]
    assert "@here" not in payload_text
    assert "@everyone" not in payload_text
    assert "@ here" in payload_text
    assert "@ everyone" in payload_text
    assert "Verify terms in EVE" in payload_text


def test_direct_discord_market_order_post_stays_manual():
    settings = corp_market.DiscordPostSettings(destination_label="Corp orders", sender_name="Market Desk")
    post = corp_market.clean_direct_discord_post_payload(
        {
            "post_type": "market_order",
            "category": "minerals",
            "item_name": "Mexallon",
            "quantity": "500,000",
            "price_text": "Regional sell order",
            "location": "Amarr",
            "details": "Post the Discord notice first, then place or verify the market order manually in EVE.",
        }
    )

    payload = corp_market.build_direct_discord_post_payload(post, settings)
    payload_text = json.dumps(payload)
    fields = {field["name"]: field["value"] for field in payload["embeds"][0]["fields"]}

    assert fields["Type"] == "Market Order"
    assert payload["allowed_mentions"] == {"parse": []}
    assert "Market Order Mexallon x500,000 at Amarr | Regional sell order" in payload["content"]
    assert "market order" in payload_text
    assert "manually" in payload_text


def test_direct_discord_post_preview_uses_read_access_in_public_mode(tmp_path):
    server, thread, session_id = _start_public_discord_post_server(tmp_path)
    try:
        status, data, body = _post_json(
            server,
            "/api/discord-post/direct",
            {
                "send": False,
                "post": {
                    "post_type": "wtb",
                    "category": "minerals",
                    "item_name": "Isogen",
                    "quantity": "1,000,000",
                    "price_text": "290 ISK per unit",
                    "location": "Dihra 24",
                    "details": "Contract manually in EVE.",
                },
            },
            session_id=session_id,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 200, body
    assert data["ok"] is True
    assert data["sent_to_discord"] is False
    assert data["preview_payload"]["embeds"][0]["title"] == "WTB Isogen x1,000,000 at Dihra 24 | 290 ISK per unit"


def test_direct_discord_post_send_still_requires_write_access_in_public_mode(tmp_path):
    server, thread, session_id = _start_public_discord_post_server(tmp_path)
    try:
        status, data, body = _post_json(
            server,
            "/api/discord-post/direct",
            {
                "send": True,
                "settings": {"webhook_url": WEBHOOK_URL},
                "post": {
                    "post_type": "wtb",
                    "category": "minerals",
                    "item_name": "Isogen",
                    "quantity": "1,000,000",
                    "price_text": "290 ISK per unit",
                },
            },
            session_id=session_id,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 403, body
    assert data["ok"] is False
    assert "--trusted-members-can-write-market" in data["error"]


def test_discord_market_listing_payload_accepts_sender_name(tmp_path):
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

    payload = build_discord_webhook_payload(
        listing,
        public_base_url="http://market.test",
        sender_name="Corp Market Concierge",
    )

    assert payload["username"] == "Corp Market Concierge"
    assert payload["allowed_mentions"] == {"parse": []}


def test_dashboard_includes_discord_market_posting_controls():
    page = render_dashboard()

    assert "id=\"discord-post-form\"" in page
    assert "id=\"discord-post-webhook-url\"" in page
    assert "id=\"discord-post-forum-posts\"" in page
    assert "id=\"discord-post-forum-tag-map\"" in page
    assert "id=\"discord-post-tag-chips\"" in page
    assert "id=\"direct-discord-post-form\"" in page
    assert "id=\"direct-discord-visual-preview\"" in page
    assert "id=\"direct-discord-send\"" in page
    assert "data-direct-discord-type=\"market_order\"" in page
    assert "Advanced Intel Bot Routes" in page
    assert "Research Pattern Checklist" in page
    assert "/api/discord-post/settings" in page
    assert "/api/discord-post/direct" in page
    assert "Create forum/media-channel post" in page
