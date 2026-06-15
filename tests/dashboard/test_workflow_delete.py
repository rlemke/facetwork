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

"""Tests for workflow-run deletion: store cascade + dashboard API routes."""

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

from facetwork.runtime.entities import StepLogEntry, TaskDefinition
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.step import StepDefinition

pytestmark_routes = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE,
    reason="fastapi or mongomock not installed",
)


def _make_workflow(uuid, name="osm.TestWF"):
    from facetwork.runtime.entities import WorkflowDefinition

    return WorkflowDefinition(
        uuid=uuid,
        name=name,
        namespace_id="ns-1",
        facet_id="f-1",
        flow_id="flow-1",
        starting_step="s-1",
        version="1.0",
    )


def _make_runner(uuid, workflow_uuid, state="completed"):
    from facetwork.runtime.entities import RunnerDefinition

    workflow = _make_workflow(workflow_uuid)
    return RunnerDefinition(
        uuid=uuid,
        workflow_id=workflow.uuid,
        workflow=workflow,
        state=state,
    )


def _seed_run(store, runner_id, workflow_id):
    """Save a runner plus one step, task, and step log all keyed to it."""
    store.save_runner(_make_runner(runner_id, workflow_id))
    step = StepDefinition(
        id=f"{workflow_id}-step",
        object_type="Facet",
        workflow_id=workflow_id,
        state="state.statement.Complete",
    )
    store.save_step(step)
    store.save_task(
        TaskDefinition(
            uuid=f"{workflow_id}-task",
            name="osm.cache.Download",
            runner_id=runner_id,
            workflow_id=workflow_id,
            flow_id="flow-1",
            step_id=step.id,
            state="completed",
        )
    )
    store.save_step_log(
        StepLogEntry(
            uuid=f"{workflow_id}-log",
            step_id=step.id,
            workflow_id=workflow_id,
            runner_id=runner_id,
            facet_name="osm.cache.Download",
            source="handler",
            level="info",
            message="done",
            details={},
            time=1,
        )
    )
    return step


class TestDeleteRunnerCascade:
    """Store-level cascade behaviour (uses the in-memory store)."""

    def test_delete_removes_run_and_children(self):
        store = MemoryStore()
        step = _seed_run(store, "r-1", "wf-1")

        result = store.delete_runner("r-1")

        assert result["found"] is True
        assert result["runners"] == 1
        assert result["steps"] == 1
        assert result["tasks"] == 1
        assert result["step_logs"] == 1
        assert store.get_runner("r-1") is None
        assert list(store.get_steps_by_workflow("wf-1")) == []
        assert store.get_task_for_step(step.id) is None
        assert list(store.get_step_logs_by_step(step.id)) == []

    def test_delete_missing_runner_is_noop(self):
        store = MemoryStore()
        result = store.delete_runner("nope")
        assert result["found"] is False
        assert result["runners"] == 0

    def test_delete_does_not_touch_other_runs(self):
        store = MemoryStore()
        _seed_run(store, "r-1", "wf-1")
        _seed_run(store, "r-2", "wf-2")

        store.delete_runner("r-1")

        assert store.get_runner("r-1") is None
        assert store.get_runner("r-2") is not None
        assert len(list(store.get_steps_by_workflow("wf-2"))) == 1

    def test_bulk_delete_aggregates(self):
        store = MemoryStore()
        _seed_run(store, "r-1", "wf-1")
        _seed_run(store, "r-2", "wf-2")

        result = store.delete_runners(["r-1", "r-2", "missing"])

        assert result["deleted"] == 2
        assert result["steps"] == 2
        assert result["tasks"] == 2
        assert store.get_runner("r-1") is None
        assert store.get_runner("r-2") is None


@pytestmark_routes
class TestDeleteRunnerRoutes:
    @pytest.fixture
    def client(self):
        from facetwork.dashboard import dependencies as deps
        from facetwork.dashboard.app import create_app
        from facetwork.runtime.mongo_store import MongoStore

        mock_client = mongomock.MongoClient()
        store = MongoStore(database_name="afl_test_delete", client=mock_client)

        app = create_app()
        app.dependency_overrides[deps.get_store] = lambda: store
        with TestClient(app) as tc:
            yield tc, store
        store.drop_database()
        store.close()

    def test_delete_single(self, client):
        tc, store = client
        _seed_run(store, "r-1", "wf-1")

        resp = tc.post("/api/runners/r-1/delete")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["runners"] == 1
        assert store.get_runner("r-1") is None

    def test_delete_missing_is_404(self, client):
        tc, store = client
        resp = tc.post("/api/runners/ghost/delete")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_bulk_delete(self, client):
        tc, store = client
        _seed_run(store, "r-1", "wf-1")
        _seed_run(store, "r-2", "wf-2")

        resp = tc.post("/api/runners/bulk-delete", json={"runner_ids": ["r-1", "r-2"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["deleted"] == 2
        assert store.get_runner("r-1") is None
        assert store.get_runner("r-2") is None

    def test_bulk_delete_empty_is_400(self, client):
        tc, store = client
        resp = tc.post("/api/runners/bulk-delete", json={"runner_ids": []})
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_run_row_rendered_in_list(self, client):
        tc, store = client
        _seed_run(store, "r-1", "wf-1")
        # The v3 Runs list renders each run as a row linking to its detail page,
        # which is where per-run management (incl. delete) now lives.
        resp = tc.get("/v3/workflows?tab=completed")
        assert resp.status_code == 200
        assert 'href="/v3/workflows/r-1"' in resp.text
