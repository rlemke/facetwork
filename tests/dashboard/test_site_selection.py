"""Tests for site-selection dashboard routes."""

from __future__ import annotations

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
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE,
    reason="fastapi or mongomock not installed",
)


# ---------------------------------------------------------------------------
# Helper unit tests (no server needed)
# ---------------------------------------------------------------------------

from facetwork.dashboard.routes.domain.site_selection import (
    _FIELD_LABELS,
    _PREFERRED_FIELDS,
    _filter_numeric_fields,
    _get_field_label,
)


class TestFieldLabels:
    def test_get_field_label_known(self):
        assert _get_field_label("suitability_score") == "Suitability Score"
        assert _get_field_label("demand_index") == "Demand Index"

    def test_get_field_label_unknown_passthrough(self):
        assert _get_field_label("unknown_field") == "unknown_field"

    def test_preferred_fields_have_labels(self):
        for field in _PREFERRED_FIELDS:
            label = _FIELD_LABELS.get(field)
            assert label is not None, f"Missing label for field: {field}"


class TestFilterNumericFields:
    def test_preferred_fields_first(self):
        sample = {
            "suitability_score": 75.0,
            "population": 50000,
            "median_income": 55000,
            "custom_field": 42.0,
            "NAME": "Test",  # Non-numeric
        }
        result = _filter_numeric_fields(sample)
        assert result[0] == "suitability_score"
        assert "custom_field" in result
        assert "NAME" not in result

    def test_skips_raw_acs_fields(self):
        sample = {
            "suitability_score": 75.0,
            "B01003_001E": 50000,
            "ALAND": 1000000,
        }
        result = _filter_numeric_fields(sample)
        assert "suitability_score" in result
        assert "B01003_001E" not in result
        assert "ALAND" not in result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_TRIANGLE = {
    "type": "Polygon",
    "coordinates": [[[-86.0, 32.0], [-85.0, 32.0], [-85.5, 33.0], [-86.0, 32.0]]],
}


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_site_sel", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _seed_meta(store, dataset_key: str, facet_name: str = "ExportScored", record_count: int = 67):
    """Insert a handler_output_meta document."""
    store._db.handler_output_meta.insert_one(
        {
            "dataset_key": dataset_key,
            "facet_name": facet_name,
            "record_count": record_count,
            "data_type": "geojson_feature",
            "imported_at": 1708873045000,
        }
    )


def _seed_feature(store, dataset_key: str, geoid: str, name: str, geometry: dict, **extra_props):
    """Insert a handler_output_data document with scored data."""
    props = {
        "GEOID": geoid,
        "NAME": name,
        "suitability_score": 75.0,
        "demand_index": 0.65,
        "restaurant_count": 10,
        "restaurants_per_1000": 0.5,
        "population": 50000,
        "median_income": 55000,
        **extra_props,
    }
    store._db.handler_output.insert_one(
        {
            "dataset_key": dataset_key,
            "feature_key": geoid,
            "facet_name": "ExportScored",
            "data_type": "geojson_feature",
            "properties": props,
            "geometry": geometry,
            "imported_at": 1708873045000,
        }
    )


# ---------------------------------------------------------------------------
# TestSiteSelectionList
# ---------------------------------------------------------------------------


class TestSiteSelectionList:
    def test_empty_list(self, client):
        tc, store = client
        resp = tc.get("/site-selection/")
        assert resp.status_code == 200
        assert "No scored datasets found" in resp.text

    def test_lists_states(self, client):
        tc, store = client
        _seed_meta(store, "sitesel.scored.01", record_count=67)
        _seed_meta(store, "sitesel.scored.02", record_count=29)

        resp = tc.get("/site-selection/")
        assert resp.status_code == 200
        assert "Alabama" in resp.text
        assert "Alaska" in resp.text


# ---------------------------------------------------------------------------
# TestSiteSelectionMap
# ---------------------------------------------------------------------------


class TestSiteSelectionMap:
    def test_map_renders(self, client):
        tc, store = client
        _seed_feature(store, "sitesel.scored.01", "01001", "Autauga", _TRIANGLE)
        resp = tc.get("/site-selection/01")
        assert resp.status_code == 200
        assert "Leaflet" in resp.text or "leaflet" in resp.text

    def test_choropleth_js_present(self, client):
        tc, store = client
        _seed_feature(store, "sitesel.scored.01", "01001", "Autauga", _TRIANGLE)
        resp = tc.get("/site-selection/01")
        assert resp.status_code == 200
        assert "choropleth-field" in resp.text

    def test_field_labels_in_view(self, client):
        tc, store = client
        _seed_feature(store, "sitesel.scored.01", "01001", "Autauga", _TRIANGLE)
        resp = tc.get("/site-selection/01")
        assert resp.status_code == 200
        assert "fieldLabels" in resp.text


# ---------------------------------------------------------------------------
# TestSiteSelectionTable
# ---------------------------------------------------------------------------


class TestSiteSelectionTable:
    def test_table_renders(self, client):
        tc, store = client
        _seed_feature(
            store, "sitesel.scored.01", "01001", "Autauga", _TRIANGLE, suitability_score=80.0
        )
        _seed_feature(
            store, "sitesel.scored.01", "01002", "Baldwin", _TRIANGLE, suitability_score=60.0
        )
        resp = tc.get("/site-selection/01/table")
        assert resp.status_code == 200
        assert "Autauga" in resp.text
        assert "Baldwin" in resp.text

    def test_table_has_headers(self, client):
        tc, store = client
        _seed_feature(store, "sitesel.scored.01", "01001", "Test", _TRIANGLE)
        resp = tc.get("/site-selection/01/table")
        assert resp.status_code == 200
        assert "Suitability Score" in resp.text
        assert "Population" in resp.text


# ---------------------------------------------------------------------------
# TestSiteSelectionAPI
# ---------------------------------------------------------------------------


class TestSiteSelectionAPI:
    def test_geojson_endpoint(self, client):
        tc, store = client
        _seed_feature(store, "sitesel.scored.01", "01001", "Autauga", _TRIANGLE)
        resp = tc.get("/site-selection/api/01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1
