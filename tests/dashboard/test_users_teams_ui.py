"""Dashboard UI tests for Users/Teams admin + acting-as + run tagging."""

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


OWNED_AFL = """
namespace app {
    event facet Compute(input: Long) => (out: Long)
    workflow OwnedWF(x: Long = 1) => (result: Long)
      with Author(email = "ada@example.com")
      with Teams(names = ["geo"])
      andThen {
        s1 = Compute(input = $.x)
        yield OwnedWF(result = s1.out)
    }
}
"""


@pytest.fixture
def client():
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_users_ui", client=mongomock.MongoClient())
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    # TestClient does not follow redirects by default unless asked per-request.
    with TestClient(app) as tc:
        yield tc, store
    store.drop_database()
    store.close()


def _seed_flow(store):
    from facetwork.runtime.entities import (
        FlowDefinition,
        FlowIdentity,
        SourceText,
        WorkflowDefinition,
    )
    from facetwork.runtime.types import generate_id

    flow_id, wf_id = generate_id(), generate_id()
    flow = FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name="app.OwnedWF", path="test", uuid=flow_id),
        compiled_sources=[SourceText(name="source.ffl", content=OWNED_AFL)],
    )
    store.save_flow(flow)
    wf = WorkflowDefinition(
        uuid=wf_id, name="app.OwnedWF", namespace_id="app", facet_id=wf_id,
        flow_id=flow_id, starting_step="", version="1.0",
    )
    store.save_workflow(wf)
    return flow, wf


# ── Users CRUD ────────────────────────────────────────────────────────────────


def test_user_list_shows_special_users(client):
    tc, _store = client
    resp = tc.get("/v3/users")
    assert resp.status_code == 200
    assert "anonymous@facetwork.local" in resp.text
    assert "Users" in resp.text


def test_create_and_soft_delete_user(client):
    tc, store = client
    r = tc.post(
        "/v3/users",
        data={"email": "ada@example.com", "first_name": "Ada", "last_name": "Lovelace"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert store.get_user("ada@example.com").display_name == "Ada Lovelace"

    tc.post("/v3/users/ada@example.com/delete", follow_redirects=False)
    assert store.get_user("ada@example.com").is_deleted


def test_force_delete_user(client):
    tc, store = client
    tc.post("/v3/users", data={"email": "ada@example.com", "first_name": "Ada"},
            follow_redirects=False)
    tc.post("/v3/users/ada@example.com/force-delete", follow_redirects=False)
    assert store.get_user("ada@example.com") is None


# ── Acting-as ─────────────────────────────────────────────────────────────────


def test_act_as_sets_cookie(client):
    tc, store = client
    tc.post("/v3/users", data={"email": "ada@example.com", "first_name": "Ada"},
            follow_redirects=False)
    r = tc.post("/v3/users/act-as", data={"email": "ada@example.com", "next": "/v3/users"},
                follow_redirects=False)
    assert r.status_code == 303
    # Starlette quotes the "@" in the cookie value; just assert it carries the email.
    assert "ada@example.com" in tc.cookies.get("afl_current_user", "")


# ── Teams CRUD + membership ───────────────────────────────────────────────────


def test_create_team_with_members(client):
    tc, store = client
    tc.post("/v3/users", data={"email": "ada@example.com", "first_name": "Ada"},
            follow_redirects=False)
    r = tc.post(
        "/v3/teams",
        data={"name": "geo", "description": "Geospatial", "members": ["ada@example.com"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    team = store.get_team("geo")
    assert team is not None
    assert {u.email for u in store.get_team_members("geo")} == {"ada@example.com"}

    # delete scrubs membership
    tc.post(f"/v3/teams/{team.uuid}/delete", follow_redirects=False)
    assert store.get_team("geo") is None
    assert store.get_user("ada@example.com").teams == []


# ── Run tagging through the dashboard ──────────────────────────────────────────


def test_run_form_shows_purpose_and_teams(client):
    tc, store = client
    store.save_team(_team("geo"))
    flow, wf = _seed_flow(store)
    resp = tc.get(f"/flows/{flow.uuid}/run/{wf.uuid}")
    assert resp.status_code == 200
    assert "Purpose" in resp.text
    assert "production" in resp.text
    assert "List this run for teams" in resp.text


def test_run_execute_tags_run_with_user_author_purpose_teams(client):
    tc, store = client
    store.save_user(_user("ada@example.com", "Ada"))
    flow, wf = _seed_flow(store)
    # Act as Ada, then run with purpose=test and team geo
    tc.post("/v3/users/act-as", data={"email": "ada@example.com", "next": "/"},
            follow_redirects=False)
    r = tc.post(
        f"/flows/{flow.uuid}/run/{wf.uuid}",
        data={"inputs_json": json.dumps({"x": 5}), "purpose": "test", "teams": ["geo"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    runners = list(store.get_all_runners())
    assert len(runners) == 1
    run = runners[0]
    assert run.user.email == "ada@example.com"           # started_by = acting user
    assert run.author.email == "ada@example.com"          # from the Author mixin
    assert run.purpose == "test"
    assert run.teams == ["geo"]


def _user(email, first):
    from facetwork.runtime.entities import User

    return User(email=email, first_name=first)


def _team(name):
    from facetwork.runtime.entities import TeamDefinition
    from facetwork.runtime.types import generate_id

    return TeamDefinition(uuid=generate_id(), name=name)
