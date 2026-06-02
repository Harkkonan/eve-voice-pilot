from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.screen_ocr_gui import (
    BUTTON_GUIDE,
    USER_SETTINGS_PATH,
    button_guide_lines,
    default_settings_dict,
    region_from_points,
    region_summary,
)
from eve_voice_pilot.screen_ocr_watcher import ScreenRegion


def test_default_ocr_gui_settings_start_in_dry_run():
    settings = default_settings_dict()
    assert settings["region"] == "100,200,260,40"
    assert settings["hotkey"] == "CTRL+SHIFT+F9"
    assert settings["dry_run"] is True
    assert settings["window_title_contains"] == "EVE"


def test_button_guide_explains_core_buttons():
    guide = "\n".join(button_guide_lines())
    assert "Read Once:" in guide
    assert "Show Region:" in guide
    assert "Preview Region:" in guide
    assert "Select Region:" in guide
    assert "Mouse Position:" in guide
    assert "Start Dry Run:" in guide
    assert "Start Live Watch:" in guide
    assert "Set Top Left:" in guide


def test_button_guide_mentions_saved_settings_file():
    assert any(USER_SETTINGS_PATH.name in description for _, description in BUTTON_GUIDE)


def test_region_summary_matches_region_field_format():
    assert region_summary(ScreenRegion(100, 200, 260, 40)) == "100,200,260,40"


def test_region_from_points_normalizes_drag_direction():
    assert region_from_points((300, 240), (100, 200)) == ScreenRegion(100, 200, 200, 40)


def test_region_from_points_keeps_minimum_size():
    assert region_from_points((100, 200), (100, 200)) == ScreenRegion(100, 200, 1, 1)
