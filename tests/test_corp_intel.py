import base64
import http.client
import json
from pathlib import Path
import sys
import threading
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import eve_voice_pilot.corp_intel as corp_intel
from eve_voice_pilot.corp_intel import (
    ChannelFilter,
    ChatMessage,
    EveSsoConfig,
    EventDatabase,
    IntelWatchlist,
    IntelEvent,
    IntelEventStore,
    IntelParser,
    PilotRegistry,
    SystemMatcher,
    VerifiedPilot,
    WatchlistStore,
    build_sso_authorization_url,
    dashboard_access_status,
    decode_eve_access_token,
    discover_chat_log_files,
    eve_timestamp_to_iso,
    fetch_remote_watchlist,
    hash_agent_token,
    host_is_loopback_bind,
    ingest_payload,
    membership_allowed,
    parse_channel_name_from_text,
    parse_chat_line,
    parse_listener_name_from_text,
    request_has_watchlist_read_token,
    verify_sso_character,
)


def make_unsigned_jwt(payload: dict) -> str:
    def encode(part: dict) -> str:
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(payload)}.signature"


class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJwkClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, _token: str) -> FakeSigningKey:
        return FakeSigningKey(self.key)


def make_signed_jwt(payload: dict, *, private_key=None) -> tuple[str, object]:
    private_key = private_key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key"})
    return token, private_key.public_key()


def test_parse_chat_line_reads_eve_timestamp_speaker_and_message():
    message = parse_chat_line(
        "[ 2026.06.03 02:10:11 ] Alice Example > hostile in Tama on gate",
        channel="Corp",
        log_path="corp.txt",
    )
    assert message is not None
    assert message.timestamp == "2026.06.03 02:10:11"
    assert message.speaker == "Alice Example"
    assert message.text == "hostile in Tama on gate"
    assert message.channel == "Corp"
    assert message.log_path == "corp.txt"


def test_parse_chat_line_tolerates_repeated_utf16_bom():
    message = parse_chat_line(
        "\ufeff[ 2026.06.03 02:10:11 ] Alice Example > hostile in Tama on gate",
        channel="Corp",
        log_path="corp.txt",
    )
    assert message is not None
    assert message.timestamp == "2026.06.03 02:10:11"
    assert message.speaker == "Alice Example"
    assert message.text == "hostile in Tama on gate"


def test_parse_channel_name_from_header_text():
    text = """
------------------------------------------------------------
  Channel ID: 123
  Channel Name: Fleet
  Listener: Alice Example
------------------------------------------------------------
"""
    assert parse_channel_name_from_text(text) == "Fleet"


def test_parse_listener_name_from_header_text():
    text = """
------------------------------------------------------------
  Channel ID: 123
  Channel Name: Fleet
  Listener: Alice Example
------------------------------------------------------------
"""
    assert parse_listener_name_from_text(text) == "Alice Example"


def test_channel_filter_allows_exact_and_wildcard_names():
    channel_filter = ChannelFilter(["Corp", "*Intel*"])
    assert channel_filter.allows("Corp")
    assert channel_filter.allows("Standing Intel")
    assert not channel_filter.allows("Private Chat")


def test_host_is_loopback_bind_rejects_lan_all_interfaces():
    assert host_is_loopback_bind("")
    assert host_is_loopback_bind("127.0.0.1")
    assert host_is_loopback_bind("localhost")
    assert host_is_loopback_bind("::1")
    assert not host_is_loopback_bind("0.0.0.0")
    assert not host_is_loopback_bind("192.168.1.50")


def test_discover_chat_log_files_can_filter_by_listener(tmp_path):
    dandin_log = tmp_path / "Local_20260606_200141_1.txt"
    dandin_log.write_text(
        """
------------------------------------------------------------
  Channel ID: local
  Channel Name: Local
  Listener: Dandin Ridderston
------------------------------------------------------------
""".lstrip(),
        encoding="utf-8",
    )
    other_log = tmp_path / "Local_20260606_200141_2.txt"
    other_log.write_text(
        """
------------------------------------------------------------
  Channel ID: local
  Channel Name: Local
  Listener: Other Pilot
------------------------------------------------------------
""".lstrip(),
        encoding="utf-8",
    )
    states = {}
    log_lines = []

    discover_chat_log_files(
        tmp_path,
        states,
        ChannelFilter(("Local",)),
        read_existing=True,
        listener_filter=("Dandin Ridderston",),
        log=log_lines.append,
    )

    assert list(states) == [dandin_log]
    assert states[dandin_log].listener == "Dandin Ridderston"
    assert log_lines == ["Sharing channel 'Local' for Dandin Ridderston from Local_20260606_200141_1.txt"]


def test_build_sso_authorization_url_uses_state_callback_and_scopes():
    config = EveSsoConfig(
        client_id="client-123",
        client_secret="secret",
        callback_url="http://127.0.0.1:8765/auth/callback",
        scopes=("esi-location.read_location.v1",),
    )
    url = build_sso_authorization_url(
        config,
        "state-value",
        metadata={"authorization_endpoint": "https://login.eveonline.com/v2/oauth/authorize"},
    )
    assert "response_type=code" in url
    assert "client_id=client-123" in url
    assert "state=state-value" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fauth%2Fcallback" in url
    assert "scope=esi-location.read_location.v1" in url


def test_decode_eve_access_token_validates_character_identity():
    token, public_key = make_signed_jwt(
        {
            "iss": "https://login.eveonline.com/",
            "aud": ["client-123", "EVE Online"],
            "exp": int(time.time()) + 600,
            "sub": "CHARACTER:EVE:123456789",
            "name": "Scout Pilot",
            "scp": ["esi-location.read_location.v1"],
            "owner": "owner-hash",
        }
    )
    payload = decode_eve_access_token(token, client_id="client-123", jwk_client=FakeJwkClient(public_key))
    assert payload["name"] == "Scout Pilot"
    assert payload["sub"] == "CHARACTER:EVE:123456789"


def test_decode_eve_access_token_allows_small_iat_clock_skew():
    token, public_key = make_signed_jwt(
        {
            "iss": "https://login.eveonline.com/",
            "aud": ["client-123", "EVE Online"],
            "exp": int(time.time()) + 600,
            "iat": int(time.time()) + 60,
            "sub": "CHARACTER:EVE:123456789",
            "name": "Scout Pilot",
        }
    )
    payload = decode_eve_access_token(token, client_id="client-123", jwk_client=FakeJwkClient(public_key))
    assert payload["name"] == "Scout Pilot"


def test_decode_eve_access_token_rejects_large_iat_clock_skew():
    token, public_key = make_signed_jwt(
        {
            "iss": "https://login.eveonline.com/",
            "aud": ["client-123", "EVE Online"],
            "exp": int(time.time()) + 600,
            "iat": int(time.time()) + 300,
            "sub": "CHARACTER:EVE:123456789",
            "name": "Scout Pilot",
        }
    )
    try:
        decode_eve_access_token(token, client_id="client-123", jwk_client=FakeJwkClient(public_key))
    except Exception as exc:
        assert "not yet valid" in str(exc)
    else:
        raise AssertionError("expected excessive iat clock skew to be rejected")


def test_decode_eve_access_token_rejects_wrong_audience():
    token, public_key = make_signed_jwt(
        {
            "iss": "https://login.eveonline.com/",
            "aud": ["other-client", "EVE Online"],
            "exp": int(time.time()) + 600,
            "sub": "CHARACTER:EVE:123456789",
            "name": "Scout Pilot",
        }
    )
    try:
        decode_eve_access_token(token, client_id="client-123", jwk_client=FakeJwkClient(public_key))
    except Exception as exc:
        assert "audience" in str(exc)
    else:
        raise AssertionError("expected wrong audience to be rejected")


def test_decode_eve_access_token_rejects_bad_signature():
    token, _public_key = make_signed_jwt(
        {
            "iss": "https://login.eveonline.com/",
            "aud": ["client-123", "EVE Online"],
            "exp": int(time.time()) + 600,
            "sub": "CHARACTER:EVE:123456789",
            "name": "Scout Pilot",
        }
    )
    wrong_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    try:
        decode_eve_access_token(token, client_id="client-123", jwk_client=FakeJwkClient(wrong_private_key.public_key()))
    except Exception as exc:
        assert "failed verification" in str(exc)
    else:
        raise AssertionError("expected bad signature to be rejected")


def test_decode_eve_access_token_rejects_unsigned_token():
    token = make_unsigned_jwt(
        {
            "iss": "https://login.eveonline.com/",
            "aud": ["client-123", "EVE Online"],
            "exp": int(time.time()) + 600,
            "sub": "CHARACTER:EVE:123456789",
            "name": "Scout Pilot",
        }
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    try:
        decode_eve_access_token(token, client_id="client-123", jwk_client=FakeJwkClient(private_key.public_key()))
    except Exception as exc:
        assert "failed verification" in str(exc)
    else:
        raise AssertionError("expected unsigned token to be rejected")


def test_membership_allowed_accepts_configured_corporation_or_alliance():
    config = EveSsoConfig(allowed_corporation_ids=(1001,), allowed_alliance_ids=(2002,))
    assert membership_allowed(config, corporation_id=1001, alliance_id=None)
    assert membership_allowed(config, corporation_id=9999, alliance_id=2002)
    assert not membership_allowed(config, corporation_id=9999, alliance_id=None)


def test_membership_allowed_accepts_configured_character():
    config = EveSsoConfig(allowed_character_ids=(2124413713,))
    assert membership_allowed(config, character_id=2124413713, corporation_id=9999, alliance_id=None)
    assert not membership_allowed(config, character_id=123456789, corporation_id=9999, alliance_id=None)


def test_dashboard_access_requires_enabled_sso():
    access = dashboard_access_status(EveSsoConfig(), None)
    assert access.ok is False
    assert access.status == 503
    assert "SSO is not configured" in access.message


def test_dashboard_access_requires_signed_in_pilot():
    config = EveSsoConfig(
        client_id="client-123",
        client_secret="secret",
        callback_url="http://127.0.0.1:8765/auth/callback",
    )
    access = dashboard_access_status(config, None)
    assert access.ok is False
    assert access.status == 401


def test_dashboard_access_rejects_non_allowlisted_pilot():
    config = EveSsoConfig(
        client_id="client-123",
        client_secret="secret",
        callback_url="http://127.0.0.1:8765/auth/callback",
        allowed_corporation_ids=(1001,),
    )
    pilot = VerifiedPilot(
        character_id=123456789,
        character_name="Neutral Pilot",
        corporation_id=9999,
        membership_ok=False,
    )
    access = dashboard_access_status(config, pilot)
    assert access.ok is False
    assert access.status == 403


def test_dashboard_access_allows_verified_member():
    config = EveSsoConfig(
        client_id="client-123",
        client_secret="secret",
        callback_url="http://127.0.0.1:8765/auth/callback",
        allowed_corporation_ids=(1001,),
    )
    pilot = VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        membership_ok=True,
    )
    access = dashboard_access_status(config, pilot)
    assert access.ok is True
    assert access.status == 200


def test_dashboard_includes_plex_button_press_effect():
    assert "plex-petal-layer" in corp_intel.DASHBOARD_HTML
    assert "PLEX" in corp_intel.DASHBOARD_HTML
    assert "prefers-reduced-motion" in corp_intel.DASHBOARD_HTML
    assert "event.isTrusted" in corp_intel.DASHBOARD_HTML
    assert "managed-document-duck-layer" in corp_intel.DASHBOARD_HTML
    assert "eve-managed-document-change" in corp_intel.DASHBOARD_HTML
    assert "eveVoiceManagedDocumentChanged" in corp_intel.DASHBOARD_HTML


def test_verify_sso_character_builds_verified_pilot_from_public_esi(monkeypatch):
    config = EveSsoConfig(
        client_id="client-123",
        allowed_corporation_ids=(1001,),
    )
    token_payload = {
        "sub": "CHARACTER:EVE:123456789",
        "name": "Scout Pilot",
        "scp": ["esi-location.read_location.v1"],
        "owner": "owner-hash",
    }

    monkeypatch.setattr(corp_intel, "fetch_esi_character", lambda _config, _id: {"corporation_id": 1001})
    monkeypatch.setattr(corp_intel, "fetch_esi_corporation", lambda _config, _id: {"name": "Star Fleet"})
    monkeypatch.setattr(corp_intel, "fetch_esi_alliance", lambda _config, _id: {})

    pilot = verify_sso_character(config, access_token="unused", token_payload=token_payload)
    assert pilot.character_id == 123456789
    assert pilot.character_name == "Scout Pilot"
    assert pilot.corporation_id == 1001
    assert pilot.corporation_name == "Star Fleet"
    assert pilot.membership_ok is True
    assert pilot.scopes == ("esi-location.read_location.v1",)


def test_pilot_registry_persists_verified_pilot(tmp_path):
    registry = PilotRegistry(tmp_path / "pilots.sqlite3")
    pilot = VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        owner_hash="owner-hash",
        scopes=("esi-location.read_location.v1",),
        membership_ok=True,
        verified_at="2026-06-03T06:30:00Z",
        last_login_at="2026-06-03T06:30:00Z",
    )
    registry.upsert(pilot)
    reloaded = PilotRegistry(tmp_path / "pilots.sqlite3").get(123456789)
    assert reloaded is not None
    assert reloaded.character_name == "Scout Pilot"
    assert reloaded.membership_ok is True
    assert reloaded.scopes == ("esi-location.read_location.v1",)


def test_pilot_registry_creates_hash_only_agent_token(tmp_path):
    registry = PilotRegistry(tmp_path / "pilots.sqlite3")
    pilot = VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        membership_ok=True,
        verified_at="2026-06-03T06:30:00Z",
        last_login_at="2026-06-03T06:30:00Z",
    )
    registry.upsert(pilot)

    token, record = registry.create_agent_token(pilot.character_id, label="Home PC")
    assert token.startswith("cit_")
    assert record.label == "Home PC"
    assert registry.resolve_agent_token(token).character_name == "Scout Pilot"

    with corp_intel.sqlite3.connect(tmp_path / "pilots.sqlite3") as connection:
        raw = connection.execute("SELECT token_hash FROM agent_tokens").fetchone()[0]
    assert raw == hash_agent_token(token)
    assert token not in raw


def test_pilot_registry_revokes_agent_token(tmp_path):
    registry = PilotRegistry(tmp_path / "pilots.sqlite3")
    pilot = VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        membership_ok=True,
        verified_at="2026-06-03T06:30:00Z",
        last_login_at="2026-06-03T06:30:00Z",
    )
    registry.upsert(pilot)
    token, record = registry.create_agent_token(pilot.character_id, label="Home PC")
    assert registry.revoke_agent_token(character_id=pilot.character_id, token_id=record.token_id)
    assert registry.resolve_agent_token(token) is None


def test_watchlist_read_token_accepts_shared_token_unless_verified_required(tmp_path):
    registry = PilotRegistry(tmp_path / "pilots.sqlite3")
    handler = type("Handler", (), {"headers": {"Authorization": "Bearer shared-token"}})()

    assert request_has_watchlist_read_token(
        handler,
        ingest_token="shared-token",
        pilot_registry=registry,
        require_verified=False,
    )
    assert not request_has_watchlist_read_token(
        handler,
        ingest_token="shared-token",
        pilot_registry=registry,
        require_verified=True,
    )


def test_watchlist_read_token_accepts_verified_agent_token(tmp_path):
    registry = PilotRegistry(tmp_path / "pilots.sqlite3")
    pilot = VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        membership_ok=True,
        verified_at="2026-06-03T06:30:00Z",
        last_login_at="2026-06-03T06:30:00Z",
    )
    registry.upsert(pilot)
    token, _record = registry.create_agent_token(pilot.character_id, label="Home PC")
    handler = type("Handler", (), {"headers": {"Authorization": f"Bearer {token}"}})()

    assert request_has_watchlist_read_token(
        handler,
        ingest_token="shared-token",
        pilot_registry=registry,
        require_verified=True,
    )


def test_fetch_remote_watchlist_sends_bearer_token(monkeypatch):
    captured: dict[str, object] = {}

    def fake_get_json(url: str, *, timeout_seconds: float, headers: dict[str, str] | None = None):
        captured["url"] = url
        captured["timeout_seconds"] = timeout_seconds
        captured["headers"] = headers
        return {"hostile_pilots": ["Bad Pilot"]}

    monkeypatch.setattr(corp_intel, "get_json", fake_get_json)

    watchlist = fetch_remote_watchlist("http://example.test/", token="cit_token", timeout_seconds=3)

    assert watchlist.hostile_pilots == ("Bad Pilot",)
    assert captured["url"] == "http://example.test/api/watchlist"
    assert captured["timeout_seconds"] == 3
    assert captured["headers"] == {"Authorization": "Bearer cit_token"}


def test_corp_intel_dashboard_sends_nonce_csp():
    store = IntelEventStore()
    server = corp_intel.build_http_server("127.0.0.1", 0, store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        headers = dict(response.getheaders())
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    csp = headers["Content-Security-Policy"]
    assert response.status == 200
    assert "default-src 'self'" in csp
    assert "script-src 'self' 'nonce-" in csp
    assert "style-src 'self' 'nonce-" in csp
    assert "script-src-attr 'none'" in csp
    assert "nonce=" in body
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_system_matcher_finds_canonical_system_names():
    matcher = SystemMatcher(["Jita", "Old Man Star", "Tama"])
    assert matcher.find("hostile in tama and Old Man Star") == ("Tama", "Old Man Star")


def test_intel_parser_detects_hostile_system_report():
    parser = IntelParser(["Jita", "Tama"])
    chat = ChatMessage(
        log_path="",
        channel="Corp",
        timestamp="2026.06.03 02:10:11",
        speaker="Scout",
        text="red hostile in Tama on gate",
    )
    event = parser.analyze(chat, source="ScoutClient")
    assert event is not None
    assert event.severity == "high"
    assert event.systems == ("Tama",)
    assert "hostile" in event.categories
    assert "red" in event.keywords


def test_intel_parser_detects_critical_aid_call_without_system():
    parser = IntelParser(["Jita", "Tama"])
    chat = ChatMessage(
        log_path="",
        channel="Fleet",
        timestamp="2026.06.03 02:10:11",
        speaker="Miner",
        text="need reps now, tackled",
    )
    event = parser.analyze(chat, source="MinerClient")
    assert event is not None
    assert event.severity == "critical"
    assert event.systems == ()
    assert "aid" in event.categories
    assert "need reps" in event.keywords


def test_intel_parser_ignores_plain_system_mentions():
    parser = IntelParser(["Jita", "Tama"])
    chat = ChatMessage(
        log_path="",
        channel="Corp",
        timestamp="2026.06.03 02:10:11",
        speaker="Hauler",
        text="I am hauling through Jita now",
    )
    assert parser.analyze(chat, source="HaulerClient") is None


def test_intel_parser_detects_watchlisted_hostile_pilot():
    watchlist_store = WatchlistStore(
        watchlist=IntelWatchlist(hostile_pilots=("Bad Pilot",)),
    )
    parser = IntelParser(["Jita", "Tama"], watchlist_store=watchlist_store)
    chat = ChatMessage(
        log_path="",
        channel="Local",
        timestamp="2026.06.03 02:10:11",
        speaker="Scout",
        text="Bad Pilot just landed in Tama",
    )
    event = parser.analyze(chat, source="ScoutClient")
    assert event is not None
    assert event.severity == "high"
    assert event.systems == ("Tama",)
    assert "hostile" in event.categories
    assert "watchlist-pilot" in event.categories
    assert "pilot: Bad Pilot" in event.keywords


def test_intel_parser_detects_watchlisted_hostile_pilot_speaker():
    watchlist_store = WatchlistStore(
        watchlist=IntelWatchlist(hostile_pilots=("Bad Pilot",)),
    )
    parser = IntelParser(["Jita", "Tama"], watchlist_store=watchlist_store)
    chat = ChatMessage(
        log_path="",
        channel="Local",
        timestamp="2026.06.03 02:10:11",
        speaker="Bad Pilot",
        text="o/",
    )
    event = parser.analyze(chat, source="ScoutClient")
    assert event is not None
    assert event.severity == "high"
    assert event.speaker == "Bad Pilot"
    assert "hostile" in event.categories
    assert "watchlist-pilot" in event.categories
    assert "pilot: Bad Pilot" in event.keywords


def test_intel_parser_detects_watchlisted_help_phrase():
    watchlist_store = WatchlistStore(
        watchlist=IntelWatchlist(help_phrases=("armor breaking",)),
    )
    parser = IntelParser(["Jita", "Tama"], watchlist_store=watchlist_store)
    chat = ChatMessage(
        log_path="",
        channel="Fleet",
        timestamp="2026.06.03 02:10:11",
        speaker="Miner",
        text="armor breaking at belt 4",
    )
    event = parser.analyze(chat, source="MinerClient")
    assert event is not None
    assert event.severity == "critical"
    assert "aid" in event.categories
    assert "watchlist-help" in event.categories
    assert "help: armor breaking" in event.keywords


def test_eve_timestamp_to_iso_treats_log_time_as_utc():
    assert eve_timestamp_to_iso("2026.06.03 02:10:11") == "2026-06-03T02:10:11Z"


def test_event_store_summarizes_counts_and_systems():
    store = IntelEventStore(max_events=10)
    store.add(
        IntelEvent(
            source="Scout",
            channel="Corp",
            speaker="Scout",
            message="hostile in Tama",
            categories=("hostile",),
            severity="high",
            systems=("Tama",),
            keywords=("hostile",),
            observed_at="2026-06-03T02:10:11Z",
        )
    )
    snapshot = store.snapshot()
    assert snapshot["counts"]["events"] == 1
    assert snapshot["counts"]["high"] == 1
    assert snapshot["systems"][0]["system"] == "Tama"


def test_event_store_counts_watchlist_hits():
    store = IntelEventStore(max_events=10)
    store.add(
        IntelEvent(
            source="Scout",
            channel="Local",
            speaker="Scout",
            message="Bad Pilot in Tama",
            categories=("hostile", "watchlist-pilot"),
            severity="high",
            systems=("Tama",),
            keywords=("pilot: Bad Pilot",),
            observed_at="2026-06-03T02:10:11Z",
        )
    )
    snapshot = store.snapshot()
    assert snapshot["counts"]["watchlist"] == 1
    assert snapshot["counts"]["hostile"] == 1


def test_event_store_persists_events_to_sqlite(tmp_path):
    recorded_at = corp_intel.now_iso()
    database = EventDatabase(tmp_path / "events.sqlite3")
    store = IntelEventStore(max_events=10, database=database)
    store.add(
        IntelEvent(
            event_id="evt-1",
            source="Scout",
            channel="Local",
            speaker="Scout",
            message="Bad Pilot in Tama",
            categories=("hostile", "watchlist-pilot"),
            severity="high",
            systems=("Tama",),
            keywords=("pilot: Bad Pilot",),
            observed_at=recorded_at,
            reported_at=recorded_at,
            log_path=r"C:\Users\Pilot\Documents\EVE\logs\Chatlogs\Local.txt",
        )
    )

    reloaded = IntelEventStore(max_events=10, database=EventDatabase(tmp_path / "events.sqlite3"))
    snapshot = reloaded.snapshot()
    assert snapshot["counts"]["events"] == 1
    assert snapshot["retention"] == {
        "days": corp_intel.DEFAULT_EVENT_RETENTION_DAYS,
        "max_events": 10,
    }
    assert snapshot["events"][0]["message"] == "Bad Pilot in Tama"
    assert "log_path" not in snapshot["events"][0]


def test_event_store_prunes_old_persisted_events(tmp_path):
    path = tmp_path / "events.sqlite3"
    database = EventDatabase(path)
    old_store = IntelEventStore(max_events=10, database=database, retention_days=30)
    old_store.add(
        IntelEvent(
            event_id="old",
            source="Scout",
            channel="Local",
            speaker="Scout",
            message="old hostile",
            categories=("hostile",),
            severity="high",
            observed_at="2020-01-01T00:00:00Z",
            reported_at="2020-01-01T00:00:01Z",
        )
    )

    pruned = IntelEventStore(max_events=10, database=EventDatabase(path), retention_days=1)
    assert pruned.snapshot()["counts"]["events"] == 0


def test_intel_event_json_does_not_expose_local_log_path():
    event = IntelEvent(
        source="Scout",
        channel="Corp",
        speaker="Scout",
        message="hostile in Tama",
        categories=("hostile",),
        severity="high",
        log_path=r"C:\Users\Pilot\Documents\EVE\logs\Chatlogs\Corp.txt",
    )
    assert "log_path" not in event.to_dict()


def test_watchlist_store_persists_sanitized_terms(tmp_path):
    path = tmp_path / "watchlist.json"
    store = WatchlistStore(path)
    updated = store.update(
        {
            "hostile_pilots": "Bad Pilot\nBad Pilot\n",
            "hostile_corporations": ["Red Corp", "Red Corp"],
            "help_phrases": ["armor breaking"],
            "keywords": ["bubble camp"],
        },
        updated_by="test",
    )
    assert updated.hostile_pilots == ("Bad Pilot",)
    assert updated.hostile_corporations == ("Red Corp",)

    reloaded = WatchlistStore(path)
    snapshot = reloaded.snapshot()
    assert snapshot.hostile_pilots == ("Bad Pilot",)
    assert snapshot.help_phrases == ("armor breaking",)


def test_watchlist_safety_flags_broad_terms():
    safety = corp_intel.analyze_watchlist_safety(
        IntelWatchlist(
            hostile_pilots=("Bad",),
            help_phrases=("help",),
            keywords=("red", "gate camp"),
        )
    )

    risky_terms = {item["term"]: item for item in safety["risks"]}
    assert safety["risk_count"] == 3
    assert safety["high"] == 2
    assert risky_terms["help"]["level"] == "high"
    assert risky_terms["red"]["level"] == "high"
    assert risky_terms["Bad"]["level"] == "medium"


def test_watchlist_preview_uses_retained_sanitized_events():
    store = IntelEventStore(max_events=10)
    store.add(
        IntelEvent(
            event_id="match",
            source="Scout",
            channel="Local",
            speaker="Neutral Pilot",
            message="red gate camp in Tama",
            categories=("hostile",),
            severity="high",
            systems=("Tama",),
            observed_at="2026-06-03T06:30:00Z",
            log_path=r"C:\Users\Pilot\Documents\EVE\logs\Chatlogs\Local.txt",
        )
    )
    store.add(
        IntelEvent(
            event_id="clean",
            source="Scout",
            channel="Corp",
            speaker="Friendly",
            message="mining fleet forming",
            categories=("info",),
            severity="info",
            observed_at="2026-06-03T06:31:00Z",
            log_path=r"C:\Users\Pilot\Documents\EVE\logs\Chatlogs\Corp.txt",
        )
    )

    payload = corp_intel.build_watchlist_preview({"keywords": ["red"]}, store)

    assert payload["source"] == "retained_intel"
    assert payload["preview"]["events_checked"] == 2
    assert payload["preview"]["matched_events"] == 1
    match = payload["preview"]["matches"][0]
    assert match["event"]["id"] == "match"
    assert match["matched_terms"][0]["term"] == "red"
    assert "log_path" not in match["event"]


def test_ingest_payload_accepts_single_event_dict():
    store = IntelEventStore(max_events=10)
    added = ingest_payload(
        {
            "source": "Scout",
            "channel": "Corp",
            "speaker": "Scout",
            "message": "hostile in Tama",
            "categories": ["hostile"],
            "severity": "high",
            "systems": ["Tama"],
            "keywords": ["hostile"],
        },
        store,
    )
    assert added == 1
    assert store.snapshot()["counts"]["events"] == 1


def test_ingest_payload_stamps_verified_pilot_identity():
    store = IntelEventStore(max_events=10)
    pilot = VerifiedPilot(
        character_id=123456789,
        character_name="Scout Pilot",
        corporation_id=1001,
        corporation_name="Star Fleet",
        membership_ok=True,
    )
    added = ingest_payload(
        {
            "source": "Typed Label",
            "channel": "Local",
            "speaker": "Scout",
            "message": "hostile in Tama",
            "categories": ["hostile"],
            "severity": "high",
            "systems": ["Tama"],
            "keywords": ["hostile"],
            "log_path": r"C:\Users\Pilot\Documents\EVE\logs\Chatlogs\Local.txt",
        },
        store,
        verified_pilot=pilot,
    )
    event = store.snapshot()["events"][0]
    assert added == 1
    assert event["source"] == "Scout Pilot"
    assert event["verified_character_id"] == 123456789
    assert event["verified_corporation_id"] == 1001
    assert "log_path" not in event
