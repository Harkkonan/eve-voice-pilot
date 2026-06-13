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
    assert payload["parsed"]["fit"]["hull"] == "Hawk"
    assert payload["parsed"]["item_count"] >= 4
    assert payload["parsed"]["ore_count"] == 1
    assert {"fit-handoff", "item-router", "ore-reprocessing", "manufacturing-plan"} <= recommendation_keys(payload)
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
