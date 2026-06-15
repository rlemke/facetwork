"""Deterministic offline fixtures for ``use_mock=True``.

No network, no GDAL/rasterio. Scenes and per-scene index grids are derived
deterministically from the AOI + date window + scene id, so a mock run computes
a real (if synthetic) composite/change and is reproducible in tests. Real
imagery flows through the same function signatures in ``stac`` / ``raster``.
"""

from __future__ import annotations

import hashlib

# Small synthetic raster size for the mock (keeps fixtures tiny).
MOCK_W = 16
MOCK_H = 16


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def mock_scene_ids(aoi: str, date_from: str, date_to: str, max_cloud: float) -> list[str]:
    """A stable handful of scene ids for the window (3-6 scenes)."""
    n = 3 + (_seed(aoi, date_from, date_to) % 4)
    return [f"S2_MOCK_{date_from}_{date_to}_{i:02d}" for i in range(n)]


def mock_index_grid(scene_id: str, aoi: str, index: str) -> list[list[float]]:
    """A deterministic WxH grid of index values in [-1, 1]."""
    base = _seed(scene_id, aoi, index)
    grid: list[list[float]] = []
    for y in range(MOCK_H):
        row = []
        for x in range(MOCK_W):
            cell = _seed(scene_id, index, str(x), str(y))
            v = ((base + x * 7 + y * 13 + cell % 97) % 200) / 100.0 - 1.0
            row.append(round(v, 4))
        grid.append(row)
    return grid
