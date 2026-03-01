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

"""AFL (Agent Flow Language) compiler package."""

from .ast import (
    AndThenBlock,
    ArrayLiteral,
    ArrayType,
    ASTNode,
    BinaryExpr,
    Block,
    CallExpr,
    ConcatExpr,
    EventFacetDecl,
    FacetDecl,
    FacetSig,
    ForeachClause,
    ImplicitDecl,
    IndexExpr,
    Literal,
    MapEntry,
    MapLiteral,
    MatchBlock,
    MatchCase,
    MixinCall,
    MixinSig,
    NamedArg,
    Namespace,
    Parameter,
    Program,
    Reference,
    ReturnClause,
    SchemaDecl,
    SchemaField,
    SourceLocation,
    StepStmt,
    TypeRef,
    UnaryExpr,
    UsesDecl,
    WorkflowDecl,
    YieldStmt,
)
from .ast_utils import find_all_workflows, find_workflow, normalize_program_ast
from .config import AFLConfig, MongoDBConfig, ResolverConfig, load_config
from .emitter import JSONEmitter, emit_dict, emit_json
from .loader import SourceLoader
from .parser import AFLParser, ParseError, parse
from .publisher import PublishError, SourcePublisher
from .resolver import DependencyResolver, MongoDBNamespaceResolver, NamespaceIndex
from .source import (
    CompilerInput,
    FileOrigin,
    MavenOrigin,
    MongoDBOrigin,
    SourceEntry,
    SourceOrigin,
    SourceRegistry,
)
from .validator import AFLValidator, ValidationError, ValidationResult, validate

__version__ = "0.28.0"

__all__ = [
    # AST utilities
    "normalize_program_ast",
    "find_workflow",
    "find_all_workflows",
    # Parser
    "AFLParser",
    "ParseError",
    "parse",
    # Emitter
    "JSONEmitter",
    "emit_json",
    "emit_dict",
    # Validator
    "AFLValidator",
    "ValidationResult",
    "ValidationError",
    "validate",
    # Source input
    "SourceEntry",
    "CompilerInput",
    "SourceRegistry",
    "SourceOrigin",
    "FileOrigin",
    "MongoDBOrigin",
    "MavenOrigin",
    "SourceLoader",
    # Configuration
    "AFLConfig",
    "MongoDBConfig",
    "ResolverConfig",
    "load_config",
    # Resolver
    "DependencyResolver",
    "NamespaceIndex",
    "MongoDBNamespaceResolver",
    # Publisher
    "SourcePublisher",
    "PublishError",
    # AST nodes
    "Program",
    "Namespace",
    "UsesDecl",
    "FacetDecl",
    "EventFacetDecl",
    "WorkflowDecl",
    "ImplicitDecl",
    "FacetSig",
    "ReturnClause",
    "Parameter",
    "TypeRef",
    "MixinSig",
    "MixinCall",
    "CallExpr",
    "NamedArg",
    "StepStmt",
    "YieldStmt",
    "Block",
    "AndThenBlock",
    "ForeachClause",
    "Literal",
    "Reference",
    "ConcatExpr",
    "BinaryExpr",
    "UnaryExpr",
    "ArrayLiteral",
    "MapEntry",
    "MapLiteral",
    "MatchBlock",
    "MatchCase",
    "IndexExpr",
    "SourceLocation",
    "ASTNode",
    "ArrayType",
    "SchemaDecl",
    "SchemaField",
]
