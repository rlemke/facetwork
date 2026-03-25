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

"""AFL runtime persistence abstraction.

The Evaluator MUST NOT directly access the database.
All persistence operations are performed through this API.
"""

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from .step import StepDefinition
from .types import BlockId, StepId

if TYPE_CHECKING:
    from .entities import (
        FlowDefinition,
        HandlerRegistration,
        LogDefinition,
        RunnerDefinition,
        ServerDefinition,
        StepLogEntry,
        TaskDefinition,
    )


@dataclass
class IterationChanges:
    """Accumulated changes from a single iteration.

    Changes are collected in memory during iteration and
    atomically committed at iteration boundary.
    """

    created_steps: list[StepDefinition] = field(default_factory=list)
    updated_steps: list[StepDefinition] = field(default_factory=list)
    created_tasks: list["TaskDefinition"] = field(default_factory=list)

    # Track step IDs to avoid duplicates
    _created_ids: set[StepId] = field(default_factory=set)
    _updated_ids: dict[StepId, int] = field(default_factory=dict)

    def add_created_step(self, step: StepDefinition) -> None:
        """Record a newly created step (idempotent)."""
        if step.id not in self._created_ids:
            self._created_ids.add(step.id)
            self.created_steps.append(step)

    def add_updated_step(self, step: StepDefinition) -> None:
        """Record an updated step (replaces previous update for same ID)."""
        if step.id in self._updated_ids:
            # Replace the previous version
            idx = self._updated_ids[step.id]
            self.updated_steps[idx] = step
        else:
            self._updated_ids[step.id] = len(self.updated_steps)
            self.updated_steps.append(step)

    def add_created_task(self, task: "TaskDefinition") -> None:
        """Record a newly created task."""
        self.created_tasks.append(task)

    @property
    def has_changes(self) -> bool:
        """Check if there are any changes to commit."""
        return (
            len(self.created_steps) > 0
            or len(self.updated_steps) > 0
            or len(self.created_tasks) > 0
        )

    def clear(self) -> None:
        """Clear all accumulated changes."""
        self.created_steps.clear()
        self.updated_steps.clear()
        self.created_tasks.clear()
        self._created_ids.clear()
        self._updated_ids.clear()


@runtime_checkable
class PersistenceAPI(Protocol):
    """Protocol defining the persistence abstraction boundary.

    All database operations MUST go through this interface.
    Implementations handle:
    - Concurrency and locking semantics
    - Atomicity guarantees
    - Database-specific details
    """

    # Step operations
    @abstractmethod
    def get_step(self, step_id: str) -> StepDefinition | None:
        """Fetch a step by its persistent ID.

        Args:
            step_id: The step's unique identifier

        Returns:
            The step if found, None otherwise
        """
        ...

    @abstractmethod
    def get_steps_by_block(self, block_id: StepId | BlockId) -> Sequence[StepDefinition]:
        """Fetch all steps belonging to a block.

        Args:
            block_id: The block's unique identifier (StepId or BlockId,
                since blocks are steps)

        Returns:
            All steps in the block
        """
        ...

    @abstractmethod
    def get_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps belonging to a workflow.

        Args:
            workflow_id: The workflow's unique identifier

        Returns:
            All steps in the workflow
        """
        ...

    def get_actionable_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch steps that need processing in an evaluator iteration.

        Returns steps that are NOT terminal (Complete/Error) and NOT
        parked at EventTransmit without a pending transition.  Subclasses
        may override to push the filtering into the database query.

        Args:
            workflow_id: The workflow's unique identifier

        Returns:
            Steps eligible for evaluator processing
        """
        from .states import StepState

        steps = self.get_steps_by_workflow(workflow_id)
        return [
            s
            for s in steps
            if not StepState.is_terminal(s.state)
            and not (
                s.state == StepState.EVENT_TRANSMIT and not s.transition.is_requesting_state_change
            )
        ]

    def get_pending_resume_workflow_ids(self) -> list[str]:
        """Get workflow IDs that have EventTransmit steps with pending transitions.

        These are steps where an external handler has completed (via
        continue_step) but the subsequent resume failed to advance
        the step.  The default implementation scans all steps; subclasses
        should override with an efficient database query.

        Returns:
            Distinct workflow IDs needing resume
        """
        from .states import StepState

        seen: set[str] = set()
        for step in self.get_steps_by_state(StepState.EVENT_TRANSMIT):
            if step.transition.is_requesting_state_change and step.workflow_id not in seen:
                seen.add(step.workflow_id)
        return list(seen)

    @abstractmethod
    def get_steps_by_state(self, state: str) -> Sequence[StepDefinition]:
        """Fetch all steps in a given state.

        Args:
            state: The step state to filter by

        Returns:
            All steps in the given state
        """
        ...

    @abstractmethod
    def get_steps_by_container(self, container_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps with a given container.

        Args:
            container_id: The container step's ID

        Returns:
            All steps in the container
        """
        ...

    @abstractmethod
    def save_step(self, step: StepDefinition) -> None:
        """Persist a new or updated step.

        Args:
            step: The step to save
        """
        ...

    # Block operations
    @abstractmethod
    def get_blocks_by_step(self, step_id: str) -> Sequence[StepDefinition]:
        """Fetch all block steps for a containing step.

        Args:
            step_id: The containing step's ID

        Returns:
            All block steps for this step
        """
        ...

    def delete_steps(self, step_ids: Sequence[str]) -> int:
        """Delete steps by their UUIDs.

        Args:
            step_ids: The step UUIDs to delete

        Returns:
            Number of steps deleted
        """
        raise NotImplementedError

    def delete_tasks_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete tasks associated with the given step IDs.

        Args:
            step_ids: The step UUIDs whose tasks should be deleted

        Returns:
            Number of tasks deleted
        """
        raise NotImplementedError

    def delete_step_logs_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete step log entries for the given step IDs.

        Args:
            step_ids: The step UUIDs whose logs should be deleted

        Returns:
            Number of log entries deleted
        """
        raise NotImplementedError

    # Atomic operations
    @abstractmethod
    def commit(self, changes: IterationChanges) -> None:
        """Atomically commit all iteration changes.

        This is called at iteration boundary to persist
        all in-memory changes atomically.

        Args:
            changes: The accumulated changes to commit
        """
        ...

    # Query operations
    @abstractmethod
    def get_workflow_root(self, workflow_id: str) -> StepDefinition | None:
        """Get the root step of a workflow.

        Args:
            workflow_id: The workflow's unique identifier

        Returns:
            The root step if found
        """
        ...

    @abstractmethod
    def step_exists(self, statement_id: str, block_id: StepId | BlockId | None) -> bool:
        """Check if a step already exists for a statement in a block.

        Used to prevent duplicate step creation (idempotency).

        Args:
            statement_id: The statement definition ID
            block_id: The containing block ID (StepId or BlockId,
                since blocks are steps)

        Returns:
            True if step already exists
        """
        ...

    @abstractmethod
    def block_step_exists(self, statement_id: str, container_id: StepId) -> bool:
        """Check if a block step already exists for a statement in a container.

        Block steps use container_id (not block_id) for hierarchy,
        so they need a dedicated check separate from step_exists().

        Args:
            statement_id: The block statement ID (e.g. "block-0")
            container_id: The containing step's ID

        Returns:
            True if block step already exists
        """
        ...

    # Runner operations

    @abstractmethod
    def get_runner(self, runner_id: str) -> Optional["RunnerDefinition"]:
        """Get a runner by ID.

        Args:
            runner_id: The runner's unique identifier

        Returns:
            The runner if found, None otherwise
        """
        ...

    @abstractmethod
    def save_runner(self, runner: "RunnerDefinition") -> None:
        """Save a runner.

        Args:
            runner: The runner to save
        """
        ...

    @abstractmethod
    def get_runners_by_state(self, state: str) -> Sequence["RunnerDefinition"]:
        """Get runners by state.

        Args:
            state: The runner state to filter by

        Returns:
            All runners in the given state
        """
        ...

    # Task operations

    @abstractmethod
    def get_pending_tasks(self, task_list: str) -> Sequence["TaskDefinition"]:
        """Get pending tasks for a task list.

        Args:
            task_list: The task list name

        Returns:
            All pending tasks in the task list
        """
        ...

    @abstractmethod
    def get_task_for_step(self, step_id: str) -> Optional["TaskDefinition"]:
        """Get the most recent task associated with a step.

        Args:
            step_id: The step's unique identifier

        Returns:
            The most recent task for the step, or None if not found
        """
        ...

    @abstractmethod
    def save_task(self, task: "TaskDefinition") -> None:
        """Save a task.

        Args:
            task: The task to save
        """
        ...

    @abstractmethod
    def claim_task(
        self,
        task_names: list[str],
        task_list: str = "default",
        server_id: str = "",
    ) -> Optional["TaskDefinition"]:
        """Atomically claim a pending task matching one of the given names.

        Transitions a single task from PENDING to RUNNING atomically.
        Returns the claimed task, or None if no matching task is available.

        Args:
            task_names: List of task names to match
            task_list: The task list to search (default: "default")
            server_id: The claiming server's ID (for orphan detection)

        Returns:
            The claimed task, or None
        """
        ...

    def reap_orphaned_tasks(self, down_timeout_ms: int = 300_000) -> list[dict[str, str]]:
        """Reset tasks whose claiming server is down.

        A server is considered down if its ``ping_time`` is stale (older than
        *down_timeout_ms*) while its state is still ``running`` or ``startup``.
        Both running and pending tasks pinned to dead servers are reset so
        they can be picked up by a healthy runner.  Dead servers are also
        marked as ``shutdown``.

        Args:
            down_timeout_ms: How long a server's heartbeat can be stale
                before it is considered dead (default: 5 minutes).

        Returns:
            List of dicts with ``step_id``, ``workflow_id``, ``name``,
            and ``server_id`` for each reaped task.
        """
        return []

    def reap_stuck_tasks(self, default_stuck_ms: int = 14_400_000) -> list[dict[str, str]]:
        """Reset tasks stuck in RUNNING state beyond their timeout.

        Catches tasks with an explicit ``timeout_ms`` exceeded, or tasks
        without a timeout that have had no activity (no heartbeat or update)
        for longer than *default_stuck_ms* (default: 4 hours).

        Returns:
            List of dicts with ``step_id``, ``workflow_id``, ``name``,
            ``server_id``, ``reason``, and ``timeout_ms`` for each reaped task.
        """
        return []

    def update_task_heartbeat(self, task_id: str, heartbeat_time: int) -> None:
        """Update a running task's heartbeat timestamp.

        Handlers call this periodically during long-running operations so the
        orphan reaper knows the task is still making progress even if the
        server's heartbeat is stale (e.g. due to I/O contention).
        """
        return None

    # Log operations

    @abstractmethod
    def save_log(self, log: "LogDefinition") -> None:
        """Save a log entry.

        Args:
            log: The log entry to save
        """
        ...

    @abstractmethod
    def get_logs_by_runner(self, runner_id: str) -> Sequence["LogDefinition"]:
        """Get logs for a runner.

        Args:
            runner_id: The runner's unique identifier

        Returns:
            All logs for the runner
        """
        ...

    # Step log operations

    @abstractmethod
    def save_step_log(self, entry: "StepLogEntry") -> None:
        """Save a step log entry.

        Args:
            entry: The step log entry to save
        """
        ...

    @abstractmethod
    def get_step_logs_by_step(self, step_id: str) -> Sequence["StepLogEntry"]:
        """Get step logs for a step, ordered by time ascending.

        Args:
            step_id: The step's unique identifier

        Returns:
            All step log entries for the step
        """
        ...

    @abstractmethod
    def get_step_logs_by_workflow(self, workflow_id: str) -> Sequence["StepLogEntry"]:
        """Get step logs for a workflow, ordered by time ascending.

        Args:
            workflow_id: The workflow's unique identifier

        Returns:
            All step log entries for the workflow
        """
        ...

    def get_tasks_by_facet_name(
        self, facet_name: str, states: list[str] | None = None
    ) -> Sequence["TaskDefinition"]:
        """Get tasks matching a facet name, optionally filtered by states.

        Args:
            facet_name: The qualified facet name (matches task.name)
            states: Optional list of states to filter by

        Returns:
            Tasks matching the criteria
        """
        return []

    def get_step_logs_since(self, step_id: str, since_time: int) -> Sequence["StepLogEntry"]:
        """Get step logs for a step newer than the given timestamp.

        Args:
            step_id: The step's unique identifier
            since_time: Millisecond timestamp; only entries with time > since_time are returned

        Returns:
            Matching step log entries, ordered by time ascending
        """
        return []

    def get_workflow_logs_since(
        self, workflow_id: str, since_time: int
    ) -> Sequence["StepLogEntry"]:
        """Get step logs for a workflow newer than the given timestamp.

        Args:
            workflow_id: The workflow's unique identifier
            since_time: Millisecond timestamp; only entries with time > since_time are returned

        Returns:
            Matching step log entries, ordered by time ascending
        """
        return []

    def get_step_logs_by_facet(self, facet_name: str, limit: int = 20) -> Sequence["StepLogEntry"]:
        """Get recent step logs for a facet, ordered by time descending.

        Args:
            facet_name: The qualified facet name
            limit: Maximum number of entries to return

        Returns:
            Recent step log entries for the facet
        """
        return []

    # Handler registration operations

    @abstractmethod
    def save_handler_registration(self, registration: "HandlerRegistration") -> None:
        """Upsert a handler registration by facet_name.

        Args:
            registration: The handler registration to save
        """
        ...

    @abstractmethod
    def get_handler_registration(self, facet_name: str) -> Optional["HandlerRegistration"]:
        """Get a handler registration by facet name.

        Args:
            facet_name: The qualified facet name

        Returns:
            The registration if found, None otherwise
        """
        ...

    @abstractmethod
    def list_handler_registrations(self) -> Sequence["HandlerRegistration"]:
        """List all handler registrations.

        Returns:
            All registered handlers
        """
        ...

    @abstractmethod
    def delete_handler_registration(self, facet_name: str) -> bool:
        """Delete a handler registration by facet name.

        Args:
            facet_name: The qualified facet name

        Returns:
            True if deleted, False if not found
        """
        ...

    # Flow operations

    def get_flow(self, flow_id: str) -> Optional["FlowDefinition"]:
        """Get a flow by ID.

        Args:
            flow_id: The flow's unique identifier

        Returns:
            The flow if found, None otherwise
        """
        return None

    # Server operations

    def save_server(self, server: "ServerDefinition") -> None:
        """Save a server definition.

        Args:
            server: The server definition to save
        """

    def get_server(self, server_id: str) -> Optional["ServerDefinition"]:
        """Get a server by ID.

        Args:
            server_id: The server's unique identifier

        Returns:
            The server if found, None otherwise
        """
        return None

    def update_server_ping(self, server_id: str, ping_time: int) -> None:
        """Update a server's ping time.

        Args:
            server_id: The server's unique identifier
            ping_time: The new ping time in milliseconds
        """
