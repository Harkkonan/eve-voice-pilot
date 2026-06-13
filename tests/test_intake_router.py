from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.intake_router import build_intake_router_payload


def recommendation_keys(payload: dict) -> set[str]:
    return {str(item.get("key")) for item in payload.get("recommendations") or []}


def test_intake_router_classifies_fit_items_and_ore_without_esi():
    payload = build_intake_router_payload(
        raw_text="""[Hawk, Abyss runner]
Ballistic Control System II
1MN Afterburner II

Scourge Rage Rocket x4772
Veldspar 5000
Merlin Blueprint (Copy) 1""",
        goal="buy_ship",
        preferred_hub="amarr",
    )

    assert payload["ok"] is True
    assert payload["source"] == "local-intake-router"
    assert payload["persistence"] == "none"
    assert payload["trust"]["esi_scopes"] == []
    assert payload["trust"]["token_storage"] == "none"
    assert payload["classification"]["primary_kind"] == "fit"
    assert payload["parsed_input"]["detected_type"] == "fit"
    assert payload["parsed_input"]["stored"] is False
    assert payload["parsed"]["fit"]["hull"] == "Hawk"
    assert payload["parsed"]["item_count"] >= 4
    assert payload["parsed"]["ore_count"] == 1
    assert {"fit-handoff", "item-router", "ore-reprocessing", "manufacturing-plan"} <= recommendation_keys(payload)
    assert payload["recommendations"][0]["plain_reason"]
    assert payload["recommendations"][0]["data_source_keys"] == payload["recommendations"][0]["source_keys"]
    assert "Raw paste" not in payload["share_text"]


def test_intake_router_routes_wallet_rows_to_profit_audit():
    payload = build_intake_router_payload(
        raw_text="""Date	Type	Item	Quantity	Unit price	Total
2026-06-13 12:00	Sell	Tritanium	1000	5 ISK	5000 ISK
2026-06-13 12:03	Broker Fee	Tritanium	1	100 ISK	100 ISK""",
        goal="audit_profit",
    )

    assert payload["classification"]["primary_kind"] == "wallet"
    assert "wallet-profit-audit" in recommendation_keys(payload)
    assert payload["recommendations"][0]["key"] == "wallet-profit-audit"
    assert payload["recommendations"][0]["risk_level"] == "low"
    assert payload["recommendations"][0]["learning_summary"]["source"] == "local-corp-market-sqlite"
    rendered = json.dumps(payload)
    assert "Trade P&L" in rendered
    assert "broker + tax" in rendered


def test_intake_router_keeps_combat_context_lightweight():
    payload = build_intake_router_payload(
        raw_text="https://zkillboard.com/kill/123456789/\nName\tType\tDistance\nTarget\tCatalyst\t8 km",
        goal="what_now",
    )

    assert payload["classification"]["primary_kind"] in {"dscan", "killmail"}
    assert "risk-context" in recommendation_keys(payload)
    rendered = json.dumps(payload)
    assert "does not create shared intel feeds" in rendered
    assert "DSCAN-ICU" in rendered
    assert "not upload D-scan/local text" in rendered


def test_intake_router_routes_contracts_with_manual_risk_checklist():
    payload = build_intake_router_payload(
        raw_text="""Contract: Item Exchange
Issuer: Example Pilot
Collateral: 125,000,000 ISK
Reward: 8,000,000 ISK
Expires: 2026-06-20
Tritanium 100000""",
        goal="what_now",
    )

    assert payload["classification"]["primary_kind"] == "contract"
    rec = next(item for item in payload["recommendations"] if item["key"] == "contract-review")
    assert rec["risk_level"] == "high"
    assert rec["missing_data"]
    assert "contracts" in json.dumps(rec).lower()


def test_intake_router_unknown_text_keeps_general_triage_shape():
    payload = build_intake_router_payload(
        raw_text="I want to do something useful tonight but I do not know what to paste yet.",
        goal="learn",
    )

    assert payload["classification"]["primary_kind"] == "unknown"
    assert payload["parsed_input"]["detected_type"] == "unknown"
    assert payload["recommendations"][0]["key"] == "general-triage"
    assert payload["recommendations"][0]["risk_level"] == "low"
