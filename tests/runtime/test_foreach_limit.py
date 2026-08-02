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

"""`foreach v in xs limit N` — a bulkhead on fan-out width.

Motivation: concurrent 51-state fan-outs once filled the Docker VM disk and put
MongoDB into a crash-loop, and the county atlas fans out 3,167 wide. Today the
only defence is operator discipline (serialise the runs, watch `fw maint
disk-guard`). The language could express unbounded fan-out and nothing else.

The contract this pins down:

* the SAME elements run, in the same order, with the same aggregate result —
  only the number in flight at once changes;
* an errored iteration still frees its slot, so one failure cannot wedge the
  window and strand the remainder;
* a limit >= the collection size, and no limit at all, behave identically to
  the historical unbounded path.

The width assertion is the point of the whole feature, so it is measured
directly rather than inferred from the final step count.
"""

from __future__ import annotations

import json
import time

import pytest

from facetwork.runtime.evaluator import Evaluator
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.telemetry import Telemetry
from facetwork.runtime.types import ObjectType


def _workflow_ast(limit=None):
    """ProcessAll: fan out `Value` over `items`, optionally capped."""
    foreach = {
        "variable": "r",
        "iterable": {"type": "InputRef", "path": ["items"]},
    }
    if limit is not None:
        foreach["limit"] = limit
    return {
        "type": "WorkflowDecl",
        "name": "ProcessAll",
        "params": [{"name": "items", "type": "Json"}, {"name": "cap", "type": "Int"}],
        "returns": [{"name": "count", "type": "Long"}],
        "body": {
            "type": "AndThenBlock",
            "foreach": foreach,
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-v",
                    "name": "v",
                    "call": {
                        "type": "CallExpr",
                        "target": "Value",
                        "args": [{"name": "input", "value": {"type": "InputRef", "path": ["r"]}}],
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


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def evaluator(store):
    return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))


def _value_inputs(store):
    return sorted(
        s.get_attribute("input")
        for s in store.get_all_steps()
        if s.object_type == ObjectType.VARIABLE_ASSIGNMENT
        and s.facet_name == "Value"
        and s.is_complete
    )


def _sub_blocks(store):
    return [
        s
        for s in store.get_all_steps()
        if s.object_type == ObjectType.AND_THEN and s.foreach_var == "r"
    ]


class TestForeachLimitRunsEverything:
    """A cap changes width, never the set of work performed."""

    def test_capped_runs_every_element(self, store, evaluator):
        items = list(range(20))
        result = evaluator.execute(
            _workflow_ast(limit={"type": "Int", "value": 4}), inputs={"items": items}
        )

        assert result.success is True
        assert _value_inputs(store) == items, "a cap must not drop iterations"
        assert len(_sub_blocks(store)) == 20

    def test_matches_the_uncapped_result_exactly(self, store, evaluator):
        items = list(range(12))
        capped = MemoryStore()
        Evaluator(persistence=capped, telemetry=Telemetry(enabled=False)).execute(
            _workflow_ast(limit={"type": "Int", "value": 3}), inputs={"items": items}
        )
        uncapped = MemoryStore()
        Evaluator(persistence=uncapped, telemetry=Telemetry(enabled=False)).execute(
            _workflow_ast(), inputs={"items": items}
        )

        assert _value_inputs(capped) == _value_inputs(uncapped)
        assert len(_sub_blocks(capped)) == len(_sub_blocks(uncapped))

    def test_limit_larger_than_collection_is_a_no_op(self, store, evaluator):
        result = evaluator.execute(
            _workflow_ast(limit={"type": "Int", "value": 500}), inputs={"items": [1, 2, 3]}
        )
        assert result.success is True
        assert _value_inputs(store) == [1, 2, 3]

    def test_limit_of_one_serialises(self, store, evaluator):
        """limit 1 is the explicit 'serialise this fan-out' knob."""
        result = evaluator.execute(
            _workflow_ast(limit={"type": "Int", "value": 1}), inputs={"items": [1, 2, 3, 4]}
        )
        assert result.success is True
        assert _value_inputs(store) == [1, 2, 3, 4]

    def test_empty_collection_still_completes(self, store, evaluator):
        result = evaluator.execute(
            _workflow_ast(limit={"type": "Int", "value": 8}), inputs={"items": []}
        )
        assert result.success is True
        assert _sub_blocks(store) == []


class TestForeachLimitBoundsWidth:
    """The cap must actually bound concurrency — the reason the clause exists."""

    def test_never_exceeds_the_cap_in_flight(self, store, evaluator):
        """Observe the real high-water mark of non-terminal sub-blocks.

        Asserting only on the final count would pass even if the window were
        ignored entirely, so sample width while the fan-out is running.
        """
        limit = 4
        items = list(range(25))
        high_water = []

        # Sample after every persisted step write.
        real_save = store.save_step

        def sampling_save(step, *a, **kw):
            out = real_save(step, *a, **kw)
            live = [
                s
                for s in store.get_all_steps()
                if s.object_type == ObjectType.AND_THEN
                and s.foreach_var == "r"
                and not s.is_terminal
            ]
            high_water.append(len(live))
            return out

        store.save_step = sampling_save
        try:
            result = evaluator.execute(
                _workflow_ast(limit={"type": "Int", "value": limit}), inputs={"items": items}
            )
        finally:
            store.save_step = real_save

        assert result.success is True
        assert _value_inputs(store) == items
        assert high_water, "sampler never ran — the assertion below would be vacuous"
        assert max(high_water) <= limit, (
            f"fan-out width reached {max(high_water)}, exceeding limit {limit}"
        )

    def test_uncapped_fans_out_wide(self, store, evaluator):
        """Guards the sampler above: without a limit the width does exceed 4."""
        high_water = []
        real_save = store.save_step

        def sampling_save(step, *a, **kw):
            out = real_save(step, *a, **kw)
            live = [
                s
                for s in store.get_all_steps()
                if s.object_type == ObjectType.AND_THEN
                and s.foreach_var == "r"
                and not s.is_terminal
            ]
            high_water.append(len(live))
            return out

        store.save_step = sampling_save
        try:
            evaluator.execute(_workflow_ast(), inputs={"items": list(range(25))})
        finally:
            store.save_step = real_save

        assert max(high_water) > 4, (
            "uncapped foreach did not fan out past 4 — the capped test proves nothing"
        )


class TestForeachLimitFromAReference:
    """The cap may come from a workflow parameter, not just a constant."""

    def test_reference_limit(self, store, evaluator):
        result = evaluator.execute(
            _workflow_ast(limit={"type": "InputRef", "path": ["cap"]}),
            inputs={"items": list(range(10)), "cap": 3},
        )
        assert result.success is True
        assert _value_inputs(store) == list(range(10))


class TestForeachWindowCannotWedge:
    """A stalled sub-block must not hold a window slot forever.

    The failure this pins down was observed live, not imagined. The
    3,167-county atlas fan-out stopped dead at 628: 31 of its 32 slots were
    held by sub-blocks that had finished their work but never cascaded to
    Complete, so no further county was ever admitted.

    Uncapped, that stranding is a slow tail — the other iterations still run.
    Capped, it is fatal, because N stalled sub-blocks ARE the whole window.
    The cap turned a latency bug into a liveness bug.

    Nothing else rescues it in time: repair check 7 requires every task
    terminal (one was still running), and the stuck-step sweep runs every
    5 min, capped at 25 steps, on a workflow set that deliberately excludes
    block-Continue states.
    """

    def _block_handler(self, sub_states, ages_ms, limit=4, values=None):
        """Build a Continue handler over synthetic sub-blocks."""
        from unittest.mock import MagicMock

        from facetwork.runtime.handlers.block_execution import (
            BlockExecutionContinueHandler,
        )
        from facetwork.runtime.persistence import IterationChanges
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        now = int(time.time() * 1000)
        values = values if values is not None else list(range(20))

        block = StepDefinition.create(
            workflow_id="wf",
            object_type=ObjectType.AND_THEN,
            facet_name="",
            statement_id="fe",
            container_id="c",
            block_id="",
            root_id="c",
        )
        block.set_attribute("_foreach_values", values)
        block.set_attribute("_foreach_limit", limit)

        subs = []
        for i, (st, age) in enumerate(zip(sub_states, ages_ms)):
            s = StepDefinition.create(
                workflow_id="wf",
                object_type=ObjectType.AND_THEN,
                facet_name="",
                statement_id=f"foreach-{i}",
                container_id="c",
                block_id=block.id,
                root_id="c",
            )
            s.state = st
            s.last_modified = now - age
            subs.append(s)

        ctx = MagicMock()
        ctx.changes = IterationChanges()
        ctx.persistence.step_exists.return_value = False
        ctx.get_block_ast.return_value = {"foreach": {"variable": "v"}, "steps": []}

        h = BlockExecutionContinueHandler.__new__(BlockExecutionContinueHandler)
        h.step = block
        h.context = ctx
        return h, subs, ctx

    def test_stalled_slots_are_nudged_so_the_window_can_recover(self):
        """The production shape: every slot held, nothing progressing."""
        stalled = "state.block.execution.Continue"
        h, subs, ctx = self._block_handler([stalled] * 4, [5 * 60_000] * 4, limit=4)
        remaining = h._refill_foreach_window(subs)

        assert remaining > 0, "elements remain unadmitted — the window IS blocked"
        nudged = ctx.changes.continuation_tasks
        assert len(nudged) == 4, f"all 4 stalled slots must be nudged, got {len(nudged)}"
        assert all(t.step_id in {s.id for s in subs} for t in nudged)
        assert all(t.data.get("reason") == "foreach_window_stalled" for t in nudged), (
            "nudges must be identifiable in the task stream"
        )

    def test_a_live_cascade_is_never_nudged(self):
        """Sub-blocks that just changed are working, not stalled.

        Nudging those would generate churn proportional to fan-out width on
        every poll of a perfectly healthy run.
        """
        h, subs, ctx = self._block_handler(
            ["state.block.execution.Continue"] * 4, [500] * 4, limit=4
        )
        h._refill_foreach_window(subs)
        assert ctx.changes.continuation_tasks == []

    def test_healthy_window_with_free_slots_does_not_nudge(self):
        """With room to admit, the refill takes the normal path and pays nothing."""
        h, subs, ctx = self._block_handler(
            ["state.statement.Complete"] * 2, [5 * 60_000] * 2, limit=4
        )
        h._refill_foreach_window(subs)
        assert ctx.changes.continuation_tasks == []

    def test_stalled_straggler_after_full_admission_is_nudged(self):
        """The other wedge: all elements admitted, last one stalls.

        Blocks COMPLETION rather than admission — the refill returns early on
        `created >= total`, so this needs its own path or the fan-out sits at
        N-1/N forever.
        """
        vals = list(range(4))
        h, subs, ctx = self._block_handler(
            ["state.statement.Complete"] * 3 + ["state.block.execution.Continue"],
            [5 * 60_000] * 4,
            limit=4,
            values=vals,
        )
        remaining = h._refill_foreach_window(subs)
        assert remaining == 0, "everything is admitted"
        assert len(ctx.changes.continuation_tasks) == 1, "the straggler must be nudged"

    def test_nudges_dedupe_by_step(self):
        """Re-nudging across polls must not pile up duplicate continuations."""
        h, subs, ctx = self._block_handler(
            ["state.block.execution.Continue"] * 4, [5 * 60_000] * 4, limit=4
        )
        h._refill_foreach_window(subs)
        h._refill_foreach_window(subs)
        assert len(ctx.changes.continuation_tasks) == 4, "deduped by target step"

    def test_uncapped_foreach_is_untouched(self):
        """No limit → no window, no nudging, original behaviour."""
        from unittest.mock import MagicMock

        from facetwork.runtime.handlers.block_execution import (
            BlockExecutionContinueHandler,
        )
        from facetwork.runtime.persistence import IterationChanges
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        block = StepDefinition.create(
            workflow_id="wf",
            object_type=ObjectType.AND_THEN,
            facet_name="",
            statement_id="fe",
            container_id="c",
            block_id="",
            root_id="c",
        )
        ctx = MagicMock()
        ctx.changes = IterationChanges()
        h = BlockExecutionContinueHandler.__new__(BlockExecutionContinueHandler)
        h.step = block
        h.context = ctx
        assert h._refill_foreach_window([]) == 0
        assert ctx.changes.continuation_tasks == []


class TestForeachLimitUpLevelScope:
    """A `$$`-scoped cap must resolve on the CLAUSE's context.

    `fwh_county_atlas.BuildAtlasFanout` writes `limit $$.concurrency` — the cap
    walks up to the workflow params while the iterable resolves one level down.
    The limit is evaluated on the foreach clause's context, not the body's, so
    up-level resolution there does not follow from the literal-cap tests above
    and is proved here directly.

    (The atlas additionally sources its iterable from the containing step's
    return. That exact shape is not executable in this in-process harness — a
    plain facet's array return never becomes ready, with or without a limit, and
    no runtime test in the suite covers it — so the iterable here comes from a
    workflow input while the cap keeps the `$$` scope under test.)
    """

    SRC = """
    namespace demo {
        facet Echo(v: String) => (out: String) andThen { yield Echo(out = $.v) }
        workflow Demo(items: Json, cap: Long) => (out: String) andThen {
            s = Echo(v = "seed") andThen foreach i in $$.items limit $$.cap {
                leaf = Echo(v = $.i)
                yield Demo(out = leaf.out)
            }
        }
    }
    """

    def _run(self, inputs):
        from facetwork.emitter import emit_dict
        from facetwork.parser import parse

        program = emit_dict(parse(self.SRC))
        workflow = next(
            w
            for ns in program["declarations"]
            for w in ns.get("declarations", ns.get("workflows", []))
            if w.get("name") == "Demo"
        )
        store = MemoryStore()
        ev = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        return ev.execute(workflow, inputs=inputs, program_ast=program), store

    def test_up_level_cap_resolves_and_runs_every_element(self, monkeypatch):
        monkeypatch.setenv("FW_FFL_RELATIVE_SCOPING", "1")
        items = ["a", "b", "c", "d", "e", "f"]
        result, store = self._run({"items": items, "cap": 2})
        assert result.success, f"failed: {getattr(result, 'error', None)}"

        echoed = sorted(
            s.attributes.returns["out"].value
            for s in store.get_all_steps()
            if s.statement_name == "leaf" and "out" in s.attributes.returns
        )
        assert echoed == items, "a $$-scoped cap must still run every element"

    def test_compiles_in_the_atlas_shape(self):
        """The literal county-atlas clause must compile and emit an up-level cap.

        Execution of that shape is not reachable here, so pin the compile-side
        contract at least: `$$` on the cap emits `up_levels: 1`.
        """
        from facetwork.emitter import emit_dict
        from facetwork.parser import parse
        from facetwork.validator import validate

        src = """
        namespace demo {
            facet ListIt(prefix: String) => (items: Json, count: Long)
            facet Work(key: String, tier: Long) => (out: String)
            workflow Fan(prefix: String = "p", tier: Long = 1, concurrency: Long = 32) => (built: [String]) andThen {
                children = ListIt(prefix = $.prefix) andThen foreach it in $.items limit $$.concurrency {
                    leaf = Work(key = $.it, tier = $$.tier)
                    yield Fan(built = [leaf.out])
                }
            }
        }
        """
        program = parse(src)
        assert validate(program).is_valid
        blob = json.dumps(emit_dict(program))
        i = blob.find('"ForeachClause"')
        clause = blob[i : i + 220]
        assert '"limit"' in clause, clause
        assert '"up_levels": 1' in clause, clause


class TestForeachLimitRejectsNonsense:
    """A cap that cannot be honoured must fail loudly, never silently uncap.

    Falling back to unlimited would reintroduce the exact stampede the clause
    exists to prevent, at the moment the author asked for protection.
    """

    @pytest.mark.parametrize("bad", [0, -1])
    def test_non_positive_limit_is_an_error(self, store, evaluator, bad):
        result = evaluator.execute(
            _workflow_ast(limit={"type": "Int", "value": bad}), inputs={"items": [1, 2, 3]}
        )
        assert result.success is False

    def test_non_integer_limit_is_an_error(self, store, evaluator):
        result = evaluator.execute(
            _workflow_ast(limit={"type": "String", "value": "eight"}),
            inputs={"items": [1, 2, 3]},
        )
        assert result.success is False
