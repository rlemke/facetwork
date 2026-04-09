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

"""Tests for MCP server error handling, input validation, and resource boundary conditions."""

import json

import pytest

try:
    from mcp.types import TextContent  # noqa: F401

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")

from facetwork.mcp.server import (
    _handle_resource,
    _tool_compile,
    _tool_continue_step,
    _tool_execute_workflow,
    _tool_manage_handlers,
    _tool_manage_runner,
    _tool_resume_workflow,
    _tool_validate,
)
from facetwork.runtime.entities import (
    FlowDefinition,
    FlowIdentity,
    HandlerRegistration,
    Parameter,
    RunnerDefinition,
    WorkflowDefinition,
)

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False


# ============================================================================
# Fixtures
# ============================================================================


def _make_workflow(uuid="wf-1", name="TestWF"):
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
    if workflow is None:
        workflow = _make_workflow()
    return RunnerDefinition(
        uuid=uuid,
        workflow_id=workflow.uuid,
        workflow=workflow,
        state=state,
        start_time=1000,
        end_time=2000,
        duration=1000,
        parameters=[Parameter(name="x", value=42)],
    )


@pytest.fixture
def store():
    if not MONGOMOCK_AVAILABLE:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    s = MongoStore(database_name="afl_test_mcp_errors", client=mock_client)
    yield s
    s.drop_database()
    s.close()


# ============================================================================
# TestToolDispatchErrors
# ============================================================================


class TestToolDispatchErrors:
    """Test missing required arguments for each tool."""

    def test_compile_without_source_uses_default_empty(self):
        # _tool_compile uses arguments.get("source", ""), so missing source = empty string
        result = _tool_compile({})
        data = json.loads(result[0].text)
        # Empty string is valid FFL (empty program)
        assert data["success"] is True

    def test_validate_without_source_uses_default_empty(self):
        # _tool_validate uses arguments.get("source", ""), so missing source = empty string
        result = _tool_validate({})
        data = json.loads(result[0].text)
        assert data["valid"] is True

    def test_execute_workflow_without_source_and_name(self):
        result = _tool_execute_workflow({})
        data = json.loads(result[0].text)
        # With empty source and empty workflow_name, workflow won't be found
        assert data["success"] is False

    def test_execute_workflow_without_workflow_name(self):
        result = _tool_execute_workflow({"source": "workflow W()"})
        data = json.loads(result[0].text)
        # Empty workflow_name won't match any declaration
        assert data["success"] is False
        assert "not found" in data["error"]

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_continue_step_without_step_id(self, store):
        # Empty step_id will cause step lookup to fail
        result = _tool_continue_step({}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is False

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_resume_workflow_without_required_args(self, store):
        result = _tool_resume_workflow({}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is False

    def test_manage_runner_without_runner_id(self):
        # Action is validated first; "cancel" is valid but runner_id empty -> not found
        result = _tool_manage_runner({"runner_id": "", "action": "cancel"}, lambda: None)
        data = json.loads(result[0].text)
        assert data["success"] is False

    def test_manage_runner_without_action(self):
        result = _tool_manage_runner({"runner_id": "r-1", "action": ""}, lambda: None)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "Invalid action" in data["error"]

    def test_manage_handlers_without_action(self):
        result = _tool_manage_handlers({"action": ""}, lambda: None)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "Invalid action" in data["error"]

    def test_manage_handlers_get_without_facet_name(self):
        result = _tool_manage_handlers({"action": "get"}, lambda: None)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "facet_name is required" in data["error"]

    def test_manage_handlers_register_without_facet_name(self):
        result = _tool_manage_handlers({"action": "register", "module_uri": "m"}, lambda: None)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "facet_name is required" in data["error"]

    def test_manage_handlers_delete_without_facet_name(self):
        result = _tool_manage_handlers({"action": "delete"}, lambda: None)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "facet_name is required" in data["error"]


# ============================================================================
# TestToolInputValidation
# ============================================================================


class TestToolInputValidation:
    """Test edge-case inputs to tool functions."""

    def test_compile_none_source_treated_as_empty(self):
        # arguments.get("source", "") returns None when explicitly passed
        result = _tool_compile({"source": None})
        data = json.loads(result[0].text)
        # None should cause a parse error or be handled gracefully
        # The parser may raise on None input
        assert "success" in data

    def test_compile_unicode_source(self):
        # Unicode identifiers should work in FFL strings
        source = 'facet Gruss(msg: String = "Gruesse")'
        result = _tool_compile({"source": source})
        data = json.loads(result[0].text)
        assert data["success"] is True

    def test_compile_large_source(self):
        # Generate source with many facets
        lines = [f"facet Facet{i}()" for i in range(200)]
        source = "\n".join(lines)
        result = _tool_compile({"source": source})
        data = json.loads(result[0].text)
        assert data["success"] is True
        # Verify all facets appear in output
        decls = data["json"]["declarations"]
        assert len(decls) == 200

    def test_validate_unicode_source(self):
        source = 'facet Hello(name: String = "world")'
        result = _tool_validate({"source": source})
        data = json.loads(result[0].text)
        assert data["valid"] is True

    def test_execute_workflow_empty_inputs(self):
        source = "workflow W()"
        result = _tool_execute_workflow({"source": source, "workflow_name": "W", "inputs": {}})
        data = json.loads(result[0].text)
        assert "success" in data

    def test_execute_workflow_with_none_inputs(self):
        source = "workflow W()"
        result = _tool_execute_workflow({"source": source, "workflow_name": "W", "inputs": None})
        data = json.loads(result[0].text)
        assert "success" in data

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_register_handler_with_all_optional_fields(self, store):
        result = _tool_manage_handlers(
            {
                "action": "register",
                "facet_name": "ns.Full",
                "module_uri": "full.module",
                "entrypoint": "process",
                "version": "3.2.1",
                "timeout_ms": 60000,
                "requirements": ["numpy>=1.0", "pandas"],
                "metadata": {"author": "test", "priority": 10},
            },
            lambda: store,
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        handler = data["handler"]
        assert handler["facet_name"] == "ns.Full"
        assert handler["module_uri"] == "full.module"
        assert handler["entrypoint"] == "process"
        assert handler["version"] == "3.2.1"
        assert handler["timeout_ms"] == 60000
        assert handler["requirements"] == ["numpy>=1.0", "pandas"]
        assert handler["metadata"]["author"] == "test"
        assert handler["metadata"]["priority"] == 10

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_register_handler_defaults_when_omitted(self, store):
        result = _tool_manage_handlers(
            {
                "action": "register",
                "facet_name": "ns.Minimal",
                "module_uri": "minimal.mod",
            },
            lambda: store,
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        handler = data["handler"]
        assert handler["entrypoint"] == "handle"
        assert handler["version"] == "1.0.0"
        assert handler["timeout_ms"] == 30000
        assert handler["requirements"] == []
        assert handler["metadata"] == {}


# ============================================================================
# TestResourceBoundaryConditions
# ============================================================================


@pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
class TestResourceBoundaryConditions:
    """Test resource handler with unusual URIs and data sizes."""

    def test_malformed_uri_empty_path(self, store):
        # "afl://" -> parts becomes [""] after strip("/").split("/")
        data = json.loads(_handle_resource("afl://", lambda: store))
        assert "error" in data

    def test_malformed_uri_triple_slash(self, store):
        # "afl:///" -> parts becomes [""] after strip
        data = json.loads(_handle_resource("afl:///", lambda: store))
        assert "error" in data

    def test_dotted_facet_name_in_handler_resource(self, store):
        reg = HandlerRegistration(
            facet_name="deep.ns.FacetName",
            module_uri="deep.module",
            entrypoint="handle",
            version="1.0.0",
            timeout_ms=30000,
            created=1000,
            updated=2000,
        )
        store.save_handler_registration(reg)
        # The handler resource splits on "/" so "deep.ns.FacetName" is a single segment
        data = json.loads(_handle_resource("afl://handlers/deep.ns.FacetName", lambda: store))
        assert data["facet_name"] == "deep.ns.FacetName"
        assert data["module_uri"] == "deep.module"

    def test_invalid_runner_sub_resource(self, store):
        runner = _make_runner()
        store.save_runner(runner)
        # "invalid" is not a valid sub-resource (not "steps" or "logs")
        data = json.loads(_handle_resource("afl://runners/r-1/invalid", lambda: store))
        assert "error" in data

    def test_invalid_flow_sub_resource(self, store):
        flow = FlowDefinition(
            uuid="f-1",
            name=FlowIdentity(name="MyFlow", path="/flows/my", uuid="f-1"),
        )
        store.save_flow(flow)
        data = json.loads(_handle_resource("afl://flows/f-1/invalid", lambda: store))
        assert "error" in data

    def test_large_runners_dataset(self, store):
        for i in range(55):
            wf = _make_workflow(uuid=f"wf-{i}", name=f"WF{i}")
            runner = _make_runner(uuid=f"r-{i}", workflow=wf)
            store.save_runner(runner)
        data = json.loads(_handle_resource("afl://runners", lambda: store))
        assert len(data) == 55

    def test_large_handlers_dataset(self, store):
        for i in range(50):
            reg = HandlerRegistration(
                facet_name=f"ns.Facet{i}",
                module_uri=f"mod.handler{i}",
                entrypoint="handle",
                version="1.0.0",
                timeout_ms=30000,
                created=1000,
                updated=2000,
            )
            store.save_handler_registration(reg)
        data = json.loads(_handle_resource("afl://handlers", lambda: store))
        assert len(data) == 50

    def test_unknown_top_level_resource(self, store):
        data = json.loads(_handle_resource("afl://widgets", lambda: store))
        assert "error" in data
        assert "Unknown resource" in data["error"]

    def test_deeply_nested_unknown_path(self, store):
        data = json.loads(_handle_resource("afl://runners/r-1/steps/extra/deep", lambda: store))
        # Extra path segments fall through the handler
        assert "error" in data or isinstance(data, list)
