"""Building extraction event facet handlers.

Handles building extraction events defined in osmbuildings.afl.
"""

import logging
import os
from datetime import datetime, timezone

from .building_extractor import (
    HAS_OSMIUM,
    BuildingResult,
    BuildingStats,
    BuildingType,
    calculate_building_stats,
    extract_buildings,
)

log = logging.getLogger(__name__)

NAMESPACE = "osm.geo.Buildings"


def _make_extract_buildings_handler(facet_name: str):
    """Create handler for ExtractBuildings event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        building_type = payload.get("building_type", "all")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {building_type} buildings from {pbf_path}")
        log.info("%s extracting %s buildings from %s", facet_name, building_type, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(building_type)}

        try:
            result = extract_buildings(pbf_path, building_type=building_type)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {building_type} buildings", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract buildings: %s", e)
            return {"result": _empty_result(building_type)}

    return handler


def _make_typed_building_handler(facet_name: str, building_type: str):
    """Create handler for a specific building type."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {building_type} buildings from {pbf_path}")
        log.info("%s extracting from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(building_type)}

        try:
            result = extract_buildings(pbf_path, building_type=building_type)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {building_type} buildings", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract %s buildings: %s", building_type, e)
            return {"result": _empty_result(building_type)}

    return handler


def _make_buildings_3d_handler(facet_name: str):
    """Create handler for Buildings3D (buildings with height data)."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting 3D buildings from {pbf_path}")
        log.info("%s extracting 3D buildings from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("all")}

        try:
            result = extract_buildings(pbf_path, building_type="all", require_height=True)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} 3D buildings", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract 3D buildings: %s", e)
            return {"result": _empty_result("all")}

    return handler


def _make_large_buildings_handler(facet_name: str):
    """Create handler for LargeBuildings."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        min_area_m2 = payload.get("min_area_m2", 1000.0)
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting buildings >= {min_area_m2:.0f} m2 from {pbf_path}")
        log.info("%s extracting buildings >= %.0f m² from %s", facet_name, min_area_m2, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("all")}

        try:
            result = extract_buildings(pbf_path, building_type="all", min_area_m2=min_area_m2)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} buildings >= {min_area_m2:.0f} m2", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract large buildings: %s", e)
            return {"result": _empty_result("all")}

    return handler


def _make_building_stats_handler(facet_name: str):
    """Create handler for BuildingStatistics."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: calculating stats for {input_path}")
        log.info("%s calculating stats for %s", facet_name, input_path)

        if not input_path:
            return {"stats": _empty_stats()}

        try:
            stats = calculate_building_stats(input_path)
            if step_log:
                step_log(
                    f"{facet_name}: {stats.total_buildings} buildings, {stats.total_area_km2:.2f} km2"
                    f" (residential={stats.residential}, commercial={stats.commercial})",
                    level="success",
                )
            return {"stats": _stats_to_dict(stats)}
        except Exception as e:
            log.error("Failed to calculate building stats: %s", e)
            return {"stats": _empty_stats()}

    return handler


def _make_filter_buildings_handler(facet_name: str):
    """Create handler for FilterBuildingsByType."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        building_type = payload.get("building_type", "all")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: filtering {input_path} for {building_type} buildings")
        log.info("%s filtering %s for %s buildings", facet_name, input_path, building_type)

        if not input_path:
            return {"result": _empty_result(building_type)}

        try:
            import json
            from pathlib import Path

            input_path = Path(input_path)
            with open(input_path, encoding="utf-8") as f:
                geojson = json.load(f)

            filtered = []
            for feature in geojson.get("features", []):
                props = feature.get("properties", {})
                if building_type == "all" or props.get("building_type") == building_type:
                    filtered.append(feature)

            output_path = input_path.with_stem(f"{input_path.stem}_{building_type}")
            output_geojson = {"type": "FeatureCollection", "features": filtered}

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_geojson, f, indent=2)

            total_area = sum(f["properties"].get("area_m2", 0) for f in filtered)
            with_height = sum(1 for f in filtered
                              if f["properties"].get("height") or f["properties"].get("levels"))

            all_features = geojson.get("features", [])
            if step_log:
                step_log(f"{facet_name}: {len(filtered)}/{len(all_features)} {building_type} buildings", level="success")
            return {"result": {
                "output_path": str(output_path),
                "feature_count": len(filtered),
                "building_type": building_type,
                "total_area_km2": round(total_area / 1_000_000, 4),
                "with_height_data": with_height,
                "format": "GeoJSON",
                "extraction_date": datetime.now(timezone.utc).isoformat(),
            }}
        except Exception as e:
            log.error("Failed to filter buildings: %s", e)
            return {"result": _empty_result(building_type)}

    return handler


def _result_to_dict(result: BuildingResult) -> dict:
    """Convert BuildingResult to dict."""
    return {
        "output_path": result.output_path,
        "feature_count": result.feature_count,
        "building_type": result.building_type,
        "total_area_km2": result.total_area_km2,
        "with_height_data": result.with_height_data,
        "format": result.format,
        "extraction_date": result.extraction_date,
    }


def _stats_to_dict(stats: BuildingStats) -> dict:
    """Convert BuildingStats to dict."""
    return {
        "total_buildings": stats.total_buildings,
        "total_area_km2": stats.total_area_km2,
        "residential": stats.residential,
        "commercial": stats.commercial,
        "industrial": stats.industrial,
        "retail": stats.retail,
        "other": stats.other,
        "avg_levels": stats.avg_levels,
        "with_height": stats.with_height,
    }


def _empty_result(building_type: str) -> dict:
    """Return empty result dict."""
    return {
        "output_path": "",
        "feature_count": 0,
        "building_type": building_type,
        "total_area_km2": 0.0,
        "with_height_data": 0,
        "format": "GeoJSON",
        "extraction_date": datetime.now(timezone.utc).isoformat(),
    }


def _empty_stats() -> dict:
    """Return empty stats dict."""
    return {
        "total_buildings": 0,
        "total_area_km2": 0.0,
        "residential": 0,
        "commercial": 0,
        "industrial": 0,
        "retail": 0,
        "other": 0,
        "avg_levels": 0.0,
        "with_height": 0,
    }


BUILDING_FACETS = [
    ("ExtractBuildings", _make_extract_buildings_handler),
    ("ResidentialBuildings", lambda n: _make_typed_building_handler(n, "residential")),
    ("CommercialBuildings", lambda n: _make_typed_building_handler(n, "commercial")),
    ("IndustrialBuildings", lambda n: _make_typed_building_handler(n, "industrial")),
    ("RetailBuildings", lambda n: _make_typed_building_handler(n, "retail")),
    ("Buildings3D", _make_buildings_3d_handler),
    ("LargeBuildings", _make_large_buildings_handler),
    ("BuildingStatistics", _make_building_stats_handler),
    ("FilterBuildingsByType", _make_filter_buildings_handler),
]


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, callable] = {}


def _build_dispatch() -> None:
    for facet_name, handler_factory in BUILDING_FACETS:
        _DISPATCH[f"{NAMESPACE}.{facet_name}"] = handler_factory(facet_name)


_build_dispatch()


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_handlers(runner) -> None:
    """Register all facets with a RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_building_handlers(poller) -> None:
    """Register all building event facet handlers."""
    for facet_name, handler_factory in BUILDING_FACETS:
        qualified_name = f"{NAMESPACE}.{facet_name}"
        poller.register(qualified_name, handler_factory(facet_name))
        log.debug("Registered building handler: %s", qualified_name)
