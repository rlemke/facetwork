"""OSM route extraction for various transport modes.

Extracts routes and related infrastructure from OSM PBF files for:
- bicycle: Cycle routes, cycleways, bike infrastructure
- hiking: Hiking/walking trails, footpaths
- train: Railway lines, stations
- bus: Bus routes, stops
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from afl.runtime.storage import get_storage_backend

from ..shared._output import open_output, uri_stem

_storage = get_storage_backend()

log = logging.getLogger(__name__)

import importlib.util

HAS_OSMIUM = importlib.util.find_spec("osmium") is not None


class RouteType(Enum):
    """Supported route types."""

    BICYCLE = "bicycle"
    HIKING = "hiking"
    TRAIN = "train"
    BUS = "bus"
    PUBLIC_TRANSPORT = "public_transport"

    @classmethod
    def from_string(cls, value: str) -> "RouteType":
        """Parse a route type string (case-insensitive)."""
        normalized = value.lower().strip()
        aliases = {
            "bicycle": cls.BICYCLE,
            "bike": cls.BICYCLE,
            "cycling": cls.BICYCLE,
            "cycle": cls.BICYCLE,
            "hiking": cls.HIKING,
            "hike": cls.HIKING,
            "walking": cls.HIKING,
            "foot": cls.HIKING,
            "trail": cls.HIKING,
            "train": cls.TRAIN,
            "rail": cls.TRAIN,
            "railway": cls.TRAIN,
            "bus": cls.BUS,
            "public_transport": cls.PUBLIC_TRANSPORT,
            "transit": cls.PUBLIC_TRANSPORT,
            "pt": cls.PUBLIC_TRANSPORT,
        }
        if normalized in aliases:
            return aliases[normalized]
        raise ValueError(f"Unknown route type: {value}")


# OSM tag configurations for each route type
ROUTE_TAGS = {
    RouteType.BICYCLE: {
        "routes": {
            "route": ["bicycle", "mtb"],
        },
        "ways": {
            "highway": ["cycleway"],
            "cycleway": [
                "lane",
                "track",
                "opposite",
                "opposite_lane",
                "opposite_track",
                "shared_lane",
            ],
            "bicycle": ["designated", "yes"],
        },
        "infrastructure": {
            "amenity": ["bicycle_parking", "bicycle_rental", "bicycle_repair_station"],
            "shop": ["bicycle"],
        },
        "network_key": "network",
        "network_values": ["icn", "ncn", "rcn", "lcn"],  # International/National/Regional/Local
    },
    RouteType.HIKING: {
        "routes": {
            "route": ["hiking", "foot", "walking"],
        },
        "ways": {
            "highway": ["path", "footway", "pedestrian", "track"],
            "foot": ["designated", "yes"],
            "sac_scale": [
                "hiking",
                "mountain_hiking",
                "demanding_mountain_hiking",
                "alpine_hiking",
                "demanding_alpine_hiking",
                "difficult_alpine_hiking",
            ],
        },
        "infrastructure": {
            "amenity": ["shelter", "drinking_water"],
            "tourism": ["alpine_hut", "wilderness_hut", "viewpoint", "picnic_site", "camp_site"],
            "information": ["guidepost", "map", "board"],
        },
        "network_key": "network",
        "network_values": [
            "iwn",
            "nwn",
            "rwn",
            "lwn",
        ],  # International/National/Regional/Local Walking
    },
    RouteType.TRAIN: {
        "routes": {
            "route": ["train", "railway", "light_rail", "subway", "tram"],
        },
        "ways": {
            "railway": ["rail", "light_rail", "subway", "tram", "narrow_gauge"],
        },
        "infrastructure": {
            "railway": ["station", "halt", "tram_stop", "subway_entrance"],
            "public_transport": ["station", "stop_position", "platform"],
        },
        "network_key": "network",
        "network_values": [],
    },
    RouteType.BUS: {
        "routes": {
            "route": ["bus", "trolleybus"],
        },
        "ways": {
            "highway": ["bus_guideway"],
            "bus": ["designated"],
        },
        "infrastructure": {
            "amenity": ["bus_station"],
            "highway": ["bus_stop"],
            "public_transport": ["stop_position", "platform", "station"],
        },
        "network_key": "network",
        "network_values": [],
    },
    RouteType.PUBLIC_TRANSPORT: {
        "routes": {
            "route": [
                "train",
                "railway",
                "light_rail",
                "subway",
                "tram",
                "bus",
                "trolleybus",
                "ferry",
            ],
        },
        "ways": {
            "railway": ["rail", "light_rail", "subway", "tram"],
            "highway": ["bus_guideway"],
        },
        "infrastructure": {
            "railway": ["station", "halt", "tram_stop"],
            "amenity": ["bus_station", "ferry_terminal"],
            "highway": ["bus_stop"],
            "public_transport": ["station", "stop_position", "platform"],
        },
        "network_key": "network",
        "network_values": [],
    },
}


@dataclass
class RouteFeatures:
    """Result of a route extraction operation."""

    output_path: str
    feature_count: int
    route_type: str
    network_level: str
    include_infrastructure: bool
    format: str = "GeoJSON"
    extraction_date: str = ""


@dataclass
class RouteStats:
    """Statistics for extracted routes."""

    route_count: int
    total_length_km: float
    infrastructure_count: int
    route_type: str


def filter_routes_by_type(
    input_path: str | Path,
    route_type: str | RouteType,
    network: str = "*",
    output_path: str | Path | None = None,
) -> RouteFeatures:
    """Filter already-extracted GeoJSON by route type.

    Args:
        input_path: Path to input GeoJSON file
        route_type: Type of route to filter for
        network: Network level filter
        output_path: Path to output GeoJSON file

    Returns:
        RouteFeatures with output path and statistics
    """
    input_path = str(input_path)
    if output_path is None:
        suffix = f"_{route_type}" if isinstance(route_type, str) else f"_{route_type.value}"
        stem = uri_stem(input_path)
        import posixpath

        _dir = posixpath.dirname(input_path)
        output_path = f"{_dir}/{stem}{suffix}.geojson"
    output_path = str(output_path)

    # Parse route type
    if isinstance(route_type, str):
        route_type = RouteType.from_string(route_type)

    # Load input
    with get_storage_backend(input_path).open(input_path, "r") as f:
        geojson = json.load(f)

    # Filter features
    network_filter = network if network != "*" else None
    filtered = []
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})

        # Check route type
        if props.get("route_type") != route_type.value:
            # Also check OSM route tag
            route_tag = props.get("route", "")
            config = ROUTE_TAGS.get(route_type, {})
            route_values = config.get("routes", {}).get("route", [])
            if route_tag not in route_values:
                continue

        # Check network
        if network_filter:
            if props.get("network", "").lower() != network_filter.lower():
                continue

        filtered.append(feature)

    # Build output
    output_geojson = {
        "type": "FeatureCollection",
        "features": filtered,
    }

    with open_output(str(output_path)) as f:
        json.dump(output_geojson, f, indent=2)

    return RouteFeatures(
        output_path=str(output_path),
        feature_count=len(filtered),
        route_type=route_type.value,
        network_level=network,
        include_infrastructure=False,
        extraction_date=datetime.now(UTC).isoformat(),
    )


def calculate_route_stats(input_path: str | Path) -> RouteStats:
    """Calculate statistics for extracted routes.

    Args:
        input_path: Path to GeoJSON file with routes

    Returns:
        RouteStats with counts and total length
    """
    input_path = str(input_path)
    with get_storage_backend(input_path).open(input_path, "r") as f:
        geojson = json.load(f)

    features = geojson.get("features", [])
    route_count = 0
    infra_count = 0
    total_length = 0.0
    route_type = ""

    for feature in features:
        props = feature.get("properties", {})
        feature_type = props.get("feature_type", "")

        if not route_type:
            route_type = props.get("route_type", "")

        if feature_type == "route":
            route_count += 1
        elif feature_type == "infrastructure":
            infra_count += 1
        elif feature_type == "way":
            # Calculate length for LineString geometries
            geometry = feature.get("geometry")
            if geometry and geometry.get("type") == "LineString":
                coords = geometry.get("coordinates", [])
                for i in range(len(coords) - 1):
                    total_length += _haversine_distance(
                        coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0]
                    )

    return RouteStats(
        route_count=route_count,
        total_length_km=total_length,
        infrastructure_count=infra_count,
        route_type=route_type,
    )


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers using Haversine formula."""
    import math

    R = 6371  # Earth's radius in km

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c
