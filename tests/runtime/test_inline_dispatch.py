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

"""Integration tests for inline handler dispatch during evaluation.

Tests that workflows with registered handlers complete in a single
execute() call — no PAUSED status, no task created, no polling.
"""

import pytest

from facetwork.runtime import (
    Evaluator,
    ExecutionStatus,
    HandlerRegistration,
    MemoryStore,
    StepState,
    Telemetry,
)
from facetwork.runtime.dispatcher import InMemoryDispatcher
from facetwork.runtime.registry_runner import RegistryRunner, RegistryRunnerConfig

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


# =========================================================================
# AST Fixtures — AddOne (single step)
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


# =========================================================================
# AST Fixtures — Multi-step (Double then Square)
# =========================================================================


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
                "name": "doubled",
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
                "name": "squared",
                "call": {
                    "type": "CallExpr",
                    "target": "Square",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "StepRef", "path": ["doubled", "output"]},
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
                        "value": {"type": "StepRef", "path": ["squared", "output"]},
                    }
                ],
            },
        },
    },
}


# =========================================================================
# AST Fixtures — Three-step pipeline
# =========================================================================


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
            "id": "yield-Pipeline",
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


# =========================================================================
# AST Fixtures — Foreach
# =========================================================================


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


# =========================================================================
# AST Fixtures — Mixed (one registered, one not)
# =========================================================================


MIXED_PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "mixed",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "Local",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "Remote",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        },
    ],
}

MIXED_WORKFLOW_AST = {
    "type": "WorkflowDecl",
    "name": "MixedWorkflow",
    "params": [{"name": "x", "type": "Long"}],
    "returns": [{"name": "result", "type": "Long"}],
    "body": {
        "type": "AndThenBlock",
        "steps": [
            {
                "type": "StepStmt",
                "id": "step-local",
                "name": "local",
                "call": {
                    "type": "CallExpr",
                    "target": "Local",
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
                "id": "step-remote",
                "name": "remote",
                "call": {
                    "type": "CallExpr",
                    "target": "Remote",
                    "args": [
                        {
                            "name": "input",
                            "value": {"type": "StepRef", "path": ["local", "output"]},
                        }
                    ],
                },
            },
        ],
        "yield": {
            "type": "YieldStmt",
            "id": "yield-Mixed",
            "call": {
                "type": "CallExpr",
                "target": "MixedWorkflow",
                "args": [
                    {
                        "name": "result",
                        "value": {"type": "StepRef", "path": ["remote", "output"]},
                    }
                ],
            },
        },
    },
}


# =========================================================================
# TestInlineDispatchAddOne
# =========================================================================


class TestInlineDispatchAddOne:
    """Single-step inline dispatch — mirrors RegistryRunner AddOne tests."""

    def test_single_step_completes_without_pause(self, store, evaluator):
        """AddOne with dispatcher completes in one execute(), no PAUSED."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("AddOne", lambda p: {"output": p["input"] + 1})

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["result"] == 2

    def test_single_step_output_values(self, store, evaluator):
        """Verify step attributes set correctly after inline dispatch."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("AddOne", lambda p: {"output": p["input"] + 10})

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 7},
            program_ast=ADDONE_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        assert result.outputs["result"] == 17

    def test_handler_error_marks_step_error(self, store, evaluator):
        """Exception from handler puts step in STATEMENT_ERROR."""

        def failing_handler(p):
            raise ValueError("boom")

        dispatcher = InMemoryDispatcher()
        dispatcher.register("AddOne", failing_handler)

        _result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        # The step should be in error state
        error_steps = store.get_steps_by_state(StepState.STATEMENT_ERROR)
        assert len(error_steps) >= 1

    def test_no_tasks_created_with_inline_dispatch(self, store, evaluator):
        """No tasks in persistence when inline dispatch succeeds."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("AddOne", lambda p: {"output": p["input"] + 1})

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        # No tasks should have been created
        assert len(store._tasks) == 0


# =========================================================================
# TestInlineDispatchMultiStep
# =========================================================================


class TestInlineDispatchMultiStep:
    """Multi-step workflows completing in single execute()."""

    def test_multi_step_pipeline_single_execute(self, store, evaluator):
        """Double(3)=6, Square(6)=36 => result=36, all in one execute()."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("Double", lambda p: {"output": p["input"] * 2})
        dispatcher.register("Square", lambda p: {"output": p["input"] ** 2})

        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST,
            inputs={"x": 3},
            program_ast=MULTI_STEP_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["result"] == 36

    def test_three_step_pipeline_single_execute(self, store, evaluator):
        """A(5)=15, B(15)=45, C(45)=44 => result=44, all in one execute()."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("StepA", lambda p: {"output": p["input"] + 10})
        dispatcher.register("StepB", lambda p: {"output": p["input"] * 3})
        dispatcher.register("StepC", lambda p: {"output": p["input"] - 1})

        result = evaluator.execute(
            PIPELINE_WORKFLOW_AST,
            inputs={"x": 5},
            program_ast=PIPELINE_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["result"] == 44

    def test_data_flow_between_inline_steps(self, store, evaluator):
        """Verify intermediate values flow correctly between inline steps."""
        call_log = []

        def double_handler(p):
            call_log.append(("Double", p["input"]))
            return {"output": p["input"] * 2}

        def square_handler(p):
            call_log.append(("Square", p["input"]))
            return {"output": p["input"] ** 2}

        dispatcher = InMemoryDispatcher()
        dispatcher.register("Double", double_handler)
        dispatcher.register("Square", square_handler)

        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST,
            inputs={"x": 4},
            program_ast=MULTI_STEP_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        assert result.outputs["result"] == 64  # (4*2)^2

        # Verify handlers were called with correct intermediate values
        assert ("Double", 4) in call_log
        assert ("Square", 8) in call_log


# =========================================================================
# TestInlineDispatchForeach
# =========================================================================


class TestInlineDispatchForeach:
    """Foreach iteration with inline dispatch."""

    def test_foreach_single_item_inline(self, store, evaluator):
        """Foreach with single item, dispatched inline."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("ProcessItem", lambda p: {"output": p["input"] * 10})

        result = evaluator.execute(
            FOREACH_WORKFLOW_AST,
            inputs={"items": [5]},
            program_ast=FOREACH_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["count"] == 50

    def test_foreach_empty(self, store, evaluator):
        """Empty list completes immediately."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("ProcessItem", lambda p: {"output": p["input"] * 10})

        result = evaluator.execute(
            FOREACH_WORKFLOW_AST,
            inputs={"items": []},
            program_ast=FOREACH_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED


# =========================================================================
# TestInlineDispatchFallback
# =========================================================================


class TestInlineDispatchFallback:
    """Tests for fallback to task creation when no inline handler available."""

    def test_no_dispatcher_creates_task(self, store, evaluator):
        """Without dispatcher, existing behavior preserved (PAUSED + task)."""
        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
            # No dispatcher — existing behavior
        )
        assert result.status == ExecutionStatus.PAUSED
        assert len(store._tasks) == 1

    def test_unregistered_facet_creates_task(self, store, evaluator):
        """Dispatcher present but can't handle facet => task created."""
        dispatcher = InMemoryDispatcher()
        # Register handler for wrong facet
        dispatcher.register("SomethingElse", lambda p: {"output": 0})

        result = evaluator.execute(
            ADDONE_WORKFLOW_AST,
            inputs={"x": 1},
            program_ast=ADDONE_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        assert result.status == ExecutionStatus.PAUSED
        assert len(store._tasks) == 1

    def test_mixed_inline_and_task(self, store, evaluator):
        """One facet registered (Local), another not (Remote).
        First step dispatched inline, second creates task."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("Local", lambda p: {"output": p["input"] * 10})
        # "Remote" is NOT registered

        result = evaluator.execute(
            MIXED_WORKFLOW_AST,
            inputs={"x": 5},
            program_ast=MIXED_PROGRAM_AST,
            dispatcher=dispatcher,
        )
        # Should pause at Remote (second step), Local was dispatched inline
        assert result.status == ExecutionStatus.PAUSED
        assert len(store._tasks) == 1
        task = list(store._tasks.values())[0]
        assert "Remote" in task.name


# =========================================================================
# TestInlineDispatchWithRegistryRunner
# =========================================================================


class TestInlineDispatchWithRegistryRunner:
    """Tests that RegistryRunner passes dispatcher to resume."""

    def test_registry_runner_with_inline_dispatch(self, store, evaluator, tmp_path):
        """RegistryRunner passes dispatcher to resume, completing pipeline faster."""
        # Register both handlers
        f_double = tmp_path / "double_handler.py"
        f_double.write_text("def handle(payload):\n    return {'output': payload['input'] * 2}\n")
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="compute.Double",
                module_uri=f"file://{f_double}",
                entrypoint="handle",
            )
        )
        f_square = tmp_path / "square_handler.py"
        f_square.write_text("def handle(payload):\n    return {'output': payload['input'] ** 2}\n")
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="compute.Square",
                module_uri=f"file://{f_square}",
                entrypoint="handle",
            )
        )

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )

        # Execute — pauses at first EVENT_TRANSMIT (Double)
        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST,
            inputs={"x": 3},
            program_ast=MULTI_STEP_PROGRAM_AST,
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, MULTI_STEP_WORKFLOW_AST, program_ast=MULTI_STEP_PROGRAM_AST
        )

        # Only 1 task claimed — Square dispatched inline during resume
        dispatched = runner.poll_once()
        assert dispatched == 1

        # Workflow already completed by inline dispatch
        final = evaluator.resume(
            result.workflow_id, MULTI_STEP_WORKFLOW_AST, MULTI_STEP_PROGRAM_AST
        )
        assert final.success
        assert final.outputs["result"] == 36

    def test_registry_runner_poll_only_unregistered(self, store, evaluator, tmp_path):
        """Tasks only created for facets without handlers."""
        # Only register Double, NOT Square
        f_double = tmp_path / "double_handler.py"
        f_double.write_text("def handle(payload):\n    return {'output': payload['input'] * 2}\n")
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="compute.Double",
                module_uri=f"file://{f_double}",
                entrypoint="handle",
            )
        )

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )

        result = evaluator.execute(
            MULTI_STEP_WORKFLOW_AST,
            inputs={"x": 3},
            program_ast=MULTI_STEP_PROGRAM_AST,
        )
        assert result.status == ExecutionStatus.PAUSED

        runner.cache_workflow_ast(
            result.workflow_id, MULTI_STEP_WORKFLOW_AST, program_ast=MULTI_STEP_PROGRAM_AST
        )

        # Double claimed from queue; resume hits Square with no handler => task created
        dispatched = runner.poll_once()
        assert dispatched == 1

        # Square task should be in the queue (pending or waiting)
        square_tasks = [t for t in store._tasks.values() if "Square" in t.name]
        assert len(square_tasks) >= 1
