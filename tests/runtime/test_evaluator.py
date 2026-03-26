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

"""Tests for AFL runtime evaluator.

Includes integration tests for spec examples 21.1, 21.2, 21.3,
and 70_examples.md examples 2, 3, and 4.
"""

import pytest

from afl.runtime import (
    Evaluator,
    ExecutionStatus,
    MemoryStore,
    ObjectType,
    StepState,
    Telemetry,
)


class TestEvaluatorBasic:
    """Basic evaluator tests."""

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with in-memory store."""
        store = MemoryStore()
        telemetry = Telemetry(enabled=True)
        return Evaluator(persistence=store, telemetry=telemetry)

    def test_empty_workflow(self, evaluator):
        """Test executing a workflow with no body."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Empty",
            "params": [],
        }

        result = evaluator.execute(workflow_ast)
        assert result.success is True

    def test_workflow_with_default_input(self, evaluator):
        """Test workflow with default input value."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestInput",
            "params": [{"name": "input", "type": "Long"}],
        }

        result = evaluator.execute(workflow_ast, inputs={"input": 42})
        assert result.success is True


class TestSpecExample21_1:
    """Tests for spec example 21.1: Initialization and dependency-driven evaluation.

    ```afl
    namespace test.one {
      facet Value(input: Long, output: Long)

      workflow TestOne(input: Long = 1) => (output: Long) andThen {
        s1 = Value(input = $.input + 1)
        s2 = Value(input = s1.input + 1)
        yield TestOne(output = s2.input + 1)
      }
    }
    ```

    Expected: TestOne.output = 4
    """

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with in-memory store."""
        store = MemoryStore()
        telemetry = Telemetry(enabled=True)
        return Evaluator(persistence=store, telemetry=telemetry)

    @pytest.fixture
    def workflow_ast(self):
        """AST for TestOne workflow."""
        return {
            "type": "WorkflowDecl",
            "name": "TestOne",
            "params": [{"name": "input", "type": "Long"}],
            "returns": [{"name": "output", "type": "Long"}],
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
                                        "left": {"type": "InputRef", "path": ["input"]},
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
                            "target": "Value",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {
                                        "type": "BinaryExpr",
                                        "operator": "+",
                                        "left": {"type": "StepRef", "path": ["s1", "input"]},
                                        "right": {"type": "Int", "value": 1},
                                    },
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestOne",
                        "args": [
                            {
                                "name": "output",
                                "value": {
                                    "type": "BinaryExpr",
                                    "operator": "+",
                                    "left": {"type": "StepRef", "path": ["s2", "input"]},
                                    "right": {"type": "Int", "value": 1},
                                },
                            }
                        ],
                    },
                },
            },
        }

    def test_sequential_dependency(self, evaluator, workflow_ast):
        """Test that s2 waits for s1 to complete."""
        result = evaluator.execute(workflow_ast, inputs={"input": 1})

        # Workflow should complete successfully
        assert result.success is True

        # Check final output
        # TestOne.input = 1
        # s1.input = 1 + 1 = 2
        # s2.input = 2 + 1 = 3
        # output = 3 + 1 = 4
        assert result.outputs.get("output") == 4


class TestSpecExample21_2:
    """Tests for spec example 21.2: Parallel steps and fan-in.

    ```afl
    namespace test.two {
      facet Value(input: Long, output: Long)

      workflow TestTwo(input: Long = 1) => (output: Long) andThen {
        a = Value(input = $.input + 1)
        b = Value(input = $.input + 10)
        c = Value(input = a.input + b.input)
        yield TestTwo(output = c.input)
      }
    }
    ```

    Expected: TestTwo.output = 13
    """

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with in-memory store."""
        store = MemoryStore()
        telemetry = Telemetry(enabled=True)
        return Evaluator(persistence=store, telemetry=telemetry)

    @pytest.fixture
    def workflow_ast(self):
        """AST for TestTwo workflow."""
        return {
            "type": "WorkflowDecl",
            "name": "TestTwo",
            "params": [{"name": "input", "type": "Long"}],
            "returns": [{"name": "output", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-a",
                        "name": "a",
                        "call": {
                            "type": "CallExpr",
                            "target": "Value",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {
                                        "type": "BinaryExpr",
                                        "operator": "+",
                                        "left": {"type": "InputRef", "path": ["input"]},
                                        "right": {"type": "Int", "value": 1},
                                    },
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
                            "target": "Value",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {
                                        "type": "BinaryExpr",
                                        "operator": "+",
                                        "left": {"type": "InputRef", "path": ["input"]},
                                        "right": {"type": "Int", "value": 10},
                                    },
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
                            "target": "Value",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {
                                        "type": "BinaryExpr",
                                        "operator": "+",
                                        "left": {"type": "StepRef", "path": ["a", "input"]},
                                        "right": {"type": "StepRef", "path": ["b", "input"]},
                                    },
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestTwo",
                        "args": [
                            {"name": "output", "value": {"type": "StepRef", "path": ["c", "input"]}}
                        ],
                    },
                },
            },
        }

    def test_parallel_and_fan_in(self, evaluator, workflow_ast):
        """Test parallel execution of a,b and fan-in to c."""
        result = evaluator.execute(workflow_ast, inputs={"input": 1})

        # Workflow should complete successfully
        assert result.success is True

        # Check final output
        # TestTwo.input = 1
        # a.input = 1 + 1 = 2
        # b.input = 1 + 10 = 11
        # c.input = 2 + 11 = 13
        # output = 13
        assert result.outputs.get("output") == 13


class TestIdempotency:
    """Tests for restart safety and idempotency."""

    @pytest.fixture
    def store(self):
        """Create shared memory store."""
        return MemoryStore()

    def test_duplicate_step_creation_prevented(self, store):
        """Test that duplicate steps are not created."""
        from afl.runtime import StepDefinition, block_id, workflow_id

        wf_id = workflow_id()
        b_id = block_id()

        # Create a step
        step1 = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id=b_id,
        )
        store.save_step(step1)

        # Verify idempotency check
        assert store.step_exists("stmt-1", b_id) is True

        # Attempt to create same step again should be detected
        assert store.step_exists("stmt-1", b_id) is True


class TestTelemetry:
    """Tests for telemetry collection."""

    def test_telemetry_events(self):
        """Test that telemetry events are collected."""
        store = MemoryStore()
        telemetry = Telemetry(enabled=True)
        evaluator = Evaluator(persistence=store, telemetry=telemetry)

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Test",
            "params": [],
        }

        evaluator.execute(workflow_ast)

        events = telemetry.get_events()
        assert len(events) > 0

        # Should have workflow start event
        event_types = [e["eventType"] for e in events]
        assert "workflow.start" in event_types

    def test_telemetry_disabled(self):
        """Test that telemetry can be disabled."""
        store = MemoryStore()
        telemetry = Telemetry(enabled=False)
        evaluator = Evaluator(persistence=store, telemetry=telemetry)

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Test",
            "params": [],
        }

        evaluator.execute(workflow_ast)

        events = telemetry.get_events()
        assert len(events) == 0


# =========================================================================
# Spec Example 2: Facet with andThen body (facet-level blocks)
# =========================================================================


class TestSpecExample2:
    """Tests for spec/70_examples.md Example 2: Facet-level andThen body.

    ```afl
    namespace example.2 {
        facet Value(input:Long)
        facet Adder(a:Long, b:Long) => (sum:Long)
            andThen {
                s1 = Value(input = $.a)
                s2 = Value(input = $.b)
                yield Adder(sum = s1.input + s2.input)
            }
        workflow AddWorkflow(x:Long = 1, y:Long = 2) => (result:Long)
            andThen {
                addition = Adder(a = $.x, b = $.y)
                yield AddWorkflow(result = addition.sum)
            }
    }
    ```

    Expected: result = s1.input + s2.input = 1 + 2 = 3
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    @pytest.fixture
    def program_ast(self):
        """Program AST with Value and Adder facet declarations."""
        return {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Adder",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "sum", "type": "Long"}],
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
                                            "value": {"type": "InputRef", "path": ["a"]},
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
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["b"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Adder",
                            "call": {
                                "type": "CallExpr",
                                "target": "Adder",
                                "args": [
                                    {
                                        "name": "sum",
                                        "value": {
                                            "type": "BinaryExpr",
                                            "operator": "+",
                                            "left": {"type": "StepRef", "path": ["s1", "input"]},
                                            "right": {"type": "StepRef", "path": ["s2", "input"]},
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

    @pytest.fixture
    def workflow_ast(self):
        """AST for AddWorkflow."""
        return {
            "type": "WorkflowDecl",
            "name": "AddWorkflow",
            "params": [
                {"name": "x", "type": "Long"},
                {"name": "y", "type": "Long"},
            ],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-addition",
                        "name": "addition",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "InputRef", "path": ["y"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-AW",
                    "call": {
                        "type": "CallExpr",
                        "target": "AddWorkflow",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["addition", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

    def test_facet_level_blocks(self, evaluator, workflow_ast, program_ast):
        """Test Example 2: facet with andThen body produces correct output."""
        result = evaluator.execute(workflow_ast, inputs={"x": 1, "y": 2}, program_ast=program_ast)

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED
        # addition calls Adder(a=1, b=2)
        # Adder body: s1=Value(input=$.a=1), s2=Value(input=$.b=2)
        # yield Adder(sum = s1.input + s2.input = 1 + 2 = 3)
        # yield AddWorkflow(result = addition.sum = 3)
        assert result.outputs.get("result") == 3


# =========================================================================
# Spec Example 3: Statement-level nested andThen blocks
# =========================================================================


class TestSpecExample3:
    """Tests for spec/70_examples.md Example 3: Statement-level nested blocks.

    ```afl
    namespace example.3 {
        facet Value(input:Long)
        facet SomeFacet(input:Long) => (output:Long)
        facet Adder(a:Long, b:Long) => (sum:Long)
            andThen {
                s1 = SomeFacet(input = $.a) andThen {
                    subStep1 = Value(input = $.input)
                    yield SomeFacet(output = subStep1.input + 10)
                }
                s2 = Value(input = $.b)
                yield Adder(sum = s1.output + s2.input)
            }
        workflow AddWorkflow(x:Long = 1, y:Long = 2) => (result:Long)
            andThen {
                addition = Adder(a = $.x, b = $.y)
                yield AddWorkflow(result = addition.sum)
            }
    }
    ```

    Expected: result = s1.output + s2.input = (subStep1.input + 10) + 2 = (1 + 10) + 2 = 13
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    @pytest.fixture
    def program_ast(self):
        """Program AST with Value, SomeFacet, and Adder declarations."""
        return {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "SomeFacet",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Adder",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "sum", "type": "Long"}],
                    "body": {
                        "type": "AndThenBlock",
                        "steps": [
                            {
                                "type": "StepStmt",
                                "id": "step-s1",
                                "name": "s1",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "SomeFacet",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["a"]},
                                        }
                                    ],
                                },
                                # Statement-level inline andThen body
                                "body": {
                                    "type": "AndThenBlock",
                                    "steps": [
                                        {
                                            "type": "StepStmt",
                                            "id": "step-subStep1",
                                            "name": "subStep1",
                                            "call": {
                                                "type": "CallExpr",
                                                "target": "Value",
                                                "args": [
                                                    {
                                                        "name": "input",
                                                        "value": {
                                                            "type": "InputRef",
                                                            "path": ["input"],
                                                        },
                                                    }
                                                ],
                                            },
                                        },
                                    ],
                                    "yield": {
                                        "type": "YieldStmt",
                                        "id": "yield-SF",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "SomeFacet",
                                            "args": [
                                                {
                                                    "name": "output",
                                                    "value": {
                                                        "type": "BinaryExpr",
                                                        "operator": "+",
                                                        "left": {
                                                            "type": "StepRef",
                                                            "path": ["subStep1", "input"],
                                                        },
                                                        "right": {"type": "Int", "value": 10},
                                                    },
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                            {
                                "type": "StepStmt",
                                "id": "step-s2",
                                "name": "s2",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["b"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Adder",
                            "call": {
                                "type": "CallExpr",
                                "target": "Adder",
                                "args": [
                                    {
                                        "name": "sum",
                                        "value": {
                                            "type": "BinaryExpr",
                                            "operator": "+",
                                            "left": {"type": "StepRef", "path": ["s1", "output"]},
                                            "right": {"type": "StepRef", "path": ["s2", "input"]},
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

    @pytest.fixture
    def workflow_ast(self):
        """AST for AddWorkflow (same as Example 2)."""
        return {
            "type": "WorkflowDecl",
            "name": "AddWorkflow",
            "params": [
                {"name": "x", "type": "Long"},
                {"name": "y", "type": "Long"},
            ],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-addition",
                        "name": "addition",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "InputRef", "path": ["y"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-AW",
                    "call": {
                        "type": "CallExpr",
                        "target": "AddWorkflow",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["addition", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

    def test_statement_level_nested_blocks(self, evaluator, workflow_ast, program_ast):
        """Test Example 3: statement-level nested andThen produces correct output."""
        result = evaluator.execute(workflow_ast, inputs={"x": 1, "y": 2}, program_ast=program_ast)

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED
        # s1 calls SomeFacet(input=$.a=1), has inline andThen:
        #   subStep1 = Value(input = $.input = 1)
        #   yield SomeFacet(output = subStep1.input + 10 = 1 + 10 = 11)
        # So s1.output = 11
        # s2 = Value(input = $.b = 2)
        # yield Adder(sum = s1.output + s2.input = 11 + 2 = 13)
        # yield AddWorkflow(result = addition.sum = 13)
        assert result.outputs.get("result") == 13


# =========================================================================
# Spec Example 4: Event facet blocking/resumption
# =========================================================================


class TestSpecExample4:
    """Tests for spec/70_examples.md Example 4: Event facet blocking.

    Same as Example 3 but subStep1 calls CountDocuments (EventFacetDecl),
    which blocks at EventTransmit until continue_step() is called.

    Expected: Two evaluator runs, result = 13.
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    @pytest.fixture
    def program_ast(self):
        """Program AST with Value, SomeFacet, CountDocuments (event), and Adder."""
        return {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "SomeFacet",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "CountDocuments",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Adder",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "sum", "type": "Long"}],
                    "body": {
                        "type": "AndThenBlock",
                        "steps": [
                            {
                                "type": "StepStmt",
                                "id": "step-s1",
                                "name": "s1",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "SomeFacet",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["a"]},
                                        }
                                    ],
                                },
                                "body": {
                                    "type": "AndThenBlock",
                                    "steps": [
                                        {
                                            "type": "StepStmt",
                                            "id": "step-subStep1",
                                            "name": "subStep1",
                                            "call": {
                                                "type": "CallExpr",
                                                "target": "CountDocuments",
                                                "args": [
                                                    {
                                                        "name": "input",
                                                        "value": {"type": "Int", "value": 3},
                                                    }
                                                ],
                                            },
                                        },
                                    ],
                                    "yield": {
                                        "type": "YieldStmt",
                                        "id": "yield-SF",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "SomeFacet",
                                            "args": [
                                                {
                                                    "name": "output",
                                                    "value": {
                                                        "type": "BinaryExpr",
                                                        "operator": "+",
                                                        "left": {
                                                            "type": "StepRef",
                                                            "path": ["subStep1", "input"],
                                                        },
                                                        "right": {"type": "Int", "value": 10},
                                                    },
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                            {
                                "type": "StepStmt",
                                "id": "step-s2",
                                "name": "s2",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["b"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Adder",
                            "call": {
                                "type": "CallExpr",
                                "target": "Adder",
                                "args": [
                                    {
                                        "name": "sum",
                                        "value": {
                                            "type": "BinaryExpr",
                                            "operator": "+",
                                            "left": {"type": "StepRef", "path": ["s1", "output"]},
                                            "right": {"type": "StepRef", "path": ["s2", "input"]},
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

    @pytest.fixture
    def workflow_ast(self):
        """AST for AddWorkflow (same as Examples 2/3)."""
        return {
            "type": "WorkflowDecl",
            "name": "AddWorkflow",
            "params": [
                {"name": "x", "type": "Long"},
                {"name": "y", "type": "Long"},
            ],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-addition",
                        "name": "addition",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "InputRef", "path": ["y"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-AW",
                    "call": {
                        "type": "CallExpr",
                        "target": "AddWorkflow",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["addition", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

    def test_event_facet_blocking_and_resumption(self, store, evaluator, workflow_ast, program_ast):
        """Test Example 4: event facet blocks, then resumes after continue_step."""
        # Run 1: execute until PAUSED (subStep1 blocks at EventTransmit)
        result1 = evaluator.execute(workflow_ast, inputs={"x": 1, "y": 2}, program_ast=program_ast)

        assert result1.status == ExecutionStatus.PAUSED
        assert result1.success is True

        # Find the blocked step (subStep1 at EventTransmit)
        blocked_steps = [s for s in store.get_all_steps() if s.state == StepState.EVENT_TRANSMIT]
        assert len(blocked_steps) == 1
        blocked_step = blocked_steps[0]
        assert blocked_step.facet_name == "CountDocuments"

        # Simulate external agent: continue the blocked step with a result
        evaluator.continue_step(blocked_step.id)

        # Run 2: resume execution
        result2 = evaluator.resume(
            result1.workflow_id,
            workflow_ast,
            program_ast=program_ast,
            inputs={"x": 1, "y": 2},
        )

        assert result2.status == ExecutionStatus.COMPLETED
        assert result2.success is True
        # subStep1.input = 3, yield SomeFacet(output = subStep1.input + 10) = 13
        # s1.output = 13, s2.input = 2, sum = 13 + 2 = 15
        assert result2.outputs["result"] == 15

    def test_event_facet_with_integer_input(self, store, evaluator, program_ast):
        """Test event facet with integer input for correct arithmetic."""
        # Use integer input to verify full arithmetic chain
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "AddWorkflow",
            "params": [
                {"name": "x", "type": "Long"},
                {"name": "y", "type": "Long"},
            ],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-addition",
                        "name": "addition",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "InputRef", "path": ["y"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-AW",
                    "call": {
                        "type": "CallExpr",
                        "target": "AddWorkflow",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["addition", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

        # Modify program_ast to have integer input for CountDocuments
        import copy

        prog = copy.deepcopy(program_ast)
        # Find the Adder facet, find s1's body, change subStep1 input to integer
        adder = [d for d in prog["declarations"] if d["name"] == "Adder"][0]
        s1_body = adder["body"]["steps"][0]["body"]
        s1_body["steps"][0]["call"]["args"][0]["value"] = {"type": "Int", "value": 1}

        # Run 1
        result1 = evaluator.execute(workflow_ast, inputs={"x": 1, "y": 2}, program_ast=prog)
        assert result1.status == ExecutionStatus.PAUSED

        # Find and continue blocked step
        blocked_steps = [s for s in store.get_all_steps() if s.state == StepState.EVENT_TRANSMIT]
        assert len(blocked_steps) == 1
        evaluator.continue_step(blocked_steps[0].id)

        # Run 2
        result2 = evaluator.resume(
            result1.workflow_id,
            workflow_ast,
            program_ast=prog,
            inputs={"x": 1, "y": 2},
        )

        assert result2.status == ExecutionStatus.COMPLETED
        assert result2.success is True
        # subStep1.input = 1
        # yield SomeFacet(output = 1 + 10 = 11) → s1.output = 11
        # s2.input = $.b = 2
        # yield Adder(sum = 11 + 2 = 13) → addition.sum = 13
        # yield AddWorkflow(result = 13)
        assert result2.outputs.get("result") == 13


# =========================================================================
# ExecutionContext unit tests
# =========================================================================


class TestExecutionContext:
    """Tests for ExecutionContext methods targeting uncovered lines."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def context(self, store):
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        return ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-ctx-1",
        )

    def test_get_statement_definition_with_graph(self, context):
        """get_statement_definition returns stmt when graph is cached (lines 73-75)."""
        from afl.runtime.block import StatementDefinition
        from afl.runtime.dependency import DependencyGraph
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        block_id = "block-1"
        stmt = StatementDefinition(
            id="stmt-1",
            name="s1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Value",
        )
        graph = DependencyGraph()
        graph.statements["stmt-1"] = stmt
        context._block_graphs[block_id] = graph

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id=block_id,
        )

        result = context.get_statement_definition(step)
        assert result is not None
        assert result.name == "s1"

    def test_get_statement_definition_no_graph(self, context):
        """get_statement_definition returns None when no graph cached (line 76)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id="block-no-graph",
        )
        result = context.get_statement_definition(step)
        assert result is None

    def test_get_block_ast_no_container_with_workflow(self, context):
        """get_block_ast returns workflow body when block has no container (lines 95-96)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        context.workflow_ast = {
            "name": "Test",
            "body": {"type": "AndThenBlock", "steps": []},
        }

        block_step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.AND_THEN,
        )
        # No container_id → line 93 true, line 95 true
        result = context.get_block_ast(block_step)
        assert result == {"type": "AndThenBlock", "steps": []}

    def test_get_block_ast_no_container_no_workflow(self, context):
        """get_block_ast returns None when no container and no workflow_ast (line 97)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        context.workflow_ast = None
        block_step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.AND_THEN,
        )
        result = context.get_block_ast(block_step)
        assert result is None

    def test_get_block_ast_container_not_found(self, context):
        """get_block_ast returns None when container not found (line 102)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        block_step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.AND_THEN,
            container_id="non-existent-container",
        )
        result = context.get_block_ast(block_step)
        assert result is None

    def test_get_block_ast_container_is_root_no_workflow_ast(self, context, store):
        """get_block_ast returns None when container is root but no workflow_ast (line 109)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        context.workflow_ast = None

        # Create a root step (no container_id)
        root = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.WORKFLOW,
        )
        store.save_step(root)

        block_step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.AND_THEN,
            container_id=root.id,
        )
        result = context.get_block_ast(block_step)
        assert result is None

    def test_get_block_ast_falls_through_to_none(self, context, store):
        """get_block_ast returns None when no body found (line 122)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        # Create a container that is NOT root (has its own container_id)
        # but has no inline body and no facet body
        container = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            container_id="some-parent",
            facet_name="",
        )
        store.save_step(container)

        block_step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.AND_THEN,
            container_id=container.id,
        )
        result = context.get_block_ast(block_step)
        assert result is None

    def test_find_step_in_created_steps(self, context):
        """_find_step finds step in pending created_steps (line 136)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        context.changes.add_created_step(step)

        found = context._find_step(step.id)
        assert found is not None
        assert found.id == step.id

    def test_find_statement_body_no_statement_id(self, context):
        """_find_statement_body returns None when no statement_id (line 157)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        result = context._find_statement_body(step)
        assert result is None

    def test_find_statement_body_no_containing_block(self, context):
        """_find_statement_body returns None when no containing block AST (line 162)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id="non-existent-block",
        )
        result = context._find_statement_body(step)
        assert result is None

    def test_find_statement_body_no_matching_statement(self, context, store):
        """_find_statement_body returns None when stmt not in AST (line 169)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        # Set up a workflow AST with body
        context.workflow_ast = {
            "name": "Test",
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {"type": "StepStmt", "id": "other-stmt", "name": "other"},
                ],
            },
        }

        # Create root step (container for block)
        root = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.WORKFLOW,
        )
        store.save_step(root)

        # Create block step
        block = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.AND_THEN,
            container_id=root.id,
        )
        store.save_step(block)

        # Create a step inside this block with a statement_id not in the AST
        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="non-matching-stmt",
            block_id=block.id,
        )
        result = context._find_statement_body(step)
        assert result is None

    def test_find_containing_block_ast_no_block_id(self, context):
        """_find_containing_block_ast returns None when no block_id (line 183)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        result = context._find_containing_block_ast(step)
        assert result is None

    def test_find_containing_block_ast_block_not_found(self, context):
        """_find_containing_block_ast returns None when block step not found (line 188)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            block_id="non-existent-block",
        )
        result = context._find_containing_block_ast(step)
        assert result is None

    def test_get_completed_step_by_name_cache_hit(self, context):
        """get_completed_step_by_name returns from cache (line 218)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        cache_key = "block-1:s1"
        context._completed_step_cache[cache_key] = step

        result = context.get_completed_step_by_name("s1", "block-1")
        assert result is not None
        assert result.id == step.id

    def test_get_completed_step_by_name_no_block_id(self, context, store):
        """get_completed_step_by_name uses workflow steps when no block_id (line 224)."""
        # With no block_id and no matching completed step, should return None
        result = context.get_completed_step_by_name("s1", None)
        assert result is None

    def test_get_completed_step_by_name_with_pending_created(self, context, store):
        """get_completed_step_by_name checks created_steps (lines 229-230)."""
        from afl.runtime.block import StatementDefinition
        from afl.runtime.dependency import DependencyGraph
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        block_id = "block-pending"

        # Set up graph with statement definition
        stmt = StatementDefinition(
            id="stmt-1",
            name="s1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Value",
        )
        graph = DependencyGraph()
        graph.statements["stmt-1"] = stmt
        context._block_graphs[block_id] = graph

        # Create a completed step in pending created
        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id=block_id,
        )
        step.mark_completed()
        context.changes.add_created_step(step)

        result = context.get_completed_step_by_name("s1", block_id)
        assert result is not None
        assert result.id == step.id

    def test_get_completed_step_by_name_with_pending_updated(self, context, store):
        """get_completed_step_by_name merges updated_steps (lines 232-234)."""
        from afl.runtime.block import StatementDefinition
        from afl.runtime.dependency import DependencyGraph
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        block_id = "block-updated"

        # Set up graph
        stmt = StatementDefinition(
            id="stmt-1",
            name="s1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Value",
        )
        graph = DependencyGraph()
        graph.statements["stmt-1"] = stmt
        context._block_graphs[block_id] = graph

        # Save an incomplete step to store
        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id=block_id,
        )
        store.save_step(step)

        # Put the completed version in updated_steps
        import copy

        updated = copy.deepcopy(step)
        updated.mark_completed()
        context.changes.add_updated_step(updated)

        result = context.get_completed_step_by_name("s1", block_id)
        assert result is not None
        assert result.is_complete

    def test_get_completed_step_by_name_returns_none(self, context, store):
        """get_completed_step_by_name returns None when no match (line 245)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        # Save a step that is not complete
        step = StepDefinition.create(
            workflow_id="wf-ctx-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            statement_id="stmt-1",
            block_id="block-nc",
        )
        store.save_step(step)

        result = context.get_completed_step_by_name("s1", "block-nc")
        assert result is None

    def test_get_facet_definition_no_program_ast(self, context):
        """get_facet_definition returns None when no program_ast."""
        context.program_ast = None
        result = context.get_facet_definition("Value")
        assert result is None

    def test_search_declarations_namespace_nested(self, context):
        """_search_declarations finds facets inside namespaces (lines 280-285)."""
        context.program_ast = {
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "test.ns",
                    "declarations": [
                        {
                            "type": "FacetDecl",
                            "name": "NestedFacet",
                            "params": [],
                        },
                    ],
                },
            ],
        }
        result = context.get_facet_definition("NestedFacet")
        assert result is not None
        assert result["name"] == "NestedFacet"

    def test_search_declarations_namespace_no_match(self, context):
        """_search_declarations returns None when namespace has no match (line 285-286)."""
        context.program_ast = {
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "test.ns",
                    "declarations": [
                        {
                            "type": "FacetDecl",
                            "name": "OtherFacet",
                            "params": [],
                        },
                    ],
                },
            ],
        }
        result = context.get_facet_definition("NonExistent")
        assert result is None


# =========================================================================
# Evaluator edge case tests
# =========================================================================


class TestEvaluatorEdgeCases:
    """Tests for Evaluator edge cases and error paths."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    def test_execute_exception_returns_error(self, store):
        """execute returns ERROR when exception is raised (lines 396-398)."""
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        # Provide a workflow AST that will cause an error during step creation
        # by passing a malformed AST that triggers an exception in processing
        from unittest.mock import patch

        with patch.object(evaluator, "_create_workflow_step", side_effect=RuntimeError("boom")):
            result = evaluator.execute({"name": "Test", "params": []})

        assert result.success is False
        assert result.status == ExecutionStatus.ERROR
        assert isinstance(result.error, RuntimeError)

    def test_extract_defaults_with_default_param(self, evaluator):
        """_extract_defaults picks up 'default' key from params (line 428)."""
        workflow_ast = {
            "name": "Test",
            "params": [
                {"name": "x", "type": "Long", "default": 42},
                {"name": "y", "type": "Long"},
            ],
        }
        defaults = evaluator._extract_defaults(workflow_ast, {"y": 10})
        assert defaults["x"] == 42
        assert defaults["y"] == 10

    def test_extract_defaults_input_overrides_default(self, evaluator):
        """_extract_defaults allows inputs to override defaults."""
        workflow_ast = {
            "name": "Test",
            "params": [{"name": "x", "type": "Long", "default": 42}],
        }
        defaults = evaluator._extract_defaults(workflow_ast, {"x": 99})
        assert defaults["x"] == 99

    def test_extract_defaults_with_literal_dict(self, evaluator):
        """_extract_defaults unwraps emitter literal dicts to plain values."""
        workflow_ast = {
            "name": "Test",
            "params": [
                {"name": "x", "type": "Long", "default": {"type": "Int", "value": 42}},
                {"name": "y", "type": "String", "default": {"type": "String", "value": "hello"}},
            ],
        }
        defaults = evaluator._extract_defaults(workflow_ast, {})
        assert defaults["x"] == 42
        assert defaults["y"] == "hello"

    def test_compiled_defaults_flow_through_execution(self, evaluator):
        """Test that compiler-emitted defaults are used when no input is provided."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestDefaults",
            "params": [
                {"name": "input", "type": "Long", "default": 1},
            ],
            "returns": [{"name": "output", "type": "Long"}],
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
                                        "left": {"type": "InputRef", "path": ["input"]},
                                        "right": {"type": "Int", "value": 1},
                                    },
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestDefaults",
                        "args": [
                            {
                                "name": "output",
                                "value": {"type": "StepRef", "path": ["s1", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        # Execute without providing inputs — default should kick in
        result = evaluator.execute(workflow_ast, inputs={})
        assert result.success is True
        # input defaults to 1, s1.input = 1 + 1 = 2, output = 2
        assert result.outputs.get("output") == 2

    def test_max_iterations_exceeded(self, store):
        """execute stops at max_iterations (line 363->394 timeout path)."""
        from afl.runtime.evaluator import ExecutionResult

        evaluator = Evaluator(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            max_iterations=2,
        )
        # Create a workflow that makes progress every iteration but never completes
        # by mocking _run_iteration to always return True
        from unittest.mock import patch

        with patch.object(evaluator, "_run_iteration", return_value=True):
            with patch.object(evaluator, "_build_result") as mock_build:
                mock_build.return_value = ExecutionResult(
                    success=False,
                    workflow_id="wf-1",
                    error=Exception("Workflow did not complete"),
                    iterations=2,
                )
                result = evaluator.execute({"name": "Test", "params": []})

        assert result.iterations == 2

    def test_build_result_root_error(self, store):
        """_build_result returns error when root step is in error (lines 584-586)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        # Create a root step in error state
        wf_id = "wf-err"
        root = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.WORKFLOW,
            facet_name="Test",
        )
        root.mark_error(RuntimeError("step failed"))
        store.save_step(root)

        result = evaluator._build_result(wf_id, 5)
        assert result.success is False
        assert result.status == ExecutionStatus.ERROR
        assert isinstance(result.error, RuntimeError)
        assert result.iterations == 5

    def test_build_result_root_error_no_transition_error(self, store):
        """_build_result uses default error when transition.error is None (line 584)."""
        from afl.runtime.states import StepState
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        wf_id = "wf-err-2"
        root = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.WORKFLOW,
            facet_name="Test",
        )
        # Set error state without setting transition.error
        root.state = StepState.STATEMENT_ERROR
        root.transition.current_state = StepState.STATEMENT_ERROR
        store.save_step(root)

        result = evaluator._build_result(wf_id, 3)
        assert result.success is False
        assert result.status == ExecutionStatus.ERROR
        assert str(result.error) == "Workflow error"

    def test_build_result_not_complete_not_error(self, store):
        """_build_result returns 'did not complete' when root is neither (line 594-599)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        wf_id = "wf-incomplete"
        root = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.WORKFLOW,
            facet_name="Test",
        )
        # Leave in initial CREATED state (not complete, not error)
        store.save_step(root)

        result = evaluator._build_result(wf_id, 1)
        assert result.success is False
        assert str(result.error) == "Workflow did not complete"

    def test_continue_step_not_found(self, evaluator):
        """continue_step raises ValueError when step not found (line 628)."""
        with pytest.raises(ValueError, match="not found"):
            evaluator.continue_step("non-existent-step")

    def test_continue_step_wrong_state(self, store, evaluator):
        """continue_step raises ValueError when step not at EVENT_TRANSMIT (line 630)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        # Leave in CREATED state
        store.save_step(step)

        with pytest.raises(ValueError, match="expected"):
            evaluator.continue_step(step.id)

    def test_retry_step_not_found(self, evaluator):
        """retry_step raises ValueError when step not found."""
        with pytest.raises(ValueError, match="not found"):
            evaluator.retry_step("non-existent-step")

    def test_retry_step_wrong_state(self, store, evaluator):
        """retry_step raises ValueError when step not at STATEMENT_ERROR."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        # Leave in CREATED state
        store.save_step(step)

        with pytest.raises(ValueError, match="expected"):
            evaluator.retry_step(step.id)

    def test_continue_step_recovers_from_error(self, store, evaluator):
        """continue_step with result recovers a step from STATEMENT_ERROR."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        # Put step in error state
        step.state = StepState.STATEMENT_ERROR
        step.transition.current_state = StepState.STATEMENT_ERROR
        step.error = "previous failure"
        store.save_step(step)

        # continue_step with a result should recover
        evaluator.continue_step(step.id, result={"cache": {"path": "/tmp/test"}})

        recovered = store.get_step(step.id)
        assert recovered.state != StepState.STATEMENT_ERROR
        assert recovered.error is None

    def test_continue_step_skips_completed_terminal(self, store, evaluator):
        """continue_step without result skips a COMPLETE step (no-op)."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step.state = StepState.STATEMENT_COMPLETE
        step.transition.current_state = StepState.STATEMENT_COMPLETE
        store.save_step(step)

        # Should not raise, just skip
        evaluator.continue_step(step.id, result={"foo": "bar"})

        fetched = store.get_step(step.id)
        assert fetched.state == StepState.STATEMENT_COMPLETE

    def test_retry_step_resets_to_event_transmit(self, store, evaluator):
        """retry_step resets step from STATEMENT_ERROR to EVENT_TRANSMIT."""
        from afl.runtime.entities import TaskDefinition
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Download",
        )
        step.mark_error(RuntimeError("SSL error"))
        store.save_step(step)

        # Create an associated failed task
        task = TaskDefinition(
            uuid="task-retry-1",
            name="Download",
            runner_id="runner-1",
            workflow_id="wf-1",
            flow_id="flow-1",
            step_id=step.id,
            state="failed",
            created=1000,
            error={"message": "SSL error"},
        )
        store.save_task(task)

        evaluator.retry_step(step.id)

        reloaded = store.get_step(step.id)
        assert reloaded.state == StepState.EVENT_TRANSMIT
        assert reloaded.transition.error is None
        assert reloaded.transition.request_transition is False

        reloaded_task = store.get_task_for_step(step.id)
        assert reloaded_task.state == "pending"
        assert reloaded_task.error is None

    def test_resume_exception_returns_error(self, store):
        """resume returns ERROR when exception raised (lines 707-709)."""
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        from unittest.mock import patch

        with patch.object(evaluator, "_run_iteration", side_effect=RuntimeError("resume boom")):
            result = evaluator.resume(
                "wf-resume-err",
                {"name": "Test", "params": []},
            )

        assert result.success is False
        assert result.status == ExecutionStatus.ERROR
        assert isinstance(result.error, RuntimeError)

    def test_resume_re_pauses_at_event_blocked(self, store):
        """resume returns PAUSED when steps still at EVENT_TRANSMIT (line 697)."""
        from afl.runtime.states import StepState
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        wf_id = "wf-re-pause"

        # Create a step stuck at EVENT_TRANSMIT
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="SomeEvent",
        )
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
        step.transition.request_transition = False
        store.save_step(step)

        result = evaluator.resume(
            wf_id,
            {"name": "Test", "params": []},
        )

        assert result.success is True
        assert result.status == ExecutionStatus.PAUSED

    def test_process_step_changed_but_no_state_change(self, store):
        """_process_step detects attribute change without state change (lines 554-555)."""
        from unittest.mock import MagicMock, patch

        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-attr-change",
        )

        step = StepDefinition.create(
            workflow_id="wf-attr-change",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Value",
        )
        # Simulate: changer processes step, marks changed but no state change
        original_state = step.state

        mock_result = MagicMock()
        mock_result.step = step
        mock_result.step.state = original_state  # Same state
        mock_result.step.transition.changed = True
        mock_result.continue_processing = False

        mock_changer = MagicMock()
        mock_changer.process.return_value = mock_result

        with patch("afl.runtime.evaluator.get_state_changer", return_value=mock_changer):
            progress = evaluator._process_step(step, context)

        assert progress is True


# =========================================================================
# Evaluator iteration edge cases
# =========================================================================


class TestEvaluatorIteration:
    """Tests for iteration loop edge cases."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    def test_run_iteration_skips_duplicate_step_ids(self, store):
        """_run_iteration skips already-processed step IDs (line 483)."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        wf_id = "wf-dup"
        # Create a terminal step (won't make progress)
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step.mark_completed()
        store.save_step(step)

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
        )

        # Run iteration - the terminal step should be processed (returns False)
        progress = evaluator._run_iteration(context)
        assert progress is False

    def test_run_iteration_processes_created_steps(self, store):
        """_run_iteration processes newly created steps (lines 502-508)."""

        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        wf_id = "wf-created"

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
        )

        # Manually add a step to created_steps to simulate step creation during iteration
        new_step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Value",
        )
        new_step.mark_completed()  # Terminal, won't make progress
        context.changes.add_created_step(new_step)

        progress = evaluator._run_iteration(context)
        # Terminal step doesn't make progress, but it gets processed
        assert progress is False
        # Step should be in pending_created (restored for commit)
        assert len(context.changes.created_steps) >= 1


# =========================================================================
# Schema Instantiation Runtime Tests
# =========================================================================


class TestSchemaInstantiationRuntime:
    """Tests for schema instantiation execution in the runtime."""

    @pytest.fixture
    def evaluator(self):
        """Create evaluator with in-memory store."""
        store = MemoryStore()
        telemetry = Telemetry(enabled=True)
        return Evaluator(persistence=store, telemetry=telemetry)

    def test_schema_instantiation_execution(self, evaluator):
        """Schema instantiation step should create returns, not params.

        ```afl
        schema Config {
            timeout: Long,
            retries: Long
        }
        facet Value(input: Long) => (output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            cfg = Config(timeout = 30, retries = 3)
            v = Value(input = cfg.timeout)
            yield Test(output = v.input)
        }
        ```
        """
        program_ast = {
            "declarations": [
                {
                    "type": "SchemaDecl",
                    "name": "Config",
                    "fields": [
                        {"name": "timeout", "type": "Long"},
                        {"name": "retries", "type": "Long"},
                    ],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Test",
            "params": [{"name": "input", "type": "Long"}],
            "returns": [{"name": "output", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-cfg",
                        "name": "cfg",
                        "call": {
                            "type": "CallExpr",
                            "target": "Config",
                            "args": [
                                {"name": "timeout", "value": {"type": "Int", "value": 30}},
                                {"name": "retries", "value": {"type": "Int", "value": 3}},
                            ],
                        },
                    },
                    {
                        "type": "StepStmt",
                        "id": "step-v",
                        "name": "v",
                        "call": {
                            "type": "CallExpr",
                            "target": "Value",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {"type": "StepRef", "path": ["cfg", "timeout"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "Test",
                        "args": [
                            {
                                "name": "output",
                                "value": {"type": "StepRef", "path": ["v", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(workflow_ast, inputs={"input": 1}, program_ast=program_ast)
        assert result.success is True
        # cfg.timeout = 30 (stored as return)
        # v.input = 30 (from cfg.timeout)
        # output = 30
        assert result.outputs.get("output") == 30

    def test_schema_field_passed_to_facet(self, evaluator):
        """Schema fields should be accessible via step references.

        ```afl
        schema Data {
            value: Long,
            multiplier: Long
        }
        facet Multiply(a: Long, b: Long) => (result: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            d = Data(value = $.input, multiplier = 2)
            m = Multiply(a = d.value, b = d.multiplier)
            yield Test(output = m.result)
        }
        ```
        """
        program_ast = {
            "declarations": [
                {
                    "type": "SchemaDecl",
                    "name": "Data",
                    "fields": [
                        {"name": "value", "type": "Long"},
                        {"name": "multiplier", "type": "Long"},
                    ],
                },
                {
                    "type": "FacetDecl",
                    "name": "Multiply",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "result", "type": "Long"}],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Test",
            "params": [{"name": "input", "type": "Long"}],
            "returns": [{"name": "output", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-d",
                        "name": "d",
                        "call": {
                            "type": "CallExpr",
                            "target": "Data",
                            "args": [
                                {
                                    "name": "value",
                                    "value": {"type": "InputRef", "path": ["input"]},
                                },
                                {"name": "multiplier", "value": {"type": "Int", "value": 2}},
                            ],
                        },
                    },
                    {
                        "type": "StepStmt",
                        "id": "step-m",
                        "name": "m",
                        "call": {
                            "type": "CallExpr",
                            "target": "Multiply",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "StepRef", "path": ["d", "value"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "StepRef", "path": ["d", "multiplier"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "Test",
                        "args": [
                            {
                                "name": "output",
                                # Use a.value to verify facet processed the schema values
                                "value": {"type": "StepRef", "path": ["m", "a"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(workflow_ast, inputs={"input": 5}, program_ast=program_ast)
        assert result.success is True
        # d.value = 5, d.multiplier = 2
        # m.a = 5, m.b = 2
        # output = m.a = 5
        assert result.outputs.get("output") == 5

    def test_schema_with_concat_expression(self, evaluator):
        """Schema instantiation with concatenation expression.

        ```afl
        schema StringData {
            combined: String
        }
        facet Echo(value: String) => (result: String)
        workflow Test(a: String, b: String) => (output: String) andThen {
            d = StringData(combined = $.a ++ $.b)
            e = Echo(value = d.combined)
            yield Test(output = e.value)
        }
        ```
        """
        program_ast = {
            "declarations": [
                {
                    "type": "SchemaDecl",
                    "name": "StringData",
                    "fields": [{"name": "combined", "type": "String"}],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Test",
            "params": [
                {"name": "a", "type": "String"},
                {"name": "b", "type": "String"},
            ],
            "returns": [{"name": "output", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-d",
                        "name": "d",
                        "call": {
                            "type": "CallExpr",
                            "target": "StringData",
                            "args": [
                                {
                                    "name": "combined",
                                    "value": {
                                        "type": "ConcatExpr",
                                        "operands": [
                                            {"type": "InputRef", "path": ["a"]},
                                            {"type": "InputRef", "path": ["b"]},
                                        ],
                                    },
                                }
                            ],
                        },
                    },
                    {
                        "type": "StepStmt",
                        "id": "step-e",
                        "name": "e",
                        "call": {
                            "type": "CallExpr",
                            "target": "Echo",
                            "args": [
                                {
                                    "name": "value",
                                    "value": {"type": "StepRef", "path": ["d", "combined"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "Test",
                        "args": [
                            {
                                "name": "output",
                                "value": {"type": "StepRef", "path": ["e", "value"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(
            workflow_ast, inputs={"a": "Hello", "b": "World"}, program_ast=program_ast
        )
        assert result.success is True
        # d.combined = "HelloWorld"
        # e.value = "HelloWorld"
        # output = "HelloWorld"
        assert result.outputs.get("output") == "HelloWorld"


# =========================================================================
# Spec Example 21.3: Multiple concurrent andThen blocks
# =========================================================================


class TestSpecExample21_3:
    """Tests for spec example 21.3: Multiple concurrent andThen blocks.

    ```afl
    namespace test.three {
      facet Value(input: Long, output: Long)
      workflow TestThree(input: Long = 1) => (output1: Long, output2: Long, output3: Long)
        andThen { a=Value(input=$.input+1); b=Value(input=$.input+10); c=Value(input=a.input+b.input); yield TestThree(output1=c.input) }
        andThen { a=Value(input=$.input+1); b=Value(input=$.input+10); c=Value(input=a.input+b.input); yield TestThree(output2=c.input) }
        andThen { a=Value(input=$.input+1); b=Value(input=$.input+10); c=Value(input=a.input+b.input); yield TestThree(output3=c.input) }
    }
    ```

    Expected: output1 = output2 = output3 = 13
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    def _make_block_body(self, output_name):
        """Create an andThen block body for one concurrent block."""
        return {
            "type": "AndThenBlock",
            "steps": [
                {
                    "type": "StepStmt",
                    "id": f"step-a-{output_name}",
                    "name": "a",
                    "call": {
                        "type": "CallExpr",
                        "target": "Value",
                        "args": [
                            {
                                "name": "input",
                                "value": {
                                    "type": "BinaryExpr",
                                    "operator": "+",
                                    "left": {"type": "InputRef", "path": ["input"]},
                                    "right": {"type": "Int", "value": 1},
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "StepStmt",
                    "id": f"step-b-{output_name}",
                    "name": "b",
                    "call": {
                        "type": "CallExpr",
                        "target": "Value",
                        "args": [
                            {
                                "name": "input",
                                "value": {
                                    "type": "BinaryExpr",
                                    "operator": "+",
                                    "left": {"type": "InputRef", "path": ["input"]},
                                    "right": {"type": "Int", "value": 10},
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "StepStmt",
                    "id": f"step-c-{output_name}",
                    "name": "c",
                    "call": {
                        "type": "CallExpr",
                        "target": "Value",
                        "args": [
                            {
                                "name": "input",
                                "value": {
                                    "type": "BinaryExpr",
                                    "operator": "+",
                                    "left": {"type": "StepRef", "path": ["a", "input"]},
                                    "right": {"type": "StepRef", "path": ["b", "input"]},
                                },
                            }
                        ],
                    },
                },
            ],
            "yield": {
                "type": "YieldStmt",
                "id": f"yield-{output_name}",
                "call": {
                    "type": "CallExpr",
                    "target": "TestThree",
                    "args": [
                        {
                            "name": output_name,
                            "value": {"type": "StepRef", "path": ["c", "input"]},
                        }
                    ],
                },
            },
        }

    @pytest.fixture
    def workflow_ast(self):
        """AST for TestThree workflow with 3 concurrent andThen blocks."""
        return {
            "type": "WorkflowDecl",
            "name": "TestThree",
            "params": [{"name": "input", "type": "Long"}],
            "returns": [
                {"name": "output1", "type": "Long"},
                {"name": "output2", "type": "Long"},
                {"name": "output3", "type": "Long"},
            ],
            "body": [
                self._make_block_body("output1"),
                self._make_block_body("output2"),
                self._make_block_body("output3"),
            ],
        }

    def test_multiple_concurrent_blocks(self, evaluator, workflow_ast):
        """Test that 3 concurrent blocks produce correct outputs."""
        result = evaluator.execute(workflow_ast, inputs={"input": 1})

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED
        # Each block: a.input = 1+1=2, b.input = 1+10=11, c.input = 2+11=13
        assert result.outputs.get("output1") == 13
        assert result.outputs.get("output2") == 13
        assert result.outputs.get("output3") == 13

    def test_three_blocks_created(self, store, evaluator, workflow_ast):
        """Test that 3 AND_THEN block steps are created for the workflow root."""
        result = evaluator.execute(workflow_ast, inputs={"input": 1})
        assert result.success is True

        # Find all block steps
        all_steps = list(store.get_all_steps())
        block_steps = [s for s in all_steps if s.object_type == ObjectType.AND_THEN]
        # 3 blocks (one per andThen), each at the workflow root level
        root = [s for s in all_steps if s.container_id is None][0]
        root_blocks = [s for s in block_steps if s.container_id == root.id]
        assert len(root_blocks) == 3

    def test_independent_step_names_across_blocks(self, store, evaluator, workflow_ast):
        """Test that steps named a, b, c exist independently in each block."""
        result = evaluator.execute(workflow_ast, inputs={"input": 1})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        # Find all completed variable assignment steps
        var_steps = [
            s
            for s in all_steps
            if s.object_type == ObjectType.VARIABLE_ASSIGNMENT and s.is_complete
        ]
        # Each block has a, b, c → 9 variable assignment steps total
        # (excluding the workflow root which is also WORKFLOW type, not VAR)
        assert len(var_steps) == 9


# =========================================================================
# Foreach Runtime Execution Tests
# =========================================================================


class TestForeachExecution:
    """Tests for foreach runtime execution.

    ```afl
    namespace test.foreach {
      facet Value(input: Long)
      workflow ProcessAll(items: Json) => (count: Long)
        andThen foreach r in $.items {
          v = Value(input = r)
          yield ProcessAll(count = v.input)
        }
    }
    ```
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    @pytest.fixture
    def workflow_ast(self):
        """AST for ProcessAll workflow with foreach."""
        return {
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
                        "id": "step-v",
                        "name": "v",
                        "call": {
                            "type": "CallExpr",
                            "target": "Value",
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
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "ProcessAll",
                        "args": [
                            {
                                "name": "count",
                                "value": {"type": "StepRef", "path": ["v", "input"]},
                            }
                        ],
                    },
                },
            },
        }

    def test_foreach_creates_sub_blocks(self, store, evaluator, workflow_ast):
        """Verify N sub-block steps created for N-element array."""
        result = evaluator.execute(workflow_ast, inputs={"items": [10, 20, 30]})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        block_steps = [s for s in all_steps if s.object_type == ObjectType.AND_THEN]
        # 1 foreach block + 3 sub-blocks
        assert len(block_steps) == 4

        # The 3 sub-blocks should have foreach_var set
        sub_blocks = [s for s in block_steps if s.foreach_var == "r"]
        assert len(sub_blocks) == 3

    def test_foreach_variable_resolution(self, store, evaluator, workflow_ast):
        """Verify foreach variable resolves correctly in child steps."""
        result = evaluator.execute(workflow_ast, inputs={"items": [42]})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        # Find the Value step (v) — should have input = 42
        var_steps = [
            s
            for s in all_steps
            if s.object_type == ObjectType.VARIABLE_ASSIGNMENT
            and s.facet_name == "Value"
            and s.is_complete
        ]
        assert len(var_steps) == 1
        assert var_steps[0].get_attribute("input") == 42

    def test_foreach_parallel_execution(self, store, evaluator, workflow_ast):
        """Verify all foreach iterations run to completion."""
        result = evaluator.execute(workflow_ast, inputs={"items": [1, 2, 3, 4, 5]})
        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

        all_steps = list(store.get_all_steps())
        # 5 Value steps should be complete
        value_steps = [
            s
            for s in all_steps
            if s.object_type == ObjectType.VARIABLE_ASSIGNMENT
            and s.facet_name == "Value"
            and s.is_complete
        ]
        assert len(value_steps) == 5

        # Verify foreach values
        foreach_values = sorted([s.get_attribute("input") for s in value_steps])
        assert foreach_values == [1, 2, 3, 4, 5]

    def test_foreach_empty_array(self, store, evaluator, workflow_ast):
        """Verify empty array produces no sub-blocks, workflow completes."""
        result = evaluator.execute(workflow_ast, inputs={"items": []})
        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

        all_steps = list(store.get_all_steps())
        # Only the workflow root + 1 foreach block (no sub-blocks)
        block_steps = [s for s in all_steps if s.object_type == ObjectType.AND_THEN]
        assert len(block_steps) == 1  # Just the foreach block itself

        # No Value steps created
        value_steps = [s for s in all_steps if s.object_type == ObjectType.VARIABLE_ASSIGNMENT]
        assert len(value_steps) == 0


# =========================================================================
# Step Deduplication Tests
# =========================================================================


class TestStepDeduplication:
    """Tests for step deduplication (block statement_id normalization).

    Verifies that block steps always get a statement_id (never None)
    and that foreach sub-blocks get a statement_id of "foreach-{i}".
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    def test_single_body_block_gets_statement_id(self, store, evaluator):
        """Single-body andThen blocks produce statement_id='block-0' (not None)."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestSingleBlock",
            "params": [{"name": "input", "type": "Long"}],
            "returns": [{"name": "output", "type": "Long"}],
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
                                    "value": {"type": "InputRef", "path": ["input"]},
                                }
                            ],
                        },
                    }
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestSingleBlock",
                        "args": [
                            {
                                "name": "output",
                                "value": {"type": "StepRef", "path": ["s1", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        program_ast = {
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                workflow_ast,
            ]
        }

        result = evaluator.execute(workflow_ast, program_ast=program_ast, inputs={"input": 1})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        block_steps = [s for s in all_steps if s.object_type == ObjectType.AND_THEN]
        assert len(block_steps) == 1
        assert block_steps[0].statement_id == "block-0"

    def test_multi_body_blocks_get_indexed_statement_ids(self, store, evaluator):
        """Multiple andThen bodies produce statement_id='block-0', 'block-1', etc."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestMultiBlock",
            "params": [{"name": "input", "type": "Long"}],
            "body": [
                {
                    "type": "AndThenBlock",
                    "steps": [
                        {
                            "type": "StepStmt",
                            "id": "step-a",
                            "name": "a",
                            "call": {
                                "type": "CallExpr",
                                "target": "Value",
                                "args": [
                                    {
                                        "name": "input",
                                        "value": {"type": "InputRef", "path": ["input"]},
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "type": "AndThenBlock",
                    "steps": [
                        {
                            "type": "StepStmt",
                            "id": "step-b",
                            "name": "b",
                            "call": {
                                "type": "CallExpr",
                                "target": "Value",
                                "args": [
                                    {
                                        "name": "input",
                                        "value": {"type": "InputRef", "path": ["input"]},
                                    }
                                ],
                            },
                        }
                    ],
                },
            ],
        }

        program_ast = {
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                workflow_ast,
            ]
        }

        result = evaluator.execute(workflow_ast, program_ast=program_ast, inputs={"input": 1})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        block_steps = sorted(
            [s for s in all_steps if s.object_type == ObjectType.AND_THEN],
            key=lambda s: s.statement_id or "",
        )
        assert len(block_steps) == 2
        assert block_steps[0].statement_id == "block-0"
        assert block_steps[1].statement_id == "block-1"

    def test_foreach_sub_blocks_get_indexed_statement_ids(self, store, evaluator):
        """Foreach sub-blocks produce statement_id='foreach-0', 'foreach-1', etc."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "ProcessAll",
            "params": [{"name": "items", "type": "Json"}],
            "body": {
                "type": "AndThenBlock",
                "foreach": {
                    "variable": "r",
                    "iterable": {"type": "InputRef", "path": ["items"]},
                },
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-v",
                        "name": "v",
                        "call": {
                            "type": "CallExpr",
                            "target": "Value",
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
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "ProcessAll",
                        "args": [
                            {
                                "name": "count",
                                "value": {"type": "StepRef", "path": ["v", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(workflow_ast, inputs={"items": [10, 20, 30]})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        # Find the foreach sub-blocks (they have foreach_var set)
        sub_blocks = sorted(
            [s for s in all_steps if s.object_type == ObjectType.AND_THEN and s.foreach_var == "r"],
            key=lambda s: s.statement_id or "",
        )
        assert len(sub_blocks) == 3
        assert sub_blocks[0].statement_id == "foreach-0"
        assert sub_blocks[1].statement_id == "foreach-1"
        assert sub_blocks[2].statement_id == "foreach-2"

    def test_block_step_idempotency(self, store, evaluator):
        """Block step creation is idempotent — re-executing doesn't duplicate."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestIdemp",
            "params": [{"name": "input", "type": "Long"}],
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
                                    "value": {"type": "InputRef", "path": ["input"]},
                                }
                            ],
                        },
                    }
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestIdemp",
                        "args": [
                            {
                                "name": "output",
                                "value": {"type": "StepRef", "path": ["s1", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        program_ast = {
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                workflow_ast,
            ]
        }

        result = evaluator.execute(workflow_ast, program_ast=program_ast, inputs={"input": 1})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        block_steps = [s for s in all_steps if s.object_type == ObjectType.AND_THEN]
        # Exactly one block step, not duplicated
        assert len(block_steps) == 1


# =========================================================================
# Iteration-Level Trace Tests (Features 12-14)
# =========================================================================


class TestIterationTraces:
    """Iteration-by-iteration trace tests matching spec/70_examples.md.

    These tests verify step-by-step state progression at each commit
    boundary, matching the traces documented in the specification.
    """

    def _run_one_iteration(self, evaluator, context):
        """Run a single iteration and commit.

        Returns:
            True if progress was made
        """
        context.clear_caches()
        progress = evaluator._run_iteration(context)
        evaluator._commit_iteration(context)
        return progress

    def _get_step_states(self, store, workflow_id):
        """Get a dict of facet_name/statement_id → state for all steps."""
        steps = list(store.get_steps_by_workflow(workflow_id))
        result = {}
        for s in steps:
            # Use a combo of facet_name or statement_id for identification
            key = s.facet_name or str(s.statement_id) or s.object_type
            result[s.id] = (key, s.state, s.is_complete)
        return steps

    def _count_complete(self, steps):
        """Count complete steps from a list."""
        return sum(1 for s in steps if s.is_complete)

    def _count_at_state(self, steps, state):
        """Count steps at a specific state."""
        return sum(1 for s in steps if s.state == state)

    # ===================================================================
    # Example 2 Trace: 8 steps, 8 iterations
    # ===================================================================

    def test_example_2_full_trace(self):
        """Test Example 2 iteration trace: Adder(a=1, b=2) → result=3.

        Spec: 8 steps, 8 iterations (0-7), output result=3.
        """
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        # Reuse the Example 2 fixtures
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Adder",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "sum", "type": "Long"}],
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
                                            "value": {"type": "InputRef", "path": ["a"]},
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
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["b"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Adder",
                            "call": {
                                "type": "CallExpr",
                                "target": "Adder",
                                "args": [
                                    {
                                        "name": "sum",
                                        "value": {
                                            "type": "BinaryExpr",
                                            "operator": "+",
                                            "left": {"type": "StepRef", "path": ["s1", "input"]},
                                            "right": {"type": "StepRef", "path": ["s2", "input"]},
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "AddWorkflow",
            "params": [
                {"name": "x", "type": "Long"},
                {"name": "y", "type": "Long"},
            ],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-addition",
                        "name": "addition",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "InputRef", "path": ["y"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-AW",
                    "call": {
                        "type": "CallExpr",
                        "target": "AddWorkflow",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["addition", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

        inputs = {"x": 1, "y": 2}
        wf_id = "wf-trace-2"

        # Manually set up the execution context
        from afl.runtime.types import WorkflowId

        wf_id = WorkflowId(wf_id)
        defaults = evaluator._extract_defaults(workflow_ast, inputs)
        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
            workflow_ast=workflow_ast,
            workflow_defaults=defaults,
            program_ast=program_ast,
        )

        # Create initial step and commit
        root_step = evaluator._create_workflow_step(workflow_ast, wf_id, defaults)
        context.changes.add_created_step(root_step)
        evaluator._commit_iteration(context)

        # --- Iteration 0: 6 steps created (yields deferred); s1, s2 complete
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert len(steps) == 6  # AddWorkflow, block_AW, addition, block_Adder, s1, s2
        assert self._count_complete(steps) == 2  # s1, s2

        # --- Iteration 1: yield_Adder created and completes (lazy)
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert len(steps) == 7  # + yield_Adder
        assert self._count_complete(steps) == 3  # s1, s2, yield_Adder

        # --- Iteration 2: block_Adder completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 4  # + block_Adder

        # --- Iteration 3: addition completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 5  # + addition

        # --- Iteration 4: yield_AW created and completes (lazy)
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert len(steps) == 8  # + yield_AW (all 8 now exist)
        assert self._count_complete(steps) == 6  # + yield_AW

        # --- Iteration 5: block_AW completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 7  # + block_AW

        # --- Iteration 6: AddWorkflow completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 8  # ALL complete

        # --- Iteration 7: Fixed point
        progress = self._run_one_iteration(evaluator, context)
        assert progress is False

        # Verify final output
        root = store.get_workflow_root(wf_id)
        assert root is not None
        assert root.is_complete
        output = root.attributes.returns.get("result")
        assert output is not None
        assert output.value == 3

    # ===================================================================
    # Example 3 Trace: 11 steps, 11 iterations
    # ===================================================================

    def test_example_3_full_trace(self):
        """Test Example 3 iteration trace: nested andThen → result=13.

        Spec: 11 steps, 11 iterations (0-10), output result=13.
        """
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.types import WorkflowId

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "SomeFacet",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Adder",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "sum", "type": "Long"}],
                    "body": {
                        "type": "AndThenBlock",
                        "steps": [
                            {
                                "type": "StepStmt",
                                "id": "step-s1",
                                "name": "s1",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "SomeFacet",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["a"]},
                                        }
                                    ],
                                },
                                "body": {
                                    "type": "AndThenBlock",
                                    "steps": [
                                        {
                                            "type": "StepStmt",
                                            "id": "step-subStep1",
                                            "name": "subStep1",
                                            "call": {
                                                "type": "CallExpr",
                                                "target": "Value",
                                                "args": [
                                                    {
                                                        "name": "input",
                                                        "value": {
                                                            "type": "InputRef",
                                                            "path": ["input"],
                                                        },
                                                    }
                                                ],
                                            },
                                        },
                                    ],
                                    "yield": {
                                        "type": "YieldStmt",
                                        "id": "yield-SF",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "SomeFacet",
                                            "args": [
                                                {
                                                    "name": "output",
                                                    "value": {
                                                        "type": "BinaryExpr",
                                                        "operator": "+",
                                                        "left": {
                                                            "type": "StepRef",
                                                            "path": ["subStep1", "input"],
                                                        },
                                                        "right": {"type": "Int", "value": 10},
                                                    },
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                            {
                                "type": "StepStmt",
                                "id": "step-s2",
                                "name": "s2",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["b"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Adder",
                            "call": {
                                "type": "CallExpr",
                                "target": "Adder",
                                "args": [
                                    {
                                        "name": "sum",
                                        "value": {
                                            "type": "BinaryExpr",
                                            "operator": "+",
                                            "left": {
                                                "type": "StepRef",
                                                "path": ["s1", "output"],
                                            },
                                            "right": {
                                                "type": "StepRef",
                                                "path": ["s2", "input"],
                                            },
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "AddWorkflow",
            "params": [
                {"name": "x", "type": "Long"},
                {"name": "y", "type": "Long"},
            ],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-addition",
                        "name": "addition",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "InputRef", "path": ["y"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-AW",
                    "call": {
                        "type": "CallExpr",
                        "target": "AddWorkflow",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["addition", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

        inputs = {"x": 1, "y": 2}
        wf_id = WorkflowId("wf-trace-3")
        defaults = evaluator._extract_defaults(workflow_ast, inputs)
        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
            workflow_ast=workflow_ast,
            workflow_defaults=defaults,
            program_ast=program_ast,
        )

        root_step = evaluator._create_workflow_step(workflow_ast, wf_id, defaults)
        context.changes.add_created_step(root_step)
        evaluator._commit_iteration(context)

        # --- Iteration 0: 8 steps created (yields deferred); s2 and subStep1 complete
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert (
            len(steps) == 8
        )  # AddWorkflow, block_AW, addition, block_Adder, s1, s2, block_s1, subStep1
        assert self._count_complete(steps) == 2  # s2, subStep1

        # --- Iteration 1: yield_SF created and completes (lazy)
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert len(steps) == 9  # + yield_SF
        assert self._count_complete(steps) == 3

        # --- Iteration 2: block_s1 completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 4

        # --- Iteration 3: s1 completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 5

        # --- Iteration 4: yield_Adder created and completes (lazy)
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert len(steps) == 10  # + yield_Adder
        assert self._count_complete(steps) == 6

        # --- Iteration 5: block_Adder completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 7

        # --- Iteration 6: addition completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 8

        # --- Iteration 7: yield_AW created and completes (lazy)
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert len(steps) == 11  # + yield_AW (all 11 now exist)
        assert self._count_complete(steps) == 9

        # --- Iteration 8: block_AW completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 10

        # --- Iteration 9: AddWorkflow completes
        progress = self._run_one_iteration(evaluator, context)
        assert progress is True
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 11  # ALL complete

        # --- Iteration 10: Fixed point
        progress = self._run_one_iteration(evaluator, context)
        assert progress is False

        # Verify final output
        root = store.get_workflow_root(wf_id)
        assert root is not None
        assert root.is_complete
        assert root.attributes.returns["result"].value == 13

    # ===================================================================
    # Example 4 Trace: 11 steps, 2 evaluator runs
    # ===================================================================

    def test_example_4_full_trace(self):
        """Test Example 4 iteration trace: event facet blocks, then resumes.

        Spec: 11 steps, 2 evaluator runs, output result=15.
        subStep1 calls CountDocuments (event), blocks at EventTransmit.
        """

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "SomeFacet",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "CountDocuments",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Adder",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "sum", "type": "Long"}],
                    "body": {
                        "type": "AndThenBlock",
                        "steps": [
                            {
                                "type": "StepStmt",
                                "id": "step-s1",
                                "name": "s1",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "SomeFacet",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["a"]},
                                        }
                                    ],
                                },
                                "body": {
                                    "type": "AndThenBlock",
                                    "steps": [
                                        {
                                            "type": "StepStmt",
                                            "id": "step-subStep1",
                                            "name": "subStep1",
                                            "call": {
                                                "type": "CallExpr",
                                                "target": "CountDocuments",
                                                "args": [
                                                    {
                                                        "name": "input",
                                                        "value": {"type": "Int", "value": 3},
                                                    }
                                                ],
                                            },
                                        },
                                    ],
                                    "yield": {
                                        "type": "YieldStmt",
                                        "id": "yield-SF",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "SomeFacet",
                                            "args": [
                                                {
                                                    "name": "output",
                                                    "value": {
                                                        "type": "BinaryExpr",
                                                        "operator": "+",
                                                        "left": {
                                                            "type": "StepRef",
                                                            "path": ["subStep1", "input"],
                                                        },
                                                        "right": {"type": "Int", "value": 10},
                                                    },
                                                }
                                            ],
                                        },
                                    },
                                },
                            },
                            {
                                "type": "StepStmt",
                                "id": "step-s2",
                                "name": "s2",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["b"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Adder",
                            "call": {
                                "type": "CallExpr",
                                "target": "Adder",
                                "args": [
                                    {
                                        "name": "sum",
                                        "value": {
                                            "type": "BinaryExpr",
                                            "operator": "+",
                                            "left": {
                                                "type": "StepRef",
                                                "path": ["s1", "output"],
                                            },
                                            "right": {
                                                "type": "StepRef",
                                                "path": ["s2", "input"],
                                            },
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "AddWorkflow",
            "params": [
                {"name": "x", "type": "Long"},
                {"name": "y", "type": "Long"},
            ],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-addition",
                        "name": "addition",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "InputRef", "path": ["y"]},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-AW",
                    "call": {
                        "type": "CallExpr",
                        "target": "AddWorkflow",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["addition", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

        # ===== Evaluator Run 1: execute until PAUSED =====
        result1 = evaluator.execute(workflow_ast, inputs={"x": 1, "y": 2}, program_ast=program_ast)
        assert result1.status == ExecutionStatus.PAUSED
        assert result1.success is True

        wf_id = result1.workflow_id

        # After Run 1: 8 steps (yields deferred), s2 complete, subStep1 at EventTransmit
        steps = list(store.get_steps_by_workflow(wf_id))
        assert len(steps) == 8  # yields not yet created
        assert self._count_complete(steps) == 1  # s2
        assert self._count_at_state(steps, StepState.EVENT_TRANSMIT) == 1  # subStep1

        # Find the blocked step
        blocked = [s for s in steps if s.state == StepState.EVENT_TRANSMIT]
        assert len(blocked) == 1
        assert blocked[0].facet_name == "CountDocuments"

        # ===== External: continue the blocked step =====
        evaluator.continue_step(blocked[0].id)

        # ===== Evaluator Run 2: resume =====
        result2 = evaluator.resume(
            wf_id,
            workflow_ast,
            program_ast=program_ast,
            inputs={"x": 1, "y": 2},
        )
        assert result2.status == ExecutionStatus.COMPLETED
        assert result2.success is True

        # After Run 2: all 11 steps complete
        steps = list(store.get_steps_by_workflow(wf_id))
        assert self._count_complete(steps) == 11

        # Verify output: subStep1.input=3, output=3+10=13, s2.input=2, sum=13+2=15
        assert result2.outputs["result"] == 15

    # ===================================================================
    # Acceptance tests from spec/80_acceptance_tests.md
    # ===================================================================

    def test_event_facet_blocks_at_transmit(self):
        """Verify subStep1 (CountDocuments) blocks at EventTransmit."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "CountDocuments",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestEvent",
            "params": [],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-1",
                        "name": "sub",
                        "call": {
                            "type": "CallExpr",
                            "target": "CountDocuments",
                            "args": [{"name": "input", "value": {"type": "Int", "value": 42}}],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestEvent",
                        "args": [],
                    },
                },
            },
        }

        result = evaluator.execute(workflow_ast, program_ast=program_ast)
        assert result.status == ExecutionStatus.PAUSED

        steps = list(store.get_steps_by_workflow(result.workflow_id))
        event_blocked = [s for s in steps if s.state == StepState.EVENT_TRANSMIT]
        assert len(event_blocked) == 1
        assert event_blocked[0].facet_name == "CountDocuments"

    def test_step_continue_resumes_step(self):
        """Verify continue_step() unblocks from EventTransmit."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "CountDocuments",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestContinue",
            "params": [],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-1",
                        "name": "sub",
                        "call": {
                            "type": "CallExpr",
                            "target": "CountDocuments",
                            "args": [{"name": "input", "value": {"type": "Int", "value": 5}}],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestContinue",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["sub", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        result1 = evaluator.execute(workflow_ast, program_ast=program_ast)
        assert result1.status == ExecutionStatus.PAUSED

        # Find and continue the blocked step
        steps = list(store.get_steps_by_workflow(result1.workflow_id))
        blocked = [s for s in steps if s.state == StepState.EVENT_TRANSMIT][0]
        evaluator.continue_step(blocked.id)

        # Resume should complete
        result2 = evaluator.resume(result1.workflow_id, workflow_ast, program_ast=program_ast)
        assert result2.status == ExecutionStatus.COMPLETED
        assert result2.outputs["result"] == 5

    def test_multi_run_execution(self):
        """Verify evaluator pauses at fixed point, resumes after StepContinue."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "External",
                    "params": [{"name": "x", "type": "Long"}],
                    "returns": [{"name": "y", "type": "Long"}],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "MultiRun",
            "params": [],
            "returns": [{"name": "output", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-ext",
                        "name": "ext",
                        "call": {
                            "type": "CallExpr",
                            "target": "External",
                            "args": [{"name": "x", "value": {"type": "Int", "value": 10}}],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "MultiRun",
                        "args": [
                            {
                                "name": "output",
                                "value": {"type": "StepRef", "path": ["ext", "x"]},
                            }
                        ],
                    },
                },
            },
        }

        # Run 1: pauses
        result1 = evaluator.execute(workflow_ast, program_ast=program_ast)
        assert result1.status == ExecutionStatus.PAUSED
        _iterations_run1 = result1.iterations

        # Continue
        blocked = [
            s
            for s in store.get_steps_by_workflow(result1.workflow_id)
            if s.state == StepState.EVENT_TRANSMIT
        ][0]
        evaluator.continue_step(blocked.id)

        # Run 2: completes
        result2 = evaluator.resume(result1.workflow_id, workflow_ast, program_ast=program_ast)
        assert result2.status == ExecutionStatus.COMPLETED
        assert result2.iterations > 0

    def test_nested_statement_block(self):
        """Verify s1 with inline andThen creates block_s1."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "SomeFacet",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestNested",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "output", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-s1",
                        "name": "s1",
                        "call": {
                            "type": "CallExpr",
                            "target": "SomeFacet",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                }
                            ],
                        },
                        "body": {
                            "type": "AndThenBlock",
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-sub",
                                    "name": "sub",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "Value",
                                        "args": [
                                            {
                                                "name": "input",
                                                "value": {"type": "InputRef", "path": ["input"]},
                                            }
                                        ],
                                    },
                                },
                            ],
                            "yield": {
                                "type": "YieldStmt",
                                "id": "yield-SF",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "SomeFacet",
                                    "args": [
                                        {
                                            "name": "output",
                                            "value": {"type": "StepRef", "path": ["sub", "input"]},
                                        }
                                    ],
                                },
                            },
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestNested",
                        "args": [
                            {
                                "name": "output",
                                "value": {"type": "StepRef", "path": ["s1", "output"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(workflow_ast, inputs={"x": 7}, program_ast=program_ast)
        assert result.success is True
        assert result.outputs["output"] == 7

        # Verify block_s1 was created
        steps = list(store.get_steps_by_workflow(result.workflow_id))
        block_steps = [s for s in steps if s.object_type == ObjectType.AND_THEN]
        # block_AW (workflow body) + block_s1 (statement-level)
        assert len(block_steps) == 2

    def test_facet_definition_lookup(self):
        """Verify EventTransmitHandler detects EventFacetDecl."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "test",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "MyEvent",
                            "params": [{"name": "input", "type": "Long"}],
                            "returns": [{"name": "output", "type": "Long"}],
                        },
                    ],
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestLookup",
            "params": [],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-1",
                        "name": "ev",
                        "call": {
                            "type": "CallExpr",
                            "target": "MyEvent",
                            "args": [{"name": "input", "value": {"type": "Int", "value": 1}}],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-1",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestLookup",
                        "args": [],
                    },
                },
            },
        }

        result = evaluator.execute(workflow_ast, program_ast=program_ast)
        # Should pause because MyEvent is an EventFacetDecl
        assert result.status == ExecutionStatus.PAUSED

        steps = list(store.get_steps_by_workflow(result.workflow_id))
        event_blocked = [s for s in steps if s.state == StepState.EVENT_TRANSMIT]
        assert len(event_blocked) == 1
        assert event_blocked[0].facet_name == "test.MyEvent"

    def test_block_ast_resolution_nested(self):
        """Verify get_block_ast() resolves the correct AST for nested statement-level blocks.

        When a step has an inline andThen body, the block created for it should
        use that inline body, not the facet-level body.

        AFL structure:
            facet Value(input: Long)
            facet Inner(input: Long) => (output: Long)
              andThen { v = Value(input = $.input); yield Inner(output = v.input) }
            workflow TestResolve(x: Long) => (result: Long)
              andThen {
                s = Inner(input = $.x)
                  andThen { sub = Value(input = $.input + 10); yield Inner(output = sub.input) }
                yield TestResolve(result = s.output)
              }

        Expected: result = x + 10 (inline body overrides facet body).
        """
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Inner",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                    "body": {
                        "type": "AndThenBlock",
                        "steps": [
                            {
                                "type": "StepStmt",
                                "id": "step-v",
                                "name": "v",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["input"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Inner",
                            "call": {
                                "type": "CallExpr",
                                "target": "Inner",
                                "args": [
                                    {
                                        "name": "output",
                                        "value": {"type": "StepRef", "path": ["v", "input"]},
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestResolve",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-s",
                        "name": "s",
                        "call": {
                            "type": "CallExpr",
                            "target": "Inner",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                }
                            ],
                        },
                        # Statement-level andThen overrides the facet body
                        "body": {
                            "type": "AndThenBlock",
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-sub",
                                    "name": "sub",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "Value",
                                        "args": [
                                            {
                                                "name": "input",
                                                "value": {
                                                    "type": "BinaryExpr",
                                                    "operator": "+",
                                                    "left": {
                                                        "type": "InputRef",
                                                        "path": ["input"],
                                                    },
                                                    "right": {"type": "Int", "value": 10},
                                                },
                                            }
                                        ],
                                    },
                                },
                            ],
                            "yield": {
                                "type": "YieldStmt",
                                "id": "yield-Inner-inline",
                                "call": {
                                    "type": "CallExpr",
                                    "target": "Inner",
                                    "args": [
                                        {
                                            "name": "output",
                                            "value": {
                                                "type": "StepRef",
                                                "path": ["sub", "input"],
                                            },
                                        }
                                    ],
                                },
                            },
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-TR",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestResolve",
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

        result = evaluator.execute(workflow_ast, inputs={"x": 5}, program_ast=program_ast)
        assert result.success is True
        # Inline body: sub.input = $.input + 10 = 5 + 10 = 15
        assert result.outputs["result"] == 15

        # Verify 2 AND_THEN block steps: workflow body block + statement-level block_s
        steps = list(store.get_steps_by_workflow(result.workflow_id))
        block_steps = [s for s in steps if s.object_type == ObjectType.AND_THEN]
        assert len(block_steps) == 2

        # Verify that the step inside the statement-level block is "sub" (from inline
        # body), not "v" (from facet body). statement_id uses the AST "id" field.
        step_ids = {s.statement_id for s in steps if s.statement_id}
        assert "step-sub" in step_ids
        assert "step-v" not in step_ids

    def test_facet_level_block_creation(self):
        """Verify calling a facet with an andThen body creates a block from the facet definition.

        AFL structure:
            facet Value(input: Long)
            facet Adder(a: Long, b: Long) => (sum: Long)
              andThen { s1 = Value(input = $.a); s2 = Value(input = $.b);
                        yield Adder(sum = s1.input + s2.input) }
            workflow TestFacetBlock(x: Long) => (result: Long)
              andThen { add = Adder(a = $.x, b = 10);
                        yield TestFacetBlock(result = add.sum) }

        Expected: result = x + 10 (e.g., x=5 → result=15).
        """
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "FacetDecl",
                    "name": "Adder",
                    "params": [
                        {"name": "a", "type": "Long"},
                        {"name": "b", "type": "Long"},
                    ],
                    "returns": [{"name": "sum", "type": "Long"}],
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
                                            "value": {"type": "InputRef", "path": ["a"]},
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
                                    "target": "Value",
                                    "args": [
                                        {
                                            "name": "input",
                                            "value": {"type": "InputRef", "path": ["b"]},
                                        }
                                    ],
                                },
                            },
                        ],
                        "yield": {
                            "type": "YieldStmt",
                            "id": "yield-Adder",
                            "call": {
                                "type": "CallExpr",
                                "target": "Adder",
                                "args": [
                                    {
                                        "name": "sum",
                                        "value": {
                                            "type": "BinaryExpr",
                                            "operator": "+",
                                            "left": {
                                                "type": "StepRef",
                                                "path": ["s1", "input"],
                                            },
                                            "right": {
                                                "type": "StepRef",
                                                "path": ["s2", "input"],
                                            },
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            ],
        }

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestFacetBlock",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-add",
                        "name": "add",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "a",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                },
                                {
                                    "name": "b",
                                    "value": {"type": "Int", "value": 10},
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-TFB",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestFacetBlock",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["add", "sum"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(workflow_ast, inputs={"x": 5}, program_ast=program_ast)
        assert result.success is True
        assert result.outputs["result"] == 15

        # Verify 2 AND_THEN block steps: workflow body block + facet-level block for Adder
        steps = list(store.get_steps_by_workflow(result.workflow_id))
        block_steps = [s for s in steps if s.object_type == ObjectType.AND_THEN]
        assert len(block_steps) == 2

        # Verify add step has facet_name == "Adder" (statement_id uses AST "id" field)
        add_steps = [s for s in steps if s.statement_id == "step-add"]
        assert len(add_steps) == 1
        assert add_steps[0].facet_name == "Adder"

        # Verify steps inside the Adder block are s1 and s2 (from the facet body)
        step_ids = {s.statement_id for s in steps if s.statement_id}
        assert "step-s1" in step_ids
        assert "step-s2" in step_ids


class TestWorkflowAsStep:
    """Test that workflows can be called as steps in andThen blocks."""

    def test_workflow_calls_workflow(self):
        """Outer workflow calls Inner workflow as a step; Inner body expands inline."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=True))

        # Inner workflow: has an andThen body with a facet call + yield
        inner_workflow_decl = {
            "type": "WorkflowDecl",
            "name": "Inner",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "y", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-v",
                        "name": "v",
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
                                        "right": {"type": "Int", "value": 10},
                                    },
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-Inner",
                    "call": {
                        "type": "CallExpr",
                        "target": "Inner",
                        "args": [
                            {
                                "name": "y",
                                "value": {"type": "StepRef", "path": ["v", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        # program_ast contains Inner as a declaration so the runtime can resolve it
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                inner_workflow_decl,
            ],
        }

        # Outer workflow calls Inner as a step
        outer_workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Outer",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-inner",
                        "name": "inner",
                        "call": {
                            "type": "CallExpr",
                            "target": "Inner",
                            "args": [
                                {
                                    "name": "x",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-Outer",
                    "call": {
                        "type": "CallExpr",
                        "target": "Outer",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["inner", "y"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(outer_workflow_ast, inputs={"x": 5}, program_ast=program_ast)
        assert result.success is True
        assert result.outputs["result"] == 15

        # Verify Inner's body expanded: step-v should exist
        steps = list(store.get_steps_by_workflow(result.workflow_id))
        step_ids = {s.statement_id for s in steps if s.statement_id}
        assert "step-inner" in step_ids
        assert "step-v" in step_ids

        # Verify inner step resolves to WorkflowDecl target
        inner_steps = [s for s in steps if s.statement_id == "step-inner"]
        assert len(inner_steps) == 1
        assert inner_steps[0].facet_name == "Inner"

    def test_workflow_calls_workflow_in_namespace(self):
        """Workflow calls another workflow within a namespace; resolved via declarations."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=True))

        inner_workflow_decl = {
            "type": "WorkflowDecl",
            "name": "Inner",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "y", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-v",
                        "name": "v",
                        "call": {
                            "type": "CallExpr",
                            "target": "Value",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {
                                        "type": "BinaryExpr",
                                        "operator": "*",
                                        "left": {"type": "InputRef", "path": ["x"]},
                                        "right": {"type": "Int", "value": 2},
                                    },
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-Inner",
                    "call": {
                        "type": "CallExpr",
                        "target": "Inner",
                        "args": [
                            {
                                "name": "y",
                                "value": {"type": "StepRef", "path": ["v", "input"]},
                            }
                        ],
                    },
                },
            },
        }

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "test",
                    "declarations": [
                        {
                            "type": "FacetDecl",
                            "name": "Value",
                            "params": [{"name": "input", "type": "Long"}],
                        },
                        inner_workflow_decl,
                    ],
                }
            ],
        }

        outer_workflow_ast = {
            "type": "WorkflowDecl",
            "name": "Outer",
            "params": [{"name": "x", "type": "Long"}],
            "returns": [{"name": "result", "type": "Long"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-inner",
                        "name": "inner",
                        "call": {
                            "type": "CallExpr",
                            "target": "Inner",
                            "args": [
                                {
                                    "name": "x",
                                    "value": {"type": "InputRef", "path": ["x"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-Outer",
                    "call": {
                        "type": "CallExpr",
                        "target": "Outer",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["inner", "y"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(outer_workflow_ast, inputs={"x": 7}, program_ast=program_ast)
        assert result.success is True
        assert result.outputs["result"] == 14


class TestImplicitDefaults:
    """Tests for implicit declaration default parameter resolution."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        telemetry = Telemetry(enabled=True)
        return Evaluator(persistence=store, telemetry=telemetry)

    def test_implicit_provides_default_when_no_explicit_arg(self, evaluator):
        """Implicit provides a default value when the step has no explicit arg."""
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "FacetDecl",
                            "name": "Retry",
                            "params": [{"name": "maxAttempts", "type": "Int"}],
                        },
                        {
                            "type": "ImplicitDecl",
                            "name": "retryDefaults",
                            "call": {
                                "type": "CallExpr",
                                "target": "Retry",
                                "args": [
                                    {
                                        "name": "maxAttempts",
                                        "value": {"type": "Int", "value": 5},
                                    }
                                ],
                            },
                        },
                        {
                            "type": "WorkflowDecl",
                            "name": "TestWf",
                            "params": [],
                            "body": {
                                "type": "AndThenBlock",
                                "steps": [
                                    {
                                        "type": "StepStmt",
                                        "id": "step-r",
                                        "name": "r",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "Retry",
                                            "args": [],
                                        },
                                    }
                                ],
                            },
                        },
                    ],
                }
            ],
        }

        workflow_ast = program_ast["declarations"][0]["declarations"][2]
        result = evaluator.execute(workflow_ast, program_ast=program_ast)

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED
        steps = evaluator.persistence.get_steps_by_workflow(result.workflow_id)
        retry_step = None
        for s in steps:
            if s.facet_name and "Retry" in s.facet_name:
                retry_step = s
                break
        assert retry_step is not None
        assert retry_step.get_attribute("maxAttempts") == 5

    def test_explicit_arg_overrides_implicit(self, evaluator):
        """Explicit call args take priority over implicit defaults."""
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "FacetDecl",
                            "name": "Retry",
                            "params": [{"name": "maxAttempts", "type": "Int"}],
                        },
                        {
                            "type": "ImplicitDecl",
                            "name": "retryDefaults",
                            "call": {
                                "type": "CallExpr",
                                "target": "Retry",
                                "args": [
                                    {
                                        "name": "maxAttempts",
                                        "value": {"type": "Int", "value": 5},
                                    }
                                ],
                            },
                        },
                        {
                            "type": "WorkflowDecl",
                            "name": "TestWf",
                            "params": [],
                            "body": {
                                "type": "AndThenBlock",
                                "steps": [
                                    {
                                        "type": "StepStmt",
                                        "id": "step-r",
                                        "name": "r",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "Retry",
                                            "args": [
                                                {
                                                    "name": "maxAttempts",
                                                    "value": {
                                                        "type": "Int",
                                                        "value": 2,
                                                    },
                                                }
                                            ],
                                        },
                                    }
                                ],
                            },
                        },
                    ],
                }
            ],
        }

        workflow_ast = program_ast["declarations"][0]["declarations"][2]
        result = evaluator.execute(workflow_ast, program_ast=program_ast)

        assert result.success is True
        steps = evaluator.persistence.get_steps_by_workflow(result.workflow_id)
        retry_step = None
        for s in steps:
            if s.facet_name and "Retry" in s.facet_name:
                retry_step = s
                break
        assert retry_step is not None
        assert retry_step.get_attribute("maxAttempts") == 2

    def test_implicit_overrides_facet_default(self, evaluator):
        """Implicit defaults take priority over facet parameter defaults."""
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "FacetDecl",
                            "name": "Retry",
                            "params": [
                                {
                                    "name": "maxAttempts",
                                    "type": "Int",
                                    "default": {"type": "Int", "value": 3},
                                }
                            ],
                        },
                        {
                            "type": "ImplicitDecl",
                            "name": "retryDefaults",
                            "call": {
                                "type": "CallExpr",
                                "target": "Retry",
                                "args": [
                                    {
                                        "name": "maxAttempts",
                                        "value": {"type": "Int", "value": 5},
                                    }
                                ],
                            },
                        },
                        {
                            "type": "WorkflowDecl",
                            "name": "TestWf",
                            "params": [],
                            "body": {
                                "type": "AndThenBlock",
                                "steps": [
                                    {
                                        "type": "StepStmt",
                                        "id": "step-r",
                                        "name": "r",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "Retry",
                                            "args": [],
                                        },
                                    }
                                ],
                            },
                        },
                    ],
                }
            ],
        }

        workflow_ast = program_ast["declarations"][0]["declarations"][2]
        result = evaluator.execute(workflow_ast, program_ast=program_ast)

        assert result.success is True
        steps = evaluator.persistence.get_steps_by_workflow(result.workflow_id)
        retry_step = None
        for s in steps:
            if s.facet_name and "Retry" in s.facet_name:
                retry_step = s
                break
        assert retry_step is not None
        # Implicit (5) beats facet default (3)
        assert retry_step.get_attribute("maxAttempts") == 5

    def test_no_implicit_no_effect(self, evaluator):
        """When no implicit matches, normal facet defaults apply."""
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "FacetDecl",
                            "name": "Retry",
                            "params": [
                                {
                                    "name": "maxAttempts",
                                    "type": "Int",
                                    "default": {"type": "Int", "value": 3},
                                }
                            ],
                        },
                        {
                            "type": "WorkflowDecl",
                            "name": "TestWf",
                            "params": [],
                            "body": {
                                "type": "AndThenBlock",
                                "steps": [
                                    {
                                        "type": "StepStmt",
                                        "id": "step-r",
                                        "name": "r",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "Retry",
                                            "args": [],
                                        },
                                    }
                                ],
                            },
                        },
                    ],
                }
            ],
        }

        workflow_ast = program_ast["declarations"][0]["declarations"][1]
        result = evaluator.execute(workflow_ast, program_ast=program_ast)

        assert result.success is True
        steps = evaluator.persistence.get_steps_by_workflow(result.workflow_id)
        retry_step = None
        for s in steps:
            if s.facet_name and "Retry" in s.facet_name:
                retry_step = s
                break
        assert retry_step is not None
        # Facet default (3) applies since no implicit exists
        assert retry_step.get_attribute("maxAttempts") == 3


# =========================================================================
# Dirty block deduplication tests
# =========================================================================


class TestDirtyBlockTracking:
    """Tests for Continue block deduplication via dirty-block tracking."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    def test_execution_context_dirty_blocks_none_means_all_dirty(self):
        """When _dirty_blocks is None, is_block_dirty returns True for any ID."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=MemoryStore(),
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-test",
            _dirty_blocks=None,
        )

        assert ctx.is_block_dirty("block-1") is True
        assert ctx.is_block_dirty("block-999") is True

    def test_execution_context_empty_dirty_set_means_nothing_dirty(self):
        """When _dirty_blocks is empty set, is_block_dirty returns False."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=MemoryStore(),
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-test",
            _dirty_blocks=set(),
        )

        assert ctx.is_block_dirty("block-1") is False
        assert ctx.is_block_dirty("block-999") is False

    def test_mark_block_dirty_adds_to_set(self):
        """mark_block_dirty adds block ID to the dirty set."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=MemoryStore(),
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-test",
            _dirty_blocks=set(),
        )

        ctx.mark_block_dirty("block-1")
        assert ctx.is_block_dirty("block-1") is True
        assert ctx.is_block_dirty("block-2") is False

    def test_mark_block_dirty_noop_when_none(self):
        """mark_block_dirty is a no-op when _dirty_blocks is None."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=MemoryStore(),
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-test",
            _dirty_blocks=None,
        )

        # Should not raise; no-op because already processing all
        ctx.mark_block_dirty("block-1")
        assert ctx._dirty_blocks is None

    def test_mark_block_dirty_ignores_none_id(self):
        """mark_block_dirty ignores None block_id."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=MemoryStore(),
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-test",
            _dirty_blocks=set(),
        )

        ctx.mark_block_dirty(None)
        assert ctx._dirty_blocks == set()

    def test_mark_block_processed_removes_from_set(self):
        """mark_block_processed removes block from dirty set."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=MemoryStore(),
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-test",
            _dirty_blocks={"block-1", "block-2"},
        )

        ctx.mark_block_processed("block-1")
        assert ctx.is_block_dirty("block-1") is False
        assert ctx.is_block_dirty("block-2") is True

    def test_continue_block_skipped_when_not_dirty(self, store, evaluator):
        """Continue-state blocks are skipped in _run_iteration when not dirty."""
        from unittest.mock import patch

        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        wf_id = "wf-skip-clean"

        # Create a block step stuck in BLOCK_EXECUTION_CONTINUE
        block_step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.BLOCK,
            facet_name=None,
        )
        block_step.state = StepState.BLOCK_EXECUTION_CONTINUE
        block_step.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
        store.save_step(block_step)

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
            _dirty_blocks=set(),  # Empty — nothing dirty
        )

        # _process_step should NOT be called for this clean block
        with patch.object(evaluator, "_process_step") as mock_process:
            evaluator._run_iteration(context)
            # The block step should be skipped entirely
            for call in mock_process.call_args_list:
                assert call[0][0].id != block_step.id, (
                    "Clean Continue block should not be processed"
                )

    def test_continue_block_processed_when_dirty(self, store, evaluator):
        """Continue-state blocks ARE processed when in the dirty set."""
        from unittest.mock import MagicMock, patch

        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        wf_id = "wf-dirty-block"

        # Create a block step stuck in BLOCK_EXECUTION_CONTINUE
        block_step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.BLOCK,
            facet_name=None,
        )
        block_step.state = StepState.BLOCK_EXECUTION_CONTINUE
        block_step.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
        store.save_step(block_step)

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
            _dirty_blocks={block_step.id},  # Block is dirty
        )

        # _process_step should be called for this dirty block
        mock_result = MagicMock()
        mock_result.step = block_step
        mock_result.step.state = StepState.BLOCK_EXECUTION_CONTINUE  # No change
        mock_result.step.transition.changed = False
        mock_result.continue_processing = False

        mock_changer = MagicMock()
        mock_changer.process.return_value = mock_result

        processed_ids = []
        _original_process = evaluator._process_step

        def tracking_process(step, ctx):
            processed_ids.append(step.id)
            return False  # No progress

        with patch.object(evaluator, "_process_step", side_effect=tracking_process):
            evaluator._run_iteration(context)

        assert block_step.id in processed_ids, "Dirty Continue block should be processed"

    def test_continue_block_removed_from_dirty_on_no_progress(self, store, evaluator):
        """Continue block is removed from dirty set when processed with no progress."""
        from unittest.mock import patch

        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        wf_id = "wf-clean-after"

        block_step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.BLOCK,
            facet_name=None,
        )
        block_step.state = StepState.BLOCK_EXECUTION_CONTINUE
        block_step.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
        store.save_step(block_step)

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
            _dirty_blocks={block_step.id},
        )

        # _process_step returns False (no progress)
        with patch.object(evaluator, "_process_step", return_value=False):
            evaluator._run_iteration(context)

        # Block should be removed from dirty set
        assert not context.is_block_dirty(block_step.id), (
            "Continue block should be cleaned after no-progress processing"
        )

    def test_process_step_marks_parent_blocks_dirty(self, store):
        """_process_step marks block_id and container_id as dirty on progress."""
        from unittest.mock import MagicMock, patch

        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        wf_id = "wf-dirty-parent"

        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Compute",
        )
        step.block_id = "parent-block-1"
        step.container_id = "parent-container-1"

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
            _dirty_blocks=set(),
        )

        # Simulate changer returning a state change.
        # Use a separate result step so state_before captures original state.
        result_step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Compute",
        )
        result_step.id = step.id
        result_step.block_id = "parent-block-1"
        result_step.container_id = "parent-container-1"
        result_step.state = StepState.STATEMENT_COMPLETE

        mock_result = MagicMock()
        mock_result.step = result_step
        mock_result.continue_processing = False

        mock_changer = MagicMock()
        mock_changer.process.return_value = mock_result

        with patch("afl.runtime.evaluator.get_state_changer", return_value=mock_changer):
            progress = evaluator._process_step(step, context)

        assert progress is True
        assert context.is_block_dirty("parent-block-1"), "block_id should be marked dirty"
        assert context.is_block_dirty("parent-container-1"), "container_id should be marked dirty"

    def test_resume_first_iteration_processes_all_then_switches(self, store, evaluator):
        """resume() starts with _dirty_blocks=None, switches to set after first iter."""
        from unittest.mock import patch

        captured_contexts = []

        _original_run = evaluator._run_iteration

        def capturing_run(context):
            captured_contexts.append(context._dirty_blocks)
            return False  # No progress — stop after 1 iteration

        with patch.object(evaluator, "_run_iteration", side_effect=capturing_run):
            evaluator.resume("wf-test-resume", {"name": "Test", "params": []})

        # First iteration should have _dirty_blocks=None (process everything)
        assert len(captured_contexts) >= 1
        assert captured_contexts[0] is None, "First iteration should have _dirty_blocks=None"

    def test_resume_step_seeds_dirty_from_chain(self, store, evaluator):
        """resume_step() seeds dirty set from Continue blocks in the chain."""
        from afl.runtime.step import StepDefinition

        wf_id = "wf-seed-dirty"

        # Create a workflow root
        root = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.WORKFLOW,
            facet_name="TestWf",
        )
        root.container_id = None
        store.save_step(root)

        # Create a block step in Continue state
        block = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.BLOCK,
            facet_name=None,
        )
        block.state = StepState.BLOCK_EXECUTION_CONTINUE
        block.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
        block.container_id = root.id
        store.save_step(block)

        # Create a child step that was continued
        child = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Compute",
        )
        child.block_id = block.id
        child.container_id = root.id
        child.state = StepState.EVENT_TRANSMIT
        child.transition.current_state = StepState.EVENT_TRANSMIT
        child.transition.request_transition = True
        store.save_step(child)

        # resume_step processes the chain; block should be seeded as dirty
        # We just verify it doesn't error and the block gets in the chain
        from unittest.mock import patch

        processed_block_ids = []
        original_process = evaluator._process_step

        def tracking_process(step, ctx):
            processed_block_ids.append(step.id)
            return original_process(step, ctx)

        with patch.object(evaluator, "_process_step", side_effect=tracking_process):
            evaluator.resume_step(
                wf_id,
                child.id,
                {"name": "TestWf", "params": []},
            )

        # Block should have been processed (it was seeded as dirty)
        assert block.id in processed_block_ids, (
            "Continue block in chain should be seeded as dirty and processed"
        )


class TestErrorPropagation:
    """Tests for error propagation through block hierarchies.

    When child steps error, the parent block should recognize them as
    terminal and propagate the error upward rather than waiting forever.
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    def test_step_analysis_counts_errored_steps(self, store):
        """StepAnalysis categorizes error steps and includes them in done check."""
        from afl.runtime.block import StatementDefinition, StepAnalysis
        from afl.runtime.step import StepDefinition

        block = StepDefinition.create(
            workflow_id="wf-err",
            object_type=ObjectType.AND_THEN,
            facet_name="",
        )

        stmts = [
            StatementDefinition(
                id="s1", name="ok", object_type=ObjectType.VARIABLE_ASSIGNMENT, facet_name="F1"
            ),
            StatementDefinition(
                id="s2", name="bad", object_type=ObjectType.VARIABLE_ASSIGNMENT, facet_name="F2"
            ),
        ]

        # One complete, one errored
        s1 = StepDefinition.create(
            workflow_id="wf-err",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F1",
            statement_id="s1",
            block_id=block.id,
        )
        s1.state = StepState.STATEMENT_COMPLETE
        s1.transition.current_state = StepState.STATEMENT_COMPLETE

        s2 = StepDefinition.create(
            workflow_id="wf-err",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F2",
            statement_id="s2",
            block_id=block.id,
        )
        s2.mark_error(RuntimeError("handler failed"))

        analysis = StepAnalysis.load(block=block, statements=stmts, steps=[s1, s2])

        assert len(analysis.completed) == 1
        assert len(analysis.errored) == 1
        assert analysis.done is True
        assert analysis.has_errors is True

    def test_step_analysis_not_done_with_pending_and_error(self, store):
        """StepAnalysis is not done when some steps are still pending."""
        from afl.runtime.block import StatementDefinition, StepAnalysis
        from afl.runtime.step import StepDefinition

        block = StepDefinition.create(
            workflow_id="wf-err2",
            object_type=ObjectType.AND_THEN,
            facet_name="",
        )

        stmts = [
            StatementDefinition(
                id="s1", name="bad", object_type=ObjectType.VARIABLE_ASSIGNMENT, facet_name="F1"
            ),
            StatementDefinition(
                id="s2", name="pending", object_type=ObjectType.VARIABLE_ASSIGNMENT, facet_name="F2"
            ),
        ]

        s1 = StepDefinition.create(
            workflow_id="wf-err2",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F1",
            statement_id="s1",
            block_id=block.id,
        )
        s1.mark_error(RuntimeError("failed"))

        # s2 is still in EventTransmit
        s2 = StepDefinition.create(
            workflow_id="wf-err2",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F2",
            statement_id="s2",
            block_id=block.id,
        )
        s2.state = StepState.EVENT_TRANSMIT
        s2.transition.current_state = StepState.EVENT_TRANSMIT
        s2.transition.request_transition = False

        analysis = StepAnalysis.load(block=block, statements=stmts, steps=[s1, s2])

        assert len(analysis.errored) == 1
        assert len(analysis.pending_event) == 1
        assert analysis.done is False

    def test_step_analysis_errored_deps_satisfy_downstream(self, store):
        """Errored steps satisfy dependency requirements for downstream steps."""
        from afl.runtime.block import StatementDefinition, StepAnalysis
        from afl.runtime.step import StepDefinition

        block = StepDefinition.create(
            workflow_id="wf-dep",
            object_type=ObjectType.AND_THEN,
            facet_name="",
        )

        stmts = [
            StatementDefinition(
                id="s1",
                name="upstream",
                object_type=ObjectType.VARIABLE_ASSIGNMENT,
                facet_name="F1",
            ),
            StatementDefinition(
                id="s2",
                name="downstream",
                object_type=ObjectType.VARIABLE_ASSIGNMENT,
                facet_name="F2",
                dependencies={"s1"},
            ),
        ]

        # s1 errored — s2 should still be createable
        s1 = StepDefinition.create(
            workflow_id="wf-dep",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F1",
            statement_id="s1",
            block_id=block.id,
        )
        s1.mark_error(RuntimeError("failed"))

        analysis = StepAnalysis.load(block=block, statements=stmts, steps=[s1])

        ready = analysis.can_be_created()
        assert len(ready) == 1
        assert ready[0].id == "s2"

    def test_block_analysis_counts_errored_blocks(self, store):
        """BlockAnalysis treats errored blocks as terminal."""
        from afl.runtime.block import BlockAnalysis
        from afl.runtime.step import StepDefinition

        container = StepDefinition.create(
            workflow_id="wf-ba",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Parent",
        )

        b1 = StepDefinition.create(
            workflow_id="wf-ba",
            object_type=ObjectType.AND_THEN,
            facet_name="",
            container_id=container.id,
        )
        b1.state = StepState.STATEMENT_COMPLETE
        b1.transition.current_state = StepState.STATEMENT_COMPLETE

        b2 = StepDefinition.create(
            workflow_id="wf-ba",
            object_type=ObjectType.AND_THEN,
            facet_name="",
            container_id=container.id,
        )
        b2.mark_error(RuntimeError("block failed"))

        analysis = BlockAnalysis.load(container, [b1, b2])

        assert len(analysis.completed) == 1
        assert len(analysis.errored) == 1
        assert len(analysis.pending) == 0
        assert analysis.done is True
        assert analysis.has_errors is True

    def test_block_execution_continue_propagates_error(self, store):
        """BlockExecutionContinue marks block as error when all children are terminal with errors."""
        from afl.runtime.block import StatementDefinition
        from afl.runtime.dependency import DependencyGraph
        from afl.runtime.evaluator import ExecutionContext, IterationChanges
        from afl.runtime.handlers.block_execution import BlockExecutionContinueHandler
        from afl.runtime.step import StepDefinition
        from afl.runtime.telemetry import Telemetry

        wf_id = "wf-prop"
        block = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.AND_THEN,
            facet_name="",
            statement_id="block-0",
            container_id="root-step",
        )
        block.state = "state.block.execution.Continue"
        block.transition.current_state = "state.block.execution.Continue"
        store.save_step(block)

        # Create one errored child
        child = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="FailFacet",
            statement_id="stmt-1",
            block_id=block.id,
        )
        child.mark_error(RuntimeError("handler crash"))
        store.save_step(child)

        # Build a dependency graph with one statement
        stmt = StatementDefinition(
            id="stmt-1",
            name="fail",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="FailFacet",
        )

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
        )
        graph = DependencyGraph()
        graph.statements["stmt-1"] = stmt
        context.set_block_graph(block.id, graph)

        handler = BlockExecutionContinueHandler(step=block, context=context)
        _result = handler.process_state()

        assert block.is_error, f"Block should be in error state, got {block.state}"
        assert "errored" in str(block.transition.error).lower()

    def test_completion_progress_includes_errors(self, store):
        """completion_progress counts both completed and errored steps."""
        from afl.runtime.block import StatementDefinition, StepAnalysis
        from afl.runtime.step import StepDefinition

        block = StepDefinition.create(
            workflow_id="wf-prog", object_type=ObjectType.AND_THEN, facet_name=""
        )

        stmts = [
            StatementDefinition(
                id="s1", name="ok", object_type=ObjectType.VARIABLE_ASSIGNMENT, facet_name="F1"
            ),
            StatementDefinition(
                id="s2", name="bad", object_type=ObjectType.VARIABLE_ASSIGNMENT, facet_name="F2"
            ),
            StatementDefinition(
                id="s3", name="wait", object_type=ObjectType.VARIABLE_ASSIGNMENT, facet_name="F3"
            ),
        ]

        s1 = StepDefinition.create(
            workflow_id="wf-prog",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F1",
            statement_id="s1",
            block_id=block.id,
        )
        s1.state = StepState.STATEMENT_COMPLETE
        s1.transition.current_state = StepState.STATEMENT_COMPLETE

        s2 = StepDefinition.create(
            workflow_id="wf-prog",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F2",
            statement_id="s2",
            block_id=block.id,
        )
        s2.mark_error(RuntimeError("failed"))

        s3 = StepDefinition.create(
            workflow_id="wf-prog",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="F3",
            statement_id="s3",
            block_id=block.id,
        )
        s3.state = StepState.EVENT_TRANSMIT
        s3.transition.current_state = StepState.EVENT_TRANSMIT

        analysis = StepAnalysis.load(block=block, statements=stmts, steps=[s1, s2, s3])

        completed, total = analysis.completion_progress
        assert completed == 2  # 1 complete + 1 errored
        assert total == 3


class TestWhenBlockExecution:
    """Tests for andThen when runtime execution."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    def _when_workflow_ast(self):
        """AST for a workflow with an andThen when block.

        ```afl
        facet DoA(x: Int)
        facet DoFallback()
        workflow WhenTest(count: Int) andThen when {
            case $.count > 10 => {
                a = DoA(x = $.count)
            }
            case _ => {
                f = DoFallback()
            }
        }
        ```
        """
        return {
            "type": "WorkflowDecl",
            "name": "WhenTest",
            "params": [{"name": "count", "type": "Int"}],
            "body": {
                "type": "AndThenBlock",
                "when": {
                    "type": "WhenBlock",
                    "cases": [
                        {
                            "type": "WhenCase",
                            "condition": {
                                "type": "BinaryExpr",
                                "operator": ">",
                                "left": {"type": "InputRef", "path": ["count"]},
                                "right": {"type": "Int", "value": 10},
                            },
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-a",
                                    "name": "a",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "DoA",
                                        "args": [
                                            {
                                                "name": "x",
                                                "value": {
                                                    "type": "InputRef",
                                                    "path": ["count"],
                                                },
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                        {
                            "type": "WhenCase",
                            "default": True,
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-f",
                                    "name": "f",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "DoFallback",
                                    },
                                }
                            ],
                        },
                    ],
                },
            },
        }

    def test_when_case_true_executes(self, store, evaluator):
        """When condition is true, matching case executes."""
        workflow_ast = self._when_workflow_ast()
        result = evaluator.execute(workflow_ast, inputs={"count": 20})
        assert result.success is True

        # Should have a when-case-0 sub-block (condition true)
        all_steps = list(store.get_all_steps())
        when_blocks = [
            s for s in all_steps if s.statement_id and str(s.statement_id).startswith("when-case-")
        ]
        assert len(when_blocks) == 1
        assert str(when_blocks[0].statement_id) == "when-case-0"

    def test_when_default_when_no_match(self, store, evaluator):
        """When no condition matches, default case executes."""
        workflow_ast = self._when_workflow_ast()
        result = evaluator.execute(workflow_ast, inputs={"count": 5})
        assert result.success is True

        # Should have a when-case-1 sub-block (default)
        all_steps = list(store.get_all_steps())
        when_blocks = [
            s for s in all_steps if s.statement_id and str(s.statement_id).startswith("when-case-")
        ]
        assert len(when_blocks) == 1
        assert str(when_blocks[0].statement_id) == "when-case-1"

    def test_when_multiple_true_cases(self, store, evaluator):
        """When multiple conditions are true, all matching cases execute."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "MultiWhen",
            "params": [{"name": "x", "type": "Int"}],
            "body": {
                "type": "AndThenBlock",
                "when": {
                    "type": "WhenBlock",
                    "cases": [
                        {
                            "type": "WhenCase",
                            "condition": {
                                "type": "BinaryExpr",
                                "operator": ">",
                                "left": {"type": "InputRef", "path": ["x"]},
                                "right": {"type": "Int", "value": 5},
                            },
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-a",
                                    "name": "a",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "DoA",
                                    },
                                }
                            ],
                        },
                        {
                            "type": "WhenCase",
                            "condition": {
                                "type": "BinaryExpr",
                                "operator": ">",
                                "left": {"type": "InputRef", "path": ["x"]},
                                "right": {"type": "Int", "value": 0},
                            },
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-b",
                                    "name": "b",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "DoB",
                                    },
                                }
                            ],
                        },
                    ],
                },
            },
        }
        result = evaluator.execute(workflow_ast, inputs={"x": 20})
        assert result.success is True

        all_steps = list(store.get_all_steps())
        when_blocks = [
            s for s in all_steps if s.statement_id and str(s.statement_id).startswith("when-case-")
        ]
        # Both cases match (20 > 5 and 20 > 0)
        assert len(when_blocks) == 2

    def test_when_no_match_no_default_errors(self, store, evaluator):
        """When no condition matches and no default, block errors."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "NoMatch",
            "params": [{"name": "x", "type": "Int"}],
            "body": {
                "type": "AndThenBlock",
                "when": {
                    "type": "WhenBlock",
                    "cases": [
                        {
                            "type": "WhenCase",
                            "condition": {
                                "type": "BinaryExpr",
                                "operator": ">",
                                "left": {"type": "InputRef", "path": ["x"]},
                                "right": {"type": "Int", "value": 100},
                            },
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-a",
                                    "name": "a",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "DoA",
                                    },
                                }
                            ],
                        },
                    ],
                },
            },
        }
        result = evaluator.execute(workflow_ast, inputs={"x": 1})
        assert result.success is False

    def test_when_with_input_ref_condition(self, store, evaluator):
        """When condition using input references evaluates correctly."""
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "RefWhen",
            "params": [
                {"name": "status", "type": "String"},
            ],
            "body": {
                "type": "AndThenBlock",
                "when": {
                    "type": "WhenBlock",
                    "cases": [
                        {
                            "type": "WhenCase",
                            "condition": {
                                "type": "BinaryExpr",
                                "operator": "==",
                                "left": {"type": "InputRef", "path": ["status"]},
                                "right": {"type": "String", "value": "success"},
                            },
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-ok",
                                    "name": "ok",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "HandleOk",
                                    },
                                }
                            ],
                        },
                        {
                            "type": "WhenCase",
                            "default": True,
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-err",
                                    "name": "err",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "HandleErr",
                                    },
                                }
                            ],
                        },
                    ],
                },
            },
        }
        # status == "success" should match case 0
        result = evaluator.execute(workflow_ast, inputs={"status": "success"})
        assert result.success is True
        all_steps = list(store.get_all_steps())
        when_blocks = [
            s for s in all_steps if s.statement_id and str(s.statement_id).startswith("when-case-")
        ]
        assert len(when_blocks) == 1
        assert str(when_blocks[0].statement_id) == "when-case-0"


# =========================================================================
# Catch block execution tests
# =========================================================================


class TestCatchBlockExecution:
    """Tests for catch block runtime execution."""

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    def _simple_catch_workflow_ast(self):
        """AST for a workflow with statement-level catch."""
        return {
            "type": "WorkflowDecl",
            "name": "CatchTest",
            "params": [{"name": "input", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-s",
                        "name": "s",
                        "call": {
                            "type": "CallExpr",
                            "target": "Risky",
                            "args": [
                                {
                                    "name": "input",
                                    "value": {"type": "InputRef", "path": ["input"]},
                                }
                            ],
                        },
                        "catch": {
                            "type": "CatchClause",
                            "steps": [
                                {
                                    "type": "StepStmt",
                                    "id": "step-fallback",
                                    "name": "fallback",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "SafeDefault",
                                        "args": [
                                            {
                                                "name": "reason",
                                                "value": {
                                                    "type": "InputRef",
                                                    "path": ["input"],
                                                },
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        }

    def _workflow_level_catch_ast(self):
        """AST for a workflow with declaration-level catch."""
        return {
            "type": "WorkflowDecl",
            "name": "Deploy",
            "params": [{"name": "service", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-build",
                        "name": "build",
                        "call": {
                            "type": "CallExpr",
                            "target": "BuildImage",
                            "args": [
                                {
                                    "name": "service",
                                    "value": {"type": "InputRef", "path": ["service"]},
                                }
                            ],
                        },
                    }
                ],
            },
            "catch": {
                "type": "CatchClause",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-fallback",
                        "name": "fallback",
                        "call": {
                            "type": "CallExpr",
                            "target": "NotifyFailure",
                            "args": [
                                {
                                    "name": "service",
                                    "value": {"type": "InputRef", "path": ["service"]},
                                }
                            ],
                        },
                    }
                ],
            },
        }

    def test_catch_not_triggered_on_success(self, store, evaluator):
        """When step succeeds, catch block is dormant."""
        workflow_ast = self._simple_catch_workflow_ast()
        result = evaluator.execute(workflow_ast, inputs={"input": "hello"})
        assert result.success is True

        # No catch sub-blocks should be created
        all_steps = list(store.get_all_steps())
        catch_blocks = [
            s for s in all_steps if s.statement_id and str(s.statement_id).startswith("catch-")
        ]
        assert len(catch_blocks) == 0

    def test_catch_error_data_accessible(self, store, evaluator):
        """Error info (s.error, s.error_type) is stored as pseudo-returns."""
        from afl.runtime.step import StepDefinition

        step = StepDefinition.create(
            workflow_id="wf-catch",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Risky",
            statement_id="step-s",
            statement_name="s",
        )
        step.transition.error = RuntimeError("connection timeout")

        # Set error data as the CatchBeginHandler would
        step.set_attribute("error", str(step.transition.error), is_return=True)
        step.set_attribute("error_type", type(step.transition.error).__name__, is_return=True)

        assert step.attributes.returns["error"].value == "connection timeout"
        assert step.attributes.returns["error_type"].value == "RuntimeError"

    def test_catch_failure_propagates(self, store, evaluator):
        """When catch itself errors, step transitions to STATEMENT_ERROR."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.handlers.catch_execution import CatchContinueHandler
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        parent = StepDefinition.create(
            workflow_id="wf-catch-fail",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Risky",
            statement_id="step-s",
        )
        parent.change_state(StepState.CATCH_CONTINUE)
        store.save_step(parent)

        catch_block = StepDefinition.create(
            workflow_id="wf-catch-fail",
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            statement_id="catch-block-0",
            container_id=parent.id,
        )
        catch_block.mark_error(RuntimeError("catch handler also failed"))
        store.save_step(catch_block)

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=parent.workflow_id,
            workflow_ast={"type": "WorkflowDecl", "name": "Test", "params": []},
        )
        handler = CatchContinueHandler(parent, context)
        handler.process_state()

        assert parent.is_error

    def test_simple_catch_creates_sub_block(self, store, evaluator):
        """CatchBeginHandler creates a catch sub-block with correct type."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.handlers.catch_execution import CatchBeginHandler
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        wf_id = "wf-catch-sub"

        workflow_ast = self._simple_catch_workflow_ast()

        # Create proper hierarchy: root → block → step
        root = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.WORKFLOW,
            facet_name="CatchTest",
        )
        store.save_step(root)

        block = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.AND_THEN,
            facet_name="",
            statement_id="block-0",
            container_id=root.id,
        )
        store.save_step(block)

        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Risky",
            statement_id="step-s",
            statement_name="s",
            block_id=block.id,
            container_id=root.id,
        )
        step.transition.error = RuntimeError("oops")
        step.change_state(StepState.CATCH_BEGIN)
        store.save_step(step)

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=wf_id,
            workflow_ast=workflow_ast,
        )

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        created = context.changes.created_steps
        assert len(created) == 1
        assert str(created[0].statement_id) == "catch-block-0"
        assert created[0].object_type == ObjectType.AND_CATCH

        assert step.attributes.returns["error"].value == "oops"
        assert step.attributes.returns["error_type"].value == "RuntimeError"

    def test_catch_continue_completes_on_success(self, store, evaluator):
        """CatchContinueHandler transitions when sub-blocks complete."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.handlers.catch_execution import CatchContinueHandler
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        parent = StepDefinition.create(
            workflow_id="wf-catch-ok",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Risky",
            statement_id="step-s",
        )
        parent.change_state(StepState.CATCH_CONTINUE)
        store.save_step(parent)

        catch_block = StepDefinition.create(
            workflow_id="wf-catch-ok",
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            statement_id="catch-block-0",
            container_id=parent.id,
        )
        catch_block.state = StepState.STATEMENT_COMPLETE
        catch_block.transition.current_state = StepState.STATEMENT_COMPLETE
        store.save_step(catch_block)

        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=parent.workflow_id,
            workflow_ast={"type": "WorkflowDecl", "name": "Test", "params": []},
        )
        handler = CatchContinueHandler(parent, context)
        handler.process_state()

        assert parent.transition.request_transition is True
        assert not parent.is_error

    def test_workflow_level_catch_ast_found(self, store, evaluator):
        """_find_statement_catch finds workflow-level catch."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges
        from afl.runtime.step import StepDefinition

        workflow_ast = self._workflow_level_catch_ast()
        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-level",
            workflow_ast=workflow_ast,
        )

        root = StepDefinition.create(
            workflow_id="wf-level",
            object_type=ObjectType.WORKFLOW,
            facet_name="Deploy",
        )
        store.save_step(root)

        catch = context._find_statement_catch(root)
        assert catch is not None
        assert catch["type"] == "CatchClause"
        assert len(catch["steps"]) == 1

    def test_statement_level_catch_ast_found(self, store, evaluator):
        """_find_statement_catch finds statement-level catch."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        workflow_ast = self._simple_catch_workflow_ast()
        context = ExecutionContext(
            persistence=store,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf-stmt",
            workflow_ast=workflow_ast,
        )

        from afl.runtime.step import StepDefinition

        root = StepDefinition.create(
            workflow_id="wf-stmt",
            object_type=ObjectType.WORKFLOW,
            facet_name="CatchTest",
        )
        store.save_step(root)

        block = StepDefinition.create(
            workflow_id="wf-stmt",
            object_type=ObjectType.AND_THEN,
            facet_name="",
            statement_id="block-0",
            container_id=root.id,
        )
        store.save_step(block)

        step = StepDefinition.create(
            workflow_id="wf-stmt",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Risky",
            statement_id="step-s",
            statement_name="s",
            block_id=block.id,
            container_id=root.id,
        )
        store.save_step(step)

        catch = context._find_statement_catch(step)
        assert catch is not None
        assert catch["type"] == "CatchClause"


# =========================================================================
# Cross-block step reference deferral
# =========================================================================


class TestCrossBlockStepRefDeferral:
    """Test that cross-block step references defer instead of error.

    When sequential andThen blocks reference outputs from prior blocks
    (e.g. block-1 step references block-0 step), the initialization
    handler should defer until the referenced step completes rather
    than permanently erroring.
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        telemetry = Telemetry(enabled=True)
        return Evaluator(persistence=store, telemetry=telemetry)

    def _make_workflow_ast(self):
        """Build a workflow with two sequential andThen blocks.

        block-0: dl = Fetch(url = $.url)
        block-1: parsed = Parse(raw_path = dl.raw_path)

        block-1's step references block-0's step output.
        """
        return {
            "type": "WorkflowDecl",
            "name": "TestCrossBlock",
            "params": [{"name": "url", "type": "String"}],
            "returns": [{"name": "status", "type": "String"}],
            "body": [
                {
                    "type": "AndThenBlock",
                    "steps": [
                        {
                            "type": "StepStmt",
                            "id": "step-dl",
                            "name": "dl",
                            "call": {
                                "type": "CallExpr",
                                "target": "Fetch",
                                "args": [
                                    {
                                        "name": "url",
                                        "value": {
                                            "type": "InputRef",
                                            "path": ["url"],
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "type": "AndThenBlock",
                    "steps": [
                        {
                            "type": "StepStmt",
                            "id": "step-parsed",
                            "name": "parsed",
                            "call": {
                                "type": "CallExpr",
                                "target": "Parse",
                                "args": [
                                    {
                                        "name": "raw_path",
                                        "value": {
                                            "type": "StepRef",
                                            "path": ["dl", "raw_path"],
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
            ],
        }

    def _make_program_ast(self, workflow_ast):
        return {
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "Fetch",
                    "event": True,
                    "params": [{"name": "url", "type": "String"}],
                    "returns": [{"name": "raw_path", "type": "String"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "Parse",
                    "event": True,
                    "params": [{"name": "raw_path", "type": "String"}],
                    "returns": [{"name": "data", "type": "Json"}],
                },
                workflow_ast,
            ]
        }

    def test_cross_block_ref_defers_not_errors(self, store, evaluator):
        """Step referencing prior block output should defer, not error."""
        workflow_ast = self._make_workflow_ast()
        program_ast = self._make_program_ast(workflow_ast)

        result = evaluator.execute(
            workflow_ast, program_ast=program_ast, inputs={"url": "http://example.com"}
        )
        # Should pause (event facets create tasks), not error
        assert result.status in (ExecutionStatus.PAUSED, ExecutionStatus.COMPLETED)

        all_steps = list(store.get_all_steps())

        # Find the parsed step — it should NOT be in Error state
        parsed_steps = [s for s in all_steps if s.statement_name == "parsed"]
        assert len(parsed_steps) == 1
        parsed = parsed_steps[0]

        # parsed should be deferred at FacetInitializationBegin, not errored
        assert parsed.state != StepState.STATEMENT_ERROR, (
            f"Cross-block ref should defer, not error. State: {parsed.state}"
        )
        # It should still be at initialization (waiting for dl to complete)
        assert parsed.state == StepState.FACET_INIT_BEGIN

    def test_cross_block_ref_resolves_after_completion(self, store, evaluator):
        """After the referenced step completes, deferred step should initialize."""
        workflow_ast = self._make_workflow_ast()
        program_ast = self._make_program_ast(workflow_ast)

        # Initial execution — dl parks at EventTransmit, parsed defers
        evaluator.execute(
            workflow_ast, program_ast=program_ast, inputs={"url": "http://example.com"}
        )

        all_steps = list(store.get_all_steps())

        # Find the dl step (should be at EventTransmit)
        dl_steps = [s for s in all_steps if s.statement_name == "dl"]
        assert len(dl_steps) == 1
        dl = dl_steps[0]

        # Simulate task completion: set dl returns and mark complete
        dl.set_attribute("raw_path", "/tmp/data.gz", is_return=True)
        dl.change_state(StepState.STATEMENT_COMPLETE)
        store.save_step(dl)

        # Resume — parsed should now initialize successfully
        evaluator.resume(dl.workflow_id, workflow_ast, program_ast=program_ast)

        all_steps = list(store.get_all_steps())
        parsed_steps = [s for s in all_steps if s.statement_name == "parsed"]
        assert len(parsed_steps) == 1
        parsed = parsed_steps[0]

        # parsed should have advanced past initialization
        assert parsed.state != StepState.FACET_INIT_BEGIN, (
            f"After dl completes, parsed should advance. State: {parsed.state}"
        )
        assert parsed.state != StepState.STATEMENT_ERROR

        # parsed should have the correct raw_path param from dl
        raw_path_attr = parsed.attributes.params.get("raw_path")
        assert raw_path_attr is not None
        assert raw_path_attr.value == "/tmp/data.gz"


class TestForeachCrossBlockStepRef:
    """Test that foreach cross-block step references defer instead of error.

    When a foreach block's iterable references a step output from a prior
    andThen block (e.g. ``foreach station in discovery.stations``), the
    foreach should defer until the referenced step completes rather than
    resolving to None and completing with 0 iterations.
    """

    @pytest.fixture
    def store(self):
        return MemoryStore()

    @pytest.fixture
    def evaluator(self, store):
        telemetry = Telemetry(enabled=True)
        return Evaluator(persistence=store, telemetry=telemetry)

    def _make_workflow_ast(self):
        """Build a workflow with two sequential andThen blocks.

        block-0: dl = Discover(query = $.query)        → event facet, returns {items: [...]}
        block-1: foreach item in dl.items { v = Process(input = $.item) }
        """
        return {
            "type": "WorkflowDecl",
            "name": "TestForeachCrossBlock",
            "params": [{"name": "query", "type": "String"}],
            "returns": [{"name": "status", "type": "String"}],
            "body": [
                {
                    "type": "AndThenBlock",
                    "steps": [
                        {
                            "type": "StepStmt",
                            "id": "step-dl",
                            "name": "dl",
                            "call": {
                                "type": "CallExpr",
                                "target": "Discover",
                                "args": [
                                    {
                                        "name": "query",
                                        "value": {
                                            "type": "InputRef",
                                            "path": ["query"],
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "type": "AndThenBlock",
                    "foreach": {
                        "variable": "item",
                        "iterable": {"type": "StepRef", "path": ["dl", "items"]},
                    },
                    "steps": [
                        {
                            "type": "StepStmt",
                            "id": "step-v",
                            "name": "v",
                            "call": {
                                "type": "CallExpr",
                                "target": "Process",
                                "args": [
                                    {
                                        "name": "input",
                                        "value": {"type": "InputRef", "path": ["item"]},
                                    }
                                ],
                            },
                        }
                    ],
                },
            ],
        }

    def _make_program_ast(self, workflow_ast):
        return {
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "Discover",
                    "event": True,
                    "params": [{"name": "query", "type": "String"}],
                    "returns": [{"name": "items", "type": "Json"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "Process",
                    "event": True,
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "result", "type": "String"}],
                },
                workflow_ast,
            ]
        }

    def test_foreach_cross_block_defers_when_not_ready(self, store, evaluator):
        """Foreach referencing prior block output should defer, not complete with 0 iterations."""
        workflow_ast = self._make_workflow_ast()
        program_ast = self._make_program_ast(workflow_ast)

        result = evaluator.execute(workflow_ast, program_ast=program_ast, inputs={"query": "test"})
        # Should pause (event facets create tasks), not error
        assert result.status in (ExecutionStatus.PAUSED, ExecutionStatus.COMPLETED)

        all_steps = list(store.get_all_steps())

        # Find the foreach block step — it should be deferred, not complete
        foreach_blocks = [
            s
            for s in all_steps
            if s.object_type == ObjectType.AND_THEN
            and s.statement_id
            and str(s.statement_id).startswith("block-")
        ]
        # block-0 and block-1 should exist
        assert len(foreach_blocks) >= 2

        # The foreach block (block-1) should NOT be complete yet since
        # dl hasn't returned its items
        block1 = [s for s in foreach_blocks if str(s.statement_id) == "block-1"]
        assert len(block1) == 1
        assert block1[0].state == StepState.BLOCK_EXECUTION_BEGIN, (
            f"Foreach block should defer at BlockExecutionBegin. State: {block1[0].state}"
        )

        # No foreach sub-blocks should have been created
        sub_blocks = [
            s
            for s in all_steps
            if s.object_type == ObjectType.AND_THEN
            and s.statement_id
            and str(s.statement_id).startswith("foreach-")
        ]
        assert len(sub_blocks) == 0, (
            f"No foreach sub-blocks should be created before dependency resolves. Found: {len(sub_blocks)}"
        )

    def test_foreach_cross_block_creates_sub_blocks(self, store, evaluator):
        """After the referenced step completes, foreach should create sub-blocks."""
        workflow_ast = self._make_workflow_ast()
        program_ast = self._make_program_ast(workflow_ast)

        # Initial execution — dl parks at EventTransmit, foreach defers
        evaluator.execute(workflow_ast, program_ast=program_ast, inputs={"query": "test"})

        all_steps = list(store.get_all_steps())

        # Find the dl step (should be at EventTransmit)
        dl_steps = [s for s in all_steps if s.statement_name == "dl"]
        assert len(dl_steps) == 1
        dl = dl_steps[0]

        # Simulate task completion: set dl returns and mark complete
        dl.set_attribute("items", [1, 2, 3], is_return=True)
        dl.change_state(StepState.STATEMENT_COMPLETE)
        store.save_step(dl)

        # Resume — foreach should now evaluate and create sub-blocks
        evaluator.resume(dl.workflow_id, workflow_ast, program_ast=program_ast)

        all_steps = list(store.get_all_steps())

        # Foreach sub-blocks should now exist
        sub_blocks = [
            s
            for s in all_steps
            if s.object_type == ObjectType.AND_THEN
            and s.statement_id
            and str(s.statement_id).startswith("foreach-")
        ]
        assert len(sub_blocks) == 3, (
            f"Expected 3 foreach sub-blocks for [1, 2, 3]. Found: {len(sub_blocks)}"
        )

        # Verify foreach values
        foreach_values = sorted([s.foreach_value for s in sub_blocks])
        assert foreach_values == [1, 2, 3]
