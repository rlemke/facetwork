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

"""Workflow repair mixin for MongoStore."""

from collections.abc import Callable

from ..step import StepDefinition
from ._internals import _MixinBase
from .base import _current_time_ms, _reset_task_to_pending


def _generate_id() -> str:
    import uuid as _uuid

    return str(_uuid.uuid4())


class RepairMixin(_MixinBase):
    """Workflow repair operations."""

    def _repair_log(
        self,
        step_id: str,
        workflow_id: str,
        message: str,
        level: str = "warning",
        facet_name: str = "",
    ) -> None:
        """Emit a step log entry for a repair action."""
        from ..entities.logging import StepLogEntry, StepLogSource

        entry = StepLogEntry(
            uuid=_generate_id(),
            step_id=step_id,
            workflow_id=workflow_id,
            runner_id="",
            facet_name=facet_name,
            source=StepLogSource.FRAMEWORK,
            level=level,
            message=message,
            time=_current_time_ms(),
        )
        try:
            self.save_step_log(entry)
        except Exception:
            pass  # best-effort — don't let log failures break repair

    def _set_step_state(self, step: StepDefinition, target_state: str) -> None:
        """Set a step's state, clear any prior transition error, and save.

        Centralizes the "reset an errored step so the runtime will re-execute
        it" pattern used by every repair branch.
        """
        step.state = target_state
        if hasattr(step, "transition") and step.transition:
            step.transition.current_state = target_state
            step.transition.clear_error()
            step.transition.request_transition = False
            step.transition.changed = True
        # ``error`` may live on the step itself for some paths
        if hasattr(step, "error"):
            step.error = None
        self.save_step(step)

    def _walk_errored_ancestors(
        self,
        start_id: str | None,
        advance: Callable[[StepDefinition], str | None],
        target_state: str,
        seen: set[str],
        step_by_id: dict[str, StepDefinition],
        ancestors_reset: list[str],
    ) -> None:
        """Walk an ancestor chain, resetting each errored step to ``target_state``.

        ``advance`` returns the next ID to visit from a given step — callers
        pass ``lambda a: a.block_id`` for block chains, or a wider fallback
        for container chains. The ``seen`` set is shared across multiple
        walks rooted at the same starting step to avoid revisiting nodes.
        """
        current_id = start_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            from ..states import StepState

            ancestor = step_by_id.get(current_id) or self.get_step(current_id)
            if ancestor is None:
                break
            if ancestor.state == StepState.STATEMENT_ERROR:
                self._set_step_state(ancestor, target_state)
                ancestors_reset.append(ancestor.id)
            current_id = advance(ancestor)

    def _reset_failed_step_and_ancestors(
        self,
        step: StepDefinition,
        step_by_id: dict[str, StepDefinition],
        ancestors_reset: list[str],
    ) -> None:
        """Reset an errored step plus its ancestor chain so the runtime
        will retry it on the next iteration.

        For regular steps this resets the step itself to
        ``EVENT_TRANSMIT`` and walks the block + container chains with
        the standard continue states.  For **aliased mixin sub-steps**
        (``container_id`` set, ``block_id`` unset, ``statement_name``
        populated) it resets the step to ``CREATED`` (so the full
        STEP_TRANSITIONS lifecycle re-walks, re-running the mixin
        facet's body), clears its returns, and resets the immediate
        parent container at ``MIXIN_BLOCKS_CONTINUE`` instead of
        ``STATEMENT_BLOCKS_CONTINUE`` so the parent re-waits on the
        mixin instead of skipping the mixin phase.  Higher ancestors
        revert to ``STATEMENT_BLOCKS_CONTINUE`` as usual.

        Centralises the mixin-aware reset logic so every repair
        branch agrees on it; mirrors the dashboard's
        ``_reset_errored_ancestors`` after Scope C.
        """
        from ..mixin_alias import is_mixin_sub_step
        from ..states import StepState

        seen: set[str] = set()
        if is_mixin_sub_step(step):
            self._set_step_state(step, StepState.CREATED)
            step.attributes.returns = {}
            self.save_step(step)
            # Reset the immediate parent at MIXIN_BLOCKS_CONTINUE if it
            # errored at its mixin phase.
            parent_id = step.container_id
            higher_start: str | None = None
            if parent_id and parent_id not in seen:
                seen.add(parent_id)
                parent = step_by_id.get(parent_id) or self.get_step(parent_id)
                if parent is not None:
                    if parent.state == StepState.STATEMENT_ERROR:
                        self._set_step_state(parent, StepState.MIXIN_BLOCKS_CONTINUE)
                        ancestors_reset.append(parent_id)
                    higher_start = parent.block_id or parent.container_id
            self._walk_errored_ancestors(
                step.block_id,
                lambda a: a.block_id,
                StepState.BLOCK_EXECUTION_CONTINUE,
                seen,
                step_by_id,
                ancestors_reset,
            )
            self._walk_errored_ancestors(
                higher_start,
                lambda a: a.block_id or a.container_id,
                StepState.STATEMENT_BLOCKS_CONTINUE,
                seen,
                step_by_id,
                ancestors_reset,
            )
        else:
            # Only true event-facet statement steps (VariableAssignment /
            # YieldAssignment) dispatch an event via EVENT_TRANSMIT. A
            # Workflow or Block step lands in STATEMENT_ERROR only because a
            # descendant errored; resetting *it* to EVENT_TRANSMIT makes the
            # runtime spawn a bogus event task named after the workflow —
            # one no runner can service (the workflow facet has no handler).
            # Such a container step must instead re-drive its block
            # continuation, mirroring the ancestor-walk states below
            # (blocks -> BLOCK_EXECUTION_CONTINUE, workflow / other
            # containers -> STATEMENT_BLOCKS_CONTINUE).
            if step.is_statement:
                self._set_step_state(step, StepState.EVENT_TRANSMIT)
            elif step.is_block:
                self._set_step_state(step, StepState.BLOCK_EXECUTION_CONTINUE)
            else:
                self._set_step_state(step, StepState.STATEMENT_BLOCKS_CONTINUE)
            self._walk_errored_ancestors(
                step.block_id,
                lambda a: a.block_id,
                StepState.BLOCK_EXECUTION_CONTINUE,
                seen,
                step_by_id,
                ancestors_reset,
            )
            self._walk_errored_ancestors(
                step.container_id,
                lambda a: a.block_id or a.container_id,
                StepState.STATEMENT_BLOCKS_CONTINUE,
                seen,
                step_by_id,
                ancestors_reset,
            )

    def repair_workflow(
        self, runner_id: str, dry_run: bool = False, stranded_min_age_ms: int = 900_000
    ) -> dict:
        """Diagnose and repair a stuck workflow.

        Performs seven checks:
        1. Runner state — if completed/failed but has non-terminal work, reset to running
        2. Orphaned tasks — running tasks on dead/shutdown servers -> pending
        3. Transient step errors — connection/timeout errors -> retry (EventTransmit)
        4. Ancestor blocks — reset errored ancestors so execution resumes
        5. Dead-lettered tasks — re-enqueue with retry count reset, fix steps and ancestors
        6. Inconsistent steps — steps marked Complete but with failed tasks -> reset
        7. Stranded block steps — block-level steps left non-terminal although every
           task in the workflow is terminal. DETECTION ONLY here; advancing them
           needs the evaluator, which this layer must not import, so the caller
           resumes the returned steps (see scripts/lib/maint/repair-workflow).

        Returns a dict describing all repairs made.
        """
        from ..entities import RunnerState, TaskState
        from ..states import STUCK_STEP_STATES, StepState

        now = _current_time_ms()
        runner = self.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner {runner_id} not found")
        workflow_id = runner.workflow_id

        result: dict = {
            "runner_id": runner_id,
            "workflow_id": workflow_id,
            "runner_reset": False,
            "runner_previous_state": runner.state,
            "orphaned_tasks_reset": [],
            "transient_steps_retried": [],
            "ancestors_reset": [],
        }

        # Gather all tasks and steps
        tasks = list(self.get_tasks_by_workflow(workflow_id))
        steps = list(self.get_steps_by_workflow(workflow_id))
        step_by_id: dict[str, StepDefinition] = {s.id: s for s in steps}

        # -- 1. Check runner state consistency --
        terminal_task_states = {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.IGNORED,
            TaskState.CANCELED,
        }
        has_nonterminal_tasks = any(t.state not in terminal_task_states for t in tasks)
        has_nonterminal_steps = any(not StepState.is_terminal(s.state) for s in steps)
        if runner.state in (RunnerState.COMPLETED, RunnerState.FAILED):
            if has_nonterminal_tasks or has_nonterminal_steps:
                result["runner_reset"] = True
                if not dry_run:
                    runner.state = RunnerState.RUNNING
                    runner.end_time = 0
                    runner.duration = 0
                    self.save_runner(runner)

        # -- 2. Reset orphaned tasks (running on dead/shutdown servers) --
        # Collect server IDs from running tasks for this workflow
        running_tasks = [t for t in tasks if t.state == TaskState.RUNNING]
        server_ids = {t.server_id for t in running_tasks if t.server_id}
        dead_server_ids: set[str] = set()
        for sid in server_ids:
            srv = self.get_server(sid)
            if not srv or srv.state == "shutdown":
                dead_server_ids.add(sid)
            elif srv.ping_time and (now - srv.ping_time) > 300_000:
                dead_server_ids.add(sid)

        for t in running_tasks:
            is_orphaned = (
                t.server_id in dead_server_ids or not t.server_id  # no server claimed it
            )
            if is_orphaned:
                result["orphaned_tasks_reset"].append(
                    {
                        "task_id": t.uuid,
                        "name": t.name,
                        "step_id": t.step_id,
                        "server_id": t.server_id,
                    }
                )
                if not dry_run:
                    self._db.tasks.update_one(
                        {"uuid": t.uuid},
                        {"$set": _reset_task_to_pending(now)},
                    )
                    reason = (
                        "no server claimed it"
                        if not t.server_id
                        else f"server {t.server_id[:8]} is dead/shutdown"
                    )
                    self._repair_log(
                        step_id=t.step_id,
                        workflow_id=workflow_id,
                        message=f"Repair: task reclaimed — {t.name}, {reason}, resetting to pending",
                        facet_name=t.name,
                    )

        # -- 3. Retry transient step errors --
        transient_patterns = [
            "connection refused",
            "timed out",
            "timeout",
            "connectionerror",
            "serverselectiontimeouterror",
            "networktimeout",
            "autoreconnect",
            "errno 61",
            "no such file or directory",
            "input/output error",
        ]
        errored_steps = [s for s in steps if s.state == StepState.STATEMENT_ERROR]
        for step in errored_steps:
            raw_error = (
                getattr(step.transition, "error", None) if hasattr(step, "transition") else None
            )
            error_text = (str(raw_error) if raw_error else "").lower()
            if not any(pat in error_text for pat in transient_patterns):
                continue

            error_str = str(raw_error) if raw_error else ""
            result["transient_steps_retried"].append(
                {
                    "step_id": step.id,
                    "facet_name": step.facet_name or "",
                    "error_snippet": error_str[:120],
                }
            )
            if not dry_run:
                # Mixin-aware reset: regular steps go to EVENT_TRANSMIT,
                # mixin sub-steps to CREATED with cleared returns, and
                # the parent container resumes at MIXIN_BLOCKS_CONTINUE
                # vs STATEMENT_BLOCKS_CONTINUE per the descendant's
                # mixin status.
                self._reset_failed_step_and_ancestors(step, step_by_id, result["ancestors_reset"])

                self._repair_log(
                    step_id=step.id,
                    workflow_id=workflow_id,
                    message=(
                        f"Repair: transient error retried — {step.facet_name or 'unknown'}, "
                        f"error: {error_str[:100]}"
                    ),
                    facet_name=step.facet_name or "",
                )

                # Reset associated failed task
                task_doc = self._db.tasks.find_one(
                    {"step_id": step.id, "state": {"$in": ["failed", "running"]}}
                )
                if task_doc:
                    self._db.tasks.update_one(
                        {"uuid": task_doc["uuid"]},
                        {"$set": _reset_task_to_pending(now, clear_error=True)},
                    )

        # -- 5. Re-enqueue dead-lettered tasks --
        result["dead_letter_tasks_reset"] = []
        dead_letter_tasks = [t for t in tasks if t.state == TaskState.DEAD_LETTER]
        for t in dead_letter_tasks:
            result["dead_letter_tasks_reset"].append(
                {
                    "task_id": t.uuid,
                    "name": t.name,
                    "step_id": t.step_id,
                    "error": (t.error or "")[:120]
                    if isinstance(t.error, str)
                    else str(t.error)[:120],
                }
            )
            if not dry_run:
                error_snippet = (
                    (t.error or "")[:100] if isinstance(t.error, str) else str(t.error)[:100]
                )
                self._db.tasks.update_one(
                    {"uuid": t.uuid},
                    {"$set": _reset_task_to_pending(now, clear_error=True, reset_retries=True)},
                )
                self._repair_log(
                    step_id=t.step_id,
                    workflow_id=workflow_id,
                    message=(
                        f"Repair: dead-letter task re-enqueued — {t.name}, "
                        f"previous error: {error_snippet}"
                    ),
                    facet_name=t.name,
                )
                # Reset the associated errored step + its ancestors,
                # mixin-aware.
                dl_step = step_by_id.get(t.step_id) or self.get_step(t.step_id)
                if dl_step and dl_step.state == StepState.STATEMENT_ERROR:
                    self._reset_failed_step_and_ancestors(
                        dl_step, step_by_id, result["ancestors_reset"]
                    )

        # -- 6. Detect steps marked Complete but with failed tasks --
        # Build task lookup by step_id
        result["inconsistent_steps_reset"] = []
        task_by_step: dict[str, list] = {}
        for t in tasks:
            task_by_step.setdefault(t.step_id, []).append(t)

        for step in steps:
            if step.state != StepState.STATEMENT_COMPLETE:
                continue
            step_tasks = task_by_step.get(step.id, [])
            # If any task failed and none completed, the step shouldn't be complete
            has_failed = any(t.state == TaskState.FAILED for t in step_tasks)
            has_completed = any(t.state == TaskState.COMPLETED for t in step_tasks)
            if has_failed and not has_completed:
                result["inconsistent_steps_reset"].append(
                    {
                        "step_id": step.id,
                        "facet_name": step.facet_name or "",
                        "task_state": "failed",
                    }
                )
                if not dry_run:
                    # Use the mixin-aware reset path so an inconsistent
                    # mixin sub-step (marked Complete despite a failed
                    # task) lands at CREATED rather than EVENT_TRANSMIT.
                    self._reset_failed_step_and_ancestors(
                        step, step_by_id, result["ancestors_reset"]
                    )
                    self._repair_log(
                        step_id=step.id,
                        workflow_id=workflow_id,
                        message=(
                            f"Repair: inconsistent step reset — {step.facet_name or 'unknown'} "
                            f"was marked complete but has failed tasks, resetting to retry"
                        ),
                        facet_name=step.facet_name or "",
                    )
                    # Reset the failed task to pending
                    for t in step_tasks:
                        if t.state == TaskState.FAILED:
                            self._db.tasks.update_one(
                                {"uuid": t.uuid},
                                {"$set": _reset_task_to_pending(now, clear_error=True)},
                            )

        # -- 7. Stranded block steps --------------------------------------
        # The failure mode none of checks 1-6 intersect: every task is terminal
        # and no step errored, yet block-level steps never cascaded to terminal,
        # so the runner stays `running` forever. Observed on the national
        # county-atlas fan-out: 3,290/3,290 tasks completed, 9,036 steps
        # Complete, and 314 steps stuck at blocks.Begin / block.execution.Continue
        # for 49 hours. `repair-workflow` reported "No issues found" because the
        # runner was legitimately running, nothing had errored, and no task was
        # orphaned or dead-lettered.
        #
        # Detection is deliberately conservative:
        #   * EVERY task terminal — so we never touch a workflow still waiting on
        #     handler work (that is checks 3/5's job).
        #   * EventTransmit is EXCLUDED — that state means "awaiting a task", not
        #     "block failed to cascade".
        #   * the step must be older than `stranded_min_age_ms` so a live
        #     cascade mid-flight is never mistaken for a stall.
        result["stranded_block_steps"] = []
        block_stuck_states = tuple(
            st for st in STUCK_STEP_STATES if st != StepState.EVENT_TRANSMIT
        )
        if not has_nonterminal_tasks and tasks:
            cutoff = now - stranded_min_age_ms
            for step in steps:
                if step.state not in block_stuck_states:
                    continue
                last = step.last_modified or step.start_time or 0
                if last and last > cutoff:
                    continue  # too recent — could be an in-flight cascade
                result["stranded_block_steps"].append(
                    {
                        "step_id": step.id,
                        "statement_name": step.statement_name or "",
                        "facet_name": step.facet_name or "",
                        "state": step.state,
                        "age_ms": (now - last) if last else None,
                    }
                )

        return result
