from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.corp_intel import (
    ChannelFilter,
    ChatMessage,
    IntelWatchlist,
    IntelEvent,
    IntelEventStore,
    IntelParser,
    SystemMatcher,
    WatchlistStore,
    eve_timestamp_to_iso,
    ingest_payload,
    parse_channel_name_from_text,
    parse_chat_line,
)


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
