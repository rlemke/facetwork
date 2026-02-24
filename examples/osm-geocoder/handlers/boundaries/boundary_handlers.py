"""Boundary event facet handlers for OSM boundary extraction.

Handles administrative and natural boundary extraction events defined
in osmboundaries.afl under osm.geo.Boundaries.
"""

import logging
import os
from datetime import datetime, timezone

from .boundary_extractor import (
    ADMIN_LEVEL_CITY,
    ADMIN_LEVEL_COUNTRY,
    ADMIN_LEVEL_COUNTY,
    ADMIN_LEVEL_STATE,
    HAS_OSMIUM,
    extract_boundaries,
)

log = logging.getLogger(__name__)

NAMESPACE = "osm.geo.Boundaries"


def _make_admin_handler(facet_name: str, admin_levels: list[int]):
    """Create a handler for an administrative boundary event facet.

    Args:
        facet_name: Name of the event facet
        admin_levels: List of OSM admin_level values to extract

    Returns:
        Handler function that extracts boundaries and returns BoundaryResult
    """

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting admin levels {admin_levels} from {pbf_path}")
        log.info(
            "%s extracting admin levels %s from: %s",
            facet_name,
            admin_levels,
            pbf_path,
        )

        if not HAS_OSMIUM or not pbf_path:
            # Return mock result when pyosmium not available or no path
            return {
                "result": {
                    "output_path": "",
                    "feature_count": 0,
                    "boundary_type": ", ".join(_level_name(l) for l in admin_levels),
                    "admin_levels": ",".join(str(l) for l in admin_levels),
                    "format": "GeoJSON",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                }
            }

        result = extract_boundaries(pbf_path, admin_levels=admin_levels)
        if step_log:
            step_log(f"{facet_name}: extracted {result.feature_count} {result.boundary_type} boundaries", level="success")
        return {
            "result": {
                "output_path": result.output_path,
                "feature_count": result.feature_count,
                "boundary_type": result.boundary_type,
                "admin_levels": result.admin_levels,
                "format": result.format,
                "extraction_date": result.extraction_date,
            }
        }

    return handler


def _make_natural_handler(facet_name: str, natural_types: list[str]):
    """Create a handler for a natural boundary event facet.

    Args:
        facet_name: Name of the event facet
        natural_types: List of natural boundary types (water, forest, park)

    Returns:
        Handler function that extracts boundaries and returns BoundaryResult
    """

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting natural types {natural_types} from {pbf_path}")
        log.info(
            "%s extracting natural types %s from: %s",
            facet_name,
            natural_types,
            pbf_path,
        )

        if not HAS_OSMIUM or not pbf_path:
            # Return mock result when pyosmium not available or no path
            return {
                "result": {
                    "output_path": "",
                    "feature_count": 0,
                    "boundary_type": ", ".join(natural_types),
                    "admin_levels": "",
                    "format": "GeoJSON",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                }
            }

        result = extract_boundaries(pbf_path, natural_types=natural_types)
        if step_log:
            step_log(f"{facet_name}: extracted {result.feature_count} {result.boundary_type} boundaries", level="success")
        return {
            "result": {
                "output_path": result.output_path,
                "feature_count": result.feature_count,
                "boundary_type": result.boundary_type,
                "admin_levels": result.admin_levels,
                "format": result.format,
                "extraction_date": result.extraction_date,
            }
        }

    return handler


def _make_configurable_admin_handler(facet_name: str):
    """Create a handler for the configurable AdminBoundary event facet.

    Reads admin_level from the payload (defaults to 2 = country).
    """

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        admin_level = payload.get("admin_level", 2)
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting admin level {admin_level} from {pbf_path}")
        log.info(
            "%s extracting admin level %d from: %s",
            facet_name,
            admin_level,
            pbf_path,
        )

        if not HAS_OSMIUM or not pbf_path:
            return {
                "result": {
                    "output_path": "",
                    "feature_count": 0,
                    "boundary_type": _level_name(admin_level),
                    "admin_levels": str(admin_level),
                    "format": "GeoJSON",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                }
            }

        result = extract_boundaries(pbf_path, admin_levels=[admin_level])
        if step_log:
            step_log(f"{facet_name}: extracted {result.feature_count} {result.boundary_type} boundaries", level="success")
        return {
            "result": {
                "output_path": result.output_path,
                "feature_count": result.feature_count,
                "boundary_type": result.boundary_type,
                "admin_levels": result.admin_levels,
                "format": result.format,
                "extraction_date": result.extraction_date,
            }
        }

    return handler


def _make_configurable_natural_handler(facet_name: str):
    """Create a handler for the configurable NaturalBoundary event facet.

    Reads natural_type from the payload (defaults to "water").
    """

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        natural_type = payload.get("natural_type", "water")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting natural type {natural_type} from {pbf_path}")
        log.info(
            "%s extracting natural type %s from: %s",
            facet_name,
            natural_type,
            pbf_path,
        )

        if not HAS_OSMIUM or not pbf_path:
            return {
                "result": {
                    "output_path": "",
                    "feature_count": 0,
                    "boundary_type": natural_type,
                    "admin_levels": "",
                    "format": "GeoJSON",
                    "extraction_date": datetime.now(timezone.utc).isoformat(),
                }
            }

        result = extract_boundaries(pbf_path, natural_types=[natural_type])
        if step_log:
            step_log(f"{facet_name}: extracted {result.feature_count} {result.boundary_type} boundaries", level="success")
        return {
            "result": {
                "output_path": result.output_path,
                "feature_count": result.feature_count,
                "boundary_type": result.boundary_type,
                "admin_levels": result.admin_levels,
                "format": result.format,
                "extraction_date": result.extraction_date,
            }
        }

    return handler


def _level_name(level: int) -> str:
    """Get human-readable name for an admin level."""
    names = {
        ADMIN_LEVEL_COUNTRY: "country",
        ADMIN_LEVEL_STATE: "state",
        ADMIN_LEVEL_COUNTY: "county",
        ADMIN_LEVEL_CITY: "city",
    }
    return names.get(level, f"admin{level}")


# Fixed admin level handlers
ADMIN_FACETS: dict[str, list[int]] = {
    "CountryBoundaries": [ADMIN_LEVEL_COUNTRY],
    "StateBoundaries": [ADMIN_LEVEL_STATE],
    "CountyBoundaries": [ADMIN_LEVEL_COUNTY],
    "CityBoundaries": [ADMIN_LEVEL_CITY],
}

# Fixed natural type handlers
NATURAL_FACETS: dict[str, list[str]] = {
    "LakeBoundaries": ["water"],
    "ForestBoundaries": ["forest"],
    "ParkBoundaries": ["park"],
}


def register_boundary_handlers(poller) -> None:
    """Register all boundary event facet handlers with the poller."""
    # Register fixed admin level handlers
    for facet_name, admin_levels in ADMIN_FACETS.items():
        qualified_name = f"{NAMESPACE}.{facet_name}"
        poller.register(qualified_name, _make_admin_handler(facet_name, admin_levels))

    # Register configurable admin handler
    poller.register(
        f"{NAMESPACE}.AdminBoundary",
        _make_configurable_admin_handler("AdminBoundary"),
    )

    # Register fixed natural type handlers
    for facet_name, natural_types in NATURAL_FACETS.items():
        qualified_name = f"{NAMESPACE}.{facet_name}"
        poller.register(
            qualified_name, _make_natural_handler(facet_name, natural_types)
        )

    # Register configurable natural handler
    poller.register(
        f"{NAMESPACE}.NaturalBoundary",
        _make_configurable_natural_handler("NaturalBoundary"),
    )


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, callable] = {}


def _build_dispatch() -> None:
    for facet_name, admin_levels in ADMIN_FACETS.items():
        _DISPATCH[f"{NAMESPACE}.{facet_name}"] = _make_admin_handler(facet_name, admin_levels)
    _DISPATCH[f"{NAMESPACE}.AdminBoundary"] = _make_configurable_admin_handler("AdminBoundary")
    for facet_name, natural_types in NATURAL_FACETS.items():
        _DISPATCH[f"{NAMESPACE}.{facet_name}"] = _make_natural_handler(facet_name, natural_types)
    _DISPATCH[f"{NAMESPACE}.NaturalBoundary"] = _make_configurable_natural_handler("NaturalBoundary")


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
