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

"""MongoDB entity dataclasses for AFL runtime.

These dataclasses represent the documents stored in MongoDB collections.
All timestamps are stored as int (milliseconds since Unix epoch).
"""

from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# Supporting Types
# =============================================================================


@dataclass
class Parameter:
    """Runtime parameter for workflows and steps."""

    name: str
    value: Any
    type_hint: str = "Any"


@dataclass
class UserDefinition:
    """User information."""

    email: str
    name: str = ""
    avatar: str = ""


@dataclass
class Ownership:
    """Ownership information for flows."""

    owner: UserDefinition | None = None
    group: str = ""


@dataclass
class Classifier:
    """Flow classification."""

    category: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class SourceText:
    """Compiled source text."""

    name: str
    content: str
    language: str = "afl"


@dataclass
class InlineSource:
    """Inline source code."""

    content: str
    language: str = "afl"


@dataclass
class FileArtifact:
    """File artifact reference."""

    path: str
    checksum: str = ""


@dataclass
class JarArtifact:
    """JAR artifact reference."""

    group_id: str
    artifact_id: str
    version: str


@dataclass
class ResourceSource:
    """Resource source reference."""

    name: str
    path: str


@dataclass
class TextSource:
    """Text source reference."""

    name: str
    content: str


@dataclass
class ScriptCode:
    """Generated script code."""

    name: str
    code: str
    language: str = "python"


@dataclass
class WorkflowMetaData:
    """Workflow metadata."""

    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)


# =============================================================================
# Flow Definition Types
# =============================================================================


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


# =============================================================================
# Flow Identity and Definition
# =============================================================================


@dataclass
class FlowIdentity:
    """Flow identification."""

    name: str
    path: str
    uuid: str


@dataclass
class FlowDefinition:
    """Compiled AFL flow definition.

    Stored in the `flows` collection.
    """

    uuid: str
    name: FlowIdentity
    namespaces: list[NamespaceDefinition] = field(default_factory=list)
    facets: list[FacetDefinition] = field(default_factory=list)
    workflows: list["WorkflowDefinition"] = field(default_factory=list)
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


# =============================================================================
# Workflow Definition
# =============================================================================


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


# =============================================================================
# Runner Definition
# =============================================================================


class RunnerState:
    """Runner state constants."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class RunnerDefinition:
    """Workflow execution instance.

    Stored in the `runners` collection.
    """

    uuid: str
    workflow_id: str
    workflow: WorkflowDefinition
    parameters: list[Parameter] = field(default_factory=list)
    step_id: str | None = None
    user: UserDefinition | None = None
    start_time: int = 0  # Execution start timestamp (ms)
    end_time: int = 0  # Execution end timestamp (ms)
    duration: int = 0  # Total execution duration (ms)
    retain: int = 0  # Retention period (ms)
    state: str = RunnerState.CREATED
    compiled_ast: dict | None = None  # Snapshotted program AST at workflow start
    workflow_ast: dict | None = None  # Snapshotted workflow node AST at workflow start


# =============================================================================
# Task Definition
# =============================================================================


class TaskState:
    """Task state constants."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    IGNORED = "ignored"
    CANCELED = "canceled"


@dataclass
class TaskDefinition:
    """Async task queue entry.

    Stored in the `tasks` collection.
    """

    uuid: str
    name: str
    runner_id: str
    workflow_id: str
    flow_id: str
    step_id: str
    state: str = TaskState.PENDING
    created: int = 0  # Creation timestamp (ms)
    updated: int = 0  # Last updated timestamp (ms)
    error: dict | None = None
    task_list_name: str = "default"
    data_type: str = ""
    data: dict | None = None
    server_id: str = ""  # Claiming server's ID (for orphan detection)
    timeout_ms: int = 0  # Handler timeout (0 = use registration default)
    task_heartbeat: int = 0  # Handler-level heartbeat timestamp (ms)


# =============================================================================
# Log Definition
# =============================================================================


class NoteType:
    """Log note type constants."""

    ERROR = "error"
    INFO = "info"
    WARNING = "warning"


class NoteOriginator:
    """Log note originator constants."""

    WORKFLOW = "workflow"
    AGENT = "agent"


class NoteImportance:
    """Log importance level constants."""

    HIGH = 1
    NORMAL = 5
    LOW = 10


@dataclass
class LogDefinition:
    """Audit and execution log entry.

    Stored in the `logs` collection.
    """

    uuid: str
    order: int
    runner_id: str
    step_id: str | None = None
    object_id: str = ""
    object_type: str = ""
    note_originator: str = NoteOriginator.WORKFLOW
    note_type: str = NoteType.INFO
    note_importance: int = NoteImportance.NORMAL
    message: str = ""
    state: str = ""
    line: int = 0
    file: str = ""
    details: dict = field(default_factory=dict)
    time: int = 0  # Log timestamp (ms)


# =============================================================================
# Step Log Definition
# =============================================================================


class StepLogLevel:
    """Step log level constants."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class StepLogSource:
    """Step log source constants."""

    FRAMEWORK = "framework"
    HANDLER = "handler"


@dataclass
class StepLogEntry:
    """Step-level lifecycle log entry.

    Captures key milestones during event facet handler execution,
    such as task claimed, handler dispatching, handler completed,
    and handler-emitted messages.

    Stored in the ``step_logs`` collection.
    """

    uuid: str
    step_id: str
    workflow_id: str
    runner_id: str = ""
    facet_name: str = ""
    source: str = StepLogSource.FRAMEWORK
    level: str = StepLogLevel.INFO
    message: str = ""
    details: dict = field(default_factory=dict)
    time: int = 0


# =============================================================================
# Server Definition
# =============================================================================


class ServerState:
    """Server state constants."""

    STARTUP = "startup"
    RUNNING = "running"
    SHUTDOWN = "shutdown"
    ERROR = "error"


@dataclass
class HandledCount:
    """Event handling statistics."""

    handler: str
    handled: int = 0
    not_handled: int = 0


@dataclass
class ServerDefinition:
    """Agent/server registration.

    Stored in the `servers` collection.
    """

    uuid: str
    server_group: str
    service_name: str
    server_name: str
    server_ips: list[str] = field(default_factory=list)
    start_time: int = 0  # Server start timestamp (ms)
    ping_time: int = 0  # Last ping timestamp (ms)
    topics: list[str] = field(default_factory=list)
    handlers: list[str] = field(default_factory=list)
    handled: list[HandledCount] = field(default_factory=list)
    state: str = ServerState.STARTUP
    http_port: int = 0
    manager: str = ""
    error: dict | None = None


# =============================================================================
# Handler Registration
# =============================================================================


@dataclass
class HandlerRegistration:
    """Handler registration for the RegistryRunner.

    Maps a qualified facet name to a Python module + entrypoint
    so the RegistryRunner can dynamically load and dispatch handlers.
    """

    facet_name: str  # Qualified name: "ns.FacetName" (primary key)
    module_uri: str  # Python module path ("my.handlers") or "file:///path/to.py"
    entrypoint: str = "handle"  # Function name within module
    version: str = "1.0.0"
    checksum: str = ""  # For cache invalidation
    timeout_ms: int = 30000
    requirements: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created: int = 0  # Timestamp (ms)
    updated: int = 0  # Timestamp (ms)


# =============================================================================
# Lock Definition
# =============================================================================


@dataclass
class PublishedSource:
    """Published AFL source for namespace-based lookup.

    Stored in the ``afl_sources`` collection.
    """

    uuid: str
    namespace_name: str
    source_text: str
    namespaces_defined: list[str] = field(default_factory=list)
    version: str = "latest"
    published_at: int = 0
    origin: str = ""
    checksum: str = ""
