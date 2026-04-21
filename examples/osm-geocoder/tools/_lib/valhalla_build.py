"""Valhalla tile-set build library.

Single source of truth for building Valhalla routing tilesets from
cached OSM PBFs. Used by both the ``build-valhalla-tiles`` CLI tool
and the FFL ``osm.ops.Valhalla.BuildTiles`` handler — they share one
cache layout (``<cache_root>/valhalla/<region>-latest/``) and one
manifest.

Valhalla differs from GraphHopper in one important way: **profiles are
query-time, not build-time.** A single built tileset serves routing
for ``auto``, ``bicycle``, ``pedestrian``, ``truck``, etc. — there is
no per-profile directory. That means manifest keys are just
``<region>-latest`` (no profile suffix), and the CLI has no
``--profile`` axis.

Cache validity requires:

- Source PBF's SHA-256 still matches, AND
- Recorded ``valhalla_version`` matches this library's constant.
  Bumping ``VALHALLA_VERSION`` on a toolchain upgrade invalidates
  all tilesets automatically.

Requires the ``valhalla_build_config`` and ``valhalla_build_tiles``
binaries from the Valhalla distribution (``brew install valhalla``
on macOS).
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
OUTPUT_CACHE_TYPE = "valhalla"

# Valhalla toolchain version we build against. Bump when upgrading —
# cache entries tagged with an older version become stale automatically.
# (Tile binary format is tied to the producing toolchain.)
VALHALLA_VERSION = "3.5"

# Profiles Valhalla serves at query time. Not a build-time axis; kept
# here for CLI --help and FFL documentation.
QUERY_PROFILES: tuple[str, ...] = (
    "auto",
    "bicycle",
    "pedestrian",
    "truck",
    "motor_scooter",
    "motorcycle",
    "bus",
    "taxi",
)

DEFAULT_TIMEOUT_SECONDS = 3600

# In-process locks: one per region so concurrent same-region builds
# serialize, and a module-level manifest write lock.
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()
_manifest_write_lock = threading.Lock()


def _build_lock(region: str) -> threading.Lock:
    with _build_locks_guard:
        lock = _build_locks.get(region)
        if lock is None:
            lock = threading.Lock()
            _build_locks[region] = lock
        return lock


@dataclass
class BuildResult:
    """Outcome of a ``build_tiles`` call."""

    region: str
    tile_dir: str              # absolute path to the built tileset directory
    relative_path: str         # relative to valhalla/ cache dir
    total_size_bytes: int
    tile_count: int
    tile_levels: dict[str, int]   # {"0": count, "1": count, "2": count}
    valhalla_version: str
    generated_at: str
    duration_seconds: float
    was_cached: bool
    source_url: str
    source_pbf_path: str
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class BuildError(RuntimeError):
    """Raised when a tile build fails (binaries missing, valhalla error, etc.)."""


def pbf_rel_path(region: str) -> str:
    return f"{region}-latest.osm.pbf"


def pbf_abs_path(region: str) -> Path:
    return Path(cache_dir(SOURCE_CACHE_TYPE)) / pbf_rel_path(region)


def tileset_rel_path(region: str) -> str:
    """Manifest key — <region>-latest."""
    return f"{region}-latest"


def tileset_abs_path(region: str) -> Path:
    return Path(cache_dir(OUTPUT_CACHE_TYPE)) / tileset_rel_path(region)


def _staging_dir(region: str) -> Path:
    """Stage adjacent to the final destination. Override with
    ``AFL_OSM_CONVERT_STAGING=tmp`` for the legacy local-tmp behavior.
    """
    if (os.environ.get("AFL_OSM_CONVERT_STAGING") or "").lower() == "tmp":
        base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
        safe = region.replace("/", "_")
        return Path(base) / "facetwork-valhalla-staging" / safe
    out = tileset_abs_path(region)
    return out.with_name(out.name + ".staging")


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _count_tiles(tile_dir: Path) -> tuple[int, dict[str, int]]:
    """Count ``.gph`` tile files; return (total, per-level breakdown)."""
    total = 0
    per_level: dict[str, int] = {}
    if not tile_dir.exists():
        return 0, per_level
    for tile in tile_dir.rglob("*.gph"):
        total += 1
        # First path component after tile_dir is the hierarchy level (0/1/2).
        try:
            rel = tile.relative_to(tile_dir)
            level = rel.parts[0]
        except (ValueError, IndexError):
            continue
        per_level[level] = per_level.get(level, 0) + 1
    return total, per_level


def _tileset_exists(tile_dir: Path) -> bool:
    """A tileset exists if the directory contains at least one .gph file."""
    if not tile_dir.is_dir():
        return False
    for _ in tile_dir.rglob("*.gph"):
        return True
    return False


def is_up_to_date(region: str, pbf_entry: dict, tile_dir: Path) -> bool:
    """True if the cached tileset still matches PBF SHA and Valhalla version."""
    cache_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    rel = tileset_rel_path(region)
    existing = cache_manifest.get("entries", {}).get(rel)
    if not existing:
        return False
    if existing.get("source", {}).get("sha256") != pbf_entry.get("sha256"):
        return False
    if existing.get("valhalla_version") != VALHALLA_VERSION:
        return False
    if not _tileset_exists(tile_dir):
        return False
    return True


def _generate_config(
    tile_dir: Path, config_path: Path, config_bin: str
) -> None:
    """Produce a Valhalla config JSON pointing at ``tile_dir``."""
    try:
        result = subprocess.run(
            [config_bin, "--mjolnir-tile-dir", str(tile_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BuildError(
            f"{config_bin!r} not found on PATH. "
            "Install Valhalla (e.g. 'brew install valhalla')."
        ) from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "(no stderr)"
        raise BuildError(
            f"valhalla_build_config failed (exit {result.returncode}): {stderr}"
        )
    if not result.stdout:
        raise BuildError("valhalla_build_config produced no output")
    config_path.write_text(result.stdout)


def _run_build(
    pbf_path: Path,
    config_path: Path,
    tiles_bin: str,
    timeout_seconds: int,
) -> None:
    """Invoke ``valhalla_build_tiles``, raising BuildError on failure."""
    cmd = [tiles_bin, "-c", str(config_path), str(pbf_path)]
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
            f"valhalla_build_tiles timed out after {timeout_seconds}s "
            f"for {pbf_path.name}"
        ) from exc
    except FileNotFoundError as exc:
        raise BuildError(
            f"{tiles_bin!r} not found on PATH. "
            "Install Valhalla (e.g. 'brew install valhalla')."
        ) from exc
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").splitlines()[-20:]
        raise BuildError(
            f"valhalla_build_tiles failed (exit {result.returncode}): "
            f"{'; '.join(stderr_tail) or '(no stderr)'}"
        )


def build_tiles(
    region: str,
    *,
    force: bool = False,
    config_bin: str = "valhalla_build_config",
    tiles_bin: str = "valhalla_build_tiles",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> BuildResult:
    """Build a Valhalla tileset from a region's cached PBF.

    Thread-safe per region. Concurrent same-region calls serialize;
    different regions run independently.
    """
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

    with _build_lock(region):
        tile_dir = tileset_abs_path(region)
        rel = tileset_rel_path(region)

        if not force and is_up_to_date(region, pbf_entry, tile_dir):
            tile_count, levels = _count_tiles(tile_dir)
            existing = read_manifest(OUTPUT_CACHE_TYPE).get("entries", {}).get(rel, {})
            return BuildResult(
                region=region,
                tile_dir=str(tile_dir),
                relative_path=rel + "/",
                total_size_bytes=existing.get("total_size_bytes", _dir_size(tile_dir)),
                tile_count=tile_count,
                tile_levels=levels,
                valhalla_version=VALHALLA_VERSION,
                generated_at=existing.get("generated_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                source_url=source_url,
                source_pbf_path=str(src_pbf),
                manifest_entry=existing,
            )

        # Stage the build locally so the destination doesn't see a
        # partial tree if the subprocess crashes.
        staging = _staging_dir(region)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        staging_tiles = staging / "tiles"
        staging_tiles.mkdir(parents=True, exist_ok=True)
        config_path = staging / "valhalla.json"

        start = time.monotonic()
        try:
            _generate_config(staging_tiles, config_path, config_bin)
            _run_build(src_pbf, config_path, tiles_bin, timeout_seconds)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        elapsed = time.monotonic() - start

        tile_count, levels = _count_tiles(staging_tiles)
        if tile_count == 0:
            shutil.rmtree(staging, ignore_errors=True)
            raise BuildError(
                f"valhalla_build_tiles produced no tiles for {region}"
            )

        storage = LocalStorage()
        storage.finalize_dir_from_local(str(staging_tiles), str(tile_dir))
        # finalize_dir_from_local removes staging_tiles but leaves the
        # parent staging/ and the valhalla.json config behind.
        shutil.rmtree(staging, ignore_errors=True)

        total_size = _dir_size(tile_dir)
        generated_at = utcnow_iso()
        entry = {
            "relative_path": rel + "/",
            "region": region,
            "valhalla_version": VALHALLA_VERSION,
            "total_size_bytes": total_size,
            "tile_count": tile_count,
            "tile_levels": levels,
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
                "command": "valhalla_build_tiles",
                "config_bin": config_bin,
                "tiles_bin": tiles_bin,
            },
            "extra": {},
        }
        with _manifest_write_lock, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
            manifest.setdefault("entries", {})[rel] = entry

        return BuildResult(
            region=region,
            tile_dir=str(tile_dir),
            relative_path=rel + "/",
            total_size_bytes=total_size,
            tile_count=tile_count,
            tile_levels=levels,
            valhalla_version=VALHALLA_VERSION,
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            source_url=source_url,
            source_pbf_path=str(src_pbf),
            manifest_entry=entry,
        )


def clean_tiles(tile_dir: str) -> bool:
    """Remove a built tileset directory. Returns True if deleted."""
    p = Path(tile_dir)
    if p.exists():
        shutil.rmtree(p)
        return True
    return False


def to_valhalla_cache(result: BuildResult) -> dict[str, Any]:
    """Map a ``BuildResult`` to the ``ValhallaCache`` FFL schema dict."""
    return {
        "osmSource": result.source_pbf_path,
        "tileDir": result.tile_dir,
        "date": result.generated_at,
        "size": result.total_size_bytes,
        "wasInCache": result.was_cached,
        "version": result.valhalla_version,
        "tileCount": result.tile_count,
    }
