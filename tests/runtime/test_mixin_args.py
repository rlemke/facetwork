"""Tests for call-site mixin argument evaluation in the runtime.

Covers:
- Flat merge of non-aliased mixin args
- Nested dict for aliased mixin args
- Non-override of explicit call args
- Multiple mixins (one aliased, one flat)
- Step reference dependencies from mixin args
- Literal-only mixin args (no dependency)
- Backward compat (no mixins field)
- Implicit fallback for params not in call or mixin
"""

from __future__ import annotations

import pytest

from afl.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
from afl.runtime.agent_poller import AgentPoller, AgentPollerConfig
from afl.runtime.block import StatementDefinition
from afl.runtime.dependency import DependencyGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evaluator():
    store = MemoryStore()
    return Evaluator(persistence=store, telemetry=Telemetry(enabled=False)), store


def _make_poller(store, evaluator, handlers: dict):
    poller = AgentPoller(
        persistence=store,
        evaluator=evaluator,
        config=AgentPollerConfig(service_name="test-mixin"),
    )
    for name, fn in handlers.items():
        poller.register(name, fn)
    return poller


# ---------------------------------------------------------------------------
# TestCallSiteMixinArgs
# ---------------------------------------------------------------------------

class TestCallSiteMixinArgs:
    """Unit + integration tests for call-site mixin args and aliases."""

    def test_callsite_mixin_args_flat_merge(self):
        """Non-aliased mixin args merge into step params."""
        evaluator, store = _make_evaluator()

        def handle_do(params):
            return {"got_x": params.get("x"), "got_retry": params.get("max_retries")}

        poller = _make_poller(store, evaluator, {"ns.DoWork": handle_do})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [{"name": "val", "type": "Int"}],
            "returns": [{"name": "result", "type": "Json"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-do",
                        "name": "d",
                        "call": {
                            "type": "CallExpr",
                            "target": "DoWork",
                            "args": [
                                {"name": "x", "value": {"type": "InputRef", "path": ["val"]}},
                            ],
                            "mixins": [
                                {
                                    "type": "MixinCall",
                                    "target": "RetryPolicy",
                                    "args": [
                                        {"name": "max_retries", "value": {"type": "Int", "value": 5}},
                                    ],
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-W",
                    "call": {
                        "type": "CallExpr",
                        "target": "W",
                        "args": [
                            {"name": "result", "value": {"type": "StepRef", "path": ["d", "got_x"]}},
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
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "DoWork",
                            "params": [
                                {"name": "x", "type": "Int"},
                                {"name": "max_retries", "type": "Int"},
                            ],
                            "returns": [
                                {"name": "got_x", "type": "Int"},
                                {"name": "got_retry", "type": "Int"},
                            ],
                        },
                    ],
                },
            ],
        }

        result = evaluator.execute(workflow_ast, inputs={"val": 42}, program_ast=program_ast)
        assert result.status == ExecutionStatus.PAUSED

        poller.poll_once()
        final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
        assert final.success
        assert final.outputs["result"] == 42

    def test_callsite_mixin_args_alias_nested(self):
        """Aliased mixin creates nested dict keyed by alias."""
        evaluator, store = _make_evaluator()

        def handle_do(params):
            retry = params.get("retry", {})
            return {"max": retry.get("max_retries", 0)}

        poller = _make_poller(store, evaluator, {"ns.DoWork": handle_do})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "result", "type": "Int"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-do",
                        "name": "d",
                        "call": {
                            "type": "CallExpr",
                            "target": "DoWork",
                            "args": [],
                            "mixins": [
                                {
                                    "type": "MixinCall",
                                    "target": "RetryPolicy",
                                    "args": [
                                        {"name": "max_retries", "value": {"type": "Int", "value": 3}},
                                    ],
                                    "alias": "retry",
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-W",
                    "call": {
                        "type": "CallExpr",
                        "target": "W",
                        "args": [
                            {"name": "result", "value": {"type": "StepRef", "path": ["d", "max"]}},
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
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "DoWork",
                            "params": [{"name": "retry", "type": "Json"}],
                            "returns": [{"name": "max", "type": "Int"}],
                        },
                    ],
                },
            ],
        }

        result = evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        poller.poll_once()
        final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
        assert final.success
        assert final.outputs["result"] == 3

    def test_callsite_mixin_no_override(self):
        """Mixin args don't override explicit call args."""
        evaluator, store = _make_evaluator()

        def handle_do(params):
            return {"got": params.get("x")}

        poller = _make_poller(store, evaluator, {"ns.DoWork": handle_do})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "result", "type": "Int"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-do",
                        "name": "d",
                        "call": {
                            "type": "CallExpr",
                            "target": "DoWork",
                            "args": [
                                {"name": "x", "value": {"type": "Int", "value": 10}},
                            ],
                            "mixins": [
                                {
                                    "type": "MixinCall",
                                    "target": "Mixin",
                                    "args": [
                                        {"name": "x", "value": {"type": "Int", "value": 99}},
                                    ],
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-W",
                    "call": {
                        "type": "CallExpr",
                        "target": "W",
                        "args": [
                            {"name": "result", "value": {"type": "StepRef", "path": ["d", "got"]}},
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
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "DoWork",
                            "params": [{"name": "x", "type": "Int"}],
                            "returns": [{"name": "got", "type": "Int"}],
                        },
                    ],
                },
            ],
        }

        result = evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        poller.poll_once()
        final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
        assert final.success
        assert final.outputs["result"] == 10  # call arg wins

    def test_callsite_mixin_multiple(self):
        """Two mixins: one aliased, one flat."""
        evaluator, store = _make_evaluator()

        def handle_do(params):
            retry = params.get("retry", {})
            channel = params.get("channel", "default")
            return {"r": retry.get("max_retries", 0), "ch": channel}

        poller = _make_poller(store, evaluator, {"ns.DoWork": handle_do})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-do",
                        "name": "d",
                        "call": {
                            "type": "CallExpr",
                            "target": "DoWork",
                            "args": [],
                            "mixins": [
                                {
                                    "type": "MixinCall",
                                    "target": "RetryPolicy",
                                    "args": [
                                        {"name": "max_retries", "value": {"type": "Int", "value": 5}},
                                    ],
                                    "alias": "retry",
                                },
                                {
                                    "type": "MixinCall",
                                    "target": "AlertConfig",
                                    "args": [
                                        {"name": "channel", "value": {"type": "String", "value": "alerts"}},
                                    ],
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-W",
                    "call": {
                        "type": "CallExpr",
                        "target": "W",
                        "args": [
                            {"name": "result", "value": {"type": "StepRef", "path": ["d", "ch"]}},
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
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "DoWork",
                            "params": [
                                {"name": "retry", "type": "Json"},
                                {"name": "channel", "type": "String"},
                            ],
                            "returns": [
                                {"name": "r", "type": "Int"},
                                {"name": "ch", "type": "String"},
                            ],
                        },
                    ],
                },
            ],
        }

        result = evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        poller.poll_once()
        final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
        assert final.success
        assert final.outputs["result"] == "alerts"

    def test_callsite_mixin_step_ref_dependency(self):
        """Mixin arg with step reference creates dependency in DependencyGraph."""
        block_ast = {
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-a",
                    "name": "a",
                    "call": {
                        "type": "CallExpr",
                        "target": "FacetA",
                        "args": [
                            {"name": "x", "value": {"type": "Int", "value": 1}},
                        ],
                    },
                },
                {
                    "type": "StepStmt",
                    "id": "step-b",
                    "name": "b",
                    "call": {
                        "type": "CallExpr",
                        "target": "FacetB",
                        "args": [],
                        "mixins": [
                            {
                                "type": "MixinCall",
                                "target": "Mixin",
                                "args": [
                                    {"name": "val", "value": {"type": "StepRef", "path": ["a", "out"]}},
                                ],
                            },
                        ],
                    },
                },
            ],
        }

        graph = DependencyGraph.from_ast(block_ast, workflow_inputs=set())
        assert "step-a" in graph.dependencies["step-b"]

    def test_callsite_mixin_literal_only_no_dep(self):
        """Mixin arg with literal value has no dependency."""
        block_ast = {
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-a",
                    "name": "a",
                    "call": {
                        "type": "CallExpr",
                        "target": "FacetA",
                        "args": [],
                        "mixins": [
                            {
                                "type": "MixinCall",
                                "target": "Mixin",
                                "args": [
                                    {"name": "x", "value": {"type": "Int", "value": 5}},
                                ],
                            },
                        ],
                    },
                },
            ],
        }

        graph = DependencyGraph.from_ast(block_ast, workflow_inputs=set())
        assert graph.dependencies["step-a"] == set()

    def test_callsite_mixin_backward_compat(self):
        """Statement without mixins field works (empty list default)."""
        stmt = StatementDefinition(
            id="s1",
            name="step1",
            object_type="VariableAssignment",
            facet_name="ns.Facet",
            args=[],
        )
        assert stmt.mixins == []

    def test_callsite_mixin_implicit_fallback(self):
        """Implicit defaults still apply for params not in call or mixin."""
        evaluator, store = _make_evaluator()

        def handle_do(params):
            return {"x": params.get("x"), "level": params.get("level")}

        poller = _make_poller(store, evaluator, {"ns.DoWork": handle_do})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-do",
                        "name": "d",
                        "call": {
                            "type": "CallExpr",
                            "target": "DoWork",
                            "args": [
                                {"name": "x", "value": {"type": "Int", "value": 1}},
                            ],
                            "mixins": [
                                {
                                    "type": "MixinCall",
                                    "target": "Retry",
                                    "args": [
                                        {"name": "retries", "value": {"type": "Int", "value": 3}},
                                    ],
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-W",
                    "call": {
                        "type": "CallExpr",
                        "target": "W",
                        "args": [
                            {"name": "result", "value": {"type": "StepRef", "path": ["d", "level"]}},
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
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "DoWork",
                            "params": [
                                {"name": "x", "type": "Int"},
                                {"name": "level", "type": "String"},
                            ],
                            "returns": [
                                {"name": "x", "type": "Int"},
                                {"name": "level", "type": "String"},
                            ],
                        },
                        {
                            "type": "ImplicitDecl",
                            "name": "defaultLevel",
                            "call": {
                                "type": "CallExpr",
                                "target": "DoWork",
                                "args": [
                                    {"name": "level", "value": {"type": "String", "value": "info"}},
                                ],
                            },
                        },
                    ],
                },
            ],
        }

        result = evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        poller.poll_once()
        final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
        assert final.success
        assert final.outputs["result"] == "info"
