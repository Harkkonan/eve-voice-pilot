from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Iterable


DEFAULT_MAX_BULK_APPRAISAL_TEXT_LENGTH = 200_000
BULK_APPRAISAL_PUBLIC_HUBS = {
    "jita": {"label": "Jita", "system_id": 30000142, "region_id": 10000002, "security_status": 0.9},
    "amarr": {"label": "Amarr", "system_id": 30002187, "region_id": 10000043, "security_status": 1.0},
    "dodixie": {"label": "Dodixie", "system_id": 30002659, "region_id": 10000032, "security_status": 0.9},
    "hek": {"label": "Hek", "system_id": 30002053, "region_id": 10000042, "security_status": 0.5},
    "rens": {"label": "Rens", "system_id": 30002510, "region_id": 10000030, "security_status": 0.9},
}

FIT_HEADER_RE = re.compile(r"^\[(?P<hull>[^,\]]+),\s*(?P<name>[^\]]+)\]\s*$")
BULK_APPRAISAL_X_QUANTITY_RE = re.compile(r"^(?P<name>.+?)\s+x(?P<quantity>[\d,]+)\s*$", re.IGNORECASE)
BULK_APPRAISAL_LEADING_QUANTITY_RE = re.compile(r"^(?P<quantity>[\d,]+)\s*x?\s+(?P<name>.+?)\s*$", re.IGNORECASE)
BULK_APPRAISAL_TRAILING_QUANTITY_RE = re.compile(r"^(?P<name>.+?)\s+(?P<quantity>[\d,]+)\s*$")
BULK_APPRAISAL_COPY_MARKER_RE = re.compile(r"\s*\((?P<marker>copy|original)\)\s*$", re.IGNORECASE)
BULK_APPRAISAL_SECTION_HEADINGS = frozenset(
    {
        "cargo",
        "drone bay",
        "fighter bay",
        "fleet hangar",
        "fuel bay",
        "high power",
        "low power",
        "medium power",
        "mid power",
        "module",
        "modules",
        "rig slots",
        "rigs",
        "ship hangar",
        "subsystems",
    }
)


@dataclass(frozen=True)
class BulkAppraisalDependencies:
    route_system_factory: Callable[..., Any]
    load_static_market_data: Callable[[], Any | None]
    scan_system_market_orders: Callable[..., tuple[dict[int, list[dict[str, Any]]], int, list[dict[str, Any]]]]
    liquidation_value_from_orders: Callable[..., dict[str, Any]]
    clean_optional_float: Callable[[Any], float | None]
    format_isk: Callable[[Any], str]
    market_order_cache_status: Callable[[], dict[str, Any]]
    now_iso: Callable[[], str]
    item_limit: int


def _normalize_name_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _clean_multiline(value: Any, field: str, *, max_length: int) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.split("\n")]
    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line)
        previous_blank = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    text = "\n".join(cleaned)
    if len(text) > max_length:
        raise ValueError(f"{field} must be {max_length} characters or less.")
    return text


def _static_market_type_infos_by_name(static_data: Any) -> dict[str, list[dict[str, Any]]]:
    type_infos_by_name: dict[str, list[dict[str, Any]]] = {}
    for type_infos in getattr(static_data, "types_by_group", {}).values():
        for type_info in type_infos:
            name = str(type_info.get("name") or "")
            key = _normalize_name_key(name)
            if key:
                type_infos_by_name.setdefault(key, []).append(dict(type_info))
    return type_infos_by_name


def clean_bulk_appraisal_quantity(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        quantity = int(text)
    except ValueError:
        return None
    return quantity if quantity > 0 else None


def clean_bulk_appraisal_item_name(value: Any) -> tuple[str, bool]:
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    blueprint_copy = False
    copy_match = BULK_APPRAISAL_COPY_MARKER_RE.search(text)
    if copy_match:
        blueprint_copy = copy_match.group("marker").casefold() == "copy"
        text = BULK_APPRAISAL_COPY_MARKER_RE.sub("", text).strip()
    if re.search(r"\bbpc\b", text, flags=re.IGNORECASE):
        blueprint_copy = True
        text = re.sub(r"\bbpc\b", "", text, flags=re.IGNORECASE)
    if re.search(r"\bblueprint\s+copy\b", text, flags=re.IGNORECASE):
        blueprint_copy = True
        text = re.sub(r"\bblueprint\s+copy\b", "Blueprint", text, flags=re.IGNORECASE)
    return " ".join(text.strip(" -:\t").split()), blueprint_copy


def bulk_appraisal_is_header_or_section(line: str) -> bool:
    clean_line = " ".join(str(line or "").strip().strip(":").split())
    if not clean_line:
        return True
    folded = clean_line.casefold()
    if folded in BULK_APPRAISAL_SECTION_HEADINGS:
        return True
    if folded.startswith("[empty ") and folded.endswith(" slot]"):
        return True
    if folded in {"item", "items", "name", "type", "quantity", "qty", "item quantity", "item qty", "name quantity", "name qty"}:
        return True
    if "\t" in line:
        cells = [cell.strip().casefold() for cell in line.split("\t") if cell.strip()]
        if cells and cells[0] in {"item", "name", "type"} and any(cell in {"quantity", "qty"} for cell in cells[1:3]):
            return True
    return False


def parse_bulk_appraisal_tabbed_line(line: str) -> tuple[str, int, str] | None:
    cells = [cell.strip() for cell in line.split("\t") if cell.strip()]
    if len(cells) < 2 or cells[0].casefold() in {"item", "name", "type"}:
        return None
    quantity = clean_bulk_appraisal_quantity(cells[1])
    if quantity is not None:
        return cells[0], quantity, "inventory"
    quantity = clean_bulk_appraisal_quantity(cells[0])
    if quantity is not None:
        return cells[1], quantity, "inventory"
    return None


def parse_bulk_appraisal_plain_line(line: str) -> tuple[str, int, str] | None:
    header = FIT_HEADER_RE.match(line)
    if header:
        return header.group("hull"), 1, "eft"
    for pattern, source in (
        (BULK_APPRAISAL_X_QUANTITY_RE, "quantity_suffix"),
        (BULK_APPRAISAL_LEADING_QUANTITY_RE, "quantity_prefix"),
        (BULK_APPRAISAL_TRAILING_QUANTITY_RE, "quantity_suffix"),
    ):
        match = pattern.match(line)
        if not match:
            continue
        quantity = clean_bulk_appraisal_quantity(match.group("quantity"))
        name = match.group("name").strip()
        if quantity is not None and name:
            return name, quantity, source
    return line, 1, "single_line"


def merge_bulk_appraisal_parse_row(rows: dict[tuple[str, bool], dict[str, Any]], row: dict[str, Any]) -> None:
    key = (_normalize_name_key(row.get("name")), bool(row.get("blueprint_copy")))
    if not key[0]:
        return
    existing = rows.get(key)
    if existing is None:
        rows[key] = row
        return
    existing["quantity"] = int(existing.get("quantity") or 0) + int(row.get("quantity") or 0)
    existing.setdefault("line_numbers", []).extend(row.get("line_numbers") or [])
    source_formats = set(existing.get("source_formats") or [])
    source_formats.update(row.get("source_formats") or [])
    existing["source_formats"] = sorted(source_formats)


def parse_bulk_appraisal_text(raw_text: Any) -> dict[str, Any]:
    text = _clean_multiline(raw_text, "appraisal_text", max_length=DEFAULT_MAX_BULK_APPRAISAL_TEXT_LENGTH)
    rows_by_name: dict[tuple[str, bool], dict[str, Any]] = {}
    unresolved_lines: list[dict[str, Any]] = []
    ignored_line_count = 0
    for index, line in enumerate(text.split("\n"), start=1):
        raw_line = line.strip()
        if not raw_line or bulk_appraisal_is_header_or_section(raw_line):
            ignored_line_count += 1
            continue
        parsed = parse_bulk_appraisal_tabbed_line(raw_line) if "\t" in raw_line else None
        if parsed is None:
            parsed = parse_bulk_appraisal_plain_line(raw_line)
        if parsed is None:
            unresolved_lines.append({"line_number": index, "raw": raw_line, "reason": "Could not parse an item and quantity."})
            continue
        raw_name, quantity, source_format = parsed
        name, blueprint_copy = clean_bulk_appraisal_item_name(raw_name)
        if not name:
            unresolved_lines.append({"line_number": index, "raw": raw_line, "reason": "Item name was empty after cleanup."})
            continue
        merge_bulk_appraisal_parse_row(
            rows_by_name,
            {
                "input_name": raw_name.strip(),
                "name": name,
                "quantity": quantity,
                "line_numbers": [index],
                "source_formats": [source_format],
                "blueprint_copy": blueprint_copy,
                "warnings": ["Blueprint copy detected; BPCs are marked unpriceable."] if blueprint_copy else [],
            },
        )
    return {
        "ok": True,
        "raw_line_count": len(text.split("\n")) if text else 0,
        "ignored_line_count": ignored_line_count,
        "items": sorted(rows_by_name.values(), key=lambda item: (str(item["name"]).casefold(), bool(item["blueprint_copy"]))),
        "unresolved_lines": unresolved_lines,
    }


def resolve_bulk_appraisal_items(
    parsed_items: Iterable[dict[str, Any]],
    *,
    static_data: Any | None = None,
    deps: BulkAppraisalDependencies,
    limit: int | None = None,
) -> dict[str, Any]:
    all_items = list(parsed_items)
    clean_limit = deps.item_limit if limit is None else max(0, int(limit))
    scoped_items = all_items[:clean_limit]
    if static_data is None:
        static_data = deps.load_static_market_data()
    if static_data is None:
        return {
            "items": [],
            "unresolved_lines": [
                {
                    "line_number": min(item.get("line_numbers") or [0]),
                    "raw": item.get("input_name") or item.get("name") or "",
                    "reason": "Static market data cache is missing; item type could not be resolved.",
                }
                for item in scoped_items
            ],
            "truncated_item_count": max(0, len(all_items) - len(scoped_items)),
            "static_data_available": False,
        }
    type_infos_by_name = _static_market_type_infos_by_name(static_data)
    resolved_items: list[dict[str, Any]] = []
    unresolved_lines: list[dict[str, Any]] = []
    for item in scoped_items:
        matches = type_infos_by_name.get(_normalize_name_key(item.get("name")), [])
        if len(matches) != 1:
            unresolved_lines.append(
                {
                    "line_number": min(item.get("line_numbers") or [0]),
                    "raw": item.get("input_name") or item.get("name") or "",
                    "name": item.get("name") or "",
                    "quantity": item.get("quantity") or 0,
                    "reason": "Item name is ambiguous in static market data." if matches else "Item name was not found in static market data.",
                    "match_count": len(matches),
                }
            )
            continue
        match = matches[0]
        quantity = int(item.get("quantity") or 0)
        volume_m3 = deps.clean_optional_float(match.get("volume_m3"))
        resolved_items.append(
            {
                **item,
                "type_id": int(match["type_id"]),
                "name": str(match.get("name") or item.get("name") or ""),
                "quantity": quantity,
                "volume_m3": volume_m3,
                "total_volume_m3": round((volume_m3 or 0.0) * quantity, 6),
                "market_group_id": match.get("market_group_id"),
                "market_group_name": match.get("market_group_name") or "",
            }
        )
    return {
        "items": resolved_items,
        "unresolved_lines": unresolved_lines,
        "truncated_item_count": max(0, len(all_items) - len(scoped_items)),
        "static_data_available": True,
    }


def normalize_bulk_appraisal_hub(hub_name: Any) -> str:
    key = _normalize_name_key(hub_name)
    if key in BULK_APPRAISAL_PUBLIC_HUBS:
        return key
    for candidate_key, hub in BULK_APPRAISAL_PUBLIC_HUBS.items():
        if _normalize_name_key(hub["label"]) == key:
            return candidate_key
    return "jita"


def bulk_appraisal_hub_options() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": str(value["label"]), "system_id": int(value["system_id"]), "region_id": int(value["region_id"])}
        for key, value in BULK_APPRAISAL_PUBLIC_HUBS.items()
    ]


def bulk_appraisal_hub_system(hub_key: str, *, deps: BulkAppraisalDependencies) -> Any:
    hub = BULK_APPRAISAL_PUBLIC_HUBS[normalize_bulk_appraisal_hub(hub_key)]
    return deps.route_system_factory(
        solar_system_id=int(hub["system_id"]),
        name=str(hub["label"]),
        region_id=int(hub["region_id"]),
        security_status=deps.clean_optional_float(hub.get("security_status")),
    )


def price_bulk_appraisal_items(
    *,
    config: Any,
    resolved_items: Iterable[dict[str, Any]],
    hub_key: str,
    deps: BulkAppraisalDependencies,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items = list(resolved_items)
    system = bulk_appraisal_hub_system(hub_key, deps=deps)
    type_ids = [int(item["type_id"]) for item in items if not item.get("blueprint_copy")]
    buy_orders_by_type, buy_order_count, buy_errors = deps.scan_system_market_orders(
        config=config,
        type_ids=type_ids,
        system=system,
        order_type="buy",
    )
    sell_orders_by_type, sell_order_count, sell_errors = deps.scan_system_market_orders(
        config=config,
        type_ids=type_ids,
        system=system,
        order_type="sell",
    )
    priced_items: list[dict[str, Any]] = []
    for item in items:
        quantity = int(item.get("quantity") or 0)
        warnings = list(item.get("warnings") or [])
        if item.get("blueprint_copy"):
            warnings.append("Blueprint copies do not have a reliable public market price; verify manually.")
            priced_items.append(
                {
                    **item,
                    "quick_sell_value_isk": None,
                    "quick_sell_unit_isk": None,
                    "replace_buy_value_isk": None,
                    "replace_buy_unit_isk": None,
                    "spread_isk": None,
                    "spread_percent": None,
                    "quick_sell_complete": False,
                    "replace_buy_complete": False,
                    "buy_order_count": 0,
                    "sell_order_count": 0,
                    "low_confidence": True,
                    "unpriceable": True,
                    "confidence_notes": ["BPC/unpriceable"],
                    "warnings": sorted(set(warnings)),
                }
            )
            continue
        type_id = int(item["type_id"])
        buy_orders = buy_orders_by_type.get(type_id, [])
        sell_orders = sell_orders_by_type.get(type_id, [])
        quick_sell = deps.liquidation_value_from_orders(buy_orders, quantity=quantity)
        replace_buy = deps.liquidation_value_from_orders(sell_orders, quantity=quantity)
        quick_sell_value = deps.clean_optional_float(quick_sell.get("value"))
        replace_buy_value = deps.clean_optional_float(replace_buy.get("value"))
        quick_sell_unit = quick_sell_value / quantity if quick_sell_value is not None and quantity > 0 else None
        replace_buy_unit = replace_buy_value / quantity if replace_buy_value is not None and quantity > 0 else None
        spread = replace_buy_value - quick_sell_value if replace_buy_value is not None and quick_sell_value is not None else None
        spread_percent = spread / replace_buy_value * 100.0 if spread is not None and replace_buy_value else None
        confidence_notes: list[str] = []
        if not quick_sell.get("complete"):
            confidence_notes.append("not enough public buy depth")
        if not replace_buy.get("complete"):
            confidence_notes.append("not enough public sell depth")
        if len(buy_orders) < 2:
            confidence_notes.append("thin buy orders")
        if len(sell_orders) < 2:
            confidence_notes.append("thin sell orders")
        priced_items.append(
            {
                **item,
                "quick_sell_value_isk": quick_sell_value if quick_sell.get("priced_quantity") else None,
                "quick_sell_unit_isk": quick_sell_unit if quick_sell.get("priced_quantity") else None,
                "replace_buy_value_isk": replace_buy_value if replace_buy.get("priced_quantity") else None,
                "replace_buy_unit_isk": replace_buy_unit if replace_buy.get("priced_quantity") else None,
                "spread_isk": spread,
                "spread_percent": spread_percent,
                "quick_sell_complete": bool(quick_sell.get("complete")),
                "replace_buy_complete": bool(replace_buy.get("complete")),
                "quick_sell_priced_quantity": int(quick_sell.get("priced_quantity") or 0),
                "replace_buy_priced_quantity": int(replace_buy.get("priced_quantity") or 0),
                "buy_order_count": len(buy_orders),
                "sell_order_count": len(sell_orders),
                "buy_orders_used": int(quick_sell.get("order_count") or 0),
                "sell_orders_used": int(replace_buy.get("order_count") or 0),
                "low_confidence": bool(confidence_notes),
                "unpriceable": False,
                "confidence_notes": confidence_notes,
                "warnings": sorted(set(warnings)),
            }
        )
    errors = [{"side": "quick_sell", **error} for error in buy_errors] + [
        {"side": "replace_buy", **error} for error in sell_errors
    ]
    if buy_order_count == 0 and sell_order_count == 0 and type_ids:
        errors.append({"error": f"No public market orders were found in {system.name} for the resolved item set."})
    return priced_items, errors


def bulk_appraisal_totals(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    quick_sell_total = sum(float(item.get("quick_sell_value_isk") or 0.0) for item in rows)
    replace_buy_total = sum(float(item.get("replace_buy_value_isk") or 0.0) for item in rows)
    spread = replace_buy_total - quick_sell_total
    return {
        "item_count": len(rows),
        "resolved_row_count": len(rows),
        "total_quantity": sum(int(item.get("quantity") or 0) for item in rows),
        "total_volume_m3": round(sum(float(item.get("total_volume_m3") or 0.0) for item in rows), 6),
        "quick_sell_value_isk": round(quick_sell_total, 4),
        "replace_buy_value_isk": round(replace_buy_total, 4),
        "spread_isk": round(spread, 4),
        "spread_percent": round(spread / replace_buy_total * 100.0, 4) if replace_buy_total > 0 else None,
        "low_confidence_count": sum(1 for item in rows if item.get("low_confidence")),
        "unpriceable_count": sum(1 for item in rows if item.get("unpriceable")),
    }


def build_bulk_appraisal_export_text(payload: dict[str, Any], *, deps: BulkAppraisalDependencies) -> str:
    totals = payload.get("totals") or {}
    hub = payload.get("hub") or {}
    lines = [
        f"Bulk Appraisal - {hub.get('label') or 'Jita'} public orders",
        f"Quick-sell: {deps.format_isk(totals.get('quick_sell_value_isk'))}",
        f"Replace/buy: {deps.format_isk(totals.get('replace_buy_value_isk'))}",
        f"Spread: {deps.format_isk(totals.get('spread_isk'))}",
        f"Total m3: {totals.get('total_volume_m3') or 0}",
        "",
        "Rows:",
    ]
    for item in payload.get("items") or []:
        flags = []
        if item.get("low_confidence"):
            flags.append("low confidence")
        if item.get("unpriceable"):
            flags.append("unpriceable")
        flag_text = f" ({', '.join(flags)})" if flags else ""
        lines.append(
            f"- {item.get('quantity')}x {item.get('name')}: "
            f"quick-sell {deps.format_isk(item.get('quick_sell_value_isk'))}, "
            f"replace {deps.format_isk(item.get('replace_buy_value_isk'))}{flag_text}"
        )
    unresolved = payload.get("unresolved_lines") or []
    if unresolved:
        lines.append("")
        lines.append("Unresolved:")
        for item in unresolved[:12]:
            line_number = item.get("line_number")
            prefix = f"line {line_number}: " if line_number else ""
            lines.append(f"- {prefix}{item.get('raw') or item.get('name') or 'unknown'} - {item.get('reason') or 'unresolved'}")
        if len(unresolved) > 12:
            lines.append(f"- +{len(unresolved) - 12} more unresolved lines")
    lines.append("")
    lines.append("Advisory only. Verify low-confidence rows, BPCs, and public-order depth in EVE before acting.")
    return "\n".join(lines)


def build_bulk_appraisal_payload(
    *,
    config: Any,
    raw_text: Any,
    deps: BulkAppraisalDependencies,
    hub_name: Any = "jita",
    static_data: Any | None = None,
) -> dict[str, Any]:
    parsed = parse_bulk_appraisal_text(raw_text)
    if not parsed["items"] and not parsed["unresolved_lines"]:
        raise ValueError("Paste at least one item line or EVE fitting block.")
    resolved = resolve_bulk_appraisal_items(parsed["items"], static_data=static_data, deps=deps)
    hub_key = normalize_bulk_appraisal_hub(hub_name)
    priced_items, price_errors = price_bulk_appraisal_items(
        config=config,
        resolved_items=resolved["items"],
        hub_key=hub_key,
        deps=deps,
    )
    unresolved_lines = list(parsed["unresolved_lines"]) + list(resolved["unresolved_lines"])
    totals = bulk_appraisal_totals(priced_items)
    hub = BULK_APPRAISAL_PUBLIC_HUBS[hub_key]
    warnings: list[dict[str, Any]] = []
    if resolved["truncated_item_count"]:
        warnings.append(
            {
                "level": "warning",
                "message": f"{resolved['truncated_item_count']} parsed item rows were skipped because the local appraisal limit was reached.",
            }
        )
    if not resolved["static_data_available"]:
        warnings.append({"level": "error", "message": "Static market data cache is missing; refresh the local cache before appraising."})
    if price_errors:
        warnings.extend({"level": "warning", "message": str(error.get("error") or error)} for error in price_errors[:8])
    if totals["unpriceable_count"]:
        warnings.append({"level": "warning", "message": "Blueprint copies or unpriceable rows were left out of ISK totals."})
    if totals["low_confidence_count"]:
        warnings.append({"level": "warning", "message": "Low-confidence rows need manual review for thin order depth or missing side prices."})
    payload: dict[str, Any] = {
        "ok": True,
        "generated_at": deps.now_iso(),
        "source": "local-bulk-appraisal",
        "persistence": "none",
        "hub": {"key": hub_key, "label": str(hub["label"]), "system_id": int(hub["system_id"]), "region_id": int(hub["region_id"])},
        "hub_options": bulk_appraisal_hub_options(),
        "items": priced_items,
        "unresolved_lines": unresolved_lines,
        "ignored_line_count": parsed["ignored_line_count"],
        "raw_line_count": parsed["raw_line_count"],
        "totals": totals,
        "warnings": warnings,
        "market_cache": deps.market_order_cache_status(),
        "notes": [
            "Uses local static market data and public ESI market orders only.",
            "No EVE SSO scope or token is required for this appraisal tab.",
            "Appraisals are not stored by the server.",
        ],
    }
    payload["export_text"] = build_bulk_appraisal_export_text(payload, deps=deps)
    return payload
