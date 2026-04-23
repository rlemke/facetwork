"""OpenLitterMap downloader — geotagged litter observations.

OpenLitterMap (https://openlittermap.com) is a crowd-sourced,
CC-BY-SA licensed database of geotagged litter photos. This module
fetches the public GeoJSON feed, caches it with a sidecar, and
normalizes the feature properties so downstream tools see a stable
shape regardless of upstream field renames.

Cache layout::

    cache/save-earth/openlittermap/points.geojson        + .meta.json

Because the upstream API evolves, the URL is overridable — both via
constructor argument and via the ``--url`` flag on the CLI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TOOLS_ROOT = Path(__file__).resolve().parent.parent
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from _lib import sidecar  # noqa: E402
from _lib.storage import LocalStorage, Storage, local_staging_subdir  # noqa: E402

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

logger = logging.getLogger("save-earth.openlittermap")

NAMESPACE = "save-earth"
CACHE_TYPE = "openlittermap"
RELATIVE_PATH = "points.geojson"

# Public global-clusters endpoint. If this is stale when you run, pass
# ``--url`` with the current endpoint from openlittermap.com/data.
DEFAULT_URL = "https://openlittermap.com/global.geojson"
USER_AGENT = "facetwork-save-earth/1.0 (+https://github.com/rlemke/facetwork)"
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 180
DEFAULT_MAX_AGE_HOURS = 24.0

_lock = threading.Lock()


@dataclass
class FetchResult:
    absolute_path: str
    relative_path: str
    size_bytes: int
    sha256: str
    feature_count: int
    source_url: str
    was_cached: bool
    generated_at: str
    used_mock: bool = False


def download(
    *,
    url: str = DEFAULT_URL,
    force: bool = False,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    bbox: tuple[float, float, float, float] | None = None,
    storage: Storage | None = None,
    use_mock: bool = False,
) -> FetchResult:
    """Fetch the OpenLitterMap GeoJSON and cache it.

    ``bbox``, if set, is ``(min_lat, max_lat, min_lon, max_lon)`` and
    trims the cached feature set to that window (so re-running the
    downloader with different bboxes gives different cached files —
    but we still write to the same cache entry. Callers who need
    multiple bboxes should direct-call with distinct output paths).
    """
    s = storage or LocalStorage()
    art_path = sidecar.cache_path(NAMESPACE, CACHE_TYPE, RELATIVE_PATH, s)

    with _lock:
        if not force:
            side = sidecar.read_sidecar(NAMESPACE, CACHE_TYPE, RELATIVE_PATH, s)
            if side and sidecar.exists_and_valid(
                NAMESPACE, CACHE_TYPE, RELATIVE_PATH, s
            ):
                age = _age_hours(side.get("generated_at"))
                if age is None or age < max_age_hours:
                    logger.info("openlittermap cache hit (%.1fh old)", age or -1.0)
                    return FetchResult(
                        absolute_path=art_path,
                        relative_path=RELATIVE_PATH,
                        size_bytes=side.get("size_bytes", 0),
                        sha256=side.get("sha256", ""),
                        feature_count=int(side.get("extra", {}).get("feature_count", 0)),
                        source_url=url,
                        was_cached=True,
                        generated_at=side.get("generated_at", ""),
                    )

        if use_mock:
            body_bytes = json.dumps(_mock_geojson()).encode("utf-8")
            return _persist(body_bytes, url=url, storage=s, used_mock=True, bbox=bbox)

        if requests is None:
            raise RuntimeError(
                "requests library is not installed. Install it, run via the .sh "
                "wrapper (activates .venv), or pass --use-mock."
            )

        logger.info("downloading %s", url)
        resp = requests.get(
            url,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        data = _normalize(data, bbox=bbox)
        body_bytes = json.dumps(data).encode("utf-8")
        return _persist(body_bytes, url=url, storage=s, used_mock=False, bbox=bbox)


def _persist(
    body: bytes, *, url: str, storage: Storage, used_mock: bool, bbox
) -> FetchResult:
    staging_dir = local_staging_subdir(f"{NAMESPACE}/{CACHE_TYPE}")
    os.makedirs(staging_dir, exist_ok=True)
    stage_path = os.path.join(staging_dir, f"{RELATIVE_PATH}.stage-{os.getpid()}")
    with open(stage_path, "wb") as f:
        f.write(body)
    digest = hashlib.sha256(body).hexdigest()

    # Count features without re-parsing the whole blob twice.
    try:
        parsed = json.loads(body)
        feature_count = len(parsed.get("features") or [])
    except Exception:
        feature_count = 0

    final_path = sidecar.cache_path(NAMESPACE, CACHE_TYPE, RELATIVE_PATH, storage)
    with sidecar.entry_lock(NAMESPACE, CACHE_TYPE, RELATIVE_PATH, storage=storage):
        storage.finalize_from_local(stage_path, final_path)
        side = sidecar.write_sidecar(
            NAMESPACE,
            CACHE_TYPE,
            RELATIVE_PATH,
            kind="file",
            size_bytes=len(body),
            sha256=digest,
            source={"publisher": "OpenLitterMap", "url": url, "used_mock": used_mock},
            tool={"name": "openlittermap", "version": "1.0"},
            extra={
                "feature_count": feature_count,
                "bbox": list(bbox) if bbox else None,
            },
            storage=storage,
        )
    return FetchResult(
        absolute_path=final_path,
        relative_path=RELATIVE_PATH,
        size_bytes=len(body),
        sha256=digest,
        feature_count=feature_count,
        source_url=url,
        was_cached=False,
        generated_at=side["generated_at"],
        used_mock=used_mock,
    )


def _normalize(
    data: Any, *, bbox: tuple[float, float, float, float] | None
) -> dict[str, Any]:
    """Coerce whatever the upstream returned into a stable FeatureCollection.

    OpenLitterMap's API sometimes returns cluster summaries, sometimes
    raw points. We keep the shape as a FeatureCollection with
    ``properties`` preserved verbatim so popups can surface whatever
    upstream provided.
    """
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        fc = data
    elif isinstance(data, list):
        # Assume list of Feature objects.
        fc = {"type": "FeatureCollection", "features": data}
    elif isinstance(data, dict) and isinstance(data.get("features"), list):
        fc = {"type": "FeatureCollection", "features": data["features"]}
    else:
        raise ValueError(
            "OpenLitterMap response is not a recognizable GeoJSON shape"
        )

    features = fc.get("features") or []
    if bbox is not None:
        min_lat, max_lat, min_lon, max_lon = bbox
        features = [
            f
            for f in features
            if _point_in_bbox(f, min_lat, max_lat, min_lon, max_lon)
        ]

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def _point_in_bbox(feature, min_lat, max_lat, min_lon, max_lon) -> bool:
    geom = (feature or {}).get("geometry") or {}
    coords = geom.get("coordinates") or []
    if geom.get("type") != "Point" or len(coords) < 2:
        # Non-point features pass through — bbox is meant to trim points.
        return True
    lon, lat = coords[0], coords[1]
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _age_hours(generated_at: str | None) -> float | None:
    if not generated_at:
        return None
    from datetime import datetime, timezone

    try:
        ts = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def _mock_geojson() -> dict[str, Any]:
    """Small hand-crafted feature collection for offline tests."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-73.9857, 40.7484]},
                "properties": {
                    "id": 1,
                    "city": "New York",
                    "state": "NY",
                    "country": "USA",
                    "datetime": "2026-04-10T12:30:00Z",
                    "tags": ["plastic", "bottle"],
                    "description": "Plastic bottle on sidewalk near Empire State Building",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-122.4194, 37.7749]},
                "properties": {
                    "id": 2,
                    "city": "San Francisco",
                    "state": "CA",
                    "country": "USA",
                    "datetime": "2026-04-12T09:15:00Z",
                    "tags": ["cigarette", "butt"],
                    "description": "Cigarette butts accumulated at bus stop",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-87.6298, 41.8781]},
                "properties": {
                    "id": 3,
                    "city": "Chicago",
                    "state": "IL",
                    "country": "USA",
                    "datetime": "2026-04-14T16:45:00Z",
                    "tags": ["packaging", "food"],
                    "description": "Fast-food packaging in park",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [2.3522, 48.8566]},
                "properties": {
                    "id": 4,
                    "city": "Paris",
                    "state": "Île-de-France",
                    "country": "France",
                    "datetime": "2026-04-11T14:00:00Z",
                    "tags": ["plastic", "bag"],
                    "description": "Plastic bag caught in Seine embankment",
                },
            },
        ],
    }
