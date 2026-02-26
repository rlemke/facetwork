"""Tests for site-selection handlers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_tiger_geojson(features: list[dict[str, Any]]) -> str:
    """Write a TIGER-like GeoJSON to a temp file, return path."""
    fd, path = tempfile.mkstemp(suffix=".geojson")
    with os.fdopen(fd, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    return path


def _make_acs_csv(rows: list[dict[str, str]], columns: list[str]) -> str:
    """Write an ACS-like CSV to a temp file, return path."""
    import csv
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _county_feature(geoid: str, name: str, state_fips: str,
                    aland: int = 1_000_000_000,
                    geometry: dict | None = None,
                    **extra_props) -> dict[str, Any]:
    """Create a TIGER-like county feature."""
    if geometry is None:
        geometry = {
            "type": "Polygon",
            "coordinates": [[[-86.0, 32.0], [-85.0, 32.0],
                             [-85.5, 33.0], [-86.0, 32.0]]],
        }
    props = {
        "GEOID": geoid,
        "NAME": name,
        "STATEFP": state_fips,
        "ALAND": aland,
        **extra_props,
    }
    return {"type": "Feature", "properties": props, "geometry": geometry}


def _restaurant_feature(lon: float, lat: float, name: str = "Test",
                        amenity: str = "restaurant",
                        cuisine: str = "") -> dict[str, Any]:
    """Create a restaurant point feature."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"name": name, "amenity": amenity, "cuisine": cuisine},
    }


def _make_restaurants_geojson(features: list[dict[str, Any]]) -> str:
    """Write restaurants GeoJSON to a temp file, return path."""
    fd, path = tempfile.mkstemp(suffix=".geojson")
    with os.fdopen(fd, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    return path


# ---------------------------------------------------------------------------
# TestDownloadPBF
# ---------------------------------------------------------------------------


class TestDownloadPBF:
    def test_download_pbf_cached(self, tmp_path):
        """Test PBF download with existing cached file."""
        from handlers.shared.downloader import download_pbf

        cache_dir = str(tmp_path / "cache")
        pbf_dir = os.path.join(cache_dir, "pbf")
        os.makedirs(pbf_dir, exist_ok=True)

        # Pre-create cached file
        cached = os.path.join(pbf_dir, "alabama-latest.osm.pbf")
        Path(cached).write_bytes(b"fake pbf data")

        with patch.dict(os.environ, {"AFL_SITESEL_CACHE_DIR": cache_dir}):
            # Need to reimport to pick up env change
            import importlib
            import handlers.shared.downloader as dl
            importlib.reload(dl)
            result = dl.download_pbf(region="alabama")

        assert result["wasInCache"] is True
        assert result["region"] == "alabama"
        assert "alabama-latest.osm.pbf" in result["path"]

    def test_download_pbf_result_fields(self, tmp_path):
        """Test that download_pbf returns expected fields."""
        from handlers.shared.downloader import download_pbf

        cache_dir = str(tmp_path / "cache")
        pbf_dir = os.path.join(cache_dir, "pbf")
        os.makedirs(pbf_dir, exist_ok=True)
        cached = os.path.join(pbf_dir, "alaska-latest.osm.pbf")
        Path(cached).write_bytes(b"data")

        with patch.dict(os.environ, {"AFL_SITESEL_CACHE_DIR": cache_dir}):
            import importlib
            import handlers.shared.downloader as dl
            importlib.reload(dl)
            result = dl.download_pbf(region="alaska")

        assert "url" in result
        assert "path" in result
        assert "date" in result
        assert "size" in result
        assert result["region"] == "alaska"


# ---------------------------------------------------------------------------
# TestDemographicsExtractor
# ---------------------------------------------------------------------------


class TestDemographicsExtractor:
    def test_join_demographics_output(self, tmp_path):
        """Test that join_demographics produces a valid GeoJSON."""
        from handlers.extract.demographics_extractor import join_demographics

        tiger_path = _make_tiger_geojson([
            _county_feature("01001", "Autauga County", "01"),
        ])
        acs_path = _make_acs_csv(
            [{"GEOID": "0500000US01001", "NAME": "Autauga County",
              "B01003_001E": "55000", "B19013_001E": "55000",
              "B17001_001E": "55000", "B17001_002E": "5500",
              "B23025_001E": "30000", "B23025_002E": "25000",
              "B23025_003E": "24000", "B23025_005E": "1000",
              "B25003_001E": "20000", "B25003_002E": "14000",
              "B15003_001E": "40000", "B15003_022E": "4000",
              "B15003_023E": "1000", "B15003_024E": "500", "B15003_025E": "200",
              "B02001_001E": "55000", "B02001_002E": "40000"}],
            ["GEOID", "NAME", "B01003_001E", "B19013_001E",
             "B17001_001E", "B17001_002E",
             "B23025_001E", "B23025_002E", "B23025_003E", "B23025_005E",
             "B25003_001E", "B25003_002E",
             "B15003_001E", "B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E",
             "B02001_001E", "B02001_002E"],
        )

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(tmp_path)}):
            import importlib
            import handlers.extract.demographics_extractor as de
            importlib.reload(de)
            result = de.join_demographics(acs_path, tiger_path, "01")

        assert result["feature_count"] == 1
        assert result["state_fips"] == "01"
        assert os.path.exists(result["output_path"])

        with open(result["output_path"]) as f:
            geojson = json.load(f)
        assert geojson["type"] == "FeatureCollection"
        props = geojson["features"][0]["properties"]
        assert props["population"] == 55000.0

    def test_derived_fields(self, tmp_path):
        """Test that derived metrics are computed correctly."""
        from handlers.extract.demographics_extractor import _compute_derived_metrics

        props = {
            "B01003_001E": "100000",
            "B19013_001E": "60000",
            "B17001_001E": "100000",
            "B17001_002E": "15000",
            "B23025_001E": "50000",
            "B23025_002E": "45000",
            "B23025_003E": "44000",
            "B23025_005E": "2000",
            "B25003_001E": "30000",
            "B25003_002E": "20000",
            "B15003_001E": "80000",
            "B15003_022E": "10000",
            "B15003_023E": "5000",
            "B15003_024E": "2000",
            "B15003_025E": "1000",
        }
        derived = _compute_derived_metrics(props)
        assert derived["population"] == 100000.0
        assert derived["median_income"] == 60000.0
        assert derived["pct_below_poverty"] == 15.0  # 15000/100000*100
        assert abs(derived["unemployment_rate"] - 4.55) < 0.1  # 2000/44000*100
        assert derived["labor_force_participation"] == 90.0  # 45000/50000*100
        assert abs(derived["pct_owner_occupied"] - 66.67) < 0.1  # 20000/30000*100
        assert derived["pct_bachelors_plus"] == 22.5  # (10000+5000+2000+1000)/80000*100

    def test_population_density(self, tmp_path):
        """Test density computation from ALAND."""
        from handlers.extract.demographics_extractor import join_demographics

        tiger_path = _make_tiger_geojson([
            _county_feature("01001", "Test County", "01",
                            aland=2_000_000_000),  # 2000 km²
        ])
        acs_path = _make_acs_csv(
            [{"GEOID": "0500000US01001", "NAME": "Test County",
              "B01003_001E": "100000", "B19013_001E": "50000",
              "B17001_001E": "100000", "B17001_002E": "10000",
              "B23025_001E": "50000", "B23025_002E": "45000",
              "B23025_003E": "44000", "B23025_005E": "1000",
              "B25003_001E": "30000", "B25003_002E": "20000",
              "B15003_001E": "80000", "B15003_022E": "8000",
              "B15003_023E": "2000", "B15003_024E": "1000", "B15003_025E": "500",
              "B02001_001E": "100000", "B02001_002E": "70000"}],
            ["GEOID", "NAME", "B01003_001E", "B19013_001E",
             "B17001_001E", "B17001_002E",
             "B23025_001E", "B23025_002E", "B23025_003E", "B23025_005E",
             "B25003_001E", "B25003_002E",
             "B15003_001E", "B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E",
             "B02001_001E", "B02001_002E"],
        )

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(tmp_path)}):
            import importlib
            import handlers.extract.demographics_extractor as de
            importlib.reload(de)
            result = de.join_demographics(acs_path, tiger_path, "01")

        with open(result["output_path"]) as f:
            geojson = json.load(f)
        props = geojson["features"][0]["properties"]
        assert props["population_density_km2"] == 50.0  # 100000 / 2000

    def test_zero_population(self, tmp_path):
        """Test handling of zero-population county."""
        from handlers.extract.demographics_extractor import _compute_derived_metrics

        props = {
            "B01003_001E": "0",
            "B19013_001E": "-666666666",  # Census "no data"
            "B17001_001E": "0",
            "B17001_002E": "0",
        }
        derived = _compute_derived_metrics(props)
        assert derived["population"] == 0.0
        # pct_below_poverty should be None (0/0)
        assert "pct_below_poverty" not in derived

    def test_missing_columns(self, tmp_path):
        """Test handling when ACS columns are missing."""
        from handlers.extract.demographics_extractor import _compute_derived_metrics

        props = {"B01003_001E": "50000"}
        derived = _compute_derived_metrics(props)
        assert derived["population"] == 50000.0
        assert "median_income" not in derived
        assert "pct_below_poverty" not in derived


# ---------------------------------------------------------------------------
# TestRestaurantExtractor
# ---------------------------------------------------------------------------


class TestRestaurantExtractor:
    def test_extract_food_amenities_filter(self):
        """Test that only food amenities are included."""
        from handlers.extract.restaurant_extractor import FOOD_AMENITIES
        assert "restaurant" in FOOD_AMENITIES
        assert "fast_food" in FOOD_AMENITIES
        assert "cafe" in FOOD_AMENITIES
        assert "hospital" not in FOOD_AMENITIES
        assert "school" not in FOOD_AMENITIES

    def test_empty_result(self, tmp_path):
        """Test empty result when no PBF."""
        from handlers.extract.restaurant_extractor import _empty_result

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(tmp_path)}):
            import importlib
            import handlers.extract.restaurant_extractor as re_mod
            importlib.reload(re_mod)
            result = re_mod._empty_result("test")

        assert result["restaurant_count"] == 0
        assert result["region"] == "test"
        assert os.path.exists(result["output_path"])

    def test_output_format(self, tmp_path):
        """Test that output GeoJSON has correct structure."""
        from handlers.extract.restaurant_extractor import _empty_result

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(tmp_path)}):
            import importlib
            import handlers.extract.restaurant_extractor as re_mod
            importlib.reload(re_mod)
            result = re_mod._empty_result("test_format")

        with open(result["output_path"]) as f:
            geojson = json.load(f)
        assert geojson["type"] == "FeatureCollection"
        assert isinstance(geojson["features"], list)

    def test_extract_without_osmium(self, tmp_path):
        """Test extraction when pyosmium is not available."""
        from handlers.extract.restaurant_extractor import extract_restaurants

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(tmp_path)}):
            import importlib
            import handlers.extract.restaurant_extractor as re_mod
            # Temporarily disable osmium
            orig = re_mod.HAS_OSMIUM
            re_mod.HAS_OSMIUM = False
            importlib.reload(re_mod)
            try:
                re_mod.HAS_OSMIUM = False
                result = re_mod.extract_restaurants("/nonexistent.pbf", "test")
                assert result["restaurant_count"] == 0
            finally:
                re_mod.HAS_OSMIUM = orig


# ---------------------------------------------------------------------------
# TestScoring
# ---------------------------------------------------------------------------


class TestScoring:
    @pytest.fixture
    def scored_data(self, tmp_path):
        """Create demographics and restaurant data for scoring tests."""
        # Two counties: one dense urban, one rural
        demo_features = [
            _county_feature(
                "01001", "Urban County", "01",
                aland=500_000_000,
                geometry={
                    "type": "Polygon",
                    "coordinates": [[[-86.5, 32.0], [-85.5, 32.0],
                                     [-85.5, 33.0], [-86.5, 33.0],
                                     [-86.5, 32.0]]],
                },
            ),
            _county_feature(
                "01002", "Rural County", "01",
                aland=3_000_000_000,
                geometry={
                    "type": "Polygon",
                    "coordinates": [[[-88.0, 34.0], [-87.0, 34.0],
                                     [-87.0, 35.0], [-88.0, 35.0],
                                     [-88.0, 34.0]]],
                },
            ),
        ]
        # Add demographic derived fields
        demo_features[0]["properties"].update({
            "population": 200000.0,
            "median_income": 65000.0,
            "population_density_km2": 400.0,
            "pct_below_poverty": 12.0,
            "unemployment_rate": 4.0,
            "labor_force_participation": 70.0,
            "pct_bachelors_plus": 35.0,
            "pct_owner_occupied": 60.0,
        })
        demo_features[1]["properties"].update({
            "population": 10000.0,
            "median_income": 35000.0,
            "population_density_km2": 3.33,
            "pct_below_poverty": 25.0,
            "unemployment_rate": 8.0,
            "labor_force_participation": 55.0,
            "pct_bachelors_plus": 15.0,
            "pct_owner_occupied": 75.0,
        })
        demo_path = _make_tiger_geojson(demo_features)

        # Restaurants: 50 in urban county, 2 in rural
        restaurants = []
        for i in range(50):
            restaurants.append(
                _restaurant_feature(-86.0 + i * 0.01, 32.5,
                                    f"Urban Rest {i}"))
        restaurants.append(
            _restaurant_feature(-87.5, 34.5, "Rural Rest 1"))
        restaurants.append(
            _restaurant_feature(-87.3, 34.3, "Rural Rest 2"))
        rest_path = _make_restaurants_geojson(restaurants)

        return demo_path, rest_path, tmp_path

    def test_point_in_polygon_assignment(self, scored_data):
        """Test that restaurants are assigned to correct counties."""
        demo_path, rest_path, tmp_path = scored_data

        pytest.importorskip("shapely")
        from handlers.scoring.scoring_builder import _count_restaurants_per_county

        with open(demo_path) as f:
            demo = json.load(f)
        with open(rest_path) as f:
            rest = json.load(f)

        counts = _count_restaurants_per_county(
            demo["features"], rest["features"])
        assert counts.get("01001", 0) == 50
        assert counts.get("01002", 0) == 2

    def test_demand_index_computation(self, scored_data, tmp_path):
        """Test demand index is between 0 and 1."""
        demo_path, rest_path, out_tmp = scored_data

        pytest.importorskip("shapely")
        from handlers.scoring.scoring_builder import score_counties

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(out_tmp)}):
            import importlib
            import handlers.scoring.scoring_builder as sb
            importlib.reload(sb)
            result = sb.score_counties(demo_path, rest_path, "01")

        with open(result["output_path"]) as f:
            scored = json.load(f)

        for feat in scored["features"]:
            di = feat["properties"]["demand_index"]
            assert 0.0 <= di <= 1.0, f"Demand index out of range: {di}"

    def test_suitability_formula(self, scored_data, tmp_path):
        """Test suitability = demand * 100 / (1 + competition)."""
        demo_path, rest_path, out_tmp = scored_data

        pytest.importorskip("shapely")
        from handlers.scoring.scoring_builder import score_counties

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(out_tmp)}):
            import importlib
            import handlers.scoring.scoring_builder as sb
            importlib.reload(sb)
            result = sb.score_counties(demo_path, rest_path, "01")

        with open(result["output_path"]) as f:
            scored = json.load(f)

        for feat in scored["features"]:
            props = feat["properties"]
            di = props["demand_index"]
            rp1k = props["restaurants_per_1000"]
            expected = round(di * 100.0 / (1.0 + rp1k), 2)
            assert props["suitability_score"] == expected

    def test_restaurants_per_1000(self, scored_data, tmp_path):
        """Test restaurants_per_1000 computation."""
        demo_path, rest_path, out_tmp = scored_data

        pytest.importorskip("shapely")
        from handlers.scoring.scoring_builder import score_counties

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(out_tmp)}):
            import importlib
            import handlers.scoring.scoring_builder as sb
            importlib.reload(sb)
            result = sb.score_counties(demo_path, rest_path, "01")

        with open(result["output_path"]) as f:
            scored = json.load(f)

        # Urban: 50 restaurants / (200000/1000) = 0.25
        urban = [f for f in scored["features"]
                 if f["properties"]["NAME"] == "Urban County"][0]
        assert abs(urban["properties"]["restaurants_per_1000"] - 0.25) < 0.01

    def test_zero_population_county(self, tmp_path):
        """Test scoring with zero-population county."""
        demo_features = [
            _county_feature("01099", "Empty County", "01",
                            aland=1_000_000_000),
        ]
        demo_features[0]["properties"].update({
            "population": 0.0,
            "median_income": 0.0,
            "population_density_km2": 0.0,
            "pct_below_poverty": 0.0,
            "labor_force_participation": 0.0,
            "pct_bachelors_plus": 0.0,
            "pct_owner_occupied": 0.0,
        })
        demo_path = _make_tiger_geojson(demo_features)
        rest_path = _make_restaurants_geojson([])

        from handlers.scoring.scoring_builder import score_counties

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(tmp_path)}):
            import importlib
            import handlers.scoring.scoring_builder as sb
            importlib.reload(sb)
            result = sb.score_counties(demo_path, rest_path, "01")

        assert result["county_count"] == 1

    def test_empty_restaurants(self, tmp_path):
        """Test scoring with no restaurants at all."""
        demo_features = [
            _county_feature("01001", "Test County", "01"),
        ]
        demo_features[0]["properties"].update({
            "population": 50000.0,
            "median_income": 50000.0,
            "population_density_km2": 50.0,
            "pct_below_poverty": 15.0,
            "labor_force_participation": 65.0,
            "pct_bachelors_plus": 25.0,
            "pct_owner_occupied": 65.0,
        })
        demo_path = _make_tiger_geojson(demo_features)
        rest_path = _make_restaurants_geojson([])

        from handlers.scoring.scoring_builder import score_counties

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(tmp_path)}):
            import importlib
            import handlers.scoring.scoring_builder as sb
            importlib.reload(sb)
            result = sb.score_counties(demo_path, rest_path, "01")

        with open(result["output_path"]) as f:
            scored = json.load(f)

        props = scored["features"][0]["properties"]
        assert props["restaurant_count"] == 0
        assert props["restaurants_per_1000"] == 0.0
        # With no competition, suitability = demand * 100
        assert props["suitability_score"] == round(
            props["demand_index"] * 100.0, 2)

    def test_top_county_correctness(self, scored_data, tmp_path):
        """Test that top_county matches highest-scored county."""
        demo_path, rest_path, out_tmp = scored_data

        pytest.importorskip("shapely")
        from handlers.scoring.scoring_builder import score_counties

        with patch.dict(os.environ, {"AFL_SITESEL_OUTPUT_DIR": str(out_tmp)}):
            import importlib
            import handlers.scoring.scoring_builder as sb
            importlib.reload(sb)
            result = sb.score_counties(demo_path, rest_path, "01")

        with open(result["output_path"]) as f:
            scored = json.load(f)

        # First feature should be highest score (sorted desc)
        top = scored["features"][0]["properties"]
        assert top["NAME"] == result["top_county"]
        assert top["suitability_score"] == result["top_score"]

    def test_weight_normalization(self):
        """Test that demand weights sum to 1.0."""
        from handlers.scoring.scoring_builder import DEMAND_WEIGHTS
        total = sum(DEMAND_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-10


# ---------------------------------------------------------------------------
# TestExportScored
# ---------------------------------------------------------------------------


class TestExportScored:
    def test_output_path(self, tmp_path):
        """Test that export creates a file at the expected path."""
        scored_features = [
            _county_feature("01001", "Test County", "01"),
        ]
        scored_features[0]["properties"]["suitability_score"] = 75.0
        scored_path = _make_tiger_geojson(scored_features)

        from handlers.output.output_handlers import handle_export_scored

        with patch.dict(os.environ, {"AFL_LOCAL_OUTPUT_DIR": str(tmp_path)}):
            result = handle_export_scored({
                "scored_path": scored_path,
                "state_fips": "01",
                "_facet_name": "sitesel.Output.ExportScored",
            })

        assert result["result"]["format"] == "geojson"
        assert os.path.exists(result["result"]["output_path"])

    def test_export_format(self, tmp_path):
        """Test that exported file is valid GeoJSON."""
        scored_features = [
            _county_feature("01001", "Test County", "01"),
        ]
        scored_features[0]["properties"]["suitability_score"] = 80.0
        scored_path = _make_tiger_geojson(scored_features)

        from handlers.output.output_handlers import handle_export_scored

        with patch.dict(os.environ, {"AFL_LOCAL_OUTPUT_DIR": str(tmp_path)}):
            result = handle_export_scored({
                "scored_path": scored_path,
                "state_fips": "01",
                "_facet_name": "sitesel.Output.ExportScored",
            })

        with open(result["result"]["output_path"]) as f:
            geojson = json.load(f)
        assert geojson["type"] == "FeatureCollection"


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_download_dispatch(self):
        """Test download handler dispatch table."""
        from handlers.downloads.download_handlers import _DISPATCH, NAMESPACE
        assert f"{NAMESPACE}.DownloadACS" in _DISPATCH
        assert f"{NAMESPACE}.DownloadTIGER" in _DISPATCH
        assert f"{NAMESPACE}.DownloadPBF" in _DISPATCH

    def test_extract_dispatch(self):
        """Test extract handler dispatch table."""
        from handlers.extract.extract_handlers import _DISPATCH, NAMESPACE
        assert f"{NAMESPACE}.JoinDemographics" in _DISPATCH
        assert f"{NAMESPACE}.ExtractRestaurants" in _DISPATCH

    def test_scoring_dispatch(self):
        """Test scoring handler dispatch table."""
        from handlers.scoring.scoring_handlers import _DISPATCH, NAMESPACE
        assert f"{NAMESPACE}.ScoreCounties" in _DISPATCH

    def test_output_dispatch(self):
        """Test output handler dispatch table."""
        from handlers.output.output_handlers import _DISPATCH, NAMESPACE
        assert f"{NAMESPACE}.ExportScored" in _DISPATCH

    def test_handle_routing(self):
        """Test that handle() routes to correct handler."""
        from handlers.downloads.download_handlers import handle

        with pytest.raises(ValueError, match="Unknown facet"):
            handle({"_facet_name": "nonexistent.Facet"})

    def test_total_dispatch_count(self):
        """Test total number of registered facets."""
        from handlers.downloads.download_handlers import _DISPATCH as d_dispatch
        from handlers.extract.extract_handlers import _DISPATCH as e_dispatch
        from handlers.scoring.scoring_handlers import _DISPATCH as s_dispatch
        from handlers.output.output_handlers import _DISPATCH as o_dispatch

        total = len(d_dispatch) + len(e_dispatch) + len(s_dispatch) + len(o_dispatch)
        # 3 downloads + 2 extract + 1 scoring + 1 output = 7
        assert total == 7
