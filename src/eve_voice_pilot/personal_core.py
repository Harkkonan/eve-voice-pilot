from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from eve_voice_pilot.decision_engine import (
    DataSourceBadge,
    LearningSummary,
    Recommendation,
    checklist_item as shared_checklist_item,
    decision_action,
    discord_handoff as shared_discord_handoff,
)


PERSONAL_CORE_GOALS = frozenset(
    {
        "what_now",
        "manufacture",
        "gather",
        "reprocess",
        "haul",
        "buy_ship",
        "sell",
        "audit_profit",
        "explore",
        "learn",
    }
)
PERSONAL_CORE_TIME_BUDGETS = frozenset({"any", "short", "medium", "long"})
PERSONAL_CORE_RISK_MODES = frozenset({"balanced", "safer", "highsec", "lowsec", "wormhole", "aggressive"})
PERSONAL_CORE_HUBS = frozenset({"jita", "amarr", "dodixie", "hek", "rens"})

PERSONAL_CORE_SCOPE_BY_SOURCE = {
    "location": "esi-location.read_location.v1",
    "assets": "esi-assets.read_assets.v1",
    "blueprints": "esi-characters.read_blueprints.v1",
    "skills": "esi-skills.read_skills.v1",
    "standings": "esi-characters.read_standings.v1",
    "wallet": "esi-wallet.read_character_wallet.v1",
}

TRACKED_SKILLS = {
    3380: "Industry",
    3385: "Reprocessing",
    3386: "Mining",
    3388: "Advanced Industry",
    3389: "Reprocessing Efficiency",
    3443: "Marketing",
    3444: "Trade",
    3446: "Broker Relations",
    16622: "Accounting",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_core_text(value: Any, *, max_length: int = 120) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:max_length]


def parse_core_isk(value: Any) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    raw = str(value).strip().lower().replace(",", "").replace("isk", "").strip()
    multiplier = 1.0
    if raw.endswith("b"):
        multiplier = 1_000_000_000.0
        raw = raw[:-1]
    elif raw.endswith("m"):
        multiplier = 1_000_000.0
        raw = raw[:-1]
    elif raw.endswith("k"):
        multiplier = 1_000.0
        raw = raw[:-1]
    try:
        amount = float(raw) * multiplier
    except ValueError:
        return 0.0
    return max(0.0, min(amount, 10_000_000_000_000.0))


def clean_personal_core_preferences(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    goal = clean_core_text(data.get("goal"), max_length=40).lower().replace("-", "_") or "what_now"
    if goal == "auto":
        goal = "what_now"
    if goal not in PERSONAL_CORE_GOALS:
        goal = "what_now"
    time_budget = clean_core_text(data.get("time_budget"), max_length=40).lower().replace("-", "_") or "any"
    if time_budget not in PERSONAL_CORE_TIME_BUDGETS:
        time_budget = "any"
    risk = clean_core_text(data.get("risk"), max_length=40).lower().replace("-", "_") or "balanced"
    if risk not in PERSONAL_CORE_RISK_MODES:
        risk = "balanced"
    preferred_hub = clean_core_text(data.get("preferred_hub") or data.get("hub"), max_length=40).lower() or "jita"
    if preferred_hub not in PERSONAL_CORE_HUBS:
        preferred_hub = "jita"
    return {
        "goal": goal,
        "time_budget": time_budget,
        "risk": risk,
        "preferred_hub": preferred_hub,
        "industry_home": clean_core_text(data.get("industry_home") or data.get("industry_system"), max_length=80),
        "refine_home": clean_core_text(data.get("refine_home") or data.get("refine_system"), max_length=80),
        "mission_home": clean_core_text(data.get("mission_home") or data.get("quest_system"), max_length=80),
        "desired_ship": clean_core_text(data.get("desired_ship") or data.get("ship"), max_length=100),
        "isk_target": parse_core_isk(data.get("isk_target")),
        "corp_needs": clean_core_text(data.get("corp_needs"), max_length=240),
    }


def clean_optional_int(value: Any) -> int | None:
    try:
        clean = int(value)
    except (TypeError, ValueError):
        return None
    return clean if clean > 0 else None


def summarize_assets(assets: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    rows = [item for item in (assets or []) if isinstance(item, Mapping)]
    type_ids: set[int] = set()
    location_ids: set[int] = set()
    total_units = 0
    for item in rows:
        type_id = clean_optional_int(item.get("type_id"))
        location_id = clean_optional_int(item.get("location_id"))
        if type_id:
            type_ids.add(type_id)
        if location_id:
            location_ids.add(location_id)
        try:
            total_units += max(0, int(item.get("quantity") or 0))
        except (TypeError, ValueError):
            continue
    return {
        "available": True,
        "stack_count": len(rows),
        "unique_type_count": len(type_ids),
        "location_count": len(location_ids),
        "total_units": total_units,
    }


def summarize_blueprints(blueprints: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    rows = [item for item in (blueprints or []) if isinstance(item, Mapping)]
    originals = 0
    copies = 0
    type_ids: set[int] = set()
    for item in rows:
        type_id = clean_optional_int(item.get("type_id"))
        if type_id:
            type_ids.add(type_id)
        quantity = item.get("quantity")
        if quantity == -1:
            originals += 1
        elif quantity == -2:
            copies += 1
    return {
        "available": True,
        "total": len(rows),
        "unique_type_count": len(type_ids),
        "originals": originals,
        "copies": copies,
    }


def summarize_skills(skills: Mapping[str, Any] | None) -> dict[str, Any]:
    rows = [item for item in ((skills or {}).get("skills") or []) if isinstance(item, Mapping)]
    tracked = []
    for item in rows:
        skill_id = clean_optional_int(item.get("skill_id"))
        if skill_id not in TRACKED_SKILLS:
            continue
        try:
            level = int(item.get("trained_skill_level") or 0)
        except (TypeError, ValueError):
            level = 0
        tracked.append({"skill_id": skill_id, "name": TRACKED_SKILLS[skill_id], "level": max(0, min(level, 5))})
    try:
        total_sp = int((skills or {}).get("total_sp") or 0)
    except (TypeError, ValueError):
        total_sp = 0
    return {
        "available": True,
        "trained_skill_count": len(rows),
        "total_sp": max(0, total_sp),
        "tracked": sorted(tracked, key=lambda item: item["name"]),
    }


def summarize_standings(standings: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    rows = [item for item in (standings or []) if isinstance(item, Mapping)]
    values: list[float] = []
    for item in rows:
        try:
            values.append(float(item.get("standing")))
        except (TypeError, ValueError):
            continue
    return {
        "available": True,
        "count": len(rows),
        "positive_count": sum(1 for value in values if value > 0),
        "reprocessing_candidate_count": sum(1 for value in values if value >= 1.5),
        "negative_count": sum(1 for value in values if value < 0),
        "best": max(values) if values else None,
        "worst": min(values) if values else None,
    }


def summarize_wallet(
    transactions: Iterable[Mapping[str, Any]] | None,
    journal_entries: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    tx_rows = [item for item in (transactions or []) if isinstance(item, Mapping)]
    journal_rows = [item for item in (journal_entries or []) if isinstance(item, Mapping)]
    buys = [item for item in tx_rows if bool(item.get("is_buy"))]
    sells = [item for item in tx_rows if not bool(item.get("is_buy"))]

    def total(rows: Iterable[Mapping[str, Any]]) -> float:
        amount = 0.0
        for item in rows:
            try:
                amount += float(item.get("unit_price") or 0.0) * float(item.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
        return amount

    fee_total = 0.0
    for item in journal_rows:
        ref_type = str(item.get("ref_type") or "").lower()
        if "tax" not in ref_type and "fee" not in ref_type:
            continue
        try:
            fee_total += abs(float(item.get("amount") or 0.0))
        except (TypeError, ValueError):
            continue
    latest = ""
    for item in tx_rows:
        date_value = str(item.get("date") or "")
        if date_value > latest:
            latest = date_value
    return {
        "available": True,
        "transaction_count": len(tx_rows),
        "journal_entry_count": len(journal_rows),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "gross_bought_isk": total(buys),
        "gross_sold_isk": total(sells),
        "fee_rows_total_isk": fee_total,
        "latest_transaction_date": latest,
    }


def source_card(
    *,
    key: str,
    label: str,
    scope: str,
    granted_scopes: set[str],
    summary: Mapping[str, Any] | None,
    error: str = "",
    generated_at: str,
) -> dict[str, Any]:
    if error:
        status = "error"
        detail = error
    elif summary is not None:
        status = "ready"
        detail = "Summarized for this response."
    elif scope not in granted_scopes:
        status = "missing_scope"
        detail = "Reconnect ESI with this read-only scope if you want this signal included."
    else:
        status = "empty"
        detail = "ESI returned no usable rows for this signal."
    return DataSourceBadge(
        key=key,
        label=label,
        status=status,
        posture="read-only ESI" if scope.startswith("esi-") else "manual setting",
        freshness=generated_at if status == "ready" else "",
        persistence="summaries only",
        scope=scope,
        detail=detail,
    ).to_dict()


def checklist_item(label: str, value: Any, detail: str = "") -> dict[str, Any]:
    return shared_checklist_item(label, value, detail)


def action(
    label: str,
    href: str,
    detail: str,
    *,
    target_tab: str = "",
    prefill: Mapping[str, Any] | None = None,
    discord_handoff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return decision_action(
        label,
        href,
        detail,
        target_tab=target_tab,
        prefill=prefill,
        discord_handoff=discord_handoff,
    )


def discord_handoff(
    *,
    workflow_key: str,
    destination_hint: str,
    destination_label: str,
    post_type: str,
    category: str,
    title: str,
    item_name: str = "",
    quantity: str = "",
    price_text: str = "",
    location: str = "",
    contact: str = "",
    link_url: str = "",
    details: str = "",
) -> dict[str, Any]:
    return shared_discord_handoff(
        workflow_key=workflow_key,
        destination_hint=destination_hint,
        destination_label=destination_label,
        post_type=post_type,
        category=category,
        title=title,
        item_name=item_name,
        quantity=quantity,
        price_text=price_text,
        location=location,
        contact=contact,
        link_url=link_url,
        details=details,
        source="personal-core",
    )


def core_recommendation(
    key: str,
    title: str,
    summary: str,
    *,
    priority: int,
    confidence: str,
    checklist: Iterable[Mapping[str, Any]],
    actions: Iterable[Mapping[str, Any]],
    assumptions: Iterable[str],
    source_keys: Iterable[str] = (),
    risk_level: str = "medium",
    missing_data: Iterable[str] = (),
    learning_summary: Mapping[str, Any] | None = None,
    discord_handoff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return Recommendation(
        key=key,
        title=title,
        plain_reason=summary,
        priority=priority,
        confidence=confidence,
        risk_level=risk_level,
        assumptions=assumptions,
        missing_data=missing_data,
        source_keys=source_keys,
        manual_checklist=checklist,
        next_actions=actions,
        learning_summary=dict(learning_summary) if learning_summary else None,
        discord_handoff=dict(discord_handoff) if discord_handoff else None,
    ).to_dict()


def build_personal_core_recommendations(
    *,
    preferences: Mapping[str, Any],
    context: Mapping[str, Any],
    sources: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    goal = str(preferences.get("goal") or "what_now")
    time_budget = str(preferences.get("time_budget") or "any")
    risk = str(preferences.get("risk") or "balanced")
    preferred_hub = str(preferences.get("preferred_hub") or "jita").title()
    preferred_hub_key = str(preferences.get("preferred_hub") or "jita").lower()
    industry_home = str(preferences.get("industry_home") or "")
    refine_home = str(preferences.get("refine_home") or "")
    mission_home = str(preferences.get("mission_home") or "")
    desired_ship = str(preferences.get("desired_ship") or "")
    corp_needs = str(preferences.get("corp_needs") or "")
    source_rows = list(sources)
    missing = [row for row in source_rows if row.get("status") == "missing_scope"]
    errored = [row for row in source_rows if row.get("status") == "error"]
    assets = context.get("assets") or {}
    blueprints = context.get("blueprints") or {}
    wallet = context.get("wallet") or {}
    standings = context.get("standings") or {}
    skills = context.get("skills") or {}
    recs: list[dict[str, Any]] = []

    if missing or errored:
        recs.append(
            core_recommendation(
                "source-readiness",
                "Fix the context gaps before trusting a full answer",
                "The core can still give advice, but missing or failed ESI reads reduce confidence. Start by reconnecting only for the read-only scopes you actually want this workflow to use.",
                priority=92 if goal == "what_now" else 70,
                confidence="medium" if missing else "low",
                checklist=[
                    checklist_item("Missing scopes", len(missing), "These are visible in the source cards; they are not silently assumed."),
                    checklist_item("Read errors", len(errored), "Retry after ESI or network issues clear."),
                    checklist_item("Token storage", "server memory only", "Access tokens are not written to a local token file by this app."),
                ],
                actions=[
                    action("Reconnect ESI", "/flight/login", "Refresh the session with the core read-only scopes.", target_tab="flight"),
                    action("Use Intake without ESI", "#intake", "Paste a concrete artifact and get a manual checklist while ESI context is incomplete.", target_tab="intake"),
                ],
                assumptions=[
                    "Missing scopes are treated as unknown, not as zero assets, zero wallet activity, or no standings.",
                    "No recommendation places orders, moves assets, or controls the EVE client.",
                ],
                source_keys=[str(row.get("key") or "") for row in [*missing, *errored]],
                risk_level="medium",
                missing_data=[str(row.get("label") or row.get("key") or "source") for row in missing],
            )
        )

    if goal in {"manufacture", "what_now"} or blueprints.get("total"):
        priority = 88 if goal == "manufacture" else 76
        recs.append(
            core_recommendation(
                "industry-core",
                "Check what you can build from owned blueprints and stock",
                "Your industry answer depends on owned blueprints, owned materials, location, skills, and public buyer demand. Use the Industry Library when you want the most grounded manufacturing next step.",
                priority=priority,
                confidence="high" if blueprints.get("total") and assets.get("stack_count") else "medium",
                checklist=[
                    checklist_item("Blueprint rows", blueprints.get("total", "unknown"), "More owned blueprints give the planner more build candidates."),
                    checklist_item("Asset stacks", assets.get("stack_count", "unknown"), "Owned materials reduce cash needed but still have opportunity value."),
                    checklist_item("Industry home", preferences.get("industry_home") or "current or manual", "Use this as the planned build-location assumption."),
                ],
                actions=[
                    action(
                        "Open Industry Library",
                        "#industry",
                        "Rank blueprint, asset, recipe, and buyer context.",
                        target_tab="industry",
                        prefill={"industry_home": industry_home, "source": "personal-core"},
                    ),
                    action(
                        "Open Trade P&L",
                        "#trade-pnl",
                        "After selling output, compare expected against realized wallet result.",
                        target_tab="trade-pnl",
                        prefill={"window_hours": "720", "lens": "inventory", "consideration_rule": "materials"},
                    ),
                ],
                assumptions=[
                    "Facility tax, rigs, system cost index, and structure access still need manual verification.",
                    f"Market hub preference is {preferred_hub}; verify prices before committing materials.",
                ],
                source_keys=("blueprints", "assets", "skills", "manual-preferences"),
                risk_level="medium",
                missing_data=("Facility tax, rigs, system cost index, structure access, and live buyer demand.",),
            )
        )

    if goal in {"buy_ship", "explore"} or desired_ship:
        ship_text = desired_ship or ""
        recs.append(
            core_recommendation(
                "ship-goal",
                "Turn the desired ship into a shopping and readiness checklist",
                "Start from the ship or fit, then split the work into skill readiness, hull/modules, ammo, route, and expected cost. This keeps a ship goal from becoming a vague shopping trip.",
                priority=90 if goal == "buy_ship" or desired_ship else 62,
                confidence="medium",
                checklist=[
                    checklist_item("Target", desired_ship or "ship or fit not set", "Paste the fit or type the hull name to tighten the plan."),
                    checklist_item("Wallet target", round(float(preferences.get("isk_target") or 0)), "Use this as the spend or earning target if set."),
                    checklist_item("Risk stance", risk, "Safer plans should favor known hubs, shorter routes, and lower exposure."),
                ],
                actions=[
                    action(
                        "Open Intake + Goals",
                        "#intake",
                        "Paste a fit, cargo list, or contract and route it to the right workflow.",
                        target_tab="intake",
                        prefill={
                            "goal": "buy_ship" if goal == "buy_ship" else "explore",
                            "text": ship_text,
                            "preferred_hub": preferred_hub_key,
                            "time_budget": time_budget,
                        },
                    ),
                    action(
                        "Open Bulk Appraisal",
                        "#appraisal",
                        "Price the hull, modules, ammo, and cargo list.",
                        target_tab="appraisal",
                        prefill={"bulk_appraisal_text": ship_text, "hub": preferred_hub_key, "source": "personal-core-ship"} if ship_text else {},
                    ),
                    action(
                        "Open Hauler Routes",
                        "#hauling",
                        "Plan manual pickup and staging if the shopping list is scattered.",
                        target_tab="hauling",
                        prefill={
                            "pasted_items": ship_text,
                            "destination": mission_home or preferred_hub,
                            "source": "personal-core-ship",
                        }
                        if ship_text
                        else {"destination": mission_home or preferred_hub, "source": "personal-core-ship"},
                    ),
                ],
                assumptions=[
                    "The app does not fit the ship in EVE or buy the items.",
                    "Skill readiness is advisory until exact fit parsing and skill-plan matching are added.",
                ],
                source_keys=("manual-preferences", "skills", "wallet", "location"),
                risk_level="medium",
                missing_data=("Exact fit requirements and current sell orders.",),
            )
        )

    if goal in {"gather", "reprocess"}:
        recs.append(
            core_recommendation(
                "resource-loop",
                "Choose gather, refine, or sell before undocking",
                "For resource work, the profitable choice is often not mining longer; it is knowing whether to refine, haul, or sell the input directly with your standings and skills.",
                priority=87,
                confidence="high" if skills.get("tracked") or standings.get("count") else "medium",
                checklist=[
                    checklist_item("Refine home", preferences.get("refine_home") or "current or manual", "Facility yield and standings change net output."),
                    checklist_item("Standing candidates", standings.get("reprocessing_candidate_count", "unknown"), "NPC standings over 1.5 can matter for station processing fees."),
                    checklist_item("Time budget", time_budget, "Short sessions should favor low-friction pickup, refining, or selling."),
                ],
                actions=[
                    action(
                        "Open Reprocessing",
                        "#reprocessing",
                        "Compare ore input, skills, standings, facility yield, and market output.",
                        target_tab="reprocessing",
                        prefill={"refine_home": refine_home, "source": "personal-core-resource"},
                    ),
                    action("Open Mining Yield", "#mining-yield", "Summarize opt-in mining ledger rows and manual session timing.", target_tab="mining-yield"),
                    action(
                        "Open Bulk Appraisal",
                        "#appraisal",
                        "Compare direct ore sale value before refining.",
                        target_tab="appraisal",
                        prefill={"hub": preferred_hub_key, "source": "personal-core-resource"},
                    ),
                ],
                assumptions=[
                    "Mining ledger is optional and cached; it is not live module or cycle telemetry.",
                    "Structure rigs and private taxes still need manual confirmation.",
                ],
                source_keys=("skills", "standings", "location", "manual-preferences"),
                risk_level="medium",
                missing_data=("Facility yield, structure taxes, current ore and mineral prices.",),
            )
        )

    if goal in {"audit_profit", "sell"} or wallet.get("transaction_count"):
        recs.append(
            core_recommendation(
                "profit-audit",
                "Audit expected profit against wallet reality",
                "Use recent wallet transactions and fee rows to see whether the plan actually made the money it claimed. This directly supports the accuracy-learning loop.",
                priority=91 if goal == "audit_profit" else 79,
                confidence="high" if wallet.get("transaction_count") else "medium",
                checklist=[
                    checklist_item("Transactions", wallet.get("transaction_count", "unknown"), "Recent market transaction rows visible through ESI."),
                    checklist_item("Sell rows", wallet.get("sell_count", "unknown"), "Realized revenue starts here."),
                    checklist_item("Fee rows", round(float(wallet.get("fee_rows_total_isk") or 0)), "Taxes and broker fees often explain estimate drift."),
                ],
                actions=[
                    action(
                        "Open Trade P&L",
                        "#trade-pnl",
                        "Compare buys, sells, fees, open stock, and saved expectations.",
                        target_tab="trade-pnl",
                        prefill={"window_hours": "720", "lens": "inventory", "consideration_rule": "all", "show_matches": True},
                    ),
                    action(
                        "Open Investment Portfolio",
                        "#acquisition",
                        "Create future plans with expected-vs-realized tracking columns.",
                        target_tab="acquisition",
                        prefill={"destination": preferred_hub, "source": "personal-core-profit"},
                    ),
                ],
                assumptions=[
                    "Only wallet history available through ESI can be matched.",
                    "Open inventory value is an estimate until sold.",
                ],
                source_keys=("wallet", "local-corp-market-sqlite", "manual-preferences"),
                risk_level="low",
                learning_summary=LearningSummary(
                    source="local-corp-market-sqlite",
                    status="available-after-trade-pnl-refresh",
                    detail="Trade P&L stores expected-vs-actual evidence from saved portfolio plans, wallet matches, fees, and open stock.",
                    signal_count=0,
                    evidence_item_count=0,
                ).to_dict(),
            )
        )

    if goal == "haul" or (assets.get("location_count") or 0) > 1:
        recs.append(
            core_recommendation(
                "asset-logistics",
                "Turn scattered assets into a manual hauling plan",
                "If assets are spread across locations, the next useful action is often a route and load decision instead of another market scan.",
                priority=84 if goal == "haul" else 68,
                confidence="medium",
                checklist=[
                    checklist_item("Asset locations", assets.get("location_count", "unknown"), "More locations means more staging friction."),
                    checklist_item("Asset stacks", assets.get("stack_count", "unknown"), "Use managed containers for clearer corp handoff later."),
                    checklist_item("Preferred hub", preferred_hub, "Use this as the default destination unless corp needs say otherwise."),
                ],
                actions=[
                    action("Open Trade Asset Ledger", "#asset-ledger", "Find managed containers and copy item names for handoff.", target_tab="asset-ledger"),
                    action(
                        "Open Hauler Routes",
                        "#hauling",
                        "Plan pickup, route, cargo, and sell destination manually.",
                        target_tab="hauling",
                        prefill={"destination": preferred_hub, "source": "personal-core-assets"},
                    ),
                ],
                assumptions=[
                    "Docking access and structure access are not guaranteed by ESI asset rows.",
                    "The app does not move, contract, or repackage assets.",
                ],
                source_keys=("assets", "location", "manual-preferences"),
                risk_level="medium",
                missing_data=("Docking access, current route safety, cargo volume, and collateral tolerance.",),
            )
        )

    if corp_needs:
        need_handoff = discord_handoff(
            workflow_key="personal-core-corp-need",
            destination_hint="portfolio market industry hauling",
            destination_label="Corp Needs",
            post_type="wtb",
            category="general",
            title="Corp need before public market orders",
            item_name=corp_needs[:120],
            price_text="Manual basis, e.g. 10% under Jita before public buy orders",
            location=preferred_hub,
            details=(
                f"Personal Core corp need: {corp_needs}\n"
                "Ask corp members first, then place public market orders manually if nobody can fill it. "
                "Verify price basis, quantity, location, and delivery terms before sending."
            ),
        )
        recs.append(
            core_recommendation(
                "corp-need-handoff",
                "Convert the corp need into a Discord-ready manual ask",
                "When corp needs are explicit, the best next step may be coordination: ask members before placing public orders, then use the market board or Discord channel that fits the function.",
                priority=82,
                confidence="medium",
                checklist=[
                    checklist_item("Corp need", corp_needs, "Use this as the top-line ask."),
                    checklist_item("Discount stance", "manual", "Example: ask corp for 10% under Jita before posting public buy orders."),
                    checklist_item("Channel", "function-specific", "Portfolio, hauling, market, or industry output should route to the relevant Discord destination."),
                ],
                actions=[
                    action(
                        "Open Market Posts",
                        "#market",
                        "Draft a WTB/WTS or coordination message for the relevant Discord route.",
                        target_tab="market",
                        discord_handoff=need_handoff,
                    ),
                    action(
                        "Open Investment Portfolio",
                        "#acquisition",
                        "Turn the need into manual buy-order candidates if corp cannot fill it.",
                        target_tab="acquisition",
                        prefill={"pasted_items": corp_needs, "destination": preferred_hub, "source": "personal-core-corp-need"},
                        discord_handoff=need_handoff,
                    ),
                ],
                assumptions=[
                    "Discord sends remain explicit manual actions.",
                    "The app does not create in-game contracts or market orders.",
                ],
                source_keys=("manual-preferences",),
                risk_level="low",
                discord_handoff=need_handoff,
            )
        )

    if not recs:
        recs.append(
            core_recommendation(
                "start-with-intake",
                "Start with one concrete EVE artifact",
                "The core does not have enough context to choose a specialized workflow. Paste a fit, cargo list, wallet rows, ore list, contract text, or BOM into Intake + Goals.",
                priority=60,
                confidence="low",
                checklist=[
                    checklist_item("Best paste", "fit / cargo / wallet / ore / BOM", "Specific clipboard formats produce better manual steps."),
                    checklist_item("Goal", goal, "The selected goal steers the first workflow."),
                ],
                actions=[
                    action(
                        "Open Intake + Goals",
                        "#intake",
                        "Route the next paste into a checklist.",
                        target_tab="intake",
                        prefill={"goal": goal, "preferred_hub": preferred_hub_key, "time_budget": time_budget},
                    )
                ],
                assumptions=["No ESI data is treated as proof that no opportunity exists."],
                source_keys=("manual-preferences",),
                risk_level="low",
                missing_data=("Concrete EVE artifact such as a fit, cargo list, wallet rows, ore list, contract, or BOM.",),
            )
        )
    recs.sort(key=lambda item: int(item.get("priority") or 0), reverse=True)
    return recs[:5]


def build_personal_core_share_text(payload: Mapping[str, Any]) -> str:
    preferences = payload.get("preferences") if isinstance(payload.get("preferences"), Mapping) else {}
    recs = payload.get("recommendations") if isinstance(payload.get("recommendations"), list) else []
    lines = [
        "EVE Personal Core recommendation",
        f"Goal: {preferences.get('goal', 'what_now')} | Time: {preferences.get('time_budget', 'any')} | Risk: {preferences.get('risk', 'balanced')}",
    ]
    for rec in recs[:3]:
        if isinstance(rec, Mapping):
            lines.append(f"- {rec.get('title', 'Recommendation')}: {rec.get('summary', '')}")
    lines.append("Manual only. Verify prices, route, skills, wallet, contracts, and game state in EVE before acting.")
    return "\n".join(lines)


def build_personal_core_payload(
    *,
    preferences: Mapping[str, Any] | None = None,
    character: Mapping[str, Any] | None = None,
    location: Mapping[str, Any] | None = None,
    assets: Iterable[Mapping[str, Any]] | None = None,
    blueprints: Iterable[Mapping[str, Any]] | None = None,
    skills: Mapping[str, Any] | None = None,
    standings: Iterable[Mapping[str, Any]] | None = None,
    wallet_transactions: Iterable[Mapping[str, Any]] | None = None,
    wallet_journal: Iterable[Mapping[str, Any]] | None = None,
    granted_scopes: Iterable[str] = (),
    fetch_errors: Mapping[str, str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or utc_now_iso()
    clean_preferences = clean_personal_core_preferences(preferences)
    errors = {str(key): str(value) for key, value in (fetch_errors or {}).items() if str(value or "").strip()}
    context: dict[str, Any] = {
        "location": dict(location) if isinstance(location, Mapping) else None,
        "assets": summarize_assets(assets) if assets is not None else None,
        "blueprints": summarize_blueprints(blueprints) if blueprints is not None else None,
        "skills": summarize_skills(skills) if skills is not None else None,
        "standings": summarize_standings(standings) if standings is not None else None,
        "wallet": summarize_wallet(wallet_transactions, wallet_journal) if wallet_transactions is not None or wallet_journal is not None else None,
    }
    source_labels = {
        "location": "Current location",
        "assets": "Owned assets",
        "blueprints": "Owned blueprints",
        "skills": "Character skills",
        "standings": "NPC standings",
        "wallet": "Wallet activity",
    }
    granted = {str(scope) for scope in granted_scopes if str(scope or "").strip()}
    sources = [
        source_card(
            key=key,
            label=source_labels[key],
            scope=scope,
            granted_scopes=granted,
            summary=context.get(key) if isinstance(context.get(key), Mapping) else None,
            error=errors.get(key, ""),
            generated_at=generated,
        )
        for key, scope in PERSONAL_CORE_SCOPE_BY_SOURCE.items()
    ]
    sources.append(
        DataSourceBadge(
            key="manual-preferences",
            label="Local preferences",
            status="ready",
            posture="manual setting",
            freshness=generated,
            persistence="browser localStorage only",
            scope="manual",
            detail="Goal, risk, hub, homes, desired ship, ISK target, and corp-needs text are user-entered settings.",
        ).to_dict()
    )
    recommendations = build_personal_core_recommendations(
        preferences=clean_preferences,
        context=context,
        sources=sources,
    )
    payload = {
        "ok": True,
        "generated_at": generated,
        "source": "personal-core",
        "persistence": "none",
        "character": dict(character or {}),
        "preferences": clean_preferences,
        "context": context,
        "sources": sources,
        "trust": {
            "esi_scopes": sorted(granted.intersection(PERSONAL_CORE_SCOPE_BY_SOURCE.values())),
            "token_storage": "server-memory-only",
            "server_persistence": "none",
            "browser_persistence": "local preferences only",
            "raw_data_returned": "summaries-only",
        },
        "beginner_translation": (
            "This is a decision checklist, not an autopilot. It combines your selected goal with authorized ESI summaries, "
            "shows which sources were fresh or missing, explains the assumptions, and points you to the next manual workflow."
        ),
        "warnings": [
            "No recommendation buys, sells, contracts, moves assets, sends mail, warps, clicks, or presses keys.",
            "Missing ESI scopes mean unknown context, not zero assets, zero standings, or zero wallet activity.",
            "Verify live prices, taxes, route safety, docking access, and in-game confirmation windows before acting.",
        ],
        "recommendations": recommendations,
    }
    payload["share_text"] = build_personal_core_share_text(payload)
    return payload
