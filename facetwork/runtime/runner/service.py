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

"""AFL distributed runner service.

A long-lived process that polls MongoDB for blocked steps and pending tasks,
dispatches events to registered ToolRegistry handlers, and resumes workflows
via the Evaluator.

Multiple instances can run concurrently on different machines, coordinated
through MongoDB atomic ``find_one_and_update`` task claiming and server
registration.
"""

import json as _json
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from ..agent import ToolRegistry
from ..entities import (
    HandledCount,
    RunnerState,
    ServerDefinition,
    ServerState,
    StepLogLevel,
    StepLogSource,
    TaskDefinition,
    TaskState,
)
from ..evaluator import Evaluator, ExecutionStatus
from ..persistence import PersistenceAPI
from ..states import StepState
from ..step import StepDefinition
from ..types import generate_id

logger = logging.getLogger(__name__)

RESUME_TASK_NAME = "fw:resume"


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


def _step_params_as_payload(step: StepDefinition) -> dict:
    """Build a flat ``{name: value}`` event payload from a step's params.

    Mirrors ``StatementBeginHandler._build_payload`` (the canonical event-
    task creator in ``handlers/completion.py``). Used by
    ``_sweep_workflow_steps`` when synthesizing a task for an
    EventTransmit step that never got one — handlers consume payload
    keys directly (``payload["x"]``), so the flat shape is the only one
    that works.
    """
    if not step.attributes:
        return {}
    return {name: attr.value for name, attr in step.attributes.params.items()}


def _reaper_message(task_info: dict[str, str], reclaimer_name: str = "") -> str:
    """Build a descriptive reaper step log message with timing diagnostics."""
    now = _current_time_ms()
    server_id = task_info.get("server_id", "")
    name = task_info.get("name", "unknown")

    parts = [f"Task reclaimed: {name} — previous server ({server_id[:8]}) stopped responding"]

    last_ping = int(task_info.get("last_ping_ms", "0"))
    if last_ping > 0:
        silent_s = (now - last_ping) / 1000
        parts.append(f"server silent for {silent_s:.0f}s")

    task_started = int(task_info.get("task_started_ms", "0"))
    if task_started > 0:
        running_s = (now - task_started) / 1000
        parts.append(f"task was running for {running_s:.0f}s")

    if reclaimer_name:
        parts.append(f"reclaimed by {reclaimer_name}")

    parts.append("resetting to pending")
    return ", ".join(parts)


def _stuck_message(task_info: dict[str, str], reclaimer_name: str = "") -> str:
    """Build a descriptive stuck-task watchdog log message."""
    now = _current_time_ms()
    name = task_info.get("name", "unknown")
    reason = task_info.get("reason", "stuck")
    timeout_ms = int(task_info.get("timeout_ms", "0"))

    if reason == "timeout":
        parts = [f"Task reclaimed: {name} — explicit timeout ({timeout_ms / 1000:.0f}s) exceeded"]
    else:
        parts = [f"Task reclaimed: {name} — no progress for {timeout_ms / 3_600_000:.1f}h"]

    task_started = int(task_info.get("task_started_ms", "0"))
    if task_started > 0:
        running_s = (now - task_started) / 1000
        parts.append(f"task was running for {running_s:.0f}s")

    if reclaimer_name:
        parts.append(f"reclaimed by {reclaimer_name}")

    parts.append("resetting to pending")
    return ", ".join(parts)


from ..base_runner import BaseRunner
from ..runner_config import BaseRunnerConfig

_SENTINEL = -1

# Per-invocation caps for the stuck-step sweep (runtime.md §10.4). The sweep runs
# synchronously on the poll thread; bounding it keeps it from starving event-task
# claiming on a large foreach fan-out. Matches RegistryRunner's caps.
_SWEEP_MAX_STEPS = 25
_SWEEP_MAX_MS = 1500


class _SweepBudget:
    """A shared per-invocation budget for the stuck-step sweep: at most
    ``max_steps`` steps and until ``deadline_ms`` wall-clock, across all
    workflows in one sweep pass."""

    __slots__ = ("remaining", "deadline_ms")

    def __init__(self, max_steps: int, deadline_ms: int) -> None:
        self.remaining = max_steps
        self.deadline_ms = deadline_ms

    def exhausted(self) -> bool:
        return self.remaining <= 0 or _current_time_ms() > self.deadline_ms

    def consume(self) -> None:
        self.remaining -= 1


class _ToolRegistryDispatcher:
    """Adapts a ``ToolRegistry`` to the ``HandlerDispatcher`` protocol so
    ``BaseRunner._process_continuation`` can dispatch inline on a RunnerService
    (which uses a ToolRegistry, not a RegistryDispatcher). Mirrors the
    qualified-then-short-name lookup RunnerService uses elsewhere.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def can_dispatch(self, facet_name: str) -> bool:
        return self._registry.has_handler(facet_name) or self._registry.has_handler(
            facet_name.rsplit(".", 1)[-1]
        )

    def dispatch(self, facet_name: str, payload: dict) -> dict | None:
        result = self._registry.handle(facet_name, payload)
        if result is None and "." in facet_name:
            result = self._registry.handle(facet_name.rsplit(".", 1)[-1], payload)
        return result


@dataclass
class RunnerConfig(BaseRunnerConfig):
    """Configuration for the RunnerService.

    Extends BaseRunnerConfig with HTTP status server and shutdown settings.
    """

    service_name: str = "afl-runner"
    shutdown_timeout_ms: int = 30000
    http_port: int = 8090
    http_max_port_attempts: int = 20


class _StatusHandler(BaseHTTPRequestHandler):
    """HTTP request handler for runner health/status endpoints."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json_response(200, {"ok": True})
        elif self.path == "/status":
            svc: RunnerService = self.server.runner_service  # type: ignore[attr-defined]
            now = _current_time_ms()
            uptime_ms = now - svc._start_time_ms if svc._start_time_ms else 0
            data = {
                "server_id": svc.server_id,
                "version": getattr(svc, "_version", "unknown"),
                "running": svc.is_running,
                "uptime_ms": uptime_ms,
                "handled": {
                    name: {"handled": c.handled, "not_handled": c.not_handled}
                    for name, c in svc._handled_counts.items()
                },
                "active_work_items": svc._active_count(),
                "execution_timeout_ms": svc._execution_timeout_ms,
                "circuit_breakers": svc._circuit_breakers.get_all_states(),
                "config": {
                    "server_group": svc._config.server_group,
                    "service_name": svc._config.service_name,
                    "server_name": svc._config.server_name,
                    "topics": svc._config.topics,
                    "max_concurrent": svc._config.max_concurrent,
                    "poll_interval_ms": svc._config.poll_interval_ms,
                },
            }
            self._json_response(200, data)
        elif self.path == "/circuits":
            svc = self.server.runner_service  # type: ignore[attr-defined]
            self._json_response(200, svc._circuit_breakers.get_all_states())
        else:
            self._json_response(404, {"error": "not found"})

    def _json_response(self, status: int, data: dict) -> None:
        body = _json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default stderr logging."""


class RunnerService(BaseRunner):
    """Distributed runner service for processing event steps and tasks.

    Polls the persistence store for pending tasks, claims them atomically
    via ``find_one_and_update``, dispatches events to ToolRegistry handlers,
    and resumes workflows via the Evaluator.
    """

    # Narrow the inherited BaseRunner._config to this runner's config subtype
    # so its HTTP-server / shutdown fields are visible to the type checker.
    _config: RunnerConfig

    def __init__(
        self,
        persistence: PersistenceAPI,
        evaluator: Evaluator,
        config: RunnerConfig,
        tool_registry: ToolRegistry,
    ) -> None:
        self._persistence = persistence
        self._evaluator = evaluator
        self._config = config
        self._tool_registry = tool_registry

        self._server_id = generate_id()
        self._running = False
        self._stopping = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        # Each entry: (future, task_id, claimed_at_ms)
        self._active_futures: list[tuple[Future, str, int]] = []
        self._active_lock = threading.Lock()
        self._handled_counts: dict[str, HandledCount] = {}
        self._ast_cache: dict[str, dict] = {}
        self._program_ast_cache: dict[str, dict] = {}
        self._start_time_ms: int = 0
        self._http_server: HTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._last_sweep: int = 0
        self._sweep_interval_ms: int = 300_000  # 5 min — safety net only
        # Per-workflow resume locks + pending-requeue (BaseRunner._resume_with_lock).
        self._resume_locks: dict[str, threading.Lock] = {}
        self._resume_locks_lock = threading.Lock()
        self._resume_pending: set[str] = set()
        self._resume_pending_lock = threading.Lock()
        self._last_reap: int = 0
        self._reap_interval_ms: int = 60000  # check for orphans every 60s
        self._execution_timeout_ms: int = int(
            os.environ.get("FW_TASK_EXECUTION_TIMEOUT_MS", "900000")
        )  # default 15 minutes

        # Circuit breaker for cascading failure protection
        from facetwork.runtime.circuit_breaker import CircuitBreakerRegistry

        self._circuit_breakers = CircuitBreakerRegistry()

        # Inline dispatcher used by BaseRunner._process_continuation while
        # draining the shared _fw_continue backlog (adapts the ToolRegistry to
        # the HandlerDispatcher protocol).
        self._continuation_dispatcher = _ToolRegistryDispatcher(self._tool_registry)

        # Register built-in task handler
        self._tool_registry.register("fw:execute", self._handle_execute_workflow)

    # server_id / is_running: inherited from BaseRunner.

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def start(self) -> None:
        """Start the runner service (blocking).

        Registers the server, starts the heartbeat thread, and enters
        the main poll loop. Blocks until stop() is called.
        """
        self._running = True
        self._stopping.clear()
        self._start_time_ms = _current_time_ms()
        self._executor = ThreadPoolExecutor(max_workers=self._config.max_concurrent)

        try:
            from facetwork import __full_version__

            self._version = __full_version__
            self._start_http_server()
            self._register_server()
            logger.info(
                "Runner started: server_id=%s, server_name=%s, group=%s, version=%s",
                self._server_id,
                self._config.server_name,
                self._config.server_group,
                self._version,
            )

            # Start heartbeat daemon
            heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            heartbeat_thread.start()

            # Main poll loop
            self._poll_loop()

        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the service to stop gracefully."""
        logger.info("Runner stopping: server_id=%s", self._server_id)
        self._stopping.set()

    def run_once(self) -> int:
        """Run a single poll cycle (for testing).

        Returns:
            Number of work items dispatched.
        """
        return self._poll_cycle()

    # =========================================================================
    # Server Registration
    # =========================================================================

    def _register_server(self) -> None:
        """Register this server instance in the persistence store."""
        now = _current_time_ms()
        handlers = list(self._tool_registry._handlers.keys())
        server = ServerDefinition(
            uuid=self._server_id,
            server_group=self._config.server_group,
            service_name=self._config.service_name,
            server_name=self._config.server_name,
            server_ips=self._get_server_ips(),
            start_time=now,
            ping_time=now,
            topics=list(self._config.topics),
            handlers=handlers,
            handled=[],
            state=ServerState.RUNNING,
            http_port=self.http_port or 0,
            version=getattr(self, "_version", ""),
            task_list=self._config.task_list,
        )
        self._persistence.save_server(server)

    # _deregister_server / _get_server_ips / _heartbeat_loop: inherited from BaseRunner.

    # =========================================================================
    # Poll Loop
    # =========================================================================

    def _poll_loop(self) -> None:
        """Main loop: poll for work until stopped."""
        interval_s = self._config.poll_interval_ms / 1000.0
        reconcile_counter = 0
        was_quarantined = False
        while not self._stopping.is_set():
            try:
                if self._is_quarantined():
                    if not was_quarantined:
                        logger.warning(
                            "Server %s is quarantined — skipping task claims until released",
                            self._server_id,
                        )
                        was_quarantined = True
                else:
                    if was_quarantined:
                        logger.info(
                            "Server %s released from quarantine — resuming task claims",
                            self._server_id,
                        )
                        was_quarantined = False
                    self._poll_cycle()
                    self._maybe_sweep_stuck_steps()
                    self._maybe_reap_orphaned_tasks()
                    reconcile_counter += 1
                    if reconcile_counter >= 10:
                        self._reconcile_with_db()
                        reconcile_counter = 0
            except Exception:
                logger.exception("Poll cycle error")
            self._stopping.wait(self._poll_wait_seconds(interval_s))

    def _is_quarantined(self) -> bool:
        """Return True if the server has been marked quarantined in the DB.

        Re-read each poll so a human-triggered toggle takes effect on the
        next cycle without restarting the runner.
        """
        try:
            server = self._persistence.get_server(self._server_id)
        except Exception:
            logger.exception("Failed to read server state for quarantine check")
            return False
        return server is not None and server.state == ServerState.QUARANTINE

    def _poll_cycle(self) -> int:
        """Single poll cycle: find and dispatch work.

        Returns:
            Number of work items dispatched.
        """
        dispatched = 0

        # Clean up completed futures
        self._cleanup_futures()

        capacity = self._config.max_concurrent - self._active_count()

        if capacity <= 0:
            return 0

        # Poll the lists for the namespaces this runner actually serves (derived
        # from its loaded handlers), plus its configured list for shared/default
        # work — namespace routing. A task is claimed only if it's on
        # one of these AND the runner has its handler, so the queue label always
        # follows the handler.
        from ..task_list_routing import namespaces_for

        poll_lists = sorted(set(namespaces_for(self._get_event_names())) | {self._config.task_list})

        # Claim event tasks from the task queue (filtered by circuit breaker)
        event_names = [n for n in self._get_event_names() if self._circuit_breakers.is_allowed(n)]
        if event_names:
            while capacity > 0:
                task = self._persistence.claim_task(
                    task_names=event_names,
                    task_list=poll_lists,
                    server_id=self._server_id,
                )
                if task is None:
                    break
                self._submit_event_task(task)
                capacity -= 1
                dispatched += 1

        # Claim resume tasks inserted by external agents
        while capacity > 0:
            task = self._persistence.claim_task(
                task_names=[RESUME_TASK_NAME],
                task_list=poll_lists,
                server_id=self._server_id,
            )
            if task is None:
                break
            self._submit_resume_task(task)
            capacity -= 1
            dispatched += 1

        # Claim built-in tasks (like afl:execute) via atomic find_one_and_update
        builtin_names = self._get_builtin_task_names()
        if builtin_names:
            while capacity > 0:
                task = self._persistence.claim_task(
                    task_names=builtin_names,
                    task_list=poll_lists,
                    server_id=self._server_id,
                )
                if task is None:
                    break
                self._submit_task(task)
                capacity -= 1
                dispatched += 1

        # Drain the shared _fw_continue backlog. RegistryRunner does this too;
        # without it a RunnerService-only fleet never processes continuations, so
        # cross-server cascades stall until the 5-min sweep and pending
        # continuation rows accumulate unboundedly. Skipped for handler-only
        # runners (continuation_mode off) — the ffl-runner tier owns the backlog.
        if self._config.polls_shared_continuations():
            from ..continuation import CONTINUATION_TASK_LIST, CONTINUATION_TASK_NAME

            while capacity > 0:
                task = self._persistence.claim_task(
                    task_names=[CONTINUATION_TASK_NAME],
                    task_list=CONTINUATION_TASK_LIST,
                    server_id=self._server_id,
                )
                if task is None:
                    break
                self._submit_continuation_task(task)
                capacity -= 1
                dispatched += 1

        return dispatched

    # _active_count: inherited from BaseRunner.

    # _cleanup_futures / _release_timed_out_task / _task_label / _safe_save_task /
    # _transition_for_retry: all inherited from BaseRunner.

    def _build_handler_payload(self, task: Any) -> dict:
        """Construct the per-task payload passed to a handler.

        Starts from ``task.data`` (the params the workflow supplied) and
        layers on the runtime-injected callbacks and retry context that
        every handler may use:

        - ``_step_log``: write a user-visible log entry under
          ``StepLogSource.HANDLER``.
        - ``_task_heartbeat``: renew the lease and optionally report
          progress; lets long-running handlers avoid the stuck-task
          watchdog.
        - ``_set_stage_budget``: declare a per-stage timeout deadline
          that extends the watchdog independent of the global
          execution-timeout safety net.
        - ``_task_uuid``: lets handlers correlate their own logs to the
          task without parsing.
        - ``_retry_count`` / ``_is_retry``: lets handlers detect a
          reclaim and skip operations they already completed.

        Extracted from ``_process_event_task`` so the dispatch logic
        isn't drowning in closure definitions.
        """
        payload: dict[str, Any] = dict(task.data or {})

        def _step_log_callback(message, level=StepLogLevel.INFO, details=None):
            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                facet_name=task.name,
                level=level,
                message=message,
                source=StepLogSource.HANDLER,
                details=details,
            )

        def _task_heartbeat_callback(
            progress_pct: int | None = None,
            progress_message: str | None = None,
        ) -> None:
            self._persistence.update_task_heartbeat(
                task.uuid,
                _current_time_ms(),
                progress_pct=progress_pct,
                progress_message=progress_message,
                # Only renew while we still own the task — a reclaimed zombie
                # must not extend the new owner's lease.
                expected_server_id=self._server_id,
            )

        def _set_stage_budget_callback(
            timeout_ms: int,
            stage_name: str = "",
        ) -> None:
            if timeout_ms <= 0:
                return
            budget_expires = _current_time_ms() + int(timeout_ms)
            self._persistence.update_task_stage_budget(
                task.uuid,
                budget_expires,
                stage_name=stage_name,
            )

        def _fetch_step_callback(ref: object) -> dict:
            from ..types import StepReference, serialize_attribute_value

            if isinstance(ref, dict):
                step_ref = StepReference.from_json(ref)
            elif isinstance(ref, StepReference):
                step_ref = ref
            else:
                raise TypeError(
                    f"fetch_step expects StepReference or tagged dict, got {type(ref).__name__}"
                )
            step = self._persistence.get_step(step_ref.step_id)
            if step is None:
                raise LookupError(f"Referenced step '{step_ref.step_id}' not found")
            return {
                "step_id": str(step.id),
                "facet_name": step.facet_name,
                "workflow_id": str(step.workflow_id),
                "params": {
                    k: serialize_attribute_value(v.value) for k, v in step.attributes.params.items()
                },
                "returns": {
                    k: serialize_attribute_value(v.value)
                    for k, v in step.attributes.returns.items()
                },
            }

        retry_count = getattr(task, "retry_count", 0) or 0
        payload.update(
            {
                "_step_log": _step_log_callback,
                "_task_heartbeat": _task_heartbeat_callback,
                "_set_stage_budget": _set_stage_budget_callback,
                "_fetch_step": _fetch_step_callback,
                "_task_uuid": task.uuid,
                "_retry_count": retry_count,
                "_is_retry": retry_count > 0,
            }
        )
        return payload

    # _emit_step_log: inherited from BaseRunner (harmonized keyword-only contract).

    def _lookup_runner_context(self, workflow_id: str) -> tuple[str, str]:
        """Return ``(runner_id, qualified_workflow_name)`` for a workflow.

        Resumed tasks need both fields so the runner-id is carried forward
        and logs can show the workflow's qualified name. Empty strings are
        returned when the persistence backend doesn't expose
        ``get_runners_by_workflow`` (e.g. some test fakes) or no runner is
        registered for the workflow yet.
        """
        if not hasattr(self._persistence, "get_runners_by_workflow"):
            return "", ""
        try:
            runners = self._persistence.get_runners_by_workflow(workflow_id)
        except Exception:
            return "", ""
        if not runners:
            return "", ""
        return runners[0].uuid, runners[0].workflow.name

    def _reconcile_with_db(self) -> None:
        """Reconcile in-memory active futures with actual DB state.

        Detects tasks that the DB shows as no longer running for this
        server (e.g. reaped by another runner) and releases the capacity
        slot. Also detects tasks in DB that have no corresponding future
        and resets them.
        """
        # Snapshot in-memory futures FIRST, then query the DB. A task claimed +
        # submitted concurrently then appears in memory before we compare (rather
        # than looking like a DB-running orphan we "lost"), and the DB read
        # reflects state at-least-as-new as the memory snapshot.
        with self._active_lock:
            memory_task_ids = {task_id for _, task_id, _ in self._active_futures}

        try:
            db_tasks = {
                t.uuid
                for t in self._persistence.get_tasks_by_server_id(self._server_id, limit=500)
                if t.state == TaskState.RUNNING
            }
        except Exception:
            logger.debug("Reconciliation: could not query DB", exc_info=True)
            return

        # Tasks in memory but not in DB → someone else reaped them, release slot
        orphaned_memory = memory_task_ids - db_tasks
        if orphaned_memory:
            logger.info(
                "Reconciliation: %d in-memory task(s) no longer running in DB, releasing slots",
                len(orphaned_memory),
            )
            with self._active_lock:
                self._active_futures = [
                    entry for entry in self._active_futures if entry[1] not in orphaned_memory
                ]

        # Tasks in DB but not in memory → we may have lost track. RE-VERIFY each
        # before resetting: the two snapshots straddle concurrent
        # completion/claim, so a task that completed (or was just claimed and
        # added to a future) between them must NOT be reset — that would
        # re-run finished work. Reset only if it is STILL running for us and
        # still has no future.
        orphaned_db = db_tasks - memory_task_ids
        if orphaned_db:
            reset = 0
            for task_id in orphaned_db:
                task = self._persistence.get_task(task_id)
                if (
                    task is None
                    or task.state != TaskState.RUNNING
                    or getattr(task, "server_id", "") != self._server_id
                ):
                    continue  # completed / reclaimed since the snapshot
                with self._active_lock:
                    if any(tid == task_id for _, tid, _ in self._active_futures):
                        continue  # a future exists now — not actually orphaned
                self._release_timed_out_task(task_id)
                reset += 1
            if reset:
                logger.warning("Reconciliation: reset %d orphaned DB task(s) to pending", reset)

    # =========================================================================
    # Polling
    # =========================================================================

    def _poll_event_steps(self) -> list[StepDefinition]:
        """Find steps blocked at EVENT_TRANSMIT."""
        steps = list(self._persistence.get_steps_by_state(StepState.EVENT_TRANSMIT))

        # Filter by topics if configured (supports both qualified and short names)
        if self._config.topics:
            topics_set = set(self._config.topics)
            steps = [
                s
                for s in steps
                if s.facet_name in topics_set or s.facet_name.rsplit(".", 1)[-1] in topics_set
            ]

        # Filter by handler availability (check both qualified and short name)
        steps = [
            s
            for s in steps
            if (
                self._tool_registry.has_handler(s.facet_name)
                or self._tool_registry.has_handler(s.facet_name.rsplit(".", 1)[-1])
            )
        ]

        return steps

    def _get_event_names(self) -> list[str]:
        """Get the list of event facet names this runner can handle.

        If topics are configured, uses those (qualified names).
        Otherwise, uses all handler names from the tool registry.
        """
        if self._config.topics:
            return list(self._config.topics)
        # Return handler names that are not built-in task handlers
        return [name for name in self._tool_registry._handlers.keys() if not name.startswith("fw:")]

    def _get_builtin_task_names(self) -> list[str]:
        """Get task name prefixes for built-in handlers (e.g. fw:execute).

        These are claimed via ``claim_task()`` separately from event tasks
        so that topic filtering does not interfere.  Only returns names
        that start with ``fw:`` (protocol tasks), not event handler names.

        Task names may include a workflow suffix (e.g. ``fw:execute:MyWorkflow``),
        so claim_task uses regex prefix matching.
        """
        return [
            name
            for name in self._tool_registry._handlers.keys()
            if name.startswith("fw:") and name != RESUME_TASK_NAME
        ]

    # =========================================================================
    # Work Submission
    # =========================================================================

    def _submit(self, process_fn: Any, item: Any, item_id: str) -> None:
        """Submit ``process_fn(item)`` to the executor and track its future.

        Runs inline when no executor is configured (test mode). Otherwise
        registers ``(future, item_id, now)`` so the cleanup/reconcile loops
        can find it. ``item_id`` is the step id or task uuid — whichever
        identifies the work for accounting and reconciliation.
        """
        if self._executor is None:
            process_fn(item)
            return
        future = self._executor.submit(process_fn, item)
        with self._active_lock:
            self._active_futures.append((future, item_id, _current_time_ms()))

    def _submit_step(self, step: StepDefinition) -> None:
        """Submit a step for processing in the thread pool."""
        self._submit(self._process_step, step, getattr(step, "id", ""))

    def _submit_event_task(self, task: Any) -> None:
        """Submit an event task for processing in the thread pool."""
        self._submit(self._process_event_task, task, task.uuid)

    def _submit_task(self, task: Any) -> None:
        """Submit a task for processing in the thread pool."""
        self._submit(self._process_task, task, task.uuid)

    def _submit_continuation_task(self, task: Any) -> None:
        """Submit a continuation task (_fw_continue) for processing."""
        self._submit(self._process_continuation, task, task.uuid)

    def _submit_resume_task(self, task: Any) -> None:
        """Submit a resume task for processing in the thread pool."""
        self._submit(self._process_resume_task, task, task.uuid)

    # =========================================================================
    # Step Processing
    # =========================================================================

    def _process_step(self, step: StepDefinition) -> None:
        """Process a single event step.

        1. Build payload from step params
        2. Dispatch to ToolRegistry handler
        3. Call evaluator.continue_step() with result
        4. Resume the workflow
        5. Update handled stats
        """
        try:
            # Build payload
            payload = {name: attr.value for name, attr in step.attributes.params.items()}

            # Dispatch to handler (try qualified name first, then short name)
            result = self._tool_registry.handle(step.facet_name, payload)
            if result is None and "." in step.facet_name:
                short_name = step.facet_name.rsplit(".", 1)[-1]
                result = self._tool_registry.handle(short_name, payload)

            if result is None:
                # No handler available — leave for another server
                logger.warning(
                    "No handler for facet '%s' on step %s",
                    step.facet_name,
                    step.id,
                )
                return

            # Continue the step with the result
            self._evaluator.continue_step(step.id, result)

            # Resume the workflow
            self._resume_workflow(step.workflow_id)

            # Update stats
            self._update_handled_stats(step.facet_name, handled=True)

            logger.info(
                "Processed step %s (facet=%s)",
                step.id,
                step.facet_name,
            )

        except Exception:
            self._update_handled_stats(step.facet_name, handled=False)
            logger.exception(
                "Error processing step %s (facet=%s)",
                step.id,
                step.facet_name,
            )

    # =========================================================================
    # Event Task Processing
    # =========================================================================

    def _process_event_task(self, task: Any) -> None:
        """Process an event task claimed from the task queue.

        1. Dispatch to ToolRegistry handler using task.name
        2. Call evaluator.continue_step() with result
        3. Resume the workflow
        4. Mark task as COMPLETED (or FAILED on error)
        5. Update handled stats
        """
        try:
            # Log task claimed with server identity
            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                facet_name=task.name,
                level=StepLogLevel.INFO,
                message=(
                    f"Task claimed: {task.name} "
                    f"(server={self._config.server_name}, id={self._server_id[:8]})"
                ),
            )

            payload = self._build_handler_payload(task)

            # Dispatch to handler (try exact name, then prefix for builtin
            # tasks like "fw:execute:WorkflowName", then short name)
            result = self._tool_registry.handle(task.name, payload)
            if result is None and task.name.startswith("fw:"):
                # Try base name without workflow suffix (e.g. "fw:execute")
                base_name = ":".join(task.name.split(":")[:2])
                result = self._tool_registry.handle(base_name, payload)
            if result is None and "." in task.name:
                short_name = task.name.rsplit(".", 1)[-1]
                result = self._tool_registry.handle(short_name, payload)

            if result is None:
                # This runner has no handler for the task. In a multi-runner
                # deployment the right runner just may not have claimed it yet,
                # so release it back to pending (with backoff) for retry; only
                # fail it for good once retries are exhausted — i.e. no runner
                # in the fleet can handle it.
                self._update_handled_stats(task.name, handled=False)
                is_terminal = self._transition_for_retry(
                    task,
                    dead_letter_state=TaskState.FAILED,
                    set_next_retry_after=True,
                )
                if is_terminal:
                    error_msg = (
                        f"No handler for event task '{task.name}' "
                        f"(no runner could service it after {task.retry_count} attempts)"
                    )
                    task.error = {"message": error_msg}
                    try:
                        self._evaluator.fail_step(task.step_id, error_msg)
                    except Exception:
                        logger.debug("Could not fail step %s", task.step_id, exc_info=True)
                    logger.warning(
                        "No handler for event task '%s' anywhere — failing after %d attempts "
                        "(step=%s) — %s",
                        task.name,
                        task.retry_count,
                        task.step_id,
                        self._task_label(task.uuid),
                    )
                else:
                    logger.info(
                        "No handler for event task '%s' on this runner — releasing back to "
                        "pending (attempt %d/%d) — %s",
                        task.name,
                        task.retry_count,
                        task.max_retries,
                        self._task_label(task.uuid),
                    )
                self._safe_save_task(task)
                return

            # Mark the task COMPLETED *before* continue/resume. This terminal
            # write is the OWNERSHIP FENCE: it goes through save_task_if_owned,
            # which succeeds only if this runner still owns the task. If the
            # lease was reclaimed under a slow handler, the write is dropped and
            # we must NOT continue_step/resume — the new owner will (re)produce
            # the result. Advancing workflow state here would race the reclaimer.
            # (It also lands before resume so the terminal-state check sees THIS
            # task terminal and can complete the runner on this cycle.)
            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            if not self._safe_save_task(task):
                logger.warning(
                    "Not advancing workflow for task %s (step=%s): lease was reclaimed "
                    "or the completion write failed — dropping this runner's result — %s",
                    task.uuid,
                    task.step_id,
                    self._task_label(task.uuid),
                )
                return

            # Continue the step and resume the workflow.
            # Uses resume_step (O(depth)) instead of resume (O(all steps)).
            # Built-in bootstrap tasks (``fw:execute[:Workflow]``) have no
            # step — the handler itself starts the workflow — so there's
            # nothing to continue/resume here.
            resume_error = None
            if task.step_id:
                try:
                    self._evaluator.continue_step(task.step_id, result)
                    self._resume_workflow_for_step(task.workflow_id, task.step_id)
                except Exception as resume_exc:
                    resume_error = resume_exc
                    logger.warning(
                        "Post-handler resume failed for %s (step=%s): %s — "
                        "task already marked completed (handler succeeded) — %s",
                        task.uuid,
                        task.step_id,
                        resume_exc,
                        self._task_label(task.uuid),
                    )

            # Update stats and circuit breaker
            self._update_handled_stats(task.name, handled=True)
            self._circuit_breakers.record_success(task.name)

            if resume_error:
                logger.info(
                    "Task %s completed (handler OK, resume needs retry): %s",
                    task.uuid,
                    task.name,
                )
            else:
                logger.info(
                    "Processed event task %s (name=%s, step=%s)",
                    task.uuid,
                    task.name,
                    task.step_id,
                )

        except (ImportError, ModuleNotFoundError) as exc:
            # Handler module can't be loaded on this runner.  Increment
            # retry_count so the task eventually dead-letters instead of
            # looping forever when no runner has the right handler.
            if self._transition_for_retry(task):
                task.error = {
                    "message": f"Handler not loadable after {task.retry_count} attempts: {exc}"
                }
                log_msg = f"Handler not loadable after {task.retry_count} attempts: {exc}"
                log_level = StepLogLevel.ERROR
                logger.warning(
                    "Dead-lettering task %s (%s): handler not loadable after %d attempts — %s",
                    task.uuid,
                    task.name,
                    task.retry_count,
                    self._task_label(task.uuid),
                )
            else:
                log_msg = (
                    f"Cannot load handler (attempt {task.retry_count}/{task.max_retries}): {exc}"
                )
                log_level = StepLogLevel.WARNING
                logger.warning(
                    "Cannot load handler for '%s', releasing task %s back to pending "
                    "(attempt %d/%d): %s — %s",
                    task.name,
                    task.uuid,
                    task.retry_count,
                    task.max_retries,
                    exc,
                    self._task_label(task.uuid),
                )
            self._safe_save_task(task)
            # Write step log so the error is visible in the dashboard
            if task.step_id:
                self._emit_step_log(
                    step_id=task.step_id,
                    workflow_id=task.workflow_id,
                    facet_name=task.name,
                    level=log_level,
                    message=log_msg,
                )

        except Exception as exc:
            # Preserve the original error message across retry attempts —
            # the helper is told not to clear it on the retry branch.
            task.error = {"message": str(exc)}
            if self._transition_for_retry(
                task,
                set_next_retry_after=True,
                clear_error_on_retry=False,
            ):
                try:
                    self._evaluator.fail_step(task.step_id, str(exc))
                except Exception:
                    logger.debug("Could not fail step %s", task.step_id, exc_info=True)
                logger.warning(
                    "Task %s dead-lettered after %d retries — %s: %s",
                    task.uuid,
                    task.retry_count,
                    self._task_label(task.uuid),
                    exc,
                )
            else:
                delay_s = (task.next_retry_after - task.updated) / 1000
                logger.warning(
                    "Task %s failed (retry %d/%d, next in %.0fs) — %s: %s",
                    task.uuid,
                    task.retry_count,
                    task.max_retries,
                    delay_s,
                    self._task_label(task.uuid),
                    exc,
                )

            self._safe_save_task(task)
            self._update_handled_stats(task.name, handled=False)
            self._circuit_breakers.record_failure(task.name)

    # =========================================================================
    # Resume Task Processing
    # =========================================================================

    def _process_resume_task(self, task: Any) -> None:
        """Process a resume task inserted by an external agent.

        External agents (Java/Scala/Go) handle event facets directly,
        write return attributes to the step, and insert an afl:resume
        task. This method picks up that task, calls continue_step to
        validate and transition the step, then resumes the workflow.
        """
        # Defensive: only genuine resume tasks (``fw:resume[:Facet]``) belong
        # here. If anything else got routed in (e.g. a misclassified
        # ``fw:execute:Workflow`` bootstrap task), hand it to the generic
        # task handler instead of failing with a misleading "missing step_id".
        if task.name != RESUME_TASK_NAME and not task.name.startswith(f"{RESUME_TASK_NAME}:"):
            logger.warning(
                "Non-resume task %s (name=%s) routed to resume handler — dispatching as a regular task",
                task.uuid,
                task.name,
            )
            self._process_task(task)
            return

        try:
            data = task.data or {}
            step_id = data.get("step_id") or task.step_id
            workflow_id = data.get("workflow_id") or task.workflow_id

            if not step_id:
                raise ValueError("Resume task missing step_id")

            # continue_step validates the step is at EVENT_TRANSMIT
            # and advances it to STATEMENT_BLOCKS_BEGIN. Pass empty result
            # because the external agent already wrote return attributes.
            self._evaluator.continue_step(step_id, {})

            # Resume scoped to the continued step (O(depth))
            self._resume_workflow_for_step(workflow_id, step_id)

            # Mark task completed
            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            self._safe_save_task(task)

            self._update_handled_stats(RESUME_TASK_NAME, handled=True)

            logger.info(
                "Processed resume task %s (step=%s, workflow=%s)",
                task.uuid,
                step_id,
                workflow_id,
            )

        except Exception as exc:
            task.state = TaskState.FAILED
            task.error = {"message": str(exc)}
            task.updated = _current_time_ms()
            self._safe_save_task(task)
            self._update_handled_stats(RESUME_TASK_NAME, handled=False)
            logger.exception(
                "Error processing resume task %s (step=%s)",
                task.uuid,
                task.data.get("step_id") if task.data else "unknown",
            )

    # =========================================================================
    # Task Processing
    # =========================================================================

    def _process_task(self, task: Any) -> None:
        """Process a single task (already claimed atomically via claim_task).

        1. Dispatch to handler
        2. Mark task as completed/failed
        """
        try:
            # Dispatch
            payload = task.data or {}
            result = self._tool_registry.handle(task.name, payload)
            if result is None and task.name.startswith("fw:"):
                # Built-in protocol tasks carry a human-readable suffix
                # (e.g. "fw:execute:MyWorkflow"); fall back to the base
                # name ("fw:execute") so the registered handler is found.
                base_name = ":".join(task.name.split(":")[:2])
                if base_name != task.name:
                    result = self._tool_registry.handle(base_name, payload)

            if result is not None:
                task.state = TaskState.COMPLETED
            elif task.max_retries > 0 and (task.retry_count + 1) >= task.max_retries:
                # No handler here and retries exhausted — fail for good.
                task.retry_count += 1
                task.state = TaskState.FAILED
                task.error = {
                    "message": (
                        f"No handler for task '{task.name}' "
                        f"(no runner could service it after {task.retry_count} attempts)"
                    )
                }
            else:
                # No handler on this runner — release back to pending (with
                # backoff) so a runner that has the handler can pick it up.
                from facetwork.runtime.mongo_store import _compute_next_retry_after

                task.retry_count += 1
                task.state = TaskState.PENDING
                task.server_id = ""
                task.error = None
                task.next_retry_after = _compute_next_retry_after(
                    task.retry_count, _current_time_ms()
                )

            task.updated = _current_time_ms()
            self._safe_save_task(task)

            logger.info("Processed task %s (name=%s, state=%s)", task.uuid, task.name, task.state)

        except Exception as exc:
            task.state = TaskState.FAILED
            task.error = {"message": str(exc)}
            task.updated = _current_time_ms()
            self._safe_save_task(task)
            logger.exception("Error processing task %s", task.uuid)

    # =========================================================================
    # Built-in Task Handlers
    # =========================================================================

    def _handle_execute_workflow(self, payload: dict) -> dict:
        """Handle an afl:execute task.

        Loads the flow from persistence, parses FFL source, finds the
        workflow AST, and executes it via the evaluator.

        Args:
            payload: Task data containing flow_id, workflow_name, inputs, runner_id

        Returns:
            Dict with status and workflow_id
        """
        flow_id = payload.get("flow_id", "")
        submitted_wf_id = payload.get("workflow_id", "")
        workflow_name = payload.get("workflow_name", "")
        inputs = payload.get("inputs") or {}
        runner_id = payload.get("runner_id", "")

        # Update runner state to RUNNING
        runner = None
        if runner_id and hasattr(self._persistence, "get_runner"):
            runner = self._persistence.get_runner(runner_id)
            if runner:
                runner.state = RunnerState.RUNNING
                runner.start_time = _current_time_ms()
                self._persistence.save_runner(runner)

        try:
            # Load flow from persistence
            if not hasattr(self._persistence, "get_flow"):
                raise RuntimeError("Persistence store does not support get_flow")

            # Transient-failure tolerance: a re-seed (or any other administrative
            # rewrite of the flow row) may briefly leave `get_flow` returning
            # None on a row that exists logically. Stable-UUID re-seeding (see
            # facetwork/examples/__init__.py:seed_example_flows) prevents the
            # systemic version of this race; the short retry here covers any
            # remaining transient window. Bounded by ~3s total — anything
            # longer is a real "flow gone" and should fail.
            flow = None
            for attempt in range(6):
                flow = self._persistence.get_flow(flow_id)
                if flow:
                    if attempt > 0:
                        logger.info("Flow %s resolved after %d retry/retries", flow_id, attempt)
                    break
                import time as _t

                _t.sleep(0.5)
            if not flow:
                raise RuntimeError(f"Flow '{flow_id}' not found")

            # Use stored compiled AST; fall back to recompilation for legacy flows
            program_dict = flow.compiled_ast
            if not program_dict:
                if not flow.compiled_sources:
                    raise RuntimeError(f"Flow '{flow_id}' has no compiled AST or sources")
                import json

                from ...emitter import JSONEmitter
                from ...parser import FFLParser

                parser = FFLParser()
                ast = parser.parse(flow.compiled_sources[0].content)
                emitter = JSONEmitter(include_locations=False)
                program_dict = json.loads(emitter.emit(ast))
                logger.warning("Flow '%s' has no compiled_ast, fell back to recompilation", flow_id)

            if program_dict is None:
                raise RuntimeError(f"Flow '{flow_id}' has no compiled AST")

            # Find workflow AST by name (supports qualified names like "ns.WorkflowName")
            workflow_ast = self._find_workflow_in_program(program_dict, workflow_name)

            if workflow_ast is None:
                raise RuntimeError(f"Workflow '{workflow_name}' not found in flow '{flow_id}'")

            # Execute — use the submitted workflow UUID so that external
            # agents can look up the AST via get_workflow(workflow_id).
            result = self._evaluator.execute(
                workflow_ast,
                inputs=inputs,
                program_ast=program_dict,
                runner_id=runner_id,
                wf_id=submitted_wf_id,
                qualified_workflow_name=workflow_name,
            )

            # Cache AST for resume
            self._ast_cache[result.workflow_id] = workflow_ast
            self._program_ast_cache[result.workflow_id] = program_dict

            # Snapshot ASTs into runner for self-contained resume
            if runner:
                runner.compiled_ast = program_dict
                runner.workflow_ast = workflow_ast

            # Update runner with evaluator's workflow_id so dashboard can find steps
            if runner:
                runner.workflow_id = result.workflow_id

            # Update runner state based on result
            if runner:
                if result.status == ExecutionStatus.COMPLETED:
                    runner.state = RunnerState.COMPLETED
                    runner.end_time = _current_time_ms()
                    runner.duration = runner.end_time - runner.start_time
                elif result.status == ExecutionStatus.PAUSED:
                    runner.state = RunnerState.RUNNING
                elif result.status == ExecutionStatus.ERROR:
                    runner.state = RunnerState.FAILED
                    runner.end_time = _current_time_ms()
                    runner.duration = runner.end_time - runner.start_time
                self._persistence.save_runner(runner)

            return {
                "status": result.status,
                "workflow_id": result.workflow_id,
            }

        except Exception:
            if runner:
                runner.state = RunnerState.FAILED
                runner.end_time = _current_time_ms()
                runner.duration = runner.end_time - runner.start_time
                self._persistence.save_runner(runner)
            raise

    # _find_workflow_in_program: inherited from BaseRunner.

    # =========================================================================
    # Stuck-Step Recovery Sweep
    # =========================================================================

    def _maybe_sweep_stuck_steps(self) -> None:
        """Periodically resume steps stuck at intermediate states.

        Uses resume_step() per stuck step (O(depth) each) instead of
        full resume() (which can hang on large workflows).  For steps
        at EventTransmit that need tasks created, creates the tasks
        directly.
        """
        # Orchestration role: only inline/shared runners run the sweep. A
        # handler-only runner (--continuation off) leaves it to the ffl-runner
        # tier. See docs/architecture/ffl-runner-orchestration-tier.md.
        if not self._config.runs_stuck_step_sweep():
            return
        now = _current_time_ms()
        if now - self._last_sweep < self._sweep_interval_ms:
            return
        self._last_sweep = now

        # Event handlers take priority. If every worker slot is occupied, skip
        # the sweep so it cannot run ahead of active work on the poll thread.
        # (runtime.md §10.4 — matches RegistryRunner's bounded sweep.)
        if self._active_count() >= self._config.max_concurrent:
            return

        # Bound the work per invocation. The sweep creates tasks + resumes blocks
        # SYNCHRONOUSLY on the poll thread; an unbounded sweep over a large
        # foreach fan-out out-runs the poll interval and starves event-task
        # claiming — the livelock §10.4 warns about (runners busy sweeping, 0
        # events claimed). Cap the step count and wall-clock; the remainder is
        # picked up by the next sweep once normal claiming has advanced.
        budget = _SweepBudget(_SWEEP_MAX_STEPS, now + _SWEEP_MAX_MS)

        try:
            workflow_ids = self._persistence.get_pending_resume_workflow_ids()
            if not workflow_ids:
                return

            # Resolve workflow names for readable logging
            wf_names: dict[str, str] = {}
            for wf_id in workflow_ids:
                _, name = self._lookup_runner_context(wf_id)
                if name:
                    wf_names[wf_id] = name

            names = ", ".join(wf_names.get(wid, wid[:12]) for wid in workflow_ids)
            logger.info(
                "Stuck-step sweep: %d workflow(s) need resume: %s",
                len(workflow_ids),
                names,
            )

            for wf_id in workflow_ids:
                if budget.exhausted():
                    logger.debug("Stuck-step sweep hit per-invocation cap; deferring rest")
                    break
                try:
                    self._sweep_workflow_steps(wf_id, budget)
                except Exception:
                    logger.debug("Sweep failed for workflow %s", wf_id, exc_info=True)
        except Exception:
            logger.debug("Stuck-step sweep failed", exc_info=True)

    def _sweep_workflow_steps(self, workflow_id: str, budget: _SweepBudget | None = None) -> None:
        """Resume individual stuck steps in a workflow using resume_step().

        Processes leaf steps (EventTransmit) first, then block steps,
        so parent blocks see completed children. Routes through the
        ``PersistenceAPI`` so any backend that implements
        ``get_stuck_steps_for_workflow`` is supported, not just MongoStore.
        """
        stuck_steps = list(self._persistence.get_stuck_steps_for_workflow(workflow_id))
        if not stuck_steps:
            return

        # Process leaf steps (EventTransmit) first, then blocks
        leaf_steps = [s for s in stuck_steps if s.state == StepState.EVENT_TRANSMIT]
        block_steps = [s for s in stuck_steps if s.state != StepState.EVENT_TRANSMIT]

        step_details = [
            f"{(s.statement_name or s.facet_name or s.object_type or '?')} ({s.state})"
            for s in stuck_steps
        ]
        logger.info(
            "Sweep workflow %s: %d leaf + %d block steps stuck: %s",
            workflow_id[:12],
            len(leaf_steps),
            len(block_steps),
            ", ".join(step_details),
        )

        # For EventTransmit steps without tasks, create tasks so handlers run.
        # resume_step() can't do this — it only walks the ancestor chain.
        from ..task_list_routing import namespace_of

        for step in leaf_steps:
            if budget is not None and budget.exhausted():
                return
            facet_name = step.facet_name
            if not facet_name:
                continue  # block-level step, not an event facet
            if self._persistence.has_active_task_for_step(step.id):
                continue

            # A dead-lettered task means this step's work was permanently
            # abandoned (retries exhausted). Do NOT recreate a fresh
            # retry_count=0 task — _dead_letter_overdue leaves the step at
            # EventTransmit, so resurrecting it here loops forever. Fail the step
            # instead (with AST so catch/error-propagation runs) and cascade, so
            # the workflow finishes in error rather than retrying endlessly.
            if self._persistence.has_dead_letter_task_for_step(step.id):
                if budget is not None:
                    budget.consume()
                try:
                    self._evaluator.fail_step(
                        step.id,
                        "task dead-lettered (retries exhausted)",
                        workflow_ast=self._ast_cache.get(workflow_id),
                        program_ast=self._program_ast_cache.get(workflow_id),
                    )
                    self._resume_workflow_for_step(workflow_id, step.id)
                    logger.warning(
                        "Sweep failed dead-lettered step %s (%s) instead of resurrecting it",
                        step.id[:12],
                        facet_name,
                    )
                except Exception:
                    logger.debug(
                        "Sweep fail_step failed: workflow=%s step=%s",
                        workflow_id[:12],
                        step.id[:12],
                        exc_info=True,
                    )
                continue

            if budget is not None:
                budget.consume()

            runner_id, _ = self._lookup_runner_context(workflow_id)
            task = TaskDefinition(
                uuid=generate_id(),
                name=facet_name,
                runner_id=runner_id,
                workflow_id=workflow_id,
                flow_id="",
                step_id=step.id,
                state=TaskState.PENDING,
                # Route by the facet's own namespace so the recreated
                # task lands on the list its handler-runners poll.
                task_list_name=namespace_of(facet_name),
                data=_step_params_as_payload(step),
            )
            self._persistence.save_task(task)
            logger.info(
                "Sweep created task for stuck step: %s (%s)",
                step.id[:12],
                facet_name,
            )

        # Resume block steps to cascade completion
        for step in block_steps:
            if budget is not None and budget.exhausted():
                return
            if budget is not None:
                budget.consume()
            try:
                self._resume_workflow_for_step(workflow_id, step.id)
            except Exception:
                logger.debug(
                    "Sweep resume_step failed: workflow=%s step=%s",
                    workflow_id[:12],
                    step.id[:12],
                    exc_info=True,
                )

    # =========================================================================
    # Orphaned Task Reaper
    # =========================================================================

    def _maybe_reap_orphaned_tasks(self) -> None:
        """Periodically reset tasks orphaned by crashed servers.

        If a server's heartbeat is stale (>5 min) but its state is still
        ``running``/``startup``, any tasks it claimed are stuck forever.
        This reaper resets them to ``pending`` so healthy runners can
        pick them up.
        """
        now = _current_time_ms()
        if now - self._last_reap < self._reap_interval_ms:
            return
        self._last_reap = now

        try:
            timeout_ms = int(os.environ.get("FW_REAPER_TIMEOUT_MS", "120000"))
            reaped = self._persistence.reap_orphaned_tasks(down_timeout_ms=timeout_ms)
            if reaped:
                logger.warning(
                    "Orphan reaper: reset %d task(s) from crashed server(s)",
                    len(reaped),
                )
                for task_info in reaped:
                    self._emit_step_log(
                        step_id=task_info["step_id"],
                        workflow_id=task_info["workflow_id"],
                        facet_name=task_info["name"],
                        level=StepLogLevel.WARNING,
                        message=_reaper_message(task_info, reclaimer_name=self._config.server_name),
                    )
        except Exception:
            logger.debug("Orphan reaper failed", exc_info=True)

        # Also prune stale ServerDefinition records. Stopped containers leave
        # heartbeat-stale rows that crowd debug queries and the dashboard's
        # grouped-by-task_list view. Two windows: live (running/startup) rows
        # use a generous 10×reaper window so a transiently-slow runner is never
        # deleted out from under itself; explicitly-dead ``shutdown`` rows (from
        # graceful deregister or the reaper above) carry no live work, so they
        # are pruned on the short reaper-timeout window instead of inflating
        # ``list-runners``/dashboard counts for the full 20 min.
        try:
            if hasattr(self._persistence, "prune_stale_servers"):
                prune_window_ms = max(timeout_ms * 10, 600_000)
                pruned = self._persistence.prune_stale_servers(
                    older_than_ms=prune_window_ms,
                    terminal_older_than_ms=timeout_ms,
                )
                if pruned:
                    logger.info("Pruned %d stale server record(s)", pruned)
        except Exception:
            logger.debug("Stale-server pruner failed", exc_info=True)

        # --- Stuck task watchdog ---
        try:
            stuck_timeout_ms = int(os.environ.get("FW_STUCK_TIMEOUT_MS", "1800000"))
            stuck = self._persistence.reap_stuck_tasks(default_stuck_ms=stuck_timeout_ms)
            if stuck:
                logger.warning(
                    "Stuck watchdog: reset %d task(s) exceeding timeout",
                    len(stuck),
                )
                for task_info in stuck:
                    self._emit_step_log(
                        step_id=task_info["step_id"],
                        workflow_id=task_info["workflow_id"],
                        facet_name=task_info["name"],
                        level=StepLogLevel.WARNING,
                        message=_stuck_message(task_info, reclaimer_name=self._config.server_name),
                    )
        except Exception:
            logger.debug("Stuck task watchdog failed", exc_info=True)

    # =========================================================================
    # Workflow Resume
    # =========================================================================

    def _resume_workflow(self, workflow_id: str) -> None:
        """Resume a paused workflow after step completion (full resume).

        Runs the full ``evaluator.resume`` body under BaseRunner's
        non-blocking per-workflow lock — the SAME lock the scoped
        ``_resume_workflow_for_step`` path uses — so the two resume
        strategies can no longer race or double-run a workflow. If the
        lock is held, the workflow is marked pending and the holder
        re-runs; the stuck-step sweep is the ultimate safety net.
        """
        self._resume_with_lock(workflow_id, lambda: self._do_resume_full(workflow_id))

    def _do_resume_full(self, workflow_id: str) -> None:
        """One full-resume cycle (the body run under _resume_with_lock).

        Uses a cached AST when available.  When the workflow reaches a
        terminal state (COMPLETED or ERROR), updates the associated
        runner entity so the dashboard reflects the final status.
        """
        workflow_ast = self._ast_cache.get(workflow_id)
        if workflow_ast is None:
            # Attempt to load from persistence if available
            workflow_ast = self._load_workflow_ast(workflow_id)
            if workflow_ast:
                self._ast_cache[workflow_id] = workflow_ast

        if workflow_ast is None:
            logger.warning(
                "No AST available for workflow %s, skipping resume "
                "(check that workflow and flow exist in persistence)",
                workflow_id,
            )
            return

        program_ast = self._program_ast_cache.get(workflow_id)

        # Look up the runner_id so resumed tasks inherit the workflow's runner
        runner_id, qualified_workflow_name = self._lookup_runner_context(workflow_id)

        # Run resume with a timeout to prevent blocking the handler thread
        # indefinitely. Large workflows (100+ steps) can have long iteration
        # loops that consume the thread, preventing capacity from being freed.
        import concurrent.futures

        resume_timeout_s = int(os.environ.get("FW_RESUME_TIMEOUT_S", "600"))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                self._evaluator.resume,
                workflow_id,
                workflow_ast,
                program_ast=program_ast,
                runner_id=runner_id,
                qualified_workflow_name=qualified_workflow_name,
            )
            result = future.result(timeout=resume_timeout_s)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "Workflow resume timed out after %ds for workflow %s — will retry on next sweep",
                resume_timeout_s,
                workflow_id,
            )
            return
        finally:
            executor.shutdown(wait=False)

        if result.status == ExecutionStatus.ERROR:
            logger.warning(
                "Workflow resume returned ERROR: workflow_id=%s error=%s",
                workflow_id,
                result.error,
            )

        # Update runner state on terminal status
        if result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.ERROR):
            self._update_runner_terminal_state(workflow_id, result.status)

    def _resume_workflow_for_step(self, workflow_id: str, step_id: str) -> None:
        """Resume a workflow scoped to a single completed step.

        Uses ``evaluator.resume_step()`` which walks the ancestor chain
        (step → block → parent block → root) — O(depth) instead of
        scanning all steps.  Falls back to the full-resume body
        (``_do_resume_full``) on error.

        Runs under BaseRunner._resume_with_lock: a NON-BLOCKING per-workflow
        lock so concurrent handler threads for the same workflow don't
        serialize (park the worker pool) waiting on the lock — if it's held,
        the workflow is marked pending and the holder re-runs, with the
        stuck-step sweep as the ultimate safety net.
        """
        self._resume_with_lock(workflow_id, lambda: self._do_resume_step(workflow_id, step_id))

    def _do_resume_step(self, workflow_id: str, step_id: str) -> None:
        """One resume-step cycle (the body run under _resume_with_lock)."""
        try:
            workflow_ast = self._ast_cache.get(workflow_id)
            if workflow_ast is None:
                workflow_ast = self._load_workflow_ast(workflow_id)
                if workflow_ast:
                    self._ast_cache[workflow_id] = workflow_ast

            if workflow_ast is None:
                logger.warning(
                    "No AST for workflow %s, falling back to full resume",
                    workflow_id,
                )
                return

            program_ast = self._program_ast_cache.get(workflow_id)

            runner_id, qualified_workflow_name = self._lookup_runner_context(workflow_id)

            result = self._evaluator.resume_step(
                workflow_id,
                step_id,
                workflow_ast,
                program_ast=program_ast,
                runner_id=runner_id,
                qualified_workflow_name=qualified_workflow_name,
            )

            if result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.ERROR):
                self._update_runner_terminal_state(workflow_id, result.status)

            logger.debug(
                "resume_step done: workflow=%s step=%s status=%s",
                workflow_id,
                step_id,
                result.status,
            )
        except Exception:
            logger.warning(
                "resume_step failed for workflow %s step %s, falling back to full resume",
                workflow_id,
                step_id,
                exc_info=True,
            )
            # We already hold this workflow's resume lock here — call the raw
            # full-resume body directly, NOT _resume_workflow (whose
            # non-blocking re-acquire would fail, mark the workflow pending,
            # and livelock the outer _resume_with_lock re-run loop).
            try:
                self._do_resume_full(workflow_id)
            except Exception:
                logger.debug("Fallback resume also failed", exc_info=True)

    # _update_runner_terminal_state / _has_non_terminal_tasks: inherited from BaseRunner
    # (now guarded — won't COMPLETE a runner while non-terminal tasks remain).

    # _load_workflow_ast: inherited from BaseRunner (this was the canonical impl).

    def cache_workflow_ast(self, workflow_id: str, ast: dict) -> None:
        """Pre-cache a workflow AST for use during processing.

        Args:
            workflow_id: The workflow ID
            ast: The compiled workflow AST dict
        """
        self._ast_cache[workflow_id] = ast

    # =========================================================================
    # Stats
    # =========================================================================

    def _update_handled_stats(self, handler_name: str, handled: bool) -> None:
        """Update handled/not-handled counts for a handler."""
        if handler_name not in self._handled_counts:
            self._handled_counts[handler_name] = HandledCount(handler=handler_name)

        counts = self._handled_counts[handler_name]
        if handled:
            counts.handled += 1
        else:
            counts.not_handled += 1

        # Persist ONLY the handled stats with a targeted field write. A full
        # get_server → mutate → save_server would read-modify-write the whole
        # server doc and could silently revert a concurrent state change made
        # between the read and write — e.g. a dashboard QUARANTINE (finding #10).
        try:
            self._persistence.update_server_handled(
                self._server_id, list(self._handled_counts.values())
            )
        except Exception:
            logger.debug("Failed to update handled stats", exc_info=True)

    # =========================================================================
    # HTTP Status Server
    # =========================================================================

    def _start_http_server(self) -> int:
        """Start the embedded HTTP status server.

        Tries ports starting from ``http_port``, incrementing on
        ``EADDRINUSE`` up to ``http_max_port_attempts`` times.

        Returns:
            The actual port the server bound to.

        Raises:
            RuntimeError: If no port could be bound.
        """
        base = self._config.http_port
        for attempt in range(self._config.http_max_port_attempts):
            port = base + attempt
            try:
                server = HTTPServer(("0.0.0.0", port), _StatusHandler)
            except OSError:
                continue
            server.runner_service = self  # type: ignore[attr-defined]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._http_server = server
            self._http_thread = thread
            logger.info("HTTP status server listening on port %d", port)
            return port

        raise RuntimeError(
            f"Could not bind HTTP status server on ports "
            f"{base}–{base + self._config.http_max_port_attempts - 1}"
        )

    def _stop_http_server(self) -> None:
        """Shut down the embedded HTTP status server."""
        if self._http_server:
            self._http_server.shutdown()
        if self._http_thread:
            self._http_thread.join(timeout=5)
        self._http_server = None
        self._http_thread = None

    @property
    def http_port(self) -> int | None:
        """Return the port the HTTP status server is listening on, or None."""
        if self._http_server:
            return self._http_server.server_address[1]
        return None

    # =========================================================================
    # Shutdown
    # =========================================================================

    def _shutdown(self) -> None:
        """Gracefully shut down the service."""
        self._running = False

        # Stop HTTP status server
        self._stop_http_server()

        # Wait for active work to complete
        if self._executor:
            timeout_s = self._config.shutdown_timeout_ms / 1000.0
            self._executor.shutdown(wait=True, cancel_futures=False)
            # Wait for remaining futures
            with self._active_lock:
                for future, _task_id, _claimed_at in self._active_futures:
                    try:
                        future.result(timeout=timeout_s)
                    except Exception:
                        pass
                self._active_futures.clear()
            self._executor = None

        # Deregister
        try:
            self._deregister_server()
        except Exception:
            logger.exception("Error deregistering server")

        logger.info("Runner stopped: server_id=%s", self._server_id)
