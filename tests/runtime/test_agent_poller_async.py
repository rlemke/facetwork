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

"""Tests for async AgentPoller functionality.

Tests cover:
- Async handler registration
- Async callback invocation via asyncio.run()
- Mixed sync/async handlers
- update_step() for partial results
"""

import asyncio
import inspect

import pytest

from facetwork.runtime import (
    Evaluator,
    ExecutionStatus,
    MemoryStore,
    StepState,
    Telemetry,
)
from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig
from facetwork.runtime.entities import (
    TaskState,
)
from facetwork.runtime.step import FacetAttributes, StepDefinition
from facetwork.runtime.types import AttributeValue, ObjectType
from facetwork.runtime.types import workflow_id as make_wf_id

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
def config():
    """Default poller config."""
    return AgentPollerConfig()


@pytest.fixture
def poller(store, evaluator, config):
    """AgentPoller with defaults."""
    return AgentPoller(
        persistence=store,
        evaluator=evaluator,
        config=config,
    )


# ---- Workflow AST fixtures ----


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
                "name": "AsyncFacet",
                "params": [{"name": "input", "type": "String"}],
                "returns": [{"name": "output", "type": "String"}],
            },
        ],
    }


@pytest.fixture
def workflow_ast():
    """Simple workflow that calls an async event facet."""
    return {
        "type": "WorkflowDecl",
        "name": "TestWorkflow",
        "params": [{"name": "x", "type": "String"}],
        "returns": [{"name": "result", "type": "String"}],
        "body": {
            "type": "AndThenBlock",
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-s1",
                    "name": "s1",
                    "call": {
                        "type": "CallExpr",
                        "target": "AsyncFacet",
                        "args": [
                            {
                                "name": "input",
                                "value": {"type": "InputRef", "path": ["x"]},
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
                            "value": {"type": "StepRef", "path": ["s1", "output"]},
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
# TestAsyncRegistration
# =========================================================================


class TestAsyncRegistration:
    """Tests for async handler registration."""

    def test_register_async_callback(self, poller):
        """An async handler is stored via register_async."""

        async def async_handler(payload: dict) -> dict:
            return {"output": "async_result"}

        poller.register_async("ns.AsyncEvent", async_handler)
        assert "ns.AsyncEvent" in poller._handlers
        assert inspect.iscoroutinefunction(poller._handlers["ns.AsyncEvent"])

    def test_register_sync_callback(self, poller):
        """A sync handler is stored via register."""

        def sync_handler(payload: dict) -> dict:
            return {"output": "sync_result"}

        poller.register("ns.SyncEvent", sync_handler)
        assert "ns.SyncEvent" in poller._handlers
        assert not inspect.iscoroutinefunction(poller._handlers["ns.SyncEvent"])

    def test_mixed_registration(self, poller):
        """Both sync and async handlers can be registered."""

        async def async_handler(payload: dict) -> dict:
            return {"async": True}

        def sync_handler(payload: dict) -> dict:
            return {"sync": True}

        poller.register_async("ns.AsyncEvent", async_handler)
        poller.register("ns.SyncEvent", sync_handler)

        assert len(poller._handlers) == 2
        assert inspect.iscoroutinefunction(poller._handlers["ns.AsyncEvent"])
        assert not inspect.iscoroutinefunction(poller._handlers["ns.SyncEvent"])


# =========================================================================
# TestAsyncInvocation
# =========================================================================


class TestAsyncInvocation:
    """Tests for async callback invocation."""

    def test_async_handler_invoked(self, store, evaluator, workflow_ast, program_ast):
        """An async handler is properly invoked with asyncio.run()."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": "test_input"}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Find the event-blocked step
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) >= 1
        step = blocked[0]

        # Find the auto-created task
        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        assert len(pending_tasks) == 1
        task = pending_tasks[0]

        # Track invocation
        invoked = {"count": 0, "payload": None}

        async def async_handler(payload: dict) -> dict:
            invoked["count"] += 1
            invoked["payload"] = payload
            await asyncio.sleep(0.01)  # Simulate async work
            return {"output": "async_response"}

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register_async("AsyncFacet", async_handler)
        poller.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = poller.poll_once()
        assert dispatched == 1

        # Verify async handler was invoked
        assert invoked["count"] == 1
        assert invoked["payload"]["input"] == "test_input"
        assert "_step_log" in invoked["payload"]

        # Task should be completed
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED

    def test_async_handler_exception(self, store, evaluator, workflow_ast, program_ast):
        """An exception in async handler results in task failure."""
        _execute_until_paused(evaluator, workflow_ast, {"x": "test"}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        task = pending_tasks[0]

        async def failing_async_handler(payload: dict) -> dict:
            await asyncio.sleep(0.01)
            raise ValueError("async handler failed")

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register_async("AsyncFacet", failing_async_handler)

        dispatched = poller.poll_once()
        assert dispatched == 1

        # Task should be failed
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "async handler failed" in updated_task.error["message"]

        # Step should be in error state
        updated_step = store.get_step(step.id)
        assert updated_step.state == StepState.STATEMENT_ERROR


# =========================================================================
# TestUpdateStep
# =========================================================================


class TestUpdateStep:
    """Tests for update_step() partial results."""

    def test_update_step_adds_returns(self, store, evaluator):
        """update_step() adds return attributes to a step."""
        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.TestFacet",
        )
        step.state = StepState.EVENT_TRANSMIT
        step.attributes = FacetAttributes()
        store.save_step(step)

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )

        # Update with partial result
        poller.update_step(step.id, {"partial": "value1"})

        updated = store.get_step(step.id)
        assert updated.attributes.returns is not None
        assert "partial" in updated.attributes.returns
        assert updated.attributes.returns["partial"].value == "value1"
        assert updated.attributes.returns["partial"].type_hint == "String"

    def test_update_step_merges_returns(self, store, evaluator):
        """update_step() merges with existing returns."""
        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.TestFacet",
        )
        step.state = StepState.EVENT_TRANSMIT
        step.attributes = FacetAttributes(
            returns={"existing": AttributeValue(name="existing", value="old", type_hint="String")}
        )
        store.save_step(step)

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )

        # Update with new partial result
        poller.update_step(step.id, {"new_field": 42})

        updated = store.get_step(step.id)
        assert "existing" in updated.attributes.returns
        assert updated.attributes.returns["existing"].value == "old"
        assert "new_field" in updated.attributes.returns
        assert updated.attributes.returns["new_field"].value == 42
        assert updated.attributes.returns["new_field"].type_hint == "Long"

    def test_update_step_not_found(self, store, evaluator):
        """update_step() raises for non-existent step."""
        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )

        with pytest.raises(ValueError, match="not found"):
            poller.update_step("nonexistent-id", {"field": "value"})

    def test_update_step_type_hints(self, store, evaluator):
        """update_step() infers correct type hints."""
        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.TestFacet",
        )
        step.state = StepState.EVENT_TRANSMIT
        store.save_step(step)

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )

        # Update with various types
        poller.update_step(
            step.id,
            {
                "str_field": "text",
                "int_field": 123,
                "bool_field": True,
                "float_field": 3.14,
                "list_field": [1, 2, 3],
                "dict_field": {"key": "value"},
                "none_field": None,
            },
        )

        updated = store.get_step(step.id)
        returns = updated.attributes.returns
        assert returns["str_field"].type_hint == "String"
        assert returns["int_field"].type_hint == "Long"
        assert returns["bool_field"].type_hint == "Boolean"
        assert returns["float_field"].type_hint == "Double"
        assert returns["list_field"].type_hint == "List"
        assert returns["dict_field"].type_hint == "Map"
        assert returns["none_field"].type_hint == "Any"

    def test_update_step_from_async_handler(self, store, evaluator, workflow_ast, program_ast):
        """update_step() can be called from within an async handler."""
        result = _execute_until_paused(
            evaluator, workflow_ast, {"x": "streaming_test"}, program_ast
        )
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        task = pending_tasks[0]

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )

        async def streaming_handler(payload: dict) -> dict:
            # Simulate streaming with partial updates
            poller.update_step(step.id, {"partial_1": "chunk1"})
            await asyncio.sleep(0.01)
            poller.update_step(step.id, {"partial_2": "chunk2"})
            await asyncio.sleep(0.01)
            return {"output": "final_result"}

        poller.register_async("AsyncFacet", streaming_handler)
        poller.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = poller.poll_once()
        assert dispatched == 1

        # Task should be completed
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED
