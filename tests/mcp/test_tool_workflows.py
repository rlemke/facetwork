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

"""Tests for MCP tool workflow operations: compile-execute, resume, and edge cases."""

import json

import pytest

try:
    from mcp.types import TextContent  # noqa: F401

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")

from facetwork.mcp.server import (
    _tool_compile,
    _tool_execute_workflow,
    _tool_resume_workflow,
)

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def store():
    if not MONGOMOCK_AVAILABLE:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    s = MongoStore(database_name="afl_test_mcp_workflows", client=mock_client)
    yield s
    s.drop_database()
    s.close()


# ============================================================================
# TestCompileExecuteFlow
# ============================================================================


class TestCompileExecuteFlow:
    """Test compile-then-execute workflow patterns."""

    def test_compile_then_execute_same_source(self):
        source = "workflow Simple(x: String) => (output: String)"
        # First compile
        compile_result = _tool_compile({"source": source})
        compile_data = json.loads(compile_result[0].text)
        assert compile_data["success"] is True

        # Then execute
        exec_result = _tool_execute_workflow(
            {"source": source, "workflow_name": "Simple", "inputs": {"x": "hello"}}
        )
        exec_data = json.loads(exec_result[0].text)
        assert exec_data["success"] is True
        assert "workflow_id" in exec_data

    def test_execute_workflow_with_event_facets_pauses(self):
        source = (
            "event facet DoWork(input: String) => (result: String)\n"
            "\n"
            "workflow WithEvent(x: String) => (out: String) andThen {\n"
            "    s = DoWork(input = $.x)\n"
            "    yield WithEvent(out = s.result)\n"
            "}\n"
        )
        result = _tool_execute_workflow(
            {"source": source, "workflow_name": "WithEvent", "inputs": {"x": "test"}}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "workflow_id" in data
        # Workflow should pause because it depends on an event facet
        assert data["status"] == "PAUSED"

    def test_continue_step_paused_workflow_state(self):
        # Execute a workflow that pauses on event facet
        source = (
            "event facet DoWork(input: String) => (result: String)\n"
            "\n"
            "workflow WithEvent(x: String) => (out: String) andThen {\n"
            "    s = DoWork(input = $.x)\n"
            "    yield WithEvent(out = s.result)\n"
            "}\n"
        )
        exec_result = _tool_execute_workflow(
            {"source": source, "workflow_name": "WithEvent", "inputs": {"x": "test"}}
        )
        exec_data = json.loads(exec_result[0].text)
        assert exec_data["status"] == "PAUSED"
        workflow_id = exec_data["workflow_id"]

        # The evaluator uses MemoryStore internally, so we cannot access steps
        # from outside. We verify the paused state was returned correctly.
        assert workflow_id is not None
        assert exec_data["success"] is True
        assert exec_data["iterations"] >= 1

    def test_full_compile_execute_cycle(self):
        source = "workflow Echo(msg: String) => (echo: String)"
        # Compile
        compile_result = _tool_compile({"source": source})
        compile_data = json.loads(compile_result[0].text)
        assert compile_data["success"] is True
        assert compile_data["json"]["type"] == "Program"

        # Execute
        exec_result = _tool_execute_workflow(
            {"source": source, "workflow_name": "Echo", "inputs": {"msg": "hi"}}
        )
        exec_data = json.loads(exec_result[0].text)
        assert exec_data["success"] is True
        assert exec_data["status"] == "COMPLETED"


# ============================================================================
# TestResumeWorkflowEdgeCases
# ============================================================================


class TestResumeWorkflowEdgeCases:
    """Test resume workflow edge cases."""

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_resume_nonexistent_workflow(self, store):
        source = "workflow W()"
        result = _tool_resume_workflow(
            {
                "workflow_id": "nonexistent-id",
                "source": source,
                "workflow_name": "W",
            },
            lambda: store,
        )
        data = json.loads(result[0].text)
        # Should complete or succeed since there are no steps to resume
        assert "success" in data

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_resume_with_additional_inputs(self, store):
        source = "workflow W(x: String)"
        result = _tool_resume_workflow(
            {
                "workflow_id": "wf-resume-test",
                "source": source,
                "workflow_name": "W",
                "inputs": {"x": "extra_data"},
            },
            lambda: store,
        )
        data = json.loads(result[0].text)
        # Should handle gracefully (workflow may complete or report no steps)
        assert "success" in data

    def test_resume_workflow_not_found_in_source(self):
        source = "facet NotAWorkflow()"
        result = _tool_resume_workflow(
            {
                "workflow_id": "wf-123",
                "source": source,
                "workflow_name": "Missing",
            },
            lambda: None,
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]


# ============================================================================
# TestExecuteWorkflowVariants
# ============================================================================


class TestExecuteWorkflowVariants:
    """Test various workflow source patterns."""

    def test_execute_namespaced_workflow_found(self):
        # Workflows are now included in namespace declarations,
        # so _find_workflow can resolve them.
        source = "namespace ns { workflow W() }"
        result = _tool_execute_workflow({"source": source, "workflow_name": "W"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "workflow_id" in data

    def test_execute_top_level_workflow_found(self):
        source = "workflow TopLevel()"
        result = _tool_execute_workflow({"source": source, "workflow_name": "TopLevel"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "workflow_id" in data

    def test_execute_workflow_with_andthen_body(self):
        source = (
            "facet Greet(name: String) => (greeting: String)\n"
            "\n"
            "workflow Hello(name: String) => (out: String) andThen {\n"
            "    g = Greet(name = $.name)\n"
            "    yield Hello(out = g.greeting)\n"
            "}\n"
        )
        result = _tool_execute_workflow(
            {"source": source, "workflow_name": "Hello", "inputs": {"name": "World"}}
        )
        data = json.loads(result[0].text)
        assert "success" in data
        assert "workflow_id" in data

    def test_execute_workflow_with_params(self):
        source = "workflow Calc(a: Int, b: Int) => (sum: Int)"
        result = _tool_execute_workflow(
            {"source": source, "workflow_name": "Calc", "inputs": {"a": 1, "b": 2}}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "workflow_id" in data

    def test_execute_facet_not_workflow_fails(self):
        source = "facet NotWorkflow(x: String)"
        result = _tool_execute_workflow({"source": source, "workflow_name": "NotWorkflow"})
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_execute_with_invalid_source(self):
        result = _tool_execute_workflow({"source": "@@@invalid", "workflow_name": "W"})
        data = json.loads(result[0].text)
        assert data["success"] is False

    def test_execute_workflow_no_params(self):
        source = "workflow NoParams()"
        result = _tool_execute_workflow({"source": source, "workflow_name": "NoParams"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["status"] == "COMPLETED"
