from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.personal_core import (
    build_personal_core_payload,
    clean_personal_core_preferences,
)


CORE_SCOPES = (
    "esi-location.read_location.v1",
    "esi-assets.read_assets.v1",
    "esi-characters.read_blueprints.v1",
    "esi-skills.read_skills.v1",
    "esi-characters.read_standings.v1",
    "esi-wallet.read_character_wallet.v1",
)


def test_personal_core_recommends_industry_and_profit_audit_from_context():
    payload = build_personal_core_payload(
        preferences={
            "goal": "manufacture",
            "time_budget": "short",
            "risk": "safer",
            "preferred_hub": "amarr",
            "industry_home": "Dihra",
            "desired_ship": "Hawk",
            "isk_target": "250m",
        },
        character={"character_name": "Test Pilot"},
        location={"solar_system_name": "Dihra", "updated_at": "2026-06-13T00:00:00Z"},
        assets=[
            {"type_id": 34, "quantity": 1000, "location_id": 60008494},
            {"type_id": 35, "quantity": 2000, "location_id": 60008494},
        ],
        blueprints=[{"type_id": 123, "quantity": -1}, {"type_id": 456, "quantity": -2}],
        skills={"total_sp": 1234567, "skills": [{"skill_id": 3380, "trained_skill_level": 4}]},
        standings=[{"standing": 2.0}, {"standing": -0.5}],
        wallet_transactions=[
            {"is_buy": True, "unit_price": 10.0, "quantity": 10, "date": "2026-06-12T00:00:00Z"},
            {"is_buy": False, "unit_price": 20.0, "quantity": 10, "date": "2026-06-13T00:00:00Z"},
        ],
        wallet_journal=[{"ref_type": "transaction_tax", "amount": -5.0}],
        granted_scopes=CORE_SCOPES,
        generated_at="2026-06-13T01:00:00Z",
    )

    assert payload["ok"] is True
    assert payload["persistence"] == "none"
    assert payload["trust"]["raw_data_returned"] == "summaries-only"
    assert payload["preferences"]["isk_target"] == 250_000_000
    assert payload["context"]["assets"]["stack_count"] == 2
    assert payload["context"]["wallet"]["fee_rows_total_isk"] == 5.0
    assert {source["status"] for source in payload["sources"]} == {"ready"}
    keys = [rec["key"] for rec in payload["recommendations"]]
    assert "industry-core" in keys
    assert "profit-audit" in keys
    assert "access-token" not in str(payload)


def test_personal_core_missing_scopes_are_unknown_context_not_zeroes():
    payload = build_personal_core_payload(
        preferences={"goal": "what_now"},
        location={"solar_system_name": "Jita"},
        granted_scopes=("esi-location.read_location.v1",),
        generated_at="2026-06-13T01:00:00Z",
    )

    sources = {source["key"]: source for source in payload["sources"]}
    assert sources["location"]["status"] == "ready"
    assert sources["assets"]["status"] == "missing_scope"
    assert payload["context"]["assets"] is None
    assert payload["recommendations"][0]["key"] == "source-readiness"
    assert "unknown" in payload["recommendations"][0]["assumptions"][0].lower()


def test_personal_core_cleans_preferences_and_caps_isk_target():
    prefs = clean_personal_core_preferences(
        {
            "goal": "bad-goal",
            "time_budget": "bad-time",
            "risk": "aggressive",
            "hub": "rens",
            "industry_system": "  Amarr   VIII  ",
            "isk_target": "999999999999999999b",
        }
    )

    assert prefs["goal"] == "what_now"
    assert prefs["time_budget"] == "any"
    assert prefs["risk"] == "aggressive"
    assert prefs["preferred_hub"] == "rens"
    assert prefs["industry_home"] == "Amarr VIII"
    assert prefs["isk_target"] == 10_000_000_000_000
