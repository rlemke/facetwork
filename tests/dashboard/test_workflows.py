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
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_workflows", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _make_flow(uuid, name, compiled_ast):
    """Helper to create a FlowDefinition with compiled_ast."""
    from facetwork.runtime.entities import FlowDefinition, FlowIdentity

    return FlowDefinition(
        uuid=uuid,
        name=FlowIdentity(name=name, path="test", uuid=uuid),
        compiled_ast=compiled_ast,
    )


def _seed_workflows(store, flow_id, compiled_ast):
    """Seed WorkflowDefinition records matching the compiled_ast workflows."""
    from facetwork.dashboard.routes.execution.workflows import _collect_workflows_with_ns
    from facetwork.runtime.entities import WorkflowDefinition

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
    """Tests for GET /v3/workflows/new (the new-run workflow browser)."""

    def test_new_page_renders(self, client):
        tc, store = client
        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        assert "New run" in resp.text
        # The browser has a search box to find a workflow to run.
        assert 'id="n-search"' in resp.text

    def test_new_page_empty_db(self, client):
        """Page renders fine with no flows in the database."""
        tc, store = client
        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        assert "New run" in resp.text
        # Empty-state message, no namespace groups.
        assert "No workflows available" in resp.text
        assert 'class="ns-head"' not in resp.text

    def test_new_page_shows_workflows_from_db(self, client):
        """Workflows from seeded flows appear in the browser."""
        tc, store = client
        flow = _make_flow("flow-1", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)
        # Seed workflow records so run URLs can be resolved
        _seed_workflows(store, "flow-1", NAMESPACED_AST)

        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        assert "BicycleRoutes" in resp.text
        assert "HikingTrails" in resp.text
        assert "StateBoundaries" in resp.text

    def test_new_page_groups_by_namespace(self, client):
        """Workflows are grouped under per-namespace sections."""
        tc, store = client
        flow = _make_flow("flow-2", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)

        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        # Both namespace groups appear as namespace sections.
        assert "geo.Routes" in resp.text
        assert "geo.Boundaries" in resp.text
        assert resp.text.count('class="ns-head"') == 2

    def test_new_page_toplevel_workflows(self, client):
        """Top-level workflows (no namespace) appear under the unnamespaced group."""
        tc, store = client
        flow = _make_flow("flow-3", "TestFlow", TOPLEVEL_AST)
        store.save_flow(flow)

        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        assert "system.unnamespaced" in resp.text
        assert "SimpleWF" in resp.text

    def test_new_page_afl_snippet_rendered(self, client):
        """Each workflow row renders its FFL snippet in a <pre> block."""
        tc, store = client
        flow = _make_flow("flow-4", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)

        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        # The FFL snippet should contain a namespace wrapper and workflow keyword
        assert "namespace geo.Routes" in resp.text
        assert "region: String = " in resp.text
        assert "Alaska" in resp.text

    def test_new_page_has_run_links(self, client):
        """Workflows with DB records get run links to /v3/flows/.../run/..."""
        tc, store = client
        flow = _make_flow("flow-run", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)
        _seed_workflows(store, "flow-run", NAMESPACED_AST)

        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        # Each workflow should have a run link pointing to the v3 flow run page
        assert "/v3/flows/flow-run/run/wf-geo.Routes.BicycleRoutes" in resp.text
        assert "/v3/flows/flow-run/run/wf-geo.Routes.HikingTrails" in resp.text
        assert "/v3/flows/flow-run/run/wf-geo.Boundaries.StateBoundaries" in resp.text

    def test_new_page_no_run_links_without_workflow_records(self, client):
        """Workflows without DB records don't get run links."""
        tc, store = client
        flow = _make_flow("flow-nowr", "TestFlow", NAMESPACED_AST)
        store.save_flow(flow)
        # Don't seed workflow records

        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        # Workflow names still appear
        assert "BicycleRoutes" in resp.text
        # But no run links
        assert "/v3/flows/flow-nowr/run/" not in resp.text

    def test_new_page_flow_without_compiled_ast(self, client):
        """Flows without compiled_ast are silently skipped."""
        tc, store = client
        flow = _make_flow("flow-5", "NoAST", None)
        store.save_flow(flow)

        resp = tc.get("/v3/workflows/new")
        assert resp.status_code == 200
        # No namespace groups; empty state shown.
        assert 'class="ns-head"' not in resp.text
        assert "No workflows available" in resp.text


class TestNavLink:
    """Tests for the navigation link."""

    def test_nav_has_new_link(self, client):
        tc, store = client
        # "/" redirects to the v3 Runs list, whose sidebar has a "New run" CTA.
        resp = tc.get("/")
        assert resp.status_code == 200
        assert 'href="/v3/workflows/new"' in resp.text
        assert "New run" in resp.text
