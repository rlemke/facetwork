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


# =============================================================================
# TestStateColorRendering
# =============================================================================


class TestStateColorRendering:
    def test_running_badge_primary(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        assert "badge-primary" in resp.text

    def test_completed_badge_success(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-2", state="completed"))
        resp = tc.get("/v2/workflows?tab=completed")
        assert resp.status_code == 200
        assert "badge-success" in resp.text

    def test_failed_badge_danger(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-3", state="failed"))
        resp = tc.get("/v2/workflows?tab=failed")
        assert resp.status_code == 200
        assert "badge-danger" in resp.text

    def test_paused_badge_warning(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-4", state="paused"))
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        assert "badge-warning" in resp.text


# =============================================================================
# TestNavigationRendering
# =============================================================================


class TestNavigationRendering:
    def test_nav_links_on_home_page(self, client):
        tc, store = client
        resp = tc.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="/v2/workflows"' in html
        assert 'href="/flows"' in html
        assert 'href="/tasks"' in html
        assert 'href="/v2/servers"' in html
        assert 'href="/output"' in html
        assert 'href="/workflows/new"' in html

    def test_nav_links_on_workflows_page(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert resp.status_code == 200
        html = resp.text
        # Base template nav should be present on every page
        assert 'href="/v2/workflows"' in html
        assert 'href="/flows"' in html

    def test_nav_links_on_tasks_page(self, client):
        tc, store = client
        resp = tc.get("/tasks")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="/events"' in html
        assert 'href="/v2/servers"' in html

    def test_breadcrumb_on_workflow_detail(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        # Workflow detail page should contain a link back to workflows
        assert "workflows" in resp.text.lower()


# =============================================================================
# TestTableRendering
# =============================================================================


class TestTableRendering:
    def test_workflow_list_page_renders(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert resp.status_code == 200
        html = resp.text
        assert "Workflows" in html

    def test_task_list_column_headers(self, client):
        tc, store = client
        resp = tc.get("/tasks")
        assert resp.status_code == 200
        html = resp.text
        assert "<th>ID</th>" in html
        assert "<th>Name</th>" in html
        assert "<th>Step</th>" in html
        assert "<th>State</th>" in html
        assert "<th>Task List</th>" in html
        assert "<th>Duration</th>" in html

    def test_flow_list_column_headers(self, client):
        tc, store = client
        resp = tc.get("/flows")
        assert resp.status_code == 200
        html = resp.text
        assert "<th>Name</th>" in html
        assert "<th>Path</th>" in html

    def test_server_list_column_headers(self, client):
        tc, store = client
        resp = tc.get("/servers")
        assert resp.status_code == 200
        html = resp.text
        assert "<th>Name</th>" in html
        assert "<th>Group</th>" in html
        assert "<th>Service</th>" in html
        assert "<th>State</th>" in html


# =============================================================================
# TestFormRendering
# =============================================================================


class TestFormRendering:
    def test_textarea_on_new_workflow_page(self, client):
        tc, store = client
        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        assert "<textarea" in resp.text
        assert 'name="source"' in resp.text

    def test_validate_button_exists(self, client):
        tc, store = client
        resp = tc.get("/workflows/new")
        assert resp.status_code == 200
        assert "Validate Only" in resp.text
