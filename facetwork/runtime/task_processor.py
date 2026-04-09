"""Shared task processing logic for all runner types.

Extracts the common payload-build → dispatch → handle-result → retry/dead-letter
cycle that RunnerService, RegistryRunner, and AgentPoller all implement.

Runner implementations override ``_dispatch_handler()`` to plug in their
specific handler discovery mechanism (ToolRegistry, RegistryDispatcher,
or registered callbacks).

Usage::

    class MyRunner(TaskProcessor):
        def _dispatch_handler(self, task_name, payload):
            return my_registry.dispatch(task_name, payload)

    processor = MyRunner(persistence, evaluator)
    processor.process_event_task(task)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from .entities import StepLogEntry, StepLogLevel, StepLogSource, TaskState
from .persistence import PersistenceAPI
from .states import StepState
from .types import generate_id

logger = logging.getLogger(__name__)


def _current_time_ms() -> int:
    """Return the current time in milliseconds since epoch."""
    import time

    return int(time.time() * 1000)


class TaskProcessor:
    """Shared task processing logic for all runner types.

    Handles:
    - Building the handler payload with injected callbacks
    - Step-level logging
    - Heartbeat callback construction
    - Error handling with retry/dead-letter
    - Step error reset on retry
    - Ancestor block reset on retry

    Subclasses must implement ``_dispatch_handler()`` to provide
    their handler discovery mechanism.
    """

    def __init__(self, persistence: PersistenceAPI, evaluator: Any) -> None:
        self._persistence = persistence
        self._evaluator = evaluator

    def _dispatch_handler(self, task_name: str, payload: dict) -> dict | None:
        """Dispatch to the appropriate handler. Override in subclasses.

        Args:
            task_name: Qualified facet name or task name.
            payload: Handler payload with parameters and injected callbacks.

        Returns:
            Result dict from the handler, or None if no handler found.
        """
        raise NotImplementedError

    # =========================================================================
    # Payload Construction
    # =========================================================================

    def build_payload(self, task: Any) -> dict:
        """Build the handler payload with all injected callbacks.

        Adds ``_step_log``, ``_task_heartbeat``, ``_task_uuid``,
        ``_retry_count``, ``_is_retry`` to the task data.
        """
        payload = dict(task.data or {})

        # Step logging callback
        def _step_log_callback(
            message: str,
            level: str = StepLogLevel.INFO,
            details: dict | None = None,
        ) -> None:
            entry = StepLogEntry(
                uuid=generate_id(),
                step_id=task.step_id,
                workflow_id=task.workflow_id,
                runner_id=task.runner_id,
                facet_name=task.name,
                source=StepLogSource.HANDLER,
                level=level,
                message=message,
                details=details or {},
                time=_current_time_ms(),
            )
            try:
                self._persistence.save_step_log(entry)
            except Exception:
                logger.debug("Failed to save step log for %s", task.step_id)

        payload["_step_log"] = _step_log_callback

        # Heartbeat callback
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

        return payload

    # =========================================================================
    # Step Log Emission
    # =========================================================================

    def emit_step_log(
        self,
        step_id: str,
        workflow_id: str,
        message: str,
        level: str = StepLogLevel.INFO,
        facet_name: str = "",
        runner_id: str = "",
        details: dict | None = None,
    ) -> None:
        """Emit a step log entry."""
        entry = StepLogEntry(
            uuid=generate_id(),
            step_id=step_id,
            workflow_id=workflow_id,
            runner_id=runner_id,
            facet_name=facet_name,
            source=StepLogSource.FRAMEWORK,
            level=level,
            message=message,
            details=details or {},
            time=_current_time_ms(),
        )
        try:
            self._persistence.save_step_log(entry)
        except Exception:
            logger.debug("Failed to emit step log for %s", step_id)

    # =========================================================================
    # Error Handling with Retry/Dead-Letter
    # =========================================================================

    def handle_task_error(
        self,
        task: Any,
        exc: Exception,
        task_label: str = "",
    ) -> None:
        """Handle a task execution error with retry/dead-letter logic.

        Increments ``retry_count``, checks against ``max_retries``,
        and either resets to pending (with backoff) or dead-letters.
        """
        now = _current_time_ms()
        task.retry_count = (getattr(task, "retry_count", 0) or 0) + 1
        task.updated = now
        label = task_label or task.name

        if task.max_retries > 0 and task.retry_count >= task.max_retries:
            # Dead-letter
            task.state = TaskState.DEAD_LETTER
            task.error = {"message": str(exc)}
            try:
                self._evaluator.fail_step(task.step_id, str(exc))
            except Exception:
                logger.debug("Could not fail step %s", task.step_id, exc_info=True)
            logger.warning(
                "Task %s dead-lettered after %d retries — %s: %s",
                task.uuid,
                task.retry_count,
                label,
                exc,
            )
        else:
            # Retry with exponential backoff
            task.state = TaskState.PENDING
            task.server_id = ""
            task.error = None
            delay_ms = min(10000 * (2 ** (task.retry_count - 1)), 300000)  # 10s, 20s, 40s... max 5min
            task.next_retry_after = now + delay_ms
            logger.warning(
                "Task %s failed (retry %d/%d, next in %.0fs) — %s: %s",
                task.uuid,
                task.retry_count,
                task.max_retries,
                delay_ms / 1000,
                label,
                exc,
            )

        self._safe_save_task(task)

    def handle_timeout(self, task: Any, task_label: str = "") -> None:
        """Handle a task timeout with retry/dead-letter logic."""
        now = _current_time_ms()
        task.retry_count = (getattr(task, "retry_count", 0) or 0) + 1
        task.updated = now
        label = task_label or task.name

        if task.max_retries > 0 and task.retry_count >= task.max_retries:
            task.state = TaskState.DEAD_LETTER
            task.error = (
                f"Timed out {task.retry_count} times "
                f"(limit {task.max_retries}), dead-lettered"
            )
            try:
                self._evaluator.fail_step(task.step_id, task.error)
            except Exception:
                logger.debug("Could not fail step %s", task.step_id, exc_info=True)
            logger.warning(
                "Task %s dead-lettered after %d timeout retries — %s",
                task.uuid,
                task.retry_count,
                label,
            )
        else:
            task.state = TaskState.PENDING
            task.server_id = ""
            task.error = None

        self._safe_save_task(task)

    # =========================================================================
    # Step Reset on Retry
    # =========================================================================

    def reset_step_for_retry(self, step_id: str) -> None:
        """Reset an errored step back to EventTransmit for retry."""
        step = self._persistence.get_step(step_id)
        if step and StepState.is_error(step.state):
            step.state = StepState.EVENT_TRANSMIT
            if hasattr(step, "transition") and step.transition:
                step.transition.current_state = StepState.EVENT_TRANSMIT
                step.transition.clear_error()
                step.transition.request_transition = False
                step.transition.changed = True
            self._persistence.save_step(step)

    # =========================================================================
    # Utilities
    # =========================================================================

    def _safe_save_task(self, task: Any, retries: int = 3) -> None:
        """Save task state with retries to survive transient DB failures."""
        for attempt in range(retries):
            try:
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
                else:
                    logger.exception("save_task failed for %s after %d attempts", task.uuid, retries)
