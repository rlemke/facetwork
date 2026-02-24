"""Park extraction event facet handlers.

Handles park extraction events defined in osmparks.afl under osm.geo.Parks.
"""

import logging
import os
from datetime import datetime, timezone

from .park_extractor import (
    HAS_OSMIUM,
    ParkResult,
    ParkStats,
    ParkType,
    calculate_park_stats,
    extract_parks,
    filter_parks_by_type,
)

log = logging.getLogger(__name__)

NAMESPACE = "osm.geo.Parks"


def _make_national_parks_handler(facet_name: str):
    """Create handler for NationalParks event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting national parks from {pbf_path}")
        log.info("%s extracting national parks from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("national", "*")}

        try:
            result = extract_parks(
                pbf_path,
                park_type=ParkType.NATIONAL,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} national parks", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract national parks: %s", e)
            return {"result": _empty_result("national", "*")}

    return handler


def _make_state_parks_handler(facet_name: str):
    """Create handler for StateParks event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting state parks from {pbf_path}")
        log.info("%s extracting state parks from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("state", "*")}

        try:
            result = extract_parks(
                pbf_path,
                park_type=ParkType.STATE,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} state parks", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract state parks: %s", e)
            return {"result": _empty_result("state", "*")}

    return handler


def _make_nature_reserves_handler(facet_name: str):
    """Create handler for NatureReserves event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting nature reserves from {pbf_path}")
        log.info("%s extracting nature reserves from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("nature_reserve", "*")}

        try:
            result = extract_parks(
                pbf_path,
                park_type=ParkType.NATURE_RESERVE,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} nature reserves", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract nature reserves: %s", e)
            return {"result": _empty_result("nature_reserve", "*")}

    return handler


def _make_protected_areas_handler(facet_name: str):
    """Create handler for ProtectedAreas event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        protect_classes = payload.get("protect_classes", "*")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting protected areas from {pbf_path}")
        log.info(
            "%s extracting protected areas from %s (classes=%s)",
            facet_name, pbf_path, protect_classes
        )

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("protected_area", protect_classes)}

        try:
            result = extract_parks(
                pbf_path,
                park_type=ParkType.PROTECTED_AREA,
                protect_classes=protect_classes,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} protected areas", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract protected areas: %s", e)
            return {"result": _empty_result("protected_area", protect_classes)}

    return handler


def _make_extract_parks_handler(facet_name: str):
    """Create handler for ExtractParks event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        park_type = payload.get("park_type", "all")
        protect_classes = payload.get("protect_classes", "*")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {park_type} parks from {pbf_path}")
        log.info(
            "%s extracting %s parks from %s (classes=%s)",
            facet_name, park_type, pbf_path, protect_classes
        )

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(park_type, protect_classes)}

        try:
            result = extract_parks(
                pbf_path,
                park_type=park_type,
                protect_classes=protect_classes,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {park_type} parks", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract parks: %s", e)
            return {"result": _empty_result(park_type, protect_classes)}

    return handler


def _make_filter_parks_handler(facet_name: str):
    """Create handler for FilterParksByType event facet."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        park_type = payload.get("park_type", "all")
        protect_classes = payload.get("protect_classes", "*")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: filtering {input_path} for {park_type} parks")
        log.info(
            "%s filtering %s for %s parks (classes=%s)",
            facet_name, input_path, park_type, protect_classes
        )

        if not input_path:
            return {"result": _empty_result(park_type, protect_classes)}

        try:
            result = filter_parks_by_type(
                input_path,
                park_type=park_type,
                protect_classes=protect_classes,
            )
            if step_log:
                step_log(f"{facet_name}: filtered to {result.feature_count} {park_type} parks", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to filter parks: %s", e)
            return {"result": _empty_result(park_type, protect_classes)}

    return handler


def _make_park_stats_handler(facet_name: str):
    """Create handler for ParkStatistics event facet."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: calculating stats for {input_path}")
        log.info("%s calculating stats for %s", facet_name, input_path)

        if not input_path:
            return {"stats": _empty_stats()}

        try:
            stats = calculate_park_stats(input_path)
            if step_log:
                step_log(
                    f"{facet_name}: {stats.total_parks} parks, {stats.total_area_km2:.1f} km2"
                    f" (national={stats.national_parks}, state={stats.state_parks},"
                    f" reserves={stats.nature_reserves})",
                    level="success",
                )
            return {"stats": _stats_to_dict(stats)}
        except Exception as e:
            log.error("Failed to calculate park stats: %s", e)
            return {"stats": _empty_stats()}

    return handler


def _make_large_parks_handler(facet_name: str):
    """Create handler for LargeParks event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        min_area_km2 = payload.get("min_area_km2", 100.0)
        park_type = payload.get("park_type", "all")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {park_type} parks >= {min_area_km2:.1f} km2 from {pbf_path}")
        log.info(
            "%s extracting %s parks >= %.1f km² from %s",
            facet_name, park_type, min_area_km2, pbf_path
        )

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(park_type, "*")}

        try:
            result = extract_parks(
                pbf_path,
                park_type=park_type,
                min_area_km2=min_area_km2,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} large {park_type} parks (>= {min_area_km2:.1f} km2)", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract large parks: %s", e)
            return {"result": _empty_result(park_type, "*")}

    return handler


def _result_to_dict(result: ParkResult) -> dict:
    """Convert a ParkResult to a dictionary."""
    return {
        "output_path": result.output_path,
        "feature_count": result.feature_count,
        "park_type": result.park_type,
        "protect_classes": result.protect_classes,
        "total_area_km2": result.total_area_km2,
        "format": result.format,
        "extraction_date": result.extraction_date,
    }


def _stats_to_dict(stats: ParkStats) -> dict:
    """Convert ParkStats to a dictionary."""
    return {
        "total_parks": stats.total_parks,
        "total_area_km2": stats.total_area_km2,
        "national_parks": stats.national_parks,
        "state_parks": stats.state_parks,
        "nature_reserves": stats.nature_reserves,
        "other_protected": stats.other_protected,
        "park_type": stats.park_type,
    }


def _empty_result(park_type: str, protect_classes: str) -> dict:
    """Return an empty result dict."""
    return {
        "output_path": "",
        "feature_count": 0,
        "park_type": park_type,
        "protect_classes": protect_classes,
        "total_area_km2": 0.0,
        "format": "GeoJSON",
        "extraction_date": datetime.now(timezone.utc).isoformat(),
    }


def _empty_stats() -> dict:
    """Return empty stats dict."""
    return {
        "total_parks": 0,
        "total_area_km2": 0.0,
        "national_parks": 0,
        "state_parks": 0,
        "nature_reserves": 0,
        "other_protected": 0,
        "park_type": "",
    }


# Event facet definitions for handler registration
PARK_FACETS = [
    ("NationalParks", _make_national_parks_handler),
    ("StateParks", _make_state_parks_handler),
    ("NatureReserves", _make_nature_reserves_handler),
    ("ProtectedAreas", _make_protected_areas_handler),
    ("ExtractParks", _make_extract_parks_handler),
    ("FilterParksByType", _make_filter_parks_handler),
    ("ParkStatistics", _make_park_stats_handler),
    ("LargeParks", _make_large_parks_handler),
]


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, callable] = {}


def _build_dispatch() -> None:
    for facet_name, handler_factory in PARK_FACETS:
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


def register_park_handlers(poller) -> None:
    """Register all park event facet handlers with the poller."""
    for facet_name, handler_factory in PARK_FACETS:
        qualified_name = f"{NAMESPACE}.{facet_name}"
        poller.register(qualified_name, handler_factory(facet_name))
        log.debug("Registered park handler: %s", qualified_name)
