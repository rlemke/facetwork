"""PostGIS import engine for OSM PBF files.

Parses PBF files via pyosmium and imports nodes/ways into PostGIS
via psycopg2. Supports batched inserts, upsert semantics, and
per-region partitioned imports with skip-if-imported logic.

Performance optimizations:
- Single PBF pass for both nodes and ways (pyosmium NodeLocationsForWays)
- Staging tables: imports write to unlogged, index-free staging tables
  then merge into main tables in a single bulk operation. This eliminates
  index maintenance, WAL overhead, and cross-import lock contention.
- Large batches (50k rows) with infrequent commits
- synchronous_commit=off during import to reduce WAL pressure
- DELETE+INSERT instead of UPSERT for force-reimport (avoids index lookups)
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from facetwork.runtime.storage import localize

from ..shared.scan_progress import ScanProgressTracker, _fmt_elapsed, get_file_size

log = logging.getLogger(__name__)

try:
    import osmium

    HAS_OSMIUM = True
except ImportError:
    HAS_OSMIUM = False
    osmium = None

try:
    import psycopg2
    import psycopg2.extras

    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    psycopg2 = None

DEFAULT_POSTGIS_URL = "postgresql://afl_osm:afl_osm_2024@afl-postgres:5432/osm"
DEFAULT_BATCH_SIZE = 50000

# DDL statements
CREATE_POSTGIS_EXT = "CREATE EXTENSION IF NOT EXISTS postgis"
CREATE_HSTORE_EXT = "CREATE EXTENSION IF NOT EXISTS hstore"

CREATE_NODES_TABLE = """
CREATE TABLE IF NOT EXISTS osm_nodes (
    osm_id BIGINT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    tags JSONB,
    geom geometry(Point, 4326),
    PRIMARY KEY (osm_id, region)
)
"""

CREATE_WAYS_TABLE = """
CREATE TABLE IF NOT EXISTS osm_ways (
    osm_id BIGINT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    tags JSONB,
    geom geometry(LineString, 4326),
    PRIMARY KEY (osm_id, region)
)
"""

CREATE_IMPORT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS osm_import_log (
    id SERIAL PRIMARY KEY,
    url TEXT,
    path TEXT,
    region TEXT NOT NULL DEFAULT '',
    node_count INT,
    way_count INT,
    imported_at TIMESTAMPTZ DEFAULT NOW()
)
"""

CREATE_NODES_GEOM_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_osm_nodes_geom ON osm_nodes USING GIST (geom)"
)
CREATE_NODES_TAGS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_osm_nodes_tags ON osm_nodes USING GIN (tags)"
)
CREATE_NODES_REGION_IDX = "CREATE INDEX IF NOT EXISTS idx_osm_nodes_region ON osm_nodes (region)"
CREATE_WAYS_GEOM_IDX = "CREATE INDEX IF NOT EXISTS idx_osm_ways_geom ON osm_ways USING GIST (geom)"
CREATE_WAYS_TAGS_IDX = "CREATE INDEX IF NOT EXISTS idx_osm_ways_tags ON osm_ways USING GIN (tags)"
CREATE_WAYS_REGION_IDX = "CREATE INDEX IF NOT EXISTS idx_osm_ways_region ON osm_ways (region)"

UPSERT_NODES_SQL = """
INSERT INTO osm_nodes (osm_id, region, tags, geom)
VALUES %s
ON CONFLICT (osm_id, region) DO UPDATE SET tags = EXCLUDED.tags, geom = EXCLUDED.geom
"""

INSERT_NODES_SQL = """
INSERT INTO osm_nodes (osm_id, region, tags, geom)
VALUES %s
"""

UPSERT_WAYS_SQL = """
INSERT INTO osm_ways (osm_id, region, tags, geom)
VALUES %s
ON CONFLICT (osm_id, region) DO UPDATE SET tags = EXCLUDED.tags, geom = EXCLUDED.geom
"""

INSERT_WAYS_SQL = """
INSERT INTO osm_ways (osm_id, region, tags, geom)
VALUES %s
"""

INSERT_LOG_SQL = """
INSERT INTO osm_import_log (url, path, region, node_count, way_count)
VALUES (%s, %s, %s, %s, %s)
"""

CHECK_PRIOR_IMPORT_SQL = """
SELECT id, node_count, way_count, imported_at
FROM osm_import_log
WHERE region = %s
ORDER BY imported_at DESC
LIMIT 1
"""

DELETE_REGION_NODES_SQL = "DELETE FROM osm_nodes WHERE region = %s"
DELETE_REGION_WAYS_SQL = "DELETE FROM osm_ways WHERE region = %s"

# ---------------------------------------------------------------------------
# Staging table helpers — import into unlogged, index-free tables then merge
# ---------------------------------------------------------------------------


def _staging_table_name(base: str, region: str) -> str:
    """Generate a safe staging table name from region."""
    # Sanitize region: keep alphanumeric and underscores only
    safe = re.sub(r"[^a-zA-Z0-9]", "_", region or "global").lower().strip("_")[:40]
    # Add PID to avoid collisions if two imports run for the same region
    return f"_staging_{base}_{safe}_{os.getpid()}"


def _create_staging_tables(conn, region: str, step_log=None) -> tuple[str, str]:
    """Create unlogged staging tables for nodes and ways. Returns (nodes_table, ways_table)."""
    nodes_tbl = _staging_table_name("nodes", region)
    ways_tbl = _staging_table_name("ways", region)
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {nodes_tbl}")
        cur.execute(f"DROP TABLE IF EXISTS {ways_tbl}")
        cur.execute(f"""
            CREATE UNLOGGED TABLE {nodes_tbl} (
                osm_id BIGINT NOT NULL,
                region TEXT NOT NULL DEFAULT '',
                tags JSONB,
                geom geometry(Point, 4326)
            )
        """)
        cur.execute(f"""
            CREATE UNLOGGED TABLE {ways_tbl} (
                osm_id BIGINT NOT NULL,
                region TEXT NOT NULL DEFAULT '',
                tags JSONB,
                geom geometry(LineString, 4326)
            )
        """)
    conn.commit()
    if step_log:
        step_log(f"Created staging tables: {nodes_tbl}, {ways_tbl}")
    return nodes_tbl, ways_tbl


_MERGE_BATCH_SIZE = 200_000  # rows per merge batch — balances speed vs heartbeat freshness


def _merge_staging_tables(
    conn, nodes_tbl: str, ways_tbl: str, use_upsert: bool,
    step_log=None, task_heartbeat=None,
) -> tuple[int, int]:
    """Merge staging tables into main tables in batches and drop them.

    Batched merge ensures heartbeats fire between batches so the stuck
    task reaper doesn't kill long-running imports.

    Returns (node_count, way_count) merged.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {nodes_tbl}")
        node_count = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {ways_tbl}")
        way_count = cur.fetchone()[0]

    if step_log:
        step_log(f"Merging {node_count:,} nodes + {way_count:,} ways from staging tables...")

    merge_t0 = time.monotonic()

    node_merged = _batched_merge(
        conn, nodes_tbl, "osm_nodes", use_upsert,
        label="nodes", total=node_count,
        step_log=step_log, task_heartbeat=task_heartbeat,
    )
    way_merged = _batched_merge(
        conn, ways_tbl, "osm_ways", use_upsert,
        label="ways", total=way_count,
        step_log=step_log, task_heartbeat=task_heartbeat,
    )

    # Drop staging tables
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {nodes_tbl}")
        cur.execute(f"DROP TABLE IF EXISTS {ways_tbl}")
    conn.commit()

    merge_elapsed = time.monotonic() - merge_t0
    if step_log:
        step_log(
            f"Merge complete: {node_merged:,} nodes + {way_merged:,} ways "
            f"in {_fmt_elapsed(merge_elapsed)}"
        )
    return node_merged, way_merged


def _batched_merge(
    conn, staging_tbl: str, target_tbl: str, use_upsert: bool,
    label: str = "", total: int = 0,
    step_log=None, task_heartbeat=None,
    batch_size: int = _MERGE_BATCH_SIZE,
) -> int:
    """Merge staging table into target in batches with heartbeats.

    Uses a serial ``_row_id`` column added to the staging table to
    iterate in fixed-size windows, avoiding full-table locks and
    allowing heartbeats between batches.
    """
    # Add a serial column for batching — fast on unlogged tables
    with conn.cursor() as cur:
        cur.execute(f"ALTER TABLE {staging_tbl} ADD COLUMN _row_id SERIAL")
    conn.commit()

    merged = 0
    offset = 0
    batch_t0 = time.monotonic()

    while True:
        if use_upsert:
            sql = f"""
                INSERT INTO {target_tbl} (osm_id, region, tags, geom)
                SELECT osm_id, region, tags, geom
                FROM {staging_tbl}
                WHERE _row_id > %s AND _row_id <= %s
                ON CONFLICT (osm_id, region)
                DO UPDATE SET tags = EXCLUDED.tags, geom = EXCLUDED.geom
            """
        else:
            sql = f"""
                INSERT INTO {target_tbl} (osm_id, region, tags, geom)
                SELECT osm_id, region, tags, geom
                FROM {staging_tbl}
                WHERE _row_id > %s AND _row_id <= %s
            """

        with conn.cursor() as cur:
            cur.execute(sql, (offset, offset + batch_size))
            rows = cur.rowcount
        conn.commit()

        if rows == 0:
            break

        merged += rows
        offset += batch_size

        # Heartbeat + progress after each batch
        elapsed = time.monotonic() - batch_t0
        rate = merged / elapsed if elapsed > 0 else 0
        if task_heartbeat:
            try:
                task_heartbeat(
                    progress_message=(
                        f"Merging {label}: {merged:,}/{total:,} "
                        f"({_fmt_elapsed(elapsed)}, {rate:,.0f}/s)"
                    ),
                )
            except Exception:
                pass
        if step_log and merged % (batch_size * 5) == 0:
            step_log(
                f"Merge {label}: {merged:,}/{total:,} "
                f"({_fmt_elapsed(elapsed)}, {rate:,.0f}/s)"
            )

    return merged

# ---------------------------------------------------------------------------
# osm2pgsql-compatible views — zero-storage compatibility layer
# ---------------------------------------------------------------------------
# These views expose the JSONB tags as named columns so tools expecting the
# osm2pgsql schema (planet_osm_point, planet_osm_line, planet_osm_polygon,
# planet_osm_roads) can query the data without a separate import.

CREATE_PLANET_OSM_POINT_VIEW = """
CREATE OR REPLACE VIEW planet_osm_point AS
SELECT osm_id,
       tags->>'name' AS name,
       tags->>'amenity' AS amenity,
       tags->>'shop' AS shop,
       tags->>'highway' AS highway,
       tags->>'building' AS building,
       tags->>'tourism' AS tourism,
       tags->>'natural' AS "natural",
       tags->>'leisure' AS leisure,
       tags->>'landuse' AS landuse,
       tags->>'place' AS place,
       tags->>'railway' AS railway,
       tags->>'aeroway' AS aeroway,
       tags->>'man_made' AS man_made,
       tags->>'cuisine' AS cuisine,
       tags->>'religion' AS religion,
       tags->>'sport' AS sport,
       tags->>'population' AS population,
       tags->>'addr:street' AS "addr:street",
       tags->>'addr:housenumber' AS "addr:housenumber",
       tags->>'addr:city' AS "addr:city",
       tags->>'addr:postcode' AS "addr:postcode",
       tags,
       region,
       geom AS way
FROM osm_nodes
"""

CREATE_PLANET_OSM_LINE_VIEW = """
CREATE OR REPLACE VIEW planet_osm_line AS
SELECT osm_id,
       tags->>'name' AS name,
       tags->>'highway' AS highway,
       tags->>'railway' AS railway,
       tags->>'waterway' AS waterway,
       tags->>'aeroway' AS aeroway,
       tags->>'route' AS route,
       tags->>'barrier' AS barrier,
       tags->>'boundary' AS boundary,
       tags->>'power' AS power,
       tags->>'surface' AS surface,
       tags->>'lanes' AS lanes,
       tags->>'maxspeed' AS maxspeed,
       tags->>'oneway' AS oneway,
       tags->>'bridge' AS bridge,
       tags->>'tunnel' AS tunnel,
       tags->>'ref' AS ref,
       tags,
       region,
       geom AS way
FROM osm_ways
"""

CREATE_PLANET_OSM_ROADS_VIEW = """
CREATE OR REPLACE VIEW planet_osm_roads AS
SELECT osm_id,
       tags->>'name' AS name,
       tags->>'highway' AS highway,
       tags->>'railway' AS railway,
       tags->>'ref' AS ref,
       tags->>'surface' AS surface,
       tags->>'lanes' AS lanes,
       tags->>'maxspeed' AS maxspeed,
       tags->>'oneway' AS oneway,
       tags->>'bridge' AS bridge,
       tags->>'tunnel' AS tunnel,
       tags,
       region,
       geom AS way
FROM osm_ways
WHERE tags ? 'highway' OR tags ? 'railway'
"""

# Expression indexes for common tag lookups (partial — only rows with the tag)
TAG_INDEXES = [
    ("idx_osm_nodes_amenity", "osm_nodes", "amenity"),
    ("idx_osm_nodes_shop", "osm_nodes", "shop"),
    ("idx_osm_nodes_name", "osm_nodes", "name"),
    ("idx_osm_nodes_place", "osm_nodes", "place"),
    ("idx_osm_nodes_highway", "osm_nodes", "highway"),
    ("idx_osm_nodes_building", "osm_nodes", "building"),
    ("idx_osm_nodes_tourism", "osm_nodes", "tourism"),
    ("idx_osm_nodes_leisure", "osm_nodes", "leisure"),
    ("idx_osm_ways_highway", "osm_ways", "highway"),
    ("idx_osm_ways_railway", "osm_ways", "railway"),
    ("idx_osm_ways_waterway", "osm_ways", "waterway"),
    ("idx_osm_ways_name", "osm_ways", "name"),
    ("idx_osm_ways_building", "osm_ways", "building"),
]

# Commit after this many batches to balance speed vs memory
_COMMIT_EVERY_N_BATCHES = 20


@dataclass
class ImportResult:
    """Result of a PostGIS import operation."""

    node_count: int
    way_count: int
    postgis_url: str
    was_prior_import: bool
    imported_at: str
    region: str


def get_postgis_url() -> str:
    """Get PostGIS connection URL from environment or default."""
    return os.environ.get("AFL_POSTGIS_URL", DEFAULT_POSTGIS_URL)


def sanitize_url(url: str) -> str:
    """Strip password from a PostgreSQL URL for logging/return values."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)


def ensure_schema(conn) -> None:
    """Create PostGIS extension and tables if they don't exist.

    Index creation is skipped when the indexes already exist, since
    ``CREATE INDEX IF NOT EXISTS`` on large GIST/GIN indexes can block
    for hours even when the index is already present.
    """
    with conn.cursor() as cur:
        try:
            cur.execute(CREATE_POSTGIS_EXT)
        except (psycopg2.errors.DuplicateObject, psycopg2.errors.UniqueViolation):
            conn.rollback()
        try:
            cur.execute(CREATE_HSTORE_EXT)
        except (psycopg2.errors.DuplicateObject, psycopg2.errors.UniqueViolation):
            conn.rollback()
        cur.execute(CREATE_NODES_TABLE)
        cur.execute(CREATE_WAYS_TABLE)
        cur.execute(CREATE_IMPORT_LOG_TABLE)

        # Only create indexes if they don't already exist
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename IN ('osm_nodes', 'osm_ways')")
        existing = {row[0] for row in cur.fetchall()}

        if "idx_osm_nodes_geom" not in existing:
            cur.execute(CREATE_NODES_GEOM_IDX)
        if "idx_osm_nodes_tags" not in existing:
            cur.execute(CREATE_NODES_TAGS_IDX)
        if "idx_osm_nodes_region" not in existing:
            cur.execute(CREATE_NODES_REGION_IDX)
        if "idx_osm_ways_geom" not in existing:
            cur.execute(CREATE_WAYS_GEOM_IDX)
        if "idx_osm_ways_tags" not in existing:
            cur.execute(CREATE_WAYS_TAGS_IDX)
        if "idx_osm_ways_region" not in existing:
            cur.execute(CREATE_WAYS_REGION_IDX)

        # Expression indexes on common tags (partial, only rows with the tag)
        for idx_name, table, tag in TAG_INDEXES:
            if idx_name not in existing:
                cur.execute(
                    f'CREATE INDEX {idx_name} ON {table} '
                    f"((tags->>'{tag}')) WHERE tags ? '{tag}'"
                )

    conn.commit()

    # osm2pgsql-compatible views (CREATE OR REPLACE is idempotent)
    with conn.cursor() as cur:
        cur.execute(CREATE_PLANET_OSM_POINT_VIEW)
        cur.execute(CREATE_PLANET_OSM_LINE_VIEW)
        cur.execute(CREATE_PLANET_OSM_ROADS_VIEW)
    conn.commit()


class _BatchFlusher:
    """Shared batched-insert logic for nodes and ways."""

    PROGRESS_INTERVAL = 60.0  # seconds between progress logs and heartbeats

    def __init__(
        self,
        conn,
        insert_sql: str,
        template: str,
        label: str,
        region: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        step_log=None,
        task_heartbeat=None,
    ):
        self.conn = conn
        self._insert_sql = insert_sql
        self._template = template
        self._label = label
        self.region = region
        self.batch_size = batch_size
        self.batch: list[tuple] = []
        self.total_count: int = 0
        self._step_log = step_log
        self._task_heartbeat = task_heartbeat
        self._t0 = time.monotonic()
        self._last_progress = self._t0
        self._batches_since_commit = 0

    def _flush(self) -> None:
        if not self.batch:
            return
        # Heartbeat before the UPSERT — a single batch insert can take
        # minutes on large tables with index conflicts, and we need to
        # signal liveness before that blocking call.
        self._maybe_heartbeat()

        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                self._insert_sql,
                self.batch,
                template=self._template,
                page_size=len(self.batch),
            )
        self.total_count += len(self.batch)
        self.batch.clear()
        self._batches_since_commit += 1

        # Commit periodically to keep WAL bounded
        if self._batches_since_commit >= _COMMIT_EVERY_N_BATCHES:
            self.conn.commit()
            self._batches_since_commit = 0

        self._maybe_progress()

    def _maybe_heartbeat(self) -> None:
        """Lightweight heartbeat — signals liveness without logging."""
        if not self._task_heartbeat:
            return
        now = time.monotonic()
        if now - self._last_progress < self.PROGRESS_INTERVAL:
            return
        try:
            self._task_heartbeat(
                progress_message=f"{self._label} ({self.region}): flushing batch ({self.total_count:,} so far)",
            )
        except Exception:
            pass

    def _maybe_progress(self) -> None:
        now = time.monotonic()
        if now - self._last_progress < self.PROGRESS_INTERVAL:
            return
        self._last_progress = now
        elapsed = now - self._t0
        rate = self.total_count / elapsed if elapsed > 0 else 0

        # Signal liveness to avoid task timeout during long imports
        if self._task_heartbeat:
            try:
                self._task_heartbeat(
                    progress_message=f"{self._label} ({self.region}): {self.total_count:,} @ {rate:,.0f}/s",
                )
            except Exception:
                log.debug("%s: heartbeat callback failed", self._label)

        if not self._step_log:
            return
        msg = (
            f"{self._label} ({self.region}): {self.total_count:,} inserted "
            f"({_fmt_elapsed(elapsed)}, {rate:,.0f}/s)"
        )
        log.info(msg)
        try:
            self._step_log(msg)
        except Exception:
            log.exception("%s: step_log callback failed", self._label)

    def finalize(self) -> int:
        self._flush()
        self.conn.commit()
        self._batches_since_commit = 0
        if self._step_log and self.total_count > 0:
            elapsed = time.monotonic() - self._t0
            rate = self.total_count / elapsed if elapsed > 0 else 0
            self._step_log(
                f"{self._label} ({self.region}): complete — {self.total_count:,} "
                f"in {_fmt_elapsed(elapsed)} ({rate:,.0f}/s)"
            )
        return self.total_count


class CombinedCollector(osmium.SimpleHandler if HAS_OSMIUM else object):
    """Single-pass collector for both nodes and ways.

    Uses pyosmium's NodeLocationsForWays index for efficient way
    geometry construction without caching node locations in Python.
    """

    def __init__(
        self,
        conn,
        region: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        use_upsert: bool = True,
        progress=None,
        step_log=None,
        task_heartbeat=None,
        node_insert_sql: str | None = None,
        way_insert_sql: str | None = None,
    ):
        if HAS_OSMIUM:
            super().__init__()
        self._progress = progress
        if node_insert_sql:
            node_sql = node_insert_sql
        else:
            node_sql = UPSERT_NODES_SQL if use_upsert else INSERT_NODES_SQL
        if way_insert_sql:
            way_sql = way_insert_sql
        else:
            way_sql = UPSERT_WAYS_SQL if use_upsert else INSERT_WAYS_SQL
        self._nodes = _BatchFlusher(
            conn,
            node_sql,
            "(%s, %s, %s, ST_GeomFromEWKT(%s))",
            "PostGIS Nodes",
            region,
            batch_size,
            step_log,
            task_heartbeat,
        )
        self._ways = _BatchFlusher(
            conn,
            way_sql,
            "(%s, %s, %s, ST_GeomFromEWKT(%s))",
            "PostGIS Ways",
            region,
            batch_size,
            step_log,
            task_heartbeat,
        )
        self.region = region
        self._task_heartbeat = task_heartbeat
        self._element_count = 0
        self._last_hb = time.monotonic()
        self._hb_interval = 60.0  # heartbeat every 60s during scan

    def _scan_heartbeat(self) -> None:
        """Send periodic heartbeat during osmium scan to signal liveness."""
        self._element_count += 1
        now = time.monotonic()
        if now - self._last_hb < self._hb_interval:
            return
        self._last_hb = now
        if self._task_heartbeat:
            try:
                nodes = self._nodes.total_count + len(self._nodes.batch)
                ways = self._ways.total_count + len(self._ways.batch)
                self._task_heartbeat(
                    progress_message=(
                        f"Scanning {self.region}: {self._element_count:,} elements "
                        f"({nodes:,}N/{ways:,}W queued)"
                    ),
                )
            except Exception:
                pass

    def node(self, n) -> None:
        self._scan_heartbeat()
        if self._progress:
            self._progress.tick("node")
        tags = {t.k: t.v for t in n.tags}
        if not tags:
            return
        lon = n.location.lon
        lat = n.location.lat
        ewkt = f"SRID=4326;POINT({lon} {lat})"
        self._nodes.batch.append((n.id, self.region, json.dumps(tags), ewkt))
        if len(self._nodes.batch) >= self._nodes.batch_size:
            self._nodes._flush()

    def way(self, w) -> None:
        self._scan_heartbeat()
        if self._progress:
            self._progress.tick("way")
        tags = {t.k: t.v for t in w.tags}
        if not tags:
            return

        # Build LINESTRING from node locations resolved by pyosmium
        coords = []
        for n in w.nodes:
            try:
                lon = n.location.lon
                lat = n.location.lat
                coords.append(f"{lon} {lat}")
            except osmium.InvalidLocationError:
                pass

        if len(coords) < 2:
            return

        ewkt = f"SRID=4326;LINESTRING({', '.join(coords)})"
        self._ways.batch.append((w.id, self.region, json.dumps(tags), ewkt))
        if len(self._ways.batch) >= self._ways.batch_size:
            self._ways._flush()

    @property
    def node_count(self) -> int:
        return self._nodes.total_count

    @property
    def way_count(self) -> int:
        return self._ways.total_count

    def finalize(self) -> tuple[int, int]:
        node_count = self._nodes.finalize()
        way_count = self._ways.finalize()
        return node_count, way_count


# Keep old classes for backward compatibility with tests
class NodeCollector(osmium.SimpleHandler if HAS_OSMIUM else object):
    """Collects OSM nodes and flushes them to PostGIS in batches."""

    PROGRESS_INTERVAL = 120.0

    def __init__(
        self,
        conn,
        region: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        progress=None,
        step_log=None,
    ):
        if HAS_OSMIUM:
            super().__init__()
        self.conn = conn
        self.region = region
        self.batch_size = batch_size
        self.batch: list[tuple] = []
        self.total_count: int = 0
        self._progress = progress
        self._step_log = step_log
        self._t0 = time.monotonic()
        self._last_progress = self._t0

    def node(self, n) -> None:
        if self._progress:
            self._progress.tick("node")
        tags = {t.k: t.v for t in n.tags}
        if not tags:
            return
        lon = n.location.lon
        lat = n.location.lat
        ewkt = f"SRID=4326;POINT({lon} {lat})"
        self.batch.append((n.id, self.region, json.dumps(tags), ewkt))
        if len(self.batch) >= self.batch_size:
            self._flush()

    def _flush(self) -> None:
        if not self.batch:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                UPSERT_NODES_SQL,
                self.batch,
                template="(%s, %s, %s, ST_GeomFromEWKT(%s))",
            )
        self.conn.commit()
        self.total_count += len(self.batch)
        self.batch.clear()

    def finalize(self) -> int:
        self._flush()
        return self.total_count


class WayCollector(osmium.SimpleHandler if HAS_OSMIUM else object):
    """Collects OSM ways and flushes them to PostGIS in batches."""

    PROGRESS_INTERVAL = 120.0

    def __init__(
        self,
        conn,
        region: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        progress=None,
        step_log=None,
    ):
        if HAS_OSMIUM:
            super().__init__()
        self.conn = conn
        self.region = region
        self.batch_size = batch_size
        self.batch: list[tuple] = []
        self.total_count: int = 0
        self._node_cache: dict[int, tuple[float, float]] = {}
        self._pending_ways: list[tuple[int, dict, list[int]]] = []
        self._progress = progress
        self._step_log = step_log
        self._t0 = time.monotonic()
        self._last_progress = self._t0

    def node(self, n) -> None:
        if self._progress:
            self._progress.tick("node")
        self._node_cache[n.id] = (n.location.lon, n.location.lat)

    def way(self, w) -> None:
        if self._progress:
            self._progress.tick("way")
        tags = {t.k: t.v for t in w.tags}
        if not tags:
            return
        node_refs = [n.ref for n in w.nodes]
        self._pending_ways.append((w.id, tags, node_refs))

    def _build_and_flush(self) -> None:
        for osm_id, tags, node_refs in self._pending_ways:
            coords = []
            for ref in node_refs:
                if ref in self._node_cache:
                    lon, lat = self._node_cache[ref]
                    coords.append(f"{lon} {lat}")
            if len(coords) < 2:
                continue
            ewkt = f"SRID=4326;LINESTRING({', '.join(coords)})"
            self.batch.append((osm_id, self.region, json.dumps(tags), ewkt))
            if len(self.batch) >= self.batch_size:
                self._flush_batch()
        self._flush_batch()
        self._node_cache.clear()
        self._pending_ways.clear()

    def _flush_batch(self) -> None:
        if not self.batch:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                UPSERT_WAYS_SQL,
                self.batch,
                template="(%s, %s, %s, ST_GeomFromEWKT(%s))",
            )
        self.conn.commit()
        self.total_count += len(self.batch)
        self.batch.clear()

    def finalize(self) -> int:
        self._build_and_flush()
        return self.total_count


def import_to_postgis(
    pbf_path: str,
    postgis_url: str | None = None,
    source_url: str = "",
    region: str = "",
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    step_log=None,
    task_heartbeat=None,
) -> ImportResult:
    """Import OSM nodes and ways from a PBF file into PostGIS.

    Uses a single-pass PBF scan with pyosmium's NodeLocationsForWays
    index to process nodes and ways simultaneously. Disables
    synchronous_commit during import for higher throughput.

    Args:
        pbf_path: Path to the OSM PBF file
        postgis_url: PostgreSQL connection URL (reads AFL_POSTGIS_URL if None)
        source_url: Original download URL for import log
        region: Region identifier (e.g. "france", "california")
        force: Re-import even if region was previously imported
        batch_size: Number of rows per batch insert
        step_log: Optional callback for progress reporting
        task_heartbeat: Optional callback to signal liveness and avoid timeout

    Returns:
        ImportResult with counts and metadata
    """
    if not HAS_OSMIUM:
        raise ImportError("pyosmium is required for PBF parsing")
    if not HAS_PSYCOPG2:
        raise ImportError("psycopg2 is required for PostGIS import")

    pbf_path = localize(pbf_path)
    postgis_url = postgis_url or get_postgis_url()
    conn = psycopg2.connect(postgis_url, gssencmode="disable")

    staging_nodes = staging_ways = ""
    try:
        ensure_schema(conn)

        # Check for prior import of this region
        if region:
            with conn.cursor() as cur:
                cur.execute(CHECK_PRIOR_IMPORT_SQL, (region,))
                row = cur.fetchone()
                if row is not None:
                    if not force:
                        log.info(
                            "Region '%s' already imported (nodes=%d, ways=%d, at=%s), skipping",
                            region,
                            row[1],
                            row[2],
                            row[3],
                        )
                        if step_log:
                            step_log(
                                f"PostGisImport: region '{region}' already imported "
                                f"({row[1]} nodes, {row[2]} ways), skipping",
                            )
                        return ImportResult(
                            node_count=row[1],
                            way_count=row[2],
                            postgis_url=sanitize_url(postgis_url),
                            was_prior_import=True,
                            imported_at=str(row[3]),
                            region=region,
                        )
                    log.info(
                        "Re-importing region '%s' (force=True, prior: nodes=%d, ways=%d)",
                        region,
                        row[1],
                        row[2],
                    )

        # Performance: disable synchronous_commit for this session
        with conn.cursor() as cur:
            cur.execute("SET synchronous_commit = off")

        # For force-reimport: delete old data and use plain INSERT for merge
        use_upsert = True
        if force and region:
            log.info("Deleting prior data for region '%s'", region)
            with conn.cursor() as cur:
                cur.execute(DELETE_REGION_NODES_SQL, (region,))
                cur.execute(DELETE_REGION_WAYS_SQL, (region,))
            conn.commit()
            use_upsert = False  # no conflicts possible after delete
        elif not region:
            # Global import with no region — plain INSERT for merge
            use_upsert = False

        # Create unlogged staging tables — no indexes, no WAL, no contention
        staging_nodes, staging_ways = _create_staging_tables(conn, region, step_log)
        staging_insert_nodes = f"INSERT INTO {staging_nodes} (osm_id, region, tags, geom) VALUES %s"
        staging_insert_ways = f"INSERT INTO {staging_ways} (osm_id, region, tags, geom) VALUES %s"

        # Single-pass import using pyosmium's NodeLocationsForWays
        log.info(
            "Importing from %s (region=%s, single-pass, staging)",
            pbf_path, region or "<global>",
        )
        file_size = get_file_size(str(pbf_path))
        progress = ScanProgressTracker(file_size, step_log, label="PostGIS Import")

        collector = CombinedCollector(
            conn,
            region=region,
            batch_size=batch_size,
            use_upsert=False,  # staging tables have no PK — always plain INSERT
            progress=progress,
            step_log=step_log,
            task_heartbeat=task_heartbeat,
            node_insert_sql=staging_insert_nodes,
            way_insert_sql=staging_insert_ways,
        )

        # locations=True enables pyosmium's built-in NodeLocationsForWays
        # index so way.nodes[i].location is resolved automatically
        collector.apply_file(pbf_path, locations=True)

        collector.finalize()
        progress.finish()
        scan_nodes = collector.node_count
        scan_ways = collector.way_count
        log.info(
            "Scan complete: %d nodes + %d ways staged (region=%s)",
            scan_nodes, scan_ways, region or "<global>",
        )

        # Merge staging tables into main tables (single bulk operation)
        node_count, way_count = _merge_staging_tables(
            conn, staging_nodes, staging_ways,
            use_upsert=use_upsert,
            step_log=step_log,
            task_heartbeat=task_heartbeat,
        )
        log.info(
            "Merged %d nodes + %d ways (region=%s)",
            node_count, way_count, region or "<global>",
        )

        # Log the import
        now = datetime.now(UTC).isoformat()
        with conn.cursor() as cur:
            cur.execute("SET synchronous_commit = on")
            cur.execute(INSERT_LOG_SQL, (source_url, pbf_path, region, node_count, way_count))
        conn.commit()

        return ImportResult(
            node_count=node_count,
            way_count=way_count,
            postgis_url=sanitize_url(postgis_url),
            was_prior_import=False,
            imported_at=now,
            region=region,
        )
    except Exception:
        # Clean up staging tables on failure
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {staging_nodes}")
                cur.execute(f"DROP TABLE IF EXISTS {staging_ways}")
            conn.commit()
        except Exception:
            pass
        raise
    finally:
        conn.close()
