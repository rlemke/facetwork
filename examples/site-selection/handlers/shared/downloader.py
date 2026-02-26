"""HTTP download utilities for site-selection data.

Downloads ACS data via the Census Bureau REST API, TIGER/Line
shapefiles, and Geofabrik PBF files with per-path locking and
filesystem caching.
"""

import csv
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)

_LOCAL_OUTPUT = os.environ.get("AFL_LOCAL_OUTPUT_DIR", "/tmp")
_CACHE_DIR = os.environ.get("AFL_SITESEL_CACHE_DIR",
                            os.path.join(_LOCAL_OUTPUT, "sitesel-cache"))

# Per-path locks to prevent duplicate concurrent downloads
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()

CENSUS_API_BASE = "https://api.census.gov/data"
TIGER_BASE = "https://www2.census.gov/geo/tiger"
GEOFABRIK_BASE = "https://download.geofabrik.de/north-america/us"

# ACS columns needed for site-selection scoring:
# B01003 = population, B19013 = median income, B17001 = poverty,
# B23025 = employment, B02001 = race, B25003 = housing tenure,
# B15003 = education
DEFAULT_ACS_COLUMNS = (
    "B01003_001E,B19013_001E,"
    "B17001_001E,B17001_002E,"
    "B23025_001E,B23025_002E,B23025_003E,B23025_005E,"
    "B02001_001E,B02001_002E,"
    "B25003_001E,B25003_002E,"
    "B15003_001E,B15003_022E,B15003_023E,B15003_024E,B15003_025E"
)

# TIGER geo_level -> directory and file suffix mapping
_TIGER_GEO = {
    "COUNTY": ("COUNTY", "county"),
    "TRACT": ("TRACT", "tract"),
}

# TIGER geo_levels that use a national file (us) instead of per-state
_TIGER_NATIONAL_GEO = {"COUNTY"}


def _get_lock(path: str) -> threading.Lock:
    """Get or create a per-path lock."""
    with _locks_lock:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def _download_file(url: str, dest: str) -> int:
    """Download a URL to a local path, returning file size in bytes."""
    if not HAS_REQUESTS:
        raise RuntimeError("requests library required for downloads")

    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s -> %s", url, dest)
    start = time.monotonic()

    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()

    size = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            size += len(chunk)

    elapsed = time.monotonic() - start
    logger.info("Downloaded %s (%d bytes, %.1fs)", dest, size, elapsed)
    return size


def download_acs(year: str = "2023", state_fips: str = "01",
                 columns: str = DEFAULT_ACS_COLUMNS) -> dict[str, Any]:
    """Download ACS data for a state via the Census Bureau REST API.

    The API returns JSON: [[header...], [row1...], ...].
    We write a CSV with columns: GEOID, NAME, plus requested columns.

    Returns a dict with url, path, date, size, wasInCache, state_fips.
    """
    filename = f"acs_{year}_{state_fips}_sitesel.csv"
    dest = os.path.join(_CACHE_DIR, "acs", year, filename)
    url = (f"{CENSUS_API_BASE}/{year}/acs/acs5"
           f"?get=NAME,{columns}&for=county:*&in=state:{state_fips}")

    requested_cols = {c.strip() for c in columns.split(",")}

    lock = _get_lock(dest)
    with lock:
        was_cached = os.path.exists(dest)
        if was_cached:
            try:
                with open(dest, newline="") as f:
                    header = next(csv.reader(f))
                if not requested_cols.issubset(set(header)):
                    logger.info("ACS cache stale (missing columns): %s", dest)
                    was_cached = False
            except (OSError, StopIteration):
                was_cached = False

        if was_cached:
            size = os.path.getsize(dest)
            logger.info("ACS cache hit: %s (%d bytes)", dest, size)
        else:
            size = _download_acs_api(url, dest, state_fips)

    return {
        "url": url,
        "path": dest,
        "date": datetime.now(UTC).isoformat(),
        "size": size,
        "wasInCache": was_cached,
        "state_fips": state_fips,
    }


def _download_acs_api(url: str, dest: str, state_fips: str) -> int:
    """Fetch ACS data from Census API and write as CSV."""
    if not HAS_REQUESTS:
        raise RuntimeError("requests library required for downloads")

    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    logger.info("Fetching Census API: %s", url)
    start = time.monotonic()

    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    if not data or len(data) < 2:
        raise RuntimeError(f"Census API returned no data for state {state_fips}")

    header = data[0]
    rows = data[1:]

    name_idx = header.index("NAME")
    state_idx = header.index("state")
    county_idx = header.index("county")
    skip = {"NAME", "state", "county"}
    data_cols = [c for c in header if c not in skip]

    with open(dest, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["GEOID", "NAME"] + data_cols)
        for row in rows:
            st = row[state_idx]
            cty = row[county_idx]
            geoid = f"0500000US{st}{cty}"
            name = row[name_idx]
            values = [row[header.index(c)] for c in data_cols]
            writer.writerow([geoid, name] + values)

    size = os.path.getsize(dest)
    elapsed = time.monotonic() - start
    logger.info("Fetched ACS API -> %s (%d rows, %d bytes, %.1fs)",
                dest, len(rows), size, elapsed)
    return size


def download_tiger(year: str = "2024", geo_level: str = "COUNTY",
                   state_fips: str = "01") -> dict[str, Any]:
    """Download TIGER/Line shapefile for a state and geography level.

    For COUNTY, downloads the national file (tl_{year}_us_county.zip).
    For TRACT, downloads the per-state file.

    Returns a dict with url, path, date, size, wasInCache, state_fips, geo_level.
    """
    geo_upper = geo_level.upper()
    if geo_upper not in _TIGER_GEO:
        raise ValueError(f"Unsupported geo_level: {geo_level}. "
                         f"Supported: {list(_TIGER_GEO.keys())}")

    tiger_dir, tiger_suffix = _TIGER_GEO[geo_upper]

    if geo_upper in _TIGER_NATIONAL_GEO:
        filename = f"tl_{year}_us_{tiger_suffix}.zip"
    else:
        filename = f"tl_{year}_{state_fips}_{tiger_suffix}.zip"

    url = f"{TIGER_BASE}/TIGER{year}/{tiger_dir}/{filename}"
    dest = os.path.join(_CACHE_DIR, "tiger", year, filename)

    lock = _get_lock(dest)
    with lock:
        was_cached = os.path.exists(dest)
        if was_cached:
            size = os.path.getsize(dest)
            logger.info("TIGER cache hit: %s (%d bytes)", dest, size)
        else:
            size = _download_file(url, dest)

    return {
        "url": url,
        "path": dest,
        "date": datetime.now(UTC).isoformat(),
        "size": size,
        "wasInCache": was_cached,
        "state_fips": state_fips,
        "geo_level": geo_upper,
    }


def download_pbf(region: str = "alabama") -> dict[str, Any]:
    """Download Geofabrik state PBF file.

    URL pattern: https://download.geofabrik.de/north-america/us/{region}-latest.osm.pbf
    Caches to {cache_dir}/pbf/{region}-latest.osm.pbf

    Returns a dict with url, path, date, size, wasInCache, region.
    """
    filename = f"{region}-latest.osm.pbf"
    dest = os.path.join(_CACHE_DIR, "pbf", filename)
    url = f"{GEOFABRIK_BASE}/{region}-latest.osm.pbf"

    lock = _get_lock(dest)
    with lock:
        was_cached = os.path.exists(dest)
        if was_cached:
            size = os.path.getsize(dest)
            logger.info("PBF cache hit: %s (%d bytes)", dest, size)
        else:
            size = _download_file(url, dest)

    return {
        "url": url,
        "path": dest,
        "date": datetime.now(UTC).isoformat(),
        "size": size,
        "wasInCache": was_cached,
        "region": region,
    }
