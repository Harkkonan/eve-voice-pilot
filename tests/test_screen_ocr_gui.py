from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.screen_ocr_gui import (
    BUTTON_GUIDE,
    USER_SETTINGS_PATH,
    button_guide_lines,
    default_settings_dict,
)


def test_default_ocr_gui_settings_start_in_dry_run():
    settings = default_settings_dict()
    assert settings["region"] == "100,200,260,40"
    assert settings["hotkey"] == "CTRL+SHIFT+F9"
    assert settings["dry_run"] is True
    assert settings["window_title_contains"] == "EVE"


def test_button_guide_explains_core_buttons():
    guide = "\n".join(button_guide_lines())
    assert "Read Once:" in guide
    assert "Start Dry Run:" in guide
    assert "Start Live Watch:" in guide
    assert "Set Top Left:" in guide


def test_button_guide_mentions_saved_settings_file():
    assert any(USER_SETTINGS_PATH.name in description for _, description in BUTTON_GUIDE)
