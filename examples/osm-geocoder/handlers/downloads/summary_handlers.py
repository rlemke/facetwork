"""PostGIS database summary event facet handler.

Handles the AnalyzeDatabase event facet defined in osmsummary.afl
under the osm.ops.Summary namespace. Queries osm_nodes, osm_ways,
and osm_import_log to produce structured summaries.
"""

import json
import logging
import os
from datetime import UTC, datetime

from facetwork.config import get_output_base

from .postgis_importer import HAS_PSYCOPG2, get_postgis_url

log = logging.getLogger(__name__)

NAMESPACE = "osm.ops.Summary"

# SQL queries for analysis

REGION_SUMMARY_SQL = """
SELECT
    il.region,
    il.node_count,
    il.way_count,
    il.imported_at,
    COALESCE(poi.poi_count, 0) AS poi_count,
    COALESCE(named.named_count, 0) AS named_count
FROM osm_import_log il
LEFT JOIN LATERAL (
    SELECT count(*) AS poi_count
    FROM osm_nodes n
    WHERE n.region = il.region
      AND (n.tags ? 'amenity' OR n.tags ? 'tourism' OR n.tags ? 'shop' OR n.tags ? 'leisure')
) poi ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS named_count
    FROM osm_nodes n
    WHERE n.region = il.region
      AND n.tags ? 'name'
) named ON true
WHERE il.id IN (
    SELECT DISTINCT ON (region) id
    FROM osm_import_log
    ORDER BY region, imported_at DESC
)
ORDER BY il.region
"""

REGION_SUMMARY_FAST_SQL = """
SELECT
    il.region,
    il.node_count,
    il.way_count,
    il.imported_at,
    0 AS poi_count,
    0 AS named_count
FROM osm_import_log il
WHERE il.id IN (
    SELECT DISTINCT ON (region) id
    FROM osm_import_log
    ORDER BY region, imported_at DESC
)
ORDER BY il.region
"""

REGION_SUMMARY_COUNTRY_SQL = """
SELECT
    il.region,
    il.node_count,
    il.way_count,
    il.imported_at,
    0 AS poi_count,
    0 AS named_count
FROM osm_import_log il
WHERE il.region = %s
  AND il.id IN (
    SELECT DISTINCT ON (region) id
    FROM osm_import_log
    ORDER BY region, imported_at DESC
)
"""


def _analyze_database(payload: dict) -> dict:
    """Handle osm.ops.Summary.AnalyzeDatabase.

    Queries PostGIS for per-region summary statistics and writes
    a JSON file with the full breakdown.
    """
    country = payload.get("country", "")
    include_pois = payload.get("include_pois", True)
    step_log = payload.get("_step_log")

    if step_log:
        label = f"region={country}" if country else "all regions"
        step_log(f"AnalyzeDatabase: analyzing {label}")

    if not HAS_PSYCOPG2:
        log.warning("AnalyzeDatabase: psycopg2 not available")
        return {"summary": _empty_summary()}

    import psycopg2

    postgis_url = get_postgis_url()
    conn = psycopg2.connect(postgis_url, gssencmode="disable")
    try:
        with conn.cursor() as cur:
            if country:
                cur.execute(REGION_SUMMARY_COUNTRY_SQL, (country,))
            elif include_pois:
                cur.execute(REGION_SUMMARY_SQL)
            else:
                cur.execute(REGION_SUMMARY_FAST_SQL)

            rows = cur.fetchall()

        regions = []
        total_nodes = 0
        total_ways = 0
        total_pois = 0

        for row in rows:
            region_name, node_count, way_count, imported_at, poi_count, named_count = row
            total_nodes += node_count or 0
            total_ways += way_count or 0
            total_pois += poi_count or 0
            regions.append({
                "region": region_name,
                "node_count": node_count or 0,
                "way_count": way_count or 0,
                "poi_count": poi_count or 0,
                "named_count": named_count or 0,
                "imported_at": str(imported_at) if imported_at else "",
            })

        # Write JSON output
        output_dir = os.path.join(get_output_base(), "osm", "summary")
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        suffix = f"-{country}" if country else ""
        output_path = os.path.join(output_dir, f"db-summary{suffix}-{timestamp}.json")

        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "total_regions": len(regions),
            "total_nodes": total_nodes,
            "total_ways": total_ways,
            "total_pois": total_pois,
            "regions": regions,
        }

        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        if step_log:
            step_log(
                f"AnalyzeDatabase: {len(regions)} regions, "
                f"{total_nodes:,} nodes, {total_ways:,} ways, {total_pois:,} POIs",
                level="success",
            )

        return {
            "summary": {
                "total_regions": len(regions),
                "total_nodes": total_nodes,
                "total_ways": total_ways,
                "total_pois": total_pois,
                "output_path": output_path,
            }
        }
    finally:
        conn.close()


def _empty_summary() -> dict:
    return {
        "total_regions": 0,
        "total_nodes": 0,
        "total_ways": 0,
        "total_pois": 0,
        "output_path": "",
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, callable] = {
    f"{NAMESPACE}.AnalyzeDatabase": _analyze_database,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_summary_handlers(poller) -> None:
    """Register summary event facet handlers with the poller."""
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
