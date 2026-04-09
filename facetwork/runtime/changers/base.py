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

"""Abstract base class for state changers."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from ..step import StepDefinition

if TYPE_CHECKING:
    from ..evaluator import ExecutionContext


@dataclass
class StateChangeResult:
    """Result of a state change operation."""

    step: StepDefinition
    success: bool = True
    error: Exception | None = None
    continue_processing: bool = True


class StateChanger(ABC):
    """Abstract base for state machine orchestrators.

    A StateChanger drives the state machine loop:
    1. Check if step is requesting state change
    2. Select next state
    3. Execute state handler
    4. Repeat until step is complete or blocked
    """

    def __init__(self, step: StepDefinition, context: "ExecutionContext"):
        """Initialize state changer.

        Args:
            step: The step to process
            context: Execution context with persistence and handlers
        """
        self.step = step
        self.context = context

    def process(self) -> StateChangeResult:
        """Process the step through its state machine.

        Loops through state transitions until the step:
        - Reaches a terminal state (Complete/Error)
        - Is no longer requesting state changes
        - Is blocked waiting on external work

        Returns:
            StateChangeResult with final step state
        """
        if self.step.is_complete:
            return StateChangeResult(step=self.step, continue_processing=False)

        logger.debug(
            "StateChanger process: step_id=%s object_type=%s current_state=%s",
            self.step.id,
            self.step.object_type,
            self.step.current_state,
        )

        try:
            while True:
                # Check if requesting state change
                if self.step.is_requesting_state_change:
                    # Select and change to next state
                    next_state = self.select_state()
                    if next_state and next_state != self.step.current_state:
                        logger.debug(
                            "State transition: step_id=%s from=%s to=%s",
                            self.step.id,
                            self.step.current_state,
                            next_state,
                        )
                        self.step.change_state(next_state)

                # Execute current state handler
                result = self.execute_state(self.step.current_state)

                if not result.success:
                    logger.warning(
                        "Handler error: step_id=%s state=%s error=%s",
                        self.step.id,
                        self.step.current_state,
                        result.error,
                    )
                    # Check for catch clause before erroring
                    catch_ast = self.context._find_statement_catch(self.step)
                    if catch_ast:
                        from ..states import StepState

                        self.step.transition.error = result.error
                        self.step.change_state(StepState.CATCH_BEGIN)
                        self.step.request_state_change(True)
                        self.step = result.step
                        continue  # Re-enter the loop to process CATCH_BEGIN
                    self.step.mark_error(result.error)
                    return StateChangeResult(
                        step=self.step,
                        success=False,
                        error=result.error,
                        continue_processing=False,
                    )

                # Update step from result
                self.step = result.step

                # Check if we should continue
                if self.step.is_terminal:
                    return StateChangeResult(
                        step=self.step,
                        continue_processing=False,
                    )

                if not self.step.is_requesting_state_change:
                    break

            return StateChangeResult(
                step=self.step,
                continue_processing=self.step.transition.is_requesting_push,
            )

        except Exception as e:
            logger.error(
                "StateChanger exception: step_id=%s error=%s",
                self.step.id,
                e,
            )
            self.step.mark_error(e)
            return StateChangeResult(
                step=self.step,
                success=False,
                error=e,
                continue_processing=False,
            )

    @abstractmethod
    def select_state(self) -> str | None:
        """Select the next state for the step.

        Returns:
            The next state, or None if at terminal
        """
        ...

    @abstractmethod
    def execute_state(self, state: str) -> StateChangeResult:
        """Execute the handler for a state.

        Args:
            state: The state to execute

        Returns:
            StateChangeResult from handler
        """
        ...
