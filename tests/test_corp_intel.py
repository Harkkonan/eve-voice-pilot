import base64
import json
from pathlib import Path
import sys
import time

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
    decode_eve_access_token,
    eve_timestamp_to_iso,
    hash_agent_token,
    ingest_payload,
    membership_allowed,
    parse_channel_name_from_text,
    parse_chat_line,
    verify_sso_character,
)


def make_unsigned_jwt(payload: dict) -> str:
    def encode(part: dict) -> str:
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(payload)}.signature"


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


def test_parse_channel_name_from_header_text():
    text = """
------------------------------------------------------------
  Channel ID: 123
  Channel Name: Fleet
  Listener: Alice Example
------------------------------------------------------------
"""
    assert parse_channel_name_from_text(text) == "Fleet"


def test_channel_filter_allows_exact_and_wildcard_names():
    channel_filter = ChannelFilter(["Corp", "*Intel*"])
    assert channel_filter.allows("Corp")
    assert channel_filter.allows("Standing Intel")
    assert not channel_filter.allows("Private Chat")


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
    token = make_unsigned_jwt(
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
    payload = decode_eve_access_token(token, client_id="client-123")
    assert payload["name"] == "Scout Pilot"
    assert payload["sub"] == "CHARACTER:EVE:123456789"


def test_decode_eve_access_token_rejects_wrong_audience():
    token = make_unsigned_jwt(
        {
            "iss": "https://login.eveonline.com/",
            "aud": ["other-client", "EVE Online"],
            "exp": int(time.time()) + 600,
            "sub": "CHARACTER:EVE:123456789",
            "name": "Scout Pilot",
        }
    )
    try:
        decode_eve_access_token(token, client_id="client-123")
    except Exception as exc:
        assert "audience" in str(exc)
    else:
        raise AssertionError("expected wrong audience to be rejected")


def test_membership_allowed_accepts_configured_corporation_or_alliance():
    config = EveSsoConfig(allowed_corporation_ids=(1001,), allowed_alliance_ids=(2002,))
    assert membership_allowed(config, corporation_id=1001, alliance_id=None)
    assert membership_allowed(config, corporation_id=9999, alliance_id=2002)
    assert not membership_allowed(config, corporation_id=9999, alliance_id=None)


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
            observed_at="2026-06-03T02:10:11Z",
            reported_at="2026-06-03T02:10:12Z",
            log_path=r"C:\Users\Pilot\Documents\EVE\logs\Chatlogs\Local.txt",
        )
    )

    reloaded = IntelEventStore(max_events=10, database=EventDatabase(tmp_path / "events.sqlite3"))
    snapshot = reloaded.snapshot()
    assert snapshot["counts"]["events"] == 1
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
