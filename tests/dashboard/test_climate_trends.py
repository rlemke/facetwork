"""Tests for climate trends dashboard routes."""

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


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_climate", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _examples_db(store):
    """Return the examples database from the mongomock client."""
    import os

    db_name = os.environ.get("AFL_EXAMPLES_DATABASE", "facetwork_examples")
    return store._db.client[db_name]


def _seed_state_year(store, state: str, year: int, temp_mean: float = 12.0, precip: float = 900.0):
    """Insert a climate_state_years document."""
    _examples_db(store)["climate_state_years"].insert_one(
        {
            "state": state,
            "year": year,
            "station_count": 3,
            "temp_mean": temp_mean,
            "temp_min_avg": temp_mean - 7,
            "temp_max_avg": temp_mean + 7,
            "precip_annual": precip,
            "hot_days": 15,
            "frost_days": 60,
            "precip_days": 100,
        }
    )


def _seed_trend(store, state: str, warming: float = 0.15, precip_pct: float = 5.0):
    """Insert a climate_trends document."""
    _examples_db(store)["climate_trends"].insert_one(
        {
            "state": state,
            "start_year": 1944,
            "end_year": 2024,
            "warming_rate_per_decade": warming,
            "precip_change_pct": precip_pct,
            "decades": {"1940s": {"avg_temp": 10.0}, "2020s": {"avg_temp": 12.0}},
            "narrative": f"Climate analysis for {state}.",
        }
    )


class TestClimateTrendsPage:
    def test_page_loads_empty(self, client):
        tc, store = client
        resp = tc.get("/climate-trends/")
        assert resp.status_code == 200
        assert "Climate Trends" in resp.text

    def test_page_shows_state_options(self, client):
        tc, store = client
        _seed_trend(store, "NY")
        _seed_trend(store, "CA")
        resp = tc.get("/climate-trends/")
        assert resp.status_code == 200
        assert "NY" in resp.text
        assert "CA" in resp.text

    def test_sidebar_link_present(self, client):
        tc, store = client
        resp = tc.get("/climate-trends/")
        assert resp.status_code == 200
        assert "Climate Trends" in resp.text
        assert "/climate-trends/" in resp.text


class TestClimateTrendsAPI:
    def test_states_empty(self, client):
        tc, store = client
        resp = tc.get("/climate-trends/api/states")
        assert resp.status_code == 200
        data = resp.json()
        assert data["states"] == []

    def test_states_with_data(self, client):
        tc, store = client
        _seed_trend(store, "NY")
        resp = tc.get("/climate-trends/api/states")
        data = resp.json()
        assert len(data["states"]) == 1
        assert data["states"][0]["code"] == "NY"

    def test_data_empty_state(self, client):
        tc, store = client
        resp = tc.get("/climate-trends/api/data?state=ZZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "ZZ"
        assert data["yearly"] == []
        assert data["trend"] == {}

    def test_data_with_seeded(self, client):
        tc, store = client
        _seed_trend(store, "NY")
        _seed_state_year(store, "NY", 2020, 12.5, 1100.0)
        _seed_state_year(store, "NY", 2021, 13.0, 1050.0)

        resp = tc.get("/climate-trends/api/data?state=NY")
        data = resp.json()
        assert data["state"] == "NY"
        assert len(data["yearly"]) == 2
        assert data["trend"]["warming_rate_per_decade"] == 0.15
        assert "Climate analysis" in data["narrative"]

    def test_compare_endpoint(self, client):
        tc, store = client
        _seed_trend(store, "NY")
        _seed_trend(store, "CA")
        _seed_state_year(store, "NY", 2020)
        _seed_state_year(store, "CA", 2020)

        resp = tc.get("/climate-trends/api/compare?states=NY,CA")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["states"]) == {"NY", "CA"}
        assert "NY" in data["data"]
        assert "CA" in data["data"]

    def test_compare_empty(self, client):
        tc, store = client
        resp = tc.get("/climate-trends/api/compare?states=")
        assert resp.status_code == 200
        data = resp.json()
        assert data["states"] == []


class TestStateNames:
    def test_state_names_in_page(self, client):
        tc, store = client
        _seed_trend(store, "NY")
        _seed_trend(store, "CA")
        resp = tc.get("/climate-trends/")
        assert resp.status_code == 200
        assert "New York" in resp.text
        assert "California" in resp.text

    def test_state_names_in_api(self, client):
        tc, store = client
        _seed_trend(store, "NY")
        _seed_trend(store, "FL")
        resp = tc.get("/climate-trends/api/states")
        data = resp.json()
        names = {s["code"]: s["name"] for s in data["states"]}
        assert names["NY"] == "New York"
        assert names["FL"] == "Florida"

    def test_unknown_state_code_uses_code(self, client):
        tc, store = client
        _seed_trend(store, "ZZ")
        resp = tc.get("/climate-trends/api/states")
        data = resp.json()
        assert data["states"][0]["name"] == "ZZ"
