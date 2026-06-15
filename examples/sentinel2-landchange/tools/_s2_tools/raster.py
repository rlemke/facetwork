"""Raster ops: per-scene index, median composite, change detection.

Cache layout (namespace ``s2``):
    scene-index/<aoi_key>/<index>/<scene_id>.json     per-scene index grid
    composite/<aoi_key>/<index>/<date_from>_<date_to>.json   epoch composite
    change/<aoi_key>/<method>.json                    change raster + stats

The mock path represents a raster as a small JSON ``{width,height,data}`` grid
so the whole chain (index → composite → change) runs offline and computes real
(if synthetic) statistics. The real path is stubbed.

TODO (real path): use rasterio + rio-tiler (or GDAL) to window-read the COG
bands for the AOI, compute the index, write a Cloud-Optimized GeoTIFF, and
median-composite / difference across the cached epoch rasters with numpy. Keep
the same cache keys and return shapes so handlers/FFL don't change.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from _s2_tools import s2_mocks, sidecar

SCENE_INDEX = "scene-index"
COMPOSITE = "composite"
CHANGE = "change"

_BANDS = {"ndvi": ("nir", "red"), "ndwi": ("green", "nir"), "ndbi": ("swir16", "nir")}


def aoi_key(aoi: str) -> str:
    """Filesystem-safe key for an AOI bbox string."""
    return aoi.replace(",", "_").replace("-", "m").replace(".", "p")


def _grid_bytes(grid: list[list[float]]) -> bytes:
    return json.dumps({"width": len(grid[0]), "height": len(grid), "data": grid}).encode()


def _load_grid(cache_type: str, rel: str) -> list[list[float]]:
    abs_path = sidecar.cache_path(cache_type, rel)
    with open(abs_path, encoding="utf-8") as f:
        return json.load(f)["data"]


# ---------------------------------------------------------------------------


def fetch_scene_index(
    scene_id: str, aoi: str, *, index: str = "ndvi", force: bool = False, use_mock: bool = False
) -> dict[str, Any]:
    if index not in _BANDS:
        raise ValueError(f"unknown index {index!r} (known: {sorted(_BANDS)})")
    ak = aoi_key(aoi)
    rel = f"{ak}/{index}/{scene_id}.json"
    if not force and sidecar.exists(SCENE_INDEX, rel):
        meta = sidecar.read_meta(SCENE_INDEX, rel)
        return _result(SCENE_INDEX, rel, ak, index, 1, meta, was_cached=True, used_mock=False)

    if not use_mock:
        _fetch_real(scene_id, aoi, index)  # raises NotImplementedError

    grid = s2_mocks.mock_index_grid(scene_id, aoi, index)
    meta = sidecar.write(SCENE_INDEX, rel, _grid_bytes(grid),
                         source=f"mock://{scene_id}", tool="fetch_scene_index")
    return _result(SCENE_INDEX, rel, ak, index, 1, meta, was_cached=False, used_mock=use_mock)


def composite(
    aoi: str, date_from: str, date_to: str, *, index: str = "ndvi",
    reducer: str = "median", use_mock: bool = False
) -> dict[str, Any]:
    """Reduce every cached per-scene index grid for this AOI+index into one."""
    ak = aoi_key(aoi)
    scene_rels = [r for r in sidecar.list_entries(SCENE_INDEX) if r.startswith(f"{ak}/{index}/")]
    if not scene_rels:
        raise FileNotFoundError(
            f"no cached scene-index rasters for aoi={ak} index={index}; run ScanScenes first"
        )
    grids = [_load_grid(SCENE_INDEX, r) for r in scene_rels]
    h, w = len(grids[0]), len(grids[0][0])
    reduce_fn = statistics.median if reducer == "median" else statistics.mean
    out = [[round(reduce_fn([g[y][x] for g in grids]), 4) for x in range(w)] for y in range(h)]
    rel = f"{ak}/{index}/{date_from}_{date_to}.json"
    meta = sidecar.write(COMPOSITE, rel, _grid_bytes(out),
                         source=f"composite({reducer}) of {len(grids)} scenes", tool="composite")
    return _result(COMPOSITE, rel, ak, index, len(grids), meta, was_cached=False, used_mock=use_mock)


def detect_change(
    baseline_rel: str, recent_rel: str, aoi_key_str: str, *,
    method: str = "difference", threshold: float = 0.15, use_mock: bool = False
) -> dict[str, Any]:
    """Index-difference + threshold → loss/gain counts and a change grid."""
    base = _load_grid(COMPOSITE, baseline_rel)
    recent = _load_grid(COMPOSITE, recent_rel)
    h, w = len(base), len(base[0])
    change_grid: list[list[int]] = []
    loss = gain = 0
    for y in range(h):
        row = []
        for x in range(w):
            d = recent[y][x] - base[y][x]
            if d <= -threshold:
                row.append(-1); loss += 1
            elif d >= threshold:
                row.append(1); gain += 1
            else:
                row.append(0)
        change_grid.append(row)
    total = w * h
    rel = f"{aoi_key_str}/{method}.json"
    payload = json.dumps({"width": w, "height": h, "data": change_grid}).encode()
    meta = sidecar.write(CHANGE, rel, payload,
                         source=f"{method}(threshold={threshold})", tool="detect_change")
    return {
        "relative_path": rel, "aoi_key": aoi_key_str, "method": method,
        "changed_pixels": loss + gain, "total_pixels": total,
        "pct_loss": round(100.0 * loss / total, 2), "pct_gain": round(100.0 * gain / total, 2),
        "class_counts": {"loss": loss, "gain": gain, "stable": total - loss - gain},
        "size_bytes": meta["size_bytes"], "sha256": meta["sha256"],
    }


# ---------------------------------------------------------------------------


def _result(cache_type, rel, ak, index, scene_count, meta, *, was_cached, used_mock):
    g = json.loads(open(sidecar.cache_path(cache_type, rel), encoding="utf-8").read())
    return {
        "cache_type": cache_type, "relative_path": rel, "aoi_key": ak, "index": index,
        "scene_count": scene_count, "width": g["width"], "height": g["height"],
        "size_bytes": meta["size_bytes"], "sha256": meta["sha256"],
        "was_cached": was_cached, "used_mock": used_mock,
    }


def _fetch_real(scene_id, aoi, index):  # pragma: no cover
    raise NotImplementedError(
        "Real COG window-read + index not implemented yet. See module TODO. "
        "Dependencies: rasterio + rio-tiler (or GDAL) + numpy. Run with use_mock=true for now."
    )
