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

"""Template rendering tests — verify HTML structure, CSS classes, and navigation."""

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
    store = MongoStore(database_name="afl_test_templates", client=mock_client)

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


def _make_task(uuid="task-1", name="SendEmail", state="pending"):
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
    )


# =============================================================================
# TestStateColorRendering
# =============================================================================


class TestStateColorRendering:
    def test_running_badge_primary(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v3/workflows?tab=running")
        assert resp.status_code == 200
        assert "--pc:var(--st-running)" in resp.text
        assert "Running" in resp.text

    def test_completed_badge_success(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-2", state="completed"))
        resp = tc.get("/v3/workflows?tab=completed")
        assert resp.status_code == 200
        assert "--pc:var(--st-complete)" in resp.text
        assert "Completed" in resp.text

    def test_failed_badge_danger(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-3", state="failed"))
        resp = tc.get("/v3/workflows?tab=failed")
        assert resp.status_code == 200
        assert "--pc:var(--st-error)" in resp.text
        assert "Failed" in resp.text

    def test_paused_badge_warning(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-4", state="paused"))
        resp = tc.get("/v3/workflows?tab=running")
        assert resp.status_code == 200
        assert "--pc:var(--st-paused)" in resp.text
        assert "Paused" in resp.text


# =============================================================================
# TestNavigationRendering
# =============================================================================


class TestNavigationRendering:
    def test_nav_links_on_home_page(self, client):
        tc, store = client
        # "/" 302-redirects to the v3 Runs list, which the TestClient follows.
        resp = tc.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="/v3/workflows"' in html
        assert 'href="/v3/flows"' in html
        assert 'href="/v3/tasks"' in html
        assert 'href="/v3/servers"' in html
        assert 'href="/v3/output"' in html
        assert 'href="/v3/workflows/new"' in html

    def test_nav_links_on_workflows_page(self, client):
        tc, store = client
        resp = tc.get("/v3/workflows")
        assert resp.status_code == 200
        html = resp.text
        # Base template nav should be present on every page
        assert 'href="/v3/workflows"' in html
        assert 'href="/v3/flows"' in html

    def test_nav_links_on_tasks_page(self, client):
        tc, store = client
        resp = tc.get("/v3/tasks")
        assert resp.status_code == 200
        html = resp.text
        # Base nav (present on every v3 page) links to Events and Servers.
        assert 'href="/v3/events"' in html
        assert 'href="/v3/servers"' in html

    def test_breadcrumb_on_workflow_detail(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1"))
        resp = tc.get("/v3/workflows/r-1")
        assert resp.status_code == 200
        # Workflow detail page breadcrumb links back to the runs list.
        assert 'href="/v3/workflows"' in resp.text


# =============================================================================
# TestTableRendering
# =============================================================================


class TestTableRendering:
    def test_workflow_list_page_renders(self, client):
        tc, store = client
        resp = tc.get("/v3/workflows")
        assert resp.status_code == 200
        html = resp.text
        # v3 Runs list: breadcrumb/title say "Runs" and the new-run CTA is present.
        assert "Runs" in html
        assert 'href="/v3/workflows/new"' in html

    def test_task_list_column_headers(self, client):
        tc, store = client
        # A task is needed for the v3 list to render its (grid) header row.
        store.save_task(_make_task("t-1", state="pending"))
        resp = tc.get("/v3/tasks")
        assert resp.status_code == 200
        html = resp.text
        # v3 uses a CSS-grid table with <span> column headers, not <th>.
        assert "<span>Task</span>" in html
        assert "<span>Step</span>" in html
        assert "<span>Task list</span>" in html
        assert "<span>Server</span>" in html
        assert "<span>Updated</span>" in html
        assert "<span>Retry</span>" in html

    def test_flow_list_column_headers(self, client):
        tc, store = client
        from facetwork.runtime.entities import FlowDefinition, FlowIdentity

        store.save_flow(
            FlowDefinition(
                uuid="flow-1",
                name=FlowIdentity(name="HeaderFlow", path="/h", uuid="flow-1"),
            )
        )
        resp = tc.get("/v3/flows")
        assert resp.status_code == 200
        html = resp.text
        # v3 Library list column headers (.col-h spans).
        assert ">Flow<" in html
        assert ">Workflows<" in html
        assert ">Facets<" in html
        assert ">Namespaces<" in html

    def test_server_list_column_headers(self, client):
        tc, store = client
        import time

        from facetwork.runtime.entities import ServerDefinition, ServerState

        store.save_server(
            ServerDefinition(
                uuid="s-1",
                server_group="workers",
                service_name="facetwork",
                server_name="worker-01",
                state=ServerState.RUNNING,
                ping_time=int(time.time() * 1000),  # fresh → stays "running"
            )
        )
        resp = tc.get("/v3/servers")
        assert resp.status_code == 200
        html = resp.text
        # v3 Servers list column headers (.col-h spans), grouped by host.
        assert ">Runner<" in html
        assert ">Group<" in html
        assert ">Handlers<" in html
        assert ">Active tasks<" in html
        assert ">Last ping<" in html


