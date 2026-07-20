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

import os
import re
from dataclasses import dataclass, field

from .ast import (
    EnvironmentDecl,
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
    MixinSig,
    NamedArg,
    Namespace,
    Program,
    PromptBlock,
    Reference,
    SchemaDecl,
    ScriptBlock,
    SourceLocation,
    SysAssertStmt,
    SysLogStmt,
    TypeRef,
    UnaryExpr,
    WhenBlock,
    WorkflowDecl,
    YieldStmt,
)


@dataclass
class ValidationError:
    """A semantic validation error or warning.

    ``rule_id`` is a stable machine-readable identifier (e.g. ``REF_UNDEFINED_STEP``)
    that callers can use to look up paired wrong/right examples and repair guidance
    without parsing the human ``message``. ``docs_uri`` points at the canonical
    rule doc; the MCP server resolves these via the ``fw://docs/rules/{rule_id}``
    resource.
    """

    message: str
    rule_id: str = "UNKNOWN"
    severity: str = "error"
    line: int | None = None
    column: int | None = None
    docs_uri: str | None = None
    suggested_fix: str | None = None

    def __str__(self) -> str:
        location = ""
        if self.line is not None:
            location = f" at line {self.line}"
            if self.column is not None:
                location += f", column {self.column}"
        return f"{self.message}{location}"


def _docs_uri_for(rule_id: str) -> str | None:
    if rule_id and rule_id != "UNKNOWN":
        return f"fw://docs/rules/{rule_id}"
    return None


@dataclass
class ValidationResult:
    """Result of validation containing any errors found."""

    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(
        self,
        message: str,
        location: SourceLocation | None = None,
        *,
        rule_id: str = "UNKNOWN",
        suggested_fix: str | None = None,
    ) -> None:
        """Add a validation error."""
        line = location.line if location else None
        column = location.column if location else None
        self.errors.append(
            ValidationError(
                message=message,
                rule_id=rule_id,
                severity="error",
                line=line,
                column=column,
                docs_uri=_docs_uri_for(rule_id),
                suggested_fix=suggested_fix,
            )
        )

    def add_warning(
        self,
        message: str,
        location: SourceLocation | None = None,
        *,
        rule_id: str = "UNKNOWN",
        suggested_fix: str | None = None,
    ) -> None:
        """Add a validation warning."""
        line = location.line if location else None
        column = location.column if location else None
        self.warnings.append(
            ValidationError(
                message=message,
                rule_id=rule_id,
                severity="warning",
                line=line,
                column=column,
                docs_uri=_docs_uri_for(rule_id),
                suggested_fix=suggested_fix,
            )
        )


@dataclass
class FacetInfo:
    """Information about a declared facet for reference validation."""

    name: str
    params: set[str]  # Input parameter names
    returns: set[str]  # Return parameter names
    returns_types: dict[str, str] = field(default_factory=dict)  # Return field name → type
    params_types: dict[str, str] = field(default_factory=dict)  # Param name → type
    mixin_aliases: dict[str, str] = field(default_factory=dict)  # Alias → mixin facet target
    mixin_targets: list[str] = field(
        default_factory=list
    )  # All mixin targets (aliased + un-aliased)
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


@dataclass
class _ContainerFrame:
    """One lexical container in the relative-``$``-scoping stack.

    A frame is pushed for the enclosing facet/workflow (base frame) and for
    each step whose body/clauses we descend into. ``$`` resolves against the
    top frame, ``$$`` one down, ``$$$`` two down, ... (see
    ``docs/architecture/ffl-relative-scoping.md``). Only used when the
    relative-scoping flag is on; the legacy resolver never builds the stack.
    """

    name: str
    kind: str  # 'facet' | 'workflow' | 'step'
    attrs: set[str]  # attribute surface: params ∪ returns
    open: bool = False  # True when unknown extra attrs may exist (pre-script keys)


class FFLValidator:
    """Validates FFL AST for semantic correctness."""

    def __init__(self, relative_scoping: bool | None = None):
        # Relative ``$``-scoping (docs/architecture/ffl-relative-scoping.md).
        # Flag-gated: off = the legacy flat-inputs resolver (current behavior);
        # on = container-relative $/$$ resolution. Default from the environment
        # so the whole toolchain can opt in without code changes.
        if relative_scoping is None:
            # Default ON (relative scoping is the model); explicitly disable with
            # FW_FFL_RELATIVE_SCOPING=0/off for the legacy flat resolver.
            relative_scoping = os.environ.get(
                "FW_FFL_RELATIVE_SCOPING", "on"
            ).strip().lower() not in ("0", "false", "no", "off")
        self._relative_scoping: bool = relative_scoping
        self._container_stack: list[_ContainerFrame] = []
        # Steps visible from an ENCLOSING block (relative-scoping only): under
        # the new model these may NOT be named directly — reach them via $/$$.
        # Same-block steps stay nameable.
        self._enclosing_step_names: set[str] = set()
        self._result: ValidationResult = ValidationResult()
        self._facets: dict[str, FacetInfo] = {}  # All known facets by full name
        self._facets_by_short_name: dict[str, list[str]] = {}  # Short name -> list of full names
        self._namespaces: set[str] = set()  # All namespace names
        self._schemas: dict[str, SourceLocation] = {}  # Schema names by full name (for uniqueness)
        self._environments: dict[str, "EnvironmentDecl"] = {}  # Environment decls by full name
        self._schema_info: dict[str, SchemaInfo] = {}  # Full name -> SchemaInfo
        self._schemas_by_short_name: dict[str, list[str]] = {}  # Short name -> [full names]
        self._current_namespace: str = ""
        self._current_imports: set[str] = set()  # Namespaces imported via 'use'
        self._param_scope: dict[str, str] = {}  # Parameter name -> inferred type
        self._facet_param_types: dict[
            str, str
        ] = {}  # Param name -> facet type name (step-ref params only)

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
        self._environments = {}
        self._schema_info = {}
        self._schemas_by_short_name = {}
        self._current_namespace = ""
        self._current_imports = set()
        self._param_scope = {}
        self._facet_param_types = {}
        self._container_stack = []
        self._enclosing_step_names = set()

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
        # Top-level environments are not allowed - emit error
        for env in program.environments:
            self._result.add_error(
                f"Environment '{env.name}' must be defined inside a namespace. "
                f"Top-level environments are not allowed.",
                env.location,
                rule_id="ENV_AT_TOP_LEVEL",
            )
        # Top-level schemas are not allowed - emit error
        for schema in program.schemas:
            self._result.add_error(
                f"Schema '{schema.name}' must be defined inside a namespace. "
                f"Top-level schemas are not allowed.",
                schema.location,
                rule_id="SCHEMA_AT_TOP_LEVEL",
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
            for env in namespace.environments:
                self._environments[f"{namespace.name}.{env.name}"] = env

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
        params_types: dict[str, str] = {}
        for p in sig.params:
            params_types[p.name] = self._type_ref_to_str(p.type)
        returns = {p.name for p in sig.returns.params} if sig.returns else set()
        returns_types: dict[str, str] = {}
        if sig.returns:
            for p in sig.returns.params:
                returns_types[p.name] = self._type_ref_to_str(p.type)
        mixin_aliases: dict[str, str] = {}
        mixin_targets: list[str] = []
        for m in sig.mixins:
            if m.alias:
                mixin_aliases[m.alias] = m.name
            mixin_targets.append(m.name)
        self._facets[full_name] = FacetInfo(
            name=sig.name,
            params=params,
            returns=returns,
            returns_types=returns_types,
            params_types=params_types,
            mixin_aliases=mixin_aliases,
            mixin_targets=mixin_targets,
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
                rule_id="REF_AMBIGUOUS_SCHEMA",
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
                rule_id="REF_AMBIGUOUS_SCHEMA",
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

        # A param/return may also be typed as a facet (step-reference): the
        # value passed is a whole step, and the consumer reads its fields via
        # `$.param.field` or `ctx.fetch_step(ref)`. Accept any known facet
        # short name or qualified name.
        if type_name in self._facets or type_name in self._facets_by_short_name:
            return

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
                    rule_id="REF_UNKNOWN_TYPE",
                )
            elif "." in type_name and type_name not in self._schema_info:
                # Qualified name not found
                self._result.add_error(
                    f"Unknown schema '{type_name}': no schema found with this qualified name.",
                    type_node.location or location,
                    rule_id="REF_UNKNOWN_SCHEMA",
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

        # Validate mixin aliases: must not collide with the primary facet's
        # own params, returns, or another mixin's alias.  The alias becomes
        # an accessible name on a FacetRef consumer (e.g. `$.fref.alias.field`)
        # so it shares the consumer-side namespace with primary attributes.
        self._validate_mixin_aliases(sig)

    def _validate_mixin_aliases(self, sig: FacetSig) -> None:
        """Reject mixin alias collisions on a facet signature."""
        param_names = {p.name for p in sig.params}
        return_names = {p.name for p in sig.returns.params} if sig.returns else set()
        seen_aliases: dict[str, MixinSig] = {}
        for mixin in sig.mixins:
            alias = mixin.alias
            if not alias:
                continue
            if alias in param_names:
                self._result.add_error(
                    f"Mixin alias '{alias}' on facet '{sig.name}' conflicts "
                    f"with a parameter of the same name. Aliases share the "
                    f"consumer-side namespace with primary attributes.",
                    mixin.location or sig.location,
                    rule_id="MIXIN_ALIAS_NAME_CONFLICT",
                )
                continue
            if alias in return_names:
                self._result.add_error(
                    f"Mixin alias '{alias}' on facet '{sig.name}' conflicts "
                    f"with a return field of the same name.",
                    mixin.location or sig.location,
                    rule_id="MIXIN_ALIAS_NAME_CONFLICT",
                )
                continue
            if alias in seen_aliases:
                self._result.add_error(
                    f"Duplicate mixin alias '{alias}' on facet '{sig.name}' "
                    f"(also used by 'with {seen_aliases[alias].name}').",
                    mixin.location or sig.location,
                    rule_id="MIXIN_ALIAS_NAME_CONFLICT",
                )
                continue
            seen_aliases[alias] = mixin

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
            self._result.add_error(
                f"Unknown facet '{name}'",
                location,
                rule_id="REF_UNKNOWN_FACET",
            )
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
                rule_id="REF_AMBIGUOUS_FACET",
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
                    rule_id="REF_AMBIGUOUS_FACET",
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
                rule_id="REF_AMBIGUOUS_FACET",
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
            self._result.add_error(
                f"Workflow '{workflow.sig.name}' must be declared inside a namespace",
                workflow.location,
                rule_id="WORKFLOW_AT_TOP_LEVEL",
            )
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

    def _validate_environment_decl(self, env: "EnvironmentDecl") -> None:
        """Validate an environment declaration."""
        if not env.language:
            self._result.add_error(
                f"Environment '{env.name}' must declare a language "
                f'(e.g. language = "python")',
                env.location,
                rule_id="ENV_MISSING_LANGUAGE",
            )

    def _resolve_environment(self, name: str) -> "EnvironmentDecl | None":
        """Resolve an environment reference (local, imported, or qualified)."""
        if "." in name:
            return self._environments.get(name)
        if self._current_namespace:
            local = self._environments.get(f"{self._current_namespace}.{name}")
            if local is not None:
                return local
        for ns in self._current_imports:
            imported = self._environments.get(f"{ns}.{name}")
            if imported is not None:
                return imported
        return None

    def _check_declaration_environment(self, decl) -> None:
        """Validate an ``in environment`` reference on a declaration.

        Checks the environment exists (ENV_UNKNOWN) and that every script
        block on the declaration agrees with its language
        (ENV_LANGUAGE_SCRIPT_MISMATCH). A bare ``script { … }`` block is
        implicitly python, so binding it to a non-python environment is a
        real mismatch, not a technicality.
        """
        env_name = getattr(decl, "environment", None)
        if not env_name:
            return
        env = self._resolve_environment(env_name)
        if env is None:
            self._result.add_error(
                f"Unknown environment '{env_name}' — no environment declaration "
                f"with this name is visible from namespace "
                f"'{self._current_namespace}'",
                decl.location,
                rule_id="ENV_UNKNOWN",
            )
            return
        if not env.language:
            return  # ENV_MISSING_LANGUAGE already reported on the declaration
        scripts = []
        if getattr(decl, "pre_script", None) is not None:
            scripts.append(decl.pre_script)
        body = getattr(decl, "body", None)
        bodies = body if isinstance(body, list) else ([body] if body else [])
        for blk in bodies:
            script = getattr(blk, "script", None)
            if script is not None:
                scripts.append(script)
        for script in scripts:
            if script.language and script.language.lower() != env.language.lower():
                self._result.add_error(
                    f"Script language '{script.language}' does not match "
                    f"environment '{env_name}' (language '{env.language}')",
                    script.location or decl.location,
                    rule_id="ENV_LANGUAGE_SCRIPT_MISMATCH",
                )

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
                    rule_id="USE_UNKNOWN_NAMESPACE",
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
        for env in namespace.environments:
            self._check_name_unique(env.name, env.location, names, "environment")
            self._validate_environment_decl(env)

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
            self._result.add_error(
                f"Duplicate {kind} name '{name}'{prev_line}",
                location,
                rule_id="DUPLICATE_NAME",
            )
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

    def _build_facet_param_scope(self, sig: FacetSig) -> dict[str, str]:
        """Build a param name -> facet type name map for step-ref params.

        Only retains parameters whose declared type resolves to a known
        facet (`ds: SomeFacet`).  Consumers can dereference them via
        `$.ds.attr` or `$.ds.alias.attr` — Phase C validates those paths
        against this map.
        """
        scope: dict[str, str] = {}
        for param in sig.params:
            if not isinstance(param.type, TypeRef):
                continue
            name = param.type.name
            if not name or name in self.BUILTIN_TYPES:
                continue
            # Only step-ref params — type is a known facet (not a schema).
            if self._lookup_facet_for_type(name) is not None:
                scope[param.name] = name
        return scope

    @staticmethod
    def _sig_attr_names(sig: FacetSig) -> set[str]:
        """A signature's attribute surface: param names ∪ return names."""
        names = {p.name for p in sig.params}
        if sig.returns:
            names |= {p.name for p in sig.returns.params}
        return names

    def _validate_body(self, body, sig: FacetSig, has_pre_script: bool = False) -> None:
        """Validate a body, handling single or list of AndThenBlocks."""
        if self._relative_scoping:
            # Base frame: the enclosing facet/workflow. Its top-level andThen
            # blocks all resolve $ against this frame. `open` when a pre-script
            # may inject extra $. keys we can't enumerate statically.
            self._container_stack.append(
                _ContainerFrame(
                    name=sig.name,
                    kind="facet",
                    attrs=self._sig_attr_names(sig),
                    open=has_pre_script,
                )
            )
            try:
                self._validate_body_inner(body, sig)
            finally:
                self._container_stack.pop()
            return
        self._validate_body_inner(body, sig)

    def _validate_body_inner(self, body, sig: FacetSig) -> None:
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
        self._check_declaration_environment(decl)
        # Validate type references in signature
        self._validate_signature_types(decl.sig)
        # Validate mixin references in signature
        for mixin in decl.sig.mixins:
            self._resolve_facet_name(mixin.name, mixin.location)
        self._param_scope = self._build_param_scope(decl.sig)
        self._facet_param_types = self._build_facet_param_scope(decl.sig)
        if decl.pre_script:
            self._validate_script_block(decl.pre_script, decl.sig)
        if decl.body:
            self._validate_body(decl.body, decl.sig, has_pre_script=bool(decl.pre_script))
        if decl.catch:
            self._validate_catch_clause(decl.catch, decl.sig)
        self._param_scope = {}
        self._facet_param_types = {}

    def _validate_event_facet_decl(self, decl: EventFacetDecl) -> None:
        """Validate an event facet declaration."""
        self._check_declaration_environment(decl)
        # Validate type references in signature
        self._validate_signature_types(decl.sig)
        # Validate mixin references in signature
        for mixin in decl.sig.mixins:
            self._resolve_facet_name(mixin.name, mixin.location)
        self._param_scope = self._build_param_scope(decl.sig)
        self._facet_param_types = self._build_facet_param_scope(decl.sig)
        if decl.pre_script:
            self._validate_script_block(decl.pre_script, decl.sig)
        if decl.body:
            if isinstance(decl.body, PromptBlock):
                self._validate_prompt_block(decl.body, decl.sig)
            else:
                self._validate_body(decl.body, decl.sig, has_pre_script=bool(decl.pre_script))
        if decl.catch:
            self._validate_catch_clause(decl.catch, decl.sig)
        self._param_scope = {}
        self._facet_param_types = {}

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
                rule_id="PROMPT_MISSING_TEMPLATE",
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
                        rule_id="PROMPT_INVALID_PLACEHOLDER",
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
                rule_id="SCRIPT_EMPTY",
            )
            return

        if block.language not in ("python",):
            self._result.add_error(
                f"Unsupported script language '{block.language}'. "
                f"Currently only 'python' is supported.",
                block.location,
                rule_id="SCRIPT_UNSUPPORTED_LANGUAGE",
            )

    def _validate_workflow_decl(self, decl: WorkflowDecl) -> None:
        """Validate a workflow declaration."""
        self._check_declaration_environment(decl)
        # Validate type references in signature
        self._validate_signature_types(decl.sig)
        # Validate mixin references in signature
        for mixin in decl.sig.mixins:
            self._resolve_facet_name(mixin.name, mixin.location)
        self._param_scope = self._build_param_scope(decl.sig)
        self._facet_param_types = self._build_facet_param_scope(decl.sig)
        if decl.pre_script:
            self._validate_script_block(decl.pre_script, decl.sig)
        if decl.body:
            self._validate_body(decl.body, decl.sig, has_pre_script=bool(decl.pre_script))
        if decl.catch:
            self._validate_catch_clause(decl.catch, decl.sig)
        self._param_scope = {}
        self._facet_param_types = {}

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
        if not self._relative_scoping:
            return self._validate_and_then_block_impl(
                body,
                containing_sig,
                extra_yield_targets,
                other_block_steps,
                parent_steps,
                parent_step_returns,
                parent_step_returns_types,
                parent_foreach_var,
            )
        # Relative scoping: steps reachable only from an enclosing block become
        # non-nameable here (reach them via $/$$). Same-block steps stay named.
        prev_enclosing = self._enclosing_step_names
        self._enclosing_step_names = set(parent_steps or {})
        try:
            return self._validate_and_then_block_impl(
                body,
                containing_sig,
                extra_yield_targets,
                other_block_steps,
                parent_steps,
                parent_step_returns,
                parent_step_returns_types,
                parent_foreach_var,
            )
        finally:
            self._enclosing_step_names = prev_enclosing

    def _validate_and_then_block_impl(
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
                other_block_steps=other_block_steps,
            )
            return

        if not body.block:
            return

        # Get valid input references (parameters of containing facet)
        input_attrs = {p.name for p in containing_sig.params}

        # Get valid yield targets: the containing facet name, every
        # mixin's short target name, and every declared alias.  Aliases
        # are the disambiguated form authors must use when the same
        # target appears twice (see YIELD_TARGET_AMBIGUOUS below).
        valid_yield_targets = {containing_sig.name}
        # Count mixin short names so we can flag ambiguous bare-target yields.
        mixin_target_counts: dict[str, int] = {}
        for mixin in containing_sig.mixins:
            short = mixin.name.split(".")[-1]
            valid_yield_targets.add(short)
            mixin_target_counts[short] = mixin_target_counts.get(short, 0) + 1
            if mixin.alias:
                valid_yield_targets.add(mixin.alias)
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

        # Validate a foreach STEP-collection reference (`foreach v in step.field`).
        # The collection is evaluated at block entry — before the loop var binds
        # and before any same-block step runs — so a step reference here may only
        # name a step visible in THIS block's scope (or an enclosing one), never a
        # sibling andThen block's step. Routing it through _validate_reference with
        # `parent_foreach_var` (not this block's new var) and this block's
        # `steps`/`other_block_steps` gives the same REF_CROSS_BLOCK_STEP /
        # REF_UNDEFINED_STEP checks statement refs get. Without this the collection
        # ref bypassed cross-block checking entirely, so
        # `andThen { s = F() } andThen foreach v in s.items { … }` compiled even
        # though `s` is out of scope here.
        #
        # Only STEP references are checked, not `$.` input refs: a `$.attr`
        # collection may name a pre-script-produced attribute (added to the `$.`
        # scope at runtime, grammar.md pre-script rule) that isn't a declared
        # param, and pre-script result keys aren't statically known — validating
        # them here would false-positive on legitimate `foreach x in $.derived`.
        # (Under relative scoping the resolver checks `$.` collections too — the
        # step frame's surface is fully known, and an `open` base frame stays
        # lenient about pre-script keys, so no false-positive.)
        if body.foreach and (self._relative_scoping or not body.foreach.iterable.is_input):
            self._validate_reference(
                body.foreach.iterable,
                input_attrs,
                steps,
                step_returns,
                parent_foreach_var,
                current_step=None,
                other_block_steps=other_block_steps,
            )

        # If foreach, add the iteration variable; inherit from parent if not set
        foreach_var: str | None = None
        if body.foreach:
            foreach_var = body.foreach.variable
        elif parent_foreach_var:
            foreach_var = parent_foreach_var

        # Validate each step.  body.block.steps is a flat list that may
        # interleave StepStmt, SysLogStmt, and SysAssertStmt; the
        # diagnostic statements are validated separately below and
        # skipped in the main step-iteration logic (they have no
        # ``name`` or ``call`` field).
        for step in body.block.steps:
            if isinstance(step, (SysLogStmt, SysAssertStmt)):
                self._validate_sys_stmt(
                    step,
                    input_attrs,
                    steps,
                    step_returns,
                    foreach_var,
                    step_returns_types,
                    other_block_steps,
                )
                continue
            # Check step name uniqueness
            if step.name in steps:
                prev = steps[step.name]
                prev_line = (
                    f" (previously defined at line {prev.location.line})"
                    if prev.location and prev.location.line
                    else ""
                )
                self._result.add_error(
                    f"Duplicate step name '{step.name}'{prev_line}",
                    step.location,
                    rule_id="DUPLICATE_STEP_NAME",
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

            # Relative scoping: a step body / catch descends into the step's
            # own container frame — inside it, $ = this step (its params ∪
            # returns), $$ = this block's container. Push once, covering both
            # the body and the catch clause.
            step_frame_pushed = False
            if self._relative_scoping and (step.body or step.catch or step.extra_bodies):
                finfo = self._resolve_facet_name(step.call.name)
                step_attrs = (
                    (finfo.params | finfo.returns)
                    if finfo
                    else set(step_returns.get(step.name, set()))
                )
                # A catch clause exposes the caught step's synthetic error
                # attributes on $ (the step). Harmless for a plain body (which
                # never references them).
                if step.catch:
                    step_attrs = step_attrs | {"error", "error_type"}
                self._container_stack.append(
                    _ContainerFrame(name=step.name, kind="step", attrs=step_attrs, open=False)
                )
                step_frame_pushed = True
            try:
                # Validate the step's andThen clauses. A step may chain several
                # (`s = F() andThen {…} andThen foreach … andThen when …`); each
                # is a co-clause sharing $ = the step, and — like sibling andThen
                # blocks — a co-clause may not name another co-clause's steps.
                step_clauses = ([step.body] if step.body else []) + list(step.extra_bodies)
                if step_clauses:
                    step_target = step.call.name.split(".")[-1]
                    clause_step_names = [self._collect_block_step_names(c) for c in step_clauses]
                    for i, clause in enumerate(step_clauses):
                        co_clause_steps: set[str] = set()
                        for j, names in enumerate(clause_step_names):
                            if j != i:
                                co_clause_steps |= names
                        self._validate_and_then_block(
                            clause,
                            containing_sig,
                            extra_yield_targets={step_target},
                            # Co-clauses of the same step are siblings to each
                            # other (cross-clause refs are rejected), mirroring
                            # sibling andThen blocks.
                            other_block_steps=co_clause_steps or None,
                            parent_steps=steps,
                            parent_step_returns=step_returns,
                            parent_step_returns_types=step_returns_types,
                            # A nested step body sits inside the enclosing block's
                            # scope, so an enclosing `foreach` loop variable stays
                            # visible here (loop vars share the workflow-input
                            # scope, which nested bodies inherit). Without this the
                            # loop var is flagged as an unknown parameter one level
                            # into an `andThen { }` body.
                            parent_foreach_var=foreach_var,
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
            finally:
                if step_frame_pushed:
                    self._container_stack.pop()

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
                containing_sig=containing_sig,
                mixin_target_counts=mixin_target_counts,
            )
            target = yield_stmt.call.name.split(".")[-1]
            if target in yield_targets_used:
                self._result.add_error(
                    f"Duplicate yield target '{target}': each yield must reference a different facet or mixin",
                    yield_stmt.location,
                    rule_id="YIELD_DUPLICATE_TARGET",
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
        other_block_steps: set[str] | None = None,
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
                rule_id="WHEN_NO_CASES",
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
                rule_id="WHEN_MULTIPLE_DEFAULTS",
            )

        if default_count == 0:
            self._result.add_error(
                "When block must have a default case (case _ =>)",
                when.location,
                rule_id="WHEN_MISSING_DEFAULT",
            )

        if default_count == 1 and default_index != len(when.cases) - 1:
            self._result.add_error(
                "Default case must be the last case in a when block",
                when.location,
                rule_id="WHEN_DEFAULT_NOT_LAST",
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
                        rule_id="WHEN_CONDITION_NOT_BOOLEAN",
                    )
                # Validate references in condition
                for ref in self._extract_references(case.condition):
                    if self._relative_scoping:
                        # $ = the container the when is attached to; step refs
                        # obey the universal no-outside-block rule (a top-level
                        # when's sibling refs surface as REF_CROSS_BLOCK_STEP).
                        self._validate_reference(
                            ref,
                            input_attrs,
                            steps or {},
                            step_returns or {},
                            foreach_var=None,
                            other_block_steps=other_block_steps,
                        )
                    elif ref.is_input:
                        if ref.path and ref.path[0] not in input_attrs:
                            self._result.add_error(
                                f"Invalid input reference '$.{ref.path[0]}': "
                                f"no parameter named '{ref.path[0]}'",
                                ref.location,
                                rule_id="REF_INVALID_INPUT",
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
                    other_block_steps=other_block_steps,
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
        # A facet/workflow-level catch is validated outside _validate_body, so
        # under relative scoping the base container frame isn't on the stack yet.
        # Push it so $ resolves to the containing facet/workflow. (Step-level
        # catches already run with the step frame pushed → stack non-empty.)
        if self._relative_scoping and not self._container_stack:
            self._container_stack.append(
                _ContainerFrame(
                    name=containing_sig.name,
                    kind="facet",
                    # A declaration-level catch also sees the synthetic error
                    # attributes ($.error / $.error_type) — the runtime stores
                    # them as returns on the caught step (CatchBeginHandler).
                    attrs=self._sig_attr_names(containing_sig) | {"error", "error_type"},
                    open=False,
                )
            )
            try:
                self._validate_catch_clause_impl(
                    catch,
                    containing_sig,
                    extra_yield_targets,
                    parent_steps,
                    parent_step_returns,
                    parent_step_returns_types,
                    parent_foreach_var,
                )
            finally:
                self._container_stack.pop()
            return
        self._validate_catch_clause_impl(
            catch,
            containing_sig,
            extra_yield_targets,
            parent_steps,
            parent_step_returns,
            parent_step_returns_types,
            parent_foreach_var,
        )

    def _validate_catch_clause_impl(
        self,
        catch: CatchClause,
        containing_sig: FacetSig,
        extra_yield_targets: set[str] | None = None,
        parent_steps: dict[str, "StepInfo"] | None = None,
        parent_step_returns: dict[str, set[str]] | None = None,
        parent_step_returns_types: dict[str, dict[str, str]] | None = None,
        parent_foreach_var: str | None = None,
    ) -> None:
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
    # Containment operators: left ∈ right (or vice-versa for `contains`).
    # Right side must be a collection (array) or string (substring search).
    _CONTAINMENT_OPS = {"in", "not in", "contains"}
    # String-match operators: both operands must be String.
    _STRING_MATCH_OPS = {"startsWith", "endsWith"}
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
                    rule_id="SCHEMA_UNKNOWN_FIELD",
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
                        rule_id="TYPE_BOOLEAN_OPERAND_REQUIRED",
                    )
                    return "Unknown"
                if right_type != "Unknown" and right_type != "Boolean":
                    self._result.add_error(
                        f"Type error: operator '{expr.operator}' requires Boolean operands, "
                        f"got {right_type}",
                        getattr(expr, "location", None),
                        rule_id="TYPE_BOOLEAN_OPERAND_REQUIRED",
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
                            rule_id="TYPE_ORDERED_COMPARISON_BOOLEAN",
                        )
                        return "Unknown"
                    for t in (left_type, right_type):
                        if self._is_schema_type(t):
                            self._result.add_error(
                                f"Type error: cannot use ordered comparison '{expr.operator}' "
                                f"with schema type '{t}'",
                                getattr(expr, "location", None),
                                rule_id="TYPE_ORDERED_COMPARISON_SCHEMA",
                            )
                            return "Unknown"
                return "Boolean"

            # Containment operators: in / not in / contains.  Right
            # side (or left for `contains`) must be a collection or
            # string; the other operand is the element.  We don't
            # have an Array-of-T type in the validator scope today,
            # so the check is permissive: reject only obviously
            # malformed cases (e.g. Boolean as collection).
            if expr.operator in self._CONTAINMENT_OPS:
                collection_side = left_type if expr.operator == "contains" else right_type
                if collection_side == "Boolean":
                    self._result.add_error(
                        f"Type error: operator '{expr.operator}' requires a collection or "
                        f"String operand, got Boolean",
                        getattr(expr, "location", None),
                        rule_id="TYPE_CONTAINMENT_OPERAND",
                    )
                    return "Unknown"
                return "Boolean"

            # String-match operators: startsWith / endsWith — both
            # operands must be String.
            if expr.operator in self._STRING_MATCH_OPS:
                for t in (left_type, right_type):
                    if t != "Unknown" and t != "String":
                        self._result.add_error(
                            f"Type error: operator '{expr.operator}' requires String "
                            f"operands, got {t}",
                            getattr(expr, "location", None),
                            rule_id="TYPE_STRING_MATCH_OPERAND",
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
                        rule_id="TYPE_ARITHMETIC_STRING",
                    )
                    return "Unknown"
                if left_type == "Boolean" or right_type == "Boolean":
                    self._result.add_error(
                        f"Type error: cannot use arithmetic operator '{expr.operator}' "
                        f"with Boolean operand",
                        getattr(expr, "location", None),
                        rule_id="TYPE_ARITHMETIC_BOOLEAN",
                    )
                    return "Unknown"
                # Schema types cannot be used in arithmetic
                for t in (left_type, right_type):
                    if self._is_schema_type(t):
                        self._result.add_error(
                            f"Type error: cannot use arithmetic operator '{expr.operator}' "
                            f"with schema type '{t}'",
                            getattr(expr, "location", None),
                            rule_id="TYPE_ARITHMETIC_SCHEMA",
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
                        rule_id="TYPE_NOT_OPERAND_REQUIRED",
                    )
                    return "Unknown"
                return "Boolean"
            # Arithmetic negation
            if operand_type != "Unknown":
                if operand_type == "String":
                    self._result.add_error(
                        "Type error: cannot negate String operand",
                        getattr(expr, "location", None),
                        rule_id="TYPE_NEGATE_STRING",
                    )
                    return "Unknown"
                if operand_type == "Boolean":
                    self._result.add_error(
                        "Type error: cannot negate Boolean operand",
                        getattr(expr, "location", None),
                        rule_id="TYPE_NEGATE_BOOLEAN",
                    )
                    return "Unknown"
                if self._is_schema_type(operand_type):
                    self._result.add_error(
                        f"Type error: cannot negate schema type '{operand_type}'",
                        getattr(expr, "location", None),
                        rule_id="TYPE_NEGATE_SCHEMA",
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
        target_facet = self._lookup_facet_for_type(call.name)
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
            self._check_step_ref_facet_type(arg, target_facet, steps)

        # Also validate mixin call arguments
        for mixin in call.mixins:
            mixin_facet = self._lookup_facet_for_type(mixin.name)
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
                self._check_step_ref_facet_type(arg, mixin_facet, steps)

    def _validate_facet_ref_attr_path(self, ref: Reference, param_name: str) -> None:
        """Validate `$.<facetRef>.<attr>[.<sub>]*` against the param's facet.

        For a step-ref param `param_name: <FacetType>`:
          - The next path segment must be one of <FacetType>'s
            params, returns, or mixin aliases.
          - If it matches a mixin alias, one further segment must be
            on the mixin facet's params/returns/aliases.
          - Segments past that level are resolved through the type of
            the previously-matched attribute (via
            ``_resolve_nested_field_type``), the same way a normal
            ``step.result.<field>`` schema chain is validated.

        Mixins on the param's facet that have *no* alias are
        unaddressable through a FacetRef — by design, so the
        consumer-side namespace is unambiguous.
        """
        facet_type = self._facet_param_types.get(param_name)
        if not facet_type:
            return
        facet = self._lookup_facet_for_type(facet_type)
        if facet is None:
            return  # facet type unresolvable; other rules report this

        segment = ref.path[1]
        # Resolve the first segment against params ∪ returns ∪ mixin_aliases.
        attr_type, deeper_path_base = self._resolve_facet_ref_first_segment(
            facet, segment, param_name, ref
        )
        if attr_type is None:
            return  # error already emitted (or alias->mixin needs sub-segment)

        # Deeper-path validation — chain through schema fields, transitive
        # FacetRefs, or aliased mixin sub-attributes.
        for i in range(2, len(ref.path)):
            sub = ref.path[i]
            base = deeper_path_base
            attr_type, deeper_path_base = self._resolve_facet_ref_deep_segment(
                attr_type, sub, base, ref
            )
            if attr_type is None:
                return

    def _resolve_facet_ref_first_segment(
        self,
        facet: FacetInfo,
        segment: str,
        param_name: str,
        ref: Reference,
    ) -> tuple[str | None, str]:
        """Validate the segment immediately after ``$.<param>``.

        Returns ``(type_of_segment, base_path_for_errors)`` on success,
        or ``(None, _)`` on a definitive error.  When the segment is a
        mixin alias, the second-level segment is also validated here so
        the caller can resume deep-path resolution against the mixin
        attribute's type.
        """
        base = f"$.{param_name}.{segment}"
        if segment in facet.returns:
            return (facet.returns_types.get(segment, "Unknown"), base)
        if segment in facet.params:
            return (facet.params_types.get(segment, "Unknown"), base)
        if segment in facet.mixin_aliases:
            mixin_type = facet.mixin_aliases[segment]
            mixin = self._lookup_facet_for_type(mixin_type)
            if mixin is None:
                return (None, base)
            # The alias needs a sub-attribute to be useful; if the path
            # ends at the alias, that's a partial reference and another
            # rule path will report it as a type error.
            if len(ref.path) < 3:
                return (None, base)
            sub = ref.path[2]
            sub_type = mixin.returns_types.get(sub) or mixin.params_types.get(sub)
            if (
                sub not in mixin.params
                and sub not in mixin.returns
                and sub not in mixin.mixin_aliases
            ):
                valid = sorted(mixin.params | mixin.returns | set(mixin.mixin_aliases))
                self._result.add_error(
                    f"Invalid mixin attribute '{base}.{sub}': "
                    f"mixin facet '{mixin.name}' has no param, return, or "
                    f"mixin alias named '{sub}'. Valid names: {valid}",
                    ref.location,
                    rule_id="REF_INVALID_FACET_REF_ATTRIBUTE",
                )
                return (None, base)
            # Skip the alias-substep level for the caller's deep loop —
            # it has already validated `ref.path[2]`. The caller's loop
            # starts at index 2, so signal that by returning a base that
            # already includes the sub.  We do this by returning the
            # mixin attribute's type but a base path that already covers
            # `ref.path[:3]`.  The caller's loop will then advance one
            # more iteration than needed; to keep loop logic simple we
            # let it run and treat unknown types as no-ops.
            return (sub_type or "Unknown", f"{base}.{sub}")
        valid = sorted(facet.params | facet.returns | set(facet.mixin_aliases))
        self._result.add_error(
            f"Invalid FacetRef attribute '$.{param_name}.{segment}': "
            f"facet '{facet.name}' has no param, return, or mixin alias "
            f"named '{segment}'. Valid names: {valid}",
            ref.location,
            rule_id="REF_INVALID_FACET_REF_ATTRIBUTE",
        )
        return (None, base)

    def _resolve_facet_ref_deep_segment(
        self,
        current_type: str,
        segment: str,
        base: str,
        ref: Reference,
    ) -> tuple[str | None, str]:
        """Walk one segment of a deep `$.fref.<...>.<segment>` path.

        Resolves ``segment`` against the current type:
          - If the type is a schema, look the segment up on the
            schema's fields.
          - If the type is itself a facet, treat segments transitively
            (params ∪ returns ∪ aliases).
          - Unknown/primitive/collection types: don't error (we don't
            have enough info), just return Unknown so the loop ends.
        """
        if current_type == "Unknown" or not current_type:
            return ("Unknown", f"{base}.{segment}")
        # Transitive FacetRef
        inner_facet = self._lookup_facet_for_type(current_type)
        if inner_facet is not None:
            if (
                segment in inner_facet.params
                or segment in inner_facet.returns
                or segment in inner_facet.mixin_aliases
            ):
                next_type = (
                    inner_facet.returns_types.get(segment)
                    or inner_facet.params_types.get(segment)
                    or "Unknown"
                )
                return (next_type, f"{base}.{segment}")
            valid = sorted(
                inner_facet.params | inner_facet.returns | set(inner_facet.mixin_aliases)
            )
            self._result.add_error(
                f"Invalid FacetRef attribute '{base}.{segment}': "
                f"facet '{inner_facet.name}' has no param, return, or "
                f"mixin alias named '{segment}'. Valid names: {valid}",
                ref.location,
                rule_id="REF_INVALID_FACET_REF_ATTRIBUTE",
            )
            return (None, base)
        # Schema chain — reuse the existing helper.
        if self._is_schema_type(current_type):
            next_type = self._resolve_nested_field_type(current_type, [segment], ref.location)
            return (next_type, f"{base}.{segment}")
        # Primitive / collection — nothing further to check.
        return ("Unknown", f"{base}.{segment}")

    def _lookup_facet_for_type(self, type_name: str) -> FacetInfo | None:
        """Resolve a type name to a FacetInfo without emitting errors.

        Returns the FacetInfo only when the name is an unambiguous reference
        to a known facet. Used to identify step-reference param types.
        """
        if not type_name or type_name in self.BUILTIN_TYPES or type_name == "Unknown":
            return None
        if type_name in self._facets:
            return self._facets[type_name]
        matches = self._facets_by_short_name.get(type_name, [])
        if len(matches) == 1:
            return self._facets[matches[0]]
        return None

    def _check_step_ref_facet_type(
        self,
        arg: NamedArg,
        target_facet: FacetInfo | None,
        steps: dict[str, StepInfo],
    ) -> None:
        """Flag step-ref args whose source facet differs from the param's
        declared facet type. Fires STEP_REF_FACET_MISMATCH when:

        - the target param's type is a known facet (step-reference param),
        - the arg value is a bare step ref (e.g. ``ds = s1``), and
        - the referenced step's facet doesn't satisfy the expected
          facet — i.e. it isn't the same facet AND doesn't declare the
          expected facet as one of its mixins (transitively).
        """
        if target_facet is None:
            return
        expected_type = target_facet.params_types.get(arg.name, "")
        expected_facet = self._lookup_facet_for_type(expected_type)
        if expected_facet is None:
            return
        value = arg.value
        if not isinstance(value, Reference):
            return
        if value.is_input or len(value.path) != 1:
            return
        source = steps.get(value.path[0])
        if source is None:
            return
        source_facet = self._lookup_facet_for_type(source.facet_name)
        if source_facet is None:
            return
        if self._facet_satisfies(source_facet, expected_facet):
            return
        self._result.add_error(
            f"Step '{source.name}' is a '{source_facet.name}', "
            f"but parameter '{arg.name}' expects a step of facet "
            f"'{expected_facet.name}' (or one whose signature mixes in "
            f"'{expected_facet.name}')",
            value.location,
            rule_id="STEP_REF_FACET_MISMATCH",
        )

    def _facet_satisfies(
        self, source: FacetInfo, expected: FacetInfo, _seen: set[str] | None = None
    ) -> bool:
        """Mixin-compatible identity check.

        Returns True when ``source`` IS-A ``expected``:
          - same facet (identity), or
          - ``source`` declares ``expected`` as one of its signature
            mixins (aliased or un-aliased), or
          - any of ``source``'s mixin targets transitively satisfies
            ``expected`` (one facet's mixin being itself composed of
            further mixins).

        The transitive check has a per-call ``_seen`` set to guard
        against accidental cycles in malformed sources.
        """
        if source is expected:
            return True
        if _seen is None:
            _seen = set()
        if source.name in _seen:
            return False
        _seen.add(source.name)
        for target_name in source.mixin_targets:
            mixin_facet = self._lookup_facet_for_type(target_name)
            if mixin_facet is None:
                continue
            if mixin_facet is expected:
                return True
            if self._facet_satisfies(mixin_facet, expected, _seen):
                return True
        return False

    def _resolve_relative_reference(
        self,
        ref: Reference,
        steps: dict[str, StepInfo],
        step_returns: dict[str, set[str]],
        foreach_var: str | None,
        current_step: str | None,
        other_block_steps: set[str] | None,
    ) -> None:
        """Resolve a reference under container-relative ``$``-scoping.

        ``$`` = the immediate container frame, ``$$`` one down, ...; a step is
        nameable only if declared in the SAME block. See
        ``docs/architecture/ffl-relative-scoping.md``.
        """
        if ref.is_input:
            if not ref.path:
                return
            attr = ref.path[0]
            # Loop var lives on the immediate frame (accessed $.r); stay lenient
            # about the exact depth to match inherited-loop-var behavior.
            if foreach_var and attr == foreach_var:
                return
            depth = ref.up_levels
            idx = len(self._container_stack) - 1 - depth
            if idx < 0:
                dollars = "$" * (depth + 1)
                levels = max(len(self._container_stack) - 1, 0)
                self._result.add_error(
                    f"'{dollars}.{attr}' walks up {depth} container level(s), "
                    f"past the outermost container ({levels} enclosing level(s) "
                    f"available)",
                    ref.location,
                    rule_id="REF_DOLLAR_OVERFLOW",
                )
                return
            frame = self._container_stack[idx]
            if attr in frame.attrs:
                # Deep facet-ref path ($.param.field…): validate the sub-path
                # against the param's facet type. Only meaningful at the base
                # facet frame (idx 0), whose params drive _facet_param_types;
                # for step frames it is a safe no-op (unknown param → skip).
                if len(ref.path) >= 2 and idx == 0:
                    self._validate_facet_ref_attr_path(ref, attr)
                return
            if frame.open:
                # A pre-script may inject extra $. keys we can't enumerate.
                return
            dollars = "$" * (depth + 1)
            self._result.add_error(
                f"Invalid reference '{dollars}.{attr}': container '{frame.name}' "
                f"has no attribute '{attr}'",
                ref.location,
                rule_id="REF_INVALID_INPUT",
            )
            return

        # Step reference (step.attr or bare step).
        if not ref.path:
            self._result.add_error(
                "Empty step reference", ref.location, rule_id="REF_INVALID_STEP_FORMAT"
            )
            return
        step_name = ref.path[0]
        same_block = step_name in steps and step_name not in self._enclosing_step_names

        if same_block:
            # Forward reference within the block (same rule as the flat model).
            if current_step:
                names = list(steps.keys())
                if step_name in names and current_step in names:
                    if names.index(step_name) > names.index(current_step) or (
                        step_name != current_step
                        and names.index(step_name) == names.index(current_step)
                    ):
                        self._result.add_error(
                            f"Step '{current_step}' cannot reference step "
                            f"'{step_name}' which is not defined before it",
                            ref.location,
                            rule_id="REF_FORWARD_STEP",
                        )
                        return
            if len(ref.path) == 1:
                return  # bare pass-by-reference
            attr = ref.path[1]
            returns = step_returns.get(step_name, set())
            if returns and attr not in returns:
                self._result.add_error(
                    f"Invalid attribute '{attr}' for step '{step_name}': "
                    f"valid attributes are {sorted(returns)}",
                    ref.location,
                    rule_id="REF_INVALID_STEP_ATTRIBUTE",
                )
            return

        if other_block_steps and step_name in other_block_steps:
            self._result.add_error(
                f"Cross-block step reference: '{step_name}' is defined in a "
                f"sibling andThen block and cannot be referenced here. Use a step "
                f"body (e.g. step = Call(...) andThen {{ ... }}) to compose steps "
                f"that depend on each other.",
                ref.location,
                rule_id="REF_CROSS_BLOCK_STEP",
            )
            return
        if step_name in self._enclosing_step_names:
            self._result.add_error(
                f"Cannot name the enclosing step '{step_name}' directly: its name "
                f"is not defined in this block. Reach its attributes via "
                f"'$.{ref.path[1] if len(ref.path) > 1 else '<field>'}' (or '$$…' "
                f"for an outer container).",
                ref.location,
                rule_id="REF_CONTAINING_STEP_BY_NAME",
            )
            return
        if foreach_var and step_name == foreach_var:
            return
        self._result.add_error(
            f"Reference to undefined step '{step_name}'",
            ref.location,
            rule_id="REF_UNDEFINED_STEP",
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
        if self._relative_scoping:
            self._resolve_relative_reference(
                ref, steps, step_returns, foreach_var, current_step, other_block_steps
            )
            return
        if ref.is_input:
            # $.attr - must reference a valid input parameter
            if ref.path:
                attr: str | None = ref.path[0]
                # Allow foreach variable
                if foreach_var and attr == foreach_var:
                    return
                if attr not in input_attrs:
                    self._result.add_error(
                        f"Invalid input reference '$.{attr}': no parameter named '{attr}'",
                        ref.location,
                        rule_id="REF_INVALID_INPUT",
                    )
                    return
                # Step-ref param: validate the next path segment against
                # the referenced facet's params ∪ returns ∪ mixin aliases.
                if len(ref.path) >= 2:
                    self._validate_facet_ref_attr_path(ref, attr)
        else:
            # step.attr or bare step - must reference a valid step
            if not ref.path:
                self._result.add_error(
                    "Empty step reference",
                    ref.location,
                    rule_id="REF_INVALID_STEP_FORMAT",
                )
                return

            step_name = ref.path[0]
            # Bare step ref (len(path) == 1) — pass-by-reference into a
            # facet-typed param. Validate step existence only; the runtime
            # builds a StepReference and the consumer dereferences lazily.
            bare_ref = len(ref.path) == 1
            attr = ref.path[1] if not bare_ref else None

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
                        rule_id="REF_CROSS_BLOCK_STEP",
                    )
                    return
                self._result.add_error(
                    f"Reference to undefined step '{step_name}'",
                    ref.location,
                    rule_id="REF_UNDEFINED_STEP",
                )
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
                                rule_id="REF_FORWARD_STEP",
                            )
                            return

            # Bare step refs (`ds = s1`) have no attribute to validate.
            if bare_ref:
                return

            # Check if attribute is valid for that step's facet
            returns = step_returns.get(step_name, set())
            if returns and attr not in returns:
                self._result.add_error(
                    f"Invalid attribute '{attr}' for step '{step_name}': "
                    f"valid attributes are {sorted(returns)}",
                    ref.location,
                    rule_id="REF_INVALID_STEP_ATTRIBUTE",
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
        containing_sig: FacetSig | None = None,
        mixin_target_counts: dict[str, int] | None = None,
    ) -> None:
        """Validate a yield statement."""
        target = yield_stmt.call.name.split(".")[-1]  # Use short name

        if target not in valid_targets:
            self._result.add_error(
                f"Invalid yield target '{target}': must be the containing facet or one of its mixins. "
                f"Valid targets are: {sorted(valid_targets)}",
                yield_stmt.location,
                rule_id="YIELD_INVALID_TARGET",
            )
        elif (
            mixin_target_counts is not None
            and containing_sig is not None
            and mixin_target_counts.get(target, 0) > 1
        ):
            # Multiple sig-level mixins target the same facet — the bare
            # target name can't pick one.  Author must use an alias.
            alias_choices = sorted(
                m.alias
                for m in containing_sig.mixins
                if m.alias and m.name.split(".")[-1] == target
            )
            self._result.add_error(
                f"Ambiguous yield target '{target}': the containing facet has "
                f"{mixin_target_counts[target]} mixins with this target. "
                f"Use one of the aliases instead: {alias_choices}",
                yield_stmt.location,
                rule_id="YIELD_TARGET_AMBIGUOUS",
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

    def _validate_sys_stmt(
        self,
        stmt: "SysLogStmt | SysAssertStmt",
        input_attrs: set[str],
        steps: dict[str, "StepInfo"],
        step_returns: dict[str, set[str]],
        foreach_var: str | None,
        step_returns_types: dict[str, dict[str, str]] | None,
        other_block_steps: set[str] | None,
    ) -> None:
        """Validate ``sys.log`` and ``sys.assert`` inline diagnostic
        statements.

        ``sys.log`` args: each ``NamedArg`` value goes through the
        standard reference + type-inference checks (the same
        machinery step call args use).  Names are free-form
        identifiers per the named-args grammar — no further check
        needed.

        ``sys.assert`` condition: the expression's inferred type must
        be Boolean.  Anything else fires ``SYS_ASSERT_NOT_BOOLEAN``.
        """
        if isinstance(stmt, SysLogStmt):
            for arg in stmt.args:
                for ref in self._extract_references(arg.value):
                    self._validate_reference(
                        ref,
                        input_attrs,
                        steps,
                        step_returns,
                        foreach_var,
                        None,
                        other_block_steps,
                    )
                self._infer_type(arg.value, step_returns_types)
            return

        # SysAssertStmt
        condition = stmt.condition
        for ref in self._extract_references(condition):
            self._validate_reference(
                ref,
                input_attrs,
                steps,
                step_returns,
                foreach_var,
                None,
                other_block_steps,
            )
        inferred = self._infer_type(condition, step_returns_types)
        if inferred and inferred != "Boolean" and inferred != "Unknown":
            self._result.add_error(
                f"sys.assert condition must be Boolean, got {inferred}",
                stmt.location,
                rule_id="SYS_ASSERT_NOT_BOOLEAN",
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
                rule_id="SCHEMA_INSTANTIATION_NO_MIXINS",
            )

        # Check that all provided arguments are valid schema fields
        for arg in call.args:
            if arg.name not in schema_info.fields:
                self._result.add_error(
                    f"Unknown field '{arg.name}' for schema '{call.name}'. "
                    f"Valid fields are: {sorted(schema_info.fields)}",
                    arg.location,
                    rule_id="SCHEMA_UNKNOWN_FIELD",
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
                    rule_id="IMPLICIT_UNKNOWN_PARAM",
                )


def validate(program: Program, relative_scoping: bool | None = None) -> ValidationResult:
    """Validate a program AST.

    Args:
        program: The Program AST to validate
        relative_scoping: opt into container-relative ``$``-scoping
            (see ``docs/architecture/ffl-relative-scoping.md``). ``None`` reads
            ``FW_FFL_RELATIVE_SCOPING`` from the environment (default off).

    Returns:
        ValidationResult containing any errors found
    """
    validator = FFLValidator(relative_scoping=relative_scoping)
    return validator.validate(program)
