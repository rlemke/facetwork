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

"""Tests for AFL runtime entity dataclasses."""

from dataclasses import asdict

from afl.runtime.entities import (
    BlockDefinition,
    Classifier,
    FacetDefinition,
    FlowDefinition,
    FlowIdentity,
    HandledCount,
    LogDefinition,
    NamespaceDefinition,
    NoteImportance,
    NoteOriginator,
    NoteType,
    Ownership,
    # Supporting types
    Parameter,
    RunnerDefinition,
    RunnerState,
    ServerDefinition,
    ServerState,
    SourceText,
    TaskDefinition,
    TaskState,
    UserDefinition,
    # Workflow and execution
    WorkflowDefinition,
)


class TestSupportingTypes:
    """Tests for supporting type dataclasses."""

    def test_parameter_creation(self):
        """Test Parameter dataclass creation."""
        param = Parameter(name="count", value=42, type_hint="Long")
        assert param.name == "count"
        assert param.value == 42
        assert param.type_hint == "Long"

    def test_parameter_defaults(self):
        """Test Parameter default values."""
        param = Parameter(name="test", value="hello")
        assert param.type_hint == "Any"

    def test_user_definition(self):
        """Test UserDefinition dataclass."""
        user = UserDefinition(email="test@example.com", name="Test User")
        assert user.email == "test@example.com"
        assert user.name == "Test User"
        assert user.avatar == ""

    def test_ownership(self):
        """Test Ownership dataclass."""
        user = UserDefinition(email="owner@example.com")
        ownership = Ownership(owner=user, group="developers")
        assert ownership.owner.email == "owner@example.com"
        assert ownership.group == "developers"

    def test_classifier(self):
        """Test Classifier dataclass."""
        classifier = Classifier(category="workflow", tags=["production", "v1"])
        assert classifier.category == "workflow"
        assert "production" in classifier.tags

    def test_source_text(self):
        """Test SourceText dataclass."""
        source = SourceText(name="main.afl", content="facet Test()")
        assert source.name == "main.afl"
        assert source.content == "facet Test()"
        assert source.language == "afl"


class TestFlowTypes:
    """Tests for flow-related dataclasses."""

    def test_flow_identity(self):
        """Test FlowIdentity dataclass."""
        identity = FlowIdentity(name="MyWorkflow", path="/workflows/my", uuid="flow-123")
        assert identity.name == "MyWorkflow"
        assert identity.path == "/workflows/my"
        assert identity.uuid == "flow-123"

    def test_namespace_definition(self):
        """Test NamespaceDefinition dataclass."""
        namespace = NamespaceDefinition(uuid="ns-123", name="com.example", path="/com/example")
        assert namespace.uuid == "ns-123"
        assert namespace.name == "com.example"

    def test_facet_definition(self):
        """Test FacetDefinition dataclass."""
        facet = FacetDefinition(
            uuid="facet-123",
            name="ProcessOrder",
            namespace_id="ns-123",
            parameters=[Parameter(name="orderId", value="")],
            return_type="OrderResult",
        )
        assert facet.uuid == "facet-123"
        assert facet.name == "ProcessOrder"
        assert len(facet.parameters) == 1

    def test_block_definition(self):
        """Test BlockDefinition dataclass."""
        block = BlockDefinition(
            uuid="block-123",
            name="mainBlock",
            block_type="AndThen",
            statements=["stmt-1", "stmt-2"],
        )
        assert block.uuid == "block-123"
        assert block.block_type == "AndThen"
        assert len(block.statements) == 2

    def test_flow_definition(self):
        """Test FlowDefinition dataclass."""
        flow = FlowDefinition(
            uuid="flow-123",
            name=FlowIdentity(name="Test", path="/test", uuid="flow-123"),
            namespaces=[NamespaceDefinition(uuid="ns-1", name="com.test")],
        )
        assert flow.uuid == "flow-123"
        assert flow.name.name == "Test"
        assert len(flow.namespaces) == 1


class TestWorkflowAndExecution:
    """Tests for workflow and execution dataclasses."""

    def test_workflow_definition(self):
        """Test WorkflowDefinition dataclass."""
        workflow = WorkflowDefinition(
            uuid="wf-123",
            name="ProcessOrder",
            namespace_id="ns-123",
            facet_id="facet-123",
            flow_id="flow-123",
            starting_step="step-1",
            version="1.0.0",
            documentation="Process an order",
        )
        assert workflow.uuid == "wf-123"
        assert workflow.name == "ProcessOrder"
        assert workflow.date == 0

    def test_runner_definition(self):
        """Test RunnerDefinition dataclass."""
        workflow = WorkflowDefinition(
            uuid="wf-123",
            name="ProcessOrder",
            namespace_id="ns-123",
            facet_id="facet-123",
            flow_id="flow-123",
            starting_step="step-1",
            version="1.0.0",
        )
        runner = RunnerDefinition(
            uuid="runner-123",
            workflow_id="wf-123",
            workflow=workflow,
            parameters=[Parameter(name="orderId", value="ORD-001")],
            state=RunnerState.RUNNING,
        )
        assert runner.uuid == "runner-123"
        assert runner.state == "running"
        assert len(runner.parameters) == 1

    def test_runner_states(self):
        """Test RunnerState constants."""
        assert RunnerState.CREATED == "created"
        assert RunnerState.RUNNING == "running"
        assert RunnerState.COMPLETED == "completed"
        assert RunnerState.FAILED == "failed"
        assert RunnerState.PAUSED == "paused"
        assert RunnerState.CANCELLED == "cancelled"

    def test_task_definition(self):
        """Test TaskDefinition dataclass."""
        task = TaskDefinition(
            uuid="task-123",
            name="SendEmail",
            runner_id="runner-123",
            workflow_id="wf-123",
            flow_id="flow-123",
            step_id="step-1",
            task_list_name="email-tasks",
            data={"to": "test@example.com"},
        )
        assert task.uuid == "task-123"
        assert task.state == TaskState.PENDING
        assert task.data["to"] == "test@example.com"

    def test_task_states(self):
        """Test TaskState constants."""
        assert TaskState.PENDING == "pending"
        assert TaskState.RUNNING == "running"
        assert TaskState.COMPLETED == "completed"
        assert TaskState.FAILED == "failed"
        assert TaskState.IGNORED == "ignored"
        assert TaskState.CANCELED == "canceled"


class TestLogging:
    """Tests for logging dataclasses."""

    def test_log_definition(self):
        """Test LogDefinition dataclass."""
        log = LogDefinition(
            uuid="log-123",
            order=1,
            runner_id="runner-123",
            message="Step completed",
            state="completed",
            note_type=NoteType.INFO,
            note_importance=NoteImportance.NORMAL,
        )
        assert log.uuid == "log-123"
        assert log.message == "Step completed"
        assert log.note_type == "info"
        assert log.note_importance == 5

    def test_note_types(self):
        """Test NoteType constants."""
        assert NoteType.ERROR == "error"
        assert NoteType.INFO == "info"
        assert NoteType.WARNING == "warning"

    def test_note_originators(self):
        """Test NoteOriginator constants."""
        assert NoteOriginator.WORKFLOW == "workflow"
        assert NoteOriginator.AGENT == "agent"

    def test_note_importance(self):
        """Test NoteImportance constants."""
        assert NoteImportance.HIGH == 1
        assert NoteImportance.NORMAL == 5
        assert NoteImportance.LOW == 10


class TestServerAndLocks:
    """Tests for server and lock dataclasses."""

    def test_server_definition(self):
        """Test ServerDefinition dataclass."""
        server = ServerDefinition(
            uuid="server-123",
            server_group="workers",
            service_name="afl-worker",
            server_name="worker-01",
            server_ips=["192.168.1.100"],
            topics=["workflow.events"],
            handlers=["StepHandler", "EventHandler"],
            state=ServerState.RUNNING,
        )
        assert server.uuid == "server-123"
        assert server.state == "running"
        assert "192.168.1.100" in server.server_ips

    def test_server_states(self):
        """Test ServerState constants."""
        assert ServerState.STARTUP == "startup"
        assert ServerState.RUNNING == "running"
        assert ServerState.SHUTDOWN == "shutdown"
        assert ServerState.ERROR == "error"

    def test_handled_count(self):
        """Test HandledCount dataclass."""
        count = HandledCount(handler="StepHandler", handled=100, not_handled=5)
        assert count.handler == "StepHandler"
        assert count.handled == 100
        assert count.not_handled == 5


class TestSerialization:
    """Tests for dataclass serialization."""

    def test_asdict_parameter(self):
        """Test Parameter serializes to dict."""
        param = Parameter(name="count", value=42, type_hint="Long")
        data = asdict(param)
        assert data == {"name": "count", "value": 42, "type_hint": "Long"}

    def test_asdict_flow_identity(self):
        """Test FlowIdentity serializes to dict."""
        identity = FlowIdentity(name="Test", path="/test", uuid="123")
        data = asdict(identity)
        assert data == {"name": "Test", "path": "/test", "uuid": "123"}

    def test_asdict_runner_definition(self):
        """Test RunnerDefinition serializes to dict."""
        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="Test",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="runner-1", workflow_id="wf-1", workflow=workflow, state=RunnerState.RUNNING
        )
        data = asdict(runner)
        assert data["uuid"] == "runner-1"
        assert data["state"] == "running"
        assert data["workflow"]["uuid"] == "wf-1"
