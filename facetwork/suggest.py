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

"""Did-you-mean suggestions for validator errors.

The REF and TYPE rule families are 26 of the 63 rule_ids the validator emits —
by far the most common way to get FFL wrong is to *refer to something* by a name
that is not there, or not there from here. "Reference to undefined step 'resolvd'"
is accurate and unhelpful; the author has to go and read the block to find out
what the name should have been, and a newcomer has to first learn which names are
even eligible.

Two things fix most of that, and both are computable at the point of the error:

* the nearest name among the ones actually in scope, and
* the list of names in scope, so a wrong guess still teaches the scoping rule.

Deliberately stdlib-only (``difflib``): the validator is part of the compiler,
whose only runtime dependency is lark.
"""

from __future__ import annotations

from collections.abc import Iterable
from difflib import get_close_matches

# Below this ratio the "suggestion" is noise — proposing 'counties' for 'x' sends
# the reader off after something unrelated, which is worse than staying silent.
_CUTOFF = 0.6

# Enough to orient, short enough to read in a terminal. Long lists are truncated
# with a count so the author knows the list was cut rather than exhaustive.
_MAX_LISTED = 8


def closest(name: str, candidates: Iterable[str]) -> str | None:
    """The nearest candidate to *name*, or None when nothing is close enough."""
    pool = [c for c in dict.fromkeys(candidates) if c and c != name]
    if not pool:
        return None
    matches = get_close_matches(name, pool, n=1, cutoff=_CUTOFF)
    if matches:
        return matches[0]
    # A qualified/unqualified mismatch scores badly on edit distance but is a
    # very common slip (`ResolveCapital` for `capitals.ResolveCapital`), so match
    # on the last segment too before giving up.
    tail_matches = [c for c in pool if c.rsplit(".", 1)[-1] == name.rsplit(".", 1)[-1]]
    return tail_matches[0] if tail_matches else None


def did_you_mean(name: str, candidates: Iterable[str]) -> str:
    """``" did you mean 'x'?"`` for the nearest candidate, else ``""``.

    Returns a fragment ready to append to a message, so call sites stay a single
    f-string and cannot accidentally emit "did you mean ''?".
    """
    best = closest(name, candidates)
    return f" — did you mean '{best}'?" if best else ""


def in_scope(label: str, names: Iterable[str]) -> str:
    """``" (steps in scope here: a, b, c)"`` — what the author COULD have written.

    Empty when there is nothing in scope, because "(steps in scope here: )" reads
    as a bug in the compiler rather than as an empty block.
    """
    listed = sorted({n for n in names if n})
    if not listed:
        return ""
    shown = ", ".join(listed[:_MAX_LISTED])
    if len(listed) > _MAX_LISTED:
        shown += f", … (+{len(listed) - _MAX_LISTED} more)"
    return f" ({label}: {shown})"


def suggestion(name: str, candidates: Iterable[str], label: str) -> str:
    """did-you-mean plus the in-scope list — the usual pairing.

    The list is the load-bearing half: a near-miss gets a name to copy, and a
    wild miss still learns which names are eligible from this position, which is
    the scoping rule the author actually got wrong.
    """
    pool = list(candidates)
    return f"{did_you_mean(name, pool)}{in_scope(label, pool)}"


def dollar_ladder(frames: list[tuple[str, Iterable[str]]], attr: str) -> str:
    """Explain what each ``$`` depth refers to, and where *attr* actually lives.

    ``$``-overflow is the scoping error that most needs showing rather than
    telling: the author knows they want ``states`` and has guessed at the number
    of dollars. Printing the ladder answers "how many?" directly.

    *frames* is innermost-first: ``[("the foreach body", ["s"]), ...]``.
    """
    if not frames:
        return ""
    lines = []
    found_at: int | None = None
    for depth, (name, attrs) in enumerate(frames):
        attr_list = sorted({a for a in attrs if a})
        if attr in attr_list and found_at is None:
            found_at = depth
        shown = ", ".join(attr_list[:_MAX_LISTED]) if attr_list else "no attributes"
        if attr_list and len(attr_list) > _MAX_LISTED:
            shown += f", … (+{len(attr_list) - _MAX_LISTED} more)"
        lines.append(f"    {'$' * (depth + 1):<6} = {name} ({shown})")

    out = "\n  the containers in scope here are:\n" + "\n".join(lines)
    if found_at is not None:
        out += f"\n  '{attr}' is on {'$' * (found_at + 1)} — try '{'$' * (found_at + 1)}.{attr}'"
    return out
