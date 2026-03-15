"""Pairwise routing event facet handlers.

Handles the ComputePairwiseRoutes event facet defined in osmcityrouting.afl
under osm.Routing namespace. Computes all-pairs shortest-path driving
routes between cities using a pre-built GraphHopper routing graph.
"""

import json
import logging
import os
from itertools import combinations
from typing import Any

from afl.config import get_output_base

log = logging.getLogger(__name__)

NAMESPACE = "osm.Routing"

# GraphHopper API endpoint (local instance)
GRAPHHOPPER_API_URL = os.environ.get("GRAPHHOPPER_API_URL", "http://localhost:8989")


def _load_cities_geojson(cities_path: str) -> list[dict[str, Any]]:
    """Load city features from a GeoJSON file.

    Returns a list of feature dicts with name, population, and coordinates.
    """
    with open(cities_path) as f:
        data = json.load(f)

    cities = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry", {})
        coords = geom.get("coordinates", [0, 0])

        name = props.get("name", "Unknown")
        population = props.get("population", 0)
        if isinstance(population, str):
            try:
                population = int(population)
            except ValueError:
                population = 0

        cities.append(
            {
                "name": name,
                "population": population,
                "lon": coords[0],
                "lat": coords[1],
            }
        )

    return cities


def _query_graphhopper_route(
    from_city: dict,
    to_city: dict,
    graph_dir: str,
    profile: str = "car",
) -> dict[str, Any] | None:
    """Query GraphHopper for a route between two cities.

    Tries the GraphHopper HTTP API first (for running instances),
    falls back to a simple great-circle estimate if unavailable.

    Returns dict with distance_km, duration_min, and geometry coordinates.
    """
    try:
        import requests

        url = f"{GRAPHHOPPER_API_URL}/route"
        params = {
            "point": [
                f"{from_city['lat']},{from_city['lon']}",
                f"{to_city['lat']},{to_city['lon']}",
            ],
            "profile": profile,
            "type": "json",
            "points_encoded": "false",
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            path = data.get("paths", [{}])[0]
            coords = path.get("points", {}).get("coordinates", [])
            return {
                "distance_km": round(path.get("distance", 0) / 1000, 1),
                "duration_min": round(path.get("time", 0) / 60000, 1),
                "coordinates": coords,
            }
    except Exception:
        pass

    # Fallback: great-circle distance estimate (no actual routing)
    return _estimate_route(from_city, to_city)


def _estimate_route(from_city: dict, to_city: dict) -> dict[str, Any]:
    """Estimate route distance and duration using great-circle distance."""
    import math

    lat1 = math.radians(from_city["lat"])
    lat2 = math.radians(to_city["lat"])
    dlat = lat2 - lat1
    dlon = math.radians(to_city["lon"] - from_city["lon"])

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance_km = 6371 * c

    # Estimate driving time at 80 km/h average with 1.3 detour factor
    road_distance = distance_km * 1.3
    duration_min = road_distance / 80 * 60

    return {
        "distance_km": round(road_distance, 1),
        "duration_min": round(duration_min, 1),
        "coordinates": [
            [from_city["lon"], from_city["lat"]],
            [to_city["lon"], to_city["lat"]],
        ],
    }


def compute_pairwise_routes(payload: dict) -> dict:
    """Compute all-pairs driving routes between cities in a GeoJSON file.

    Reads cities from the GeoJSON, iterates all unique pairs, queries
    GraphHopper for each route, and writes a GeoJSON with LineString
    geometries.

    Params:
        cities_path: Path to GeoJSON file with city features
        graph: GraphHopperCache dict with graphDir and profile

    Returns:
        result: PairwiseRoutingResult dict
    """
    cities_path = payload.get("cities_path", "")
    graph = payload.get("graph", {})
    graph_dir = graph.get("graphDir", "")
    profile = graph.get("profile", "car")
    step_log = payload.get("_step_log")

    if step_log:
        step_log(f"ComputePairwiseRoutes: computing routes from {cities_path} ({profile} profile)")
    if not cities_path or not os.path.exists(cities_path):
        return {"result": _empty_result(profile)}

    cities = _load_cities_geojson(cities_path)
    if len(cities) < 2:
        return {"result": _empty_result(profile)}

    # Compute all-pairs routes
    features = []
    total_distance = 0.0
    total_duration = 0.0

    for city_a, city_b in combinations(cities, 2):
        route = _query_graphhopper_route(city_a, city_b, graph_dir, profile)
        if route is None:
            continue

        total_distance += route["distance_km"]
        total_duration += route["duration_min"]

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": route["coordinates"],
                },
                "properties": {
                    "from": city_a["name"],
                    "to": city_b["name"],
                    "distance_km": route["distance_km"],
                    "duration_min": route["duration_min"],
                    "profile": profile,
                },
            }
        )

    # Write output GeoJSON
    output_dir = os.path.join(get_output_base(), "osm", "routing")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(
        output_dir,
        f"pairwise-routes-{len(cities)}cities-{profile}.geojson",
    )

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    with open(output_path, "w") as f:
        json.dump(geojson, f)

    route_count = len(features)
    if step_log:
        step_log(
            f"ComputePairwiseRoutes: {route_count} routes between {len(cities)} cities ({round(total_distance)} km total)",
            level="success",
        )
    return {
        "result": {
            "output_path": output_path,
            "route_count": route_count,
            "city_count": len(cities),
            "total_distance_km": round(total_distance),
            "total_duration_min": round(total_duration),
            "avg_distance_km": round(total_distance / route_count) if route_count else 0,
            "avg_duration_min": round(total_duration / route_count) if route_count else 0,
            "profile": profile,
            "format": "GeoJSON",
        },
    }


def _empty_result(profile: str) -> dict:
    """Return an empty PairwiseRoutingResult."""
    return {
        "output_path": "",
        "route_count": 0,
        "city_count": 0,
        "total_distance_km": 0,
        "total_duration_min": 0,
        "avg_distance_km": 0,
        "avg_duration_min": 0,
        "profile": profile,
        "format": "GeoJSON",
    }


# RegistryRunner dispatch adapter
_DISPATCH = {
    f"{NAMESPACE}.ComputePairwiseRoutes": compute_pairwise_routes,
}


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


def register_routing_handlers(poller) -> None:
    """Register pairwise routing handlers with the poller."""
    poller.register(
        f"{NAMESPACE}.ComputePairwiseRoutes",
        compute_pairwise_routes,
    )
    log.debug("Registered routing handler: %s.ComputePairwiseRoutes", NAMESPACE)
