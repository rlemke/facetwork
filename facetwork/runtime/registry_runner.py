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

    from facetwork.runtime import MemoryStore, Evaluator, Telemetry
    from facetwork.runtime.registry_runner import RegistryRunner, RegistryRunnerConfig

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
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..ast_features import known_features as _known_ast_features
from .base_runner import BaseRunner
from .cancellation import HandlerCancelled
from .dispatcher import RegistryDispatcher
from .entities import (
    HandlerRegistration,
    RunnerState,
    ServerDefinition,
    ServerState,
    StepLogLevel,
    StepLogSource,
    TaskState,
)
from .errors import PermanentError
from .evaluator import Evaluator, ExecutionResult, ExecutionStatus
from .persistence import PersistenceAPI
from .runner_config import BaseRunnerConfig
from .states import StepState
from .types import AttributeValue, generate_id

logger = logging.getLogger(__name__)


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


@dataclass
class RegistryRunnerConfig(BaseRunnerConfig):
    """Configuration for the RegistryRunner.

    Extends BaseRunnerConfig with handler registry refresh settings.
    """

    service_name: str = "afl-registry-runner"
    registry_refresh_interval_ms: int = 30000


class RegistryRunner(BaseRunner):
    """Universal runner that dynamically loads handlers from persistence.

    Instead of requiring developers to write standalone microservices,
    handler registrations are stored in the persistence layer and
    loaded on demand. Module loading results are cached by
    ``(module_uri, checksum)`` for efficiency.
    """

    # Narrow the inherited BaseRunner._config to this runner's config subtype
    # so its registry-refresh field is visible to the type checker.
    _config: RegistryRunnerConfig

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
        self._work_ready = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        # (future, task_id, claimed_at_ms) so the shared _cleanup_futures can
        # reap execution-timed-out handlers instead of pinning a slot forever.
        self._active_futures: list[tuple[Future, str, int]] = []
        self._active_lock = threading.Lock()
        self._execution_timeout_ms: int = int(
            os.environ.get("FW_TASK_EXECUTION_TIMEOUT_MS", "900000")
        )  # default 15 minutes
        self._ast_cache: dict[str, dict] = {}
        self._program_ast_cache: dict[str, dict] = {}
        self._resume_locks: dict[str, threading.Lock] = {}
        self._resume_locks_lock = threading.Lock()
        self._resume_pending: dict[str, dict[str, Any]] = {}
        self._resume_pending_lock = threading.Lock()

        # Shared dispatcher for inline execution and _process_event
        self._dispatcher = RegistryDispatcher(
            persistence=persistence,
            topics=self._config.topics if self._config.topics else None,
        )
        # BaseRunner._process_continuation dispatches inline through this.
        self._continuation_dispatcher = self._dispatcher

        # Registry-specific state (delegate module cache to dispatcher)
        self._module_cache = self._dispatcher.module_cache
        self._registered_names: list[str] = []
        self._last_refresh: int = 0
        self._last_sweep: int = 0
        self._sweep_interval_ms: int = 5000

        # Per-handler circuit breakers (parity with RunnerService): stop claiming
        # a facet whose handler keeps failing so a broken handler can't burn the
        # poll loop re-claiming + re-failing; a half-open probe lets it recover.
        from .circuit_breaker import CircuitBreakerRegistry

        self._circuit_breakers = CircuitBreakerRegistry()

    # server_id / is_running: inherited from BaseRunner.

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
        """Check if a facet name matches any configured topic pattern.

        The framework's own facets always match — see
        :func:`~facetwork.runtime.task_list_routing.is_ambient`.
        """
        from .task_list_routing import is_ambient

        if is_ambient(facet_name):
            return True
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
        self._work_ready.set()  # wake a poll loop parked in _poll_pause

    def _poll_task_lists(self) -> list[str]:
        """Lists this runner polls: the namespaces of its loaded handlers plus
        its configured list (namespace routing). A task is claimable
        only if it's on one of these AND the runner has its handler, so the
        queue label follows the handler — no per-runner task-list config needed.
        """
        from .task_list_routing import namespaces_for

        return sorted(set(namespaces_for(self._registered_names)) | {self._config.task_list})

    def poll_once(self) -> int:
        """Run a single poll cycle (synchronous, for testing).

        Does not use the thread pool executor. Claims and processes
        tasks sequentially.  Also processes continuation tasks.

        Returns:
            Number of tasks dispatched.
        """
        self._maybe_refresh_registry()

        capacity = self._config.max_concurrent - self._active_count()
        if capacity <= 0:
            return 0

        dispatched = 0

        # Claim handler tasks — skip facets whose circuit breaker is OPEN so a
        # persistently-failing handler stops being re-claimed (and re-failed)
        # until its half-open probe window.
        if self._registered_names:
            task_names = [n for n in self._registered_names if self._circuit_breakers.is_allowed(n)]
            while task_names and capacity > 0:
                task = self._persistence.claim_task(
                    task_names=task_names,
                    task_list=self._poll_task_lists(),
                    server_id=self._server_id,
                    known_features=_known_ast_features(),
                )
                if task is None:
                    break
                self._process_event(task)
                capacity -= 1
                dispatched += 1

        # Claim continuation tasks (may have been generated during
        # handler processing above).  Loop until no more continuations
        # are available — each processed continuation may generate more.
        # Skipped for handler-only runners (--continuation off): a dedicated
        # ffl-runner owns the shared backlog. See
        # docs/architecture/ffl-runner-orchestration-tier.md.
        if self._config.polls_shared_continuations():
            from .continuation import CONTINUATION_TASK_LIST, CONTINUATION_TASK_NAME

            while capacity > 0:
                task = self._persistence.claim_task(
                    task_names=[CONTINUATION_TASK_NAME],
                    task_list=CONTINUATION_TASK_LIST,
                    server_id=self._server_id,
                    known_features=_known_ast_features(),
                )
                if task is None:
                    break
                self._process_continuation(task)
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

    # _load_workflow_ast / _find_workflow_in_program: inherited from BaseRunner
    # (snapshot-preferring — reads runner.compiled_ast before the flow lookup).

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
            task_list=self._config.task_list,
            provided_environments=self._provided_environments(),
            ast_features=self._known_ast_features(),
        )
        self._persistence.save_server(server)

    # _deregister_server / _get_server_ips / _heartbeat_loop: inherited from BaseRunner.

    # =========================================================================
    # Poll Loop
    # =========================================================================

    def _poll_loop(self) -> None:
        """Main loop: poll for work until stopped."""
        interval_s = self._config.poll_interval_ms / 1000.0
        while not self._stopping.is_set():
            dispatched = 0
            try:
                self._maybe_refresh_registry()
                dispatched = self._poll_cycle()
                self._maybe_sweep_stuck_steps()
                self._maybe_materialize_environments()
            except Exception:
                logger.exception("Poll cycle error")
            self._poll_pause(interval_s, dispatched)

    def _poll_cycle(self) -> int:
        """Single poll cycle: claim and dispatch handler + continuation tasks.

        Returns:
            Number of tasks dispatched.
        """
        self._cleanup_futures()

        provided_envs = self._provided_environments()

        capacity = self._config.max_concurrent - self._active_count()
        if capacity <= 0:
            return 0

        dispatched = 0

        # Claim handler tasks — skip facets whose circuit breaker is OPEN so a
        # persistently-failing handler stops being re-claimed (and re-failed)
        # until its half-open probe window.
        if self._registered_names:
            task_names = [n for n in self._registered_names if self._circuit_breakers.is_allowed(n)]
            while task_names and capacity > 0:
                task = self._persistence.claim_task(
                    task_names=task_names,
                    task_list=self._poll_task_lists(),
                    server_id=self._server_id,
                    known_features=_known_ast_features(),
                )
                if task is None:
                    break
                self._submit_event(task)
                capacity -= 1
                dispatched += 1

        # Claim env-routed script tasks this runner's environments can serve
        # (script-environments.md §3 — the environment IS the capability).
        if provided_envs:
            while capacity > 0:
                task = self._persistence.claim_script_task(provided_envs, server_id=self._server_id)
                if task is None:
                    break
                self._submit_event(task)
                capacity -= 1
                dispatched += 1

        # Claim continuation tasks (internal step-processing events).
        # Skipped for handler-only runners (--continuation off): the ffl-runner
        # tier owns the shared backlog. See
        # docs/architecture/ffl-runner-orchestration-tier.md.
        if self._config.polls_shared_continuations():
            from .continuation import CONTINUATION_TASK_LIST, CONTINUATION_TASK_NAME

            while capacity > 0:
                task = self._persistence.claim_task(
                    task_names=[CONTINUATION_TASK_NAME],
                    task_list=CONTINUATION_TASK_LIST,
                    server_id=self._server_id,
                    known_features=_known_ast_features(),
                )
                if task is None:
                    break
                self._submit_continuation(task)
                capacity -= 1
                dispatched += 1

        return dispatched

    # _active_count / _cleanup_futures (now execution-timeout-aware): inherited from BaseRunner.

    def _submit_event(self, task: Any) -> None:
        """Submit an event task to the thread pool."""
        if self._executor is None:
            self._process_event(task)
            return

        future = self._executor.submit(self._process_event, task)
        future.add_done_callback(self._on_future_done)
        with self._active_lock:
            self._active_futures.append((future, task.uuid, _current_time_ms()))

    def _submit_continuation(self, task: Any) -> None:
        """Submit a continuation task to the thread pool."""
        if self._executor is None:
            self._process_continuation(task)
            return

        future = self._executor.submit(self._process_continuation, task)
        future.add_done_callback(self._on_future_done)
        with self._active_lock:
            self._active_futures.append((future, task.uuid, _current_time_ms()))

    # _process_continuation: inherited from BaseRunner (uses
    # self._continuation_dispatcher, set to this runner's RegistryDispatcher).

    # =========================================================================
    # Stuck-Step Recovery Sweep
    # =========================================================================

    def _maybe_sweep_stuck_steps(self) -> None:
        """Periodically process stuck steps directly.

        Safety net for steps that should have been processed but weren't
        (e.g. lost continuation events, server crashes).  Processes
        stuck steps via process_single_step, which follows the parent
        chain and generates continuation events as needed.

        Skipped for handler-only runners (--continuation off): the ffl-runner
        tier owns the sweep. See docs/architecture/ffl-runner-orchestration-tier.md.
        """
        if not self._config.runs_stuck_step_sweep():
            return
        now = _current_time_ms()
        if now - self._last_sweep < self._sweep_interval_ms:
            return
        self._last_sweep = now

        # Event handlers take priority. If every worker slot is occupied (e.g.
        # by long-running osmium exports), skip the sweep so it cannot run ahead
        # of active work on the poll thread.
        if self._active_count() >= self._config.max_concurrent:
            return

        # Bound the work per sweep. process_single_step runs SYNCHRONOUSLY on the
        # poll thread, so an unbounded sweep over a large fan-out (a foreach with
        # hundreds of sub-blocks) runs longer than the poll interval and starves
        # _poll_cycle's event-task claiming — the very steps it tries to unstick
        # never get their handler dispatched, so the next sweep re-finds them and
        # the loop livelocks (runners busy sweeping, 0 events claimed). Cap the
        # count and wall-clock time; the remainder is handled by the next sweep,
        # by which point normal claiming has advanced the backlog.
        SWEEP_MAX_STEPS = 25
        SWEEP_MAX_MS = 1500
        processed = 0
        sweep_start = now

        try:
            workflow_ids = self._persistence.get_pending_resume_workflow_ids()
            if not workflow_ids:
                return

            # Resolve workflow names for readable logging
            wf_names: dict[str, str] = {}
            for wf_id in workflow_ids:
                try:
                    runners = self._persistence.get_runners_by_workflow(wf_id)
                    if runners:
                        wf_names[wf_id] = runners[0].workflow.name
                except Exception:
                    pass

            names = ", ".join(wf_names.get(wid, wid[:12]) for wid in workflow_ids)
            logger.info(
                "Stuck-step sweep: %d workflow(s) need resume: %s",
                len(workflow_ids),
                names,
            )

            capped = False
            for wf_id in workflow_ids:
                if processed >= SWEEP_MAX_STEPS or _current_time_ms() - sweep_start > SWEEP_MAX_MS:
                    capped = True
                    break
                steps = self._persistence.get_actionable_steps_by_workflow(wf_id)
                stuck = [
                    s
                    for s in steps
                    if not StepState.is_terminal(s.state)
                    and not (
                        s.state == StepState.EVENT_TRANSMIT
                        and not s.transition.is_requesting_state_change
                    )
                ]
                if stuck:
                    step_details = ", ".join(
                        f"{s.statement_name or s.facet_name or s.object_type} ({s.state})"
                        for s in stuck[:SWEEP_MAX_STEPS]
                    )
                    logger.info(
                        "Sweep workflow %s: %d stuck steps: %s",
                        wf_names.get(wf_id, wf_id[:12]),
                        len(stuck),
                        step_details,
                    )

                for step in stuck:
                    if (
                        processed >= SWEEP_MAX_STEPS
                        or _current_time_ms() - sweep_start > SWEEP_MAX_MS
                    ):
                        capped = True
                        break
                    workflow_ast = self._ast_cache.get(wf_id)
                    if workflow_ast is None:
                        workflow_ast = self._load_workflow_ast(wf_id)
                        if workflow_ast:
                            self._ast_cache[wf_id] = workflow_ast
                    if workflow_ast:
                        program_ast = self._program_ast_cache.get(wf_id)
                        self._evaluator.process_single_step(
                            step_id=step.id,
                            workflow_ast=workflow_ast,
                            program_ast=program_ast,
                            dispatcher=self._dispatcher,
                        )
                        processed += 1
                if capped:
                    break

            if capped:
                logger.info(
                    "Stuck-step sweep bounded at %d steps in %dms; remainder next sweep",
                    processed,
                    _current_time_ms() - sweep_start,
                )

        except Exception:
            logger.debug("Stuck-step sweep failed", exc_info=True)

    # =========================================================================
    # Step Log Emission
    # =========================================================================

    # _emit_step_log: inherited from BaseRunner (harmonized keyword-only contract).

    def _reset_errored_ancestors(self, step: Any) -> None:
        """Reset errored ancestor blocks/containers to Continue state.

        Walks up the block_id → container_id chain and resets any step
        in an error state back to its appropriate Continue state so the
        dashboard shows the ancestor chain as running during a retry.
        """
        from .types import ObjectType

        seen: set[str] = set()
        current_id = step.block_id or step.container_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            ancestor = self._persistence.get_step(current_id)
            if ancestor is None:
                break
            if StepState.is_error(ancestor.state):
                if ObjectType.is_block(ancestor.object_type):
                    new_state = StepState.BLOCK_EXECUTION_CONTINUE
                else:
                    new_state = StepState.STATEMENT_BLOCKS_CONTINUE
                ancestor.state = new_state
                ancestor.transition.current_state = new_state
                ancestor.transition.clear_error()
                ancestor.transition.request_transition = False
                ancestor.transition.changed = True
                self._persistence.save_step(ancestor)
                logger.debug(
                    "Reset errored ancestor %s to %s",
                    current_id,
                    new_state,
                )
            current_id = ancestor.block_id or ancestor.container_id

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
                message=(
                    f"Task claimed: {task.name} "
                    f"(server={self._config.server_name}, id={self._server_id[:8]})"
                ),
                facet_name=task.name,
            )

            # If the step is in error state (retry scenario), reset it to
            # EventTransmit immediately so the dashboard shows it as
            # running rather than errored while the handler executes.
            # Also reset errored ancestor blocks so the workflow view
            # doesn't show the parent chain as errored during the retry.
            step = self._persistence.get_step(task.step_id)
            if step and StepState.is_error(step.state):
                step.state = StepState.EVENT_TRANSMIT
                step.transition.current_state = StepState.EVENT_TRANSMIT
                step.transition.clear_error()
                step.transition.request_transition = False
                step.transition.changed = True
                self._persistence.save_step(step)
                self._emit_step_log(
                    step_id=task.step_id,
                    workflow_id=task.workflow_id,
                    message=f"Step reset from error for retry: {task.name}",
                    facet_name=task.name,
                )
                # Walk up the ancestor chain and reset errored blocks
                self._reset_errored_ancestors(step)

            # Env-routed script task: the facet's script IS the implementation
            # (script-environments.md §3/§4) — no registered handler exists.
            if getattr(task, "kind", "") == "script":
                result = self._execute_script_for_task(task)
                if result is None:
                    if self._transition_for_retry(
                        task, dead_letter_state=TaskState.FAILED, set_next_retry_after=True
                    ):
                        error_msg = (
                            f"Script task '{task.name}' unservable "
                            f"after {task.retry_count} attempts"
                        )
                        task.error = {"message": error_msg}
                        try:
                            self._evaluator.fail_step(task.step_id, error_msg)
                        except Exception:
                            logger.debug("Could not fail step %s", task.step_id, exc_info=True)
                    self._safe_save_task(task)
                    return
                task.state = TaskState.COMPLETED
                task.updated = _current_time_ms()
                if not self._safe_save_task(task):
                    return  # lease reclaimed — the new owner produces the result
                self._evaluator.continue_step(task.step_id, result)
                self._resume_workflow(task.workflow_id, task.runner_id)
                # NB: handled-stats tracking is a RunnerService feature
                # (_handled_counts / _update_handled_stats); RegistryRunner has
                # no such state, so calling it here would AttributeError. The
                # lighter RegistryRunner simply doesn't record handled stats.
                return

            if not self._dispatcher.can_dispatch(task.name):
                # This runner has no handler for the facet (e.g. a registry-
                # refresh race after claiming). In a fleet another runner may
                # have it, so release back to pending (with backoff) for retry;
                # only fail for good once retries are exhausted — i.e. no runner
                # in the fleet could service it (§17.1.1 defence in depth).
                if self._transition_for_retry(
                    task, dead_letter_state=TaskState.FAILED, set_next_retry_after=True
                ):
                    error_msg = (
                        f"No handler for event task '{task.name}' "
                        f"(no runner could service it after {task.retry_count} attempts)"
                    )
                    task.error = {"message": error_msg}
                    try:
                        self._evaluator.fail_step(task.step_id, error_msg)
                    except Exception:
                        logger.debug("Could not fail step %s", task.step_id, exc_info=True)
                    self._emit_step_log(
                        step_id=task.step_id,
                        workflow_id=task.workflow_id,
                        message=f"Handler error: {error_msg}",
                        level=StepLogLevel.ERROR,
                        facet_name=task.name,
                    )
                    logger.warning(
                        "No handler for event task '%s' anywhere — failing after %d attempts (step=%s)",
                        task.name,
                        task.retry_count,
                        task.step_id,
                    )
                else:
                    logger.info(
                        "No handler for event task '%s' on this runner — releasing back to "
                        "pending (attempt %d/%d)",
                        task.name,
                        task.retry_count,
                        task.max_retries,
                    )
                self._safe_save_task(task)
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
            ):
                now = _current_time_ms()
                self._persistence.update_task_heartbeat(
                    task.uuid,
                    now,
                    progress_pct=progress_pct,
                    progress_message=progress_message,
                    # Only renew while we still own the task — a reclaimed
                    # zombie must not extend the new owner's lease.
                    expected_server_id=self._server_id,
                )

            payload["_task_heartbeat"] = _task_heartbeat_callback

            # Cooperative cancellation (lessons-learned §16). The token asks the
            # store whether this execution's result would still be accepted —
            # operator terminate, a watchdog that already failed the task, or a
            # reclaim that turned us into a zombie. Cached, so a handler may
            # check it as often as it likes.
            payload["_cancellation_check"] = (
                lambda: self._persistence.task_cancellation_reason(task.uuid, self._server_id)
            )

            payload["_task_uuid"] = task.uuid
            # Execution scope (unique per run) — lets handlers isolate per-run
            # output artifacts. This is the EXECUTION id, distinct from the
            # workflow definition; do not use it to key cacheable intermediates.
            payload["_workflow_id"] = task.workflow_id
            payload["_step_id"] = task.step_id

            # Retry context — lets handlers detect reclaims and skip
            # previously-completed operations (e.g. partial DB imports).
            retry_count = getattr(task, "retry_count", 0) or 0
            payload["_retry_count"] = retry_count
            payload["_is_retry"] = retry_count > 0

            # Look up handler timeout — task-level (from FFL Timeout mixin)
            # takes priority over registration-level default
            timeout_ms = getattr(task, "timeout_ms", 0) or 0
            if timeout_ms <= 0:
                timeout_ms = self._dispatcher.get_timeout_ms(task.name)
            timeout_s = timeout_ms / 1000.0 if timeout_ms > 0 else None
            timeout_label = f" (timeout {timeout_ms}ms)" if timeout_ms > 0 else ""

            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Dispatching handler: {task.name}{timeout_label}",
                facet_name=task.name,
            )
            dispatch_start = _current_time_ms()

            # Dispatch via shared dispatcher (handles module loading + async detection)
            try:
                if timeout_s is not None and timeout_s > 0:
                    # Run dispatch in a bounded thread so a per-handler Timeout
                    # (FFL Timeout mixin) is enforced. On timeout the pool is
                    # shut down with wait=False (see finally) so the runaway
                    # handler does NOT block this poll worker — Future.cancel()
                    # can't interrupt a thread wedged in a C extension, so we
                    # abandon it (it finishes in the background) and free the
                    # slot immediately. Previously the `with` block's implicit
                    # shutdown(wait=True) blocked the worker for the handler's
                    # full real duration, defeating the timeout.
                    timeout_pool = ThreadPoolExecutor(max_workers=1)
                    try:
                        future = timeout_pool.submit(self._dispatcher.dispatch, task.name, payload)
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
                            # Route through the shared retry contract: bump
                            # retry_count + backoff and dead-letter at max_retries.
                            # Previously this reset to PENDING with no retry_count
                            # and no backoff, so a handler that reliably exceeds
                            # its timeout was re-claimed immediately, forever.
                            task.error = {"message": error_msg}
                            if self._transition_for_retry(task, set_next_retry_after=True):
                                try:
                                    self._evaluator.fail_step(task.step_id, error_msg)
                                except Exception:
                                    logger.debug(
                                        "Could not fail step %s", task.step_id, exc_info=True
                                    )
                                self._safe_save_task(task)
                                logger.warning(
                                    "Handler '%s' dead-lettered after %d timeouts (step=%s)",
                                    task.name,
                                    task.retry_count,
                                    task.step_id,
                                )
                            else:
                                self._safe_save_task(task)
                                self._emit_step_log(
                                    step_id=task.step_id,
                                    workflow_id=task.workflow_id,
                                    message=(
                                        "Step released to pending after timeout "
                                        f"(retry {task.retry_count}/{task.max_retries}): {task.name}"
                                    ),
                                    level=StepLogLevel.WARNING,
                                    facet_name=task.name,
                                )
                                logger.warning(
                                    "Handler timed out for '%s' (step=%s, elapsed=%dms, "
                                    "limit=%dms), releasing to pending (retry %d/%d)",
                                    task.name,
                                    task.step_id,
                                    elapsed,
                                    timeout_ms,
                                    task.retry_count,
                                    task.max_retries,
                                )
                            # A timeout is a handler-health failure too.
                            self._circuit_breakers.record_failure(task.name)
                            return
                    finally:
                        # wait=False: never block this poll worker on a runaway
                        # handler. On success the future is already done so this
                        # is a no-op; on timeout it abandons the wedged thread.
                        timeout_pool.shutdown(wait=False)
                else:
                    result = self._dispatcher.dispatch(task.name, payload)
            except HandlerCancelled as exc:
                # A cooperative handler noticed it was cancelled and stopped.
                # This is a clean stop, NOT a failure: do not retry (the work was
                # deliberately abandoned) and do not fail the step (terminate
                # already marked it, or another runner now owns the task).
                # _safe_save_task's ownership gate drops this write outright when
                # the reason was a reclaim, so a zombie cannot cancel the task
                # its successor is legitimately running.
                logger.info(
                    "Handler '%s' stopped on cancellation (step=%s): %s",
                    task.name,
                    task.step_id,
                    exc.reason,
                )
                self._emit_step_log(
                    step_id=task.step_id,
                    workflow_id=task.workflow_id,
                    message=f"Handler cancelled: {exc.reason}",
                    level=StepLogLevel.WARNING,
                    facet_name=task.name,
                )
                task.state = TaskState.CANCELED
                task.error = {"message": f"cancelled: {exc.reason}"}
                task.updated = _current_time_ms()
                self._safe_save_task(task)
                return
            except (ImportError, ModuleNotFoundError) as exc:
                # Handler module can't be loaded on this runner.  Increment
                # retry_count so the task eventually dead-letters instead of
                # looping forever when no runner has the right handler.
                task.retry_count += 1
                if task.max_retries > 0 and task.retry_count >= task.max_retries:
                    task.state = TaskState.DEAD_LETTER
                    task.error = {
                        "message": f"Handler not loadable after {task.retry_count} attempts: {exc}"
                    }
                    log_msg = f"Handler not loadable after {task.retry_count} attempts: {exc}"
                    log_level = StepLogLevel.ERROR
                    logger.warning(
                        "Dead-lettering task %s (%s): handler not loadable after %d attempts",
                        task.uuid,
                        task.name,
                        task.retry_count,
                    )
                else:
                    task.state = TaskState.PENDING
                    task.error = None
                    task.server_id = ""
                    log_msg = f"Cannot load handler (attempt {task.retry_count}/{task.max_retries}): {exc}"
                    log_level = StepLogLevel.WARNING
                    logger.warning(
                        "Cannot load handler for '%s', releasing task %s (attempt %d/%d): %s",
                        task.name,
                        task.uuid,
                        task.retry_count,
                        task.max_retries,
                        exc,
                    )
                task.updated = _current_time_ms()
                self._persistence.save_task(task)
                # Write step log so the error is visible in the dashboard
                if task.step_id:
                    self._emit_step_log(
                        step_id=task.step_id,
                        workflow_id=task.workflow_id,
                        message=log_msg,
                        level=log_level,
                        facet_name=task.name,
                    )
                return
            except (AttributeError, TypeError) as exc:
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

            # Mark the task COMPLETED *first* — this ownership-gated terminal
            # write (save_task_if_owned) is the FENCE. It succeeds only if this
            # runner still owns the task; if the lease was reclaimed under a slow
            # handler, the write is dropped and we must NOT continue_step/resume,
            # or we would advance workflow state concurrently with the reclaimer.
            # (It also lands before resume so the terminal-state check sees THIS
            # task terminal and can complete the runner on this cycle.)
            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            if not self._safe_save_task(task):
                logger.warning(
                    "Not advancing workflow for task %s (step=%s): lease was reclaimed "
                    "or the completion write failed — dropping this runner's result",
                    task.uuid,
                    task.step_id,
                )
                return

            # We own the completion → apply the result and resume.
            # process_single_step handles the continued step and cascades up to
            # parent blocks; falls back to full resume() for complex dispatch.
            self._evaluator.continue_step(task.step_id, result)
            self._resume_workflow(task.workflow_id, task.runner_id)
            self._circuit_breakers.record_success(task.name)

            logger.info(
                "Processed event task %s (name=%s, step=%s)",
                task.uuid,
                task.name,
                task.step_id,
            )

        except PermanentError as exc:
            # The handler has declared this failure deterministic: retrying would
            # re-run the same doomed work five times to reach the same answer.
            # Dead-letter now, but still fail the step so catch blocks and error
            # propagation run exactly as they would at the end of the budget.
            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Permanent handler error (not retried): {exc}",
                level=StepLogLevel.ERROR,
                facet_name=task.name,
            )
            task.error = {"message": str(exc), "permanent": True}
            self._transition_for_retry(task, permanent=True)
            try:
                self._evaluator.fail_step(
                    task.step_id,
                    str(exc),
                    workflow_ast=self._ast_cache.get(task.workflow_id),
                    program_ast=self._program_ast_cache.get(task.workflow_id),
                )
            except Exception:
                logger.debug("Could not fail step %s", task.step_id, exc_info=True)
            self._safe_save_task(task)
            logger.warning(
                "Event task %s dead-lettered as PERMANENT (name=%s, step=%s): %s",
                task.uuid,
                task.name,
                task.step_id,
                exc,
            )
            try:
                self._resume_workflow(task.workflow_id, task.runner_id)
            except Exception:
                logger.debug(
                    "Could not resume workflow %s after permanent error",
                    task.workflow_id,
                    exc_info=True,
                )
            # Deliberately NOT counted against the circuit breaker: a permanent
            # error is a fact about this task's input, not about the handler's
            # health. A handful of unsupported items in a fan-out must not stop
            # this runner from claiming the facet for the good ones.

        except Exception as exc:
            # A handler EXECUTION error. Retry transient failures (connection
            # resets, throttling, a restarted DB) with backoff; only fail the
            # step for good — and run catch/error-propagation — once retries are
            # exhausted. Previously this dead-failed on the FIRST exception, so a
            # single transient blip permanently failed the step. Now it routes
            # through the shared _transition_for_retry contract (retry+backoff
            # then dead-letter at max_retries), matching RunnerService.
            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                message=f"Handler error: {exc}",
                level=StepLogLevel.ERROR,
                facet_name=task.name,
            )
            task.error = {"message": str(exc)}
            if self._transition_for_retry(
                task, set_next_retry_after=True, clear_error_on_retry=False
            ):
                # Dead-lettered: fail the step (with AST so catch blocks / error
                # propagation resolve) and resume so that error handling runs.
                try:
                    workflow_ast = self._ast_cache.get(task.workflow_id)
                    program_ast = self._program_ast_cache.get(task.workflow_id)
                    self._evaluator.fail_step(
                        task.step_id,
                        str(exc),
                        workflow_ast=workflow_ast,
                        program_ast=program_ast,
                    )
                except Exception:
                    logger.debug("Could not fail step %s", task.step_id, exc_info=True)
                self._safe_save_task(task)
                logger.warning(
                    "Event task %s dead-lettered after %d retries (name=%s, step=%s): %s",
                    task.uuid,
                    task.retry_count,
                    task.name,
                    task.step_id,
                    exc,
                )
                try:
                    self._resume_workflow(task.workflow_id, task.runner_id)
                except Exception:
                    logger.debug(
                        "Could not resume workflow %s after error",
                        task.workflow_id,
                        exc_info=True,
                    )
            else:
                # Retry: released back to pending with backoff. The step stays
                # at EVENT_TRANSMIT for re-claim, so do NOT fail_step or resume —
                # the next claim re-dispatches the handler.
                self._safe_save_task(task)
                logger.warning(
                    "Event task %s failed (retry %d/%d) — releasing to pending "
                    "(name=%s, step=%s): %s",
                    task.uuid,
                    task.retry_count,
                    task.max_retries,
                    task.name,
                    task.step_id,
                    exc,
                )
            # A handler-execution failure (retry or dead-letter) counts toward
            # the facet's circuit breaker — enough of them OPEN it so this runner
            # stops re-claiming the facet until the half-open probe.
            self._circuit_breakers.record_failure(task.name)

    # =========================================================================
    # Workflow Resume
    # =========================================================================

    def _resume_workflow(self, workflow_id: str, runner_id: str = "") -> None:
        """Resume a paused workflow after step completion (non-blocking)."""
        self._resume_with_lock(workflow_id, lambda: self._do_resume(workflow_id, runner_id))

    def _do_resume(self, workflow_id: str, runner_id: str) -> None:
        """Execute a single resume cycle for a workflow."""
        workflow_ast = self._ast_cache.get(workflow_id)
        if workflow_ast is None:
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
        result = self._evaluator.resume(
            workflow_id,
            workflow_ast,
            program_ast=program_ast,
            runner_id=runner_id,
            dispatcher=self._dispatcher,
        )

        if result.status == ExecutionStatus.ERROR:
            logger.warning(
                "Workflow resume returned ERROR: workflow_id=%s error=%s",
                workflow_id,
                result.error,
            )

        if result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.ERROR):
            if runner_id:
                self._update_runner_state(runner_id, result)
            else:
                self._update_runner_terminal_state(workflow_id, result.status)

    # _has_non_terminal_tasks: inherited from BaseRunner.

    def _update_runner_state(self, runner_id: str, result: ExecutionResult) -> None:
        """Update runner state based on execution result."""

        try:
            runner = self._persistence.get_runner(runner_id)
            if runner and runner.state == RunnerState.RUNNING:
                now = _current_time_ms()
                if result.status == ExecutionStatus.COMPLETED:
                    if self._has_non_terminal_tasks(runner.workflow_id):
                        logger.warning(
                            "Runner %s: evaluator says COMPLETED but non-terminal "
                            "tasks remain; keeping runner in RUNNING state",
                            runner_id,
                        )
                        return
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

    # _update_runner_terminal_state: inherited from BaseRunner (takes status).

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
    RegistryRunnerConfig from the standard FFL configuration.  It eliminates
    the 7-line bootstrap that every example otherwise duplicates.

    Args:
        service_name: Logical service name (e.g. "noaa-weather").
        server_group: Server group for clustering (default "default").
        max_concurrent: Override for FW_MAX_CONCURRENT env var.
        poll_interval_ms: Override for FW_POLL_INTERVAL_MS env var.
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
