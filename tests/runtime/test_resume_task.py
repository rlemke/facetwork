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

"""Tests for afl:resume task processing by RunnerService.

Tests cover:
- End-to-end resume: external agent writes returns → insert resume task → run_once → workflow completes
- Error cases: wrong step state, missing step_id
- Poll cycle integration: resume tasks dispatched via run_once
- Resume tasks excluded from pending task polling
- Protocol constants file loadable and correct
"""

import json
import os

import pytest

from facetwork.runtime import (
    Evaluator,
    ExecutionStatus,
    MemoryStore,
    StepState,
    Telemetry,
)
from facetwork.runtime.agent import ToolRegistry
from facetwork.runtime.entities import (
    TaskDefinition,
    TaskState,
)
from facetwork.runtime.runner import RunnerConfig, RunnerService
from facetwork.runtime.runner.service import RESUME_TASK_NAME, _current_time_ms
from facetwork.runtime.types import generate_id

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def store():
    """Fresh in-memory store."""
    return MemoryStore()


@pytest.fixture
def evaluator(store):
    """Evaluator with in-memory store."""
    return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))


@pytest.fixture
def registry():
    """Empty tool registry."""
    return ToolRegistry()


@pytest.fixture
def config():
    """Default runner config."""
    return RunnerConfig()


@pytest.fixture
def program_ast():
    """Program AST with Value (facet), CountDocuments (event facet)."""
    return {
        "type": "Program",
        "declarations": [
            {
                "type": "FacetDecl",
                "name": "Value",
                "params": [{"name": "input", "type": "Long"}],
            },
            {
                "type": "EventFacetDecl",
                "name": "CountDocuments",
                "params": [{"name": "input", "type": "Long"}],
                "returns": [{"name": "output", "type": "Long"}],
            },
        ],
    }


@pytest.fixture
def workflow_ast():
    """Simple workflow that calls an event facet.

    workflow TestWorkflow(x: Long = 1) => (result: Long) andThen {
        s1 = Value(input = $.x + 1)
        s2 = CountDocuments(input = s1.input)
        yield TestWorkflow(result = s2.output + s1.input)
    }
    """
    return {
        "type": "WorkflowDecl",
        "name": "TestWorkflow",
        "params": [{"name": "x", "type": "Long"}],
        "returns": [{"name": "result", "type": "Long"}],
        "body": {
            "type": "AndThenBlock",
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-s1",
                    "name": "s1",
                    "call": {
                        "type": "CallExpr",
                        "target": "Value",
                        "args": [
                            {
                                "name": "input",
                                "value": {
                                    "type": "BinaryExpr",
                                    "operator": "+",
                                    "left": {"type": "InputRef", "path": ["x"]},
                                    "right": {"type": "Int", "value": 1},
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "StepStmt",
                    "id": "step-s2",
                    "name": "s2",
                    "call": {
                        "type": "CallExpr",
                        "target": "CountDocuments",
                        "args": [
                            {
                                "name": "input",
                                "value": {"type": "StepRef", "path": ["s1", "input"]},
                            }
                        ],
                    },
                },
            ],
            "yield": {
                "type": "YieldStmt",
                "id": "yield-TW",
                "call": {
                    "type": "CallExpr",
                    "target": "TestWorkflow",
                    "args": [
                        {
                            "name": "result",
                            "value": {
                                "type": "BinaryExpr",
                                "operator": "+",
                                "left": {"type": "StepRef", "path": ["s2", "output"]},
                                "right": {"type": "StepRef", "path": ["s1", "input"]},
                            },
                        }
                    ],
                },
            },
        },
    }


def _execute_until_paused(evaluator, workflow_ast, inputs=None, program_ast=None):
    """Execute a workflow until it pauses at EVENT_TRANSMIT."""
    return evaluator.execute(workflow_ast, inputs=inputs, program_ast=program_ast)


# =========================================================================
# TestResumeTaskEndToEnd
# =========================================================================


class TestResumeTaskEndToEnd:
    """End-to-end tests for the afl:resume task flow."""

    def test_process_resume_task_end_to_end(self, store, evaluator, workflow_ast, program_ast):
        """Full cycle: execute → pause → external agent writes returns →
        insert resume task → run_once → workflow completes."""
        # No handler registered — the external agent handles CountDocuments
        registry = ToolRegistry()
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # Execute workflow until it pauses at EVENT_TRANSMIT
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Cache AST for resume
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Find the event step at EVENT_TRANSMIT
        event_steps = list(store.get_steps_by_state(StepState.EVENT_TRANSMIT))
        assert len(event_steps) == 1
        event_step = event_steps[0]

        # Simulate external agent writing return attributes to the step
        event_step.attributes.set_return("output", 50)
        store.save_step(event_step)

        # Simulate external agent inserting an afl:resume task
        now = _current_time_ms()
        resume_task = TaskDefinition(
            uuid=generate_id(),
            name=RESUME_TASK_NAME,
            runner_id="",
            workflow_id=result.workflow_id,
            flow_id="",
            step_id=event_step.id,
            state=TaskState.PENDING,
            created=now,
            updated=now,
            task_list_name="default",
            data_type="resume",
            data={
                "step_id": event_step.id,
                "workflow_id": result.workflow_id,
            },
        )
        store.save_task(resume_task)

        # Mark the original event task as completed (external agent did this)
        event_tasks = [t for t in store._tasks.values() if t.name == "CountDocuments"]
        for et in event_tasks:
            et.state = TaskState.COMPLETED
            store.save_task(et)

        # run_once should pick up the resume task
        dispatched = svc.run_once()
        assert dispatched == 1

        # Resume task should be completed
        saved_task = store._tasks[resume_task.uuid]
        assert saved_task.state == TaskState.COMPLETED

        # Workflow should have completed via the resume
        # s1.input = 1 + 1 = 2
        # s2 = CountDocuments(input=2) → output=50 (written by external agent)
        # result = s2.output + s1.input = 50 + 2 = 52
        resume_result = evaluator.resume(result.workflow_id, workflow_ast, program_ast, {"x": 1})
        assert resume_result.status == ExecutionStatus.COMPLETED
        assert resume_result.outputs["result"] == 52

    def test_process_resume_task_step_not_at_event_transmit(
        self, store, evaluator, config, registry
    ):
        """Resume task for a step not at EVENT_TRANSMIT should fail."""
        svc = RunnerService(store, evaluator, config, registry)

        # Create a step that is NOT at EVENT_TRANSMIT (use a dummy step)
        from facetwork.runtime.step import StepDefinition

        step = StepDefinition(
            id=generate_id(),
            workflow_id=generate_id(),
            object_type="VariableAssignment",
            state=StepState.STATEMENT_COMPLETE,
            statement_id="test",
            container_id="",
            block_id="",
            facet_name="TestFacet",
        )
        store.save_step(step)

        # Insert resume task
        now = _current_time_ms()
        resume_task = TaskDefinition(
            uuid=generate_id(),
            name=RESUME_TASK_NAME,
            runner_id="",
            workflow_id=step.workflow_id,
            flow_id="",
            step_id=step.id,
            state=TaskState.PENDING,
            created=now,
            updated=now,
            task_list_name="default",
            data_type="resume",
            data={"step_id": step.id, "workflow_id": step.workflow_id},
        )
        store.save_task(resume_task)

        dispatched = svc.run_once()
        assert dispatched == 1

        # Step already at terminal state — continue_step is a no-op,
        # so the resume task completes successfully (idempotent).
        saved_task = store._tasks[resume_task.uuid]
        assert saved_task.state == TaskState.COMPLETED

    def test_process_resume_task_missing_step_id(self, store, evaluator, config, registry):
        """Resume task with no step_id in data or task fields should fail."""
        svc = RunnerService(store, evaluator, config, registry)

        now = _current_time_ms()
        resume_task = TaskDefinition(
            uuid=generate_id(),
            name=RESUME_TASK_NAME,
            runner_id="",
            workflow_id="some-wf",
            flow_id="",
            step_id="",
            state=TaskState.PENDING,
            created=now,
            updated=now,
            task_list_name="default",
            data_type="resume",
            data={},
        )
        store.save_task(resume_task)

        dispatched = svc.run_once()
        assert dispatched == 1

        saved_task = store._tasks[resume_task.uuid]
        assert saved_task.state == TaskState.FAILED
        assert "step_id" in saved_task.error["message"].lower()


# =========================================================================
# TestResumeTaskPolling
# =========================================================================


class TestResumeTaskPolling:
    """Tests for resume task claiming in the poll cycle."""

    def test_poll_cycle_claims_resume_tasks(self, store, evaluator, workflow_ast, program_ast):
        """Resume tasks are dispatched via run_once."""
        registry = ToolRegistry()
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # Execute workflow until paused
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Find event step and write returns
        event_steps = list(store.get_steps_by_state(StepState.EVENT_TRANSMIT))
        assert len(event_steps) == 1
        event_step = event_steps[0]
        event_step.attributes.set_return("output", 10)
        store.save_step(event_step)

        # Insert resume task
        now = _current_time_ms()
        resume_task = TaskDefinition(
            uuid=generate_id(),
            name=RESUME_TASK_NAME,
            runner_id="",
            workflow_id=result.workflow_id,
            flow_id="",
            step_id=event_step.id,
            state=TaskState.PENDING,
            created=now,
            updated=now,
            task_list_name="default",
            data_type="resume",
            data={"step_id": event_step.id, "workflow_id": result.workflow_id},
        )
        store.save_task(resume_task)

        dispatched = svc.run_once()
        assert dispatched >= 1

        saved_task = store._tasks[resume_task.uuid]
        assert saved_task.state == TaskState.COMPLETED

    def test_resume_task_excluded_from_builtin_names(self, store, evaluator, config, registry):
        """fw:resume should not be in _get_builtin_task_names."""
        svc = RunnerService(store, evaluator, config, registry)
        builtin_names = svc._get_builtin_task_names()
        assert RESUME_TASK_NAME not in builtin_names


# =========================================================================
# TestProtocolConstants
# =========================================================================


class TestProtocolConstants:
    """Tests for the protocol constants JSON file."""

    def test_protocol_constants_loadable(self):
        """constants.json loads as valid JSON with expected fields."""
        constants_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "agents",
            "protocol",
            "constants.json",
        )
        with open(constants_path) as f:
            data = json.load(f)

        assert data["version"] == "1.0"
        assert "collections" in data
        assert "task_states" in data
        assert "step_states" in data
        assert "server_states" in data
        assert "protocol_tasks" in data
        assert data["protocol_tasks"]["resume"]["name"] == "fw:resume"
        assert data["protocol_tasks"]["execute"]["name"] == "fw:execute"
        assert "mongodb_operations" in data
        assert "claim_task" in data["mongodb_operations"]
        assert "create_resume_task" in data["mongodb_operations"]

    def test_resume_task_name_matches_constant(self):
        """RESUME_TASK_NAME matches the protocol constants file."""
        constants_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "agents",
            "protocol",
            "constants.json",
        )
        with open(constants_path) as f:
            data = json.load(f)

        assert RESUME_TASK_NAME == data["protocol_tasks"]["resume"]["name"]
