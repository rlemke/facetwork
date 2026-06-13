"""Tests for the Users + Teams data model and store operations.

Covers CRUD, special-user seeding, team membership + scrub-on-delete, soft
delete (keeps a run's embedded author), and force delete (reassigns dangling
references to the ``deleted`` principal, optionally removing the user's work).
"""

import pytest

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

from facetwork.runtime.entities import (
    DELETED_USER_EMAIL,
    RunnerDefinition,
    RunnerState,
    TeamDefinition,
    User,
    UserDefinition,
    WorkflowDefinition,
)

pytestmark = pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock required")


@pytest.fixture
def store():
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_users", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _workflow(uuid="wf-1"):
    return WorkflowDefinition(
        uuid=uuid,
        name="ns.Test",
        namespace_id="ns-1",
        facet_id="f-1",
        flow_id="flow-1",
        starting_step="s-1",
        version="1.0",
    )


def _runner(uuid, *, user=None, author=None):
    return RunnerDefinition(
        uuid=uuid,
        workflow_id="wf-1",
        workflow=_workflow(),
        state=RunnerState.COMPLETED,
        user=user,
        author=author,
    )


# ── Entity behaviour ─────────────────────────────────────────────────────────


def test_user_display_name_and_projection():
    u = User(email="a@b.com", first_name="Ada", last_name="Lovelace")
    assert u.display_name == "Ada Lovelace"
    assert not u.is_special and not u.is_deleted
    snap = u.to_user_definition()
    assert isinstance(snap, UserDefinition)
    assert snap.email == "a@b.com" and snap.name == "Ada Lovelace"


def test_user_display_name_falls_back_to_email():
    assert User(email="nameless@x.com").display_name == "nameless@x.com"


# ── Seeding ──────────────────────────────────────────────────────────────────


def test_special_users_seeded(store):
    emails = {u.email for u in store.list_users()}
    assert DELETED_USER_EMAIL in emails
    assert "system@facetwork.local" in emails
    assert "claude@facetwork.local" in emails
    assert "anonymous@facetwork.local" in emails
    assert all(store.get_user(e).is_special for e in emails)


def test_seeding_is_idempotent(store):
    before = len(store.list_users())
    store.ensure_special_users()
    assert len(store.list_users()) == before


# ── CRUD + listing filters ───────────────────────────────────────────────────


def test_save_get_and_list_filters(store):
    store.save_user(User(email="ada@x.com", first_name="Ada"))
    got = store.get_user("ada@x.com")
    assert got is not None and got.created_at > 0 and got.updated_at > 0

    humans = store.list_users(include_special=False)
    assert [u.email for u in humans] == ["ada@x.com"]
    # special principals appear by default but humans-only filter excludes them
    assert any(u.is_special for u in store.list_users())


def test_soft_deleted_user_hidden_unless_requested(store):
    store.save_user(User(email="ada@x.com", first_name="Ada"))
    store.soft_delete_user("ada@x.com")
    assert store.get_user("ada@x.com").is_deleted
    assert "ada@x.com" not in {u.email for u in store.list_users()}
    assert "ada@x.com" in {u.email for u in store.list_users(include_deleted=True)}


# ── Teams + membership ───────────────────────────────────────────────────────


def test_team_crud_and_membership(store):
    store.save_team(TeamDefinition(uuid="t-1", name="geo", leader_email="lead@x.com"))
    store.save_user(User(email="ada@x.com", first_name="Ada", teams=["geo"], default_team="geo"))
    store.save_user(User(email="bob@x.com", first_name="Bob", teams=["geo"]))

    assert store.get_team("geo").leader_email == "lead@x.com"
    assert {u.email for u in store.get_team_members("geo")} == {"ada@x.com", "bob@x.com"}


def test_delete_team_scrubs_membership(store):
    store.save_team(TeamDefinition(uuid="t-1", name="geo"))
    store.save_user(User(email="ada@x.com", teams=["geo", "research"], default_team="geo"))

    res = store.delete_team("geo")
    assert res["deleted"] and res["members_updated"] == 1 and res["default_cleared"] == 1
    ada = store.get_user("ada@x.com")
    assert ada.teams == ["research"] and ada.default_team == ""
    assert store.get_team("geo") is None


# ── Soft vs force delete + reassignment ──────────────────────────────────────


def test_soft_delete_keeps_runner_author(store):
    ada = User(email="ada@x.com", first_name="Ada")
    store.save_user(ada)
    store.save_runner(_runner("r-1", user=ada.to_user_definition(), author=ada.to_user_definition()))

    store.soft_delete_user("ada@x.com")
    run = store.get_runner("r-1")
    assert run.user.email == "ada@x.com" and run.author.email == "ada@x.com"


def test_force_delete_reassigns_references(store):
    ada = User(email="ada@x.com", first_name="Ada")
    store.save_user(ada)
    store.save_runner(_runner("r-1", user=ada.to_user_definition(), author=ada.to_user_definition()))

    res = store.force_delete_user("ada@x.com")
    assert res["deleted"]
    assert store.get_user("ada@x.com") is None  # record gone
    run = store.get_runner("r-1")  # run survives, references flipped
    assert run.user.email == DELETED_USER_EMAIL
    assert run.author.email == DELETED_USER_EMAIL


def test_force_delete_with_delete_work_removes_runs(store):
    ada = User(email="ada@x.com", first_name="Ada")
    store.save_user(ada)
    store.save_runner(_runner("r-1", user=ada.to_user_definition()))
    store.save_runner(_runner("r-2", author=ada.to_user_definition()))

    res = store.force_delete_user("ada@x.com", delete_work=True)
    assert res["deleted_runs"] == 2
    assert store.get_runner("r-1") is None and store.get_runner("r-2") is None


def test_force_delete_drops_membership(store):
    store.save_team(TeamDefinition(uuid="t-1", name="geo"))
    store.save_user(User(email="ada@x.com", teams=["geo"]))

    store.force_delete_user("ada@x.com")
    assert {u.email for u in store.get_team_members("geo")} == set()


# ── Special-user protection ──────────────────────────────────────────────────


def test_special_users_cannot_be_deleted(store):
    soft = store.soft_delete_user(DELETED_USER_EMAIL)
    assert soft["deleted"] is False and soft["reason"] == "special user"
    force = store.force_delete_user("system@facetwork.local")
    assert force["deleted"] is False and force["reason"] == "special user"
    assert store.get_user(DELETED_USER_EMAIL) is not None


def test_delete_missing_user_is_noop(store):
    assert store.soft_delete_user("ghost@x.com")["found"] is False
    assert store.force_delete_user("ghost@x.com")["found"] is False
