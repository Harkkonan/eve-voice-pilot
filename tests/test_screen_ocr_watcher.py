from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import eve_voice_pilot.screen_ocr_watcher as ocr_watcher
from eve_voice_pilot.screen_ocr_watcher import (
    StableValueTracker,
    build_tesseract_config,
    normalize_ocr_value,
    parse_region,
)


def test_parse_region_accepts_pixel_box():
    region = parse_region("100, 200, 300, 40")
    assert region.left == 100
    assert region.top == 200
    assert region.width == 300
    assert region.height == 40
    assert region.as_mss_monitor() == {"left": 100, "top": 200, "width": 300, "height": 40}


def test_parse_region_allows_negative_monitor_coordinates():
    region = parse_region("-1200, 10, 200, 30")
    assert region.left == -1200
    assert region.top == 10


def test_normalize_ocr_value_can_extract_regex_capture():
    value = normalize_ocr_value("Cargo: 12,345 m3\n", pattern=r"Cargo:\s*([0-9,]+)")
    assert value == "12,345"


def test_normalize_ocr_value_collapses_lines_and_casefolds():
    value = normalize_ocr_value("ISK\nBalance")
    assert value == "isk balance"


def test_stable_value_tracker_sets_baseline_without_change():
    tracker = StableValueTracker(stable_samples=2)
    assert tracker.observe("jita") is None
    event = tracker.observe("jita")
    assert event is not None
    assert event.initial
    assert not event.changed
    assert event.value == "jita"


def test_stable_value_tracker_fires_after_stable_change():
    tracker = StableValueTracker(stable_samples=2)
    tracker.observe("jita")
    tracker.observe("jita")
    assert tracker.observe("amarr") is None
    event = tracker.observe("amarr")
    assert event is not None
    assert event.changed
    assert event.previous == "jita"
    assert event.value == "amarr"


def test_stable_value_tracker_ignores_empty_reads():
    tracker = StableValueTracker(stable_samples=1)
    assert tracker.observe("") is None
    assert tracker.current is None


def test_build_tesseract_config_includes_extra_flags():
    assert build_tesseract_config(7, "-c tessedit_char_whitelist=0123456789") == (
        "--psm 7 -c tessedit_char_whitelist=0123456789"
    )


def test_main_defaults_watch_mode_to_dry_run(monkeypatch):
    seen = {}

    def fake_run_watch(args):
        seen["dry_run"] = args.dry_run
        return 0

    monkeypatch.setattr(ocr_watcher, "run_watch", fake_run_watch)

    assert ocr_watcher.main(["--region", "1,2,3,4", "--hotkey", "CTRL+SHIFT+F9"]) == 0
    assert seen["dry_run"] is True


def test_main_requires_allow_live_send_for_hotkey_sends(monkeypatch):
    seen = {}

    def fake_run_watch(args):
        seen["dry_run"] = args.dry_run
        return 0

    monkeypatch.setattr(ocr_watcher, "run_watch", fake_run_watch)

    assert (
        ocr_watcher.main(["--region", "1,2,3,4", "--hotkey", "CTRL+SHIFT+F9", "--allow-live-send"])
        == 0
    )
    assert seen["dry_run"] is False
