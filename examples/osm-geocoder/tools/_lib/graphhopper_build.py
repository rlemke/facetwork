"""GraphHopper routing-graph build library.

Single source of truth for building GraphHopper routing graphs from
cached OSM PBFs. Used by both the ``build-graphhopper-graph`` CLI tool
and the FFL ``osm.ops.GraphHopper.BuildGraph*`` handlers — they share
one cache layout (``<cache_root>/graphhopper/<region>-latest/<profile>/``)
and one manifest.

A built graph is a **directory** of GraphHopper binary files (nodes,
edges, properties, geometry, ...). Each (region, profile) combination
is its own directory; a region can have multiple profile graphs
simultaneously.

Cache validity requires:

- The source PBF's SHA-256 still matches what the manifest recorded, AND
- The recorded ``graphhopper_version`` matches the current version
  constant (graphs built with GraphHopper 8.0 are not loadable by 9.0).

Local-backend only — GraphHopper writes directly to a filesystem path
via its Java ``import`` subcommand, and the built graph is a directory
tree that the HDFS backend doesn't currently support.

Requires Java 17+ and a GraphHopper 8.x ``-web.jar``. The jar path is
discovered from (in order): ``--jar`` CLI flag → ``$GRAPHHOPPER_JAR``
env var → ``~/.graphhopper/graphhopper-web.jar``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _lib.manifest import (
    cache_dir,
    manifest_transaction,
    read_manifest,
    utcnow_iso,
)
from _lib.storage import LocalStorage

SOURCE_CACHE_TYPE = "pbf"
OUTPUT_CACHE_TYPE = "graphhopper"

# GraphHopper version we build against. Bump when upgrading — cache
# entries tagged with an older version become stale automatically.
GRAPHHOPPER_VERSION = "8.0"

# Supported routing profiles (mirrors what GraphHopper 8 ships with).
PROFILES: tuple[str, ...] = (
    "car",
    "bike",
    "foot",
    "motorcycle",
    "truck",
    "hike",
    "mtb",
    "racingbike",
)

_MOTORIZED_PROFILES = {"car", "motorcycle", "truck"}
_NON_MOTORIZED_PROFILES = {"bike", "mtb", "racingbike"}

DEFAULT_JVM_MEMORY = os.environ.get("GRAPHHOPPER_XMX", "4g")
DEFAULT_TIMEOUT_SECONDS = 3600  # 1h — large countries need it

# In-process locks: one per (region, profile) pair to serialize
# concurrent same-graph builds, and one module-level manifest write lock
# to serialize concurrent manifest updates within a single process.
_build_locks: dict[tuple[str, str], threading.Lock] = {}
_build_locks_guard = threading.Lock()
_manifest_write_lock = threading.Lock()


def _build_lock(region: str, profile: str) -> threading.Lock:
    key = (region, profile)
    with _build_locks_guard:
        lock = _build_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[key] = lock
        return lock


@dataclass
class BuildResult:
    """Outcome of a ``build_graph`` call."""

    region: str
    profile: str
    graph_dir: str              # absolute path to the built graph directory
    relative_path: str          # relative to graphhopper/ cache dir
    total_size_bytes: int
    node_count: int
    edge_count: int
    graphhopper_version: str
    generated_at: str
    duration_seconds: float
    was_cached: bool
    source_url: str
    source_pbf_path: str
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class BuildError(RuntimeError):
    """Raised when graph build fails (JAR missing, import failure, unknown profile, etc.)."""


def default_jar_path() -> str:
    """Resolve the GraphHopper jar path from env or the standard default."""
    return os.environ.get(
        "GRAPHHOPPER_JAR", os.path.expanduser("~/.graphhopper/graphhopper-web.jar")
    )


def pbf_rel_path(region: str) -> str:
    return f"{region}-latest.osm.pbf"


def pbf_abs_path(region: str) -> Path:
    return Path(cache_dir(SOURCE_CACHE_TYPE)) / pbf_rel_path(region)


def graph_rel_path(region: str, profile: str) -> str:
    """Manifest key — <region>-latest/<profile>."""
    return f"{region}-latest/{profile}"


def graph_abs_path(region: str, profile: str) -> Path:
    return Path(cache_dir(OUTPUT_CACHE_TYPE)) / graph_rel_path(region, profile)


def _staging_dir(region: str, profile: str) -> Path:
    """Stage adjacent to the final destination. Override with
    ``AFL_OSM_CONVERT_STAGING=tmp`` for the legacy local-tmp behavior.
    """
    if (os.environ.get("AFL_OSM_CONVERT_STAGING") or "").lower() == "tmp":
        base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
        safe = region.replace("/", "_")
        return Path(base) / "facetwork-graphhopper-staging" / safe / profile
    out = graph_abs_path(region, profile)
    return out.with_name(out.name + ".tmp")


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _graph_exists(graph_dir: Path) -> bool:
    if not graph_dir.is_dir():
        return False
    for entry in graph_dir.iterdir():
        name = entry.name
        if name.startswith("nodes") or name.startswith("edges"):
            return True
    return False


@dataclass
class GraphStats:
    valid: bool
    node_count: int
    edge_count: int


def read_graph_stats(graph_dir: Path) -> GraphStats:
    """Read GraphHopper's ``properties`` file for node / edge counts."""
    if not _graph_exists(graph_dir):
        return GraphStats(valid=False, node_count=0, edge_count=0)
    properties = graph_dir / "properties"
    if not properties.exists():
        # Graph files exist but properties missing — still loadable, just no stats.
        return GraphStats(valid=True, node_count=0, edge_count=0)
    node_count = 0
    edge_count = 0
    try:
        for line in properties.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line:
                continue
            key, _, value = line.strip().partition("=")
            if key == "graph.nodes.count":
                node_count = int(value)
            elif key == "graph.edges.count":
                edge_count = int(value)
    except (OSError, ValueError):
        return GraphStats(valid=True, node_count=0, edge_count=0)
    return GraphStats(valid=node_count > 0, node_count=node_count, edge_count=edge_count)


def is_up_to_date(
    region: str, profile: str, pbf_entry: dict, graph_dir: Path
) -> bool:
    """True if the cached graph still matches the source PBF SHA and GraphHopper version."""
    if profile not in PROFILES:
        return False
    cache_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    rel = graph_rel_path(region, profile)
    existing = cache_manifest.get("entries", {}).get(rel)
    if not existing:
        return False
    if existing.get("source", {}).get("sha256") != pbf_entry.get("sha256"):
        return False
    if existing.get("graphhopper_version") != GRAPHHOPPER_VERSION:
        return False
    if not _graph_exists(graph_dir):
        return False
    return True


def _build_config_yaml(osm_path: Path, graph_dir: Path, profile: str) -> str:
    """Generate the GraphHopper 8.0 config YAML for an import run."""
    if profile in _MOTORIZED_PROFILES:
        ignored = "footway,cycleway,path,pedestrian,steps"
    elif profile in _NON_MOTORIZED_PROFILES:
        ignored = "motorway,trunk"
    else:
        ignored = ""
    lines = [
        "graphhopper:",
        f"  datareader.file: {osm_path}",
        f"  graph.location: {graph_dir}",
        f"  import.osm.ignored_highways: {ignored}",
        "  profiles:",
        f"    - name: {profile}",
        f"      vehicle: {profile}",
        "      custom_model_files: []",
    ]
    return "\n".join(lines) + "\n"


def _run_import(
    osm_path: Path,
    graph_dir: Path,
    profile: str,
    *,
    jar_path: str,
    jvm_memory: str,
    timeout_seconds: int,
) -> None:
    """Run GraphHopper's import subcommand, raising BuildError on failure."""
    if not Path(jar_path).is_file():
        raise BuildError(
            f"GraphHopper jar not found: {jar_path!r}. "
            "Set $GRAPHHOPPER_JAR or pass --jar."
        )
    graph_dir.mkdir(parents=True, exist_ok=True)

    config_yaml = _build_config_yaml(osm_path, graph_dir, profile)
    config_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", prefix="gh-config-", delete=False
        ) as tmp:
            tmp.write(config_yaml)
            config_path = tmp.name

        cmd = [
            "java",
            f"-Xmx{jvm_memory}",
            "-jar",
            jar_path,
            "import",
            config_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BuildError(
                f"GraphHopper import timed out after {timeout_seconds}s "
                f"(region={osm_path.name}, profile={profile})"
            ) from exc
        except FileNotFoundError as exc:
            raise BuildError(
                "java not found on PATH. Install a JDK 17+ runtime."
            ) from exc
        if result.returncode != 0:
            # Tail the stderr so the caller sees the actual failure reason.
            stderr_tail = (result.stderr or "").splitlines()[-20:]
            raise BuildError(
                "GraphHopper import failed (exit "
                f"{result.returncode}): {'; '.join(stderr_tail) or '(no stderr)'}"
            )
    finally:
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass


def build_graph(
    region: str,
    profile: str,
    *,
    force: bool = False,
    jar_path: str | None = None,
    jvm_memory: str = DEFAULT_JVM_MEMORY,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> BuildResult:
    """Build or fetch a cached GraphHopper routing graph.

    Thread-safe per (region, profile). Concurrent calls for the same
    (region, profile) serialize; different pairs run independently.
    """
    if profile not in PROFILES:
        raise BuildError(
            f"unknown profile: {profile!r}. Valid: {', '.join(PROFILES)}"
        )

    pbf_manifest = read_manifest(SOURCE_CACHE_TYPE)
    pbf_rel = pbf_rel_path(region)
    pbf_entry = pbf_manifest.get("entries", {}).get(pbf_rel)
    if not pbf_entry:
        raise BuildError(
            f"no pbf manifest entry for {region!r}; run download-pbf first"
        )
    src_pbf = pbf_abs_path(region)
    if not src_pbf.exists():
        raise BuildError(f"pbf file missing on disk: {src_pbf}")
    source_url = pbf_entry.get("source_url", "")

    jar = jar_path or default_jar_path()

    with _build_lock(region, profile):
        graph_dir = graph_abs_path(region, profile)
        rel = graph_rel_path(region, profile)

        if not force and is_up_to_date(region, profile, pbf_entry, graph_dir):
            stats = read_graph_stats(graph_dir)
            existing = read_manifest(OUTPUT_CACHE_TYPE).get("entries", {}).get(rel, {})
            return BuildResult(
                region=region,
                profile=profile,
                graph_dir=str(graph_dir),
                relative_path=rel + "/",
                total_size_bytes=existing.get("total_size_bytes", _dir_size(graph_dir)),
                node_count=stats.node_count,
                edge_count=stats.edge_count,
                graphhopper_version=GRAPHHOPPER_VERSION,
                generated_at=existing.get("generated_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                source_url=source_url,
                source_pbf_path=str(src_pbf),
                manifest_entry=existing,
            )

        # Stage the build into a local temp dir, then finalize to the
        # final location. Keeps partial builds out of the destination
        # tree if the subprocess crashes.
        staging = _staging_dir(region, profile)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        try:
            _run_import(
                src_pbf,
                staging,
                profile,
                jar_path=jar,
                jvm_memory=jvm_memory,
                timeout_seconds=timeout_seconds,
            )
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        elapsed = time.monotonic() - start

        stats = read_graph_stats(staging)
        if not stats.valid:
            shutil.rmtree(staging, ignore_errors=True)
            raise BuildError(
                f"GraphHopper produced no valid graph for {region}/{profile}"
            )

        storage = LocalStorage()
        storage.finalize_dir_from_local(str(staging), str(graph_dir))
        total_size = _dir_size(graph_dir)

        generated_at = utcnow_iso()
        entry = {
            "relative_path": rel + "/",
            "region": region,
            "profile": profile,
            "graphhopper_version": GRAPHHOPPER_VERSION,
            "total_size_bytes": total_size,
            "node_count": stats.node_count,
            "edge_count": stats.edge_count,
            "generated_at": generated_at,
            "duration_seconds": round(elapsed, 2),
            "source": {
                "cache_type": SOURCE_CACHE_TYPE,
                "relative_path": pbf_rel,
                "sha256": pbf_entry.get("sha256"),
                "size_bytes": pbf_entry.get("size_bytes"),
                "source_checksum": pbf_entry.get("source_checksum"),
                "source_timestamp": pbf_entry.get("source_timestamp"),
                "downloaded_at": pbf_entry.get("downloaded_at"),
            },
            "tool": {
                "command": "java -jar graphhopper-web.jar import",
                "jar_path": jar,
                "jvm_memory": jvm_memory,
            },
            "extra": {},
        }
        with _manifest_write_lock, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
            manifest.setdefault("entries", {})[rel] = entry

        return BuildResult(
            region=region,
            profile=profile,
            graph_dir=str(graph_dir),
            relative_path=rel + "/",
            total_size_bytes=total_size,
            node_count=stats.node_count,
            edge_count=stats.edge_count,
            graphhopper_version=GRAPHHOPPER_VERSION,
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            source_url=source_url,
            source_pbf_path=str(src_pbf),
            manifest_entry=entry,
        )


def clean_graph(graph_dir: str) -> bool:
    """Remove a built graph directory. Returns True if something was deleted."""
    p = Path(graph_dir)
    if p.exists():
        shutil.rmtree(p)
        return True
    return False


def to_graphhopper_cache(result: BuildResult) -> dict[str, Any]:
    """Map a ``BuildResult`` to the ``GraphHopperCache`` FFL schema dict."""
    return {
        "osmSource": result.source_pbf_path,
        "graphDir": result.graph_dir,
        "profile": result.profile,
        "date": result.generated_at,
        "size": result.total_size_bytes,
        "wasInCache": result.was_cached,
        "version": result.graphhopper_version,
        "nodeCount": result.node_count,
        "edgeCount": result.edge_count,
    }
