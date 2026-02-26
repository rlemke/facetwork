"""HTTP download utilities for US Census Bureau data.

Downloads ACS data via the Census Bureau REST API and TIGER/Line
shapefiles with per-path locking and filesystem caching.
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
_CACHE_DIR = os.environ.get("AFL_CENSUS_CACHE_DIR",
                            os.path.join(_LOCAL_OUTPUT, "census-cache"))

# Per-path locks to prevent duplicate concurrent downloads
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()

CENSUS_API_BASE = "https://api.census.gov/data"
TIGER_BASE = "https://www2.census.gov/geo/tiger"

# TIGER geo_level -> directory and file suffix mapping
_TIGER_GEO = {
    "COUNTY": ("COUNTY", "county"),
    "TRACT": ("TRACT", "tract"),
    "BG": ("BG", "bg"),
    "PLACE": ("PLACE", "place"),
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


def download_acs(year: str = "2023", period: str = "5-Year",
                 state_fips: str = "01",
                 columns: str = "B01003_001E,B19013_001E,B25001_001E,"
                                "B15003_001E,B15003_022E,B15003_023E,B15003_024E,B15003_025E,"
                                "B08301_001E,B08301_003E,B08301_010E,B08301_019E,B08301_021E,"
                                "B25003_001E,B25003_002E,B25003_003E,"
                                "B11001_001E,B11001_002E,B11001_003E,"
                                "B11001_004E,B11001_005E,B11001_006E,"
                                "B11001_007E,B11001_008E,B11001_009E,"
                                "B25044_001E,B25044_002E,B25044_003E,"
                                "B25044_004E,B25044_005E,B25044_006E,"
                                "B25044_007E,B25044_008E,B25044_009E,"
                                "B25044_010E,B25044_011E,B25044_012E,"
                                "B25044_013E,B25044_014E,B25044_015E",
                 tag: str = "default") -> dict[str, Any]:
    """Download ACS data for a state via the Census Bureau REST API.

    The API returns JSON: [[header...], [row1...], ...].
    We write a CSV with columns: GEOID, NAME, plus requested columns.

    Args:
        year: ACS survey year.
        period: Survey period.
        state_fips: Two-digit state FIPS code.
        columns: Comma-separated list of ACS estimate columns.
        tag: Cache filename tag for differentiating downloads.

    Returns a CensusFile dict with url, path, date, size, wasInCache.
    """
    filename = f"acs_{year}_{state_fips}_{tag}.csv"
    dest = os.path.join(_CACHE_DIR, "acs", year, filename)
    url = (f"{CENSUS_API_BASE}/{year}/acs/acs5"
           f"?get=NAME,{columns}&for=county:*&in=state:{state_fips}")

    requested_cols = {c.strip() for c in columns.split(",")}

    lock = _get_lock(dest)
    with lock:
        was_cached = os.path.exists(dest)
        if was_cached:
            # Validate cache has all requested columns
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

    # Find column indices
    name_idx = header.index("NAME")
    state_idx = header.index("state")
    county_idx = header.index("county")
    # Data columns are everything except NAME, state, county
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
    For TRACT, BG, PLACE, downloads the per-state file.

    Returns a CensusFile dict with url, path, date, size, wasInCache.
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
    }
