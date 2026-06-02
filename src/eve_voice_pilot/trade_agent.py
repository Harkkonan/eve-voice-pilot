from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGETS_PATH = ROOT / "data" / "eve_trade_targets.json"
DEFAULT_BASE_URL = "https://webapi.eveworkbench.com"
DEFAULT_RUN_TYPE = "sell-buy"
DEFAULT_VOLUME = 10_000.0
DEFAULT_TOP = 8


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
    target_names: Iterable[str] | None = None,
) -> DistributionRunPlan:
    origin = client.resolve_system(from_system)
    destinations = _destination_systems(client, origin, to_system, target_names)
    checked: list[TradePlan] = []
    ranked: list[RankedOpportunity] = []
    skipped: list[str] = []

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
        for opportunity in trade_plan.opportunities:
            if opportunity.total_profit <= min_profit:
                continue
            if opportunity.isk_per_jump < 0:
                continue
            ranked.append(
                RankedOpportunity(
                    destination=destination,
                    jumps=trade_plan.jumps,
                    min_security=trade_plan.min_security,
                    opportunity=opportunity,
                )
            )

    ranked.sort(key=lambda item: _sort_key(item, sort_by), reverse=True)
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


def format_plan(plan: DistributionRunPlan, *, volume: float, sort_by: str) -> str:
    lines: list[str] = []
    lines.append("EVE Workbench sell-buy distribution plan")
    lines.append(f"From: {plan.origin.name} ({plan.origin.system_id})")
    lines.append(f"Cargo volume limit: {_format_number(volume)} m3")
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
            lines.append(f"{index}. {item.type_name}")
            lines.append(
                f"   Buy {item.max_quantity} near {item.from_order.location_name} "
                f"at {_format_isk(item.from_order.price)} each."
            )
            lines.append(
                f"   Sell at {item.to_order.location_name} for {_format_isk(item.to_order.price)} each."
            )
            lines.append(
                f"   Profit: {_format_isk(item.total_profit)} total, "
                f"{_format_isk(item.isk_per_jump)}/jump, {_format_isk(item.isk_per_m3)}/m3."
            )
            lines.append(
                f"   Cargo: {_format_number(item.max_total_volume)} m3; "
                f"route to {ranked.destination.name} is {ranked.jumps} jumps, min security {security}."
            )
            lines.append(
                "   Why: positive spread, fits your volume limit, and the destination buy order "
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
    parser.add_argument("--from", dest="from_system", help="Current solar system, like Jita.")
    parser.add_argument("--to", dest="to_system", help="Destination solar system, like Amarr.")
    parser.add_argument("--max-jumps", type=int, help="Only keep target routes at or below this jump count.")
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME, help="Cargo volume limit in m3.")
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

    if args.from_system is None:
        args.from_system = input("Where are you now? ").strip()
    if args.to_system is None and args.max_jumps is None:
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
            target_names=target_names,
        )
    except TradeAgentError as exc:
        print(f"Trade agent error: {exc}", file=sys.stderr)
        return 1

    print(format_plan(plan, volume=args.volume, sort_by=args.sort_by))
    return 0


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


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sort_key(item: RankedOpportunity, sort_by: str) -> float:
    opportunity = item.opportunity
    if sort_by == "profit":
        return opportunity.total_profit
    if sort_by == "isk-per-m3":
        return opportunity.isk_per_m3
    return opportunity.isk_per_jump


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
