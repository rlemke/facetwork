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

"""Integration tests for RegistryRunner end-to-end workflow execution.

Tests cover:
- AddOne-equivalent single event facet (mirrors test_addone_agent.py)
- Multi-step workflows with data flow between steps
- Async handler support (mirrors test_agent_poller_async.py)
- Complex resume scenarios (multi-pause/resume cycles)
- Foreach iteration with event facet handlers
"""

import threading

import pytest

from facetwork.runtime import (
    Evaluator,
    ExecutionStatus,
    HandlerRegistration,
    MemoryStore,
    StepState,
    Telemetry,
)
from facetwork.runtime.entities import (
    RunnerDefinition,
    RunnerState,
    TaskState,
    WorkflowDefinition,
)
from facetwork.runtime.registry_runner import RegistryRunner, RegistryRunnerConfig
from facetwork.runtime.types import ObjectType, generate_id

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


def _make_runner(store, evaluator):
    """Create a RegistryRunner with default config."""
    return RegistryRunner(
        persistence=store,
        evaluator=evaluator,
        config=RegistryRunnerConfig(),
    )


def _register_file_handler(store, tmp_path, facet_name, code):
    """Write a handler module to a temp file and register it.

    Purges ``sys.modules`` for the generated module name so that
    ``importlib.import_module`` loads the new code instead of returning
    a stale cached version from a prior test using the same facet name.
    """
    import sys as _sys

    module_name = f"{facet_name.replace('.', '_')}_handler"
    f = tmp_path / f"{module_name}.py"
    f.write_text(code)

    # Evict stale module so the dispatcher reimports from the new file
    _sys.modules.pop(module_name, None)

    reg = HandlerRegistration(
        facet_name=facet_name,
        module_uri=f"file://{f}",
        entrypoint="handle",
    )
    store.save_handler_registration(reg)
    return reg


# =========================================================================
# 1. AddOne-equivalent — single event facet end-to-end
# =========================================================================


ADDONE_PROGRAM_AST = {
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

ADDONE_WORKFLOW_AST = {
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

ADDONE_HANDLER_CODE = """\
def handle(payload):
    return {"output": payload["input"] + 1}
"""


class TestRegistryRunnerAddOne:
    """End-to-end AddOne agent tests using RegistryRunner (mirrors test_addone_agent.py)."""

    def test_addone_input_1_returns_2(self, store, evaluator, tmp_path):
        """AddOne(input=1) => output=2, workflow result=2."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST, inputs={"x": 1}, program_ast=ADDONE_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        dispatched = runner.poll_once()
        assert dispatched == 1

        final = evaluator.resume(result.workflow_id, ADDONE_WORKFLOW_AST, ADDONE_PROGRAM_AST)
        assert final.success
        assert final.status == ExecutionStatus.COMPLETED
        assert final.outputs["result"] == 2

    def test_addone_input_41_returns_42(self, store, evaluator, tmp_path):
        """AddOne(input=41) => output=42, workflow result=42."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST, inputs={"x": 41}, program_ast=ADDONE_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        dispatched = runner.poll_once()
        assert dispatched == 1

        final = evaluator.resume(result.workflow_id, ADDONE_WORKFLOW_AST, ADDONE_PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == 42

    def test_addone_input_0_returns_1(self, store, evaluator, tmp_path):
        """AddOne(input=0) => output=1, workflow result=1."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST, inputs={"x": 0}, program_ast=ADDONE_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        runner.poll_once()

        final = evaluator.resume(result.workflow_id, ADDONE_WORKFLOW_AST, ADDONE_PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == 1

    def test_addone_negative_input(self, store, evaluator, tmp_path):
        """AddOne(input=-1) => output=0, workflow result=0."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST, inputs={"x": -1}, program_ast=ADDONE_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        runner.poll_once()

        final = evaluator.resume(result.workflow_id, ADDONE_WORKFLOW_AST, ADDONE_PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == 0

    def test_step_output_attribute(self, store, evaluator, tmp_path):
        """After processing, step.output equals input + 1."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST, inputs={"x": 5}, program_ast=ADDONE_PROGRAM_AST
        )

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        step_id = blocked[0].id

        step_before = store.get_step(step_id)
        assert step_before.attributes.get_param("input") == 5

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        runner.poll_once()

        step_after = store.get_step(step_id)
        assert step_after.state != StepState.EVENT_TRANSMIT
        assert step_after.attributes.get_return("output") == 6

    def test_handler_failure_marks_step_error(self, store, evaluator, tmp_path):
        """If the handler raises, the step transitions to STATEMENT_ERROR."""
        _register_file_handler(
            store,
            tmp_path,
            "handlers.AddOne",
            "def handle(payload):\n    raise ValueError('cannot process')\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST, inputs={"x": 1}, program_ast=ADDONE_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step_id = blocked[0].id

        runner.poll_once()

        step = store.get_step(step_id)
        assert step.state == StepState.STATEMENT_ERROR

    def test_server_registers_with_handler(self, store, evaluator, tmp_path):
        """The runner registers as a server with its handler name."""
        import threading
        import time

        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(poll_interval_ms=50),
        )

        t = threading.Thread(target=runner.start, daemon=True)
        t.start()
        time.sleep(0.2)

        server = store.get_server(runner.server_id)
        assert server is not None
        assert "handlers.AddOne" in server.handlers

        runner.stop()
        t.join(timeout=2)


# =========================================================================
# 2. Multi-step workflow with data flow between steps
# =========================================================================


# namespace compute {
#     event Double(input: Long) => (output: Long)
#     event Square(input: Long) => (output: Long)
#
#     workflow DoubleAndSquare(x: Long) => (result: Long) andThen {
#         d = Double(input = $.x)
#         s = Square(input = d.output)
#         yield DoubleAndSquare(result = s.output)
#     }
# }

MULTI_STEP_PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "compute",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "Double",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "Square",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        },
    ],
}

MULTI_STEP_WORKFLOW_AST = {
    "type": "WorkflowDecl",
    "name": "DoubleAndSquare",
    "params": [{"name": "x", "type": "Long"}],
    "returns": [{"name": "result", "type": "Long"}],
    "body": {
        "type": "AndThenBlock",
        "steps": [
            {
                "type": "StepStmt",
                "id": "step-double",
                "name": "d",
                "call": {
                    "type": "CallExpr",
                    "target": "Double",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "InputRef", "path": ["x"]},
                        }
                    ],
                },
            },
            {
                "type": "StepStmt",
                "id": "step-square",
                "name": "s",
                "call": {
                    "type": "CallExpr",
                    "target": "Square",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "StepRef", "path": ["d", "output"]},
                        }
                    ],
                },
            },
        ],
        "yield": {
            "type": "YieldStmt",
            "id": "yield-DAS",
            "call": {
                "type": "CallExpr",
                "target": "DoubleAndSquare",
                "args": [
                    {
                        "name": "result",
                        "value": {"type": "StepRef", "path": ["s", "output"]},
                    }
                ],
            },
        },
    },
}


class TestRegistryRunnerMultiStep:
    """Multi-step workflow: Double then Square with data flow between steps."""

    def test_double_then_square(self, store, evaluator, tmp_path):
        """DoubleAndSquare(x=3) => Double(3)=6 => Square(6)=36 => result=36."""
        _register_file_handler(
            store,
            tmp_path,
            "compute.Double",
            "def handle(payload):\n    return {'output': payload['input'] * 2}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "compute.Square",
            "def handle(payload):\n    return {'output': payload['input'] ** 2}\n",
        )
        runner = _make_runner(store, evaluator)

        # Execute — pauses at first EVENT_TRANSMIT (Double)
        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST, inputs={"x": 3}, program_ast=MULTI_STEP_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, MULTI_STEP_WORKFLOW_AST, program_ast=MULTI_STEP_PROGRAM_AST
        )

        # poll_once handles full pipeline: Double claimed from queue,
        # Square dispatched inline during auto-resume (no task created)
        dispatched = runner.poll_once()
        assert dispatched == 1

        # Workflow already completed by auto-resume inside poll_once
        final = evaluator.resume(
            result.workflow_id, MULTI_STEP_WORKFLOW_AST, MULTI_STEP_PROGRAM_AST
        )
        assert final.success
        assert final.status == ExecutionStatus.COMPLETED
        assert final.outputs["result"] == 36  # (3*2)^2 = 36

    def test_multi_step_data_flow_values(self, store, evaluator, tmp_path):
        """Verify intermediate step attributes carry correct values."""
        _register_file_handler(
            store,
            tmp_path,
            "compute.Double",
            "def handle(payload):\n    return {'output': payload['input'] * 2}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "compute.Square",
            "def handle(payload):\n    return {'output': payload['input'] ** 2}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST, inputs={"x": 5}, program_ast=MULTI_STEP_PROGRAM_AST
        )

        # Double step should have input=5
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        double_step_id = blocked[0].id
        assert store.get_step(double_step_id).attributes.get_param("input") == 5

        # Process Double WITHOUT AST cache — runner cannot auto-resume
        runner.poll_once()

        # After Double, output should be 10
        double_step = store.get_step(double_step_id)
        assert double_step.attributes.get_return("output") == 10

        # Manually resume to create Square step
        evaluator.resume(result.workflow_id, MULTI_STEP_WORKFLOW_AST, MULTI_STEP_PROGRAM_AST)

        # Square step should have input=10 (from d.output)
        blocked2 = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked2) == 1
        square_step_id = blocked2[0].id
        assert store.get_step(square_step_id).attributes.get_param("input") == 10

        runner.poll_once()

        # After Square, output should be 100
        square_step = store.get_step(square_step_id)
        assert square_step.attributes.get_return("output") == 100

    def test_multi_step_first_handler_fails(self, store, evaluator, tmp_path):
        """If the first handler fails, the workflow does not proceed to step 2."""
        _register_file_handler(
            store,
            tmp_path,
            "compute.Double",
            "def handle(payload):\n    raise RuntimeError('double failed')\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "compute.Square",
            "def handle(payload):\n    return {'output': payload['input'] ** 2}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST, inputs={"x": 3}, program_ast=MULTI_STEP_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.poll_once()

        # Double step should be in error state
        all_steps = list(store.get_all_steps())
        double_steps = [
            s for s in all_steps if s.facet_name == "Double" or s.facet_name == "compute.Double"
        ]
        assert any(s.state == StepState.STATEMENT_ERROR for s in double_steps)

        # No Square tasks should have been created
        square_blocked = [
            s
            for s in store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            if "Square" in (s.facet_name or "")
        ]
        assert len(square_blocked) == 0

    def test_multi_step_different_inputs(self, store, evaluator, tmp_path):
        """DoubleAndSquare(x=7) => Double(7)=14 => Square(14)=196 => result=196."""
        _register_file_handler(
            store,
            tmp_path,
            "compute.Double",
            "def handle(payload):\n    return {'output': payload['input'] * 2}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "compute.Square",
            "def handle(payload):\n    return {'output': payload['input'] ** 2}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST, inputs={"x": 7}, program_ast=MULTI_STEP_PROGRAM_AST
        )
        runner.cache_workflow_ast(
            result.workflow_id, MULTI_STEP_WORKFLOW_AST, program_ast=MULTI_STEP_PROGRAM_AST
        )

        runner.poll_once()
        evaluator.resume(result.workflow_id, MULTI_STEP_WORKFLOW_AST, MULTI_STEP_PROGRAM_AST)
        runner.poll_once()

        final = evaluator.resume(
            result.workflow_id, MULTI_STEP_WORKFLOW_AST, MULTI_STEP_PROGRAM_AST
        )
        assert final.success
        assert final.outputs["result"] == 196  # (7*2)^2 = 196


# =========================================================================
# 3. Async handler support
# =========================================================================


ASYNC_PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "async_ns",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "AsyncProcess",
                    "params": [{"name": "input", "type": "String"}],
                    "returns": [{"name": "output", "type": "String"}],
                },
            ],
        },
    ],
}

ASYNC_WORKFLOW_AST = {
    "type": "WorkflowDecl",
    "name": "TestAsync",
    "params": [{"name": "x", "type": "String"}],
    "returns": [{"name": "result", "type": "String"}],
    "body": {
        "type": "AndThenBlock",
        "steps": [
            {
                "type": "StepStmt",
                "id": "step-async",
                "name": "s1",
                "call": {
                    "type": "CallExpr",
                    "target": "AsyncProcess",
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
            "id": "yield-TA",
            "call": {
                "type": "CallExpr",
                "target": "TestAsync",
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


class TestRegistryRunnerAsync:
    """Tests for async handler support in RegistryRunner."""

    def test_async_handler_invoked(self, store, evaluator, tmp_path):
        """An async handler is properly invoked via asyncio.run()."""
        _register_file_handler(
            store,
            tmp_path,
            "async_ns.AsyncProcess",
            "import asyncio\n"
            "async def handle(payload):\n"
            "    await asyncio.sleep(0.01)\n"
            "    return {'output': 'processed_' + payload['input']}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ASYNC_WORKFLOW_AST, inputs={"x": "hello"}, program_ast=ASYNC_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ASYNC_WORKFLOW_AST, program_ast=ASYNC_PROGRAM_AST
        )
        dispatched = runner.poll_once()
        assert dispatched == 1

        final = evaluator.resume(result.workflow_id, ASYNC_WORKFLOW_AST, ASYNC_PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == "processed_hello"

    def test_async_handler_exception(self, store, evaluator, tmp_path):
        """An exception in an async handler results in task failure."""
        _register_file_handler(
            store,
            tmp_path,
            "async_ns.AsyncProcess",
            "import asyncio\n"
            "async def handle(payload):\n"
            "    await asyncio.sleep(0.01)\n"
            "    raise ValueError('async handler failed')\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ASYNC_WORKFLOW_AST, inputs={"x": "test"}, program_ast=ASYNC_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step_id = blocked[0].id

        runner.poll_once()

        # Task should be failed
        pending = [t for t in store._tasks.values() if t.step_id == step_id]
        assert any(t.state == TaskState.FAILED for t in pending)

        # Step should be in error state
        step = store.get_step(step_id)
        assert step.state == StepState.STATEMENT_ERROR

    def test_async_handler_with_multiple_awaits(self, store, evaluator, tmp_path):
        """Async handler with multiple await points completes correctly."""
        _register_file_handler(
            store,
            tmp_path,
            "async_ns.AsyncProcess",
            "import asyncio\n"
            "async def handle(payload):\n"
            "    result = payload['input']\n"
            "    await asyncio.sleep(0.01)\n"
            "    result = result.upper()\n"
            "    await asyncio.sleep(0.01)\n"
            "    result = result + '!'\n"
            "    return {'output': result}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ASYNC_WORKFLOW_AST, inputs={"x": "world"}, program_ast=ASYNC_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ASYNC_WORKFLOW_AST, program_ast=ASYNC_PROGRAM_AST
        )
        runner.poll_once()

        final = evaluator.resume(result.workflow_id, ASYNC_WORKFLOW_AST, ASYNC_PROGRAM_AST)
        assert final.success
        assert final.outputs["result"] == "WORLD!"

    def test_update_step_partial_results(self, store, evaluator):
        """update_step() adds return attributes to a step via RegistryRunner."""
        from facetwork.runtime.step import FacetAttributes, StepDefinition
        from facetwork.runtime.types import workflow_id as make_wf_id

        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.TestFacet",
        )
        step.state = StepState.EVENT_TRANSMIT
        step.attributes = FacetAttributes()
        store.save_step(step)

        runner = _make_runner(store, evaluator)
        runner.update_step(step.id, {"partial": "value1"})

        updated = store.get_step(step.id)
        assert updated.attributes.returns is not None
        assert "partial" in updated.attributes.returns
        assert updated.attributes.returns["partial"].value == "value1"

    def test_update_step_type_hints(self, store, evaluator):
        """update_step() infers correct type hints for various Python types."""
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import workflow_id as make_wf_id

        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.TypeTest",
        )
        step.state = StepState.EVENT_TRANSMIT
        store.save_step(step)

        runner = _make_runner(store, evaluator)
        runner.update_step(
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

    def test_update_step_not_found(self, store, evaluator):
        """update_step() raises for non-existent step."""
        runner = _make_runner(store, evaluator)
        with pytest.raises(ValueError, match="not found"):
            runner.update_step("nonexistent-id", {"field": "value"})


# =========================================================================
# 4. Complex resume — multi-pause/resume cycles
# =========================================================================


# namespace pipeline {
#     event StepA(input: Long) => (output: Long)
#     event StepB(input: Long) => (output: Long)
#     event StepC(input: Long) => (output: Long)
#
#     workflow ThreeStepPipeline(x: Long) => (result: Long) andThen {
#         a = StepA(input = $.x)
#         b = StepB(input = a.output)
#         c = StepC(input = b.output)
#         yield ThreeStepPipeline(result = c.output)
#     }
# }

PIPELINE_PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "pipeline",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "StepA",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "StepB",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "StepC",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        },
    ],
}

PIPELINE_WORKFLOW_AST = {
    "type": "WorkflowDecl",
    "name": "ThreeStepPipeline",
    "params": [{"name": "x", "type": "Long"}],
    "returns": [{"name": "result", "type": "Long"}],
    "body": {
        "type": "AndThenBlock",
        "steps": [
            {
                "type": "StepStmt",
                "id": "step-a",
                "name": "a",
                "call": {
                    "type": "CallExpr",
                    "target": "StepA",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "InputRef", "path": ["x"]},
                        }
                    ],
                },
            },
            {
                "type": "StepStmt",
                "id": "step-b",
                "name": "b",
                "call": {
                    "type": "CallExpr",
                    "target": "StepB",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "StepRef", "path": ["a", "output"]},
                        }
                    ],
                },
            },
            {
                "type": "StepStmt",
                "id": "step-c",
                "name": "c",
                "call": {
                    "type": "CallExpr",
                    "target": "StepC",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "StepRef", "path": ["b", "output"]},
                        }
                    ],
                },
            },
        ],
        "yield": {
            "type": "YieldStmt",
            "id": "yield-TSP",
            "call": {
                "type": "CallExpr",
                "target": "ThreeStepPipeline",
                "args": [
                    {
                        "name": "result",
                        "value": {"type": "StepRef", "path": ["c", "output"]},
                    }
                ],
            },
        },
    },
}


class TestRegistryRunnerComplexResume:
    """Three-step pipeline with 3 pause/resume cycles."""

    def _setup_pipeline_handlers(self, store, tmp_path):
        """Register handlers: A adds 10, B multiplies by 3, C subtracts 1."""
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepA",
            "def handle(payload):\n    return {'output': payload['input'] + 10}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepB",
            "def handle(payload):\n    return {'output': payload['input'] * 3}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepC",
            "def handle(payload):\n    return {'output': payload['input'] - 1}\n",
        )

    def test_three_step_pipeline_completes(self, store, evaluator, tmp_path):
        """x=5 => A(5)=15 => B(15)=45 => C(45)=44 => result=44."""
        self._setup_pipeline_handlers(store, tmp_path)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            PIPELINE_WORKFLOW_AST, inputs={"x": 5}, program_ast=PIPELINE_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED
        runner.cache_workflow_ast(
            result.workflow_id, PIPELINE_WORKFLOW_AST, program_ast=PIPELINE_PROGRAM_AST
        )

        # poll_once claims StepA from queue; B and C dispatched inline during auto-resume
        dispatched = runner.poll_once()
        assert dispatched == 1

        # Workflow already completed by auto-resume
        final = evaluator.resume(result.workflow_id, PIPELINE_WORKFLOW_AST, PIPELINE_PROGRAM_AST)
        assert final.success
        assert final.status == ExecutionStatus.COMPLETED
        assert final.outputs["result"] == 44  # (5+10)*3 - 1

    def test_three_step_intermediate_states(self, store, evaluator, tmp_path):
        """Verify each step processes exactly once and in order."""
        self._setup_pipeline_handlers(store, tmp_path)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            PIPELINE_WORKFLOW_AST, inputs={"x": 2}, program_ast=PIPELINE_PROGRAM_AST
        )
        # Do NOT call cache_workflow_ast — runner cannot auto-resume,
        # giving us step-by-step control over each pipeline stage.

        # Cycle 1: StepA processes input=2, output=12
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        assert blocked[0].attributes.get_param("input") == 2

        runner.poll_once()
        step_a = store.get_step(blocked[0].id)
        assert step_a.attributes.get_return("output") == 12

        evaluator.resume(result.workflow_id, PIPELINE_WORKFLOW_AST, PIPELINE_PROGRAM_AST)

        # Cycle 2: StepB processes input=12, output=36
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        assert blocked[0].attributes.get_param("input") == 12

        runner.poll_once()
        step_b = store.get_step(blocked[0].id)
        assert step_b.attributes.get_return("output") == 36

        evaluator.resume(result.workflow_id, PIPELINE_WORKFLOW_AST, PIPELINE_PROGRAM_AST)

        # Cycle 3: StepC processes input=36, output=35
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        assert blocked[0].attributes.get_param("input") == 36

        runner.poll_once()
        step_c = store.get_step(blocked[0].id)
        assert step_c.attributes.get_return("output") == 35

    def test_middle_step_failure_stops_pipeline(self, store, evaluator, tmp_path):
        """If StepB fails, StepC is never created."""
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepA",
            "def handle(payload):\n    return {'output': payload['input'] + 10}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepB",
            "def handle(payload):\n    raise RuntimeError('StepB exploded')\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepC",
            "def handle(payload):\n    return {'output': payload['input'] - 1}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            PIPELINE_WORKFLOW_AST, inputs={"x": 1}, program_ast=PIPELINE_PROGRAM_AST
        )
        runner.cache_workflow_ast(
            result.workflow_id, PIPELINE_WORKFLOW_AST, program_ast=PIPELINE_PROGRAM_AST
        )

        # Cycle 1: StepA succeeds
        runner.poll_once()
        evaluator.resume(result.workflow_id, PIPELINE_WORKFLOW_AST, PIPELINE_PROGRAM_AST)

        # Cycle 2: StepB fails
        runner.poll_once()

        # Verify StepB is in error state
        all_steps = list(store.get_all_steps())
        step_b_candidates = [s for s in all_steps if s.state == StepState.STATEMENT_ERROR]
        assert len(step_b_candidates) >= 1

        # StepC should not be at EVENT_TRANSMIT
        step_c_blocked = [
            s
            for s in store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            if "StepC" in (s.facet_name or "")
        ]
        assert len(step_c_blocked) == 0


# =========================================================================
# 5. Foreach with event facet handlers
# =========================================================================


# namespace batch {
#     event ProcessItem(input: Long) => (output: Long)
#     facet Value(input: Long)
#
#     workflow ProcessAll(items: Json) => (count: Long)
#       andThen foreach r in $.items {
#         v = ProcessItem(input = r)
#         yield ProcessAll(count = v.output)
#       }
# }

FOREACH_PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "batch",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "ProcessItem",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
            ],
        },
    ],
}

FOREACH_WORKFLOW_AST = {
    "type": "WorkflowDecl",
    "name": "ProcessAll",
    "params": [{"name": "items", "type": "Json"}],
    "returns": [{"name": "count", "type": "Long"}],
    "body": {
        "type": "AndThenBlock",
        "foreach": {
            "variable": "r",
            "iterable": {"type": "InputRef", "path": ["items"]},
        },
        "steps": [
            {
                "type": "StepStmt",
                "id": "step-process",
                "name": "v",
                "call": {
                    "type": "CallExpr",
                    "target": "ProcessItem",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "InputRef", "path": ["r"]},
                        }
                    ],
                },
            },
        ],
        "yield": {
            "type": "YieldStmt",
            "id": "yield-PA",
            "call": {
                "type": "CallExpr",
                "target": "ProcessAll",
                "args": [
                    {
                        "name": "count",
                        "value": {"type": "StepRef", "path": ["v", "output"]},
                    }
                ],
            },
        },
    },
}


class TestRegistryRunnerForeach:
    """Foreach iteration with event facet handlers via RegistryRunner."""

    def test_foreach_single_item(self, store, evaluator, tmp_path):
        """Foreach with single item processes and completes."""
        _register_file_handler(
            store,
            tmp_path,
            "batch.ProcessItem",
            "def handle(payload):\n    return {'output': payload['input'] * 10}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            FOREACH_WORKFLOW_AST, inputs={"items": [7]}, program_ast=FOREACH_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, FOREACH_WORKFLOW_AST, program_ast=FOREACH_PROGRAM_AST
        )

        # poll_once handles the single foreach iteration with auto-resume
        total_dispatched = 0
        for _ in range(10):
            d = runner.poll_once()
            total_dispatched += d
            if d == 0:
                break
        assert total_dispatched >= 1

        final = evaluator.resume(result.workflow_id, FOREACH_WORKFLOW_AST, FOREACH_PROGRAM_AST)
        assert final.success

    def test_foreach_multiple_items(self, store, evaluator, tmp_path):
        """Foreach with multiple items creates tasks for each iteration."""
        _register_file_handler(
            store,
            tmp_path,
            "batch.ProcessItem",
            "def handle(payload):\n    return {'output': payload['input'] * 10}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            FOREACH_WORKFLOW_AST, inputs={"items": [1, 2, 3]}, program_ast=FOREACH_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, FOREACH_WORKFLOW_AST, program_ast=FOREACH_PROGRAM_AST
        )

        # Process all tasks — foreach iterations may need multiple poll cycles
        # because auto-resume may create new tasks as sub-blocks progress
        total_dispatched = 0
        for _ in range(10):  # safety limit
            d = runner.poll_once()
            total_dispatched += d
            if d == 0:
                break

        assert total_dispatched >= 3

        final = evaluator.resume(result.workflow_id, FOREACH_WORKFLOW_AST, FOREACH_PROGRAM_AST)
        assert final.success

    def test_foreach_empty_list(self, store, evaluator, tmp_path):
        """Foreach with empty list completes immediately without invoking handlers."""
        _register_file_handler(
            store,
            tmp_path,
            "batch.ProcessItem",
            "def handle(payload):\n    return {'output': payload['input'] * 10}\n",
        )
        _runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            FOREACH_WORKFLOW_AST, inputs={"items": []}, program_ast=FOREACH_PROGRAM_AST
        )
        # Empty foreach should complete without pausing
        assert result.success

    def test_foreach_handler_failure_in_iteration(self, store, evaluator, tmp_path):
        """Handler failure in one foreach iteration is captured."""
        _register_file_handler(
            store,
            tmp_path,
            "batch.ProcessItem",
            "def handle(payload):\n"
            "    if payload['input'] == 2:\n"
            "        raise ValueError('bad item')\n"
            "    return {'output': payload['input'] * 10}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            FOREACH_WORKFLOW_AST, inputs={"items": [1, 2, 3]}, program_ast=FOREACH_PROGRAM_AST
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, FOREACH_WORKFLOW_AST, program_ast=FOREACH_PROGRAM_AST
        )

        # Process all available tasks (auto-resume after each)
        for _ in range(10):
            d = runner.poll_once()
            if d == 0:
                break

        # At least one step should be in error state (the item==2 iteration)
        error_steps = store.get_steps_by_state(StepState.STATEMENT_ERROR)
        assert len(error_steps) >= 1


# =========================================================================
# 6. Runner state completion — runner transitions to COMPLETED after resume
# =========================================================================


def _create_runner_entity(store, workflow_id, runner_id=None):
    """Create and save a RunnerDefinition in RUNNING state for a workflow."""
    if runner_id is None:
        runner_id = generate_id()
    wf_def = WorkflowDefinition(
        uuid=workflow_id,
        name="TestWorkflow",
        namespace_id="ns-1",
        facet_id="f-1",
        flow_id="flow-1",
        starting_step="step-1",
        version="1.0",
    )
    runner = RunnerDefinition(
        uuid=runner_id,
        workflow_id=workflow_id,
        workflow=wf_def,
        state=RunnerState.RUNNING,
        start_time=int(__import__("time").time() * 1000),
    )
    store.save_runner(runner)
    return runner


class TestRegistryRunnerStateCompletion:
    """Tests that runner state transitions to COMPLETED after all events finish."""

    def test_runner_completed_after_single_event(self, store, evaluator, tmp_path):
        """Runner state transitions to COMPLETED when workflow completes via poll_once."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
            runner_id="test-runner-1",
        )
        assert result.status == ExecutionStatus.PAUSED

        # Create a runner entity in RUNNING state
        _runner_entity = _create_runner_entity(store, result.workflow_id, "test-runner-1")

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        dispatched = runner.poll_once()
        assert dispatched == 1

        # Runner state should now be COMPLETED
        updated_runner = store.get_runner("test-runner-1")
        assert updated_runner.state == RunnerState.COMPLETED
        assert updated_runner.end_time > 0
        assert updated_runner.duration > 0

    def test_runner_completed_after_pipeline(self, store, evaluator, tmp_path):
        """Runner transitions to COMPLETED after multi-step pipeline finishes."""
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepA",
            "def handle(payload):\n    return {'output': payload['input'] + 10}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepB",
            "def handle(payload):\n    return {'output': payload['input'] * 3}\n",
        )
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepC",
            "def handle(payload):\n    return {'output': payload['input'] - 1}\n",
        )
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            PIPELINE_WORKFLOW_AST,
            inputs={"x": 5},
            program_ast=PIPELINE_PROGRAM_AST,
            runner_id="pipeline-runner-1",
        )
        assert result.status == ExecutionStatus.PAUSED

        _create_runner_entity(store, result.workflow_id, "pipeline-runner-1")

        runner.cache_workflow_ast(
            result.workflow_id, PIPELINE_WORKFLOW_AST, program_ast=PIPELINE_PROGRAM_AST
        )

        # Process all tasks (auto-resume handles inline dispatch)
        total = 0
        for _ in range(10):
            d = runner.poll_once()
            total += d
            if d == 0:
                break

        updated = store.get_runner("pipeline-runner-1")
        assert updated.state == RunnerState.COMPLETED
        assert updated.end_time > 0

    def test_runner_stays_running_when_paused(self, store, evaluator, tmp_path):
        """Runner stays RUNNING when workflow pauses (not all events done)."""
        _register_file_handler(
            store,
            tmp_path,
            "pipeline.StepA",
            "def handle(payload):\n    return {'output': payload['input'] + 10}\n",
        )
        # Don't register StepB/StepC so we can control step-by-step
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            PIPELINE_WORKFLOW_AST,
            inputs={"x": 5},
            program_ast=PIPELINE_PROGRAM_AST,
            runner_id="partial-runner-1",
        )
        assert result.status == ExecutionStatus.PAUSED
        _create_runner_entity(store, result.workflow_id, "partial-runner-1")

        # Only process StepA — workflow re-pauses waiting for StepB
        runner.poll_once()

        updated = store.get_runner("partial-runner-1")
        assert updated.state == RunnerState.RUNNING

    def test_runner_no_update_without_runner_id(self, store, evaluator, tmp_path):
        """When runner_id is empty on the task, no runner update occurs."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        # Execute without runner_id — task.runner_id will be ""
        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        dispatched = runner.poll_once()
        assert dispatched == 1

        # No runner entity should have been created or updated
        # (get_runner returns None for non-existent IDs)
        assert store.get_runner("") is None


class TestRegistryRunnerConcurrentResumeLock:
    """Tests that per-workflow resume lock prevents concurrent resumes."""

    def test_concurrent_resume_skipped(self, store, evaluator, tmp_path):
        """When resume is already in progress, a second attempt is skipped."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )

        # Manually acquire the resume lock for this workflow
        with runner._resume_locks_lock:
            runner._resume_locks[result.workflow_id] = threading.Lock()
        lock = runner._resume_locks[result.workflow_id]
        lock.acquire()

        try:
            # _resume_workflow should return immediately (lock already held)
            runner._resume_workflow(result.workflow_id)

            # Workflow should still be paused (resume was skipped)
            blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            assert len(blocked) >= 1
        finally:
            lock.release()

    def test_lock_released_after_resume(self, store, evaluator, tmp_path):
        """After resume completes, the lock is released for future resumes."""
        _register_file_handler(store, tmp_path, "handlers.AddOne", ADDONE_HANDLER_CODE)
        runner = _make_runner(store, evaluator)

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
        )
        assert result.status == ExecutionStatus.PAUSED

        # Process the event
        runner.cache_workflow_ast(
            result.workflow_id, ADDONE_WORKFLOW_AST, program_ast=ADDONE_PROGRAM_AST
        )
        runner.poll_once()

        # Lock should be released after poll_once (which calls _resume_workflow)
        lock = runner._resume_locks.get(result.workflow_id)
        if lock:
            assert lock.acquire(blocking=False), "Lock should be available after resume"
            lock.release()
