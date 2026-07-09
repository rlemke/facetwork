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

"""Shared base for the runner classes.

``RunnerService`` and ``RegistryRunner`` historically duplicated a large amount
of identical logic (server lifecycle, heartbeat, work-item accounting). Keeping
two copies let them drift — the source of several distributed-systems bugs where
one runner had the correct behavior and the other didn't. ``BaseRunner`` holds
the logic that is (or should be) identical across both, so there is ONE
implementation.

Subclasses set the attributes this base references in their own ``__init__``:

    _server_id: str
    _running: bool
    _persistence: PersistenceAPI
    _config: BaseRunnerConfig
    _stopping: threading.Event
    _active_lock: threading.Lock
    _active_futures: list

(These are not initialized here so each subclass keeps full control of its
construction; the base only reads them.)
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .entities import RunnerState, ServerState, TaskState
from .evaluator import ExecutionStatus

if TYPE_CHECKING:
    from .persistence import PersistenceAPI
    from .runner_config import BaseRunnerConfig

logger = logging.getLogger(__name__)


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


class BaseRunner:
    """Server-lifecycle and accounting logic shared by all Facetwork runners."""

    # Attributes the base reads, set by each subclass's __init__ (declared here
    # for type-checkers / readers; not assigned so subclasses own construction).
    _server_id: str
    _running: bool
    _persistence: PersistenceAPI
    _config: BaseRunnerConfig
    _stopping: threading.Event
    _active_lock: threading.Lock
    _active_futures: list
    # Per-workflow resume locks + pending-requeue set (see _resume_with_lock).
    _resume_locks: dict[str, threading.Lock]
    _resume_locks_lock: threading.Lock
    _resume_pending: set[str]
    _resume_pending_lock: threading.Lock

    @property
    def server_id(self) -> str:
        """Get the server's unique ID."""
        return self._server_id

    @property
    def is_running(self) -> bool:
        """Check if the runner is currently running."""
        return self._running

    def _deregister_server(self) -> None:
        """Mark this server as shut down."""
        server = self._persistence.get_server(self._server_id)
        if server:
            server.state = ServerState.SHUTDOWN
            server.ping_time = _current_time_ms()
            self._persistence.save_server(server)

    def _get_server_ips(self) -> list[str]:
        """Get local IP addresses."""
        try:
            hostname = socket.gethostname()
            return [socket.gethostbyname(hostname)]
        except Exception:
            return []

    def _heartbeat_loop(self) -> None:
        """Periodically update the server's ping_time."""
        interval_s = self._config.heartbeat_interval_ms / 1000.0
        while not self._stopping.wait(interval_s):
            try:
                self._persistence.update_server_ping(self._server_id, _current_time_ms())
            except Exception:
                logger.exception("Heartbeat failed")

    def _active_count(self) -> int:
        """Get the number of active work items."""
        with self._active_lock:
            return len(self._active_futures)

    # =========================================================================
    # Retry / dead-letter transitions (shared resilience contract)
    # =========================================================================

    def _safe_save_task(self, task: Any, retries: int = 3) -> None:
        """Save task state with retries to survive transient DB failures.

        Terminal writes (``completed``/``failed``/``canceled``/``dead_letter``)
        go through the ownership-gated path: only this runner — the one whose
        server_id is currently on the doc — may write the result. A handler
        whose lease was reclaimed under it gets silently dropped here rather
        than overwriting the new claimer's state. Non-terminal writes
        (heartbeat fields, retry resets that explicitly clear server_id) keep
        going through the unconditional path because they're orchestration
        and not the lease-reclaim race.
        """
        terminal_states = (
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELED,
            TaskState.DEAD_LETTER,
        )
        gate = task.state in terminal_states and getattr(task, "server_id", "") == self._server_id
        for attempt in range(retries):
            try:
                if gate and hasattr(self._persistence, "save_task_if_owned"):
                    accepted = self._persistence.save_task_if_owned(task, self._server_id)
                    if not accepted:
                        logger.warning(
                            "Dropped terminal write for task %s (name=%s, state=%s): "
                            "lease was reclaimed by another server — likely a slow "
                            "handler whose work has already been redone elsewhere",
                            task.uuid,
                            task.name,
                            task.state,
                        )
                else:
                    self._persistence.save_task(task)
                return
            except Exception:
                if attempt < retries - 1:
                    logger.warning(
                        "save_task failed for %s (attempt %d/%d), retrying",
                        task.uuid,
                        attempt + 1,
                        retries,
                    )
                    time.sleep(0.5 * (attempt + 1))
                else:
                    label = task.name or task.uuid[:12]
                    logger.error(
                        "save_task failed for %s after %d attempts, task may be stuck — %s",
                        task.uuid,
                        retries,
                        label,
                        exc_info=True,
                    )

    def _transition_for_retry(
        self,
        task: Any,
        *,
        dead_letter_state: str = TaskState.DEAD_LETTER,
        set_next_retry_after: bool = False,
        clear_error_on_retry: bool = True,
    ) -> bool:
        """Apply the retry-or-dead-letter state transition to ``task``.

        - Increments ``retry_count`` and stamps ``updated``.
        - If ``max_retries`` has been reached: sets ``state`` to
          ``dead_letter_state`` (defaults to ``DEAD_LETTER``; the "no
          handler anywhere" site passes ``FAILED`` for finality).
        - Otherwise: ``state = PENDING``, clears ``server_id``, optionally
          clears ``error`` (the generic-exception site keeps the prior
          error message for the retry attempt), optionally computes
          ``next_retry_after`` via exponential backoff (the timeout and
          ImportError sites don't — they're already paced by their outer
          watchdog cadence).

        Returns ``True`` if the task was dead-lettered. The caller is
        responsible for ``task.error`` payload, calling ``fail_step``,
        logging, and ``_safe_save_task`` — those differ enough at every
        site that lifting them in here would make the code harder to
        read, not easier.
        """
        task.retry_count += 1
        task.updated = _current_time_ms()
        is_dead_letter = task.max_retries > 0 and task.retry_count >= task.max_retries
        if is_dead_letter:
            task.state = dead_letter_state
            return True
        task.state = TaskState.PENDING
        task.server_id = ""
        if clear_error_on_retry:
            task.error = None
        if set_next_retry_after:
            from facetwork.runtime.mongo_store import _compute_next_retry_after

            task.next_retry_after = _compute_next_retry_after(task.retry_count, task.updated)
        return False

    # =========================================================================
    # Workflow resume (non-blocking, bulkheaded)
    # =========================================================================

    def _resume_with_lock(self, workflow_id: str, resume_fn: Callable[[], None]) -> None:
        """Run ``resume_fn()`` under a NON-BLOCKING per-workflow lock.

        If another thread already holds this workflow's resume lock, the
        workflow is marked pending and the call returns immediately — the
        holder re-runs ``resume_fn`` for any pending flag after its current
        iteration. This bulkheads the poll/handler threads: they never BLOCK
        waiting on a contended per-workflow lock (which under a wide fan-out
        parks the whole worker pool — the convoy this replaces), and no resume
        request is lost. The stuck-step sweep remains the ultimate safety net.
        """
        with self._resume_locks_lock:
            lock = self._resume_locks.setdefault(workflow_id, threading.Lock())

        if not lock.acquire(blocking=False):
            with self._resume_pending_lock:
                self._resume_pending.add(workflow_id)
            logger.debug("Resume already in progress for workflow %s, marked pending", workflow_id)
            return

        try:
            resume_fn()
            # Re-run if other threads flagged a pending resume while we held it.
            while True:
                with self._resume_pending_lock:
                    if workflow_id not in self._resume_pending:
                        break
                    self._resume_pending.discard(workflow_id)
                resume_fn()
        finally:
            lock.release()

    # =========================================================================
    # Runner terminal-state transition (guarded)
    # =========================================================================

    def _has_non_terminal_tasks(self, workflow_id: str) -> bool:
        """Whether a workflow has any task not in a terminal state."""
        terminal = {TaskState.COMPLETED, TaskState.FAILED, TaskState.IGNORED, TaskState.CANCELED}
        if not hasattr(self._persistence, "get_tasks_by_workflow"):
            return False
        tasks = self._persistence.get_tasks_by_workflow(workflow_id)
        return any(t.state not in terminal for t in tasks)

    def _update_runner_terminal_state(self, workflow_id: str, status: str) -> None:
        """Move the workflow's runner entities to a terminal state.

        Guards COMPLETED with ``_has_non_terminal_tasks``: if the evaluator
        reports COMPLETED but tasks are still non-terminal (a lease-reclaimed
        zombie, an in-flight dead-letter, a step being retried), the runners are
        LEFT in RUNNING rather than prematurely completed — matching the
        preventative "verify all tasks terminal before COMPLETED" contract.
        Previously RunnerService completed purely on status, with no such guard.
        """
        if not hasattr(self._persistence, "get_runners_by_workflow"):
            return
        try:
            now = _current_time_ms()
            target_state = (
                RunnerState.COMPLETED
                if status == ExecutionStatus.COMPLETED
                else RunnerState.FAILED
            )
            if target_state == RunnerState.COMPLETED and self._has_non_terminal_tasks(workflow_id):
                logger.warning(
                    "Workflow %s: evaluator says COMPLETED but non-terminal tasks remain; "
                    "keeping runners in RUNNING state",
                    workflow_id,
                )
                return
            for runner in self._persistence.get_runners_by_workflow(workflow_id):
                if runner.state not in (RunnerState.COMPLETED, RunnerState.FAILED):
                    runner.state = target_state
                    runner.end_time = now
                    runner.duration = now - runner.start_time if runner.start_time else 0
                    self._persistence.save_runner(runner)
                    logger.info(
                        "Runner %s updated to %s for workflow %s",
                        runner.uuid,
                        target_state,
                        workflow_id,
                    )
        except Exception:
            logger.debug("Could not update runners for workflow %s", workflow_id, exc_info=True)
