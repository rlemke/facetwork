"""MongoDB entity dataclasses for FFL runtime.

These dataclasses represent the documents stored in MongoDB collections.
All timestamps are stored as int (milliseconds since Unix epoch).

Entity classes are organized into domain-specific modules:

- ``common`` — Parameter, UserDefinition, Ownership, etc.
- ``flow`` — FlowDefinition, WorkflowDefinition, NamespaceDefinition, etc.
- ``runner`` — RunnerDefinition, RunnerState
- ``task`` — TaskDefinition, TaskState
- ``logging`` — LogDefinition, StepLogEntry, StepLogLevel, etc.
- ``server`` — ServerDefinition, HandlerRegistration, PublishedSource

All classes are re-exported here for backward compatibility — existing
``from facetwork.runtime.entities import X`` imports continue to work.
"""

# Common types
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

# Flow definitions
from .flow import (
    BlockDefinition,
    FacetDefinition,
    FlowDefinition,
    FlowIdentity,
    MixinDefinition,
    NamespaceDefinition,
    StatementArguments,
    StatementDefinition,
    StatementReferences,
    WorkflowDefinition,
)

# Logging
from .logging import (
    LogDefinition,
    NoteImportance,
    NoteOriginator,
    NoteType,
    StepLogEntry,
    StepLogLevel,
    StepLogSource,
)

# Runner
from .runner import RunnerDefinition, RunnerState

# Users and teams
from .user import (
    DELETED_USER_EMAIL,
    SPECIAL_KINDS,
    STATUS_ACTIVE,
    STATUS_DELETED,
    TeamDefinition,
    User,
    special_users,
)

# Server and handler registration
from .server import (
    HandledCount,
    HandlerRegistration,
    PublishedSource,
    ServerDefinition,
    ServerState,
)

# Task
from .task import TaskDefinition, TaskState

__all__ = [
    # Common
    "Classifier",
    "FileArtifact",
    "InlineSource",
    "JarArtifact",
    "Ownership",
    "Parameter",
    "ResourceSource",
    "ScriptCode",
    "SourceText",
    "TextSource",
    "UserDefinition",
    "WorkflowMetaData",
    # Flow
    "BlockDefinition",
    "FacetDefinition",
    "FlowDefinition",
    "FlowIdentity",
    "MixinDefinition",
    "NamespaceDefinition",
    "StatementArguments",
    "StatementDefinition",
    "StatementReferences",
    "WorkflowDefinition",
    # Logging
    "LogDefinition",
    "NoteImportance",
    "NoteOriginator",
    "NoteType",
    "StepLogEntry",
    "StepLogLevel",
    "StepLogSource",
    # Runner
    "RunnerDefinition",
    "RunnerState",
    # Users and teams
    "User",
    "TeamDefinition",
    "special_users",
    "SPECIAL_KINDS",
    "STATUS_ACTIVE",
    "STATUS_DELETED",
    "DELETED_USER_EMAIL",
    # Server
    "HandledCount",
    "HandlerRegistration",
    "PublishedSource",
    "ServerDefinition",
    "ServerState",
    # Task
    "TaskDefinition",
    "TaskState",
]
