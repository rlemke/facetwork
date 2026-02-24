"""Route extraction event facet handlers.

Handles route extraction events defined in osmroutes.afl under osm.geo.Routes.
"""

import logging
import os
from datetime import datetime, timezone

from .route_extractor import (
    HAS_OSMIUM,
    RouteResult,
    RouteStats,
    RouteType,
    calculate_route_stats,
    extract_routes,
    filter_routes_by_type,
)

log = logging.getLogger(__name__)

NAMESPACE = "osm.geo.Routes"


def _make_extract_routes_handler(facet_name: str):
    """Create handler for ExtractRoutes event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        route_type = payload.get("route_type", "bicycle")
        network = payload.get("network", "*")
        include_infrastructure = payload.get("include_infrastructure", True)
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {route_type} routes from {pbf_path}")
        log.info(
            "%s extracting %s routes from %s (network=%s, infra=%s)",
            facet_name, route_type, pbf_path, network, include_infrastructure
        )

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(route_type, network, include_infrastructure)}

        try:
            result = extract_routes(
                pbf_path,
                route_type=route_type,
                network=network,
                include_infrastructure=include_infrastructure,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {route_type} routes", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract routes: %s", e)
            return {"result": _empty_result(route_type, network, include_infrastructure)}

    return handler


def _make_filter_routes_handler(facet_name: str):
    """Create handler for FilterRoutesByType event facet."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        route_type = payload.get("route_type", "bicycle")
        network = payload.get("network", "*")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: filtering {input_path} for {route_type} routes")
        log.info(
            "%s filtering %s for %s routes (network=%s)",
            facet_name, input_path, route_type, network
        )

        if not input_path:
            return {"result": _empty_result(route_type, network, False)}

        try:
            result = filter_routes_by_type(
                input_path,
                route_type=route_type,
                network=network,
            )
            if step_log:
                step_log(f"{facet_name}: filtered to {result.feature_count} {route_type} routes", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to filter routes: %s", e)
            return {"result": _empty_result(route_type, network, False)}

    return handler


def _make_route_stats_handler(facet_name: str):
    """Create handler for RouteStatistics event facet."""

    def handler(payload: dict) -> dict:
        input_path = payload.get("input_path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: calculating stats for {input_path}")
        log.info("%s calculating stats for %s", facet_name, input_path)

        if not input_path:
            return {"stats": _empty_stats()}

        try:
            stats = calculate_route_stats(input_path)
            if step_log:
                step_log(f"{facet_name}: {stats.route_count} routes, {stats.total_length_km:.1f} km", level="success")
            return {"stats": _stats_to_dict(stats)}
        except Exception as e:
            log.error("Failed to calculate stats: %s", e)
            return {"stats": _empty_stats()}

    return handler


def _make_typed_route_handler(facet_name: str, route_type: str):
    """Create handler for a specific route type (BicycleRoutes, HikingTrails, etc.)."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        network = payload.get("network", "*")
        include_infrastructure = payload.get("include_infrastructure", True)
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting {route_type} routes from {pbf_path}")
        log.info(
            "%s extracting from %s (network=%s, infra=%s)",
            facet_name, pbf_path, network, include_infrastructure
        )

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result(route_type, network, include_infrastructure)}

        try:
            result = extract_routes(
                pbf_path,
                route_type=route_type,
                network=network,
                include_infrastructure=include_infrastructure,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} {route_type} routes", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract %s routes: %s", route_type, e)
            return {"result": _empty_result(route_type, network, include_infrastructure)}

    return handler


def _make_public_transport_handler(facet_name: str):
    """Create handler for PublicTransport event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        pbf_path = cache.get("path", "")
        step_log = payload.get("_step_log")

        if step_log:
            step_log(f"{facet_name}: extracting public transport from {pbf_path}")
        log.info("%s extracting public transport from %s", facet_name, pbf_path)

        if not HAS_OSMIUM or not pbf_path:
            return {"result": _empty_result("public_transport", "*", True)}

        try:
            result = extract_routes(
                pbf_path,
                route_type="public_transport",
                include_infrastructure=True,
            )
            if step_log:
                step_log(f"{facet_name}: extracted {result.feature_count} public transport routes", level="success")
            return {"result": _result_to_dict(result)}
        except Exception as e:
            log.error("Failed to extract public transport: %s", e)
            return {"result": _empty_result("public_transport", "*", True)}

    return handler


def _result_to_dict(result: RouteResult) -> dict:
    """Convert a RouteResult to a dictionary."""
    return {
        "output_path": result.output_path,
        "feature_count": result.feature_count,
        "route_type": result.route_type,
        "network_level": result.network_level,
        "include_infrastructure": result.include_infrastructure,
        "format": result.format,
        "extraction_date": result.extraction_date,
    }


def _stats_to_dict(stats: RouteStats) -> dict:
    """Convert RouteStats to a dictionary."""
    return {
        "route_count": stats.route_count,
        "total_length_km": stats.total_length_km,
        "infrastructure_count": stats.infrastructure_count,
        "route_type": stats.route_type,
    }


def _empty_result(route_type: str, network: str, include_infra: bool) -> dict:
    """Return an empty result dict."""
    return {
        "output_path": "",
        "feature_count": 0,
        "route_type": route_type,
        "network_level": network,
        "include_infrastructure": include_infra,
        "format": "GeoJSON",
        "extraction_date": datetime.now(timezone.utc).isoformat(),
    }


def _empty_stats() -> dict:
    """Return empty stats dict."""
    return {
        "route_count": 0,
        "total_length_km": 0.0,
        "infrastructure_count": 0,
        "route_type": "",
    }


# Event facet definitions for handler registration
ROUTE_FACETS = [
    # Generic extractors
    ("ExtractRoutes", _make_extract_routes_handler),
    ("FilterRoutesByType", _make_filter_routes_handler),
    ("RouteStatistics", _make_route_stats_handler),
    # Typed convenience facets
    ("BicycleRoutes", lambda name: _make_typed_route_handler(name, "bicycle")),
    ("HikingTrails", lambda name: _make_typed_route_handler(name, "hiking")),
    ("TrainRoutes", lambda name: _make_typed_route_handler(name, "train")),
    ("BusRoutes", lambda name: _make_typed_route_handler(name, "bus")),
    ("PublicTransport", _make_public_transport_handler),
]


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, callable] = {}


def _build_dispatch() -> None:
    for facet_name, handler_factory in ROUTE_FACETS:
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


def register_route_handlers(poller) -> None:
    """Register all route event facet handlers with the poller."""
    for facet_name, handler_factory in ROUTE_FACETS:
        qualified_name = f"{NAMESPACE}.{facet_name}"
        poller.register(qualified_name, handler_factory(facet_name))
        log.debug("Registered route handler: %s", qualified_name)
