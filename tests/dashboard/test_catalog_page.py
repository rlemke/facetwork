"""Dashboard catalog page tests (TestClient + mongomock)."""

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

ADDER = """namespace claude.demo {
    workflow Adder(a: Int = 2, b: Int = 3) => (sum: Int) andThen {
        yield Adder(sum = $.a + $.b)
    }
}"""


@pytest.fixture
def client():
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_catalog", client=mongomock.MongoClient())
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    with TestClient(app) as tc:
        yield tc, store
    store.drop_database()
    store.close()


def _seed(store, slug="demo.adder", publish=False):
    from facetwork.catalog import CatalogService, MongoCatalogStore

    svc = CatalogService(MongoCatalogStore(store._db), store)
    r = svc.save(slug, ffl_source=ADDER, title="Adder", description="adds two ints", tags=["math"])
    assert r.ok and r.is_valid, r
    if publish:
        svc.publish(slug)
    return svc


def test_catalog_list_renders_entry(client):
    tc, store = client
    _seed(store)
    r = tc.get("/v3/catalog")
    assert r.status_code == 200
    assert "Catalog" in r.text and "demo.adder" in r.text


def test_render_summary_md_renders_subset_safely():
    from facetwork.dashboard.routes.execution.catalog import render_summary_md

    html = render_summary_md(
        "Built from a request.\n\n"
        "**Approach**:\n\n"
        "1. step `one`\n"
        "2. step *two*\n\n"
        "See [docs](https://example.com/x)."
    )
    assert "<strong>Approach</strong>" in html
    assert "<ol>" in html and "<li>step <code>one</code></li>" in html
    assert "<em>two</em>" in html
    assert '<a href="https://example.com/x" target="_blank" rel="noopener">docs</a>' in html
    # raw HTML is escaped (XSS-safe), markdown still applies around it
    evil = render_summary_md("<script>alert(1)</script> **bold**")
    assert "<script>" not in evil and "&lt;script&gt;" in evil
    assert "<strong>bold</strong>" in evil


def test_catalog_detail_shows_ffl_params_and_run_form(client):
    tc, store = client
    _seed(store)
    r = tc.get("/v3/catalog/demo.adder")
    assert r.status_code == 200
    assert "namespace claude.demo" in r.text  # FFL source rendered
    assert "claude.demo.Adder" in r.text  # entry workflow
    assert "draft" in r.text  # status
    assert 'action="/catalog/demo.adder/run"' in r.text  # run form present


def test_publish_then_run_creates_bootstrap_task(client):
    tc, store = client
    _seed(store)
    rp = tc.post("/catalog/demo.adder/publish", data={"version": "1"}, follow_redirects=False)
    assert rp.status_code == 303 and rp.headers["location"] == "/v3/catalog/demo.adder"
    assert "published" in tc.get("/v3/catalog/demo.adder").text

    rr = tc.post(
        "/catalog/demo.adder/run",
        data={"version": "1", "inputs_json": '{"a": 10, "b": 5}'},
        follow_redirects=False,
    )
    assert rr.status_code == 303 and rr.headers["location"].startswith("/runners/")
    task = store._db.tasks.find_one({"name": "fw:execute:claude.demo.Adder"})
    assert task is not None and task["data"]["inputs"] == {"a": 10, "b": 5}


def test_run_draft_is_blocked_by_gate(client):
    tc, store = client
    _seed(store)  # draft, not published
    rr = tc.post(
        "/catalog/demo.adder/run",
        data={"version": "1", "inputs_json": "{}"},
        follow_redirects=False,
    )
    assert rr.status_code == 303
    assert "error" in rr.headers["location"]  # gate redirect, not a run
    assert store._db.tasks.find_one({"name": "fw:execute:claude.demo.Adder"}) is None


def test_publish_via_button_then_visible(client):
    tc, store = client
    _seed(store, publish=True)
    body = tc.get("/v3/catalog/demo.adder").text
    assert "published" in body
