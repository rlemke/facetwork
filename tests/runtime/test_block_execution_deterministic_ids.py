"""Regression tests for deterministic, block-scoped step ids.

Background: under a multi-runner fleet, two runners could concurrently
process the *same* block (foreach/when/ready fan-out). Each call to
``StepDefinition.create`` minted a fresh random uuid, so the losing
runner's sub-blocks — and the grandchild steps created beneath them —
survived with ids that escaped the ``(statement_id, block_id,
container_id)`` dedup index (their ``block_id`` differed). The result was
N× duplicate child steps for a single logical iteration.

The fix derives block-scoped step ids deterministically from
``(workflow_id, block_id, statement_id, container_id)`` so concurrent
creators converge on one id and the unique index collapses the
duplicates. These tests pin that convergence.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from facetwork.runtime.handlers.block_execution import BlockExecutionBeginHandler
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import ObjectType, deterministic_step_id


def _foreach_block_step(workflow_id: str = "wf-1") -> StepDefinition:
    """A foreach AndThen block step (the parent that fans out)."""
    return StepDefinition.create(
        workflow_id=workflow_id,
        object_type=ObjectType.AND_THEN,
        facet_name="",
        statement_id="foreach-block",
    )


def _foreach_block_ast(values: list[str]) -> dict:
    return {
        "type": "AndThenBlock",
        "foreach": {
            "variable": "r",
            "iterable": {
                "type": "ArrayLiteral",
                "elements": [{"type": "String", "value": v} for v in values],
            },
        },
        "steps": [],
    }


def _fresh_context() -> MagicMock:
    """A context as a freshly-started runner sees it: empty DB."""
    context = MagicMock()
    context.persistence.step_exists.return_value = False
    context.persistence.get_steps_by_workflow.return_value = []
    context.get_workflow_root.return_value = None
    context._find_step.return_value = None
    context.changes.created_steps = []
    context.changes.add_created_step.side_effect = (
        lambda s: context.changes.created_steps.append(s)
    )
    return context


class TestDeterministicStepId:
    def test_same_inputs_yield_same_id(self):
        a = deterministic_step_id("wf", "blk", "foreach-1", "cont")
        b = deterministic_step_id("wf", "blk", "foreach-1", "cont")
        assert a == b

    def test_distinct_statements_yield_distinct_ids(self):
        a = deterministic_step_id("wf", "blk", "foreach-1", "cont")
        b = deterministic_step_id("wf", "blk", "foreach-2", "cont")
        assert a != b

    def test_distinct_runs_yield_distinct_ids(self):
        """A fresh execution workflow id (re-run) must not collide with a
        prior run's steps."""
        a = deterministic_step_id("run-A", "blk", "foreach-1", "cont")
        b = deterministic_step_id("run-B", "blk", "foreach-1", "cont")
        assert a != b


class TestForeachFanOutConvergence:
    def test_two_runners_produce_identical_subblock_ids(self):
        """Two independent runners, each seeing an empty DB, must mint the
        SAME sub-block ids — so the unique index dedups them instead of
        letting the loser's iterations survive as orphans."""
        values = ["africa", "europe", "asia"]

        step_a = _foreach_block_step()
        ctx_a = _fresh_context()
        BlockExecutionBeginHandler(step_a, ctx_a)._process_foreach(
            _foreach_block_ast(values)
        )

        # A second runner working the SAME persisted foreach block step.
        step_b = step_a.clone()
        ctx_b = _fresh_context()
        BlockExecutionBeginHandler(step_b, ctx_b)._process_foreach(
            _foreach_block_ast(values)
        )

        ids_a = [s.id for s in ctx_a.changes.created_steps]
        ids_b = [s.id for s in ctx_b.changes.created_steps]

        assert len(ids_a) == len(values)
        assert ids_a == ids_b  # convergence: duplicates collapse on commit
        assert len(set(ids_a)) == len(values)  # distinct per iteration

    def test_subblock_ids_match_dedup_index_key(self):
        step = _foreach_block_step()
        ctx = _fresh_context()
        BlockExecutionBeginHandler(step, ctx)._process_foreach(
            _foreach_block_ast(["x", "y"])
        )

        created = ctx.changes.created_steps
        for i, sub in enumerate(created):
            expected = deterministic_step_id(
                step.workflow_id, step.id, f"foreach-{i}", step.container_id
            )
            assert sub.id == expected
            assert sub.statement_id == f"foreach-{i}"
            assert sub.block_id == step.id

    def test_foreach_value_still_bound_per_iteration(self):
        step = _foreach_block_step()
        ctx = _fresh_context()
        BlockExecutionBeginHandler(step, ctx)._process_foreach(
            _foreach_block_ast(["africa", "europe"])
        )
        created = ctx.changes.created_steps
        assert [s.foreach_value for s in created] == ["africa", "europe"]
        assert all(s.foreach_var == "r" for s in created)
