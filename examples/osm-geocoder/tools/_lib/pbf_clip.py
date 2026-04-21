"""Clip a cached Geofabrik PBF to a bbox or polygon via ``osmium extract``.

Produces a new, smaller PBF that lands in the **same** ``pbf/`` cache
the download tool writes to — just under a ``clips/`` subdirectory.
That means every downstream tool (``convert-pbf-geojson``,
``convert-pbf-shapefile``, ``extract``, ``build-graphhopper-graph``,
``build-valhalla-tiles``, etc.) treats a clip as a regular region
called ``clips/<name>`` with zero code changes.

Cache validity for a clip requires:

- Source PBF's SHA-256 still matches what the clip's entry recorded, AND
- The clip spec (bbox or polygon content) still matches.

Bumping either triggers a re-clip. The output manifest entry is
written into ``pbf/manifest.json`` alongside the Geofabrik-sourced
entries and follows the same shape (``relative_path``, ``sha256``,
``size_bytes``, ``downloaded_at``), plus a ``clip`` sub-object with the
source-region pointer, clip spec, and filter-version. Downstream
tools that only consume the standard fields work unchanged; tools
that want to know "is this a clip?" check for the ``clip`` key.
"""

from __future__ import annotations

import hashlib
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

CACHE_TYPE = "pbf"
CLIP_VERSION = 1
CHUNK_SIZE = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 1800

_clip_locks: dict[str, threading.Lock] = {}
_clip_locks_guard = threading.Lock()
_manifest_write_lock = threading.Lock()


def _clip_lock(name: str) -> threading.Lock:
    with _clip_locks_guard:
        lock = _clip_locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _clip_locks[name] = lock
        return lock


@dataclass
class ClipSpec:
    """How a clip is defined."""

    kind: str                   # "bbox" or "polygon"
    bbox: tuple[float, float, float, float] | None = None  # (west, south, east, north)
    polygon_path: str | None = None
    polygon_sha256: str | None = None   # content hash of the polygon file

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.bbox is not None:
            d["bbox"] = list(self.bbox)
        if self.polygon_path is not None:
            d["polygon_path"] = self.polygon_path
            d["polygon_sha256"] = self.polygon_sha256
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ClipSpec":
        return cls(
            kind=d.get("kind", ""),
            bbox=tuple(d["bbox"]) if d.get("bbox") else None,  # type: ignore[arg-type]
            polygon_path=d.get("polygon_path"),
            polygon_sha256=d.get("polygon_sha256"),
        )

    def matches(self, other: dict[str, Any]) -> bool:
        """True if ``other`` (a manifest ``clip`` sub-dict) describes the same clip."""
        if other.get("kind") != self.kind:
            return False
        if self.kind == "bbox":
            got = other.get("bbox")
            return list(self.bbox) == got if self.bbox else False
        if self.kind == "polygon":
            return other.get("polygon_sha256") == self.polygon_sha256
        return False


@dataclass
class ClipResult:
    """Outcome of a ``clip_pbf`` call."""

    name: str                   # user-supplied clip name
    region: str                 # clips/<name> — usable as a region key downstream
    path: str                   # absolute path to the clipped PBF
    relative_path: str          # relative to pbf/ cache dir
    source_region: str
    source_pbf_sha256: str
    clip: ClipSpec
    size_bytes: int
    sha256: str
    generated_at: str
    duration_seconds: float
    was_cached: bool
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class ClipError(RuntimeError):
    """Raised when clipping fails (missing source, invalid spec, osmium error)."""


def clip_rel_path(name: str) -> str:
    """Relative path within ``pbf/`` for a clip named ``name``."""
    return f"clips/{name}-latest.osm.pbf"


def clip_abs_path(name: str) -> Path:
    return Path(cache_dir(CACHE_TYPE)) / clip_rel_path(name)


def clip_region_key(name: str) -> str:
    """Region key downstream tools use (e.g. ``regions_from_pbf_manifest``)."""
    return f"clips/{name}"


def _source_pbf_path(region: str) -> Path:
    return Path(cache_dir(CACHE_TYPE)) / f"{region}-latest.osm.pbf"


def _staging_dir(name: str) -> Path:
    base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
    safe = name.replace("/", "_")
    return Path(base) / "facetwork-pbf-clip-staging" / safe


def _sha256_file(path: Path) -> tuple[int, str]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            size += len(chunk)
    return size, sha.hexdigest()


def _sha256_file_only(path: Path) -> str:
    _, h = _sha256_file(path)
    return h


def _osmium_version(osmium_bin: str) -> str:
    try:
        result = subprocess.run(
            [osmium_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (result.stdout or "").splitlines()
        return first_line[0].strip() if first_line else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    if len(bbox) != 4:
        raise ClipError("bbox must be (west, south, east, north)")
    w, s, e, n = bbox
    if not (-180.0 <= w < e <= 180.0):
        raise ClipError(f"bbox longitudes out of range: west={w} east={e}")
    if not (-90.0 <= s < n <= 90.0):
        raise ClipError(f"bbox latitudes out of range: south={s} north={n}")


def build_spec(
    *,
    bbox: tuple[float, float, float, float] | None = None,
    polygon_path: str | None = None,
) -> ClipSpec:
    """Validate and normalize user-supplied clip parameters into a ClipSpec."""
    if bbox is not None and polygon_path is not None:
        raise ClipError("pass either bbox or polygon_path, not both")
    if bbox is not None:
        _validate_bbox(bbox)
        return ClipSpec(kind="bbox", bbox=bbox)
    if polygon_path is not None:
        p = Path(polygon_path)
        if not p.is_file():
            raise ClipError(f"polygon file not found: {polygon_path}")
        return ClipSpec(
            kind="polygon",
            polygon_path=str(p.resolve()),
            polygon_sha256=_sha256_file_only(p),
        )
    raise ClipError("must supply one of bbox or polygon_path")


def is_up_to_date(
    name: str,
    source_region: str,
    source_pbf_sha256: str,
    spec: ClipSpec,
) -> bool:
    """True if the cached clip still matches source SHA and clip spec."""
    cache_manifest = read_manifest(CACHE_TYPE)
    rel = clip_rel_path(name)
    existing = cache_manifest.get("entries", {}).get(rel)
    if not existing:
        return False
    clip_info = existing.get("clip") or {}
    if clip_info.get("source_region") != source_region:
        return False
    if clip_info.get("source_sha256") != source_pbf_sha256:
        return False
    if not spec.matches(clip_info):
        return False
    out_abs = clip_abs_path(name)
    if not out_abs.exists():
        return False
    return out_abs.stat().st_size == existing.get("size_bytes")


def clip_pbf(
    name: str,
    source_region: str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    polygon_path: str | None = None,
    force: bool = False,
    osmium_bin: str = "osmium",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ClipResult:
    """Clip a cached source PBF to a bbox or polygon.

    The output is written into the regular ``pbf/`` cache at
    ``pbf/clips/<name>-latest.osm.pbf`` so every downstream tool can
    treat it as a normal region called ``clips/<name>``.
    """
    if not name or "/" in name:
        raise ClipError(
            f"clip name must not be empty or contain '/'. got: {name!r}"
        )

    spec = build_spec(bbox=bbox, polygon_path=polygon_path)

    pbf_manifest = read_manifest(CACHE_TYPE)
    source_rel = f"{source_region}-latest.osm.pbf"
    source_entry = pbf_manifest.get("entries", {}).get(source_rel)
    if not source_entry:
        raise ClipError(
            f"no pbf manifest entry for source region {source_region!r}. "
            "Run download-pbf first."
        )
    source_pbf = _source_pbf_path(source_region)
    if not source_pbf.exists():
        raise ClipError(f"source pbf missing on disk: {source_pbf}")
    source_sha = source_entry.get("sha256", "")

    with _clip_lock(name):
        out_abs = clip_abs_path(name)
        out_rel = clip_rel_path(name)

        if not force and is_up_to_date(name, source_region, source_sha, spec):
            existing = read_manifest(CACHE_TYPE).get("entries", {}).get(out_rel, {})
            return ClipResult(
                name=name,
                region=clip_region_key(name),
                path=str(out_abs),
                relative_path=out_rel,
                source_region=source_region,
                source_pbf_sha256=source_sha,
                clip=spec,
                size_bytes=existing.get("size_bytes", out_abs.stat().st_size),
                sha256=existing.get("sha256", ""),
                generated_at=existing.get("downloaded_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                manifest_entry=existing,
            )

        staging = _staging_dir(name)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        staged_pbf = staging / f"{name}-latest.osm.pbf"

        cmd = [osmium_bin, "extract", "--overwrite", "-o", str(staged_pbf)]
        if spec.kind == "bbox":
            assert spec.bbox is not None
            cmd += ["--bbox", ",".join(f"{x}" for x in spec.bbox)]
        elif spec.kind == "polygon":
            assert spec.polygon_path is not None
            cmd += ["--polygon", spec.polygon_path]
        cmd += [str(source_pbf)]

        start = time.monotonic()
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            stderr = (exc.stderr or "").strip()
            raise ClipError(f"osmium extract failed: {stderr or exc}") from exc
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ClipError(
                f"osmium extract timed out after {timeout_seconds}s"
            ) from exc
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        elapsed = time.monotonic() - start

        size, sha256_hex = _sha256_file(staged_pbf)

        storage = LocalStorage()
        storage.finalize_from_local(str(staged_pbf), str(out_abs))
        shutil.rmtree(staging, ignore_errors=True)

        generated_at = utcnow_iso()
        entry = {
            "relative_path": out_rel,
            "source_url": "",
            "size_bytes": size,
            "sha256": sha256_hex,
            "source_checksum": None,
            "downloaded_at": generated_at,
            "source_timestamp": None,
            "clip": {
                **spec.to_dict(),
                "source_region": source_region,
                "source_sha256": source_sha,
                "version": CLIP_VERSION,
            },
            "tool": {
                "command": "osmium extract",
                "osmium_version": _osmium_version(osmium_bin),
            },
            "extra": {
                "region": clip_region_key(name),
                "duration_seconds": round(elapsed, 2),
            },
        }
        with _manifest_write_lock, manifest_transaction(CACHE_TYPE) as manifest:
            manifest.setdefault("entries", {})[out_rel] = entry

        return ClipResult(
            name=name,
            region=clip_region_key(name),
            path=str(out_abs),
            relative_path=out_rel,
            source_region=source_region,
            source_pbf_sha256=source_sha,
            clip=spec,
            size_bytes=size,
            sha256=sha256_hex,
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            manifest_entry=entry,
        )


def list_clips() -> list[dict[str, Any]]:
    """Return every clip currently recorded in the pbf manifest."""
    manifest = read_manifest(CACHE_TYPE)
    out: list[dict[str, Any]] = []
    for rel, entry in manifest.get("entries", {}).items():
        if not rel.startswith("clips/"):
            continue
        if not isinstance(entry, dict) or "clip" not in entry:
            continue
        out.append(entry)
    out.sort(key=lambda e: e.get("relative_path", ""))
    return out
