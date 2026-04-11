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

"""AFL Agent Poller library.

Standalone polling library for building FFL Agent services. The AgentPoller
handles one concern: poll the task queue for event tasks, dispatch to
registered callbacks, and manage the step/task lifecycle on success or failure.

Example usage::

    from facetwork.runtime import MemoryStore, Evaluator, Telemetry
    from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig

    store = MongoStore(config)
    evaluator = Evaluator(persistence=store)

    poller = AgentPoller(
        persistence=store,
        evaluator=evaluator,
        config=AgentPollerConfig(service_name="my-agent"),
    )

    def count_documents(payload: dict) -> dict:
        count = db.collection.count_documents(payload.get("filter", {}))
        return {"count": count}

    poller.register("ns.CountDocuments", count_documents)
    poller.start()  # blocks until stopped

For async handlers (e.g., LLM-based handlers)::

    async def llm_handler(payload: dict) -> dict:
        response = await openai.chat.completions.create(...)
        return {"response": response}

    poller.register_async("ns.LLMQuery", llm_handler)
"""

import asyncio
import inspect
import logging
import os
import socket
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .runner_config import BaseRunnerConfig

from .entities import (
    RunnerState,
    ServerDefinition,
    ServerState,
    StepLogEntry,
    StepLogLevel,
    StepLogSource,
    TaskState,
)
from .evaluator import Evaluator, ExecutionResult, ExecutionStatus
from .persistence import PersistenceAPI
from .types import AttributeValue, generate_id

logger = logging.getLogger(__name__)

# Type aliases for sync and async callbacks
SyncCallback = Callable[[dict], dict]
AsyncCallback = Callable[[dict], Awaitable[dict]]
AnyCallback = SyncCallback | AsyncCallback


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


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


@dataclass
class AgentPollerConfig(BaseRunnerConfig):
    """Configuration for the AgentPoller.

    Uses all defaults from BaseRunnerConfig.
    """

    service_name: str = "afl-agent"


class AgentPoller:
    """Polls the task queue for event tasks and dispatches to registered callbacks.

    Handles task claiming, callback dispatch, step continuation/failure,
    and workflow resumption. Multiple AgentPoller instances can run
    concurrently, coordinated through the persistence store's atomic
    claim_task mechanism.
    """

    def __init__(
        self,
        persistence: PersistenceAPI,
        evaluator: Evaluator,
        config: AgentPollerConfig | None = None,
    ) -> None:
        self._persistence = persistence
        self._evaluator = evaluator
        self._config = config or AgentPollerConfig()

        self._server_id = generate_id()
        self._handlers: dict[str, AnyCallback] = {}
        self._running = False
        self._stopping = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        # Each entry: (future, task_id, claimed_at_ms)
        self._active_futures: list[tuple[Future, str, int]] = []
        self._active_lock = threading.Lock()
        self._ast_cache: dict[str, dict] = {}
        self._program_ast_cache: dict[str, dict] = {}
        self._resume_locks: dict[str, threading.Lock] = {}
        self._resume_locks_lock = threading.Lock()
        self._resume_pending: set[str] = set()
        self._resume_pending_lock = threading.Lock()
        self._last_reap: int = 0
        self._reap_interval_ms: int = 60000
        self._execution_timeout_ms: int = int(
            os.environ.get("AFL_TASK_EXECUTION_TIMEOUT_MS", "900000")
        )

    @property
    def server_id(self) -> str:
        """Get the server's unique ID."""
        return self._server_id

    @property
    def is_running(self) -> bool:
        """Check if the poller is currently running."""
        return self._running

    # =========================================================================
    # Registration
    # =========================================================================

    def register(self, facet_name: str, callback: SyncCallback) -> None:
        """Register a synchronous callback for a qualified facet name.

        Args:
            facet_name: Qualified event facet name (e.g. "ns.CountDocuments")
            callback: Sync function (payload_dict) -> result_dict.
                      Raise an exception to signal failure.
        """
        self._handlers[facet_name] = callback

    def register_async(self, facet_name: str, callback: AsyncCallback) -> None:
        """Register an async callback for a qualified facet name.

        Async callbacks are useful for LLM-based handlers that need to await
        API calls. The callback will be invoked with asyncio.run().

        Args:
            facet_name: Qualified event facet name (e.g. "ns.LLMQuery")
            callback: Async function (payload_dict) -> result_dict.
                      Raise an exception to signal failure.
        """
        self._handlers[facet_name] = callback

    def registered_names(self) -> list[str]:
        """Return the list of registered facet names."""
        return list(self._handlers.keys())

    def update_step(self, step_id: str, partial_result: dict) -> None:
        """Update a step with partial results (for streaming handlers).

        This method can be called from within a handler to incrementally
        update the step's return attributes before final completion.
        Useful for streaming LLM responses.

        Args:
            step_id: The step ID to update
            partial_result: Dict of return attribute names to values to merge

        Raises:
            ValueError: If step is not found
        """
        from .step import FacetAttributes

        step = self._persistence.get_step(step_id)
        if not step:
            raise ValueError(f"Step not found: {step_id}")

        if step.attributes is None:
            step.attributes = FacetAttributes()
        if step.attributes.returns is None:
            step.attributes.returns = {}

        # Merge partial results into existing returns
        for name, value in partial_result.items():
            step.attributes.returns[name] = AttributeValue(
                name=name,
                value=value,
                type_hint=self._infer_type_hint(value),
            )

        self._persistence.save_step(step)

    def _infer_type_hint(self, value: object) -> str:
        """Infer type hint from a Python value."""
        if isinstance(value, bool):
            return "Boolean"
        elif isinstance(value, int):
            return "Long"
        elif isinstance(value, float):
            return "Double"
        elif isinstance(value, str):
            return "String"
        elif isinstance(value, list):
            return "List"
        elif isinstance(value, dict):
            return "Map"
        elif value is None:
            return "Any"
        else:
            return "Any"

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def start(self) -> None:
        """Start the poller (blocking).

        Registers the server, starts the heartbeat thread, and enters
        the main poll loop. Blocks until stop() is called.

        Signal handlers (SIGTERM/SIGINT) should be set up by the caller.
        """
        self._running = True
        self._stopping.clear()
        self._executor = ThreadPoolExecutor(max_workers=self._config.max_concurrent)

        try:
            self._register_server()
            logger.info(
                "AgentPoller started: server_id=%s, service=%s, handlers=%s",
                self._server_id,
                self._config.service_name,
                list(self._handlers.keys()),
            )

            # Start heartbeat daemon
            heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            heartbeat_thread.start()

            # Main poll loop
            self._poll_loop()

        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the poller to stop gracefully."""
        logger.info("AgentPoller stopping: server_id=%s", self._server_id)
        self._stopping.set()

    def poll_once(self) -> int:
        """Run a single poll cycle (synchronous, for testing).

        Does not use the thread pool executor. Claims and processes
        tasks sequentially.

        Returns:
            Number of tasks dispatched.
        """
        if not self._handlers:
            return 0

        capacity = self._config.max_concurrent - self._active_count()
        if capacity <= 0:
            return 0

        dispatched = 0
        task_names = list(self._handlers.keys())

        while capacity > 0:
            task = self._persistence.claim_task(
                task_names=task_names,
                task_list=self._config.task_list,
                server_id=self._server_id,
            )
            if task is None:
                break
            self._process_event(task)
            capacity -= 1
            dispatched += 1

        return dispatched

    # =========================================================================
    # AST Caching
    # =========================================================================

    def cache_workflow_ast(
        self, workflow_id: str, ast: dict, program_ast: dict | None = None
    ) -> None:
        """Pre-cache a workflow AST for use during processing.

        Args:
            workflow_id: The workflow ID
            ast: The compiled workflow AST dict
            program_ast: Optional full program AST for facet lookups
        """
        self._ast_cache[workflow_id] = ast
        if program_ast is not None:
            self._program_ast_cache[workflow_id] = program_ast

    def _load_workflow_ast(self, workflow_id: str) -> dict | None:
        """Load a workflow AST from persistence if available.

        Prefers runner-snapshotted ASTs (self-contained, immune to flow
        changes) and falls back to the flow lookup for backward compat.
        """
        try:
            # Prefer runner-snapshotted AST
            if hasattr(self._persistence, "get_runners_by_workflow"):
                for r in self._persistence.get_runners_by_workflow(workflow_id):
                    if r.compiled_ast and r.workflow_ast:
                        self._program_ast_cache[workflow_id] = r.compiled_ast
                        return r.workflow_ast

            # Fall back to flow lookup
            if not hasattr(self._persistence, "get_workflow"):
                return None

            wf = self._persistence.get_workflow(workflow_id)
            if not wf:
                return None

            if not hasattr(self._persistence, "get_flow"):
                return None

            flow = self._persistence.get_flow(wf.flow_id)
            if not flow:
                return None

            # Use stored compiled AST; fall back to recompilation for legacy flows
            program_dict = flow.compiled_ast
            if not program_dict:
                if not flow.compiled_sources:
                    return None
                import json

                from ..emitter import JSONEmitter
                from ..parser import FFLParser

                parser = FFLParser()
                ast = parser.parse(flow.compiled_sources[0].content)
                emitter = JSONEmitter(include_locations=False)
                program_dict = json.loads(emitter.emit(ast))
                logger.warning(
                    "Flow '%s' has no compiled_ast, fell back to recompilation", wf.flow_id
                )

            # At this point program_dict is guaranteed non-None (guarded above)
            if program_dict is None:
                return None

            # Cache program AST for facet definition lookups during resume
            self._program_ast_cache[workflow_id] = program_dict

            return self._find_workflow_in_program(program_dict, wf.name)
        except Exception:
            logger.debug("Could not load AST for workflow %s", workflow_id, exc_info=True)
            return None

    @staticmethod
    def _find_workflow_in_program(program_dict: dict, workflow_name: str) -> dict | None:
        """Find a workflow in the program AST by name."""
        from facetwork.ast_utils import find_workflow

        return find_workflow(program_dict, workflow_name)

    # =========================================================================
    # Server Registration
    # =========================================================================

    def _register_server(self) -> None:
        """Register this agent in the persistence store."""
        now = _current_time_ms()
        server = ServerDefinition(
            uuid=self._server_id,
            server_group=self._config.server_group,
            service_name=self._config.service_name,
            server_name=self._config.server_name,
            server_ips=self._get_server_ips(),
            start_time=now,
            ping_time=now,
            topics=list(self._handlers.keys()),
            handlers=list(self._handlers.keys()),
            handled=[],
            state=ServerState.RUNNING,
        )
        self._persistence.save_server(server)

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

    # =========================================================================
    # Heartbeat
    # =========================================================================

    def _heartbeat_loop(self) -> None:
        """Periodically update the server's ping_time."""
        interval_s = self._config.heartbeat_interval_ms / 1000.0
        while not self._stopping.wait(interval_s):
            try:
                self._persistence.update_server_ping(self._server_id, _current_time_ms())
            except Exception:
                logger.exception("Heartbeat failed")

    # =========================================================================
    # Poll Loop
    # =========================================================================

    def _poll_loop(self) -> None:
        """Main loop: poll for work until stopped."""
        interval_s = self._config.poll_interval_ms / 1000.0
        while not self._stopping.is_set():
            try:
                self._poll_cycle()
                self._maybe_reap_orphaned_tasks()
            except Exception:
                logger.exception("Poll cycle error")
            self._stopping.wait(interval_s)

    def _poll_cycle(self) -> int:
        """Single poll cycle: claim and dispatch tasks.

        Returns:
            Number of tasks dispatched.
        """
        self._cleanup_futures()

        capacity = self._config.max_concurrent - self._active_count()
        if capacity <= 0:
            return 0

        if not self._handlers:
            return 0

        dispatched = 0
        task_names = list(self._handlers.keys())

        while capacity > 0:
            task = self._persistence.claim_task(
                task_names=task_names,
                task_list=self._config.task_list,
                server_id=self._server_id,
            )
            if task is None:
                break
            self._submit_event(task)
            capacity -= 1
            dispatched += 1

        return dispatched

    def _active_count(self) -> int:
        """Get the number of active work items."""
        with self._active_lock:
            return len(self._active_futures)

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
            timeout_ms = int(os.environ.get("AFL_REAPER_TIMEOUT_MS", "120000"))
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
                        message=_reaper_message(task_info, reclaimer_name=self._config.server_name),
                        level=StepLogLevel.WARNING,
                        facet_name=task_info["name"],
                    )
        except Exception:
            logger.debug("Orphan reaper failed", exc_info=True)

        # --- Stuck task watchdog ---
        try:
            stuck_timeout_ms = int(os.environ.get("AFL_STUCK_TIMEOUT_MS", "1800000"))
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
                        message=_stuck_message(task_info, reclaimer_name=self._config.server_name),
                        level=StepLogLevel.WARNING,
                        facet_name=task_info["name"],
                    )
        except Exception:
            logger.debug("Stuck task watchdog failed", exc_info=True)

    def _cleanup_futures(self) -> None:
        """Remove completed futures and kill timed-out ones."""
        now = _current_time_ms()
        kept: list[tuple[Future, str, int]] = []
        with self._active_lock:
            for future, task_id, claimed_at in self._active_futures:
                if future.done():
                    continue
                elapsed = now - claimed_at
                if self._execution_timeout_ms > 0 and elapsed > self._execution_timeout_ms:
                    future.cancel()
                    logger.warning(
                        "Task %s timed out after %ds, resetting to pending",
                        task_id,
                        elapsed // 1000,
                    )
                    self._release_timed_out_task(task_id)
                    continue
                kept.append((future, task_id, claimed_at))
            self._active_futures = kept

    def _release_timed_out_task(self, task_id: str) -> None:
        """Reset a timed-out task to pending so it can be reclaimed."""
        try:
            task = self._persistence.get_task(task_id)
            if task and task.state == TaskState.RUNNING:
                task.state = TaskState.PENDING
                task.server_id = ""
                task.error = None
                task.updated = _current_time_ms()
                self._safe_save_task(task)
        except Exception:
            logger.debug("Could not release timed-out task %s", task_id, exc_info=True)

    def _safe_save_task(self, task: Any, retries: int = 3) -> None:
        """Save task state with retries to survive transient DB failures."""
        for attempt in range(retries):
            try:
                self._safe_save_task(task)
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
                    logger.error(
                        "save_task failed for %s after %d attempts",
                        task.uuid,
                        retries,
                        exc_info=True,
                    )

    def _submit_event(self, task: Any) -> None:
        """Submit an event task to the thread pool."""
        if self._executor is None:
            self._process_event(task)
            return

        future = self._executor.submit(self._process_event, task)
        now = _current_time_ms()
        with self._active_lock:
            self._active_futures.append((future, task.uuid, now))

    # =========================================================================
    # Step Log Emission
    # =========================================================================

    def _emit_step_log(
        self,
        step_id: str,
        workflow_id: str,
        message: str,
        source: str = StepLogSource.FRAMEWORK,
        level: str = StepLogLevel.INFO,
        facet_name: str = "",
        details: dict | None = None,
    ) -> None:
        """Create and save a step log entry."""
        entry = StepLogEntry(
            uuid=generate_id(),
            step_id=step_id,
            workflow_id=workflow_id,
            runner_id=self._server_id,
            facet_name=facet_name,
            source=source,
            level=level,
            message=message,
            details=details or {},
            time=_current_time_ms(),
        )
        try:
            self._persistence.save_step_log(entry)
        except Exception:
            logger.debug("Could not save step log for step %s", step_id, exc_info=True)

    # =========================================================================
    # Event Processing
    # =========================================================================

    def _process_event(self, task: Any) -> None:
        """Process an event task.

        1. Extract payload from task.data
        2. Look up callback by task.name (try qualified, then short name)
        3. Inject _step_log callback into payload
        4. Call callback(payload)
        5. On success: continue_step, resume workflow, mark task completed
        6. On failure: fail_step, mark task failed
        """
        try:
            payload = dict(task.data or {})  # shallow copy to avoid mutating task.data

            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=(
                    f"Task claimed: {task.name} "
                    f"(server={self._config.server_name}, id={self._server_id[:8]})"
                ),
                facet_name=task.name,
            )

            # Look up callback (try exact name, then short name)
            callback = self._handlers.get(task.name)
            if callback is None and "." in task.name:
                short_name = task.name.rsplit(".", 1)[-1]
                callback = self._handlers.get(short_name)

            if callback is None:
                error_msg = f"No handler for event task '{task.name}'"
                self._emit_step_log(
                    step_id=task.step_id,
                    workflow_id=task.workflow_id,
                    message=f"Handler error: {error_msg}",
                    level=StepLogLevel.ERROR,
                    facet_name=task.name,
                )
                self._evaluator.fail_step(task.step_id, error_msg)
                task.state = TaskState.FAILED
                task.error = {"message": error_msg}
                task.updated = _current_time_ms()
                self._safe_save_task(task)
                logger.warning(
                    "No handler for event task '%s' (step=%s)",
                    task.name,
                    task.step_id,
                )
                return

            # Inject _step_log callback for handler-level logging
            def _step_log_callback(message, level=StepLogLevel.INFO, details=None):
                self._emit_step_log(
                    step_id=task.step_id,
                    workflow_id=task.workflow_id,
                    message=message,
                    source=StepLogSource.HANDLER,
                    level=level,
                    facet_name=task.name,
                    details=details,
                )

            payload["_step_log"] = _step_log_callback

            # Inject _task_heartbeat callback so long-running handlers can
            # signal progress and avoid being reaped by the orphan detector.
            def _task_heartbeat_callback(
                progress_pct: int | None = None,
                progress_message: str | None = None,
            ) -> None:
                now = _current_time_ms()
                self._persistence.update_task_heartbeat(
                    task.uuid,
                    now,
                    progress_pct=progress_pct,
                    progress_message=progress_message,
                )

            payload["_task_heartbeat"] = _task_heartbeat_callback
            payload["_task_uuid"] = task.uuid

            # Retry context
            retry_count = getattr(task, "retry_count", 0) or 0
            payload["_retry_count"] = retry_count
            payload["_is_retry"] = retry_count > 0

            # Look up timeout — task-level (from FFL Timeout mixin)
            timeout_ms = getattr(task, "timeout_ms", 0) or 0
            timeout_s = timeout_ms / 1000.0 if timeout_ms > 0 else None
            timeout_label = f" (timeout {timeout_ms}ms)" if timeout_ms > 0 else ""

            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Dispatching handler: {task.name}{timeout_label}",
                facet_name=task.name,
            )
            dispatch_start = _current_time_ms()

            # Invoke callback (handle both sync and async)
            if timeout_s is not None and timeout_s > 0:
                with ThreadPoolExecutor(max_workers=1) as timeout_pool:
                    if inspect.iscoroutinefunction(callback):
                        future = timeout_pool.submit(asyncio.run, callback(payload))
                    else:
                        future = timeout_pool.submit(callback, payload)
                    try:
                        result = future.result(timeout=timeout_s)
                    except TimeoutError:
                        elapsed = _current_time_ms() - dispatch_start
                        error_msg = (
                            f"Handler timed out after {elapsed}ms "
                            f"(limit: {timeout_ms}ms): {task.name}"
                        )
                        self._emit_step_log(
                            step_id=task.step_id,
                            workflow_id=task.workflow_id,
                            message=error_msg,
                            level=StepLogLevel.ERROR,
                            facet_name=task.name,
                        )
                        task.state = TaskState.PENDING
                        task.error = None
                        task.server_id = ""
                        task.updated = _current_time_ms()
                        self._safe_save_task(task)
                        self._emit_step_log(
                            step_id=task.step_id,
                            workflow_id=task.workflow_id,
                            message=f"Step restarted after timeout — task reset to pending: {task.name}",
                            level=StepLogLevel.WARNING,
                            facet_name=task.name,
                        )
                        logger.warning(
                            "Handler timed out for '%s' (step=%s, "
                            "elapsed=%dms, limit=%dms), resetting to pending",
                            task.name,
                            task.step_id,
                            elapsed,
                            timeout_ms,
                        )
                        return
            elif inspect.iscoroutinefunction(callback):
                result = asyncio.run(callback(payload))
            else:
                result = callback(payload)

            dispatch_duration = _current_time_ms() - dispatch_start
            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Handler completed: {task.name} ({dispatch_duration}ms)",
                level=StepLogLevel.SUCCESS,
                facet_name=task.name,
            )

            # Continue the step with the result
            self._evaluator.continue_step(task.step_id, result)

            # Resume the workflow (scoped to the completed step)
            self._resume_workflow(task.workflow_id, task.runner_id, step_id=task.step_id)

            # Mark task completed
            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            self._safe_save_task(task)

            logger.info(
                "Processed event task %s (name=%s, step=%s)",
                task.uuid,
                task.name,
                task.step_id,
            )

        except Exception as exc:
            # Emit error step log
            try:
                self._emit_step_log(
                    step_id=task.step_id,
                    workflow_id=task.workflow_id,
                    message=f"Handler error: {exc}",
                    level=StepLogLevel.ERROR,
                    facet_name=task.name,
                )
            except Exception:
                pass
            # Fail the step and mark task as failed
            try:
                self._evaluator.fail_step(task.step_id, str(exc))
            except Exception:
                logger.debug("Could not fail step %s", task.step_id, exc_info=True)
            task.state = TaskState.FAILED
            task.error = {"message": str(exc)}
            task.updated = _current_time_ms()
            self._safe_save_task(task)
            logger.exception(
                "Error processing event task %s (name=%s)",
                task.uuid,
                task.name,
            )

    # =========================================================================
    # Workflow Resume
    # =========================================================================

    def _resume_workflow(self, workflow_id: str, runner_id: str = "", step_id: str = "") -> None:
        """Resume a paused workflow after step completion.

        Uses a per-workflow lock to prevent concurrent evaluator resumes.
        When a thread cannot acquire the lock, it marks the workflow as
        pending so the lock holder re-runs the resume after its current
        iteration completes.  This ensures no step transitions are lost
        when multiple tasks for the same workflow complete concurrently.

        When *step_id* is provided, uses ``evaluator.resume_step()`` for
        O(depth) processing instead of iterating all actionable steps.
        """
        # Acquire per-workflow lock to prevent concurrent resumes
        with self._resume_locks_lock:
            if workflow_id not in self._resume_locks:
                self._resume_locks[workflow_id] = threading.Lock()
            lock = self._resume_locks[workflow_id]

        if not lock.acquire(blocking=False):
            # Another thread is already resuming — mark pending so
            # the holder re-runs after its current iteration.
            with self._resume_pending_lock:
                self._resume_pending.add(workflow_id)
            logger.debug("Resume already in progress for workflow %s, marked pending", workflow_id)
            return

        try:
            self._do_resume(workflow_id, runner_id, step_id=step_id)

            # Re-run if other threads flagged a pending resume while
            # we held the lock.  Pending resumes use full resume()
            # since we don't know which specific step triggered them.
            while True:
                with self._resume_pending_lock:
                    if workflow_id not in self._resume_pending:
                        break
                    self._resume_pending.discard(workflow_id)
                self._do_resume(workflow_id, runner_id)
        finally:
            lock.release()

    def _do_resume(self, workflow_id: str, runner_id: str, step_id: str = "") -> None:
        """Execute a single evaluator resume for *workflow_id*.

        When *step_id* is provided, uses the focused ``resume_step()``
        path (O(depth)).  Otherwise falls back to full ``resume()``.
        """
        workflow_ast = self._ast_cache.get(workflow_id)
        if workflow_ast is None:
            workflow_ast = self._load_workflow_ast(workflow_id)
            if workflow_ast:
                self._ast_cache[workflow_id] = workflow_ast

        if workflow_ast is None:
            logger.warning(
                "No AST available for workflow %s, skipping resume",
                workflow_id,
            )
            return

        program_ast = self._program_ast_cache.get(workflow_id)

        if step_id:
            result = self._evaluator.resume_step(
                workflow_id,
                step_id,
                workflow_ast,
                program_ast=program_ast,
                runner_id=runner_id,
            )
        else:
            result = self._evaluator.resume(
                workflow_id, workflow_ast, program_ast=program_ast, runner_id=runner_id
            )

        if result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.ERROR):
            if runner_id:
                self._update_runner_state(runner_id, result)
            else:
                self._update_runner_terminal_state(workflow_id, result)

    def _update_runner_state(self, runner_id: str, result: ExecutionResult) -> None:
        """Update runner state based on execution result."""

        try:
            runner = self._persistence.get_runner(runner_id)
            if runner and runner.state == RunnerState.RUNNING:
                now = _current_time_ms()
                if result.status == ExecutionStatus.COMPLETED:
                    runner.state = RunnerState.COMPLETED
                    runner.end_time = now
                    runner.duration = now - (runner.start_time or now)
                elif result.status == ExecutionStatus.ERROR:
                    runner.state = RunnerState.FAILED
                    runner.end_time = now
                    runner.duration = now - (runner.start_time or now)
                self._persistence.save_runner(runner)
                logger.info("Updated runner %s state to %s", runner_id, runner.state)
        except Exception:
            logger.debug("Could not update runner %s", runner_id, exc_info=True)

    def _update_runner_terminal_state(self, workflow_id: str, result: ExecutionResult) -> None:
        """Update runner entity when workflow reaches a terminal state.

        Used when runner_id is not available (e.g. stuck-step sweep).
        Looks up runners by workflow_id instead.
        """
        if not hasattr(self._persistence, "get_runners_by_workflow"):
            return
        try:
            now = _current_time_ms()
            target_state = (
                RunnerState.COMPLETED
                if result.status == ExecutionStatus.COMPLETED
                else RunnerState.FAILED
            )
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

    # =========================================================================
    # Shutdown
    # =========================================================================

    def _shutdown(self) -> None:
        """Gracefully shut down the poller."""
        self._running = False

        # Wait for active work to complete
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            with self._active_lock:
                for future, _task_id, _claimed_at in self._active_futures:
                    try:
                        future.result(timeout=30)
                    except Exception:
                        pass
                self._active_futures.clear()
            self._executor = None

        # Deregister server
        try:
            self._deregister_server()
        except Exception:
            logger.exception("Error deregistering server")

        logger.info("AgentPoller stopped: server_id=%s", self._server_id)
