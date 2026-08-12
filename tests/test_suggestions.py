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

"""Validator messages should name the fix, not just the fault.

REF and TYPE are 26 of the 63 rule_ids: the most common way to get FFL wrong is
to refer to something by a name that is not there, or not there *from here*.
"Reference to undefined step 'resolvd'" is accurate and useless — the author
must go read the block to learn what the name should have been, and a newcomer
must first learn which names are even eligible from that position.

These tests pin the two halves that fix it: the near-match to copy, and the
in-scope list that teaches the scoping rule when the guess was not near.
"""

from __future__ import annotations

from facetwork import parse, suggest
from facetwork.validator import validate


def _messages(source: str) -> tuple[list[str], list[str]]:
    result = validate(parse(source))
    return [str(e) for e in result.errors], [str(w) for w in result.warnings]


# --------------------------------------------------------------------------
# The suggestion primitives
# --------------------------------------------------------------------------

def test_a_near_miss_is_suggested():
    assert suggest.closest("fetchd", ["fetched", "combined"]) == "fetched"


def test_an_unrelated_name_suggests_nothing():
    """Silence beats sending the reader after something irrelevant."""
    assert suggest.closest("zzz", ["fetched", "combined"]) is None


def test_a_qualification_slip_is_matched_on_the_last_segment():
    """`ResolveCapital` for `capitals.ResolveCapital` scores badly on edit
    distance but is one of the most common mistakes there is."""
    assert suggest.closest("ResolveCapital", ["capitals.ResolveCapital"]) == (
        "capitals.ResolveCapital"
    )


def test_the_name_itself_is_never_suggested():
    assert suggest.closest("fetched", ["fetched"]) is None


def test_an_empty_scope_produces_no_list():
    """"(steps in scope here: )" reads as a compiler bug, not an empty block."""
    assert suggest.in_scope("steps in scope here", []) == ""


def test_long_scopes_are_truncated_with_a_count():
    out = suggest.in_scope("steps", [f"s{i}" for i in range(20)])
    assert "more)" in out, "a truncated list must say it was truncated"


# --------------------------------------------------------------------------
# End to end, through the validator
# --------------------------------------------------------------------------

STEPS = """
namespace demo {
    event facet Fetch() => (path: String, count: Int)
    workflow W() => (n: Int) andThen {
        fetched = Fetch()
        other = Fetch()
        yield W(n = fetchd.count)
    }
}
"""


def test_an_undefined_step_names_the_near_miss_and_the_scope():
    errors, _ = _messages(STEPS)
    msg = next(m for m in errors if "undefined step" in m)
    assert "did you mean 'fetched'?" in msg
    assert "fetched" in msg and "other" in msg, "the in-scope list is missing"


ATTR = """
namespace demo {
    event facet Fetch() => (cache_path: String, count: Int)
    workflow W() => (n: Int) andThen {
        fetched = Fetch()
        yield W(n = fetched.cache_paths)
    }
}
"""


def test_a_wrong_attribute_names_the_near_miss():
    errors, _ = _messages(ATTR)
    msg = next(m for m in errors if "Invalid attribute" in m)
    assert "did you mean 'cache_path'?" in msg


OVERFLOW = """
namespace demo {
    event facet Unit(code: String, width: Int) => (out: String)
    facet Fan(states: Json, width: Int) => (paths: Json)
        andThen foreach s in $.states {
            u = Unit(code = $.s, width = $$$$.width)
            yield Fan(paths = u.out)
        }
}
"""


def test_dollar_overflow_shows_the_ladder_and_the_right_depth():
    """The author knows which attribute they want and guessed the dollar count.

    Printing the ladder answers "how many?" directly rather than making them
    count braces in the source.
    """
    errors, _ = _messages(OVERFLOW)
    msg = next(m for m in errors if "walks up" in m)
    assert "the containers in scope here are" in msg
    assert "try '$.width'" in msg, "the correct depth was not named"


def test_the_location_stays_on_the_summary_line():
    """A multi-line explanation must not push the position onto its last line,
    where it would appear to point at the advice rather than the reference."""
    errors, _ = _messages(OVERFLOW)
    msg = next(m for m in errors if "walks up" in m)
    assert "at line" in msg.splitlines()[0]


TYPO_FACET = """
namespace demo {
    event facet ResolveCapital(code: String) => (out: String)
    workflow W() => (n: String) andThen {
        r = ResolveCaptal(code = "US-CA")
        yield W(n = r.out)
    }
}
"""


def test_a_typoed_facet_warns_instead_of_compiling_silently():
    """The worst authoring failure in the language: a mistyped facet name is
    legal (it may be declared in a library compiled separately), so it compiles
    clean and fails at RUN time as "no handler" — far from the offending line.
    """
    errors, warnings = _messages(TYPO_FACET)
    assert not errors, "an undeclared facet must stay legal, not become an error"
    msg = next((w for w in warnings if "ResolveCaptal" in w), None)
    assert msg is not None, "a typoed facet passed with no warning at all"
    assert "did you mean 'demo.ResolveCapital'?" in msg


GENUINELY_EXTERNAL = """
namespace demo {
    workflow W() => (n: String) andThen {
        r = some.other.library.Thing(code = "x")
        yield W(n = r.out)
    }
}
"""


def test_a_genuinely_external_facet_is_not_warned_about():
    """False positives here would train people to ignore the warning.

    With no near candidate the reference really is external, so silence is
    correct — that is what keeps the signal worth reading.
    """
    _, warnings = _messages(GENUINELY_EXTERNAL)
    assert not [w for w in warnings if "treated as external" in w]
