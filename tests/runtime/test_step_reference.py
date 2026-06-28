"""Step-reference feature: parse, validate, evaluate, serialize.

Covers the contract described in `examples/canonical/08-step-reference.ffl`:
- bare step names in argument position parse to StepRef(path=[name])
- the validator accepts them (existence is still checked)
- the evaluator returns a StepReference when path has length 1
- _resolve_path dereferences StepReference via the get_step_by_id callback
- StepReference round-trips through its tagged JSON form
- the canonical example parses and validates cleanly
"""

from __future__ import annotations

from pathlib import Path

import pytest

from facetwork.emitter import emit_dict
from facetwork.parser import parse
from facetwork.runtime.expression import EvaluationContext, ExpressionEvaluator
from facetwork.runtime.types import (
    AttributeValue,
    FacetAttributes,
    StepReference,
    deserialize_attribute_value,
    serialize_attribute_value,
)
from facetwork.validator import validate as validate_ast

CANONICAL = Path(__file__).resolve().parents[2] / "examples" / "canonical" / "08-step-reference.ffl"
CANONICAL_MIXIN_ALIAS = (
    Path(__file__).resolve().parents[2] / "examples" / "canonical" / "09-facetref-mixin-alias.ffl"
)


# =========================================================================
# Parser / emitter
# =========================================================================


def _find(node, predicate):
    """Walk a nested dict/list AST and return the first node where predicate is true."""
    if isinstance(node, dict):
        if predicate(node):
            return node
        for v in node.values():
            r = _find(v, predicate)
            if r is not None:
                return r
    elif isinstance(node, list):
        for x in node:
            r = _find(x, predicate)
            if r is not None:
                return r
    return None


def test_bare_step_name_emits_stepref_with_single_path():
    src = """
    namespace t {
      facet A(input: String) => (out: String) andThen {
        yield A(out = $.input)
      }
      facet B(ds: A) => (out: String) andThen {
        yield B(out = $.ds.out)
      }
      workflow W(x: String) => (out: String) andThen {
        s1 = A(input = $.x)
        s2 = B(ds = s1)
        yield W(out = s2.out)
      }
    }
    """
    ast = parse(src, "t.ffl")
    out = emit_dict(ast, include_locations=False)

    s2 = _find(out, lambda n: n.get("name") == "s2" and "call" in n)
    assert s2 is not None
    ds_arg = next(a for a in s2["call"]["args"] if a["name"] == "ds")
    assert ds_arg["value"]["type"] == "StepRef"
    assert ds_arg["value"]["path"] == ["s1"]


def test_canonical_example_parses_and_validates():
    src = CANONICAL.read_text()
    ast = parse(src, str(CANONICAL))
    result = validate_ast(ast)
    assert not result.errors, f"unexpected validator errors: {result.errors}"


def test_canonical_mixin_alias_example_parses_and_validates():
    """09-facetref-mixin-alias.ffl exercises the full
    pass-by-step + mixin-alias surface: with M() as m1 in a signature
    and $.fref.m1.field on the consumer side. Must validate cleanly."""
    src = CANONICAL_MIXIN_ALIAS.read_text()
    ast = parse(src, str(CANONICAL_MIXIN_ALIAS))
    result = validate_ast(ast)
    assert not result.errors, f"unexpected validator errors: {result.errors}"


# =========================================================================
# Validator — STEP_REF_FACET_MISMATCH
# =========================================================================


_MISMATCH_SRC = """
namespace refs {
    facet DoSomething(input: String) => (output: String) andThen {
        yield DoSomething(output = $.input ++ "!")
    }
    facet OtherFacet(value: String) => (output: String) andThen {
        yield OtherFacet(output = $.value)
    }
    facet AnotherThing(ds: DoSomething) => (output: String) andThen {
        yield AnotherThing(output = $.ds.output)
    }
    workflow Demo(input: String) => (output: String) andThen {
        s1  = DoSomething(input = $.input)
        bad = OtherFacet(value = $.input)
        s2  = AnotherThing(ds = bad)
        yield Demo(output = s2.output)
    }
}
"""


def test_step_ref_facet_mismatch_emits_rule_id():
    result = validate_ast(parse(_MISMATCH_SRC))
    mismatches = [e for e in result.errors if e.rule_id == "STEP_REF_FACET_MISMATCH"]
    assert len(mismatches) == 1, f"expected one mismatch, got {result.errors}"
    msg = str(mismatches[0])
    assert "bad" in msg
    assert "OtherFacet" in msg
    assert "DoSomething" in msg
    assert "ds" in msg


def test_step_ref_facet_match_passes():
    src = """
namespace refs {
    facet DoSomething(input: String) => (output: String) andThen {
        yield DoSomething(output = $.input)
    }
    facet AnotherThing(ds: DoSomething) => (output: String) andThen {
        yield AnotherThing(output = $.ds.output)
    }
    workflow Demo(input: String) => (output: String) andThen {
        s1 = DoSomething(input = $.input)
        s2 = AnotherThing(ds = s1)
        yield Demo(output = s2.output)
    }
}
"""
    result = validate_ast(parse(src))
    assert not [e for e in result.errors if e.rule_id == "STEP_REF_FACET_MISMATCH"], (
        f"unexpected mismatch: {result.errors}"
    )


def test_step_ref_field_access_not_flagged_as_mismatch():
    """`step.field` references are not step-refs; they should never trigger
    STEP_REF_FACET_MISMATCH even if the field name happens to be a facet
    name."""
    src = """
namespace refs {
    facet DoSomething(input: String) => (output: String) andThen {
        yield DoSomething(output = $.input)
    }
    facet Sink(value: String) => (out: String) andThen {
        yield Sink(out = $.value)
    }
    workflow Demo(input: String) => (out: String) andThen {
        s1 = DoSomething(input = $.input)
        s2 = Sink(value = s1.output)
        yield Demo(out = s2.out)
    }
}
"""
    result = validate_ast(parse(src))
    assert not [e for e in result.errors if e.rule_id == "STEP_REF_FACET_MISMATCH"]


def test_step_ref_mixin_compatible_passes():
    """A step whose facet declares the expected target as a mixin
    satisfies the FacetRef param type check."""
    src = """
namespace refs {
    facet Base(input: String) => (output: String) andThen {
        yield Base(output = $.input)
    }
    facet Specialized(input: String) => (output: String) with Base() andThen {
        yield Specialized(output = $.input) with Base(output = $.input)
    }
    facet Consumer(b: Base) => (output: String) andThen {
        yield Consumer(output = $.b.output)
    }
    workflow Demo(input: String) => (output: String) andThen {
        s = Specialized(input = $.input)
        c = Consumer(b = s)
        yield Demo(output = c.output)
    }
}
"""
    result = validate_ast(parse(src))
    assert not [e for e in result.errors if e.rule_id == "STEP_REF_FACET_MISMATCH"], (
        f"unexpected mismatch: {result.errors}"
    )


def test_step_ref_mismatch_for_unrelated_facet_still_errors():
    """An unrelated source facet (not a mixin) still fails."""
    src = """
namespace refs {
    facet A(input: String) => (output: String) andThen { yield A(output = $.input) }
    facet B(input: String) => (output: String) andThen { yield B(output = $.input) }
    facet Consumer(a: A) => (output: String) andThen { yield Consumer(output = $.a.output) }
    workflow Demo(input: String) => (output: String) andThen {
        b = B(input = $.input)
        c = Consumer(a = b)
        yield Demo(output = c.output)
    }
}
"""
    result = validate_ast(parse(src))
    mismatches = [e for e in result.errors if e.rule_id == "STEP_REF_FACET_MISMATCH"]
    assert len(mismatches) == 1
    assert "'A'" in str(mismatches[0])
    assert "'B'" in str(mismatches[0])


def test_step_ref_mismatch_in_mixin_args():
    """Mixin call args should also be checked."""
    src = """
namespace refs {
    facet DoSomething(input: String) => (output: String) andThen {
        yield DoSomething(output = $.input)
    }
    facet OtherFacet(value: String) => (output: String) andThen {
        yield OtherFacet(output = $.value)
    }
    facet WithRef(ds: DoSomething) => (output: String) andThen {
        yield WithRef(output = $.ds.output)
    }
    facet Composed() => (output: String) andThen {
        yield Composed(output = "x")
    }
    workflow Demo(input: String) => (output: String) andThen {
        bad = OtherFacet(value = $.input)
        c   = Composed() with WithRef(ds = bad)
        yield Demo(output = c.output)
    }
}
"""
    result = validate_ast(parse(src))
    mismatches = [e for e in result.errors if e.rule_id == "STEP_REF_FACET_MISMATCH"]
    assert len(mismatches) == 1, f"expected mismatch in mixin args, got {result.errors}"


# =========================================================================
# Evaluator
# =========================================================================


class _FakeStep:
    """Minimal StepDefinition shape consumed by the evaluator."""

    def __init__(self, step_id, workflow_id, facet_name, returns, params=None):
        self.id = step_id
        self.workflow_id = workflow_id
        self.facet_name = facet_name
        self.attributes = FacetAttributes(
            params={k: AttributeValue(k, v) for k, v in (params or {}).items()},
            returns={k: AttributeValue(k, v) for k, v in returns.items()},
        )


def test_eval_step_ref_bare_returns_step_reference():
    step = _FakeStep("sid-1", "wf-1", "DoSomething", {"output": "hello"})
    ctx = EvaluationContext(
        inputs={},
        get_step_output=lambda *_: None,
        get_step_by_name=lambda name: step if name == "s1" else None,
    )
    evaluator = ExpressionEvaluator()
    val = evaluator.evaluate({"type": "StepRef", "path": ["s1"]}, ctx)
    assert isinstance(val, StepReference)
    assert val.step_id == "sid-1"
    assert val.workflow_id == "wf-1"
    assert val.facet_name == "DoSomething"
    # caching populated
    assert "sid-1" in ctx._resolved_refs


def test_resolve_path_dereferences_step_reference():
    step = _FakeStep("sid-2", "wf-1", "DoSomething", {"output": "world"})
    ref = StepReference(step_id="sid-2", workflow_id="wf-1", facet_name="DoSomething")
    ctx = EvaluationContext(
        inputs={"ds": ref},
        get_step_output=lambda *_: None,
        get_step_by_id=lambda sid: step if sid == "sid-2" else None,
    )
    evaluator = ExpressionEvaluator()
    val = evaluator.evaluate({"type": "InputRef", "path": ["ds", "output"]}, ctx)
    assert val == "world"


def test_resolve_path_missing_callback_errors():
    ref = StepReference(step_id="sid-3", workflow_id="wf-1", facet_name="Anything")
    ctx = EvaluationContext(
        inputs={"ds": ref},
        get_step_output=lambda *_: None,
    )
    evaluator = ExpressionEvaluator()
    from facetwork.runtime.errors import ReferenceError

    with pytest.raises(ReferenceError, match="get_step_by_id"):
        evaluator.evaluate({"type": "InputRef", "path": ["ds", "output"]}, ctx)


def test_resolve_path_unknown_field_errors_on_param_or_return():
    step = _FakeStep("sid-4", "wf-1", "DoSomething", {"output": "x"})
    ref = StepReference(step_id="sid-4", workflow_id="wf-1", facet_name="DoSomething")
    ctx = EvaluationContext(
        inputs={"ds": ref},
        get_step_output=lambda *_: None,
        get_step_by_id=lambda sid: step,
    )
    evaluator = ExpressionEvaluator()
    from facetwork.runtime.errors import ReferenceError

    with pytest.raises(ReferenceError, match="no param, return, or mixin alias 'nope'"):
        evaluator.evaluate({"type": "InputRef", "path": ["ds", "nope"]}, ctx)


def test_resolve_path_reads_bound_input_via_step_ref():
    """A step-ref consumer reads bound inputs of the upstream step as well
    as its returns — both are persisted attributes of a fully-evaluated
    step."""
    step = _FakeStep(
        "sid-5",
        "wf-1",
        "Value",
        returns={},
        params={"input": "hi"},
    )
    ref = StepReference(step_id="sid-5", workflow_id="wf-1", facet_name="Value")
    ctx = EvaluationContext(
        inputs={"facetRef": ref},
        get_step_output=lambda *_: None,
        get_step_by_id=lambda sid: step,
    )
    evaluator = ExpressionEvaluator()
    val = evaluator.evaluate({"type": "InputRef", "path": ["facetRef", "input"]}, ctx)
    assert val == "hi"


def test_resolve_path_follows_mixin_alias_via_callback():
    """`$.fref.alias.field` looks up the mixin sub-step via
    get_mixin_step_by_alias and continues resolution into its
    attributes."""
    parent = _FakeStep("parent-1", "wf-1", "F2", returns={"output": "parent-out"})
    mixin = _FakeStep("mixin-1", "wf-1", "M1", returns={"output": "mixin-out"})
    ref = StepReference(step_id="parent-1", workflow_id="wf-1", facet_name="F2")
    ctx = EvaluationContext(
        inputs={"f2": ref},
        get_step_output=lambda *_: None,
        get_step_by_id=lambda sid: parent if sid == "parent-1" else None,
        get_mixin_step_by_alias=lambda pid, alias: (
            mixin if pid == "parent-1" and alias == "m1" else None
        ),
    )
    evaluator = ExpressionEvaluator()
    val = evaluator.evaluate({"type": "InputRef", "path": ["f2", "m1", "output"]}, ctx)
    assert val == "mixin-out"


def test_resolve_path_mixin_alias_unfound_errors():
    """If the alias callback returns None, the resolver reports a
    no-param-return-or-alias error rather than silently dereferencing
    something unrelated."""
    parent = _FakeStep("parent-2", "wf-1", "F2", returns={"output": "x"})
    ref = StepReference(step_id="parent-2", workflow_id="wf-1", facet_name="F2")
    ctx = EvaluationContext(
        inputs={"f2": ref},
        get_step_output=lambda *_: None,
        get_step_by_id=lambda sid: parent,
        get_mixin_step_by_alias=lambda pid, alias: None,
    )
    evaluator = ExpressionEvaluator()
    from facetwork.runtime.errors import ReferenceError

    with pytest.raises(ReferenceError, match="no param, return, or mixin alias 'bogus'"):
        evaluator.evaluate({"type": "InputRef", "path": ["f2", "bogus"]}, ctx)


def test_resolve_path_return_shadows_param_on_name_collision():
    """When a field name appears in both params and returns (e.g. a yielded
    value overrides the input under the same name), the return wins —
    matching FacetAttributes.merge() semantics."""
    step = _FakeStep(
        "sid-6",
        "wf-1",
        "Value",
        returns={"input": "yielded"},
        params={"input": "bound"},
    )
    ref = StepReference(step_id="sid-6", workflow_id="wf-1", facet_name="Value")
    ctx = EvaluationContext(
        inputs={"facetRef": ref},
        get_step_output=lambda *_: None,
        get_step_by_id=lambda sid: step,
    )
    evaluator = ExpressionEvaluator()
    val = evaluator.evaluate({"type": "InputRef", "path": ["facetRef", "input"]}, ctx)
    assert val == "yielded"


# =========================================================================
# Serialization
# =========================================================================


def test_step_reference_json_round_trip():
    ref = StepReference(step_id="abc", workflow_id="wf", facet_name="A")
    data = ref.to_json()
    assert data == {
        "_facet_ref": True,
        "step_id": "abc",
        "workflow_id": "wf",
        "facet_name": "A",
    }
    back = StepReference.from_json(data)
    assert back == ref


def test_serialize_attribute_value_handles_nested_refs():
    ref = StepReference(step_id="abc", workflow_id="wf", facet_name="A")
    nested = {"list": [ref, 1, "x"], "ref": ref, "plain": "ok"}
    out = serialize_attribute_value(nested)
    assert out["ref"] == ref.to_json()
    assert out["list"][0] == ref.to_json()
    assert out["list"][1:] == [1, "x"]
    assert out["plain"] == "ok"

    restored = deserialize_attribute_value(out)
    assert restored["ref"] == ref
    assert restored["list"][0] == ref


def test_is_ref_recognizes_both_forms():
    ref = StepReference(step_id="x", workflow_id="w", facet_name="A")
    assert StepReference.is_ref(ref)
    assert StepReference.is_ref(ref.to_json())
    assert not StepReference.is_ref({"foo": "bar"})
    assert not StepReference.is_ref("not-a-ref")


# =========================================================================
# End-to-end: yields land on distinct mixin sub-steps; FacetRef consumer
# reads each alias's returns independently.
# =========================================================================


_TWO_ALIASES_SRC = """
namespace two_aliases {
    facet M(input: String) => (output: String)

    facet F(input: String) => (output: String)
        with M() as primary
        with M() as fallback
        andThen {
            yield F(output = "main")
            yield primary(output = $.input ++ "-p")
            yield fallback(output = $.input ++ "-f")
        }

    facet Consumer(f: F) => (a: String, b: String, c: String) andThen {
        yield Consumer(
            a = $.f.output,
            b = $.f.primary.output,
            c = $.f.fallback.output
        )
    }

    workflow Demo(input: String) => (a: String, b: String, c: String) andThen {
        f1   = F(input = $.input)
        cons = Consumer(f = f1)
        yield Demo(a = cons.a, b = cons.b, c = cons.c)
    }
}
"""


def test_e2e_two_aliases_to_same_target_route_independently():
    """End-to-end execution of a workflow whose facet F declares two
    aliases (``primary`` and ``fallback``) on the same target mixin
    (``M``).  Each alias is populated by a separate ``yield alias(...)``
    statement; a downstream FacetRef consumer reads both aliases'
    returns alongside F's primary return.  Exercises:

    - validator: alias names are valid yield targets, and the bare
      target name ``M`` is *not* used (would be YIELD_TARGET_AMBIGUOUS).
    - runtime: ``StatementCaptureBeginHandler._route_yield`` resolves
      ``primary`` → primary sub-step, ``fallback`` → fallback sub-step,
      and ``F`` → parent.
    - resolver: ``resolve_mixin_step_by_alias`` returns the persisted
      sub-step (no synthesis), and the consumer's
      ``$.f.<alias>.output`` reads the routed return.
    """
    from facetwork.ast_utils import find_workflow
    from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry

    program = emit_dict(parse(_TWO_ALIASES_SRC))

    # Validator must accept the alias-named yields.
    val = validate_ast(parse(_TWO_ALIASES_SRC))
    assert not val.errors, f"unexpected validator errors: {val.errors}"

    workflow_ast = find_workflow(program, "Demo")
    store = MemoryStore()
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
    result = evaluator.execute(workflow_ast, inputs={"input": "hi"}, program_ast=program)

    assert result.success, f"workflow failed: {result.status}"
    assert result.status == ExecutionStatus.COMPLETED
    assert result.outputs == {"a": "main", "b": "hi-p", "c": "hi-f"}


def test_e2e_two_aliases_persist_distinct_substeps():
    """The runtime persists one sub-step per alias under F's step, with
    each carrying only its own routed return.  Verifies the two
    sub-steps are addressable separately (not synthesized on read)."""
    from facetwork.ast_utils import find_workflow
    from facetwork.runtime import Evaluator, MemoryStore, Telemetry

    program = emit_dict(parse(_TWO_ALIASES_SRC))
    workflow_ast = find_workflow(program, "Demo")
    store = MemoryStore()
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
    result = evaluator.execute(workflow_ast, inputs={"input": "x"}, program_ast=program)
    assert result.success

    # Find the variable-assignment F-step (the call ``f1 = F(...)``).
    # The same workflow also contains a yield-assignment step whose
    # facet_name is "F" (the body's ``yield F(output = ...)``); we want
    # the call site, not the yield row.
    from facetwork.runtime import ObjectType

    root_steps = list(store.get_steps_by_workflow(result.workflow_id))
    f_steps = [
        s
        for s in root_steps
        if s.facet_name
        and s.facet_name.split(".")[-1] == "F"
        and s.object_type == ObjectType.VARIABLE_ASSIGNMENT
    ]
    assert len(f_steps) == 1, (
        f"expected one F variable-assignment step, "
        f"got {[(s.facet_name, s.object_type) for s in f_steps]}"
    )
    f_step = f_steps[0]

    # The two aliased mixin sub-steps live as children of F with
    # statement_name set to the alias.
    children = list(store.get_steps_by_container(f_step.id))
    by_alias = {c.statement_name: c for c in children if c.statement_name}
    assert "primary" in by_alias, f"missing primary: {list(by_alias)}"
    assert "fallback" in by_alias, f"missing fallback: {list(by_alias)}"

    primary = by_alias["primary"]
    fallback = by_alias["fallback"]
    assert primary.attributes.returns["output"].value == "x-p"
    assert fallback.attributes.returns["output"].value == "x-f"
    # The two sub-steps carry only their own return — routing is per-alias.
    assert "fallback" not in primary.attributes.returns
    assert "primary" not in fallback.attributes.returns
