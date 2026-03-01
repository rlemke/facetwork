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

"""Lark Transformer to convert parse tree to AFL AST."""

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
    TypeRef,
    UnaryExpr,
    UsesDecl,
    WhenBlock,
    WhenCase,
    WorkflowDecl,
    YieldStmt,
)

_TAG_RE = re.compile(r"^@(param|return)\s+(\w+)\s+(.*)")


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


class AFLTransformer(Transformer):
    """Transform Lark parse tree to AFL AST."""

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
        # Remove quotes and process escapes
        s = str(token)[1:-1]
        return s.encode().decode("unicode_escape")

    def INTEGER(self, token: Token) -> int:
        return int(token)

    def FLOAT(self, token: Token) -> float:
        return float(token)

    def BOOLEAN(self, token: Token) -> bool:
        return str(token) == "true"

    def NULL(self, token: Token) -> None:
        return None

    def INPUT_REF(self, token: Token) -> list[str]:
        # $.field.subfield -> ["field", "subfield"]
        return str(token)[2:].split(".")

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
        if isinstance(item, list):
            # INPUT_REF already parsed to list
            return Reference(path=item, is_input=True, location=self._loc(meta))
        else:
            # step_ref
            return item

    @v_args(meta=True)
    def step_ref(self, meta, items: list) -> Reference:
        path = [str(item) for item in items]
        return Reference(path=path, is_input=False, location=self._loc(meta))

    # Expressions
    def expr(self, items: list):
        return items[0]

    @v_args(meta=True)
    def or_expr(self, meta, items: list):
        # items alternates: expr, expr, expr, ...
        if len(items) == 1:
            return items[0]
        # Left-associative binary tree
        result = items[0]
        for i in range(1, len(items)):
            result = BinaryExpr(
                operator="||", left=result, right=items[i], location=self._loc(meta)
            )
        return result

    @v_args(meta=True)
    def and_expr(self, meta, items: list):
        # items alternates: expr, expr, expr, ...
        if len(items) == 1:
            return items[0]
        # Left-associative binary tree
        result = items[0]
        for i in range(1, len(items)):
            result = BinaryExpr(
                operator="&&", left=result, right=items[i], location=self._loc(meta)
            )
        return result

    @v_args(meta=True)
    def comparison_expr(self, meta, items: list):
        # items = [left] or [left, COMP_OP, right]
        if len(items) == 1:
            return items[0]
        left = items[0]
        op = str(items[1])
        right = items[2]
        return BinaryExpr(operator=op, left=left, right=right, location=self._loc(meta))

    def COMP_OP(self, token: Token) -> str:
        return str(token)

    @v_args(meta=True)
    def not_expr(self, meta, items: list):
        # items = [operand] (the "!" is consumed by the grammar)
        return UnaryExpr(operator="!", operand=items[0], location=self._loc(meta))

    @v_args(meta=True)
    def concat_expr(self, meta, items: list):
        # If there's only one operand, return it directly
        if len(items) == 1:
            return items[0]
        # Otherwise create a ConcatExpr with all operands
        return ConcatExpr(operands=list(items), location=self._loc(meta))

    @v_args(meta=True)
    def additive_expr(self, meta, items: list):
        # items alternates: expr, op, expr, op, expr, ...
        if len(items) == 1:
            return items[0]
        # Left-associative binary tree
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = BinaryExpr(operator=op, left=result, right=right, location=self._loc(meta))
            i += 2
        return result

    @v_args(meta=True)
    def multiplicative_expr(self, meta, items: list):
        # items alternates: expr, op, expr, op, expr, ...
        if len(items) == 1:
            return items[0]
        # Left-associative binary tree
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = BinaryExpr(operator=op, left=result, right=right, location=self._loc(meta))
            i += 2
        return result

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
        args = items[1] if len(items) > 1 else []
        return MixinSig(name=name, args=args, location=self._loc(meta))

    @v_args(meta=True)
    def mixin_call(self, meta, items: list) -> MixinCall:
        name = items[0]
        args = []
        alias = None
        for item in items[1:]:
            if isinstance(item, list):
                args = item
            elif isinstance(item, str):
                alias = item
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
        body = self._find_one(items[2:], AndThenBlock)
        catch = self._find_one(items[2:], CatchClause)
        return StepStmt(name=name, call=call, body=body, catch=catch, location=self._loc(meta))

    @v_args(meta=True)
    def step_body(self, meta, items: list) -> AndThenBlock:
        foreach = self._find_one(items, ForeachClause)
        block = self._find_one(items, Block)
        return AndThenBlock(block=block, foreach=foreach, location=self._loc(meta))

    @v_args(meta=True, inline=True)
    def yield_stmt(self, meta, call: CallExpr) -> YieldStmt:
        return YieldStmt(call=call, location=self._loc(meta))

    # Blocks
    @v_args(meta=True)
    def block_body(self, meta, items: list) -> tuple[list[StepStmt], list[YieldStmt]]:
        return (self._find_all(items, StepStmt), self._find_all(items, YieldStmt))

    @v_args(meta=True)
    def block(self, meta, items: list) -> Block:
        if items and isinstance(items[0], tuple):
            steps, yield_stmts = items[0]
        else:
            # Flatten items
            steps = []
            yield_stmts = []
            for item in items:
                if isinstance(item, StepStmt):
                    steps.append(item)
                elif isinstance(item, YieldStmt):
                    yield_stmts.append(item)
                elif isinstance(item, tuple):
                    steps.extend(item[0])
                    yield_stmts.extend(item[1])
        return Block(steps=steps, yield_stmts=yield_stmts, location=self._loc(meta))

    @v_args(meta=True)
    def foreach_clause(self, meta, items: list) -> ForeachClause:
        var = items[0]
        ref = items[1]
        return ForeachClause(variable=var, iterable=ref, location=self._loc(meta))

    @v_args(meta=True)
    def andthen_clause(self, meta, items: list) -> AndThenBlock:
        """Handle regular andThen block clause."""
        foreach = self._find_one(items, ForeachClause)
        block = self._find_one(items, Block)
        return AndThenBlock(block=block, foreach=foreach, location=self._loc(meta))

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

    # Prompt block handling
    @v_args(meta=True)
    def prompt_block(self, meta, items: list) -> PromptBlock:
        """Convert prompt_block rule to PromptBlock AST node."""
        system = None
        template = None
        model = None
        # Flatten items - prompt_body returns a list, so items may be nested
        directives = items[0] if items and isinstance(items[0], list) else items
        for item in directives:
            if isinstance(item, tuple):
                key, value = item
                if key == "system":
                    system = value
                elif key == "template":
                    template = value
                elif key == "model":
                    model = value
        return PromptBlock(system=system, template=template, model=model, location=self._loc(meta))

    def prompt_body(self, items: list) -> list:
        """Collect prompt directives."""
        return list(items)

    @v_args(meta=True, inline=True)
    def prompt_system(self, meta, value: str) -> tuple[str, str]:
        """Handle system directive."""
        return ("system", value)

    @v_args(meta=True, inline=True)
    def prompt_template(self, meta, value: str) -> tuple[str, str]:
        """Handle template directive."""
        return ("template", value)

    @v_args(meta=True, inline=True)
    def prompt_model(self, meta, value: str) -> tuple[str, str]:
        """Handle model directive."""
        return ("model", value)

    # Script block handling
    @v_args(meta=True, inline=True)
    def script_block(self, meta, code: str) -> ScriptBlock:
        """Convert script_block rule (bare string) to ScriptBlock AST node."""
        return ScriptBlock(language="python", code=code, location=self._loc(meta))

    @v_args(meta=True, inline=True)
    def script_python(self, meta, code: str) -> ScriptBlock:
        """Handle explicit 'python' script directive."""
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
        params = []
        returns = None
        mixins = []
        for item in items[1:]:
            if isinstance(item, list) and item and isinstance(item[0], Parameter):
                params = item
            elif isinstance(item, ReturnClause):
                returns = item
            elif isinstance(item, MixinSig):
                mixins.append(item)
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
