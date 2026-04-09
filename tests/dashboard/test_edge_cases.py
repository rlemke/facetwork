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

"""Edge-case integration tests for dashboard routes."""

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

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_edge", client=mock_client)

    app = create_app()
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


# =============================================================================
# TestHealthRoute
# =============================================================================


class TestHealthRoute:
    def test_health_returns_200(self, client):
        tc, store = client
        resp = tc.get("/health")
        assert resp.status_code == 200

    def test_health_json_body(self, client):
        tc, store = client
        resp = tc.get("/health")
        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"


# =============================================================================
# TestRunnerEdgeCases
# =============================================================================


class TestRunnerEdgeCases:
    def test_cancel_nonexistent_runner_returns_redirect(self, client):
        tc, store = client
        resp = tc.post("/runners/nonexistent/cancel", follow_redirects=False)
        assert resp.status_code == 303

    def test_pause_nonexistent_runner_returns_redirect(self, client):
        tc, store = client
        resp = tc.post("/runners/nonexistent/pause", follow_redirects=False)
        assert resp.status_code == 303

    def test_resume_nonexistent_runner_returns_redirect(self, client):
        tc, store = client
        resp = tc.post("/runners/nonexistent/resume", follow_redirects=False)
        assert resp.status_code == 303

    def test_runner_completed_state(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="completed"))
        resp = tc.get("/runners/r-1")
        assert resp.status_code == 200
        assert "completed" in resp.text

    def test_runner_failed_state(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-2", state="failed"))
        resp = tc.get("/runners/r-2")
        assert resp.status_code == 200
        assert "failed" in resp.text

    def test_runner_cancelled_state(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-3", state="cancelled"))
        resp = tc.get("/runners/r-3")
        assert resp.status_code == 200
        assert "cancelled" in resp.text


# =============================================================================
# TestStepEdgeCases
# =============================================================================


class TestStepEdgeCases:
    def test_step_with_params_display(self, client):
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
            facet_name="ns.MyFacet",
        )
        store.save_step(step)

        resp = tc.get(f"/steps/{sid}")
        assert resp.status_code == 200
        assert "VariableAssignment" in resp.text

    def test_step_in_event_transmit_state(self, client):
        tc, store = client
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import step_id, workflow_id

        sid = step_id()
        wid = workflow_id()
        step = StepDefinition(
            id=sid,
            workflow_id=wid,
            object_type="VariableAssignment",
            state="state.facet.event_transmit.Transmit",
        )
        store.save_step(step)

        resp = tc.get(f"/steps/{sid}")
        assert resp.status_code == 200
        assert "Transmit" in resp.text or "event_transmit" in resp.text

    def test_retry_nonexistent_step_returns_redirect(self, client):
        tc, store = client
        resp = tc.post("/steps/nonexistent/retry", follow_redirects=False)
        assert resp.status_code == 303

    def test_step_with_populated_returns(self, client):
        tc, store = client
        from facetwork.runtime.entities import Parameter
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import FacetAttributes, step_id, workflow_id

        sid = step_id()
        wid = workflow_id()
        attrs = FacetAttributes()
        attrs.returns = {"result": Parameter(name="result", value="hello", type_hint="String")}
        step = StepDefinition(
            id=sid,
            workflow_id=wid,
            object_type="VariableAssignment",
            state="state.facet.completion.Complete",
            attributes=attrs,
        )
        store.save_step(step)

        resp = tc.get(f"/steps/{sid}")
        assert resp.status_code == 200


# =============================================================================
# TestFlowEdgeCases
# =============================================================================


class TestFlowEdgeCases:
    def test_flow_with_multiple_workflows(self, client):
        tc, store = client
        from facetwork.runtime.entities import (
            FlowDefinition,
            FlowIdentity,
            WorkflowDefinition,
        )

        flow = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="MultiFlow", path="/multi", uuid="flow-1"),
            workflows=[
                WorkflowDefinition(
                    uuid="wf-1",
                    name="WF1",
                    namespace_id="ns-1",
                    facet_id="f-1",
                    flow_id="flow-1",
                    starting_step="s-1",
                    version="1.0",
                ),
                WorkflowDefinition(
                    uuid="wf-2",
                    name="WF2",
                    namespace_id="ns-1",
                    facet_id="f-2",
                    flow_id="flow-1",
                    starting_step="s-2",
                    version="1.0",
                ),
            ],
        )
        store.save_flow(flow)

        resp = tc.get("/flows/flow-1")
        assert resp.status_code == 200
        assert "MultiFlow" in resp.text

    def test_flow_with_no_sources(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        flow = FlowDefinition(
            uuid="flow-2",
            name=FlowIdentity(name="EmptySourceFlow", path="/empty", uuid="flow-2"),
        )
        store.save_flow(flow)

        resp = tc.get("/flows/flow-2/source")
        assert resp.status_code == 200

    def test_flow_source_view_with_content(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity, SourceText

        flow = FlowDefinition(
            uuid="flow-3",
            name=FlowIdentity(name="SourceFlow", path="/src", uuid="flow-3"),
            compiled_sources=[SourceText(name="main.ffl", content="facet Hello(x: Long)")],
        )
        store.save_flow(flow)

        resp = tc.get("/flows/flow-3/source")
        assert resp.status_code == 200
        assert "facet Hello(x: Long)" in resp.text

    def test_flow_json_with_multiple_sources(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity, SourceText

        flow = FlowDefinition(
            uuid="flow-4",
            name=FlowIdentity(name="DoubleSource", path="/dbl", uuid="flow-4"),
            compiled_sources=[
                SourceText(name="first.ffl", content="facet A(x: String)"),
                SourceText(name="second.ffl", content="facet B(y: Long)"),
            ],
        )
        store.save_flow(flow)

        # JSON view uses the first source
        resp = tc.get("/flows/flow-4/json")
        assert resp.status_code == 200
        assert "A" in resp.text


# =============================================================================
# TestTaskEdgeCases
# =============================================================================


class TestTaskEdgeCases:
    def test_task_pending_state(self, client):
        tc, store = client
        store.save_task(_make_task("t-1", state="pending"))
        resp = tc.get("/tasks/t-1")
        assert resp.status_code == 200
        assert "pending" in resp.text

    def test_task_running_state(self, client):
        tc, store = client
        store.save_task(_make_task("t-2", state="running"))
        resp = tc.get("/tasks/t-2")
        assert resp.status_code == 200
        assert "running" in resp.text

    def test_task_completed_state(self, client):
        tc, store = client
        store.save_task(_make_task("t-3", state="completed"))
        resp = tc.get("/tasks/t-3")
        assert resp.status_code == 200
        assert "completed" in resp.text

    def test_task_failed_state(self, client):
        tc, store = client
        store.save_task(_make_task("t-4", state="failed"))
        resp = tc.get("/tasks/t-4")
        assert resp.status_code == 200
        assert "failed" in resp.text

    def test_task_with_error_message(self, client):
        tc, store = client
        store.save_task(_make_task("t-5", error={"message": "Timeout exceeded"}))
        resp = tc.get("/tasks/t-5")
        assert resp.status_code == 200
        assert "Timeout exceeded" in resp.text

    def test_task_with_data_payload(self, client):
        tc, store = client
        store.save_task(_make_task("t-6", data={"recipient": "user@example.com"}))
        resp = tc.get("/tasks/t-6")
        assert resp.status_code == 200
        assert "user@example.com" in resp.text

    def test_empty_state_filter_returns_all_tasks(self, client):
        tc, store = client
        store.save_task(_make_task("t-7", state="pending"))
        store.save_task(_make_task("t-8", name="Other", state="running"))
        resp = tc.get("/tasks")
        assert resp.status_code == 200
        assert "t-7" in resp.text or "SendEmail" in resp.text


# =============================================================================
# TestServerEdgeCases
# =============================================================================


class TestServerEdgeCases:
    def test_server_with_multiple_groups(self, client):
        tc, store = client
        from facetwork.runtime.entities import ServerDefinition, ServerState

        store.save_server(
            ServerDefinition(
                uuid="s-1",
                server_group="workers",
                service_name="afl",
                server_name="worker-01",
                state=ServerState.RUNNING,
            )
        )
        store.save_server(
            ServerDefinition(
                uuid="s-2",
                server_group="managers",
                service_name="afl",
                server_name="manager-01",
                state=ServerState.RUNNING,
            )
        )
        resp = tc.get("/servers")
        assert resp.status_code == 200
        assert "workers" in resp.text
        assert "managers" in resp.text

    def test_server_with_handlers_list(self, client):
        tc, store = client
        from facetwork.runtime.entities import ServerDefinition, ServerState

        server = ServerDefinition(
            uuid="s-3",
            server_group="workers",
            service_name="afl",
            server_name="worker-02",
            state=ServerState.RUNNING,
            handlers=["osm.CacheLookup", "osm.ReverseGeocode"],
        )
        store.save_server(server)
        resp = tc.get("/servers/s-3")
        assert resp.status_code == 200
        assert "worker-02" in resp.text

    def test_server_in_shutdown_state(self, client):
        tc, store = client
        from facetwork.runtime.entities import ServerDefinition, ServerState

        server = ServerDefinition(
            uuid="s-4",
            server_group="workers",
            service_name="afl",
            server_name="worker-03",
            state=ServerState.SHUTDOWN,
        )
        store.save_server(server)
        resp = tc.get("/servers/s-4")
        assert resp.status_code == 200
        assert "shutdown" in resp.text

    def test_server_without_optional_fields(self, client):
        tc, store = client
        from facetwork.runtime.entities import ServerDefinition, ServerState

        server = ServerDefinition(
            uuid="s-5",
            server_group="default",
            service_name="afl",
            server_name="minimal-server",
            state=ServerState.STARTUP,
        )
        store.save_server(server)
        resp = tc.get("/servers/s-5")
        assert resp.status_code == 200
        assert "minimal-server" in resp.text


# =============================================================================
# TestEventEdgeCases
# =============================================================================


class TestEventEdgeCases:
    def test_event_execute_vs_resume_types(self, client):
        tc, store = client
        store.save_task(_make_event_task("evt-1", name="fw:execute"))
        store.save_task(_make_event_task("evt-2", step_id="step-2", name="fw:resume"))
        resp = tc.get("/events")
        assert resp.status_code == 200
        assert "evt-1" in resp.text
        assert "evt-2" in resp.text

    def test_event_with_large_data(self, client):
        tc, store = client
        from facetwork.runtime.entities import TaskDefinition

        large_data = {"data": "x" * 5000}
        task = TaskDefinition(
            uuid="evt-big",
            name="fw:execute",
            runner_id="",
            workflow_id="wf-1",
            flow_id="",
            step_id="step-1",
            state="pending",
            created=0,
            updated=0,
            task_list_name="default",
            data=large_data,
        )
        store.save_task(task)
        resp = tc.get("/events/evt-big")
        assert resp.status_code == 200

    def test_event_filter_nonexistent_state_returns_empty(self, client):
        tc, store = client
        store.save_task(_make_event_task("evt-1"))
        resp = tc.get("/events?state=nonexistent_state")
        assert resp.status_code == 200
        # Should return a page but without the event that has a different state
        assert "Events" in resp.text


# =============================================================================
# TestSourceEdgeCases
# =============================================================================


class TestSourceEdgeCases:
    def test_source_full_content_displayed(self, client):
        tc, store = client
        src = _make_published_source(
            source_text="namespace geo.cache {\n  facet CacheLookup(key: String)\n}"
        )
        store.save_published_source(src)
        resp = tc.get("/sources/geo.cache")
        assert resp.status_code == 200
        assert "CacheLookup" in resp.text
        assert "geo.cache" in resp.text

    def test_delete_nonexistent_source_returns_redirect(self, client):
        tc, store = client
        resp = tc.post("/sources/nonexistent.ns/delete", follow_redirects=False)
        assert resp.status_code == 303

    def test_source_detail_shows_version(self, client):
        tc, store = client
        # Use version="latest" (default) so the route can find it via get_source_by_namespace
        src = _make_published_source(version="latest")
        store.save_published_source(src)
        resp = tc.get("/sources/geo.cache")
        assert resp.status_code == 200
        assert "latest" in resp.text


# =============================================================================
# TestFilteringEdgeCases
# =============================================================================


class TestFilteringEdgeCases:
    def test_runner_filter_with_invalid_state(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/runners?state=nonexistent_state")
        assert resp.status_code == 200
        # Should still return a page, just with no matching runners

    def test_case_insensitive_search_query(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        store.save_flow(
            FlowDefinition(
                uuid="flow-1",
                name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-1"),
            )
        )
        # Search with lowercase should match uppercase name
        resp = tc.get("/flows?q=testflow")
        assert resp.status_code == 200
        assert "TestFlow" in resp.text

    def test_empty_search_query_returns_all(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        store.save_flow(
            FlowDefinition(
                uuid="flow-1",
                name=FlowIdentity(name="FlowA", path="/a", uuid="flow-1"),
            )
        )
        store.save_flow(
            FlowDefinition(
                uuid="flow-2",
                name=FlowIdentity(name="FlowB", path="/b", uuid="flow-2"),
            )
        )
        resp = tc.get("/flows")
        assert resp.status_code == 200
        assert "FlowA" in resp.text
        assert "FlowB" in resp.text
