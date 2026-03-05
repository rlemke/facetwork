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

"""AFL runtime package.

Executes compiled AFL workflows through iterative evaluation.
"""

from .agent import (
    ClaudeAgentRunner,
    LLMHandler,
    LLMHandlerConfig,
    TokenUsage,
    ToolDefinition,
    ToolRegistry,
)
from .agent_poller import AgentPoller, AgentPollerConfig
from .agent_runner import AgentConfig, make_store, run_agent
from .block import BlockAnalysis, StatementDefinition, StepAnalysis

# DAO protocols
from .dao import (
    DataServices,
    FlowDefinitionDAO,
    KeyLockDAO,
    LogDefinitionDAO,
    RunnerDefinitionDAO,
    ServerDefinitionDAO,
    StepDefinitionDAO,
    TaskDefinitionDAO,
    WorkflowDefinitionDAO,
)
from .dependency import DependencyGraph
from .dispatcher import (
    CompositeDispatcher,
    HandlerDispatcher,
    InMemoryDispatcher,
    RegistryDispatcher,
    ToolRegistryDispatcher,
)

# Entity definitions
from .entities import (
    BlockDefinition,
    Classifier,
    FacetDefinition,
    FileArtifact,
    FlowDefinition,
    FlowIdentity,
    HandledCount,
    HandlerRegistration,
    InlineSource,
    JarArtifact,
    LockDefinition,
    LockMetaData,
    LogDefinition,
    MixinDefinition,
    # Flow types
    NamespaceDefinition,
    NoteImportance,
    NoteOriginator,
    NoteType,
    Ownership,
    # Supporting types
    Parameter,
    ResourceSource,
    RunnerDefinition,
    RunnerState,
    ScriptCode,
    ServerDefinition,
    ServerState,
    SourceText,
    StatementArguments,
    StatementReferences,
    StepLogEntry,
    StepLogLevel,
    StepLogSource,
    TaskDefinition,
    TaskState,
    TextSource,
    UserDefinition,
    # Workflow and execution
    WorkflowDefinition,
    WorkflowMetaData,
)
from .entities import (
    StatementDefinition as EntityStatementDefinition,
)
from .errors import (
    BlockNotFoundError,
    ConcurrencyError,
    DependencyNotSatisfiedError,
    EvaluationError,
    InvalidStepStateError,
    InvalidTransitionError,
    ReferenceError,
    RuntimeError,
    StepNotFoundError,
    TokenBudgetExceededError,
    VersionMismatchError,
)
from .evaluator import Evaluator, ExecutionContext, ExecutionResult, ExecutionStatus
from .expression import EvaluationContext, ExpressionEvaluator, evaluate_args, evaluate_default
from .memory_store import MemoryStore
from .persistence import IterationChanges, PersistenceAPI
from .registry_runner import RegistryRunner, RegistryRunnerConfig, create_registry_runner
from .runner import RunnerConfig, RunnerService
from .states import (
    BLOCK_TRANSITIONS,
    SCHEMA_TRANSITIONS,
    STEP_TRANSITIONS,
    YIELD_TRANSITIONS,
    StepState,
    get_next_state,
    select_transitions,
)
from .step import StepDefinition, StepTransition
from .telemetry import Telemetry, TelemetryEvent
from .types import (
    AttributeValue,
    BlockId,
    FacetAttributes,
    ObjectType,
    StatementId,
    StepId,
    VersionInfo,
    WorkflowId,
    block_id,
    generate_id,
    step_id,
    workflow_id,
)

__all__ = [
    # Types
    "StepId",
    "BlockId",
    "WorkflowId",
    "StatementId",
    "ObjectType",
    "AttributeValue",
    "FacetAttributes",
    "VersionInfo",
    "generate_id",
    "step_id",
    "block_id",
    "workflow_id",
    # States
    "StepState",
    "STEP_TRANSITIONS",
    "BLOCK_TRANSITIONS",
    "YIELD_TRANSITIONS",
    "SCHEMA_TRANSITIONS",
    "get_next_state",
    "select_transitions",
    # Step
    "StepDefinition",
    "StepTransition",
    # Persistence
    "PersistenceAPI",
    "IterationChanges",
    "MemoryStore",
    # Block
    "StatementDefinition",
    "StepAnalysis",
    "BlockAnalysis",
    # Dependency
    "DependencyGraph",
    # Expression
    "ExpressionEvaluator",
    "EvaluationContext",
    "evaluate_args",
    "evaluate_default",
    # Evaluator
    "Evaluator",
    "ExecutionContext",
    "ExecutionResult",
    "ExecutionStatus",
    # Telemetry
    "Telemetry",
    "TelemetryEvent",
    # Events
    # Agent
    "ClaudeAgentRunner",
    "ToolRegistry",
    "ToolDefinition",
    "TokenUsage",
    "LLMHandler",
    "LLMHandlerConfig",
    # Runner
    "RunnerService",
    "RunnerConfig",
    # Agent Poller
    "AgentPoller",
    "AgentPollerConfig",
    # Agent Runner helper
    "AgentConfig",
    "run_agent",
    "make_store",
    # Registry Runner
    "RegistryRunner",
    "RegistryRunnerConfig",
    "create_registry_runner",
    "HandlerRegistration",
    # Dispatchers
    "HandlerDispatcher",
    "RegistryDispatcher",
    "InMemoryDispatcher",
    "ToolRegistryDispatcher",
    "CompositeDispatcher",
    # Errors
    "RuntimeError",
    "InvalidStepStateError",
    "StepNotFoundError",
    "BlockNotFoundError",
    "DependencyNotSatisfiedError",
    "EvaluationError",
    "ReferenceError",
    "InvalidTransitionError",
    "ConcurrencyError",
    "VersionMismatchError",
    "TokenBudgetExceededError",
    # Entity types
    "Parameter",
    "UserDefinition",
    "Ownership",
    "Classifier",
    "SourceText",
    "InlineSource",
    "FileArtifact",
    "JarArtifact",
    "ResourceSource",
    "TextSource",
    "ScriptCode",
    "WorkflowMetaData",
    "NamespaceDefinition",
    "FacetDefinition",
    "MixinDefinition",
    "BlockDefinition",
    "EntityStatementDefinition",
    "StatementArguments",
    "StatementReferences",
    "FlowIdentity",
    "FlowDefinition",
    "WorkflowDefinition",
    "RunnerDefinition",
    "RunnerState",
    "TaskDefinition",
    "TaskState",
    "LogDefinition",
    "NoteType",
    "NoteOriginator",
    "NoteImportance",
    "ServerDefinition",
    "ServerState",
    "HandledCount",
    "LockDefinition",
    "LockMetaData",
    "StepLogEntry",
    "StepLogLevel",
    "StepLogSource",
    # DAO protocols
    "FlowDefinitionDAO",
    "WorkflowDefinitionDAO",
    "RunnerDefinitionDAO",
    "StepDefinitionDAO",
    "TaskDefinitionDAO",
    "LogDefinitionDAO",
    "ServerDefinitionDAO",
    "KeyLockDAO",
    "DataServices",
]
