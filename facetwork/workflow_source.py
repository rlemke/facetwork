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

"""Reconstruct focused FFL source for a single workflow from a compiled program.

A compiled FFL program (``emit_dict`` output / ``flow.compiled_ast``) is
self-contained: the dependency resolver inlines every transitively-referenced
namespace into one flat ``declarations`` tree. When a UI shows "the source" of
one workflow, dumping the whole compiled file is unhelpful — a namespace may
hold many unrelated workflows and the file may carry a dozen pulled-in
dependency namespaces.

:func:`reconstruct_workflow_source` slices the compiled program down to a single
workflow plus *only* the facets, event facets, workflows and schemas it
transitively references (following calls, mixins and parameter/return types,
across namespaces), then renders that slice back to FFL — as if it had been
written by hand. Declarations that the workflow does not touch are omitted.

The reference closure mirrors the compiler's name-resolution order
(current namespace → ``use`` imports → top level), so cross-namespace
references resolve exactly as they did at compile time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast_utils import normalize_program_ast

__all__ = ["reconstruct_workflow_source", "WorkflowNotFoundError"]

# Builtin types are never user declarations — they have no source to inline.
_BUILTIN_TYPES = frozenset({"String", "Long", "Int", "Double", "Boolean", "Json"})

_DECL_TYPES = frozenset(
    {"FacetDecl", "EventFacetDecl", "WorkflowDecl", "SchemaDecl", "ImplicitDecl"}
)


class WorkflowNotFoundError(ValueError):
    """Raised when the requested workflow is not present in the program."""


@dataclass
class _Entry:
    """One declaration plus the namespace context needed to resolve its refs."""

    qname: str  # fully-qualified name, e.g. "a.b.MyWorkflow"
    namespace: str  # owning namespace ("" for a top-level declaration)
    name: str  # short name, e.g. "MyWorkflow"
    kind: str  # the JSON "type" discriminator
    decl: dict
    uses: list[str] = field(default_factory=list)  # the namespace's `use` imports


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def reconstruct_workflow_source(program: dict, workflow_name: str) -> str:
    """Reconstruct FFL source for one workflow and its referenced declarations.

    Args:
        program: A compiled FFL program dict (``emit_dict`` output or a stored
            ``compiled_ast``). Either the declarations-only or the legacy
            categorized shape is accepted — it is normalized internally.
        workflow_name: The workflow to extract. May be fully qualified
            (``"ns.sub.MyWorkflow"``) or a simple name (``"MyWorkflow"``) when
            unambiguous.

    Returns:
        FFL source text containing only the named workflow and the
        declarations it transitively references, grouped by namespace.

    Raises:
        WorkflowNotFoundError: If no matching workflow is found, or a simple
            name matches more than one workflow.
    """
    program = normalize_program_ast(program)
    index = _build_index(program)

    target = _find_target(index, workflow_name)
    included = _closure(index, target)

    return _render(program, index, included)


# ---------------------------------------------------------------------------
# Index construction
# ---------------------------------------------------------------------------


def _decl_name(decl: dict) -> str:
    return decl.get("name", "")


def _build_index(program: dict) -> dict[str, _Entry]:
    """Map every declaration's qualified name to its :class:`_Entry`."""
    index: dict[str, _Entry] = {}
    _index_into(program, "", [], index)
    return index


def _index_into(
    container: dict, prefix: str, inherited_uses: list[str], index: dict[str, _Entry]
) -> None:
    for decl in container.get("declarations", []):
        kind = decl.get("type")
        if kind == "Namespace":
            ns = decl.get("name", "")
            full_ns = f"{prefix}.{ns}" if prefix else ns
            uses = list(decl.get("uses", []))
            _index_into(decl, full_ns, uses, index)
        elif kind in _DECL_TYPES:
            name = _decl_name(decl)
            qname = f"{prefix}.{name}" if prefix else name
            index[qname] = _Entry(
                qname=qname,
                namespace=prefix,
                name=name,
                kind=kind,
                decl=decl,
                uses=inherited_uses,
            )


# ---------------------------------------------------------------------------
# Target lookup
# ---------------------------------------------------------------------------


def _find_target(index: dict[str, _Entry], workflow_name: str) -> _Entry:
    # Exact qualified match wins.
    entry = index.get(workflow_name)
    if entry is not None and entry.kind == "WorkflowDecl":
        return entry

    # Otherwise match by short name among workflows.
    matches = [
        e
        for e in index.values()
        if e.kind == "WorkflowDecl" and e.name == workflow_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(sorted(e.qname for e in matches))
        raise WorkflowNotFoundError(
            f"Ambiguous workflow name {workflow_name!r}; candidates: {names}"
        )
    raise WorkflowNotFoundError(f"Workflow {workflow_name!r} not found")


# ---------------------------------------------------------------------------
# Reference resolution (mirrors the compiler's lookup order)
# ---------------------------------------------------------------------------


def _resolve(index: dict[str, _Entry], name: str, from_ns: str, uses: list[str]) -> str | None:
    """Resolve a name (as written) to a declaration's qualified name, or None.

    Lookup order matches the validator: a qualified name is taken as-is;
    a bare name is resolved against the current namespace, then `use` imports,
    then the top level.
    """
    if "." in name:
        return name if name in index else None

    # 1. Current namespace.
    if from_ns:
        candidate = f"{from_ns}.{name}"
        if candidate in index:
            return candidate

    # 2. Imported namespaces.
    for imported in uses:
        candidate = f"{imported}.{name}"
        if candidate in index:
            return candidate

    # 3. Top level.
    if name in index and index[name].namespace == "":
        return name

    return None


# ---------------------------------------------------------------------------
# Transitive closure
# ---------------------------------------------------------------------------


def _closure(index: dict[str, _Entry], target: _Entry) -> set[str]:
    """Return the set of qualified names reachable from *target*."""
    included: set[str] = set()
    worklist = [target.qname]

    while worklist:
        qname = worklist.pop()
        if qname in included:
            continue
        included.add(qname)
        entry = index[qname]

        for ref in _referenced_names(entry.decl):
            resolved = _resolve(index, ref, entry.namespace, entry.uses)
            if resolved is not None and resolved not in included:
                worklist.append(resolved)

    return included


def _referenced_names(decl: dict) -> set[str]:
    """All declaration names a single declaration references (calls + types)."""
    names: set[str] = set()
    _collect_call_targets(decl, names)
    _collect_type_names(decl, names)
    return names


def _collect_call_targets(node: object, acc: set[str]) -> None:
    """Walk the node tree collecting CallExpr / mixin targets."""
    if isinstance(node, dict):
        kind = node.get("type")
        if kind in ("CallExpr", "MixinSig", "MixinCall"):
            target = node.get("target")
            if isinstance(target, str):
                acc.add(target)
        for value in node.values():
            _collect_call_targets(value, acc)
    elif isinstance(node, list):
        for item in node:
            _collect_call_targets(item, acc)


def _collect_type_names(decl: dict, acc: set[str]) -> None:
    """Collect user-defined type names from params, returns and schema fields."""
    for param in decl.get("params", []):
        _add_type_names(param.get("type"), acc)
    for ret in decl.get("returns", []):
        _add_type_names(ret.get("type"), acc)
    for field_ in decl.get("fields", []):  # SchemaDecl
        _add_type_names(field_.get("type"), acc)


def _add_type_names(type_node: object, acc: set[str]) -> None:
    if isinstance(type_node, str):
        if type_node not in _BUILTIN_TYPES:
            acc.add(type_node)
    elif isinstance(type_node, dict) and type_node.get("type") == "ArrayType":
        _add_type_names(type_node.get("elementType"), acc)


# ---------------------------------------------------------------------------
# Rendering (JSON -> FFL)
# ---------------------------------------------------------------------------

_INDENT = "    "


def _render(program: dict, index: dict[str, _Entry], included: set[str]) -> str:
    included_namespaces = {index[q].namespace for q in included}
    writer = _Writer()

    first = True
    for decl in program.get("declarations", []):
        kind = decl.get("type")
        if kind == "Namespace":
            ns = decl.get("name", "")
            kept = [
                d
                for d in decl.get("declarations", [])
                if _qname_for(ns, d) in included
            ]
            if not kept:
                continue
            if not first:
                writer.blank()
            first = False
            uses = [u for u in decl.get("uses", []) if u in included_namespaces]
            writer.line(f"namespace {ns} {{")
            for u in uses:
                writer.line(f"{_INDENT}use {u}")
            if uses:
                writer.blank()
            for i, d in enumerate(kept):
                if i:
                    writer.blank()
                _render_decl(writer, d, 1)
            writer.line("}")
        elif kind in _DECL_TYPES:
            qname = decl.get("name", "")
            if qname not in included:
                continue
            if not first:
                writer.blank()
            first = False
            _render_decl(writer, decl, 0)

    return writer.text()


def _qname_for(ns: str, decl: dict) -> str:
    name = decl.get("name", "")
    return f"{ns}.{name}" if ns else name


class _Writer:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def line(self, text: str) -> None:
        self._lines.append(text)

    def append(self, text: str) -> None:
        """Append to the last emitted line (keeps a clause on the prior line)."""
        if self._lines:
            self._lines[-1] += text
        else:
            self._lines.append(text)

    def blank(self) -> None:
        if self._lines and self._lines[-1] != "":
            self._lines.append("")

    def text(self) -> str:
        return "\n".join(self._lines).rstrip() + "\n"


# -- declarations -----------------------------------------------------------


def _render_decl(w: _Writer, decl: dict, depth: int) -> None:
    _render_doc(w, decl.get("doc"), depth)
    kind = decl.get("type")
    if kind == "SchemaDecl":
        _render_schema(w, decl, depth)
    elif kind == "ImplicitDecl":
        _render_implicit(w, decl, depth)
    elif kind in ("FacetDecl", "EventFacetDecl", "WorkflowDecl"):
        _render_facet_like(w, decl, depth)


def _render_doc(w: _Writer, doc: dict | None, depth: int) -> None:
    """Render a structured doc comment back to a ``/** ... */`` block."""
    if not doc:
        return
    ind = _INDENT * depth
    w.line(f"{ind}/**")
    for line in str(doc.get("description", "")).splitlines() or [""]:
        w.line(f"{ind} * {line}".rstrip())
    for p in doc.get("params", []):
        w.line(f"{ind} * @param {p['name']} {p.get('description', '')}".rstrip())
    for r in doc.get("returns", []):
        w.line(f"{ind} * @return {r['name']} {r.get('description', '')}".rstrip())
    w.line(f"{ind} */")


def _keyword(kind: str) -> str:
    return {
        "FacetDecl": "facet",
        "EventFacetDecl": "event facet",
        "WorkflowDecl": "workflow",
    }[kind]


def _render_facet_like(w: _Writer, decl: dict, depth: int) -> None:
    ind = _INDENT * depth
    header = f"{ind}{_keyword(decl['type'])} {_signature(decl)}"
    w.line(header)

    tail_depth = depth  # tail clauses sit at the declaration's own indent

    if decl.get("pre_script"):
        _render_script(w, decl["pre_script"], tail_depth, keyword="script")

    # ``body`` is one AndThenBlock, a list of them, or (event facets) a
    # PromptBlock. _render_andthen dispatches PromptBlock / script / when itself.
    body = decl.get("body")
    if body is not None:
        blocks = body if isinstance(body, list) else [body]
        for block in blocks:
            _render_andthen(w, block, tail_depth, keyword="andThen")

    if decl.get("catch"):
        _render_catch(w, decl["catch"], tail_depth)


def _signature(decl: dict) -> str:
    name = decl.get("name", "")
    params = _params(decl.get("params", []))
    out = f"{name}({params})"
    if decl.get("returns"):
        out += f" => ({_params(decl['returns'])})"
    for mixin in decl.get("mixins", []):
        out += " " + _mixin(mixin)
    return out


def _params(params: list[dict]) -> str:
    parts = []
    for p in params:
        piece = f"{p['name']}: {_type(p.get('type'))}"
        if "default" in p and p["default"] is not None:
            piece += f" = {_expr(p['default'])}"
        parts.append(piece)
    return ", ".join(parts)


def _type(type_node: object) -> str:
    if isinstance(type_node, str):
        return type_node
    if isinstance(type_node, dict) and type_node.get("type") == "ArrayType":
        return f"[{_type(type_node.get('elementType'))}]"
    return "Json"


def _mixin(mixin: dict) -> str:
    out = f"with {mixin['target']}({_args(mixin.get('args', []))})"
    if mixin.get("alias"):
        out += f" as {mixin['alias']}"
    return out


def _render_schema(w: _Writer, decl: dict, depth: int) -> None:
    ind = _INDENT * depth
    fields = decl.get("fields", [])
    if not fields:
        w.line(f"{ind}schema {decl['name']} {{}}")
        return
    w.line(f"{ind}schema {decl['name']} {{")
    inner = _INDENT * (depth + 1)
    for i, f in enumerate(fields):
        comma = "," if i < len(fields) - 1 else ""
        w.line(f"{inner}{f['name']}: {_type(f.get('type'))}{comma}")
    w.line(f"{ind}}}")


def _render_implicit(w: _Writer, decl: dict, depth: int) -> None:
    ind = _INDENT * depth
    w.line(f"{ind}implicit {decl['name']} = {_call(decl['call'])}")


# -- blocks -----------------------------------------------------------------


def _open(w: _Writer, depth: int, text: str, attach: bool) -> None:
    """Emit a clause's opening line, either fresh or attached to the prior line.

    Statement-level clauses (a step's ``andThen``/``catch``) must continue on the
    step's own line — the grammar does not accept a newline before them. Declaration
    -level tails (after a signature) may stand on their own line, which reads better.
    """
    if attach:
        w.append(f" {text}")
    else:
        w.line(f"{_INDENT * depth}{text}")


def _render_andthen(
    w: _Writer, block: dict, depth: int, keyword: str, attach: bool = False
) -> None:
    """Render an AndThenBlock (also used for step bodies and prompt blocks)."""
    ind = _INDENT * depth

    if block.get("type") == "PromptBlock":
        _render_prompt(w, block, depth, keyword="prompt", attach=attach)
        return

    if block.get("script"):
        _render_script(w, block["script"], depth, keyword=f"{keyword} script", attach=attach)
        return

    header = keyword
    if block.get("foreach"):
        fe = block["foreach"]
        header += f" foreach {fe['variable']} in {_expr(fe['iterable'])}"
    if block.get("when"):
        _open(w, depth, f"{header} when {{", attach)
        _render_when_cases(w, block["when"], depth + 1)
        w.line(f"{ind}}}")
        return

    _open(w, depth, f"{header} {{", attach)
    _render_block_body(w, block, depth + 1)
    w.line(f"{ind}}}")


def _render_block_body(w: _Writer, block: dict, depth: int) -> None:
    for stmt in block.get("steps", []):
        _render_stmt(w, stmt, depth)
    for y in _yields(block):
        w.line(f"{_INDENT * depth}yield {_call(y['call'])}")


def _yields(block: dict) -> list[dict]:
    if block.get("yields"):
        return block["yields"]
    if block.get("yield"):
        return [block["yield"]]
    return []


def _render_stmt(w: _Writer, stmt: dict, depth: int) -> None:
    ind = _INDENT * depth
    kind = stmt.get("type")
    if kind == "SysLogStmt":
        w.line(f"{ind}sys.log({_args(stmt.get('args', []))})")
        return
    if kind == "SysAssertStmt":
        w.line(f"{ind}sys.assert({_expr(stmt['condition'])})")
        return

    # StepStmt — any andThen/catch must stay on the step's own line.
    w.line(f"{ind}{stmt['name']} = {_call(stmt['call'])}")
    if stmt.get("body"):
        _render_andthen(w, stmt["body"], depth, keyword="andThen", attach=True)
    if stmt.get("catch"):
        _render_catch(w, stmt["catch"], depth, attach=True)


def _render_when_cases(w: _Writer, when_block: dict, depth: int) -> None:
    ind = _INDENT * depth
    for case in when_block.get("cases", []):
        if case.get("default"):
            w.line(f"{ind}case _ => {{")
        else:
            w.line(f"{ind}case {_expr(case['condition'])} => {{")
        _render_block_body(w, case, depth + 1)
        w.line(f"{ind}}}")


def _render_catch(w: _Writer, catch: dict, depth: int, attach: bool = False) -> None:
    ind = _INDENT * depth
    if catch.get("when"):
        _open(w, depth, "catch when {", attach)
        _render_when_cases(w, catch["when"], depth + 1)
        w.line(f"{ind}}}")
        return
    _open(w, depth, "catch {", attach)
    _render_block_body(w, catch, depth + 1)
    w.line(f"{ind}}}")


def _render_script(
    w: _Writer, script: dict, depth: int, keyword: str, attach: bool = False
) -> None:
    ind = _INDENT * depth
    language = script.get("language", "python")
    inner = _INDENT * (depth + 1)
    code_lines = script.get("code", "").splitlines()
    # Drop trailing blank lines — the dedent of the brace form would otherwise
    # reintroduce them inconsistently. They carry no meaning in Python.
    while code_lines and not code_lines[-1].strip():
        code_lines.pop()
    _open(w, depth, f"{keyword} {language} {{", attach)
    for code_line in code_lines:
        w.line(f"{inner}{code_line}" if code_line.strip() else "")
    w.line(f"{ind}}}")


def _render_prompt(
    w: _Writer, prompt: dict, depth: int, keyword: str, attach: bool = False
) -> None:
    ind = _INDENT * depth
    inner = _INDENT * (depth + 1)
    _open(w, depth, f"{keyword} {{", attach)
    for directive in ("system", "template", "model"):
        if prompt.get(directive) is not None:
            w.line(f"{inner}{directive} {_string(prompt[directive])}")
    w.line(f"{ind}}}")


# -- calls & expressions ----------------------------------------------------


def _call(call: dict) -> str:
    out = f"{call['target']}({_args(call.get('args', []))})"
    for mixin in call.get("mixins", []):
        out += " " + _mixin(mixin)
    return out


def _args(args: list[dict]) -> str:
    return ", ".join(f"{a['name']} = {_expr(a['value'])}" for a in args)


# Operator precedence (FFL grammar, lowest binds loosest). Used to decide
# where parentheses are needed so the rendered text reparses to the same tree.
_CONCAT_PREC = 4
_ATOM_PREC = 99
_BINOP_PREC = {
    "||": 1,
    "&&": 2,
    "==": 3, "!=": 3, ">": 3, "<": 3, ">=": 3, "<=": 3,
    "in": 3, "not in": 3, "contains": 3, "startsWith": 3, "endsWith": 3,
    "+": 5, "-": 5,
    "*": 6, "/": 6, "%": 6,
}


def _prec(node: object) -> int:
    if not isinstance(node, dict):
        return _ATOM_PREC
    kind = node.get("type")
    if kind == "BinaryExpr":
        return _BINOP_PREC.get(node.get("operator"), 3)
    if kind == "ConcatExpr":
        return _CONCAT_PREC
    if kind == "UnaryExpr":
        return 7
    return _ATOM_PREC


def _operand(node: object, parent_prec: int, force_parens: bool = False) -> str:
    """Render a sub-expression, parenthesizing it when precedence requires."""
    text = _expr(node)
    if _prec(node) < parent_prec or (force_parens and _prec(node) == parent_prec):
        return f"({text})"
    return text


def _expr(node: object) -> str:
    if not isinstance(node, dict):
        return str(node)

    kind = node.get("type")

    if kind == "String":
        return _string(node.get("value", ""))
    if kind == "Int":
        return str(node.get("value"))
    if kind == "Double":
        return _double(node.get("value"))
    if kind == "Boolean":
        return "true" if node.get("value") else "false"
    if kind == "Null":
        return "null"
    if kind == "InputRef":
        return "$." + ".".join(node.get("path", []))
    if kind == "StepRef":
        return ".".join(node.get("path", []))
    if kind == "ConcatExpr":
        return " ++ ".join(
            _operand(o, _CONCAT_PREC) for o in node.get("operands", [])
        )
    if kind == "BinaryExpr":
        prec = _BINOP_PREC.get(node.get("operator"), 3)
        left = _operand(node["left"], prec)
        # Right operand of a left-associative op needs parens at equal precedence.
        right = _operand(node["right"], prec, force_parens=True)
        return f"{left} {node['operator']} {right}"
    if kind == "UnaryExpr":
        return f"{node['operator']}{_operand(node['operand'], 7, force_parens=True)}"
    if kind == "ArrayLiteral":
        return "[" + ", ".join(_expr(e) for e in node.get("elements", [])) + "]"
    if kind == "MapLiteral":
        entries = ", ".join(
            f"{_string(e['key'])}: {_expr(e['value'])}" for e in node.get("entries", [])
        )
        return "#{" + entries + "}"
    if kind == "IndexExpr":
        return f"{_operand(node['target'], _ATOM_PREC)}[{_expr(node['index'])}]"

    return "null"


def _double(value: object) -> str:
    text = repr(value)
    if isinstance(value, float) and "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text


def _string(value: str) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'
