"""Amenity extraction event facet handlers.

Handles amenity extraction events defined in osmamenities.afl.
"""

import logging
import os
from datetime import datetime, timezone

from .amenity_extractor import (
    HAS_OSMIUM,
    AmenityCategory,
    AmenityResult,
    AmenityStats,
    calculate_amenity_stats,
    extract_amenities,
    search_amenities,
    FOOD_AMENITIES,
    SHOPPING_TAGS,
    HEALTHCARE_AMENITIES,
    EDUCATION_AMENITIES,
    ENTERTAINMENT_AMENITIES,
)

log = logging.getLogger(__name__)

NAMESPACE = "osm.geo.Amenities"


def _make_extract_amenities_handler(facet_name: str):
    """Create handler for ExtractAmenities event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        category = payload.get("category", "all")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {category} amenities from {pbf_path}")
        log.info("%s extracting %s amenities from %s", facet_name, category, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(category)}

        try:
            result = extract_amenities(pbf_path, category=category)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {category} amenities", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract amenities: %s", e)
            return {"result": _empty_result(category)}

    return handler


def _make_typed_amenity_handler(facet_name: str, amenity_types: set[str], category: str):
    """Create handler for a specific amenity type."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {category} amenities from {pbf_path}")
        log.info("%s extracting from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(category)}

        try:
            result = extract_amenities(pbf_path, amenity_types=amenity_types)
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {category} amenities", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract %s amenities: %s", category, e)
            return {"result": _empty_result(category)}

    return handler


def _make_single_amenity_handler(facet_name: str, amenity_type: str, category: str):
    """Create handler for a single amenity type."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {amenity_type} from {pbf_path}")
        log.info("%s extracting from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(category)}

        try:
            result = extract_amenities(pbf_path, amenity_types={amenity_type})
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {amenity_type}", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract %s: %s", amenity_type, e)
            return {"result": _empty_result(category)}

    return handler


def _make_amenity_stats_handler(facet_name: str):
    """Create handler for AmenityStatistics."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: calculating stats for {input_path}")
        log.info("%s calculating stats for %s", facet_name, input_path)

        if not input_path:
            return {"stats": _empty_stats()}

        try:
            stats = calculate_amenity_stats(input_path)
            if step_log:
                step_log(
                    f"{facet_name}: {stats.total_amenities} amenities"
                    f" (food={stats.food}, shopping={stats.shopping}, healthcare={stats.healthcare})",
                    level="success",
                )
            return {"stats": _stats_to_dict(stats)}
        except Exception as e:
            log.error("Failed to calculate amenity stats: %s", e)
            return {"stats": _empty_stats()}

    return handler


def _make_search_amenities_handler(facet_name: str):
    """Create handler for SearchAmenities."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        name_pattern = payload.get("name_pattern", ".*")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: searching {input_path} for pattern '{name_pattern}'")
        log.info("%s searching %s for pattern '%s'", facet_name, input_path, name_pattern)

        if not input_path:
            return {"result": _empty_result("search")}

        try:
            result = search_amenities(input_path, name_pattern)
            if step_log:
                step_log(f"{facet_name}: found {result.feature_count} matching '{name_pattern}'", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to search amenities: %s", e)
            return {"result": _empty_result("search")}

    return handler


def _make_filter_by_category_handler(facet_name: str):
    """Create handler for FilterByCategory."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        category = payload.get("category", "all")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: filtering {input_path} for category {category}")
        log.info("%s filtering %s for category %s", facet_name, input_path, category)

        if not input_path:
            return {"result": _empty_result(category)}

        try:
            import json
            from pathlib import Path

            input_path = Path(input_path)
            with open(input_path, encoding="utf-8") as f:
                geojson = json.load(f)

            filtered = []
            for feature in geojson.get("features", []):
                props = feature.get("properties", {})
                if category == "all" or props.get("category") == category:
                    filtered.append(feature)

            output_path = input_path.with_stem(f"{input_path.stem}_{category}")
            output_geojson = {"type": "FeatureCollection", "features": filtered}

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_geojson, f, indent=2)

            all_features = geojson.get("features", [])
            if step_log:
                step_log(f"{facet_name}: {len(filtered)}/{len(all_features)} {category} amenities", level="success")
            return {"result": {
                "output_path": str(output_path),
                "feature_count": len(filtered),
                "amenity_category": category,
                "amenity_types": category,
                "format": "GeoJSON",
                "extraction_date": datetime.now(timezone.utc).isoformat(),
            }}
        except Exception as e:
            log.error("Failed to filter amenities: %s", e)
            return {"result": _empty_result(category)}

    return handler


def _result_to_dict(result: AmenityResult) -> dict:
    """Convert AmenityResult to dict."""
    return {
        "output_path": result.output_path,
        "feature_count": result.feature_count,
        "amenity_category": result.amenity_category,
        "amenity_types": result.amenity_types,
        "format": result.format,
        "extraction_date": result.extraction_date,
    }


def _stats_to_dict(stats: AmenityStats) -> dict:
    """Convert AmenityStats to dict."""
    return {
        "total_amenities": stats.total_amenities,
        "food": stats.food,
        "shopping": stats.shopping,
        "services": stats.services,
        "healthcare": stats.healthcare,
        "education": stats.education,
        "entertainment": stats.entertainment,
        "transport": stats.transport,
        "other": stats.other,
        "with_name": stats.with_name,
        "with_opening_hours": stats.with_opening_hours,
    }


def _empty_result(category: str) -> dict:
    """Return empty result dict."""
    return {
        "output_path": "",
        "feature_count": 0,
        "amenity_category": category,
        "amenity_types": category,
        "format": "GeoJSON",
        "extraction_date": datetime.now(timezone.utc).isoformat(),
    }


def _empty_stats() -> dict:
    """Return empty stats dict."""
    return {
        "total_amenities": 0,
        "food": 0,
        "shopping": 0,
        "services": 0,
        "healthcare": 0,
        "education": 0,
        "entertainment": 0,
        "transport": 0,
        "other": 0,
        "with_name": 0,
        "with_opening_hours": 0,
    }


AMENITY_FACETS = [
    # General extraction
    ("ExtractAmenities", _make_extract_amenities_handler),

    # Food & Drink
    ("FoodAndDrink", lambda n: _make_typed_amenity_handler(n, FOOD_AMENITIES, "food")),
    ("Restaurants", lambda n: _make_single_amenity_handler(n, "restaurant", "food")),
    ("Cafes", lambda n: _make_single_amenity_handler(n, "cafe", "food")),
    ("Bars", lambda n: _make_single_amenity_handler(n, "bar", "food")),
    ("FastFood", lambda n: _make_single_amenity_handler(n, "fast_food", "food")),

    # Shopping
    ("Shopping", lambda n: _make_typed_amenity_handler(n, SHOPPING_TAGS, "shopping")),
    ("Supermarkets", lambda n: _make_single_amenity_handler(n, "supermarket", "shopping")),

    # Services
    ("Banks", lambda n: _make_single_amenity_handler(n, "bank", "services")),
    ("ATMs", lambda n: _make_single_amenity_handler(n, "atm", "services")),
    ("PostOffices", lambda n: _make_single_amenity_handler(n, "post_office", "services")),
    ("FuelStations", lambda n: _make_single_amenity_handler(n, "fuel", "services")),
    ("ChargingStations", lambda n: _make_single_amenity_handler(n, "charging_station", "services")),
    ("Parking", lambda n: _make_single_amenity_handler(n, "parking", "services")),

    # Healthcare
    ("Healthcare", lambda n: _make_typed_amenity_handler(n, HEALTHCARE_AMENITIES, "healthcare")),
    ("Hospitals", lambda n: _make_single_amenity_handler(n, "hospital", "healthcare")),
    ("Clinics", lambda n: _make_single_amenity_handler(n, "clinic", "healthcare")),
    ("Pharmacies", lambda n: _make_single_amenity_handler(n, "pharmacy", "healthcare")),
    ("Dentists", lambda n: _make_single_amenity_handler(n, "dentist", "healthcare")),

    # Education
    ("Education", lambda n: _make_typed_amenity_handler(n, EDUCATION_AMENITIES, "education")),
    ("Schools", lambda n: _make_single_amenity_handler(n, "school", "education")),
    ("Universities", lambda n: _make_single_amenity_handler(n, "university", "education")),
    ("Libraries", lambda n: _make_single_amenity_handler(n, "library", "education")),

    # Entertainment
    ("Entertainment", lambda n: _make_typed_amenity_handler(n, ENTERTAINMENT_AMENITIES, "entertainment")),
    ("Cinemas", lambda n: _make_single_amenity_handler(n, "cinema", "entertainment")),
    ("Theatres", lambda n: _make_single_amenity_handler(n, "theatre", "entertainment")),

    # Statistics and filtering
    ("AmenityStatistics", _make_amenity_stats_handler),
    ("SearchAmenities", _make_search_amenities_handler),
    ("FilterByCategory", _make_filter_by_category_handler),
]


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, callable] = {}


def _build_dispatch() -> None:
    for facet_name, handler_factory in AMENITY_FACETS:
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


def register_amenity_handlers(poller) -> None:
    """Register all amenity event facet handlers."""
    for facet_name, handler_factory in AMENITY_FACETS:
        qualified_name = f"{NAMESPACE}.{facet_name}"
        poller.register(qualified_name, handler_factory(facet_name))
        log.debug("Registered amenity handler: %s", qualified_name)
