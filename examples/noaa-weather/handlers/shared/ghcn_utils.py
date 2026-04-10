"""Shared utility functions for GHCN-Daily (AWS S3) weather pipeline.

GHCN-Daily data is hosted on AWS S3 at:
    https://noaa-ghcn-pds.s3.amazonaws.com/

Station IDs use FIPS country codes as prefix (first 2 chars):
    US, CA, GM=Germany, UK, FR, RS=Russia, IN=India, etc.

DATA_VALUE units: temperature in tenths of degrees C, precipitation in
tenths of mm.  Q_FLAG set means observation FAILED quality control.

All functions are pure and testable.  Mock fallbacks use hashlib for
deterministic outputs when ``requests`` is unavailable.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

from facetwork.config import get_output_base

logger = logging.getLogger("ghcn")

_LOCAL_OUTPUT = get_output_base()
_GHCN_CACHE_DIR = os.path.join(_LOCAL_OUTPUT, "ghcn-cache")
_GEOCODE_CACHE_DIR = os.path.join(_LOCAL_OUTPUT, "weather-geocode-cache")

GHCN_S3_BASE = "https://noaa-ghcn-pds.s3.amazonaws.com/"

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ---------------------------------------------------------------------------
# Per-path download locks
# ---------------------------------------------------------------------------

_download_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    """Get or create a per-path download lock."""
    with _lock_guard:
        if path not in _download_locks:
            _download_locks[path] = threading.Lock()
        return _download_locks[path]


# ---------------------------------------------------------------------------
# Hash helpers (for deterministic mocks)
# ---------------------------------------------------------------------------


def _hash_int(seed: str, lo: int, hi: int) -> int:
    """Deterministic integer from a seed string."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % (hi - lo))


def _hash_float(seed: str, lo: float, hi: float) -> float:
    """Deterministic float from a seed string."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % 10000) / 10000 * (hi - lo)


# ---------------------------------------------------------------------------
# US state bounding boxes (approximate lat/lon)
# ---------------------------------------------------------------------------

_US_STATE_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    # state: (min_lat, max_lat, min_lon, max_lon)
    "AL": (30.22, 35.01, -88.47, -84.89),
    "AK": (51.21, 71.39, -179.15, -129.98),
    "AZ": (31.33, 37.00, -114.81, -109.04),
    "AR": (33.00, 36.50, -94.62, -89.64),
    "CA": (32.53, 42.01, -124.41, -114.13),
    "CO": (36.99, 41.00, -109.06, -102.04),
    "CT": (40.98, 42.05, -73.73, -71.79),
    "DE": (38.45, 39.84, -75.79, -75.05),
    "FL": (24.40, 31.00, -87.63, -80.03),
    "GA": (30.36, 35.00, -85.61, -80.84),
    "HI": (18.91, 22.24, -160.25, -154.81),
    "ID": (41.99, 49.00, -117.24, -111.04),
    "IL": (36.97, 42.51, -91.51, -87.02),
    "IN": (37.77, 41.76, -88.10, -84.78),
    "IA": (40.38, 43.50, -96.64, -90.14),
    "KS": (36.99, 40.00, -102.05, -94.59),
    "KY": (36.50, 39.15, -89.57, -81.96),
    "LA": (28.93, 33.02, -94.04, -88.82),
    "ME": (43.06, 47.46, -71.08, -66.95),
    "MD": (37.91, 39.72, -79.49, -75.05),
    "MA": (41.24, 42.89, -73.51, -69.93),
    "MI": (41.70, 48.26, -90.42, -82.41),
    "MN": (43.50, 49.38, -97.24, -89.49),
    "MS": (30.17, 34.99, -91.66, -88.10),
    "MO": (35.99, 40.61, -95.77, -89.10),
    "MT": (44.36, 49.00, -116.05, -104.04),
    "NE": (39.99, 43.00, -104.05, -95.31),
    "NV": (35.00, 42.00, -120.01, -114.04),
    "NH": (42.70, 45.31, -72.56, -70.70),
    "NJ": (38.93, 41.36, -75.56, -73.89),
    "NM": (31.33, 37.00, -109.05, -103.00),
    "NY": (40.50, 45.02, -79.76, -71.86),
    "NC": (33.84, 36.59, -84.32, -75.46),
    "ND": (45.94, 49.00, -104.05, -96.55),
    "OH": (38.40, 41.98, -84.82, -80.52),
    "OK": (33.62, 37.00, -103.00, -94.43),
    "OR": (41.99, 46.29, -124.57, -116.46),
    "PA": (39.72, 42.27, -80.52, -74.69),
    "RI": (41.15, 42.02, -71.86, -71.12),
    "SC": (32.05, 35.22, -83.35, -78.54),
    "SD": (42.48, 45.94, -104.06, -96.44),
    "TN": (34.98, 36.68, -90.31, -81.65),
    "TX": (25.84, 36.50, -106.65, -93.51),
    "UT": (36.99, 42.00, -114.05, -109.04),
    "VT": (42.73, 45.02, -73.44, -71.46),
    "VA": (36.54, 39.47, -83.68, -75.24),
    "WA": (45.54, 49.00, -124.85, -116.92),
    "WV": (37.20, 40.64, -82.64, -77.72),
    "WI": (42.49, 47.08, -92.89, -86.25),
    "WY": (40.99, 45.01, -111.06, -104.05),
}


# ---------------------------------------------------------------------------
# Country / state filtering
# ---------------------------------------------------------------------------


def station_country(station_id: str) -> str:
    """Return the FIPS country code from a GHCN station ID (first 2 chars)."""
    return station_id[:2] if len(station_id) >= 2 else ""


def station_in_state(lat: float, lon: float, state: str) -> bool:
    """Check if coordinates fall within a US state bounding box.

    *state* should be a 2-letter uppercase state abbreviation (e.g. ``"NY"``).
    Returns ``False`` if the state is not in ``_US_STATE_BOUNDS``.
    """
    bounds = _US_STATE_BOUNDS.get(state.upper())
    if bounds is None:
        return False
    min_lat, max_lat, min_lon, max_lon = bounds
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


# ---------------------------------------------------------------------------
# Station catalog download and parsing
# ---------------------------------------------------------------------------


def download_station_catalog(
    cache_dir: str | None = None,
    max_age_hours: float = 24.0,
) -> str:
    """Download ghcnd-stations.txt from GHCN S3, returning the raw text.

    Uses a file cache at *cache_dir*.  Skips re-download if the cached file
    is less than *max_age_hours* old.  Returns mock data if ``requests`` is
    unavailable.
    """
    if cache_dir is None:
        cache_dir = _GHCN_CACHE_DIR
    cache_path = os.path.join(cache_dir, "ghcnd-stations.txt")

    lock = _get_lock(cache_path)
    with lock:
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            age_s = time.time() - os.path.getmtime(cache_path)
            if age_s < max_age_hours * 3600:
                logger.info(
                    "Station catalog cache hit (%s, %.1fh old)",
                    cache_path,
                    age_s / 3600,
                )
                with open(cache_path) as f:
                    return f.read()
            logger.info(
                "Station catalog cache stale (%.1fh > %.1fh), re-downloading",
                age_s / 3600,
                max_age_hours,
            )

        if HAS_REQUESTS:
            url = GHCN_S3_BASE + "ghcnd-stations.txt"
            logger.info("Downloading station catalog from %s", url)
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    text = resp.text
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(cache_path, "w") as f:
                        f.write(text)
                    logger.info(
                        "Station catalog downloaded: %d bytes, %d lines",
                        len(text),
                        text.count("\n"),
                    )
                    return text
                logger.warning("Station catalog download failed: HTTP %d", resp.status_code)
            except Exception as exc:
                logger.warning("Station catalog download error: %s", exc)

    return _mock_station_catalog()


def download_inventory(
    cache_dir: str | None = None,
    max_age_hours: float = 24.0,
) -> str:
    """Download ghcnd-inventory.txt from GHCN S3, returning the raw text.

    Uses a file cache at *cache_dir*.  Skips re-download if the cached file
    is less than *max_age_hours* old.  Returns mock data if ``requests`` is
    unavailable.
    """
    if cache_dir is None:
        cache_dir = _GHCN_CACHE_DIR
    cache_path = os.path.join(cache_dir, "ghcnd-inventory.txt")

    lock = _get_lock(cache_path)
    with lock:
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            age_s = time.time() - os.path.getmtime(cache_path)
            if age_s < max_age_hours * 3600:
                logger.info(
                    "Inventory cache hit (%s, %.1fh old)",
                    cache_path,
                    age_s / 3600,
                )
                with open(cache_path) as f:
                    return f.read()
            logger.info(
                "Inventory cache stale (%.1fh > %.1fh), re-downloading",
                age_s / 3600,
                max_age_hours,
            )

        if HAS_REQUESTS:
            url = GHCN_S3_BASE + "ghcnd-inventory.txt"
            logger.info("Downloading inventory from %s", url)
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    text = resp.text
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(cache_path, "w") as f:
                        f.write(text)
                    logger.info(
                        "Inventory downloaded: %d bytes, %d lines",
                        len(text),
                        text.count("\n"),
                    )
                    return text
                logger.warning("Inventory download failed: HTTP %d", resp.status_code)
            except Exception as exc:
                logger.warning("Inventory download error: %s", exc)

    return _mock_inventory()


def parse_stations(text: str) -> list[dict[str, Any]]:
    """Parse ghcnd-stations.txt fixed-width format into a list of dicts.

    Fixed-width layout::

        ID            1-11   Character
        LATITUDE     13-20   Real
        LONGITUDE    22-30   Real
        ELEVATION    32-37   Real
        NAME         42-..   Character

    Returns list of dicts with keys: station_id, name, lat, lon, elevation.
    """
    stations: list[dict[str, Any]] = []
    for line in text.splitlines():
        if len(line) < 38:
            continue
        try:
            station_id = line[0:11].strip()
            lat = float(line[12:20].strip())
            lon = float(line[21:30].strip())
            elev_str = line[31:37].strip()
            elevation = float(elev_str) if elev_str else 0.0
            name = line[41:].strip() if len(line) > 41 else ""
        except (ValueError, IndexError):
            continue
        stations.append(
            {
                "station_id": station_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "elevation": elevation,
            }
        )
    return stations


def parse_inventory(text: str) -> dict[str, dict[str, Any]]:
    """Parse ghcnd-inventory.txt into a dict keyed by station_id.

    Fixed-width layout::

        ID            1-11   Character
        LATITUDE     13-20   Real
        LONGITUDE    22-30   Real
        ELEMENT      32-35   Character
        FIRSTYEAR    37-40   Integer
        LASTYEAR     42-45   Integer

    Returns ``{station_id: {elements: set, first_year: int, last_year: int,
    element_ranges: {element: (start, end)}}}``.
    """
    inventory: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if len(line) < 45:
            continue
        try:
            station_id = line[0:11].strip()
            element = line[31:35].strip()
            first_year = int(line[36:40].strip())
            last_year = int(line[41:45].strip())
        except (ValueError, IndexError):
            continue

        if station_id not in inventory:
            inventory[station_id] = {
                "elements": set(),
                "first_year": first_year,
                "last_year": last_year,
                "element_ranges": {},
            }

        entry = inventory[station_id]
        entry["elements"].add(element)
        entry["element_ranges"][element] = (first_year, last_year)
        if first_year < entry["first_year"]:
            entry["first_year"] = first_year
        if last_year > entry["last_year"]:
            entry["last_year"] = last_year

    return inventory


def filter_stations(
    stations: list[dict[str, Any]],
    inventory: dict[str, dict[str, Any]],
    country: str = "US",
    state: str = "",
    max_stations: int = 10,
    min_years: int = 20,
    required_elements: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter stations by country, state, data coverage, and required elements.

    Parameters
    ----------
    stations : list[dict]
        Output of :func:`parse_stations`.
    inventory : dict[str, dict]
        Output of :func:`parse_inventory`.
    country : str
        FIPS country code prefix to match (e.g. ``"US"``).
    state : str
        US state abbreviation (e.g. ``"NY"``).  Checked via bounding box.
        Ignored if empty.
    max_stations : int
        Maximum number of stations to return.
    min_years : int
        Minimum number of years of data required.
    required_elements : list[str] | None
        Elements that must be present (default ``["TMAX", "TMIN", "PRCP"]``).

    Returns
    -------
    list[dict]
        Enriched station dicts with ``first_year``, ``last_year``, and
        ``elements`` fields added.  Sorted by data coverage (most years
        first).
    """
    if required_elements is None:
        required_elements = ["TMAX", "TMIN", "PRCP"]

    candidates: list[dict[str, Any]] = []

    for stn in stations:
        sid = stn["station_id"]

        # Country filter
        if station_country(sid) != country:
            continue

        # State filter (lat/lon bounding box)
        if state and not station_in_state(stn["lat"], stn["lon"], state):
            continue

        # Inventory check
        inv = inventory.get(sid)
        if inv is None:
            continue

        # Required elements
        if not all(el in inv["elements"] for el in required_elements):
            continue

        # Minimum years
        year_span = inv["last_year"] - inv["first_year"] + 1
        if year_span < min_years:
            continue

        enriched = {
            **stn,
            "first_year": inv["first_year"],
            "last_year": inv["last_year"],
            "elements": sorted(inv["elements"]),
        }
        candidates.append(enriched)

    # Sort by coverage (most years first), then by station_id for stability
    candidates.sort(key=lambda s: (-(s["last_year"] - s["first_year"]), s["station_id"]))

    return candidates[:max_stations]


# ---------------------------------------------------------------------------
# Data download and parsing
# ---------------------------------------------------------------------------


def download_station_csv(
    station_id: str,
    cache_dir: str | None = None,
) -> str:
    """Download ``csv/by_station/{station_id}.csv`` from GHCN S3.

    Returns the local file path (cached by station_id).  Falls back to a
    mock CSV if ``requests`` is unavailable.
    """
    if cache_dir is None:
        cache_dir = os.path.join(_GHCN_CACHE_DIR, "by_station")
    filename = f"{station_id}.csv"
    local_path = os.path.join(cache_dir, filename)

    lock = _get_lock(local_path)
    with lock:
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            size = os.path.getsize(local_path)
            logger.info("Cache hit %s (%s bytes)", station_id, f"{size:,}")
            return local_path

        if HAS_REQUESTS:
            url = f"{GHCN_S3_BASE}csv/by_station/{station_id}.csv"
            logger.info("Downloading %s from %s", station_id, url)
            t0 = time.monotonic()
            try:
                resp = requests.get(url, timeout=60, stream=True)
                elapsed = time.monotonic() - t0
                if resp.status_code == 200:
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(local_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            f.write(chunk)
                    size = os.path.getsize(local_path)
                    logger.info(
                        "Download complete %s: %s bytes in %.1fs",
                        station_id,
                        f"{size:,}",
                        elapsed,
                    )
                    return local_path
                logger.warning(
                    "Download failed %s: HTTP %d in %.1fs",
                    station_id,
                    resp.status_code,
                    elapsed,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                logger.warning("Download error %s: %s in %.1fs", station_id, exc, elapsed)

    if HAS_REQUESTS:
        raise RuntimeError(
            f"Failed to download GHCN CSV for {station_id} from "
            f"{GHCN_S3_BASE}csv/by_station/{station_id}.csv"
        )

    # Mock fallback (no requests installed)
    logger.info("Using mock data for %s (requests not installed)", station_id)
    os.makedirs(cache_dir, exist_ok=True)
    mock_text = _mock_station_csv(station_id, 2000, 2023)
    with open(local_path, "w") as f:
        f.write(mock_text)
    return local_path


def parse_ghcn_csv(
    path: str,
    start_year: int,
    end_year: int,
    skip_flagged: bool = True,
) -> list[dict[str, Any]]:
    """Parse a GHCN-Daily CSV file and pivot to wide daily format.

    GHCN CSV columns: ``ID,DATE,ELEMENT,DATA_VALUE,M_FLAG,Q_FLAG,S_FLAG,OBS_TIME``

    The long format (one row per element per day) is pivoted to wide format
    (one dict per day with ``tmax``, ``tmin``, ``prcp``, ``snow``, ``snwd``
    fields).

    Parameters
    ----------
    path : str
        Local path to the CSV file.
    start_year : int
        First year to include (inclusive).
    end_year : int
        Last year to include (inclusive).
    skip_flagged : bool
        If ``True``, skip rows where Q_FLAG is non-empty (observation failed
        quality control).

    Returns
    -------
    list[dict]
        Daily dicts sorted by date with keys: ``date``, ``tmax``, ``tmin``,
        ``prcp``, ``snow``, ``snwd``.  Temperature values are in degrees C,
        precipitation/snow values in mm.
    """
    # Collect raw element values keyed by date
    element_map = {"TMAX", "TMIN", "PRCP", "SNOW", "SNWD"}
    by_date: dict[str, dict[str, float | None]] = {}

    with open(path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 6:
                continue
            # Columns: ID, DATE, ELEMENT, DATA_VALUE, M_FLAG, Q_FLAG, ...
            try:
                date_str = row[1]
                element = row[2]
                data_value_str = row[3]
                q_flag = row[5] if len(row) > 5 else ""
            except IndexError:
                continue

            # Skip header row if present
            if date_str == "DATE":
                continue

            # Year filter
            if len(date_str) < 4:
                continue
            try:
                year = int(date_str[:4])
            except ValueError:
                continue
            if year < start_year or year > end_year:
                continue

            # Skip quality-flagged observations
            if skip_flagged and q_flag.strip():
                continue

            if element not in element_map:
                continue

            try:
                raw_value = int(data_value_str)
            except (ValueError, TypeError):
                continue

            # Convert from tenths to real units
            value = raw_value / 10.0

            if date_str not in by_date:
                by_date[date_str] = {}
            by_date[date_str][element] = value

    # Build wide-format daily records
    daily: list[dict[str, Any]] = []
    for date_str in sorted(by_date):
        vals = by_date[date_str]
        daily.append(
            {
                "date": date_str,
                "tmax": vals.get("TMAX"),
                "tmin": vals.get("TMIN"),
                "prcp": vals.get("PRCP"),
                "snow": vals.get("SNOW"),
                "snwd": vals.get("SNWD"),
            }
        )

    return daily


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def compute_yearly_summaries(
    daily_data: list[dict[str, Any]],
    station_id: str,
    state: str,
) -> list[dict[str, Any]]:
    """Group daily data by year and compute annual climate summaries.

    Parameters
    ----------
    daily_data : list[dict]
        Output of :func:`parse_ghcn_csv`.
    station_id : str
        Station identifier (included in output for reference).
    state : str
        State abbreviation (included in output for reference).

    Returns
    -------
    list[dict]
        Yearly summary dicts with keys: ``year``, ``station_id``, ``state``,
        ``temp_mean``, ``temp_min_avg``, ``temp_max_avg``, ``precip_annual``,
        ``hot_days``, ``frost_days``, ``precip_days``, ``obs_days``.
    """
    by_year: dict[int, list[dict[str, Any]]] = {}
    for d in daily_data:
        date_str = d.get("date", "")
        if len(date_str) < 4:
            continue
        try:
            year = int(date_str[:4])
        except ValueError:
            continue
        by_year.setdefault(year, []).append(d)

    summaries: list[dict[str, Any]] = []
    for year in sorted(by_year):
        days = by_year[year]
        tmaxs = [d["tmax"] for d in days if d.get("tmax") is not None]
        tmins = [d["tmin"] for d in days if d.get("tmin") is not None]
        prcps = [d["prcp"] for d in days if d.get("prcp") is not None]

        # Mean of daily means (average of tmax and tmin per day)
        daily_means: list[float] = []
        for d in days:
            if d.get("tmax") is not None and d.get("tmin") is not None:
                daily_means.append((d["tmax"] + d["tmin"]) / 2.0)

        temp_mean = round(sum(daily_means) / len(daily_means), 2) if daily_means else None
        temp_min_avg = round(sum(tmins) / len(tmins), 2) if tmins else None
        temp_max_avg = round(sum(tmaxs) / len(tmaxs), 2) if tmaxs else None
        precip_annual = round(sum(prcps), 1) if prcps else 0.0

        hot_days = sum(1 for t in tmaxs if t > 35.0)
        frost_days = sum(1 for t in tmins if t < 0.0)
        precip_days = sum(1 for p in prcps if p > 0.0)

        summaries.append(
            {
                "year": year,
                "station_id": station_id,
                "state": state,
                "temp_mean": temp_mean,
                "temp_min_avg": temp_min_avg,
                "temp_max_avg": temp_max_avg,
                "precip_annual": precip_annual,
                "hot_days": hot_days,
                "frost_days": frost_days,
                "precip_days": precip_days,
                "obs_days": len(days),
            }
        )

    return summaries


def simple_linear_regression(
    xs: list[float],
    ys: list[float],
) -> tuple[float, float]:
    """Ordinary least-squares regression.  Returns ``(slope, intercept)``.

    With fewer than 2 points, returns ``(0.0, ys[0])`` or ``(0.0, 0.0)``.
    """
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, ys[0]

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0, sum_y / n

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


# ---------------------------------------------------------------------------
# MongoDB helpers — ClimateStore, WeatherReportStore, get_weather_db
# ---------------------------------------------------------------------------


def get_weather_db(db: Any = None) -> Any:
    """Return a MongoDB database handle for weather report storage.

    If *db* is already provided (e.g. for testing), return it as-is.
    Otherwise connect via ``AFL_MONGODB_URL`` / ``AFL_EXAMPLES_DATABASE``.

    Example data is stored in a separate database (default ``afl_examples``)
    so that ``db.dropDatabase()`` on the FFL runtime database does not
    destroy cached weather reports and climate trends.
    """
    if db is not None:
        return db
    from pymongo import MongoClient

    url = os.environ.get("AFL_MONGODB_URL")
    if not url:
        raise RuntimeError(
            "AFL_MONGODB_URL is not set — cannot connect to MongoDB for weather reports"
        )
    db_name = os.environ.get("AFL_EXAMPLES_DATABASE", "facetwork_examples")
    return MongoClient(url)[db_name]


class WeatherReportStore:
    """Lightweight wrapper around two MongoDB collections for weather outputs."""

    def __init__(self, db: Any) -> None:
        self.reports = db["weather_reports"]
        self.batches = db["weather_batch_summaries"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self.reports.create_index([("station_id", 1), ("year", 1)], unique=True)
        self.reports.create_index([("updated_at", -1)])
        self.batches.create_index([("batch_id", 1)], unique=True)

    def upsert_report(
        self,
        station_id: str,
        station_name: str,
        year: int,
        location: str,
        report: dict[str, Any],
        daily_stats: list[dict[str, Any]],
    ) -> str:
        """Upsert the core report fields into *weather_reports*."""
        now = datetime.datetime.now(datetime.UTC)
        self.reports.update_one(
            {"station_id": station_id, "year": year},
            {
                "$set": {
                    "station_name": station_name,
                    "location": location,
                    "report": report,
                    "daily_stats": daily_stats,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return f"weather://{station_id}/{year}"

    def upsert_html(self, station_id: str, year: int, html_content: str) -> str:
        """Set *html_content* on the report document."""
        now = datetime.datetime.now(datetime.UTC)
        self.reports.update_one(
            {"station_id": station_id, "year": year},
            {
                "$set": {"html_content": html_content, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return f"weather://{station_id}/{year}"

    def upsert_map(self, station_id: str, year: int, map_content: str) -> str:
        """Set *map_content* on the report document."""
        now = datetime.datetime.now(datetime.UTC)
        self.reports.update_one(
            {"station_id": station_id, "year": year},
            {
                "$set": {"map_content": map_content, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return f"weather://{station_id}/{year}"

    def upsert_batch(
        self,
        batch_id: str,
        station_count: int,
        completed: int,
        failed: int,
        results: list[dict[str, Any]],
        summary: str,
    ) -> str:
        """Upsert a batch summary document."""
        now = datetime.datetime.now(datetime.UTC)
        self.batches.update_one(
            {"batch_id": batch_id},
            {
                "$set": {
                    "station_count": station_count,
                    "completed": completed,
                    "failed": failed,
                    "results": results,
                    "summary": summary,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return f"weather://batch/{batch_id}"

    def get_report(self, station_id: str, year: int) -> dict[str, Any] | None:
        """Retrieve a single report document."""
        return self.reports.find_one({"station_id": station_id, "year": year}, {"_id": 0})

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent reports, newest first."""
        return list(self.reports.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit))


class ClimateStore:
    """MongoDB wrapper for climate_state_years and climate_trends collections."""

    def __init__(self, db: Any) -> None:
        self.state_years = db["climate_state_years"]
        self.trends = db["climate_trends"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self.state_years.create_index([("state", 1), ("year", 1)], unique=True)
        self.trends.create_index([("state", 1)], unique=True)

    def upsert_state_year(self, data: dict[str, Any]) -> None:
        """Upsert a yearly climate summary."""
        now = datetime.datetime.now(datetime.UTC)
        self.state_years.update_one(
            {"state": data["state"], "year": data["year"]},
            {"$set": {**data, "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    def upsert_trend(self, data: dict[str, Any]) -> None:
        """Upsert a climate trend document."""
        now = datetime.datetime.now(datetime.UTC)
        self.trends.update_one(
            {"state": data["state"]},
            {"$set": {**data, "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    def get_state_years(
        self, state: str, start_year: int = 0, end_year: int = 9999
    ) -> list[dict[str, Any]]:
        """Query yearly climate data for a state within a year range."""
        return list(
            self.state_years.find(
                {"state": state, "year": {"$gte": start_year, "$lte": end_year}},
                {"_id": 0},
            ).sort("year", 1)
        )

    def get_trend(self, state: str) -> dict[str, Any] | None:
        """Retrieve the trend document for a state."""
        return self.trends.find_one({"state": state}, {"_id": 0})

    def list_states(self) -> list[str]:
        """Return distinct state codes that have trend data."""
        return sorted(self.trends.distinct("state"))

    def get_narrative(self, state: str) -> str | None:
        """Retrieve the narrative from the trend document."""
        doc = self.trends.find_one({"state": state}, {"_id": 0, "narrative": 1})
        if doc:
            return doc.get("narrative")
        return None


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


def reverse_geocode_nominatim(lat: float, lon: float) -> dict[str, Any]:
    """Reverse geocode via OSM Nominatim with filesystem cache and rate limiting.

    Falls back to hash-based mock if requests is unavailable or the API fails.
    """
    cache_key = f"{lat:.4f}_{lon:.4f}"
    cache_path = os.path.join(_GEOCODE_CACHE_DIR, f"{cache_key}.json")

    # Check cache first
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    if HAS_REQUESTS:
        try:
            url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
            resp = requests.get(url, headers={"User-Agent": "Facetwork/0.38"}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                addr = data.get("address", {})
                result = {
                    "display_name": data.get("display_name", ""),
                    "city": addr.get("city", addr.get("town", addr.get("village", ""))),
                    "state": addr.get("state", ""),
                    "country": addr.get("country", ""),
                    "county": addr.get("county", ""),
                }
                os.makedirs(_GEOCODE_CACHE_DIR, exist_ok=True)
                with open(cache_path, "w") as f:
                    json.dump(result, f)
                time.sleep(1)  # Rate limit: 1 req/sec
                return result
        except Exception:
            pass

    # Hash-based mock fallback
    seed = f"geo:{lat}:{lon}"
    return {
        "display_name": f"Location at {lat:.2f}, {lon:.2f}",
        "city": f"City-{_hash_int(seed + ':city', 1000, 9999)}",
        "state": f"State-{_hash_int(seed + ':state', 10, 99)}",
        "country": "US",
        "county": f"County-{_hash_int(seed + ':county', 100, 999)}",
    }


# ---------------------------------------------------------------------------
# Mock fallbacks
# ---------------------------------------------------------------------------


def _mock_station_catalog() -> str:
    """Generate a small mock ghcnd-stations.txt for testing.

    Fixed-width format: ID(0:11) lat(12:20) lon(21:30) elev(31:37) name(41:).
    """
    lines = [
        "USW00094728  40.7789  -73.9692    39.6    NEW YORK CENTRAL PARK OBS",
        "USW00014732  40.7794  -73.8803     3.4    LA GUARDIA AIRPORT",
        "USW00014734  40.6833  -74.1694     2.1    NEWARK LIBERTY INTL AP",
        "USW00012839  25.7906  -80.3164     8.8    MIAMI INTL AP",
        "USW00094846  41.9950  -87.9336   201.8    CHICAGO OHARE INTL AP",
        "USW00023234  32.8983  -97.0192   170.7    DALLAS FT WORTH INTL AP",
        "USW00024233  47.4489  -122.3094   132.6    SEATTLE TACOMA INTL AP",
        "USW00023174  33.9381  -118.3894    29.6    LOS ANGELES INTL AP",
        "USW00023174  37.6197  -122.3647     2.4    SAN FRANCISCO INTL AP",
        "USW00014922  44.8831  -93.2289   255.1    MINNEAPOLIS ST PAUL INTL AP",
        "USW00013874  33.6301  -84.4419   315.2    ATLANTA HARTSFIELD INTL AP",
        "USW00014739  42.3606  -71.0106     9.1    BOSTON LOGAN INTL AP",
        # International
        "CA006158731  43.6772  -79.6306   173.4    TORONTO PEARSON INTL",
        "GME00127786  50.0500    8.6000   112.0    FRANKFURT MAIN",
        "UK000056225  51.4780   -0.4610    25.0    LONDON HEATHROW",
        "FR000007157  49.0128    2.5494   119.0    PARIS CHARLES DE GAULLE",
        "RSM00027612  55.9722   37.4153   167.0    MOSCOW SHEREMETYEVO",
        "IN022021600  28.5850   77.2060   216.0    NEW DELHI SAFDARJUNG",
    ]
    return "\n".join(lines) + "\n"


def _mock_inventory() -> str:
    """Generate a small mock ghcnd-inventory.txt for testing.

    Fixed-width format: ID(0:11) lat(12:20) lon(21:30) element(31:35)
    firstyear(36:40) lastyear(41:45).
    """
    stations = [
        ("USW00094728", 40.7789, -73.9692),
        ("USW00014732", 40.7794, -73.8803),
        ("USW00014734", 40.6833, -74.1694),
        ("USW00012839", 25.7906, -80.3164),
        ("USW00094846", 41.9950, -87.9336),
        ("USW00023234", 32.8983, -97.0192),
        ("USW00024233", 47.4489, -122.3094),
        ("USW00023174", 33.9381, -118.3894),
        ("USW00014922", 44.8831, -93.2289),
        ("USW00013874", 33.6301, -84.4419),
        ("USW00014739", 42.3606, -71.0106),
        ("CA006158731", 43.6772, -79.6306),
        ("GME00127786", 50.0500, 8.6000),
        ("UK000056225", 51.4780, -0.4610),
    ]
    elements = ["TMAX", "TMIN", "PRCP", "SNOW", "SNWD"]
    lines: list[str] = []
    for sid, lat, lon in stations:
        for el in elements:
            # Deterministic year range based on station
            start = _hash_int(f"{sid}:start", 1940, 1970)
            end = _hash_int(f"{sid}:end", 2020, 2024)
            # Fixed-width: ID(0:11) lat(12:20) lon(21:30) _gap_ element(31:35) fy(36:40) ly(41:45)
            lines.append(f"{sid:<11s} {lat:8.4f} {lon:9.4f} {el:<4s} {start:4d} {end:4d}")
    return "\n".join(lines) + "\n"


def _mock_station_csv(
    station_id: str,
    start_year: int,
    end_year: int,
) -> str:
    """Generate mock GHCN CSV data for a station (deterministic).

    Returns CSV text in GHCN format:
    ``ID,DATE,ELEMENT,DATA_VALUE,M_FLAG,Q_FLAG,S_FLAG,OBS_TIME``
    """
    rows: list[str] = []
    for year in range(start_year, end_year + 1):
        days_per_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        for month in range(1, 13):
            for day in range(1, days_per_month[month - 1] + 1):
                date_str = f"{year:04d}{month:02d}{day:02d}"
                seed = f"{station_id}:{date_str}"

                # Seasonal temperature base (tenths of deg C)
                seasonal = -50 + 250 * (1 - abs(month - 7) / 6)
                tmax = int(seasonal + _hash_float(seed + ":tmax", 0, 100))
                tmin = int(seasonal - _hash_float(seed + ":tmin", 0, 100))

                # Precipitation (tenths of mm) — ~30% chance of precip
                has_precip = _hash_int(seed + ":hp", 0, 10) > 6
                prcp = _hash_int(seed + ":prcp", 0, 300) if has_precip else 0

                # Snow (tenths of mm) — only in cold months
                snow = 0
                if month in (1, 2, 3, 11, 12) and tmin < 0:
                    snow = _hash_int(seed + ":snow", 0, 500) if has_precip else 0

                for element, value in [
                    ("TMAX", tmax),
                    ("TMIN", tmin),
                    ("PRCP", prcp),
                    ("SNOW", snow),
                ]:
                    rows.append(f"{station_id},{date_str},{element},{value},,,S,")

    return "\n".join(rows) + "\n"
