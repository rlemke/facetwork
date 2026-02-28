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

"""Tests for dashboard v2 helpers and routes."""

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
    categorize_step_state,
    extract_namespace,
    group_runners_by_namespace,
    short_workflow_name,
)


class TestExtractNamespace:
    def test_qualified_name(self):
        assert extract_namespace("osm.geo.Routes.BicycleRoutes") == "osm.geo.Routes"

    def test_simple_name(self):
        assert extract_namespace("SimpleWorkflow") == "(top-level)"

    def test_two_parts(self):
        assert extract_namespace("ns.Workflow") == "ns"

    def test_empty_string(self):
        assert extract_namespace("") == "(top-level)"

    def test_deeply_nested(self):
        assert extract_namespace("a.b.c.d.e.F") == "a.b.c.d.e"


class TestShortWorkflowName:
    def test_qualified_name(self):
        assert short_workflow_name("osm.geo.Routes.BicycleRoutes") == "BicycleRoutes"

    def test_simple_name(self):
        assert short_workflow_name("SimpleWorkflow") == "SimpleWorkflow"

    def test_two_parts(self):
        assert short_workflow_name("ns.Workflow") == "Workflow"


class TestCategorizeStepState:
    def test_complete(self):
        assert categorize_step_state("state.statement.Complete") == "complete"

    def test_error(self):
        assert categorize_step_state("state.statement.Error") == "error"

    def test_created(self):
        assert categorize_step_state("state.statement.Created") == "running"

    def test_event_transmit(self):
        assert categorize_step_state("state.EventTransmit") == "running"

    def test_facet_init_begin(self):
        assert categorize_step_state("state.facet.initialization.Begin") == "running"

    def test_facet_init_end(self):
        assert categorize_step_state("state.facet.initialization.End") == "running"

    def test_block_begin_is_other(self):
        assert categorize_step_state("state.block.execution.Begin") == "other"

    def test_block_continue_is_other(self):
        assert categorize_step_state("state.block.execution.Continue") == "other"

    def test_mixin_blocks_begin_is_other(self):
        assert categorize_step_state("state.mixin.blocks.Begin") == "other"

    def test_statement_capture_is_other(self):
        assert categorize_step_state("state.statement.capture.Begin") == "other"

    def test_statement_end_is_other(self):
        assert categorize_step_state("state.statement.End") == "other"


class TestGroupRunnersByNamespace:
    def _make_runner(self, name, state="running"):
        """Create a minimal runner-like object for grouping tests."""
        from types import SimpleNamespace

        return SimpleNamespace(
            uuid=f"r-{name}",
            workflow=SimpleNamespace(name=name),
            state=state,
        )

    def test_groups_by_namespace(self):
        runners = [
            self._make_runner("osm.geo.BicycleRoutes"),
            self._make_runner("osm.geo.WalkingRoutes"),
            self._make_runner("aws.Lambda"),
        ]
        groups = group_runners_by_namespace(runners)
        assert len(groups) == 2
        assert groups[0]["namespace"] == "aws"
        assert groups[0]["total"] == 1
        assert groups[1]["namespace"] == "osm.geo"
        assert groups[1]["total"] == 2

    def test_top_level(self):
        runners = [self._make_runner("SimpleWorkflow")]
        groups = group_runners_by_namespace(runners)
        assert len(groups) == 1
        assert groups[0]["namespace"] == "(top-level)"

    def test_empty(self):
        groups = group_runners_by_namespace([])
        assert groups == []

    def test_counts_by_state(self):
        runners = [
            self._make_runner("ns.A", "running"),
            self._make_runner("ns.B", "completed"),
            self._make_runner("ns.C", "running"),
        ]
        groups = group_runners_by_namespace(runners)
        assert groups[0]["counts"] == {"running": 2, "completed": 1}


# ---------------------------------------------------------------------------
# Route integration tests (require fastapi + mongomock)
# ---------------------------------------------------------------------------

pytestmark_routes = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE, reason="fastapi or mongomock not installed"
)


def _make_workflow(uuid="wf-1", name="osm.geo.TestWF"):
    from afl.runtime.entities import WorkflowDefinition

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
    from afl.runtime.entities import RunnerDefinition

    if workflow is None:
        workflow = _make_workflow()
    return RunnerDefinition(
        uuid=uuid,
        workflow_id=workflow.uuid,
        workflow=workflow,
        state=state,
    )


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from afl.dashboard import dependencies as deps
    from afl.dashboard.app import create_app
    from afl.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_v2", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


@pytestmark_routes
class TestV2WorkflowList:
    def test_workflow_list_empty(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows", follow_redirects=False)
        assert resp.status_code == 200
        assert "Workflows" in resp.text

    def test_workflow_list_with_runners(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        assert "osm.geo" in resp.text
        assert "TestWF" in resp.text

    def test_workflow_list_completed_tab(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="completed"))
        resp = tc.get("/v2/workflows?tab=completed")
        assert resp.status_code == 200
        assert "TestWF" in resp.text

    def test_workflow_list_failed_tab(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="failed"))
        resp = tc.get("/v2/workflows?tab=failed")
        assert resp.status_code == 200
        assert "TestWF" in resp.text

    def test_workflow_list_running_excludes_completed(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="completed"))
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        assert "No workflows" in resp.text

    def test_workflow_list_partial(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/partial?tab=running")
        assert resp.status_code == 200
        assert "osm.geo" in resp.text


@pytestmark_routes
class TestV2WorkflowDetail:
    def test_detail_not_found(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows/nonexistent")
        assert resp.status_code == 200
        assert "Not Found" in resp.text

    def test_detail_with_runner(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "TestWF" in resp.text

    def test_step_rows_partial(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1/steps/partial?step_tab=running")
        assert resp.status_code == 200

    def test_step_rows_partial_not_found(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows/nonexistent/steps/partial?step_tab=running")
        assert resp.status_code == 200

    def test_step_expand(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1/steps/some-step/expand")
        assert resp.status_code == 200


@pytestmark_routes
class TestHomeRedirect:
    def test_home_redirects_to_v2(self, client):
        tc, store = client
        resp = tc.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/v2/workflows"


@pytestmark_routes
class TestNavStructure:
    def test_nav_has_workflows_link(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert '/v2/workflows"' in resp.text

    def test_nav_has_servers_link(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert '/v2/servers"' in resp.text

    def test_nav_has_flat_tabs(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert "/tasks" in resp.text
        assert "/flows" in resp.text
        assert "/runners" in resp.text
        assert "/output" in resp.text

    def test_old_runners_route_still_works(self, client):
        tc, store = client
        resp = tc.get("/runners")
        assert resp.status_code == 200


@pytestmark_routes
class TestV2WorkflowOtherTab:
    def test_detail_has_other_pill(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "step_tab=other" in resp.text
        assert "Other" in resp.text

    def test_other_tab_returns_200(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1?step_tab=other")
        assert resp.status_code == 200

    def test_other_tab_partial(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1/steps/partial?step_tab=other")
        assert resp.status_code == 200


@pytestmark_routes
class TestV2HandlerDetail:
    def _make_handler(self, facet_name="osm.geo.Cache"):
        from afl.runtime.entities import HandlerRegistration

        return HandlerRegistration(
            facet_name=facet_name,
            module_uri="examples.osm_geocoder.handlers.cache",
            entrypoint="handle",
            version="1.0.0",
        )

    def test_handler_detail_has_small_font_class(self, client):
        tc, store = client
        store.save_handler_registration(self._make_handler())
        resp = tc.get("/v2/handlers/osm.geo.Cache")
        assert resp.status_code == 200
        assert "summary-value-sm" in resp.text

    def test_handler_detail_shows_activity_section(self, client):
        tc, store = client
        store.save_handler_registration(self._make_handler())
        resp = tc.get("/v2/handlers/osm.geo.Cache")
        assert resp.status_code == 200
        assert "Current Activity" in resp.text
        assert "Recent Logs" in resp.text
        assert "No active tasks" in resp.text
        assert "No recent logs" in resp.text

    def test_handler_detail_with_active_task(self, client):
        from afl.runtime.entities import TaskDefinition

        tc, store = client
        store.save_handler_registration(self._make_handler())
        store.save_task(TaskDefinition(
            uuid="task-1",
            name="osm.geo.Cache",
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="step-1",
            state="running",
            created=1000,
        ))
        resp = tc.get("/v2/handlers/osm.geo.Cache")
        assert resp.status_code == 200
        assert "step-1" in resp.text
        assert "No active tasks" not in resp.text

    def test_handler_detail_with_recent_log(self, client):
        from afl.runtime.entities import StepLogEntry

        tc, store = client
        store.save_handler_registration(self._make_handler())
        store.save_step_log(StepLogEntry(
            uuid="log-1",
            step_id="step-1",
            workflow_id="wf-1",
            runner_id="r-1",
            facet_name="osm.geo.Cache",
            message="Task completed",
            time=1000,
        ))
        resp = tc.get("/v2/handlers/osm.geo.Cache")
        assert resp.status_code == 200
        assert "Task completed" in resp.text
        assert "No recent logs" not in resp.text

    def test_handler_partial_includes_activity(self, client):
        tc, store = client
        store.save_handler_registration(self._make_handler())
        resp = tc.get("/v2/handlers/osm.geo.Cache/partial")
        assert resp.status_code == 200
        assert "Current Activity" in resp.text


# ---------------------------------------------------------------------------
# Sidebar navigation tests
# ---------------------------------------------------------------------------


@pytestmark_routes
class TestSidebarNav:
    def test_sidebar_present(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert resp.status_code == 200
        assert 'class="sidebar"' in resp.text
        assert "sidebar-brand" in resp.text

    def test_sidebar_has_workflows_link(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert '/v2/workflows" class="sidebar-link active"' in resp.text

    def test_sidebar_has_flows_link(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert '/flows"' in resp.text

    def test_sidebar_has_servers_link(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert '/v2/servers"' in resp.text

    def test_sidebar_has_handlers_link(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert '/v2/handlers"' in resp.text

    def test_sidebar_has_new_workflow_link(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert '/workflows/new"' in resp.text

    def test_sidebar_has_search_trigger(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert "sidebar-search" in resp.text


# ---------------------------------------------------------------------------
# Command palette / search API tests
# ---------------------------------------------------------------------------


@pytestmark_routes
class TestGlobalSearch:
    def test_search_empty_query(self, client):
        tc, store = client
        resp = tc.get("/v2/search?q=")
        assert resp.status_code == 200
        assert "Type to search" in resp.text

    def test_search_no_results(self, client):
        tc, store = client
        resp = tc.get("/v2/search?q=nonexistent_xyz")
        assert resp.status_code == 200
        assert "No results" in resp.text

    def test_search_finds_workflow(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/search?q=TestWF")
        assert resp.status_code == 200
        assert "TestWF" in resp.text
        assert "/v2/workflows/r-1" in resp.text

    def test_search_finds_handler(self, client):
        from afl.runtime.entities import HandlerRegistration

        tc, store = client
        store.save_handler_registration(HandlerRegistration(
            facet_name="osm.geo.Cache",
            module_uri="examples.handlers.cache",
            entrypoint="handle",
            version="1.0",
        ))
        resp = tc.get("/v2/search?q=Cache")
        assert resp.status_code == 200
        assert "osm.geo.Cache" in resp.text

    def test_search_case_insensitive(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/search?q=testwf")
        assert resp.status_code == 200
        assert "TestWF" in resp.text


# ---------------------------------------------------------------------------
# Command palette template tests
# ---------------------------------------------------------------------------


@pytestmark_routes
class TestCommandPalette:
    def test_command_palette_in_page(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert resp.status_code == 200
        assert "cmd-palette" in resp.text
        assert "cmd-palette-input" in resp.text

    def test_command_palette_has_esc_key(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert "Esc" in resp.text


# ---------------------------------------------------------------------------
# Progress enrichment tests
# ---------------------------------------------------------------------------


from afl.dashboard.helpers import compute_step_progress


class TestComputeStepProgress:
    def _make_step(self, state="state.statement.Complete"):
        from types import SimpleNamespace

        return SimpleNamespace(state=state)

    def test_all_complete(self):
        steps = [self._make_step("state.statement.Complete") for _ in range(5)]
        result = compute_step_progress(None, steps)
        assert result["completed"] == 5
        assert result["total"] == 5
        assert result["pct"] == 100

    def test_none_complete(self):
        steps = [self._make_step("state.statement.Created") for _ in range(3)]
        result = compute_step_progress(None, steps)
        assert result["completed"] == 0
        assert result["total"] == 3
        assert result["pct"] == 0

    def test_partial_complete(self):
        steps = [
            self._make_step("state.statement.Complete"),
            self._make_step("state.statement.Created"),
            self._make_step("state.statement.Error"),
            self._make_step("state.statement.Complete"),
        ]
        result = compute_step_progress(None, steps)
        assert result["completed"] == 2
        assert result["total"] == 4
        assert result["pct"] == 50

    def test_empty_steps(self):
        result = compute_step_progress(None, [])
        assert result["completed"] == 0
        assert result["total"] == 0
        assert result["pct"] == 0


# ---------------------------------------------------------------------------
# Workflow list redesign tests
# ---------------------------------------------------------------------------


@pytestmark_routes
class TestWorkflowListRedesign:
    def test_list_has_search_input(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert resp.status_code == 200
        assert 'data-list-filter=' in resp.text

    def test_list_has_auto_refresh_on_running(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        assert 'hx-trigger="every 5s"' in resp.text

    def test_list_no_auto_refresh_on_completed(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows?tab=completed")
        assert resp.status_code == 200
        assert 'hx-trigger="every 5s"' not in resp.text

    def test_list_has_progress_column(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        assert "Progress" in resp.text

    def test_list_has_breadcrumb(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows")
        assert resp.status_code == 200
        assert 'class="breadcrumb"' in resp.text

    def test_empty_state_shown(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        assert "empty-state" in resp.text

    def test_accordions_collapsed_by_default(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows?tab=running")
        assert resp.status_code == 200
        # Accordions should NOT have 'open' attribute by default
        assert '<details class="ns-group" open>' not in resp.text
        assert '<details class="ns-group">' in resp.text


# ---------------------------------------------------------------------------
# Step tree controls tests
# ---------------------------------------------------------------------------


@pytestmark_routes
class TestStepTreeControls:
    def test_detail_has_expand_collapse_buttons(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "step-tree-expand-all" in resp.text
        assert "step-tree-collapse-all" in resp.text

    def test_detail_has_tree_search(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "step-tree-search" in resp.text

    def test_detail_has_summary_bar_with_steps(self, client):
        from afl.runtime.step import StepDefinition

        tc, store = client
        runner = _make_runner("r-1", state="running")
        store.save_runner(runner)
        store.save_step(StepDefinition(
            id="step-1",
            object_type="step",
            workflow_id=runner.workflow_id,
            state="state.statement.Complete",
        ))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "step-summary-legend" in resp.text
        assert "step-summary-bar" in resp.text

    def test_detail_auto_refresh_for_running(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert 'hx-trigger="every 5s"' in resp.text

    def test_detail_no_auto_refresh_for_completed(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="completed"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert 'hx-trigger="every 5s"' not in resp.text

    def test_detail_has_breadcrumb(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert 'class="breadcrumb"' in resp.text
        assert "Workflows" in resp.text
