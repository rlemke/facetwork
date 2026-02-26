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

from afl.dashboard.routes.census_maps import _region_label


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
        pos_pop = text.index(">population<")
        pos_income = text.index(">median_income<")
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
