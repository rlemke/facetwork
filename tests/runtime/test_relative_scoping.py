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

"""Runtime resolution under relative $-scoping (FW_FFL_RELATIVE_SCOPING).

See docs/architecture/ffl-relative-scoping.md. Covers the expression-level
resolver (unit) and end-to-end execution of $$ (up-level) + immediate-container
param resolution (integration).
"""

import pytest

from facetwork.ast_utils import find_workflow
from facetwork.emitter import emit_dict
from facetwork.parser import parse
from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
from facetwork.runtime.expression import EvaluationContext, ExpressionEvaluator
from facetwork.runtime.errors import ReferenceError as FwReferenceError


def _ref(field, up=0):
    d = {"type": "InputRef", "path": [field]}
    if up:
        d["up_levels"] = up
    return d


class TestRelativeResolver:
    """Unit tests for the container-stack $/$$ resolver."""

    def _ctx(self):
        return EvaluationContext(
            inputs={},
            get_step_output=lambda n, a: f"RET:{n}.{a}",
            scope_stack=[
                {"params": {"input": "step-in"}, "returns": {"output"}, "name": "s3"},
                {"params": {"input": "wf-in"}, "returns": set(), "name": None},
            ],
            foreach_var="r",
            foreach_value="loopval",
        )

    def test_immediate_param(self):
        assert ExpressionEvaluator().evaluate(_ref("input"), self._ctx()) == "step-in"

    def test_immediate_return_defers_via_getter(self):
        # A container return resolves through get_step_output (which defers).
        assert ExpressionEvaluator().evaluate(_ref("output"), self._ctx()) == "RET:s3.output"

    def test_up_level_param(self):
        assert ExpressionEvaluator().evaluate(_ref("input", up=1), self._ctx()) == "wf-in"

    def test_loop_var_on_immediate_frame(self):
        assert ExpressionEvaluator().evaluate(_ref("r"), self._ctx()) == "loopval"

    def test_unknown_attr_is_none(self):
        assert ExpressionEvaluator().evaluate(_ref("nope"), self._ctx()) is None

    def test_overflow_raises(self):
        with pytest.raises(FwReferenceError):
            ExpressionEvaluator().evaluate(_ref("input", up=2), self._ctx())

    def test_flag_off_stack_none_uses_inputs(self):
        ctx = EvaluationContext(inputs={"a": 1}, get_step_output=lambda n, a: None)
        assert ExpressionEvaluator().evaluate(_ref("a"), ctx) == 1


def _run(src, inputs, workflow="Demo"):
    program = emit_dict(parse(src))
    store = MemoryStore()
    ev = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
    result = ev.execute(find_workflow(program, workflow), inputs=inputs, program_ast=program)
    return result, store


_UP_LEVEL_SRC = """
namespace demo {
    facet Echo(v: String) => (out: String) andThen { yield Echo(out = $.v) }
    workflow Demo(input: String) => (out: String) andThen {
        outer = Echo(v = $.input ++ "-A") andThen {
            inner = Echo(v = $$.input ++ "-B-" ++ $.v)
            yield Demo(out = inner.out)
        }
    }
}
"""


class TestRelativeExecution:
    """End-to-end execution with the flag on."""

    def test_up_level_and_immediate_param(self, monkeypatch):
        # $$.input = workflow input; $.v = the containing step's own param
        # (static, ready at init) — no deferral, no deadlock.
        monkeypatch.setenv("FW_FFL_RELATIVE_SCOPING", "1")
        result, _ = _run(_UP_LEVEL_SRC, inputs={"input": "hi"})
        assert result.success, f"failed: {getattr(result, 'error', None)}"
        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs == {"out": "hi-B-hi-A"}

    def test_chained_extra_bodies_both_execute(self, monkeypatch):
        # A step with two chained co-clauses: both must expand into their own
        # sibling blocks and run, each resolving $$ against the workflow.
        monkeypatch.setenv("FW_FFL_RELATIVE_SCOPING", "1")
        src = """
        namespace demo {
            facet Echo(v: String) => (out: String) andThen { yield Echo(out = $.v) }
            workflow Demo(input: String) => (out: String) andThen {
                s = Echo(v = $.input) andThen {
                    x = Echo(v = $$.input ++ "-x")
                } andThen {
                    y = Echo(v = $$.input ++ "-y")
                }
                yield Demo(out = $.input)
            }
        }
        """
        result, store = _run(src, inputs={"input": "hi"})
        assert result.success, f"failed: {getattr(result, 'error', None)}"
        steps = {s.statement_name: s for s in store.get_all_steps() if s.statement_name}
        assert "x" in steps and "y" in steps, "both co-clauses must expand"
        assert steps["x"].attributes.returns["out"].value == "hi-x"
        assert steps["y"].attributes.returns["out"].value == "hi-y"

    def test_flag_off_does_not_build_stack(self, monkeypatch):
        # With the flag off the same nested body resolves $. via the legacy flat
        # scope; $$ has no meaning there. Assert the flag gates the behavior:
        # a plain nested $. workflow still runs unchanged.
        monkeypatch.delenv("FW_FFL_RELATIVE_SCOPING", raising=False)
        src = """
        namespace demo {
            facet Echo(v: String) => (out: String) andThen { yield Echo(out = $.v) }
            workflow Demo(input: String) => (out: String) andThen {
                outer = Echo(v = $.input) andThen {
                    inner = Echo(v = $.input)
                    yield Demo(out = inner.out)
                }
            }
        }
        """
        result, _ = _run(src, inputs={"input": "hi"})
        assert result.success
        assert result.outputs == {"out": "hi"}
