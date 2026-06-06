from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.corp_intel import ChannelFilter, ChatMessage, EveSsoConfig, watch_chat_logs
import eve_voice_pilot.intel_pet as intel_pet_module
from eve_voice_pilot.intel_pet import (
    ALERT_SPRITE_SEQUENCE,
    BEHAVIOR_ALERT,
    BEHAVIOR_COMBAT,
    BEHAVIOR_HAPPY,
    BEHAVIOR_IDLE,
    BEHAVIOR_NONE,
    DEFAULT_ALERT_SECONDS,
    DEFAULT_ALERT_BEHAVIORS,
    IDLE_SPRITE_SEQUENCE,
    KILL_SPRITE_STEPS,
    LOCATION_SCOPE,
    SHIP_FRAME_COUNT,
    GameLogState,
    IntelPetCombatCheer,
    IntelPetLocationCheer,
    IntelPetLocationSession,
    IntelPetEngine,
    IntelPetSettings,
    alert_behavior_key,
    behavior_for_alert,
    behavior_for_kind,
    behavior_key_from_label,
    behavior_label,
    clean_alert_behaviors,
    clean_user_terms,
    combat_cheer_from_game_log_line,
    display_message_from_alert,
    display_message_from_cheer,
    display_message_from_combat_cheer,
    fetch_pet_location,
    history_item_from_alert,
    history_item_from_cheer,
    history_item_from_combat_cheer,
    history_item_from_status,
    is_kill_event_text,
    is_happy_system,
    load_sprite_frames,
    load_settings,
    read_new_combat_cheers,
    replace_alert_behaviors,
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
  "alert_seconds": 12,
  "alert_behaviors": {
    "mention": "happy",
    "help": "combat",
    "hostile": "none",
    "keyword": "idle",
    "location": "alert",
    "combat": "not-a-real-behavior"
  }
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
    assert settings.alert_behaviors["mention"] == BEHAVIOR_HAPPY
    assert settings.alert_behaviors["help"] == BEHAVIOR_COMBAT
    assert settings.alert_behaviors["hostile"] == BEHAVIOR_NONE
    assert settings.alert_behaviors["keyword"] == BEHAVIOR_IDLE
    assert settings.alert_behaviors["location"] == BEHAVIOR_ALERT
    assert settings.alert_behaviors["combat"] == DEFAULT_ALERT_BEHAVIORS["combat"]


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
        alert_behaviors=clean_alert_behaviors(
            {
                "mention": BEHAVIOR_HAPPY,
                "combat": BEHAVIOR_NONE,
            }
        ),
    )

    save_settings(settings_path, settings)
    loaded = load_settings(settings_path)

    assert loaded == settings


def test_default_alert_duration_is_fifteen_seconds():
    assert IntelPetSettings().alert_seconds == DEFAULT_ALERT_SECONDS == 15.0


def test_default_alert_behaviors_preserve_existing_pet_actions():
    settings = IntelPetSettings()

    assert settings.alert_behaviors == DEFAULT_ALERT_BEHAVIORS
    assert behavior_for_kind("mention", settings) == BEHAVIOR_ALERT
    assert behavior_for_kind("location", settings) == BEHAVIOR_HAPPY
    assert behavior_for_kind("combat", settings) == BEHAVIOR_COMBAT


def test_replace_alert_behaviors_cleans_unknown_values():
    settings = replace_alert_behaviors(
        IntelPetSettings(),
        {
            "mention": BEHAVIOR_HAPPY,
            "help": "not-real",
            "combat": BEHAVIOR_NONE,
            "unknown": BEHAVIOR_COMBAT,
        },
    )

    assert settings.alert_behaviors["mention"] == BEHAVIOR_HAPPY
    assert settings.alert_behaviors["help"] == DEFAULT_ALERT_BEHAVIORS["help"]
    assert settings.alert_behaviors["combat"] == BEHAVIOR_NONE
    assert "unknown" not in settings.alert_behaviors


def test_behavior_labels_round_trip_for_options_ui():
    assert behavior_key_from_label(behavior_label(BEHAVIOR_COMBAT)) == BEHAVIOR_COMBAT
    assert behavior_key_from_label("not a label") == BEHAVIOR_ALERT


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


def test_alert_behavior_key_routes_chat_alert_types():
    settings = replace_alert_behaviors(
        IntelPetSettings(
            pilot_names=("Dandin Ridderston",),
            extra_keywords=("buy order",),
        ),
        {
            "mention": BEHAVIOR_HAPPY,
            "help": BEHAVIOR_COMBAT,
            "keyword": BEHAVIOR_NONE,
        },
    )
    engine = IntelPetEngine(settings)

    mention = engine.analyze(make_message("Dandin Ridderston can you scout?"))
    help_call = engine.analyze(make_message("need help on the gate"))
    keyword = engine.analyze(make_message("that buy order is up"))

    assert mention is not None
    assert alert_behavior_key(mention) == "mention"
    assert behavior_for_alert(mention, settings) == BEHAVIOR_HAPPY
    assert help_call is not None
    assert alert_behavior_key(help_call) == "help"
    assert behavior_for_alert(help_call, settings) == BEHAVIOR_COMBAT
    assert keyword is not None
    assert alert_behavior_key(keyword) == "keyword"
    assert behavior_for_alert(keyword, settings) == BEHAVIOR_NONE


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


def test_combat_cheer_parses_destroyed_game_log_line():
    line = (
        "[ 2026.06.06 19:15:31 ] (combat) "
        "<color=0xff00ffff><b>Guristas Wrecker has been destroyed</b>"
    )

    cheer = combat_cheer_from_game_log_line(line, log_path="game.txt")

    assert cheer is not None
    assert cheer.message == "Guristas Wrecker has been destroyed"
    assert cheer.observed_at == "2026-06-06T19:15:31Z"
    assert cheer.log_path == "game.txt"


def test_combat_cheer_ignores_your_own_loss_line():
    line = "[ 2026.06.06 19:15:31 ] (notify) Your ship has been destroyed"

    assert combat_cheer_from_game_log_line(line) is None
    assert not is_kill_event_text("Your capsule has been destroyed")


def test_read_new_combat_cheers_updates_game_log_offset(tmp_path):
    path = tmp_path / "GameLog_20260606_191531.txt"
    path.write_text(
        "\n".join(
            (
                "[ 2026.06.06 19:15:31 ] (combat) Your blaster hits a pirate",
                "[ 2026.06.06 19:15:33 ] (combat) Serpentis Spy has been destroyed",
            )
        ),
        encoding="utf-8",
    )
    state = GameLogState(path=path, encoding="utf-8", offset=0)

    cheers = read_new_combat_cheers(state)

    assert [cheer.message for cheer in cheers] == ["Serpentis Spy has been destroyed"]
    assert state.offset > 0
    assert read_new_combat_cheers(state) == []


def test_display_message_from_alert_is_message_only():
    engine = IntelPetEngine(IntelPetSettings(extra_keywords=("gate camp",)))
    alert = engine.analyze(make_message("gate camp on the Amarr undock", speaker="Scout Pilot"))

    assert alert is not None
    assert display_message_from_alert(alert) == "gate camp on the Amarr undock"


def test_display_message_from_cheer_is_short_arrival_text():
    cheer = IntelPetLocationCheer(system_name="Amarr", character_name="Scout Pilot", updated_at="2026-06-06T10:45:00Z")

    assert display_message_from_cheer(cheer) == "Arrived in Amarr."


def test_display_message_from_combat_cheer_is_game_log_message_only():
    cheer = IntelPetCombatCheer(
        message="Guristas Wrecker has been destroyed",
        observed_at="2026-06-06T10:45:00Z",
        reported_at="2026-06-06T10:45:01Z",
    )

    assert display_message_from_combat_cheer(cheer) == "Guristas Wrecker has been destroyed"


def test_history_item_from_combat_cheer_records_local_context():
    cheer = IntelPetCombatCheer(
        message="Guristas Wrecker has been destroyed",
        observed_at="2026-06-06T10:45:00Z",
        reported_at="2026-06-06T10:45:01Z",
    )

    item = history_item_from_combat_cheer(cheer)

    assert item.title == "Kill cheer"
    assert item.detail == "Guristas Wrecker has been destroyed"
    assert "Local game log" in item.meta
    assert item.severity == "high"
    assert item.recorded_at == "2026-06-06T10:45:01Z"


def test_history_item_from_status_surfaces_watcher_failures():
    item = history_item_from_status("Watcher stopped: Chat log folder does not exist")

    assert item.title == "Pet watcher status"
    assert item.severity == "high"
    assert "Watcher stopped" in item.detail
    assert item.meta == "Local watcher"


def test_chat_log_watcher_alerts_on_new_appended_name_mention(tmp_path):
    chat_path = tmp_path / "Corp_20260606_193742_123.txt"
    chat_path.write_text(
        """
        ---------------------------------------------------------------

          Channel ID:      corp
          Channel Name:    Corp
          Listener:        Liet-kynes Ridderston
          Session started: 2026.06.06 19:37:42
        ---------------------------------------------------------------
""".lstrip(),
        encoding="utf-8",
    )
    engine = IntelPetEngine(IntelPetSettings(pilot_names=("Dandin",)))
    stop_event = threading.Event()
    alert_seen = threading.Event()
    sharing_seen = threading.Event()
    alerts = []

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            alerts.append(alert)
            alert_seen.set()

    watcher = threading.Thread(
        target=watch_chat_logs,
        kwargs={
            "log_dir": tmp_path,
            "channel_filter": ChannelFilter(("Corp", "Local")),
            "on_message": on_message,
            "poll_seconds": 0.05,
            "read_existing": False,
            "stop_event": stop_event,
            "log": lambda text: sharing_seen.set() if "Sharing channel" in text else None,
        },
        daemon=True,
    )
    watcher.start()
    try:
        assert sharing_seen.wait(2.0)
        with chat_path.open("a", encoding="utf-8") as handle:
            handle.write("\ufeff[ 2026.06.06 19:38:31 ] Liet-kynes Ridderston > Dandin\n")
        assert alert_seen.wait(2.0)
    finally:
        stop_event.set()
        watcher.join(timeout=2.0)

    assert alerts[-1].title == "Your name was mentioned in Corp"
    assert alerts[-1].keywords == ("name: Dandin",)


def test_native_window_drag_noops_off_windows(monkeypatch):
    monkeypatch.setattr(intel_pet_module.os, "name", "posix")

    assert not intel_pet_module.start_native_window_drag(object())


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
    assert {step[0] for step in KILL_SPRITE_STEPS} <= valid_indexes
    assert IDLE_SPRITE_SEQUENCE[-1] == 0
    assert ALERT_SPRITE_SEQUENCE[-1] == 0
    assert KILL_SPRITE_STEPS[-1][0] == 0


def test_load_sprite_frames_returns_empty_when_any_frame_is_missing(tmp_path):
    class FakeTk:
        class PhotoImage:
            def __init__(self, *, file, master):
                self.file = file
                self.master = master

    assert len(load_sprite_frames(FakeTk, object())) == SHIP_FRAME_COUNT
    assert load_sprite_frames(FakeTk, object(), paths=ship_sprite_frame_paths(tmp_path)) == ()
