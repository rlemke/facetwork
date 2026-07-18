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
        HandledCount,
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
    continuation_tasks: list["TaskDefinition"] = field(default_factory=list)

    # Track step IDs to avoid duplicates
    _created_ids: set[StepId] = field(default_factory=set)
    _updated_ids: dict[StepId, int] = field(default_factory=dict)
    _continuation_step_ids: set[str] = field(default_factory=set)

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

    def add_continuation_task(self, task: "TaskDefinition") -> None:
        """Record a continuation task (deduplicated by target step_id)."""
        if task.step_id not in self._continuation_step_ids:
            self._continuation_step_ids.add(task.step_id)
            self.continuation_tasks.append(task)

    @property
    def has_changes(self) -> bool:
        """Check if there are any changes to commit."""
        return (
            len(self.created_steps) > 0
            or len(self.updated_steps) > 0
            or len(self.created_tasks) > 0
            or len(self.continuation_tasks) > 0
        )

    def clear(self) -> None:
        """Clear all accumulated changes."""
        self.created_steps.clear()
        self.updated_steps.clear()
        self.created_tasks.clear()
        self.continuation_tasks.clear()
        self._created_ids.clear()
        self._updated_ids.clear()
        self._continuation_step_ids.clear()


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

    def get_stuck_steps_for_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch steps in a workflow that need an external nudge to progress.

        Returns steps at ``EventTransmit`` (waiting for handler dispatch)
        or at intermediate block-execution states the evaluator left
        partway. Used by the runner's stuck-step sweep — distinct from
        ``get_actionable_steps_by_workflow`` (which is the per-iteration
        view from inside the evaluator). Subclasses with a database
        backend should override to push filtering server-side.
        """
        from .states import StepState

        stuck_states = {
            StepState.EVENT_TRANSMIT,
            StepState.STATEMENT_BLOCKS_BEGIN,
            StepState.STATEMENT_BLOCKS_CONTINUE,
            StepState.BLOCK_EXECUTION_BEGIN,
            StepState.BLOCK_EXECUTION_CONTINUE,
        }
        return [s for s in self.get_steps_by_workflow(workflow_id) if s.state in stuck_states]

    def get_pending_resume_workflow_ids(self) -> list[str]:
        """Get workflow IDs that have steps needing resume processing.

        Finds steps at EventTransmit with pending transitions, or at
        intermediate states (BlocksBegin/Continue) that need the
        evaluator to advance them.  The default implementation scans
        all steps; subclasses should override with an efficient query.

        Returns:
            Distinct workflow IDs needing resume
        """
        from .states import StepState

        # Begin states indicate a step that was advanced by continue_step
        # but not yet processed by the evaluator.
        intermediate_states = {
            StepState.STATEMENT_BLOCKS_BEGIN,
            StepState.BLOCK_EXECUTION_BEGIN,
        }
        seen: set[str] = set()
        for step in self.get_steps_by_state(StepState.EVENT_TRANSMIT):
            if step.transition.is_requesting_state_change and step.workflow_id not in seen:
                seen.add(step.workflow_id)
        for state in intermediate_states:
            for step in self.get_steps_by_state(state):
                if step.workflow_id not in seen:
                    seen.add(step.workflow_id)
        return list(seen)

    def get_pending_continuation_step_ids(self, workflow_id: str) -> "set[str]":
        """Return step_ids that already have a PENDING continuation task for
        this workflow.

        Used to suppress duplicate continuation tasks for the same block. On a
        large fan-out (a foreach with hundreds of sub-blocks) every child event
        would otherwise regenerate a continuation for the parent block, piling
        up duplicates that flood the runner thread pool and starve event-task
        execution — an O(N^2) "continuation storm" that livelocks the runner.
        The default scans the workflow's tasks; subclasses may override with an
        indexed query.
        """
        from .continuation import CONTINUATION_TASK_NAME
        from .entities import TaskState

        result: set[str] = set()
        for t in self.get_tasks_by_workflow(workflow_id):
            if (
                getattr(t, "name", None) == CONTINUATION_TASK_NAME
                and t.state == TaskState.PENDING
                and getattr(t, "step_id", None)
            ):
                result.add(t.step_id)
        return result

    def delete_pending_continuations_for_step(self, step_id: str, except_task_id: str = "") -> int:
        """Delete PENDING continuation tasks for ``step_id`` other than
        ``except_task_id`` and return how many were removed.

        Called when a runner *begins* processing a continuation for a step: that
        single re-evaluation already reflects the current state of all the step's
        children, so any other continuations queued for it are redundant.
        Deleting them — rather than letting each be claimed and processed to a
        no-op — is the claim-time half of continuation coalescing (the
        generation-time dedup in get_pending_continuation_step_ids is the other
        half, and also closes the race where two of them are enqueued at once).
        Continuations created *after* processing begins are unaffected, so new
        child events are never lost. Default is a no-op; stores override with an
        efficient delete.
        """
        return 0

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

    def delete_runner(self, runner_id: str) -> dict:
        """Delete a runner (workflow run) and all of its execution data.

        Cascades to the run's steps, tasks, step logs, and workflow logs so
        no orphaned documents are left behind and the stuck-step sweep stops
        re-processing them.

        Args:
            runner_id: The runner UUID to delete

        Returns:
            A dict of deleted counts plus ``found`` (False if no such runner).
        """
        raise NotImplementedError

    def delete_runners(self, runner_ids: Sequence[str]) -> dict:
        """Delete multiple runners and their execution data.

        Aggregates the per-runner counts from :meth:`delete_runner`. Missing
        runners are skipped. Returns ``{"deleted": N, ...aggregated counts}``.
        """
        totals = {"deleted": 0, "runners": 0, "steps": 0, "tasks": 0, "step_logs": 0, "logs": 0}
        for rid in runner_ids:
            res = self.delete_runner(rid)
            if res.get("found"):
                totals["deleted"] += 1
            for key in ("runners", "steps", "tasks", "step_logs", "logs"):
                totals[key] += res.get(key, 0)
        return totals

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

    def get_runners_by_workflow(self, workflow_id: str) -> Sequence["RunnerDefinition"]:
        """Get all runners for a workflow.

        Args:
            workflow_id: The workflow's unique identifier

        Returns:
            All runners associated with the workflow
        """
        return []

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

    def get_task(self, task_id: str) -> Optional["TaskDefinition"]:
        """Get a task by its unique identifier."""
        return None

    def get_tasks_by_server_id(
        self, server_id: str, limit: int = 200
    ) -> "Sequence[TaskDefinition]":
        """Get tasks claimed by a specific server, most recent first."""
        return []

    def get_tasks_by_workflow(self, workflow_id: str) -> "Sequence[TaskDefinition]":
        """Get all tasks for a workflow."""
        return []

    @abstractmethod
    def get_task_for_step(self, step_id: str) -> Optional["TaskDefinition"]:
        """Get the most recent task associated with a step.

        Args:
            step_id: The step's unique identifier

        Returns:
            The most recent task for the step, or None if not found
        """
        ...

    def has_active_task_for_step(self, step_id: str) -> bool:
        """Return True if any task for ``step_id`` is pending or running.

        Used by the stuck-step sweep to decide whether to create a fresh
        task for a parked ``EventTransmit`` step — only if no existing
        task is still in flight. Default scans tasks for the step;
        subclasses with a DB backend should push the filter server-side.
        """
        from .entities import TaskState

        for task in self.get_tasks_by_step(step_id):
            if task.state in (TaskState.PENDING, TaskState.RUNNING):
                return True
        return False

    def has_dead_letter_task_for_step(self, step_id: str) -> bool:
        """Return True if ``step_id`` has a DEAD_LETTER task.

        A dead-lettered task means the step's work was permanently abandoned
        (retries exhausted). The stuck-step sweep uses this so it FAILS such a
        step instead of resurrecting it with a fresh ``retry_count=0`` task —
        which would otherwise loop forever, since ``_dead_letter_overdue`` flips
        the task terminal but leaves the step at ``EventTransmit``.
        """
        from .entities import TaskState

        return any(t.state == TaskState.DEAD_LETTER for t in self.get_tasks_by_step(step_id))

    def get_tasks_by_step(self, step_id: str) -> "Sequence[TaskDefinition]":
        """Get all tasks associated with a step. Default returns empty."""
        return []

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
        task_list: str | list[str] = "default",
        server_id: str = "",
    ) -> Optional["TaskDefinition"]:
        """Atomically claim a pending task matching one of the given names.

        ``task_list`` may be a single list name or several (a runner polling the
        namespaces of its handlers); a task on any of them is eligible.

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

    def update_task_heartbeat(
        self,
        task_id: str,
        heartbeat_time: int,
        progress_pct: int | None = None,
        progress_message: str | None = None,
        expected_server_id: str = "",
    ) -> None:
        """Update a running task's heartbeat timestamp and renew lease.

        Handlers call this periodically during long-running operations so the
        orphan reaper knows the task is still making progress even if the
        server's heartbeat is stale (e.g. due to I/O contention).

        Optionally records ``progress_pct`` (0-100) and ``progress_message``
        for the stuck-task watchdog. When ``expected_server_id`` is given the
        renewal is ownership-gated so a lease-reclaimed zombie can't renew the
        new owner's lease.
        """
        return None

    def update_task_stage_budget(
        self,
        task_id: str,
        budget_expires: int,
        stage_name: str = "",
    ) -> None:
        """Set a deadline (``budget_expires``, ms epoch) for the task's current stage.

        Used by handlers that declare per-stage timeouts. Stores cease to be
        relevant for in-memory implementations, which may treat this as a no-op.
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

    def heartbeat_server(self, server_id: str, ping_time: int) -> bool:
        """Update a server's ping time iff its record is live.

        Unlike ``update_server_ping`` this reports whether a live (non-
        ``shutdown``) record was actually updated. A transiently-quiet runner
        can be marked ``shutdown`` by another runner's reaper and then pruned;
        after that a bare ping update silently no-ops and the runner works on
        invisible ("zombie") forever. Callers should re-register when this
        returns False.

        Args:
            server_id: The server's unique identifier
            ping_time: The new ping time in milliseconds

        Returns:
            True if a live record was updated; False if the record is
            missing or in a terminal state (caller should re-register).
        """
        server = self.get_server(server_id)
        if server is None or server.state == "shutdown":
            return False
        self.update_server_ping(server_id, ping_time)
        return True

    def update_server_handled(self, server_id: str, handled: "list[HandledCount]") -> None:
        """Replace ONLY a server's ``handled`` stats (a targeted field write).

        Unlike ``save_server`` this must not read-modify-write the whole server
        document, so a concurrent state change (e.g. a dashboard QUARANTINE)
        made between a runner's read and write is not silently reverted.

        Args:
            server_id: The server's unique identifier
            handled: The current per-handler counts to persist
        """
