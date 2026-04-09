"""Tests for call-site mixin argument evaluation in the runtime.

Covers:
- Flat merge of non-aliased mixin args
- Nested dict for aliased mixin args
- Non-override of explicit call args
- Multiple mixins (one aliased, one flat)
- Step reference dependencies from mixin args
- Literal-only mixin args (no dependency)
- Backward compat (no mixins field)
- Timeout mixin extraction to task.timeout_ms
- Implicit fallback for params not in call or mixin
"""

from __future__ import annotations

from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig
from facetwork.runtime.block import StatementDefinition
from facetwork.runtime.dependency import DependencyGraph

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
                                        {
                                            "name": "max_retries",
                                            "value": {"type": "Int", "value": 5},
                                        },
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
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["d", "got_x"]},
                            },
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
                                        {
                                            "name": "max_retries",
                                            "value": {"type": "Int", "value": 3},
                                        },
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
                                        {
                                            "name": "max_retries",
                                            "value": {"type": "Int", "value": 5},
                                        },
                                    ],
                                    "alias": "retry",
                                },
                                {
                                    "type": "MixinCall",
                                    "target": "AlertConfig",
                                    "args": [
                                        {
                                            "name": "channel",
                                            "value": {"type": "String", "value": "alerts"},
                                        },
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
                                    {
                                        "name": "val",
                                        "value": {"type": "StepRef", "path": ["a", "out"]},
                                    },
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
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["d", "level"]},
                            },
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


# ---------------------------------------------------------------------------
# TestTimeoutMixin
# ---------------------------------------------------------------------------


class TestTimeoutMixin:
    """Tests for Timeout mixin extraction to task.timeout_ms."""

    def test_facet_level_timeout_mixin(self):
        """Timeout mixin on event facet sets task.timeout_ms."""
        evaluator, store = _make_evaluator()

        def handle_slow(params):
            return {"done": True}

        poller = _make_poller(store, evaluator, {"ns.SlowOp": handle_slow})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "result", "type": "Boolean"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-slow",
                        "name": "s",
                        "call": {
                            "type": "CallExpr",
                            "target": "SlowOp",
                            "args": [],
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
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["s", "done"]},
                            },
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
                            "name": "SlowOp",
                            "params": [],
                            "returns": [{"name": "done", "type": "Boolean"}],
                            "mixins": [
                                {
                                    "type": "MixinSig",
                                    "target": "Timeout",
                                    "args": [
                                        {
                                            "name": "minutes",
                                            "value": {"type": "Int", "value": 2},
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }

        result = evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Check the created task has timeout_ms set (2 minutes = 120000ms)
        tasks = store.get_pending_tasks("default")
        slow_tasks = [t for t in tasks if t.name == "ns.SlowOp"]
        assert len(slow_tasks) == 1
        assert slow_tasks[0].timeout_ms == 120000

        # Complete the workflow
        poller.poll_once()
        final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
        assert final.success

    def test_no_timeout_mixin_defaults_to_zero(self):
        """Event facet without Timeout mixin gets task.timeout_ms=0."""
        evaluator, store = _make_evaluator()

        def handle_fast(params):
            return {"done": True}

        _make_poller(store, evaluator, {"ns.FastOp": handle_fast})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "result", "type": "Boolean"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-fast",
                        "name": "f",
                        "call": {
                            "type": "CallExpr",
                            "target": "FastOp",
                            "args": [],
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
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["f", "done"]},
                            },
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
                            "name": "FastOp",
                            "params": [],
                            "returns": [{"name": "done", "type": "Boolean"}],
                        },
                    ],
                },
            ],
        }

        evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        tasks = store.get_pending_tasks("default")
        fast_tasks = [t for t in tasks if t.name == "ns.FastOp"]
        assert len(fast_tasks) == 1
        assert fast_tasks[0].timeout_ms == 0

    def test_callsite_timeout_overrides_facet(self):
        """Call-site Timeout mixin overrides facet-level Timeout."""
        evaluator, store = _make_evaluator()

        def handle_op(params):
            return {"done": True}

        _make_poller(store, evaluator, {"ns.Op": handle_op})

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "result", "type": "Boolean"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-op",
                        "name": "o",
                        "call": {
                            "type": "CallExpr",
                            "target": "Op",
                            "args": [],
                            "mixins": [
                                {
                                    "type": "MixinCall",
                                    "target": "Timeout",
                                    "args": [
                                        {
                                            "name": "minutes",
                                            "value": {"type": "Int", "value": 5},
                                        },
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
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["o", "done"]},
                            },
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
                            "name": "Op",
                            "params": [],
                            "returns": [{"name": "done", "type": "Boolean"}],
                            "mixins": [
                                {
                                    "type": "MixinSig",
                                    "target": "Timeout",
                                    "args": [
                                        {
                                            "name": "minutes",
                                            "value": {"type": "Int", "value": 1},
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }

        evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        tasks = store.get_pending_tasks("default")
        op_tasks = [t for t in tasks if t.name == "ns.Op"]
        assert len(op_tasks) == 1
        # Call-site (300000) overrides facet-level (60000)
        assert op_tasks[0].timeout_ms == 300000
