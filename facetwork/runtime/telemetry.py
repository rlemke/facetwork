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

"""AFL runtime telemetry.

Structured logging for runtime operations.
Telemetry MUST NOT affect execution semantics.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .types import BlockId, StepId

if TYPE_CHECKING:
    from .step import StepDefinition


@dataclass
class TelemetryEvent:
    """A single telemetry event."""

    timestamp: str
    event_type: str
    workflow_id: str | None = None
    step_id: StepId | None = None
    block_id: BlockId | StepId | None = None
    state: str | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "timestamp": self.timestamp,
            "eventType": self.event_type,
        }
        if self.workflow_id:
            result["workflowId"] = self.workflow_id
        if self.step_id:
            result["stepId"] = self.step_id
        if self.block_id:
            result["blockId"] = self.block_id
        if self.state:
            result["state"] = self.state
        if self.details:
            result["details"] = self.details
        return result


class Telemetry:
    """Telemetry collector for runtime operations.

    Collects structured telemetry for:
    - Step state transitions
    - Dependency resolution
    - Iteration boundaries
    - Event publication
    """

    def __init__(self, enabled: bool = True):
        """Initialize telemetry.

        Args:
            enabled: Whether telemetry is enabled
        """
        self.enabled = enabled
        self.events: list[TelemetryEvent] = []

    def _now(self) -> str:
        """Get current timestamp."""
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _log(
        self,
        event_type: str,
        workflow_id: str | None = None,
        step_id: StepId | None = None,
        block_id: BlockId | StepId | None = None,
        state: str | None = None,
        **details: Any,
    ) -> None:
        """Log a telemetry event."""
        if not self.enabled:
            return

        event = TelemetryEvent(
            timestamp=self._now(),
            event_type=event_type,
            workflow_id=workflow_id,
            step_id=step_id,
            block_id=block_id,
            state=state,
            details=details,
        )
        self.events.append(event)

    def log_workflow_start(self, workflow_id: str, workflow_name: str) -> None:
        """Log workflow execution start."""
        self._log(
            "workflow.start",
            workflow_id=workflow_id,
            workflowName=workflow_name,
        )

    def log_workflow_complete(self, workflow_id: str, result: dict) -> None:
        """Log workflow execution complete."""
        self._log(
            "workflow.complete",
            workflow_id=workflow_id,
            result=result,
        )

    def log_workflow_error(self, workflow_id: str, error: Exception) -> None:
        """Log workflow execution error."""
        self._log(
            "workflow.error",
            workflow_id=workflow_id,
            error=str(error),
            errorType=type(error).__name__,
        )

    def log_iteration_start(self, workflow_id: str, iteration: int) -> None:
        """Log iteration start."""
        self._log(
            "iteration.start",
            workflow_id=workflow_id,
            iteration=iteration,
        )

    def log_iteration_end(
        self,
        workflow_id: str,
        iteration: int,
        steps_created: int,
        steps_updated: int,
    ) -> None:
        """Log iteration end with summary."""
        self._log(
            "iteration.end",
            workflow_id=workflow_id,
            iteration=iteration,
            stepsCreated=steps_created,
            stepsUpdated=steps_updated,
        )

    def log_step_created(
        self,
        step: "StepDefinition",
    ) -> None:
        """Log step creation."""
        self._log(
            "step.created",
            workflow_id=step.workflow_id,
            step_id=step.id,
            block_id=step.block_id,
            state=step.state,
            objectType=step.object_type,
            facetName=step.facet_name,
        )

    def log_state_begin(self, step: "StepDefinition", state: str) -> None:
        """Log state handler begin."""
        self._log(
            "state.begin",
            workflow_id=step.workflow_id,
            step_id=step.id,
            state=state,
        )

    def log_state_end(self, step: "StepDefinition", state: str) -> None:
        """Log state handler end."""
        self._log(
            "state.end",
            workflow_id=step.workflow_id,
            step_id=step.id,
            state=state,
        )

    def log_state_transition(
        self,
        step: "StepDefinition",
        from_state: str,
        to_state: str,
    ) -> None:
        """Log state transition."""
        self._log(
            "state.transition",
            workflow_id=step.workflow_id,
            step_id=step.id,
            fromState=from_state,
            toState=to_state,
        )

    def log_error(
        self,
        step: "StepDefinition",
        state: str,
        error: Exception,
    ) -> None:
        """Log error during state execution."""
        self._log(
            "step.error",
            workflow_id=step.workflow_id,
            step_id=step.id,
            state=state,
            error=str(error),
            errorType=type(error).__name__,
        )

    def log_dependency_resolved(
        self,
        step_id: StepId,
        dependency_id: StepId,
    ) -> None:
        """Log dependency resolution."""
        self._log(
            "dependency.resolved",
            step_id=step_id,
            dependencyId=str(dependency_id),
        )

    def log_event_published(
        self,
        event_id: str,
        step_id: StepId,
        event_type: str,
    ) -> None:
        """Log event publication."""
        self._log(
            "event.published",
            step_id=step_id,
            eventId=event_id,
            eventType=event_type,
        )

    def clear(self) -> None:
        """Clear all events."""
        self.events.clear()

    def get_events(self) -> list[dict]:
        """Get all events as dictionaries."""
        return [e.to_dict() for e in self.events]

    def to_json(self, indent: int = 2) -> str:
        """Export events as JSON."""
        return json.dumps(self.get_events(), indent=indent)
