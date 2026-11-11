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

"""Resetting errored ancestors must never put a block in a statement state.

Measured 2026-11-11: two failed runs had their dead-lettered tasks retried to
completion, and their errored ancestors were then reset with the dashboard's
``_reset_errored_ancestors``. That copy walked up through andThen BLOCK steps
and reset them to ``STATEMENT_BLOCKS_CONTINUE``, a state the block state
machine has no exit from. The block changer then looped forever in memory,
re-querying the block's sub-blocks on every pass: every runner in the fleet
spun at once (~30,000 Mongo queries/s, nothing logged) until restarted.

Pinned here:

* the dashboard walk resumes blocks at ``BLOCK_EXECUTION_CONTINUE`` (it now
  delegates to the evaluator's walk, which was already right);
* a step left in a state its machine cannot leave FAILS, loudly, instead of
  spinning;
* a resume that leaves the root unfinished is not reported as COMPLETED.
"""

from __future__ import annotations

import pytest

from facetwork import FFLParser, emit_dict
from facetwork.ast_utils import find_workflow
from facetwork.runtime.evaluator import Evaluator, ExecutionStatus
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.states import StepState

SRC = """
namespace t {
  event facet Fetch(u: String) => (out: String)
  event facet Pub(v: String) => (ok: String)
  facet Inner(u: String) => (r: String) andThen {
    f = Fetch(u = $.u)
    p = Pub(v = f.out)
    yield Inner(r = p.ok)
  }
  workflow W(xs: [String]) => (n: String) andThen foreach x in $.xs {
    i = Inner(u = $.x)
    yield W(n += i.r)
  }
}
"""


def _failed_then_completed_leaf():
    """A run whose second Pub failed (erroring every ancestor), then completed.

    Mirrors a dead-letter retry of an already-failed run: the task finishes,
    the ancestors are still ``statement.Error``.
    """
    prog = emit_dict(FFLParser().parse(SRC))
    wf = find_workflow(prog, "t.W")
    store = MemoryStore()
    ev = Evaluator(persistence=store)
    wid = ev.execute(wf, inputs={"xs": ["a", "b"]}, program_ast=prog).workflow_id

    def parked(name):
        return [
            s
            for s in store.get_steps_by_workflow(wid)
            if s.facet_name.endswith(name) and s.state == StepState.EVENT_TRANSMIT
        ]

    for s in parked("Fetch"):
        ev.continue_step(s.id, {"out": "o"})
    ev.resume(wid, wf, program_ast=prog)
    first, second = parked("Pub")
    ev.continue_step(first.id, {"ok": "1"})
    ev.fail_step(second.id, "504 from the remote")
    assert ev.resume(wid, wf, program_ast=prog).status == ExecutionStatus.ERROR

    leaf = store.get_step(second.id)
    leaf.state = StepState.EVENT_TRANSMIT
    leaf.transition.current_state = StepState.EVENT_TRANSMIT
    leaf.transition.clear_error()
    store.save_step(leaf)
    ev.continue_step(leaf.id, {"ok": "2"})
    return store, ev, wf, prog, wid, store.get_step(leaf.id)


def test_dashboard_reset_resumes_blocks_at_their_own_state():
    pytest.importorskip("fastapi")
    from facetwork.dashboard.routes.execution.steps import _reset_errored_ancestors

    store, ev, wf, prog, wid, leaf = _failed_then_completed_leaf()
    _reset_errored_ancestors(leaf, store)

    for s in store.get_steps_by_workflow(wid):
        if s.is_block and not s.is_terminal:
            assert s.state == StepState.BLOCK_EXECUTION_CONTINUE, (s.object_type, s.state)

    result = ev.resume_step(wid, leaf.id, wf, program_ast=prog)
    assert result.status == ExecutionStatus.COMPLETED
    root = store.get_workflow_root(wid)
    assert root.attributes.returns["n"].value == ["1", "2"]


def test_block_in_a_statement_state_fails_instead_of_spinning():
    """The old reset's output: the changer must give up, not loop forever."""
    store, ev, wf, prog, wid, leaf = _failed_then_completed_leaf()
    for s in store.get_steps_by_workflow(wid):
        if s.state == StepState.STATEMENT_ERROR:
            s.state = StepState.STATEMENT_BLOCKS_CONTINUE  # blocks too: the bug
            s.transition.current_state = StepState.STATEMENT_BLOCKS_CONTINUE
            s.transition.clear_error()
            s.transition.request_transition = False
            store.save_step(s)

    calls = {"n": 0}
    orig = store.get_blocks_by_step

    def counting(step_id):
        calls["n"] += 1
        assert calls["n"] < 10_000, "the block changer is spinning"
        return orig(step_id)

    store.get_blocks_by_step = counting
    result = ev.resume(wid, wf, program_ast=prog)

    assert result.status == ExecutionStatus.ERROR
    errored_blocks = [s for s in store.get_steps_by_workflow(wid) if s.is_block and s.is_error]
    assert errored_blocks
    assert any("no transition out of state" in str(s.transition.error) for s in errored_blocks)


def test_unfinished_root_is_not_reported_completed():
    from facetwork.runtime.step import StepDefinition
    from facetwork.runtime.types import ObjectType

    store = MemoryStore()
    ev = Evaluator(persistence=store, max_iterations=5)
    root = StepDefinition.create(
        workflow_id="wf-x", object_type=ObjectType.WORKFLOW, facet_name="T"
    )
    store.save_step(root)

    assert ev._build_result("wf-x", 1).status == ExecutionStatus.PAUSED
    assert ev._build_result("wf-x", 5).status == ExecutionStatus.TIMEOUT
