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

"""Tests for MongoDB persistence implementation.

By default, tests use mongomock to simulate MongoDB without a real instance.
To run against a real MongoDB server, use the --mongodb flag:

    PYTHONPATH=. pytest tests/runtime/test_mongo_store.py --mongodb

The real server connection uses the AFL config resolution order
(afl.config.json, env vars, or built-in defaults).
"""

import pytest

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

from afl.runtime.entities import (
    FlowDefinition,
    FlowIdentity,
    HandledCount,
    LogDefinition,
    Parameter,
    RunnerDefinition,
    RunnerState,
    ServerDefinition,
    ServerState,
    TaskDefinition,
    TaskState,
    WorkflowDefinition,
)
from afl.runtime.persistence import IterationChanges
from afl.runtime.step import StepDefinition
from afl.runtime.types import StepId, block_id, step_id, workflow_id


def _use_real_mongodb(request) -> bool:
    """Check if --mongodb flag was passed."""
    return request.config.getoption("--mongodb", default=False)


try:
    _PYMONGO_AVAILABLE = bool(__import__("pymongo"))
except ImportError:
    _PYMONGO_AVAILABLE = False

# Skip all tests if neither mongomock nor pymongo is available
pytestmark = pytest.mark.skipif(
    not MONGOMOCK_AVAILABLE and not _PYMONGO_AVAILABLE,
    reason="Neither mongomock nor pymongo installed",
)


@pytest.fixture
def mongo_store(request):
    """Create a MongoStore backed by mongomock or a real MongoDB server.

    Uses mongomock by default. Pass --mongodb to pytest to connect
    to the real server configured via AFL config.
    """
    from afl.runtime.mongo_store import MongoStore

    if _use_real_mongodb(request):
        from afl.config import load_config

        config = load_config()
        store = MongoStore(
            connection_string=config.mongodb.connection_string(),
            database_name="afl_test",
        )
    else:
        client = mongomock.MongoClient()
        store = MongoStore(database_name="afl_test", client=client)

    yield store
    store.drop_database()
    store.close()


class TestStepOperations:
    """Tests for step persistence operations."""

    def test_save_and_get_step(self, mongo_store):
        """Test saving and retrieving a step."""
        wf_id = workflow_id()
        step = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )

        mongo_store.save_step(step)
        retrieved = mongo_store.get_step(step.id)

        assert retrieved is not None
        assert retrieved.id == step.id
        assert retrieved.workflow_id == wf_id
        assert retrieved.object_type == "VariableAssignment"

    def test_get_nonexistent_step(self, mongo_store):
        """Test getting a step that doesn't exist."""
        result = mongo_store.get_step(StepId("nonexistent"))
        assert result is None

    def test_get_steps_by_workflow(self, mongo_store):
        """Test getting all steps for a workflow."""
        wf_id = workflow_id()
        step1 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step2 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )

        mongo_store.save_step(step1)
        mongo_store.save_step(step2)

        steps = mongo_store.get_steps_by_workflow(wf_id)
        assert len(steps) == 2

    def test_get_steps_by_block(self, mongo_store):
        """Test getting all steps in a block."""
        wf_id = workflow_id()
        blk_id = block_id()

        step1 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step1.block_id = blk_id

        step2 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step2.block_id = blk_id

        mongo_store.save_step(step1)
        mongo_store.save_step(step2)

        steps = mongo_store.get_steps_by_block(blk_id)
        assert len(steps) == 2

    def test_step_exists(self, mongo_store):
        """Test checking if a step exists for a statement."""
        wf_id = workflow_id()
        blk_id = block_id()

        step = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step.statement_id = "stmt-123"
        step.block_id = blk_id

        mongo_store.save_step(step)

        assert mongo_store.step_exists("stmt-123", blk_id) is True
        assert mongo_store.step_exists("stmt-456", blk_id) is False
        assert mongo_store.step_exists("stmt-123", None) is False

    def test_block_step_exists(self, mongo_store):
        """Test checking if a block step exists by statement_id and container_id."""
        wf_id = workflow_id()
        container = step_id()

        step = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="AndThen",
            state="state.block.execution.Begin",
        )
        step.statement_id = "block-0"
        step.container_id = container

        mongo_store.save_step(step)

        assert mongo_store.block_step_exists("block-0", container) is True
        assert mongo_store.block_step_exists("block-1", container) is False
        assert mongo_store.block_step_exists("block-0", step_id()) is False


class TestStepDedupIndex:
    """Tests for the step deduplication unique index."""

    def test_duplicate_step_insert_skipped_on_commit(self, mongo_store):
        """DuplicateKeyError on commit is silently skipped."""
        wf_id = workflow_id()
        blk = step_id()
        ctr = step_id()

        # Insert a step directly
        step1 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step1.statement_id = "s1"
        step1.block_id = blk
        step1.container_id = ctr
        mongo_store.save_step(step1)

        # Attempt to commit a second step with the same (statement_id, block_id, container_id)
        step2 = StepDefinition(
            id=step_id(),  # different uuid
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step2.statement_id = "s1"
        step2.block_id = blk
        step2.container_id = ctr

        changes = IterationChanges()
        changes.add_created_step(step2)

        # Should NOT raise — DuplicateKeyError is caught
        mongo_store.commit(changes)

        # Only the first step should exist
        assert mongo_store.get_step(step1.id) is not None
        assert mongo_store.get_step(step2.id) is None

    def test_different_statement_ids_allowed(self, mongo_store):
        """Steps with different statement_ids in the same block are allowed."""
        wf_id = workflow_id()
        blk = step_id()
        ctr = step_id()

        step1 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step1.statement_id = "s1"
        step1.block_id = blk
        step1.container_id = ctr
        mongo_store.save_step(step1)

        step2 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step2.statement_id = "s2"
        step2.block_id = blk
        step2.container_id = ctr

        changes = IterationChanges()
        changes.add_created_step(step2)
        mongo_store.commit(changes)

        assert mongo_store.get_step(step1.id) is not None
        assert mongo_store.get_step(step2.id) is not None

    def test_orphan_task_skipped_when_step_is_duplicate(self, mongo_store):
        """Tasks referencing a skipped duplicate step are not committed."""
        wf_id = workflow_id()
        blk = step_id()
        ctr = step_id()

        # Insert a step directly
        step1 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step1.statement_id = "s1"
        step1.block_id = blk
        step1.container_id = ctr
        mongo_store.save_step(step1)

        # Create a duplicate step + a task that references it
        dup_step = StepDefinition(
            id=step_id(),  # different uuid — will be skipped
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        dup_step.statement_id = "s1"
        dup_step.block_id = blk
        dup_step.container_id = ctr

        orphan_task = TaskDefinition(
            uuid=str(step_id()),
            name="osm.Test",
            runner_id="r1",
            workflow_id=str(wf_id),
            flow_id="f1",
            step_id=str(dup_step.id),
            state=TaskState.PENDING,
            created=1000,
            updated=1000,
            task_list_name="default",
        )

        changes = IterationChanges()
        changes.add_created_step(dup_step)
        changes.add_created_task(orphan_task)
        mongo_store.commit(changes)

        # Step was skipped (duplicate)
        assert mongo_store.get_step(dup_step.id) is None
        # Orphan task should also be skipped
        assert mongo_store.get_task(orphan_task.uuid) is None

    def test_task_committed_when_step_succeeds(self, mongo_store):
        """Tasks referencing a successfully committed step are kept."""
        wf_id = workflow_id()

        step1 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step1.statement_id = "unique-stmt"
        step1.block_id = step_id()
        step1.container_id = step_id()

        task1 = TaskDefinition(
            uuid=str(step_id()),
            name="osm.Test",
            runner_id="r1",
            workflow_id=str(wf_id),
            flow_id="f1",
            step_id=str(step1.id),
            state=TaskState.PENDING,
            created=1000,
            updated=1000,
            task_list_name="default",
        )

        changes = IterationChanges()
        changes.add_created_step(step1)
        changes.add_created_task(task1)
        mongo_store.commit(changes)

        # Both should exist
        assert mongo_store.get_step(step1.id) is not None
        assert mongo_store.get_task(task1.uuid) is not None


class TestCommitOperations:
    """Tests for atomic commit operations."""

    def test_commit_created_steps(self, mongo_store):
        """Test committing newly created steps."""
        wf_id = workflow_id()
        step1 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )
        step2 = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )

        changes = IterationChanges()
        changes.add_created_step(step1)
        changes.add_created_step(step2)

        mongo_store.commit(changes)

        assert mongo_store.get_step(step1.id) is not None
        assert mongo_store.get_step(step2.id) is not None

    def test_commit_updated_steps(self, mongo_store):
        """Test committing updated steps."""
        wf_id = workflow_id()
        step = StepDefinition(
            id=step_id(),
            workflow_id=wf_id,
            object_type="VariableAssignment",
            state="state.facet.initialization.Begin",
        )

        # First save
        mongo_store.save_step(step)

        # Update
        step.state = "state.facet.completion.Complete"
        changes = IterationChanges()
        changes.add_updated_step(step)
        mongo_store.commit(changes)

        retrieved = mongo_store.get_step(step.id)
        assert retrieved.state == "state.facet.completion.Complete"


class TestRunnerOperations:
    """Tests for runner persistence operations."""

    def test_save_and_get_runner(self, mongo_store):
        """Test saving and retrieving a runner."""
        workflow = WorkflowDefinition(
            uuid="wf-123",
            name="TestWorkflow",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="step-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="runner-123",
            workflow_id="wf-123",
            workflow=workflow,
            state=RunnerState.RUNNING,
            parameters=[Parameter(name="input", value="test")],
        )

        mongo_store.save_runner(runner)
        retrieved = mongo_store.get_runner("runner-123")

        assert retrieved is not None
        assert retrieved.uuid == "runner-123"
        assert retrieved.state == RunnerState.RUNNING
        assert len(retrieved.parameters) == 1

    def test_get_runners_by_state(self, mongo_store):
        """Test getting runners by state."""
        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="Test",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )

        runner1 = RunnerDefinition(
            uuid="r-1", workflow_id="wf-1", workflow=workflow, state=RunnerState.RUNNING
        )
        runner2 = RunnerDefinition(
            uuid="r-2", workflow_id="wf-1", workflow=workflow, state=RunnerState.COMPLETED
        )

        mongo_store.save_runner(runner1)
        mongo_store.save_runner(runner2)

        running = mongo_store.get_runners_by_state(RunnerState.RUNNING)
        assert len(running) == 1
        assert running[0].uuid == "r-1"

    def test_runner_compiled_ast_round_trip(self, mongo_store):
        """Test that compiled_ast and workflow_ast survive runner save/load."""
        workflow = WorkflowDefinition(
            uuid="wf-ast-rt",
            name="AstWorkflow",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        program_dict = {
            "declarations": [{"type": "WorkflowDecl", "name": "AstWorkflow", "params": []}]
        }
        wf_ast = {"type": "WorkflowDecl", "name": "AstWorkflow", "params": []}
        runner = RunnerDefinition(
            uuid="r-ast-1",
            workflow_id="wf-ast-rt",
            workflow=workflow,
            state=RunnerState.RUNNING,
            compiled_ast=program_dict,
            workflow_ast=wf_ast,
        )
        mongo_store.save_runner(runner)
        retrieved = mongo_store.get_runner("r-ast-1")

        assert retrieved is not None
        assert retrieved.compiled_ast == program_dict
        assert retrieved.workflow_ast == wf_ast

    def test_runner_compiled_ast_none_for_legacy(self, mongo_store):
        """Test that runners without compiled_ast get None (backward compat)."""
        workflow = WorkflowDefinition(
            uuid="wf-legacy-r",
            name="LegacyRunner",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-legacy-1",
            workflow_id="wf-legacy-r",
            workflow=workflow,
            state=RunnerState.RUNNING,
        )
        mongo_store.save_runner(runner)
        retrieved = mongo_store.get_runner("r-legacy-1")

        assert retrieved is not None
        assert retrieved.compiled_ast is None
        assert retrieved.workflow_ast is None


class TestTaskOperations:
    """Tests for task persistence operations."""

    def test_save_and_get_pending_tasks(self, mongo_store):
        """Test saving and getting pending tasks."""
        task1 = TaskDefinition(
            uuid="task-1",
            name="SendEmail",
            runner_id="runner-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="step-1",
            task_list_name="email-tasks",
            state=TaskState.PENDING,
        )
        task2 = TaskDefinition(
            uuid="task-2",
            name="SendNotification",
            runner_id="runner-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="step-2",
            task_list_name="email-tasks",
            state=TaskState.COMPLETED,
        )

        mongo_store.save_task(task1)
        mongo_store.save_task(task2)

        pending = mongo_store.get_pending_tasks("email-tasks")
        assert len(pending) == 1
        assert pending[0].uuid == "task-1"


class TestLogOperations:
    """Tests for log persistence operations."""

    def test_save_and_get_logs(self, mongo_store):
        """Test saving and getting logs."""
        log1 = LogDefinition(
            uuid="log-1", order=1, runner_id="runner-1", message="Step started", time=1000
        )
        log2 = LogDefinition(
            uuid="log-2", order=2, runner_id="runner-1", message="Step completed", time=2000
        )

        mongo_store.save_log(log1)
        mongo_store.save_log(log2)

        logs = mongo_store.get_logs_by_runner("runner-1")
        assert len(logs) == 2
        # Should be ordered by 'order' field
        assert logs[0].message == "Step started"


class TestNewQueryMethods:
    """Tests for dashboard query methods added to MongoStore."""

    def test_get_all_runners(self, mongo_store):
        """Test getting all runners."""
        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="Test",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        r1 = RunnerDefinition(
            uuid="r-1",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.RUNNING,
            start_time=1000,
        )
        r2 = RunnerDefinition(
            uuid="r-2",
            workflow_id="wf-1",
            workflow=workflow,
            state=RunnerState.COMPLETED,
            start_time=2000,
        )
        mongo_store.save_runner(r1)
        mongo_store.save_runner(r2)

        runners = mongo_store.get_all_runners()
        assert len(runners) == 2

    def test_get_all_runners_limit(self, mongo_store):
        """Test get_all_runners respects limit."""
        workflow = WorkflowDefinition(
            uuid="wf-1",
            name="Test",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        for i in range(5):
            r = RunnerDefinition(
                uuid=f"r-{i}",
                workflow_id="wf-1",
                workflow=workflow,
                state=RunnerState.RUNNING,
            )
            mongo_store.save_runner(r)

        runners = mongo_store.get_all_runners(limit=3)
        assert len(runners) == 3

    def test_get_all_flows(self, mongo_store):
        """Test getting all flows."""
        f1 = FlowDefinition(
            uuid="flow-1",
            name=FlowIdentity(name="Flow1", path="/f1", uuid="flow-1"),
        )
        f2 = FlowDefinition(
            uuid="flow-2",
            name=FlowIdentity(name="Flow2", path="/f2", uuid="flow-2"),
        )
        mongo_store.save_flow(f1)
        mongo_store.save_flow(f2)

        flows = mongo_store.get_all_flows()
        assert len(flows) == 2

    def test_get_all_tasks(self, mongo_store):
        """Test getting all tasks."""
        t1 = TaskDefinition(
            uuid="task-1",
            name="T1",
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="s-1",
            task_list_name="default",
            state=TaskState.PENDING,
        )
        t2 = TaskDefinition(
            uuid="task-2",
            name="T2",
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="s-2",
            task_list_name="default",
            state=TaskState.COMPLETED,
        )
        mongo_store.save_task(t1)
        mongo_store.save_task(t2)

        tasks = mongo_store.get_all_tasks()
        assert len(tasks) == 2

    def test_get_tasks_by_runner(self, mongo_store):
        """Test getting tasks by runner."""
        t1 = TaskDefinition(
            uuid="task-1",
            name="T1",
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="s-1",
            task_list_name="default",
            state=TaskState.PENDING,
        )
        t2 = TaskDefinition(
            uuid="task-2",
            name="T2",
            runner_id="r-2",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="s-2",
            task_list_name="default",
            state=TaskState.PENDING,
        )
        mongo_store.save_task(t1)
        mongo_store.save_task(t2)

        tasks = mongo_store.get_tasks_by_runner("r-1")
        assert len(tasks) == 1
        assert tasks[0].uuid == "task-1"


class TestFlowOperations:
    """Tests for flow persistence operations."""

    def test_save_and_get_flow(self, mongo_store):
        """Test saving and retrieving a flow."""
        flow = FlowDefinition(
            uuid="flow-123", name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-123")
        )

        mongo_store.save_flow(flow)
        retrieved = mongo_store.get_flow("flow-123")

        assert retrieved is not None
        assert retrieved.uuid == "flow-123"
        assert retrieved.name.name == "TestFlow"

    def test_get_flow_by_path(self, mongo_store):
        """Test getting a flow by path."""
        flow = FlowDefinition(
            uuid="flow-123", name=FlowIdentity(name="TestFlow", path="/test/flow", uuid="flow-123")
        )

        mongo_store.save_flow(flow)
        retrieved = mongo_store.get_flow_by_path("/test/flow")

        assert retrieved is not None
        assert retrieved.uuid == "flow-123"

    def test_delete_flow(self, mongo_store):
        """Test deleting a flow."""
        flow = FlowDefinition(
            uuid="flow-123", name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-123")
        )

        mongo_store.save_flow(flow)
        result = mongo_store.delete_flow("flow-123")
        assert result is True

        retrieved = mongo_store.get_flow("flow-123")
        assert retrieved is None

    def test_flow_compiled_ast_round_trip(self, mongo_store):
        """Test that compiled_ast is persisted and retrieved correctly."""
        program_dict = {
            "declarations": [
                {
                    "type": "namespace",
                    "name": "test",
                    "body": [
                        {
                            "type": "workflow",
                            "name": "TestWorkflow",
                            "id": "stmt-abc-123",
                            "params": [{"name": "x", "type": "String"}],
                            "body": {"type": "block", "statements": []},
                        }
                    ],
                }
            ]
        }
        flow = FlowDefinition(
            uuid="flow-ast-1",
            name=FlowIdentity(name="TestFlow", path="/test", uuid="flow-ast-1"),
            compiled_ast=program_dict,
        )
        mongo_store.save_flow(flow)
        retrieved = mongo_store.get_flow("flow-ast-1")

        assert retrieved is not None
        assert retrieved.compiled_ast is not None
        assert retrieved.compiled_ast == program_dict
        # Verify nested structure survives round-trip
        decls = retrieved.compiled_ast["declarations"]
        assert len(decls) == 1
        assert decls[0]["body"][0]["id"] == "stmt-abc-123"

    def test_flow_compiled_ast_none_for_legacy(self, mongo_store):
        """Test that flows without compiled_ast get None (backward compat)."""
        flow = FlowDefinition(
            uuid="flow-legacy",
            name=FlowIdentity(name="LegacyFlow", path="/test", uuid="flow-legacy"),
        )
        mongo_store.save_flow(flow)
        retrieved = mongo_store.get_flow("flow-legacy")

        assert retrieved is not None
        assert retrieved.compiled_ast is None


class TestWorkflowOperations:
    """Tests for workflow persistence operations."""

    def test_save_and_get_workflow(self, mongo_store):
        """Test saving and retrieving a workflow."""
        workflow = WorkflowDefinition(
            uuid="wf-123",
            name="TestWorkflow",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="step-1",
            version="1.0",
            documentation="A test workflow",
        )

        mongo_store.save_workflow(workflow)
        retrieved = mongo_store.get_workflow("wf-123")

        assert retrieved is not None
        assert retrieved.uuid == "wf-123"
        assert retrieved.name == "TestWorkflow"
        assert retrieved.documentation == "A test workflow"

    def test_get_workflows_by_flow(self, mongo_store):
        """Test getting all workflows for a flow."""
        wf1 = WorkflowDefinition(
            uuid="wf-1",
            name="Workflow1",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        wf2 = WorkflowDefinition(
            uuid="wf-2",
            name="Workflow2",
            namespace_id="ns-1",
            facet_id="f-2",
            flow_id="flow-1",
            starting_step="s-2",
            version="1.0",
        )

        mongo_store.save_workflow(wf1)
        mongo_store.save_workflow(wf2)

        workflows = mongo_store.get_workflows_by_flow("flow-1")
        assert len(workflows) == 2


class TestServerOperations:
    """Tests for server persistence operations."""

    def test_save_and_get_server(self, mongo_store):
        """Test saving and retrieving a server."""
        server = ServerDefinition(
            uuid="server-1",
            server_group="workers",
            service_name="afl-worker",
            server_name="worker-01",
            server_ips=["192.168.1.100"],
            topics=["workflow.events"],
            handlers=["StepHandler"],
            handled=[HandledCount(handler="StepHandler", handled=10)],
            state=ServerState.RUNNING,
        )

        mongo_store.save_server(server)
        retrieved = mongo_store.get_server("server-1")

        assert retrieved is not None
        assert retrieved.uuid == "server-1"
        assert retrieved.state == ServerState.RUNNING
        assert len(retrieved.handled) == 1
        assert retrieved.handled[0].handled == 10

    def test_get_servers_by_state(self, mongo_store):
        """Test getting servers by state."""
        server1 = ServerDefinition(
            uuid="s-1",
            server_group="workers",
            service_name="afl",
            server_name="w-1",
            state=ServerState.RUNNING,
        )
        server2 = ServerDefinition(
            uuid="s-2",
            server_group="workers",
            service_name="afl",
            server_name="w-2",
            state=ServerState.SHUTDOWN,
        )

        mongo_store.save_server(server1)
        mongo_store.save_server(server2)

        running = mongo_store.get_servers_by_state(ServerState.RUNNING)
        assert len(running) == 1
        assert running[0].uuid == "s-1"


# =============================================================================
# Orphaned Task Reaper Tests (MongoStore-specific)
# =============================================================================


class TestReapOrphanedTasks:
    """Tests for reap_orphaned_tasks on the MongoStore."""

    def test_no_dead_servers_no_reaping(self, mongo_store):
        """If all servers are healthy, nothing is reaped."""
        import time

        server = ServerDefinition(
            uuid="healthy-1",
            server_group="test",
            service_name="runner",
            server_name="host1",
            state=ServerState.RUNNING,
            ping_time=int(time.time() * 1000),
        )
        mongo_store.save_server(server)

        task = TaskDefinition(
            uuid="task-1",
            name="MyEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        # Manually set server_id on the task doc
        mongo_store._db.tasks.update_one({"uuid": "task-1"}, {"$set": {"server_id": "healthy-1"}})

        reaped = mongo_store.reap_orphaned_tasks()
        assert len(reaped) == 0

        # Task should still be running
        fetched = mongo_store.get_task("task-1")
        assert fetched is not None
        assert fetched.state == TaskState.RUNNING

    def test_dead_server_tasks_reaped(self, mongo_store):
        """Tasks claimed by a dead server are reset to pending."""
        dead_server = ServerDefinition(
            uuid="dead-1",
            server_group="test",
            service_name="runner",
            server_name="host-dead",
            state=ServerState.RUNNING,
            ping_time=1000,  # ancient timestamp — definitely stale
        )
        mongo_store.save_server(dead_server)

        # Two tasks claimed by the dead server
        for i in range(2):
            task = TaskDefinition(
                uuid=f"orphan-{i}",
                name="SomeEvent",
                runner_id="r1",
                workflow_id="w1",
                flow_id="f1",
                step_id=f"step-{i}",
                state=TaskState.RUNNING,
                task_list_name="default",
            )
            mongo_store.save_task(task)
            mongo_store._db.tasks.update_one(
                {"uuid": f"orphan-{i}"}, {"$set": {"server_id": "dead-1"}}
            )

        reaped = mongo_store.reap_orphaned_tasks()
        assert len(reaped) == 2
        # Verify returned task info
        assert all(r["server_id"] == "dead-1" for r in reaped)
        assert {r["step_id"] for r in reaped} == {"step-0", "step-1"}

        # Both tasks should now be pending
        for i in range(2):
            fetched = mongo_store.get_task(f"orphan-{i}")
            assert fetched is not None
            assert fetched.state == TaskState.PENDING

    def test_shutdown_server_tasks_not_reaped(self, mongo_store):
        """Tasks from a gracefully shut-down server are not reaped.

        Only servers with state running/startup AND stale ping are
        considered dead. A server in shutdown state was stopped
        intentionally.
        """
        server = ServerDefinition(
            uuid="shutdown-1",
            server_group="test",
            service_name="runner",
            server_name="host-shutdown",
            state=ServerState.SHUTDOWN,
            ping_time=1000,  # old, but server is shut down
        )
        mongo_store.save_server(server)

        task = TaskDefinition(
            uuid="task-shutdown",
            name="SomeEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        mongo_store._db.tasks.update_one(
            {"uuid": "task-shutdown"}, {"$set": {"server_id": "shutdown-1"}}
        )

        reaped = mongo_store.reap_orphaned_tasks()
        assert len(reaped) == 0

    def test_claim_task_with_server_id(self, mongo_store):
        """claim_task stores server_id on the task document."""
        task = TaskDefinition(
            uuid="task-claim",
            name="MyEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        mongo_store.save_task(task)

        claimed = mongo_store.claim_task(["MyEvent"], server_id="server-xyz")
        assert claimed is not None
        assert claimed.state == TaskState.RUNNING

        # Verify server_id was stored in the document
        doc = mongo_store._db.tasks.find_one({"uuid": "task-claim"})
        assert doc["server_id"] == "server-xyz"

    def test_mixed_dead_and_healthy_servers(self, mongo_store):
        """Only tasks from dead servers are reaped, not healthy ones."""
        import time

        now = int(time.time() * 1000)

        # Healthy server
        mongo_store.save_server(
            ServerDefinition(
                uuid="alive-1",
                server_group="test",
                service_name="runner",
                server_name="host-alive",
                state=ServerState.RUNNING,
                ping_time=now,
            )
        )
        # Dead server
        mongo_store.save_server(
            ServerDefinition(
                uuid="dead-1",
                server_group="test",
                service_name="runner",
                server_name="host-dead",
                state=ServerState.RUNNING,
                ping_time=1000,
            )
        )

        # Task from alive server
        t1 = TaskDefinition(
            uuid="alive-task",
            name="E",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s1",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(t1)
        mongo_store._db.tasks.update_one({"uuid": "alive-task"}, {"$set": {"server_id": "alive-1"}})

        # Task from dead server
        t2 = TaskDefinition(
            uuid="dead-task",
            name="E",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s2",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(t2)
        mongo_store._db.tasks.update_one({"uuid": "dead-task"}, {"$set": {"server_id": "dead-1"}})

        reaped = mongo_store.reap_orphaned_tasks()
        assert len(reaped) == 1
        assert reaped[0]["server_id"] == "dead-1"

        # alive-task still running
        assert mongo_store.get_task("alive-task").state == TaskState.RUNNING
        # dead-task reset to pending
        assert mongo_store.get_task("dead-task").state == TaskState.PENDING

    def test_task_heartbeat_protects_from_reaping(self, mongo_store):
        """Tasks with a recent task_heartbeat are NOT reaped even if server is dead."""
        import time

        now = int(time.time() * 1000)

        # Dead server (stale ping)
        mongo_store.save_server(
            ServerDefinition(
                uuid="dead-hb",
                server_group="test",
                service_name="runner",
                server_name="host-dead-hb",
                state=ServerState.RUNNING,
                ping_time=1000,  # ancient
            )
        )

        # Task with recent task_heartbeat — should be protected
        t1 = TaskDefinition(
            uuid="hb-alive",
            name="LongFilter",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s1",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(t1)
        mongo_store._db.tasks.update_one(
            {"uuid": "hb-alive"},
            {"$set": {"server_id": "dead-hb", "task_heartbeat": now}},
        )

        # Task with NO task_heartbeat — should be reaped
        t2 = TaskDefinition(
            uuid="hb-stale",
            name="OtherEvent",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s2",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(t2)
        mongo_store._db.tasks.update_one(
            {"uuid": "hb-stale"},
            {"$set": {"server_id": "dead-hb", "task_heartbeat": 0}},
        )

        reaped = mongo_store.reap_orphaned_tasks()
        assert len(reaped) == 1
        assert reaped[0]["step_id"] == "s2"

        # Protected task still running
        assert mongo_store.get_task("hb-alive").state == TaskState.RUNNING
        # Stale task reset to pending
        assert mongo_store.get_task("hb-stale").state == TaskState.PENDING

    def test_stale_task_heartbeat_still_reaped(self, mongo_store):
        """Tasks with an OLD task_heartbeat are still reaped."""
        # Dead server
        mongo_store.save_server(
            ServerDefinition(
                uuid="dead-old-hb",
                server_group="test",
                service_name="runner",
                server_name="host-dead-old-hb",
                state=ServerState.RUNNING,
                ping_time=1000,
            )
        )

        # Task with stale heartbeat (older than down_timeout)
        task = TaskDefinition(
            uuid="hb-old",
            name="StaleFilter",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s1",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(task)
        mongo_store._db.tasks.update_one(
            {"uuid": "hb-old"},
            {"$set": {"server_id": "dead-old-hb", "task_heartbeat": 500}},
        )

        reaped = mongo_store.reap_orphaned_tasks()
        assert len(reaped) == 1
        assert reaped[0]["step_id"] == "s1"
        assert mongo_store.get_task("hb-old").state == TaskState.PENDING

    def test_update_task_heartbeat(self, mongo_store):
        """update_task_heartbeat sets the task_heartbeat field."""
        import time

        task = TaskDefinition(
            uuid="hb-update",
            name="MyEvent",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s1",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(task)

        now = int(time.time() * 1000)
        mongo_store.update_task_heartbeat("hb-update", now)

        doc = mongo_store._db.tasks.find_one({"uuid": "hb-update"})
        assert doc["task_heartbeat"] == now

    def test_pending_tasks_pinned_to_dead_server_reaped(self, mongo_store):
        """Pending tasks pinned to a dead server have server_id cleared."""
        dead_server = ServerDefinition(
            uuid="dead-pending",
            server_group="test",
            service_name="runner",
            server_name="host-dead-pending",
            state=ServerState.RUNNING,
            ping_time=1000,  # ancient — definitely stale
        )
        mongo_store.save_server(dead_server)

        task = TaskDefinition(
            uuid="pinned-pending",
            name="SomeEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        mongo_store._db.tasks.update_one(
            {"uuid": "pinned-pending"}, {"$set": {"server_id": "dead-pending"}}
        )

        reaped = mongo_store.reap_orphaned_tasks()
        assert len(reaped) == 1
        assert reaped[0]["step_id"] == "s1"

        # Task should still be pending but with server_id cleared
        fetched = mongo_store.get_task("pinned-pending")
        assert fetched is not None
        assert fetched.state == TaskState.PENDING
        doc = mongo_store._db.tasks.find_one({"uuid": "pinned-pending"})
        assert doc["server_id"] == ""

    def test_dead_servers_marked_shutdown(self, mongo_store):
        """Dead servers are marked as shutdown after reaping."""
        dead_server = ServerDefinition(
            uuid="dead-mark",
            server_group="test",
            service_name="runner",
            server_name="host-dead-mark",
            state=ServerState.RUNNING,
            ping_time=1000,
        )
        mongo_store.save_server(dead_server)

        # Need at least one task to trigger the reaper path
        task = TaskDefinition(
            uuid="task-mark",
            name="E",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s1",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(task)
        mongo_store._db.tasks.update_one(
            {"uuid": "task-mark"}, {"$set": {"server_id": "dead-mark"}}
        )

        mongo_store.reap_orphaned_tasks()

        # Server should now be marked as shutdown
        doc = mongo_store._db.servers.find_one({"uuid": "dead-mark"})
        assert doc["state"] == "shutdown"

    def test_pending_tasks_without_server_id_not_affected(self, mongo_store):
        """Pending tasks with no server_id are not touched by the reaper."""
        dead_server = ServerDefinition(
            uuid="dead-nopin",
            server_group="test",
            service_name="runner",
            server_name="host-dead-nopin",
            state=ServerState.RUNNING,
            ping_time=1000,
        )
        mongo_store.save_server(dead_server)

        # A normal pending task with no server_id
        task = TaskDefinition(
            uuid="free-pending",
            name="FreeEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        mongo_store.save_task(task)

        # Also create a running task on the dead server so reaper activates
        t2 = TaskDefinition(
            uuid="dead-running",
            name="E",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s2",
            state=TaskState.RUNNING,
        )
        mongo_store.save_task(t2)
        mongo_store._db.tasks.update_one(
            {"uuid": "dead-running"}, {"$set": {"server_id": "dead-nopin"}}
        )

        mongo_store.reap_orphaned_tasks()

        # The free pending task should be untouched
        fetched = mongo_store.get_task("free-pending")
        assert fetched.state == TaskState.PENDING


# =============================================================================
# Stuck Task Watchdog Tests
# =============================================================================


class TestReapStuckTasks:
    """Tests for reap_stuck_tasks on the MongoStore."""

    def test_explicit_timeout_exceeded(self, mongo_store):
        """A task with timeout_ms set whose last activity exceeds it is reaped."""
        import time

        now = int(time.time() * 1000)
        task = TaskDefinition(
            uuid="stuck-explicit",
            name="SlowEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        # Set timeout_ms=5000 and updated to 6 seconds ago
        mongo_store._db.tasks.update_one(
            {"uuid": "stuck-explicit"},
            {"$set": {"timeout_ms": 5000, "updated": now - 6000}},
        )

        reaped = mongo_store.reap_stuck_tasks(default_stuck_ms=999_999_999)
        assert len(reaped) == 1
        assert reaped[0]["step_id"] == "s1"
        assert reaped[0]["reason"] == "timeout"

        fetched = mongo_store.get_task("stuck-explicit")
        assert fetched.state == TaskState.PENDING

    def test_explicit_timeout_not_exceeded(self, mongo_store):
        """A task within its timeout_ms is not reaped."""
        import time

        now = int(time.time() * 1000)
        task = TaskDefinition(
            uuid="ok-explicit",
            name="FastEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        mongo_store._db.tasks.update_one(
            {"uuid": "ok-explicit"},
            {"$set": {"timeout_ms": 60000, "updated": now - 1000}},
        )

        reaped = mongo_store.reap_stuck_tasks(default_stuck_ms=999_999_999)
        assert len(reaped) == 0
        assert mongo_store.get_task("ok-explicit").state == TaskState.RUNNING

    def test_heartbeat_protects_from_stuck_reaping(self, mongo_store):
        """A task with a recent task_heartbeat is not reaped even if updated is old."""
        import time

        now = int(time.time() * 1000)
        task = TaskDefinition(
            uuid="hb-protects",
            name="HeartbeatEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        # Explicit timeout: updated is old, but heartbeat is fresh
        mongo_store._db.tasks.update_one(
            {"uuid": "hb-protects"},
            {"$set": {"timeout_ms": 5000, "updated": now - 60000, "task_heartbeat": now - 1000}},
        )

        reaped = mongo_store.reap_stuck_tasks(default_stuck_ms=999_999_999)
        assert len(reaped) == 0
        assert mongo_store.get_task("hb-protects").state == TaskState.RUNNING

    def test_default_stuck_timeout(self, mongo_store):
        """A task without timeout_ms exceeding the default threshold is reaped."""
        import time

        now = int(time.time() * 1000)
        task = TaskDefinition(
            uuid="stuck-default",
            name="AbandonedEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        # timeout_ms=0 (default), updated 5 hours ago, no heartbeat
        mongo_store._db.tasks.update_one(
            {"uuid": "stuck-default"},
            {"$set": {"timeout_ms": 0, "updated": now - 18_000_000}},
        )

        reaped = mongo_store.reap_stuck_tasks(default_stuck_ms=14_400_000)
        assert len(reaped) == 1
        assert reaped[0]["reason"] == "stuck"

        fetched = mongo_store.get_task("stuck-default")
        assert fetched.state == TaskState.PENDING

    def test_default_stuck_not_exceeded(self, mongo_store):
        """A task without timeout_ms within the default threshold is not reaped."""
        import time

        now = int(time.time() * 1000)
        task = TaskDefinition(
            uuid="ok-default",
            name="RecentEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        mongo_store._db.tasks.update_one(
            {"uuid": "ok-default"},
            {"$set": {"timeout_ms": 0, "updated": now - 3_600_000}},  # 1h ago
        )

        reaped = mongo_store.reap_stuck_tasks(default_stuck_ms=14_400_000)
        assert len(reaped) == 0
        assert mongo_store.get_task("ok-default").state == TaskState.RUNNING

    def test_pending_tasks_not_reaped(self, mongo_store):
        """Only running tasks are reaped, not pending ones."""
        import time

        now = int(time.time() * 1000)
        task = TaskDefinition(
            uuid="pending-old",
            name="OldPending",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        mongo_store.save_task(task)
        mongo_store._db.tasks.update_one(
            {"uuid": "pending-old"},
            {"$set": {"timeout_ms": 5000, "updated": now - 60000}},
        )

        reaped = mongo_store.reap_stuck_tasks(default_stuck_ms=1000)
        assert len(reaped) == 0
