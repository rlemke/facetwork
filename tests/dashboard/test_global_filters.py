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

"""Tests for the global Flow/Workflow filter page and its cookie-driven application."""

from __future__ import annotations

import json
from types import SimpleNamespace

import mongomock
import pytest
from fastapi.testclient import TestClient

from facetwork.dashboard import viewfilters as vf


@pytest.fixture
def client():
    """Test client with a mongomock-backed store."""
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_global_filters", client=mongomock.MongoClient())
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    with TestClient(app) as tc:
        yield tc, store
    store.drop_database()
    store.close()


# ── unit: matching logic ────────────────────────────────────────────────────────


def _runner(**kw):
    base = dict(
        teams=[],
        purpose="none",
        state="completed",
        start_time=0,
        user=None,
        author=None,
        workflow=SimpleNamespace(name="osm.Foo"),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_empty_filters_match_everything():
    f = vf.empty_filters()
    assert vf.runner_matches(_runner(), f)
    assert not vf.is_active(f)


def test_runner_team_filter():
    f = vf.empty_filters()
    f["workflows"]["teams"] = ["alpha"]
    assert vf.runner_matches(_runner(teams=["alpha", "beta"]), f)
    assert not vf.runner_matches(_runner(teams=["beta"]), f)
    assert not vf.runner_matches(_runner(teams=[]), f)


def test_runner_user_filter():
    f = vf.empty_filters()
    f["workflows"]["user"] = "ada@x.io"
    u = SimpleNamespace(email="ada@x.io", name="Ada")
    assert vf.runner_matches(_runner(user=u), f)
    assert not vf.runner_matches(_runner(user=SimpleNamespace(email="bob@x.io", name="Bob")), f)


def test_runner_state_and_purpose_filter():
    f = vf.empty_filters()
    f["workflows"]["state"] = "failed"
    assert vf.runner_matches(_runner(state="failed"), f)
    assert vf.runner_matches(_runner(state="cancelled"), f)
    assert not vf.runner_matches(_runner(state="completed"), f)
    f = vf.empty_filters()
    f["workflows"]["purpose"] = "production"
    assert vf.runner_matches(_runner(purpose="production"), f)
    assert not vf.runner_matches(_runner(purpose="none"), f)


def test_runner_workflow_name_filter():
    f = vf.empty_filters()
    f["workflows"]["workflows"] = ["osm.Foo"]
    assert vf.runner_matches(_runner(workflow=SimpleNamespace(name="osm.Foo")), f)
    assert not vf.runner_matches(_runner(workflow=SimpleNamespace(name="osm.Bar")), f)


def test_runner_run_date_range():
    f = vf.empty_filters()
    f["workflows"]["run_from"] = "2026-01-01"
    f["workflows"]["run_to"] = "2026-01-31"
    jan15 = 1768435200000  # 2026-01-15 (ms, within range)
    feb = 1769904000000  # 2026-02-01 (outside)
    assert vf.runner_matches(_runner(start_time=jan15), f)
    assert not vf.runner_matches(_runner(start_time=feb), f)
    # unknown timestamp cannot satisfy an explicit bound
    assert not vf.runner_matches(_runner(start_time=0), f)


def test_read_filters_roundtrip_and_bad_cookie():
    payload = {"workflows": {"teams": ["x"], "state": "failed"}}
    req = SimpleNamespace(cookies={vf.FILTERS_COOKIE: json.dumps(payload)})
    f = vf.read_filters(req)
    assert f["workflows"]["teams"] == ["x"]
    assert f["workflows"]["state"] == "failed"
    assert f["flows"]["teams"] == []  # default
    assert vf.is_active(f)
    # malformed cookie → all-Any
    bad = SimpleNamespace(cookies={vf.FILTERS_COOKIE: "{not json"})
    assert not vf.is_active(vf.read_filters(bad))


# ── route: filter page + cookie application ───────────────────────────────────────


class TestFilterPage:
    def test_page_renders_both_sections(self, client):
        tc, _ = client
        r = tc.get("/v3/filters")
        assert r.status_code == 200
        assert "Flows" in r.text and "Workflows" in r.text
        assert "Any author" in r.text and "Any user" in r.text
        assert 'name="flow_created_from"' in r.text and 'name="wf_run_from"' in r.text

    def test_save_sets_cookie_and_redirects(self, client):
        tc, _ = client
        r = tc.post(
            "/v3/filters",
            data={"wf_state": "failed", "next": "/v3/workflows"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/v3/workflows"
        assert vf.FILTERS_COOKIE in r.cookies or any(
            vf.FILTERS_COOKIE in h for _, h in r.headers.multi_items() if _ == "set-cookie"
        )

    def test_active_filter_shows_banner_on_runs(self, client):
        tc, _ = client
        tc.post("/v3/filters", data={"wf_state": "failed", "next": "/v3/workflows"})
        assert "Filters active" in tc.get("/v3/workflows?tab=failed").text

    def test_active_filter_shows_banner_on_flows(self, client):
        tc, _ = client
        tc.post("/v3/filters", data={"flow_teams": "__no_such_team__", "next": "/v3/flows"})
        body = tc.get("/v3/flows").text
        assert "Filters active" in body

    def test_clear_removes_filter(self, client):
        tc, _ = client
        tc.post("/v3/filters", data={"flow_teams": "__no_such_team__", "next": "/v3/flows"})
        r = tc.post("/v3/filters/clear", data={"next": "/v3/flows"}, follow_redirects=False)
        assert r.status_code == 303
        # after clearing, the banner is gone
        assert "Filters active" not in tc.get("/v3/flows").text

    def test_no_cookie_shows_everything(self, client):
        tc, _ = client
        assert "Filters active" not in tc.get("/v3/flows").text
        assert "Filters active" not in tc.get("/v3/workflows").text
