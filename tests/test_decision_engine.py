from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.decision_engine import (  # noqa: E402
    DataSourceBadge,
    LearningSummary,
    ParsedInput,
    Recommendation,
    checklist_item,
    decision_action,
    discord_handoff,
    external_link,
)


def test_recommendation_serializes_shared_and_legacy_aliases():
    rec = Recommendation(
        key="wallet-profit-audit",
        title="Audit wallet rows",
        plain_reason="Compare actual wallet results against the plan.",
        priority=110,
        confidence="high",
        risk_level="medium",
        assumptions=["Fees matter"],
        missing_data=["Open stock value"],
        source_keys=["wallet", "local-corp-market-sqlite"],
        manual_checklist=[checklist_item("Fee rows", 2, "Broker plus sales tax.")],
        next_actions=[
            decision_action(
                "Open Trade P&L",
                "#trade-pnl",
                "Refresh wallet rows.",
                target_tab="trade-pnl",
                prefill={"window_hours": "720", "lens": "inventory"},
            )
        ],
        links=[external_link("EVE University trading", "https://wiki.eveuniversity.org/Trading")],
        discord_handoff=discord_handoff(
            workflow_key="trade-pnl",
            destination_hint="accounting",
            destination_label="Trade P&L",
            post_type="announcement",
            category="general",
            title="Profit audit ready",
        ),
        learning_summary=LearningSummary(
            source="local-corp-market-sqlite",
            status="available",
            detail="Existing Trade P&L learning can explain estimate drift.",
            signal_count=2,
            evidence_item_count=3,
            signal_counts={"fees_higher_than_expected": 1},
        ).to_dict(),
    ).to_dict()

    assert rec["priority"] == 100
    assert rec["plain_reason"] == "Compare actual wallet results against the plan."
    assert rec["explanation"] == rec["plain_reason"]
    assert rec["summary"] == rec["plain_reason"]
    assert rec["source_keys"] == ["wallet", "local-corp-market-sqlite"]
    assert rec["data_source_keys"] == rec["source_keys"]
    assert rec["manual_checklist"][0]["value"] == "2"
    assert rec["next_actions"][0]["target_tab"] == "trade-pnl"
    assert rec["next_actions"][0]["prefill"]["window_hours"] == "720"
    assert rec["discord_handoff"]["workflow_key"] == "trade-pnl"
    assert rec["links"][0]["url"].startswith("https://")
    assert rec["learning_summary"]["signal_count"] == 2


def test_data_source_and_parsed_input_serialization():
    source = DataSourceBadge(
        key="wallet",
        label="Wallet activity",
        status="ready",
        posture="ESI read",
        freshness="2026-06-13T01:00:00Z",
        persistence="summaries only",
        scope="esi-wallet.read_character_wallet.v1",
        detail="Summarized for this response.",
    ).to_dict()
    parsed = ParsedInput(
        primary_kind="wallet",
        label="Wallet rows",
        confidence=91,
        signals=[{"kind": "wallet", "strength": 91}],
        line_count=3,
        raw_line_count=4,
        nonempty_line_count=3,
        character_count=120,
        summary={"row_count": 2},
    ).to_dict()

    assert source["key"] == "wallet"
    assert source["scope"] == "esi-wallet.read_character_wallet.v1"
    assert parsed["detected_type"] == "wallet"
    assert parsed["stored"] is False
    assert parsed["summary"]["row_count"] == 2
