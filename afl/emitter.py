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

"""AFL AST to JSON emitter."""

import json
from typing import Any

from .ast import (
    AndThenBlock,
    ArrayLiteral,
    ArrayType,
    ASTNode,
    BinaryExpr,
    Block,
    CallExpr,
    ConcatExpr,
    DocComment,
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
from .source import (
    FileOrigin,
    MavenOrigin,
    MongoDBOrigin,
    SourceOrigin,
    SourceRegistry,
)


class JSONEmitter:
    """Converts AFL AST to JSON representation."""

    def __init__(
        self,
        include_locations: bool = True,
        include_provenance: bool = False,
        source_registry: SourceRegistry | None = None,
        indent: int | None = 2,
    ):
        """Initialize emitter.

        Args:
            include_locations: Include source locations in output
            include_provenance: Include source provenance in locations
            source_registry: Registry for looking up provenance data
            indent: JSON indentation (None for compact)
        """
        self.include_locations = include_locations
        self.include_provenance = include_provenance
        self.source_registry = source_registry
        self.indent = indent

    def emit(self, node: ASTNode) -> str:
        """Convert AST node to JSON string.

        Args:
            node: AST node to convert

        Returns:
            JSON string representation
        """
        data = self._convert(node)
        return json.dumps(data, indent=self.indent)

    def emit_dict(self, node: ASTNode) -> dict[str, Any]:
        """Convert AST node to dictionary.

        Args:
            node: AST node to convert

        Returns:
            Dictionary representation
        """
        return self._convert(node)

    def _convert(self, node: Any) -> Any:
        """Convert a node to its JSON-serializable form."""
        if node is None:
            return None

        if isinstance(node, (str, int, float, bool)):
            return node

        if isinstance(node, list):
            return [self._convert(item) for item in node]

        if isinstance(node, Program):
            return self._program(node)
        if isinstance(node, Namespace):
            return self._namespace(node)
        if isinstance(node, UsesDecl):
            return self._uses_decl(node)
        if isinstance(node, FacetDecl):
            return self._facet_decl(node)
        if isinstance(node, EventFacetDecl):
            return self._event_facet_decl(node)
        if isinstance(node, WorkflowDecl):
            return self._workflow_decl(node)
        if isinstance(node, ImplicitDecl):
            return self._implicit_decl(node)
        if isinstance(node, FacetSig):
            return self._facet_sig(node)
        if isinstance(node, ReturnClause):
            return self._return_clause(node)
        if isinstance(node, Parameter):
            return self._parameter(node)
        if isinstance(node, TypeRef):
            return self._type_ref(node)
        if isinstance(node, MixinSig):
            return self._mixin_sig(node)
        if isinstance(node, MixinCall):
            return self._mixin_call(node)
        if isinstance(node, CallExpr):
            return self._call_expr(node)
        if isinstance(node, NamedArg):
            return self._named_arg(node)
        if isinstance(node, StepStmt):
            return self._step_stmt(node)
        if isinstance(node, YieldStmt):
            return self._yield_stmt(node)
        if isinstance(node, Block):
            return self._block(node)
        if isinstance(node, AndThenBlock):
            return self._and_then_block(node)
        if isinstance(node, ForeachClause):
            return self._foreach_clause(node)
        if isinstance(node, Literal):
            return self._literal(node)
        if isinstance(node, Reference):
            return self._reference(node)
        if isinstance(node, ConcatExpr):
            return self._concat_expr(node)
        if isinstance(node, BinaryExpr):
            return self._binary_expr(node)
        if isinstance(node, UnaryExpr):
            return self._unary_expr(node)
        if isinstance(node, ArrayLiteral):
            return self._array_literal(node)
        if isinstance(node, MapLiteral):
            return self._map_literal(node)
        if isinstance(node, MapEntry):
            return self._map_entry(node)
        if isinstance(node, IndexExpr):
            return self._index_expr(node)
        if isinstance(node, SourceLocation):
            return self._location(node)
        if isinstance(node, SchemaDecl):
            return self._schema_decl(node)
        if isinstance(node, SchemaField):
            return self._schema_field(node)
        if isinstance(node, ArrayType):
            return self._array_type(node)
        if isinstance(node, PromptBlock):
            return self._prompt_block(node)
        if isinstance(node, ScriptBlock):
            return self._script_block(node)
        if isinstance(node, WhenBlock):
            return self._when_block(node)
        if isinstance(node, WhenCase):
            return self._when_case(node)

        raise ValueError(f"Unknown node type: {type(node)}")

    def _add_metadata(self, data: dict, node: ASTNode) -> dict:
        """Add node ID and location info to data dict."""
        data["id"] = node.node_id
        if self.include_locations and node.location:
            data["location"] = self._location(node.location)
        return data

    def _location(self, loc: SourceLocation) -> dict[str, Any]:
        """Convert source location with optional provenance."""
        result: dict[str, Any] = {"line": loc.line, "column": loc.column}
        if loc.end_line is not None:
            result["endLine"] = loc.end_line
        if loc.end_column is not None:
            result["endColumn"] = loc.end_column

        # Add provenance if enabled and available
        if self.include_provenance and loc.source_id:
            result["sourceId"] = loc.source_id
            if self.source_registry:
                origin = self.source_registry.get(loc.source_id)
                if origin:
                    result["provenance"] = self._provenance_to_dict(origin)

        return result

    def _provenance_to_dict(self, origin: SourceOrigin) -> dict:
        """Convert source provenance to JSON."""
        if isinstance(origin, FileOrigin):
            return {"type": "file", "path": origin.path}
        elif isinstance(origin, MongoDBOrigin):
            return {
                "type": "mongodb",
                "collectionId": origin.collection_id,
                "displayName": origin.display_name,
            }
        elif isinstance(origin, MavenOrigin):
            result = {
                "type": "maven",
                "groupId": origin.group_id,
                "artifactId": origin.artifact_id,
                "version": origin.version,
            }
            if origin.classifier:
                result["classifier"] = origin.classifier
            return result
        else:
            return {"type": "unknown"}

    def _program(self, node: Program) -> dict[str, Any]:
        """Convert Program node."""
        data: dict[str, Any] = {"type": "Program"}

        declarations: list = []
        if node.namespaces:
            declarations.extend(self._convert(node.namespaces))
        if node.facets:
            declarations.extend(self._convert(node.facets))
        if node.event_facets:
            declarations.extend(self._convert(node.event_facets))
        if node.workflows:
            declarations.extend(self._convert(node.workflows))
        if node.implicits:
            declarations.extend(self._convert(node.implicits))
        if node.schemas:
            declarations.extend(self._convert(node.schemas))
        if declarations:
            data["declarations"] = declarations

        return self._add_metadata(data, node)

    def _doc_comment(self, doc: DocComment) -> dict:
        """Convert DocComment to structured dict."""
        return {
            "description": doc.description,
            "params": [{"name": p.name, "description": p.description} for p in doc.params],
            "returns": [{"name": r.name, "description": r.description} for r in doc.returns],
        }

    def _namespace(self, node: Namespace) -> dict[str, Any]:
        """Convert Namespace node."""
        data: dict[str, Any] = {
            "type": "Namespace",
            "name": node.name,
        }
        if node.doc is not None:
            data["doc"] = self._doc_comment(node.doc)

        if node.uses:
            data["uses"] = [u.name for u in node.uses]

        declarations: list = []
        if node.facets:
            declarations.extend(self._convert(node.facets))
        if node.event_facets:
            declarations.extend(self._convert(node.event_facets))
        if node.workflows:
            declarations.extend(self._convert(node.workflows))
        if node.implicits:
            declarations.extend(self._convert(node.implicits))
        if node.schemas:
            declarations.extend(self._convert(node.schemas))
        if declarations:
            data["declarations"] = declarations

        return self._add_metadata(data, node)

    def _uses_decl(self, node: UsesDecl) -> dict:
        """Convert UsesDecl node."""
        data = {
            "type": "UsesDecl",
            "name": node.name,
        }
        return self._add_metadata(data, node)

    def _emit_body(self, body) -> Any:
        """Emit a body field, handling single vs multiple andThen blocks."""
        if isinstance(body, list):
            if len(body) == 1:
                return self._convert(body[0])
            return self._convert(body)
        return self._convert(body)

    def _facet_decl(self, node: FacetDecl) -> dict[str, Any]:
        """Convert FacetDecl node."""
        data: dict[str, Any] = {
            "type": "FacetDecl",
            "name": node.sig.name,
        }
        if node.doc is not None:
            data["doc"] = self._doc_comment(node.doc)

        if node.sig.params:
            data["params"] = self._convert(node.sig.params)
        if node.sig.returns:
            data["returns"] = self._convert(node.sig.returns.params)
        if node.sig.mixins:
            data["mixins"] = self._convert(node.sig.mixins)
        if node.pre_script:
            data["pre_script"] = self._convert(node.pre_script)
        if node.body:
            data["body"] = self._emit_body(node.body)

        return self._add_metadata(data, node)

    def _event_facet_decl(self, node: EventFacetDecl) -> dict[str, Any]:
        """Convert EventFacetDecl node."""
        data: dict[str, Any] = {
            "type": "EventFacetDecl",
            "name": node.sig.name,
        }
        if node.doc is not None:
            data["doc"] = self._doc_comment(node.doc)

        if node.sig.params:
            data["params"] = self._convert(node.sig.params)
        if node.sig.returns:
            data["returns"] = self._convert(node.sig.returns.params)
        if node.sig.mixins:
            data["mixins"] = self._convert(node.sig.mixins)
        if node.pre_script:
            data["pre_script"] = self._convert(node.pre_script)
        if node.body:
            data["body"] = self._emit_body(node.body)

        return self._add_metadata(data, node)

    def _workflow_decl(self, node: WorkflowDecl) -> dict[str, Any]:
        """Convert WorkflowDecl node."""
        data: dict[str, Any] = {
            "type": "WorkflowDecl",
            "name": node.sig.name,
        }
        if node.doc is not None:
            data["doc"] = self._doc_comment(node.doc)

        if node.sig.params:
            data["params"] = self._convert(node.sig.params)
        if node.sig.returns:
            data["returns"] = self._convert(node.sig.returns.params)
        if node.sig.mixins:
            data["mixins"] = self._convert(node.sig.mixins)
        if node.pre_script:
            data["pre_script"] = self._convert(node.pre_script)
        if node.body:
            data["body"] = self._emit_body(node.body)

        return self._add_metadata(data, node)

    def _implicit_decl(self, node: ImplicitDecl) -> dict:
        """Convert ImplicitDecl node."""
        data = {
            "type": "ImplicitDecl",
            "name": node.name,
            "call": self._convert(node.call),
        }
        return self._add_metadata(data, node)

    def _facet_sig(self, node: FacetSig) -> dict:
        """Convert FacetSig node."""
        data = {
            "type": "FacetSig",
            "name": node.name,
        }

        if node.params:
            data["params"] = self._convert(node.params)
        if node.returns:
            data["returns"] = self._convert(node.returns.params)
        if node.mixins:
            data["mixins"] = self._convert(node.mixins)

        return self._add_metadata(data, node)

    def _return_clause(self, node: ReturnClause) -> dict:
        """Convert ReturnClause node."""
        return self._convert(node.params)

    def _parameter(self, node: Parameter) -> dict:
        """Convert Parameter node."""
        data = {
            "name": node.name,
            "type": self._convert(node.type),
        }
        if node.default is not None:
            data["default"] = self._convert(node.default)
        return data

    def _type_ref(self, node: TypeRef) -> str:
        """Convert TypeRef node."""
        return node.name

    def _mixin_sig(self, node: MixinSig) -> dict:
        """Convert MixinSig node."""
        data = {
            "type": "MixinSig",
            "target": node.name,
        }

        if node.args:
            data["args"] = self._convert(node.args)

        return data

    def _mixin_call(self, node: MixinCall) -> dict:
        """Convert MixinCall node."""
        data = {
            "type": "MixinCall",
            "target": node.name,
        }

        if node.args:
            data["args"] = self._convert(node.args)
        if node.alias:
            data["alias"] = node.alias

        return data

    def _call_expr(self, node: CallExpr) -> dict:
        """Convert CallExpr node."""
        data = {
            "type": "CallExpr",
            "target": node.name,
        }

        if node.args:
            data["args"] = self._convert(node.args)
        if node.mixins:
            data["mixins"] = self._convert(node.mixins)

        return self._add_metadata(data, node)

    def _named_arg(self, node: NamedArg) -> dict:
        """Convert NamedArg node."""
        return {
            "name": node.name,
            "value": self._convert(node.value),
        }

    def _step_stmt(self, node: StepStmt) -> dict:
        """Convert StepStmt node."""
        data = {
            "type": "StepStmt",
            "name": node.name,
            "call": self._convert(node.call),
        }
        if node.body:
            data["body"] = self._convert(node.body)
        return self._add_metadata(data, node)

    def _yield_stmt(self, node: YieldStmt) -> dict:
        """Convert YieldStmt node."""
        data = {
            "type": "YieldStmt",
            "call": self._convert(node.call),
        }
        return self._add_metadata(data, node)

    def _block(self, node: Block) -> dict:
        """Convert Block node."""
        data = {"type": "Block"}

        if node.steps:
            data["steps"] = self._convert(node.steps)
        if node.yield_stmt:
            data["yield"] = self._convert(node.yield_stmt)

        return self._add_metadata(data, node)

    def _and_then_block(self, node: AndThenBlock) -> dict:
        """Convert AndThenBlock node."""
        data = {"type": "AndThenBlock"}

        if node.foreach:
            data["foreach"] = self._convert(node.foreach)
        if node.when:
            data["when"] = self._convert(node.when)
        elif node.script:
            data["script"] = self._convert(node.script)
        elif node.block:
            if node.block.steps:
                data["steps"] = self._convert(node.block.steps)
            if node.block.yield_stmts:
                if len(node.block.yield_stmts) == 1:
                    data["yield"] = self._convert(node.block.yield_stmts[0])
                else:
                    data["yields"] = self._convert(node.block.yield_stmts)

        return self._add_metadata(data, node)

    def _foreach_clause(self, node: ForeachClause) -> dict:
        """Convert ForeachClause node."""
        return {
            "type": "ForeachClause",
            "variable": node.variable,
            "iterable": self._convert(node.iterable),
        }

    def _literal(self, node: Literal) -> dict:
        """Convert Literal node."""
        if node.kind == "null":
            return {"type": "Null"}
        elif node.kind == "string":
            return {"type": "String", "value": node.value}
        elif node.kind == "integer":
            return {"type": "Int", "value": node.value}
        elif node.kind == "double":
            return {"type": "Double", "value": node.value}
        elif node.kind == "boolean":
            return {"type": "Boolean", "value": node.value}
        else:
            return {"type": "Unknown", "value": node.value}

    def _reference(self, node: Reference) -> dict:
        """Convert Reference node."""
        if node.is_input:
            return {"type": "InputRef", "path": node.path}
        else:
            return {"type": "StepRef", "path": node.path}

    def _schema_decl(self, node: SchemaDecl) -> dict[str, Any]:
        """Convert SchemaDecl node."""
        data: dict[str, Any] = {
            "type": "SchemaDecl",
            "name": node.name,
        }
        if node.doc is not None:
            data["doc"] = self._doc_comment(node.doc)
        data["fields"] = self._convert(node.fields)
        return self._add_metadata(data, node)

    def _schema_field(self, node: SchemaField) -> dict:
        """Convert SchemaField node."""
        return {
            "name": node.name,
            "type": self._convert(node.type),
        }

    def _array_type(self, node: ArrayType) -> dict:
        """Convert ArrayType node."""
        return {
            "type": "ArrayType",
            "elementType": self._convert(node.element_type),
        }

    def _prompt_block(self, node: PromptBlock) -> dict:
        """Convert PromptBlock node."""
        data = {"type": "PromptBlock"}
        if node.system is not None:
            data["system"] = node.system
        if node.template is not None:
            data["template"] = node.template
        if node.model is not None:
            data["model"] = node.model
        return self._add_metadata(data, node)

    def _script_block(self, node: ScriptBlock) -> dict:
        """Convert ScriptBlock node."""
        data = {
            "type": "ScriptBlock",
            "language": node.language,
            "code": node.code,
        }
        return self._add_metadata(data, node)

    def _concat_expr(self, node: ConcatExpr) -> dict:
        """Convert ConcatExpr node."""
        return {
            "type": "ConcatExpr",
            "operands": self._convert(node.operands),
        }

    def _binary_expr(self, node: BinaryExpr) -> dict:
        """Convert BinaryExpr node."""
        return {
            "type": "BinaryExpr",
            "operator": node.operator,
            "left": self._convert(node.left),
            "right": self._convert(node.right),
        }

    def _unary_expr(self, node: UnaryExpr) -> dict:
        """Convert UnaryExpr node."""
        return {
            "type": "UnaryExpr",
            "operator": node.operator,
            "operand": self._convert(node.operand),
        }

    def _array_literal(self, node: ArrayLiteral) -> dict:
        """Convert ArrayLiteral node."""
        return {
            "type": "ArrayLiteral",
            "elements": [self._convert(e) for e in node.elements],
        }

    def _map_entry(self, node: MapEntry) -> dict:
        """Convert MapEntry node."""
        return {
            "key": node.key,
            "value": self._convert(node.value),
        }

    def _map_literal(self, node: MapLiteral) -> dict:
        """Convert MapLiteral node."""
        return {
            "type": "MapLiteral",
            "entries": [self._convert(e) for e in node.entries],
        }

    def _index_expr(self, node: IndexExpr) -> dict:
        """Convert IndexExpr node."""
        return {
            "type": "IndexExpr",
            "target": self._convert(node.target),
            "index": self._convert(node.index),
        }

    def _when_block(self, node: WhenBlock) -> dict:
        """Convert WhenBlock node."""
        return {
            "type": "WhenBlock",
            "cases": [self._convert(c) for c in node.cases],
        }

    def _when_case(self, node: WhenCase) -> dict:
        """Convert WhenCase node."""
        data: dict = {"type": "WhenCase"}
        if node.is_default:
            data["default"] = True
        else:
            data["condition"] = self._convert(node.condition)
        if node.block.steps:
            data["steps"] = self._convert(node.block.steps)
        if node.block.yield_stmts:
            if len(node.block.yield_stmts) == 1:
                data["yield"] = self._convert(node.block.yield_stmts[0])
            else:
                data["yields"] = self._convert(node.block.yield_stmts)
        return data


def emit_json(ast: Program, include_locations: bool = True, indent: int | None = 2) -> str:
    """Convert AST to JSON string.

    Args:
        ast: Program AST node
        include_locations: Include source locations
        indent: JSON indentation (None for compact)

    Returns:
        JSON string
    """
    emitter = JSONEmitter(include_locations=include_locations, indent=indent)
    return emitter.emit(ast)


def emit_dict(ast: Program, include_locations: bool = True) -> dict[str, Any]:
    """Convert AST to dictionary.

    Args:
        ast: Program AST node
        include_locations: Include source locations

    Returns:
        Dictionary representation
    """
    emitter = JSONEmitter(include_locations=include_locations)
    return emitter.emit_dict(ast)
