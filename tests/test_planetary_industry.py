import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.planetary_industry import (
    PlanetaryTaxProfile,
    load_planetary_industry_cache,
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
                },
                "commodities": {
                    "2389": {"type_id": 2389, "name": "Plasmoids", "tier": "P1"},
                    "3645": {"type_id": 3645, "name": "Water", "tier": "P1"},
                    "9838": {"type_id": 9838, "name": "Superconductors", "tier": "P2"},
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
