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

"""AFL AST node definitions using dataclasses."""

import uuid
from dataclasses import dataclass, field


def _generate_uuid() -> str:
    """Generate a unique UUID for an AST node."""
    return str(uuid.uuid4())


@dataclass
class SourceLocation:
    """Source code location for error reporting with provenance tracking."""

    line: int
    column: int
    end_line: int | None = None
    end_column: int | None = None
    source_id: str | None = None  # Reference to SourceRegistry for provenance


@dataclass
class ASTNode:
    """Base class for all AST nodes."""

    node_id: str = field(default_factory=_generate_uuid, compare=False, repr=False, kw_only=True)
    location: SourceLocation | None = field(default=None, compare=False, repr=False, kw_only=True)


# Types
@dataclass
class TypeRef(ASTNode):
    """Type reference (builtin or qualified name)."""

    name: str


@dataclass
class ArrayType(ASTNode):
    """Array type: [ElementType]."""

    element_type: "TypeRef | ArrayType"


# Parameters
@dataclass
class Parameter(ASTNode):
    """Parameter declaration: name: Type = default"""

    name: str
    type: "TypeRef | ArrayType"
    default: "Literal | Reference | ConcatExpr | BinaryExpr | None" = None


# Expressions
@dataclass
class Literal(ASTNode):
    """Literal value (string, int, bool, null)."""

    value: object
    kind: str  # "string", "integer", "boolean", "null"


@dataclass
class Reference(ASTNode):
    """Reference to input ($.field) or step output (step.field)."""

    path: list[str]
    is_input: bool  # True for $.field, False for step.field


@dataclass
class ConcatExpr(ASTNode):
    """Concatenation expression: expr ++ expr ++ ..."""

    operands: list["Literal | Reference | ConcatExpr | BinaryExpr"]


@dataclass
class BinaryExpr(ASTNode):
    """Binary expression: expr op expr"""

    operator: str  # "+", "-", "*", "/", "%", "==", "!=", ">", "<", ">=", "<=", "&&", "||"
    left: "Literal | Reference | ConcatExpr | BinaryExpr"
    right: "Literal | Reference | ConcatExpr | BinaryExpr"


@dataclass
class UnaryExpr(ASTNode):
    """Unary expression: -expr or !expr"""

    operator: str  # "-", "!"
    operand: "Literal | Reference | BinaryExpr | UnaryExpr | ConcatExpr"


@dataclass
class ArrayLiteral(ASTNode):
    """Array literal: [elem1, elem2, ...]"""

    elements: list


@dataclass
class MapEntry(ASTNode):
    """Map entry: key: value"""

    key: str
    value: object  # expression node


@dataclass
class MapLiteral(ASTNode):
    """Map literal: #{key1: val1, key2: val2, ...}"""

    entries: list  # list[MapEntry]


@dataclass
class IndexExpr(ASTNode):
    """Index expression: target[index]"""

    target: object  # expression node
    index: object  # expression node


@dataclass
class NamedArg(ASTNode):
    """Named argument: name = expr"""

    name: str
    value: "Literal | Reference | ConcatExpr | BinaryExpr | ArrayLiteral | MapLiteral | IndexExpr"


# Mixins
@dataclass
class MixinSig(ASTNode):
    """Mixin signature in facet declaration: with Name(args)"""

    name: str
    args: list[NamedArg] = field(default_factory=list)


@dataclass
class MixinCall(ASTNode):
    """Mixin call in expression: with Name(args) as alias"""

    name: str
    args: list[NamedArg] = field(default_factory=list)
    alias: str | None = None


# Call expressions
@dataclass
class CallExpr(ASTNode):
    """Call expression: Name(args) with mixins"""

    name: str
    args: list[NamedArg] = field(default_factory=list)
    mixins: list[MixinCall] = field(default_factory=list)


# Statements
@dataclass
class StepStmt(ASTNode):
    """Step statement: name = CallExpr [andThen block] [catch block]"""

    name: str
    call: CallExpr
    body: "AndThenBlock | None" = None
    catch: "CatchClause | None" = None


@dataclass
class YieldStmt(ASTNode):
    """Yield statement: yield CallExpr"""

    call: CallExpr


# Blocks
@dataclass
class ForeachClause(ASTNode):
    """Foreach clause: foreach var in reference"""

    variable: str
    iterable: Reference


@dataclass
class Block(ASTNode):
    """Block: { steps... yields* }"""

    steps: list[StepStmt] = field(default_factory=list)
    yield_stmts: list[YieldStmt] = field(default_factory=list)

    # Backwards compatibility property
    @property
    def yield_stmt(self) -> YieldStmt | None:
        """Return first yield statement for backwards compatibility."""
        return self.yield_stmts[0] if self.yield_stmts else None


@dataclass
class WhenCase(ASTNode):
    """A case in a when block."""

    condition: (
        "Literal | Reference | BinaryExpr | UnaryExpr | ConcatExpr | None"  # None = default (_)
    )
    block: Block
    is_default: bool = False


@dataclass
class WhenBlock(ASTNode):
    """When block: when { case condition => { ... } ... }"""

    cases: list[WhenCase]


@dataclass
class CatchClause(ASTNode):
    """Catch clause: catch { steps } or catch when { case ... }"""

    block: Block | None = None  # Simple: catch { steps }
    when: WhenBlock | None = None  # Conditional: catch when { case ... }


@dataclass
class AndThenBlock(ASTNode):
    """andThen block with optional foreach/when.

    Has EITHER block (regular andThen), script, or when, not multiple.
    """

    block: Block | None = None
    foreach: ForeachClause | None = None
    script: "ScriptBlock | None" = None
    when: WhenBlock | None = None


@dataclass
class PromptBlock(ASTNode):
    """Prompt block for LLM-based event facets.

    Contains directives for system prompt, template string, and model selection.
    Templates can include {param_name} placeholders that reference facet parameters.
    """

    system: str | None = None
    template: str | None = None
    model: str | None = None


@dataclass
class ScriptBlock(ASTNode):
    """Script block for inline code execution.

    Contains code that will be executed by a sandboxed interpreter.
    The code has access to `params` dict and should set values in `result` dict.

    Attributes:
        language: Programming language (currently only "python")
        code: The script source code
    """

    language: str = "python"
    code: str = ""


# Return clause
@dataclass
class ReturnClause(ASTNode):
    """Return clause: => (params)"""

    params: list[Parameter] = field(default_factory=list)


# Facet signature
@dataclass
class FacetSig(ASTNode):
    """Facet signature: Name(params) => (returns) with mixins"""

    name: str
    params: list[Parameter] = field(default_factory=list)
    returns: ReturnClause | None = None
    mixins: list[MixinSig] = field(default_factory=list)


# Doc comments
@dataclass
class DocParam:
    """Documented parameter or return value from a doc comment tag."""

    name: str
    description: str


@dataclass
class DocComment:
    """Structured doc comment with description and @param/@return tags."""

    description: str
    params: list[DocParam] = field(default_factory=list)
    returns: list[DocParam] = field(default_factory=list)


# Declarations
@dataclass
class FacetDecl(ASTNode):
    """Facet declaration."""

    sig: FacetSig
    pre_script: ScriptBlock | None = None
    body: "list[AndThenBlock] | AndThenBlock | None" = None
    catch: "CatchClause | None" = field(default=None, kw_only=True)
    doc: "DocComment | None" = field(default=None, kw_only=True)


@dataclass
class EventFacetDecl(ASTNode):
    """Event facet declaration."""

    sig: FacetSig
    pre_script: ScriptBlock | None = None
    body: "list[AndThenBlock] | AndThenBlock | PromptBlock | None" = None
    catch: "CatchClause | None" = field(default=None, kw_only=True)
    doc: "DocComment | None" = field(default=None, kw_only=True)


@dataclass
class WorkflowDecl(ASTNode):
    """Workflow declaration."""

    sig: FacetSig
    pre_script: ScriptBlock | None = None
    body: "list[AndThenBlock] | AndThenBlock | None" = None
    catch: "CatchClause | None" = field(default=None, kw_only=True)
    doc: "DocComment | None" = field(default=None, kw_only=True)


@dataclass
class ImplicitDecl(ASTNode):
    """Implicit declaration: implicit name = CallExpr"""

    name: str
    call: CallExpr


@dataclass
class UsesDecl(ASTNode):
    """Uses declaration: uses qualified.name"""

    name: str


# Schema declarations
@dataclass
class SchemaField(ASTNode):
    """Schema field: name: Type"""

    name: str
    type: "TypeRef | ArrayType"


@dataclass
class SchemaDecl(ASTNode):
    """Schema declaration: schema Name { fields }"""

    name: str
    fields: list[SchemaField] = field(default_factory=list)
    doc: "DocComment | None" = field(default=None, kw_only=True)


# Namespace
@dataclass
class Namespace(ASTNode):
    """Namespace block."""

    name: str
    uses: list[UsesDecl] = field(default_factory=list)
    facets: list[FacetDecl] = field(default_factory=list)
    event_facets: list[EventFacetDecl] = field(default_factory=list)
    workflows: list[WorkflowDecl] = field(default_factory=list)
    implicits: list[ImplicitDecl] = field(default_factory=list)
    schemas: list[SchemaDecl] = field(default_factory=list)
    doc: "DocComment | None" = field(default=None, kw_only=True)


# Program (root)
@dataclass
class Program(ASTNode):
    """Root AST node representing an FFL program."""

    namespaces: list[Namespace] = field(default_factory=list)
    facets: list[FacetDecl] = field(default_factory=list)
    event_facets: list[EventFacetDecl] = field(default_factory=list)
    workflows: list[WorkflowDecl] = field(default_factory=list)
    implicits: list[ImplicitDecl] = field(default_factory=list)
    schemas: list[SchemaDecl] = field(default_factory=list)

    @classmethod
    def merge(cls, programs: list["Program"]) -> "Program":
        """Merge multiple Program ASTs into one.

        Args:
            programs: List of Program ASTs to merge

        Returns:
            Single merged Program AST
        """
        merged = cls()
        for prog in programs:
            merged.namespaces.extend(prog.namespaces)
            merged.facets.extend(prog.facets)
            merged.event_facets.extend(prog.event_facets)
            merged.workflows.extend(prog.workflows)
            merged.implicits.extend(prog.implicits)
            merged.schemas.extend(prog.schemas)
        return merged
