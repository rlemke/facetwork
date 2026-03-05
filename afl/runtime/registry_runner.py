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

"""AFL Registry Runner.

A universal runner that reads handler registrations from persistence,
dynamically loads Python modules, caches them, and dispatches tasks.
This eliminates the need for per-facet microservices — developers
register a ``(facet_name, module_uri, entrypoint)`` tuple and the
RegistryRunner handles the rest.

Example usage::

    from afl.runtime import MemoryStore, Evaluator, Telemetry
    from afl.runtime.registry_runner import RegistryRunner, RegistryRunnerConfig

    store = MemoryStore()
    evaluator = Evaluator(persistence=store)

    runner = RegistryRunner(
        persistence=store,
        evaluator=evaluator,
        config=RegistryRunnerConfig(service_name="my-registry-runner"),
    )

    # Register a handler (persisted — survives restarts)
    runner.register_handler(
        facet_name="ns.CountDocuments",
        module_uri="my.handlers",
        entrypoint="count_documents",
    )

    runner.start()  # blocks until stopped
"""

import fnmatch
import logging
import os
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .dispatcher import RegistryDispatcher
from .entities import (
    HandlerRegistration,
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


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


@dataclass
class RegistryRunnerConfig:
    """Configuration for the RegistryRunner."""

    service_name: str = "afl-registry-runner"
    server_group: str = "default"
    server_name: str = ""
    task_list: str = "default"
    poll_interval_ms: int = int(os.environ.get("AFL_POLL_INTERVAL_MS", "1000"))
    max_concurrent: int = int(os.environ.get("AFL_MAX_CONCURRENT", "2"))
    heartbeat_interval_ms: int = 10000
    registry_refresh_interval_ms: int = 30000
    topics: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.server_name:
            self.server_name = socket.gethostname()


class RegistryRunner:
    """Universal runner that dynamically loads handlers from persistence.

    Instead of requiring developers to write standalone microservices,
    handler registrations are stored in the persistence layer and
    loaded on demand. Module loading results are cached by
    ``(module_uri, checksum)`` for efficiency.
    """

    def __init__(
        self,
        persistence: PersistenceAPI,
        evaluator: Evaluator,
        config: RegistryRunnerConfig | None = None,
    ) -> None:
        self._persistence = persistence
        self._evaluator = evaluator
        self._config = config or RegistryRunnerConfig()

        self._server_id = generate_id()
        self._running = False
        self._stopping = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        self._active_futures: list[Future] = []
        self._active_lock = threading.Lock()
        self._ast_cache: dict[str, dict] = {}
        self._program_ast_cache: dict[str, dict] = {}
        self._resume_locks: dict[str, threading.Lock] = {}
        self._resume_locks_lock = threading.Lock()

        # Shared dispatcher for inline execution and _process_event
        self._dispatcher = RegistryDispatcher(
            persistence=persistence,
            topics=self._config.topics if self._config.topics else None,
        )

        # Registry-specific state (delegate module cache to dispatcher)
        self._module_cache = self._dispatcher.module_cache
        self._registered_names: list[str] = []
        self._last_refresh: int = 0

    @property
    def server_id(self) -> str:
        """Get the server's unique ID."""
        return self._server_id

    @property
    def is_running(self) -> bool:
        """Check if the runner is currently running."""
        return self._running

    # =========================================================================
    # Handler Registration (convenience API)
    # =========================================================================

    def register_handler(
        self,
        facet_name: str,
        module_uri: str,
        entrypoint: str = "handle",
        version: str = "1.0.0",
        checksum: str = "",
        timeout_ms: int = 30000,
        requirements: list[str] | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Register a handler in persistence (convenience method).

        Creates a ``HandlerRegistration`` and saves it to the persistence
        store. The registration is picked up on the next registry refresh.

        Args:
            facet_name: Qualified event facet name (e.g. "ns.CountDocuments")
            module_uri: Python module path or ``file:///path/to/module.py``
            entrypoint: Function name within the module (default: "handle")
            version: Handler version string
            checksum: Cache-invalidation checksum
            timeout_ms: Handler timeout in milliseconds
            requirements: Optional pip requirements
            metadata: Optional metadata dict
        """
        now = _current_time_ms()
        reg = HandlerRegistration(
            facet_name=facet_name,
            module_uri=module_uri,
            entrypoint=entrypoint,
            version=version,
            checksum=checksum,
            timeout_ms=timeout_ms,
            requirements=requirements or [],
            metadata=metadata or {},
            created=now,
            updated=now,
        )
        self._persistence.save_handler_registration(reg)
        # Force immediate refresh so the name is available for polling
        self._refresh_registry()

    def registered_names(self) -> list[str]:
        """Return the list of registered facet names (from persistence)."""
        self._maybe_refresh_registry()
        return list(self._registered_names)

    # =========================================================================
    # Registry Refresh
    # =========================================================================

    def _matches_topics(self, facet_name: str) -> bool:
        """Check if a facet name matches any configured topic pattern."""
        return any(fnmatch.fnmatch(facet_name, pattern) for pattern in self._config.topics)

    def _refresh_registry(self) -> None:
        """Reload handler registrations from persistence."""
        registrations = self._persistence.list_handler_registrations()
        names = [r.facet_name for r in registrations]
        if self._config.topics:
            names = [n for n in names if self._matches_topics(n)]
        self._registered_names = names
        self._last_refresh = _current_time_ms()

    def _maybe_refresh_registry(self) -> None:
        """Refresh the registry if the refresh interval has elapsed."""
        now = _current_time_ms()
        if now - self._last_refresh >= self._config.registry_refresh_interval_ms:
            self._refresh_registry()

    def update_step(self, step_id: str, partial_result: dict) -> None:
        """Update a step with partial results (for streaming handlers).

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
        """Start the runner (blocking).

        Registers the server, starts the heartbeat thread, and enters
        the main poll loop. Blocks until stop() is called.
        """
        self._running = True
        self._stopping.clear()
        self._executor = ThreadPoolExecutor(max_workers=self._config.max_concurrent)

        try:
            self._refresh_registry()
            self._register_server()
            logger.info(
                "RegistryRunner started: server_id=%s, service=%s, handlers=%s",
                self._server_id,
                self._config.service_name,
                self._registered_names,
            )

            # Start heartbeat daemon
            heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            heartbeat_thread.start()

            # Main poll loop
            self._poll_loop()

        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the runner to stop gracefully."""
        logger.info("RegistryRunner stopping: server_id=%s", self._server_id)
        self._stopping.set()

    def poll_once(self) -> int:
        """Run a single poll cycle (synchronous, for testing).

        Does not use the thread pool executor. Claims and processes
        tasks sequentially.

        Returns:
            Number of tasks dispatched.
        """
        self._maybe_refresh_registry()

        if not self._registered_names:
            return 0

        capacity = self._config.max_concurrent - self._active_count()
        if capacity <= 0:
            return 0

        dispatched = 0
        task_names = list(self._registered_names)

        while capacity > 0:
            task = self._persistence.claim_task(
                task_names=task_names,
                task_list=self._config.task_list,
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
        """Load a workflow AST from persistence if available."""
        try:
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
                from ..parser import AFLParser

                parser = AFLParser()
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
        from afl.ast_utils import find_workflow

        return find_workflow(program_dict, workflow_name)

    # =========================================================================
    # Server Registration
    # =========================================================================

    def _register_server(self) -> None:
        """Register this runner in the persistence store."""
        now = _current_time_ms()
        server = ServerDefinition(
            uuid=self._server_id,
            server_group=self._config.server_group,
            service_name=self._config.service_name,
            server_name=self._config.server_name,
            server_ips=self._get_server_ips(),
            start_time=now,
            ping_time=now,
            topics=list(self._registered_names),
            handlers=list(self._registered_names),
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
                self._maybe_refresh_registry()
                self._poll_cycle()
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

        if not self._registered_names:
            return 0

        dispatched = 0
        task_names = list(self._registered_names)

        while capacity > 0:
            task = self._persistence.claim_task(
                task_names=task_names,
                task_list=self._config.task_list,
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

    def _cleanup_futures(self) -> None:
        """Remove completed futures from the active list."""
        with self._active_lock:
            self._active_futures = [f for f in self._active_futures if not f.done()]

    def _submit_event(self, task: Any) -> None:
        """Submit an event task to the thread pool."""
        if self._executor is None:
            self._process_event(task)
            return

        future = self._executor.submit(self._process_event, task)
        with self._active_lock:
            self._active_futures.append(future)

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
        """Process an event task via dynamic handler lookup.

        Delegates handler loading and invocation to the shared
        RegistryDispatcher. On success, continues the step and
        resumes the workflow; on failure, fails the step.
        """
        try:
            payload = dict(task.data or {})  # shallow copy to avoid mutating task.data

            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Task claimed: {task.name}",
                facet_name=task.name,
            )

            if not self._dispatcher.can_dispatch(task.name):
                error_msg = f"No handler registration for event task '{task.name}'"
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
                self._persistence.save_task(task)
                logger.warning(
                    "No handler registration for event task '%s' (step=%s)",
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

            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Dispatching handler: {task.name}",
                facet_name=task.name,
            )
            dispatch_start = _current_time_ms()

            # Dispatch via shared dispatcher (handles module loading + async detection)
            try:
                result = self._dispatcher.dispatch(task.name, payload)
            except (ImportError, AttributeError, TypeError) as exc:
                error_msg = f"Failed to load handler for '{task.name}': {exc}"
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
                self._persistence.save_task(task)
                logger.exception(
                    "Failed to load handler for '%s' (step=%s)",
                    task.name,
                    task.step_id,
                )
                return

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

            # Resume the workflow
            self._resume_workflow(task.workflow_id, task.runner_id)

            # Mark task completed
            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            self._persistence.save_task(task)

            logger.info(
                "Processed event task %s (name=%s, step=%s)",
                task.uuid,
                task.name,
                task.step_id,
            )

        except Exception as exc:
            # Fail the step and mark task as failed
            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Handler error: {exc}",
                level=StepLogLevel.ERROR,
                facet_name=task.name,
            )
            try:
                self._evaluator.fail_step(task.step_id, str(exc))
            except Exception:
                logger.debug("Could not fail step %s", task.step_id, exc_info=True)
            task.state = TaskState.FAILED
            task.error = {"message": str(exc)}
            task.updated = _current_time_ms()
            self._persistence.save_task(task)
            logger.exception(
                "Error processing event task %s (name=%s)",
                task.uuid,
                task.name,
            )

    # =========================================================================
    # Workflow Resume
    # =========================================================================

    def _resume_workflow(self, workflow_id: str, runner_id: str = "") -> None:
        """Resume a paused workflow after step completion."""
        # Acquire per-workflow lock to prevent concurrent resumes
        with self._resume_locks_lock:
            if workflow_id not in self._resume_locks:
                self._resume_locks[workflow_id] = threading.Lock()
            lock = self._resume_locks[workflow_id]

        if not lock.acquire(blocking=False):
            logger.debug("Resume already in progress for workflow %s, skipping", workflow_id)
            return

        try:
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
            result = self._evaluator.resume(
                workflow_id,
                workflow_ast,
                program_ast=program_ast,
                runner_id=runner_id,
                dispatcher=self._dispatcher,
            )

            if runner_id and result.status in (
                ExecutionStatus.COMPLETED,
                ExecutionStatus.ERROR,
            ):
                self._update_runner_state(runner_id, result)
        finally:
            lock.release()

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

    # =========================================================================
    # Shutdown
    # =========================================================================

    def _shutdown(self) -> None:
        """Gracefully shut down the runner."""
        self._running = False

        # Wait for active work to complete
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            with self._active_lock:
                for future in self._active_futures:
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

        logger.info("RegistryRunner stopped: server_id=%s", self._server_id)


# =========================================================================
# Factory Helper
# =========================================================================


def create_registry_runner(
    service_name: str,
    *,
    server_group: str = "default",
    max_concurrent: int | None = None,
    poll_interval_ms: int | None = None,
    topics: list[str] | None = None,
    telemetry_enabled: bool = True,
) -> RegistryRunner:
    """Create a fully-wired RegistryRunner with sensible defaults.

    This is a convenience factory that sets up MongoStore, Evaluator, and
    RegistryRunnerConfig from the standard AFL configuration.  It eliminates
    the 7-line bootstrap that every example otherwise duplicates.

    Args:
        service_name: Logical service name (e.g. "noaa-weather").
        server_group: Server group for clustering (default "default").
        max_concurrent: Override for AFL_MAX_CONCURRENT env var.
        poll_interval_ms: Override for AFL_POLL_INTERVAL_MS env var.
        topics: Optional topic/glob filters for handler selection.
        telemetry_enabled: Whether to enable telemetry (default True).

    Returns:
        A ready-to-use :class:`RegistryRunner` — call ``start()`` to begin.
    """
    from ..config import load_config
    from .mongo_store import MongoStore
    from .telemetry import Telemetry

    config = load_config()
    store = MongoStore.from_config(config.mongodb)
    evaluator = Evaluator(
        persistence=store,
        telemetry=Telemetry(enabled=telemetry_enabled),
    )

    kwargs: dict[str, Any] = {"service_name": service_name, "server_group": server_group}
    if max_concurrent is not None:
        kwargs["max_concurrent"] = max_concurrent
    if poll_interval_ms is not None:
        kwargs["poll_interval_ms"] = poll_interval_ms
    if topics is not None:
        kwargs["topics"] = topics

    runner_config = RegistryRunnerConfig(**kwargs)
    return RegistryRunner(persistence=store, evaluator=evaluator, config=runner_config)
