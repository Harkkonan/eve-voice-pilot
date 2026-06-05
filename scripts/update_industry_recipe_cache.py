from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = ROOT / "cache" / "eve_industry_recipes.json"
DEFAULT_ROUTE_OUTPUT_PATH = ROOT / "cache" / "eve_route_graph.json"
LATEST_SDE_INFO_URL = "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
LATEST_JSONL_ZIP_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
RECIPE_SCHEMA = "eve_voice_pilot.industry_recipes.v1"
ROUTE_SCHEMA = "eve_voice_pilot.route_graph.v1"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        latest_info = load_latest_sde_info()
        sde_zip = args.sde_zip
        source_url = ""
        if sde_zip is None:
            sde_zip = download_latest_sde_zip(latest_info, force=args.force_download)
            source_url = LATEST_JSONL_ZIP_URL
        sde_zip = sde_zip.expanduser()
        recipe_cache = build_recipe_cache(sde_zip=sde_zip, fallback_info=latest_info, source_url=source_url)
        route_cache = build_route_graph_cache(sde_zip=sde_zip, fallback_info=latest_info, source_url=source_url)
        write_json(args.output.expanduser(), recipe_cache)
        write_json(args.route_output.expanduser(), route_cache)
    except (CorpRecipeCacheError, OSError, HTTPError, URLError) as exc:
        print(f"Could not update industry static caches: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {recipe_cache['recipe_count']} manufacturing recipes to {args.output}")
    print(f"Wrote {route_cache['system_count']} systems and {route_cache['edge_count']} gates to {args.route_output}")
    print(f"SDE build: {recipe_cache.get('build_number') or 'unknown'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the local Flight Attendant blueprint recipe cache from CCP's JSONL SDE.",
    )
    parser.add_argument(
        "--sde-zip",
        type=Path,
        help="Existing eve-online-static-data-*-jsonl.zip. Defaults to downloading the latest SDE into cache.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output recipe cache path.",
    )
    parser.add_argument(
        "--route-output",
        type=Path,
        default=DEFAULT_ROUTE_OUTPUT_PATH,
        help="Output route graph cache path.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Download the current SDE zip again even if the local cache copy exists.",
    )
    return parser


class CorpRecipeCacheError(RuntimeError):
    pass


def load_latest_sde_info() -> dict[str, Any]:
    request = Request(LATEST_SDE_INFO_URL, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=30.0) as response:
        raw = response.read().decode("utf-8")
    for line in raw.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict) and payload.get("_key") == "sde":
            return payload
    raise CorpRecipeCacheError("Latest SDE metadata did not include the sde record.")


def download_latest_sde_zip(latest_info: dict[str, Any], *, force: bool) -> Path:
    build_number = clean_int(latest_info.get("buildNumber"))
    if build_number <= 0:
        raise CorpRecipeCacheError("Latest SDE metadata did not include a build number.")
    target = ROOT / "cache" / f"eve-online-static-data-{build_number}-jsonl.zip"
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(".zip.part")
    request = Request(LATEST_JSONL_ZIP_URL, headers={"Accept": "application/zip"}, method="GET")
    with urlopen(request, timeout=120.0) as response, temp_target.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    temp_target.replace(target)
    return target


def build_recipe_cache(*, sde_zip: Path, fallback_info: dict[str, Any], source_url: str = "") -> dict[str, Any]:
    if not sde_zip.exists():
        raise CorpRecipeCacheError(f"SDE zip does not exist: {sde_zip}")
    with ZipFile(sde_zip) as archive:
        type_metadata = read_type_metadata(archive)
        sde_info = read_sde_info(archive) or fallback_info
        recipes = read_manufacturing_recipes(archive, type_metadata)

    sorted_recipes = {str(key): recipes[key] for key in sorted(recipes)}
    return {
        "schema": RECIPE_SCHEMA,
        "source": "eve_sde_jsonl",
        "source_url": source_url,
        "build_number": clean_int(sde_info.get("buildNumber")) or None,
        "release_date": str(sde_info.get("releaseDate") or ""),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "recipe_count": len(sorted_recipes),
        "recipes": sorted_recipes,
    }


def build_route_graph_cache(*, sde_zip: Path, fallback_info: dict[str, Any], source_url: str = "") -> dict[str, Any]:
    if not sde_zip.exists():
        raise CorpRecipeCacheError(f"SDE zip does not exist: {sde_zip}")
    with ZipFile(sde_zip) as archive:
        sde_info = read_sde_info(archive) or fallback_info
        systems = read_solar_systems(archive)
        adjacency = read_stargate_adjacency(archive, known_system_ids=set(systems))

    sorted_systems = {str(key): systems[key] for key in sorted(systems)}
    sorted_adjacency = {
        str(key): sorted(adjacency[key])
        for key in sorted(adjacency)
        if key in systems and adjacency[key]
    }
    edge_count = sum(len(targets) for targets in sorted_adjacency.values())
    return {
        "schema": ROUTE_SCHEMA,
        "source": "eve_sde_jsonl",
        "source_url": source_url,
        "build_number": clean_int(sde_info.get("buildNumber")) or None,
        "release_date": str(sde_info.get("releaseDate") or ""),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "system_count": len(sorted_systems),
        "edge_count": edge_count,
        "systems": sorted_systems,
        "adjacency": sorted_adjacency,
    }


def read_type_metadata(archive: ZipFile) -> dict[int, dict[str, Any]]:
    metadata: dict[int, dict[str, Any]] = {}
    for record in iter_jsonl_member(archive, "types.jsonl"):
        type_id = clean_int(record.get("_key"))
        if type_id <= 0:
            continue
        name = english_name(record.get("name"))
        metadata[type_id] = {
            "name": name or f"Type {type_id}",
            "volume_m3": clean_float(record.get("volume")),
        }
    return metadata


def read_solar_systems(archive: ZipFile) -> dict[int, dict[str, Any]]:
    systems: dict[int, dict[str, Any]] = {}
    for record in iter_jsonl_member(archive, "mapSolarSystems.jsonl"):
        system_id = clean_int(record.get("_key"))
        if system_id <= 0:
            continue
        systems[system_id] = {
            "solar_system_id": system_id,
            "name": english_name(record.get("name")) or f"System {system_id}",
            "region_id": clean_int(record.get("regionID")) or None,
            "constellation_id": clean_int(record.get("constellationID")) or None,
            "security_status": clean_float(record.get("securityStatus")),
            "security_class": str(record.get("securityClass") or ""),
        }
    return systems


def read_stargate_adjacency(archive: ZipFile, *, known_system_ids: set[int]) -> dict[int, set[int]]:
    adjacency: dict[int, set[int]] = {system_id: set() for system_id in known_system_ids}
    for record in iter_jsonl_member(archive, "mapStargates.jsonl"):
        source_system_id = clean_int(record.get("solarSystemID"))
        destination = record.get("destination")
        if not isinstance(destination, dict):
            continue
        target_system_id = clean_int(destination.get("solarSystemID"))
        if source_system_id <= 0 or target_system_id <= 0:
            continue
        if source_system_id not in known_system_ids or target_system_id not in known_system_ids:
            continue
        adjacency.setdefault(source_system_id, set()).add(target_system_id)
        adjacency.setdefault(target_system_id, set()).add(source_system_id)
    return adjacency


def read_sde_info(archive: ZipFile) -> dict[str, Any] | None:
    for record in iter_jsonl_member(archive, "_sde.jsonl"):
        if record.get("_key") == "sde":
            return record
    return None


def read_manufacturing_recipes(archive: ZipFile, type_metadata: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    recipes: dict[int, dict[str, Any]] = {}
    for record in iter_jsonl_member(archive, "blueprints.jsonl"):
        blueprint_type_id = clean_int(record.get("blueprintTypeID") or record.get("_key"))
        activities = record.get("activities")
        if blueprint_type_id <= 0 or not isinstance(activities, dict):
            continue
        manufacturing = activities.get("manufacturing")
        if not isinstance(manufacturing, dict):
            continue
        product_records = list(clean_records(manufacturing.get("products")))
        material_records = list(clean_records(manufacturing.get("materials")))
        skill_records = list(clean_records(manufacturing.get("skills")))
        if not product_records or not material_records:
            continue

        products = []
        for product in product_records:
            product_type_id = clean_int(product.get("typeID") or product.get("type_id"))
            quantity = clean_int(product.get("quantity"))
            if product_type_id > 0 and quantity > 0:
                product_payload = {
                    "type_id": product_type_id,
                    "name": type_name(type_metadata, product_type_id),
                    "quantity": quantity,
                }
                volume_m3 = type_volume(type_metadata, product_type_id)
                if volume_m3 is not None:
                    product_payload["volume_m3"] = volume_m3
                products.append(product_payload)
        materials = []
        for material in material_records:
            material_type_id = clean_int(material.get("typeID") or material.get("type_id"))
            quantity = clean_int(material.get("quantity"))
            if material_type_id > 0 and quantity > 0:
                material_payload = {
                    "type_id": material_type_id,
                    "name": type_name(type_metadata, material_type_id),
                    "quantity": quantity,
                }
                volume_m3 = type_volume(type_metadata, material_type_id)
                if volume_m3 is not None:
                    material_payload["volume_m3"] = volume_m3
                materials.append(material_payload)
        skills = []
        for skill in skill_records:
            skill_type_id = clean_int(skill.get("typeID") or skill.get("type_id"))
            level = clean_int(skill.get("level"))
            if skill_type_id > 0 and level > 0:
                skills.append(
                    {
                        "type_id": skill_type_id,
                        "name": type_name(type_metadata, skill_type_id, fallback_prefix="Skill"),
                        "level": level,
                    }
                )
        if not products or not materials:
            continue

        first_product = products[0]
        recipes[blueprint_type_id] = {
            "blueprint_type_id": blueprint_type_id,
            "blueprint_name": type_name(type_metadata, blueprint_type_id, fallback_prefix="Blueprint"),
            "max_production_limit": clean_int(record.get("maxProductionLimit") or record.get("max_production_limit")),
            "activity": "manufacturing",
            "product_type_id": first_product["type_id"],
            "product_name": first_product["name"],
            "product_quantity": first_product["quantity"],
            "products": products,
            "manufacturing_time_seconds": clean_int(manufacturing.get("time")),
            "materials": materials,
            "skills": skills,
        }
    return recipes


def type_name(type_metadata: dict[int, dict[str, Any]], type_id: int, *, fallback_prefix: str = "Type") -> str:
    metadata = type_metadata.get(type_id) or {}
    name = str(metadata.get("name") or "").strip()
    return name or f"{fallback_prefix} {type_id}"


def type_volume(type_metadata: dict[int, dict[str, Any]], type_id: int) -> float | None:
    metadata = type_metadata.get(type_id) or {}
    volume_m3 = clean_float(metadata.get("volume_m3"))
    if volume_m3 is None or volume_m3 <= 0:
        return None
    return volume_m3


def iter_jsonl_member(archive: ZipFile, name: str) -> Iterable[dict[str, Any]]:
    if name not in archive.namelist():
        raise CorpRecipeCacheError(f"SDE zip is missing {name}.")
    with archive.open(name) as handle:
        for raw_line in handle:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorpRecipeCacheError(f"{name} has invalid JSONL: {exc}") from exc
            if isinstance(payload, dict):
                yield payload


def clean_records(value: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def english_name(value: Any) -> str:
    if isinstance(value, dict):
        candidate = value.get("en")
        if candidate:
            return str(candidate)
        for item in value.values():
            if item:
                return str(item)
    if value:
        return str(value)
    return ""


def clean_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def clean_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
