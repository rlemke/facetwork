"""Tests for census-us handler dispatch adapter pattern.

Verifies that each handler module's handle() function dispatches correctly
using the _facet_name key, that _DISPATCH dicts have the expected keys,
and that register_handlers() calls runner.register_handler the expected
number of times.
"""

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import shapefile

CENSUS_DIR = str(Path(__file__).resolve().parent.parent)


def _census_import(module_name: str):
    """Import a census-us handlers submodule, ensuring correct sys.path."""
    if CENSUS_DIR in sys.path:
        sys.path.remove(CENSUS_DIR)
    sys.path.insert(0, CENSUS_DIR)

    full_name = f"handlers.{module_name}"

    # If module is already loaded from the right location, return it
    if full_name in sys.modules:
        mod = sys.modules[full_name]
        mod_file = getattr(mod, "__file__", "")
        if mod_file and "census-us" in mod_file:
            return mod
        del sys.modules[full_name]

    # Ensure the handlers package itself is from census-us
    if "handlers" in sys.modules:
        pkg = sys.modules["handlers"]
        pkg_file = getattr(pkg, "__file__", "")
        if pkg_file and "census-us" not in pkg_file:
            stale = [k for k in sys.modules
                     if k == "handlers" or k.startswith("handlers.")]
            for k in stale:
                del sys.modules[k]

    return importlib.import_module(full_name)


class TestDownloadHandlers:
    def test_dispatch_keys(self):
        mod = _census_import("downloads.download_handlers")
        assert len(mod._DISPATCH) == 3
        assert "census.Operations.DownloadACS" in mod._DISPATCH
        assert "census.Operations.DownloadTIGER" in mod._DISPATCH
        assert "census.Operations.DownloadACSDetailed" in mod._DISPATCH

    def test_handle_dispatches(self):
        mod = _census_import("downloads.download_handlers")
        mock_file = {
            "url": "https://example.com/acs.csv",
            "path": "/tmp/acs.csv",
            "date": "2026-01-01T00:00:00+00:00",
            "size": 1024,
            "wasInCache": True,
        }
        with patch.object(mod, "download_acs", return_value=mock_file):
            result = mod.handle({
                "_facet_name": "census.Operations.DownloadACS",
                "state_fips": "01",
            })
        assert isinstance(result, dict)
        assert "file" in result
        assert result["file"]["wasInCache"] is True

    def test_handle_unknown_facet(self):
        mod = _census_import("downloads.download_handlers")
        with pytest.raises(ValueError, match="Unknown facet"):
            mod.handle({"_facet_name": "census.Operations.NonExistent"})

    def test_register_handlers(self):
        mod = _census_import("downloads.download_handlers")
        runner = MagicMock()
        mod.register_handlers(runner)
        assert runner.register_handler.call_count == 3

    def test_handle_download_acs_detailed(self):
        mod = _census_import("downloads.download_handlers")
        mock_file = {
            "url": "https://example.com/acs_detailed.csv",
            "path": "/tmp/acs_detailed.csv",
            "date": "2026-01-01T00:00:00+00:00",
            "size": 2048,
            "wasInCache": False,
        }
        with patch.object(mod, "download_acs", return_value=mock_file):
            result = mod.handle({
                "_facet_name": "census.Operations.DownloadACSDetailed",
                "state_fips": "01",
            })
        assert isinstance(result, dict)
        assert "file" in result
        assert result["file"]["wasInCache"] is False

    def test_error_step_log_download_acs(self):
        mod = _census_import("downloads.download_handlers")
        step_log = MagicMock()
        with patch.object(mod, "download_acs",
                          side_effect=RuntimeError("connection timeout")):
            with pytest.raises(RuntimeError, match="connection timeout"):
                mod.handle({
                    "_facet_name": "census.Operations.DownloadACS",
                    "state_fips": "01",
                    "_step_log": step_log,
                })
        step_log.assert_called_once()
        call_args = step_log.call_args
        assert "connection timeout" in call_args[0][0]
        assert call_args[1]["level"] == "error"

    def test_error_step_log_download_tiger(self):
        mod = _census_import("downloads.download_handlers")
        step_log = MagicMock()
        with patch.object(mod, "download_tiger",
                          side_effect=ValueError("bad geo_level")):
            with pytest.raises(ValueError, match="bad geo_level"):
                mod.handle({
                    "_facet_name": "census.Operations.DownloadTIGER",
                    "state_fips": "01",
                    "_step_log": step_log,
                })
        step_log.assert_called_once()
        assert "bad geo_level" in step_log.call_args[0][0]
        assert step_log.call_args[1]["level"] == "error"


class TestACSHandlers:
    def test_dispatch_keys(self):
        mod = _census_import("acs.acs_handlers")
        assert len(mod._DISPATCH) == 12
        for key in mod._DISPATCH:
            assert key.startswith("census.ACS.")

    def test_handle_dispatches(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractPopulation",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert isinstance(result, dict)
        assert "result" in result
        assert result["result"]["table_id"] == "B01003"

    def test_handle_unknown_facet(self):
        mod = _census_import("acs.acs_handlers")
        with pytest.raises(ValueError, match="Unknown facet"):
            mod.handle({"_facet_name": "census.ACS.NonExistent"})

    def test_register_handlers(self):
        mod = _census_import("acs.acs_handlers")
        runner = MagicMock()
        mod.register_handlers(runner)
        assert runner.register_handler.call_count == 12

    def test_extract_population(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractPopulation",
            "file": {"path": ""},
            "state_fips": "01",
            "geo_level": "county",
        })
        assert result["result"]["table_id"] == "B01003"
        assert result["result"]["geography_level"] == "county"

    def test_extract_income(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractIncome",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B19013"

    def test_extract_housing(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractHousing",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B25001"

    def test_extract_education(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractEducation",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B15003"

    def test_extract_commuting(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractCommuting",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B08301"

    def test_extract_tenure(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractTenure",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B25003"

    def test_extract_households(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractHouseholds",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B11001"

    def test_extract_age(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractAge",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B01001"

    def test_extract_vehicles(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractVehicles",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B25044"

    def test_extract_race(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractRace",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B02001"

    def test_extract_poverty(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractPoverty",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B17001"

    def test_extract_employment(self):
        mod = _census_import("acs.acs_handlers")
        result = mod.handle({
            "_facet_name": "census.ACS.ExtractEmployment",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["table_id"] == "B23025"

    def test_extract_multi_column_csv(self, tmp_path):
        """ACS extractor handles multi-column tables correctly."""
        mod = _census_import("acs.acs_extractor")
        csv_file = tmp_path / "acs_tenure.csv"
        csv_file.write_text(
            "GEOID,NAME,B25003_001E,B25003_002E,B25003_003E\n"
            "0500000US01001,Autauga County,22000,16000,6000\n"
            "0500000US01003,Baldwin County,85000,65000,20000\n"
        )
        result = mod.extract_acs_table(
            csv_path=str(csv_file),
            table_id="B25003",
            state_fips="01",
        )
        assert result.record_count == 2
        assert result.table_id == "B25003"
        # Verify output has all 3 columns
        import csv
        with open(result.output_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert "B25003_001E" in rows[0]
        assert "B25003_002E" in rows[0]
        assert "B25003_003E" in rows[0]
        assert rows[0]["B25003_001E"] == "22000"

    def test_extract_from_csv(self, tmp_path):
        """ACS extractor reads CSV files produced by Census API."""
        mod = _census_import("acs.acs_extractor")
        csv_file = tmp_path / "acs_2023_01.csv"
        csv_file.write_text(
            "GEOID,NAME,B01003_001E,B19013_001E\n"
            "0500000US01001,Autauga County,59285,61756\n"
            "0500000US01003,Baldwin County,231767,63756\n"
        )
        result = mod.extract_acs_table(
            csv_path=str(csv_file),
            table_id="B01003",
            state_fips="01",
        )
        assert result.record_count == 2
        assert result.table_id == "B01003"

    def test_extract_csv_filters_by_state_fips(self, tmp_path):
        """ACS extractor only returns rows matching state FIPS."""
        mod = _census_import("acs.acs_extractor")
        csv_file = tmp_path / "acs_mixed.csv"
        csv_file.write_text(
            "GEOID,NAME,B01003_001E\n"
            "0500000US01001,Autauga County,59285\n"
            "0500000US02013,Aleutians East,3420\n"
        )
        result = mod.extract_acs_table(
            csv_path=str(csv_file),
            table_id="B01003",
            state_fips="01",
        )
        assert result.record_count == 1

    def test_error_step_log_acs_handler(self):
        mod = _census_import("acs.acs_handlers")
        step_log = MagicMock()
        with patch.object(
            mod, "extract_acs_table",
            side_effect=ValueError("Unknown ACS table: BOGUS"),
        ):
            with pytest.raises(ValueError, match="Unknown ACS table"):
                mod.handle({
                    "_facet_name": "census.ACS.ExtractPopulation",
                    "file": {"path": "/tmp/test.csv"},
                    "state_fips": "01",
                    "_step_log": step_log,
                })
        step_log.assert_called_once()
        assert "Unknown ACS table" in step_log.call_args[0][0]
        assert step_log.call_args[1]["level"] == "error"


class TestTIGERHandlers:
    def test_dispatch_keys(self):
        mod = _census_import("tiger.tiger_handlers")
        assert len(mod._DISPATCH) == 4
        for key in mod._DISPATCH:
            assert key.startswith("census.TIGER.")

    def test_handle_dispatches(self):
        mod = _census_import("tiger.tiger_handlers")
        result = mod.handle({
            "_facet_name": "census.TIGER.ExtractCounties",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert isinstance(result, dict)
        assert "result" in result
        assert result["result"]["format"] == "GeoJSON"

    def test_handle_unknown_facet(self):
        mod = _census_import("tiger.tiger_handlers")
        with pytest.raises(ValueError, match="Unknown facet"):
            mod.handle({"_facet_name": "census.TIGER.NonExistent"})

    def test_register_handlers(self):
        mod = _census_import("tiger.tiger_handlers")
        runner = MagicMock()
        mod.register_handlers(runner)
        assert runner.register_handler.call_count == 4

    def test_extract_counties(self):
        mod = _census_import("tiger.tiger_handlers")
        result = mod.handle({
            "_facet_name": "census.TIGER.ExtractCounties",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["geography_level"] == "COUNTY"

    def test_extract_tracts(self):
        mod = _census_import("tiger.tiger_handlers")
        result = mod.handle({
            "_facet_name": "census.TIGER.ExtractTracts",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["geography_level"] == "TRACT"

    def test_extract_block_groups(self):
        mod = _census_import("tiger.tiger_handlers")
        result = mod.handle({
            "_facet_name": "census.TIGER.ExtractBlockGroups",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["geography_level"] == "BG"

    def test_extract_places(self):
        mod = _census_import("tiger.tiger_handlers")
        result = mod.handle({
            "_facet_name": "census.TIGER.ExtractPlaces",
            "file": {"path": ""},
            "state_fips": "01",
        })
        assert result["result"]["geography_level"] == "PLACE"

    def test_error_step_log_tiger_handler(self):
        mod = _census_import("tiger.tiger_handlers")
        step_log = MagicMock()
        with patch.object(
            mod, "extract_tiger",
            side_effect=OSError("Permission denied"),
        ):
            with pytest.raises(OSError, match="Permission denied"):
                mod.handle({
                    "_facet_name": "census.TIGER.ExtractCounties",
                    "file": {"path": "/tmp/test.zip"},
                    "state_fips": "01",
                    "_step_log": step_log,
                })
        step_log.assert_called_once()
        assert "Permission denied" in step_log.call_args[0][0]
        assert step_log.call_args[1]["level"] == "error"


class TestDownloaderURLs:
    """Test that download URLs are constructed correctly."""

    def test_tiger_county_uses_national_file(self):
        mod = _census_import("shared.downloader")
        # COUNTY should use 'us' instead of state FIPS
        assert "COUNTY" in mod._TIGER_NATIONAL_GEO

    def test_tiger_tract_is_per_state(self):
        mod = _census_import("shared.downloader")
        assert "TRACT" not in mod._TIGER_NATIONAL_GEO

    def test_tiger_county_url_pattern(self):
        """download_tiger for COUNTY builds a tl_{year}_us_county.zip URL."""
        mod = _census_import("shared.downloader")
        with patch.object(mod, "_download_file", return_value=1024):
            with patch("os.path.exists", return_value=False):
                result = mod.download_tiger(
                    year="2024", geo_level="COUNTY", state_fips="01")
        assert "tl_2024_us_county.zip" in result["url"]
        assert "tl_2024_01_county.zip" not in result["url"]

    def test_tiger_tract_url_pattern(self):
        """download_tiger for TRACT builds a per-state URL."""
        mod = _census_import("shared.downloader")
        with patch.object(mod, "_download_file", return_value=512):
            with patch("os.path.exists", return_value=False):
                result = mod.download_tiger(
                    year="2024", geo_level="TRACT", state_fips="06")
        assert "tl_2024_06_tract.zip" in result["url"]

    def test_acs_uses_census_api(self):
        """download_acs builds a Census API URL, not a ZIP URL."""
        mod = _census_import("shared.downloader")
        assert hasattr(mod, "CENSUS_API_BASE")
        assert "api.census.gov" in mod.CENSUS_API_BASE


class TestJoinGeoDensity:
    """Test population density computation in join_geo."""

    def test_density_computed_from_aland_and_population(self, tmp_path):
        mod = _census_import("summary.summary_builder")
        # ACS CSV with population estimate
        acs_csv = tmp_path / "pop.csv"
        acs_csv.write_text("GEOID,B01003_001E,NAME\n0500000US01001,50000,Autauga\n")
        # TIGER GeoJSON with ALAND (100 km2 = 1e8 m2)
        tiger_geojson = tmp_path / "counties.geojson"
        tiger_geojson.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"GEOID": "0500000US01001", "ALAND": 100000000},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }],
        }))
        result = mod.join_geo(str(acs_csv), str(tiger_geojson))
        assert result.feature_count == 1
        # Read output and check density
        with open(result.output_path) as f:
            data = json.load(f)
        props = data["features"][0]["properties"]
        # 50000 / (1e8 / 1e6) = 50000 / 100 = 500.0
        assert props["population_density_km2"] == 500.0

    def test_density_skipped_when_aland_missing(self, tmp_path):
        mod = _census_import("summary.summary_builder")
        acs_csv = tmp_path / "pop.csv"
        acs_csv.write_text("GEOID,B01003_001E,NAME\nGEO1,1000,Place\n")
        tiger_geojson = tmp_path / "geo.geojson"
        tiger_geojson.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"GEOID": "GEO1"},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }],
        }))
        result = mod.join_geo(str(acs_csv), str(tiger_geojson))
        with open(result.output_path) as f:
            data = json.load(f)
        props = data["features"][0]["properties"]
        assert "population_density_km2" not in props

    def test_density_zero_when_aland_is_zero(self, tmp_path):
        mod = _census_import("summary.summary_builder")
        acs_csv = tmp_path / "pop.csv"
        acs_csv.write_text("GEOID,B01003_001E,NAME\nGEO1,500,Place\n")
        tiger_geojson = tmp_path / "geo.geojson"
        tiger_geojson.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"GEOID": "GEO1", "ALAND": 0},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }],
        }))
        result = mod.join_geo(str(acs_csv), str(tiger_geojson))
        with open(result.output_path) as f:
            data = json.load(f)
        props = data["features"][0]["properties"]
        assert props["population_density_km2"] == 0.0


class TestDerivedMetrics:
    """Test derived metric computation in join_geo."""

    def _join_with_cols(self, tmp_path, acs_cols, tiger_props=None):
        """Helper: create ACS CSV + TIGER GeoJSON, call join_geo, return props."""
        mod = _census_import("summary.summary_builder")
        # Build ACS CSV
        header = ["GEOID", "NAME"] + list(acs_cols.keys())
        values = ["GEO1", "Test County"] + [str(v) for v in acs_cols.values()]
        acs_csv = tmp_path / "acs.csv"
        acs_csv.write_text(",".join(header) + "\n" + ",".join(values) + "\n")
        # Build TIGER GeoJSON
        props = {"GEOID": "GEO1", "ALAND": 100000000}
        if tiger_props:
            props.update(tiger_props)
        tiger = tmp_path / "tiger.geojson"
        tiger.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }],
        }))
        result = mod.join_geo(str(acs_csv), str(tiger))
        with open(result.output_path) as f:
            data = json.load(f)
        return data["features"][0]["properties"]

    def test_pct_owner_occupied(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B25003_001E": "500", "B25003_002E": "400", "B25003_003E": "100",
        })
        assert props["pct_owner_occupied"] == 80.0

    def test_pct_renter_occupied_complement(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B25003_001E": "500", "B25003_002E": "400", "B25003_003E": "100",
        })
        assert props["pct_renter_occupied"] == 20.0

    def test_pct_below_poverty(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "10000",
            "B17001_001E": "1000", "B17001_002E": "150",
        })
        assert props["pct_below_poverty"] == 15.0

    def test_unemployment_rate(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B23025_001E": "800", "B23025_002E": "700",
            "B23025_003E": "600", "B23025_005E": "30",
        })
        assert props["unemployment_rate"] == 5.0

    def test_pct_white(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B02001_001E": "1000", "B02001_002E": "750",
        })
        assert props["pct_white"] == 75.0

    def test_pct_bachelors_plus(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B15003_001E": "800",
            "B15003_022E": "100", "B15003_023E": "50",
            "B15003_024E": "30", "B15003_025E": "20",
        })
        # (100+50+30+20)/800*100 = 25.0
        assert props["pct_bachelors_plus"] == 25.0

    def test_pct_drove_alone(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B08301_001E": "500", "B08301_003E": "400",
        })
        assert props["pct_drove_alone"] == 80.0

    def test_vehicles_per_household(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B25044_001E": "100",
            "B25044_003E": "30", "B25044_004E": "40",
            "B25044_005E": "10", "B25044_006E": "5",
            "B25044_010E": "5", "B25044_011E": "5",
            "B25044_012E": "3", "B25044_013E": "2",
        })
        # weighted: 30*1+40*2+10*3+5*4+5*1+5*2+3*3+2*4 = 30+80+30+20+5+10+9+8 = 192
        # 192/100 = 1.92
        assert props["vehicles_per_household"] == 1.92

    def test_zero_denominator_returns_none(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
            "B25003_001E": "0", "B25003_002E": "0",
        })
        assert "pct_owner_occupied" not in props

    def test_missing_columns_no_metric(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "1000",
        })
        assert "pct_owner_occupied" not in props
        assert "unemployment_rate" not in props
        assert "pct_bachelors_plus" not in props

    def test_extra_acs_paths_merged(self, tmp_path):
        mod = _census_import("summary.summary_builder")
        # Primary ACS (population)
        primary = tmp_path / "pop.csv"
        primary.write_text("GEOID,NAME,B01003_001E\nGEO1,Test,5000\n")
        # Extra ACS (tenure)
        extra = tmp_path / "tenure.csv"
        extra.write_text("GEOID,NAME,B25003_001E,B25003_002E,B25003_003E\nGEO1,Test,200,160,40\n")
        # TIGER
        tiger = tmp_path / "tiger.geojson"
        tiger.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"GEOID": "GEO1", "ALAND": 100000000},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }],
        }))
        result = mod.join_geo(str(primary), str(tiger), extra_acs_paths=[str(extra)])
        with open(result.output_path) as f:
            data = json.load(f)
        props = data["features"][0]["properties"]
        assert props["pct_owner_occupied"] == 80.0
        assert props["population"] == 5000.0

    def test_friendly_aliases(self, tmp_path):
        props = self._join_with_cols(tmp_path, {
            "B01003_001E": "50000",
            "B19013_001E": "65000",
            "B25001_001E": "20000",
        })
        assert props["population"] == 50000.0
        assert props["median_income"] == 65000.0
        assert props["housing_units"] == 20000.0


class TestSummaryHandlers:
    def test_dispatch_keys(self):
        mod = _census_import("summary.summary_handlers")
        assert len(mod._DISPATCH) == 2
        assert "census.Summary.JoinGeo" in mod._DISPATCH
        assert "census.Summary.SummarizeState" in mod._DISPATCH

    def test_handle_join_geo(self):
        mod = _census_import("summary.summary_handlers")
        result = mod.handle({
            "_facet_name": "census.Summary.JoinGeo",
            "acs_path": "",
            "tiger_path": "",
        })
        assert isinstance(result, dict)
        assert "result" in result

    def test_handle_summarize_state(self):
        mod = _census_import("summary.summary_handlers")
        empty_acs = {
            "table_id": "", "output_path": "", "record_count": 0,
            "geography_level": "", "year": "", "extraction_date": "",
        }
        result = mod.handle({
            "_facet_name": "census.Summary.SummarizeState",
            "population": empty_acs,
            "income": empty_acs,
            "housing": empty_acs,
            "education": empty_acs,
            "commuting": empty_acs,
        })
        assert isinstance(result, dict)
        assert result["result"]["tables_joined"] == 0

    def test_handle_unknown_facet(self):
        mod = _census_import("summary.summary_handlers")
        with pytest.raises(ValueError, match="Unknown facet"):
            mod.handle({"_facet_name": "census.Summary.NonExistent"})

    def test_register_handlers(self):
        mod = _census_import("summary.summary_handlers")
        runner = MagicMock()
        mod.register_handlers(runner)
        assert runner.register_handler.call_count == 2

    def test_error_step_log_join_geo(self):
        mod = _census_import("summary.summary_handlers")
        step_log = MagicMock()
        with patch.object(
            mod, "join_geo",
            side_effect=json.JSONDecodeError("bad json", "", 0),
        ):
            with pytest.raises(json.JSONDecodeError):
                mod.handle({
                    "_facet_name": "census.Summary.JoinGeo",
                    "acs_path": "/tmp/a.csv",
                    "tiger_path": "/tmp/b.geojson",
                    "_step_log": step_log,
                })
        step_log.assert_called_once()
        assert step_log.call_args[1]["level"] == "error"

    def test_error_step_log_summarize_state(self):
        mod = _census_import("summary.summary_handlers")
        step_log = MagicMock()
        with patch.object(
            mod, "summarize_state",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                mod.handle({
                    "_facet_name": "census.Summary.SummarizeState",
                    "_step_log": step_log,
                })
        step_log.assert_called_once()
        assert "disk full" in step_log.call_args[0][0]
        assert step_log.call_args[1]["level"] == "error"


def _make_tiger_zip(path: Path, records: list[dict]):
    """Create a minimal TIGER-like shapefile ZIP using pyshp Writer.

    Args:
        path: Base path (without extension). Creates {path}.zip containing
              shapefile components (.shp, .dbf, .shx).
        records: List of dicts with 'fields' (dict of DBF fields) and
                 optionally 'geometry' (list of polygon parts) or None for null.
    """
    import zipfile as _zf

    shp_base = str(path) + "_shp"
    w = shapefile.Writer(shp_base)
    # Define fields from first record
    if records:
        for fname in records[0]["fields"]:
            w.field(fname, "C", size=40)
    for rec in records:
        fields = rec["fields"]
        geom = rec.get("geometry")
        if geom is None:
            w.null()
        else:
            w.poly(geom)
        w.record(**fields)
    w.close()
    # Bundle into a ZIP (as TIGER distributes)
    zip_path = str(path) + ".zip"
    with _zf.ZipFile(zip_path, "w") as zf:
        stem = Path(shp_base).name
        for ext in (".shp", ".dbf", ".shx"):
            comp = shp_base + ext
            if os.path.exists(comp):
                zf.write(comp, stem + ext)


class TestTIGERExtractor:
    """Tests for pyshp-based TIGER shapefile extraction."""

    def test_extract_with_pyshp(self, tmp_path, monkeypatch):
        """Pyshp path extracts features from a shapefile ZIP."""
        mod = _census_import("tiger.tiger_extractor")
        monkeypatch.setitem(mod.__dict__, "HAS_FIONA", False)
        monkeypatch.setitem(mod.__dict__, "HAS_PYSHP", True)
        monkeypatch.setitem(mod.__dict__, "_OUTPUT_DIR", str(tmp_path / "out"))

        zip_base = tmp_path / "counties"
        _make_tiger_zip(zip_base, [
            {"fields": {"STATEFP": "01", "GEOID": "01001", "NAME": "Autauga"},
             "geometry": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            {"fields": {"STATEFP": "01", "GEOID": "01003", "NAME": "Baldwin"},
             "geometry": [[[2, 2], [2, 3], [3, 3], [3, 2], [2, 2]]]},
        ])
        zip_path = str(zip_base) + ".zip"

        result = mod.extract_tiger(zip_path, "COUNTY", "01")
        assert result.feature_count == 2
        assert result.geography_level == "COUNTY"
        with open(result.output_path) as f:
            data = json.load(f)
        assert len(data["features"]) == 2
        assert data["features"][0]["properties"]["NAME"] == "Autauga"

    def test_pyshp_filters_by_statefp(self, tmp_path, monkeypatch):
        """Only features matching state_fips are extracted."""
        mod = _census_import("tiger.tiger_extractor")
        monkeypatch.setitem(mod.__dict__, "HAS_FIONA", False)
        monkeypatch.setitem(mod.__dict__, "HAS_PYSHP", True)
        monkeypatch.setitem(mod.__dict__, "_OUTPUT_DIR", str(tmp_path / "out"))

        zip_base = tmp_path / "mixed"
        _make_tiger_zip(zip_base, [
            {"fields": {"STATEFP": "01", "GEOID": "01001", "NAME": "Alabama Co"},
             "geometry": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            {"fields": {"STATEFP": "02", "GEOID": "02013", "NAME": "Alaska Co"},
             "geometry": [[[2, 2], [2, 3], [3, 3], [3, 2], [2, 2]]]},
        ])
        zip_path = str(zip_base) + ".zip"

        result = mod.extract_tiger(zip_path, "COUNTY", "01")
        assert result.feature_count == 1
        with open(result.output_path) as f:
            data = json.load(f)
        assert data["features"][0]["properties"]["GEOID"] == "01001"

    def test_pyshp_preserves_aland(self, tmp_path, monkeypatch):
        """ALAND field survives extraction for density calculation."""
        import zipfile as _zf

        mod = _census_import("tiger.tiger_extractor")
        monkeypatch.setitem(mod.__dict__, "HAS_FIONA", False)
        monkeypatch.setitem(mod.__dict__, "HAS_PYSHP", True)
        monkeypatch.setitem(mod.__dict__, "_OUTPUT_DIR", str(tmp_path / "out"))

        shp_base = str(tmp_path / "aland_shp")
        w = shapefile.Writer(shp_base)
        w.field("STATEFP", "C", size=2)
        w.field("GEOID", "C", size=10)
        w.field("ALAND", "N", size=14, decimal=0)
        w.poly([[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]])
        w.record(STATEFP="01", GEOID="01001", ALAND=100000000)
        w.close()
        zip_path = str(tmp_path / "aland") + ".zip"
        with _zf.ZipFile(zip_path, "w") as zf:
            for ext in (".shp", ".dbf", ".shx"):
                zf.write(shp_base + ext, "aland_shp" + ext)

        result = mod.extract_tiger(zip_path, "COUNTY", "01")
        assert result.feature_count == 1
        with open(result.output_path) as f:
            data = json.load(f)
        aland = data["features"][0]["properties"]["ALAND"]
        # float() must work — join_geo does float(aland)
        assert float(aland) == 100000000.0

    def test_no_readers_returns_zero(self, tmp_path, monkeypatch):
        """With both HAS_FIONA and HAS_PYSHP False, returns 0 features."""
        mod = _census_import("tiger.tiger_extractor")
        monkeypatch.setitem(mod.__dict__, "HAS_FIONA", False)
        monkeypatch.setitem(mod.__dict__, "HAS_PYSHP", False)
        monkeypatch.setitem(mod.__dict__, "_OUTPUT_DIR", str(tmp_path / "out"))

        zip_base = tmp_path / "noreader"
        _make_tiger_zip(zip_base, [
            {"fields": {"STATEFP": "01", "GEOID": "01001", "NAME": "Test"},
             "geometry": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
        ])
        zip_path = str(zip_base) + ".zip"

        result = mod.extract_tiger(zip_path, "COUNTY", "01")
        assert result.feature_count == 0

    def test_pyshp_skips_null_geometry(self, tmp_path, monkeypatch):
        """Records with null geometry are skipped."""
        mod = _census_import("tiger.tiger_extractor")
        monkeypatch.setitem(mod.__dict__, "HAS_FIONA", False)
        monkeypatch.setitem(mod.__dict__, "HAS_PYSHP", True)
        monkeypatch.setitem(mod.__dict__, "_OUTPUT_DIR", str(tmp_path / "out"))

        zip_base = tmp_path / "nullgeo"
        _make_tiger_zip(zip_base, [
            {"fields": {"STATEFP": "01", "GEOID": "01001", "NAME": "HasGeo"},
             "geometry": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            {"fields": {"STATEFP": "01", "GEOID": "01002", "NAME": "NoGeo"},
             "geometry": None},
        ])
        zip_path = str(zip_base) + ".zip"

        result = mod.extract_tiger(zip_path, "COUNTY", "01")
        assert result.feature_count == 1
        with open(result.output_path) as f:
            data = json.load(f)
        assert data["features"][0]["properties"]["NAME"] == "HasGeo"


class TestInitRegistryHandlers:
    def test_register_all_registry_handlers(self):
        mod = _census_import("__init__")
        runner = MagicMock()
        mod.register_all_registry_handlers(runner)
        # 3 downloads + 12 ACS + 4 TIGER + 2 summary + 15 ingestion = 36
        assert runner.register_handler.call_count == 36

    def test_register_all_handlers(self):
        mod = _census_import("__init__")
        poller = MagicMock()
        mod.register_all_handlers(poller)
        assert poller.register.call_count == 36
