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

"""Base class for state handlers."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..changers.base import StateChangeResult
    from ..evaluator import ExecutionContext
    from ..step import StepDefinition


class StateHandler(ABC):
    """Abstract base for state handlers.

    Each state in the state machine has a handler that:
    - Performs the work for that state
    - Optionally transitions to the next state
    - Handles errors appropriately
    """

    def __init__(self, step: "StepDefinition", context: "ExecutionContext"):
        """Initialize handler.

        Args:
            step: The step being processed
            context: Execution context with persistence and services
        """
        self.step = step
        self.context = context

    def process(self) -> "StateChangeResult":
        """Process this state.

        Wrapper that logs and handles errors.

        Returns:
            StateChangeResult with updated step
        """
        from ..changers.base import StateChangeResult

        self.context.telemetry.log_state_begin(self.step, self.state_name)

        try:
            result = self.process_state()
            self.context.telemetry.log_state_end(self.step, self.state_name)
            return result
        except Exception as e:
            self.context.telemetry.log_error(self.step, self.state_name, e)
            return StateChangeResult(
                step=self.step,
                success=False,
                error=e,
            )

    @abstractmethod
    def process_state(self) -> "StateChangeResult":
        """Process the state logic.

        Subclasses implement this to define state-specific behavior.

        Returns:
            StateChangeResult with updated step
        """
        ...

    @property
    def state_name(self) -> str:
        """Get the state name this handler processes."""
        return self.__class__.__name__

    def transition(self) -> "StateChangeResult":
        """Request transition to next state.

        Convenience method for handlers that just need to advance.
        """
        from ..changers.base import StateChangeResult

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def stay(self, push: bool = False) -> "StateChangeResult":
        """Stay in current state.

        Args:
            push: If True, request re-queue for continued processing

        Returns:
            StateChangeResult staying in current state
        """
        from ..changers.base import StateChangeResult

        self.step.request_state_change(False)
        self.step.transition.set_push_me(push)
        return StateChangeResult(
            step=self.step,
            continue_processing=push,
        )

    def error(self, exception: Exception) -> "StateChangeResult":
        """Mark step as errored.

        Args:
            exception: The error that occurred

        Returns:
            StateChangeResult with error
        """
        from ..changers.base import StateChangeResult

        self.step.mark_error(exception)
        return StateChangeResult(
            step=self.step,
            success=False,
            error=exception,
            continue_processing=False,
        )
