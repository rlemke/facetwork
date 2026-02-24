"""Road extraction event facet handlers.

Handles road extraction events defined in osmroads.afl.
"""

import logging
import os
from datetime import datetime, timezone

from .road_extractor import (
    HAS_OSMIUM,
    RoadClass,
    RoadResult,
    RoadStats,
    MAJOR_ROAD_CLASSES,
    calculate_road_stats,
    extract_roads,
    filter_roads_by_class,
    filter_by_speed_limit,
)

log = logging.getLogger(__name__)

NAMESPACE = "osm.geo.Roads"


def _make_extract_roads_handler(facet_name: str):
    """Create handler for ExtractRoads event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        road_class = payload.get("road_class", "all")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {road_class} roads from {pbf_path}")
        log.info("%s extracting %s roads from %s", facet_name, road_class, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(road_class)}

        try:
            result = extract_roads(pbf_path, road_class=road_class)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {road_class} roads", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract roads: %s", e)
            return {"result": _empty_result(road_class)}

    return handler


def _make_typed_road_handler(facet_name: str, road_class: str):
    """Create handler for a specific road class."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {road_class} roads from {pbf_path}")
        log.info("%s extracting from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(road_class)}

        try:
            result = extract_roads(pbf_path, road_class=road_class)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {road_class} roads", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract %s roads: %s", road_class, e)
            return {"result": _empty_result(road_class)}

    return handler


def _make_major_roads_handler(facet_name: str):
    """Create handler for MajorRoads (motorway + trunk + primary + secondary)."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting major roads from {pbf_path}")
        log.info("%s extracting major roads from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("major")}

        try:
            # Extract all roads first, then filter to major classes
            result = extract_roads(pbf_path, road_class="all")

            import json
            from pathlib import Path

            with open(result.output_path, encoding="utf-8") as f:
                geojson = json.load(f)

            major_classes = {rc.value for rc in MAJOR_ROAD_CLASSES}
            filtered = [
                f for f in geojson.get("features", [])
                if f.get("properties", {}).get("road_class") in major_classes
            ]

            output_path = Path(result.output_path).with_stem(
                Path(result.output_path).stem.replace("_all_roads", "_major_roads")
            )
            output_geojson = {"type": "FeatureCollection", "features": filtered}

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_geojson, f, indent=2)

            total_length = sum(f["properties"].get("length_km", 0) for f in filtered)
            with_speed = sum(1 for f in filtered if f["properties"].get("maxspeed"))

            if step_log:
                step_log(f"{facet_name}: {len(filtered)}/{result.feature_count} major roads", level="success")
            return {"result": {
                "output_path": str(output_path),
                "feature_count": len(filtered),
                "road_class": "major",
                "total_length_km": round(total_length, 2),
                "with_speed_limit": with_speed,
                "format": "GeoJSON",
                "extraction_date": datetime.now(timezone.utc).isoformat(),
            }}
        except Exception as e:
            log.error("Failed to extract major roads: %s", e)
            return {"result": _empty_result("major")}

    return handler


def _make_special_road_handler(facet_name: str, attribute: str):
    """Create handler for special road features (bridges, tunnels, roundabouts)."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {attribute} from {pbf_path}")
        log.info("%s extracting %s from %s", facet_name, attribute, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(attribute)}

        try:
            # Extract all roads first, then filter
            result = extract_roads(pbf_path, road_class="all")

            import json
            from pathlib import Path

            with open(result.output_path, encoding="utf-8") as f:
                geojson = json.load(f)

            filtered = [
                f for f in geojson.get("features", [])
                if f.get("properties", {}).get(attribute)
            ]

            output_path = Path(result.output_path).with_stem(
                Path(result.output_path).stem.replace("_all_roads", f"_{attribute}s")
            )
            output_geojson = {"type": "FeatureCollection", "features": filtered}

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_geojson, f, indent=2)

            total_length = sum(f["properties"].get("length_km", 0) for f in filtered)
            with_speed = sum(1 for f in filtered if f["properties"].get("maxspeed"))

            if step_log:
                step_log(f"{facet_name}: {len(filtered)}/{result.feature_count} {attribute}s", level="success")
            return {"result": {
                "output_path": str(output_path),
                "feature_count": len(filtered),
                "road_class": attribute,
                "total_length_km": round(total_length, 2),
                "with_speed_limit": with_speed,
                "format": "GeoJSON",
                "extraction_date": datetime.now(timezone.utc).isoformat(),
            }}
        except Exception as e:
            log.error("Failed to extract %s: %s", attribute, e)
            return {"result": _empty_result(attribute)}

    return handler


def _make_surface_handler(facet_name: str, surface_type: str):
    """Create handler for surface-filtered roads (paved/unpaved)."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {surface_type} roads from {pbf_path}")
        log.info("%s extracting %s roads from %s", facet_name, surface_type, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(surface_type)}

        try:
            result = extract_roads(pbf_path, road_class="all", surface_filter=surface_type)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {surface_type} roads", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract %s roads: %s", surface_type, e)
            return {"result": _empty_result(surface_type)}

    return handler


def _make_speed_limit_handler(facet_name: str):
    """Create handler for RoadsWithSpeedLimit."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting roads with speed limits from {pbf_path}")
        log.info("%s extracting roads with speed limits from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("with_speed")}

        try:
            result = extract_roads(pbf_path, road_class="all", require_speed_limit=True)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} roads with speed limits", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract roads with speed limits: %s", e)
            return {"result": _empty_result("with_speed")}

    return handler


def _make_road_stats_handler(facet_name: str):
    """Create handler for RoadStatistics."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: calculating stats for {input_path}")
        log.info("%s calculating stats for %s", facet_name, input_path)

        if not input_path:
            return {"stats": _empty_stats()}

        try:
            stats = calculate_road_stats(input_path)
            if step_log:
                step_log(f"{facet_name}: {stats.total_roads} roads, {stats.total_length_km:.1f} km", level="success")
            return {"stats": _stats_to_dict(stats)}
        except Exception as e:
            log.error("Failed to calculate road stats: %s", e)
            return {"stats": _empty_stats()}

    return handler


def _make_filter_by_class_handler(facet_name: str):
    """Create handler for FilterRoadsByClass."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        road_class = payload.get("road_class", "all")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: filtering {input_path} for {road_class} roads")
        log.info("%s filtering %s for %s roads", facet_name, input_path, road_class)

        if not input_path:
            return {"result": _empty_result(road_class)}

        try:
            result = filter_roads_by_class(input_path, road_class)
            if step_log:
                step_log(f"{facet_name}: filtered to {result.feature_count} {road_class} roads", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to filter roads: %s", e)
            return {"result": _empty_result(road_class)}

    return handler


def _make_filter_by_speed_handler(facet_name: str):
    """Create handler for FilterBySpeedLimit."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        min_speed = payload.get("min_speed", 0)
        max_speed = payload.get("max_speed", 999)
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: filtering {input_path} for speed {min_speed}-{max_speed}")
        log.info("%s filtering %s for speed %d-%d", facet_name, input_path, min_speed, max_speed)

        if not input_path:
            return {"result": _empty_result("filtered")}

        try:
            result = filter_by_speed_limit(input_path, min_speed, max_speed)
            if step_log:
                step_log(f"{facet_name}: filtered to {result.feature_count} roads (speed {min_speed}-{max_speed})", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to filter by speed limit: %s", e)
            return {"result": _empty_result("filtered")}

    return handler


def _result_to_dict(result: RoadResult) -> dict:
    """Convert RoadResult to dict."""
    return {
        "output_path": result.output_path,
        "feature_count": result.feature_count,
        "road_class": result.road_class,
        "total_length_km": result.total_length_km,
        "with_speed_limit": result.with_speed_limit,
        "format": result.format,
        "extraction_date": result.extraction_date,
    }


def _stats_to_dict(stats: RoadStats) -> dict:
    """Convert RoadStats to dict."""
    return {
        "total_roads": stats.total_roads,
        "total_length_km": stats.total_length_km,
        "motorway_km": stats.motorway_km,
        "primary_km": stats.primary_km,
        "secondary_km": stats.secondary_km,
        "tertiary_km": stats.tertiary_km,
        "residential_km": stats.residential_km,
        "other_km": stats.other_km,
        "with_speed_limit": stats.with_speed_limit,
        "with_surface": stats.with_surface,
        "with_lanes": stats.with_lanes,
        "one_way_count": stats.one_way_count,
    }


def _empty_result(road_class: str) -> dict:
    """Return empty result dict."""
    return {
        "output_path": "",
        "feature_count": 0,
        "road_class": road_class,
        "total_length_km": 0.0,
        "with_speed_limit": 0,
        "format": "GeoJSON",
        "extraction_date": datetime.now(timezone.utc).isoformat(),
    }


def _empty_stats() -> dict:
    """Return empty stats dict."""
    return {
        "total_roads": 0,
        "total_length_km": 0.0,
        "motorway_km": 0.0,
        "primary_km": 0.0,
        "secondary_km": 0.0,
        "tertiary_km": 0.0,
        "residential_km": 0.0,
        "other_km": 0.0,
        "with_speed_limit": 0,
        "with_surface": 0,
        "with_lanes": 0,
        "one_way_count": 0,
    }


ROAD_FACETS = [
    # General extraction
    ("ExtractRoads", _make_extract_roads_handler),

    # By classification
    ("Motorways", lambda n: _make_typed_road_handler(n, "motorway")),
    ("PrimaryRoads", lambda n: _make_typed_road_handler(n, "primary")),
    ("SecondaryRoads", lambda n: _make_typed_road_handler(n, "secondary")),
    ("TertiaryRoads", lambda n: _make_typed_road_handler(n, "tertiary")),
    ("ResidentialRoads", lambda n: _make_typed_road_handler(n, "residential")),

    # Major roads combined
    ("MajorRoads", _make_major_roads_handler),

    # Special types
    ("Bridges", lambda n: _make_special_road_handler(n, "bridge")),
    ("Tunnels", lambda n: _make_special_road_handler(n, "tunnel")),

    # By surface
    ("PavedRoads", lambda n: _make_surface_handler(n, "paved")),
    ("UnpavedRoads", lambda n: _make_surface_handler(n, "unpaved")),

    # With attributes
    ("RoadsWithSpeedLimit", _make_speed_limit_handler),

    # Statistics and filtering
    ("RoadStatistics", _make_road_stats_handler),
    ("FilterRoadsByClass", _make_filter_by_class_handler),
    ("FilterBySpeedLimit", _make_filter_by_speed_handler),
]


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, callable] = {}


def _build_dispatch() -> None:
    for facet_name, handler_factory in ROAD_FACETS:
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


def register_road_handlers(poller) -> None:
    """Register all road event facet handlers."""
    for facet_name, handler_factory in ROAD_FACETS:
        qualified_name = f"{NAMESPACE}.{facet_name}"
        poller.register(qualified_name, handler_factory(facet_name))
        log.debug("Registered road handler: %s", qualified_name)
