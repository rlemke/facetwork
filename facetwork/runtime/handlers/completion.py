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

"""Completion phase handlers.

Handles statement end, completion, and event transmission.
"""

import time
from typing import TYPE_CHECKING

from ..changers.base import StateChangeResult
from ..types import generate_id
from .base import StateHandler

if TYPE_CHECKING:
    pass


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


class EventTransmitHandler(StateHandler):
    """Handler for state.EventTransmit.

    Dispatches events to external agents for processing.
    For event facets, this is where the external work happens.
    """

    def process_state(self) -> StateChangeResult:
        """Transmit event to agent.

        For event facets: if an inline dispatcher is available and can
        handle this facet, dispatches immediately and completes the step.
        Otherwise creates an event and a task in the task queue, then
        BLOCKS the step until an external caller invokes continue_step().
        For regular facets: passes through to next state.
        """
        facet_def = self.context.get_facet_definition(self.step.facet_name)

        if facet_def and facet_def.get("type") == "EventFacetDecl":
            # Try inline dispatch if a dispatcher is available
            dispatcher = self.context.dispatcher
            if dispatcher and dispatcher.can_dispatch(self.step.facet_name):
                try:
                    payload = self._build_payload()
                    payload["_step_log"] = self._make_step_log_callback()

                    facet_name = self.step.facet_name
                    self._emit_step_log(f"Dispatching handler: {facet_name}")
                    dispatch_start = _current_time_ms()

                    result = dispatcher.dispatch(facet_name, payload)

                    dispatch_duration = _current_time_ms() - dispatch_start

                    if result is not None:
                        self._emit_step_log(
                            f"Handler completed: {facet_name} ({dispatch_duration}ms)",
                            level="success",
                        )
                        for name, value in result.items():
                            self.step.set_attribute(name, value, is_return=True)
                        self.step.request_state_change(True)
                        return StateChangeResult(step=self.step)
                except Exception as e:
                    self._emit_step_log(
                        f"Handler error: {e}",
                        level="error",
                    )
                    return self.error(e)

            # Fallback: create task and block
            self._create_event_task()

            # BLOCK: stay at EventTransmit, do not re-queue
            return self.stay(push=False)

        # Non-event facet: pass through to next state
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _build_payload(self) -> dict:
        """Build event payload from step attributes."""
        payload = {}
        for name, attr in self.step.attributes.params.items():
            payload[name] = attr.value
        return payload

    def _emit_step_log(self, message: str, level: str = "info") -> None:
        """Emit a framework step log entry for inline dispatch."""
        from ..entities import StepLogEntry, StepLogSource

        entry = StepLogEntry(
            uuid=generate_id(),
            step_id=self.step.id,
            workflow_id=self.step.workflow_id,
            runner_id=self.context.runner_id,
            facet_name=self.step.facet_name,
            source=StepLogSource.FRAMEWORK,
            level=level,
            message=message,
            time=_current_time_ms(),
        )
        try:
            self.context.persistence.save_step_log(entry)
        except Exception:
            pass

    def _make_step_log_callback(self):
        """Create a step log callback for handler-level logging."""
        from ..entities import StepLogEntry, StepLogSource

        step = self.step
        context = self.context

        def _step_log_callback(message, level="info", details=None):
            entry = StepLogEntry(
                uuid=generate_id(),
                step_id=step.id,
                workflow_id=step.workflow_id,
                runner_id=context.runner_id,
                facet_name=step.facet_name,
                source=StepLogSource.HANDLER,
                level=level,
                message=message,
                details=details or {},
                time=_current_time_ms(),
            )
            try:
                context.persistence.save_step_log(entry)
            except Exception:
                pass

        return _step_log_callback

    @staticmethod
    def _timeout_args_to_ms(args: list[dict]) -> int:
        """Convert Timeout mixin args (hours, minutes, seconds, ms) to ms.

        Supports any combination: ``Timeout(hours = 1, minutes = 30)``
        produces 5_400_000 ms.
        """
        multipliers = {"hours": 3_600_000, "minutes": 60_000, "seconds": 1_000, "ms": 1}
        total = 0
        for arg in args:
            name = arg.get("name", "")
            if name not in multipliers:
                continue
            val = arg.get("value", {})
            if isinstance(val, dict):
                raw = val.get("value", 0)
            elif isinstance(val, (int, float)):
                raw = val
            else:
                continue
            total += int(raw) * multipliers[name]
        return total

    def _extract_timeout_ms(self) -> int:
        """Extract timeout_ms from Timeout mixin on the facet definition.

        Checks both facet-level signature mixins and step-level call-site
        mixins (call-site overrides facet-level).

        Timeout accepts: ``hours``, ``minutes``, ``seconds``, ``ms`` —
        all additive.  E.g. ``with Timeout(minutes = 10, seconds = 30)``
        → 630_000 ms.

        Returns 0 if no Timeout mixin is found.
        """
        timeout_ms = 0

        # Check facet definition signature mixins
        facet_def = self.context.get_facet_definition(self.step.facet_name)
        if facet_def:
            for mixin in facet_def.get("mixins", []):
                target = mixin.get("target", "")
                if target == "Timeout" or target.endswith(".Timeout"):
                    timeout_ms = self._timeout_args_to_ms(mixin.get("args", []))

        # Check step-level call-site mixins (override facet-level)
        stmt_def = self.context.get_statement_definition(self.step)
        if stmt_def:
            for mixin in getattr(stmt_def, "mixins", None) or []:
                target = mixin.get("target", "")
                if target == "Timeout" or target.endswith(".Timeout"):
                    timeout_ms = self._timeout_args_to_ms(mixin.get("args", []))

        return timeout_ms

    def _create_event_task(self) -> None:
        """Create a TaskDefinition for the event and add to iteration changes."""
        from ..entities import TaskDefinition, TaskState

        now = _current_time_ms()
        timeout_ms = self._extract_timeout_ms()
        task = TaskDefinition(
            uuid=generate_id(),
            name=self.step.facet_name,
            runner_id=self.context.runner_id,
            workflow_id=self.step.workflow_id,
            flow_id="",
            step_id=self.step.id,
            state=TaskState.PENDING,
            created=now,
            updated=now,
            task_list_name="default",
            data=self._build_payload(),
            timeout_ms=timeout_ms,
        )
        self.context.changes.add_created_task(task)


class StatementEndHandler(StateHandler):
    """Handler for state.statement.End.

    Prepares step for completion.
    """

    def process_state(self) -> StateChangeResult:
        """End statement execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class StatementCompleteHandler(StateHandler):
    """Handler for state.statement.Complete.

    Marks step as complete and notifies containing block.
    """

    def process_state(self) -> StateChangeResult:
        """Complete statement execution."""
        # Mark step as completed
        self.step.mark_completed()

        # Notify containing block if any
        self._notify_container()

        return StateChangeResult(
            step=self.step,
            continue_processing=False,
        )

    def _notify_container(self) -> None:
        """Notify containing block that this step is complete.

        This triggers the block to re-evaluate its progress.
        Note: We don't add to updated_steps here because the container
        will be re-processed in the next iteration anyway. Adding a stale
        copy from persistence would overwrite the properly updated version.
        """
        # Container notification is handled implicitly through iteration
        # The container will be re-processed and see this step is complete
        pass
