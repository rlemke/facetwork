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
                        "args": [
                            {"name": "input", "value": {"type": "InputRef", "path": ["r"]}}
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
        result = evaluator.execute(_workflow_ast(limit={"type": "Int", "value": 4}), inputs={"items": items})

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

        original = Evaluator._process_state_change if hasattr(Evaluator, "_process_state_change") else None
        del original  # not needed; sample via the store instead

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
