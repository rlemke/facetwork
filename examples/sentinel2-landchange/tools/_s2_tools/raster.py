"""Raster ops: per-scene index, median composite, change detection.

Internal representation is a NumPy array persisted as ``.npz`` (``data`` plus
``bounds`` lon/lat + ``crs``), so the per-scene → composite → change → render
chain is **shared** between the real and mock paths — only ``fetch_scene_index``
differs (real reads the COG bands via rio-tiler; mock synthesizes an array).

Cache layout (namespace ``s2``):
    scene-index/<aoi_key>/<index>/<scene_id>.npz      per-scene index
    composite/<aoi_key>/<index>/<date_from>_<date_to>.npz   epoch composite
    change/<aoi_key>/<method>.npz                     change raster + stats

A fixed output grid (``MAX_SIZE``) is used for every read of a given AOI so the
per-scene arrays stack cleanly and the two epoch composites align pixel-for-pixel.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np

from _s2_tools import s2_mocks, sidecar, stac

SCENE_INDEX = "scene-index"
COMPOSITE = "composite"
CHANGE = "change"

# (numerator_band, denominator_band) for a normalized-difference index.
_BANDS = {"ndvi": ("nir", "red"), "ndwi": ("green", "nir"), "ndbi": ("swir16", "nir")}

# Longest output edge for a real COG read (keeps tiles small + scenes aligned).
MAX_SIZE = 512


def aoi_key(aoi: str) -> str:
    """Filesystem-safe key for an AOI bbox string."""
    return aoi.replace(",", "_").replace("-", "m").replace(".", "p")


# ── persistence ────────────────────────────────────────────────────────────────


def _save(cache_type: str, rel: str, arr: np.ndarray, *, bounds, crs: str,
          source: str, tool: str) -> dict[str, Any]:
    buf = io.BytesIO()
    np.savez_compressed(buf, data=arr, bounds=np.asarray(bounds, dtype="float64"),
                        crs=np.array(crs))
    return sidecar.write(cache_type, rel, buf.getvalue(), source=source, tool=tool,
                         extras={"shape": list(arr.shape), "dtype": str(arr.dtype)})


def _load(cache_type: str, rel: str) -> tuple[np.ndarray, np.ndarray, str]:
    with open(sidecar.cache_path(cache_type, rel), "rb") as f:
        npz = np.load(io.BytesIO(f.read()), allow_pickle=False)
        return npz["data"], npz["bounds"], str(npz["crs"])


def _result(cache_type, rel, ak, index, scene_count, arr, meta, *, was_cached, used_mock):
    return {
        "cache_type": cache_type, "relative_path": rel, "aoi_key": ak, "index": index,
        "scene_count": int(scene_count), "width": int(arr.shape[1]), "height": int(arr.shape[0]),
        "size_bytes": meta["size_bytes"], "sha256": meta["sha256"],
        "was_cached": was_cached, "used_mock": used_mock,
    }


# ── per-scene index ──────────────────────────────────────────────────────────


def fetch_scene_index(
    scene_id: str, aoi: str, *, index: str = "ndvi", force: bool = False,
    use_mock: bool = False, collection: str = "sentinel-2-l2a",
    stac_url: str = "https://earth-search.aws.element84.com/v1",
) -> dict[str, Any]:
    if index not in _BANDS:
        raise ValueError(f"unknown index {index!r} (known: {sorted(_BANDS)})")
    ak = aoi_key(aoi)
    rel = f"{ak}/{index}/{scene_id}.npz"
    if not force and sidecar.exists(SCENE_INDEX, rel):
        arr, _b, _c = _load(SCENE_INDEX, rel)
        meta = sidecar.read_meta(SCENE_INDEX, rel)
        return _result(SCENE_INDEX, rel, ak, index, 1, arr, meta, was_cached=True, used_mock=False)

    bbox = stac.parse_bbox(aoi)
    if use_mock:
        arr = np.asarray(s2_mocks.mock_index_grid(scene_id, aoi, index), dtype="float32")
        crs, source = "EPSG:4326", f"mock://{scene_id}"
    else:
        arr, source = _fetch_real(scene_id, aoi, index, collection, stac_url)
        crs = "EPSG:4326"
    meta = _save(SCENE_INDEX, rel, arr, bounds=bbox, crs=crs, source=source, tool="fetch_scene_index")
    return _result(SCENE_INDEX, rel, ak, index, 1, arr, meta, was_cached=False, used_mock=use_mock)


def _fetch_real(scene_id, aoi, index, collection, stac_url):
    """Window-read the two index bands for the AOI and compute the index."""
    import os

    from rio_tiler.io import Reader  # rasterio-backed COG reader

    # Public Sentinel-2 COGs on AWS are anonymous-readable.
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

    num_band, den_band = _BANDS[index]
    assets = stac.get_item_assets(scene_id, collection=collection, stac_url=stac_url)
    bbox = list(stac.parse_bbox(aoi))

    def _read(band: str) -> np.ndarray:
        href = assets[band]
        with Reader(href) as r:
            img = r.part(bbox, bounds_crs="epsg:4326", dst_crs="epsg:4326", max_size=MAX_SIZE)
        return img.data[0].astype("float32")

    num = _read(num_band)
    den = _read(den_band)
    denom = num + den
    with np.errstate(divide="ignore", invalid="ignore"):
        idx = np.where(denom != 0, (num - den) / denom, 0.0).astype("float32")
    return idx, f"{stac_url}::{scene_id}::{num_band}-{den_band}"


# ── composite ────────────────────────────────────────────────────────────────


def composite(
    aoi: str, date_from: str, date_to: str, *, scene_ids: list[str] | None = None,
    index: str = "ndvi", reducer: str = "median", use_mock: bool = False
) -> dict[str, Any]:
    """Median/mean composite over the per-scene index rasters for ``scene_ids``.

    Scoping to the epoch's scene ids is what separates the baseline and recent
    composites — they share one AOI+index cache namespace, so without it both
    epochs would reduce over the same mixed set. ``scene_ids=None`` falls back to
    every cached scene for the AOI+index (single-epoch / CLI use)."""
    ak = aoi_key(aoi)
    if scene_ids:
        rels = [f"{ak}/{index}/{sid}.npz" for sid in scene_ids
                if sidecar.exists(SCENE_INDEX, f"{ak}/{index}/{sid}.npz")]
    else:
        rels = [r for r in sidecar.list_entries(SCENE_INDEX) if r.startswith(f"{ak}/{index}/")]
    if not rels:
        raise FileNotFoundError(
            f"no cached scene-index rasters for aoi={ak} index={index}; run ScanScenes first"
        )
    stack = np.stack([_load(SCENE_INDEX, r)[0] for r in rels], axis=0)
    arr = (np.median(stack, axis=0) if reducer == "median" else np.mean(stack, axis=0)).astype("float32")
    _arr0, bounds, crs = _load(SCENE_INDEX, rels[0])
    rel = f"{ak}/{index}/{date_from}_{date_to}.npz"
    meta = _save(COMPOSITE, rel, arr, bounds=bounds, crs=crs,
                 source=f"{reducer} of {len(rels)} scenes", tool="composite")
    return _result(COMPOSITE, rel, ak, index, len(rels), arr, meta, was_cached=False, used_mock=use_mock)


# ── change detection ───────────────────────────────────────────────────────────


# Land-cover classes for the `classify` method, as (name, NDVI upper bound).
# Monotonic by greenness, so class index up = greening (gain), down = loss.
NDVI_CLASS_BREAKS = (0.0, 0.2, 0.4)  # → 4 classes
NDVI_CLASS_NAMES = ("water", "built_bare", "sparse_veg", "dense_veg")


def detect_change(
    baseline_rel: str, recent_rel: str, aoi_key_str: str, *,
    method: str = "difference", threshold: float = 0.15, use_mock: bool = False
) -> dict[str, Any]:
    base, bounds, crs = _load(COMPOSITE, baseline_rel)
    recent, _b, _c = _load(COMPOSITE, recent_rel)
    if base.shape != recent.shape:
        raise ValueError(f"composite shapes differ {base.shape} vs {recent.shape}")

    if method == "classify":
        change, class_counts, source = _classify_change(base, recent)
    elif method == "difference":
        change, class_counts, source = _difference_change(base, recent, threshold)
    else:
        raise ValueError(f"unknown method {method!r} (expected 'difference' or 'classify')")

    loss = int((change == -1).sum())
    gain = int((change == 1).sum())
    total = int(change.size)
    rel = f"{aoi_key_str}/{method}.npz"
    meta = _save(CHANGE, rel, change, bounds=bounds, crs=crs, source=source, tool="detect_change")
    return {
        "relative_path": rel, "aoi_key": aoi_key_str, "method": method,
        "changed_pixels": loss + gain, "total_pixels": total,
        "pct_loss": round(100.0 * loss / total, 2), "pct_gain": round(100.0 * gain / total, 2),
        "class_counts": class_counts,
        "size_bytes": meta["size_bytes"], "sha256": meta["sha256"],
    }


def _difference_change(base, recent, threshold):
    """Index delta thresholded into loss(-1)/stable(0)/gain(+1)."""
    delta = recent - base
    change = np.zeros(base.shape, dtype="int8")
    change[delta <= -threshold] = -1
    change[delta >= threshold] = 1
    loss = int((change == -1).sum())
    gain = int((change == 1).sum())
    counts = {"loss": loss, "gain": gain, "stable": int(change.size) - loss - gain}
    return change, counts, f"difference(threshold={threshold})"


def _classify_change(base, recent):
    """Bin each epoch into land-cover classes, then report the per-pixel
    class transition. The change raster is the sign of the class shift
    (greening = +1 gain, browning = -1 loss); ``class_counts`` carries the
    per-class histograms and the from→to transition matrix.

    This is an interpretable threshold classifier over a vegetation index; a
    drop-in upgrade is a trained random-forest over the full spectral stack
    (would require caching multiple bands per scene + a fitted model).
    """
    base_cls = np.digitize(base, NDVI_CLASS_BREAKS).astype("int8")     # 0..3
    recent_cls = np.digitize(recent, NDVI_CLASS_BREAKS).astype("int8")  # 0..3
    shift = recent_cls.astype("int16") - base_cls.astype("int16")
    change = np.zeros(base.shape, dtype="int8")
    change[shift < 0] = -1
    change[shift > 0] = 1

    def _hist(cls):
        return {NDVI_CLASS_NAMES[i]: int((cls == i).sum()) for i in range(len(NDVI_CLASS_NAMES))}

    transitions: dict[str, int] = {}
    for i in range(len(NDVI_CLASS_NAMES)):
        for j in range(len(NDVI_CLASS_NAMES)):
            if i == j:
                continue
            n = int(((base_cls == i) & (recent_cls == j)).sum())
            if n:
                transitions[f"{NDVI_CLASS_NAMES[i]}->{NDVI_CLASS_NAMES[j]}"] = n

    loss = int((change == -1).sum())
    gain = int((change == 1).sum())
    counts = {
        "loss": loss, "gain": gain, "stable": int(change.size) - loss - gain,
        "baseline": _hist(base_cls), "recent": _hist(recent_cls),
        "transitions": transitions,
    }
    return change, counts, "classify(ndvi-classes)"


def load_change_grid(change_rel: str) -> dict[str, Any]:
    """Load a change raster as a plain {width,height,data} dict for rendering."""
    arr, _b, _c = _load(CHANGE, change_rel)
    return {"width": int(arr.shape[1]), "height": int(arr.shape[0]),
            "data": arr.astype(int).tolist()}
