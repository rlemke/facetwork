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
        store.save_task(
            TaskDefinition(
                uuid="task-1",
                name="osm.geo.Cache",
                runner_id="r-1",
                workflow_id="wf-1",
                flow_id="flow-1",
                step_id="step-1",
                state="running",
                created=1000,
            )
        )
        resp = tc.get("/v2/handlers/osm.geo.Cache")
        assert resp.status_code == 200
        assert "step-1" in resp.text
        assert "No active tasks" not in resp.text

    def test_handler_detail_with_recent_log(self, client):
        from afl.runtime.entities import StepLogEntry

        tc, store = client
        store.save_handler_registration(self._make_handler())
        store.save_step_log(
            StepLogEntry(
                uuid="log-1",
                step_id="step-1",
                workflow_id="wf-1",
                runner_id="r-1",
                facet_name="osm.geo.Cache",
                message="Task completed",
                time=1000,
            )
        )
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
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="osm.geo.Cache",
                module_uri="examples.handlers.cache",
                entrypoint="handle",
                version="1.0",
            )
        )
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
        assert "data-list-filter=" in resp.text

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
        store.save_step(
            StepDefinition(
                id="step-1",
                object_type="step",
                workflow_id=runner.workflow_id,
                state="state.statement.Complete",
            )
        )
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


# ---------------------------------------------------------------------------
# Auto-refresh partial tests
# ---------------------------------------------------------------------------


@pytestmark_routes
class TestAutoRefreshPartials:
    def test_workflow_summary_partial_returns_200(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1/summary/partial")
        assert resp.status_code == 200
        assert "Runner ID" in resp.text

    def test_workflow_summary_partial_not_found(self, client):
        tc, store = client
        resp = tc.get("/v2/workflows/nonexistent/summary/partial")
        assert resp.status_code == 200
        assert resp.text == ""

    def test_workflow_summary_auto_refresh_for_running(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "workflow-summary" in resp.text
        assert "/summary/partial" in resp.text
        assert 'hx-trigger="every 5s"' in resp.text

    def test_workflow_summary_no_auto_refresh_for_completed(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="completed"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "/summary/partial" not in resp.text

    def test_step_detail_partial_returns_200(self, client):
        from afl.runtime.step import StepDefinition

        tc, store = client
        runner = _make_runner("r-1", state="running")
        store.save_runner(runner)
        store.save_step(
            StepDefinition(
                id="step-1",
                object_type="step",
                workflow_id=runner.workflow_id,
                state="state.statement.Created",
            )
        )
        resp = tc.get("/steps/step-1/partial")
        assert resp.status_code == 200
        assert "step-1" in resp.text

    def test_task_list_partial_returns_200(self, client):
        tc, store = client
        resp = tc.get("/tasks/partial")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# SSE step log streaming tests
# ---------------------------------------------------------------------------


from afl.runtime.memory_store import MemoryStore


class TestGetStepLogsSince:
    def test_memory_store_step_logs_since(self):
        from afl.runtime.entities import StepLogEntry

        store = MemoryStore()
        store.save_step_log(
            StepLogEntry(uuid="l1", step_id="s1", workflow_id="w1", time=100, message="a")
        )
        store.save_step_log(
            StepLogEntry(uuid="l2", step_id="s1", workflow_id="w1", time=200, message="b")
        )
        store.save_step_log(
            StepLogEntry(uuid="l3", step_id="s1", workflow_id="w1", time=300, message="c")
        )

        logs = store.get_step_logs_since("s1", 150)
        assert len(logs) == 2
        assert logs[0].message == "b"
        assert logs[1].message == "c"

    def test_memory_store_step_logs_since_empty(self):
        store = MemoryStore()
        logs = store.get_step_logs_since("s1", 0)
        assert len(logs) == 0

    def test_memory_store_workflow_logs_since(self):
        from afl.runtime.entities import StepLogEntry

        store = MemoryStore()
        store.save_step_log(
            StepLogEntry(uuid="l1", step_id="s1", workflow_id="w1", time=100, message="a")
        )
        store.save_step_log(
            StepLogEntry(uuid="l2", step_id="s2", workflow_id="w1", time=200, message="b")
        )

        logs = store.get_workflow_logs_since("w1", 150)
        assert len(logs) == 1
        assert logs[0].message == "b"


@pytestmark_routes
class TestSSEEndpoints:
    def test_workflow_log_stream_not_found(self, client):
        tc, store = client
        resp = tc.get("/api/runners/nonexistent/logs/stream")
        assert resp.status_code == 404

    def test_step_detail_has_live_button(self, client):
        from afl.runtime.step import StepDefinition

        tc, store = client
        runner = _make_runner("r-1", state="running")
        store.save_runner(runner)
        store.save_step(
            StepDefinition(
                id="step-1",
                object_type="step",
                workflow_id=runner.workflow_id,
                state="state.statement.Created",
            )
        )
        resp = tc.get("/steps/step-1")
        assert resp.status_code == 200
        assert "data-log-stream" in resp.text
        assert "/logs/stream" in resp.text

    def test_workflow_detail_has_live_button(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "data-log-stream" in resp.text
        assert "/logs/stream" in resp.text


# ---------------------------------------------------------------------------
# Timeline tests
# ---------------------------------------------------------------------------


from afl.dashboard.helpers import TimelineEntry, compute_timeline


class TestComputeTimeline:
    def _make_step(self, step_id, state, start_time=0, last_modified=0, facet_name=""):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=step_id,
            state=state,
            start_time=start_time,
            last_modified=last_modified,
            statement_name="",
            facet_name=facet_name,
        )

    def test_empty_steps(self):
        assert compute_timeline([]) == []

    def test_steps_without_timestamps(self):
        steps = [self._make_step("s1", "state.statement.Complete")]
        assert compute_timeline(steps) == []

    def test_single_step(self):
        steps = [self._make_step("s1", "state.statement.Complete", 1000, 2000, "F")]
        result = compute_timeline(steps)
        assert len(result) == 1
        assert result[0].step_id == "s1"
        assert result[0].offset_pct == 0.0
        assert result[0].width_pct == 100.0

    def test_multiple_steps(self):
        steps = [
            self._make_step("s1", "state.statement.Complete", 1000, 2000, "A"),
            self._make_step("s2", "state.statement.Complete", 1500, 3000, "B"),
        ]
        result = compute_timeline(steps, workflow_start=1000)
        assert len(result) == 2
        # s1 starts at 0%, s2 starts at 25%
        assert result[0].step_id == "s1"
        assert result[0].offset_pct == 0.0
        assert result[1].step_id == "s2"
        assert result[1].offset_pct == 25.0

    def test_zero_duration_step(self):
        steps = [self._make_step("s1", "state.statement.Complete", 1000, 1000, "F")]
        result = compute_timeline(steps)
        assert len(result) == 1
        # Should have minimum width
        assert result[0].width_pct >= 0.5

    def test_timeline_entry_fields(self):
        entry = TimelineEntry(
            step_id="s1",
            label="F",
            state="complete",
            start_ms=1000,
            end_ms=2000,
            offset_pct=0.0,
            width_pct=100.0,
        )
        assert entry.step_id == "s1"
        assert entry.label == "F"


@pytestmark_routes
class TestTimelineRoute:
    def test_workflow_detail_has_timeline_view(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "v2-step-timeline" in resp.text
        assert 'data-view="timeline"' in resp.text


# ---------------------------------------------------------------------------
# DAG visualization tests
# ---------------------------------------------------------------------------

from afl.dashboard.graph import compute_dag_layout


class TestComputeDagLayout:
    def _make_step(
        self,
        sid,
        state="state.statement.Created",
        facet="F",
        container_id=None,
        block_id=None,
        root_id=None,
        is_block=False,
        statement_name=None,
    ):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=sid,
            state=state,
            facet_name=facet,
            statement_name=statement_name or sid,
            container_id=container_id,
            block_id=block_id,
            root_id=root_id,
            is_block=is_block,
        )

    def test_empty_steps(self):
        assert compute_dag_layout([]) is None

    def test_single_step(self):
        steps = [self._make_step("s1")]
        dag = compute_dag_layout(steps)
        assert dag is not None
        assert len(dag.nodes) == 1
        assert len(dag.edges) == 0
        assert dag.nodes[0].step_id == "s1"
        assert dag.nodes[0].label == "s1"
        assert dag.width > 0
        assert dag.height > 0

    def test_parent_child_edges(self):
        steps = [
            self._make_step("root", statement_name="Root"),
            self._make_step("child", container_id="root", root_id="root", statement_name="Child"),
        ]
        dag = compute_dag_layout(steps)
        assert dag is not None
        assert len(dag.nodes) == 2
        assert len(dag.edges) == 1
        edge = dag.edges[0]
        assert edge.source_id == "root"
        assert edge.target_id == "child"

    def test_three_step_hierarchy(self):
        steps = [
            self._make_step("r", statement_name="Root"),
            self._make_step(
                "b1", container_id="r", root_id="r", is_block=True, statement_name="B1"
            ),
            self._make_step("s1", block_id="b1", root_id="r", statement_name="S1"),
        ]
        dag = compute_dag_layout(steps)
        assert dag is not None
        assert len(dag.nodes) == 3
        assert len(dag.edges) == 2
        # Root -> B1 -> S1
        edge_targets = {(e.source_id, e.target_id) for e in dag.edges}
        assert ("r", "b1") in edge_targets
        assert ("b1", "s1") in edge_targets

    def test_layer_assignment(self):
        steps = [
            self._make_step("r"),
            self._make_step("c1", container_id="r", root_id="r"),
            self._make_step("c2", container_id="r", root_id="r"),
        ]
        dag = compute_dag_layout(steps)
        assert dag is not None
        # Root at layer 0 (x=20), children at layer 1 (x > 20)
        root_node = [n for n in dag.nodes if n.step_id == "r"][0]
        child_nodes = [n for n in dag.nodes if n.step_id != "r"]
        for cn in child_nodes:
            assert cn.x > root_node.x

    def test_many_steps(self):
        steps = [self._make_step(f"s{i}") for i in range(10)]
        dag = compute_dag_layout(steps)
        assert dag is not None
        assert len(dag.nodes) == 10
        assert dag.height > 0

    def test_long_label_truncated(self):
        steps = [
            self._make_step("s1", statement_name="AVeryLongStatementNameThatShouldBeTruncated")
        ]
        dag = compute_dag_layout(steps)
        assert dag is not None
        assert len(dag.nodes[0].label) <= 20

    def test_state_preserved_on_node(self):
        steps = [self._make_step("s1", state="state.statement.Complete")]
        dag = compute_dag_layout(steps)
        assert dag is not None
        assert dag.nodes[0].state == "state.statement.Complete"


@pytestmark_routes
class TestDagRoute:
    def test_workflow_detail_has_dag_view(self, client):
        tc, store = client
        store.save_runner(_make_runner("r-1", state="running"))
        resp = tc.get("/v2/workflows/r-1")
        assert resp.status_code == 200
        assert "v2-step-graph" in resp.text
        assert 'data-view="graph"' in resp.text
