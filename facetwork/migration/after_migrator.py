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

"""Migrate the ``dependency_signal`` idiom to the ``after`` clause.

Before ``after`` existed, the only way to order two steps coupled through a
side channel (a shared cache, an object-store prefix, a scratch dir) was to
declare a parameter nobody reads and pass an arbitrary field into it, purely to
manufacture a data edge::

    map = BuildMigrationMap(dependency_signal = data.country_count)

This rewrites those call sites to say what they mean::

    map = BuildMigrationMap() after data

**What is rewritten**

* ``F(dependency_signal = step.field)``   → ``F() after step``
* ``F(a = 1, dependency_signal = s.x)``   → ``F(a = 1) after s``
* ``F(dependency_signal = 0)``            → ``F()`` — a literal never created an
  edge, so there is nothing to preserve.
* ``F(dependency_signal = a.n + b.n)``    → ``F() after a, b`` — a sum of step
  references whose value is discarded and whose only purpose is the edges it
  creates. This is the fan-in shape, and ``after`` states it directly.

**What is reported instead of rewritten**

Any value that is not built purely from step references and literals — notably
one reading a workflow input (``$.x``). The migrator cannot prove such a value
is inert, and guessing would either drop a real edge or invent one.

Safety rests on the parameter NAME: ``dependency_signal`` is a fleet-wide
convention meaning "this value is never read." A handler that actually consumes
it would be violating that convention, so a rewrite here is a rename of intent,
not a change of behavior.

The rewrite is by source location, so comments and formatting survive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ast import Program, StepStmt
from ..parser import FFLParser

PARAM_NAME = "dependency_signal"


@dataclass
class AfterMigrationResult:
    """Outcome of migrating one source file."""

    source: str
    changed: bool = False
    #: (line, note) for each applied rewrite
    edits: list[tuple[int, str]] = field(default_factory=list)
    #: (line, note) for each site a human must look at
    manual: list[tuple[int, str]] = field(default_factory=list)


def _iter_steps(node, out: list[StepStmt]) -> None:
    """Collect every StepStmt in the tree, at any block depth."""
    if isinstance(node, StepStmt):
        out.append(node)
    for attr in ("namespaces", "workflows", "facets", "event_facets", "steps", "extra_bodies"):
        for child in getattr(node, attr, None) or []:
            _iter_steps(child, out)
    for attr in ("body", "block", "catch", "when"):
        child = getattr(node, attr, None)
        if child is not None:
            _iter_steps(child, out)
    for case in getattr(node, "cases", None) or []:
        _iter_steps(case, out)


def _step_refs_only(expr) -> list[str] | None:
    """Distinct step names of an expression built ONLY from step refs/literals.

    Returns ``None`` if the expression contains anything else (an input
    reference, a call, an array…) — those can't be reduced to ordering edges
    without guessing. Order of first appearance is preserved so the rewritten
    `after` reads like the original expression.
    """
    names: list[str] = []

    def walk(node) -> bool:
        if node is None:
            return True
        path = getattr(node, "path", None)
        if path is not None:
            if getattr(node, "is_input", False):
                return False  # `$.x` — not a step edge
            name = path[0]
            if name not in names:
                names.append(name)
            return True
        if hasattr(node, "value") and not hasattr(node, "operands"):
            return True  # literal
        left, right = getattr(node, "left", None), getattr(node, "right", None)
        if left is not None or right is not None:
            return walk(left) and walk(right)
        operands = getattr(node, "operands", None)
        if operands is not None:
            return all(walk(o) for o in operands)
        return False

    return names if walk(expr) else None


def _insert_after_close(line: str, targets: str) -> str:
    """Insert ``after <targets>`` immediately after the call's closing ``)``.

    Appending to the end of the line would be wrong whenever the line already
    carries a following clause — `) catch {` must become `) after x catch {`,
    never `) catch { after x`, since the grammar puts `after` before the step's
    bodies.
    """
    eol = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    close = body.find(")")
    if close == -1:
        return f"{body.rstrip()} after {targets}{eol}"
    head, tail = body[: close + 1], body[close + 1 :]
    return f"{head} after {targets}{tail.rstrip()}{eol}"


def _arg_source(text: str, line: int) -> str:
    lines = text.splitlines()
    return lines[line - 1] if 0 < line <= len(lines) else ""


def drop_param_declarations(source: str) -> AfterMigrationResult:
    """Remove the now-unused ``dependency_signal`` PARAMETER from declarations.

    The final step of the migration, and the breaking one: once no call site
    passes the argument, the parameter is dead weight on the facet — an input it
    advertises and never reads. Removing it also removes the trap that made the
    idiom dangerous, where ``dependency_signal = 0`` type-checks, reads as
    ordering, and creates no edge at all.

    Run this ONLY after :func:`migrate_source` has cleared every call site, and
    only when no consumer can still pass the argument — a compiled AST stored
    elsewhere (a run snapshot, a catalog revision) that still passes it will
    fail against the new declaration.

    Refuses to touch a parameter that is still referenced in the same source, so
    a half-migrated file can't be broken silently.
    """
    ast: Program = FFLParser().parse(source)
    result = AfterMigrationResult(source=source)

    steps: list[StepStmt] = []
    _iter_steps(ast, steps)
    still_passed = [s for s in steps if any(a.name == PARAM_NAME for a in s.call.args)]
    if still_passed:
        result.manual.append(
            (
                still_passed[0].location.line if still_passed[0].location else 0,
                f"{len(still_passed)} call site(s) still pass {PARAM_NAME} — run the "
                f"call-site migration first, or removing the parameter will break them",
            )
        )
        return result

    lines = source.splitlines(keepends=True)
    decl_re = re.compile(rf"^\s*{PARAM_NAME}\s*:\s*\w+(\s*=\s*[^,)]+)?\s*,?\s*$")
    for idx in range(len(lines) - 1, -1, -1):
        if not decl_re.match(lines[idx].rstrip("\n")):
            continue
        result.edits.append((idx + 1, f"drop unused parameter {PARAM_NAME}"))
        del lines[idx]
        prev = idx - 1
        while prev >= 0 and not lines[prev].strip():
            prev -= 1
        nxt = idx
        while nxt < len(lines) and not lines[nxt].strip():
            nxt += 1
        if prev >= 0 and nxt < len(lines):
            prev_eol = "\n" if lines[prev].endswith("\n") else ""
            if lines[prev].rstrip().endswith("(") and lines[nxt].lstrip().startswith(")"):
                # It was the only parameter — `F(\n)` cannot span lines.
                lines[prev] = lines[prev].rstrip() + lines[nxt].lstrip().rstrip() + prev_eol
                del lines[nxt]
            elif lines[prev].rstrip().endswith(",") and lines[nxt].lstrip().startswith(")"):
                lines[prev] = lines[prev].rstrip().rstrip(",") + prev_eol

    if result.edits:
        result.source = "".join(lines)
        result.changed = True
        result.edits.sort()
    return result


def migrate_source(source: str) -> AfterMigrationResult:
    """Rewrite ``dependency_signal`` call sites in *source* to ``after``."""
    ast: Program = FFLParser().parse(source)
    steps: list[StepStmt] = []
    _iter_steps(ast, steps)

    result = AfterMigrationResult(source=source)
    # Line-based edits, applied bottom-up so earlier locations stay valid.
    rewrites: list[tuple[int, str, str, str]] = []  # (line, note, old_line, new_line)

    for step in steps:
        arg = next((a for a in step.call.args if a.name == PARAM_NAME), None)
        if arg is None:
            continue
        line = (arg.location or step.location).line if (arg.location or step.location) else 0
        value = arg.value

        target: str | None = None
        drop_only = False

        # A plain step reference: `dependency_signal = data.country_count`
        path = getattr(value, "path", None)
        if path and not getattr(value, "is_input", False):
            target = path[0]
        # A literal: `dependency_signal = 0` — never created an edge at all.
        elif hasattr(value, "value") and not path:
            drop_only = True
        # A compound of step references only, e.g.
        # `a.feature_count + b.feature_count + c.feature_count` — a sum whose
        # VALUE is discarded and whose only purpose is the three edges it
        # creates. That is precisely `after a, b, c`, so it is safe to rewrite:
        # the same steps are still ordered before this one. A compound
        # containing anything else (an input, a call) is left to a human.
        elif (refs := _step_refs_only(value)) is not None and refs:
            target = ", ".join(refs)
        else:
            result.manual.append(
                (
                    line,
                    f"step '{step.name}': {PARAM_NAME} value is not a plain step "
                    f"reference or literal — decide by hand whether an ordering "
                    f"edge is needed",
                )
            )
            continue

        old_line = _arg_source(source, line)
        if PARAM_NAME not in old_line:
            # The argument spans lines; a single-line rewrite would corrupt it.
            result.manual.append(
                (line, f"step '{step.name}': {PARAM_NAME} argument spans lines — rewrite by hand")
            )
            continue

        # Drop `dependency_signal = <expr>` plus one adjacent comma/whitespace.
        new_line = re.sub(
            rf",\s*{PARAM_NAME}\s*=\s*[^,)]+|{PARAM_NAME}\s*=\s*[^,)]+,\s*|{PARAM_NAME}\s*=\s*[^,)]+",
            "",
            old_line,
            count=1,
        )
        note = (
            f"step '{step.name}': drop inert {PARAM_NAME} = <literal>"
            if drop_only
            else f"step '{step.name}': {PARAM_NAME} → after {target}"
        )
        # Where the `after` clause goes: the END of the call expression, taken
        # from the PARSER, not by scanning for a ')'. Scanning is wrong whenever
        # a later argument contains a ')' inside a string literal — it silently
        # lands the clause inside that string, which still compiles and quietly
        # drops the ordering edge. (Observed on save_earth's BuildEnclaveMap,
        # whose `description` contains "(Chinatown, …)".)
        loc = step.call.location
        end = (loc.end_line, loc.end_column) if loc and loc.end_line else None
        if target and end is None:
            result.manual.append(
                (line, f"step '{step.name}': no end location for the call — rewrite by hand")
            )
            continue
        rewrites.append(
            (line, note, old_line, new_line if drop_only else (new_line, target), end)  # type: ignore[arg-type]
        )

    if not rewrites:
        return result

    lines = source.splitlines(keepends=True)
    for line_no, note, old_line, new, end in sorted(rewrites, key=lambda r: -r[0]):
        idx = line_no - 1
        if idx < 0 or idx >= len(lines):
            continue
        target = None
        if isinstance(new, tuple):
            new_line, target = new
        else:
            new_line = new
        eol = "\n" if lines[idx].endswith("\n") else ""
        rewritten = new_line
        if target:
            # Insert at the call expression's exact end (parser-supplied), so a
            # ')' inside a string literal can never be mistaken for the call's.
            end_idx, end_col = end[0] - 1, end[1] - 1
            if end_idx == idx:
                # Same line: dropping the argument shortened it, so shift the
                # parser's column by exactly what the removal took out.
                end_col -= len(old_line) - len(new_line)
                rewritten = f"{new_line[:end_col]} after {target}{new_line[end_col:].rstrip()}"
            elif 0 <= end_idx < len(lines):
                tail = lines[end_idx]
                tail_eol = "\n" if tail.endswith("\n") else ""
                lines[end_idx] = (
                    f"{tail[:end_col]} after {target}{tail[end_col:].rstrip()}{tail_eol}"
                )
            else:
                result.manual.append((line_no, f"call end out of range for {note}"))
                continue

        if rewritten.strip():
            lines[idx] = rewritten.rstrip() + eol
        else:
            # The argument was alone on its line: drop the line entirely rather
            # than leave a blank inside the call, then clean up the comma the
            # preceding argument no longer needs.
            del lines[idx]
            prev = idx - 1
            while prev >= 0 and not lines[prev].strip():
                prev -= 1
            nxt = idx
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if prev >= 0 and nxt < len(lines):
                prev_eol = "\n" if lines[prev].endswith("\n") else ""
                if lines[prev].rstrip().endswith("(") and lines[nxt].lstrip().startswith(")"):
                    # It was the call's ONLY argument. `F(\n)` is not valid FFL —
                    # an empty argument list cannot span lines — so pull the
                    # parens back together: `F() after x`.
                    lines[prev] = lines[prev].rstrip() + lines[nxt].lstrip().rstrip() + prev_eol
                    del lines[nxt]
                elif lines[prev].rstrip().endswith(",") and lines[nxt].lstrip().startswith(")"):
                    # The preceding argument no longer needs its trailing comma.
                    lines[prev] = lines[prev].rstrip().rstrip(",") + prev_eol
        result.edits.append((line_no, note))

    result.source = "".join(lines)
    result.changed = bool(result.edits)
    result.edits.sort()
    return result
