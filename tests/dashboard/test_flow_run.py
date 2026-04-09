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

"""Integration tests for running workflows from the flow detail page."""

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


VALID_AFL_SOURCE = """
facet Compute(input: Long)

workflow SimpleWF(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield SimpleWF(result = s1.input)
}
"""

DEFAULTS_AFL_SOURCE = """
facet Compute(input: Long)

workflow DefaultsWF(x: Long = 42, name: String = "hello") => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield DefaultsWF(result = s1.input)
}
"""


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_flow_run", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _seed_flow(store, source=VALID_AFL_SOURCE, workflow_name="SimpleWF"):
    """Seed a flow + workflow into the store. Returns (flow, workflow_def)."""
    from facetwork.runtime.entities import (
        FlowDefinition,
        FlowIdentity,
        SourceText,
        WorkflowDefinition,
    )
    from facetwork.runtime.types import generate_id

    flow_id = generate_id()
    wf_id = generate_id()

    flow = FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name=workflow_name, path="test", uuid=flow_id),
        compiled_sources=[SourceText(name="source.ffl", content=source)],
    )
    store.save_flow(flow)

    workflow_def = WorkflowDefinition(
        uuid=wf_id,
        name=workflow_name,
        namespace_id="test",
        facet_id=wf_id,
        flow_id=flow_id,
        starting_step="",
        version="1.0",
        date=0,
    )
    store.save_workflow(workflow_def)

    return flow, workflow_def


class TestFlowDetailRunButton:
    """Tests for the Run button in the flow detail page."""

    def test_run_link_appears_in_namespace_view(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        # Detail page now shows namespace groups — navigate to namespace page
        resp = tc.get(f"/flows/{flow.uuid}")
        assert resp.status_code == 200
        assert f"/flows/{flow.uuid}/ns/_top" in resp.text
        # Run link appears on the namespace sub-page
        resp = tc.get(f"/flows/{flow.uuid}/ns/_top")
        assert resp.status_code == 200
        assert f"/flows/{flow.uuid}/run/{wf.uuid}" in resp.text
        assert "Run</a>" in resp.text

    def test_run_link_hidden_without_compiled_sources(self, client):
        """When compiled_sources is empty, no Run button should appear."""
        from facetwork.runtime.entities import (
            FlowDefinition,
            FlowIdentity,
            WorkflowDefinition,
        )
        from facetwork.runtime.types import generate_id

        tc, store = client
        flow_id = generate_id()
        wf_id = generate_id()

        flow = FlowDefinition(
            uuid=flow_id,
            name=FlowIdentity(name="NoSource", path="test", uuid=flow_id),
            compiled_sources=[],
        )
        store.save_flow(flow)
        wf = WorkflowDefinition(
            uuid=wf_id,
            name="NoSource",
            namespace_id="test",
            facet_id=wf_id,
            flow_id=flow_id,
            starting_step="",
            version="1.0",
            date=0,
        )
        store.save_workflow(wf)

        resp = tc.get(f"/flows/{flow_id}")
        assert resp.status_code == 200
        assert "/run/" not in resp.text


class TestFlowRunForm:
    """Tests for GET /flows/{flow_id}/run/{workflow_id}."""

    def test_form_renders(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/run/{wf.uuid}")
        assert resp.status_code == 200
        assert "Run Workflow" in resp.text
        assert wf.name in resp.text

    def test_form_shows_params(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/run/{wf.uuid}")
        assert resp.status_code == 200
        assert "x" in resp.text
        assert "Long" in resp.text

    def test_form_shows_defaults(self, client):
        tc, store = client
        flow, wf = _seed_flow(store, source=DEFAULTS_AFL_SOURCE, workflow_name="DefaultsWF")
        resp = tc.get(f"/flows/{flow.uuid}/run/{wf.uuid}")
        assert resp.status_code == 200
        assert "42" in resp.text
        assert "hello" in resp.text

    def test_form_has_back_link(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/run/{wf.uuid}")
        assert resp.status_code == 200
        assert f"/flows/{flow.uuid}" in resp.text
        assert "Back to flow" in resp.text

    def test_form_missing_flow(self, client):
        tc, store = client
        resp = tc.get("/flows/nonexistent/run/wf123")
        assert resp.status_code == 200
        assert "Flow not found" in resp.text


class TestFlowRunExecute:
    """Tests for POST /flows/{flow_id}/run/{workflow_id}."""

    def test_creates_runner_and_redirects(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        resp = tc.post(
            f"/flows/{flow.uuid}/run/{wf.uuid}",
            data={"inputs_json": "{}"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/runners/" in resp.headers["location"]

    def test_reuses_existing_flow_and_workflow(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        tc.post(
            f"/flows/{flow.uuid}/run/{wf.uuid}",
            data={"inputs_json": "{}"},
            follow_redirects=False,
        )
        # Should NOT create a new flow or workflow
        flows = store.get_all_flows()
        assert len(flows) == 1
        assert flows[0].uuid == flow.uuid
        workflows = store.get_workflows_by_flow(flow.uuid)
        assert len(workflows) == 1
        assert workflows[0].uuid == wf.uuid

    def test_creates_runner(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        resp = tc.post(
            f"/flows/{flow.uuid}/run/{wf.uuid}",
            data={"inputs_json": "{}"},
            follow_redirects=False,
        )
        location = resp.headers["location"]
        runner_id = location.split("/runners/")[1]
        runner = store.get_runner(runner_id)
        assert runner is not None
        assert runner.state == "created"
        assert runner.workflow_id == wf.uuid

    def test_creates_task(self, client):
        tc, store = client
        flow, wf = _seed_flow(store)
        tc.post(
            f"/flows/{flow.uuid}/run/{wf.uuid}",
            data={"inputs_json": "{}"},
            follow_redirects=False,
        )
        tasks = store.get_pending_tasks("default")
        assert len(tasks) == 1
        assert tasks[0].name == "fw:execute"
        assert tasks[0].data["flow_id"] == flow.uuid
        assert tasks[0].data["workflow_id"] == wf.uuid
        assert tasks[0].data["workflow_name"] == "SimpleWF"

    def test_user_inputs_override_defaults(self, client):
        tc, store = client
        flow, wf = _seed_flow(store, source=DEFAULTS_AFL_SOURCE, workflow_name="DefaultsWF")
        user_inputs = json.dumps({"x": 99, "name": "world"})
        tc.post(
            f"/flows/{flow.uuid}/run/{wf.uuid}",
            data={"inputs_json": user_inputs},
            follow_redirects=False,
        )
        tasks = store.get_pending_tasks("default")
        assert len(tasks) == 1
        assert tasks[0].data["inputs"]["x"] == 99
        assert tasks[0].data["inputs"]["name"] == "world"

    def test_defaults_applied_when_no_user_input(self, client):
        tc, store = client
        flow, wf = _seed_flow(store, source=DEFAULTS_AFL_SOURCE, workflow_name="DefaultsWF")
        tc.post(
            f"/flows/{flow.uuid}/run/{wf.uuid}",
            data={"inputs_json": "{}"},
            follow_redirects=False,
        )
        tasks = store.get_pending_tasks("default")
        assert len(tasks) == 1
        assert tasks[0].data["inputs"]["x"] == 42
        assert tasks[0].data["inputs"]["name"] == "hello"

    def test_runner_has_snapshotted_asts(self, client):
        """Runner created by flow_run_execute has compiled_ast and workflow_ast."""
        tc, store = client
        flow, wf = _seed_flow(store)
        resp = tc.post(
            f"/flows/{flow.uuid}/run/{wf.uuid}",
            data={"inputs_json": "{}"},
            follow_redirects=False,
        )
        location = resp.headers["location"]
        runner_id = location.split("/runners/")[1]
        runner = store.get_runner(runner_id)
        assert runner is not None
        assert runner.compiled_ast is not None
        assert isinstance(runner.compiled_ast, dict)
        assert "declarations" in runner.compiled_ast
        assert runner.workflow_ast is not None
        assert runner.workflow_ast["name"] == "SimpleWF"

    def test_missing_flow_returns_gracefully(self, client):
        tc, store = client
        resp = tc.post(
            "/flows/nonexistent/run/wf123",
            data={"inputs_json": "{}"},
            follow_redirects=False,
        )
        # Should not crash — returns a template response (not a redirect)
        assert resp.status_code == 200
