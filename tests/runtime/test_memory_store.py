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

"""Tests for in-memory persistence implementation."""

import pytest

from facetwork.runtime import (
    IterationChanges,
    MemoryStore,
    ObjectType,
    StepDefinition,
    StepLogEntry,
    StepLogLevel,
    StepLogSource,
    block_id,
    generate_id,
    workflow_id,
)


class TestMemoryStore:
    """Tests for MemoryStore persistence."""

    @pytest.fixture
    def store(self):
        """Create a fresh memory store."""
        return MemoryStore()

    @pytest.fixture
    def sample_step(self):
        """Create a sample step."""
        return StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="TestFacet",
        )

    def test_save_and_get_step(self, store, sample_step):
        """Test saving and retrieving a step."""
        store.save_step(sample_step)

        retrieved = store.get_step(sample_step.id)
        assert retrieved is not None
        assert retrieved.id == sample_step.id
        assert retrieved.facet_name == "TestFacet"

    def test_get_returns_copy(self, store, sample_step):
        """Test that get returns a copy, not the original."""
        store.save_step(sample_step)

        retrieved = store.get_step(sample_step.id)
        retrieved.facet_name = "Modified"

        retrieved2 = store.get_step(sample_step.id)
        assert retrieved2.facet_name == "TestFacet"

    def test_get_nonexistent(self, store):
        """Test getting a nonexistent step."""
        result = store.get_step("nonexistent")
        assert result is None

    def test_steps_by_workflow(self, store):
        """Test getting steps by workflow."""
        wf_id = workflow_id()

        step1 = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step2 = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step3 = StepDefinition.create(
            workflow_id=workflow_id(),  # Different workflow
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )

        store.save_step(step1)
        store.save_step(step2)
        store.save_step(step3)

        steps = store.get_steps_by_workflow(wf_id)
        assert len(steps) == 2
        ids = {s.id for s in steps}
        assert step1.id in ids
        assert step2.id in ids

    def test_steps_by_block(self, store):
        """Test getting steps by block."""
        wf_id = workflow_id()
        b_id = block_id()

        step1 = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            block_id=b_id,
        )
        step2 = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            block_id=b_id,
        )
        step3 = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            block_id=block_id(),  # Different block
        )

        store.save_step(step1)
        store.save_step(step2)
        store.save_step(step3)

        steps = store.get_steps_by_block(b_id)
        assert len(steps) == 2

    def test_commit(self, store):
        """Test atomic commit."""
        changes = IterationChanges()

        step1 = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step2 = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )

        changes.add_created_step(step1)
        changes.add_created_step(step2)

        store.commit(changes)

        assert store.step_count() == 2
        assert store.get_step(step1.id) is not None
        assert store.get_step(step2.id) is not None

    def test_step_exists(self, store):
        """Test idempotency check."""
        wf_id = workflow_id()
        b_id = block_id()

        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id=b_id,
        )
        store.save_step(step)

        assert store.step_exists("stmt-1", b_id) is True
        assert store.step_exists("stmt-2", b_id) is False
        assert store.step_exists("stmt-1", block_id()) is False

    def test_workflow_root(self, store):
        """Test getting workflow root."""
        wf_id = workflow_id()

        root = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.WORKFLOW,
        )
        child = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            container_id=root.id,
            root_id=root.id,
        )

        store.save_step(root)
        store.save_step(child)

        retrieved = store.get_workflow_root(wf_id)
        assert retrieved is not None
        assert retrieved.id == root.id

    def test_clear(self, store, sample_step):
        """Test clearing the store."""
        store.save_step(sample_step)
        assert store.step_count() == 1

        store.clear()
        assert store.step_count() == 0


class TestStepLogOperations:
    """Tests for step log persistence operations."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    def test_save_and_get_by_step(self, store):
        """Save a step log and retrieve by step_id."""
        entry = StepLogEntry(
            uuid=generate_id(),
            step_id="step-1",
            workflow_id="wf-1",
            runner_id="runner-1",
            facet_name="ns.Facet",
            source=StepLogSource.FRAMEWORK,
            level=StepLogLevel.INFO,
            message="Task claimed",
            time=1000,
        )
        store.save_step_log(entry)

        logs = store.get_step_logs_by_step("step-1")
        assert len(logs) == 1
        assert logs[0].message == "Task claimed"
        assert logs[0].step_id == "step-1"

    def test_get_by_workflow(self, store):
        """Retrieve step logs by workflow_id."""
        for i, step_id in enumerate(["step-1", "step-2"]):
            store.save_step_log(
                StepLogEntry(
                    uuid=generate_id(),
                    step_id=step_id,
                    workflow_id="wf-1",
                    message=f"Log {i}",
                    time=1000 + i,
                )
            )
        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="step-3",
                workflow_id="wf-other",
                message="Other workflow",
                time=2000,
            )
        )

        logs = store.get_step_logs_by_workflow("wf-1")
        assert len(logs) == 2
        assert all(log.workflow_id == "wf-1" for log in logs)

    def test_ordering_by_time(self, store):
        """Logs are returned ordered by time ascending."""
        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="s1",
                workflow_id="w1",
                message="Third",
                time=3000,
            )
        )
        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="s1",
                workflow_id="w1",
                message="First",
                time=1000,
            )
        )
        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="s1",
                workflow_id="w1",
                message="Second",
                time=2000,
            )
        )

        logs = store.get_step_logs_by_step("s1")
        assert [log_entry.message for log_entry in logs] == ["First", "Second", "Third"]

    def test_empty_results(self, store):
        """Querying for non-existent step/workflow returns empty list."""
        assert store.get_step_logs_by_step("nonexistent") == []
        assert store.get_step_logs_by_workflow("nonexistent") == []

    def test_clear_removes_step_logs(self, store):
        """clear() removes step logs too."""
        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="s1",
                workflow_id="w1",
                message="Test",
                time=1000,
            )
        )
        assert len(store.get_step_logs_by_step("s1")) == 1

        store.clear()
        assert len(store.get_step_logs_by_step("s1")) == 0

    def test_get_step_logs_by_facet(self, store):
        """Retrieve step logs by facet_name, ordered by time descending."""
        for i in range(5):
            store.save_step_log(
                StepLogEntry(
                    uuid=generate_id(),
                    step_id=f"s{i}",
                    workflow_id="w1",
                    facet_name="ns.Facet",
                    message=f"Log {i}",
                    time=1000 + i,
                )
            )
        store.save_step_log(
            StepLogEntry(
                uuid=generate_id(),
                step_id="s99",
                workflow_id="w1",
                facet_name="other.Facet",
                message="Other",
                time=2000,
            )
        )

        logs = store.get_step_logs_by_facet("ns.Facet", limit=3)
        assert len(logs) == 3
        assert logs[0].message == "Log 4"  # most recent first
        assert logs[2].message == "Log 2"

    def test_get_step_logs_by_facet_empty(self, store):
        """Returns empty list for unknown facet."""
        assert store.get_step_logs_by_facet("nonexistent") == []


class TestTasksByFacetName:
    """Tests for get_tasks_by_facet_name."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    def _make_task(self, name, state="pending", uuid=None):
        from facetwork.runtime.entities import TaskDefinition

        return TaskDefinition(
            uuid=uuid or generate_id(),
            name=name,
            runner_id="r-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id="step-1",
            state=state,
            created=1000,
        )

    def test_get_by_facet_name(self, store):
        """Find tasks matching a facet name."""
        store.save_task(self._make_task("ns.FacetA", "running"))
        store.save_task(self._make_task("ns.FacetB", "running"))
        store.save_task(self._make_task("ns.FacetA", "pending"))

        tasks = store.get_tasks_by_facet_name("ns.FacetA")
        assert len(tasks) == 2
        assert all(t.name == "ns.FacetA" for t in tasks)

    def test_filter_by_states(self, store):
        """Filter tasks by state."""
        store.save_task(self._make_task("ns.Facet", "running"))
        store.save_task(self._make_task("ns.Facet", "pending"))
        store.save_task(self._make_task("ns.Facet", "completed"))

        tasks = store.get_tasks_by_facet_name("ns.Facet", states=["running", "pending"])
        assert len(tasks) == 2
        assert {t.state for t in tasks} == {"running", "pending"}

    def test_empty_result(self, store):
        """Returns empty list for unknown facet."""
        assert store.get_tasks_by_facet_name("nonexistent") == []
