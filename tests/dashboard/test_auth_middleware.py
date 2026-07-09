"""Opt-in FW_DASHBOARD_TOKEN auth on mutating requests.

Default (no token): everything is open, as before. With the token set: mutating
requests (POST/PUT/PATCH/DELETE) require it; reads stay open; a query-param token
is persisted as a cookie for the browser UI.
"""
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

needs = pytest.mark.skipif(
    not (FASTAPI_AVAILABLE and MONGOMOCK_AVAILABLE), reason="fastapi or mongomock not installed"
)

TOKEN = "s3cr3t-token"


@pytest.fixture
def client():
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_auth", client=mongomock.MongoClient())
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    with TestClient(app, follow_redirects=False) as tc:
        yield tc, store
    store.drop_database()
    store.close()


@needs
def test_no_token_configured_is_open(client, monkeypatch):
    monkeypatch.delenv("FW_DASHBOARD_TOKEN", raising=False)
    tc, _ = client
    # a mutating call is not blocked by auth (may 4xx for other reasons, but never 401)
    r = tc.post("/api/handlers", json={})
    assert r.status_code != 401


@needs
def test_mutating_blocked_without_token(client, monkeypatch):
    monkeypatch.setenv("FW_DASHBOARD_TOKEN", TOKEN)
    tc, _ = client
    r = tc.post("/api/handlers", json={"facet_name": "x", "module_uri": "evil"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


@needs
def test_reads_open_even_with_token(client, monkeypatch):
    monkeypatch.setenv("FW_DASHBOARD_TOKEN", TOKEN)
    tc, _ = client
    r = tc.get("/v3/workflows")
    assert r.status_code != 401


@needs
def test_mutating_allowed_with_bearer(client, monkeypatch):
    monkeypatch.setenv("FW_DASHBOARD_TOKEN", TOKEN)
    tc, _ = client
    r = tc.post("/api/handlers", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code != 401  # passes auth (may fail validation downstream)


@needs
def test_wrong_token_rejected(client, monkeypatch):
    monkeypatch.setenv("FW_DASHBOARD_TOKEN", TOKEN)
    tc, _ = client
    r = tc.post("/api/handlers", json={}, headers={"X-FW-Token": "nope"})
    assert r.status_code == 401
