from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.corp_intel import ChatMessage
from eve_voice_pilot.intel_pet import (
    IntelPetEngine,
    IntelPetSettings,
    load_settings,
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
