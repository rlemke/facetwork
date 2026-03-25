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

"""Tests for dashboard v2 server helpers and routes."""

import time

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


# ---------------------------------------------------------------------------
# Helper unit tests (no server needed)
# ---------------------------------------------------------------------------

from afl.dashboard.helpers import (
    SERVER_DOWN_TIMEOUT_MS,
    effective_server_state,
    group_servers_by_group,
    group_tasks_by_runner,
    group_tasks_by_state,
)


class TestGroupServersByGroup:
    def _make_server(self, name, group, state="running"):
        """Create a minimal server-like object for grouping tests."""
        from types import SimpleNamespace

        return SimpleNamespace(
            uuid=f"s-{name}",
            server_name=name,
            server_group=group,
            service_name="test-service",
            server_ips=["127.0.0.1"],
            state=state,
            ping_time=0,
            handlers=[],
        )

    def test_groups_by_server_group(self):
        servers = [
            self._make_server("node-1", "osm-geocoder"),
            self._make_server("node-2", "osm-geocoder"),
            self._make_server("node-3", "aws-lambda"),
        ]
        groups = group_servers_by_group(servers)
        assert len(groups) == 2
        assert groups[0]["group"] == "aws-lambda"
        assert groups[0]["total"] == 1
        assert groups[1]["group"] == "osm-geocoder"
        assert groups[1]["total"] == 2

    def test_single_group(self):
        servers = [self._make_server("node-1", "mygroup")]
        groups = group_servers_by_group(servers)
        assert len(groups) == 1
        assert groups[0]["group"] == "mygroup"
        assert groups[0]["total"] == 1

    def test_empty(self):
        groups = group_servers_by_group([])
        assert groups == []

    def test_sorted_alphabetically(self):
        servers = [
            self._make_server("z-node", "zebra"),
            self._make_server("a-node", "alpha"),
            self._make_server("m-node", "middle"),
        ]
        groups = group_servers_by_group(servers)
        assert [g["group"] for g in groups] == ["alpha", "middle", "zebra"]

    def test_servers_in_group(self):
        servers = [
            self._make_server("node-1", "grp", "running"),
            self._make_server("node-2", "grp", "error"),
        ]
        groups = group_servers_by_group(servers)
        assert len(groups) == 1
        assert len(groups[0]["servers"]) == 2


class TestEffectiveServerState:
    """Unit tests for effective_server_state()."""

    def _make(self, state="running", ping_time=0):
        from types import SimpleNamespace

        return SimpleNamespace(state=state, ping_time=ping_time)

    def test_running_with_recent_ping(self):
        recent = int(time.time() * 1000) - 10_000  # 10 seconds ago
        assert effective_server_state(self._make("running", recent)) == "running"

    def test_running_with_stale_ping(self):
        stale = int(time.time() * 1000) - SERVER_DOWN_TIMEOUT_MS - 1000
        assert effective_server_state(self._make("running", stale)) == "down"

    def test_running_with_zero_ping(self):
        assert effective_server_state(self._make("running", 0)) == "down"

    def test_startup_with_stale_ping(self):
        stale = int(time.time() * 1000) - SERVER_DOWN_TIMEOUT_MS - 1000
        assert effective_server_state(self._make("startup", stale)) == "down"

    def test_shutdown_stays_shutdown_even_if_stale(self):
        stale = int(time.time() * 1000) - SERVER_DOWN_TIMEOUT_MS - 1000
        assert effective_server_state(self._make("shutdown", stale)) == "shutdown"

    def test_error_stays_error_even_if_stale(self):
        stale = int(time.time() * 1000) - SERVER_DOWN_TIMEOUT_MS - 1000
        assert effective_server_state(self._make("error", stale)) == "error"


# ---------------------------------------------------------------------------
# Route integration tests (require fastapi + mongomock)
# ---------------------------------------------------------------------------

pytestmark_routes = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE, reason="fastapi or mongomock not installed"
)


def _make_server_entity(
    uuid="srv-1",
    group="test-group",
    name="test-node",
    state="running",
    ping_time=None,
):
    from afl.runtime.entities import ServerDefinition

    if ping_time is None:
        # Default to a recent ping so running servers aren't classified as down
        ping_time = int(time.time() * 1000)

    return ServerDefinition(
        uuid=uuid,
        server_group=group,
        service_name="test-service",
        server_name=name,
        server_ips=["10.0.0.1"],
        state=state,
        ping_time=ping_time,
        handlers=["handler-a"],
    )


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from afl.dashboard import dependencies as deps
    from afl.dashboard.app import create_app
    from afl.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_srv_v2", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


@pytestmark_routes
class TestV2ServerList:
    def test_server_list_empty(self, client):
        tc, store = client
        resp = tc.get("/v2/servers", follow_redirects=False)
        assert resp.status_code == 200
        assert "Servers" in resp.text

    def test_server_list_with_servers(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1", state="running"))
        resp = tc.get("/v2/servers?tab=running")
        assert resp.status_code == 200
        assert "test-group" in resp.text
        assert "test-node" in resp.text

    def test_server_list_startup_tab(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1", state="startup"))
        resp = tc.get("/v2/servers?tab=startup")
        assert resp.status_code == 200
        assert "test-node" in resp.text

    def test_server_list_error_tab(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1", state="error"))
        resp = tc.get("/v2/servers?tab=error")
        assert resp.status_code == 200
        assert "test-node" in resp.text

    def test_server_list_shutdown_tab(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1", state="shutdown"))
        resp = tc.get("/v2/servers?tab=shutdown")
        assert resp.status_code == 200
        assert "test-node" in resp.text

    def test_server_list_running_excludes_other_states(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1", state="shutdown"))
        resp = tc.get("/v2/servers?tab=running")
        assert resp.status_code == 200
        assert "No servers" in resp.text

    def test_server_list_partial(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1", state="running"))
        resp = tc.get("/v2/servers/partial?tab=running")
        assert resp.status_code == 200
        assert "test-group" in resp.text

    def test_server_list_tab_counts(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1", state="running"))
        store.save_server(_make_server_entity("srv-2", state="error"))
        resp = tc.get("/v2/servers?tab=running")
        assert resp.status_code == 200
        # The subnav should show counts
        assert "Running" in resp.text
        assert "Error" in resp.text


@pytestmark_routes
class TestV2ServerDetail:
    def test_detail_not_found(self, client):
        tc, store = client
        resp = tc.get("/v2/servers/nonexistent")
        assert resp.status_code == 200
        assert "Not Found" in resp.text

    def test_detail_with_server(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        resp = tc.get("/v2/servers/srv-1")
        assert resp.status_code == 200
        assert "test-node" in resp.text
        assert "test-group" in resp.text

    def test_detail_partial(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        resp = tc.get("/v2/servers/srv-1/partial")
        assert resp.status_code == 200
        assert "test-node" in resp.text

    def test_detail_partial_not_found(self, client):
        tc, store = client
        resp = tc.get("/v2/servers/nonexistent/partial")
        assert resp.status_code == 200

    def test_detail_shows_handlers(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        resp = tc.get("/v2/servers/srv-1")
        assert resp.status_code == 200
        assert "handler-a" in resp.text


@pytestmark_routes
class TestV2ServerNav:
    def test_nav_servers_link_is_v2(self, client):
        tc, store = client
        resp = tc.get("/v2/servers")
        assert '/v2/servers"' in resp.text

    def test_nav_servers_highlighted(self, client):
        tc, store = client
        resp = tc.get("/v2/servers")
        assert "sidebar-link active" in resp.text
        # The Servers link should be active in sidebar
        assert '/v2/servers" class="sidebar-link active"' in resp.text

    def test_old_servers_route_still_works(self, client):
        tc, store = client
        resp = tc.get("/servers")
        assert resp.status_code == 200


@pytestmark_routes
class TestV2ServerDownDetection:
    """Tests for heartbeat timeout → 'down' state detection."""

    def test_stale_server_appears_under_down_tab(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-stale", state="running", ping_time=0))
        resp = tc.get("/v2/servers?tab=down")
        assert resp.status_code == 200
        assert "test-node" in resp.text

    def test_stale_server_not_under_running_tab(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-stale", state="running", ping_time=0))
        resp = tc.get("/v2/servers?tab=running")
        assert resp.status_code == 200
        assert "No servers" in resp.text

    def test_tab_counts_include_down(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-ok", state="running"))
        stale_ping = int(time.time() * 1000) - SERVER_DOWN_TIMEOUT_MS - 1000
        store.save_server(_make_server_entity("srv-stale", state="running", ping_time=stale_ping))
        resp = tc.get("/v2/servers?tab=running")
        assert resp.status_code == 200
        assert "Down" in resp.text


# ---------------------------------------------------------------------------
# Task grouping helper tests
# ---------------------------------------------------------------------------


def _make_task(
    uuid, name="SomeEvent", state="running", runner_id="r1", server_id="srv-1", data=None
):
    from types import SimpleNamespace

    now = int(time.time() * 1000)
    return SimpleNamespace(
        uuid=uuid,
        name=name,
        state=state,
        runner_id=runner_id,
        server_id=server_id,
        workflow_id="w1",
        flow_id="f1",
        step_id=f"step-{uuid}",
        created=now - 10000,
        updated=now,
        error=None,
        data=data,
        task_list_name="default",
    )


class TestGroupTasksByState:
    def test_counts_all_states(self):
        tasks = [
            _make_task("t1", state="running"),
            _make_task("t2", state="completed"),
            _make_task("t3", state="completed"),
            _make_task("t4", state="failed"),
            _make_task("t5", state="pending"),
        ]
        counts = group_tasks_by_state(tasks)
        assert counts["running"] == 1
        assert counts["completed"] == 2
        assert counts["failed"] == 1
        assert counts["pending"] == 1
        assert counts["total"] == 5

    def test_empty_tasks(self):
        counts = group_tasks_by_state([])
        assert counts["total"] == 0
        assert counts["running"] == 0


class TestGroupTasksByRunner:
    def test_groups_by_runner_id(self):
        tasks = [
            _make_task("t1", runner_id="r1"),
            _make_task("t2", runner_id="r2"),
            _make_task("t3", runner_id="r1"),
        ]

        class FakeStore:
            def get_runner(self, rid):
                return None

        groups = group_tasks_by_runner(tasks, FakeStore())
        assert len(groups) == 2
        r1_group = next(g for g in groups if g["runner_id"] == "r1")
        assert r1_group["total"] == 2
        r2_group = next(g for g in groups if g["runner_id"] == "r2")
        assert r2_group["total"] == 1

    def test_counts_per_state(self):
        tasks = [
            _make_task("t1", runner_id="r1", state="running"),
            _make_task("t2", runner_id="r1", state="completed"),
            _make_task("t3", runner_id="r1", state="failed"),
        ]

        class FakeStore:
            def get_runner(self, rid):
                return None

        groups = group_tasks_by_runner(tasks, FakeStore())
        assert len(groups) == 1
        assert groups[0]["counts"]["running"] == 1
        assert groups[0]["counts"]["completed"] == 1
        assert groups[0]["counts"]["failed"] == 1

    def test_empty_tasks(self):
        class FakeStore:
            def get_runner(self, rid):
                return None

        groups = group_tasks_by_runner([], FakeStore())
        assert groups == []

    def test_workflow_name_from_runner(self):
        from types import SimpleNamespace

        tasks = [_make_task("t1", runner_id="r1")]
        runner = SimpleNamespace(
            workflow=SimpleNamespace(name="osm.Routes.BicycleRoutes"),
            state="running",
        )

        class FakeStore:
            def get_runner(self, rid):
                return runner

        groups = group_tasks_by_runner(tasks, FakeStore())
        assert groups[0]["workflow_name"] == "osm.Routes.BicycleRoutes"
        assert groups[0]["runner_state"] == "running"


# ---------------------------------------------------------------------------
# Server tree view integration tests
# ---------------------------------------------------------------------------


def _make_task_entity(uuid, name="SomeEvent", state="running", runner_id="r1", server_id="srv-1"):
    from afl.runtime.entities import TaskDefinition

    return TaskDefinition(
        uuid=uuid,
        name=name,
        state=state,
        runner_id=runner_id,
        workflow_id="w1",
        flow_id="f1",
        step_id=f"step-{uuid}",
        task_list_name="default",
    )


@pytestmark_routes
class TestV2ServerListWithTasks:
    def test_active_tasks_shown(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        task = _make_task_entity("t1", name="osm.ops.CacheRegion", server_id="srv-1")
        store.save_task(task)
        store._db.tasks.update_one({"uuid": "t1"}, {"$set": {"server_id": "srv-1"}})
        resp = tc.get("/v2/servers?tab=running")
        assert resp.status_code == 200
        assert "osm.ops.CacheRegion" in resp.text
        assert "1 active" in resp.text

    def test_no_tasks_message(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        resp = tc.get("/v2/servers?tab=running")
        assert resp.status_code == 200
        assert "No active tasks" in resp.text


@pytestmark_routes
class TestV2ServerDetailWithTasks:
    def test_detail_shows_task_history(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        for i, state in enumerate(["running", "completed", "failed"]):
            task = _make_task_entity(f"t{i}", name=f"Event{i}", state=state)
            store.save_task(task)
            store._db.tasks.update_one({"uuid": f"t{i}"}, {"$set": {"server_id": "srv-1"}})
        resp = tc.get("/v2/servers/srv-1")
        assert resp.status_code == 200
        assert "Task History" in resp.text
        assert "3" in resp.text  # total count

    def test_detail_shows_failed_error(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        task = _make_task_entity("t1", name="FailEvent", state="failed")
        store.save_task(task)
        store._db.tasks.update_one(
            {"uuid": "t1"},
            {"$set": {"server_id": "srv-1", "error": {"message": "connection refused"}}},
        )
        resp = tc.get("/v2/servers/srv-1")
        assert resp.status_code == 200
        assert "connection refused" in resp.text

    def test_detail_partial_includes_tasks(self, client):
        tc, store = client
        store.save_server(_make_server_entity("srv-1"))
        task = _make_task_entity("t1", name="MyEvent", state="running")
        store.save_task(task)
        store._db.tasks.update_one({"uuid": "t1"}, {"$set": {"server_id": "srv-1"}})
        resp = tc.get("/v2/servers/srv-1/partial")
        assert resp.status_code == 200
        assert "Task History" in resp.text
        assert "MyEvent" in resp.text
