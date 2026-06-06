from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.corp_intel import ChatMessage, EveSsoConfig
from eve_voice_pilot.intel_pet import (
    ALERT_SPRITE_SEQUENCE,
    IDLE_SPRITE_SEQUENCE,
    LOCATION_SCOPE,
    SHIP_FRAME_COUNT,
    IntelPetLocationCheer,
    IntelPetLocationSession,
    IntelPetEngine,
    IntelPetSettings,
    clean_user_terms,
    fetch_pet_location,
    history_item_from_alert,
    history_item_from_cheer,
    is_happy_system,
    load_sprite_frames,
    load_settings,
    replace_alert_terms,
    replace_extra_keywords,
    save_settings,
    ship_sprite_frame_paths,
    trim_history,
)


def make_message(text: str, *, speaker: str = "Alice Example", channel: str = "Corp") -> ChatMessage:
    return ChatMessage(
        log_path="corp.txt",
        channel=channel,
        timestamp="2026.06.05 18:15:00",
        speaker=speaker,
        text=text,
    )


def test_name_mention_alerts_when_another_pilot_mentions_you():
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin Ridderston",)))

    alert = engine.analyze(make_message("Dandin Ridderston can you scout the gate?"))

    assert alert is not None
    assert alert.title == "Your name was mentioned in Corp"
    assert alert.severity == "high"
    assert "self-mention" in alert.categories
    assert "name: Dandin Ridderston" in alert.keywords
    assert alert.message == "Dandin Ridderston can you scout the gate?"


def test_name_mention_does_not_alert_on_your_own_line_by_itself():
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin Ridderston",)))

    alert = engine.analyze(
        make_message("Dandin Ridderston checking in", speaker="Dandin Ridderston"),
    )

    assert alert is None


def test_help_call_uses_existing_intel_parser_and_is_critical():
    engine = IntelPetEngine(IntelPetSettings())

    alert = engine.analyze(make_message("need help on the Tama gate"))

    assert alert is not None
    assert alert.title == "Help call in Corp"
    assert alert.severity == "critical"
    assert "aid" in alert.categories
    assert "help" in alert.keywords


def test_extra_keyword_alerts_without_shared_board():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("buy order",)))

    alert = engine.analyze(make_message("that buy order finally showed up"))

    assert alert is not None
    assert alert.title == "Keyword match in Corp"
    assert alert.severity == "medium"
    assert "watchlist-keyword" in alert.categories
    assert "keyword: buy order" in alert.keywords


def test_settings_can_hide_message_text():
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin Ridderston",), show_message_text=False))

    alert = engine.analyze(make_message("Dandin Ridderston ping"))

    assert alert is not None
    assert alert.message == ""


def test_load_settings_merges_local_file_and_cli_overrides(tmp_path):
    settings_path = tmp_path / "intel_pet_settings.json"
    settings_path.write_text(
        """
{
  "pilot_names": ["Dandin Ridderston"],
  "extra_keywords": ["buy order"],
  "help_phrases": ["need evac"],
  "show_message_text": false,
  "alert_seconds": 12
}
""".strip(),
        encoding="utf-8",
    )

    class Args:
        pilot_name = ("Second Pilot",)
        keyword = ("PLEX sale",)
        help_phrase = ()
        no_message_text = False
        alert_seconds = None

    settings = load_settings(Path(settings_path), overrides=Args())

    assert settings.pilot_names == ("Dandin Ridderston", "Second Pilot")
    assert settings.extra_keywords == ("buy order", "PLEX sale")
    assert settings.help_phrases == ("need evac",)
    assert settings.show_message_text is False
    assert settings.alert_seconds == 12


def test_clean_user_terms_splits_commas_and_dedupes_case_insensitively():
    assert clean_user_terms(("buy order, gate camp", "Buy Order", "  need evac  ")) == (
        "buy order",
        "gate camp",
        "need evac",
    )


def test_save_settings_persists_keywords_for_later_load(tmp_path):
    settings_path = tmp_path / "intel_pet_settings.json"
    settings = IntelPetSettings(
        pilot_names=("Dandin Ridderston",),
        extra_keywords=("buy order", "gate camp"),
        help_phrases=("need evac",),
        show_message_text=False,
        alert_seconds=9,
    )

    save_settings(settings_path, settings)
    loaded = load_settings(settings_path)

    assert loaded == settings


def test_engine_update_settings_changes_keyword_matches_without_restarting():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("buy order",)))
    assert engine.analyze(make_message("contract alert")) is None

    updated = replace_extra_keywords(engine.current_settings(), ("contract alert",))
    engine.update_settings(updated)

    alert = engine.analyze(make_message("contract alert is up"))

    assert alert is not None
    assert alert.title == "Keyword match in Corp"
    assert alert.keywords == ("keyword: contract alert",)


def test_replace_alert_terms_updates_all_local_alert_lists():
    settings = IntelPetSettings(
        pilot_names=("Dandin Ridderston",),
        extra_keywords=("buy order",),
        help_phrases=("need help",),
        show_message_text=False,
        alert_seconds=12,
    )

    updated = replace_alert_terms(
        settings,
        pilot_names=("Dandin Ridderston, Second Pilot", "second pilot"),
        extra_keywords=("gate camp, Buy Order",),
        help_phrases=("need evac", "Need Evac"),
    )

    assert updated.pilot_names == ("Dandin Ridderston", "Second Pilot")
    assert updated.extra_keywords == ("gate camp", "Buy Order")
    assert updated.help_phrases == ("need evac",)
    assert updated.show_message_text is False
    assert updated.alert_seconds == 12


def test_replace_alert_terms_without_updates_returns_same_settings():
    settings = IntelPetSettings(extra_keywords=("buy order",))

    assert replace_alert_terms(settings) is settings


def test_is_happy_system_matches_configured_systems_case_insensitively():
    assert is_happy_system("jita", ("Dihra", "Amarr", "Jita"))
    assert is_happy_system("Dihra", ("dihra",))
    assert not is_happy_system("Perimeter", ("Dihra", "Amarr", "Jita"))


def test_fetch_pet_location_uses_read_only_esi_scope(monkeypatch):
    calls = []

    def fake_get_json(url, *, timeout_seconds=30.0, headers=None):
        calls.append((url, headers or {}))
        if "/location/" in url:
            return {"solar_system_id": 30000142}
        if "/universe/systems/30000142/" in url:
            return {"name": "Jita"}
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("eve_voice_pilot.intel_pet.get_json", fake_get_json)
    session = IntelPetLocationSession(
        character_id=123456789,
        character_name="Scout Pilot",
        scopes=(LOCATION_SCOPE,),
        access_token="access-token",
        expires_at=9999999999,
    )

    location = fetch_pet_location(EveSsoConfig(esi_base_url="https://esi.test/latest"), session)

    assert location.solar_system_id == 30000142
    assert location.solar_system_name == "Jita"
    assert calls[0][0] == "https://esi.test/latest/characters/123456789/location/?datasource=tranquility"
    assert calls[0][1]["Authorization"] == "Bearer access-token"
    assert "Authorization" not in calls[1][1]


def test_fetch_pet_location_requires_location_scope():
    session = IntelPetLocationSession(
        character_id=123456789,
        character_name="Scout Pilot",
        scopes=(),
        access_token="access-token",
        expires_at=9999999999,
    )

    try:
        fetch_pet_location(EveSsoConfig(esi_base_url="https://esi.test/latest"), session)
    except Exception as exc:
        assert LOCATION_SCOPE in str(exc)
    else:
        raise AssertionError("expected missing location scope to fail")


def test_history_item_from_alert_keeps_message_context():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp",)))
    alert = engine.analyze(make_message("gate camp on the Amarr undock", speaker="Scout Pilot"))

    assert alert is not None
    item = history_item_from_alert(alert)

    assert item.title == alert.title
    assert item.detail == "Scout Pilot: gate camp on the Amarr undock"
    assert "keyword: gate camp" in item.meta
    assert item.severity == alert.severity


def test_history_item_from_cheer_records_location_arrival():
    cheer = IntelPetLocationCheer(system_name="Amarr", character_name="Scout Pilot", updated_at="2026-06-06T10:45:00Z")

    item = history_item_from_cheer(cheer)

    assert item.title == "Happy arrival: Amarr"
    assert item.detail == "Scout Pilot reached Amarr."
    assert LOCATION_SCOPE in item.meta
    assert item.recorded_at == "2026-06-06T10:45:00Z"


def test_trim_history_keeps_most_recent_items():
    items = tuple(
        history_item_from_cheer(
            IntelPetLocationCheer(system_name=f"System {index}", character_name="Scout", updated_at=str(index))
        )
        for index in range(5)
    )

    trimmed = trim_history(items, limit=3)

    assert [item.title for item in trimmed] == [
        "Happy arrival: System 2",
        "Happy arrival: System 3",
        "Happy arrival: System 4",
    ]


def test_ship_sprite_frame_paths_point_to_committed_assets():
    paths = ship_sprite_frame_paths()

    assert len(paths) == SHIP_FRAME_COUNT
    assert all(path.exists() for path in paths)
    assert all(path.name == f"ship-frame-{index:02d}.png" for index, path in enumerate(paths))


def test_sprite_sequences_only_reference_existing_frames():
    valid_indexes = set(range(SHIP_FRAME_COUNT))

    assert set(IDLE_SPRITE_SEQUENCE) <= valid_indexes
    assert set(ALERT_SPRITE_SEQUENCE) <= valid_indexes
    assert IDLE_SPRITE_SEQUENCE[-1] == 0
    assert ALERT_SPRITE_SEQUENCE[-1] == 0


def test_load_sprite_frames_returns_empty_when_any_frame_is_missing(tmp_path):
    class FakeTk:
        class PhotoImage:
            def __init__(self, *, file, master):
                self.file = file
                self.master = master

    assert len(load_sprite_frames(FakeTk, object())) == SHIP_FRAME_COUNT
    assert load_sprite_frames(FakeTk, object(), paths=ship_sprite_frame_paths(tmp_path)) == ()
