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
acquires distributed locks, dispatches events to registered ToolRegistry
handlers, and resumes workflows via the Evaluator.

Multiple instances can run concurrently on different machines, coordinated
through MongoDB locks and server registration.
"""

import json as _json
import logging
import os
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from ..agent import ToolRegistry
from ..entities import (
    HandledCount,
    LockMetaData,
    RunnerState,
    ServerDefinition,
    ServerState,
    TaskState,
)
from ..evaluator import Evaluator, ExecutionStatus
from ..persistence import PersistenceAPI
from ..states import StepState
from ..step import StepDefinition
from ..types import generate_id

logger = logging.getLogger(__name__)

RESUME_TASK_NAME = "afl:resume"


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


@dataclass
class RunnerConfig:
    """Configuration for the runner service."""

    server_group: str = "default"
    service_name: str = "afl-runner"
    server_name: str = ""
    topics: list[str] = field(default_factory=list)
    task_list: str = "default"
    poll_interval_ms: int = int(os.environ.get("AFL_POLL_INTERVAL_MS", "1000"))
    heartbeat_interval_ms: int = 10000
    lock_duration_ms: int = 60000
    lock_extend_interval_ms: int = 20000
    max_concurrent: int = int(os.environ.get("AFL_MAX_CONCURRENT", "2"))
    shutdown_timeout_ms: int = 30000
    http_port: int = 8080
    http_max_port_attempts: int = 20

    def __post_init__(self) -> None:
        if not self.server_name:
            self.server_name = socket.gethostname()


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
                "running": svc.is_running,
                "uptime_ms": uptime_ms,
                "handled": {
                    name: {"handled": c.handled, "not_handled": c.not_handled}
                    for name, c in svc._handled_counts.items()
                },
                "active_work_items": svc._active_count(),
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


class RunnerService:
    """Distributed runner service for processing event steps and tasks.

    Polls the persistence store for steps blocked at EVENT_TRANSMIT and
    pending tasks, acquires distributed locks, dispatches events to
    ToolRegistry handlers, and resumes workflows via the Evaluator.
    """

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
        self._active_futures: list[Future] = []
        self._active_lock = threading.Lock()
        self._handled_counts: dict[str, HandledCount] = {}
        self._ast_cache: dict[str, dict] = {}
        self._program_ast_cache: dict[str, dict] = {}
        self._start_time_ms: int = 0
        self._http_server: HTTPServer | None = None
        self._http_thread: threading.Thread | None = None

        # Register built-in task handler
        self._tool_registry.register("afl:execute", self._handle_execute_workflow)

    @property
    def server_id(self) -> str:
        """Get the server's unique ID."""
        return self._server_id

    @property
    def is_running(self) -> bool:
        """Check if the service is currently running."""
        return self._running

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
            self._start_http_server()
            self._register_server()
            logger.info(
                "Runner started: server_id=%s, server_name=%s, group=%s",
                self._server_id,
                self._config.server_name,
                self._config.server_group,
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
            except Exception:
                logger.exception("Poll cycle error")
            self._stopping.wait(interval_s)

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

        # Claim event tasks from the task queue
        event_names = self._get_event_names()
        if event_names:
            while capacity > 0:
                task = self._persistence.claim_task(
                    task_names=event_names,
                    task_list=self._config.task_list,
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
                task_list=self._config.task_list,
            )
            if task is None:
                break
            self._submit_resume_task(task)
            capacity -= 1
            dispatched += 1

        # Poll pending tasks (non-event tasks like afl:execute)
        tasks = self._poll_pending_tasks()
        for task in tasks:
            if capacity <= 0:
                break
            if self._try_claim_task(task):
                self._submit_task(task)
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
        return [name for name in self._tool_registry._handlers.keys() if name != "afl:execute"]

    def _poll_pending_tasks(self) -> list:
        """Find pending tasks for this runner's task list.

        Only returns tasks for built-in handlers (like afl:execute).
        User-registered event handlers are processed via claim_task
        (which respects topics filtering). Tasks with no handler at all
        are left untouched for the correct external agent.
        """
        tasks = list(self._persistence.get_pending_tasks(self._config.task_list))
        # Event handler names: user-registered handlers (everything except afl:execute)
        event_handler_names = {
            name for name in self._tool_registry._handlers.keys() if name != "afl:execute"
        }
        return [
            t for t in tasks
            if t.name not in event_handler_names
            and t.name in self._tool_registry._handlers
            and t.name != RESUME_TASK_NAME
        ]

    # =========================================================================
    # Locking
    # =========================================================================

    def _step_lock_key(self, step: StepDefinition) -> str:
        """Get the lock key for a step."""
        return f"runner:step:{step.id}"

    def _task_lock_key(self, task: Any) -> str:
        """Get the lock key for a task."""
        return f"runner:task:{task.uuid}"

    def _try_claim_step(self, step: StepDefinition) -> bool:
        """Try to acquire a distributed lock for a step."""
        key = self._step_lock_key(step)
        meta = LockMetaData(
            topic=step.facet_name,
            handler=step.facet_name,
            step_name=step.facet_name,
            step_id=step.id,
        )
        return self._persistence.acquire_lock(key, self._config.lock_duration_ms, meta)

    def _try_claim_task(self, task: Any) -> bool:
        """Try to acquire a distributed lock for a task."""
        key = self._task_lock_key(task)
        meta = LockMetaData(
            topic=task.name,
            handler=task.name,
            step_name=task.name,
            step_id=task.step_id,
        )
        return self._persistence.acquire_lock(key, self._config.lock_duration_ms, meta)

    def _release_step_lock(self, step: StepDefinition) -> None:
        """Release the lock for a step."""
        self._persistence.release_lock(self._step_lock_key(step))

    def _release_task_lock(self, task: Any) -> None:
        """Release the lock for a task."""
        self._persistence.release_lock(self._task_lock_key(task))

    # =========================================================================
    # Work Submission
    # =========================================================================

    def _submit_step(self, step: StepDefinition) -> None:
        """Submit a step for processing in the thread pool."""
        if self._executor is None:
            # Synchronous fallback (for run_once without start)
            self._process_step(step)
            return

        future = self._executor.submit(self._process_step, step)
        with self._active_lock:
            self._active_futures.append(future)

    def _submit_event_task(self, task: Any) -> None:
        """Submit an event task for processing in the thread pool."""
        if self._executor is None:
            self._process_event_task(task)
            return

        future = self._executor.submit(self._process_event_task, task)
        with self._active_lock:
            self._active_futures.append(future)

    def _submit_task(self, task: Any) -> None:
        """Submit a task for processing in the thread pool."""
        if self._executor is None:
            self._process_task(task)
            return

        future = self._executor.submit(self._process_task, task)
        with self._active_lock:
            self._active_futures.append(future)

    def _submit_resume_task(self, task: Any) -> None:
        """Submit a resume task for processing in the thread pool."""
        if self._executor is None:
            self._process_resume_task(task)
            return

        future = self._executor.submit(self._process_resume_task, task)
        with self._active_lock:
            self._active_futures.append(future)

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
        6. Release lock
        """
        lock_extend_stop = threading.Event()
        extend_thread = threading.Thread(
            target=self._extend_lock_loop,
            args=(self._step_lock_key(step), lock_extend_stop),
            daemon=True,
        )
        extend_thread.start()

        try:
            # Build payload
            payload = {name: attr.value for name, attr in step.attributes.params.items()}

            # Dispatch to handler (try qualified name first, then short name)
            result = self._tool_registry.handle(step.facet_name, payload)
            if result is None and "." in step.facet_name:
                short_name = step.facet_name.rsplit(".", 1)[-1]
                result = self._tool_registry.handle(short_name, payload)

            if result is None:
                # No handler available — release lock, leave for another server
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
        finally:
            lock_extend_stop.set()
            extend_thread.join(timeout=1)
            self._release_step_lock(step)

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
            payload = task.data or {}

            # Dispatch to handler (try exact name, then short name)
            result = self._tool_registry.handle(task.name, payload)
            if result is None and "." in task.name:
                short_name = task.name.rsplit(".", 1)[-1]
                result = self._tool_registry.handle(short_name, payload)

            if result is None:
                error_msg = f"No handler for event task '{task.name}'"
                try:
                    self._evaluator.fail_step(task.step_id, error_msg)
                except Exception:
                    logger.debug("Could not fail step %s", task.step_id, exc_info=True)
                task.state = TaskState.FAILED
                task.error = {"message": error_msg}
                task.updated = _current_time_ms()
                self._persistence.save_task(task)
                self._update_handled_stats(task.name, handled=False)
                logger.warning(
                    "No handler for event task '%s' (step=%s)",
                    task.name,
                    task.step_id,
                )
                return

            # Continue the step with the result
            self._evaluator.continue_step(task.step_id, result)

            # Resume the workflow
            self._resume_workflow(task.workflow_id)

            # Mark task completed
            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            self._persistence.save_task(task)

            # Update stats
            self._update_handled_stats(task.name, handled=True)

            logger.info(
                "Processed event task %s (name=%s, step=%s)",
                task.uuid,
                task.name,
                task.step_id,
            )

        except Exception as exc:
            try:
                self._evaluator.fail_step(task.step_id, str(exc))
            except Exception:
                logger.debug("Could not fail step %s", task.step_id, exc_info=True)
            task.state = TaskState.FAILED
            task.error = {"message": str(exc)}
            task.updated = _current_time_ms()
            self._persistence.save_task(task)
            self._update_handled_stats(task.name, handled=False)
            logger.exception(
                "Error processing event task %s (name=%s)",
                task.uuid,
                task.name,
            )

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
        try:
            data = task.data or {}
            step_id = data.get("step_id") or task.step_id
            workflow_id = data.get("workflow_id") or task.workflow_id

            if not step_id:
                raise ValueError("Resume task missing step_id")

            # continue_step validates the step is at EVENT_TRANSMIT
            # and sets request_transition=True. Pass empty result
            # because the external agent already wrote return attributes.
            self._evaluator.continue_step(step_id, {})

            # Resume the workflow
            self._resume_workflow(workflow_id)

            # Mark task completed
            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            self._persistence.save_task(task)

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
            self._persistence.save_task(task)
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
        """Process a single pending task.

        1. Mark task as running
        2. Dispatch to handler
        3. Mark task as completed/failed
        4. Release lock
        """
        lock_extend_stop = threading.Event()
        extend_thread = threading.Thread(
            target=self._extend_lock_loop,
            args=(self._task_lock_key(task), lock_extend_stop),
            daemon=True,
        )
        extend_thread.start()

        try:
            # Mark as running
            task.state = TaskState.RUNNING
            task.updated = _current_time_ms()
            self._persistence.save_task(task)

            # Dispatch
            payload = task.data or {}
            result = self._tool_registry.handle(task.name, payload)

            if result is not None:
                task.state = TaskState.COMPLETED
            else:
                task.state = TaskState.FAILED
                task.error = {"message": f"No handler for task '{task.name}'"}

            task.updated = _current_time_ms()
            self._persistence.save_task(task)

            logger.info("Processed task %s (name=%s, state=%s)", task.uuid, task.name, task.state)

        except Exception as exc:
            task.state = TaskState.FAILED
            task.error = {"message": str(exc)}
            task.updated = _current_time_ms()
            self._persistence.save_task(task)
            logger.exception("Error processing task %s", task.uuid)
        finally:
            lock_extend_stop.set()
            extend_thread.join(timeout=1)
            self._release_task_lock(task)

    # =========================================================================
    # Built-in Task Handlers
    # =========================================================================

    def _handle_execute_workflow(self, payload: dict) -> dict:
        """Handle an afl:execute task.

        Loads the flow from persistence, parses AFL source, finds the
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

            flow = self._persistence.get_flow(flow_id)
            if not flow:
                raise RuntimeError(f"Flow '{flow_id}' not found")

            # Use stored compiled AST; fall back to recompilation for legacy flows
            program_dict = flow.compiled_ast
            if not program_dict:
                if not flow.compiled_sources:
                    raise RuntimeError(f"Flow '{flow_id}' has no compiled AST or sources")
                import json

                from ...emitter import JSONEmitter
                from ...parser import AFLParser

                parser = AFLParser()
                ast = parser.parse(flow.compiled_sources[0].content)
                emitter = JSONEmitter(include_locations=False)
                program_dict = json.loads(emitter.emit(ast))
                logger.warning("Flow '%s' has no compiled_ast, fell back to recompilation", flow_id)

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

    def _find_workflow_in_program(self, program_dict: dict, workflow_name: str) -> dict | None:
        """Find a workflow in the program AST by name."""
        from afl.ast_utils import find_workflow

        return find_workflow(program_dict, workflow_name)

    # =========================================================================
    # Workflow Resume
    # =========================================================================

    def _resume_workflow(self, workflow_id: str) -> None:
        """Resume a paused workflow after step completion.

        Uses a cached AST when available.
        """
        workflow_ast = self._ast_cache.get(workflow_id)
        if workflow_ast is None:
            # Attempt to load from persistence if available
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
        self._evaluator.resume(workflow_id, workflow_ast, program_ast=program_ast)

    def _load_workflow_ast(self, workflow_id: str) -> dict | None:
        """Attempt to load the workflow AST from persistence.

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

                from ...emitter import JSONEmitter
                from ...parser import AFLParser

                parser = AFLParser()
                ast = parser.parse(flow.compiled_sources[0].content)
                emitter = JSONEmitter(include_locations=False)
                program_dict = json.loads(emitter.emit(ast))
                logger.warning("Flow '%s' has no compiled_ast, fell back to recompilation", wf.flow_id)

            # Cache program AST for facet definition lookups during resume
            self._program_ast_cache[workflow_id] = program_dict

            return self._find_workflow_in_program(program_dict, wf.name)
        except Exception:
            logger.debug("Could not load AST for workflow %s", workflow_id, exc_info=True)
            return None

    def cache_workflow_ast(self, workflow_id: str, ast: dict) -> None:
        """Pre-cache a workflow AST for use during processing.

        Args:
            workflow_id: The workflow ID
            ast: The compiled workflow AST dict
        """
        self._ast_cache[workflow_id] = ast

    # =========================================================================
    # Lock Extension
    # =========================================================================

    def _extend_lock_loop(self, lock_key: str, stop_event: threading.Event) -> None:
        """Periodically extend a lock until the stop event is set."""
        interval_s = self._config.lock_extend_interval_ms / 1000.0
        while not stop_event.wait(interval_s):
            try:
                if not self._persistence.extend_lock(lock_key, self._config.lock_duration_ms):
                    logger.warning("Failed to extend lock: %s", lock_key)
                    break
            except Exception:
                logger.exception("Error extending lock: %s", lock_key)
                break

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

        # Update server definition
        try:
            server = self._persistence.get_server(self._server_id)
            if server:
                server.handled = list(self._handled_counts.values())
                self._persistence.save_server(server)
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
                for future in self._active_futures:
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
