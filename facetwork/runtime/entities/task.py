"""Task (async queue entry) entity definitions."""

from dataclasses import dataclass


class TaskState:
    """Task state constants."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    IGNORED = "ignored"
    CANCELED = "canceled"
    DEAD_LETTER = "dead_letter"


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
    retry_count: int = 0  # Number of times this task has been retried
    max_retries: int = 5  # Max retries before dead-lettering (0 = infinite)
    next_retry_after: int = 0  # Epoch ms; task not claimable until this time
