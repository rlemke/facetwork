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

"""Migrate legacy (flat-scoped) FFL to the relative ``$``-scoping model.

Given FFL written for the legacy resolver, rewrite the references that change
meaning under relative scoping (docs/architecture/ffl-relative-scoping.md):

1. **containing/enclosing step by name → ``$``/``$$``.** ``s.field`` where ``s``
   is an enclosing container becomes ``$.field`` (immediate) or ``$$.field`` …
   for deeper levels.
2. **flat ``$.x`` that reaches an outer container → ``$$.x``.** Legacy ``$.x``
   meant "the workflow/facet input x" at any depth; under relative scoping it is
   the immediate container, so a ``$.x`` inside a nested body whose immediate
   container has no ``x`` is re-pointed up to the level that declares it.

Both are meaning-preserving re-targetings computed from a container stack that
mirrors the validator. Edits are applied by source location so comments and
formatting survive.

**Not auto-migrated (reported for manual handling):** the sibling-block
``andThen when`` idiom (a ``when`` block reading a *prior* block's step). That
is a *structural* change — the ``when`` must be reattached to the step it gates —
so it is surfaced as a ``manual`` note rather than rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..ast import (
    AndThenBlock,
    ArrayLiteral,
    BinaryExpr,
    CatchClause,
    ConcatExpr,
    EventFacetDecl,
    FacetDecl,
    IndexExpr,
    MapLiteral,
    Reference,
    UnaryExpr,
    WorkflowDecl,
)
from ..parser import parse


@dataclass
class _Frame:
    """One container in the lexical stack (innermost last)."""

    name: str  # facet/workflow name, or a step's statement name
    attrs: set[str]  # params ∪ returns of the container


@dataclass
class _Edit:
    line: int
    col: int
    end_line: int
    end_col: int
    new_text: str
    note: str


@dataclass
class MigrationResult:
    """Outcome of migrating one FFL source string."""

    source: str  # the migrated source (unchanged if no edits)
    changed: bool
    edits: list[tuple[int, int, str]] = field(default_factory=list)  # (line, col, note)
    manual: list[tuple[int, int, str]] = field(default_factory=list)  # (line, col, note)


def _sig_attrs(sig: Any) -> set[str]:
    names = {p.name for p in sig.params}
    if sig.returns:
        names |= {p.name for p in sig.returns.params}
    return names


def _build_attr_map(program: Any) -> dict[str, set[str]]:
    """Map facet/workflow name (short and qualified) → its attribute surface."""
    attr_map: dict[str, set[str]] = {}
    for ns in program.namespaces:
        for decl in list(ns.facets) + list(ns.event_facets) + list(ns.workflows):
            attrs = _sig_attrs(decl.sig)
            attr_map[decl.sig.name] = attrs
            attr_map[f"{ns.name}.{decl.sig.name}"] = attrs
    return attr_map


class _Migrator:
    def __init__(self, attr_map: dict[str, set[str]]):
        self._attrs = attr_map
        self._stack: list[_Frame] = []
        self.edits: list[_Edit] = []
        self.manual: list[_Edit] = []

    # -- reference rewriting -------------------------------------------------

    def _frame_depth_with_attr(self, name: str) -> int | None:
        # Depth 0 = the immediate (innermost) container = the LAST stack entry.
        for depth, frame in enumerate(reversed(self._stack)):
            if name in frame.attrs:
                return depth
        return None

    def _frame_depth_named(self, step_name: str) -> int | None:
        # Innermost-first: a container step's own body reaches it at depth 0.
        for depth, frame in enumerate(reversed(self._stack)):
            if frame.name == step_name:
                return depth
        return None

    def _rewrite_ref(
        self, ref: Reference, block_steps: set[str], loop_vars: set[str]
    ) -> None:
        loc = ref.location
        if loc is None or loc.end_line is None or loc.end_column is None:
            return
        if ref.is_input:
            # Legacy sources only ever wrote a single '$' (up_levels == 0).
            if ref.up_levels != 0 or not ref.path:
                return
            head = ref.path[0]
            if head in loop_vars:
                return  # loop var stays $.v on the immediate frame
            depth = self._frame_depth_with_attr(head)
            if depth is None or depth == 0:
                return  # already correct, or unknown → leave for manual review
            new = "$" * (depth + 1) + "." + ".".join(ref.path)
            self.edits.append(
                _Edit(loc.line, loc.column, loc.end_line, loc.end_column, new,
                      f"$.{head} → {'$' * (depth + 1)}.{head} (outer container)")
            )
        else:
            if not ref.path:
                return
            step_name = ref.path[0]
            if step_name in block_steps or step_name in loop_vars:
                return  # same-block sibling / loop var — valid as written
            depth = self._frame_depth_named(step_name)
            if depth is None:
                # Not an enclosing container in scope — a sibling-block ref
                # (REF_CROSS_BLOCK_STEP, e.g. sibling `andThen when`). Structural;
                # report for manual handling.
                self.manual.append(
                    _Edit(loc.line, loc.column, loc.end_line, loc.end_column, "",
                          f"'{step_name}.…' references a sibling block — reattach "
                          f"the when/clause to the step (manual)")
                )
                return
            new = "$" * (depth + 1) + ("." + ".".join(ref.path[1:]) if len(ref.path) > 1 else "")
            self.edits.append(
                _Edit(loc.line, loc.column, loc.end_line, loc.end_column, new,
                      f"{step_name}.… → {'$' * (depth + 1)}.… (enclosing step by name)")
            )

    # -- expression / node traversal ----------------------------------------

    def _walk_expr(self, node: Any, block_steps: set[str], loop_vars: set[str]) -> None:
        if isinstance(node, Reference):
            self._rewrite_ref(node, block_steps, loop_vars)
        elif isinstance(node, ConcatExpr):
            for op in node.operands:
                self._walk_expr(op, block_steps, loop_vars)
        elif isinstance(node, BinaryExpr):
            self._walk_expr(node.left, block_steps, loop_vars)
            self._walk_expr(node.right, block_steps, loop_vars)
        elif isinstance(node, UnaryExpr):
            self._walk_expr(node.operand, block_steps, loop_vars)
        elif isinstance(node, IndexExpr):
            self._walk_expr(node.target, block_steps, loop_vars)
            self._walk_expr(node.index, block_steps, loop_vars)
        elif isinstance(node, ArrayLiteral):
            for el in node.elements:
                self._walk_expr(el, block_steps, loop_vars)
        elif isinstance(node, MapLiteral):
            for entry in node.entries:
                self._walk_expr(entry.value, block_steps, loop_vars)

    def _walk_call(self, call: Any, block_steps: set[str], loop_vars: set[str]) -> None:
        for arg in call.args:
            self._walk_expr(arg.value, block_steps, loop_vars)
        # Call-site mixins (`with M(...)`) carry their own args but no nested
        # mixins; walk each mixin's arguments.
        for mixin in getattr(call, "mixins", []):
            for arg in mixin.args:
                self._walk_expr(arg.value, block_steps, loop_vars)

    def _step_attrs(self, call: Any) -> set[str]:
        name = call.name
        return self._attrs.get(name) or self._attrs.get(name.split(".")[-1]) or set()

    def _walk_and_then_block(
        self, body: AndThenBlock, loop_vars: set[str]
    ) -> None:
        if not isinstance(body, AndThenBlock):
            return  # PromptBlock / ScriptBlock facet bodies carry no step refs
        if body.script:
            return
        if body.when:
            for case in body.when.cases:
                if case.condition is not None:
                    self._walk_expr(case.condition, set(), loop_vars)
                if case.block:
                    self._walk_block(case.block, loop_vars)
            return
        if not body.block:
            return

        # Steps visible for same-block sibling detection = this clause's own
        # steps. The container the clause hangs off is reached via $ (on the
        # stack), NOT by name — so its name is deliberately excluded here.
        clause_steps = {s.name for s in body.block.steps if getattr(s, "name", None)}
        new_loop_vars = set(loop_vars)
        if body.foreach:
            # The collection is evaluated in the clause scope (before the var
            # binds); it may name the container via $ but not a sibling block.
            self._walk_expr(body.foreach.iterable, clause_steps, loop_vars)
            new_loop_vars = set(loop_vars) | {body.foreach.variable}
        self._walk_block(body.block, new_loop_vars)

    def _walk_block(self, block: Any, loop_vars: set[str]) -> None:
        block_steps = {s.name for s in block.steps if hasattr(s, "name") and s.name}
        for step in block.steps:
            call = getattr(step, "call", None)
            if call is None:
                continue
            self._walk_call(call, block_steps, loop_vars)
            clauses = ([step.body] if step.body else []) + list(getattr(step, "extra_bodies", []))
            if clauses:
                self._stack.append(_Frame(name=step.name, attrs=self._step_attrs(call)))
                for clause in clauses:
                    self._walk_and_then_block(clause, loop_vars)
                if step.catch:
                    self._walk_catch(step.catch, loop_vars)
                self._stack.pop()
            elif step.catch:
                self._walk_catch(step.catch, loop_vars)
        for y in block.yield_stmts:
            self._walk_call(y.call, block_steps, loop_vars)

    def _walk_catch(self, catch: CatchClause, loop_vars: set[str]) -> None:
        if catch.when:
            for case in catch.when.cases:
                if case.condition is not None:
                    self._walk_expr(case.condition, set(), loop_vars)
                if case.block:
                    self._walk_block(case.block, loop_vars)
        elif catch.block:
            self._walk_block(catch.block, loop_vars)

    def walk_decl(self, decl: Any) -> None:
        body = decl.body
        if body is None:
            return
        self._stack = [_Frame(name=decl.sig.name, attrs=_sig_attrs(decl.sig))]
        self._current_block_steps = set()
        blocks = body if isinstance(body, list) else [body]
        for blk in blocks:
            self._walk_and_then_block(blk, set())
        if decl.catch:
            self._walk_catch(decl.catch, set())


def _apply_edits(source: str, edits: list[_Edit]) -> str:
    lines = source.split("\n")

    def offset(line: int, col: int) -> int:
        # 1-based line, 1-based column → absolute string offset.
        return sum(len(lines[i]) + 1 for i in range(line - 1)) + (col - 1)

    spans = sorted(
        ((offset(e.line, e.col), offset(e.end_line, e.end_col), e.new_text) for e in edits),
        key=lambda t: t[0],
        reverse=True,
    )
    out = source
    for start, end, new in spans:
        out = out[:start] + new + out[end:]
    return out


def migrate_source(source: str) -> MigrationResult:
    """Migrate one FFL source string to the relative-scoping model."""
    program = parse(source)
    attr_map = _build_attr_map(program)
    m = _Migrator(attr_map)
    for ns in program.namespaces:
        for decl in list(ns.facets) + list(ns.event_facets) + list(ns.workflows):
            if isinstance(decl, (FacetDecl, EventFacetDecl, WorkflowDecl)):
                m.walk_decl(decl)

    new_source = _apply_edits(source, m.edits) if m.edits else source
    return MigrationResult(
        source=new_source,
        changed=bool(m.edits),
        edits=[(e.line, e.col, e.note) for e in m.edits],
        manual=[(e.line, e.col, e.note) for e in m.manual],
    )
