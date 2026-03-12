"""PostGIS import event facet handler for OSM data.

Handles the PostGisImport event facet defined in osmoperations.afl
under the osm.ops namespace.
"""

import logging
import os

from .postgis_importer import HAS_OSMIUM, HAS_PSYCOPG2

log = logging.getLogger(__name__)

NAMESPACE = "osm.ops"


def _postgis_import_handler(payload: dict) -> dict:
    """Handle the PostGisImport event facet.

    Extracts cache metadata from the payload, imports the PBF file
    into PostGIS, and returns stats in OSMCache-shaped format.
    """
    cache = payload.get("cache", {})
    pbf_path = cache.get("path", "")
    source_url = cache.get("url", "")
    step_log = payload.get("_step_log")

    if step_log:
        step_log(f"PostGisImport: processing cache {source_url or pbf_path}")
    log.info("PostGisImport processing cache: %s", source_url or pbf_path)

    if not HAS_OSMIUM or not HAS_PSYCOPG2 or not pbf_path:
        log.warning(
            "PostGisImport: skipping import (osmium=%s, psycopg2=%s, path=%s)",
            HAS_OSMIUM,
            HAS_PSYCOPG2,
            bool(pbf_path),
        )
        return {
            "stats": {
                "url": source_url,
                "path": "",
                "date": cache.get("date", ""),
                "size": 0,
                "wasInCache": False,
            }
        }

    try:
        from .postgis_importer import import_to_postgis

        result = import_to_postgis(pbf_path, source_url=source_url)
        if step_log:
            step_log(
                f"PostGisImport: imported {result.node_count + result.way_count} elements (nodes={result.node_count}, ways={result.way_count})",
                level="success",
            )
        return {
            "stats": {
                "url": source_url,
                "path": result.postgis_url,
                "date": result.imported_at,
                "size": result.node_count + result.way_count,
                "wasInCache": result.was_prior_import,
            }
        }
    except Exception:
        log.exception("PostGisImport failed for %s", pbf_path)
        return {
            "stats": {
                "url": source_url,
                "path": "",
                "date": cache.get("date", ""),
                "size": 0,
                "wasInCache": False,
            }
        }


_DISPATCH: dict[str, callable] = {
    f"{NAMESPACE}.PostGisImport": _postgis_import_handler,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_postgis_handlers(poller) -> None:
    """Register PostGIS event facet handlers with the poller."""
    if not HAS_OSMIUM:
        return
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)


def register_handlers(runner) -> None:
    """Register all facets with a RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )
