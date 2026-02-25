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

"""Integration tests for dashboard workflow routes (new, compile, run)."""

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
    from afl.dashboard import dependencies as deps
    from afl.dashboard.app import create_app
    from afl.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_workflows", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


VALID_AFL_SOURCE = """
facet Compute(input: Long)

workflow SimpleWF(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield SimpleWF(result = s1.input)
}
"""

MULTI_WORKFLOW_SOURCE = """
facet Compute(input: Long)

workflow WF_A(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield WF_A(result = s1.input)
}

workflow WF_B(y: String) => (out: String) andThen {
    s1 = Compute(input = 1)
    yield WF_B(out = "done")
}
"""

INVALID_AFL_SOURCE = "this is not valid AFL %%% syntax"


def _make_flow(uuid, name, compiled_ast):
    """Helper to create a FlowDefinition with compiled_ast."""
    from afl.runtime.entities import FlowDefinition, FlowIdentity

    return FlowDefinition(
        uuid=uuid,
        name=FlowIdentity(name=name, path="test", uuid=uuid),
        compiled_ast=compiled_ast,
    )


def _seed_workflows(store, flow_id, compiled_ast):
    """Seed WorkflowDefinition records matching the compiled_ast workflows."""
    from afl.dashboard.routes.workflows import _collect_workflows_with_ns
    from afl.runtime.entities import WorkflowDefinition

    entries: list[dict] = []
    _collect_workflows_with_ns(compiled_ast, "", entries)
    for entry in entries:
        ns = entry["ns"]
        wf = entry["wf"]
        qualified = f"{ns}.{wf['name']}" if ns else wf["name"]
        store.save_workflow(
            WorkflowDefinition(
                uuid=f"wf-{qualified}",
                name=qualified,
                namespace_id=ns or "top",
                facet_id=f"facet-{qualified}",
                flow_id=flow_id,
                starting_step="",
                version="1.0",
            )
        )


# Sample compiled_ast with namespaced workflows
NAMESPACED_AST = {
    "declarations": [
        {
            "type": "Namespace",
            "name": "geo.Routes",
            "declarations": [
                {
                    "type": "WorkflowDecl",
                    "name": "BicycleRoutes",
                    "params": [
                        {"name": "region", "type": "String", "default": {"value": "Alaska"}},
                    ],
                    "returns": [{"name": "result", "type": "String"}],
                },
                {
                    "type": "WorkflowDecl",
                    "name": "HikingTrails",
                    "params": [
                        {"name": "state", "type": "String"},
                    ],
                    "returns": [],
                },
            ],
        },
        {
            "type": "Namespace",
            "name": "geo.Boundaries",
            "declarations": [
                {
                    "type": "WorkflowDecl",
                    "name": "StateBoundaries",
                    "params": [
                        {"name": "count", "type": "Long", "default": {"value": 5}},
                    ],
                    "returns": [{"name": "output", "type": "String"}],
                },
            ],
        },
    ],
}

# Sample compiled_ast with a top-level workflow (no namespace)
TOPLEVEL_AST = {
    "declarations": [
        {
            "type": "WorkflowDecl",
            "name": "SimpleWF",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "result", "type": "Long"}],
        },
    ],
}


class TestWorkflowNew:
    """Tests for GET /workflows/new."""

    def test_new_page_renders(self, client):
        tc, store = client
        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        assert "New Workflow" in resp.text
        assert "<textarea" in resp.text

    def test_new_page_has_compile_form(self, client):
        tc, store = client
        resp = tc.get("/workflows/new")
        assert "/workflows/compile" in resp.text

    def test_new_page_empty_db(self, client):
        """Page renders fine with no flows in the database."""
        tc, store = client
        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        assert "New Workflow" in resp.text
        # No accordion sections when DB is empty
        assert "<details" not in resp.text

    def test_new_page_shows_workflows_from_db(self, client):
        """Workflows from seeded flows appear in the browser."""
        tc, store = client
        flow = _make_flow("flow-1", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)
        # Seed workflow records so run URLs can be resolved
        _seed_workflows(store, "flow-1", NAMESPACED_AST)

        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        assert "BicycleRoutes" in resp.text
        assert "HikingTrails" in resp.text
        assert "StateBoundaries" in resp.text

    def test_new_page_groups_by_namespace(self, client):
        """Workflows are grouped under namespace <details> sections."""
        tc, store = client
        flow = _make_flow("flow-2", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)

        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        # Both namespace groups appear as <details> accordions
        assert "geo.Routes" in resp.text
        assert "geo.Boundaries" in resp.text
        assert resp.text.count("<details") == 2

    def test_new_page_toplevel_workflows(self, client):
        """Top-level workflows (no namespace) appear under (top-level) group."""
        tc, store = client
        flow = _make_flow("flow-3", "TestFlow", TOPLEVEL_AST)
        store.save_flow(flow)

        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        assert "(top-level)" in resp.text
        assert "SimpleWF" in resp.text

    def test_new_page_afl_snippet_in_data_source(self, client):
        """Each workflow link has a data-source attribute with AFL snippet."""
        tc, store = client
        flow = _make_flow("flow-4", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)

        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        # The AFL snippet should contain a namespace wrapper and workflow keyword
        assert "data-source=" in resp.text
        assert "namespace geo.Routes" in resp.text
        # Jinja2 |e escapes " as &#34; or &quot;
        assert "region: String = " in resp.text
        assert "Alaska" in resp.text

    def test_new_page_has_run_links(self, client):
        """Workflows with DB records get run links to /flows/.../run/..."""
        tc, store = client
        flow = _make_flow("flow-run", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)
        _seed_workflows(store, "flow-run", NAMESPACED_AST)

        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        # Each workflow should have a run link pointing to the flow run page
        assert "/flows/flow-run/run/wf-geo.Routes.BicycleRoutes" in resp.text
        assert "/flows/flow-run/run/wf-geo.Routes.HikingTrails" in resp.text
        assert "/flows/flow-run/run/wf-geo.Boundaries.StateBoundaries" in resp.text
        # Links should have the wf-run CSS class
        assert 'class="wf-run"' in resp.text

    def test_new_page_no_run_links_without_workflow_records(self, client):
        """Workflows without DB records don't get run links."""
        tc, store = client
        flow = _make_flow("flow-nowr", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)
        # Don't seed workflow records

        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        # Workflow names still appear
        assert "BicycleRoutes" in resp.text
        # But no run links
        assert "wf-run" not in resp.text
        assert "/flows/flow-nowr/run/" not in resp.text

    def test_new_page_flow_without_compiled_ast(self, client):
        """Flows without compiled_ast are silently skipped."""
        tc, store = client
        flow = _make_flow("flow-5", "NoAST", None)
        store.save_flow(flow)

        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        # No accordion sections
        assert "<details" not in resp.text


class TestWorkflowCompile:
    """Tests for POST /workflows/compile."""

    def test_compile_valid_source(self, client):
        tc, store = client
        resp = tc.post(
            "/workflows/compile",
            data={"source": VALID_AFL_SOURCE},
        )
        assert resp.status_code == 200
        assert "SimpleWF" in resp.text
        assert "Run Workflow" in resp.text

    def test_compile_shows_params(self, client):
        tc, store = client
        resp = tc.post(
            "/workflows/compile",
            data={"source": VALID_AFL_SOURCE},
        )
        assert resp.status_code == 200
        assert "x" in resp.text
        assert "Long" in resp.text

    def test_compile_invalid_source(self, client):
        tc, store = client
        resp = tc.post(
            "/workflows/compile",
            data={"source": INVALID_AFL_SOURCE},
        )
        assert resp.status_code == 200
        # Should show error
        assert "Errors" in resp.text or "error" in resp.text.lower()

    def test_compile_multiple_workflows(self, client):
        tc, store = client
        resp = tc.post(
            "/workflows/compile",
            data={"source": MULTI_WORKFLOW_SOURCE},
        )
        assert resp.status_code == 200
        assert "WF_A" in resp.text
        assert "WF_B" in resp.text

    def test_compile_no_workflows(self, client):
        tc, store = client
        resp = tc.post(
            "/workflows/compile",
            data={"source": "facet Standalone(x: Long)"},
        )
        assert resp.status_code == 200
        assert "No workflows found" in resp.text


class TestWorkflowRun:
    """Tests for POST /workflows/run."""

    def test_run_creates_entities_and_redirects(self, client):
        tc, store = client
        resp = tc.post(
            "/workflows/run",
            data={
                "source": VALID_AFL_SOURCE,
                "workflow_name": "SimpleWF",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/runners/" in resp.headers["location"]

    def test_run_creates_flow(self, client):
        tc, store = client
        tc.post(
            "/workflows/run",
            data={
                "source": VALID_AFL_SOURCE,
                "workflow_name": "SimpleWF",
            },
            follow_redirects=False,
        )
        flows = store.get_all_flows()
        assert len(flows) == 1
        assert flows[0].name.name == "SimpleWF"
        assert len(flows[0].compiled_sources) == 1
        assert flows[0].compiled_sources[0].name == "source.afl"

    def test_run_creates_workflow(self, client):
        tc, store = client
        tc.post(
            "/workflows/run",
            data={
                "source": VALID_AFL_SOURCE,
                "workflow_name": "SimpleWF",
            },
            follow_redirects=False,
        )
        flows = store.get_all_flows()
        flow = flows[0]
        workflows = store.get_workflows_by_flow(flow.uuid)
        assert len(workflows) == 1
        assert workflows[0].name == "SimpleWF"

    def test_run_creates_runner(self, client):
        tc, store = client
        resp = tc.post(
            "/workflows/run",
            data={
                "source": VALID_AFL_SOURCE,
                "workflow_name": "SimpleWF",
            },
            follow_redirects=False,
        )
        # Extract runner_id from redirect URL
        location = resp.headers["location"]
        runner_id = location.split("/runners/")[1]
        runner = store.get_runner(runner_id)
        assert runner is not None
        assert runner.state == "created"

    def test_run_creates_task(self, client):
        tc, store = client
        tc.post(
            "/workflows/run",
            data={
                "source": VALID_AFL_SOURCE,
                "workflow_name": "SimpleWF",
            },
            follow_redirects=False,
        )
        tasks = store.get_pending_tasks("default")
        assert len(tasks) == 1
        assert tasks[0].name == "afl:execute"
        assert tasks[0].data["workflow_name"] == "SimpleWF"

    def test_run_runner_has_snapshotted_asts(self, client):
        """Runner created by workflow_run has compiled_ast and workflow_ast."""
        tc, store = client
        resp = tc.post(
            "/workflows/run",
            data={
                "source": VALID_AFL_SOURCE,
                "workflow_name": "SimpleWF",
            },
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


class TestNavLink:
    """Tests for the navigation link."""

    def test_nav_has_new_link(self, client):
        tc, store = client
        resp = tc.get("/")
        assert resp.status_code == 200
        assert "/workflows/new" in resp.text
        assert ">New Workflow<" in resp.text
