"""Operations event facet handlers for OSM data processing.

Handles Download, Tile, RoutingGraph, Status, Cache, and related operations
defined in osmoperations.afl under the osm.ops namespace.
"""

import logging
import os
import re

from ..cache.cache_handlers import REGION_REGISTRY

log = logging.getLogger(__name__)

NAMESPACE = "osm.ops"

# All event facets in osm.ops and their return parameter names.
# NOTE: Cache is handled separately (takes region:String, not cache:OSMCache).
OPERATIONS_FACETS: dict[str, str | None] = {
    "Tile": "tiles",
    "RoutingGraph": "graph",
    "Status": "stats",
    "GeoOSMCache": "graph",
    "DownloadBatch": None,  # => ()
    "TileBatch": "tiles",
    "RoutingGraphBatch": "graph",
    "StatusBatch": "stats",
    "GeoOSMCacheBatch": "graph",
    "DownloadShapefile": None,  # => ()
    "DownloadShapefileBatch": None,  # => ()
}

# Flat lookup: region name -> Geofabrik path (built from cache_handlers registry)
_REGION_LOOKUP: dict[str, str] = {}
for _ns, _facets in REGION_REGISTRY.items():
    for _name, _path in _facets.items():
        if _name not in _REGION_LOOKUP:
            _REGION_LOOKUP[_name] = _path

# Top-level Geofabrik roots — treated as direct paths, not facet-name lookups.
# Matches the 9 parentless entries in Geofabrik's index-v1.json.
_GEOFABRIK_ROOTS: frozenset[str] = frozenset({
    "africa",
    "antarctica",
    "asia",
    "australia-oceania",
    "central-america",
    "europe",
    "north-america",
    "russia",
    "south-america",
})

# US state abbreviation -> full name mapping for Cache handler convenience
_US_STATE_ABBREVS: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "NewHampshire",
    "NJ": "NewJersey",
    "NM": "NewMexico",
    "NY": "NewYork",
    "NC": "NorthCarolina",
    "ND": "NorthDakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "RhodeIsland",
    "SC": "SouthCarolina",
    "SD": "SouthDakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "WestVirginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "DistrictOfColumbia",
}

# Pattern to extract region path from a Geofabrik URL
_GEOFABRIK_REGION_RE = re.compile(r"https?://download\.geofabrik\.de/(.+)-latest\.[^/]+$")


def _extract_region_path(url: str) -> str:
    """Extract the region path from a Geofabrik download URL.

    E.g. "https://download.geofabrik.de/africa/algeria-latest.osm.pbf"
    returns "africa/algeria".
    """
    m = _GEOFABRIK_REGION_RE.match(url)
    if m:
        return m.group(1)
    return url


def _make_operation_handler(facet_name: str, return_param: str | None):
    """Create a handler for an operations event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        step_log = payload.get("_step_log")
        if step_log:
            step_log(f"{facet_name}: processing {cache.get('url', 'unknown')}")
        log.info("%s processing cache: %s", facet_name, cache.get("url", "unknown"))

        if return_param is None:
            if step_log:
                step_log(f"{facet_name}: completed", level="success")
            return {}

        if step_log:
            step_log(f"{facet_name}: processed (size={cache.get('size', 0)})", level="success")
        return {
            return_param: {
                "url": cache.get("url", ""),
                "path": cache.get("path", ""),
                "date": cache.get("date", ""),
                "size": cache.get("size", 0),
                "wasInCache": True,
            }
        }

    return handler


def _make_shapefile_handler(facet_name: str):
    """Create a handler for a shapefile download event facet."""

    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        url = cache.get("url", "")
        region_path = _extract_region_path(url)
        step_log = payload.get("_step_log")
        log.info("%s downloading shapefile for: %s", facet_name, region_path)
        from ..shared.downloader import download

        result = download(region_path, fmt="shp")
        source = result.get("source", "unknown")
        if step_log:
            step_log(f"{facet_name}: shapefile for {region_path} (source={source})")
        return result

    return handler


def _download_handler(payload: dict) -> dict:
    """Handle the Download event facet: download any URL to any file path.

    Takes url:String, path:String, force:Boolean and downloads the content.
    The path may be a local filesystem path or an hdfs:// URI.
    Returns downloadCache:OSMCache.
    """
    from ..shared.downloader import download_url

    url = payload.get("url", "")
    path = payload.get("path", "")
    force = payload.get("force", False)
    step_log = payload.get("_step_log")
    if step_log:
        step_log(f"Download: {url} -> {path}")

    log.info("Download: %s -> %s (force=%s)", url, path, force)
    result = download_url(url, path, force=force)
    if step_log:
        step_log(f"Download: complete (size={result.get('size', 0)})", level="success")
    return {"downloadCache": result}


def _cache_handler(payload: dict) -> dict:
    """Handle the Cache event facet: resolve a region and download the PBF.

    Accepts two input formats for ``region``:
    - Geofabrik paths (e.g. ``"europe/germany/berlin"``, ``"africa"``) — used
      directly without lookup. Detected by presence of ``"/"`` or match against
      a known top-level Geofabrik root.
    - PascalCase facet names (e.g. ``"Algeria"``, ``"Germany"``) — looked up
      via the region registry for backward compat. Also supports US state
      abbreviations (``"CA"`` -> ``"California"``).

    Delegates to the shared PBF cache library (``shared.pbf_cache``), so
    the FFL handler and the ``download-pbf`` CLI tool read and write the
    same on-disk cache and the same manifest.

    Returns cache:OSMCache.
    """
    from ..shared.pbf_cache import download_region, to_osm_cache

    region = payload.get("region", "")
    step_log = payload.get("_step_log")

    # Geofabrik paths have slashes; top-level continent roots are bare.
    if "/" in region or region in _GEOFABRIK_ROOTS:
        region_path = region
    else:
        # Try exact match first
        region_path = _REGION_LOOKUP.get(region)

        # Try US state abbreviation (e.g. "CA" -> "California")
        if not region_path:
            full_name = _US_STATE_ABBREVS.get(region.upper())
            if full_name:
                region_path = _REGION_LOOKUP.get(full_name)

        # Fall back to case-insensitive search
        if not region_path:
            region_lower = region.lower()
            for name, path in _REGION_LOOKUP.items():
                if name.lower() == region_lower:
                    region_path = path
                    break

        if not region_path:
            log.warning("Cache: unknown region '%s', using as raw path", region)
            region_path = region.lower()

    log.info("Cache: resolving region '%s' -> '%s'", region, region_path)
    result = download_region(region_path)
    cache = to_osm_cache(result)
    if step_log:
        step_log(
            f"Cache: region '{region}' -> '{region_path}' "
            f"(source={cache['source']}, size={cache['size']})",
            level="success",
        )
    return {"cache": cache}


def register_operations_handlers(poller) -> None:
    """Register all operations event facet handlers with the poller."""
    # Register the Cache handler (takes region:String, not cache:OSMCache)
    poller.register(f"{NAMESPACE}.CacheRegion", _cache_handler)
    # Register the Download handler (takes url:String, path:String, force:Boolean)
    poller.register(f"{NAMESPACE}.DownloadPBF", _download_handler)

    shapefile_facets = {"DownloadShapefile", "DownloadShapefileBatch"}
    for facet_name, return_param in OPERATIONS_FACETS.items():
        qualified_name = f"{NAMESPACE}.{facet_name}"
        if facet_name in shapefile_facets:
            poller.register(qualified_name, _make_shapefile_handler(facet_name))
        else:
            poller.register(qualified_name, _make_operation_handler(facet_name, return_param))


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, callable] = {}


def _build_dispatch() -> None:
    _DISPATCH[f"{NAMESPACE}.CacheRegion"] = _cache_handler
    _DISPATCH[f"{NAMESPACE}.DownloadPBF"] = _download_handler
    shapefile_facets = {"DownloadShapefile", "DownloadShapefileBatch"}
    for facet_name, return_param in OPERATIONS_FACETS.items():
        qualified_name = f"{NAMESPACE}.{facet_name}"
        if facet_name in shapefile_facets:
            _DISPATCH[qualified_name] = _make_shapefile_handler(facet_name)
        else:
            _DISPATCH[qualified_name] = _make_operation_handler(facet_name, return_param)


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
