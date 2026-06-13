"""Runner (workflow execution instance) entity definitions."""

from dataclasses import dataclass, field

from .common import Parameter, UserDefinition
from .flow import WorkflowDefinition


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
    user: UserDefinition | None = None  # who STARTED the run (started_by)
    author: UserDefinition | None = None  # who AUTHORED the workflow (from the Author mixin)
    purpose: str = "none"  # production | beta | test | personal | none
    teams: list[str] = field(default_factory=list)  # teams this run is listed for
    start_time: int = 0  # Execution start timestamp (ms)
    end_time: int = 0  # Execution end timestamp (ms)
    duration: int = 0  # Total execution duration (ms)
    retain: int = 0  # Retention period (ms)
    state: str = RunnerState.CREATED
    compiled_ast: dict | None = None  # Snapshotted program AST at workflow start
    workflow_ast: dict | None = None  # Snapshotted workflow node AST at workflow start
