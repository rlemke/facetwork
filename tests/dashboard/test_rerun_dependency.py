"""Re-run From Here must delete only TRUE dependents, from the compiled AST.

Regression for the old start_time heuristic that deleted every sibling which
merely *started* after the target — destroying unrelated parallel-block work.
"""

from facetwork.dashboard.routes.execution.steps import _find_downstream_by_name


def _step(name, refs):
    return {
        "type": "Step",
        "id": name,
        "name": name,
        "call": {
            "target": "t.F",
            "args": [{"name": "x", "value": {"type": "StepRef", "path": [r]}} for r in refs],
            "mixins": [],
        },
    }


# s0 fans out to parallel a, b; a -> c; b has no dependents.
_BLOCK = {
    "steps": [_step("s0", []), _step("a", ["s0"]), _step("b", ["s0"]), _step("c", ["a"])],
    "yield": None,
}
_NAME_TO_RT = {"s0": "RT_s0", "a": "RT_a", "b": "RT_b", "c": "RT_c"}


def _ds(target, ast=_BLOCK):
    return _find_downstream_by_name(target, [], "blk", _NAME_TO_RT, ast)


def test_rerun_does_not_delete_parallel_sibling():
    # re-running 'a' must remove only its dependent 'c', never parallel 'b'
    assert _ds("a") == {"RT_c"}


def test_rerun_root_deletes_all_transitive_dependents():
    assert _ds("s0") == {"RT_a", "RT_b", "RT_c"}


def test_rerun_leaf_deletes_nothing():
    assert _ds("b") == set()
    assert _ds("c") == set()


def test_missing_ast_is_safe_underdelete():
    # No AST -> delete only the target itself (empty downstream), never guess.
    assert _ds("a", ast=None) == set()


def test_diamond_dependency():
    # s0 -> a,b ; both -> d. Re-running s0 gets everything; re-running a gets only d.
    block = {
        "steps": [
            _step("s0", []),
            _step("a", ["s0"]),
            _step("b", ["s0"]),
            _step("d", ["a", "b"]),
        ],
        "yield": None,
    }
    nm = {"s0": "RT_s0", "a": "RT_a", "b": "RT_b", "d": "RT_d"}
    assert _find_downstream_by_name("a", [], "blk", nm, block) == {"RT_d"}
    assert _find_downstream_by_name("s0", [], "blk", nm, block) == {"RT_a", "RT_b", "RT_d"}
