"""PostGIS import engine for OSM PBF files.

Parses PBF files via pyosmium and imports nodes/ways into PostGIS
via psycopg2. Supports batched inserts and upsert semantics.
"""

import json
import logging
import os
import re
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

DEFAULT_POSTGIS_URL = "postgresql://afl:afl@localhost:5432/afl_gis"

# DDL statements
CREATE_POSTGIS_EXT = "CREATE EXTENSION IF NOT EXISTS postgis"

CREATE_NODES_TABLE = """
CREATE TABLE IF NOT EXISTS osm_nodes (
    osm_id BIGINT PRIMARY KEY,
    tags JSONB,
    geom geometry(Point, 4326)
)
"""

CREATE_WAYS_TABLE = """
CREATE TABLE IF NOT EXISTS osm_ways (
    osm_id BIGINT PRIMARY KEY,
    tags JSONB,
    geom geometry(LineString, 4326)
)
"""

CREATE_IMPORT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS osm_import_log (
    id SERIAL PRIMARY KEY,
    url TEXT,
    path TEXT,
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
CREATE_WAYS_GEOM_IDX = "CREATE INDEX IF NOT EXISTS idx_osm_ways_geom ON osm_ways USING GIST (geom)"
CREATE_WAYS_TAGS_IDX = "CREATE INDEX IF NOT EXISTS idx_osm_ways_tags ON osm_ways USING GIN (tags)"

UPSERT_NODES_SQL = """
INSERT INTO osm_nodes (osm_id, tags, geom)
VALUES %s
ON CONFLICT (osm_id) DO UPDATE SET tags = EXCLUDED.tags, geom = EXCLUDED.geom
"""

UPSERT_WAYS_SQL = """
INSERT INTO osm_ways (osm_id, tags, geom)
VALUES %s
ON CONFLICT (osm_id) DO UPDATE SET tags = EXCLUDED.tags, geom = EXCLUDED.geom
"""

INSERT_LOG_SQL = """
INSERT INTO osm_import_log (url, path, node_count, way_count)
VALUES (%s, %s, %s, %s)
"""

CHECK_PRIOR_IMPORT_SQL = """
SELECT id, node_count, way_count, imported_at
FROM osm_import_log
WHERE url = %s
ORDER BY imported_at DESC
LIMIT 1
"""


@dataclass
class ImportResult:
    """Result of a PostGIS import operation."""

    node_count: int
    way_count: int
    postgis_url: str
    was_prior_import: bool
    imported_at: str


def get_postgis_url() -> str:
    """Get PostGIS connection URL from environment or default."""
    return os.environ.get("AFL_POSTGIS_URL", DEFAULT_POSTGIS_URL)


def sanitize_url(url: str) -> str:
    """Strip password from a PostgreSQL URL for logging/return values."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)


def ensure_schema(conn) -> None:
    """Create PostGIS extension and tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute(CREATE_POSTGIS_EXT)
        cur.execute(CREATE_NODES_TABLE)
        cur.execute(CREATE_WAYS_TABLE)
        cur.execute(CREATE_IMPORT_LOG_TABLE)
        cur.execute(CREATE_NODES_GEOM_IDX)
        cur.execute(CREATE_NODES_TAGS_IDX)
        cur.execute(CREATE_WAYS_GEOM_IDX)
        cur.execute(CREATE_WAYS_TAGS_IDX)
    conn.commit()


class NodeCollector(osmium.SimpleHandler if HAS_OSMIUM else object):
    """Collects OSM nodes and flushes them to PostGIS in batches."""

    def __init__(self, conn, batch_size: int = 10000, progress=None):
        if HAS_OSMIUM:
            super().__init__()
        self.conn = conn
        self.batch_size = batch_size
        self.batch: list[tuple] = []
        self.total_count: int = 0
        self._progress = progress

    def node(self, n) -> None:
        """Process a single OSM node."""
        if self._progress:
            self._progress.tick("node")
        tags = {t.k: t.v for t in n.tags}
        if not tags:
            return

        lon = n.location.lon
        lat = n.location.lat
        ewkt = f"SRID=4326;POINT({lon} {lat})"
        self.batch.append((n.id, json.dumps(tags), ewkt))

        if len(self.batch) >= self.batch_size:
            self._flush()

    def _flush(self) -> None:
        """Write current batch to PostGIS."""
        if not self.batch:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                UPSERT_NODES_SQL,
                self.batch,
                template="(%s, %s, ST_GeomFromEWKT(%s))",
            )
        self.conn.commit()
        self.total_count += len(self.batch)
        log.debug("Flushed %d nodes (total: %d)", len(self.batch), self.total_count)
        self.batch.clear()

    def finalize(self) -> int:
        """Flush remaining batch and return total count."""
        self._flush()
        return self.total_count


class WayCollector(osmium.SimpleHandler if HAS_OSMIUM else object):
    """Collects OSM ways and flushes them to PostGIS in batches.

    Uses a two-pass approach: first pass caches node locations,
    second pass builds LINESTRING geometries from node refs.
    """

    def __init__(self, conn, batch_size: int = 10000, progress=None):
        if HAS_OSMIUM:
            super().__init__()
        self.conn = conn
        self.batch_size = batch_size
        self.batch: list[tuple] = []
        self.total_count: int = 0
        self._node_cache: dict[int, tuple[float, float]] = {}
        self._pending_ways: list[tuple[int, dict, list[int]]] = []
        self._progress = progress

    def node(self, n) -> None:
        """Cache node locations for way geometry construction."""
        if self._progress:
            self._progress.tick("node")
        self._node_cache[n.id] = (n.location.lon, n.location.lat)

    def way(self, w) -> None:
        """Store way data for later geometry construction."""
        if self._progress:
            self._progress.tick("way")
        tags = {t.k: t.v for t in w.tags}
        if not tags:
            return
        node_refs = [n.ref for n in w.nodes]
        self._pending_ways.append((w.id, tags, node_refs))

    def _build_and_flush(self) -> None:
        """Build LINESTRING geometries from cached nodes and flush to PostGIS."""
        for osm_id, tags, node_refs in self._pending_ways:
            coords = []
            for ref in node_refs:
                if ref in self._node_cache:
                    lon, lat = self._node_cache[ref]
                    coords.append(f"{lon} {lat}")

            if len(coords) < 2:
                continue

            ewkt = f"SRID=4326;LINESTRING({', '.join(coords)})"
            self.batch.append((osm_id, json.dumps(tags), ewkt))

            if len(self.batch) >= self.batch_size:
                self._flush_batch()

        self._flush_batch()
        self._node_cache.clear()
        self._pending_ways.clear()

    def _flush_batch(self) -> None:
        """Write current batch to PostGIS."""
        if not self.batch:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                UPSERT_WAYS_SQL,
                self.batch,
                template="(%s, %s, ST_GeomFromEWKT(%s))",
            )
        self.conn.commit()
        self.total_count += len(self.batch)
        log.debug("Flushed %d ways (total: %d)", len(self.batch), self.total_count)
        self.batch.clear()

    def finalize(self) -> int:
        """Build geometries, flush, and return total count."""
        self._build_and_flush()
        return self.total_count


def import_to_postgis(
    pbf_path: str,
    postgis_url: str | None = None,
    source_url: str = "",
    batch_size: int = 10000,
    step_log=None,
) -> ImportResult:
    """Import OSM nodes and ways from a PBF file into PostGIS.

    Args:
        pbf_path: Path to the OSM PBF file
        postgis_url: PostgreSQL connection URL (reads AFL_POSTGIS_URL if None)
        source_url: Original download URL for import log
        batch_size: Number of rows per batch insert

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

        # Check for prior import
        was_prior = False
        if source_url:
            with conn.cursor() as cur:
                cur.execute(CHECK_PRIOR_IMPORT_SQL, (source_url,))
                row = cur.fetchone()
                if row is not None:
                    was_prior = True
                    log.info(
                        "Prior import found (id=%d, nodes=%d, ways=%d, at=%s)",
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                    )

        # Pass 1: import nodes
        log.info("Importing nodes from %s", pbf_path)
        file_size = get_file_size(str(pbf_path))
        node_progress = ScanProgressTracker(file_size, step_log, label="PostGIS Nodes")
        node_collector = NodeCollector(conn, batch_size=batch_size, progress=node_progress)
        node_collector.apply_file(pbf_path, locations=True)
        node_count = node_collector.finalize()
        node_progress.finish()
        log.info("Imported %d nodes", node_count)

        # Pass 2: import ways (needs node locations)
        log.info("Importing ways from %s", pbf_path)
        way_progress = ScanProgressTracker(file_size, step_log, label="PostGIS Ways")
        way_collector = WayCollector(conn, batch_size=batch_size, progress=way_progress)
        way_collector.apply_file(pbf_path, locations=True)
        way_count = way_collector.finalize()
        way_progress.finish()
        log.info("Imported %d ways", way_count)

        # Log the import
        now = datetime.now(UTC).isoformat()
        with conn.cursor() as cur:
            cur.execute(INSERT_LOG_SQL, (source_url, pbf_path, node_count, way_count))
        conn.commit()

        return ImportResult(
            node_count=node_count,
            way_count=way_count,
            postgis_url=sanitize_url(postgis_url),
            was_prior_import=was_prior,
            imported_at=now,
        )
    finally:
        conn.close()
