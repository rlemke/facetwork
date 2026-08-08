"""Tests for the Stock Groups dashboard app."""

from __future__ import annotations

import datetime

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
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_stocks", client=mongomock.MongoClient())

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _stocks_db(store):
    import os

    return store._db.client[os.environ.get("FW_EXAMPLES_DATABASE", "facetwork_examples")]


def _seed_group(store, group_id="g1", status="open", **overrides):
    db = _stocks_db(store)
    now = datetime.datetime(2026, 3, 2, 14, 0, tzinfo=datetime.UTC)
    group = {
        "group_id": group_id,
        "name": "Momentum week 9",
        "status": status,
        "created_at": now,
        "opened_at": now,
        "closed_at": None,
        "horizon_days": 7,
        "target_close": now + datetime.timedelta(days=7),
        "benchmark": "SPY",
        "benchmark_entry": 500.0,
        "provider": "stub",
        "strategy": {
            "universe": "sp500",
            "size": 2,
            "capital": 10_000.0,
            "use_llm": False,
            "factor_weights": {"mom_12_1": 0.35},
            "stage_weights": {"screen": 0.6},
            "lookback_days": 400,
            "pinned": [],
        },
        "universe_size": 500,
        "screened_count": 420,
        "excluded_count": 80,
        "position_count": 2,
        "skipped": [],
        "initial_value": 10_000.0,
        "current_value": 10_500.0,
        "current_pl_abs": 500.0,
        "current_pl_pct": 0.05,
    }
    group.update(overrides)
    db["stock_groups"].insert_one(group)

    db["stock_positions"].insert_many(
        [
            {
                "group_id": group_id,
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "rank": 1,
                "entry_price": 100.0,
                "shares": 50.0,
                "weight": 0.5,
                "cost": 5000.0,
                "last_price": 110.0,
                "pl_abs": 500.0,
                "pl_pct": 0.10,
                "manual": False,
                "factors": {"mom_12_1": 0.34},
                "llm_reason": "",
            },
            {
                "group_id": group_id,
                "ticker": "MSFT",
                "name": "Microsoft Corp.",
                "rank": 2,
                "entry_price": 200.0,
                "shares": 25.0,
                "weight": 0.5,
                "cost": 5000.0,
                "last_price": 200.0,
                "pl_abs": 0.0,
                "pl_pct": 0.0,
                "manual": True,
                "factors": {},
                "llm_reason": "",
            },
        ]
    )

    db["stock_snapshots"].insert_many(
        [
            {
                "group_id": group_id,
                "snapshot_date": "2026-03-02",
                "taken_at": now,
                "total_cost": 10_000.0,
                "total_value": 10_000.0,
                "pl_abs": 0.0,
                "pl_pct": 0.0,
                "benchmark_price": 500.0,
                "benchmark_pl_pct": 0.0,
                "kind": "open",
                "holdings": [],
                "missing": [],
                "as_of": "2026-03-02T14:00:00+00:00",
            },
            {
                "group_id": group_id,
                "snapshot_date": "2026-03-03",
                "taken_at": now + datetime.timedelta(days=1),
                "total_cost": 10_000.0,
                "total_value": 10_500.0,
                "pl_abs": 500.0,
                "pl_pct": 0.05,
                "benchmark_price": 510.0,
                "benchmark_pl_pct": 0.02,
                "kind": "interim",
                "holdings": [],
                "missing": [],
                "as_of": "2026-03-03T21:00:00+00:00",
            },
        ]
    )

    db["stock_candidates"].insert_many(
        [
            {"group_id": group_id, "ticker": "AAPL", "rank": 1, "score": 2.0, "selected": True},
            {"group_id": group_id, "ticker": "MSFT", "rank": 2, "score": 1.5, "selected": True},
            {"group_id": group_id, "ticker": "NVDA", "rank": 3, "score": 1.2, "selected": False},
        ]
    )
    return group_id


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------


def test_index_renders_with_no_groups(client):
    tc, _ = client
    resp = tc.get("/stocks/")
    assert resp.status_code == 200
    assert "No open groups" in resp.text


def test_index_warns_when_workflows_are_not_seeded(client):
    tc, _ = client
    resp = tc.get("/stocks/")
    assert "not seeded" in resp.text


def test_index_lists_an_open_group(client):
    tc, store = client
    _seed_group(store)
    resp = tc.get("/stocks/")
    assert resp.status_code == 200
    assert "Momentum week 9" in resp.text
    assert "+5.00%" in resp.text  # group P/L
    assert "+2.00%" in resp.text  # benchmark
    assert "+3.00%" in resp.text  # alpha = 5 - 2


def test_index_separates_closed_groups(client):
    tc, store = client
    _seed_group(
        store,
        group_id="closed1",
        status="closed",
        closed_at=datetime.datetime(2026, 3, 9, tzinfo=datetime.UTC),
        final={
            "total_cost": 10_000.0,
            "total_value": 11_000.0,
            "pl_abs": 1000.0,
            "pl_pct": 0.10,
            "benchmark_pl_pct": 0.03,
            "alpha": 0.07,
            "winners": 2,
            "losers": 0,
            "held_days": 7,
        },
    )
    resp = tc.get("/stocks/")
    assert "+10.00%" in resp.text
    assert "+7.00%" in resp.text  # alpha from the final block
    assert "Beat the benchmark" in resp.text


def test_new_group_form_renders(client):
    tc, _ = client
    resp = tc.get("/stocks/new")
    assert resp.status_code == 200
    assert "Start a stock group" in resp.text
    assert "Pinned tickers" in resp.text
    assert "S&amp;P 500" in resp.text


def test_new_group_form_explains_the_selection_method(client):
    tc, _ = client
    resp = tc.get("/stocks/new")
    assert "12-1 momentum" in resp.text
    assert "Analyst consensus" in resp.text


def test_detail_page_renders(client):
    tc, store = client
    _seed_group(store)
    resp = tc.get("/stocks/g1")
    assert resp.status_code == 200
    assert "Momentum week 9" in resp.text
    assert "AAPL" in resp.text and "MSFT" in resp.text
    assert "Refresh now" in resp.text
    assert "End &amp; record" in resp.text


def test_detail_page_flags_a_pinned_position(client):
    tc, store = client
    _seed_group(store)
    resp = tc.get("/stocks/g1")
    assert "pinned" in resp.text
    assert "Pinned manually." in resp.text


def test_detail_page_of_a_closed_group_hides_the_action_buttons(client):
    tc, store = client
    _seed_group(
        store,
        group_id="c1",
        status="closed",
        final={
            "pl_abs": 0.0,
            "pl_pct": 0.0,
            "benchmark_pl_pct": 0.0,
            "alpha": 0.0,
            "winners": 0,
            "losers": 2,
            "held_days": 7,
        },
    )
    resp = tc.get("/stocks/c1")
    assert "/stocks/c1/snapshot" not in resp.text
    assert "/stocks/c1/close" not in resp.text
    assert "End &amp; record" not in resp.text


def test_pages_render_for_a_group_that_has_never_been_snapshotted(client):
    """A freshly opened group has no current_* keys at all.

    Templates must not assume they exist — before this was fixed the detail page
    raised on an Undefined value passed to a format string.
    """
    tc, store = client
    db = _stocks_db(store)
    _seed_group(store, group_id="fresh")
    db["stock_groups"].update_one(
        {"group_id": "fresh"},
        {"$unset": {"current_value": "", "current_pl_abs": "", "current_pl_pct": ""}},
    )
    # Positions are equally bare before the first mark.
    db["stock_positions"].update_many(
        {"group_id": "fresh"},
        {"$unset": {"pl_abs": "", "pl_pct": "", "last_price": ""}},
    )
    db["stock_snapshots"].delete_many({"group_id": "fresh"})

    index = tc.get("/stocks/")
    assert index.status_code == 200
    detail = tc.get("/stocks/fresh")
    assert detail.status_code == 200
    assert "$10,000.00" in detail.text  # falls back to the cost basis
    assert "n/a" in detail.text  # P/L shown as unknown, not as zero


def test_every_page_carries_the_paper_trading_badge(client):
    """The badge sits in the topbar, so it is visible without scrolling.

    A reader must never be able to see profit/loss figures on this app without
    also seeing that the money is not real.
    """
    tc, store = client
    _seed_group(store)
    for url in ("/stocks/", "/stocks/new", "/stocks/g1"):
        text = tc.get(url).text
        assert "paperbadge" in text, f"no paper-trading badge on {url}"
        assert "Paper trading" in text, f"badge text missing on {url}"


def test_every_page_carries_the_full_disclaimer(client):
    tc, store = client
    _seed_group(store)
    for url in ("/stocks/", "/stocks/new", "/stocks/g1"):
        text = tc.get(url).text
        assert "not investment advice" in text, f"no disclaimer on {url}"
        assert "Past performance does not predict future results" in text, url
        assert "no money moves" in text or "moves no money" in text, url


def test_new_group_form_warns_at_the_point_of_commitment(client):
    """The warning must sit with the button that starts a group."""
    tc, _ = client
    text = tc.get("/stocks/new").text
    assert "This is a simulation." in text
    warn = text.index("This is a simulation.")
    start = text.index("Start group")
    assert warn < start, "the warning must precede the Start button"


def test_detail_page_qualifies_the_figures_above_them(client):
    tc, store = client
    _seed_group(store)
    text = tc.get("/stocks/g1").text
    note = text.index("Simulated positions")
    figures = text.index("Current value")
    assert note < figures, "the qualifier must precede the P/L figures"


def test_disclaimer_names_the_excluded_costs(client):
    """Returns that ignore fees and slippage must say so."""
    tc, store = client
    _seed_group(store)
    text = tc.get("/stocks/g1").text
    for term in ("commissions", "slippage", "taxes", "dividends"):
        assert term in text, f"disclaimer does not mention {term}"


def test_new_group_form_offers_sector_checkboxes(client):
    tc, _ = client
    text = tc.get("/stocks/new").text
    assert "Kinds of stock (sectors)" in text
    for value in ("Information Technology", "Health Care", "Energy", "Real Estate"):
        assert f'value="{value}"' in text, f"no checkbox for {value}"
    assert "Max picks per sector" in text


def test_new_group_form_explains_the_no_cap_default(client):
    tc, _ = client
    text = tc.get("/stocks/new").text
    assert "0 means no cap" in text
    assert "Nothing ticked = every sector." in text


def test_start_passes_selected_sectors_to_the_workflow(client, monkeypatch):
    tc, store = client
    from facetwork.dashboard.routes.domain import stocks as mod

    captured = {}

    class FakeWorkflow:
        name = mod.WF_OPEN

    class FakeName:
        name = "stocks"

    class FakeFlow:
        uuid = "flow-uuid"
        name = FakeName()

    monkeypatch.setattr(store, "get_all_flows", lambda: [FakeFlow()], raising=False)
    monkeypatch.setattr(
        store, "get_workflows_by_flow", lambda _uuid: [FakeWorkflow()], raising=False
    )
    monkeypatch.setattr(
        "facetwork.dashboard.routes.execution.flows.create_flow_run",
        lambda flow, wf, inputs, purpose, teams, st, user: (
            captured.setdefault("inputs", inputs) and "r1" or "r1"
        ),
    )

    tc.post(
        "/stocks/groups",
        data={
            "name": "Tech and medical",
            "sectors": ["Information Technology", "Health Care"],
            "max_per_sector": "2",
        },
        follow_redirects=False,
    )

    import json

    inputs = json.loads(captured["inputs"])
    assert inputs["sectors"] == "Information Technology, Health Care"
    assert inputs["max_per_sector"] == 2


def test_start_with_no_sectors_ticked_sends_an_empty_filter(client, monkeypatch):
    """Empty means every sector, not "no sectors"."""
    tc, store = client
    from facetwork.dashboard.routes.domain import stocks as mod

    captured = {}

    class FakeWorkflow:
        name = mod.WF_OPEN

    class FakeName:
        name = "stocks"

    class FakeFlow:
        uuid = "flow-uuid"
        name = FakeName()

    monkeypatch.setattr(store, "get_all_flows", lambda: [FakeFlow()], raising=False)
    monkeypatch.setattr(
        store, "get_workflows_by_flow", lambda _uuid: [FakeWorkflow()], raising=False
    )
    monkeypatch.setattr(
        "facetwork.dashboard.routes.execution.flows.create_flow_run",
        lambda flow, wf, inputs, purpose, teams, st, user: (
            captured.setdefault("inputs", inputs) and "r1" or "r1"
        ),
    )
    tc.post("/stocks/groups", data={"name": "X"}, follow_redirects=False)

    import json

    inputs = json.loads(captured["inputs"])
    assert inputs["sectors"] == ""
    assert inputs["max_per_sector"] == 0


def test_detail_page_shows_the_sector_column(client):
    tc, store = client
    db = _stocks_db(store)
    _seed_group(store)
    db["stock_positions"].update_one(
        {"group_id": "g1", "ticker": "AAPL"},
        {"$set": {"sector": "Information Technology"}},
    )
    text = tc.get("/stocks/g1").text
    assert "<th>Sector</th>" in text
    assert "Information Technology" in text


def test_detail_page_reports_the_sector_strategy(client):
    tc, store = client
    _seed_group(
        store,
        group_id="sect",
        strategy={
            "universe": "sp500",
            "size": 4,
            "capital": 10_000.0,
            "use_llm": False,
            "sectors": ["Health Care", "Information Technology"],
            "max_per_sector": 2,
            "factor_weights": {},
            "stage_weights": {},
            "lookback_days": 400,
            "pinned": [],
        },
    )
    text = tc.get("/stocks/sect").text
    assert "Health Care, Information Technology" in text
    assert "Max per sector" in text


def test_detail_page_says_all_sectors_when_unfiltered(client):
    tc, store = client
    _seed_group(store)
    text = tc.get("/stocks/g1").text
    assert "all sectors" in text
    assert "no cap" in text


def test_find_workflow_prefers_the_newest_flow(client):
    """A stale duplicate must never win.

    Re-seeding left two `stocks` flows here; the older one predated the
    `sectors` parameter, so building a run from it silently dropped settings the
    user had chosen and the feature looked broken with no error anywhere.
    """
    tc, store = client
    from facetwork.dashboard.routes.domain import stocks as mod

    class Wf:
        def __init__(self, name):
            self.name = name

    class Name:
        name = "stocks"

    class Flow:
        def __init__(self, uuid, created_at):
            self.uuid = uuid
            self.created_at = created_at
            self.name = Name()

    old, new_flow = Flow("old", 1000), Flow("new", 2000)
    store.get_all_flows = lambda: [old, new_flow]  # oldest returned first
    store.get_workflows_by_flow = lambda uuid: [Wf(mod.WF_OPEN)]

    flow, wf = mod._find_workflow(store, mod.WF_OPEN)
    assert flow.uuid == "new"
    assert wf.name == mod.WF_OPEN


def test_find_workflow_returns_none_when_absent(client):
    tc, store = client
    from facetwork.dashboard.routes.domain import stocks as mod

    assert mod._find_workflow(store, "nope.Missing") == (None, None)


def test_detail_page_shows_the_disclaimer(client):
    tc, store = client
    _seed_group(store)
    resp = tc.get("/stocks/g1")
    assert "Paper trading only" in resp.text
    assert "not investment advice" in resp.text


def test_unknown_group_redirects_to_the_index(client):
    tc, _ = client
    resp = tc.get("/stocks/nope", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/stocks/"


def test_new_route_is_not_shadowed_by_the_group_route(client):
    """`/stocks/new` must reach the form, not be read as a group id."""
    tc, _ = client
    assert "Start a stock group" in tc.get("/stocks/new").text


# --------------------------------------------------------------------------
# JSON APIs
# --------------------------------------------------------------------------


def test_api_groups(client):
    tc, store = client
    _seed_group(store)
    data = tc.get("/stocks/api/groups").json()
    assert len(data["groups"]) == 1
    assert data["groups"][0]["group_id"] == "g1"
    assert data["groups"][0]["alpha"] == pytest.approx(0.03)


def test_api_groups_filters_by_status(client):
    tc, store = client
    _seed_group(store, group_id="open1")
    _seed_group(store, group_id="closed1", status="closed")
    assert len(tc.get("/stocks/api/groups?status=open").json()["groups"]) == 1
    assert len(tc.get("/stocks/api/groups?status=closed").json()["groups"]) == 1


def test_api_group_detail(client):
    tc, store = client
    _seed_group(store)
    data = tc.get("/stocks/api/groups/g1").json()
    assert data["group"]["name"] == "Momentum week 9"
    assert len(data["positions"]) == 2
    assert len(data["snapshots"]) == 2


def test_api_group_detail_unknown_is_404(client):
    tc, _ = client
    assert tc.get("/stocks/api/groups/nope").status_code == 404


def test_api_serialises_datetimes_instead_of_dropping_them(client):
    tc, store = client
    _seed_group(store)
    group = tc.get("/stocks/api/groups/g1").json()["group"]
    assert isinstance(group["opened_at"], str)
    assert group["opened_at"].startswith("2026-03-02")


def test_api_series_returns_both_lines_in_percent(client):
    tc, store = client
    _seed_group(store)
    data = tc.get("/stocks/api/groups/g1/series").json()
    assert data["dates"] == ["2026-03-02", "2026-03-03"]
    assert data["pl_pct"] == [0.0, 5.0]
    assert data["benchmark_pct"] == [0.0, 2.0]
    assert data["benchmark"] == "SPY"
    assert data["total_value"] == [10_000.0, 10_500.0]


def test_api_series_for_an_unknown_group_is_empty_not_an_error(client):
    tc, _ = client
    data = tc.get("/stocks/api/groups/nope/series").json()
    assert data["dates"] == []


def test_api_candidates_includes_the_names_not_picked(client):
    tc, store = client
    _seed_group(store)
    rows = tc.get("/stocks/api/groups/g1/candidates").json()["candidates"]
    assert [r["ticker"] for r in rows] == ["AAPL", "MSFT", "NVDA"]
    assert rows[2]["selected"] is False


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------


def test_start_without_seeded_workflows_redirects_with_an_error(client):
    tc, _ = client
    resp = tc.post("/stocks/groups", data={"name": "X"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=not_seeded" in resp.headers["location"]


def test_snapshot_without_seeded_workflows_redirects_with_an_error(client):
    tc, store = client
    _seed_group(store)
    resp = tc.post("/stocks/g1/snapshot", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=not_seeded" in resp.headers["location"]


def test_close_without_seeded_workflows_redirects_with_an_error(client):
    tc, store = client
    _seed_group(store)
    resp = tc.post("/stocks/g1/close", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=not_seeded" in resp.headers["location"]


def test_start_submits_the_workflow_when_seeded(client, monkeypatch):
    """The start button must create a real run, not mutate the collections."""
    tc, store = client
    from facetwork.dashboard.routes.domain import stocks as mod

    captured = {}

    class FakeWorkflow:
        name = mod.WF_OPEN

    class FakeName:
        name = "stocks"

    class FakeFlow:
        uuid = "flow-uuid"
        name = FakeName()

    monkeypatch.setattr(store, "get_all_flows", lambda: [FakeFlow()], raising=False)
    monkeypatch.setattr(
        store, "get_workflows_by_flow", lambda _uuid: [FakeWorkflow()], raising=False
    )
    monkeypatch.setattr(
        "facetwork.dashboard.routes.execution.flows.create_flow_run",
        lambda flow, wf, inputs, purpose, teams, st, user: (
            captured.setdefault("inputs", inputs) and "runner-123" or "runner-123"
        ),
    )

    resp = tc.post(
        "/stocks/groups",
        data={
            "name": "  Week 9  ",
            "universe": "dow30",
            "size": "5",
            "capital": "50000",
            "horizon_days": "7",
            "benchmark": "spy",
            "pinned": "AAPL",
            "use_llm": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/v3/workflows/runner-123"

    import json

    inputs = json.loads(captured["inputs"])
    assert inputs["name"] == "Week 9"  # trimmed
    assert inputs["benchmark"] == "SPY"  # upper-cased
    assert inputs["size"] == 5
    assert inputs["use_llm"] is True
    assert inputs["pinned"] == "AAPL"


def test_start_coerces_a_nonsensical_size(client, monkeypatch):
    tc, store = client
    from facetwork.dashboard.routes.domain import stocks as mod

    captured = {}

    class FakeWorkflow:
        name = mod.WF_OPEN

    class FakeName:
        name = "stocks"

    class FakeFlow:
        uuid = "flow-uuid"
        name = FakeName()

    monkeypatch.setattr(store, "get_all_flows", lambda: [FakeFlow()], raising=False)
    monkeypatch.setattr(
        store, "get_workflows_by_flow", lambda _uuid: [FakeWorkflow()], raising=False
    )
    monkeypatch.setattr(
        "facetwork.dashboard.routes.execution.flows.create_flow_run",
        lambda flow, wf, inputs, purpose, teams, st, user: (
            captured.setdefault("inputs", inputs) and "r1" or "r1"
        ),
    )
    tc.post(
        "/stocks/groups",
        data={"name": "X", "size": "0", "horizon_days": "0"},
        follow_redirects=False,
    )

    import json

    inputs = json.loads(captured["inputs"])
    assert inputs["size"] == 1
    assert inputs["horizon_days"] == 1
