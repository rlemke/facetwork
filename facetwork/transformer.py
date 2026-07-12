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

"""Lark Transformer to convert parse tree to FFL AST."""

import re
from typing import TypeVar

from lark import Token, Transformer, v_args

_T = TypeVar("_T")

from .ast import (
    AndThenBlock,
    ArrayLiteral,
    ArrayType,
    BinaryExpr,
    Block,
    CallExpr,
    CatchClause,
    ConcatExpr,
    DocComment,
    DocParam,
    EventFacetDecl,
    FacetDecl,
    FacetSig,
    ForeachClause,
    ImplicitDecl,
    IndexExpr,
    Literal,
    MapEntry,
    MapLiteral,
    MixinCall,
    MixinSig,
    NamedArg,
    Namespace,
    Parameter,
    Program,
    PromptBlock,
    Reference,
    ReturnClause,
    SchemaDecl,
    SchemaField,
    ScriptBlock,
    SourceLocation,
    StepStmt,
    SysAssertStmt,
    SysLogStmt,
    TypeRef,
    UnaryExpr,
    UsesDecl,
    WhenBlock,
    WhenCase,
    WorkflowDecl,
    YieldStmt,
)

_TAG_RE = re.compile(r"^@(param|returns?)\s+(\w+)\s+(.*)")


def _clean_doc_comment(raw: str) -> DocComment:
    """Strip /** */ delimiters, leading *, and parse @param/@return tags."""
    # Remove trailing whitespace/newlines consumed by the regex
    raw = raw.rstrip()
    # Remove /** and */
    text = raw[3:-2]
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("*"):
            stripped = stripped[1:]
            if stripped.startswith(" "):
                stripped = stripped[1:]
        cleaned.append(stripped)

    # Split into description lines vs @param/@return tags
    desc_lines: list[str] = []
    params: list[DocParam] = []
    returns: list[DocParam] = []
    for line in cleaned:
        m = _TAG_RE.match(line)
        if m:
            tag, name, desc = m.group(1), m.group(2), m.group(3).strip()
            if tag == "param":
                params.append(DocParam(name=name, description=desc))
            else:
                returns.append(DocParam(name=name, description=desc))
        else:
            desc_lines.append(line)

    description = "\n".join(desc_lines).strip()
    return DocComment(description=description, params=params, returns=returns)


def _extract_doc_comment(items: list) -> DocComment | None:
    """Extract and remove optional DOC_COMMENT from the beginning of items."""
    if items and isinstance(items[0], Token) and items[0].type == "DOC_COMMENT":
        return _clean_doc_comment(str(items.pop(0)))
    return None


def _get_location(meta, source_id: str | None = None) -> SourceLocation | None:
    """Extract source location from Lark meta."""
    if meta and hasattr(meta, "line"):
        return SourceLocation(
            line=meta.line,
            column=meta.column,
            end_line=getattr(meta, "end_line", None),
            end_column=getattr(meta, "end_column", None),
            source_id=source_id,
        )
    return None


class FFLTransformer(Transformer):
    """Transform Lark parse tree to FFL AST."""

    def __init__(self, source_id: str | None = None):
        super().__init__()
        self._source_id = source_id

    def _loc(self, meta) -> SourceLocation | None:
        """Helper to get location with source_id."""
        return _get_location(meta, self._source_id)

    # --- Item extraction helpers ---

    @staticmethod
    def _find_one(items: list, cls: type[_T]) -> _T | None:
        """Find first item of given type, or None."""
        return next((item for item in items if isinstance(item, cls)), None)

    @staticmethod
    def _find_all(items: list, cls: type[_T]) -> list[_T]:
        """Find all items of given type."""
        return [item for item in items if isinstance(item, cls)]

    @staticmethod
    def _find_rest(items: list, *exclude: type) -> list:
        """Find items NOT matching any of the given types."""
        return [item for item in items if not isinstance(item, tuple(exclude))]

    # --- AST construction helpers ---

    def _andthen_from_items(self, meta, items: list) -> AndThenBlock:
        """Build an AndThenBlock from a Block and optional ForeachClause."""
        return AndThenBlock(
            block=self._find_one(items, Block),
            foreach=self._find_one(items, ForeachClause),
            location=self._loc(meta),
        )

    # --- Declaration helpers ---

    _DECL_TYPE_MAP: dict[type, str] = {
        Namespace: "namespaces",
        UsesDecl: "uses",
        FacetDecl: "facets",
        EventFacetDecl: "event_facets",
        WorkflowDecl: "workflows",
        ImplicitDecl: "implicits",
        SchemaDecl: "schemas",
    }

    @classmethod
    def _segregate_declarations(cls, items: list) -> dict[str, list]:
        """Sort items into typed declaration lists."""
        result: dict[str, list] = {key: [] for key in cls._DECL_TYPE_MAP.values()}
        for item in items:
            for typ, key in cls._DECL_TYPE_MAP.items():
                if isinstance(item, typ):
                    result[key].append(item)
                    break
        return result

    def _build_declaration(self, meta, items: list) -> dict:
        """Extract common declaration fields: doc, sig, pre_script, body, catch."""
        doc = _extract_doc_comment(items)
        sig = items[0]
        catch = self._find_one(items[1:], CatchClause)
        rest = self._find_rest(items[1:], CatchClause)
        tail = rest[0] if rest else None
        pre_script, body = None, None
        if isinstance(tail, tuple):
            pre_script, body = tail
        elif isinstance(tail, (PromptBlock, AndThenBlock, list)):
            body = tail
        return {
            "sig": sig,
            "pre_script": pre_script,
            "body": body,
            "catch": catch,
            "doc": doc,
            "location": self._loc(meta),
        }

    # Terminals
    def IDENT(self, token: Token) -> str:
        return str(token)

    def QNAME(self, token: Token) -> str:
        return str(token)

    def TYPE_BUILTIN(self, token: Token) -> str:
        return str(token)

    def STRING(self, token: Token) -> str:
        # Remove quotes and process backslash escapes (\n, \t, \", \\, \uXXXX).
        #
        # The `unicode_escape` codec is latin-1-based, so a naive
        # `s.encode().decode("unicode_escape")` first UTF-8-encodes non-ASCII
        # characters and then decodes those bytes as latin-1 — mangling every
        # non-ASCII literal (em-dash "—" -> "â\x80\x94", "café" -> "cafÃ©",
        # emoji/CJK -> mojibake). Round-trip through latin-1 with
        # `backslashreplace` instead: characters outside latin-1 become their
        # own `\uXXXX`/`\UXXXXXXXX` escapes, which `unicode_escape` then decodes
        # back to the original character, while genuine ASCII escapes still
        # process normally.
        s = str(token)[1:-1]
        return s.encode("latin-1", "backslashreplace").decode("unicode_escape")

    def INTEGER(self, token: Token) -> int:
        return int(token)

    def FLOAT(self, token: Token) -> float:
        return float(token)

    def BOOLEAN(self, token: Token) -> bool:
        return str(token) == "true"

    def NULL(self, token: Token) -> None:
        return None

    def CATCH_KW(self, token: Token) -> str:
        return str(token)

    def INPUT_REF(self, token: Token) -> tuple[int, list[str]]:
        # $.field.subfield     -> (0, ["field", "subfield"])
        # $$.field             -> (1, ["field"])   (up one container)
        # $$$.field            -> (2, ["field"])
        s = str(token)
        n_dollars = len(s) - len(s.lstrip("$"))
        path = s[n_dollars + 1 :].split(".")  # skip the dollars and the "."
        return (n_dollars - 1, path)

    # Types
    @v_args(inline=True)
    def type(self, value) -> "TypeRef | ArrayType":
        if isinstance(value, ArrayType):
            return value
        return TypeRef(name=value)

    @v_args(meta=True)
    def array_type(self, meta, items: list) -> ArrayType:
        return ArrayType(element_type=items[0], location=self._loc(meta))

    # Parameters
    def param(self, items: list) -> Parameter:
        name = items[0]
        type_ref = items[1]
        default = items[2] if len(items) > 2 else None
        return Parameter(name=str(name), type=type_ref, default=default)

    def params(self, items: list) -> list[Parameter]:
        return list(items)

    # Literals
    @v_args(meta=True)
    def literal(self, meta, items: list) -> Literal:
        value = items[0]
        # bool must be checked before int — bool is a subclass of int in Python
        if isinstance(value, str):
            kind = "string"
        elif isinstance(value, bool):
            kind = "boolean"
        elif isinstance(value, float):
            kind = "double"
        elif isinstance(value, int):
            kind = "integer"
        elif value is None:
            kind = "null"
        else:
            kind = "unknown"
        return Literal(value=value, kind=kind, location=self._loc(meta))

    # References
    @v_args(meta=True)
    def reference(self, meta, items: list) -> Reference:
        item = items[0]
        if isinstance(item, tuple):
            # INPUT_REF parsed to (up_levels, path)
            up_levels, path = item
            return Reference(
                path=path,
                is_input=True,
                up_levels=up_levels,
                location=self._loc(meta),
            )
        else:
            # step_ref
            return item

    @v_args(meta=True)
    def step_ref(self, meta, items: list) -> Reference:
        path = [str(item) for item in items]
        return Reference(path=path, is_input=False, location=self._loc(meta))

    # --- Left-associative expression helpers ---

    def _left_assoc_fixed_op(self, meta, items: list, operator: str):
        """Build left-associative BinaryExpr chain with a fixed operator.

        Used for or_expr (||) and and_expr (&&) where the grammar strips
        the operator token and items contains only operands.
        """
        if len(items) == 1:
            return items[0]
        result = items[0]
        for i in range(1, len(items)):
            result = BinaryExpr(
                operator=operator, left=result, right=items[i], location=self._loc(meta)
            )
        return result

    def _left_assoc_interleaved(self, meta, items: list):
        """Build left-associative BinaryExpr chain from [expr, op, expr, ...].

        Used for additive_expr and multiplicative_expr where operators are
        interleaved with operands.
        """
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            result = BinaryExpr(
                operator=op, left=result, right=items[i + 1], location=self._loc(meta)
            )
            i += 2
        return result

    # Expressions
    def expr(self, items: list):
        return items[0]

    @v_args(meta=True)
    def or_expr(self, meta, items: list):
        return self._left_assoc_fixed_op(meta, items, "||")

    @v_args(meta=True)
    def and_expr(self, meta, items: list):
        return self._left_assoc_fixed_op(meta, items, "&&")

    @v_args(meta=True)
    def comparison_expr(self, meta, items: list):
        if len(items) == 1:
            return items[0]
        op = items[1]
        # `op` is either a COMP_OP token (basic comparison) or the
        # string produced by one of the compare_op_* alias rules below.
        op_str = op if isinstance(op, str) else str(op)
        return BinaryExpr(operator=op_str, left=items[0], right=items[2], location=self._loc(meta))

    def COMP_OP(self, token: Token) -> str:
        return str(token)

    # compare_op alias rules — each returns the canonical operator
    # spelling so ``comparison_expr`` can build a uniform BinaryExpr.
    def compare_op_basic(self, items: list) -> str:
        return str(items[0])

    def compare_op_in(self, _items: list) -> str:
        return "in"

    def compare_op_not_in(self, _items: list) -> str:
        return "not in"

    def compare_op_contains(self, _items: list) -> str:
        return "contains"

    def compare_op_starts_with(self, _items: list) -> str:
        return "startsWith"

    def compare_op_ends_with(self, _items: list) -> str:
        return "endsWith"

    @v_args(meta=True)
    def not_expr(self, meta, items: list):
        return UnaryExpr(operator="!", operand=items[0], location=self._loc(meta))

    @v_args(meta=True)
    def concat_expr(self, meta, items: list):
        if len(items) == 1:
            return items[0]
        return ConcatExpr(operands=list(items), location=self._loc(meta))

    @v_args(meta=True)
    def additive_expr(self, meta, items: list):
        return self._left_assoc_interleaved(meta, items)

    @v_args(meta=True)
    def multiplicative_expr(self, meta, items: list):
        return self._left_assoc_interleaved(meta, items)

    @v_args(meta=True)
    def unary_expr(self, meta, items: list):
        if len(items) == 1:
            return items[0]  # no operator, pass through
        # items = [operator_str, operand]
        op = str(items[0])
        if op == "+":
            return items[1]  # unary + is a no-op
        return UnaryExpr(operator=op, operand=items[1], location=self._loc(meta))

    def ADD_OP(self, token: Token) -> str:
        return str(token)

    def MUL_OP(self, token: Token) -> str:
        return str(token)

    @v_args(meta=True)
    def postfix_expr(self, meta, items: list):
        # First item is the base expression, subsequent items are index expressions
        if len(items) == 1:
            return items[0]
        # Build left-associative IndexExpr chain
        result = items[0]
        for index_expr in items[1:]:
            result = IndexExpr(target=result, index=index_expr, location=self._loc(meta))
        return result

    def atom_expr(self, items: list):
        return items[0]

    # Collection literals
    @v_args(meta=True)
    def array_literal(self, meta, items: list) -> ArrayLiteral:
        return ArrayLiteral(elements=list(items), location=self._loc(meta))

    @v_args(meta=True)
    def map_entry(self, meta, items: list) -> MapEntry:
        key = items[0]
        value = items[1]
        return MapEntry(key=str(key), value=value, location=self._loc(meta))

    @v_args(meta=True)
    def map_literal(self, meta, items: list) -> MapLiteral:
        entries = self._find_all(items, MapEntry)
        return MapLiteral(entries=entries, location=self._loc(meta))

    # Named arguments
    @v_args(meta=True, inline=True)
    def named_arg(self, meta, name: str, value) -> NamedArg:
        return NamedArg(name=name, value=value, location=self._loc(meta))

    def named_args(self, items: list) -> list[NamedArg]:
        return list(items)

    # Mixins
    @v_args(meta=True)
    def mixin_sig(self, meta, items: list) -> MixinSig:
        name = items[0]
        # Remaining items may include the named_args list and/or the
        # optional `as <alias>` IDENT — mirror mixin_call's by-type lookup
        # so order independence holds even if the grammar grows.
        args = self._find_one(items[1:], list) or []
        alias = self._find_one(items[1:], str)
        return MixinSig(name=name, args=args, alias=alias, location=self._loc(meta))

    @v_args(meta=True)
    def mixin_call(self, meta, items: list) -> MixinCall:
        name = items[0]
        args = self._find_one(items[1:], list) or []
        alias = self._find_one(items[1:], str)
        return MixinCall(name=name, args=args, alias=alias, location=self._loc(meta))

    # Call expressions
    @v_args(meta=True)
    def call_expr(self, meta, items: list) -> CallExpr:
        name = items[0]
        args = self._find_one(items[1:], list) or []
        mixins = self._find_all(items[1:], MixinCall)
        return CallExpr(name=name, args=args, mixins=mixins, location=self._loc(meta))

    # Statements
    @v_args(meta=True)
    def step_stmt(self, meta, items: list) -> StepStmt:
        name = items[0]
        call = items[1]
        # A step may chain multiple andThen clauses (step_body*): the first is
        # `body`, the rest are `extra_bodies` (co-clauses sharing $ = the step).
        bodies = self._find_all(items[2:], AndThenBlock)
        body = bodies[0] if bodies else None
        extra_bodies = bodies[1:]
        catch = self._find_one(items[2:], CatchClause)
        return StepStmt(
            name=name,
            call=call,
            body=body,
            catch=catch,
            extra_bodies=extra_bodies,
            location=self._loc(meta),
        )

    @v_args(meta=True)
    def step_body(self, meta, items: list) -> AndThenBlock:
        return self._andthen_from_items(meta, items)

    @v_args(meta=True, inline=True)
    def yield_stmt(self, meta, call: CallExpr) -> list[YieldStmt]:
        """A yield statement may target one or more facets/mixins, e.g.
        ``yield F(a = 1) with M(b = 2)``. The grammar uses the same
        ``call_expr`` shape that statement-level calls accept, but in a
        yield context a ``with`` clause introduces an **additional
        yield target**, not a call-site mixin on the primary call.

        We split the parsed call_expr into:
          - one YieldStmt for the primary call (mixins stripped), and
          - one YieldStmt per mixin_call (each with empty mixins).

        ``block_body`` flattens the returned list into the block's
        yield list.
        """
        loc = self._loc(meta)
        primary = YieldStmt(
            call=CallExpr(name=call.name, args=call.args, mixins=[], location=call.location),
            location=loc,
        )
        extras = [
            YieldStmt(
                call=CallExpr(
                    name=m.name,
                    args=m.args,
                    mixins=[],
                    location=m.location,
                ),
                location=loc,
            )
            for m in call.mixins
        ]
        return [primary, *extras]

    # sys.log / sys.assert — inline diagnostic statements
    @v_args(meta=True)
    def sys_log_stmt(self, meta, items: list) -> SysLogStmt:
        """``sys.log(name = expr, ...)`` — Splunk-format diagnostic log."""
        args = items[0] if items and isinstance(items[0], list) else []
        return SysLogStmt(args=list(args), location=self._loc(meta))

    @v_args(meta=True, inline=True)
    def sys_assert_stmt(self, meta, condition) -> SysAssertStmt:
        """``sys.assert(condition)`` — runtime invariant check."""
        return SysAssertStmt(condition=condition, location=self._loc(meta))

    @v_args(inline=True)
    def block_stmt(self, item):
        """``block_stmt`` wraps a step_stmt / sys_log_stmt / sys_assert_stmt
        so the block_body rule can interleave them.  Pass-through."""
        return item

    # Blocks
    @v_args(meta=True)
    def block_body(self, meta, items: list) -> tuple[list, list[YieldStmt]]:
        # ``items`` is a flat sequence of StepStmt / SysLogStmt /
        # SysAssertStmt (in source order) followed by YieldStmt instances
        # (which may also be wrapped in lists from yield-with-chain
        # transforms).  We preserve source order across statement types
        # so the runtime evaluator can interleave step assignments with
        # inline diagnostics; yield statements stay as their own list
        # (semantically separate, captured at parent STATEMENT_CAPTURE).
        steps: list = []
        yields: list[YieldStmt] = []
        for item in items:
            if isinstance(item, (StepStmt, SysLogStmt, SysAssertStmt)):
                steps.append(item)
            elif isinstance(item, YieldStmt):
                yields.append(item)
            elif isinstance(item, list):
                yields.extend(y for y in item if isinstance(y, YieldStmt))
        return (steps, yields)

    @v_args(meta=True)
    def block(self, meta, items: list) -> Block:
        if items and isinstance(items[0], tuple):
            steps, yield_stmts = items[0]
        else:
            # Defensive fallback — block_body should always produce a tuple,
            # but Lark's untyped items lists make this a reasonable guard.
            steps = self._find_all(items, StepStmt)
            yield_stmts = self._find_all(items, YieldStmt)
        return Block(steps=steps, yield_stmts=yield_stmts, location=self._loc(meta))

    @v_args(meta=True)
    def foreach_clause(self, meta, items: list) -> ForeachClause:
        var = items[0]
        ref = items[1]
        return ForeachClause(variable=var, iterable=ref, location=self._loc(meta))

    @v_args(meta=True)
    def andthen_clause(self, meta, items: list) -> AndThenBlock:
        """Handle regular andThen block clause."""
        return self._andthen_from_items(meta, items)

    @v_args(meta=True)
    def andthen_script(self, meta, items: list) -> AndThenBlock:
        """Handle andThen script variant."""
        script = items[0]  # ScriptBlock from script_block rule
        return AndThenBlock(script=script, location=self._loc(meta))

    @v_args(meta=True)
    def andthen_when(self, meta, items: list) -> AndThenBlock:
        """Handle andThen when variant."""
        when_blk = items[0]  # WhenBlock from when_block rule
        return AndThenBlock(when=when_blk, location=self._loc(meta))

    @v_args(meta=True)
    def step_body_when(self, meta, items: list) -> AndThenBlock:
        """Handle statement-level andThen when."""
        when_blk = items[0]
        return AndThenBlock(when=when_blk, location=self._loc(meta))

    @v_args(meta=True)
    def when_block(self, meta, items: list) -> WhenBlock:
        """Convert when_block rule to WhenBlock AST node."""
        cases = self._find_all(items, WhenCase)
        return WhenBlock(cases=cases, location=self._loc(meta))

    @v_args(meta=True)
    def when_case_expr(self, meta, items: list) -> WhenCase:
        """Convert when_case_expr rule to WhenCase AST node."""
        condition = items[0]
        block = items[1]
        return WhenCase(condition=condition, block=block, location=self._loc(meta))

    @v_args(meta=True)
    def when_case_default(self, meta, items: list) -> WhenCase:
        """Convert when_case_default rule to WhenCase AST node."""
        block = items[0]
        return WhenCase(condition=None, block=block, is_default=True, location=self._loc(meta))

    def when_condition(self, items: list):
        """Pass through when condition expression."""
        return items[0]

    @v_args(meta=True)
    def catch_simple(self, meta, items: list) -> CatchClause:
        """Handle simple catch block: catch { steps }."""
        block = self._find_one(items, Block)
        return CatchClause(block=block, location=self._loc(meta))

    @v_args(meta=True)
    def catch_when(self, meta, items: list) -> CatchClause:
        """Handle conditional catch block: catch when { case ... }."""
        when_blk = self._find_one(items, WhenBlock)
        return CatchClause(when=when_blk, location=self._loc(meta))

    @v_args(meta=True)
    def facet_def_tail(self, meta, items: list):
        prompt = self._find_one(items, PromptBlock)
        if prompt is not None:
            return prompt

        pre_script = self._find_one(items, ScriptBlock)
        blocks = self._find_all(items, AndThenBlock)

        body: AndThenBlock | list[AndThenBlock] | None = None
        if len(blocks) == 1:
            body = blocks[0]
        elif blocks:
            body = blocks

        if pre_script is not None:
            return (pre_script, body)  # tuple signals both fields
        return body  # None, single AndThenBlock, or list

    @v_args(meta=True)
    def facet_def_tail_prompt(self, meta, items: list):
        """Soft keyword: the introducer IDENT must equal `prompt`.

        Using IDENT here (rather than a hard `"prompt"` keyword) keeps
        `prompt` usable as a parameter name / schema field elsewhere
        in the language.
        """
        introducer: str | None = None
        for it in items:
            if isinstance(it, Token) and it.type == "IDENT":
                introducer = str(it)
                break
            if isinstance(it, str) and not isinstance(it, PromptBlock):
                introducer = it
                break
        if introducer is not None and introducer != "prompt":
            from .parser import ParseError

            raise ParseError(
                f"Expected `prompt` to introduce a prompt block, got `{introducer}`",
                line=meta.line,
                column=meta.column,
            )
        return self._find_one(items, PromptBlock)

    # Prompt block handling
    @v_args(meta=True)
    def prompt_block(self, meta, items: list) -> PromptBlock:
        """Convert prompt_block rule to PromptBlock AST node."""
        directives = items[0] if items and isinstance(items[0], list) else items
        fields = {k: v for item in directives if isinstance(item, tuple) for k, v in [item]}
        return PromptBlock(
            system=fields.get("system"),
            template=fields.get("template"),
            model=fields.get("model"),
            location=self._loc(meta),
        )

    def prompt_body(self, items: list) -> list:
        """Collect prompt directives."""
        return list(items)

    @v_args(meta=True, inline=True)
    def prompt_directive(self, meta, key: object, value: str) -> tuple[str, str]:
        """Soft keyword: directive name must be system / template / model.

        Using IDENT here (rather than three named keyword terminals)
        keeps `system`, `template`, `model` usable as ordinary
        identifiers outside prompt-block scope. The semantic check
        lives here instead.
        """
        name = str(key)
        if name not in ("system", "template", "model"):
            from .parser import ParseError

            raise ParseError(
                f"Unknown prompt directive `{name}` (expected one of: system, template, model)",
                line=meta.line,
                column=meta.column,
            )
        return (name, value)

    # Script block handling
    @v_args(meta=True, inline=True)
    def script_block(self, meta, code: str) -> ScriptBlock:
        """Convert script_block rule (bare string) to ScriptBlock AST node."""
        return ScriptBlock(language="python", code=code, location=self._loc(meta))

    @v_args(meta=True, inline=True)
    def script_dialect(self, meta, dialect: object, code: str) -> ScriptBlock:
        """Soft keyword: dialect IDENT must be `python` (currently).

        Modeled as IDENT instead of a hard `"python"` keyword so the
        word remains usable as a parameter name elsewhere.
        """
        name = str(dialect)
        if name != "python":
            from .parser import ParseError

            raise ParseError(
                f"Unknown script dialect `{name}` (expected `python`)",
                line=meta.line,
                column=meta.column,
            )
        return ScriptBlock(language="python", code=code, location=self._loc(meta))

    # Return clause
    @v_args(meta=True)
    def return_clause(self, meta, items: list) -> ReturnClause:
        params = items[0] if items else []
        return ReturnClause(params=params, location=self._loc(meta))

    # Facet signature
    @v_args(meta=True)
    def facet_sig(self, meta, items: list) -> FacetSig:
        name = items[0]
        rest = items[1:]
        params_list = self._find_one(rest, list)
        params = (
            params_list
            if params_list and params_list and isinstance(params_list[0], Parameter)
            else []
        )
        returns = self._find_one(rest, ReturnClause)
        mixins = self._find_all(rest, MixinSig)
        return FacetSig(
            name=name, params=params, returns=returns, mixins=mixins, location=self._loc(meta)
        )

    # Declarations
    @v_args(meta=True)
    def facet_decl(self, meta, items: list) -> FacetDecl:
        return FacetDecl(**self._build_declaration(meta, items))

    @v_args(meta=True)
    def event_facet_decl(self, meta, items: list) -> EventFacetDecl:
        return EventFacetDecl(**self._build_declaration(meta, items))

    @v_args(meta=True)
    def workflow_decl(self, meta, items: list) -> WorkflowDecl:
        return WorkflowDecl(**self._build_declaration(meta, items))

    @v_args(meta=True, inline=True)
    def implicit_decl(self, meta, name: str, call: CallExpr) -> ImplicitDecl:
        return ImplicitDecl(name=name, call=call, location=self._loc(meta))

    @v_args(meta=True, inline=True)
    def uses_decl(self, meta, name: str) -> UsesDecl:
        return UsesDecl(name=name, location=self._loc(meta))

    # Schema declarations
    @v_args(meta=True, inline=True)
    def schema_field(self, meta, name: str, type_node) -> SchemaField:
        return SchemaField(name=name, type=type_node, location=self._loc(meta))

    def schema_fields(self, items: list) -> list[SchemaField]:
        return list(items)

    @v_args(meta=True)
    def schema_decl(self, meta, items: list) -> SchemaDecl:
        doc = _extract_doc_comment(items)
        name = items[0]
        fields = items[1] if len(items) > 1 else []
        return SchemaDecl(name=name, fields=fields, doc=doc, location=self._loc(meta))

    # Namespace
    @v_args(meta=True)
    def namespace_body(self, meta, items: list) -> dict:
        return self._segregate_declarations(items)

    @v_args(meta=True)
    def namespace_block(self, meta, items: list) -> Namespace:
        doc = _extract_doc_comment(items)
        name = items[0]
        body = items[1] if len(items) > 1 else {}
        return Namespace(
            name=name,
            uses=body.get("uses", []),
            facets=body.get("facets", []),
            event_facets=body.get("event_facets", []),
            workflows=body.get("workflows", []),
            implicits=body.get("implicits", []),
            schemas=body.get("schemas", []),
            doc=doc,
            location=self._loc(meta),
        )

    # Top-level
    def top_level_decl(self, items: list):
        return items[0]

    # Program (start)
    @v_args(meta=True)
    def start(self, meta, items: list) -> Program:
        seg = self._segregate_declarations(items)
        return Program(
            namespaces=seg["namespaces"],
            facets=seg["facets"],
            event_facets=seg["event_facets"],
            workflows=seg["workflows"],
            implicits=seg["implicits"],
            schemas=seg["schemas"],
            location=self._loc(meta),
        )
