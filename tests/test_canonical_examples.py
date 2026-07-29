"""Every file in examples/canonical/ must be a validator-clean template.

CLAUDE.md points authors (and Claude) at this directory as the starting point for
new FFL, and the MCP server serves the files at `fw://examples/canonical/{name}`.
A canonical example that no longer validates teaches the wrong thing at exactly
the moment someone is copying it, so the property is pinned here rather than
left to a spot check.
"""

import pathlib

import pytest

from facetwork.parser import FFLParser
from facetwork.validator import validate

CANONICAL = pathlib.Path(__file__).resolve().parent.parent / "examples" / "canonical"
EXAMPLES = sorted(CANONICAL.glob("*.ffl"))


def test_canonical_directory_is_not_empty():
    """Guard against the glob silently matching nothing (moved/renamed dir)."""
    assert EXAMPLES, f"no .ffl files found under {CANONICAL}"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_canonical_example_parses_and_validates(path):
    program = FFLParser().parse(path.read_text())
    result = validate(program)
    errors = [
        f"{getattr(e, 'rule_id', '?')}: {getattr(e, 'message', e)}"
        for e in result.errors
    ]
    assert not errors, f"{path.name} is not validator-clean:\n  " + "\n  ".join(errors)
