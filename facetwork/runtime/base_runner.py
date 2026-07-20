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
import random
import re
import socket
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .entities import (
    RunnerState,
    ServerState,
    StepLogEntry,
    StepLogLevel,
    StepLogSource,
    TaskState,
)
from .evaluator import ExecutionStatus
from .types import generate_id

if TYPE_CHECKING:
    from concurrent.futures import Future

    from .dispatcher import HandlerDispatcher
    from .evaluator import Evaluator
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
    _evaluator: Evaluator
    _config: BaseRunnerConfig
    _stopping: threading.Event
    # Wakes the poll loop early (task completion, stop()) — see _poll_pause.
    _work_ready: threading.Event
    _active_lock: threading.Lock
    # active work items: (future, task_id, claimed_at_ms) — the tuple shape lets
    # the shared _cleanup_futures reap execution-timed-out handlers.
    _active_futures: list[tuple[Future, str, int]]
    _execution_timeout_ms: int
    # Per-workflow resume locks + pending-requeue set (see _resume_with_lock).
    _resume_locks: dict[str, threading.Lock]
    _resume_locks_lock: threading.Lock
    _resume_pending: set[str]
    _resume_pending_lock: threading.Lock
    # Workflow/program AST caches (see _load_workflow_ast).
    _ast_cache: dict[str, dict]
    _program_ast_cache: dict[str, dict]
    # Inline handler dispatcher used while draining continuations (see
    # _process_continuation). Each subclass sets it to its own dispatch backend.
    _continuation_dispatcher: HandlerDispatcher | None

    # Per-runner cap on the workflow-keyed caches/locks. A long-lived runner
    # processes an unbounded number of workflows, so these must be bounded or
    # they leak (the AST dicts especially). Re-derivable, so oldest-first
    # eviction is safe. Class attribute so tests can lower it.
    _MAX_WORKFLOW_CACHE: int = 512

    # Fractional +/- jitter applied to the poll interval so a fleet of runners
    # doesn't hit Mongo in lockstep (thundering herd). 0 disables it.
    _POLL_JITTER: float = 0.15

    def _provided_environments(self) -> list[str]:
        """Environment manifest hashes this runner can execute scripts in.

        Union of the config's explicit list, FW_PROVIDED_ENVS, and the venvs
        materialized under FW_ENV_ROOT (pre-baked at image build). Cached —
        the set changes only via materialization, which refreshes it.
        """
        cached = getattr(self, "_provided_envs_cache", None)
        if cached is not None:
            return cached
        import os

        from ..environments import discover_provided_environments

        envs = set(getattr(self._config, "provided_environments", None) or [])
        envs.update(
            e.strip()
            for e in os.environ.get("FW_PROVIDED_ENVS", "").split(",")
            if e.strip()
        )
        envs.update(discover_provided_environments())
        self._provided_envs_cache = sorted(envs)
        return self._provided_envs_cache

    # Lazy materialization (script-environments.md §4.2): per-hash negative
    # cache so a failing build isn't retried in a tight loop.
    _env_matz_retry_after: dict[str, int]
    _ENV_MATZ_BACKOFF_MS: int = 600_000  # 10 min between attempts per hash
    _ENV_MATZ_INTERVAL_MS: int = 60_000  # demand-scan cadence

    def _maybe_materialize_environments(self) -> None:
        """Materialize environments with pending demand this runner lacks.

        Scans pending env-tagged script tasks (WITHOUT claiming), and for any
        manifest hash not yet provided here, extracts the frozen manifest from
        the demanding workflow's compiled snapshot and builds the venv — then
        re-registers so the environment is advertised and the very next poll
        can claim. The bake path (§4.1) remains primary; this is the fallback
        that makes environments published AFTER the image bake work without
        waiting for the next rollout. Disable with ``FW_ENV_LAZY=off``.
        Failures are negative-cached per hash (10 min) and never disturb the
        poll loop. At most one materialization per scan — installs can take
        a while and the loop must keep claiming.
        """
        import os

        if os.environ.get("FW_ENV_LAZY", "on").strip().lower() in ("0", "false", "no", "off"):
            return
        now = _current_time_ms()
        last = getattr(self, "_env_matz_last_scan", 0)
        if now - last < self._ENV_MATZ_INTERVAL_MS:
            return
        self._env_matz_last_scan = now
        try:
            demand = self._persistence.get_pending_script_environment_demand()
        except Exception:
            logger.debug("Environment demand scan failed", exc_info=True)
            return
        if not demand:
            return
        provided = set(self._provided_environments())
        retry_after = getattr(self, "_env_matz_retry_after", None)
        if retry_after is None:
            retry_after = self._env_matz_retry_after = {}
        for env_hash, workflow_id in demand:
            if env_hash in provided or retry_after.get(env_hash, 0) > now:
                continue
            manifest = self._manifest_for_hash(env_hash, workflow_id)
            if manifest is None or (manifest.get("language") or "") != "python" or not manifest.get(
                "resolved", True
            ):
                retry_after[env_hash] = now + self._ENV_MATZ_BACKOFF_MS
                continue
            from ..environments import materialize_environment

            try:
                logger.info(
                    "Lazy-materializing environment %s (%d pin(s)) — demanded by "
                    "pending script tasks",
                    env_hash,
                    len(manifest.get("pins") or []),
                )
                materialize_environment(manifest, env_hash)
            except Exception as exc:  # noqa: BLE001 - negative-cache and move on
                logger.warning(
                    "Environment %s materialization failed (retry in %ds): %s",
                    env_hash,
                    self._ENV_MATZ_BACKOFF_MS // 1000,
                    exc,
                )
                retry_after[env_hash] = now + self._ENV_MATZ_BACKOFF_MS
                continue
            self._provided_envs_cache = None  # re-discover, now includes env_hash
            try:
                self._register_server()  # advertise immediately
            except Exception:
                logger.debug("Re-register after materialization failed", exc_info=True)
            self._work_ready.set()  # claim the waiting tasks now
            logger.info("Environment %s materialized and advertised", env_hash)
            return  # one per scan

    def _manifest_for_hash(self, env_hash: str, workflow_id: str) -> dict | None:
        """Frozen manifest for ``env_hash`` from a demanding workflow's snapshot."""
        try:
            self._load_workflow_ast(workflow_id)
            program = self._program_ast_cache.get(workflow_id) or {}
            for decl in program.get("declarations") or []:
                if not isinstance(decl, dict) or decl.get("type") != "Namespace":
                    continue
                for inner in decl.get("declarations") or []:
                    if (
                        isinstance(inner, dict)
                        and inner.get("type") == "EnvironmentDecl"
                        and inner.get("manifest_hash") == env_hash
                    ):
                        return inner.get("manifest")
        except Exception:
            logger.debug("Manifest lookup failed for %s", env_hash, exc_info=True)
        return None

    def _execute_script_for_task(self, task) -> dict | None:
        """Execute an env-routed script task's facet script; return its result.

        The claiming runner is the placement authority's choice — it provides
        the task's environment. Loads the workflow's program AST (runner
        snapshot), finds the facet's script block, and runs it under the
        materialized environment's interpreter. Returns the script's result
        dict, or None when the script/interpreter cannot be resolved here
        (caller releases the task on its no-handler path).
        """
        from ..environments import interpreter_for_hash

        workflow_ast = self._load_workflow_ast(task.workflow_id)
        program_ast = self._program_ast_cache.get(task.workflow_id)
        if not program_ast:
            logger.warning("Script task %s: no program AST for workflow %s",
                           task.uuid, task.workflow_id)
            return None
        from .evaluator import ExecutionContext
        from .persistence import IterationChanges
        from .telemetry import Telemetry

        ctx = ExecutionContext(
            persistence=self._persistence,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=task.workflow_id,
            workflow_ast=workflow_ast,
            program_ast=program_ast,
            runner_id=task.runner_id,
        )
        facet_def = ctx.get_facet_definition(task.name) or {}
        script_def = facet_def.get("pre_script")
        if script_def is None:
            body = facet_def.get("body")
            if isinstance(body, dict) and body.get("type") == "ScriptBlock":
                script_def = body
        if script_def is None:
            logger.warning("Script task %s: facet %s has no script block",
                           task.uuid, task.name)
            return None
        interpreter = interpreter_for_hash(task.environment_hash)
        if interpreter is None:
            logger.warning(
                "Script task %s: environment %s not materialized on this host "
                "despite being advertised — releasing",
                task.uuid, task.environment_hash,
            )
            self._provided_envs_cache = None  # re-discover on next poll
            return None
        from .script_executor import ScriptExecutor

        # The environment's declared packages are its import surface — the
        # declaration is the review gate, so its manifest joins the script
        # sandbox's import allowlist (both spec-name and module-name forms;
        # os/subprocess etc. stay blocked).
        extra_modules: list[str] = []
        env_ref = facet_def.get("environment")
        if env_ref:
            from ..environments import environment_for_decl

            ns = task.name.rsplit(".", 1)[0] if "." in task.name else ""
            env_decl = environment_for_decl(program_ast, env_ref, ns) or {}
            pins = (env_decl.get("manifest") or {}).get("pins") or []
            for pin in pins:
                pkg = re.split(r"[=<>\[!~]", pin, 1)[0].strip().lower()
                if pkg:
                    extra_modules.append(pkg)
                    extra_modules.append(pkg.replace("-", "_"))

        timeout_s = (task.timeout_ms or 0) / 1000.0 or 30.0
        result = ScriptExecutor(timeout=timeout_s).execute(
            script_def.get("code", ""),
            dict(task.data or {}),
            script_def.get("language", "python"),
            python_executable=interpreter,
            extra_import_modules=sorted(set(extra_modules)),
        )
        if not result.success:
            raise RuntimeError(result.error or f"Script failed for {task.name}")
        return result.result

    def _poll_pause(self, interval_s: float, dispatched: int) -> None:
        """Adaptive inter-cycle pause.

        A cycle that dispatched work re-polls immediately (its handlers'
        continuations may create follow-on tasks, and freed slots should
        refill without waiting). An idle cycle waits the jittered interval
        but is woken early by ``_work_ready`` — set on task completion and
        on ``stop()`` — so cascade hops cost handler time, not poll latency.
        """
        if dispatched > 0 or self._stopping.is_set():
            return
        self._work_ready.wait(self._poll_wait_seconds(interval_s))
        self._work_ready.clear()

    def _on_future_done(self, _future: object = None) -> None:
        """Future done-callback: wake the poll loop — a slot freed and the
        finished handler's continuations may have created follow-on work."""
        self._work_ready.set()

    def _poll_wait_seconds(self, interval_s: float) -> float:
        """The poll interval with +/- ``_POLL_JITTER`` applied.

        Decorrelates the poll phase across runners (and drifts it over time,
        since a fresh value is drawn each cycle) without changing the mean poll
        rate — so N runners started in sync stop hammering Mongo on the same
        tick. Not used for correctness, so plain ``random`` is fine.
        """
        jitter = self._POLL_JITTER
        if jitter <= 0:
            return interval_s
        return interval_s * random.uniform(1.0 - jitter, 1.0 + jitter)

    @property
    def server_id(self) -> str:
        """Get the server's unique ID."""
        return self._server_id

    @property
    def is_running(self) -> bool:
        """Check if the runner is currently running."""
        return self._running

    def _register_server(self) -> None:
        """Write this runner's full ServerDefinition to the store.

        Provided by each subclass (handler sets and config shapes differ).
        The heartbeat loop calls it to self-heal a reaped/pruned record, so
        implementations must be safe to call repeatedly on a live runner.
        """
        raise NotImplementedError

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
        """Periodically update the server's ping_time, self-healing the record.

        A transiently-quiet runner (host sleep, emulation stall, Mongo blip)
        can be marked ``shutdown`` by another runner's reaper and then pruned
        entirely; a bare ping update would silently no-op forever after that,
        leaving a working-but-invisible "zombie" runner. When the heartbeat
        finds its record dead or gone it re-registers, restoring the runner to
        the live roster. Guarded on ``_stopping`` so a heartbeat racing a
        graceful shutdown can't resurrect the record ``_deregister_server``
        just wrote.
        """
        interval_s = self._config.heartbeat_interval_ms / 1000.0
        while not self._stopping.wait(interval_s):
            try:
                alive = self._persistence.heartbeat_server(self._server_id, _current_time_ms())
                if not alive and not self._stopping.is_set():
                    logger.warning(
                        "Server record for %s (%s) missing or marked shutdown — "
                        "re-registering (reaper false positive / pruned record)",
                        self._config.server_name,
                        self._server_id[:8],
                    )
                    self._register_server()
            except Exception:
                logger.exception("Heartbeat failed")

    def _active_count(self) -> int:
        """Get the number of active work items."""
        with self._active_lock:
            return len(self._active_futures)

    def _task_label(self, task_id: str) -> str:
        """Build a human-readable label for a task including qualified step name.

        Returns a string like ``"Kentucky.imp.imported (osm.ops.PostGisImport)"``
        or falls back to ``"<task_id[:12]>"`` if resolution fails.
        """
        try:
            task = self._persistence.get_task(task_id)
            if not task:
                return task_id[:12]
            step = self._persistence.get_step(task.step_id) if task.step_id else None
            if not step:
                return task.name or task_id[:12]
            # Build qualified name by walking ancestors.
            segments: list[str] = []
            seen: set[str] = set()
            current_id = step.block_id
            while current_id and current_id not in seen:
                seen.add(current_id)
                ancestor = self._persistence.get_step(current_id)
                if ancestor is None:
                    break
                fv = getattr(ancestor, "foreach_value", None)
                sn = getattr(ancestor, "statement_name", None)
                if fv:
                    segments.append(str(fv))
                elif sn:
                    segments.append(str(sn))
                current_id = getattr(ancestor, "container_id", None) or getattr(
                    ancestor, "block_id", None
                )
            segments.reverse()
            if step.statement_name:
                segments.append(step.statement_name)
            qualified = ".".join(segments) if segments else ""
            facet = step.facet_name or task.name or ""
            if qualified and facet:
                return f"{qualified} ({facet})"
            return qualified or facet or task_id[:12]
        except Exception:
            return task_id[:12]

    def _emit_step_log(
        self,
        step_id: str,
        workflow_id: str,
        message: str,
        *,
        level: str = StepLogLevel.INFO,
        facet_name: str = "",
        source: str = StepLogSource.FRAMEWORK,
        details: dict | None = None,
    ) -> None:
        """Persist a ``StepLogEntry`` best-effort.

        Centralizes the entry-construction + ``save_step_log`` + swallow-and-debug
        pattern that every claim/timeout/reaper/handler site repeats verbatim.
        Logging failures must not break runner work, so persistence errors are
        logged at debug and otherwise dropped. The keyword-only tail (``level``,
        ``facet_name``, ``source``, ``details``) is the single harmonized
        contract shared by every runner subclass.
        """
        entry = StepLogEntry(
            uuid=generate_id(),
            step_id=step_id,
            workflow_id=workflow_id,
            runner_id=self._server_id,
            facet_name=facet_name,
            source=source,
            level=level,
            message=message,
            time=_current_time_ms(),
            details=details or {},
        )
        try:
            self._persistence.save_step_log(entry)
        except Exception:
            logger.debug("Could not save step log for step %s", step_id, exc_info=True)

    def _cleanup_futures(self) -> None:
        """Remove completed futures and reap execution-timed-out ones.

        If a future has been running longer than ``_execution_timeout_ms`` (and
        the handler isn't heartbeating or inside a declared stage budget), the
        task is released for retry and the future is **always dropped** from the
        active list — ``Future.cancel()`` cannot interrupt a thread blocked in a
        C extension (e.g. psycopg2), so keeping it would pin the runner at
        capacity forever. This shared version gives RegistryRunner the same
        execution-timeout safety net RunnerService always had (it previously
        only dropped ``done()`` futures — a hung handler held a slot until the
        process died).
        """
        now = _current_time_ms()
        kept: list = []
        with self._active_lock:
            for future, task_id, claimed_at in self._active_futures:
                if future.done():
                    continue  # completed — drop from list
                if self._execution_timeout_ms > 0:
                    # Prefer the handler heartbeat over claimed_at so a
                    # long-running task making progress isn't reaped.
                    last_activity = claimed_at
                    try:
                        task = self._persistence.get_task(task_id)
                        if task and task.task_heartbeat > 0:
                            last_activity = max(claimed_at, task.task_heartbeat)
                    except Exception:
                        logger.debug(
                            "Could not read heartbeat for task %s, skipping timeout check",
                            task_id,
                            exc_info=True,
                        )
                        kept.append((future, task_id, claimed_at))
                        continue
                    elapsed = now - last_activity
                    # A declared stage budget overrides the global timeout.
                    stage_budget = 0
                    stage_name = ""
                    if task is not None:
                        stage_budget = getattr(task, "stage_budget_expires", 0) or 0
                        stage_name = getattr(task, "stage_name", "") or ""
                    stage_active = stage_budget > 0 and now < stage_budget
                    if elapsed > self._execution_timeout_ms and not stage_active:
                        future.cancel()  # best-effort
                        stage_note = f" (stage={stage_name})" if stage_name else ""
                        logger.warning(
                            "Task %s timed out after %ds, releasing capacity — %s%s",
                            task_id,
                            elapsed // 1000,
                            self._task_label(task_id),
                            stage_note,
                        )
                        self._release_timed_out_task(task_id)
                        continue  # always drop — no zombie futures
                kept.append((future, task_id, claimed_at))
            self._active_futures = kept

    def _release_timed_out_task(self, task_id: str) -> None:
        """Reset a timed-out task to pending, or dead-letter if retries exhausted."""
        try:
            task = self._persistence.get_task(task_id)
            if not task or task.state != TaskState.RUNNING:
                return
            if self._transition_for_retry(task):
                dead_letter_msg = (
                    f"Timed out {task.retry_count} times (limit {task.max_retries}), dead-lettered"
                )
                task.error = {"message": dead_letter_msg}
                try:
                    self._evaluator.fail_step(task.step_id, dead_letter_msg)
                except Exception:
                    logger.debug("Could not fail step %s", task.step_id, exc_info=True)
                logger.warning(
                    "Task %s dead-lettered after %d timeout retries — %s",
                    task_id,
                    task.retry_count,
                    self._task_label(task_id),
                )
                log_msg = (
                    f"Task dead-lettered: {task.name} — timed out {task.retry_count} times "
                    f"(limit {task.max_retries})"
                )
                log_level = StepLogLevel.ERROR
            else:
                log_msg = (
                    f"Task timed out: {task.name} — execution timeout "
                    f"({self._execution_timeout_ms / 1000:.0f}s) exceeded, resetting to pending "
                    f"(retry {task.retry_count}/{task.max_retries})"
                )
                log_level = StepLogLevel.WARNING
            self._safe_save_task(task)
            self._emit_step_log(
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                facet_name=task.name,
                level=log_level,
                message=log_msg,
            )
        except Exception:
            logger.debug("Could not release timed-out task %s", task_id, exc_info=True)

    # =========================================================================
    # Retry / dead-letter transitions (shared resilience contract)
    # =========================================================================

    def _safe_save_task(self, task: Any, retries: int = 3) -> bool:
        """Save task state with retries to survive transient DB failures.

        Terminal writes (``completed``/``failed``/``canceled``/``dead_letter``)
        go through the ownership-gated path: only this runner — the one whose
        server_id is currently on the doc — may write the result. A handler
        whose lease was reclaimed under it gets silently dropped here rather
        than overwriting the new claimer's state. Non-terminal writes
        (heartbeat fields, retry resets that explicitly clear server_id) keep
        going through the unconditional path because they're orchestration
        and not the lease-reclaim race.

        Returns ``True`` when the write was durably applied (or accepted by the
        ownership gate), ``False`` when a gated terminal write was DROPPED
        because the lease had been reclaimed, or when the write failed after all
        retries. Callers finalizing a handler result use this to fence workflow
        state advancement: a reclaimed zombie must not ``continue_step``/resume.
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
                    return accepted
                self._persistence.save_task(task)
                return True
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
        return False

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
        # Opportunistically bound the workflow-keyed caches/locks (cheap no-op
        # while under the cap) so a long-lived runner doesn't leak them.
        self._prune_workflow_caches()

    def _prune_workflow_caches(self) -> None:
        """Bound the per-workflow caches so a long-lived runner doesn't leak
        memory as it processes an unbounded number of workflows.

        The AST caches are re-derivable via ``_load_workflow_ast``, so
        oldest-first (insertion order) eviction is safe. Resume locks are only
        evicted when FREE — a held lock is an in-flight resume and must survive
        (evicting it would let a concurrent resume of the same workflow create a
        second lock and run unserialized).
        """
        cap = self._MAX_WORKFLOW_CACHE
        for cache in (self._ast_cache, self._program_ast_cache):
            while len(cache) > cap:
                try:
                    cache.pop(next(iter(cache)), None)
                except StopIteration:
                    break
        with self._resume_locks_lock:
            if len(self._resume_locks) > cap:
                for wid in list(self._resume_locks):
                    if len(self._resume_locks) <= cap:
                        break
                    lock = self._resume_locks[wid]
                    if lock.acquire(blocking=False):  # free → safe to drop
                        lock.release()
                        del self._resume_locks[wid]
                        with self._resume_pending_lock:
                            self._resume_pending.discard(wid)

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
                RunnerState.COMPLETED if status == ExecutionStatus.COMPLETED else RunnerState.FAILED
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

    # =========================================================================
    # Workflow AST loading
    # =========================================================================

    def _load_workflow_ast(self, workflow_id: str) -> dict | None:
        """Load a workflow's AST from persistence.

        Prefers the runner-SNAPSHOTTED AST (self-contained, immune to later flow
        edits/re-seeds) and falls back to the flow lookup — then to recompiling
        legacy flows that have no stored compiled_ast. Caches the program AST for
        facet-definition lookups during resume.
        """
        try:
            # Prefer runner-snapshotted AST.
            if hasattr(self._persistence, "get_runners_by_workflow"):
                for r in self._persistence.get_runners_by_workflow(workflow_id):
                    if r.compiled_ast and r.workflow_ast:
                        self._program_ast_cache[workflow_id] = r.compiled_ast
                        return r.workflow_ast

            # Fall back to flow lookup.
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
            if program_dict is None:
                return None

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
    # Continuation draining (shared _fw_continue backlog)
    # =========================================================================

    def _process_continuation(self, task: Any) -> None:
        """Process a continuation task by running process_single_step.

        Continuation tasks notify a step that one of its children has
        progressed.  The step is re-evaluated, which may complete a
        foreach block, advance a dependency graph, or generate further
        continuation events. Every runner that ``polls_shared_continuations()``
        drains this backlog (via ``_continuation_dispatcher`` for inline
        dispatch) — otherwise cross-server cascades stall on the sweep and
        pending ``_fw_continue`` rows accumulate.
        """
        step_id = task.data.get("step_id") if task.data else task.step_id
        workflow_id = task.workflow_id

        # Runtime-version gate: if the target step was stamped with a runtime
        # version this build can't safely process, RELEASE the continuation
        # (back to pending, with a short backoff) so a compatible runner claims
        # it — rather than consuming it to a no-op and stranding the step. No-op
        # today (every step is STEP_RUNTIME_VERSION). See
        # docs/architecture/ffl-runner-orchestration-tier.md §3.3.
        if step_id:
            from .types import STEP_RUNTIME_VERSION, is_runtime_compatible

            _gate_step = self._persistence.get_step(step_id)
            _runtime_ver = getattr(getattr(_gate_step, "version", None), "runtime_version", None)
            if _gate_step is not None and not is_runtime_compatible(_runtime_ver):
                task.state = TaskState.PENDING
                task.server_id = ""
                task.next_retry_after = _current_time_ms() + 5000
                task.updated = _current_time_ms()
                self._persistence.save_task(task)
                logger.warning(
                    "Continuation for step %s has incompatible runtime_version "
                    "%r (this build: %s) — released for a compatible runner",
                    step_id,
                    _runtime_ver,
                    STEP_RUNTIME_VERSION,
                )
                return

        # Claim-time coalescing: this continuation re-evaluates the step against
        # the current state of all its children, which satisfies every other
        # continuation already queued for it. Drop those redundant siblings so
        # they aren't each claimed and processed to a no-op (the fan-out storm).
        # Continuations enqueued AFTER this point reflect genuinely newer child
        # events and are left untouched, so nothing is lost.
        if step_id:
            try:
                coalesced = self._persistence.delete_pending_continuations_for_step(
                    step_id, except_task_id=task.uuid
                )
                if coalesced:
                    logger.debug(
                        "Coalesced %d redundant continuation(s) for step %s",
                        coalesced,
                        step_id,
                    )
            except Exception:
                logger.debug("continuation sibling-delete failed", exc_info=True)

        try:
            workflow_ast = self._ast_cache.get(workflow_id)
            if workflow_ast is None:
                workflow_ast = self._load_workflow_ast(workflow_id)
                if workflow_ast:
                    self._ast_cache[workflow_id] = workflow_ast

            if workflow_ast is None:
                logger.warning(
                    "No AST for continuation task (workflow=%s step=%s), skipping",
                    workflow_id,
                    step_id,
                )
                task.state = TaskState.FAILED
                task.error = {"message": "No workflow AST available"}
                task.updated = _current_time_ms()
                self._persistence.save_task(task)
                return

            program_ast = self._program_ast_cache.get(workflow_id)
            result = self._evaluator.process_single_step(
                step_id=step_id,
                workflow_ast=workflow_ast,
                program_ast=program_ast,
                runner_id=task.runner_id,
                dispatcher=self._continuation_dispatcher,
            )

            task.state = TaskState.COMPLETED
            task.updated = _current_time_ms()
            self._persistence.save_task(task)

            if result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.ERROR):
                self._update_runner_terminal_state(workflow_id, result.status)

        except Exception as exc:
            logger.warning(
                "Continuation task failed: step=%s workflow=%s error=%s",
                step_id,
                workflow_id,
                exc,
            )
            task.state = TaskState.FAILED
            task.error = {"message": str(exc)}
            task.updated = _current_time_ms()
            self._persistence.save_task(task)
