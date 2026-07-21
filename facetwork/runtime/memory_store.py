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

"""In-memory implementation of PersistenceAPI for testing."""

import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from typing import Optional

from .entities import (
    HandledCount,
    HandlerRegistration,
    LogDefinition,
    RunnerDefinition,
    ServerDefinition,
    StepLogEntry,
    TaskDefinition,
)
from .persistence import IterationChanges, PersistenceAPI
from .step import StepDefinition
from .types import BlockId, StepId


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


class MemoryStore(PersistenceAPI):
    """In-memory implementation of the persistence API.

    Used for testing without external database dependencies.
    All data is stored in dictionaries and cleared on restart.
    """

    def __init__(self):
        """Initialize empty stores."""
        self._steps: dict[str, StepDefinition] = {}

        # Indexes for efficient queries (keyed by str to accept all ID NewTypes)
        self._steps_by_block: dict[str, list[StepId]] = defaultdict(list)
        self._steps_by_workflow: dict[str, list[StepId]] = defaultdict(list)
        self._steps_by_container: dict[str, list[StepId]] = defaultdict(list)
        self._blocks_by_step: dict[str, list[StepId]] = defaultdict(list)
        self._steps_by_statement: dict[str, StepId] = {}  # statement_id+block_id -> step_id

        # New stores for extended entities
        self._runners: dict[str, RunnerDefinition] = {}
        self._tasks: dict[str, TaskDefinition] = {}
        self._logs: list[LogDefinition] = []
        self._servers: dict[str, ServerDefinition] = {}
        self._handler_registrations: dict[str, HandlerRegistration] = {}
        self._step_logs: list[StepLogEntry] = []

        # Lock for atomic task claiming
        self._claim_lock = threading.Lock()

    def get_step(self, step_id: str) -> StepDefinition | None:
        """Fetch a step by ID."""
        step = self._steps.get(step_id)
        if step:
            return step.clone()  # Return a copy for isolation
        return None

    def get_steps_by_block(self, block_id: StepId | BlockId) -> Sequence[StepDefinition]:
        """Fetch all steps in a block."""
        step_ids = self._steps_by_block.get(block_id, [])
        return [self._steps[sid].clone() for sid in step_ids if sid in self._steps]

    def get_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps in a workflow."""
        step_ids = self._steps_by_workflow.get(workflow_id, [])
        return [self._steps[sid].clone() for sid in step_ids if sid in self._steps]

    def get_actionable_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch steps that need processing in an evaluator iteration."""
        from .states import StepState

        step_ids = self._steps_by_workflow.get(workflow_id, [])
        result = []
        for sid in step_ids:
            s = self._steps.get(sid)
            if s is None:
                continue
            if StepState.is_terminal(s.state):
                continue
            if s.state == StepState.EVENT_TRANSMIT and not s.transition.is_requesting_state_change:
                continue
            result.append(s.clone())
        return result

    def get_steps_by_state(self, state: str) -> Sequence[StepDefinition]:
        """Fetch all steps in a given state."""
        return [s.clone() for s in self._steps.values() if s.state == state]

    def get_steps_by_container(self, container_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps with a given container."""
        step_ids = self._steps_by_container.get(container_id, [])
        return [self._steps[sid].clone() for sid in step_ids if sid in self._steps]

    def save_step(self, step: StepDefinition) -> None:
        """Save a step with a store-authoritative version bump.

        Mirrors MongoStore.save_step's server-side ``$inc``: the persisted
        sequence advances monotonically from the STORED value on every save,
        regardless of what the caller's in-memory copy carries — a stale
        caller can never regress the counter. Keep in behavioral lockstep
        with the Mongo store (lessons-learned §23).
        """
        now = _current_time_ms()
        if not step.start_time:
            step.start_time = now
        step.last_modified = now
        existing = self._steps.get(step.id)
        stored_seq = existing.version.sequence if existing and existing.version else 0
        if step.version is None:
            from .types import VersionInfo

            step.version = VersionInfo()
        step.version.sequence = stored_seq + 1
        # Remove from old indexes if updating
        if step.id in self._steps:
            old_step = self._steps[step.id]
            self._remove_from_indexes(old_step)

        # Store the step
        self._steps[step.id] = step.clone()

        # Update indexes
        self._add_to_indexes(step)

    def delete_steps(self, step_ids: Sequence[str]) -> int:
        """Delete steps by their UUIDs."""
        count = 0
        for sid in step_ids:
            step = self._steps.get(sid)
            if step:
                self._remove_from_indexes(step)
                del self._steps[sid]
                count += 1
        return count

    def delete_tasks_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete tasks associated with the given step IDs."""
        ids_set = set(step_ids)
        to_remove = [tid for tid, t in self._tasks.items() if t.step_id in ids_set]
        for tid in to_remove:
            self._tasks.pop(tid)
        return len(to_remove)

    def delete_pending_continuations_for_step(self, step_id: str, except_task_id: str = "") -> int:
        """Delete PENDING continuation tasks for ``step_id`` except the given one
        (claim-time continuation coalescing). See the base-class docstring."""
        from .continuation import CONTINUATION_TASK_NAME
        from .entities import TaskState

        to_remove = [
            tid
            for tid, t in self._tasks.items()
            if t.step_id == step_id
            and getattr(t, "name", None) == CONTINUATION_TASK_NAME
            and t.state == TaskState.PENDING
            and t.uuid != except_task_id
        ]
        for tid in to_remove:
            self._tasks.pop(tid, None)
        return len(to_remove)

    def delete_step_logs_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete step log entries for the given step IDs."""
        ids_set = set(step_ids)
        before = len(self._step_logs)
        self._step_logs = [e for e in self._step_logs if e.step_id not in ids_set]
        return before - len(self._step_logs)

    def _add_to_indexes(self, step: StepDefinition) -> None:
        """Add step to all indexes."""
        self._steps_by_workflow[step.workflow_id].append(step.id)

        if step.block_id:
            self._steps_by_block[step.block_id].append(step.id)

        if step.container_id:
            self._steps_by_container[step.container_id].append(step.id)
            # If this is a block step, also index it
            if step.is_block:
                self._blocks_by_step[step.container_id].append(step.id)

        # Statement index for idempotency
        if step.statement_id:
            key = self._statement_key(str(step.statement_id), step.block_id)
            self._steps_by_statement[key] = step.id

    def _remove_from_indexes(self, step: StepDefinition) -> None:
        """Remove step from all indexes."""
        if step.id in self._steps_by_workflow.get(step.workflow_id, []):
            self._steps_by_workflow[step.workflow_id].remove(step.id)

        if step.block_id and step.id in self._steps_by_block.get(step.block_id, []):
            self._steps_by_block[step.block_id].remove(step.id)

        if step.container_id and step.id in self._steps_by_container.get(step.container_id, []):
            self._steps_by_container[step.container_id].remove(step.id)

        if (
            step.container_id
            and step.is_block
            and step.id in self._blocks_by_step.get(step.container_id, [])
        ):
            self._blocks_by_step[step.container_id].remove(step.id)

        if step.statement_id:
            key = self._statement_key(str(step.statement_id), step.block_id)
            if key in self._steps_by_statement:
                del self._steps_by_statement[key]

    def _statement_key(self, statement_id: str, block_id: StepId | BlockId | None) -> str:
        """Create a unique key for statement+block combination."""
        block_str = str(block_id) if block_id else "root"
        return f"{statement_id}:{block_str}"

    def get_blocks_by_step(self, step_id: str) -> Sequence[StepDefinition]:
        """Fetch all block steps for a containing step."""
        block_ids = self._blocks_by_step.get(step_id, [])
        return [self._steps[bid].clone() for bid in block_ids if bid in self._steps]

    def commit(self, changes: IterationChanges) -> None:
        """Atomically commit all iteration changes.

        For the in-memory store, this just saves all changes.
        A real implementation would use transactions.
        """
        # Save created steps
        for step in changes.created_steps:
            self.save_step(step)

        # Save updated steps
        for step in changes.updated_steps:
            self.save_step(step)

        # Save created tasks
        for task in changes.created_tasks:
            self.save_task(task)

        # Save continuation tasks
        for task in changes.continuation_tasks:
            self.save_task(task)

    def get_workflow_root(self, workflow_id: str) -> StepDefinition | None:
        """Get the root step of a workflow."""
        step_ids = self._steps_by_workflow.get(workflow_id, [])
        for sid in step_ids:
            step = self._steps.get(sid)
            if step and step.root_id is None and step.container_id is None:
                return step.clone()
        return None

    def step_exists(self, statement_id: str, block_id: StepId | BlockId | None) -> bool:
        """Check if a step already exists for a statement in a block."""
        key = self._statement_key(statement_id, block_id)
        return key in self._steps_by_statement

    def block_step_exists(self, statement_id: str, container_id: StepId) -> bool:
        """Check if a block step already exists for a statement in a container."""
        for step in self._steps.values():
            if str(step.statement_id) == statement_id and step.container_id == container_id:
                return True
        return False

    # Utility methods for testing

    def clear(self) -> None:
        """Clear all stored data."""
        self._steps.clear()
        self._steps_by_block.clear()
        self._steps_by_workflow.clear()
        self._steps_by_container.clear()
        self._blocks_by_step.clear()
        self._steps_by_statement.clear()
        self._runners.clear()
        self._tasks.clear()
        self._logs.clear()
        self._servers.clear()
        self._handler_registrations.clear()
        self._step_logs.clear()

    def step_count(self) -> int:
        """Get total number of steps."""
        return len(self._steps)

    def get_all_steps(self) -> list[StepDefinition]:
        """Get all steps (for testing)."""
        return [s.clone() for s in self._steps.values()]

    # =========================================================================
    # Runner Operations
    # =========================================================================

    def get_runner(self, runner_id: str) -> Optional["RunnerDefinition"]:
        """Get a runner by ID."""
        return self._runners.get(runner_id)

    def save_runner(self, runner: "RunnerDefinition") -> None:
        """Save a runner."""
        self._runners[runner.uuid] = runner

    def delete_runner(self, runner_id: str) -> dict:
        """Delete a runner and all of its execution data (steps/tasks/logs)."""
        runner = self._runners.get(runner_id)
        if runner is None:
            return {
                "found": False,
                "runners": 0,
                "steps": 0,
                "tasks": 0,
                "step_logs": 0,
                "logs": 0,
            }
        workflow_id = runner.workflow_id
        step_ids = list(self._steps_by_workflow.get(workflow_id, []))
        tasks = self.delete_tasks_for_steps(step_ids)
        step_logs = self.delete_step_logs_for_steps(step_ids)
        steps = self.delete_steps(step_ids)
        before_logs = len(self._logs)
        self._logs = [log for log in self._logs if log.runner_id != runner_id]
        logs = before_logs - len(self._logs)
        del self._runners[runner_id]
        return {
            "found": True,
            "runners": 1,
            "steps": steps,
            "tasks": tasks,
            "step_logs": step_logs,
            "logs": logs,
        }

    def get_runners_by_state(self, state: str) -> Sequence["RunnerDefinition"]:
        """Get runners by state."""
        return [r for r in self._runners.values() if r.state == state]

    def get_runners_by_workflow(self, workflow_id: str) -> Sequence["RunnerDefinition"]:
        """Get all runners for a workflow."""
        return [r for r in self._runners.values() if r.workflow_id == workflow_id]

    # =========================================================================
    # Task Operations
    # =========================================================================

    def get_pending_tasks(self, task_list: str) -> Sequence["TaskDefinition"]:
        """Get pending tasks for a task list."""
        return [
            t
            for t in self._tasks.values()
            if t.task_list_name == task_list and t.state == "pending"
        ]

    def get_task_for_step(self, step_id: str) -> Optional["TaskDefinition"]:
        """Get the most recent task associated with a step."""
        matching = [t for t in self._tasks.values() if t.step_id == step_id]
        if not matching:
            return None
        return max(matching, key=lambda t: t.created)

    def get_tasks_by_step(self, step_id: str) -> list["TaskDefinition"]:
        """Get all tasks associated with a step."""
        return [t for t in self._tasks.values() if t.step_id == step_id]

    def get_tasks_by_workflow(self, workflow_id: str) -> list["TaskDefinition"]:
        """Get all tasks for a workflow (used by the terminal-state guard)."""
        return [t for t in self._tasks.values() if t.workflow_id == workflow_id]

    def get_task(self, task_id: str) -> Optional["TaskDefinition"]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def save_task(self, task: "TaskDefinition") -> None:
        """Save a task."""
        self._tasks[task.uuid] = task

    def save_task_if_owned(self, task: "TaskDefinition", expected_server_id: str) -> bool:
        """Save a task only if its current server_id still matches.

        Mirrors MongoStore.save_task_if_owned. When ``expected_server_id``
        is empty, upserts unconditionally.
        """
        if expected_server_id == "":
            self._tasks[task.uuid] = task
            return True
        existing = self._tasks.get(task.uuid)
        if existing is None or getattr(existing, "server_id", "") != expected_server_id:
            return False
        self._tasks[task.uuid] = task
        return True

    def get_all_tasks(
        self, limit: int = 100, task_list: str | None = None
    ) -> list["TaskDefinition"]:
        """Get all tasks, most recently created first.

        When *task_list* is given, only tasks on that list are returned.
        """
        tasks = sorted(self._tasks.values(), key=lambda t: t.created, reverse=True)
        if task_list:
            tasks = [t for t in tasks if t.task_list_name == task_list]
        return tasks[:limit]

    def get_tasks_by_state(
        self, state: str, task_list: str | None = None
    ) -> list["TaskDefinition"]:
        """Get tasks by state, optionally narrowed to one task list."""
        return [
            t
            for t in self._tasks.values()
            if t.state == state and (not task_list or t.task_list_name == task_list)
        ]

    def duplicate_completion_count(self) -> int:
        """Count step_ids that have more than one ``completed`` task."""
        from collections import Counter

        counts: Counter[str] = Counter()
        for t in self._tasks.values():
            if t.state == "completed" and t.step_id:
                counts[t.step_id] += 1
        return sum(1 for v in counts.values() if v > 1)

    def task_list_counts(self) -> dict[str, int]:
        """Return ``{task_list_name: count}`` across all tasks."""
        out: dict[str, int] = {}
        for t in self._tasks.values():
            out[t.task_list_name] = out.get(t.task_list_name, 0) + 1
        return out

    def get_tasks_by_server_id(self, server_id: str, limit: int = 200) -> list["TaskDefinition"]:
        """Get tasks claimed by a specific server, most recent first."""
        tasks = [t for t in self._tasks.values() if getattr(t, "server_id", "") == server_id]
        tasks.sort(key=lambda t: getattr(t, "updated", 0), reverse=True)
        return tasks[:limit]

    def claim_task(
        self,
        task_names: list[str],
        task_list: str | list[str] = "default",
        server_id: str = "",
        provided_environments: list[str] | None = None,
    ) -> Optional["TaskDefinition"]:
        """Atomically claim a pending task matching one of the given names.

        Environment routing (script-environments.md §3): a task tagged with a
        non-default ``environment_hash`` is claimable only when the hash is in
        ``provided_environments``; untagged tasks are claimable by everyone.
        Mirrors MongoStore.claim_task — keep the two in behavioral lockstep.
        """
        provided = set(provided_environments or [])
        with self._claim_lock:
            names_set = set(task_names)
            tl_set = set(task_list) if isinstance(task_list, (list, tuple, set)) else {task_list}
            for task in self._tasks.values():
                now = _current_time_ms()
                env = getattr(task, "environment_hash", "") or ""
                if (
                    task.state == "pending"
                    and task.name in names_set
                    and task.task_list_name in tl_set
                    and (env == "" or env in provided)
                    and (task.next_retry_after == 0 or task.next_retry_after <= now)
                ):
                    task.state = "running"
                    task.updated = _current_time_ms()
                    if server_id:
                        task.server_id = server_id
                    return task
            return None

    def get_pending_script_environment_demand(self) -> list[tuple[str, str]]:
        """Distinct (environment_hash, workflow_id) of pending script tasks.

        Mirrors MongoStore — keep in behavioral lockstep."""
        seen: dict[str, str] = {}
        for task in self._tasks.values():
            env = getattr(task, "environment_hash", "") or ""
            if task.state == "pending" and getattr(task, "kind", "") == "script" and env:
                seen.setdefault(env, task.workflow_id)
        return sorted(seen.items())

    def claim_script_task(
        self,
        provided_environments,
        server_id: str = "",
    ) -> Optional["TaskDefinition"]:
        """Atomically claim a pending env-routed script task (see base class).

        Mirrors MongoStore.claim_script_task — keep in behavioral lockstep.
        """
        provided = set(provided_environments or [])
        if not provided:
            return None
        with self._claim_lock:
            now = _current_time_ms()
            for task in self._tasks.values():
                if (
                    task.state == "pending"
                    and getattr(task, "kind", "") == "script"
                    and getattr(task, "environment_hash", "") in provided
                    and (task.next_retry_after == 0 or task.next_retry_after <= now)
                ):
                    task.state = "running"
                    task.updated = now
                    if server_id:
                        task.server_id = server_id
                    return task
            return None

    # =========================================================================
    # Log Operations
    # =========================================================================

    def save_log(self, log: "LogDefinition") -> None:
        """Save a log entry."""
        self._logs.append(log)

    def get_logs_by_runner(self, runner_id: str) -> Sequence["LogDefinition"]:
        """Get logs for a runner."""
        return [log for log in self._logs if log.runner_id == runner_id]

    # =========================================================================
    # Step Log Operations
    # =========================================================================

    def save_step_log(self, entry: StepLogEntry) -> None:
        """Save a step log entry."""
        self._step_logs.append(entry)

    def get_step_logs_by_step(self, step_id: str) -> Sequence[StepLogEntry]:
        """Get step logs for a step, ordered by time ascending."""
        return sorted(
            [e for e in self._step_logs if e.step_id == step_id],
            key=lambda e: e.time,
        )

    def get_step_logs_by_workflow(self, workflow_id: str) -> Sequence[StepLogEntry]:
        """Get step logs for a workflow, ordered by time ascending."""
        return sorted(
            [e for e in self._step_logs if e.workflow_id == workflow_id],
            key=lambda e: e.time,
        )

    def get_step_logs_since(self, step_id: str, since_time: int) -> Sequence[StepLogEntry]:
        """Get step logs for a step newer than the given timestamp."""
        return sorted(
            [e for e in self._step_logs if e.step_id == step_id and e.time > since_time],
            key=lambda e: e.time,
        )

    def get_workflow_logs_since(self, workflow_id: str, since_time: int) -> Sequence[StepLogEntry]:
        """Get step logs for a workflow newer than the given timestamp."""
        return sorted(
            [e for e in self._step_logs if e.workflow_id == workflow_id and e.time > since_time],
            key=lambda e: e.time,
        )

    def get_tasks_by_facet_name(
        self, facet_name: str, states: list[str] | None = None
    ) -> list[TaskDefinition]:
        """Get tasks matching a facet name, optionally filtered by states."""
        result = [t for t in self._tasks.values() if t.name == facet_name]
        if states:
            states_set = set(states)
            result = [t for t in result if t.state in states_set]
        return sorted(result, key=lambda t: t.created, reverse=True)

    def get_step_logs_by_facet(self, facet_name: str, limit: int = 20) -> list[StepLogEntry]:
        """Get recent step logs for a facet, ordered by time descending."""
        matching = [e for e in self._step_logs if e.facet_name == facet_name]
        matching.sort(key=lambda e: e.time, reverse=True)
        return matching[:limit]

    # =========================================================================
    # Server Operations
    # =========================================================================

    def get_server(self, server_id: str) -> Optional["ServerDefinition"]:
        """Get a server by ID."""
        return self._servers.get(server_id)

    def save_server(self, server: "ServerDefinition") -> None:
        """Save a server."""
        self._servers[server.uuid] = server

    def prune_stale_servers(
        self,
        older_than_ms: int = 600_000,
        terminal_older_than_ms: int | None = None,
    ) -> int:
        """Delete ServerDefinition records whose last ping is stale.

        Live (``running``/``startup``) rows use the generous ``older_than_ms``
        window; explicitly-dead ``shutdown`` rows use the shorter
        ``terminal_older_than_ms`` (default = ``older_than_ms``) so they don't
        linger and inflate counts. Returns the count deleted. Mirrors
        ``MongoStore.prune_stale_servers``."""
        import time as _t

        from .entities import ServerState

        if terminal_older_than_ms is None:
            terminal_older_than_ms = older_than_ms
        now = int(_t.time() * 1000)
        live_cutoff = now - older_than_ms
        terminal_cutoff = now - terminal_older_than_ms
        stale = []
        for uuid, s in self._servers.items():
            ping = getattr(s, "ping_time", 0)
            cutoff = (
                terminal_cutoff
                if getattr(s, "state", None) == ServerState.SHUTDOWN
                else live_cutoff
            )
            if ping < cutoff:
                stale.append(uuid)
        for uuid in stale:
            del self._servers[uuid]
        return len(stale)

    def dispatchable_facet_names(self, fresh_window_ms: int = 60_000) -> set[str]:
        """Union of handler facet names across all live runners."""
        import time as _t

        cutoff = int(_t.time() * 1000) - fresh_window_ms
        names: set[str] = set()
        for s in self._servers.values():
            if getattr(s, "ping_time", 0) > cutoff:
                for h in getattr(s, "handlers", []) or []:
                    names.add(h)
        return names

    def runners_per_task_list(self, fresh_window_ms: int = 60_000) -> dict[str, int]:
        """Return ``{task_list: live_runner_count}``."""
        import time as _t

        cutoff = int(_t.time() * 1000) - fresh_window_ms
        out: dict[str, int] = {}
        for s in self._servers.values():
            if getattr(s, "ping_time", 0) > cutoff:
                tl = getattr(s, "task_list", "default") or "default"
                out[tl] = out.get(tl, 0) + 1
        return out

    def update_server_ping(self, server_id: str, ping_time: int) -> None:
        """Update server ping time."""
        if server_id in self._servers:
            self._servers[server_id].ping_time = ping_time

    def heartbeat_server(self, server_id: str, ping_time: int) -> bool:
        """Update ping time iff the record is live; report whether it was.

        Mirrors ``MongoStore.heartbeat_server``: False means the record is
        missing or terminal and the runner should re-register.
        """
        from .entities import ServerState

        server = self._servers.get(server_id)
        if server is None or getattr(server, "state", None) == ServerState.SHUTDOWN:
            return False
        server.ping_time = ping_time
        return True

    def update_server_handled(self, server_id: str, handled: "list[HandledCount]") -> None:
        """Replace ONLY the server's ``handled`` stats (targeted field write)."""
        if server_id in self._servers:
            self._servers[server_id].handled = list(handled)

    def get_all_servers(self) -> list["ServerDefinition"]:
        """Get all servers."""
        return list(self._servers.values())

    # =========================================================================
    # Handler Registration Operations
    # =========================================================================

    def save_handler_registration(self, registration: HandlerRegistration) -> None:
        """Upsert a handler registration by facet_name."""
        self._handler_registrations[registration.facet_name] = registration

    def get_handler_registration(self, facet_name: str) -> HandlerRegistration | None:
        """Get a handler registration by facet name."""
        return self._handler_registrations.get(facet_name)

    def list_handler_registrations(self) -> list[HandlerRegistration]:
        """List all handler registrations."""
        return list(self._handler_registrations.values())

    def delete_handler_registration(self, facet_name: str) -> bool:
        """Delete a handler registration by facet name."""
        if facet_name in self._handler_registrations:
            del self._handler_registrations[facet_name]
            return True
        return False
