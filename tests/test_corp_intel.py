from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.corp_intel import (
    ChannelFilter,
    ChatMessage,
    IntelEvent,
    IntelEventStore,
    IntelParser,
    SystemMatcher,
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
