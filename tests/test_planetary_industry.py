import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.planetary_industry import (
    PlanetaryTaxProfile,
    build_planetary_chain_plan,
    load_planetary_industry_cache,
    planetary_chain_material_type_ids,
    rank_planetary_opportunities,
)


def write_planetary_cache(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "eve_voice_pilot.planetary_industry.v1",
                "build_number": 12345,
                "release_date": "2026-06-05T00:00:00Z",
                "schematics": {
                    "65": {
                        "schematic_id": 65,
                        "name": "Superconductors",
                        "cycle_time_seconds": 3600,
                        "pins": [2470],
                        "inputs": [
                            {
                                "type_id": 2389,
                                "name": "Plasmoids",
                                "tier": "P1",
                                "quantity": 40,
                                "volume_m3": 0.38,
                                "export_tax_base_per_unit": 400.0,
                                "import_tax_base_per_unit": 200.0,
                            },
                            {
                                "type_id": 3645,
                                "name": "Water",
                                "tier": "P1",
                                "quantity": 40,
                                "volume_m3": 0.38,
                                "export_tax_base_per_unit": 400.0,
                                "import_tax_base_per_unit": 200.0,
                            },
                        ],
                        "outputs": [
                            {
                                "type_id": 9838,
                                "name": "Superconductors",
                                "tier": "P2",
                                "quantity": 5,
                                "volume_m3": 1.5,
                                "export_tax_base_per_unit": 7200.0,
                                "import_tax_base_per_unit": 3600.0,
                            },
                        ],
                    },
                    "66": {
                        "schematic_id": 66,
                        "name": "Coolant",
                        "cycle_time_seconds": 3600,
                        "inputs": [
                            {
                                "type_id": 2390,
                                "name": "Electrolytes",
                                "tier": "P1",
                                "quantity": 40,
                                "export_tax_base_per_unit": 400.0,
                                "import_tax_base_per_unit": 200.0,
                            },
                        ],
                        "outputs": [
                            {
                                "type_id": 9832,
                                "name": "Coolant",
                                "tier": "P2",
                                "quantity": 5,
                                "export_tax_base_per_unit": 7200.0,
                                "import_tax_base_per_unit": 3600.0,
                            },
                        ],
                    },
                    "80": {
                        "schematic_id": 80,
                        "name": "Microfiber Shielding",
                        "cycle_time_seconds": 3600,
                        "inputs": [
                            {
                                "type_id": 2397,
                                "name": "Industrial Fibers",
                                "tier": "P1",
                                "quantity": 40,
                                "volume_m3": 0.38,
                                "export_tax_base_per_unit": 400.0,
                                "import_tax_base_per_unit": 200.0,
                            },
                            {
                                "type_id": 9828,
                                "name": "Silicon",
                                "tier": "P1",
                                "quantity": 40,
                                "volume_m3": 0.38,
                                "export_tax_base_per_unit": 400.0,
                                "import_tax_base_per_unit": 200.0,
                            },
                        ],
                        "outputs": [
                            {
                                "type_id": 2327,
                                "name": "Microfiber Shielding",
                                "tier": "P2",
                                "quantity": 5,
                                "volume_m3": 0.75,
                                "export_tax_base_per_unit": 7200.0,
                                "import_tax_base_per_unit": 3600.0,
                            },
                        ],
                    },
                    "81": {
                        "schematic_id": 81,
                        "name": "Viral Agent",
                        "cycle_time_seconds": 3600,
                        "inputs": [
                            {"type_id": 2393, "name": "Bacteria", "tier": "P1", "quantity": 40},
                            {"type_id": 3779, "name": "Biomass", "tier": "P1", "quantity": 40},
                        ],
                        "outputs": [
                            {"type_id": 3775, "name": "Viral Agent", "tier": "P2", "quantity": 5},
                        ],
                    },
                    "130": {
                        "schematic_id": 130,
                        "name": "Silicon",
                        "cycle_time_seconds": 1800,
                        "inputs": [
                            {
                                "type_id": 2307,
                                "name": "Felsic Magma",
                                "tier": "P0",
                                "quantity": 3000,
                                "volume_m3": 0.005,
                                "export_tax_base_per_unit": 5.0,
                                "import_tax_base_per_unit": 2.5,
                            },
                        ],
                        "outputs": [
                            {
                                "type_id": 9828,
                                "name": "Silicon",
                                "tier": "P1",
                                "quantity": 20,
                                "volume_m3": 0.38,
                                "export_tax_base_per_unit": 400.0,
                                "import_tax_base_per_unit": 200.0,
                            },
                        ],
                    },
                    "131": {
                        "schematic_id": 131,
                        "name": "Bacteria",
                        "cycle_time_seconds": 1800,
                        "inputs": [
                            {"type_id": 2073, "name": "Microorganisms", "tier": "P0", "quantity": 3000},
                        ],
                        "outputs": [
                            {"type_id": 2393, "name": "Bacteria", "tier": "P1", "quantity": 20},
                        ],
                    },
                    "132": {
                        "schematic_id": 132,
                        "name": "Biomass",
                        "cycle_time_seconds": 1800,
                        "inputs": [
                            {"type_id": 2286, "name": "Planktic Colonies", "tier": "P0", "quantity": 3000},
                        ],
                        "outputs": [
                            {"type_id": 3779, "name": "Biomass", "tier": "P1", "quantity": 20},
                        ],
                    },
                    "135": {
                        "schematic_id": 135,
                        "name": "Industrial Fibers",
                        "cycle_time_seconds": 1800,
                        "inputs": [
                            {
                                "type_id": 2305,
                                "name": "Autotrophs",
                                "tier": "P0",
                                "quantity": 3000,
                                "volume_m3": 0.005,
                                "export_tax_base_per_unit": 5.0,
                                "import_tax_base_per_unit": 2.5,
                            },
                        ],
                        "outputs": [
                            {
                                "type_id": 2397,
                                "name": "Industrial Fibers",
                                "tier": "P1",
                                "quantity": 20,
                                "volume_m3": 0.38,
                                "export_tax_base_per_unit": 400.0,
                                "import_tax_base_per_unit": 200.0,
                            },
                        ],
                    },
                },
                "commodities": {
                    "2389": {"type_id": 2389, "name": "Plasmoids", "tier": "P1"},
                    "3645": {"type_id": 3645, "name": "Water", "tier": "P1"},
                    "9838": {"type_id": 9838, "name": "Superconductors", "tier": "P2"},
                    "2327": {
                        "type_id": 2327,
                        "name": "Microfiber Shielding",
                        "tier": "P2",
                        "volume_m3": 0.75,
                        "export_tax_base_per_unit": 7200.0,
                        "import_tax_base_per_unit": 3600.0,
                    },
                    "2397": {
                        "type_id": 2397,
                        "name": "Industrial Fibers",
                        "tier": "P1",
                        "volume_m3": 0.38,
                        "export_tax_base_per_unit": 400.0,
                        "import_tax_base_per_unit": 200.0,
                    },
                    "9828": {
                        "type_id": 9828,
                        "name": "Silicon",
                        "tier": "P1",
                        "volume_m3": 0.38,
                        "export_tax_base_per_unit": 400.0,
                        "import_tax_base_per_unit": 200.0,
                    },
                    "2305": {
                        "type_id": 2305,
                        "name": "Autotrophs",
                        "tier": "P0",
                        "volume_m3": 0.005,
                        "export_tax_base_per_unit": 5.0,
                        "import_tax_base_per_unit": 2.5,
                    },
                    "2307": {
                        "type_id": 2307,
                        "name": "Felsic Magma",
                        "tier": "P0",
                        "volume_m3": 0.005,
                        "export_tax_base_per_unit": 5.0,
                        "import_tax_base_per_unit": 2.5,
                    },
                    "3775": {"type_id": 3775, "name": "Viral Agent", "tier": "P2"},
                    "2393": {"type_id": 2393, "name": "Bacteria", "tier": "P1"},
                    "3779": {"type_id": 3779, "name": "Biomass", "tier": "P1"},
                    "2073": {"type_id": 2073, "name": "Microorganisms", "tier": "P0"},
                    "2286": {"type_id": 2286, "name": "Planktic Colonies", "tier": "P0"},
                },
                "planets": {
                    "1": {"planet_id": 1, "type_name": "Planet (Temperate)", "solar_system_id": 30000142},
                    "2": {"planet_id": 2, "type_name": "Planet (Lava)", "solar_system_id": 30000142},
                    "3": {"planet_id": 3, "type_name": "Planet (Ice)", "solar_system_id": 30000142},
                    "4": {"planet_id": 4, "type_name": "Planet (Oceanic)", "solar_system_id": 30000142},
                },
            }
        ),
        encoding="utf-8",
    )


def test_load_planetary_industry_cache_reads_schematics(tmp_path):
    cache_path = tmp_path / "planetary.json"
    write_planetary_cache(cache_path)

    cache = load_planetary_industry_cache(cache_path)

    assert cache.available is True
    assert cache.build_number == 12345
    assert cache.schematics[65].name == "Superconductors"
    assert cache.schematics[65].inputs[0].name == "Plasmoids"
    assert cache.schematics[65].outputs[0].tier == "P2"


def test_rank_planetary_opportunities_subtracts_customs_transfer_cost(tmp_path):
    cache_path = tmp_path / "planetary.json"
    write_planetary_cache(cache_path)
    cache = load_planetary_industry_cache(cache_path)

    opportunities = rank_planetary_opportunities(
        cache,
        {
            2389: {"sell": 100.0},
            3645: {"sell": 110.0},
            9838: {"buy": 2200.0},
        },
        tax_profile=PlanetaryTaxProfile(
            owner_export_tax_rate=0.05,
            npc_export_tax_rate=0.10,
            customs_code_expertise_level=5,
            sales_tax_rate=0.03,
        ),
    )

    opportunity = opportunities[0]
    assert opportunity.schematic_name == "Superconductors"
    assert opportunity.input_value == pytest.approx(8400.0)
    assert opportunity.output_value == pytest.approx(11000.0)
    assert opportunity.import_customs_cost == pytest.approx(1600.0)
    assert opportunity.export_customs_cost == pytest.approx(3600.0)
    assert opportunity.customs_transfer_cost == pytest.approx(5200.0)
    assert opportunity.sales_tax == pytest.approx(330.0)
    assert opportunity.net_profit == pytest.approx(-2930.0)
    assert opportunity.profit_per_day == pytest.approx(-70320.0)
    assert opportunity.break_even_export_tax_rate == pytest.approx(0.0436538461538)
    assert opportunity.shopping_list[0].name == "Plasmoids"
    assert opportunity.shopping_list[0].unit_price == pytest.approx(100.0)
    assert opportunity.shopping_list[0].market_value == pytest.approx(4000.0)
    assert opportunity.shopping_list[0].import_customs_cost == pytest.approx(800.0)
    assert opportunity.sell_targets[0].name == "Superconductors"
    assert opportunity.sell_targets[0].unit_price == pytest.approx(2200.0)
    assert opportunity.sell_targets[0].market_value == pytest.approx(11000.0)
    assert opportunity.sell_targets[0].export_customs_cost == pytest.approx(3600.0)
    assert opportunity.sell_targets[0].sales_tax == pytest.approx(330.0)


def test_rank_planetary_opportunities_labels_missing_prices(tmp_path):
    cache_path = tmp_path / "planetary.json"
    write_planetary_cache(cache_path)
    cache = load_planetary_industry_cache(cache_path)

    opportunities = rank_planetary_opportunities(
        cache,
        {
            2389: {"sell": 100.0},
            9838: {"buy": 2200.0},
        },
        output_tiers=("P2",),
    )

    superconductors = next(item for item in opportunities if item.schematic_id == 65)
    assert superconductors.price_complete is False
    assert superconductors.missing_price_type_ids == (3645,)


def test_build_planetary_chain_plan_shows_p0_planets_and_customs(tmp_path):
    cache_path = tmp_path / "planetary.json"
    write_planetary_cache(cache_path)
    cache = load_planetary_industry_cache(cache_path)

    type_ids = planetary_chain_material_type_ids(cache, "Microfiber Shielding")
    assert type_ids == {2327, 2397, 9828, 2305, 2307}

    plan = build_planetary_chain_plan(
        cache,
        "Microfiber Shielding",
        prices={
            2327: {"buy": 20000.0, "sell": 25000.0},
            2397: {"buy": 700.0, "sell": 800.0},
            9828: {"buy": 800.0, "sell": 900.0},
            2305: {"buy": 5.0, "sell": 7.0},
            2307: {"buy": 4.0, "sell": 6.0},
        },
        tax_profile=PlanetaryTaxProfile(
            owner_export_tax_rate=0.05,
            npc_export_tax_rate=0.05,
            sales_tax_rate=0.03,
        ),
    )

    assert plan.target.name == "Microfiber Shielding"
    assert plan.node.required_quantity == 5
    assert [child.name for child in plan.node.children] == ["Industrial Fibers", "Silicon"]
    assert plan.node.buy_total_cost == pytest.approx(126800.0)
    assert plan.node.sell_net_value == pytest.approx(93400.0)
    assert plan.node.produce_from_bought_inputs_cost == pytest.approx(69600.0)
    assert plan.node.produce_from_bought_inputs_profit == pytest.approx(23800.0)
    assert plan.node.off_planet_transfer_customs_cost == pytest.approx(4800.0)
    assert plan.same_planet_options == ()
    raw_inputs = {item.name: item for item in plan.raw_inputs}
    assert raw_inputs["Autotrophs"].quantity == 6000
    assert raw_inputs["Autotrophs"].planet_types == ("Temperate",)
    assert raw_inputs["Autotrophs"].planet_type_counts[0].planet_count == 1
    assert raw_inputs["Felsic Magma"].quantity == 6000
    assert raw_inputs["Felsic Magma"].planet_types == ("Lava",)


def test_build_planetary_chain_plan_identifies_same_planet_options(tmp_path):
    cache_path = tmp_path / "planetary.json"
    write_planetary_cache(cache_path)
    cache = load_planetary_industry_cache(cache_path)

    plan = build_planetary_chain_plan(
        cache,
        "Viral Agent",
        prices={},
        tax_profile=PlanetaryTaxProfile(owner_export_tax_rate=0.05, npc_export_tax_rate=0.05),
    )

    assert plan.target.name == "Viral Agent"
    assert plan.same_planet_options == ("Ice", "Oceanic")
    assert "Same-planet route" in plan.notes[2]
