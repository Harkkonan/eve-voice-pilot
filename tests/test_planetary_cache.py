import json
from pathlib import Path
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import update_industry_recipe_cache as updater


def write_jsonl_zip(path: Path, members: dict[str, list[dict]]) -> None:
    with ZipFile(path, "w") as archive:
        for name, rows in members.items():
            archive.writestr(name, "\n".join(json.dumps(row) for row in rows) + "\n")


def test_build_planetary_industry_cache_reads_schematics_and_customs_tax_values(tmp_path):
    sde_zip = tmp_path / "sde.zip"
    write_jsonl_zip(
        sde_zip,
        {
            "_sde.jsonl": [
                {"_key": "sde", "buildNumber": 12345, "releaseDate": "2026-06-05T00:00:00Z"},
            ],
            "types.jsonl": [
                {
                    "_key": 2267,
                    "name": {"en": "Base Metals"},
                    "groupID": 1033,
                    "marketGroupID": 1333,
                    "published": True,
                    "volume": 0.005,
                },
                {
                    "_key": 2268,
                    "name": {"en": "Aqueous Liquids"},
                    "groupID": 1033,
                    "marketGroupID": 1333,
                    "published": True,
                    "volume": 0.005,
                },
                {
                    "_key": 3645,
                    "name": {"en": "Water"},
                    "groupID": 1042,
                    "marketGroupID": 1334,
                    "published": True,
                    "volume": 0.38,
                },
                {
                    "_key": 2389,
                    "name": {"en": "Plasmoids"},
                    "groupID": 1042,
                    "marketGroupID": 1334,
                    "published": True,
                    "volume": 0.38,
                },
                {
                    "_key": 9838,
                    "name": {"en": "Superconductors"},
                    "groupID": 1042,
                    "marketGroupID": 1335,
                    "published": True,
                    "volume": 1.5,
                },
                {"_key": 11, "name": {"en": "Temperate Planet"}, "published": False},
            ],
            "marketGroups.jsonl": [
                {"_key": 1320, "name": {"en": "Planetary Infrastructure"}},
                {"_key": 1333, "name": {"en": "Raw Planetary Materials"}, "parentGroupID": 1320},
                {"_key": 1334, "name": {"en": "Processed Planetary Materials"}, "parentGroupID": 1320},
                {"_key": 1335, "name": {"en": "Refined Planetary Materials"}, "parentGroupID": 1320},
            ],
            "planetSchematics.jsonl": [
                {
                    "_key": 121,
                    "cycleTime": 1800,
                    "name": {"en": "Water"},
                    "pins": [2469],
                    "types": [
                        {"_key": 2268, "isInput": True, "quantity": 3000},
                        {"_key": 3645, "isInput": False, "quantity": 20},
                    ],
                },
                {
                    "_key": 123,
                    "cycleTime": 1800,
                    "name": {"en": "Plasmoids"},
                    "pins": [2469],
                    "types": [
                        {"_key": 2267, "isInput": True, "quantity": 3000},
                        {"_key": 2389, "isInput": False, "quantity": 20},
                    ],
                },
                {
                    "_key": 65,
                    "cycleTime": 3600,
                    "name": {"en": "Superconductors"},
                    "pins": [2470],
                    "types": [
                        {"_key": 2389, "isInput": True, "quantity": 40},
                        {"_key": 3645, "isInput": True, "quantity": 40},
                        {"_key": 9838, "isInput": False, "quantity": 5},
                    ],
                },
            ],
            "mapSolarSystems.jsonl": [
                {
                    "_key": 30000001,
                    "name": {"en": "Test System"},
                    "regionID": 10000001,
                    "constellationID": 20000001,
                    "securityStatus": 0.9,
                },
            ],
            "mapPlanets.jsonl": [
                {
                    "_key": 40000002,
                    "typeID": 11,
                    "solarSystemID": 30000001,
                    "celestialIndex": 1,
                    "radius": 5060000,
                },
            ],
        },
    )

    cache = updater.build_planetary_industry_cache(
        sde_zip=sde_zip,
        fallback_info={},
        source_url="test-sde",
    )

    assert cache["schema"] == updater.PLANETARY_SCHEMA
    assert cache["build_number"] == 12345
    assert cache["schematic_count"] == 3
    assert cache["commodity_count"] == 5
    assert cache["planet_count"] == 1
    assert cache["tax_base_values"] == updater.PLANETARY_TAX_BASE_VALUES

    aqueous = cache["commodities"]["2268"]
    assert aqueous["tier"] == "P0"
    assert aqueous["export_tax_base_per_unit"] == 5.0
    assert aqueous["import_tax_base_per_unit"] == 2.5

    water = cache["commodities"]["3645"]
    assert water["tier"] == "P1"
    assert water["customs_tax_base_value"] == 400.0
    assert water["market_group_path"] == ["Planetary Infrastructure", "Processed Planetary Materials"]

    superconductors = cache["commodities"]["9838"]
    assert superconductors["tier"] == "P2"
    assert superconductors["export_tax_base_per_unit"] == 7200.0
    assert superconductors["import_tax_base_per_unit"] == 3600.0

    schematic = cache["schematics"]["65"]
    assert schematic["name"] == "Superconductors"
    assert schematic["input_tiers"] == ["P1"]
    assert schematic["output_tiers"] == ["P2"]
    assert schematic["inputs"][0]["quantity"] == 40
    assert schematic["outputs"][0]["name"] == "Superconductors"

    planet = cache["planets"]["40000002"]
    assert planet["type_name"] == "Temperate Planet"
    assert planet["solar_system_name"] == "Test System"
    assert planet["region_id"] == 10000001


def test_build_reprocessing_cache_uses_sde_reprocessing_skill_attribute(tmp_path):
    sde_zip = tmp_path / "sde.zip"
    write_jsonl_zip(
        sde_zip,
        {
            "_sde.jsonl": [
                {"_key": "sde", "buildNumber": 12345, "releaseDate": "2026-06-06T00:00:00Z"},
            ],
            "types.jsonl": [
                {
                    "_key": 88105,
                    "name": {"en": "Tyranite"},
                    "groupID": 4857,
                    "marketGroupID": 3743,
                    "published": True,
                    "portionSize": 100,
                    "volume": 0.6,
                    "dogmaAttributes": [
                        {
                            "attributeID": updater.REPROCESSING_SKILL_ATTRIBUTE_ID,
                            "value": updater.REPROCESSING_SKILL_TYPES["Simple Ore Processing"],
                        },
                    ],
                },
                {
                    "_key": 88087,
                    "name": {"en": "Eleutrium"},
                    "groupID": 18,
                    "published": True,
                    "volume": 0.01,
                },
                {
                    "_key": updater.REPROCESSING_SKILL_TYPES["Simple Ore Processing"],
                    "name": {"en": "Simple Ore Processing"},
                    "groupID": 1218,
                    "published": True,
                },
            ],
            "groups.jsonl": [
                {"_key": 4857, "name": {"en": "Tyranite"}, "categoryID": 25},
            ],
            "typeMaterials.jsonl": [
                {
                    "typeID": 88105,
                    "materials": [
                        {"materialTypeID": 88087, "quantity": 1035},
                    ],
                },
            ],
            "npcCorporations.jsonl": [],
            "npcStations.jsonl": [],
        },
    )

    cache = updater.build_reprocessing_cache(
        sde_zip=sde_zip,
        fallback_info={},
        source_url="test-sde",
    )

    tyranite = cache["ores"]["88105"]
    assert tyranite["name"] == "Tyranite"
    assert tyranite["group_id"] == 4857
    assert tyranite["specialization_skill_type_id"] == updater.REPROCESSING_SKILL_TYPES["Simple Ore Processing"]
    assert tyranite["specialization_skill_name"] == "Simple Ore Processing"
    assert tyranite["materials"] == [
        {
            "type_id": 88087,
            "name": "Eleutrium",
            "quantity": 1035,
            "volume_m3": 0.01,
        },
    ]
