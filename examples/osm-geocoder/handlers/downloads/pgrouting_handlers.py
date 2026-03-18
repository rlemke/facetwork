"""pgRouting topology event facet handlers.

Handles BuildRoutingTopology, ValidateTopology, and CleanTopology event
facets defined in osmpgrouting.afl under the osm.ops.PgRouting namespace.

Uses osm2pgrouting CLI to import OSM road network into pgRouting-ready
tables, and psycopg2 for topology validation and cleanup.
"""

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from .postgis_importer import HAS_PSYCOPG2, get_postgis_url, sanitize_url

log = logging.getLogger(__name__)

NAMESPACE = "osm.ops.PgRouting"

# osm2pgrouting binary location
OSM2PGROUTING_BIN = os.environ.get("OSM2PGROUTING_BIN", "osm2pgrouting")

# osm2pgrouting mapconfig files (ship with the osm2pgrouting package)
_MAPCONFIG_DIR = os.environ.get(
    "OSM2PGROUTING_MAPCONFIG_DIR", "/usr/share/osm2pgrouting"
)

PROFILE_CONFIGS = {
    "car": "mapconfig_for_cars.xml",
    "bike": "mapconfig_for_bicycles.xml",
    "foot": "mapconfig_for_pedestrian.xml",
}

# SQL constants
CREATE_PGROUTING_EXT = "CREATE EXTENSION IF NOT EXISTS pgrouting"

CREATE_TOPOLOGY_LOG = """
CREATE TABLE IF NOT EXISTS routing_topology_log (
    id SERIAL PRIMARY KEY,
    region TEXT NOT NULL,
    profile TEXT NOT NULL DEFAULT 'car',
    edge_count INT,
    node_count INT,
    built_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(region, profile)
)
"""

CHECK_PRIOR_TOPOLOGY = """
SELECT id, edge_count, node_count, built_at
FROM routing_topology_log
WHERE region = %s AND profile = %s
ORDER BY built_at DESC
LIMIT 1
"""

UPSERT_TOPOLOGY_LOG = """
INSERT INTO routing_topology_log (region, profile, edge_count, node_count)
VALUES (%s, %s, %s, %s)
ON CONFLICT (region, profile)
DO UPDATE SET edge_count = EXCLUDED.edge_count,
              node_count = EXCLUDED.node_count,
              built_at = NOW()
"""


def _prefix(region: str) -> str:
    """Normalize region name to a safe SQL table prefix."""
    return re.sub(r"[^a-z0-9]", "_", region.lower().strip())


def _parse_dsn(postgis_url: str) -> dict[str, str]:
    """Parse a PostgreSQL URL into osm2pgrouting CLI flags."""
    parsed = urlparse(postgis_url)
    return {
        "dbname": parsed.path.lstrip("/"),
        "user": parsed.username or "",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
    }


def _tables_exist(conn, prefix: str) -> bool:
    """Check whether the pgRouting tables for a region prefix exist."""
    table_name = f"{prefix}_ways"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table_name,),
        )
        return cur.fetchone() is not None


def _table_counts(conn, prefix: str) -> tuple[int, int]:
    """Return (edge_count, node_count) for a region's routing tables."""
    edge_count = 0
    node_count = 0
    with conn.cursor() as cur:
        try:
            cur.execute(f"SELECT count(*) FROM {prefix}_ways")  # noqa: S608
            edge_count = cur.fetchone()[0]
        except Exception:
            conn.rollback()
        try:
            cur.execute(f"SELECT count(*) FROM {prefix}_ways_vertices_pgr")  # noqa: S608
            node_count = cur.fetchone()[0]
        except Exception:
            conn.rollback()
    return edge_count, node_count


def _ensure_pgrouting(conn) -> None:
    """Create pgRouting extension and topology log table."""
    with conn.cursor() as cur:
        cur.execute(CREATE_PGROUTING_EXT)
        cur.execute(CREATE_TOPOLOGY_LOG)
    conn.commit()


def _run_osm2pgrouting(
    pbf_path: str,
    postgis_url: str,
    prefix: str,
    profile: str,
    clean: bool = False,
) -> bool:
    """Run osm2pgrouting CLI to import a road network.

    Returns True on success.
    """
    dsn = _parse_dsn(postgis_url)
    mapconfig = PROFILE_CONFIGS.get(profile, PROFILE_CONFIGS["car"])
    mapconfig_path = os.path.join(_MAPCONFIG_DIR, mapconfig)

    cmd = [
        OSM2PGROUTING_BIN,
        "--f", pbf_path,
        "--dbname", dsn["dbname"],
        "--username", dsn["user"],
        "--host", dsn["host"],
        "--port", dsn["port"],
        "--prefix", f"{prefix}_",
        "--conf", mapconfig_path,
    ]
    if dsn["password"]:
        cmd.extend(["--password", dsn["password"]])
    if clean:
        cmd.append("--clean")

    log.info("Running osm2pgrouting: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200,  # 2 hour timeout for large regions
        )
        if result.returncode != 0:
            log.error("osm2pgrouting failed (rc=%d): %s", result.returncode, result.stderr[:500])
            return False
        return True
    except FileNotFoundError:
        log.error("osm2pgrouting binary not found at %s", OSM2PGROUTING_BIN)
        return False
    except subprocess.TimeoutExpired:
        log.error("osm2pgrouting timed out for prefix=%s", prefix)
        return False


def _make_topology_result(
    postgis_url: str,
    region: str,
    profile: str,
    edge_count: int,
    node_count: int,
    was_cached: bool,
    date: str | None = None,
) -> dict:
    """Build a RoutingTopology-shaped result dict."""
    return {
        "postgisUrl": sanitize_url(postgis_url),
        "region": region,
        "profile": profile,
        "nodeCount": node_count,
        "edgeCount": edge_count,
        "date": date or datetime.now(UTC).isoformat(),
        "wasCached": was_cached,
    }


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------


def _build_topology_handler(payload: dict) -> dict:
    """Handle osm.ops.PgRouting.BuildRoutingTopology."""
    cache = payload.get("cache", {})
    pbf_path = cache.get("path", "")
    region = payload.get("region", "")
    profile = payload.get("profile", "car")
    recreate = payload.get("recreate", False)
    step_log = payload.get("_step_log")

    if step_log:
        step_log(f"BuildRoutingTopology: {region} ({profile} profile)")

    if not pbf_path:
        raise ValueError("No PBF path in cache")
    if not region:
        raise ValueError("Region is required")

    postgis_url = get_postgis_url()

    if not HAS_PSYCOPG2:
        log.warning("BuildRoutingTopology: psycopg2 not available")
        return {"topology": _make_topology_result(postgis_url, region, profile, 0, 0, False)}

    import psycopg2

    conn = psycopg2.connect(postgis_url)
    try:
        _ensure_pgrouting(conn)
        prefix = _prefix(region)

        # Check for prior topology
        if not recreate:
            with conn.cursor() as cur:
                cur.execute(CHECK_PRIOR_TOPOLOGY, (region, profile))
                row = cur.fetchone()
                if row is not None and _tables_exist(conn, prefix):
                    edge_count, node_count = row[1], row[2]
                    if step_log:
                        step_log(
                            f"BuildRoutingTopology: cached ({edge_count} edges, {node_count} nodes)",
                            level="success",
                        )
                    return {
                        "topology": _make_topology_result(
                            postgis_url, region, profile, edge_count, node_count,
                            True, str(row[3]),
                        )
                    }

        # Build topology via osm2pgrouting
        success = _run_osm2pgrouting(pbf_path, postgis_url, prefix, profile, clean=recreate)
        if not success:
            raise RuntimeError(f"osm2pgrouting failed for region={region}")

        edge_count, node_count = _table_counts(conn, prefix)

        # Record in log
        with conn.cursor() as cur:
            cur.execute(UPSERT_TOPOLOGY_LOG, (region, profile, edge_count, node_count))
        conn.commit()

        if step_log:
            step_log(
                f"BuildRoutingTopology: built ({edge_count} edges, {node_count} nodes)",
                level="success",
            )

        return {
            "topology": _make_topology_result(
                postgis_url, region, profile, edge_count, node_count, False,
            )
        }
    finally:
        conn.close()


def _validate_topology_handler(payload: dict) -> dict:
    """Handle osm.ops.PgRouting.ValidateTopology."""
    topology = payload.get("topology", {})
    region = topology.get("region", "")
    step_log = payload.get("_step_log")

    if step_log:
        step_log(f"ValidateTopology: checking {region}")

    if not region or not HAS_PSYCOPG2:
        return {"valid": False, "nodeCount": 0, "edgeCount": 0, "components": 0}

    import psycopg2

    postgis_url = get_postgis_url()
    conn = psycopg2.connect(postgis_url)
    prefix = _prefix(region)
    try:
        if not _tables_exist(conn, prefix):
            if step_log:
                step_log(f"ValidateTopology: no tables for {region}")
            return {"valid": False, "nodeCount": 0, "edgeCount": 0, "components": 0}

        edge_count, node_count = _table_counts(conn, prefix)

        # Count connected components
        components = 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(DISTINCT component) FROM pgr_connectedComponents("
                    f"'SELECT gid AS id, source, target, cost FROM {prefix}_ways')"
                )
                components = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            # pgr_connectedComponents may fail on empty graph
            components = 1 if edge_count > 0 else 0

        valid = edge_count > 0 and node_count > 0
        if step_log:
            step_log(
                f"ValidateTopology: valid={valid} ({edge_count} edges, "
                f"{node_count} nodes, {components} components)",
                level="success",
            )
        return {
            "valid": valid,
            "nodeCount": node_count,
            "edgeCount": edge_count,
            "components": components,
        }
    finally:
        conn.close()


def _clean_topology_handler(payload: dict) -> dict:
    """Handle osm.ops.PgRouting.CleanTopology."""
    region = payload.get("region", "")
    step_log = payload.get("_step_log")

    if step_log:
        step_log(f"CleanTopology: dropping tables for {region}")

    if not region or not HAS_PSYCOPG2:
        return {"deleted": False}

    import psycopg2

    postgis_url = get_postgis_url()
    conn = psycopg2.connect(postgis_url)
    prefix = _prefix(region)
    try:
        if not _tables_exist(conn, prefix):
            if step_log:
                step_log(f"CleanTopology: no tables for {region}")
            return {"deleted": False}

        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {prefix}_ways CASCADE")
            cur.execute(f"DROP TABLE IF EXISTS {prefix}_ways_vertices_pgr CASCADE")
            cur.execute(
                "DELETE FROM routing_topology_log WHERE region = %s",
                (region,),
            )
        conn.commit()

        if step_log:
            step_log(f"CleanTopology: dropped {prefix}_ways tables", level="success")
        return {"deleted": True}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, callable] = {
    f"{NAMESPACE}.BuildRoutingTopology": _build_topology_handler,
    f"{NAMESPACE}.ValidateTopology": _validate_topology_handler,
    f"{NAMESPACE}.CleanTopology": _clean_topology_handler,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_pgrouting_handlers(poller) -> None:
    """Register pgRouting event facet handlers with the poller."""
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
