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

"""Full state changer for VariableAssignment steps."""

from typing import TYPE_CHECKING

from ..states import STEP_TRANSITIONS
from .base import StateChanger, StateChangeResult

if TYPE_CHECKING:
    pass


class StepStateChanger(StateChanger):
    """State changer for VariableAssignment steps.

    Implements the full state machine with all phases:
    - Facet initialization
    - Facet scripts
    - Statement scripts
    - Mixin blocks
    - Mixin capture
    - Event transmit
    - Statement blocks
    - Statement capture
    - Completion
    """

    def select_state(self) -> str | None:
        """Select next state using full transition table."""
        current = self.step.current_state
        next_state = STEP_TRANSITIONS.get(current)

        if next_state is None or next_state == current:
            return None
        return next_state

    def execute_state(self, state: str) -> StateChangeResult:
        """Execute handler for current state."""
        from ..handlers import get_handler

        handler = get_handler(state, self.step, self.context)
        if handler is None:
            # No handler for this state, auto-transition
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        try:
            result = handler.process()
            return result
        except Exception as e:
            return StateChangeResult(
                step=self.step,
                success=False,
                error=e,
            )
