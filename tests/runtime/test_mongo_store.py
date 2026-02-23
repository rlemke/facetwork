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
    LockMetaData,
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
from afl.runtime.types import StepId, WorkflowId, block_id, step_id, workflow_id


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
            name="osm.geo.Test",
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
            name="osm.geo.Test",
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


class TestLockOperations:
    """Tests for distributed lock operations."""

    def test_acquire_lock(self, mongo_store):
        """Test acquiring a lock."""
        result = mongo_store.acquire_lock("test-key", 5000)
        assert result is True

    def test_acquire_already_held_lock(self, mongo_store):
        """Test acquiring a lock that's already held."""
        mongo_store.acquire_lock("test-key", 60000)
        result = mongo_store.acquire_lock("test-key", 5000)
        assert result is False

    def test_release_lock(self, mongo_store):
        """Test releasing a lock."""
        mongo_store.acquire_lock("test-key", 5000)
        result = mongo_store.release_lock("test-key")
        assert result is True

        # Should be able to acquire again
        result = mongo_store.acquire_lock("test-key", 5000)
        assert result is True

    def test_release_nonexistent_lock(self, mongo_store):
        """Test releasing a lock that doesn't exist."""
        result = mongo_store.release_lock("nonexistent")
        assert result is False

    def test_check_lock(self, mongo_store):
        """Test checking a lock."""
        meta = LockMetaData(topic="events", step_id="step-1")
        mongo_store.acquire_lock("test-key", 60000, meta)

        lock = mongo_store.check_lock("test-key")
        assert lock is not None
        assert lock.key == "test-key"
        assert lock.meta.topic == "events"

    def test_check_nonexistent_lock(self, mongo_store):
        """Test checking a lock that doesn't exist."""
        lock = mongo_store.check_lock("nonexistent")
        assert lock is None

    def test_extend_lock(self, mongo_store):
        """Test extending a lock."""
        mongo_store.acquire_lock("test-key", 1000)
        result = mongo_store.extend_lock("test-key", 60000)
        assert result is True

        lock = mongo_store.check_lock("test-key")
        assert lock is not None

    def test_extend_nonexistent_lock(self, mongo_store):
        """Test extending a lock that doesn't exist."""
        result = mongo_store.extend_lock("nonexistent", 5000)
        assert result is False


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
