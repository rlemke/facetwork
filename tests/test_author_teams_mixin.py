"""Tests for the FFL Author/Teams annotation mixins.

Covers extraction from the compiled AST (the capability index + the public
``author_and_teams`` helper) and the canonical example's validity.
"""

from pathlib import Path

from facetwork import emit_dict, parse
from facetwork.capabilities import author_and_teams, index_program
from facetwork.validator import validate

_SRC = """
namespace demo {
    event facet Do(x: String) => (y: String)
      with Author(email = "facet-owner@x.com")

    workflow W(x: String) => (y: String)
      with Author(email = "ada@example.com")
      with Teams(names = ["geo", "research"])
      andThen {
        d = Do(x = $.x)
        yield W(y = d.y)
    }
}
"""


def _program():
    return emit_dict(parse(_SRC))


def _find_workflow(program: dict) -> dict:
    from facetwork.ast_utils import find_workflow

    wf = find_workflow(program, "demo.W")
    assert wf is not None
    return wf


def test_author_and_teams_from_workflow_mixins():
    author, teams = author_and_teams(_find_workflow(_program()).get("mixins"))
    assert author == "ada@example.com"
    assert teams == ["geo", "research"]


def test_author_and_teams_absent_returns_empty():
    assert author_and_teams(None) == ("", [])
    assert author_and_teams([]) == ("", [])
    # a mixin with no Author/Teams target
    assert author_and_teams([{"target": "Effect", "args": []}]) == ("", [])


def test_capability_index_carries_author_and_teams():
    caps = {c.name: c for c in index_program(_program())}
    assert caps["Do"].author == "facet-owner@x.com"
    assert caps["Do"].teams == []
    assert "Author" in caps["Do"].mixins
    # round-trips through to_dict for the MCP surface
    assert caps["Do"].to_dict()["author"] == "facet-owner@x.com"


def test_teams_array_of_one():
    from facetwork.ast_utils import find_workflow

    src = """
namespace d {
    workflow X() => (z: String)
      with Teams(names = ["solo"])
      andThen { yield X(z = "ok") }
}
"""
    wf = find_workflow(emit_dict(parse(src)), "d.X")
    _author, teams = author_and_teams(wf.get("mixins"))
    assert teams == ["solo"]


def test_canonical_example_is_valid():
    path = Path(__file__).parent.parent / "examples" / "canonical" / "11-author-teams.ffl"
    res = validate(parse(path.read_text()))
    assert res.is_valid, res.errors
