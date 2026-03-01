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

"""AFL dependency resolution from compiled AST.

Extracts step dependencies from the compiled JSON AST
and builds a dependency graph for execution ordering.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .block import StatementDefinition
from .types import ObjectType


@dataclass
class DependencyGraph:
    """Dependency graph for a block's statements.

    Maps each statement to the statements it depends on.
    A statement can be created only when all dependencies are complete.
    """

    # Map of statement_id -> set of dependency statement_ids
    dependencies: dict[str, set[str]] = field(default_factory=dict)

    # Map of statement name -> statement_id (for name lookups)
    name_to_id: dict[str, str] = field(default_factory=dict)

    # Map of statement_id -> StatementDefinition
    statements: dict[str, StatementDefinition] = field(default_factory=dict)

    # Optional program AST for qualified facet name resolution
    _program_ast: dict | None = field(default=None, repr=False)

    @classmethod
    def from_ast(
        cls,
        block_ast: dict,
        workflow_inputs: set[str],
        program_ast: dict | None = None,
    ) -> "DependencyGraph":
        """Build dependency graph from compiled AST.

        Args:
            block_ast: The andThen block AST (dict with 'steps' and 'yield')
            workflow_inputs: Set of valid input parameter names
            program_ast: Optional program AST for qualified facet name resolution

        Returns:
            DependencyGraph for the block
        """
        graph = cls()
        graph._program_ast = program_ast

        steps = block_ast.get("steps", [])
        yields = block_ast.get("yields", [])
        single_yield = block_ast.get("yield")

        # First pass: collect all statement names and IDs
        for step_ast in steps:
            stmt = graph._parse_step(step_ast)
            graph.statements[stmt.id] = stmt
            graph.name_to_id[stmt.name] = stmt.id
            graph.dependencies[stmt.id] = set()

        # Handle yields
        if yields:
            for yield_ast in yields:
                stmt = graph._parse_yield(yield_ast)
                graph.statements[stmt.id] = stmt
                graph.dependencies[stmt.id] = set()
        elif single_yield:
            stmt = graph._parse_yield(single_yield)
            graph.statements[stmt.id] = stmt
            graph.dependencies[stmt.id] = set()

        # Second pass: extract dependencies from references
        for stmt_id, stmt in graph.statements.items():
            deps = graph._extract_dependencies(stmt.args, workflow_inputs)
            # Also scan mixin args for dependencies
            for mixin in stmt.mixins:
                deps |= graph._extract_dependencies(mixin.get("args", []), workflow_inputs)
            graph.dependencies[stmt_id] = deps
            stmt.dependencies = deps

        return graph

    def _parse_step(self, step_ast: dict) -> StatementDefinition:
        """Parse a step AST into StatementDefinition."""
        stmt_id = step_ast.get("id", step_ast.get("name", ""))
        name = step_ast.get("name", "")
        call = step_ast.get("call", {})
        facet_name = call.get("target", "")
        args = call.get("args", [])
        mixins = call.get("mixins", [])

        # Determine object type: check if target is a schema
        if self._is_schema_instantiation(facet_name):
            object_type = ObjectType.SCHEMA_INSTANTIATION
        else:
            object_type = ObjectType.VARIABLE_ASSIGNMENT
            # Only resolve facet names (not schema names)
            facet_name = self._resolve_facet_name(facet_name)

        return StatementDefinition(
            id=stmt_id,
            name=name,
            object_type=object_type,
            facet_name=facet_name,
            args=args,
            mixins=mixins,
            is_yield=False,
        )

    def _parse_yield(self, yield_ast: dict) -> StatementDefinition:
        """Parse a yield AST into StatementDefinition."""
        stmt_id = yield_ast.get("id", "yield")
        call = yield_ast.get("call", {})
        facet_name = call.get("target", "")
        args = call.get("args", [])

        # Resolve to qualified name if program AST is available
        facet_name = self._resolve_facet_name(facet_name)

        return StatementDefinition(
            id=stmt_id,
            name="_yield_" + stmt_id,
            object_type=ObjectType.YIELD_ASSIGNMENT,
            facet_name=facet_name,
            args=args,
            is_yield=True,
        )

    def _resolve_facet_name(self, short_name: str) -> str:
        """Resolve a facet name to its qualified form using the program AST.

        Args:
            short_name: The unqualified facet name

        Returns:
            Qualified name if resolvable, otherwise the original name
        """
        if not self._program_ast or not short_name:
            return short_name

        declarations = self._program_ast.get("declarations", [])
        result = self._resolve_in_declarations(declarations, short_name, prefix="")
        return result if result else short_name

    def _resolve_in_declarations(
        self, declarations: list, short_name: str, prefix: str
    ) -> str | None:
        """Recursively search declarations to resolve a qualified name."""
        for decl in declarations:
            decl_type = decl.get("type", "")
            if decl_type in ("FacetDecl", "EventFacetDecl", "WorkflowDecl"):
                if decl.get("name") == short_name:
                    if prefix:
                        return f"{prefix}.{short_name}"
                    return short_name
            elif decl_type == "Namespace":
                ns_name = decl.get("name", "")
                nested = decl.get("declarations", [])
                new_prefix = f"{prefix}.{ns_name}" if prefix else ns_name
                result = self._resolve_in_declarations(nested, short_name, new_prefix)
                if result:
                    return result
        return None

    def _is_schema_instantiation(self, name: str) -> bool:
        """Check if the given name refers to a schema.

        Args:
            name: The target name from a step call

        Returns:
            True if the name is a schema, False otherwise
        """
        if not self._program_ast or not name:
            return False

        declarations = self._program_ast.get("declarations", [])
        return self._find_schema_in_declarations(declarations, name, prefix="") is not None

    def _find_schema_in_declarations(
        self, declarations: list, name: str, prefix: str
    ) -> str | None:
        """Recursively search declarations to find a schema.

        Args:
            declarations: List of declaration dicts
            name: The schema name to find (may be qualified)
            prefix: Current namespace prefix

        Returns:
            Qualified schema name if found, None otherwise
        """
        # Handle qualified names
        if "." in name:
            parts = name.split(".", 1)
            ns_prefix = parts[0]
            rest = parts[1]
            for decl in declarations:
                if decl.get("type") == "Namespace" and decl.get("name") == ns_prefix:
                    nested = decl.get("declarations", [])
                    new_prefix = f"{prefix}.{ns_prefix}" if prefix else ns_prefix
                    return self._find_schema_in_declarations(nested, rest, new_prefix)
            return None

        # Unqualified name - search at current level and in namespaces
        for decl in declarations:
            decl_type = decl.get("type", "")
            if decl_type == "SchemaDecl":
                if decl.get("name") == name:
                    if prefix:
                        return f"{prefix}.{name}"
                    return name
            elif decl_type == "Namespace":
                ns_name = decl.get("name", "")
                nested = decl.get("declarations", [])
                new_prefix = f"{prefix}.{ns_name}" if prefix else ns_name
                result = self._find_schema_in_declarations(nested, name, new_prefix)
                if result:
                    return result
        return None

    def _extract_dependencies(
        self,
        args: list[dict],
        workflow_inputs: set[str],
    ) -> set[str]:
        """Extract step dependencies from argument expressions.

        Args:
            args: List of named argument dicts
            workflow_inputs: Valid input parameter names

        Returns:
            Set of statement IDs this depends on
        """
        deps: set[str] = set()

        for arg in args:
            value = arg.get("value", {})
            self._extract_refs_from_value(value, deps)

        return deps

    def _extract_refs_from_value(self, value: Any, deps: set[str]) -> None:
        """Recursively extract step references from a value expression.

        Args:
            value: The value expression (dict or primitive)
            deps: Set to add dependencies to
        """
        if not isinstance(value, dict):
            return

        value_type = value.get("type", "")

        if value_type == "StepRef":
            # Step reference: first path element is the step name
            path = value.get("path", [])
            if path:
                step_name = path[0]
                # Look up the statement ID for this step name
                stmt_id = self.name_to_id.get(step_name)
                if stmt_id:
                    deps.add(stmt_id)

        elif value_type == "ConcatExpr":
            # Concatenation: check all operands
            for operand in value.get("operands", []):
                self._extract_refs_from_value(operand, deps)

        elif value_type == "BinaryExpr":
            # Binary expression: check left and right operands
            self._extract_refs_from_value(value.get("left"), deps)
            self._extract_refs_from_value(value.get("right"), deps)

        elif value_type == "ArrayLiteral":
            # Array literal: check all elements
            for element in value.get("elements", []):
                self._extract_refs_from_value(element, deps)

        elif value_type == "MapLiteral":
            # Map literal: check all entry values
            for entry in value.get("entries", []):
                self._extract_refs_from_value(entry.get("value"), deps)

        elif value_type == "UnaryExpr":
            # Unary expression: check operand
            self._extract_refs_from_value(value.get("operand"), deps)

        elif value_type == "IndexExpr":
            # Index expression: check target and index
            self._extract_refs_from_value(value.get("target"), deps)
            self._extract_refs_from_value(value.get("index"), deps)

        # InputRef ($.) doesn't create dependencies - it references workflow input

    def can_create(self, statement_id: str, completed: set[str]) -> bool:
        """Check if a statement can be created.

        Args:
            statement_id: The statement to check
            completed: Set of completed statement IDs

        Returns:
            True if all dependencies are satisfied
        """
        deps = self.dependencies.get(statement_id, set())
        return deps.issubset(completed)

    def get_ready_statements(self, completed: set[str]) -> Sequence[StatementDefinition]:
        """Get statements ready to be created.

        Yield statements are only ready when ALL non-yield statements
        are terminal (complete or error). This ensures yields execute
        after the block's regular work is done.

        Args:
            completed: Set of completed statement IDs

        Returns:
            Statements with all dependencies satisfied
        """
        non_yield_ids = {sid for sid, stmt in self.statements.items() if not stmt.is_yield}
        yields_eligible = non_yield_ids.issubset(completed)

        ready = []
        for stmt_id, stmt in self.statements.items():
            if stmt_id not in completed and self.can_create(stmt_id, completed):
                if stmt.is_yield and not yields_eligible:
                    continue
                ready.append(stmt)
        return ready

    def get_statement(self, statement_id: str) -> StatementDefinition | None:
        """Get a statement by ID."""
        return self.statements.get(statement_id)

    def get_all_statements(self) -> Sequence[StatementDefinition]:
        """Get all statements in the block."""
        return list(self.statements.values())

    def topological_order(self) -> list[str]:
        """Get statements in topological order (dependencies first).

        Returns:
            List of statement IDs in dependency order
        """
        visited: set[str] = set()
        order: list[str] = []

        def visit(stmt_id: str) -> None:
            if stmt_id in visited:
                return
            visited.add(stmt_id)
            for dep_id in self.dependencies.get(stmt_id, set()):
                visit(dep_id)
            order.append(stmt_id)

        for stmt_id in self.statements:
            visit(stmt_id)

        return order
