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

"""Integration test: standalone AddOne FFL Agent using AgentPoller.

Demonstrates building a minimal FFL Agent service that handles the event facet:

    namespace handlers {
        event AddOne(input: Long) => (output: Long)
    }

The handler reads the `input` attribute, adds 1, and stores the result in `output`.

Use case:
    workflow TestAddOne(x: Long) => (result: Long) andThen {
        step = AddOne(input = $.x)
        yield TestAddOne(result = step.output)
    }

After execution with x=1, the workflow result should be 2.
"""

import pytest

from facetwork.runtime import (
    Evaluator,
    ExecutionStatus,
    MemoryStore,
    StepState,
    Telemetry,
)
from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig

# =========================================================================
# FFL AST definitions
#
# The AST below corresponds to the following FFL source:
#
#     namespace handlers {
#         event AddOne(input: Long) => (output: Long)
#     }
#
#     workflow TestAddOne(x: Long) => (result: Long) andThen {
#         step = AddOne(input = $.x)
#         yield TestAddOne(result = step.output)
#     }
#
# =========================================================================

PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "handlers",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "AddOne",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        },
    ],
}

WORKFLOW_AST = {
    "type": "WorkflowDecl",
    "name": "TestAddOne",
    "params": [{"name": "x", "type": "Long"}],
    "returns": [{"name": "result", "type": "Long"}],
    "body": {
        "type": "AndThenBlock",
        "steps": [
            {
                "type": "StepStmt",
                "id": "step-addone",
                "name": "step",
                "call": {
                    "type": "CallExpr",
                    "target": "AddOne",
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
            "id": "yield-TestAddOne",
            "call": {
                "type": "CallExpr",
                "target": "TestAddOne",
                "args": [
                    {
                        "name": "result",
                        "value": {"type": "StepRef", "path": ["step", "output"]},
                    }
                ],
            },
        },
    },
}


# =========================================================================
# The AddOne handler — this is the agent logic
# =========================================================================


def addone_handler(payload: dict) -> dict:
    """Handle AddOne event: output = input + 1."""
    return {"output": payload["input"] + 1}


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def evaluator(store):
    return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))


@pytest.fixture
def poller(store, evaluator):
    poller = AgentPoller(
        persistence=store,
        evaluator=evaluator,
        config=AgentPollerConfig(service_name="addone-agent"),
    )
    poller.register("handlers.AddOne", addone_handler)
    return poller


# =========================================================================
# Tests
# =========================================================================


class TestAddOneAgent:
    """End-to-end tests for the AddOne agent."""

    def test_addone_input_1_returns_2(self, store, evaluator, poller):
        """AddOne(input=1) => output=2, workflow result=2."""
        result = evaluator.execute(WORKFLOW_AST, inputs={"x": 1}, program_ast=PROGRAM_AST)
        assert result.status == ExecutionStatus.PAUSED

        poller.cache_workflow_ast(result.workflow_id, WORKFLOW_AST)
        dispatched = poller.poll_once()
        assert dispatched == 1

        final = evaluator.resume(result.workflow_id, WORKFLOW_AST, PROGRAM_AST)
        assert final.success
        assert final.status == ExecutionStatus.COMPLETED
        assert final.outputs["result"] == 2

    def test_addone_input_41_returns_42(self, store, evaluator, poller):
        """AddOne(input=41) => output=42, workflow result=42."""
        result = evaluator.execute(WORKFLOW_AST, inputs={"x": 41}, program_ast=PROGRAM_AST)
        assert result.status == ExecutionStatus.PAUSED

        poller.cache_workflow_ast(result.workflow_id, WORKFLOW_AST)
        dispatched = poller.poll_once()
        assert dispatched == 1

        final = evaluator.resume(result.workflow_id, WORKFLOW_AST, PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == 42

    def test_addone_input_0_returns_1(self, store, evaluator, poller):
        """AddOne(input=0) => output=1, workflow result=1."""
        result = evaluator.execute(WORKFLOW_AST, inputs={"x": 0}, program_ast=PROGRAM_AST)
        assert result.status == ExecutionStatus.PAUSED

        poller.cache_workflow_ast(result.workflow_id, WORKFLOW_AST)
        dispatched = poller.poll_once()
        assert dispatched == 1

        final = evaluator.resume(result.workflow_id, WORKFLOW_AST, PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == 1

    def test_addone_negative_input(self, store, evaluator, poller):
        """AddOne(input=-1) => output=0, workflow result=0."""
        result = evaluator.execute(WORKFLOW_AST, inputs={"x": -1}, program_ast=PROGRAM_AST)
        assert result.status == ExecutionStatus.PAUSED

        poller.cache_workflow_ast(result.workflow_id, WORKFLOW_AST)
        poller.poll_once()

        final = evaluator.resume(result.workflow_id, WORKFLOW_AST, PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == 0

    def test_step_output_attribute(self, store, evaluator, poller):
        """After processing, step.output equals input + 1."""
        result = evaluator.execute(WORKFLOW_AST, inputs={"x": 5}, program_ast=PROGRAM_AST)

        # Find the event step before processing
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        step_id = blocked[0].id

        # Verify step has input=5
        step_before = store.get_step(step_id)
        assert step_before.attributes.get_param("input") == 5

        poller.cache_workflow_ast(result.workflow_id, WORKFLOW_AST)
        poller.poll_once()

        # Verify step.output = 6 after agent processing
        step_after = store.get_step(step_id)
        assert step_after.state != StepState.EVENT_TRANSMIT
        assert step_after.attributes.get_return("output") == 6

    def test_server_registers_with_handler(self, store, evaluator, poller):
        """The agent registers as a server with its handler name."""
        # Start/stop to trigger registration
        import threading
        import time

        poller._config.poll_interval_ms = 50
        t = threading.Thread(target=poller.start, daemon=True)
        t.start()

        # Poll for server registration (may take >100ms under load)
        server = None
        for _ in range(20):
            time.sleep(0.05)
            server = store.get_server(poller.server_id)
            if server is not None:
                break
        assert server is not None
        assert "handlers.AddOne" in server.handlers

        poller.stop()
        t.join(timeout=2)

    def test_handler_failure_marks_step_error(self, store, evaluator):
        """If the handler raises, the step transitions to STATEMENT_ERROR."""

        def bad_handler(payload: dict) -> dict:
            raise ValueError("cannot process")

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("handlers.AddOne", bad_handler)

        result = evaluator.execute(WORKFLOW_AST, inputs={"x": 1}, program_ast=PROGRAM_AST)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step_id = blocked[0].id

        poller.poll_once()

        step = store.get_step(step_id)
        assert step.state == StepState.STATEMENT_ERROR
