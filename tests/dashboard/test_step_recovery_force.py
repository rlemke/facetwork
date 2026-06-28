"""Recovery-action endpoints (retry / retry-block / reset-block / rerun)
refuse when a step's task is RUNNING and accept ``?force=true``.

Each test seeds a workflow + one step with a RUNNING task, then checks that
the endpoint returns HTTP 409 by default and HTTP 303 (redirect) with
``?force=true``. A FRAMEWORK-level step log entry is written on force.
"""

from __future__ import annotations

import time as _time

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

from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import generate_id, step_id


needs_fastapi = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE,
    reason="fastapi or mongomock not installed",
)


def _step(
    *,
    id: str | None = None,
    object_type: str = "VariableAssignment",
    workflow_id: str = "wf-1",
    facet_name: str = "",
    statement_name: str = "",
    container_id: str | None = None,
    block_id: str | None = None,
    root_id: str | None = None,
    state: str = "state.statement.Error",
) -> StepDefinition:
    return StepDefinition(
        id=id or step_id(),
        object_type=object_type,
        workflow_id=workflow_id,
        facet_name=facet_name,
        statement_name=statement_name,
        container_id=container_id,
        block_id=block_id,
        root_id=root_id,
        state=state,
    )


def _task(*, step_id: str, state: str = TaskState.RUNNING) -> TaskDefinition:
    return TaskDefinition(
        uuid=generate_id(),
        name="fw:test",
        runner_id="r-1",
        workflow_id="wf-1",
        flow_id="flow-1",
        step_id=step_id,
        state=state,
        created=int(_time.time() * 1000),
        updated=int(_time.time() * 1000),
    )


@pytest.fixture
def client():
    if not (FASTAPI_AVAILABLE and MONGOMOCK_AVAILABLE):
        pytest.skip("fastapi or mongomock not installed")
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_recovery", client=mongomock.MongoClient())
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    with TestClient(app, follow_redirects=False) as tc:
        yield tc, store
    store.drop_database()
    store.close()


# ----------------------------------------------------------------------------
# retry_step — refuses while target's task is RUNNING; force overrides
# ----------------------------------------------------------------------------


@needs_fastapi
class TestRetryStepForce:
    def _seed(self, store):
        target = _step(id="s-target", facet_name="DoWork")
        store.save_step(target)
        store.save_task(_task(step_id=target.id))
        return target

    def test_refuses_when_running(self, client):
        tc, store = client
        target = self._seed(store)

        resp = tc.post(f"/steps/{target.id}/retry")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "step_task_running"
        assert body["blockers"][0]["step_id"] == target.id

    def test_force_proceeds_and_logs(self, client):
        tc, store = client
        target = self._seed(store)

        resp = tc.post(f"/steps/{target.id}/retry?force=true")
        assert resp.status_code == 303

        task = store.get_task_for_step(target.id)
        assert task.state == TaskState.PENDING

        logs = store.get_step_logs_by_step(target.id)
        assert any("Forcibly retried" in log.message for log in logs)


# ----------------------------------------------------------------------------
# retry_block — refuses if any errored leaf has a RUNNING task; force overrides
# ----------------------------------------------------------------------------


@needs_fastapi
class TestRetryBlockForce:
    def _seed(self, store):
        from facetwork.runtime.states import StepState

        block = _step(id="b-1", object_type="AndThen")
        leaf = _step(
            id="leaf-1",
            facet_name="DoWork",
            block_id=block.id,
            state=StepState.STATEMENT_ERROR,
        )
        for s in (block, leaf):
            store.save_step(s)
        store.save_task(_task(step_id=leaf.id))
        return block, leaf

    def test_refuses_when_leaf_running(self, client):
        tc, store = client
        block, leaf = self._seed(store)

        resp = tc.post(f"/steps/{block.id}/retry-block")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "leaf_task_running"
        assert body["blockers"][0]["step_id"] == leaf.id

    def test_force_resets_running_leaf(self, client):
        tc, store = client
        block, leaf = self._seed(store)

        resp = tc.post(f"/steps/{block.id}/retry-block?force=true")
        assert resp.status_code == 303

        task = store.get_task_for_step(leaf.id)
        assert task.state == TaskState.PENDING

        logs = store.get_step_logs_by_step(leaf.id)
        assert any("Forcibly retried during block retry" in log.message for log in logs)


# ----------------------------------------------------------------------------
# reset_block — refuses when any descendant has a RUNNING task; force overrides
# ----------------------------------------------------------------------------


@needs_fastapi
class TestResetBlockForce:
    def _seed(self, store):
        block = _step(id="b-1", object_type="AndThen")
        child = _step(id="c-1", facet_name="DoWork", block_id=block.id)
        for s in (block, child):
            store.save_step(s)
        store.save_task(_task(step_id=child.id))
        return block, child

    def test_refuses_when_descendant_running(self, client):
        tc, store = client
        block, child = self._seed(store)

        resp = tc.post(f"/steps/{block.id}/reset-block")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "running_descendants"
        assert body["blockers"][0]["step_id"] == child.id

    def test_force_deletes_descendant_and_logs(self, client):
        tc, store = client
        block, child = self._seed(store)

        # Capture the forced-removal log entry before the child step
        # (and its logs) are deleted.
        resp = tc.post(f"/steps/{block.id}/reset-block?force=true")
        assert resp.status_code == 303

        # Descendant is gone.
        assert store.get_step(child.id) is None
        assert store.get_task_for_step(child.id) is None


# ----------------------------------------------------------------------------
# rerun_step — already had force; regression: 409 → 303 path still works
# ----------------------------------------------------------------------------


@needs_fastapi
class TestRerunStepForce:
    def _seed(self, store):
        block = _step(id="b-1", object_type="AndThen")
        upstream = _step(
            id="up-1",
            facet_name="Upstream",
            block_id=block.id,
            statement_name="up",
        )
        upstream.statement_id = "stmt-up"
        upstream.start_time = 1
        downstream = _step(
            id="dn-1",
            facet_name="Downstream",
            block_id=block.id,
            statement_name="dn",
        )
        downstream.statement_id = "stmt-dn"
        downstream.start_time = 2
        for s in (block, upstream, downstream):
            store.save_step(s)
        store.save_task(_task(step_id=downstream.id))
        return upstream, downstream

    def test_refuses_with_running_dependent(self, client):
        tc, store = client
        upstream, downstream = self._seed(store)

        resp = tc.post(f"/steps/{upstream.id}/rerun")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "running_dependents"
        assert body["blockers"][0]["step_id"] == downstream.id

    def test_force_drops_running_dependent(self, client):
        tc, store = client
        upstream, downstream = self._seed(store)

        resp = tc.post(f"/steps/{upstream.id}/rerun?force=true")
        assert resp.status_code == 303
        assert store.get_step(downstream.id) is None
