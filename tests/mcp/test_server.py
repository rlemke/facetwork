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

"""Integration tests for MCP server tools and resources."""

import json

import pytest

try:
    from mcp.types import TextContent  # noqa: F401

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")


# Import tool/resource helpers directly for synchronous testing
from facetwork.mcp.server import (
    _find_workflow,
    _handle_resource,
    _tool_compile,
    _tool_execute_workflow,
    _tool_manage_handlers,
    _tool_manage_runner,
    _tool_validate,
    create_server,
)
from facetwork.runtime.entities import (
    FlowDefinition,
    FlowIdentity,
    HandlerRegistration,
    LogDefinition,
    Parameter,
    RunnerDefinition,
    ServerDefinition,
    SourceText,
    TaskDefinition,
    WorkflowDefinition,
)
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import ObjectType

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
    """Create a mongomock-backed store for testing."""
    if not MONGOMOCK_AVAILABLE:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    s = MongoStore(database_name="afl_test_mcp", client=mock_client)
    yield s
    s.drop_database()
    s.close()


@pytest.fixture
def server(store):
    """Create an MCP server with a test store."""
    return create_server(store=store)


# ============================================================================
# Tool tests: afl_compile
# ============================================================================


class TestCompileTool:
    def test_compile_valid_source(self):
        result = _tool_compile({"source": "facet Test()"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "json" in data
        assert data["json"]["type"] == "Program"

    def test_compile_invalid_source(self):
        result = _tool_compile({"source": "not valid afl @@@"})
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert len(data["errors"]) > 0

    def test_compile_empty_source(self):
        result = _tool_compile({"source": ""})
        data = json.loads(result[0].text)
        # Empty source is valid (empty program)
        assert data["success"] is True


# ============================================================================
# Tool tests: afl_validate
# ============================================================================


class TestValidateTool:
    def test_validate_valid_source(self):
        result = _tool_validate({"source": "facet Test()"})
        data = json.loads(result[0].text)
        assert data["valid"] is True
        assert data["errors"] == []

    def test_validate_duplicate_names(self):
        result = _tool_validate({"source": "facet Dup()\nfacet Dup()"})
        data = json.loads(result[0].text)
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_parse_error(self):
        result = _tool_validate({"source": "@@invalid"})
        data = json.loads(result[0].text)
        assert data["valid"] is False
        assert len(data["errors"]) > 0


# ============================================================================
# Tool tests: afl_execute_workflow
# ============================================================================


class TestExecuteWorkflowTool:
    def test_execute_simple_workflow(self):
        source = "workflow Simple(x: String) => (output: String)"
        result = _tool_execute_workflow(
            {
                "source": source,
                "workflow_name": "Simple",
                "inputs": {"x": "hello"},
            }
        )
        data = json.loads(result[0].text)
        assert "success" in data
        assert "workflow_id" in data

    def test_execute_workflow_not_found(self):
        result = _tool_execute_workflow(
            {
                "source": "facet Test()",
                "workflow_name": "Missing",
            }
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_execute_invalid_source(self):
        result = _tool_execute_workflow(
            {
                "source": "@@invalid",
                "workflow_name": "Anything",
            }
        )
        data = json.loads(result[0].text)
        assert data["success"] is False


# ============================================================================
# Tool tests: afl_manage_runner
# ============================================================================


class TestManageRunnerTool:
    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_cancel_runner(self, store):
        runner = _make_runner()
        store.save_runner(runner)

        result = _tool_manage_runner(
            {"runner_id": "r-1", "action": "cancel"},
            lambda: store,
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

        updated = store.get_runner("r-1")
        assert updated.state == "cancelled"

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_pause_runner(self, store):
        runner = _make_runner()
        store.save_runner(runner)

        result = _tool_manage_runner(
            {"runner_id": "r-1", "action": "pause"},
            lambda: store,
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    @pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
    def test_runner_not_found(self, store):
        result = _tool_manage_runner(
            {"runner_id": "nonexistent", "action": "cancel"},
            lambda: store,
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_invalid_action(self):
        result = _tool_manage_runner(
            {"runner_id": "r-1", "action": "destroy"},
            lambda: None,
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "Invalid action" in data["error"]


def _make_handler_registration(
    facet_name="ns.TestFacet",
    module_uri="my.handlers",
    entrypoint="handle",
    version="1.0.0",
    timeout_ms=30000,
    created=1000,
    updated=2000,
):
    return HandlerRegistration(
        facet_name=facet_name,
        module_uri=module_uri,
        entrypoint=entrypoint,
        version=version,
        timeout_ms=timeout_ms,
        created=created,
        updated=updated,
    )


# ============================================================================
# Tool tests: afl_manage_handlers
# ============================================================================


@pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
class TestManageHandlersTool:
    def test_list_empty(self, store):
        result = _tool_manage_handlers({"action": "list"}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["handlers"] == []

    def test_list_with_registrations(self, store):
        reg = _make_handler_registration()
        store.save_handler_registration(reg)
        result = _tool_manage_handlers({"action": "list"}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert len(data["handlers"]) == 1
        assert data["handlers"][0]["facet_name"] == "ns.TestFacet"

    def test_get_existing(self, store):
        reg = _make_handler_registration()
        store.save_handler_registration(reg)
        result = _tool_manage_handlers(
            {"action": "get", "facet_name": "ns.TestFacet"}, lambda: store
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["handler"]["facet_name"] == "ns.TestFacet"
        assert data["handler"]["module_uri"] == "my.handlers"

    def test_get_not_found(self, store):
        result = _tool_manage_handlers({"action": "get", "facet_name": "ns.Missing"}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_get_missing_facet_name(self, store):
        result = _tool_manage_handlers({"action": "get"}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "facet_name is required" in data["error"]

    def test_register_new(self, store):
        result = _tool_manage_handlers(
            {
                "action": "register",
                "facet_name": "ns.NewFacet",
                "module_uri": "new.module",
                "entrypoint": "run",
                "version": "2.0.0",
            },
            lambda: store,
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["handler"]["facet_name"] == "ns.NewFacet"
        assert data["handler"]["module_uri"] == "new.module"
        assert data["handler"]["entrypoint"] == "run"
        assert data["handler"]["version"] == "2.0.0"
        # Verify persisted
        saved = store.get_handler_registration("ns.NewFacet")
        assert saved is not None
        assert saved.module_uri == "new.module"

    def test_register_upsert(self, store):
        reg = _make_handler_registration(created=1000, updated=1000)
        store.save_handler_registration(reg)
        result = _tool_manage_handlers(
            {
                "action": "register",
                "facet_name": "ns.TestFacet",
                "module_uri": "updated.module",
            },
            lambda: store,
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["handler"]["module_uri"] == "updated.module"
        # Original created timestamp should be preserved
        assert data["handler"]["created"] == 1000
        # Updated should be newer
        assert data["handler"]["updated"] > 1000

    def test_register_missing_facet_name(self, store):
        result = _tool_manage_handlers({"action": "register", "module_uri": "m"}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "facet_name is required" in data["error"]

    def test_register_missing_module_uri(self, store):
        result = _tool_manage_handlers({"action": "register", "facet_name": "ns.X"}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "module_uri is required" in data["error"]

    def test_delete_existing(self, store):
        reg = _make_handler_registration()
        store.save_handler_registration(reg)
        result = _tool_manage_handlers(
            {"action": "delete", "facet_name": "ns.TestFacet"}, lambda: store
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        # Verify deleted
        assert store.get_handler_registration("ns.TestFacet") is None

    def test_delete_not_found(self, store):
        result = _tool_manage_handlers(
            {"action": "delete", "facet_name": "ns.Missing"}, lambda: store
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_delete_missing_facet_name(self, store):
        result = _tool_manage_handlers({"action": "delete"}, lambda: store)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "facet_name is required" in data["error"]

    def test_invalid_action(self):
        result = _tool_manage_handlers({"action": "destroy"}, lambda: None)
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "Invalid action" in data["error"]


# ============================================================================
# Resource tests
# ============================================================================


@pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
class TestResources:
    def test_runners_list_empty(self, store):
        data = json.loads(_handle_resource("afl://runners", lambda: store))
        assert data == []

    def test_runners_list_with_data(self, store):
        runner = _make_runner()
        store.save_runner(runner)
        data = json.loads(_handle_resource("afl://runners", lambda: store))
        assert len(data) == 1
        assert data[0]["uuid"] == "r-1"

    def test_runner_detail(self, store):
        runner = _make_runner()
        store.save_runner(runner)
        data = json.loads(_handle_resource("afl://runners/r-1", lambda: store))
        assert data["uuid"] == "r-1"
        assert data["workflow_name"] == "TestWF"

    def test_runner_detail_not_found(self, store):
        data = json.loads(_handle_resource("afl://runners/missing", lambda: store))
        assert data["error"] == "Runner not found"

    def test_runner_steps(self, store):
        runner = _make_runner()
        store.save_runner(runner)
        # Create a step for the workflow
        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.WORKFLOW,
        )
        store.save_step(step)
        data = json.loads(_handle_resource("afl://runners/r-1/steps", lambda: store))
        assert len(data) == 1

    def test_runner_logs(self, store):
        log = LogDefinition(
            uuid="l-1",
            order=1,
            runner_id="r-1",
            message="hello",
        )
        store.save_log(log)
        data = json.loads(_handle_resource("afl://runners/r-1/logs", lambda: store))
        assert len(data) == 1
        assert data[0]["message"] == "hello"

    def test_step_detail(self, store):
        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.FACET,
        )
        store.save_step(step)
        data = json.loads(_handle_resource(f"afl://steps/{step.id}", lambda: store))
        assert data["id"] == step.id
        assert data["object_type"] == ObjectType.FACET

    def test_step_not_found(self, store):
        data = json.loads(_handle_resource("afl://steps/missing", lambda: store))
        assert data["error"] == "Step not found"

    def test_flows_list(self, store):
        flow = FlowDefinition(
            uuid="f-1",
            name=FlowIdentity(name="MyFlow", path="/flows/my", uuid="f-1"),
        )
        store.save_flow(flow)
        data = json.loads(_handle_resource("afl://flows", lambda: store))
        assert len(data) == 1
        assert data[0]["name"] == "MyFlow"

    def test_flow_detail(self, store):
        flow = FlowDefinition(
            uuid="f-1",
            name=FlowIdentity(name="MyFlow", path="/flows/my", uuid="f-1"),
            workflows=[_make_workflow()],
        )
        store.save_flow(flow)
        data = json.loads(_handle_resource("afl://flows/f-1", lambda: store))
        assert data["uuid"] == "f-1"

    def test_flow_not_found(self, store):
        data = json.loads(_handle_resource("afl://flows/missing", lambda: store))
        assert data["error"] == "Flow not found"

    def test_flow_source(self, store):
        flow = FlowDefinition(
            uuid="f-1",
            name=FlowIdentity(name="MyFlow", path="/flows/my", uuid="f-1"),
            compiled_sources=[SourceText(name="main.ffl", content="facet Test()")],
        )
        store.save_flow(flow)
        data = json.loads(_handle_resource("afl://flows/f-1/source", lambda: store))
        assert len(data["sources"]) == 1
        assert data["sources"][0]["content"] == "facet Test()"

    def test_servers_list(self, store):
        server = ServerDefinition(
            uuid="srv-1",
            server_group="default",
            service_name="afl-runner",
            server_name="host1",
            state="running",
        )
        store.save_server(server)
        data = json.loads(_handle_resource("afl://servers", lambda: store))
        assert len(data) == 1
        assert data[0]["uuid"] == "srv-1"

    def test_tasks_list(self, store):
        task = TaskDefinition(
            uuid="t-1",
            name="DoWork",
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="f-1",
            step_id="s-1",
            state="pending",
        )
        store.save_task(task)
        data = json.loads(_handle_resource("afl://tasks", lambda: store))
        assert len(data) == 1
        assert data[0]["uuid"] == "t-1"

    def test_unknown_resource(self, store):
        data = json.loads(_handle_resource("afl://unknown", lambda: store))
        assert "error" in data

    def test_handlers_list_empty(self, store):
        data = json.loads(_handle_resource("afl://handlers", lambda: store))
        assert data == []

    def test_handlers_list_with_data(self, store):
        reg = _make_handler_registration()
        store.save_handler_registration(reg)
        data = json.loads(_handle_resource("afl://handlers", lambda: store))
        assert len(data) == 1
        assert data[0]["facet_name"] == "ns.TestFacet"

    def test_handler_detail(self, store):
        reg = _make_handler_registration()
        store.save_handler_registration(reg)
        data = json.loads(_handle_resource("afl://handlers/ns.TestFacet", lambda: store))
        assert data["facet_name"] == "ns.TestFacet"
        assert data["module_uri"] == "my.handlers"

    def test_handler_detail_not_found(self, store):
        data = json.loads(_handle_resource("afl://handlers/ns.Missing", lambda: store))
        assert "error" in data
        assert "not found" in data["error"]


# ============================================================================
# Helper tests
# ============================================================================


class TestFindWorkflow:
    def test_find_at_top_level(self):
        compiled = {
            "declarations": [
                {"type": "WorkflowDecl", "name": "MyWF"},
                {"type": "FacetDecl", "name": "Other"},
            ]
        }
        result = _find_workflow(compiled, "MyWF")
        assert result is not None
        assert result["name"] == "MyWF"

    def test_find_in_namespace(self):
        compiled = {
            "declarations": [
                {
                    "type": "Namespace",
                    "declarations": [
                        {"type": "WorkflowDecl", "name": "Nested"},
                    ],
                }
            ]
        }
        result = _find_workflow(compiled, "Nested")
        assert result is not None

    def test_not_found(self):
        compiled = {"declarations": [{"type": "FacetDecl", "name": "X"}]}
        assert _find_workflow(compiled, "Missing") is None


# ============================================================================
# Server creation test
# ============================================================================


class TestServerCreation:
    def test_create_server_returns_server(self, store):
        s = create_server(store=store)
        assert s is not None
        assert s.name == "afl-mcp"
