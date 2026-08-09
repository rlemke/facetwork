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

"""Integration tests for dashboard routes using TestClient + mongomock."""

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


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    # Create a mongomock-backed store
    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_dashboard", client=mock_client)

    app = create_app()

    # Override the dependency
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _make_workflow(uuid="wf-1", name="TestWF"):
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


def _make_runner(uuid="r-1", workflow=None, state="running"):
    from facetwork.runtime.entities import RunnerDefinition

    if workflow is None:
        workflow = _make_workflow()
    return RunnerDefinition(
        uuid=uuid,
        workflow_id=workflow.uuid,
        workflow=workflow,
        state=state,
    )


class TestHomeRoute:
    def test_home_page(self, client):
        tc, store = client
        # "/" is no longer a standalone home page — it 302-redirects to /v3/workflows.
        resp = tc.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/v3/workflows"

    def test_home_shows_counts(self, client):
        tc, store = client
        # Following the redirect lands on the v3 Runs list, whose sidebar nav
        # exposes the Servers section.
        resp = tc.get("/")
        assert resp.status_code == 200
        assert "Runs" in resp.text
        assert "Servers" in resp.text

    def test_home_with_data(self, client):
        """Covers runner_counts and task_counts loop bodies."""
        tc, store = client
        from facetwork.runtime.entities import TaskDefinition, TaskState

        store.save_runner(_make_runner("r-1", state="running"))
        store.save_runner(_make_runner("r-2", state="completed"))
        task = TaskDefinition(
            uuid="task-1",
            name="T1",
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="s-1",
            task_list_name="default",
            state=TaskState.PENDING,
        )
        store.save_task(task)

        resp = tc.get("/")
        assert resp.status_code == 200


class TestRunnerRoutes:
    def test_runner_list_redirects_to_v3(self, client):
        tc, store = client
        resp = tc.get("/runners", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/workflows"

    def test_runner_list_with_data_redirects(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            WorkflowDefinition,
        )

        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        resp = tc.get("/runners", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/workflows"

    def test_runner_list_filter_by_state_redirects(self, client):
        tc, store = client
        resp = tc.get("/runners?state=running", follow_redirects=False)
        assert resp.status_code == 307
        assert "tab=running" in resp.headers["location"]

    def test_runner_detail_redirects_to_v3(self, client):
        tc, store = client
        resp = tc.get("/runners/nonexistent", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/workflows/nonexistent"

    def test_runner_detail_with_data_redirects(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            WorkflowDefinition,
        )

        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        resp = tc.get("/runners/r-1", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/workflows/r-1"

    def test_cancel_runner(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            WorkflowDefinition,
        )

        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        resp = tc.post("/runners/r-1/cancel", follow_redirects=False)
        assert resp.status_code == 303

        updated = store.get_runner("r-1")
        assert updated.state == "cancelled"

    def test_pause_runner(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            WorkflowDefinition,
        )

        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        resp = tc.post("/runners/r-1/pause", follow_redirects=False)
        assert resp.status_code == 303

        updated = store.get_runner("r-1")
        assert updated.state == "paused"

    def test_resume_runner(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            WorkflowDefinition,
        )

        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.PAUSED,
        )
        store.save_runner(runner)

        resp = tc.post("/runners/r-1/resume", follow_redirects=False)
        assert resp.status_code == 303

        updated = store.get_runner("r-1")
        assert updated.state == "running"

    # NOTE: the legacy /runners/{id}/steps and /runners/{id}/logs pages were
    # removed with the v3 migration (the v3 workflow-detail page renders a live
    # DAG graph instead of a flat steps/logs list), so those tests were deleted.


class TestStepRoutes:
    def test_step_detail_not_found(self, client):
        tc, store = client
        # Legacy /steps/{id} is gone; the v3 step detail page supersedes it.
        resp = tc.get("/v3/steps/nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_step_detail_with_data(self, client):
        tc, store = client
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import step_id, workflow_id

        sid = step_id()
        wid = workflow_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wid,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        store.save_step(step)

        resp = tc.get(f"/v3/steps/{sid}")
        assert resp.status_code == 200
        assert "VariableAssignment" in resp.text

    def test_retry_step(self, client):
        tc, store = client
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import step_id, workflow_id

        sid = step_id()
        wid = workflow_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wid,
            object_type="VariableAssignment",
            state="state.facet.error.Failed",
        )
        store.save_step(step)

        resp = tc.post(f"/steps/{sid}/retry", follow_redirects=False)
        assert resp.status_code == 303

        updated = store.get_step(sid)
        assert updated.state == "state.EventTransmit"

    def test_retry_step_resets_task(self, client):
        tc, store = client
        from facetwork.runtime.entities import TaskDefinition
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import step_id, workflow_id

        sid = step_id()
        wid = workflow_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wid,
            object_type="VariableAssignment",
            state="state.statement.Error",
        )
        store.save_step(step)

        task = TaskDefinition(
            uuid="task-retry-1",
            name="TestTask",
            runner_id="r-1",
            workflow_id=wid,
            flow_id="flow-1",
            step_id=sid,
            task_list_name="default",
            state="failed",
            error={"message": "Connection timeout"},
        )
        store.save_task(task)

        resp = tc.post(f"/steps/{sid}/retry", follow_redirects=False)
        assert resp.status_code == 303

        updated_step = store.get_step(sid)
        assert updated_step.state == "state.EventTransmit"

        updated_task = store.get_task_for_step(sid)
        assert updated_task.state == "pending"
        assert updated_task.error is None


# NOTE: TestLogRoutes (the legacy /logs/{runner_id} page) was removed with the
# v3 migration; per-runner log viewing is now part of the v3 workflow/step
# pages, so that test was deleted (no standalone analog).


class TestFlowRoutes:
    def test_flow_list_empty(self, client):
        tc, store = client
        # Legacy /flows is gone; the v3 Library page supersedes it.
        resp = tc.get("/v3/flows")
        assert resp.status_code == 200
        assert "Library" in resp.text

    def test_flow_list_with_data(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        flow = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-1"),
        )
        store.save_flow(flow)

        resp = tc.get("/v3/flows")
        assert resp.status_code == 200
        assert "TestFlow" in resp.text

    def test_flow_detail(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        flow = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-1"),
        )
        store.save_flow(flow)

        resp = tc.get("/v3/flows/flow-1")
        assert resp.status_code == 200
        assert "TestFlow" in resp.text

    def test_flow_detail_not_found(self, client):
        tc, store = client
        resp = tc.get("/v3/flows/nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_flow_source_with_data(self, client):
        """Covers v3 flow_source route (renders FFL on the code view)."""
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity, SourceText

        flow = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-1"),
            compiled_sources=[SourceText(name="main.ffl", content="facet Foo()", language="afl")],
        )
        store.save_flow(flow)

        resp = tc.get("/v3/flows/flow-1/source")
        assert resp.status_code == 200
        assert "facet Foo()" in resp.text

    def test_flow_source_not_found(self, client):
        tc, store = client
        resp = tc.get("/v3/flows/nonexistent/source")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_flow_json_with_valid_afl(self, client):
        """Covers v3 flow_json route — successful parse."""
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity, SourceText

        flow = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-1"),
            compiled_sources=[
                SourceText(name="main.ffl", content="facet Bar(x: String)", language="afl")
            ],
        )
        store.save_flow(flow)

        resp = tc.get("/v3/flows/flow-1/json")
        assert resp.status_code == 200
        assert "Bar" in resp.text
        assert "String" in resp.text

    def test_flow_json_with_parse_error(self, client):
        """Covers v3 flow_json route — parse error path."""
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity, SourceText

        flow = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="BadFlow", path="/test", uuid="flow-1"),
            compiled_sources=[
                SourceText(name="bad.ffl", content="this is not valid afl {{{{", language="afl")
            ],
        )
        store.save_flow(flow)

        resp = tc.get("/v3/flows/flow-1/json")
        assert resp.status_code == 200
        assert "Failed to emit JSON" in resp.text or "Error" in resp.text

    def test_flow_json_not_found(self, client):
        tc, store = client
        resp = tc.get("/v3/flows/nonexistent/json")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_flow_json_no_sources(self, client):
        """Covers v3 flow_json when flow exists but has no compiled sources/AST."""
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        flow = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="EmptyFlow", path="/test", uuid="flow-1"),
        )
        store.save_flow(flow)

        resp = tc.get("/v3/flows/flow-1/json")
        assert resp.status_code == 200
        assert "No compiled AST or sources" in resp.text


class TestServerRoutes:
    def test_server_list(self, client):
        tc, store = client
        # Legacy /servers page is gone; the v3 Servers page supersedes it.
        resp = tc.get("/v3/servers")
        assert resp.status_code == 200
        assert "Servers" in resp.text

    def test_server_list_with_data(self, client):
        tc, store = client
        import time as _time

        from facetwork.runtime.entities import ServerDefinition, ServerState

        server = ServerDefinition(
            uuid="s-1",
            server_group="workers",
            service_name="facetwork",
            server_name="worker-01",
            state=ServerState.RUNNING,
            # Fresh heartbeat so the effective state stays "running" (a stale
            # ping_time would make the v3 page mark it "down").
            ping_time=int(_time.time() * 1000),
        )
        store.save_server(server)

        resp = tc.get("/v3/servers")
        assert resp.status_code == 200
        assert "worker-01" in resp.text

    def _live_server(self, store, uuid, envs, name="worker-01"):
        import time as _time

        from facetwork.runtime.entities import ServerDefinition, ServerState

        store.save_server(
            ServerDefinition(
                uuid=uuid,
                server_group="workers",
                service_name="facetwork",
                server_name=name,
                state=ServerState.RUNNING,
                ping_time=int(_time.time() * 1000),
                provided_environments=envs,
            )
        )

    def test_server_list_shows_provided_environment_coverage(self, client):
        """A hash a live runner advertises is listed as covered, with its host."""
        tc, store = client
        self._live_server(store, "s-env", ["0aabcaefe02915c3"])

        resp = tc.get("/v3/servers")
        assert resp.status_code == 200
        assert "Script environments" in resp.text
        assert "0aabcaefe02915c3" in resp.text
        assert "1 runner" in resp.text

    def test_server_list_flags_environment_no_runner_provides(self, client):
        """The silent failure this panel exists for: a pending script task whose
        manifest hash no live runner advertises can never be claimed."""
        tc, store = client
        from facetwork.runtime.entities import TaskDefinition

        self._live_server(store, "s-env", ["0aabcaefe02915c3"])
        store.save_task(
            TaskDefinition(
                uuid="t-1",
                name="canonical.envscript.Humanize",
                runner_id="r-1",
                workflow_id="w-1",
                flow_id="f-1",
                step_id="st-1",
                state="pending",
                environment_hash="notprovided00000",
                kind="script",
            )
        )

        resp = tc.get("/v3/servers")
        assert resp.status_code == 200
        assert "notprovided00000" in resp.text
        assert "no live runner provides this" in resp.text

    # NOTE: the v3 Servers page is a single list (runner processes grouped by
    # host) with no per-server detail page, so the server-detail tests
    # (detail, detail_not_found, handled_stats, with_error) and the
    # list-links-to-detail test were deleted — they have no v3 analog.
    # The /api/servers/{id} JSON endpoint below is unchanged and still tested.

    def test_api_server_detail(self, client):
        tc, store = client
        from facetwork.runtime.entities import ServerDefinition, ServerState

        server = ServerDefinition(
            uuid="s-1",
            server_group="workers",
            service_name="facetwork",
            server_name="worker-01",
            state=ServerState.RUNNING,
        )
        store.save_server(server)

        resp = tc.get("/api/servers/s-1")
        assert resp.status_code == 200
        assert resp.json()["uuid"] == "s-1"

    def test_api_server_not_found(self, client):
        tc, store = client
        resp = tc.get("/api/servers/nonexistent")
        assert resp.status_code == 404


class TestTaskRoutes:
    def test_task_list(self, client):
        tc, store = client
        # Legacy /tasks page is gone; the v3 Tasks page supersedes it.
        resp = tc.get("/v3/tasks")
        assert resp.status_code == 200
        assert "Task" in resp.text

    def test_task_list_with_data(self, client):
        tc, store = client
        from facetwork.runtime.entities import TaskDefinition, TaskState

        task = TaskDefinition(
            uuid="task-1",
            name="SendEmail",
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="step-1",
            task_list_name="email-tasks",
            state=TaskState.PENDING,
        )
        store.save_task(task)

        resp = tc.get("/v3/tasks")
        assert resp.status_code == 200
        assert "SendEmail" in resp.text


class TestApiRoutes:
    def test_api_runners(self, client):
        tc, store = client
        resp = tc.get("/api/runners")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_runners_with_data(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            WorkflowDefinition,
        )

        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        resp = tc.get("/api/runners")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["uuid"] == "r-1"

    def test_api_runner_detail(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            WorkflowDefinition,
        )

        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        resp = tc.get("/api/runners/r-1")
        assert resp.status_code == 200
        assert resp.json()["uuid"] == "r-1"

    def test_api_runner_not_found(self, client):
        tc, store = client
        resp = tc.get("/api/runners/nonexistent")
        assert resp.status_code == 404

    def test_api_flows(self, client):
        tc, store = client
        resp = tc.get("/api/flows")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_servers(self, client):
        tc, store = client
        resp = tc.get("/api/servers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_step_not_found(self, client):
        tc, store = client
        resp = tc.get("/api/steps/nonexistent")
        assert resp.status_code == 404

    def test_api_step_detail_with_data(self, client):
        """Covers api.py api_step_detail success path and _step_dict."""
        tc, store = client
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import step_id, workflow_id

        sid = step_id()
        wid = workflow_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wid,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        store.save_step(step)

        resp = tc.get(f"/api/steps/{sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sid
        assert data["object_type"] == "VariableAssignment"

    def test_api_runners_filter_by_state(self, client):
        """Covers api.py api_runners with state parameter."""
        tc, store = client
        from facetwork.runtime.entities import RunnerState

        store.save_runner(_make_runner("r-1", state=RunnerState.RUNNING))
        store.save_runner(_make_runner("r-2", state=RunnerState.COMPLETED))

        resp = tc.get("/api/runners?state=running")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["state"] == "running"

    def test_api_runners_partial(self, client):
        """Covers api.py api_runners with partial=true (htmx partial rendering)."""
        tc, store = client

        store.save_runner(_make_runner("r-1"))

        resp = tc.get("/api/runners?partial=true")
        assert resp.status_code == 200
        assert "r-1" in resp.text

    def test_api_runner_steps_json(self, client):
        """Covers api.py api_runner_steps JSON path."""
        tc, store = client
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import step_id as make_step_id

        wf = _make_workflow()
        runner = _make_runner("r-1", workflow=wf)
        store.save_runner(runner)

        sid = make_step_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wf.uuid,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        store.save_step(step)

        resp = tc.get("/api/runners/r-1/steps")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["object_type"] == "VariableAssignment"

    def test_api_runner_steps_not_found(self, client):
        """Covers api.py api_runner_steps 404 path."""
        tc, store = client
        resp = tc.get("/api/runners/nonexistent/steps")
        assert resp.status_code == 404

    def test_api_runner_steps_partial(self, client):
        """Covers api.py api_runner_steps with partial=true."""
        tc, store = client
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import step_id as make_step_id

        wf = _make_workflow()
        runner = _make_runner("r-1", workflow=wf)
        store.save_runner(runner)

        sid = make_step_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wf.uuid,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        store.save_step(step)

        resp = tc.get("/api/runners/r-1/steps?partial=true")
        assert resp.status_code == 200
        assert "VariableAssignment" in resp.text


def _make_handler(facet_name="ns.TestFacet", module_uri="my.handlers", entrypoint="handle"):
    from facetwork.runtime.entities import HandlerRegistration

    return HandlerRegistration(
        facet_name=facet_name,
        module_uri=module_uri,
        entrypoint=entrypoint,
        version="1.0.0",
        timeout_ms=30000,
    )


class TestHandlerRoutes:
    def test_handler_list_redirects_to_v3(self, client):
        tc, store = client
        resp = tc.get("/handlers", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/handlers"

    def test_handler_list_with_data_redirects(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler())
        resp = tc.get("/handlers", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/handlers"

    def test_handler_detail_redirects_to_v3(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler())
        resp = tc.get("/handlers/ns.TestFacet", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/handlers"

    def test_handler_detail_not_found_redirects(self, client):
        tc, store = client
        resp = tc.get("/handlers/ns.Missing", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/handlers"

    def test_delete_handler(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler())
        resp = tc.post("/handlers/ns.TestFacet/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert store.get_handler_registration("ns.TestFacet") is None

    def test_delete_handler_not_found(self, client):
        tc, store = client
        resp = tc.post("/handlers/ns.Missing/delete", follow_redirects=False)
        assert resp.status_code == 303

    def test_api_handlers_empty(self, client):
        tc, store = client
        resp = tc.get("/api/handlers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_handlers_with_data(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler())
        resp = tc.get("/api/handlers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["facet_name"] == "ns.TestFacet"
        assert data[0]["module_uri"] == "my.handlers"

    def test_home_handler_route(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler())
        resp = tc.get("/v3/handlers")
        assert resp.status_code == 200
        assert "Handlers" in resp.text


def _make_event_task(
    uuid="evt-1", step_id="step-1", workflow_id="wf-1", state="pending", name="fw:execute"
):
    from facetwork.runtime.entities import TaskDefinition

    return TaskDefinition(
        uuid=uuid,
        name=name,
        runner_id="",
        workflow_id=workflow_id,
        flow_id="",
        step_id=step_id,
        state=state,
        created=0,
        updated=0,
        task_list_name="default",
        data={"key": "value"},
    )


class TestEventRoutes:
    def test_event_list_empty(self, client):
        tc, store = client
        # Legacy /events page is gone; the v3 Events page supersedes it.
        resp = tc.get("/v3/events")
        assert resp.status_code == 200
        assert "Events" in resp.text

    def test_event_list_with_data(self, client):
        tc, store = client
        store.save_task(_make_event_task())
        resp = tc.get("/v3/events")
        assert resp.status_code == 200
        # The v3 events list renders the event name (task name), not its uuid.
        assert "fw:execute" in resp.text

    # NOTE: the legacy /events/{id} detail page was removed with the v3
    # migration and has no v3 analog (the v3 events page is list-only), so the
    # event-detail tests were deleted.

    def test_api_events_empty(self, client):
        tc, store = client
        resp = tc.get("/api/events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_events_with_data(self, client):
        tc, store = client
        store.save_task(_make_event_task())
        resp = tc.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "evt-1"
        assert data[0]["event_type"] == "fw:execute"


def _make_published_source(
    uuid="src-1",
    namespace_name="geo.cache",
    source_text="facet CacheLookup(key: String)",
    version="latest",
    origin="dashboard",
):
    from facetwork.runtime.entities import PublishedSource

    return PublishedSource(
        uuid=uuid,
        namespace_name=namespace_name,
        source_text=source_text,
        namespaces_defined=["geo.cache"],
        version=version,
        published_at=1000,
        origin=origin,
        checksum="abc123def456",
    )


class TestSourceRoutes:
    def test_source_list_empty(self, client):
        tc, store = client
        resp = tc.get("/sources")
        assert resp.status_code == 200
        assert "Sources" in resp.text

    def test_source_list_with_data(self, client):
        tc, store = client
        store.save_published_source(_make_published_source())
        resp = tc.get("/sources")
        assert resp.status_code == 200
        assert "geo.cache" in resp.text

    def test_source_detail(self, client):
        tc, store = client
        store.save_published_source(_make_published_source())
        resp = tc.get("/sources/geo.cache")
        assert resp.status_code == 200
        assert "geo.cache" in resp.text
        assert "CacheLookup" in resp.text

    def test_source_detail_not_found(self, client):
        tc, store = client
        resp = tc.get("/sources/nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    def test_source_detail_shows_source_text(self, client):
        tc, store = client
        store.save_published_source(_make_published_source())
        resp = tc.get("/sources/geo.cache")
        assert resp.status_code == 200
        assert "facet CacheLookup" in resp.text

    def test_delete_source(self, client):
        tc, store = client
        store.save_published_source(_make_published_source())
        resp = tc.post("/sources/geo.cache/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert store.get_source_by_namespace("geo.cache") is None

    def test_api_sources_empty(self, client):
        tc, store = client
        resp = tc.get("/api/sources")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_sources_with_data(self, client):
        tc, store = client
        store.save_published_source(_make_published_source())
        resp = tc.get("/api/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["namespace_name"] == "geo.cache"

    def test_home_source_route(self, client):
        tc, store = client
        store.save_published_source(_make_published_source())
        resp = tc.get("/sources")
        assert resp.status_code == 200
        assert "Sources" in resp.text


def _make_flow_with_ns(flow_id="flow-1", ns_name="test.ns", ns_uuid="ns-1"):
    from facetwork.runtime.entities import (
        FacetDefinition,
        FlowDefinition,
        FlowIdentity,
        NamespaceDefinition,
        WorkflowDefinition,
    )

    return FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name="TestFlow", path="/test", uuid=flow_id),
        namespaces=[NamespaceDefinition(uuid=ns_uuid, name=ns_name)],
        facets=[FacetDefinition(uuid="f-1", name="MyFacet", namespace_id=ns_uuid)],
        workflows=[
            WorkflowDefinition(
                uuid="wf-1",
                name="MyWorkflow",
                namespace_id=ns_uuid,
                facet_id="f-1",
                flow_id=flow_id,
                starting_step="s-1",
                version="1.0",
            )
        ],
    )


class TestNamespaceRoutes:
    def test_namespace_list_empty(self, client):
        tc, store = client
        resp = tc.get("/namespaces")
        assert resp.status_code == 200
        assert "Namespaces" in resp.text

    def test_namespace_list_with_data(self, client):
        tc, store = client
        store.save_flow(_make_flow_with_ns())
        resp = tc.get("/namespaces")
        assert resp.status_code == 200
        assert "test.ns" in resp.text

    def test_namespace_detail(self, client):
        tc, store = client
        store.save_flow(_make_flow_with_ns())
        resp = tc.get("/namespaces/test.ns")
        assert resp.status_code == 200
        assert "test.ns" in resp.text
        assert "MyFacet" in resp.text

    def test_namespace_detail_not_found(self, client):
        tc, store = client
        resp = tc.get("/namespaces/nonexistent")
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()


# NOTE: TestWorkflowValidation was removed in full. The legacy authoring form
# (/workflows/new with a "Validate Only" button and the /workflows/validate
# POST endpoint) was removed with the v3 migration. The v3 /workflows/new page
# is a browser of already-compiled workflows to run — there is no in-dashboard
# FFL authoring/validation surface, so these tests have no v3 analog.


# =============================================================================
# v0.10.13 — Task Detail, Filtering, Search, API Expansion
# =============================================================================


def _make_task(uuid="task-1", name="SendEmail", state="pending", error=None, data=None):
    from facetwork.runtime.entities import TaskDefinition

    return TaskDefinition(
        uuid=uuid,
        name=name,
        runner_id="r-1",
        workflow_id="wf-1",
        flow_id="flow-1",
        step_id="step-1",
        task_list_name="default",
        state=state,
        data_type="email",
        error=error,
        data=data,
    )


class TestTaskDetailAndFiltering:
    def test_task_list_filter_by_state(self, client):
        tc, store = client
        # The v3 tasks list renders the task NAME (not uuid), so distinct
        # names make the state filter observable.
        store.save_task(_make_task("t-1", name="PendingTask", state="pending"))
        store.save_task(_make_task("t-2", name="RunningTask", state="running"))

        resp = tc.get("/v3/tasks?state=pending")
        assert resp.status_code == 200
        assert "PendingTask" in resp.text
        assert "RunningTask" not in resp.text

    # NOTE: the legacy /tasks/{id} detail page was removed with the v3
    # migration and the v3 Tasks page is list-only (no per-task detail), so the
    # task-detail tests (detail, not_found, shows_error, shows_data, links,
    # shows_step_name_and_duration) were deleted — no v3 analog.

    def test_task_list_shows_step_name(self, client):
        tc, store = client
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import StepId, WorkflowId

        step = StepDefinition(
            id=StepId("step-1"),
            workflow_id=WorkflowId("wf-1"),
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
            statement_id="myStep",
        )
        store.save_step(step)
        store.save_task(_make_task("t-1"))

        resp = tc.get("/v3/tasks")
        assert resp.status_code == 200
        assert "myStep" in resp.text

    # NOTE: test_step_list_shows_duration was removed — the legacy
    # /runners/{id}/steps list (which rendered a per-step "Duration" column) is
    # gone; the v3 workflow-detail page shows a live DAG graph instead.


class TestFlowDetailImprovements:
    def test_flow_detail_shows_namespaces(self, client):
        tc, store = client
        store.save_flow(_make_flow_with_ns())

        # v3 flow detail surfaces a "Namespaces" metric (namespace names are
        # derived from workflow names, not shown verbatim).
        resp = tc.get("/v3/flows/flow-1")
        assert resp.status_code == 200
        assert "Namespaces" in resp.text

    def test_flow_detail_shows_facets(self, client):
        tc, store = client
        store.save_flow(_make_flow_with_ns())

        resp = tc.get("/v3/flows/flow-1")
        assert resp.status_code == 200
        assert "Facets" in resp.text

    def test_flow_detail_shows_execution_history(self, client):
        tc, store = client
        flow = _make_flow_with_ns()
        store.save_flow(flow)
        runner = _make_runner("r-1", workflow=flow.workflows[0], state="completed")
        store.save_runner(runner)

        # v3 names the run history section "Recent runs".
        resp = tc.get("/v3/flows/flow-1")
        assert resp.status_code == 200
        assert "Recent runs" in resp.text


class TestListFiltering:
    def test_event_list_filter_by_state(self, client):
        tc, store = client
        # The v3 events list renders the event name; distinct names make the
        # state filter observable.
        store.save_task(_make_event_task("evt-1", state="pending", name="PendingEvent"))
        store.save_task(
            _make_event_task("evt-2", step_id="step-2", state="completed", name="DoneEvent")
        )

        resp = tc.get("/v3/events?state=pending")
        assert resp.status_code == 200
        assert "PendingEvent" in resp.text
        assert "DoneEvent" not in resp.text

    def test_server_list_filter_by_state(self, client):
        tc, store = client
        import time as _time

        from facetwork.runtime.entities import ServerDefinition, ServerState

        server = ServerDefinition(
            uuid="s-1",
            server_group="workers",
            service_name="facetwork",
            server_name="worker-01",
            state=ServerState.RUNNING,
            ping_time=int(_time.time() * 1000),
        )
        store.save_server(server)

        # v3 servers filters via ?tab=… (default "running"); a freshly-pinged
        # running server appears under the Running tab.
        resp = tc.get("/v3/servers?tab=running")
        assert resp.status_code == 200
        assert "worker-01" in resp.text

    def test_flow_list_search(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        store.save_flow(
            FlowDefinition(
                uuid="flow-1",
                name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-1"),
            )
        )
        store.save_flow(
            FlowDefinition(
                uuid="flow-2",
                name=FlowIdentity(name="OtherFlow", path="/other", uuid="flow-2"),
            )
        )

        resp = tc.get("/v3/flows?q=Test")
        assert resp.status_code == 200
        assert "TestFlow" in resp.text
        assert "OtherFlow" not in resp.text

    def test_handler_list_search_redirects(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler("ns.TestFacet"))
        store.save_handler_registration(_make_handler("other.Handler", module_uri="other.mod"))

        resp = tc.get("/handlers?q=ns.Test", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/v3/handlers"

    def test_source_list_search(self, client):
        tc, store = client
        store.save_published_source(
            _make_published_source(uuid="src-1", namespace_name="geo.cache")
        )
        store.save_published_source(
            _make_published_source(uuid="src-2", namespace_name="auth.login")
        )

        resp = tc.get("/sources?q=geo")
        assert resp.status_code == 200
        assert "geo.cache" in resp.text
        assert "auth.login" not in resp.text


class TestApiExpansion:
    def test_api_tasks(self, client):
        tc, store = client
        resp = tc.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

        store.save_task(_make_task("t-1"))
        resp = tc.get("/api/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_api_tasks_filter_by_state(self, client):
        tc, store = client
        store.save_task(_make_task("t-1", state="pending"))
        store.save_task(_make_task("t-2", state="running"))

        resp = tc.get("/api/tasks?state=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["uuid"] == "t-1"

    def test_api_task_detail(self, client):
        tc, store = client
        store.save_task(_make_task("t-1", name="ProcessOrder"))

        resp = tc.get("/api/tasks/t-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uuid"] == "t-1"
        assert data["name"] == "ProcessOrder"

    def test_api_task_not_found(self, client):
        tc, store = client
        resp = tc.get("/api/tasks/nonexistent")
        assert resp.status_code == 404

    def test_api_flow_detail(self, client):
        tc, store = client
        store.save_flow(_make_flow_with_ns())

        resp = tc.get("/api/flows/flow-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uuid"] == "flow-1"
        assert len(data["namespaces"]) == 1
        assert data["namespaces"][0]["name"] == "test.ns"

    def test_api_flow_not_found(self, client):
        tc, store = client
        resp = tc.get("/api/flows/nonexistent")
        assert resp.status_code == 404

    def test_api_namespaces(self, client):
        tc, store = client
        store.save_flow(_make_flow_with_ns())

        resp = tc.get("/api/namespaces")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "test.ns"
        assert data[0]["flow_id"] == "flow-1"

    def test_api_events_filter(self, client):
        tc, store = client
        store.save_task(_make_event_task("evt-1", state="pending"))
        store.save_task(_make_event_task("evt-2", step_id="step-2", state="completed"))

        resp = tc.get("/api/events?state=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "evt-1"

    def test_api_servers_filter(self, client):
        tc, store = client
        from facetwork.runtime.entities import ServerDefinition, ServerState

        store.save_server(
            ServerDefinition(
                uuid="s-1",
                server_group="workers",
                service_name="facetwork",
                server_name="worker-01",
                state=ServerState.RUNNING,
            )
        )
        store.save_server(
            ServerDefinition(
                uuid="s-2",
                server_group="workers",
                service_name="facetwork",
                server_name="worker-02",
                state=ServerState.ERROR,
            )
        )

        resp = tc.get("/api/servers?state=running")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["uuid"] == "s-1"

    def test_api_flows_search(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        store.save_flow(
            FlowDefinition(
                uuid="flow-1",
                name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-1"),
            )
        )
        store.save_flow(
            FlowDefinition(
                uuid="flow-2",
                name=FlowIdentity(name="OtherFlow", path="/other", uuid="flow-2"),
            )
        )

        resp = tc.get("/api/flows?q=Test")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "TestFlow"


# =============================================================================
# v0.12.38 — Doc comments display & facets in namespace view
# =============================================================================


def _make_flow_with_docs(flow_id="flow-doc"):
    """Create a flow with doc comments on namespace, facets, and workflows."""
    from facetwork.runtime.entities import (
        FacetDefinition,
        FlowDefinition,
        FlowIdentity,
        NamespaceDefinition,
        Parameter,
        SourceText,
        WorkflowDefinition,
    )

    ns_doc = {"description": "Core handlers namespace.", "params": [], "returns": []}
    facet_doc = {
        "description": "Increments a value.",
        "params": [{"name": "value", "description": "The input value."}],
        "returns": [],
    }
    wf_doc = {
        "description": "Adds one twice.",
        "params": [{"name": "input", "description": "The starting value."}],
        "returns": [{"name": "output", "description": "The input plus two."}],
    }

    return FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name="DocFlow", path="/test", uuid=flow_id),
        namespaces=[NamespaceDefinition(uuid="ns-doc", name="handlers", documentation=ns_doc)],
        facets=[
            FacetDefinition(
                uuid="f-doc",
                name="handlers.AddOne",
                namespace_id="ns-doc",
                parameters=[Parameter(name="value", value=None, type_hint="Long")],
                return_type="result",
                documentation=facet_doc,
            ),
        ],
        workflows=[
            WorkflowDefinition(
                uuid="wf-doc",
                name="handlers.DoubleAddOne",
                namespace_id="ns-doc",
                facet_id="f-doc",
                flow_id=flow_id,
                starting_step="s-1",
                version="1.0",
                documentation=wf_doc,
            )
        ],
        compiled_sources=[
            SourceText(
                name="main.ffl",
                content="""
namespace handlers {
    /**
     * Increments a value.
     * @param value The input value.
     */
    event facet AddOne(value: Long) => (result: Long)

    /**
     * Adds one twice.
     * @param input The starting value.
     * @return output The input plus two.
     */
    workflow DoubleAddOne(input: Long) => (output: Long) andThen {
        first = AddOne(value = $.input)
        second = AddOne(value = first.result)
        yield DoubleAddOne(output = second.result)
    }
}
""",
                language="afl",
            )
        ],
    )


class TestDocCommentDisplay:
    # NOTE: the legacy flow-detail doc tables (namespace/facet documentation)
    # and the per-namespace page (/flows/{id}/ns/{ns}) were removed with the v3
    # migration. The v3 flow detail page shows namespace-grouped workflows +
    # recent runs (no doc tables), and there is no per-namespace page — so
    # test_flow_detail_shows_namespace_doc, test_flow_detail_shows_facet_doc,
    # test_flow_namespace_shows_facets and test_flow_namespace_shows_workflow_doc
    # were deleted. The run-form doc surfacing below survives in v3.

    def test_flow_run_shows_workflow_doc(self, client):
        """v3 run page shows workflow documentation above parameters."""
        tc, store = client
        flow = _make_flow_with_docs()
        store.save_flow(flow)
        store.save_workflow(flow.workflows[0])

        resp = tc.get("/v3/flows/flow-doc/run/wf-doc")
        assert resp.status_code == 200
        assert "Adds one twice" in resp.text

    def test_flow_run_shows_param_descriptions(self, client):
        """v3 run page shows parameter descriptions from @param tags."""
        tc, store = client
        flow = _make_flow_with_docs()
        store.save_flow(flow)
        store.save_workflow(flow.workflows[0])

        resp = tc.get("/v3/flows/flow-doc/run/wf-doc")
        assert resp.status_code == 200
        assert "The starting value" in resp.text


class TestSeedWorkflowDocumentation:
    def test_collect_workflows_includes_doc(self):
        """_collect_workflows returns workflow dicts that include 'doc' field."""
        import json as json_mod

        from facetwork.emitter import JSONEmitter
        from facetwork.parser import FFLParser

        source = """
namespace test_ns {
    /** Adds one to a value.
     * @param v The value.
     * @return r The result.
     */
    workflow AddOne(v: Long) => (r: Long) andThen {
        yield AddOne(r = $.v)
    }
}
"""
        parser = FFLParser()
        ast = parser.parse(source)
        emitter = JSONEmitter(include_locations=False)
        program_dict = json_mod.loads(emitter.emit(ast))

        # Import _collect_workflows from seed
        import sys

        sys.path.insert(0, "/Users/ralph_lemke/facetwork")
        from docker.seed.seed import _collect_workflows

        workflows = _collect_workflows(program_dict)
        assert len(workflows) >= 1
        # Find our specific workflow
        match = [(q, d) for q, d in workflows if q == "test_ns.AddOne"]
        assert len(match) >= 1
        qname, wf_dict = match[0]
        assert wf_dict.get("doc") is not None
        assert "Adds one" in wf_dict["doc"]["description"]

    def test_workflow_definition_accepts_dict_doc(self):
        """WorkflowDefinition.documentation accepts dict values."""
        from facetwork.runtime.entities import WorkflowDefinition

        doc = {"description": "Test doc", "params": [], "returns": []}
        wf = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
            documentation=doc,
        )
        assert wf.documentation == doc
        assert isinstance(wf.documentation, dict)


class TestStepLogs:
    """Tests for step log API and dashboard display."""

    def test_api_step_logs_returns_entries(self, client):
        """GET /api/steps/{step_id}/logs returns step log entries."""
        tc, store = client
        from facetwork.runtime.entities import StepLogEntry
        from facetwork.runtime.types import generate_id

        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="step-log-1",
                workflow_id="wf-log-1",
                runner_id="runner-1",
                facet_name="ns.Test",
                source="framework",
                level="info",
                message="Task claimed: ns.Test",
                time=1000,
            )
        )
        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="step-log-1",
                workflow_id="wf-log-1",
                source="handler",
                level="success",
                message="Download complete",
                time=2000,
            )
        )

        resp = tc.get("/api/steps/step-log-1/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["message"] == "Task claimed: ns.Test"
        assert data[0]["source"] == "framework"
        assert data[1]["message"] == "Download complete"
        assert data[1]["source"] == "handler"

    def test_api_step_logs_empty_for_unknown(self, client):
        """GET /api/steps/{step_id}/logs returns empty array for unknown step."""
        tc, store = client
        resp = tc.get("/api/steps/nonexistent/logs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_step_detail_shows_logs_section(self, client):
        """v3 step detail page shows a 'Logs' section when logs exist."""
        tc, store = client
        from facetwork.runtime.entities import StepLogEntry
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import generate_id, step_id, workflow_id

        sid = step_id()
        wid = workflow_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wid,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        store.save_step(step)

        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id=sid,
                workflow_id=wid,
                source="framework",
                level="info",
                message="Task claimed: ns.Test",
                time=1000,
            )
        )

        resp = tc.get(f"/v3/steps/{sid}")
        assert resp.status_code == 200
        assert "Logs" in resp.text
        assert "Task claimed: ns.Test" in resp.text
