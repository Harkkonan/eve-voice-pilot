from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
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
class PlanetaryPlanet:
    planet_id: int
    type_id: int
    type_name: str
    solar_system_id: int
    solar_system_name: str = ""
    region_id: int = 0
    constellation_id: int = 0
    celestial_index: int | None = None
    radius: float | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PlanetaryPlanet":
        return cls(
            planet_id=clean_int(payload.get("planet_id")),
            type_id=clean_int(payload.get("type_id")),
            type_name=str(payload.get("type_name") or ""),
            solar_system_id=clean_int(payload.get("solar_system_id")),
            solar_system_name=str(payload.get("solar_system_name") or ""),
            region_id=clean_int(payload.get("region_id")),
            constellation_id=clean_int(payload.get("constellation_id")),
            celestial_index=clean_optional_int(payload.get("celestial_index")),
            radius=clean_optional_float(payload.get("radius")),
        )


@dataclass(frozen=True)
class PlanetaryIndustryCache:
    path: Path
    available: bool
    schematics: dict[int, PlanetarySchematic]
    commodities: dict[int, PlanetaryItem]
    planets: dict[int, PlanetaryPlanet] = field(default_factory=dict)
    build_number: int | None = None
    release_date: str = ""
    error: str = ""


@dataclass(frozen=True)
class PlanetaryMarketPrice:
    type_id: int
    buy_price: float | None = None
    sell_price: float | None = None


@dataclass(frozen=True)
class PlanetaryPlanItem:
    type_id: int
    name: str
    tier: str
    quantity: int
    market_side: str
    unit_price: float | None
    market_value: float | None
    import_customs_cost: float = 0.0
    export_customs_cost: float = 0.0
    sales_tax: float = 0.0
    broker_fee: float = 0.0
    volume_m3: float | None = None
    total_volume_m3: float | None = None
    export_tax_base_per_unit: float | None = None
    import_tax_base_per_unit: float | None = None

    @property
    def price_complete(self) -> bool:
        return self.unit_price is not None and self.market_value is not None

    @property
    def customs_transfer_cost(self) -> float:
        return self.import_customs_cost + self.export_customs_cost


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
    shopping_list: tuple[PlanetaryPlanItem, ...]
    sell_targets: tuple[PlanetaryPlanItem, ...]

    @property
    def profitable(self) -> bool:
        return self.price_complete and self.net_profit > 0


@dataclass(frozen=True)
class PlanetaryPlanetTypeCount:
    planet_type: str
    planet_count: int


@dataclass(frozen=True)
class PlanetaryChainRawInput:
    type_id: int
    name: str
    tier: str
    quantity: int
    buy_unit_price: float | None
    buy_market_value: float | None
    buy_import_customs_cost: float
    buy_total_cost: float | None
    planet_types: tuple[str, ...]
    planet_type_counts: tuple[PlanetaryPlanetTypeCount, ...]

    @property
    def price_complete(self) -> bool:
        return self.buy_unit_price is not None and self.buy_market_value is not None and self.buy_total_cost is not None


@dataclass(frozen=True)
class PlanetaryChainNode:
    type_id: int
    name: str
    tier: str
    required_quantity: int
    produced_quantity: int
    depth: int
    schematic_id: int | None = None
    schematic_name: str = ""
    cycle_time_seconds: int = 0
    cycle_count: int = 0
    buy_unit_price: float | None = None
    buy_market_value: float | None = None
    buy_import_customs_cost: float = 0.0
    buy_total_cost: float | None = None
    sell_unit_price: float | None = None
    sell_market_value: float | None = None
    sell_export_customs_cost: float = 0.0
    sales_tax: float = 0.0
    broker_fee: float = 0.0
    sell_net_value: float | None = None
    immediate_input_market_cost: float | None = None
    immediate_input_customs_cost: float = 0.0
    produce_from_bought_inputs_cost: float | None = None
    produce_from_bought_inputs_profit: float | None = None
    off_planet_transfer_customs_cost: float = 0.0
    same_planet_transfer_savings: float = 0.0
    same_planet_options: tuple[str, ...] = ()
    planet_types: tuple[str, ...] = ()
    planet_type_counts: tuple[PlanetaryPlanetTypeCount, ...] = ()
    missing_price_type_ids: tuple[int, ...] = ()
    children: tuple["PlanetaryChainNode", ...] = ()

    @property
    def raw_resource(self) -> bool:
        return not self.children

    @property
    def price_complete(self) -> bool:
        return not self.missing_price_type_ids


@dataclass(frozen=True)
class PlanetaryChainPlan:
    target_query: str
    target: PlanetaryItem
    node: PlanetaryChainNode
    raw_inputs: tuple[PlanetaryChainRawInput, ...]
    same_planet_options: tuple[str, ...]
    same_planet_transfer_savings: float
    missing_price_type_ids: tuple[int, ...]
    notes: tuple[str, ...]

    @property
    def price_complete(self) -> bool:
        return not self.missing_price_type_ids


PLANETARY_PLANET_TYPE_ORDER = (
    "Barren",
    "Gas",
    "Ice",
    "Lava",
    "Oceanic",
    "Plasma",
    "Storm",
    "Temperate",
)
PLANETARY_P0_PLANET_TYPES: dict[int, tuple[str, ...]] = {
    2268: ("Gas", "Barren", "Temperate", "Storm", "Ice", "Oceanic"),  # Aqueous Liquids
    2305: ("Temperate",),  # Autotrophs
    2267: ("Gas", "Barren", "Lava", "Storm", "Plasma"),  # Base Metals
    2288: ("Barren", "Temperate", "Oceanic"),  # Carbon Compounds
    2287: ("Temperate", "Oceanic"),  # Complex Organisms
    2307: ("Lava",),  # Felsic Magma
    2272: ("Lava", "Ice", "Plasma"),  # Heavy Metals
    2309: ("Gas", "Storm"),  # Ionic Solutions
    2073: ("Barren", "Temperate", "Ice", "Oceanic"),  # Microorganisms
    2310: ("Gas", "Storm", "Ice"),  # Noble Gas
    2270: ("Barren", "Plasma"),  # Noble Metals
    2306: ("Lava", "Plasma"),  # Non-CS Crystals
    2286: ("Ice", "Oceanic"),  # Planktic Colonies
    2311: ("Gas",),  # Reactive Gas
    2308: ("Lava", "Storm", "Plasma"),  # Suspended Plasma
}


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
    planets_payload = payload.get("planets")
    planets = {
        planet.planet_id: planet
        for planet in (
            PlanetaryPlanet.from_payload(item)
            for item in (planets_payload.values() if isinstance(planets_payload, dict) else [])
            if isinstance(item, dict)
        )
        if planet.planet_id > 0
    }
    if not schematics:
        return PlanetaryIndustryCache(
            path=path,
            available=False,
            schematics={},
            commodities=commodities,
            planets=planets,
            error="Planetary cache has no usable schematics.",
        )
    return PlanetaryIndustryCache(
        path=path,
        available=True,
        schematics=schematics,
        commodities=commodities,
        planets=planets,
        build_number=clean_optional_int(payload.get("build_number")),
        release_date=str(payload.get("release_date") or ""),
    )


def resolve_planetary_chain_target(cache: PlanetaryIndustryCache, query: str) -> PlanetaryItem:
    if not cache.available:
        raise PlanetaryIndustryError(cache.error or "Planetary cache is not available.")
    clean_query = str(query or "").strip()
    if not clean_query:
        raise PlanetaryIndustryError("Enter a PI material such as Microfiber Shielding or Viral Agent.")
    type_id = clean_int(clean_query)
    if type_id > 0 and type_id in cache.commodities:
        return cache.commodities[type_id]
    normalized_query = normalize_material_name(clean_query)
    exact_matches = [
        item for item in cache.commodities.values() if normalize_material_name(item.name) == normalized_query
    ]
    if exact_matches:
        return sorted(exact_matches, key=planetary_item_sort_key)[0]
    prefix_matches = [
        item for item in cache.commodities.values() if normalize_material_name(item.name).startswith(normalized_query)
    ]
    if prefix_matches:
        return sorted(prefix_matches, key=planetary_item_sort_key)[0]
    contains_matches = [
        item for item in cache.commodities.values() if normalized_query in normalize_material_name(item.name)
    ]
    if contains_matches:
        return sorted(contains_matches, key=planetary_item_sort_key)[0]
    raise PlanetaryIndustryError(f"Planetary material {clean_query!r} was not found in the static cache.")


def planetary_chain_material_type_ids(cache: PlanetaryIndustryCache, query: str) -> set[int]:
    target = resolve_planetary_chain_target(cache, query)
    return collect_planetary_chain_type_ids(cache, target.type_id, seen=set())


def build_planetary_chain_plan(
    cache: PlanetaryIndustryCache,
    query: str,
    *,
    prices: dict[int, PlanetaryMarketPrice | dict[str, Any]],
    tax_profile: PlanetaryTaxProfile | None = None,
) -> PlanetaryChainPlan:
    target = resolve_planetary_chain_target(cache, query)
    tax_profile = tax_profile or PlanetaryTaxProfile()
    normalized_prices = normalize_price_map(prices)
    target_schematic, target_output = schematic_output_for_type_id(cache, target.type_id)
    required_quantity = target_output.quantity if target_output and target_output.quantity > 0 else max(1, target.quantity or 1)
    node = build_planetary_chain_node(
        cache,
        target,
        required_quantity=required_quantity,
        depth=0,
        prices=normalized_prices,
        tax_profile=tax_profile,
        seen=set(),
    )
    raw_inputs = aggregate_chain_raw_inputs(cache, node, prices=normalized_prices, tax_profile=tax_profile)
    missing_prices = sorted(collect_chain_missing_price_type_ids(node))
    notes = build_planetary_chain_notes(node)
    return PlanetaryChainPlan(
        target_query=str(query or "").strip(),
        target=planetary_item_with_quantity(target, required_quantity),
        node=node,
        raw_inputs=tuple(raw_inputs),
        same_planet_options=node.same_planet_options,
        same_planet_transfer_savings=node.same_planet_transfer_savings,
        missing_price_type_ids=tuple(missing_prices),
        notes=tuple(notes),
    )


def build_planetary_chain_node(
    cache: PlanetaryIndustryCache,
    item: PlanetaryItem,
    *,
    required_quantity: int,
    depth: int,
    prices: dict[int, PlanetaryMarketPrice],
    tax_profile: PlanetaryTaxProfile,
    seen: set[int],
) -> PlanetaryChainNode:
    cached_item = cache_item_for_type_id(cache, item.type_id, fallback=item)
    required_quantity = max(1, int(required_quantity))
    input_item = planetary_item_with_quantity(cached_item, required_quantity)
    schematic, output_item = schematic_output_for_type_id(cache, cached_item.type_id)
    if cached_item.type_id in seen:
        raise PlanetaryIndustryError(f"Planetary chain for {cached_item.name} loops back on itself.")

    cycle_count = 0
    produced_quantity = required_quantity
    children: tuple[PlanetaryChainNode, ...] = ()
    immediate_input_market_cost: float | None = None
    immediate_input_customs_cost = 0.0
    produce_from_bought_inputs_cost: float | None = None
    off_planet_transfer_customs_cost = 0.0
    same_planet_options: tuple[str, ...] = ()
    same_planet_transfer_savings = 0.0

    if schematic and output_item:
        output_per_cycle = max(1, int(output_item.quantity))
        cycle_count = max(1, int(math.ceil(required_quantity / output_per_cycle)))
        produced_quantity = output_per_cycle * cycle_count
        next_seen = set(seen)
        next_seen.add(cached_item.type_id)
        child_nodes = []
        immediate_market_values = []
        child_buy_totals = []
        for schematic_input in schematic.inputs:
            child_required_quantity = max(1, int(schematic_input.quantity)) * cycle_count
            child_node = build_planetary_chain_node(
                cache,
                schematic_input,
                required_quantity=child_required_quantity,
                depth=depth + 1,
                prices=prices,
                tax_profile=tax_profile,
                seen=next_seen,
            )
            child_nodes.append(child_node)
            immediate_market_values.append(child_node.buy_market_value)
            child_buy_totals.append(child_node.buy_total_cost)
            immediate_input_customs_cost += child_node.buy_import_customs_cost
        children = tuple(child_nodes)
        if all(value is not None for value in immediate_market_values):
            immediate_input_market_cost = sum(float(value or 0.0) for value in immediate_market_values)
        if all(value is not None for value in child_buy_totals):
            produce_from_bought_inputs_cost = sum(float(value or 0.0) for value in child_buy_totals)
        off_planet_transfer_customs_cost = immediate_off_planet_transfer_customs_cost(
            cache,
            schematic,
            cycle_count,
            tax_profile=tax_profile,
        )
        same_planet_options = same_planet_options_for_children(children)
        same_planet_transfer_savings = off_planet_transfer_customs_cost if same_planet_options else 0.0

    price = prices.get(cached_item.type_id)
    buy_unit_price = price.sell_price if price else None
    buy_market_value = None if buy_unit_price is None else float(buy_unit_price) * required_quantity
    buy_import_customs_cost = customs_line_cost(input_item, tax_profile, direction="import")
    buy_total_cost = None if buy_market_value is None else buy_market_value + buy_import_customs_cost

    produced_item = planetary_item_with_quantity(cached_item, produced_quantity)
    sell_unit_price = price.buy_price if price else None
    sell_market_value = None if sell_unit_price is None else float(sell_unit_price) * produced_quantity
    sell_export_customs_cost = customs_line_cost(produced_item, tax_profile, direction="export")
    sales_tax = (sell_market_value or 0.0) * max(0.0, tax_profile.sales_tax_rate)
    broker_fee = (sell_market_value or 0.0) * max(0.0, tax_profile.broker_fee_rate)
    sell_net_value = None if sell_market_value is None else sell_market_value - sell_export_customs_cost - sales_tax - broker_fee
    produce_profit = (
        sell_net_value - produce_from_bought_inputs_cost
        if sell_net_value is not None and produce_from_bought_inputs_cost is not None
        else None
    )
    planet_types = planetary_planet_types_for_item(cached_item)
    missing_price_type_ids = set()
    if buy_unit_price is None:
        missing_price_type_ids.add(cached_item.type_id)
    if sell_unit_price is None:
        missing_price_type_ids.add(cached_item.type_id)
    for child in children:
        missing_price_type_ids.update(child.missing_price_type_ids)
    return PlanetaryChainNode(
        type_id=cached_item.type_id,
        name=cached_item.name,
        tier=cached_item.tier,
        required_quantity=required_quantity,
        produced_quantity=produced_quantity,
        depth=depth,
        schematic_id=schematic.schematic_id if schematic else None,
        schematic_name=schematic.name if schematic else "",
        cycle_time_seconds=schematic.cycle_time_seconds if schematic else 0,
        cycle_count=cycle_count,
        buy_unit_price=buy_unit_price,
        buy_market_value=buy_market_value,
        buy_import_customs_cost=buy_import_customs_cost,
        buy_total_cost=buy_total_cost,
        sell_unit_price=sell_unit_price,
        sell_market_value=sell_market_value,
        sell_export_customs_cost=sell_export_customs_cost,
        sales_tax=sales_tax,
        broker_fee=broker_fee,
        sell_net_value=sell_net_value,
        immediate_input_market_cost=immediate_input_market_cost,
        immediate_input_customs_cost=immediate_input_customs_cost,
        produce_from_bought_inputs_cost=produce_from_bought_inputs_cost,
        produce_from_bought_inputs_profit=produce_profit,
        off_planet_transfer_customs_cost=off_planet_transfer_customs_cost,
        same_planet_transfer_savings=same_planet_transfer_savings,
        same_planet_options=same_planet_options,
        planet_types=planet_types,
        planet_type_counts=planet_type_counts(cache, planet_types),
        missing_price_type_ids=tuple(sorted(missing_price_type_ids)),
        children=children,
    )


def normalize_material_name(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def planetary_item_sort_key(item: PlanetaryItem) -> tuple[int, str, int]:
    tier_rank = {"P4": 0, "P3": 1, "P2": 2, "P1": 3, "P0": 4}.get(str(item.tier).upper(), 5)
    return (tier_rank, item.name.casefold(), item.type_id)


def collect_planetary_chain_type_ids(
    cache: PlanetaryIndustryCache,
    type_id: int,
    *,
    seen: set[int],
) -> set[int]:
    if type_id in seen:
        return {type_id}
    seen.add(type_id)
    type_ids = {type_id}
    schematic, _output_item = schematic_output_for_type_id(cache, type_id)
    if schematic is None:
        return type_ids
    for item in schematic.inputs:
        type_ids.update(collect_planetary_chain_type_ids(cache, item.type_id, seen=set(seen)))
    return type_ids


def schematic_output_for_type_id(
    cache: PlanetaryIndustryCache,
    type_id: int,
) -> tuple[PlanetarySchematic | None, PlanetaryItem | None]:
    for schematic in cache.schematics.values():
        for output in schematic.outputs:
            if output.type_id == type_id:
                return schematic, output
    return None, None


def cache_item_for_type_id(
    cache: PlanetaryIndustryCache,
    type_id: int,
    *,
    fallback: PlanetaryItem | None = None,
) -> PlanetaryItem:
    if type_id in cache.commodities:
        cached = cache.commodities[type_id]
        if fallback and (
            cached.export_tax_base_per_unit is None
            or cached.import_tax_base_per_unit is None
            or cached.volume_m3 is None
        ):
            return PlanetaryItem(
                type_id=cached.type_id,
                name=cached.name or fallback.name,
                tier=cached.tier or fallback.tier,
                quantity=cached.quantity or fallback.quantity,
                volume_m3=cached.volume_m3 if cached.volume_m3 is not None else fallback.volume_m3,
                export_tax_base_per_unit=(
                    cached.export_tax_base_per_unit
                    if cached.export_tax_base_per_unit is not None
                    else fallback.export_tax_base_per_unit
                ),
                import_tax_base_per_unit=(
                    cached.import_tax_base_per_unit
                    if cached.import_tax_base_per_unit is not None
                    else fallback.import_tax_base_per_unit
                ),
            )
        return cached
    if fallback is not None:
        return fallback
    return PlanetaryItem(type_id=type_id, name=f"Type {type_id}", tier="Unknown")


def planetary_item_with_quantity(item: PlanetaryItem, quantity: int) -> PlanetaryItem:
    return PlanetaryItem(
        type_id=item.type_id,
        name=item.name,
        tier=item.tier,
        quantity=max(0, int(quantity)),
        volume_m3=item.volume_m3,
        export_tax_base_per_unit=item.export_tax_base_per_unit,
        import_tax_base_per_unit=item.import_tax_base_per_unit,
    )


def planetary_planet_types_for_item(item: PlanetaryItem) -> tuple[str, ...]:
    return sort_planet_types(PLANETARY_P0_PLANET_TYPES.get(item.type_id, ()))


def planet_type_counts(
    cache: PlanetaryIndustryCache,
    planet_types: Iterable[str],
) -> tuple[PlanetaryPlanetTypeCount, ...]:
    wanted = set(planet_types)
    if not wanted:
        return ()
    counts = {planet_type: 0 for planet_type in wanted}
    for planet in cache.planets.values():
        label = normalized_planet_type_label(planet.type_name)
        if label in counts:
            counts[label] += 1
    return tuple(
        PlanetaryPlanetTypeCount(planet_type=planet_type, planet_count=counts.get(planet_type, 0))
        for planet_type in sort_planet_types(wanted)
    )


def normalized_planet_type_label(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("Planet (") and text.endswith(")"):
        text = text[len("Planet (") : -1]
    return text


def sort_planet_types(values: Iterable[str]) -> tuple[str, ...]:
    order = {name: index for index, name in enumerate(PLANETARY_PLANET_TYPE_ORDER)}
    return tuple(sorted({str(value) for value in values if str(value)}, key=lambda name: (order.get(name, 99), name)))


def same_planet_options_for_children(children: Iterable[PlanetaryChainNode]) -> tuple[str, ...]:
    raw_sets = []
    for child in children:
        raw_sets.extend(raw_planet_type_sets(child))
    if not raw_sets:
        return ()
    options = set(raw_sets[0])
    for planet_types in raw_sets[1:]:
        options &= set(planet_types)
    return sort_planet_types(options)


def raw_planet_type_sets(node: PlanetaryChainNode) -> list[tuple[str, ...]]:
    if not node.children:
        return [node.planet_types] if node.planet_types else []
    sets: list[tuple[str, ...]] = []
    for child in node.children:
        sets.extend(raw_planet_type_sets(child))
    return sets


def immediate_off_planet_transfer_customs_cost(
    cache: PlanetaryIndustryCache,
    schematic: PlanetarySchematic,
    cycle_count: int,
    *,
    tax_profile: PlanetaryTaxProfile,
) -> float:
    total = 0.0
    for schematic_input in schematic.inputs:
        input_item = cache_item_for_type_id(cache, schematic_input.type_id, fallback=schematic_input)
        moved_item = planetary_item_with_quantity(input_item, max(1, int(schematic_input.quantity)) * cycle_count)
        total += customs_line_cost(moved_item, tax_profile, direction="export")
        total += customs_line_cost(moved_item, tax_profile, direction="import")
    return total


def aggregate_chain_raw_inputs(
    cache: PlanetaryIndustryCache,
    node: PlanetaryChainNode,
    *,
    prices: dict[int, PlanetaryMarketPrice],
    tax_profile: PlanetaryTaxProfile,
) -> list[PlanetaryChainRawInput]:
    quantities: dict[int, int] = {}
    collect_raw_quantities(node, quantities)
    raw_inputs = []
    for type_id, quantity in quantities.items():
        item = planetary_item_with_quantity(cache_item_for_type_id(cache, type_id), quantity)
        price = prices.get(type_id)
        buy_unit_price = price.sell_price if price else None
        buy_market_value = None if buy_unit_price is None else float(buy_unit_price) * quantity
        buy_import_customs_cost = customs_line_cost(item, tax_profile, direction="import")
        buy_total_cost = None if buy_market_value is None else buy_market_value + buy_import_customs_cost
        planet_types = planetary_planet_types_for_item(item)
        raw_inputs.append(
            PlanetaryChainRawInput(
                type_id=type_id,
                name=item.name,
                tier=item.tier,
                quantity=quantity,
                buy_unit_price=buy_unit_price,
                buy_market_value=buy_market_value,
                buy_import_customs_cost=buy_import_customs_cost,
                buy_total_cost=buy_total_cost,
                planet_types=planet_types,
                planet_type_counts=planet_type_counts(cache, planet_types),
            )
        )
    raw_inputs.sort(key=lambda item: (item.name.casefold(), item.type_id))
    return raw_inputs


def collect_raw_quantities(node: PlanetaryChainNode, quantities: dict[int, int]) -> None:
    if not node.children:
        quantities[node.type_id] = quantities.get(node.type_id, 0) + node.required_quantity
        return
    for child in node.children:
        collect_raw_quantities(child, quantities)


def collect_chain_missing_price_type_ids(node: PlanetaryChainNode) -> set[int]:
    missing = set(node.missing_price_type_ids)
    for child in node.children:
        missing.update(collect_chain_missing_price_type_ids(child))
    return missing


def build_planetary_chain_notes(node: PlanetaryChainNode) -> list[str]:
    notes = [
        "Buy values use hub sell orders plus import customs, because those are the prices you pay to acquire material.",
        "Sell values use hub buy orders minus export customs, sales tax, and broker fee.",
    ]
    if node.same_planet_options:
        planet_text = ", ".join(node.same_planet_options)
        notes.append(
            f"Same-planet route: {planet_text} can host every raw input in this chain, so intermediate transfer customs can be avoided."
        )
    else:
        notes.append(
            "No single planet type covers every raw input in this target chain; expect a multi-planet route or a factory planet import."
        )
    if node.off_planet_transfer_customs_cost:
        notes.append(
            "Off-planet intermediate transfer customs are shown separately from the direct buy-input profit math."
        )
    return notes


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
            -float(item.profit_per_day or 0.0),
            -item.net_profit,
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
        shopping_list=tuple(
            build_plan_item(
                item,
                prices=prices,
                tax_profile=tax_profile,
                market_side="input-sell",
            )
            for item in schematic.inputs
        ),
        sell_targets=tuple(
            build_plan_item(
                item,
                prices=prices,
                tax_profile=tax_profile,
                market_side="output-buy",
            )
            for item in schematic.outputs
        ),
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
    return sum(customs_line_cost(item, tax_profile, direction=direction) for item in items)


def customs_line_cost(item: PlanetaryItem, tax_profile: PlanetaryTaxProfile, *, direction: str) -> float:
    base = item.import_tax_base_per_unit if direction == "import" else item.export_tax_base_per_unit
    if base is None:
        return 0.0
    return float(base) * item.quantity * tax_profile.effective_export_tax_rate


def build_plan_item(
    item: PlanetaryItem,
    *,
    prices: dict[int, PlanetaryMarketPrice],
    tax_profile: PlanetaryTaxProfile,
    market_side: str,
) -> PlanetaryPlanItem:
    price = prices.get(item.type_id)
    unit_price = price.sell_price if market_side == "input-sell" and price else price.buy_price if price else None
    market_value = None if unit_price is None else float(unit_price) * item.quantity
    import_customs = customs_line_cost(item, tax_profile, direction="import") if market_side == "input-sell" else 0.0
    export_customs = customs_line_cost(item, tax_profile, direction="export") if market_side == "output-buy" else 0.0
    sales_tax = (market_value or 0.0) * max(0.0, tax_profile.sales_tax_rate) if market_side == "output-buy" else 0.0
    broker_fee = (market_value or 0.0) * max(0.0, tax_profile.broker_fee_rate) if market_side == "output-buy" else 0.0
    return PlanetaryPlanItem(
        type_id=item.type_id,
        name=item.name,
        tier=item.tier,
        quantity=item.quantity,
        market_side=market_side,
        unit_price=unit_price,
        market_value=market_value,
        import_customs_cost=import_customs,
        export_customs_cost=export_customs,
        sales_tax=sales_tax,
        broker_fee=broker_fee,
        volume_m3=item.volume_m3,
        total_volume_m3=item.total_volume_m3,
        export_tax_base_per_unit=item.export_tax_base_per_unit,
        import_tax_base_per_unit=item.import_tax_base_per_unit,
    )


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
