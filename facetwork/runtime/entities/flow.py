"""Flow and workflow entity definitions."""

from __future__ import annotations

from dataclasses import dataclass, field

from .common import (
    Classifier,
    FileArtifact,
    InlineSource,
    JarArtifact,
    Ownership,
    Parameter,
    ResourceSource,
    ScriptCode,
    SourceText,
    TextSource,
    UserDefinition,
    WorkflowMetaData,
)


@dataclass
class NamespaceDefinition:
    """Namespace definition within a flow."""

    uuid: str
    name: str
    path: str = ""
    documentation: dict | str | None = None


@dataclass
class FacetDefinition:
    """Facet definition within a flow."""

    uuid: str
    name: str
    namespace_id: str
    parameters: list[Parameter] = field(default_factory=list)
    return_type: str | None = None
    documentation: dict | str | None = None


@dataclass
class MixinDefinition:
    """Mixin definition within a flow."""

    uuid: str
    name: str
    namespace_id: str
    parameters: list[Parameter] = field(default_factory=list)


@dataclass
class BlockDefinition:
    """Block definition within a flow."""

    uuid: str
    name: str
    block_type: str  # AndThen, AndMap, AndMatch
    statements: list[str] = field(default_factory=list)  # Statement IDs


@dataclass
class StatementDefinition:
    """Statement definition within a flow."""

    uuid: str
    name: str
    statement_type: str  # VariableAssignment, YieldAssignment
    block_id: str | None = None
    expression: dict | None = None


@dataclass
class StatementArguments:
    """Arguments for a statement."""

    statement_id: str
    arguments: list[Parameter] = field(default_factory=list)


@dataclass
class StatementReferences:
    """Dependency references for a statement."""

    statement_id: str
    references: list[str] = field(default_factory=list)  # Referenced statement IDs


@dataclass
class FlowIdentity:
    """Flow identification."""

    name: str
    path: str
    uuid: str


@dataclass
class FlowDefinition:
    """Compiled FFL flow definition.

    Stored in the `flows` collection.
    """

    uuid: str
    name: FlowIdentity
    namespaces: list[NamespaceDefinition] = field(default_factory=list)
    facets: list[FacetDefinition] = field(default_factory=list)
    workflows: list[WorkflowDefinition] = field(default_factory=list)
    mixins: list[MixinDefinition] = field(default_factory=list)
    blocks: list[BlockDefinition] = field(default_factory=list)
    statements: list[StatementDefinition] = field(default_factory=list)
    arguments: list[StatementArguments] = field(default_factory=list)
    references: list[StatementReferences] = field(default_factory=list)
    script_code: list[ScriptCode] = field(default_factory=list)
    file_artifacts: list[FileArtifact] = field(default_factory=list)
    jar_artifacts: list[JarArtifact] = field(default_factory=list)
    resources: list[ResourceSource] = field(default_factory=list)
    text_sources: list[TextSource] = field(default_factory=list)
    inline: InlineSource | None = None
    classification: Classifier | None = None
    publisher: UserDefinition | None = None
    ownership: Ownership | None = None
    compiled_sources: list[SourceText] = field(default_factory=list)
    compiled_ast: dict | None = None  # Immutable compiled JSON (program_ast)


@dataclass
class WorkflowDefinition:
    """Named workflow entry point.

    Stored in the `workflows` collection.
    """

    uuid: str
    name: str
    namespace_id: str
    facet_id: str
    flow_id: str
    starting_step: str
    version: str
    metadata: WorkflowMetaData | None = None
    documentation: dict | str | None = None
    date: int = 0  # Creation timestamp (milliseconds)
