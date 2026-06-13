from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable

from eve_voice_pilot.corp_market_bulk_appraisal import FIT_HEADER_RE, parse_bulk_appraisal_text


MAX_INTAKE_TEXT_LENGTH = 120_000
INTAKE_GOALS = frozenset(
    {
        "auto",
        "what_now",
        "sell",
        "buy_ship",
        "manufacture",
        "gather",
        "reprocess",
        "haul",
        "explore",
        "audit_profit",
        "learn",
    }
)
INTAKE_TIME_BUDGETS = frozenset({"any", "short", "medium", "long"})
INTAKE_HUBS = frozenset({"jita", "amarr", "dodixie", "hek", "rens"})
ORE_NAME_RE = re.compile(
    r"\b("
    r"veldspar|scordite|pyroxeres|plagioclase|omber|kernite|jaspet|hemorphite|hedbergite|gneiss|dark ochre|"
    r"spodumain|crokite|bistot|arkonor|mercoxit|bezdnacine|rakovene|talassonite|ytirium|mordunium|"
    r"moon ore|bitumens|coesite|sylvite|zeolites|cobaltite|euxenite|scheelite|titanite|chromite|otavite|"
    r"sperrylite|vanadinite|zircon|pollucite|cinnabar|euxenite|loparite|monazite|xenotime"
    r")\b",
    re.IGNORECASE,
)
KILLMAIL_URL_RE = re.compile(r"https?://(?:www\.)?zkillboard\.com/kill/(?P<kill_id>\d+)/?", re.IGNORECASE)
DSCAN_DISTANCE_RE = re.compile(r"\b(?:km|m|au)\b", re.IGNORECASE)
MONEY_RE = re.compile(r"\b(?:isk|price|collateral|reward|broker|tax|fee|total)\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_intake_text(raw_text: Any) -> str:
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_INTAKE_TEXT_LENGTH:
        raise ValueError(f"Paste text must be {MAX_INTAKE_TEXT_LENGTH:,} characters or less.")
    return text


def clean_intake_goal(value: Any) -> str:
    goal = re.sub(r"[^a-z0-9_]+", "_", str(value or "auto").strip().casefold()).strip("_")
    return goal if goal in INTAKE_GOALS else "auto"


def clean_time_budget(value: Any) -> str:
    budget = str(value or "any").strip().casefold()
    return budget if budget in INTAKE_TIME_BUDGETS else "any"


def clean_preferred_hub(value: Any) -> str:
    hub = str(value or "jita").strip().casefold()
    return hub if hub in INTAKE_HUBS else "jita"


def nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def fit_summary(lines: Iterable[str]) -> dict[str, Any] | None:
    clean_lines = list(lines)
    header_index = next((index for index, line in enumerate(clean_lines[:8]) if FIT_HEADER_RE.match(line)), None)
    if header_index is None:
        return None
    header = FIT_HEADER_RE.match(clean_lines[header_index])
    if header is None:
        return None
    fitted = 0
    cargo = 0
    empty = 0
    for line in clean_lines[header_index + 1 :]:
        folded = line.casefold()
        if folded.startswith("[empty ") and folded.endswith(" slot]"):
            empty += 1
            continue
        if folded in {"cargo", "drone bay", "fighter bay", "fleet hangar", "fuel bay"}:
            continue
        if re.search(r"\sx[\d,]+\s*$", line, flags=re.IGNORECASE):
            cargo += 1
        else:
            fitted += 1
    return {
        "hull": header.group("hull").strip(),
        "fit_name": header.group("name").strip(),
        "fitted_line_count": fitted,
        "cargo_line_count": cargo,
        "empty_slot_count": empty,
    }


def dscan_signal(lines: Iterable[str]) -> dict[str, Any] | None:
    rows = [line for line in lines if "\t" in line]
    if not rows:
        return None
    headerish = any("distance" in line.casefold() and "type" in line.casefold() for line in rows[:4])
    distance_rows = [line for line in rows if DSCAN_DISTANCE_RE.search(line)]
    if headerish or len(distance_rows) >= 3:
        return {"row_count": len(rows), "distance_row_count": len(distance_rows)}
    return None


def wallet_signal(lines: Iterable[str]) -> dict[str, Any] | None:
    joined = "\n".join(lines)
    headerish = re.search(r"\b(date|time)\b.*\b(type|item|quantity|amount|unit price|price)\b", joined, re.IGNORECASE)
    sale_terms = re.search(r"\b(buy|sell|transaction|market transaction|broker|sales tax|client)\b", joined, re.IGNORECASE)
    if (headerish and MONEY_RE.search(joined)) or (sale_terms and "\t" in joined and MONEY_RE.search(joined)):
        return {"row_count": len(list(lines))}
    return None


def contract_signal(lines: Iterable[str]) -> dict[str, Any] | None:
    joined = "\n".join(lines)
    if re.search(r"\b(contract|item exchange|courier|collateral|issuer|acceptor|reward|expires)\b", joined, re.IGNORECASE):
        return {"money_terms": len(MONEY_RE.findall(joined))}
    return None


def killmail_signal(text: str) -> dict[str, Any] | None:
    match = KILLMAIL_URL_RE.search(text)
    if match:
        return {"kill_id": match.group("kill_id"), "url": match.group(0)}
    if re.search(r"\b(killmail|victim|final blow|zkill)\b", text, re.IGNORECASE):
        return {"kill_id": "", "url": ""}
    return None


def item_parse_summary(text: str) -> dict[str, Any]:
    try:
        parsed = parse_bulk_appraisal_text(text)
    except ValueError:
        return {"items": [], "unresolved_lines": [], "ignored_line_count": 0, "raw_line_count": 0}
    items = list(parsed.get("items") or [])
    unresolved = list(parsed.get("unresolved_lines") or [])
    preview = [
        {
            "name": str(item.get("name") or ""),
            "quantity": int(item.get("quantity") or 0),
            "blueprint_copy": bool(item.get("blueprint_copy")),
            "source_formats": list(item.get("source_formats") or []),
        }
        for item in items[:20]
    ]
    return {
        "items": preview,
        "item_count": len(items),
        "unresolved_lines": unresolved[:12],
        "unresolved_count": len(unresolved),
        "ignored_line_count": int(parsed.get("ignored_line_count") or 0),
        "raw_line_count": int(parsed.get("raw_line_count") or 0),
    }


def ore_item_count(items: Iterable[dict[str, Any]]) -> int:
    return sum(1 for item in items if ORE_NAME_RE.search(str(item.get("name") or "")))


def blueprint_item_count(items: Iterable[dict[str, Any]]) -> int:
    return sum(1 for item in items if "blueprint" in str(item.get("name") or "").casefold() or item.get("blueprint_copy"))


def classify_intake(text: str, *, goal: str) -> dict[str, Any]:
    lines = nonempty_lines(text)
    items = item_parse_summary(text)
    fit = fit_summary(lines)
    dscan = dscan_signal(lines)
    wallet = wallet_signal(lines)
    contract = contract_signal(lines)
    killmail = killmail_signal(text)
    ore_count = ore_item_count(items.get("items") or [])
    blueprint_count = blueprint_item_count(items.get("items") or [])
    item_count = int(items.get("item_count") or 0)

    signals: list[dict[str, Any]] = []
    if fit:
        signals.append({"kind": "fit", "label": "EVE fitting block", "strength": 95})
    if wallet:
        signals.append({"kind": "wallet", "label": "Wallet or market transaction rows", "strength": 90})
    if contract:
        signals.append({"kind": "contract", "label": "Contract-style paste", "strength": 82})
    if dscan:
        signals.append({"kind": "dscan", "label": "D-scan table", "strength": 78})
    if killmail:
        signals.append({"kind": "killmail", "label": "Killmail or zKillboard link", "strength": 76})
    if item_count:
        item_strength = 62 + min(18, item_count * 2)
        signals.append({"kind": "items", "label": "Item, cargo, or BOM list", "strength": item_strength})
    if ore_count:
        signals.append({"kind": "ore", "label": "Ore or reprocessing list", "strength": 84 + min(10, ore_count)})
    if blueprint_count or goal == "manufacture":
        signals.append({"kind": "manufacturing", "label": "Blueprint or manufacturing context", "strength": 80 + min(10, blueprint_count)})

    if goal in {"sell", "haul"} and item_count:
        signals.append({"kind": "items", "label": "Goal-selected item/cargo review", "strength": 90})
    if goal == "reprocess" and (ore_count or item_count):
        signals.append({"kind": "ore", "label": "Goal-selected reprocessing review", "strength": 92})
    if goal == "audit_profit" and (wallet or item_count):
        signals.append({"kind": "wallet", "label": "Goal-selected profit audit", "strength": 92})

    if not signals:
        signals.append({"kind": "unknown", "label": "General EVE text", "strength": 40})

    signals.sort(key=lambda signal: (-int(signal["strength"]), str(signal["kind"])))
    primary = dict(signals[0])
    return {
        "primary_kind": primary["kind"],
        "label": primary["label"],
        "confidence": min(99, max(35, int(primary["strength"]))),
        "signals": signals,
        "line_count": len(lines),
        "fit": fit,
        "dscan": dscan,
        "wallet": wallet,
        "contract": contract,
        "killmail": killmail,
        "items": items,
        "ore_count": ore_count,
        "blueprint_count": blueprint_count,
    }


def data_sources_for_classification(classification: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [
        {
            "key": "pasted-text",
            "label": "Pasted text",
            "posture": "local request",
            "freshness": "current paste",
            "persistence": "not stored",
            "detail": "The browser sends this paste to the local server only for this analysis.",
        },
        {
            "key": "local-parser",
            "label": "Local parser",
            "posture": "standard library",
            "freshness": "current code",
            "persistence": "not stored",
            "detail": "The router classifies text and builds manual next steps without contacting EVE.",
        },
    ]
    if int((classification.get("items") or {}).get("item_count") or 0):
        sources.append(
            {
                "key": "bulk-parser",
                "label": "Bulk item parser",
                "posture": "local static-compatible parser",
                "freshness": "current paste",
                "persistence": "not stored",
                "detail": "Item-like rows are normalized so they can be handed to Bulk Appraisal, Hauler Routes, or Portfolio.",
            }
        )
    if classification.get("primary_kind") in {"dscan", "killmail"}:
        sources.append(
            {
                "key": "external-links",
                "label": "External reference links",
                "posture": "manual browser handoff",
                "freshness": "pilot verifies",
                "persistence": "not stored",
                "detail": "The router points to community tools but does not upload your paste automatically.",
            }
        )
    return sources


def checklist_item(label: str, value: str, detail: str = "", *, warning: bool = False) -> dict[str, Any]:
    return {"label": label, "value": value, "detail": detail, "warning": warning}


def action(label: str, href: str, detail: str, *, target_tab: str = "") -> dict[str, Any]:
    return {"label": label, "href": href, "target_tab": target_tab, "detail": detail}


def external_link(label: str, url: str) -> dict[str, str]:
    return {"label": label, "url": url}


def recommendation(
    key: str,
    title: str,
    explanation: str,
    checklist: list[dict[str, Any]],
    *,
    priority: int,
    assumptions: Iterable[str],
    next_actions: Iterable[dict[str, Any]],
    links: Iterable[dict[str, str]] = (),
    source_keys: Iterable[str] = ("pasted-text", "local-parser"),
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "priority": priority,
        "explanation": explanation,
        "assumptions": list(assumptions),
        "source_keys": list(source_keys),
        "manual_checklist": checklist,
        "next_actions": list(next_actions),
        "links": list(links),
    }


def fit_recommendation(classification: dict[str, Any], *, goal: str) -> dict[str, Any]:
    fit = classification.get("fit") or {}
    hull = fit.get("hull") or "ship"
    fit_name = fit.get("fit_name") or "fit"
    title = f"Review {hull} fit and decide buy, build, or share"
    if goal == "buy_ship":
        title = f"Turn {hull} fit into a ship acquisition checklist"
    return recommendation(
        "fit-handoff",
        title,
        f"The paste looks like an EVE fitting block for {hull}. Treat the fit as the target, then use existing tools to price, source, and share it manually.",
        [
            checklist_item("Fit", f"{hull} - {fit_name}", "Copy this block into EVE's fitting simulator before buying."),
            checklist_item("Modules", str(fit.get("fitted_line_count") or 0), "Fitted lines should be checked for missing skills and substitutions."),
            checklist_item("Cargo", str(fit.get("cargo_line_count") or 0), "Cargo/ammo lines can be appraised with the hull and modules."),
            checklist_item("Manual check", "EVE simulator", "Confirm capacitor, CPU, powergrid, cargo, drones, and skill requirements in the client."),
        ],
        priority=96 if goal == "buy_ship" else 86,
        assumptions=[
            "The first bracketed line is the canonical EVE fitting header.",
            "This app does not import the fit into EVE or press any simulator buttons.",
            "Prices and skill fit checks still need a follow-up workflow.",
        ],
        next_actions=[
            action("Open Shared Fittings", "#fittings", "Save or Discord-post the exact fit block for corp review.", target_tab="fittings"),
            action("Open Bulk Appraisal", "#appraisal", "Price the hull, modules, rigs, drones, and cargo against public hub orders.", target_tab="appraisal"),
        ],
        links=[
            external_link("EVE Workbench fittings", "https://www.eveworkbench.com/fitting"),
            external_link("EVE University fitting guide", "https://wiki.eveuniversity.org/Fitting_ships"),
        ],
        source_keys=("pasted-text", "local-parser", "bulk-parser"),
    )


def item_recommendation(classification: dict[str, Any], *, goal: str, preferred_hub: str) -> dict[str, Any]:
    items = classification.get("items") or {}
    item_count = int(items.get("item_count") or 0)
    unresolved_count = int(items.get("unresolved_count") or 0)
    title = "Route pasted items into appraisal, hauling, or portfolio planning"
    if goal == "sell":
        title = f"Appraise pasted items before selling near {preferred_hub.title()}"
    if goal == "haul":
        title = "Use pasted cargo as a hauler-route input"
    return recommendation(
        "item-router",
        title,
        "The paste contains item-like rows. Start with a bulk appraisal, then choose whether the result belongs in sell, haul, reprocess, manufacturing, or portfolio work.",
        [
            checklist_item("Parsed rows", str(item_count), "Rows that look like EVE items, cargo, fit contents, or a bill of materials."),
            checklist_item("Unresolved lines", str(unresolved_count), "Clean these before trusting prices or route plans.", warning=unresolved_count > 0),
            checklist_item("Preferred hub", preferred_hub.title(), "Use this as the first public-order estimate, then verify in EVE."),
            checklist_item("Manual decision", "sell / haul / build", "Choose the next workflow after checking value, volume, and confidence."),
        ],
        priority=90 if goal in {"sell", "haul", "what_now"} else 72,
        assumptions=[
            "Item rows are parsed locally and not saved.",
            "Bulk Appraisal will resolve names with the local static market cache.",
            "Public-order prices can move before the pilot acts.",
        ],
        next_actions=[
            action("Open Bulk Appraisal", "#appraisal", "Get quick-sell, replace/buy, spread, volume, and confidence warnings.", target_tab="appraisal"),
            action("Open Hauler Routes", "#hauling", "Use item names as route cargo candidates if moving them may create value.", target_tab="hauling"),
            action("Open Investment Portfolio", "#acquisition", "Use item names as a focused scan scope for buy-order planning.", target_tab="acquisition"),
        ],
        links=[
            external_link("Janice appraisal", "https://janice.e-351.com/"),
            external_link("EVE Tycoon market", "https://evetycoon.com/market"),
        ],
        source_keys=("pasted-text", "local-parser", "bulk-parser"),
    )


def ore_recommendation(classification: dict[str, Any], *, goal: str) -> dict[str, Any]:
    ore_count = int(classification.get("ore_count") or 0)
    return recommendation(
        "ore-reprocessing",
        "Compare ore sale value against reprocessing output",
        "The paste includes ore-like item names. The useful question is whether to sell ore directly, refine it where your skills/standings help, or move it before refining.",
        [
            checklist_item("Ore-like rows", str(ore_count), "Run the dedicated reprocessing tab for exact ore type, quantity, facility, and tax assumptions."),
            checklist_item("Facility", "manual choice", "Station/structure yield, tax, rigs, standings, and implant bonuses change the answer."),
            checklist_item("Market comparison", "ore vs minerals", "Compare the ore quick-sell value with after-tax mineral value."),
            checklist_item("Safety", "manual only", "No refining job, movement, or market order is started by this app."),
        ],
        priority=94 if goal in {"reprocess", "gather", "what_now"} else 82,
        assumptions=[
            "Ore recognition is a local name heuristic until the Reprocessing tab calculates exact outputs.",
            "Structure access, service availability, and tax settings must be verified by the pilot.",
        ],
        next_actions=[
            action("Open Reprocessing", "#reprocessing", "Calculate mineral output with skills, standings, optional implant reads, and manual facility settings.", target_tab="reprocessing"),
            action("Open Bulk Appraisal", "#appraisal", "Estimate direct ore sale value before committing to reprocessing.", target_tab="appraisal"),
        ],
        links=[external_link("EVE University reprocessing", "https://wiki.eveuniversity.org/Reprocessing")],
        source_keys=("pasted-text", "local-parser", "bulk-parser"),
    )


def manufacturing_recommendation(classification: dict[str, Any], *, goal: str) -> dict[str, Any]:
    blueprint_count = int(classification.get("blueprint_count") or 0)
    return recommendation(
        "manufacturing-plan",
        "Turn the paste into a build-vs-buy manufacturing plan",
        "The paste has blueprint or bill-of-material signals. Use Industry Library for owned blueprint/material context, then appraise missing inputs before starting jobs.",
        [
            checklist_item("Blueprint/BOM signals", str(blueprint_count), "Blueprint copies and originals need different handling."),
            checklist_item("Owned context", "ESI optional", "Industry Library can use authorized assets and blueprints if connected."),
            checklist_item("Missing inputs", "appraise first", "Price missing materials before deciding to build."),
            checklist_item("Accuracy loop", "record plan", "Save expected material, fee, and sell assumptions so Trade P&L can compare later."),
        ],
        priority=92 if goal == "manufacture" else 78,
        assumptions=[
            "This router does not calculate job cost index, facility rigs, or TE/ME by itself.",
            "Industry actions remain manual and must be started in EVE.",
        ],
        next_actions=[
            action("Open Industry Library", "#industry", "Review owned blueprints, assets, recipe cache, and buyer candidates.", target_tab="industry"),
            action("Open Bulk Appraisal", "#appraisal", "Price missing inputs or finished products using public hub orders.", target_tab="appraisal"),
            action("Open Trade P&L", "#trade-pnl", "After selling, compare actual wallet results against the planned spread.", target_tab="trade-pnl"),
        ],
        links=[external_link("EVE University manufacturing", "https://wiki.eveuniversity.org/Manufacturing")],
        source_keys=("pasted-text", "local-parser", "bulk-parser"),
    )


def wallet_recommendation(classification: dict[str, Any], *, goal: str) -> dict[str, Any]:
    return recommendation(
        "wallet-profit-audit",
        "Audit wallet rows against expected profit",
        "The paste looks like wallet or market transaction data. Use Trade P&L for the real comparison: buys, sells, fees, open stock, and expected-vs-actual plan deltas.",
        [
            checklist_item("Transaction signal", "detected", "Wallet rows are useful for proving whether the plan worked."),
            checklist_item("Fees", "broker + tax", "Fee rows often explain why expected profit and actual wallet return differ."),
            checklist_item("Window", "choose range", "Set the Trade P&L window wide enough to include both buy and sell legs."),
            checklist_item("Outcome learning", "save lesson", "Record whether the estimate missed price, fees, fill rate, or time-to-sell."),
        ],
        priority=96 if goal == "audit_profit" else 84,
        assumptions=[
            "Pasted wallet text is not stored by this router.",
            "The Trade P&L tab uses read-only wallet ESI when connected; it cannot place or edit orders.",
        ],
        next_actions=[
            action("Open Trade P&L", "#trade-pnl", "Refresh wallet transactions and compare expected against actual results.", target_tab="trade-pnl"),
        ],
        links=[external_link("EVE University trading", "https://wiki.eveuniversity.org/Trading")],
    )


def contract_recommendation(classification: dict[str, Any]) -> dict[str, Any]:
    return recommendation(
        "contract-review",
        "Review contract economics before accepting or posting",
        "The paste has contract-style terms. The safe path is to appraise the contents, verify issuer/location/collateral in EVE, and treat all terms as manual.",
        [
            checklist_item("Contract terms", "detected", "Issuer, location, reward, collateral, expiry, and contents must be verified in EVE."),
            checklist_item("Contents", "appraise", "Paste the item list into Bulk Appraisal if the contract includes goods."),
            checklist_item("Collateral/reward", "manual math", "Courier collateral and reward need route-risk review before accepting."),
            checklist_item("No action taken", "manual only", "The app does not accept, create, or modify contracts."),
        ],
        priority=82,
        assumptions=[
            "Contract text can be incomplete or edited; the EVE contract window remains the source of truth.",
            "Public appraisal does not prove docking access or delivery safety.",
        ],
        next_actions=[
            action("Open Bulk Appraisal", "#appraisal", "Price contract contents or collateral-relevant items.", target_tab="appraisal"),
            action("Open Hauler Routes", "#hauling", "Check route value and movement risk if this is courier-like work.", target_tab="hauling"),
        ],
        links=[external_link("EVE University contracts", "https://wiki.eveuniversity.org/Contracts")],
    )


def dscan_or_killmail_recommendation(classification: dict[str, Any]) -> dict[str, Any]:
    killmail = classification.get("killmail") or {}
    links = [
        external_link("DSCAN-ICU", "https://dscan.info/"),
        external_link("zKillboard", killmail.get("url") or "https://zkillboard.com/"),
        external_link("EVE Gatecamp Check", "https://eve-gatecheck.space/eve/"),
    ]
    return recommendation(
        "risk-context",
        "Use combat paste as risk context, not as a new intel workflow",
        "This looks like D-scan, local-risk, or killmail context. The router can point you to the right public tools, but this roadmap does not expand into a corp intel platform.",
        [
            checklist_item("Risk paste", "detected", "Use this to inform route, hauling, ship, or undock decisions."),
            checklist_item("External review", "manual", "Open a community tool yourself if you want deeper combat context."),
            checklist_item("No watchlist", "not active", "This feature does not create shared intel feeds, hidden monitoring, or automatic alerts."),
            checklist_item("Decision impact", "adjust risk", "Lower cargo value, choose a different ship, wait, scout, or skip if the risk is too high."),
        ],
        priority=74,
        assumptions=[
            "The app does not upload D-scan/local text to third-party sites automatically.",
            "Maps and war-room intel are intentionally out of scope for this product direction.",
        ],
        next_actions=[
            action("Open Hauler Routes", "#hauling", "Apply the risk context to cargo value and route choice.", target_tab="hauling"),
            action("Open Flight Attendant", "#flight", "Keep the context as a manual note while deciding what to do.", target_tab="flight"),
        ],
        links=links,
        source_keys=("pasted-text", "local-parser", "external-links"),
    )


def general_recommendation(classification: dict[str, Any], *, goal: str) -> dict[str, Any]:
    return recommendation(
        "general-triage",
        "Clarify the goal, then paste a more specific EVE artifact",
        "The router could not confidently identify a fit, cargo list, contract, wallet rows, ore, D-scan, or killmail. Choose the goal and paste the most concrete EVE text you have.",
        [
            checklist_item("Detected type", classification.get("label") or "General EVE text", "The text did not match a high-confidence workflow."),
            checklist_item("Best next paste", "fit / cargo / BOM / wallet", "Specific clipboard formats produce better manual checklists."),
            checklist_item("Goal", goal.replace("_", " "), "The selected goal can steer the next recommendation."),
        ],
        priority=45,
        assumptions=[
            "Free-form notes are useful context but weak evidence for pricing or planning.",
            "The app needs concrete EVE data before it can give a high-confidence checklist.",
        ],
        next_actions=[
            action("Open Flight Attendant", "#flight", "Save the note locally if it is general planning context.", target_tab="flight"),
            action("Open Bulk Appraisal", "#appraisal", "Use this if the text can be converted into item lines.", target_tab="appraisal"),
        ],
        links=[external_link("EVE University Wiki", "https://wiki.eveuniversity.org/Main_Page")],
    )


def build_recommendations(
    classification: dict[str, Any],
    *,
    goal: str,
    preferred_hub: str,
) -> list[dict[str, Any]]:
    primary = classification.get("primary_kind")
    recs: list[dict[str, Any]] = []
    if classification.get("fit"):
        recs.append(fit_recommendation(classification, goal=goal))
    if int((classification.get("items") or {}).get("item_count") or 0):
        recs.append(item_recommendation(classification, goal=goal, preferred_hub=preferred_hub))
    if int(classification.get("ore_count") or 0):
        recs.append(ore_recommendation(classification, goal=goal))
    if classification.get("blueprint_count") or goal == "manufacture":
        recs.append(manufacturing_recommendation(classification, goal=goal))
    if classification.get("wallet"):
        recs.append(wallet_recommendation(classification, goal=goal))
    if classification.get("contract"):
        recs.append(contract_recommendation(classification))
    if primary in {"dscan", "killmail"} or classification.get("dscan") or classification.get("killmail"):
        recs.append(dscan_or_killmail_recommendation(classification))
    if not recs:
        recs.append(general_recommendation(classification, goal=goal))
    recs.sort(key=lambda rec: (-int(rec.get("priority") or 0), str(rec.get("key") or "")))
    return recs[:5]


def build_share_text(payload: dict[str, Any]) -> str:
    classification = payload.get("classification") or {}
    lines = [
        "EVE Intake recommendation",
        f"Detected: {classification.get('label') or 'Unknown'} ({classification.get('confidence') or 0}% confidence)",
        f"Goal: {payload.get('goal_label') or payload.get('goal') or 'Auto'}",
        "",
        "Top recommendations:",
    ]
    for rec in (payload.get("recommendations") or [])[:3]:
        lines.append(f"- {rec.get('title')}: {rec.get('explanation')}")
    lines.extend(
        [
            "",
            "Manual only. Verify prices, skills, contracts, routes, and game state in EVE before acting.",
        ]
    )
    return "\n".join(lines)


def goal_label(goal: str) -> str:
    labels = {
        "auto": "Auto detect",
        "what_now": "What should I do now?",
        "sell": "Sell or appraise",
        "buy_ship": "Get a specific ship",
        "manufacture": "Manufacture",
        "gather": "Gather resources",
        "reprocess": "Reprocess/refine",
        "haul": "Haul or stage",
        "explore": "Exploration prep",
        "audit_profit": "Audit profit",
        "learn": "Explain and teach",
    }
    return labels.get(goal, goal.replace("_", " ").title())


def build_intake_router_payload(
    *,
    raw_text: Any,
    goal: Any = "auto",
    time_budget: Any = "any",
    preferred_hub: Any = "jita",
) -> dict[str, Any]:
    text = clean_intake_text(raw_text)
    if not text:
        raise ValueError("Paste EVE text before running intake analysis.")
    clean_goal = clean_intake_goal(goal)
    clean_budget = clean_time_budget(time_budget)
    clean_hub = clean_preferred_hub(preferred_hub)
    classification = classify_intake(text, goal=clean_goal)
    data_sources = data_sources_for_classification(classification)
    recommendations = build_recommendations(classification, goal=clean_goal, preferred_hub=clean_hub)
    payload: dict[str, Any] = {
        "ok": True,
        "generated_at": now_iso(),
        "source": "local-intake-router",
        "persistence": "none",
        "goal": clean_goal,
        "goal_label": goal_label(clean_goal),
        "time_budget": clean_budget,
        "preferred_hub": clean_hub,
        "input": {
            "raw_line_count": len(text.split("\n")),
            "nonempty_line_count": classification["line_count"],
            "character_count": len(text),
            "stored": False,
        },
        "classification": {
            "primary_kind": classification["primary_kind"],
            "label": classification["label"],
            "confidence": classification["confidence"],
            "signals": classification["signals"][:8],
        },
        "parsed": {
            "fit": classification.get("fit"),
            "items": (classification.get("items") or {}).get("items") or [],
            "item_count": int((classification.get("items") or {}).get("item_count") or 0),
            "unresolved_lines": (classification.get("items") or {}).get("unresolved_lines") or [],
            "unresolved_count": int((classification.get("items") or {}).get("unresolved_count") or 0),
            "ore_count": int(classification.get("ore_count") or 0),
            "blueprint_count": int(classification.get("blueprint_count") or 0),
            "dscan": classification.get("dscan"),
            "wallet": classification.get("wallet"),
            "contract": classification.get("contract"),
            "killmail": classification.get("killmail"),
        },
        "data_sources": data_sources,
        "trust": {
            "esi_scopes": [],
            "token_storage": "none",
            "server_persistence": "none",
            "cache_freshness": [
                {"label": "Pasted text", "state": "current request"},
                {"label": "Static market cache", "state": "used by follow-up appraisal, not by this classifier"},
                {"label": "ESI", "state": "not contacted by this classifier"},
            ],
            "redaction": "Raw pasted text is not echoed back in this payload.",
        },
        "recommendations": recommendations,
        "beginner_translation": (
            "Start with the top recommendation, read the assumptions, then run only the follow-up workflow that matches your goal. "
            "The app is giving you a checklist, not taking action in EVE."
        ),
        "warnings": [
            "This router does not control the EVE client, place orders, accept contracts, or upload D-scan/local text to third-party sites.",
            "Verify current prices, routes, ship fit, structure access, and contract terms in EVE before acting.",
        ],
        "notes": [
            "Raw paste text is analyzed for this request only and is not stored by the server.",
            "No character ESI scope or token is required for intake analysis.",
        ],
    }
    payload["share_text"] = build_share_text(payload)
    return payload
