from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLANETARY_CACHE_PATH = ROOT / "cache" / "eve_planetary_industry.json"
PLANETARY_SCHEMA = "eve_voice_pilot.planetary_industry.v1"


class PlanetaryIndustryError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanetaryItem:
    type_id: int
    name: str
    tier: str
    quantity: int = 0
    volume_m3: float | None = None
    export_tax_base_per_unit: float | None = None
    import_tax_base_per_unit: float | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PlanetaryItem":
        return cls(
            type_id=clean_int(payload.get("type_id")),
            name=str(payload.get("name") or ""),
            tier=str(payload.get("tier") or "Unknown"),
            quantity=clean_int(payload.get("quantity")),
            volume_m3=clean_optional_float(payload.get("volume_m3")),
            export_tax_base_per_unit=clean_optional_float(payload.get("export_tax_base_per_unit")),
            import_tax_base_per_unit=clean_optional_float(payload.get("import_tax_base_per_unit")),
        )

    @property
    def total_volume_m3(self) -> float | None:
        if self.volume_m3 is None:
            return None
        return self.volume_m3 * self.quantity


@dataclass(frozen=True)
class PlanetarySchematic:
    schematic_id: int
    name: str
    cycle_time_seconds: int
    inputs: tuple[PlanetaryItem, ...]
    outputs: tuple[PlanetaryItem, ...]
    pins: tuple[int, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PlanetarySchematic":
        inputs = tuple(PlanetaryItem.from_payload(item) for item in clean_records(payload.get("inputs")))
        outputs = tuple(PlanetaryItem.from_payload(item) for item in clean_records(payload.get("outputs")))
        return cls(
            schematic_id=clean_int(payload.get("schematic_id")),
            name=str(payload.get("name") or ""),
            cycle_time_seconds=clean_int(payload.get("cycle_time_seconds")),
            inputs=inputs,
            outputs=outputs,
            pins=tuple(clean_int(value) for value in payload.get("pins") or [] if clean_int(value) > 0),
        )


@dataclass(frozen=True)
class PlanetaryIndustryCache:
    path: Path
    available: bool
    schematics: dict[int, PlanetarySchematic]
    commodities: dict[int, PlanetaryItem]
    build_number: int | None = None
    release_date: str = ""
    error: str = ""


@dataclass(frozen=True)
class PlanetaryMarketPrice:
    type_id: int
    buy_price: float | None = None
    sell_price: float | None = None


@dataclass(frozen=True)
class PlanetaryTaxProfile:
    owner_export_tax_rate: float = 0.0
    npc_export_tax_rate: float = 0.0
    customs_code_expertise_level: int = 0
    sales_tax_rate: float = 0.0
    broker_fee_rate: float = 0.0

    @property
    def effective_npc_export_tax_rate(self) -> float:
        level = min(max(self.customs_code_expertise_level, 0), 5)
        return max(0.0, self.npc_export_tax_rate * (1.0 - 0.1 * level))

    @property
    def effective_export_tax_rate(self) -> float:
        return max(0.0, self.owner_export_tax_rate) + self.effective_npc_export_tax_rate

    @property
    def effective_import_tax_rate(self) -> float:
        return self.effective_export_tax_rate * 0.5


@dataclass(frozen=True)
class PlanetaryOpportunity:
    schematic_id: int
    schematic_name: str
    output_name: str
    output_tier: str
    cycle_time_seconds: int
    input_value: float
    output_value: float
    import_customs_cost: float
    export_customs_cost: float
    customs_transfer_cost: float
    sales_tax: float
    broker_fee: float
    net_profit: float
    profit_per_hour: float | None
    profit_per_day: float | None
    break_even_export_tax_rate: float | None
    price_complete: bool
    missing_price_type_ids: tuple[int, ...]
    inputs: tuple[PlanetaryItem, ...]
    outputs: tuple[PlanetaryItem, ...]

    @property
    def profitable(self) -> bool:
        return self.price_complete and self.net_profit > 0


def load_planetary_industry_cache(cache_path: Path = DEFAULT_PLANETARY_CACHE_PATH) -> PlanetaryIndustryCache:
    path = cache_path.expanduser()
    if not path.exists():
        return PlanetaryIndustryCache(path=path, available=False, schematics={}, commodities={}, error="Planetary cache file is missing.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PlanetaryIndustryCache(path=path, available=False, schematics={}, commodities={}, error=f"Planetary cache could not be read: {exc}")
    if not isinstance(payload, dict) or payload.get("schema") != PLANETARY_SCHEMA:
        return PlanetaryIndustryCache(path=path, available=False, schematics={}, commodities={}, error="Planetary cache has unexpected format.")
    schematics_payload = payload.get("schematics")
    commodities_payload = payload.get("commodities")
    if not isinstance(schematics_payload, dict) or not isinstance(commodities_payload, dict):
        return PlanetaryIndustryCache(path=path, available=False, schematics={}, commodities={}, error="Planetary cache is missing schematics or commodities.")

    schematics = {
        schematic.schematic_id: schematic
        for schematic in (PlanetarySchematic.from_payload(item) for item in schematics_payload.values() if isinstance(item, dict))
        if schematic.schematic_id > 0 and schematic.inputs and schematic.outputs
    }
    commodities = {
        commodity.type_id: commodity
        for commodity in (PlanetaryItem.from_payload(item) for item in commodities_payload.values() if isinstance(item, dict))
        if commodity.type_id > 0
    }
    if not schematics:
        return PlanetaryIndustryCache(path=path, available=False, schematics={}, commodities=commodities, error="Planetary cache has no usable schematics.")
    return PlanetaryIndustryCache(
        path=path,
        available=True,
        schematics=schematics,
        commodities=commodities,
        build_number=clean_optional_int(payload.get("build_number")),
        release_date=str(payload.get("release_date") or ""),
    )


def rank_planetary_opportunities(
    cache: PlanetaryIndustryCache,
    prices: dict[int, PlanetaryMarketPrice | dict[str, Any]],
    *,
    tax_profile: PlanetaryTaxProfile | None = None,
    output_tiers: Iterable[str] | None = None,
    top: int = 20,
) -> list[PlanetaryOpportunity]:
    if not cache.available:
        raise PlanetaryIndustryError(cache.error or "Planetary cache is not available.")
    tax_profile = tax_profile or PlanetaryTaxProfile()
    wanted_tiers = {tier.upper() for tier in output_tiers or [] if str(tier).strip()}
    normalized_prices = normalize_price_map(prices)
    opportunities = []
    for schematic in cache.schematics.values():
        output_tier = primary_tier(schematic.outputs)
        if wanted_tiers and output_tier.upper() not in wanted_tiers:
            continue
        opportunities.append(
            build_planetary_opportunity(
                schematic,
                prices=normalized_prices,
                tax_profile=tax_profile,
            )
        )
    opportunities.sort(
        key=lambda item: (
            0 if item.price_complete else 1,
            -item.net_profit,
            -float(item.profit_per_day or 0.0),
            item.schematic_name,
        )
    )
    return opportunities[: max(0, int(top))]


def build_planetary_opportunity(
    schematic: PlanetarySchematic,
    *,
    prices: dict[int, PlanetaryMarketPrice],
    tax_profile: PlanetaryTaxProfile,
) -> PlanetaryOpportunity:
    input_value, missing_input_prices = material_value(schematic.inputs, prices, side="sell")
    output_value, missing_output_prices = material_value(schematic.outputs, prices, side="buy")
    import_customs_cost = customs_cost(schematic.inputs, tax_profile, direction="import")
    export_customs_cost = customs_cost(schematic.outputs, tax_profile, direction="export")
    customs_transfer_cost = import_customs_cost + export_customs_cost
    sales_tax = output_value * max(0.0, tax_profile.sales_tax_rate)
    broker_fee = output_value * max(0.0, tax_profile.broker_fee_rate)
    net_profit = output_value - input_value - customs_transfer_cost - sales_tax - broker_fee
    price_complete = not missing_input_prices and not missing_output_prices
    profit_per_hour = None
    profit_per_day = None
    if schematic.cycle_time_seconds > 0:
        profit_per_hour = net_profit * 3600.0 / schematic.cycle_time_seconds
        profit_per_day = net_profit * 86400.0 / schematic.cycle_time_seconds
    return PlanetaryOpportunity(
        schematic_id=schematic.schematic_id,
        schematic_name=schematic.name,
        output_name=", ".join(item.name for item in schematic.outputs),
        output_tier=primary_tier(schematic.outputs),
        cycle_time_seconds=schematic.cycle_time_seconds,
        input_value=input_value,
        output_value=output_value,
        import_customs_cost=import_customs_cost,
        export_customs_cost=export_customs_cost,
        customs_transfer_cost=customs_transfer_cost,
        sales_tax=sales_tax,
        broker_fee=broker_fee,
        net_profit=net_profit,
        profit_per_hour=profit_per_hour,
        profit_per_day=profit_per_day,
        break_even_export_tax_rate=break_even_export_tax_rate(
            schematic,
            output_value=output_value,
            input_value=input_value,
            sales_tax=sales_tax,
            broker_fee=broker_fee,
        ),
        price_complete=price_complete,
        missing_price_type_ids=tuple(sorted(set(missing_input_prices + missing_output_prices))),
        inputs=schematic.inputs,
        outputs=schematic.outputs,
    )


def material_value(
    items: Iterable[PlanetaryItem],
    prices: dict[int, PlanetaryMarketPrice],
    *,
    side: str,
) -> tuple[float, list[int]]:
    total = 0.0
    missing = []
    for item in items:
        price = prices.get(item.type_id)
        unit_price = price.sell_price if side == "sell" and price else price.buy_price if price else None
        if unit_price is None:
            missing.append(item.type_id)
            continue
        total += float(unit_price) * item.quantity
    return total, missing


def customs_cost(items: Iterable[PlanetaryItem], tax_profile: PlanetaryTaxProfile, *, direction: str) -> float:
    rate = tax_profile.effective_export_tax_rate
    total = 0.0
    for item in items:
        base = item.import_tax_base_per_unit if direction == "import" else item.export_tax_base_per_unit
        if base is None:
            continue
        total += float(base) * item.quantity * rate
    return total


def break_even_export_tax_rate(
    schematic: PlanetarySchematic,
    *,
    output_value: float,
    input_value: float,
    sales_tax: float,
    broker_fee: float,
) -> float | None:
    customs_base = 0.0
    for item in schematic.inputs:
        customs_base += float(item.import_tax_base_per_unit or 0.0) * item.quantity
    for item in schematic.outputs:
        customs_base += float(item.export_tax_base_per_unit or 0.0) * item.quantity
    if customs_base <= 0:
        return None
    before_customs_profit = output_value - input_value - sales_tax - broker_fee
    return before_customs_profit / customs_base


def normalize_price_map(prices: dict[int, PlanetaryMarketPrice | dict[str, Any]]) -> dict[int, PlanetaryMarketPrice]:
    normalized = {}
    for key, value in prices.items():
        type_id = clean_int(key)
        if type_id <= 0:
            continue
        if isinstance(value, PlanetaryMarketPrice):
            normalized[type_id] = value
            continue
        if isinstance(value, dict):
            normalized[type_id] = PlanetaryMarketPrice(
                type_id=type_id,
                buy_price=clean_optional_float(value.get("buy_price") if "buy_price" in value else value.get("buy")),
                sell_price=clean_optional_float(value.get("sell_price") if "sell_price" in value else value.get("sell")),
            )
    return normalized


def primary_tier(items: Iterable[PlanetaryItem]) -> str:
    tiers = [item.tier for item in items if item.tier]
    if not tiers:
        return "Unknown"
    return sorted(set(tiers))[-1]


def clean_records(value: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def clean_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def clean_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clean_optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
