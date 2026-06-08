from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Iterable, Mapping


@dataclass
class AcquisitionItemScanResult:
    opportunity: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    source_buy_order_count: int = 0
    source_sell_order_count: int = 0
    destination_buy_order_count: int = 0
    history_request_count: int = 0
    source_history_gated_count: int = 0
    history_region_ids: set[int] = field(default_factory=set)
    risk_level: str = ""
    order_fetch_seconds: float = 0.0
    order_filter_seconds: float = 0.0
    history_fetch_seconds: float = 0.0
    opportunity_score_seconds: float = 0.0



def scan_acquisition_item_opportunity(
    *,
    deps: Any,
    config: EveSsoConfig,
    target: Mapping[str, Any],
    item_index: int,
    item_count: int,
    pickup_region_ids: Iterable[int],
    region_count: int,
    origin: RouteSystem,
    destination: RouteSystem,
    systems: Mapping[int, RouteSystem],
    pickup_distances: Mapping[int, int],
    destination_distances: Mapping[int, int],
    budget_isk: float,
    pickup_jumps: int,
    min_margin_percent: float,
    broker_fee_rate: float,
    sales_tax_rate: float,
    target_days: int,
    progress_percent: Callable[[int, float], int],
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> AcquisitionItemScanResult:
    result = AcquisitionItemScanResult()
    type_id = int(target["type_id"])
    item_name = str(target.get("name") or f"Type {type_id}")
    source_buy_orders: list[dict[str, Any]] = []
    source_sell_orders: list[dict[str, Any]] = []
    scan_pickup_region_ids = [int(region_id) for region_id in pickup_region_ids]

    if progress is not None:
        progress(
            "item_start",
            {
                "message": f"Checking {item_name} ({item_index}/{item_count}).",
                "item_index": item_index,
                "item_count": item_count,
                "item_name": item_name,
                "type_id": type_id,
                "percent": progress_percent(item_index, 0.0),
            },
        )

    timed_started_at = time.monotonic()
    (
        raw_source_buys_by_region,
        raw_source_sells_by_region,
        raw_destination_buys,
        order_errors,
    ) = deps.fetch_acquisition_order_batch(
        config=config,
        type_id=type_id,
        pickup_region_ids=scan_pickup_region_ids,
        destination_region_id=destination.region_id,
    )
    result.order_fetch_seconds += max(0.0, time.monotonic() - timed_started_at)
    result.errors.extend(order_errors)

    timed_started_at = time.monotonic()
    for region_index, region_id in enumerate(scan_pickup_region_ids, start=1):
        raw_source_buys = raw_source_buys_by_region.get(region_id, [])
        for order in raw_source_buys:
            record = deps.build_reachable_market_order_record(
                order,
                systems=systems,
                jump_distances=pickup_distances,
                region_id=region_id,
                order_type="buy",
            )
            if record is not None:
                source_buy_orders.append(record)
        raw_source_sells = raw_source_sells_by_region.get(region_id, [])
        for order in raw_source_sells:
            record = deps.build_reachable_market_order_record(
                order,
                systems=systems,
                jump_distances=pickup_distances,
                region_id=region_id,
                order_type="sell",
            )
            if record is not None:
                source_sell_orders.append(record)
        if progress is not None:
            progress(
                "orders",
                {
                    "message": (
                        f"{item_name}: pickup region {region_index}/{region_count} returned "
                        f"{len(raw_source_buys)} buy rows and {len(raw_source_sells)} sell rows."
                    ),
                    "item_index": item_index,
                    "item_count": item_count,
                    "item_name": item_name,
                    "region_index": region_index,
                    "region_count": region_count,
                    "percent": progress_percent(item_index, 0.45 * (region_index / max(1, region_count))),
                },
            )

    destination_buy_orders = []
    for order in raw_destination_buys:
        record = deps.build_reachable_market_order_record(
            order,
            systems=systems,
            jump_distances=destination_distances,
            region_id=destination.region_id,
            order_type="buy",
        )
        if record is not None:
            destination_buy_orders.append(record)
    result.order_filter_seconds += max(0.0, time.monotonic() - timed_started_at)
    if progress is not None:
        progress(
            "orders",
            {
                "message": f"{item_name}: destination region returned {len(raw_destination_buys)} buy rows.",
                "item_index": item_index,
                "item_count": item_count,
                "item_name": item_name,
                "percent": progress_percent(item_index, 0.55),
            },
        )

    source_buy_orders.sort(key=lambda item: deps.market_order_sort_key(item, order_type="buy"))
    source_sell_orders.sort(key=lambda item: deps.market_order_sort_key(item, order_type="sell"))
    destination_buy_orders.sort(key=lambda item: deps.market_order_sort_key(item, order_type="buy"))
    result.source_buy_order_count = len(source_buy_orders)
    result.source_sell_order_count = len(source_sell_orders)
    result.destination_buy_order_count = len(destination_buy_orders)
    if not destination_buy_orders:
        return result

    source_region_id = origin.region_id or (scan_pickup_region_ids[0] if scan_pickup_region_ids else None)
    if source_region_id is None:
        return result

    source_history: list[dict[str, Any]] = []
    destination_history: list[dict[str, Any]] = []
    has_source_order_signal = bool(source_buy_orders or source_sell_orders)
    if has_source_order_signal:
        timed_started_at = time.monotonic()
        source_history, destination_history, history_errors = deps.fetch_acquisition_history_batch(
            config=config,
            type_id=type_id,
            source_region_id=source_region_id,
            destination_region_id=destination.region_id,
        )
        result.history_fetch_seconds += max(0.0, time.monotonic() - timed_started_at)
        result.history_request_count += 1 if int(destination.region_id) == int(source_region_id) else 2
        result.errors.extend(history_errors)
    else:
        timed_started_at = time.monotonic()
        try:
            source_history = deps.fetch_market_history(config, region_id=source_region_id, type_id=type_id)
        except deps.CorpMarketError as exc:
            result.errors.append(
                {"history": "source", "type_id": type_id, "region_id": source_region_id, "error": str(exc)}
            )
            source_history = []
        result.history_fetch_seconds += max(0.0, time.monotonic() - timed_started_at)
        result.history_request_count += 1
        if not source_history:
            result.source_history_gated_count += 1
            if progress is not None:
                progress(
                    "history_skip",
                    {
                        "message": f"{item_name}: skipped destination market history because no pickup orders or source history were found.",
                        "item_index": item_index,
                        "item_count": item_count,
                        "item_name": item_name,
                        "percent": progress_percent(item_index, 0.75),
                    },
                )
            return result
        if int(destination.region_id) == int(source_region_id):
            destination_history = source_history
        else:
            timed_started_at = time.monotonic()
            try:
                destination_history = deps.fetch_market_history(config, region_id=destination.region_id, type_id=type_id)
            except deps.CorpMarketError as exc:
                result.errors.append(
                    {"history": "destination", "type_id": type_id, "region_id": destination.region_id, "error": str(exc)}
                )
                destination_history = []
            result.history_fetch_seconds += max(0.0, time.monotonic() - timed_started_at)
            result.history_request_count += 1
    if source_history:
        result.history_region_ids.add(int(source_region_id))
    if destination_history:
        result.history_region_ids.add(int(destination.region_id))
    if progress is not None:
        progress(
            "history",
            {
                "message": f"{item_name}: checked source and destination market history.",
                "item_index": item_index,
                "item_count": item_count,
                "item_name": item_name,
                "percent": progress_percent(item_index, 0.75),
            },
        )

    timed_started_at = time.monotonic()
    try:
        best_destination_buy = destination_buy_orders[0]
        best_source_buy = source_buy_orders[0] if source_buy_orders else None
        best_source_sell = source_sell_orders[0] if source_sell_orders else None
        source_stats = deps.market_history_stats(source_history)
        destination_stats = deps.market_history_stats(destination_history)
        net_destination_price = float(best_destination_buy["price"]) * (1.0 - sales_tax_rate)
        target_margin_rate = min_margin_percent / 100.0
        max_safe_bid = net_destination_price / max((1.0 + broker_fee_rate) * (1.0 + target_margin_rate), 0.0001)
        if max_safe_bid <= 0:
            return result
        suggested_bid = deps.acquisition_suggested_bid(
            max_safe_bid=max_safe_bid,
            best_source_buy=best_source_buy,
            best_source_sell=best_source_sell,
            source_history=source_stats,
        )
        if suggested_bid <= 0:
            return result
        estimated_unit_cost = suggested_bid * (1.0 + broker_fee_rate)
        budget_units = int(budget_isk // max(estimated_unit_cost, 0.01))
        history_units = deps.acquisition_history_unit_cap(source_stats, target_days=target_days)
        destination_units = int(best_destination_buy.get("volume_remain") or 0)
        units = min(limit for limit in (budget_units, destination_units, history_units) if limit >= 0)
        if units <= 0:
            return result
        broker_fee_total = suggested_bid * units * broker_fee_rate
        bid_total = suggested_bid * units
        gross_destination_revenue = float(best_destination_buy["price"]) * units
        net_revenue = net_destination_price * units
        sales_tax_total = gross_destination_revenue - net_revenue
        net_profit = net_revenue - bid_total - broker_fee_total
        if net_profit <= 0:
            return result
        margin_percent = deps.profit_margin_percent(net_profit, bid_total + broker_fee_total)
        flags = deps.acquisition_history_flags(
            source_stats=source_stats,
            destination_stats=destination_stats,
            best_source_buy=best_source_buy,
            best_source_sell=best_source_sell,
            best_destination_buy=best_destination_buy,
            max_safe_bid=max_safe_bid,
            suggested_bid=suggested_bid,
            units=units,
            target_days=target_days,
        )
        risk_level = deps.acquisition_risk_level(flags)
        range_recommendation = deps.acquisition_range_recommendation(
            risk_level=risk_level,
            source_stats=source_stats,
            units=units,
            pickup_jumps=pickup_jumps,
            margin_percent=margin_percent,
        )
        result.risk_level = risk_level
        result.opportunity = {
            "type_id": type_id,
            "item_name": item_name,
            "recipe_count": int(target.get("recipe_count") or 0),
            "source_labels": target.get("source_labels", []),
            "market_group_id": target.get("market_group_id"),
            "market_group_name": target.get("market_group_name") or "",
            "volume_m3": target.get("volume_m3"),
            "decision": deps.acquisition_decision(risk_level=risk_level, margin_percent=margin_percent),
            "risk_level": risk_level,
            "range_recommendation": range_recommendation,
            "placement_system": origin.name,
            "pickup_jumps": pickup_jumps,
            "target_days": target_days,
            "suggested_bid": suggested_bid,
            "max_safe_bid": max_safe_bid,
            "recommended_units": units,
            "estimated_bid_total": bid_total,
            "estimated_broker_fee": broker_fee_total,
            "estimated_isk_committed": bid_total + broker_fee_total,
            "gross_destination_revenue": gross_destination_revenue,
            "net_destination_price": net_destination_price,
            "estimated_sales_tax": sales_tax_total,
            "estimated_net_revenue": net_revenue,
            "net_profit": net_profit,
            "net_profit_per_unit": net_profit / units,
            "margin_percent": margin_percent,
            "sales_tax_rate": sales_tax_rate,
            "broker_fee_rate": broker_fee_rate,
            "best_source_buy": best_source_buy,
            "best_source_sell": best_source_sell,
            "best_destination_buy": best_destination_buy,
            "source_history": source_stats,
            "destination_history": destination_stats,
            "history_flags": flags,
        }
        return result
    finally:
        result.opportunity_score_seconds += max(0.0, time.monotonic() - timed_started_at)


def scan_market_acquisition_opportunities(
    *,
    deps: Any,
    config: EveSsoConfig,
    recipe_cache: IndustryRecipeCache,
    route_cache: RouteGraphCache,
    origin: RouteSystem,
    destination: RouteSystem,
    budget_isk: float,
    pickup_jumps: int,
    portfolio_jumps: int,
    min_margin_percent: float,
    broker_fee_percent: float,
    target_days: int,
    sales_tax: dict[str, Any],
    include_common_materials: bool,
    market_group_ids: Iterable[int],
    market_type_ids: Iterable[int],
    market_type_names: Iterable[Any] = (),
    item_workers: int,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    scan_timer = deps.StageTimer()
    systems = route_cache.systems or {}
    adjacency = route_cache.adjacency or {}
    clean_budget = deps.clamp_acquisition_budget_isk(budget_isk)
    clean_pickup_jumps = deps.clamp_acquisition_pickup_jumps(pickup_jumps)
    clean_portfolio_jumps = deps.clamp_acquisition_portfolio_jumps(portfolio_jumps)
    clean_min_margin_percent = deps.clamp_haul_min_detour_margin_percent(min_margin_percent)
    clean_broker_fee_percent = deps.clamp_acquisition_broker_fee_percent(broker_fee_percent)
    clean_target_days = deps.clamp_acquisition_target_days(target_days)
    clean_item_workers = deps.clamp_acquisition_item_workers(item_workers)
    broker_fee_rate = clean_broker_fee_percent / 100.0
    sales_tax_rate = deps.clean_optional_float(sales_tax.get("rate")) or 0.0

    pickup_distances = deps.jump_distances_from(
        start_system_id=origin.solar_system_id,
        adjacency=adjacency,
        max_jumps=clean_pickup_jumps,
    )
    pickup_region_ranks: dict[int, int] = {}
    for system_id, jumps in pickup_distances.items():
        system = systems.get(system_id)
        if system is None or system.region_id is None:
            continue
        current_rank = pickup_region_ranks.get(system.region_id)
        if current_rank is None or jumps < current_rank:
            pickup_region_ranks[system.region_id] = jumps
    pickup_region_ids = [
        region_id
        for region_id, _rank in sorted(pickup_region_ranks.items(), key=lambda item: (item[1], item[0]))
    ]
    pickup_region_truncated = len(pickup_region_ids) > deps.MAX_FLIGHT_BUYER_SCAN_REGIONS
    scan_pickup_region_ids = pickup_region_ids[:deps.MAX_FLIGHT_BUYER_SCAN_REGIONS]
    if destination.region_id is None:
        raise deps.CorpMarketError("Destination system does not have a usable market region in the route graph cache.")
    scan_timer.mark(
        "route_scope",
        "Build acquisition route scope",
        pickup_systems=len(pickup_distances),
        pickup_regions=len(scan_pickup_region_ids),
    )

    item_targets, item_scope = deps.build_market_item_targets(
        config=config,
        recipe_cache=recipe_cache,
        include_common_materials=include_common_materials,
        market_group_ids=market_group_ids,
        market_type_ids=market_type_ids,
        market_type_names=market_type_names,
        common_material_limit=deps.MAX_FLIGHT_ACQUISITION_COMMON_MATERIAL_TYPES,
        scan_type_limit=deps.MAX_FLIGHT_ACQUISITION_SCAN_TYPES,
    )
    item_truncated = len(item_targets) > deps.MAX_FLIGHT_ACQUISITION_SCAN_TYPES
    scan_targets = item_targets[:deps.MAX_FLIGHT_ACQUISITION_SCAN_TYPES]
    if not scan_targets:
        unresolved_pasted_names = item_scope.get("unresolved_pasted_market_type_names") or []
        if unresolved_pasted_names:
            preview = ", ".join(str(name) for name in unresolved_pasted_names[:5])
            extra = "..." if len(unresolved_pasted_names) > 5 else ""
            raise deps.CorpMarketError(f"No pasted item names matched the static market cache: {preview}{extra}.")
        raise deps.CorpMarketError("Choose Common materials, a market category, or at least one exact item before planning acquisitions.")
    scan_timer.mark(
        "item_targets",
        "Resolve item scan targets",
        scanned_item_types=len(scan_targets),
        total_item_types=len(item_targets),
        pasted_item_names=item_scope.get("pasted_market_type_count"),
        resolved_pasted_item_names=item_scope.get("resolved_pasted_market_type_count"),
        unresolved_pasted_item_names=len(item_scope.get("unresolved_pasted_market_type_names") or []),
    )
    region_count = len(scan_pickup_region_ids)
    item_count = len(scan_targets)

    def progress_percent(item_index: int, stage_fraction: float = 0.0) -> int:
        if item_count <= 0:
            return 100
        completed = max(0.0, min(float(item_count), float(item_index - 1) + stage_fraction))
        return max(10, min(98, round(10 + (completed / item_count) * 88)))

    if progress is not None:
        progress(
            "scan_scope",
            {
                "message": (
                    f"Scanning {item_count} item types across {region_count} pickup market regions, "
                    f"then checking destination orders and market history."
                ),
                "item_count": item_count,
                "region_count": region_count,
                "pickup_system_count": len(pickup_distances),
                "percent": 10,
            },
        )

    opportunities = []
    total_source_buy_order_count = 0
    total_source_sell_order_count = 0
    total_destination_buy_order_count = 0
    total_history_request_count = 0
    source_history_gated_count = 0
    history_regions = set()
    trap_signal_count = 0
    caution_signal_count = 0
    errors = []
    destination_distances = {destination.solar_system_id: 0}
    order_fetch_seconds = 0.0
    order_filter_seconds = 0.0
    history_fetch_seconds = 0.0
    opportunity_score_seconds = 0.0

    item_worker_count = max(1, min(clean_item_workers, len(scan_targets)))

    def run_item_scan(item_index: int, target: Mapping[str, Any]) -> AcquisitionItemScanResult:
        return scan_acquisition_item_opportunity(
            deps=deps,
            config=config,
            target=target,
            item_index=item_index,
            item_count=item_count,
            pickup_region_ids=scan_pickup_region_ids,
            region_count=region_count,
            origin=origin,
            destination=destination,
            systems=systems,
            pickup_distances=pickup_distances,
            destination_distances=destination_distances,
            budget_isk=clean_budget,
            pickup_jumps=clean_pickup_jumps,
            min_margin_percent=clean_min_margin_percent,
            broker_fee_rate=broker_fee_rate,
            sales_tax_rate=sales_tax_rate,
            target_days=clean_target_days,
            progress_percent=progress_percent,
            progress=progress,
        )

    with ThreadPoolExecutor(max_workers=item_worker_count) as executor:
        futures = {
            executor.submit(run_item_scan, item_index, target): (item_index, target)
            for item_index, target in enumerate(scan_targets, start=1)
        }
        item_results: list[tuple[int, AcquisitionItemScanResult]] = []
        for future in as_completed(futures):
            item_index, _target = futures[future]
            item_results.append((item_index, future.result()))

    for item_index, item_result in sorted(item_results, key=lambda item: item[0]):
        errors.extend(item_result.errors)
        total_source_buy_order_count += item_result.source_buy_order_count
        total_source_sell_order_count += item_result.source_sell_order_count
        total_destination_buy_order_count += item_result.destination_buy_order_count
        total_history_request_count += item_result.history_request_count
        source_history_gated_count += item_result.source_history_gated_count
        history_regions.update(item_result.history_region_ids)
        order_fetch_seconds += item_result.order_fetch_seconds
        order_filter_seconds += item_result.order_filter_seconds
        history_fetch_seconds += item_result.history_fetch_seconds
        opportunity_score_seconds += item_result.opportunity_score_seconds
        if item_result.risk_level == "possible-trap":
            trap_signal_count += 1
        elif item_result.risk_level == "caution":
            caution_signal_count += 1
        if item_result.opportunity is None:
            continue
        opportunities.append(item_result.opportunity)
        if progress is not None:
            progress(
                "item_done",
                {
                    "message": f"{item_result.opportunity['item_name']}: added a portfolio opportunity.",
                    "item_index": item_index,
                    "item_count": item_count,
                    "item_name": item_result.opportunity["item_name"],
                    "opportunity_count": len(opportunities),
                    "percent": progress_percent(item_index, 1.0),
                },
            )
    scan_timer.mark(
        "orders_history_scoring",
        "Fetch orders and score market history",
        item_types=len(scan_targets),
        pickup_regions=region_count,
        item_workers=item_worker_count,
        source_buy_orders=total_source_buy_order_count,
        source_sell_orders=total_source_sell_order_count,
        destination_buy_orders=total_destination_buy_order_count,
        history_requests=total_history_request_count,
        source_history_gated_items=source_history_gated_count,
        history_regions=len(history_regions),
        opportunities=len(opportunities),
        order_fetch_seconds=round(order_fetch_seconds, 3),
        order_filter_seconds=round(order_filter_seconds, 3),
        history_fetch_seconds=round(history_fetch_seconds, 3),
        opportunity_score_seconds=round(opportunity_score_seconds, 3),
    )
    if progress is not None:
        progress(
            "portfolio",
            {
                "message": f"Ranking {len(opportunities)} viable opportunity row(s) into a diversified portfolio.",
                "opportunity_count": len(opportunities),
                "percent": 98,
            },
        )

    opportunities.sort(
        key=lambda item: (
            deps.acquisition_risk_sort_rank(str(item["risk_level"])),
            -float(item.get("net_profit") or 0.0),
            -float(item.get("margin_percent") or 0.0),
            item["item_name"],
        )
    )
    portfolio = deps.build_acquisition_investment_portfolio(
        opportunities=opportunities,
        budget_isk=clean_budget,
        max_portfolio_jumps=clean_portfolio_jumps,
        pickup_jumps=clean_pickup_jumps,
    )
    scan_timer.mark(
        "ranking_portfolio",
        "Rank results and build portfolio",
        opportunities=len(opportunities),
        portfolio_lines=portfolio.get("line_count"),
        portfolio_available=portfolio.get("available"),
    )
    stage_timing = scan_timer.to_public_dict()
    return {
        "origin_system": origin.to_dict(jumps=0),
        "destination_system": destination.to_dict(jumps=0),
        "budget_isk": clean_budget,
        "pickup_jumps": clean_pickup_jumps,
        "portfolio_jumps": clean_portfolio_jumps,
        "min_margin_percent": clean_min_margin_percent,
        "broker_fee_percent": clean_broker_fee_percent,
        "target_days": clean_target_days,
        "item_workers": clean_item_workers,
        "pickup_system_count": len(pickup_distances),
        "pickup_regions_scanned": len(scan_pickup_region_ids),
        "pickup_regions_total": len(pickup_region_ids),
        "pickup_region_truncated": pickup_region_truncated,
        "destination_region_id": destination.region_id,
        "scanned_item_types": len(scan_targets),
        "total_item_types": len(item_targets),
        "item_truncated": item_truncated,
        "item_scope": {
            **item_scope,
            "scanned_item_types": len(scan_targets),
            "total_item_types": len(item_targets),
            "item_truncated": item_truncated,
        },
        "source_buy_order_count": total_source_buy_order_count,
        "source_sell_order_count": total_source_sell_order_count,
        "destination_buy_order_count": total_destination_buy_order_count,
        "history_region_count": len(history_regions),
        "opportunity_count": len(opportunities),
        "possible_trap_count": trap_signal_count,
        "caution_count": caution_signal_count,
        "strategy": deps.build_acquisition_strategy_summary(
            opportunities=opportunities,
            origin=origin,
            destination=destination,
            item_scope=item_scope,
            budget_isk=clean_budget,
            pickup_jumps=clean_pickup_jumps,
            min_margin_percent=clean_min_margin_percent,
            broker_fee_percent=clean_broker_fee_percent,
            target_days=clean_target_days,
            scanned_item_types=len(scan_targets),
            total_item_types=len(item_targets),
            pickup_regions_scanned=len(scan_pickup_region_ids),
            item_truncated=item_truncated,
            portfolio=portfolio,
        ),
        "portfolio": portfolio,
        "opportunities": opportunities[:deps.MAX_FLIGHT_ACQUISITION_OPPORTUNITIES],
        "stage_timing": stage_timing,
        "errors": errors[:12],
        "sales_tax": sales_tax,
        "market_cache": deps.market_order_cache_status(),
        "history_cache": deps.market_history_cache_status(),
        "pricing_note": (
            "This portfolio assumes manual public buy orders near the source, later hauling, and manual sales into "
            "the current destination buy orders. Suggested bid ceilings back out sales tax, estimated broker fee, "
            "and the target margin. A possible trap flag means recent history does not support the apparent spread "
            "or fill volume; verify the item in EVE before posting an order. The page does not place orders."
        ),
    }
