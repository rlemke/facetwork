"""Extended tests for MCP server — continue_step, resume_workflow, unknown tool, edge cases."""

import json
from unittest.mock import MagicMock

import pytest

try:
    from mcp.types import TextContent  # noqa: F401

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")

from facetwork.mcp.server import (
    _handle_resource,
    _tool_continue_step,
    _tool_resume_workflow,
    _tool_retry_step,
)
from facetwork.runtime import MemoryStore
from facetwork.runtime.states import StepState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import ObjectType

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False


@pytest.fixture
def store():
    if not MONGOMOCK_AVAILABLE:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    s = MongoStore(database_name="afl_test_mcp_ext", client=mock_client)
    yield s
    s.drop_database()
    s.close()


# ============================================================================
# Tool: afl_continue_step
# ============================================================================


class TestContinueStep:
    def test_continue_step_success(self):
        mem = MemoryStore()
        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step.state = StepState.EVENT_TRANSMIT
        mem.save_step(step)

        result = _tool_continue_step(
            {"step_id": step.id, "result": {"output": "done"}},
            lambda: mem,
        )

        data = json.loads(result[0].text)
        assert data["success"] is True

    def test_continue_step_not_found(self):
        mem = MemoryStore()

        result = _tool_continue_step(
            {"step_id": "nonexistent"},
            lambda: mem,
        )

        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_continue_step_wrong_state(self):
        mem = MemoryStore()
        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step.state = StepState.CREATED
        mem.save_step(step)

        result = _tool_continue_step(
            {"step_id": step.id},
            lambda: mem,
        )

        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "expected" in data["error"]

    def test_continue_step_no_result(self):
        mem = MemoryStore()
        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step.state = StepState.EVENT_TRANSMIT
        mem.save_step(step)

        result = _tool_continue_step(
            {"step_id": step.id},
            lambda: mem,
        )

        data = json.loads(result[0].text)
        assert data["success"] is True


# ============================================================================
# Tool: afl_retry_step
# ============================================================================


class TestRetryStep:
    def test_retry_step_success(self):
        from facetwork.runtime.entities import TaskDefinition

        mem = MemoryStore()
        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Download",
        )
        step.mark_error(RuntimeError("SSL error"))
        mem.save_step(step)

        task = TaskDefinition(
            uuid="task-retry-1",
            name="Download",
            runner_id="runner-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id=step.id,
            state="failed",
            created=1000,
            error={"message": "SSL error"},
        )
        mem.save_task(task)

        result = _tool_retry_step(
            {"step_id": step.id},
            lambda: mem,
        )

        data = json.loads(result[0].text)
        assert data["success"] is True

        reloaded = mem.get_step(step.id)
        assert reloaded.state == StepState.EVENT_TRANSMIT

    def test_retry_step_not_found(self):
        mem = MemoryStore()

        result = _tool_retry_step(
            {"step_id": "nonexistent"},
            lambda: mem,
        )

        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]


# ============================================================================
# Tool: afl_resume_workflow
# ============================================================================


class TestResumeWorkflow:
    def test_resume_workflow_not_found(self):
        mock_store = MagicMock()

        result = _tool_resume_workflow(
            {
                "workflow_id": "wf-1",
                "source": "facet NotAWorkflow()",
                "workflow_name": "Missing",
            },
            lambda: mock_store,
        )

        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_resume_invalid_source(self):
        mock_store = MagicMock()

        result = _tool_resume_workflow(
            {
                "workflow_id": "wf-1",
                "source": "@@@ invalid",
                "workflow_name": "Anything",
            },
            lambda: mock_store,
        )

        data = json.loads(result[0].text)
        assert data["success"] is False

    def test_resume_returns_result(self):
        """Resume with valid source returns execution result (not an error)."""
        mem = MemoryStore()
        source = "workflow Simple(x: String) => (output: String)"

        result = _tool_resume_workflow(
            {
                "workflow_id": "wf-1",
                "source": source,
                "workflow_name": "Simple",
            },
            lambda: mem,
        )

        data = json.loads(result[0].text)
        # Should return a proper execution result with workflow_id and status
        assert "workflow_id" in data
        assert "status" in data
        assert data["workflow_id"] == "wf-1"


# ============================================================================
# Resource edge cases
# ============================================================================


@pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
class TestResourceEdgeCases:
    def test_runner_steps_not_found(self, store):
        data = json.loads(_handle_resource("afl://runners/missing/steps", lambda: store))
        assert data["error"] == "Runner not found"

    def test_runner_logs_empty(self, store):
        data = json.loads(_handle_resource("afl://runners/r-1/logs", lambda: store))
        assert data == []

    def test_flow_source_not_found(self, store):
        data = json.loads(_handle_resource("afl://flows/missing/source", lambda: store))
        assert data["error"] == "Flow not found"

    def test_step_not_found(self, store):
        data = json.loads(_handle_resource("afl://steps/missing", lambda: store))
        assert data["error"] == "Step not found"

    def test_flow_detail_not_found(self, store):
        data = json.loads(_handle_resource("afl://flows/missing", lambda: store))
        assert data["error"] == "Flow not found"

    def test_unknown_resource_uri(self, store):
        data = json.loads(_handle_resource("afl://something/else", lambda: store))
        assert "error" in data

    def test_servers_empty(self, store):
        data = json.loads(_handle_resource("afl://servers", lambda: store))
        assert data == []

    def test_tasks_empty(self, store):
        data = json.loads(_handle_resource("afl://tasks", lambda: store))
        assert data == []

    def test_flows_empty(self, store):
        data = json.loads(_handle_resource("afl://flows", lambda: store))
        assert data == []
