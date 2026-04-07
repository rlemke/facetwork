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

from .base import _current_time_ms


class RepairMixin:
    """Workflow repair operations."""

    def repair_workflow(self, runner_id: str, dry_run: bool = False) -> dict:
        """Diagnose and repair a stuck workflow.

        Performs six checks:
        1. Runner state — if completed/failed but has non-terminal work, reset to running
        2. Orphaned tasks — running tasks on dead/shutdown servers -> pending
        3. Transient step errors — connection/timeout errors -> retry (EventTransmit)
        4. Ancestor blocks — reset errored ancestors so execution resumes
        5. Dead-lettered tasks — re-enqueue with retry count reset, fix steps and ancestors
        6. Inconsistent steps — steps marked Complete but with failed tasks -> reset

        Returns a dict describing all repairs made.
        """
        from ..entities import RunnerState, TaskState
        from ..states import StepState

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
        step_by_id = {s.id: s for s in steps}

        # -- 1. Check runner state consistency --
        terminal_task_states = {
            TaskState.COMPLETED, TaskState.FAILED,
            TaskState.IGNORED, TaskState.CANCELED,
        }
        has_nonterminal_tasks = any(
            t.state not in terminal_task_states for t in tasks
        )
        has_nonterminal_steps = any(
            not StepState.is_terminal(s.state) for s in steps
        )
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
                t.server_id in dead_server_ids
                or not t.server_id  # no server claimed it
            )
            if is_orphaned:
                result["orphaned_tasks_reset"].append({
                    "task_id": t.uuid,
                    "name": t.name,
                    "step_id": t.step_id,
                    "server_id": t.server_id,
                })
                if not dry_run:
                    self._db.tasks.update_one(
                        {"uuid": t.uuid},
                        {"$set": {
                            "state": "pending",
                            "server_id": "",
                            "task_heartbeat": 0,
                            "updated": now,
                        }},
                    )

        # -- 3. Retry transient step errors --
        transient_patterns = [
            "connection refused", "timed out", "timeout",
            "connectionerror", "serverselectiontimeouterror",
            "networktimeout", "autoreconnect", "errno 61",
            "no such file or directory", "input/output error",
        ]
        errored_steps = [s for s in steps if s.state == StepState.STATEMENT_ERROR]
        for step in errored_steps:
            raw_error = getattr(step.transition, "error", None) if hasattr(step, "transition") else None
            error_text = (str(raw_error) if raw_error else "").lower()
            if not any(pat in error_text for pat in transient_patterns):
                continue

            error_str = str(raw_error) if raw_error else ""
            result["transient_steps_retried"].append({
                "step_id": step.id,
                "facet_name": step.facet_name or "",
                "error_snippet": error_str[:120],
            })
            if not dry_run:
                step.state = StepState.EVENT_TRANSMIT
                if hasattr(step, "transition") and step.transition:
                    step.transition.current_state = StepState.EVENT_TRANSMIT
                    step.transition.clear_error()
                    step.transition.request_transition = False
                    step.transition.changed = True
                self.save_step(step)

                # Reset associated failed task
                task_doc = self._db.tasks.find_one(
                    {"step_id": step.id, "state": {"$in": ["failed", "running"]}}
                )
                if task_doc:
                    self._db.tasks.update_one(
                        {"uuid": task_doc["uuid"]},
                        {"$set": {
                            "state": "pending",
                            "server_id": "",
                            "error": None,
                            "task_heartbeat": 0,
                            "updated": now,
                        }},
                    )

                # -- 4. Reset errored ancestors --
                seen: set[str] = set()
                current_id = step.block_id
                while current_id and current_id not in seen:
                    seen.add(current_id)
                    ancestor = step_by_id.get(current_id) or self.get_step(current_id)
                    if ancestor is None:
                        break
                    if ancestor.state == StepState.STATEMENT_ERROR:
                        ancestor.state = StepState.BLOCK_EXECUTION_CONTINUE
                        if hasattr(ancestor, "transition") and ancestor.transition:
                            ancestor.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
                            ancestor.transition.clear_error()
                            ancestor.transition.request_transition = False
                            ancestor.transition.changed = True
                        self.save_step(ancestor)
                        result["ancestors_reset"].append(ancestor.id)
                    current_id = ancestor.block_id

                current_id = step.container_id
                while current_id and current_id not in seen:
                    seen.add(current_id)
                    ancestor = step_by_id.get(current_id) or self.get_step(current_id)
                    if ancestor is None:
                        break
                    if ancestor.state == StepState.STATEMENT_ERROR:
                        ancestor.state = StepState.STATEMENT_BLOCKS_CONTINUE
                        if hasattr(ancestor, "transition") and ancestor.transition:
                            ancestor.transition.current_state = StepState.STATEMENT_BLOCKS_CONTINUE
                            ancestor.transition.clear_error()
                            ancestor.transition.request_transition = False
                            ancestor.transition.changed = True
                        self.save_step(ancestor)
                        result["ancestors_reset"].append(ancestor.id)
                    next_id = ancestor.block_id or ancestor.container_id
                    current_id = next_id

        # -- 5. Re-enqueue dead-lettered tasks --
        result["dead_letter_tasks_reset"] = []
        dead_letter_tasks = [t for t in tasks if t.state == TaskState.DEAD_LETTER]
        for t in dead_letter_tasks:
            result["dead_letter_tasks_reset"].append({
                "task_id": t.uuid,
                "name": t.name,
                "step_id": t.step_id,
                "error": (t.error or "")[:120] if isinstance(t.error, str) else str(t.error)[:120],
            })
            if not dry_run:
                self._db.tasks.update_one(
                    {"uuid": t.uuid},
                    {"$set": {
                        "state": "pending",
                        "server_id": "",
                        "error": None,
                        "retry_count": 0,
                        "task_heartbeat": 0,
                        "updated": now,
                    }},
                )
                # Reset the associated errored step
                step = step_by_id.get(t.step_id) or self.get_step(t.step_id)
                if step and step.state == StepState.STATEMENT_ERROR:
                    step.state = StepState.EVENT_TRANSMIT
                    if hasattr(step, "transition") and step.transition:
                        step.transition.current_state = StepState.EVENT_TRANSMIT
                        step.transition.clear_error()
                        step.transition.request_transition = False
                        step.transition.changed = True
                    self.save_step(step)

                    # Reset errored ancestors
                    seen_dl: set[str] = set()
                    current_id = step.block_id
                    while current_id and current_id not in seen_dl:
                        seen_dl.add(current_id)
                        ancestor = step_by_id.get(current_id) or self.get_step(current_id)
                        if ancestor is None:
                            break
                        if ancestor.state == StepState.STATEMENT_ERROR:
                            ancestor.state = StepState.BLOCK_EXECUTION_CONTINUE
                            if hasattr(ancestor, "transition") and ancestor.transition:
                                ancestor.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
                                ancestor.transition.clear_error()
                                ancestor.transition.request_transition = False
                                ancestor.transition.changed = True
                            self.save_step(ancestor)
                            result["ancestors_reset"].append(ancestor.id)
                        current_id = ancestor.block_id

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
                result["inconsistent_steps_reset"].append({
                    "step_id": step.id,
                    "facet_name": step.facet_name or "",
                    "task_state": "failed",
                })
                if not dry_run:
                    step.state = StepState.EVENT_TRANSMIT
                    step.error = None
                    if hasattr(step, "transition") and step.transition:
                        step.transition.current_state = StepState.EVENT_TRANSMIT
                        step.transition.clear_error()
                        step.transition.request_transition = False
                        step.transition.changed = True
                    self.save_step(step)
                    # Reset the failed task to pending
                    for t in step_tasks:
                        if t.state == TaskState.FAILED:
                            self._db.tasks.update_one(
                                {"uuid": t.uuid},
                                {"$set": {
                                    "state": "pending",
                                    "server_id": "",
                                    "error": None,
                                    "task_heartbeat": 0,
                                    "updated": now,
                                }},
                            )

        return result
