"""Every built-in facet that declares itself costly MUST signal liveness.

⚠️ This exists because the convention did not hold. On 2026-09-01 a
`fw.compare.Tabular` comparing two 100 MB+ VCFs was reclaimed mid-run — "server
silent for 120s" — and re-dispatched onto a second host while the first was
still working. Investigating showed NO built-in handler signalled liveness at
all, though the runtime injects `_task_heartbeat` into every handler's params.

The same defect had been fixed FOUR times in domain handlers earlier that day
and then reproduced in fresh code hours later. A rule remembered is a rule that
lapses; this makes it a test.

The rule is anchored to a DECLARED property — the `Cost(tier=…)` mixin in the
facet's own FFL — rather than to a guess about which handlers are slow. If a
facet claims to be costly, its handler must be able to say it is still alive.
Declaring a genuinely slow facet `cheap` to dodge this check would be a lie in
the FFL, where it is visible, rather than a silent omission in Python.
"""
from __future__ import annotations

import importlib
import inspect
import pathlib
import re

import pytest

from facetwork.handlers import _BUILTIN_MODULES

#: Tiers whose handlers block long enough to be reclaimed without a heartbeat.
COSTLY = {"moderate", "expensive"}
FFL_DIR = pathlib.Path(__file__).resolve().parents[2] / "facetwork" / "ffl"


def _declared_costs(ffl_name: str) -> dict[str, str]:
    """facet short-name -> declared cost tier, from the FFL that declares it."""
    text = (FFL_DIR / ffl_name).read_text()
    out: dict[str, str] = {}
    for m in re.finditer(r"event facet (\w+)\(", text):
        nxt = text.find("event facet ", m.end())
        seg = text[m.end(): nxt if nxt > 0 else len(text)]
        cost = re.search(r'Cost\(tier = "(\w+)"\)', seg)
        out[m.group(1)] = cost.group(1) if cost else "unset"
    return out


def _cases():
    cases = []
    for module_name, ffl in _BUILTIN_MODULES:
        costs = _declared_costs(ffl)
        mod = importlib.import_module(module_name)
        for facet, fn in getattr(mod, "_DISPATCH", {}).items():
            short = facet.rsplit(".", 1)[-1]
            cases.append((facet, costs.get(short, "unset"), fn))
    return cases


CASES = _cases()


@pytest.mark.parametrize(
    "facet,tier,fn",
    [c for c in CASES if c[1] in COSTLY],
    ids=[c[0] for c in CASES if c[1] in COSTLY],
)
def test_costly_builtin_heartbeats(facet, tier, fn):
    # Ask the OBJECT, not its text: functools.wraps makes inspect.getsource
    # return the undecorated source, so a text check silently misses the
    # decorator form and would have to be satisfied the harder way.
    marked = getattr(fn, "_fw_heartbeats", None) is not None
    inline = "heartbeating" in inspect.getsource(fn)
    assert marked or inline, (
        f"{facet} declares Cost(tier=\"{tier}\") but its handler never signals "
        f"liveness. A blocking call with no heartbeat is reclaimed mid-run and "
        f"re-dispatched onto another host — two machines then do the same work. "
        f"Wrap the blocking phase in facetwork.handlers._heartbeat.heartbeating."
    )


def test_every_builtin_facet_declares_a_cost():
    """An undeclared cost silently exempts a handler from the rule above."""
    undeclared = [f for f, tier, _fn in CASES if tier == "unset"]
    assert not undeclared, f"facets with no Cost(tier=…): {undeclared}"


def test_the_rule_covers_something():
    """Guard against the check passing because it matched nothing."""
    assert [c for c in CASES if c[1] in COSTLY], "no costly built-ins found — check the parser"
