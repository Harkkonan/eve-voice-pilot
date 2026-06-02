from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGETS_PATH = ROOT / "data" / "eve_trade_targets.json"
DEFAULT_TYPE_CACHE_PATH = ROOT / "cache" / "eve_type_metadata.json"
DEFAULT_BASE_URL = "https://webapi.eveworkbench.com"
DEFAULT_ESI_BASE_URL = "https://esi.evetech.net/latest"
DEFAULT_RUN_TYPE = "sell-buy"
DEFAULT_VOLUME = 10_000.0
DEFAULT_TOP = 8

INDUSTRIAL_CATEGORY_IDS = {
    4,  # Material
    8,  # Charge
    25,  # Asteroid
    43,  # Planetary Commodities
}
INDUSTRIAL_GROUP_IDS = {
    334,  # Construction Components
    355,  # Refinables
    530,  # Materials and Compounds
    536,  # Structure Components
    711,  # Harvestable Cloud
    873,  # Capital Construction Components
    964,  # Hybrid Tech Components
    1031,  # Planetary Resources
    1118,  # Surface Infrastructure Prefab Units
    1314,  # Unknown Components
    1886,  # Technical Data Chips
    4165,  # Peculiar Materials
    4716,  # Abyssal Battlefield Filament Materials
    4821,  # Atavum
    4972,  # Verity Cryo Tech
}
INDUSTRIAL_GENERAL_ITEM_NAMES = {
    "carbon",
    "data sheets",
    "electronic parts",
    "hardware",
    "hydrogen batteries",
}
MATERIAL_GROUP_IDS = INDUSTRIAL_GROUP_IDS | {
    423,  # Ice Product
    429,  # Composite
    754,  # Salvaged Materials
    974,  # Hybrid Polymers
}
MATERIAL_CATEGORY_IDS = {
    43,  # Planetary Commodities
}
MINERAL_GROUP_IDS = {
    18,  # Mineral
}
MINERAL_CATEGORY_IDS = {
    25,  # Asteroid
}


class TradeAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class SolarSystem:
    name: str
    system_id: int
    security_status: str = ""
    security_status_class: str = ""


@dataclass(frozen=True)
class TradeOrder:
    price: float
    location_id: int | None
    location_name: str
    volume_remain: int | None
    volume_total: int | None
    first_seen_date: str = ""
    last_update: str = ""


@dataclass(frozen=True)
class RouteSystem:
    name: str
    security: float


@dataclass(frozen=True)
class TradeOpportunity:
    type_id: int
    type_name: str
    packaged_volume: float
    isk_per_jump: float
    isk_per_m3: float
    max_quantity: int
    max_total_volume: float
    price_diff: float
    from_order: TradeOrder
    to_order: TradeOrder

    @property
    def buy_total(self) -> float:
        return self.from_order.price * self.max_quantity

    @property
    def sell_total(self) -> float:
        return self.to_order.price * self.max_quantity

    @property
    def total_profit(self) -> float:
        return self.price_diff * self.max_quantity


@dataclass(frozen=True)
class ItemMetadata:
    type_id: int
    type_name: str
    group_id: int
    group_name: str
    category_id: int
    category_name: str


@dataclass(frozen=True)
class TradePlan:
    origin: SolarSystem
    destination: SolarSystem
    route: tuple[RouteSystem, ...]
    opportunities: tuple[TradeOpportunity, ...]

    @property
    def jumps(self) -> int:
        return max(len(self.route) - 1, 0)

    @property
    def min_security(self) -> float | None:
        if not self.route:
            return None
        return min(system.security for system in self.route)


@dataclass(frozen=True)
class RankedOpportunity:
    destination: SolarSystem
    jumps: int
    min_security: float | None
    opportunity: TradeOpportunity
    quantity: int
    buy_total: float
    sell_total: float
    total_profit: float
    total_volume: float
    metadata: ItemMetadata | None = None

    @property
    def profit_per_jump(self) -> float:
        return self.total_profit / max(self.jumps, 1)

    @property
    def profit_per_m3(self) -> float:
        if self.total_volume <= 0:
            return 0.0
        return self.total_profit / self.total_volume

    @property
    def return_on_investment(self) -> float:
        if self.buy_total <= 0:
            return 0.0
        return self.total_profit / self.buy_total


@dataclass(frozen=True)
class DistributionRunPlan:
    origin: SolarSystem
    checked_destinations: tuple[TradePlan, ...]
    ranked: tuple[RankedOpportunity, ...]
    skipped: tuple[str, ...]


class EveWorkbenchTradeClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def typeahead_systems(self, query: str) -> list[SolarSystem]:
        payload = self._post_json("/System/TypeAhead", {"query": query})
        if not payload.get("success"):
            raise TradeAgentError(str(payload.get("message") or "System lookup failed."))
        systems = []
        for item in payload.get("result") or []:
            try:
                systems.append(
                    SolarSystem(
                        name=str(item["name"]),
                        system_id=int(item["systemId"]),
                        security_status=str(item.get("securityStatus") or ""),
                        security_status_class=str(item.get("securityStatusClass") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise TradeAgentError(f"Unexpected system lookup result: {item!r}") from exc
        return systems

    def resolve_system(self, query: str) -> SolarSystem:
        query = query.strip()
        if not query:
            raise TradeAgentError("System name is required.")
        systems = self.typeahead_systems(query)
        if not systems:
            raise TradeAgentError(f"No EVE system matched {query!r}.")

        exact = [system for system in systems if system.name.casefold() == query.casefold()]
        if exact:
            return exact[0]
        if len(systems) == 1:
            return systems[0]

        names = ", ".join(system.name for system in systems[:8])
        raise TradeAgentError(f"{query!r} matched multiple systems: {names}. Use a more exact name.")

    def run_trade_tool(
        self,
        origin: SolarSystem,
        destination: SolarSystem,
        *,
        run_type: str = DEFAULT_RUN_TYPE,
        volume: float = DEFAULT_VOLUME,
    ) -> TradePlan:
        payload = self._post_json(
            "/TradeTool/RunTradeTool",
            {
                "fromSystemId": origin.system_id,
                "toSystemId": destination.system_id,
                "runType": run_type,
                "volume": volume,
            },
        )
        if not payload.get("success"):
            raise TradeAgentError(str(payload.get("message") or "Trade tool request failed."))

        result = payload.get("result") or {}
        return TradePlan(
            origin=origin,
            destination=destination,
            route=tuple(_parse_route_system(item) for item in result.get("route") or []),
            opportunities=tuple(_parse_opportunity(item) for item in result.get("trades") or []),
        )

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "EVE Voice Pilot Trade Agent",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TradeAgentError(f"EVE Workbench returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise TradeAgentError(f"Could not reach EVE Workbench: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TradeAgentError("Timed out waiting for EVE Workbench.") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TradeAgentError(f"EVE Workbench returned non-JSON data: {raw[:200]!r}") from exc
        if not isinstance(parsed, dict):
            raise TradeAgentError(f"EVE Workbench returned an unexpected payload: {parsed!r}")
        return parsed


class EveTypeMetadataClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_ESI_BASE_URL,
        cache_path: Path = DEFAULT_TYPE_CACHE_PATH,
        timeout_seconds: float = 20.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.cache_path = cache_path
        self.timeout_seconds = timeout_seconds
        self._type_cache: dict[str, dict[str, Any]] = {}
        self._load_cache()

    def get_type_metadata(self, type_id: int) -> ItemMetadata:
        cached = self._type_cache.get(str(type_id))
        if cached:
            return _metadata_from_dict(cached)

        metadata = self._fetch_type_metadata(type_id)
        self._type_cache[str(type_id)] = _metadata_to_dict(metadata)
        self._save_cache()
        return metadata

    def warm_type_metadata(self, type_ids: Iterable[int]) -> None:
        missing = sorted({int(type_id) for type_id in type_ids if str(type_id) not in self._type_cache})
        if not missing:
            return

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(self._fetch_type_metadata, type_id): type_id for type_id in missing}
            for future in as_completed(futures):
                type_id = futures[future]
                try:
                    metadata = future.result()
                except TradeAgentError as exc:
                    raise TradeAgentError(f"Could not classify type {type_id}: {exc}") from exc
                self._type_cache[str(type_id)] = _metadata_to_dict(metadata)
        self._save_cache()

    def _fetch_type_metadata(self, type_id: int) -> ItemMetadata:
        type_payload = self._get_json(f"/universe/types/{type_id}/?datasource=tranquility&language=en")
        group_id = int(type_payload.get("group_id") or 0)
        group_payload = self._get_json(f"/universe/groups/{group_id}/?datasource=tranquility&language=en")
        category_id = int(group_payload.get("category_id") or 0)
        category_payload = self._get_json(
            f"/universe/categories/{category_id}/?datasource=tranquility&language=en"
        )
        return ItemMetadata(
            type_id=type_id,
            type_name=str(type_payload.get("name") or ""),
            group_id=group_id,
            group_name=str(group_payload.get("name") or ""),
            category_id=category_id,
            category_name=str(category_payload.get("name") or ""),
        )

    def _get_json(self, path: str) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}{path}",
            headers={
                "Accept": "application/json",
                "User-Agent": "EVE Voice Pilot Trade Agent",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TradeAgentError(f"ESI returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise TradeAgentError(f"Could not reach ESI: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TradeAgentError("Timed out waiting for ESI.") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TradeAgentError(f"ESI returned non-JSON data: {raw[:200]!r}") from exc
        if not isinstance(parsed, dict):
            raise TradeAgentError(f"ESI returned an unexpected payload: {parsed!r}")
        return parsed

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("types"), dict):
            self._type_cache = payload["types"]

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps({"types": self._type_cache}, indent=2), encoding="utf-8")
        except OSError as exc:
            raise TradeAgentError(f"Could not write type metadata cache: {exc}") from exc


def plan_distribution_run(
    client: EveWorkbenchTradeClient,
    *,
    from_system: str,
    to_system: str | None = None,
    max_jumps: int | None = None,
    volume: float = DEFAULT_VOLUME,
    top: int = DEFAULT_TOP,
    run_type: str = DEFAULT_RUN_TYPE,
    sort_by: str = "isk-per-jump",
    highsec_only: bool = False,
    min_profit: float = 0.0,
    budget: float | None = None,
    item_domain: str = "all",
    prefer: str = "none",
    metadata_client: EveTypeMetadataClient | None = None,
    target_names: Iterable[str] | None = None,
) -> DistributionRunPlan:
    origin = client.resolve_system(from_system)
    destinations = _destination_systems(client, origin, to_system, target_names)
    checked: list[TradePlan] = []
    ranked: list[RankedOpportunity] = []
    skipped: list[str] = []
    if item_domain != "all" and metadata_client is None:
        metadata_client = EveTypeMetadataClient()

    for destination in destinations:
        try:
            trade_plan = client.run_trade_tool(
                origin,
                destination,
                run_type=run_type,
                volume=volume,
            )
        except TradeAgentError as exc:
            skipped.append(f"{destination.name}: {exc}")
            continue

        if max_jumps is not None and trade_plan.jumps > max_jumps:
            skipped.append(f"{destination.name}: {trade_plan.jumps} jumps exceeds max {max_jumps}")
            continue
        if highsec_only and trade_plan.min_security is not None and trade_plan.min_security < 0.45:
            skipped.append(f"{destination.name}: route dips below highsec ({trade_plan.min_security:.1f})")
            continue

        checked.append(trade_plan)
        if metadata_client is not None:
            metadata_client.warm_type_metadata(
                opportunity.type_id
                for opportunity in trade_plan.opportunities
                if _effective_quantity(opportunity, volume=volume, budget=budget) > 0
                and opportunity.price_diff > 0
                and opportunity.isk_per_jump >= 0
            )
        for opportunity in trade_plan.opportunities:
            quantity = _effective_quantity(opportunity, volume=volume, budget=budget)
            if quantity <= 0:
                continue
            total_profit = opportunity.price_diff * quantity
            if total_profit <= min_profit:
                continue
            if opportunity.price_diff <= 0:
                continue
            if opportunity.isk_per_jump < 0:
                continue
            metadata = None
            if item_domain != "all":
                if metadata_client is None:
                    raise TradeAgentError("Item metadata is required for item-domain filtering.")
                metadata = metadata_client.get_type_metadata(opportunity.type_id)
                if not _matches_item_domain(opportunity, metadata, item_domain):
                    continue
            ranked.append(
                RankedOpportunity(
                    destination=destination,
                    jumps=trade_plan.jumps,
                    min_security=trade_plan.min_security,
                    opportunity=opportunity,
                    quantity=quantity,
                    buy_total=opportunity.from_order.price * quantity,
                    sell_total=opportunity.to_order.price * quantity,
                    total_profit=total_profit,
                    total_volume=opportunity.packaged_volume * quantity,
                    metadata=metadata,
                )
            )

    ranked.sort(key=lambda item: _sort_key(item, sort_by, prefer), reverse=True)
    return DistributionRunPlan(
        origin=origin,
        checked_destinations=tuple(checked),
        ranked=tuple(ranked[:top]),
        skipped=tuple(skipped),
    )


def load_target_names(path: Path = DEFAULT_TARGETS_PATH) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TradeAgentError(f"Could not read target list {path}: {exc}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise TradeAgentError(f"Target list {path} should be a JSON array of system names.")
    return payload


def format_plan(
    plan: DistributionRunPlan,
    *,
    volume: float,
    sort_by: str,
    budget: float | None = None,
    item_domain: str = "all",
    prefer: str = "none",
    output_format: str = "full",
) -> str:
    if output_format == "compact":
        return _format_compact_plan(
            plan,
            volume=volume,
            sort_by=sort_by,
            budget=budget,
            item_domain=item_domain,
            prefer=prefer,
        )

    lines: list[str] = []
    lines.append("EVE Workbench sell-buy distribution plan")
    lines.append(f"From: {plan.origin.name} ({plan.origin.system_id})")
    lines.append(f"Cargo volume limit: {_format_number(volume)} m3")
    if budget is not None:
        lines.append(f"Budget limit: {_format_isk(budget)}")
    if item_domain != "all":
        lines.append(f"Item filter: {item_domain}")
    if prefer != "none":
        lines.append(f"Preference: {prefer}")
    lines.append(f"Sorted by: {sort_by}")
    lines.append("")

    if plan.checked_destinations:
        lines.append("Routes checked:")
        for checked in plan.checked_destinations:
            security = "unknown" if checked.min_security is None else f"{checked.min_security:.1f}"
            lines.append(f"- {checked.destination.name}: {checked.jumps} jumps, min security {security}")
        lines.append("")

    if not plan.ranked:
        lines.append("No positive sell-buy opportunities matched these filters.")
    else:
        lines.append("Suggested buys:")
        for index, ranked in enumerate(plan.ranked, start=1):
            item = ranked.opportunity
            security = "unknown" if ranked.min_security is None else f"{ranked.min_security:.1f}"
            roi = ranked.return_on_investment * 100
            lines.append(f"{index}. {item.type_name}")
            if ranked.metadata:
                lines.append(
                    f"   Type: {_item_role(item, ranked.metadata)} / {ranked.metadata.group_name}."
                )
            lines.append(
                f"   Buy {ranked.quantity} near {item.from_order.location_name} "
                f"at {_format_isk(item.from_order.price)} each; spend {_format_isk(ranked.buy_total)}."
            )
            lines.append(
                f"   Sell at {item.to_order.location_name} for {_format_isk(item.to_order.price)} each."
            )
            lines.append(
                f"   Profit: {_format_isk(ranked.total_profit)} total, "
                f"{_format_isk(ranked.profit_per_jump)}/jump, "
                f"{_format_isk(ranked.profit_per_m3)}/m3, {roi:.1f}% gross ROI."
            )
            lines.append(
                f"   Cargo: {_format_number(ranked.total_volume)} m3; "
                f"route to {ranked.destination.name} is {ranked.jumps} jumps, min security {security}."
            )
            lines.append(
                "   Why: positive spread, capped to your limits, and the destination buy order "
                f"shows {item.to_order.volume_remain or 0} remaining."
            )
        lines.append("")
        lines.append("Check orders in game before hauling; market orders can fill or move.")

    if plan.skipped:
        lines.append("")
        lines.append("Skipped:")
        for skipped in plan.skipped:
            lines.append(f"- {skipped}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan EVE distribution runs using the EVE Workbench sell-buy trade tool.",
    )
    parser.add_argument(
        "--route",
        help='Comma-separated route systems, like "Amarr,Jita,Hek,Rens,Amarr". Overrides --from/--to.',
    )
    parser.add_argument("--from", dest="from_system", help="Current solar system, like Jita.")
    parser.add_argument("--to", dest="to_system", help="Destination solar system, like Amarr.")
    parser.add_argument("--max-jumps", type=int, help="Only keep target routes at or below this jump count.")
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME, help="Cargo volume limit in m3.")
    parser.add_argument("--budget", type=float, help="Available ISK. Suggestions are capped to this spend.")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="Number of suggestions to print.")
    parser.add_argument("--run-type", choices=("sell-buy", "sell-sell"), default=DEFAULT_RUN_TYPE)
    parser.add_argument(
        "--sort-by",
        choices=("isk-per-jump", "profit", "isk-per-m3"),
        default="isk-per-jump",
        help="How to rank suggested items.",
    )
    parser.add_argument("--highsec-only", action="store_true", help="Skip routes that dip below 0.5 security.")
    parser.add_argument(
        "--min-profit",
        type=float,
        default=0.0,
        help="Minimum total item profit in ISK before an opportunity is shown.",
    )
    parser.add_argument(
        "--item-domain",
        choices=("all", "industrial"),
        default="all",
        help="Use 'industrial' for minerals, materials, ores, PI goods, and ammunition/charges.",
    )
    parser.add_argument(
        "--prefer",
        choices=("none", "materials"),
        default="none",
        help="Boost material-style goods above mineral/ore fillers when ranking.",
    )
    parser.add_argument(
        "--format",
        choices=("full", "compact"),
        default="full",
        help="Output detail level.",
    )
    parser.add_argument(
        "--targets-file",
        type=Path,
        default=DEFAULT_TARGETS_PATH,
        help="JSON list of target systems used when --to is not provided.",
    )
    parser.add_argument(
        "--targets",
        help="Comma-separated destination systems for this run, used when --to is not provided.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.route:
        client = EveWorkbenchTradeClient(base_url=args.base_url)
        metadata_client = EveTypeMetadataClient() if args.item_domain != "all" else None
        try:
            output = _format_route_plan(
                client,
                route=args.route,
                volume=args.volume,
                budget=args.budget,
                top=args.top,
                run_type=args.run_type,
                sort_by=args.sort_by,
                highsec_only=args.highsec_only,
                min_profit=args.min_profit,
                item_domain=args.item_domain,
                prefer=args.prefer,
                output_format=args.format,
                metadata_client=metadata_client,
            )
        except TradeAgentError as exc:
            print(f"Trade agent error: {exc}", file=sys.stderr)
            return 1
        print(output)
        return 0

    if args.from_system is None:
        args.from_system = input("Where are you now? ").strip()
    if args.to_system is None and args.max_jumps is None and args.targets is None:
        destination = input("Where do you want to go? Leave blank to use max jumps: ").strip()
        if destination:
            args.to_system = destination
        else:
            max_jumps = input("Maximum jumps? ").strip()
            try:
                args.max_jumps = int(max_jumps)
            except ValueError:
                parser.error("--max-jumps must be a whole number.")

    target_names = _target_names_from_args(args.targets, args.targets_file)
    client = EveWorkbenchTradeClient(base_url=args.base_url)
    metadata_client = EveTypeMetadataClient() if args.item_domain != "all" else None

    try:
        plan = plan_distribution_run(
            client,
            from_system=args.from_system,
            to_system=args.to_system,
            max_jumps=args.max_jumps,
            volume=args.volume,
            top=args.top,
            run_type=args.run_type,
            sort_by=args.sort_by,
            highsec_only=args.highsec_only,
            min_profit=args.min_profit,
            budget=args.budget,
            item_domain=args.item_domain,
            prefer=args.prefer,
            metadata_client=metadata_client,
            target_names=target_names,
        )
    except TradeAgentError as exc:
        print(f"Trade agent error: {exc}", file=sys.stderr)
        return 1

    print(
        format_plan(
            plan,
            volume=args.volume,
            sort_by=args.sort_by,
            budget=args.budget,
            item_domain=args.item_domain,
            prefer=args.prefer,
            output_format=args.format,
        )
    )
    return 0


def _format_route_plan(
    client: EveWorkbenchTradeClient,
    *,
    route: str,
    volume: float,
    budget: float | None,
    top: int,
    run_type: str,
    sort_by: str,
    highsec_only: bool,
    min_profit: float,
    item_domain: str,
    prefer: str,
    output_format: str,
    metadata_client: EveTypeMetadataClient | None,
) -> str:
    names = [item.strip() for item in route.split(",") if item.strip()]
    if len(names) < 2:
        raise TradeAgentError("--route needs at least two comma-separated systems.")

    lines: list[str] = []
    lines.append("EVE Workbench sell-buy route plan")
    lines.append(f"Route: {' -> '.join(names)}")
    lines.append(f"Cargo volume limit: {_format_number(volume)} m3")
    if budget is not None:
        lines.append(f"Budget limit per leg: {_format_isk(budget)}")
    if item_domain != "all":
        lines.append(f"Item filter: {item_domain}")
    if prefer != "none":
        lines.append(f"Preference: {prefer}")
    lines.append(f"Sorted by: {sort_by}")
    lines.append("")

    for from_system, to_system in zip(names, names[1:]):
        plan = plan_distribution_run(
            client,
            from_system=from_system,
            to_system=to_system,
            volume=volume,
            top=top,
            run_type=run_type,
            sort_by=sort_by,
            highsec_only=highsec_only,
            min_profit=min_profit,
            budget=budget,
            item_domain=item_domain,
            prefer=prefer,
            metadata_client=metadata_client,
        )
        if output_format == "compact":
            lines.append(_format_compact_leg(plan))
        else:
            lines.append(
                format_plan(
                    plan,
                    volume=volume,
                    sort_by=sort_by,
                    budget=budget,
                    item_domain=item_domain,
                    prefer=prefer,
                    output_format=output_format,
                )
            )
        lines.append("")

    lines.append("Check orders in game before hauling; market orders can fill or move.")
    return "\n".join(lines).rstrip()


def _format_compact_plan(
    plan: DistributionRunPlan,
    *,
    volume: float,
    sort_by: str,
    budget: float | None,
    item_domain: str,
    prefer: str,
) -> str:
    lines: list[str] = []
    lines.append("EVE Workbench sell-buy distribution plan")
    lines.append(f"From: {plan.origin.name} ({plan.origin.system_id})")
    lines.append(f"Cargo volume limit: {_format_number(volume)} m3")
    if budget is not None:
        lines.append(f"Budget limit: {_format_isk(budget)}")
    if item_domain != "all":
        lines.append(f"Item filter: {item_domain}")
    if prefer != "none":
        lines.append(f"Preference: {prefer}")
    lines.append(f"Sorted by: {sort_by}")
    lines.append("")
    lines.append(_format_compact_leg(plan))
    lines.append("")
    lines.append("Check orders in game before hauling; market orders can fill or move.")
    if plan.skipped:
        lines.append("")
        lines.append("Skipped:")
        for skipped in plan.skipped:
            lines.append(f"- {skipped}")
    return "\n".join(lines)


def _format_compact_leg(plan: DistributionRunPlan) -> str:
    if plan.checked_destinations:
        if len(plan.checked_destinations) == 1:
            route = plan.checked_destinations[0]
            security = "unknown" if route.min_security is None else f"{route.min_security:.1f}"
            header = f"{plan.origin.name} -> {route.destination.name}: {route.jumps} jumps, min security {security}"
        else:
            labels = []
            for route in plan.checked_destinations:
                security = "unknown" if route.min_security is None else f"{route.min_security:.1f}"
                labels.append(f"{route.destination.name} {route.jumps}j/{security}")
            header = f"{plan.origin.name} target scan: {', '.join(labels)}"
    else:
        header = f"{plan.origin.name}: no route checked"

    lines = [header]
    if not plan.ranked:
        lines.append("No positive opportunities matched these filters.")
        if plan.skipped:
            lines.extend(f"Skipped: {skipped}" for skipped in plan.skipped)
        return "\n".join(lines)

    for index, ranked in enumerate(plan.ranked, start=1):
        item = ranked.opportunity
        group = ""
        if ranked.metadata:
            group = f", {_item_role(item, ranked.metadata)}, {ranked.metadata.group_name}"
        destination = "" if len(plan.checked_destinations) == 1 else f" to {ranked.destination.name}"
        lines.append(
            f"{index}. {item.type_name}{group}{destination}: buy {ranked.quantity} at {_format_isk(item.from_order.price)} ea, "
            f"spend {_format_isk(ranked.buy_total)}, profit {_format_isk(ranked.total_profit)}, "
            f"ROI {ranked.return_on_investment * 100:.1f}%, cargo {_format_number(ranked.total_volume)} m3, "
            f"dest order {item.to_order.volume_remain or 0}"
        )
    return "\n".join(lines)


def _destination_systems(
    client: EveWorkbenchTradeClient,
    origin: SolarSystem,
    to_system: str | None,
    target_names: Iterable[str] | None,
) -> list[SolarSystem]:
    if to_system:
        destination = client.resolve_system(to_system)
        if destination.system_id == origin.system_id:
            raise TradeAgentError("Destination is the same as your current system.")
        return [destination]

    names = list(target_names or [])
    if not names:
        raise TradeAgentError("No destination provided and the target list is empty.")

    destinations: list[SolarSystem] = []
    seen: set[int] = {origin.system_id}
    for name in names:
        destination = client.resolve_system(name)
        if destination.system_id in seen:
            continue
        seen.add(destination.system_id)
        destinations.append(destination)
    return destinations


def _target_names_from_args(targets: str | None, targets_file: Path) -> list[str]:
    if targets:
        return [item.strip() for item in targets.split(",") if item.strip()]
    return load_target_names(targets_file)


def _parse_route_system(item: dict[str, Any]) -> RouteSystem:
    return RouteSystem(
        name=str(item.get("name") or ""),
        security=float(item.get("security") or 0),
    )


def _parse_order(item: dict[str, Any]) -> TradeOrder:
    return TradeOrder(
        price=float(item.get("price") or 0),
        location_id=_optional_int(item.get("locationId")),
        location_name=str(item.get("locationName") or "Unknown location"),
        volume_remain=_optional_int(item.get("volumeRemain")),
        volume_total=_optional_int(item.get("volumeTotal")),
        first_seen_date=str(item.get("firstSeenDate") or ""),
        last_update=str(item.get("lastUpdate") or ""),
    )


def _parse_opportunity(item: dict[str, Any]) -> TradeOpportunity:
    return TradeOpportunity(
        type_id=int(item.get("typeId") or 0),
        type_name=str(item.get("typeName") or "Unknown item"),
        packaged_volume=float(item.get("packagedVolume") or 0),
        isk_per_jump=float(item.get("iskPerJump") or 0),
        isk_per_m3=float(item.get("iskPerM3") or 0),
        max_quantity=int(item.get("maxQuantity") or 0),
        max_total_volume=float(item.get("maxTotalVolume") or 0),
        price_diff=float(item.get("priceDiff") or 0),
        from_order=_parse_order(item.get("fromOrder") or {}),
        to_order=_parse_order(item.get("toOrder") or {}),
    )


def _effective_quantity(opportunity: TradeOpportunity, *, volume: float, budget: float | None) -> int:
    limits = [opportunity.max_quantity]

    if opportunity.from_order.volume_remain is not None:
        limits.append(opportunity.from_order.volume_remain)
    if opportunity.to_order.volume_remain is not None:
        limits.append(opportunity.to_order.volume_remain)
    if opportunity.packaged_volume > 0:
        limits.append(int(volume // opportunity.packaged_volume))
    if budget is not None and opportunity.from_order.price > 0:
        limits.append(int(budget // opportunity.from_order.price))

    positive_limits = [limit for limit in limits if limit is not None]
    if not positive_limits:
        return 0
    return max(min(positive_limits), 0)


def _matches_item_domain(opportunity: TradeOpportunity, metadata: ItemMetadata, item_domain: str) -> bool:
    if item_domain == "all":
        return True
    if item_domain != "industrial":
        raise TradeAgentError(f"Unknown item domain {item_domain!r}.")

    if metadata.category_id in INDUSTRIAL_CATEGORY_IDS:
        return True
    if metadata.group_id in INDUSTRIAL_GROUP_IDS:
        return True
    if metadata.group_id == 280 and opportunity.type_name.casefold() in INDUSTRIAL_GENERAL_ITEM_NAMES:
        return True
    return False


def _item_role(opportunity: TradeOpportunity, metadata: ItemMetadata) -> str:
    if metadata.group_id in MINERAL_GROUP_IDS or metadata.category_id in MINERAL_CATEGORY_IDS:
        return "mineral/ore"
    if metadata.category_id == 8:
        return "charge"
    if metadata.category_id in MATERIAL_CATEGORY_IDS:
        return "material"
    if metadata.group_id in MATERIAL_GROUP_IDS:
        return "material"
    if metadata.group_id == 280 and opportunity.type_name.casefold() in INDUSTRIAL_GENERAL_ITEM_NAMES:
        return "material"
    return "industrial"


def _preference_multiplier(item: RankedOpportunity, prefer: str) -> float:
    if prefer == "none" or item.metadata is None:
        return 1.0
    if prefer != "materials":
        raise TradeAgentError(f"Unknown preference {prefer!r}.")

    role = _item_role(item.opportunity, item.metadata)
    if role == "material":
        return 1.25
    if role == "charge":
        return 1.05
    return 1.0


def _metadata_to_dict(metadata: ItemMetadata) -> dict[str, Any]:
    return {
        "type_id": metadata.type_id,
        "type_name": metadata.type_name,
        "group_id": metadata.group_id,
        "group_name": metadata.group_name,
        "category_id": metadata.category_id,
        "category_name": metadata.category_name,
    }


def _metadata_from_dict(item: dict[str, Any]) -> ItemMetadata:
    return ItemMetadata(
        type_id=int(item.get("type_id") or 0),
        type_name=str(item.get("type_name") or ""),
        group_id=int(item.get("group_id") or 0),
        group_name=str(item.get("group_name") or ""),
        category_id=int(item.get("category_id") or 0),
        category_name=str(item.get("category_name") or ""),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sort_key(item: RankedOpportunity, sort_by: str, prefer: str) -> float:
    if sort_by == "profit":
        base_score = item.total_profit
    elif sort_by == "isk-per-m3":
        base_score = item.profit_per_m3
    else:
        base_score = item.profit_per_jump
    return base_score * _preference_multiplier(item, prefer)


def _format_isk(value: float) -> str:
    return f"{_format_number(value)} ISK"


def _format_number(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}b"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}m"
    if abs_value >= 1_000:
        return f"{value / 1_000:.2f}k"
    if value == int(value):
        return f"{int(value)}"
    return f"{value:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
