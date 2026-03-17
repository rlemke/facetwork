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

"""AFL semantic validator.

Validates AST for semantic correctness:
- Name uniqueness within scopes
- Valid step references
- Valid yield targets
- Valid use statements (must reference existing namespaces)
- Unambiguous facet references (qualified names when needed)
"""

import re
from dataclasses import dataclass, field

from .ast import (
    AndThenBlock,
    ArrayLiteral,
    ArrayType,
    BinaryExpr,
    CallExpr,
    CatchClause,
    ConcatExpr,
    EventFacetDecl,
    FacetDecl,
    FacetSig,
    ImplicitDecl,
    IndexExpr,
    Literal,
    MapLiteral,
    Namespace,
    Program,
    PromptBlock,
    Reference,
    SchemaDecl,
    ScriptBlock,
    SourceLocation,
    TypeRef,
    UnaryExpr,
    WhenBlock,
    WorkflowDecl,
    YieldStmt,
)


@dataclass
class ValidationError:
    """A semantic validation error."""

    message: str
    line: int | None = None
    column: int | None = None

    def __str__(self) -> str:
        location = ""
        if self.line is not None:
            location = f" at line {self.line}"
            if self.column is not None:
                location += f", column {self.column}"
        return f"{self.message}{location}"


@dataclass
class ValidationResult:
    """Result of validation containing any errors found."""

    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, message: str, location: SourceLocation | None = None) -> None:
        """Add a validation error."""
        line = location.line if location else None
        column = location.column if location else None
        self.errors.append(ValidationError(message, line, column))

    def add_warning(self, message: str, location: SourceLocation | None = None) -> None:
        """Add a validation warning."""
        line = location.line if location else None
        column = location.column if location else None
        self.warnings.append(ValidationError(message, line, column))


@dataclass
class FacetInfo:
    """Information about a declared facet for reference validation."""

    name: str
    params: set[str]  # Input parameter names
    returns: set[str]  # Return parameter names
    returns_types: dict[str, str] = field(default_factory=dict)  # Return field name → type
    location: SourceLocation | None = None


@dataclass
class SchemaInfo:
    """Information about a declared schema for validation."""

    name: str
    fields: set[str]  # Field names
    fields_types: dict[str, str] = field(default_factory=dict)  # Field name → type
    location: SourceLocation | None = None


@dataclass
class StepInfo:
    """Information about a step within a block."""

    name: str
    facet_name: str
    location: SourceLocation | None = None


class AFLValidator:
    """Validates AFL AST for semantic correctness."""

    def __init__(self):
        self._result: ValidationResult = ValidationResult()
        self._facets: dict[str, FacetInfo] = {}  # All known facets by full name
        self._facets_by_short_name: dict[str, list[str]] = {}  # Short name -> list of full names
        self._namespaces: set[str] = set()  # All namespace names
        self._schemas: dict[str, SourceLocation] = {}  # Schema names by full name (for uniqueness)
        self._schema_info: dict[str, SchemaInfo] = {}  # Full name -> SchemaInfo
        self._schemas_by_short_name: dict[str, list[str]] = {}  # Short name -> [full names]
        self._current_namespace: str = ""
        self._current_imports: set[str] = set()  # Namespaces imported via 'use'
        self._param_scope: dict[str, str] = {}  # Parameter name -> inferred type

    def validate(self, program: Program) -> ValidationResult:
        """Validate a program AST.

        Args:
            program: The Program AST to validate

        Returns:
            ValidationResult containing any errors found
        """
        self._result = ValidationResult()
        self._facets = {}
        self._facets_by_short_name = {}
        self._namespaces = set()
        self._schemas = {}
        self._schema_info = {}
        self._schemas_by_short_name = {}
        self._current_namespace = ""
        self._current_imports = set()
        self._param_scope = {}

        # First pass: collect all namespace names and facet definitions
        self._collect_namespaces(program)
        self._collect_facets(program)

        # Second pass: validate references and yields
        self._validate_program(program)

        return self._result

    def _collect_namespaces(self, program: Program) -> None:
        """Collect all namespace names for use statement validation."""
        for namespace in program.namespaces:
            self._namespaces.add(namespace.name)

    def _collect_facets(self, program: Program) -> None:
        """Collect all facet and schema definitions for reference validation."""
        # Top-level declarations
        for facet in program.facets:
            self._register_facet(facet.sig)
        for event_facet in program.event_facets:
            self._register_facet(event_facet.sig)
        for workflow in program.workflows:
            self._register_facet(workflow.sig)
        # Top-level schemas are not allowed - emit error
        for schema in program.schemas:
            self._result.add_error(
                f"Schema '{schema.name}' must be defined inside a namespace. "
                f"Top-level schemas are not allowed.",
                schema.location,
            )

        # Namespace declarations
        for namespace in program.namespaces:
            for facet in namespace.facets:
                self._register_facet(facet.sig, namespace.name)
            for event_facet in namespace.event_facets:
                self._register_facet(event_facet.sig, namespace.name)
            for workflow in namespace.workflows:
                self._register_facet(workflow.sig, namespace.name)
            for schema in namespace.schemas:
                self._register_schema(schema, namespace.name)

    _PRIMITIVE_TYPES = {"String", "Int", "Long", "Double", "Boolean"}

    @staticmethod
    def _type_ref_to_str(type_ref) -> str:
        """Convert a type reference to a type string for inference.

        Returns primitive type names, "Array" for array types, or the schema
        type name (e.g. "MySchema", "ns.SchemaName") for schema-typed references.
        Returns "Unknown" only when the type reference cannot be resolved.
        """
        if isinstance(type_ref, ArrayType):
            return "Array"
        if isinstance(type_ref, TypeRef):
            name = type_ref.name
            if name:
                return name
        return "Unknown"

    def _register_facet(self, sig: FacetSig, namespace: str = "") -> None:
        """Register a facet definition."""
        full_name = f"{namespace}.{sig.name}" if namespace else sig.name
        params = {p.name for p in sig.params}
        returns = {p.name for p in sig.returns.params} if sig.returns else set()
        returns_types: dict[str, str] = {}
        if sig.returns:
            for p in sig.returns.params:
                returns_types[p.name] = self._type_ref_to_str(p.type)
        self._facets[full_name] = FacetInfo(
            name=sig.name,
            params=params,
            returns=returns,
            returns_types=returns_types,
            location=sig.location,
        )
        # Track by short name for ambiguity detection
        short_name = sig.name
        if short_name not in self._facets_by_short_name:
            self._facets_by_short_name[short_name] = []
        self._facets_by_short_name[short_name].append(full_name)

    def _register_schema(self, schema: SchemaDecl, namespace: str = "") -> None:
        """Register a schema definition for instantiation validation."""
        full_name = f"{namespace}.{schema.name}" if namespace else schema.name
        fields = {f.name for f in schema.fields}
        fields_types: dict[str, str] = {}
        for f in schema.fields:
            fields_types[f.name] = self._type_ref_to_str(f.type)
        self._schema_info[full_name] = SchemaInfo(
            name=schema.name,
            fields=fields,
            fields_types=fields_types,
            location=schema.location,
        )
        # Track by short name for ambiguity detection
        short_name = schema.name
        if short_name not in self._schemas_by_short_name:
            self._schemas_by_short_name[short_name] = []
        self._schemas_by_short_name[short_name].append(full_name)

    def _resolve_schema_name(
        self, name: str, location: SourceLocation | None = None
    ) -> SchemaInfo | None:
        """Resolve a schema name to its SchemaInfo, checking for ambiguity.

        Resolution follows the same order as facets:
        1. Fully qualified name (exact match)
        2. Current namespace (takes precedence)
        3. Imported namespaces (via 'use')
        4. Top-level (no namespace)

        Args:
            name: The schema name (may be qualified or unqualified)
            location: Source location for error reporting

        Returns:
            SchemaInfo if found unambiguously, None if not found
        """
        # If it's a fully qualified name, look it up directly
        if "." in name:
            if name in self._schema_info:
                return self._schema_info[name]
            return None

        # Unqualified name
        short_name = name

        # Check current namespace first - local schemas take precedence
        if self._current_namespace:
            full_in_current = f"{self._current_namespace}.{short_name}"
            if full_in_current in self._schema_info:
                return self._schema_info[full_in_current]

        # Check imported namespaces - collect candidates for ambiguity check
        import_candidates: list[str] = []
        for imported_ns in self._current_imports:
            full_in_import = f"{imported_ns}.{short_name}"
            if full_in_import in self._schema_info:
                import_candidates.append(full_in_import)

        # Check top-level (no namespace)
        top_level_match = None
        if short_name in self._schema_info:
            all_matches = self._schemas_by_short_name.get(short_name, [])
            if short_name in all_matches:
                top_level_match = short_name

        # Combine import candidates with top-level
        candidates = import_candidates[:]
        if top_level_match:
            candidates.append(top_level_match)

        # Check if ambiguous among imports/top-level
        if len(candidates) > 1:
            self._result.add_error(
                f"Ambiguous schema reference '{short_name}': could be {', '.join(sorted(candidates))}. "
                f"Use fully qualified name to disambiguate.",
                location,
            )
            return None

        if len(candidates) == 1:
            return self._schema_info[candidates[0]]

        # Not found in current scope or imports - try global lookup
        all_matches = self._schemas_by_short_name.get(short_name, [])
        if len(all_matches) == 1:
            return self._schema_info[all_matches[0]]
        elif len(all_matches) > 1:
            self._result.add_error(
                f"Ambiguous schema reference '{short_name}': could be {', '.join(sorted(all_matches))}. "
                f"Use fully qualified name to disambiguate.",
                location,
            )
            return None

        # Not found anywhere
        return None

    # Builtin types that don't require schema resolution
    BUILTIN_TYPES = {"String", "Long", "Int", "Double", "Boolean", "Json"}

    def _validate_type_ref(
        self, type_node: TypeRef | ArrayType, location: SourceLocation | None = None
    ) -> None:
        """Validate a type reference to ensure it resolves to a known type.

        For non-builtin types, validates that the type name resolves to a schema
        using the same resolution rules as facet references.

        Args:
            type_node: The type reference or array type to validate
            location: Source location for error reporting
        """
        if isinstance(type_node, ArrayType):
            # Recursively validate element type
            self._validate_type_ref(type_node.element_type, type_node.location or location)
            return

        # It's a TypeRef - check if it's a builtin or needs schema resolution
        type_name = type_node.name
        if type_name in self.BUILTIN_TYPES:
            return  # Builtin types are always valid

        # Not a builtin - must resolve to a schema
        schema_info = self._resolve_schema_name(type_name, type_node.location or location)
        if schema_info is None:
            # _resolve_schema_name already reports ambiguity errors
            # Only report "unknown type" if it wasn't found at all
            all_matches = self._schemas_by_short_name.get(type_name, [])
            if len(all_matches) == 0 and "." not in type_name:
                # Unqualified name not found
                self._result.add_error(
                    f"Unknown type '{type_name}': not a builtin type or known schema. "
                    f"Schema types must be defined in a namespace and either imported via 'use' "
                    f"or referenced with a fully qualified name.",
                    type_node.location or location,
                )
            elif "." in type_name and type_name not in self._schema_info:
                # Qualified name not found
                self._result.add_error(
                    f"Unknown schema '{type_name}': no schema found with this qualified name.",
                    type_node.location or location,
                )

    def _validate_signature_types(self, sig: FacetSig) -> None:
        """Validate all type references in a facet/workflow signature."""
        # Validate parameter types
        for param in sig.params:
            self._validate_type_ref(param.type, param.location)

        # Validate return types
        if sig.returns:
            for ret_param in sig.returns.params:
                self._validate_type_ref(ret_param.type, ret_param.location)

    def _resolve_facet_name(
        self, name: str, location: SourceLocation | None = None
    ) -> FacetInfo | None:
        """Resolve a facet name to its FacetInfo, checking for ambiguity.

        Resolution order:
        1. Fully qualified name (exact match)
        2. Current namespace (takes precedence, no ambiguity check)
        3. Imported namespaces (via 'use') - check for ambiguity among imports
        4. Top-level (no namespace)

        Args:
            name: The facet name (may be qualified or unqualified)
            location: Source location for error reporting

        Returns:
            FacetInfo if found unambiguously, None if not found or ambiguous
        """
        # If it's a fully qualified name, look it up directly
        if "." in name:
            if name in self._facets:
                return self._facets[name]
            # Not found as exact match
            self._result.add_error(f"Unknown facet '{name}'", location)
            return None

        # Unqualified name
        short_name = name

        # Check current namespace first - local facets take precedence
        if self._current_namespace:
            full_in_current = f"{self._current_namespace}.{short_name}"
            if full_in_current in self._facets:
                return self._facets[full_in_current]

        # Check imported namespaces - collect candidates for ambiguity check
        import_candidates: list[str] = []
        for imported_ns in self._current_imports:
            full_in_import = f"{imported_ns}.{short_name}"
            if full_in_import in self._facets:
                import_candidates.append(full_in_import)

        # Check top-level (no namespace)
        top_level_match = None
        if short_name in self._facets:
            # Check if it's actually a top-level facet (not namespaced)
            all_matches = self._facets_by_short_name.get(short_name, [])
            if short_name in all_matches:
                top_level_match = short_name

        # Combine import candidates with top-level
        candidates = import_candidates[:]
        if top_level_match:
            candidates.append(top_level_match)

        # Check if ambiguous among imports/top-level
        if len(candidates) > 1:
            self._result.add_error(
                f"Ambiguous facet reference '{short_name}': could be {', '.join(sorted(candidates))}. "
                f"Use fully qualified name to disambiguate.",
                location,
            )
            return None

        if len(candidates) == 1:
            all_matches = self._facets_by_short_name.get(short_name, [])
            if len(all_matches) > 1:
                self._result.add_error(
                    f"Ambiguous facet reference '{short_name}': could be "
                    f"{', '.join(sorted(all_matches))}. "
                    f"Use fully qualified name to disambiguate.",
                    location,
                )
                return None
            return self._facets[candidates[0]]

        # Not found in current scope or imports - try global lookup
        all_matches = self._facets_by_short_name.get(short_name, [])
        if len(all_matches) == 1:
            return self._facets[all_matches[0]]
        elif len(all_matches) > 1:
            self._result.add_error(
                f"Ambiguous facet reference '{short_name}': could be {', '.join(sorted(all_matches))}. "
                f"Use fully qualified name to disambiguate.",
                location,
            )
            return None

        # Not found anywhere - this is OK, might be an external facet
        return None

    def _validate_program(self, program: Program) -> None:
        """Validate the entire program."""
        # Validate top-level name uniqueness
        self._validate_top_level_uniqueness(program)

        # Validate each namespace
        for namespace in program.namespaces:
            self._validate_namespace(namespace)

        # Validate top-level declarations
        for facet in program.facets:
            self._validate_facet_decl(facet)
        for event_facet in program.event_facets:
            self._validate_event_facet_decl(event_facet)
        for workflow in program.workflows:
            self._validate_workflow_decl(workflow)
        for implicit in program.implicits:
            self._validate_implicit_decl(implicit)

    def _validate_top_level_uniqueness(self, program: Program) -> None:
        """Validate that top-level names are unique."""
        names: dict[str, SourceLocation | None] = {}

        for facet in program.facets:
            self._check_name_unique(facet.sig.name, facet.location, names, "facet")
        for event_facet in program.event_facets:
            self._check_name_unique(
                event_facet.sig.name, event_facet.location, names, "event facet"
            )
        for workflow in program.workflows:
            self._check_name_unique(workflow.sig.name, workflow.location, names, "workflow")
        for schema in program.schemas:
            self._check_name_unique(schema.name, schema.location, names, "schema")
            self._validate_schema_decl(schema)

    def _validate_namespace(self, namespace: Namespace) -> None:
        """Validate a namespace."""
        self._current_namespace = namespace.name
        self._current_imports = set()
        names: dict[str, SourceLocation | None] = {}

        # Validate use statements
        for uses_decl in namespace.uses:
            if uses_decl.name not in self._namespaces:
                self._result.add_error(
                    f"Invalid use statement: namespace '{uses_decl.name}' does not exist",
                    uses_decl.location,
                )
            else:
                self._current_imports.add(uses_decl.name)

        # Check name uniqueness within namespace
        for facet in namespace.facets:
            self._check_name_unique(facet.sig.name, facet.location, names, "facet")
        for event_facet in namespace.event_facets:
            self._check_name_unique(
                event_facet.sig.name, event_facet.location, names, "event facet"
            )
        for workflow in namespace.workflows:
            self._check_name_unique(workflow.sig.name, workflow.location, names, "workflow")
        for schema in namespace.schemas:
            self._check_name_unique(schema.name, schema.location, names, "schema")
            self._validate_schema_decl(schema)

        # Validate declarations
        for facet in namespace.facets:
            self._validate_facet_decl(facet)
        for event_facet in namespace.event_facets:
            self._validate_event_facet_decl(event_facet)
        for workflow in namespace.workflows:
            self._validate_workflow_decl(workflow)
        for implicit in namespace.implicits:
            self._validate_implicit_decl(implicit)

        self._current_namespace = ""
        self._current_imports = set()

    def _check_name_unique(
        self,
        name: str,
        location: SourceLocation | None,
        names: dict[str, SourceLocation | None],
        kind: str,
    ) -> None:
        """Check if a name is unique within a scope."""
        if name in names:
            prev_loc = names[name]
            prev_line = (
                f" (previously defined at line {prev_loc.line})"
                if prev_loc and prev_loc.line
                else ""
            )
            self._result.add_error(f"Duplicate {kind} name '{name}'{prev_line}", location)
        else:
            names[name] = location

    def _build_param_scope(self, sig: FacetSig) -> dict[str, str]:
        """Build a parameter name -> type mapping from a facet signature.

        Maps TypeRef names to their inferred types for type checking.
        Schema types, Json, and ArrayType map to Unknown/Array respectively.
        """
        scope: dict[str, str] = {}
        for param in sig.params:
            if isinstance(param.type, ArrayType):
                scope[param.name] = "Array"
            elif isinstance(param.type, TypeRef):
                name = param.type.name
                if name in ("String", "Int", "Long", "Double", "Boolean"):
                    scope[param.name] = name
                else:
                    # Json, schema types, etc. — not enough info to check
                    scope[param.name] = "Unknown"
        return scope

    def _validate_body(self, body, sig: FacetSig) -> None:
        """Validate a body, handling single or list of AndThenBlocks."""
        if isinstance(body, list):
            # First pass: collect step names from each block
            all_block_steps: list[set[str]] = []
            for block in body:
                all_block_steps.append(self._collect_block_step_names(block))

            # Accumulate step context from regular andThen blocks for
            # cross-block visibility in when blocks (Gap 2).
            accumulated_steps: dict[str, StepInfo] = {}
            accumulated_step_returns: dict[str, set[str]] = {}
            accumulated_step_returns_types: dict[str, dict[str, str]] = {}

            # Second pass: validate, passing other blocks' steps for
            # cross-block reference detection
            for idx, block in enumerate(body):
                other_steps = set()
                for j, s in enumerate(all_block_steps):
                    if j != idx:
                        other_steps |= s

                if block.when:
                    # When blocks can see steps from prior regular andThen blocks
                    self._validate_and_then_block(
                        block,
                        sig,
                        other_block_steps=other_steps,
                        parent_steps=accumulated_steps,
                        parent_step_returns=accumulated_step_returns,
                        parent_step_returns_types=accumulated_step_returns_types,
                    )
                else:
                    self._validate_and_then_block(block, sig, other_block_steps=other_steps)
                    # Accumulate step info from regular blocks for subsequent when blocks
                    if block.block:
                        for step in block.block.steps:
                            facet_info = self._resolve_facet_name(step.call.name)
                            schema_info = None
                            if facet_info:
                                accumulated_steps[step.name] = StepInfo(
                                    name=step.name,
                                    facet_name=step.call.name,
                                    location=step.location,
                                )
                                accumulated_step_returns[step.name] = facet_info.returns
                                accumulated_step_returns_types[step.name] = facet_info.returns_types
                            else:
                                schema_info = self._resolve_schema_name(step.call.name)
                                if schema_info:
                                    accumulated_steps[step.name] = StepInfo(
                                        name=step.name,
                                        facet_name=step.call.name,
                                        location=step.location,
                                    )
                                    accumulated_step_returns[step.name] = schema_info.fields
                                    accumulated_step_returns_types[step.name] = (
                                        schema_info.fields_types
                                    )
                                else:
                                    accumulated_steps[step.name] = StepInfo(
                                        name=step.name,
                                        facet_name=step.call.name,
                                        location=step.location,
                                    )
                                    accumulated_step_returns[step.name] = set()
                                    accumulated_step_returns_types[step.name] = {}
        elif isinstance(body, AndThenBlock):
            self._validate_and_then_block(body, sig)

    @staticmethod
    def _collect_block_step_names(block: AndThenBlock) -> set[str]:
        """Collect all step names defined in a block."""
        if not block.block:
            return set()
        return {step.name for step in block.block.steps}

    def _validate_facet_decl(self, decl: FacetDecl) -> None:
        """Validate a facet declaration."""
        # Validate type references in signature
        self._validate_signature_types(decl.sig)
        # Validate mixin references in signature
        for mixin in decl.sig.mixins:
            self._resolve_facet_name(mixin.name, mixin.location)
        self._param_scope = self._build_param_scope(decl.sig)
        if decl.pre_script:
            self._validate_script_block(decl.pre_script, decl.sig)
        if decl.body:
            self._validate_body(decl.body, decl.sig)
        if decl.catch:
            self._validate_catch_clause(decl.catch, decl.sig)
        self._param_scope = {}

    def _validate_event_facet_decl(self, decl: EventFacetDecl) -> None:
        """Validate an event facet declaration."""
        # Validate type references in signature
        self._validate_signature_types(decl.sig)
        # Validate mixin references in signature
        for mixin in decl.sig.mixins:
            self._resolve_facet_name(mixin.name, mixin.location)
        self._param_scope = self._build_param_scope(decl.sig)
        if decl.pre_script:
            self._validate_script_block(decl.pre_script, decl.sig)
        if decl.body:
            if isinstance(decl.body, PromptBlock):
                self._validate_prompt_block(decl.body, decl.sig)
            else:
                self._validate_body(decl.body, decl.sig)
        if decl.catch:
            self._validate_catch_clause(decl.catch, decl.sig)
        self._param_scope = {}

    def _validate_prompt_block(self, block: PromptBlock, sig: FacetSig) -> None:
        """Validate a prompt block.

        Checks:
        1. At least a template directive is present
        2. Placeholder references {param_name} match facet parameters
        """
        # Require at least a template
        if block.template is None:
            self._result.add_error(
                "Prompt block must have a 'template' directive",
                block.location,
            )
            return

        # Get valid parameter names
        param_names = {p.name for p in sig.params}

        # Find all {placeholder} references in template and system
        placeholder_pattern = re.compile(r"\{(\w+)\}")

        for text, directive_name in [(block.template, "template"), (block.system, "system")]:
            if text is None:
                continue
            for match in placeholder_pattern.finditer(text):
                placeholder = match.group(1)
                if placeholder not in param_names:
                    self._result.add_error(
                        f"Invalid placeholder '{{{placeholder}}}' in {directive_name}: "
                        f"no parameter named '{placeholder}'. "
                        f"Valid parameters are: {sorted(param_names)}",
                        block.location,
                    )

    def _validate_script_block(self, block: ScriptBlock, sig: FacetSig) -> None:
        """Validate a script block.

        Checks:
        1. Code is not empty
        2. Language is supported (currently only 'python')
        """
        if not block.code or not block.code.strip():
            self._result.add_error(
                "Script block must contain code",
                block.location,
            )
            return

        if block.language not in ("python",):
            self._result.add_error(
                f"Unsupported script language '{block.language}'. "
                f"Currently only 'python' is supported.",
                block.location,
            )

    def _validate_workflow_decl(self, decl: WorkflowDecl) -> None:
        """Validate a workflow declaration."""
        # Validate type references in signature
        self._validate_signature_types(decl.sig)
        # Validate mixin references in signature
        for mixin in decl.sig.mixins:
            self._resolve_facet_name(mixin.name, mixin.location)
        self._param_scope = self._build_param_scope(decl.sig)
        if decl.pre_script:
            self._validate_script_block(decl.pre_script, decl.sig)
        if decl.body:
            self._validate_body(decl.body, decl.sig)
        if decl.catch:
            self._validate_catch_clause(decl.catch, decl.sig)
        self._param_scope = {}

    def _validate_and_then_block(
        self,
        body: AndThenBlock,
        containing_sig: FacetSig,
        extra_yield_targets: set[str] | None = None,
        other_block_steps: set[str] | None = None,
        parent_steps: dict[str, "StepInfo"] | None = None,
        parent_step_returns: dict[str, set[str]] | None = None,
        parent_step_returns_types: dict[str, dict[str, str]] | None = None,
        parent_foreach_var: str | None = None,
    ) -> None:
        """Validate an andThen block."""
        # andThen script variant
        if body.script:
            self._validate_script_block(body.script, containing_sig)
            return

        # andThen when variant
        if body.when:
            self._validate_when_block(
                body.when,
                containing_sig,
                extra_yield_targets,
                step_returns_types=parent_step_returns_types,
                steps=parent_steps,
                step_returns=parent_step_returns,
            )
            return

        if not body.block:
            return

        # Get valid input references (parameters of containing facet)
        input_attrs = {p.name for p in containing_sig.params}

        # Get valid yield targets (containing facet name + mixin names)
        valid_yield_targets = {containing_sig.name}
        for mixin in containing_sig.mixins:
            valid_yield_targets.add(mixin.name.split(".")[-1])  # Use short name
        if extra_yield_targets:
            valid_yield_targets |= extra_yield_targets

        # Track steps and their return attributes
        steps: dict[str, StepInfo] = {}
        step_returns: dict[str, set[str]] = {}
        step_returns_types: dict[str, dict[str, str]] = {}  # step → {field → type}

        # Inherit parent block's steps (for step body validation)
        if parent_steps:
            for name, info in parent_steps.items():
                steps[name] = info
        if parent_step_returns:
            for name, rets in parent_step_returns.items():
                step_returns[name] = rets
        if parent_step_returns_types:
            for name, types in parent_step_returns_types.items():
                step_returns_types[name] = types

        # If foreach, add the iteration variable; inherit from parent if not set
        foreach_var: str | None = None
        if body.foreach:
            foreach_var = body.foreach.variable
        elif parent_foreach_var:
            foreach_var = parent_foreach_var

        # Validate each step
        for step in body.block.steps:
            # Check step name uniqueness
            if step.name in steps:
                prev = steps[step.name]
                prev_line = (
                    f" (previously defined at line {prev.location.line})"
                    if prev.location and prev.location.line
                    else ""
                )
                self._result.add_error(
                    f"Duplicate step name '{step.name}'{prev_line}", step.location
                )
            else:
                steps[step.name] = StepInfo(
                    name=step.name, facet_name=step.call.name, location=step.location
                )
                # Try to resolve as facet first, then as schema
                facet_info = self._resolve_facet_name(step.call.name, step.call.location)
                schema_info = None
                if facet_info:
                    step_returns[step.name] = facet_info.returns
                    step_returns_types[step.name] = facet_info.returns_types
                else:
                    # Try to resolve as schema instantiation
                    schema_info = self._resolve_schema_name(step.call.name, step.call.location)
                    if schema_info:
                        # Schema fields become the step's "returns" (accessible via step.field)
                        step_returns[step.name] = schema_info.fields
                        step_returns_types[step.name] = schema_info.fields_types
                        # Validate schema instantiation
                        self._validate_schema_instantiation(step.call, schema_info)
                    else:
                        step_returns[step.name] = set()  # Unknown facet or schema
                        step_returns_types[step.name] = {}

            # Validate mixin references in step call (only for non-schema instantiations)
            for mixin_call in step.call.mixins:
                self._resolve_facet_name(mixin_call.name, step.call.location)

            # Validate references in step's call arguments
            self._validate_call_references(
                step.call,
                input_attrs,
                steps,
                step_returns,
                foreach_var,
                step.name,  # Current step being defined
                step_returns_types,
                other_block_steps=other_block_steps,
            )

            # Validate inline step body if present
            if step.body:
                step_target = step.call.name.split(".")[-1]
                self._validate_and_then_block(
                    step.body,
                    containing_sig,
                    extra_yield_targets={step_target},
                    parent_steps=steps,
                    parent_step_returns=step_returns,
                    parent_step_returns_types=step_returns_types,
                )

            # Validate inline catch clause if present
            if step.catch:
                # In catch blocks, the caught step's .error and .error_type are accessible
                catch_step_returns = dict(step_returns)
                catch_step_returns_types = dict(step_returns_types)
                if step.name in catch_step_returns:
                    catch_step_returns[step.name] = catch_step_returns[step.name] | {
                        "error",
                        "error_type",
                    }
                else:
                    catch_step_returns[step.name] = {"error", "error_type"}
                if step.name in catch_step_returns_types:
                    catch_step_returns_types[step.name] = {
                        **catch_step_returns_types[step.name],
                        "error": "String",
                        "error_type": "String",
                    }
                else:
                    catch_step_returns_types[step.name] = {
                        "error": "String",
                        "error_type": "String",
                    }
                self._validate_catch_clause(
                    step.catch,
                    containing_sig,
                    parent_steps=steps,
                    parent_step_returns=catch_step_returns,
                    parent_step_returns_types=catch_step_returns_types,
                    parent_foreach_var=foreach_var,
                )

        # Validate yield statements and check for duplicate targets
        yield_targets_used: set[str] = set()
        for yield_stmt in body.block.yield_stmts:
            self._validate_yield(
                yield_stmt,
                valid_yield_targets,
                steps,
                step_returns,
                input_attrs,
                foreach_var,
                step_returns_types,
                other_block_steps=other_block_steps,
            )
            target = yield_stmt.call.name.split(".")[-1]
            if target in yield_targets_used:
                self._result.add_error(
                    f"Duplicate yield target '{target}': each yield must reference a different facet or mixin",
                    yield_stmt.location,
                )
            else:
                yield_targets_used.add(target)

    def _validate_when_block(
        self,
        when: WhenBlock,
        containing_sig: FacetSig,
        extra_yield_targets: set[str] | None = None,
        step_returns_types: dict[str, dict[str, str]] | None = None,
        steps: dict[str, "StepInfo"] | None = None,
        step_returns: dict[str, set[str]] | None = None,
    ) -> None:
        """Validate a when block.

        Checks:
        - At least one case
        - At most one default case
        - Default must be last
        - Each condition must be Boolean type
        - Validate references and steps in each case block
        """
        if not when.cases:
            self._result.add_error(
                "When block must have at least one case",
                when.location,
            )
            return

        default_count = 0
        default_index = -1
        for i, case in enumerate(when.cases):
            if case.is_default:
                default_count += 1
                default_index = i

        if default_count > 1:
            self._result.add_error(
                "When block can have at most one default case",
                when.location,
            )

        if default_count == 0:
            self._result.add_error(
                "When block must have a default case (case _ =>)",
                when.location,
            )

        if default_count == 1 and default_index != len(when.cases) - 1:
            self._result.add_error(
                "Default case must be the last case in a when block",
                when.location,
            )

        # Validate each case
        input_attrs = {p.name for p in containing_sig.params}
        for case in when.cases:
            if not case.is_default and case.condition is not None:
                # Check that condition type is Boolean (Gap 1: pass step_returns_types)
                condition_type = self._infer_type(case.condition, step_returns_types)
                if condition_type != "Unknown" and condition_type != "Boolean":
                    self._result.add_error(
                        f"When case condition must be Boolean, got {condition_type}",
                        case.location,
                    )
                # Validate references in condition
                for ref in self._extract_references(case.condition):
                    if ref.is_input:
                        if ref.path and ref.path[0] not in input_attrs:
                            self._result.add_error(
                                f"Invalid input reference '$.{ref.path[0]}': "
                                f"no parameter named '{ref.path[0]}'",
                                ref.location,
                            )
                    else:
                        # Gap 3: validate step references in when conditions
                        self._validate_reference(
                            ref,
                            input_attrs,
                            steps or {},
                            step_returns or {},
                            foreach_var=None,
                        )

            # Validate the case block body using a synthetic AndThenBlock
            if case.block:
                synthetic = AndThenBlock(block=case.block)
                self._validate_and_then_block(
                    synthetic,
                    containing_sig,
                    extra_yield_targets,
                    parent_steps=steps,
                    parent_step_returns=step_returns,
                    parent_step_returns_types=step_returns_types,
                )

    def _validate_catch_clause(
        self,
        catch: CatchClause,
        containing_sig: FacetSig,
        extra_yield_targets: set[str] | None = None,
        parent_steps: dict[str, "StepInfo"] | None = None,
        parent_step_returns: dict[str, set[str]] | None = None,
        parent_step_returns_types: dict[str, dict[str, str]] | None = None,
        parent_foreach_var: str | None = None,
    ) -> None:
        """Validate a catch clause.

        For catch when: delegates to _validate_when_block (default required, Boolean conditions).
        For catch block: validates block contents using synthetic AndThenBlock.
        """
        if catch.when:
            self._validate_when_block(
                catch.when,
                containing_sig,
                extra_yield_targets,
                step_returns_types=parent_step_returns_types,
                steps=parent_steps,
                step_returns=parent_step_returns,
            )
        elif catch.block:
            synthetic = AndThenBlock(block=catch.block)
            self._validate_and_then_block(
                synthetic,
                containing_sig,
                extra_yield_targets,
                parent_steps=parent_steps,
                parent_step_returns=parent_step_returns,
                parent_step_returns_types=parent_step_returns_types,
                parent_foreach_var=parent_foreach_var,
            )

    def _extract_references(self, expr) -> list[Reference]:
        """Recursively extract all Reference nodes from an expression tree."""
        if isinstance(expr, Reference):
            return [expr]
        if isinstance(expr, BinaryExpr):
            return self._extract_references(expr.left) + self._extract_references(expr.right)
        if isinstance(expr, ConcatExpr):
            refs = []
            for operand in expr.operands:
                refs.extend(self._extract_references(operand))
            return refs
        if isinstance(expr, ArrayLiteral):
            refs = []
            for element in expr.elements:
                refs.extend(self._extract_references(element))
            return refs
        if isinstance(expr, MapLiteral):
            refs = []
            for entry in expr.entries:
                refs.extend(self._extract_references(entry.value))
            return refs
        if isinstance(expr, IndexExpr):
            return self._extract_references(expr.target) + self._extract_references(expr.index)
        if isinstance(expr, UnaryExpr):
            return self._extract_references(expr.operand)
        return []

    _NUMERIC_TYPES = {"Int", "Long", "Double"}
    _COMPARISON_OPS = {"==", "!=", ">", "<", ">=", "<="}
    _BOOLEAN_OPS = {"&&", "||"}
    _ORDERED_COMPARISON_OPS = {">", "<", ">=", "<="}
    _ARITHMETIC_OPS = {"+", "-", "*", "/", "%"}
    _NON_SCHEMA_TYPES = {
        "String",
        "Int",
        "Long",
        "Double",
        "Boolean",
        "Array",
        "Map",
        "Null",
        "Unknown",
    }

    @classmethod
    def _is_schema_type(cls, type_name: str) -> bool:
        """Return True if the type name refers to a schema (not a primitive/builtin)."""
        return type_name not in cls._NON_SCHEMA_TYPES

    def _resolve_nested_field_type(
        self, current_type: str, remaining_path: list[str], location: SourceLocation | None
    ) -> str:
        """Resolve nested field access through schema types.

        For a path like step.result.count where result is a schema type,
        this resolves 'count' by looking up the schema's field types.
        """
        for field_name in remaining_path:
            if current_type == "Unknown":
                return "Unknown"
            if not self._is_schema_type(current_type):
                return "Unknown"
            # Try resolve via current namespace context first, then fallback
            # to short-name lookup (since _current_namespace may be cleared
            # by the time _infer_type runs during cross-block validation).
            schema_info = self._resolve_schema_name(current_type, location)
            if schema_info is None:
                # Fallback: check short name registry directly
                candidates = self._schemas_by_short_name.get(current_type, [])
                if len(candidates) == 1:
                    schema_info = self._schema_info.get(candidates[0])
            if schema_info is None:
                return "Unknown"
            if field_name not in schema_info.fields_types:
                self._result.add_warning(
                    f"Field '{field_name}' not found on schema '{schema_info.name}'",
                    location,
                )
                return "Unknown"
            current_type = schema_info.fields_types[field_name]
        return current_type

    def _infer_type(self, expr, step_returns_types: dict[str, dict[str, str]] | None = None) -> str:
        """Infer the type of an expression for type checking.

        Returns:
            Type name string: "String", "Int", "Long", "Double", "Boolean", "Null",
            "Array", "Map", a schema type name (e.g. "MySchema"), or "Unknown"
            for unresolvable references and complex expressions.
        """
        if isinstance(expr, Literal):
            if expr.kind == "string":
                return "String"
            elif expr.kind == "integer":
                return "Int"
            elif expr.kind == "boolean":
                return "Boolean"
            elif expr.kind == "null":
                return "Null"
            return "Unknown"
        if isinstance(expr, Reference):
            if expr.is_input and expr.path:
                return self._param_scope.get(expr.path[0], "Unknown")
            # Step references: resolve return type from facet/schema declarations
            if not expr.is_input and len(expr.path) >= 2 and step_returns_types:
                step_name, attr_name = expr.path[0], expr.path[1]
                types = step_returns_types.get(step_name, {})
                field_type = types.get(attr_name, "Unknown")
                # For 3+ segment paths (e.g. step.result.count), resolve through schemas
                if len(expr.path) > 2:
                    return self._resolve_nested_field_type(
                        field_type, expr.path[2:], getattr(expr, "location", None)
                    )
                return field_type
            return "Unknown"
        if isinstance(expr, ConcatExpr):
            return "String"
        if isinstance(expr, BinaryExpr):
            left_type = self._infer_type(expr.left, step_returns_types)
            right_type = self._infer_type(expr.right, step_returns_types)

            # Boolean operators: && ||
            if expr.operator in self._BOOLEAN_OPS:
                if left_type != "Unknown" and left_type != "Boolean":
                    self._result.add_error(
                        f"Type error: operator '{expr.operator}' requires Boolean operands, "
                        f"got {left_type}",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
                if right_type != "Unknown" and right_type != "Boolean":
                    self._result.add_error(
                        f"Type error: operator '{expr.operator}' requires Boolean operands, "
                        f"got {right_type}",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
                return "Boolean"

            # Comparison operators: == != > < >= <=
            if expr.operator in self._COMPARISON_OPS:
                # Ordered comparisons reject Boolean operands
                if expr.operator in self._ORDERED_COMPARISON_OPS:
                    if left_type == "Boolean" or right_type == "Boolean":
                        self._result.add_error(
                            f"Type error: cannot use ordered comparison '{expr.operator}' "
                            f"with Boolean operand",
                            getattr(expr, "location", None),
                        )
                        return "Unknown"
                    for t in (left_type, right_type):
                        if self._is_schema_type(t):
                            self._result.add_error(
                                f"Type error: cannot use ordered comparison '{expr.operator}' "
                                f"with schema type '{t}'",
                                getattr(expr, "location", None),
                            )
                            return "Unknown"
                return "Boolean"

            # Arithmetic operators: + - * / %
            if left_type != "Unknown" and right_type != "Unknown":
                if left_type == "String" or right_type == "String":
                    self._result.add_error(
                        f"Type error: cannot use arithmetic operator '{expr.operator}' "
                        f"with String operand (use '++' for concatenation)",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
                if left_type == "Boolean" or right_type == "Boolean":
                    self._result.add_error(
                        f"Type error: cannot use arithmetic operator '{expr.operator}' "
                        f"with Boolean operand",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
                # Schema types cannot be used in arithmetic
                for t in (left_type, right_type):
                    if self._is_schema_type(t):
                        self._result.add_error(
                            f"Type error: cannot use arithmetic operator '{expr.operator}' "
                            f"with schema type '{t}'",
                            getattr(expr, "location", None),
                        )
                        return "Unknown"
            # If either is Unknown, allow it (runtime will catch errors)
            if left_type in self._NUMERIC_TYPES and right_type in self._NUMERIC_TYPES:
                # Promote to widest type
                if "Double" in (left_type, right_type):
                    return "Double"
                if "Long" in (left_type, right_type):
                    return "Long"
                return "Int"
            return "Unknown"
        if isinstance(expr, UnaryExpr):
            operand_type = self._infer_type(expr.operand, step_returns_types)
            # Logical NOT
            if expr.operator == "!":
                if operand_type != "Unknown" and operand_type != "Boolean":
                    self._result.add_error(
                        f"Type error: operator '!' requires Boolean operand, got {operand_type}",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
                return "Boolean"
            # Arithmetic negation
            if operand_type != "Unknown":
                if operand_type == "String":
                    self._result.add_error(
                        "Type error: cannot negate String operand",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
                if operand_type == "Boolean":
                    self._result.add_error(
                        "Type error: cannot negate Boolean operand",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
                if self._is_schema_type(operand_type):
                    self._result.add_error(
                        f"Type error: cannot negate schema type '{operand_type}'",
                        getattr(expr, "location", None),
                    )
                    return "Unknown"
            if operand_type in self._NUMERIC_TYPES:
                return operand_type
            return "Unknown"
        if isinstance(expr, ArrayLiteral):
            return "Array"
        if isinstance(expr, MapLiteral):
            return "Map"
        if isinstance(expr, IndexExpr):
            return "Unknown"
        return "Unknown"

    def _validate_call_references(
        self,
        call: CallExpr,
        input_attrs: set[str],
        steps: dict[str, StepInfo],
        step_returns: dict[str, set[str]],
        foreach_var: str | None,
        current_step: str | None = None,
        step_returns_types: dict[str, dict[str, str]] | None = None,
        other_block_steps: set[str] | None = None,
    ) -> None:
        """Validate references in a call expression."""
        for arg in call.args:
            for ref in self._extract_references(arg.value):
                self._validate_reference(
                    ref,
                    input_attrs,
                    steps,
                    step_returns,
                    foreach_var,
                    current_step,
                    other_block_steps,
                )
            # Type check expressions
            self._infer_type(arg.value, step_returns_types)

        # Also validate mixin call arguments
        for mixin in call.mixins:
            for arg in mixin.args:
                for ref in self._extract_references(arg.value):
                    self._validate_reference(
                        ref,
                        input_attrs,
                        steps,
                        step_returns,
                        foreach_var,
                        current_step,
                        other_block_steps,
                    )

    def _validate_reference(
        self,
        ref: Reference,
        input_attrs: set[str],
        steps: dict[str, StepInfo],
        step_returns: dict[str, set[str]],
        foreach_var: str | None,
        current_step: str | None = None,
        other_block_steps: set[str] | None = None,
    ) -> None:
        """Validate a single reference."""
        if ref.is_input:
            # $.attr - must reference a valid input parameter
            if ref.path:
                attr = ref.path[0]
                # Allow foreach variable
                if foreach_var and attr == foreach_var:
                    return
                if attr not in input_attrs:
                    self._result.add_error(
                        f"Invalid input reference '$.{attr}': no parameter named '{attr}'",
                        ref.location,
                    )
        else:
            # step.attr - must reference a valid step and attribute
            if not ref.path or len(ref.path) < 2:
                self._result.add_error(
                    "Invalid step reference: must be 'step.attribute'", ref.location
                )
                return

            step_name = ref.path[0]
            attr = ref.path[1]

            # Check if referencing a step that exists
            if step_name not in steps:
                # Could be the foreach variable
                if foreach_var and step_name == foreach_var:
                    return  # Foreach variable references are allowed
                # Check for cross-block step reference
                if other_block_steps and step_name in other_block_steps:
                    self._result.add_error(
                        f"Cross-block step reference: '{step_name}' is defined "
                        f"in a sibling andThen block and cannot be referenced here. "
                        f"Use a step body (e.g. step = Call(...) andThen {{ ... }}) "
                        f"to compose steps that depend on each other.",
                        ref.location,
                    )
                    return
                self._result.add_error(f"Reference to undefined step '{step_name}'", ref.location)
                return

            # Check that we're not referencing a step defined after current step
            if current_step:
                step_names = list(steps.keys())
                if step_name in step_names and current_step in step_names:
                    if step_names.index(step_name) >= step_names.index(current_step):
                        if step_name != current_step or step_names.index(
                            step_name
                        ) > step_names.index(current_step):
                            self._result.add_error(
                                f"Step '{current_step}' cannot reference step '{step_name}' which is not defined before it",
                                ref.location,
                            )
                            return

            # Check if attribute is valid for that step's facet
            returns = step_returns.get(step_name, set())
            if returns and attr not in returns:
                self._result.add_error(
                    f"Invalid attribute '{attr}' for step '{step_name}': "
                    f"valid attributes are {sorted(returns)}",
                    ref.location,
                )

    def _validate_yield(
        self,
        yield_stmt: YieldStmt,
        valid_targets: set[str],
        steps: dict[str, StepInfo],
        step_returns: dict[str, set[str]],
        input_attrs: set[str],
        foreach_var: str | None,
        step_returns_types: dict[str, dict[str, str]] | None = None,
        other_block_steps: set[str] | None = None,
    ) -> None:
        """Validate a yield statement."""
        target = yield_stmt.call.name.split(".")[-1]  # Use short name

        if target not in valid_targets:
            self._result.add_error(
                f"Invalid yield target '{target}': must be the containing facet or one of its mixins. "
                f"Valid targets are: {sorted(valid_targets)}",
                yield_stmt.location,
            )

        # Validate references in yield arguments
        self._validate_call_references(
            yield_stmt.call,
            input_attrs,
            steps,
            step_returns,
            foreach_var,
            step_returns_types=step_returns_types,
            other_block_steps=other_block_steps,
        )

    def _validate_schema_instantiation(self, call: CallExpr, schema_info: SchemaInfo) -> None:
        """Validate a schema instantiation call.

        Checks that:
        1. All provided arguments are valid schema fields
        2. No mixins are used (schemas don't support mixins)

        Args:
            call: The call expression instantiating the schema
            schema_info: Information about the schema being instantiated
        """
        # Check that no mixins are used
        if call.mixins:
            self._result.add_error(
                f"Schema instantiation '{call.name}' cannot have mixins. "
                f"Schemas are simple data structures without mixin support.",
                call.location,
            )

        # Check that all provided arguments are valid schema fields
        for arg in call.args:
            if arg.name not in schema_info.fields:
                self._result.add_error(
                    f"Unknown field '{arg.name}' for schema '{call.name}'. "
                    f"Valid fields are: {sorted(schema_info.fields)}",
                    arg.location,
                )

    def _validate_schema_decl(self, schema: SchemaDecl) -> None:
        """Validate a schema declaration for field name uniqueness and type references."""
        field_names: dict[str, SourceLocation | None] = {}
        for f in schema.fields:
            self._check_name_unique(f.name, f.location, field_names, "schema field")
            # Validate field type reference
            self._validate_type_ref(f.type, f.location)

    def _validate_implicit_decl(self, implicit: ImplicitDecl) -> None:
        """Validate an implicit declaration.

        Checks that:
        - The call target references a known facet
        - The call args match the target facet's parameters
        """
        call = implicit.call
        facet_info = self._resolve_facet_name(call.name, implicit.location)
        if facet_info is None:
            # _resolve_facet_name already reported an error if it was ambiguous;
            # for unknown external facets it returns None silently — skip arg checks
            return

        # Validate that implicit args are valid params of the target facet
        for arg in call.args:
            if arg.name not in facet_info.params:
                self._result.add_error(
                    f"Implicit '{implicit.name}' passes unknown parameter "
                    f"'{arg.name}' to facet '{call.name}'",
                    implicit.location,
                )


def validate(program: Program) -> ValidationResult:
    """Validate a program AST.

    Args:
        program: The Program AST to validate

    Returns:
        ValidationResult containing any errors found
    """
    validator = AFLValidator()
    return validator.validate(program)
