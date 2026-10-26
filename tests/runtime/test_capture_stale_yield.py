"""Capture must never complete a step with empty returns because one read
missed its yield.

Observed 2026-10-25 on the fleet: in a ``foreach`` fan-out, one iteration's
inner facet (``FetchOne``) reached ``Complete`` with NO returns although its
yield step was stored complete with the value. The next step's
``one.path`` then failed the whole workflow. A complete plain ``andThen``
block has a complete step for every yield it declares, so a capture read that
cannot see one is stale: the step must wait and retry, not merge nothing.
"""

from __future__ import annotations

from facetwork.emitter import emit_dict
from facetwork.parser import parse
from facetwork.runtime import ExecutionStatus
from facetwork.runtime.evaluator import Evaluator
from facetwork.runtime.handlers import capture
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.states import STUCK_STEP_STATES, StepState
from facetwork.runtime.telemetry import Telemetry
from facetwork.runtime.types import ObjectType

SRC = """
namespace t {
    facet Value(input: Long)
    facet Inner(a: Long) => (out: Long) andThen {
        v = Value(input = $.a)
        yield Inner(out = v.input + 1)
    }
    workflow W(x: Long = 1) => (result: Long) andThen {
        one = Inner(a = $.x)
        yield W(result = one.out)
    }
}
"""


def _asts():
    program = emit_dict(parse(SRC))
    ns = program["declarations"][0]
    wf = next(d for d in ns["declarations"] if d["name"] == "W")
    return program, wf


def _hide_yields_from_capture_once(store, monkeypatch):
    """While capture runs, the first read of a block returns it WITHOUT its
    complete yield steps — the stale view the fleet produced."""
    hidden = {"left": 1}
    in_capture = {"on": False}
    real_read = store.get_steps_by_block

    def read(block_id):
        steps = list(real_read(block_id))
        if in_capture["on"] and hidden["left"] > 0:
            kept = [s for s in steps if s.object_type != ObjectType.YIELD_ASSIGNMENT]
            if len(kept) != len(steps):
                hidden["left"] -= 1
                return kept
        return steps

    monkeypatch.setattr(store, "get_steps_by_block", read)
    real_process = capture.StatementCaptureBeginHandler.process_state

    def process(self):
        in_capture["on"] = True
        try:
            return real_process(self)
        finally:
            in_capture["on"] = False

    monkeypatch.setattr(capture.StatementCaptureBeginHandler, "process_state", process)
    return hidden


def test_stale_capture_read_waits_instead_of_completing_empty(monkeypatch):
    store = MemoryStore()
    hidden = _hide_yields_from_capture_once(store, monkeypatch)
    program, wf = _asts()

    result = Evaluator(persistence=store, telemetry=Telemetry(enabled=False)).execute(
        wf, inputs={"x": 1}, program_ast=program
    )

    assert hidden["left"] == 0, "the stale read was never exercised"
    assert result.success is True and result.status == ExecutionStatus.COMPLETED
    assert result.outputs.get("result") == 2
    inner = [
        s
        for s in store.get_all_steps()
        if s.object_type == ObjectType.VARIABLE_ASSIGNMENT and s.facet_name.endswith("Inner")
    ]
    assert inner and all("out" in s.attributes.returns for s in inner)


def test_capture_state_is_swept():
    """A deferred capture relies on the sweep if its push does not survive
    the commit, so the state must be one the sweep looks for."""
    assert StepState.STATEMENT_CAPTURE_BEGIN in STUCK_STEP_STATES
