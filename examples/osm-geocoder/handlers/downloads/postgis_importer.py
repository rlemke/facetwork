"""PostGIS import engine for OSM PBF files.

Parses PBF files via pyosmium and imports nodes/ways into PostGIS
via psycopg2. Supports batched inserts, upsert semantics, and
per-region partitioned imports with skip-if-imported logic.

Performance optimizations:
- Single PBF pass for both nodes and ways (pyosmium NodeLocationsForWays)
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

from afl.runtime.storage import localize

from ..shared.scan_progress import ScanProgressTracker, get_file_size

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
        except psycopg2.errors.DuplicateObject:
            conn.rollback()
        try:
            cur.execute(CREATE_HSTORE_EXT)
        except psycopg2.errors.DuplicateObject:
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
    conn.commit()


class _BatchFlusher:
    """Shared batched-insert logic for nodes and ways."""

    PROGRESS_INTERVAL = 120.0  # seconds between progress logs

    def __init__(
        self,
        conn,
        insert_sql: str,
        template: str,
        label: str,
        region: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        step_log=None,
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
        self._t0 = time.monotonic()
        self._last_progress = self._t0
        self._batches_since_commit = 0

    def _flush(self) -> None:
        if not self.batch:
            return
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

    def _maybe_progress(self) -> None:
        if not self._step_log:
            return
        now = time.monotonic()
        if now - self._last_progress < self.PROGRESS_INTERVAL:
            return
        self._last_progress = now
        elapsed = now - self._t0
        rate = self.total_count / elapsed if elapsed > 0 else 0
        msg = (
            f"{self._label} ({self.region}): {self.total_count:,} inserted "
            f"({elapsed:.0f}s, {rate:,.0f}/s)"
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
                f"in {elapsed:.0f}s ({rate:,.0f}/s)"
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
    ):
        if HAS_OSMIUM:
            super().__init__()
        self._progress = progress
        node_sql = UPSERT_NODES_SQL if use_upsert else INSERT_NODES_SQL
        way_sql = UPSERT_WAYS_SQL if use_upsert else INSERT_WAYS_SQL
        self._nodes = _BatchFlusher(
            conn,
            node_sql,
            "(%s, %s, %s, ST_GeomFromEWKT(%s))",
            "PostGIS Nodes",
            region,
            batch_size,
            step_log,
        )
        self._ways = _BatchFlusher(
            conn,
            way_sql,
            "(%s, %s, %s, ST_GeomFromEWKT(%s))",
            "PostGIS Ways",
            region,
            batch_size,
            step_log,
        )
        self.region = region

    def node(self, n) -> None:
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

    Returns:
        ImportResult with counts and metadata
    """
    if not HAS_OSMIUM:
        raise ImportError("pyosmium is required for PBF parsing")
    if not HAS_PSYCOPG2:
        raise ImportError("psycopg2 is required for PostGIS import")

    pbf_path = localize(pbf_path)
    postgis_url = postgis_url or get_postgis_url()
    conn = psycopg2.connect(postgis_url)

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

        # For force-reimport: delete old data and use plain INSERT (faster)
        use_upsert = True
        if force and region:
            log.info("Deleting prior data for region '%s'", region)
            with conn.cursor() as cur:
                cur.execute(DELETE_REGION_NODES_SQL, (region,))
                cur.execute(DELETE_REGION_WAYS_SQL, (region,))
            conn.commit()
            use_upsert = False  # no conflicts possible after delete
        elif not region:
            # Global import with no region — plain INSERT is fine for empty tables
            pass

        # Single-pass import using pyosmium's NodeLocationsForWays
        log.info("Importing from %s (region=%s, single-pass)", pbf_path, region or "<global>")
        file_size = get_file_size(str(pbf_path))
        progress = ScanProgressTracker(file_size, step_log, label="PostGIS Import")

        collector = CombinedCollector(
            conn,
            region=region,
            batch_size=batch_size,
            use_upsert=use_upsert,
            progress=progress,
            step_log=step_log,
        )

        # locations=True enables pyosmium's built-in NodeLocationsForWays
        # index so way.nodes[i].location is resolved automatically
        collector.apply_file(pbf_path, locations=True)

        node_count, way_count = collector.finalize()
        progress.finish()
        log.info(
            "Imported %d nodes + %d ways (region=%s)",
            node_count,
            way_count,
            region or "<global>",
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
    finally:
        conn.close()
