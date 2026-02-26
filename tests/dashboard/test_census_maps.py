# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for census map visualization routes."""

from __future__ import annotations

import json

import pytest

try:
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE, reason="fastapi or mongomock not installed"
)


# ---------------------------------------------------------------------------
# Helper unit tests (no server needed)
# ---------------------------------------------------------------------------

from afl.dashboard.routes.census_maps import (
    _FIELD_LABELS,
    _PREFERRED_FIELDS,
    _aggregate_state_stats,
    _build_comparison,
    _compute_stats,
    _decimate_ring,
    _features_to_csv,
    _get_field_label,
    _region_label,
    _simplify_geometry,
    _slim_properties,
)


class TestRegionLabel:
    def test_known_fips(self):
        assert _region_label("census.tiger.county.01") == "Alabama"

    def test_texas(self):
        assert _region_label("census.joined.48") == "Texas"

    def test_unknown_fips(self):
        assert _region_label("census.tiger.county.99") == ""

    def test_no_dots(self):
        assert _region_label("nodots") == ""

    def test_dc(self):
        assert _region_label("census.tiger.county.11") == "District of Columbia"


class TestDecimateRing:
    def test_short_ring_unchanged(self):
        ring = [[-86.0, 32.0], [-85.0, 32.0], [-85.5, 33.0], [-86.0, 32.0]]
        assert _decimate_ring(ring, max_points=80) == ring

    def test_long_ring_decimated(self):
        ring = [[float(i), float(i)] for i in range(200)]
        result = _decimate_ring(ring, max_points=50)
        assert len(result) <= 55  # ~50 + possible closure point
        assert result[0] == ring[0]
        assert result[-1] == ring[-1]

    def test_ring_closure_preserved(self):
        ring = [[float(i), 0.0] for i in range(300)]
        ring[-1] = ring[0]  # closed ring
        result = _decimate_ring(ring, max_points=50)
        assert result[-1] == result[0]


class TestSimplifyGeometry:
    def test_polygon(self):
        geom = {"type": "Polygon", "coordinates": [[[float(i), 0.0] for i in range(200)]]}
        result = _simplify_geometry(geom, max_points=50)
        assert result["type"] == "Polygon"
        assert len(result["coordinates"][0]) < 200

    def test_multipolygon(self):
        ring = [[float(i), 0.0] for i in range(200)]
        geom = {"type": "MultiPolygon", "coordinates": [[ring]]}
        result = _simplify_geometry(geom, max_points=50)
        assert result["type"] == "MultiPolygon"
        assert len(result["coordinates"][0][0]) < 200

    def test_point_passthrough(self):
        geom = {"type": "Point", "coordinates": [1.0, 2.0]}
        assert _simplify_geometry(geom) == geom


class TestSlimProperties:
    def test_strips_raw_acs(self):
        props = {"NAME": "Test", "population": 100, "B01003_001E": 100, "B19013_001E": 50000}
        result = _slim_properties(props)
        assert "NAME" in result
        assert "population" in result
        assert "B01003_001E" not in result
        assert "B19013_001E" not in result

    def test_strips_tiger_metadata(self):
        props = {"NAME": "Test", "AWATER": 123, "COUNTYNS": "abc", "MTFCC": "G4020"}
        result = _slim_properties(props)
        assert "NAME" in result
        assert "AWATER" not in result
        assert "COUNTYNS" not in result
        assert "MTFCC" not in result

    def test_keeps_friendly_fields(self):
        props = {"NAME": "Test", "population": 100, "median_income": 50000, "GEOID": "01001"}
        result = _slim_properties(props)
        assert result == props


class TestFieldLabels:
    def test_get_field_label_known(self):
        assert _get_field_label("population") == "Population"
        assert _get_field_label("median_income") == "Median Income"

    def test_get_field_label_unknown_passthrough(self):
        assert _get_field_label("some_custom_field") == "some_custom_field"

    def test_preferred_fields_have_labels(self):
        for field in _PREFERRED_FIELDS:
            label = _FIELD_LABELS.get(field)
            assert label is not None, f"Missing label for preferred field: {field}"

    def test_labels_in_map_view(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test County", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/census.joined.01")
        assert resp.status_code == 200
        assert "fieldLabels" in resp.text

    def test_labels_in_map_all(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert "fieldLabels" in resp.text

    def test_labels_in_table_view(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert "Population" in resp.text


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from afl.dashboard import dependencies as deps
    from afl.dashboard.app import create_app
    from afl.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_census_maps", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _seed_meta(store, dataset_key, facet_name="ExtractCounty", data_type="geojson_feature",
               record_count=67, imported_at=1708873045000):
    """Insert a handler_output_meta document."""
    store._db.handler_output_meta.insert_one({
        "dataset_key": dataset_key,
        "facet_name": facet_name,
        "record_count": record_count,
        "data_type": data_type,
        "imported_at": imported_at,
        "source_path": f"/tmp/{dataset_key}.geojson",
    })


def _seed_feature(store, dataset_key, feature_key, name, geometry, population=None):
    """Insert a handler_output document with GeoJSON geometry."""
    props = {"GEOID": feature_key, "NAME": name}
    if population is not None:
        props["population"] = population
    store._db.handler_output.insert_one({
        "dataset_key": dataset_key,
        "feature_key": feature_key,
        "facet_name": "ExtractCounty",
        "data_type": "geojson_feature",
        "properties": props,
        "geometry": geometry,
        "imported_at": 1708873045000,
    })


# A simple triangle polygon for testing
_TRIANGLE = {
    "type": "Polygon",
    "coordinates": [[[-86.0, 32.0], [-85.0, 32.0], [-85.5, 33.0], [-86.0, 32.0]]],
}

_TRIANGLE_2 = {
    "type": "Polygon",
    "coordinates": [[[-87.0, 33.0], [-86.0, 33.0], [-86.5, 34.0], [-87.0, 33.0]]],
}


# ---------------------------------------------------------------------------
# Dataset list page: GET /census/maps
# ---------------------------------------------------------------------------


class TestCensusMapList:
    def test_empty_list(self, client):
        tc, store = client
        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert "No GeoJSON datasets found" in resp.text

    def test_lists_geojson_datasets(self, client):
        tc, store = client
        _seed_meta(store, "census.tiger.county.01")
        _seed_meta(store, "census.joined.01")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert "census.tiger.county.01" in resp.text
        assert "census.joined.01" in resp.text

    def test_filters_non_geojson(self, client):
        """Only geojson_feature entries should appear."""
        tc, store = client
        _seed_meta(store, "census.tiger.county.01", data_type="geojson_feature")
        _seed_meta(store, "census.acs.income", data_type="csv_record")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert "census.tiger.county.01" in resp.text
        assert "census.acs.income" not in resp.text

    def test_shows_record_count(self, client):
        tc, store = client
        _seed_meta(store, "census.tiger.county.01", record_count=67)

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert "67" in resp.text

    def test_shows_facet_name(self, client):
        tc, store = client
        _seed_meta(store, "census.tiger.county.01", facet_name="ExtractCounty")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert "ExtractCounty" in resp.text

    def test_dataset_key_is_link(self, client):
        tc, store = client
        _seed_meta(store, "census.tiger.county.01")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert 'href="/census/maps/census.tiger.county.01"' in resp.text

    def test_sorted_by_dataset_key(self, client):
        tc, store = client
        _seed_meta(store, "census.tiger.county.99")
        _seed_meta(store, "census.joined.01")
        _seed_meta(store, "census.tiger.county.01")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        text = resp.text
        pos_joined = text.index("census.joined.01")
        pos_county01 = text.index("census.tiger.county.01")
        pos_county99 = text.index("census.tiger.county.99")
        assert pos_joined < pos_county01 < pos_county99

    def test_shows_region_name(self, client):
        tc, store = client
        _seed_meta(store, "census.tiger.county.01")
        _seed_meta(store, "census.tiger.county.48")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert "Alabama" in resp.text
        assert "Texas" in resp.text

    def test_unknown_fips_region_blank(self, client):
        tc, store = client
        _seed_meta(store, "census.tiger.county.99")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert "census.tiger.county.99" in resp.text


# ---------------------------------------------------------------------------
# Map view page: GET /census/maps/{dataset_key}
# ---------------------------------------------------------------------------


class TestCensusMapView:
    def test_empty_dataset(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert "0 features" in resp.text

    def test_renders_with_features(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.tiger.county.01", "01003", "Baldwin", _TRIANGLE_2, population=223234)

        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert "2 features" in resp.text
        assert "census.tiger.county.01" in resp.text

    def test_geojson_embedded_in_page(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)

        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert "FeatureCollection" in resp.text
        assert "Autauga" in resp.text

    def test_numeric_fields_detected(self, client):
        """Numeric properties should appear in the choropleth dropdown."""
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert "population" in resp.text

    def test_string_fields_not_in_dropdown(self, client):
        """String-only properties should not appear as choropleth options."""
        tc, store = client
        # Seed a feature with only string properties (NAME, GEOID)
        store._db.handler_output.insert_one({
            "dataset_key": "census.strings.only",
            "feature_key": "01001",
            "facet_name": "Test",
            "data_type": "geojson_feature",
            "properties": {"NAME": "Autauga", "GEOID": "01001"},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/maps/census.strings.only")
        assert resp.status_code == 200
        # The <select> element should not be rendered when no numeric fields exist
        assert '<select id="choropleth-field"' not in resp.text

    def test_raw_acs_codes_filtered_from_dropdown(self, client):
        """Raw ACS variable codes (B*) should not clutter the choropleth dropdown."""
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {
                "NAME": "Autauga", "GEOID": "01001",
                "population": 59285, "median_income": 69841,
                "B01003_001E": 59285, "B19013_001E": 69841,
                "ALAND": 1539631459, "STATEFP": 1,
            },
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/maps/census.joined.01")
        assert resp.status_code == 200
        text = resp.text
        # Friendly fields should appear as dropdown options
        assert '<option value="population">' in text
        assert '<option value="median_income">' in text
        # Raw ACS codes and TIGER IDs should NOT appear as dropdown options
        assert '<option value="B01003_001E">' not in text
        assert '<option value="B19013_001E">' not in text
        assert '<option value="ALAND">' not in text
        assert '<option value="STATEFP">' not in text

    def test_preferred_fields_ordered_first(self, client):
        """Preferred fields like population should appear before other numeric fields."""
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.02",
            "feature_key": "02001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {
                "NAME": "Test", "GEOID": "02001",
                "population": 100, "zzz_custom": 42, "median_income": 50000,
            },
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/maps/census.joined.02")
        assert resp.status_code == 200
        text = resp.text
        pos_pop = text.index(">Population<")
        pos_income = text.index(">Median Income<")
        pos_custom = text.index(">zzz_custom<")
        # population before median_income (preferred order), both before custom
        assert pos_pop < pos_income < pos_custom

    def test_skips_docs_without_geometry(self, client):
        """Documents lacking geometry should be excluded from the map."""
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)
        # Insert a doc with no geometry
        store._db.handler_output.insert_one({
            "dataset_key": "census.tiger.county.01",
            "feature_key": "01099",
            "facet_name": "ExtractCounty",
            "data_type": "geojson_feature",
            "properties": {"NAME": "NoGeo"},
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert "1 features" in resp.text

    def test_leaflet_loaded(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)

        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert "leaflet" in resp.text.lower()

    def test_back_link(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert 'href="/census/maps"' in resp.text

    def test_dataset_key_with_dots(self, client):
        """Ensure path parameter captures dotted dataset keys."""
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/maps/census.joined.01")
        assert resp.status_code == 200
        assert "census.joined.01" in resp.text
        assert "1 features" in resp.text

    def test_shows_region_name_in_heading(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)

        resp = tc.get("/census/maps/census.tiger.county.01")
        assert resp.status_code == 200
        assert "Alabama" in resp.text

    def test_unknown_fips_no_region(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.tiger.county.99")
        assert resp.status_code == 200
        assert "census.tiger.county.99" in resp.text


# ---------------------------------------------------------------------------
# GeoJSON API: GET /census/api/maps/{dataset_key}
# ---------------------------------------------------------------------------


class TestCensusMapAPI:
    def test_empty_response(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.tiger.county.01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []

    def test_returns_geojson(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.tiger.county.01", "01003", "Baldwin", _TRIANGLE_2, population=223234)

        resp = tc.get("/census/api/maps/census.tiger.county.01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2

    def test_feature_structure(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/census.tiger.county.01")
        data = resp.json()
        feat = data["features"][0]
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Polygon"
        assert feat["properties"]["NAME"] == "Autauga"
        assert feat["properties"]["population"] == 55869

    def test_excludes_docs_without_geometry(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)
        store._db.handler_output.insert_one({
            "dataset_key": "census.tiger.county.01",
            "feature_key": "01099",
            "facet_name": "ExtractCounty",
            "data_type": "geojson_feature",
            "properties": {"NAME": "NoGeo"},
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/api/maps/census.tiger.county.01")
        data = resp.json()
        assert len(data["features"]) == 1

    def test_content_type_json(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.tiger.county.01")
        assert "application/json" in resp.headers["content-type"]

    def test_does_not_leak_mongo_id(self, client):
        """The _id field from MongoDB should not appear in the response."""
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)

        resp = tc.get("/census/api/maps/census.tiger.county.01")
        data = resp.json()
        feat = data["features"][0]
        assert "_id" not in feat
        assert "_id" not in feat["properties"]

    def test_different_dataset_keys_isolated(self, client):
        """Features from one dataset_key should not appear in another."""
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)
        _seed_feature(store, "census.tiger.county.02", "02001", "Fairbanks", _TRIANGLE_2)

        resp_01 = tc.get("/census/api/maps/census.tiger.county.01")
        resp_02 = tc.get("/census/api/maps/census.tiger.county.02")

        data_01 = resp_01.json()
        data_02 = resp_02.json()
        assert len(data_01["features"]) == 1
        assert len(data_02["features"]) == 1
        assert data_01["features"][0]["properties"]["NAME"] == "Autauga"
        assert data_02["features"][0]["properties"]["NAME"] == "Fairbanks"


# ---------------------------------------------------------------------------
# Nav link
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Combined national map: GET /census/maps/_all
# ---------------------------------------------------------------------------


class TestCensusMapAll:
    def test_empty_page(self, client):
        tc, store = client
        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert "0 states" in resp.text
        assert "0 features" in resp.text

    def test_renders_with_data(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.joined.48", "48001", "Anderson", _TRIANGLE_2, population=58458)

        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert "2 states" in resp.text
        assert "2 features" in resp.text

    def test_has_choropleth_dropdown(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert '<select id="choropleth-field"' in resp.text
        assert '<option value="population">' in resp.text

    def test_has_back_link(self, client):
        tc, store = client
        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert 'href="/census/maps"' in resp.text

    def test_loads_via_ajax(self, client):
        """The combined page should fetch GeoJSON via AJAX, not embed it."""
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert "fetch('/census/api/maps/_all')" in resp.text
        # GeoJSON should NOT be embedded inline
        assert "FeatureCollection" not in resp.text

    def test_only_joined_datasets(self, client):
        """Combined view should only include census.joined.* datasets, not tiger."""
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert "1 states" in resp.text
        assert "1 features" in resp.text


# ---------------------------------------------------------------------------
# Combined national API: GET /census/api/maps/_all
# ---------------------------------------------------------------------------


class TestCensusMapAllAPI:
    def test_empty_response(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/_all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []

    def test_returns_combined_geojson(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.joined.48", "48001", "Anderson", _TRIANGLE_2, population=58458)

        resp = tc.get("/census/api/maps/_all")
        data = resp.json()
        assert len(data["features"]) == 2

    def test_adds_state_name(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/_all")
        data = resp.json()
        feat = data["features"][0]
        assert feat["properties"]["_state"] == "Alabama"

    def test_strips_raw_acs_codes(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"NAME": "Autauga", "population": 100, "B01003_001E": 100},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/api/maps/_all")
        data = resp.json()
        props = data["features"][0]["properties"]
        assert "population" in props
        assert "B01003_001E" not in props

    def test_excludes_tiger_datasets(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/_all")
        data = resp.json()
        assert len(data["features"]) == 1
        assert data["features"][0]["properties"]["_state"] == "Alabama"

    def test_simplifies_geometry(self, client):
        """Geometry should be decimated for the combined view."""
        tc, store = client
        # Create a polygon with many coordinates
        ring = [[float(i) * 0.01 - 86.0, 32.0 + float(i) * 0.001] for i in range(300)]
        ring.append(ring[0])
        big_geom = {"type": "Polygon", "coordinates": [ring]}
        _seed_feature(store, "census.joined.01", "01001", "Autauga", big_geom, population=55869)

        resp = tc.get("/census/api/maps/_all")
        data = resp.json()
        coords = data["features"][0]["geometry"]["coordinates"][0]
        assert len(coords) < 300  # should be decimated


# ---------------------------------------------------------------------------
# CSV helper: _features_to_csv
# ---------------------------------------------------------------------------


class TestFeaturesToCSV:
    def test_empty_features(self):
        assert _features_to_csv([]) == ""

    def test_flattens_properties(self):
        features = [
            {"type": "Feature", "properties": {"GEOID": "01001", "NAME": "Autauga", "population": 55869},
             "geometry": {"type": "Point", "coordinates": [0, 0]}},
        ]
        result = _features_to_csv(features)
        lines = result.strip().split("\n")
        assert len(lines) == 2  # header + 1 row
        assert "GEOID" in lines[0]
        assert "NAME" in lines[0]
        assert "population" in lines[0]
        assert "01001" in lines[1]

    def test_excludes_geometry(self):
        features = [
            {"type": "Feature", "properties": {"GEOID": "01001"}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
        ]
        result = _features_to_csv(features)
        assert "geometry" not in result
        assert "coordinates" not in result


# ---------------------------------------------------------------------------
# Download: GET /census/api/maps/{dataset_key}/download
# ---------------------------------------------------------------------------


class TestCensusMapDownload:
    def test_geojson_empty(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.tiger.county.01/download?format=geojson")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []

    def test_csv_empty(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.tiger.county.01/download?format=csv")
        assert resp.status_code == 200
        assert resp.content == b""

    def test_geojson_with_data(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/census.joined.01/download?format=geojson")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data["features"]) == 1
        assert data["features"][0]["properties"]["NAME"] == "Autauga"

    def test_csv_with_data(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/census.joined.01/download?format=csv")
        assert resp.status_code == 200
        lines = resp.content.decode().strip().split("\n")
        assert len(lines) == 2
        assert "Autauga" in lines[1]

    def test_content_disposition_geojson(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.joined.01/download?format=geojson")
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert ".geojson" in resp.headers.get("content-disposition", "")

    def test_content_disposition_csv(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.joined.01/download?format=csv")
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert ".csv" in resp.headers.get("content-disposition", "")

    def test_content_type_geojson(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.joined.01/download?format=geojson")
        assert "geo+json" in resp.headers["content-type"]

    def test_content_type_csv(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.joined.01/download?format=csv")
        assert "text/csv" in resp.headers["content-type"]

    def test_full_resolution_not_decimated(self, client):
        """Download should return full resolution geometry, not decimated."""
        tc, store = client
        ring = [[float(i) * 0.01 - 86.0, 32.0 + float(i) * 0.001] for i in range(200)]
        ring.append(ring[0])
        big_geom = {"type": "Polygon", "coordinates": [ring]}
        _seed_feature(store, "census.joined.01", "01001", "Autauga", big_geom, population=55869)

        resp = tc.get("/census/api/maps/census.joined.01/download?format=geojson")
        data = json.loads(resp.content)
        coords = data["features"][0]["geometry"]["coordinates"][0]
        assert len(coords) == 201  # full resolution

    def test_excludes_docs_without_geometry(self, client):
        tc, store = client
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE)
        store._db.handler_output.insert_one({
            "dataset_key": "census.tiger.county.01",
            "feature_key": "01099",
            "facet_name": "ExtractCounty",
            "data_type": "geojson_feature",
            "properties": {"NAME": "NoGeo"},
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/api/maps/census.tiger.county.01/download?format=geojson")
        data = json.loads(resp.content)
        assert len(data["features"]) == 1

    def test_invalid_format_returns_400(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/census.joined.01/download?format=xml")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Download all: GET /census/api/maps/_all/download
# ---------------------------------------------------------------------------


class TestCensusMapAllDownload:
    def test_geojson_download(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/_all/download?format=geojson")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data["features"]) == 1

    def test_csv_download(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/_all/download?format=csv")
        assert resp.status_code == 200
        lines = resp.content.decode().strip().split("\n")
        assert len(lines) == 2

    def test_only_joined_prefix(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.tiger.county.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/api/maps/_all/download?format=geojson")
        data = json.loads(resp.content)
        assert len(data["features"]) == 1

    def test_content_disposition(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/_all/download?format=csv")
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert "census_all_counties" in resp.headers.get("content-disposition", "")

    def test_slim_properties_applied(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"NAME": "Autauga", "population": 100, "B01003_001E": 100, "AWATER": 999},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/api/maps/_all/download?format=geojson")
        data = json.loads(resp.content)
        props = data["features"][0]["properties"]
        assert "population" in props
        assert "B01003_001E" not in props
        assert "AWATER" not in props

    def test_invalid_format_returns_400(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/_all/download?format=xml")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Stats helper: _compute_stats
# ---------------------------------------------------------------------------


class TestComputeStats:
    def test_basic_stats(self):
        features = [
            {"properties": {"pop": 10, "income": 100}},
            {"properties": {"pop": 20, "income": 200}},
            {"properties": {"pop": 30, "income": 300}},
            {"properties": {"pop": 40, "income": 400}},
        ]
        stats = _compute_stats(features, ["pop", "income"])
        assert stats["pop"]["min"] == 10
        assert stats["pop"]["max"] == 40
        assert stats["pop"]["mean"] == 25.0
        assert stats["pop"]["median"] == 25.0  # (20+30)/2

    def test_empty_features(self):
        assert _compute_stats([], ["pop"]) == {}

    def test_non_numeric_skipped(self):
        features = [
            {"properties": {"name": "Alabama", "pop": 100}},
        ]
        stats = _compute_stats(features, ["name", "pop"])
        assert "name" not in stats
        assert "pop" in stats

    def test_single_feature(self):
        features = [{"properties": {"pop": 42}}]
        stats = _compute_stats(features, ["pop"])
        assert stats["pop"]["min"] == 42
        assert stats["pop"]["max"] == 42
        assert stats["pop"]["mean"] == 42.0
        assert stats["pop"]["median"] == 42.0


# ---------------------------------------------------------------------------
# Table view: GET /census/maps/{dataset_key}/table
# ---------------------------------------------------------------------------


class TestCensusTableView:
    def test_empty_table(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert "0 features" in resp.text

    def test_features_rendered(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)
        _seed_feature(store, "census.joined.01", "01003", "Baldwin", _TRIANGLE_2, population=223234)

        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert "2 features" in resp.text
        assert "Autauga" in resp.text
        assert "Baldwin" in resp.text

    def test_columns_ordered(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=55869)

        resp = tc.get("/census/maps/census.joined.01/table")
        text = resp.text
        # Find the data table header (id="data-table")
        table_start = text.index('id="data-table"')
        table_section = text[table_start:]
        pos_geoid = table_section.index(">GEOID<")
        pos_name = table_section.index(">NAME<")
        pos_pop = table_section.index(">Population<")
        assert pos_geoid < pos_name < pos_pop

    def test_stats_computed(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.01", "01003", "Baldwin", _TRIANGLE_2, population=200)

        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert "Summary Statistics" in resp.text

    def test_map_link(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert 'href="/census/maps/census.joined.01"' in resp.text
        assert "Map View" in resp.text

    def test_back_link(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert 'href="/census/maps"' in resp.text

    def test_region_heading(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)

        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert "Alabama" in resp.text

    def test_download_buttons_present(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert "Download GeoJSON" in resp.text
        assert "Download CSV" in resp.text


class TestMapViewTableLink:
    def test_map_has_table_link(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE)

        resp = tc.get("/census/maps/census.joined.01")
        assert resp.status_code == 200
        assert 'href="/census/maps/census.joined.01/table"' in resp.text

    def test_table_has_map_link(self, client):
        tc, store = client
        resp = tc.get("/census/maps/census.joined.01/table")
        assert resp.status_code == 200
        assert 'href="/census/maps/census.joined.01"' in resp.text


# ---------------------------------------------------------------------------
# State aggregation helper: _aggregate_state_stats
# ---------------------------------------------------------------------------


class TestAggregateStateStats:
    def test_empty(self):
        assert _aggregate_state_stats([]) == []

    def test_single_state(self):
        features = [
            {"properties": {"STATEFP": "01", "population": 100, "housing_units": 50,
                             "median_income": 40000, "ALAND": 1000000}},
            {"properties": {"STATEFP": "01", "population": 200, "housing_units": 80,
                             "median_income": 60000, "ALAND": 2000000}},
        ]
        result = _aggregate_state_stats(features)
        assert len(result) == 1
        assert result[0]["state_fips"] == "01"
        assert result[0]["state_name"] == "Alabama"
        assert result[0]["county_count"] == 2
        assert result[0]["total_population"] == 300
        assert result[0]["total_housing_units"] == 130

    def test_two_states(self):
        features = [
            {"properties": {"STATEFP": "01", "population": 100}},
            {"properties": {"STATEFP": "48", "population": 200}},
        ]
        result = _aggregate_state_stats(features)
        assert len(result) == 2
        assert result[0]["state_fips"] == "01"
        assert result[1]["state_fips"] == "48"

    def test_weighted_income(self):
        features = [
            {"properties": {"STATEFP": "01", "population": 100, "median_income": 50000}},
            {"properties": {"STATEFP": "01", "population": 300, "median_income": 70000}},
        ]
        result = _aggregate_state_stats(features)
        # (100*50000 + 300*70000) / 400 = 26_000_000 / 400 = 65000
        assert result[0]["weighted_median_income"] == 65000.0

    def test_population_density(self):
        features = [
            {"properties": {"STATEFP": "01", "population": 1000, "ALAND": 1000000000}},
        ]
        result = _aggregate_state_stats(features)
        # 1000 / (1e9/1e6) = 1000 / 1000 = 1.0
        assert result[0]["population_density"] == 1.0

    def test_missing_fields_default_zero(self):
        features = [
            {"properties": {"STATEFP": "01"}},
        ]
        result = _aggregate_state_stats(features)
        assert result[0]["total_population"] == 0
        assert result[0]["total_housing_units"] == 0
        assert result[0]["weighted_median_income"] == 0.0
        assert result[0]["population_density"] == 0.0


# ---------------------------------------------------------------------------
# State summary page: GET /census/maps/states
# ---------------------------------------------------------------------------


class TestCensusMapStates:
    def test_empty_page(self, client):
        tc, store = client
        resp = tc.get("/census/maps/states")
        assert resp.status_code == 200
        assert "0 states" in resp.text

    def test_renders_with_data(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"STATEFP": "01", "NAME": "Autauga", "GEOID": "01001",
                           "population": 55869, "housing_units": 22000, "median_income": 55000,
                           "ALAND": 1539631459},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/maps/states")
        assert resp.status_code == 200
        assert "1 states" in resp.text
        assert "Alabama" in resp.text

    def test_choropleth_dropdown(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"STATEFP": "01", "population": 100, "GEOID": "01001"},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/maps/states")
        assert resp.status_code == 200
        assert '<select id="choropleth-field"' in resp.text
        assert "total_population" in resp.text

    def test_summary_table(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"STATEFP": "01", "NAME": "Autauga", "GEOID": "01001",
                           "population": 55869},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/maps/states")
        assert resp.status_code == 200
        assert "Summary Statistics" in resp.text
        assert "55869" in resp.text

    def test_back_link(self, client):
        tc, store = client
        resp = tc.get("/census/maps/states")
        assert resp.status_code == 200
        assert 'href="/census/maps"' in resp.text


# ---------------------------------------------------------------------------
# State summary API: GET /census/api/maps/states
# ---------------------------------------------------------------------------


class TestCensusMapStatesAPI:
    def test_empty_response(self, client):
        tc, store = client
        resp = tc.get("/census/api/maps/states")
        assert resp.status_code == 200
        data = resp.json()
        assert data["features"] == []

    def test_features_have_state_value(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"STATEFP": "01", "NAME": "Autauga", "GEOID": "01001",
                           "population": 55869},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/api/maps/states?field=total_population")
        data = resp.json()
        assert len(data["features"]) == 1
        assert "_state_value" in data["features"][0]["properties"]
        assert data["features"][0]["properties"]["_state_value"] == 55869

    def test_field_parameter(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"STATEFP": "01", "NAME": "Autauga", "GEOID": "01001",
                           "population": 100, "housing_units": 50},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/api/maps/states?field=total_housing_units")
        data = resp.json()
        assert data["features"][0]["properties"]["_state_value"] == 50

    def test_only_joined_datasets(self, client):
        tc, store = client
        store._db.handler_output.insert_one({
            "dataset_key": "census.joined.01",
            "feature_key": "01001",
            "facet_name": "Joined",
            "data_type": "geojson_feature",
            "properties": {"STATEFP": "01", "population": 100, "GEOID": "01001"},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })
        store._db.handler_output.insert_one({
            "dataset_key": "census.tiger.county.01",
            "feature_key": "01001",
            "facet_name": "Tiger",
            "data_type": "geojson_feature",
            "properties": {"STATEFP": "01", "population": 100, "GEOID": "01001"},
            "geometry": _TRIANGLE,
            "imported_at": 1708873045000,
        })

        resp = tc.get("/census/api/maps/states")
        data = resp.json()
        assert len(data["features"]) == 1


# ---------------------------------------------------------------------------
# State-Level Summary link on datasets page
# ---------------------------------------------------------------------------


class TestStateSummaryLink:
    def test_states_link_present(self, client):
        tc, store = client
        _seed_meta(store, "census.joined.01")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert 'href="/census/maps/states"' in resp.text


# ---------------------------------------------------------------------------
# Comparison helper: _build_comparison
# ---------------------------------------------------------------------------


class TestBuildComparison:
    def test_basic_diff(self):
        left = {"pop": {"min": 10, "max": 100, "mean": 50.0, "median": 45.0}}
        right = {"pop": {"min": 20, "max": 200, "mean": 80.0, "median": 75.0}}
        rows = _build_comparison(left, right, ["pop"])
        assert len(rows) == 1
        assert rows[0]["field"] == "pop"
        assert rows[0]["left"] == 50.0
        assert rows[0]["right"] == 80.0
        assert rows[0]["difference"] == 30.0

    def test_empty_features(self):
        rows = _build_comparison({}, {}, ["pop"])
        assert len(rows) == 1
        assert rows[0]["left"] == 0
        assert rows[0]["right"] == 0
        assert rows[0]["difference"] == 0

    def test_missing_fields(self):
        left = {"pop": {"min": 10, "max": 100, "mean": 50.0, "median": 45.0}}
        rows = _build_comparison(left, {}, ["pop", "income"])
        assert len(rows) == 2
        assert rows[0]["left"] == 50.0
        assert rows[0]["right"] == 0
        assert rows[1]["left"] == 0


# ---------------------------------------------------------------------------
# Compare states: GET /census/compare
# ---------------------------------------------------------------------------


class TestCensusCompare:
    def test_empty_form_shows_selectors(self, client):
        tc, store = client
        resp = tc.get("/census/compare")
        assert resp.status_code == 200
        assert "Compare States" in resp.text
        assert "<form" in resp.text
        assert "Compare" in resp.text  # submit button

    def test_available_datasets_populated(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.48", "48001", "Anderson", _TRIANGLE_2, population=200)

        resp = tc.get("/census/compare")
        assert resp.status_code == 200
        assert "census.joined.01" in resp.text
        assert "census.joined.48" in resp.text

    def test_compare_two_states(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.48", "48001", "Anderson", _TRIANGLE_2, population=200)

        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.48")
        assert resp.status_code == 200
        assert "Alabama" in resp.text
        assert "Texas" in resp.text

    def test_comparison_table_present(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.48", "48001", "Anderson", _TRIANGLE_2, population=200)

        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.48")
        assert resp.status_code == 200
        assert "Comparison" in resp.text
        assert "Difference" in resp.text

    def test_two_maps_rendered(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.48", "48001", "Anderson", _TRIANGLE_2, population=200)

        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.48")
        assert resp.status_code == 200
        assert 'id="map-left"' in resp.text
        assert 'id="map-right"' in resp.text

    def test_shared_choropleth(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.48", "48001", "Anderson", _TRIANGLE_2, population=200)

        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.48")
        assert resp.status_code == 200
        assert '<select id="choropleth-field"' in resp.text

    def test_left_only_shows_selector(self, client):
        tc, store = client
        resp = tc.get("/census/compare?left=census.joined.01")
        assert resp.status_code == 200
        # Should show the form but no maps
        assert "<form" in resp.text
        assert 'id="map-left"' not in resp.text

    def test_back_link(self, client):
        tc, store = client
        resp = tc.get("/census/compare")
        assert resp.status_code == 200
        assert 'href="/census/maps"' in resp.text

    def test_same_state_comparison(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "01001", "Autauga", _TRIANGLE, population=100)

        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.01")
        assert resp.status_code == 200
        assert "Comparison" in resp.text


# ---------------------------------------------------------------------------
# Compare States link on datasets page
# ---------------------------------------------------------------------------


class TestCompareLink:
    def test_compare_link_present(self, client):
        tc, store = client
        _seed_meta(store, "census.joined.01")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert 'href="/census/compare"' in resp.text


# ---------------------------------------------------------------------------
# Dataset list: "View All" link
# ---------------------------------------------------------------------------


class TestViewAllLink:
    def test_view_all_link_present(self, client):
        tc, store = client
        _seed_meta(store, "census.joined.01")

        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert 'href="/census/maps/_all"' in resp.text

    def test_view_all_link_absent_when_empty(self, client):
        tc, store = client
        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert 'href="/census/maps/_all"' not in resp.text


# ---------------------------------------------------------------------------
# Nav link
# ---------------------------------------------------------------------------


class TestNavLink:
    def test_census_maps_in_nav(self, client):
        tc, store = client
        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert 'href="/census/maps"' in resp.text
        assert "Census Maps" in resp.text

    def test_active_tab_highlighted(self, client):
        tc, store = client
        resp = tc.get("/census/maps")
        assert resp.status_code == 200
        assert 'class="nav-active"' in resp.text


class TestColorLegend:
    """Verify compare view has a color legend."""

    def test_compare_has_legend_div(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.02", "GEO2", "Test2", _TRIANGLE_2, population=200)
        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.02")
        assert resp.status_code == 200
        assert 'id="legend"' in resp.text

    def test_compare_legend_has_min_max(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.02", "GEO2", "Test2", _TRIANGLE_2, population=200)
        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.02")
        assert resp.status_code == 200
        assert 'id="legend-min"' in resp.text
        assert 'id="legend-max"' in resp.text

    def test_compare_legend_js_update(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.02", "GEO2", "Test2", _TRIANGLE_2, population=200)
        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.02")
        assert resp.status_code == 200
        assert "updateLegend" in resp.text


class TestPopupContent:
    """Verify focused popups with field labels."""

    def test_map_view_has_popup_fields(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/census.joined.01")
        assert resp.status_code == 200
        assert "popupFields" in resp.text
        assert "fieldLabels" in resp.text

    def test_map_view_has_view_all_fields_link(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/census.joined.01")
        assert resp.status_code == 200
        assert "View all fields" in resp.text

    def test_map_all_has_popup_fields(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert "popupFields" in resp.text

    def test_compare_has_popup_fields(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        _seed_feature(store, "census.joined.02", "GEO2", "Test2", _TRIANGLE_2, population=200)
        resp = tc.get("/census/compare?left=census.joined.01&right=census.joined.02")
        assert resp.status_code == 200
        assert "popupFields" in resp.text


class TestAjaxErrorHandling:
    """Verify AJAX fetch calls have error handling."""

    def test_map_all_has_catch(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert ".catch(" in resp.text

    def test_map_all_checks_resp_ok(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/_all")
        assert resp.status_code == 200
        assert "resp.ok" in resp.text

    def test_map_states_has_catch(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/states")
        assert resp.status_code == 200
        assert ".catch(" in resp.text

    def test_map_states_checks_resp_ok(self, client):
        tc, store = client
        _seed_feature(store, "census.joined.01", "GEO1", "Test", _TRIANGLE, population=100)
        resp = tc.get("/census/maps/states")
        assert resp.status_code == 200
        assert "resp.ok" in resp.text
