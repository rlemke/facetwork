"""Tests for climate aggregation and trend handlers."""

from __future__ import annotations

import os
import sys

import pytest

# Ensure handlers package is importable
_examples_dir = os.path.join(os.path.dirname(__file__), "..")
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

from handlers.shared.weather_utils import ClimateStore, simple_linear_regression

try:
    import mongomock

    HAS_MONGOMOCK = True
except ImportError:
    HAS_MONGOMOCK = False


# ---------------------------------------------------------------------------
# simple_linear_regression
# ---------------------------------------------------------------------------


class TestLinearRegression:
    def test_perfect_line(self):
        slope, intercept = simple_linear_regression([1, 2, 3, 4], [2, 4, 6, 8])
        assert abs(slope - 2.0) < 1e-9
        assert abs(intercept - 0.0) < 1e-9

    def test_flat_line(self):
        slope, intercept = simple_linear_regression([1, 2, 3], [5, 5, 5])
        assert abs(slope) < 1e-9
        assert abs(intercept - 5.0) < 1e-9

    def test_single_point(self):
        slope, intercept = simple_linear_regression([3.0], [7.0])
        assert slope == 0.0
        assert intercept == 7.0

    def test_empty_input(self):
        slope, intercept = simple_linear_regression([], [])
        assert slope == 0.0
        assert intercept == 0.0

    def test_negative_slope(self):
        slope, intercept = simple_linear_regression([0, 1, 2], [10, 8, 6])
        assert abs(slope - (-2.0)) < 1e-9
        assert abs(intercept - 10.0) < 1e-9

    def test_with_offset(self):
        # y = 0.5x + 3
        xs = [0, 2, 4, 6, 8]
        ys = [3, 4, 5, 6, 7]
        slope, intercept = simple_linear_regression(xs, ys)
        assert abs(slope - 0.5) < 1e-9
        assert abs(intercept - 3.0) < 1e-9


# ---------------------------------------------------------------------------
# ClimateStore
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_MONGOMOCK, reason="mongomock not installed")
class TestClimateStore:
    @pytest.fixture()
    def store(self):
        client = mongomock.MongoClient()
        db = client["test_climate"]
        return ClimateStore(db)

    def test_upsert_and_get_state_year(self, store):
        data = {
            "state": "NY",
            "year": 2020,
            "station_count": 3,
            "temp_mean": 12.5,
            "temp_min_avg": 5.0,
            "temp_max_avg": 20.0,
            "precip_annual": 1100.0,
            "hot_days": 10,
            "frost_days": 80,
            "precip_days": 120,
        }
        store.upsert_state_year(data)
        results = store.get_state_years("NY")
        assert len(results) == 1
        assert results[0]["year"] == 2020
        assert results[0]["temp_mean"] == 12.5

    def test_upsert_and_get_trend(self, store):
        trend = {
            "state": "NY",
            "start_year": 1944,
            "end_year": 2024,
            "warming_rate_per_decade": 0.15,
            "precip_change_pct": 5.0,
            "decades": {"1940s": {"avg_temp": 10.0}},
        }
        store.upsert_trend(trend)
        result = store.get_trend("NY")
        assert result is not None
        assert result["warming_rate_per_decade"] == 0.15

    def test_list_states(self, store):
        store.upsert_trend({"state": "NY", "warming_rate_per_decade": 0.1})
        store.upsert_trend({"state": "CA", "warming_rate_per_decade": 0.2})
        states = store.list_states()
        assert states == ["CA", "NY"]

    def test_get_state_years_range(self, store):
        for y in range(2018, 2023):
            store.upsert_state_year({"state": "TX", "year": y, "temp_mean": 20.0 + y - 2018})
        results = store.get_state_years("TX", 2019, 2021)
        years = [r["year"] for r in results]
        assert years == [2019, 2020, 2021]

    def test_get_narrative(self, store):
        store.upsert_trend({"state": "FL", "narrative": "Hot and getting hotter."})
        assert store.get_narrative("FL") == "Hot and getting hotter."

    def test_get_narrative_missing(self, store):
        assert store.get_narrative("ZZ") is None


# ---------------------------------------------------------------------------
# Climate handlers (with mocked DB)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_MONGOMOCK, reason="mongomock not installed")
class TestClimateHandlers:
    @pytest.fixture(autouse=True)
    def _setup_db(self, monkeypatch):
        """Patch get_weather_db to return a mongomock database."""
        self.mock_client = mongomock.MongoClient()
        self.db = self.mock_client["test_climate_handlers"]
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.get_weather_db",
            lambda: self.db,
        )

    def test_aggregate_empty(self):
        from handlers.climate.climate_handlers import handle_aggregate_state_year

        result = handle_aggregate_state_year({"state": "NY", "year": 2020})
        yearly = result["yearly"]
        assert yearly["station_count"] == 0
        assert yearly["temp_mean"] == 0.0

    def test_aggregate_with_reports(self):
        from handlers.climate.climate_handlers import handle_aggregate_state_year

        # Seed two station reports
        for i, station in enumerate(["725030-14732", "724050-13743"]):
            self.db["weather_reports"].insert_one(
                {
                    "station_id": station,
                    "year": 2020,
                    "location": "New York",
                    "report": {"state": "NY"},
                    "daily_stats": [
                        {
                            "date": "2020-07-01",
                            "temp_mean": 25.0 + i,
                            "temp_min": 18.0 + i,
                            "temp_max": 32.0 + i,
                            "precip_total": 5.0,
                        },
                        {
                            "date": "2020-01-15",
                            "temp_mean": -2.0,
                            "temp_min": -8.0,
                            "temp_max": 3.0,
                            "precip_total": 0.0,
                        },
                    ],
                }
            )

        result = handle_aggregate_state_year({"state": "NY", "year": 2020})
        yearly = result["yearly"]
        assert yearly["station_count"] == 2
        assert yearly["temp_mean"] != 0.0
        assert yearly["frost_days"] > 0  # -8°C days

    def test_compute_trend_empty(self):
        from handlers.climate.climate_handlers import handle_compute_climate_trend

        result = handle_compute_climate_trend({"state": "NY", "start_year": 1944, "end_year": 2024})
        trend = result["trend"]
        assert trend["warming_rate_per_decade"] == 0.0
        assert trend["decades"] == {}

    def test_compute_trend_with_data(self):
        from handlers.climate.climate_handlers import handle_compute_climate_trend

        # Seed yearly data with increasing temps
        climate_store = ClimateStore(self.db)
        for i, year in enumerate(range(1950, 1960)):
            climate_store.upsert_state_year(
                {
                    "state": "CA",
                    "year": year,
                    "station_count": 3,
                    "temp_mean": 15.0 + i * 0.1,
                    "precip_annual": 500.0 + i * 10,
                }
            )

        result = handle_compute_climate_trend({"state": "CA", "start_year": 1950, "end_year": 1959})
        trend = result["trend"]
        # Slope should be positive (warming)
        assert trend["warming_rate_per_decade"] > 0
        assert "1950s" in trend["decades"]

    def test_narrative_fallback(self):
        from handlers.climate.climate_handlers import _climate_narrative_fallback

        trend = {
            "state": "NY",
            "start_year": 1944,
            "end_year": 2024,
            "warming_rate_per_decade": 0.18,
            "precip_change_pct": 12.5,
            "decades": {
                "1940s": {"avg_temp": 10.0, "avg_precip": 900.0, "years_with_data": 6},
                "2020s": {"avg_temp": 12.5, "avg_precip": 1050.0, "years_with_data": 5},
            },
        }
        result = _climate_narrative_fallback("NY", trend)
        narrative = result["narrative"]
        assert "NY" in narrative
        assert "0.18" in narrative
        assert "12.5%" in narrative
        assert "1940s" in narrative
        assert "2020s" in narrative

    def test_narrative_fallback_cooling(self):
        from handlers.climate.climate_handlers import _climate_narrative_fallback

        trend = {
            "warming_rate_per_decade": -0.05,
            "precip_change_pct": -3.0,
            "start_year": 1980,
            "end_year": 2020,
            "decades": {},
        }
        result = _climate_narrative_fallback("AK", trend)
        assert "cooled" in result["narrative"]

    def test_narrative_fallback_stable(self):
        from handlers.climate.climate_handlers import _climate_narrative_fallback

        trend = {
            "warming_rate_per_decade": 0,
            "precip_change_pct": 0,
            "start_year": 2000,
            "end_year": 2020,
            "decades": {},
        }
        result = _climate_narrative_fallback("HI", trend)
        assert "stable" in result["narrative"]


# ---------------------------------------------------------------------------
# Decade grouping
# ---------------------------------------------------------------------------


class TestDecadeGrouping:
    def test_grouping(self):
        from handlers.climate.climate_handlers import _group_by_decade

        data = [
            {"year": 1945, "temp_mean": 10.0, "precip_annual": 800.0},
            {"year": 1948, "temp_mean": 11.0, "precip_annual": 850.0},
            {"year": 1950, "temp_mean": 12.0, "precip_annual": 900.0},
            {"year": 1955, "temp_mean": 12.5, "precip_annual": 920.0},
        ]
        result = _group_by_decade(data)
        assert "1940s" in result
        assert "1950s" in result
        assert result["1940s"]["years_with_data"] == 2
        assert result["1950s"]["years_with_data"] == 2
        assert result["1940s"]["avg_temp"] == 10.5

    def test_empty_data(self):
        from handlers.climate.climate_handlers import _group_by_decade

        assert _group_by_decade([]) == {}

    def test_single_decade(self):
        from handlers.climate.climate_handlers import _group_by_decade

        data = [{"year": 2020, "temp_mean": 15.0, "precip_annual": 1000.0}]
        result = _group_by_decade(data)
        assert list(result.keys()) == ["2020s"]


# ---------------------------------------------------------------------------
# AnalyzeStationClimate handler tests
# ---------------------------------------------------------------------------


class TestAnalyzeStationClimateHandler:
    """Tests for the consolidated AnalyzeStationClimate handler."""

    def test_basic_analysis(self, tmp_path, monkeypatch):
        """Successfully analyzes a small year range."""

        import mongomock
        from handlers.climate.climate_handlers import handle_analyze_station_climate

        mock_client = mongomock.MongoClient()
        db = mock_client["test_station_climate"]
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.get_weather_db",
            lambda: db,
        )

        # Create mock ISD-Lite files for 2 years
        def mock_download(usaf, wban, year, cache_dir=None):
            path = str(tmp_path / f"{usaf}-{wban}-{year}.txt")
            with open(path, "w") as f:
                for month in [1, 7]:
                    for hour in [0, 12]:
                        temp = int((-5 + 25 * (1 - abs(month - 7) / 6)) * 10)
                        f.write(
                            f"{year:4d} {month:02d} 15 {hour:02d}{temp:6d}   -50 10100   180    30     2    10 -9999\n"
                        )
            return path

        monkeypatch.setattr(
            "handlers.climate.climate_handlers.download_isd_lite",
            mock_download,
        )

        result = handle_analyze_station_climate(
            {
                "usaf": "725030",
                "wban": "14732",
                "station_name": "LA GUARDIA",
                "lat": 40.779,
                "lon": -73.88,
                "start_year": 2022,
                "end_year": 2023,
            }
        )

        assert result["station_id"] == "725030-14732"
        assert result["years_analyzed"] == 2
        assert len(result["yearly_summaries"]) == 2
        assert all(s["status"] == "ok" for s in result["yearly_summaries"])

    def test_empty_range(self, monkeypatch):
        """Empty year range (start > end) returns zero results."""
        import mongomock
        from handlers.climate.climate_handlers import handle_analyze_station_climate

        mock_client = mongomock.MongoClient()
        db = mock_client["test_empty"]
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.get_weather_db",
            lambda: db,
        )

        result = handle_analyze_station_climate(
            {
                "usaf": "725030",
                "wban": "14732",
                "station_name": "TEST",
                "lat": 0,
                "lon": 0,
                "start_year": 2025,
                "end_year": 2020,
            }
        )

        assert result["years_analyzed"] == 0
        assert result["yearly_summaries"] == []

    def test_download_error_caught(self, monkeypatch):
        """Download errors are caught per-year and don't fail the whole call."""
        import mongomock
        from handlers.climate.climate_handlers import handle_analyze_station_climate

        mock_client = mongomock.MongoClient()
        db = mock_client["test_errors"]
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.get_weather_db",
            lambda: db,
        )
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.download_isd_lite",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("download failed")),
        )

        result = handle_analyze_station_climate(
            {
                "usaf": "725030",
                "wban": "14732",
                "station_name": "TEST",
                "lat": 0,
                "lon": 0,
                "start_year": 2022,
                "end_year": 2023,
            }
        )

        assert result["years_analyzed"] == 0
        assert len(result["yearly_summaries"]) == 2
        assert all(s["status"] == "error" for s in result["yearly_summaries"])

    def test_skips_cached_reports(self, monkeypatch):
        """Years with existing MongoDB reports are skipped (status='cached')."""
        import mongomock
        from handlers.climate.climate_handlers import handle_analyze_station_climate

        mock_client = mongomock.MongoClient()
        db = mock_client["test_cached"]
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.get_weather_db",
            lambda: db,
        )

        # Pre-seed a report for 2022
        db["weather_reports"].insert_one(
            {
                "station_id": "725030-14732",
                "year": 2022,
                "station_name": "LA GUARDIA",
                "report": {"total_days": 365, "annual_precip": 50.0},
                "daily_stats": [],
            }
        )

        # download_isd_lite should NOT be called for 2022 — only 2023
        download_calls: list[int] = []

        def mock_download(usaf, wban, year, cache_dir=None):
            download_calls.append(year)
            raise RuntimeError("should only be called for uncached years")

        monkeypatch.setattr(
            "handlers.climate.climate_handlers.download_isd_lite",
            mock_download,
        )

        result = handle_analyze_station_climate(
            {
                "usaf": "725030",
                "wban": "14732",
                "station_name": "LA GUARDIA",
                "lat": 40.779,
                "lon": -73.88,
                "start_year": 2022,
                "end_year": 2023,
            }
        )

        # 2022 was cached, 2023 errored (mock raises)
        assert result["years_analyzed"] == 1
        assert result["years_cached"] == 1
        assert download_calls == [2023]  # only uncached year attempted
        statuses = {s["year"]: s["status"] for s in result["yearly_summaries"]}
        assert statuses[2022] == "cached"
        assert statuses[2023] == "error"

    def test_force_skips_cache(self, tmp_path, monkeypatch):
        """force=True re-processes even years with existing reports."""
        import mongomock
        from handlers.climate.climate_handlers import handle_analyze_station_climate

        mock_client = mongomock.MongoClient()
        db = mock_client["test_force"]
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.get_weather_db",
            lambda: db,
        )

        # Pre-seed a report for 2022
        db["weather_reports"].insert_one(
            {
                "station_id": "725030-14732",
                "year": 2022,
                "station_name": "LA GUARDIA",
                "report": {"total_days": 365, "annual_precip": 50.0},
                "daily_stats": [],
            }
        )

        def mock_download(usaf, wban, year, cache_dir=None):
            path = str(tmp_path / f"{usaf}-{wban}-{year}.txt")
            with open(path, "w") as f:
                for hour in [0, 12]:
                    f.write(
                        f"{year:4d} 07 15 {hour:02d}   250   -50 10100   180    30     2    10 -9999\n"
                    )
            return path

        monkeypatch.setattr(
            "handlers.climate.climate_handlers.download_isd_lite",
            mock_download,
        )

        result = handle_analyze_station_climate(
            {
                "usaf": "725030",
                "wban": "14732",
                "station_name": "LA GUARDIA",
                "lat": 40.779,
                "lon": -73.88,
                "start_year": 2022,
                "end_year": 2022,
                "force": True,
            }
        )

        assert result["years_cached"] == 0
        assert result["years_analyzed"] == 1
        assert result["yearly_summaries"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# ComputeRegionTrend handler tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_MONGOMOCK, reason="mongomock not installed")
class TestComputeRegionTrendHandler:
    """Tests for the consolidated ComputeRegionTrend handler."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, monkeypatch):
        self.mock_client = mongomock.MongoClient()
        self.db = self.mock_client["test_region_trend"]
        monkeypatch.setattr(
            "handlers.climate.climate_handlers.get_weather_db",
            lambda: self.db,
        )

    def test_trend_with_data(self):
        from handlers.climate.climate_handlers import handle_compute_region_trend

        # Seed reports for a few years
        for year in range(2018, 2023):
            self.db["weather_reports"].insert_one(
                {
                    "station_id": "725030-14732",
                    "year": year,
                    "location": "New York",
                    "report": {"state": "NY"},
                    "daily_stats": [
                        {
                            "date": f"{year}-07-01",
                            "temp_mean": 25.0 + (year - 2018) * 0.2,
                            "temp_min": 18.0,
                            "temp_max": 32.0,
                            "precip_total": 5.0,
                        },
                    ],
                }
            )

        result = handle_compute_region_trend(
            {
                "country": "US",
                "state": "NY",
                "start_year": 2018,
                "end_year": 2022,
            }
        )

        assert "trend" in result
        assert "narrative" in result
        assert result["trend"]["state"] == "NY"
        assert result["trend"]["warming_rate_per_decade"] != 0

    def test_trend_empty_data(self):
        from handlers.climate.climate_handlers import handle_compute_region_trend

        result = handle_compute_region_trend(
            {
                "country": "US",
                "state": "ZZ",
                "start_year": 2018,
                "end_year": 2022,
            }
        )

        assert result["trend"]["warming_rate_per_decade"] == 0.0
        assert result["narrative"] != ""


# ---------------------------------------------------------------------------
# CacheBulkStationData handler tests
# ---------------------------------------------------------------------------


class TestBulkCacheHandler:
    """Tests for the CacheBulkStationData ingest handler."""

    def test_basic_cache(self, monkeypatch):
        from handlers.ingest.ingest_handlers import handle_cache_bulk_station_data

        downloads: list[tuple[str, str, int]] = []

        def mock_download(usaf, wban, year, cache_dir=None):
            downloads.append((usaf, wban, year))
            return f"/tmp/{usaf}-{wban}-{year}.gz"

        monkeypatch.setattr(
            "handlers.ingest.ingest_handlers.download_isd_lite",
            mock_download,
        )

        result = handle_cache_bulk_station_data(
            {
                "usaf": "725030",
                "wban": "14732",
                "start_year": 2020,
                "end_year": 2023,
                "begin_date": "19730101",
                "end_date": "20231231",
            }
        )

        assert result["files_cached"] == 4
        assert result["station_id"] == "725030-14732"
        assert len(downloads) == 4
        assert downloads[0] == ("725030", "14732", 2020)
        assert downloads[-1] == ("725030", "14732", 2023)

    def test_year_clipping(self, monkeypatch):
        """Year range is clipped to station's active dates."""
        from handlers.ingest.ingest_handlers import handle_cache_bulk_station_data

        downloads: list[int] = []

        def mock_download(usaf, wban, year, cache_dir=None):
            downloads.append(year)
            return f"/tmp/{usaf}-{wban}-{year}.gz"

        monkeypatch.setattr(
            "handlers.ingest.ingest_handlers.download_isd_lite",
            mock_download,
        )

        result = handle_cache_bulk_station_data(
            {
                "usaf": "725030",
                "wban": "14732",
                "start_year": 1944,
                "end_year": 2024,
                "begin_date": "20200101",  # Station only active from 2020
                "end_date": "20221231",  # ... to 2022
            }
        )

        assert result["files_cached"] == 3
        assert downloads == [2020, 2021, 2022]

    def test_download_error_handled(self, monkeypatch):
        """Download errors are caught per-year."""
        from handlers.ingest.ingest_handlers import handle_cache_bulk_station_data

        call_count = 0

        def mock_download(usaf, wban, year, cache_dir=None):
            nonlocal call_count
            call_count += 1
            if year == 2021:
                raise RuntimeError("download error")
            return f"/tmp/{usaf}-{wban}-{year}.gz"

        monkeypatch.setattr(
            "handlers.ingest.ingest_handlers.download_isd_lite",
            mock_download,
        )

        result = handle_cache_bulk_station_data(
            {
                "usaf": "725030",
                "wban": "14732",
                "start_year": 2020,
                "end_year": 2022,
                "begin_date": "19440101",
                "end_date": "20241231",
            }
        )

        assert result["files_cached"] == 2  # 2020 and 2022 succeed, 2021 fails
        assert call_count == 3


# ---------------------------------------------------------------------------
# Station inventory cache TTL tests
# ---------------------------------------------------------------------------


class TestStationInventoryCache:
    """Tests for download_station_inventory TTL-based caching."""

    def test_fresh_cache_returns_without_download(self, tmp_path):
        """A cache file newer than max_age_hours is returned directly."""
        from handlers.shared.weather_utils import download_station_inventory

        cache_path = str(tmp_path / "isd-history.csv")
        # Write a fresh cache file
        with open(cache_path, "w") as f:
            f.write("USAF,WBAN,STATION NAME\n")

        # Should return cached content (no network call)
        result = download_station_inventory(cache_path=cache_path)
        assert result == "USAF,WBAN,STATION NAME\n"

    def test_stale_cache_triggers_redownload(self, tmp_path, monkeypatch):
        """A cache file older than max_age_hours triggers a re-download."""
        import time as _time

        from handlers.shared.weather_utils import download_station_inventory

        cache_path = str(tmp_path / "isd-history.csv")
        with open(cache_path, "w") as f:
            f.write("OLD DATA\n")

        # Age the file by backdating mtime by 25 hours
        old_mtime = _time.time() - 25 * 3600
        os.utime(cache_path, (old_mtime, old_mtime))

        # Patch requests away so it falls through to mock
        monkeypatch.setattr("handlers.shared.weather_utils.HAS_REQUESTS", False)

        result = download_station_inventory(cache_path=cache_path, max_age_hours=24.0)
        # Should return mock data (not the stale "OLD DATA")
        assert "OLD DATA" not in result
        assert "USAF" in result  # mock CSV header
