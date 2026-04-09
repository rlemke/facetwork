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

"""AFL step state machine definitions.

This module defines the states and transitions for step execution.
There are three state machines:
- StepStateChanger: Full state machine for VariableAssignment
- BlockStateChanger: Simplified for AndThen blocks
- YieldStateChanger: Minimal for YieldAssignment
"""


class StepState:
    """Step state constants using hierarchical naming convention."""

    # Initial state
    CREATED = "state.statement.Created"

    # Facet initialization phase
    FACET_INIT_BEGIN = "state.facet.initialization.Begin"
    FACET_INIT_END = "state.facet.initialization.End"

    # Facet scripts phase
    FACET_SCRIPTS_BEGIN = "state.facet.scripts.Begin"
    FACET_SCRIPTS_END = "state.facet.scripts.End"

    # Statement scripts phase
    STATEMENT_SCRIPTS_BEGIN = "state.statement.scripts.Begin"
    STATEMENT_SCRIPTS_END = "state.statement.scripts.End"

    # Mixin blocks phase
    MIXIN_BLOCKS_BEGIN = "state.mixin.blocks.Begin"
    MIXIN_BLOCKS_CONTINUE = "state.mixin.blocks.Continue"
    MIXIN_BLOCKS_END = "state.mixin.blocks.End"

    # Mixin capture phase
    MIXIN_CAPTURE_BEGIN = "state.mixin.capture.Begin"
    MIXIN_CAPTURE_END = "state.mixin.capture.End"

    # Event transmit
    EVENT_TRANSMIT = "state.EventTransmit"

    # Statement blocks phase
    STATEMENT_BLOCKS_BEGIN = "state.statement.blocks.Begin"
    STATEMENT_BLOCKS_CONTINUE = "state.statement.blocks.Continue"
    STATEMENT_BLOCKS_END = "state.statement.blocks.End"

    # Block execution phase (for block steps)
    BLOCK_EXECUTION_BEGIN = "state.block.execution.Begin"
    BLOCK_EXECUTION_CONTINUE = "state.block.execution.Continue"
    BLOCK_EXECUTION_END = "state.block.execution.End"

    # Statement capture phase
    STATEMENT_CAPTURE_BEGIN = "state.statement.capture.Begin"
    STATEMENT_CAPTURE_END = "state.statement.capture.End"

    # Catch phase
    CATCH_BEGIN = "state.statement.catch.Begin"
    CATCH_CONTINUE = "state.statement.catch.Continue"
    CATCH_END = "state.statement.catch.End"

    # Terminal states
    STATEMENT_END = "state.statement.End"
    STATEMENT_COMPLETE = "state.statement.Complete"
    STATEMENT_ERROR = "state.statement.Error"

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        """Check if state is terminal (Complete or Error)."""
        return state in (cls.STATEMENT_COMPLETE, cls.STATEMENT_ERROR)

    @classmethod
    def is_complete(cls, state: str) -> bool:
        """Check if state is Complete."""
        return state == cls.STATEMENT_COMPLETE

    @classmethod
    def is_error(cls, state: str) -> bool:
        """Check if state is Error."""
        return state == cls.STATEMENT_ERROR


# Full state machine transitions for VariableAssignment steps
STEP_TRANSITIONS: dict[str, str] = {
    StepState.CREATED: StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN: StepState.FACET_INIT_END,
    StepState.FACET_INIT_END: StepState.FACET_SCRIPTS_BEGIN,
    StepState.FACET_SCRIPTS_BEGIN: StepState.FACET_SCRIPTS_END,
    StepState.FACET_SCRIPTS_END: StepState.MIXIN_BLOCKS_BEGIN,
    StepState.MIXIN_BLOCKS_BEGIN: StepState.MIXIN_BLOCKS_CONTINUE,
    StepState.MIXIN_BLOCKS_CONTINUE: StepState.MIXIN_BLOCKS_END,
    StepState.MIXIN_BLOCKS_END: StepState.MIXIN_CAPTURE_BEGIN,
    StepState.MIXIN_CAPTURE_BEGIN: StepState.MIXIN_CAPTURE_END,
    StepState.MIXIN_CAPTURE_END: StepState.EVENT_TRANSMIT,
    StepState.EVENT_TRANSMIT: StepState.STATEMENT_BLOCKS_BEGIN,
    StepState.STATEMENT_BLOCKS_BEGIN: StepState.STATEMENT_BLOCKS_CONTINUE,
    StepState.STATEMENT_BLOCKS_CONTINUE: StepState.STATEMENT_BLOCKS_END,
    StepState.STATEMENT_BLOCKS_END: StepState.STATEMENT_CAPTURE_BEGIN,
    StepState.STATEMENT_CAPTURE_BEGIN: StepState.STATEMENT_CAPTURE_END,
    StepState.STATEMENT_CAPTURE_END: StepState.STATEMENT_END,
    StepState.STATEMENT_END: StepState.STATEMENT_COMPLETE,
    # Catch phase transitions (entered via explicit state change, not normal flow)
    StepState.CATCH_BEGIN: StepState.CATCH_CONTINUE,
    StepState.CATCH_CONTINUE: StepState.CATCH_END,
    StepState.CATCH_END: StepState.STATEMENT_CAPTURE_BEGIN,
}


# Simplified state machine for Block steps (AndThen, AndMap, etc.)
BLOCK_TRANSITIONS: dict[str, str] = {
    StepState.CREATED: StepState.BLOCK_EXECUTION_BEGIN,
    StepState.BLOCK_EXECUTION_BEGIN: StepState.BLOCK_EXECUTION_CONTINUE,
    StepState.BLOCK_EXECUTION_CONTINUE: StepState.BLOCK_EXECUTION_END,
    StepState.BLOCK_EXECUTION_END: StepState.STATEMENT_END,
    StepState.STATEMENT_END: StepState.STATEMENT_COMPLETE,
}


# Minimal state machine for YieldAssignment steps
# Skips blocks - goes directly to end after facet initialization
YIELD_TRANSITIONS: dict[str, str] = {
    StepState.CREATED: StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN: StepState.FACET_INIT_END,
    StepState.FACET_INIT_END: StepState.FACET_SCRIPTS_BEGIN,
    StepState.FACET_SCRIPTS_BEGIN: StepState.FACET_SCRIPTS_END,
    StepState.FACET_SCRIPTS_END: StepState.STATEMENT_END,  # Skip blocks
    StepState.STATEMENT_END: StepState.STATEMENT_COMPLETE,
}


# Simplified state machine for SchemaInstantiation steps
# Evaluates arguments and stores them as returns, skips all other phases
SCHEMA_TRANSITIONS: dict[str, str] = {
    StepState.CREATED: StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN: StepState.FACET_INIT_END,
    StepState.FACET_INIT_END: StepState.STATEMENT_END,
    StepState.STATEMENT_END: StepState.STATEMENT_COMPLETE,
}


def get_next_state(current_state: str, transitions: dict[str, str]) -> str | None:
    """Get the next state given current state and transition table.

    Args:
        current_state: The current state
        transitions: The transition table to use

    Returns:
        The next state, or None if at terminal state
    """
    return transitions.get(current_state)


def select_transitions(object_type: str) -> dict[str, str]:
    """Select the appropriate transition table for an object type.

    Args:
        object_type: The ObjectType of the step

    Returns:
        The appropriate transition dictionary
    """
    from .types import ObjectType

    if object_type == ObjectType.YIELD_ASSIGNMENT:
        return YIELD_TRANSITIONS
    elif object_type == ObjectType.SCHEMA_INSTANTIATION:
        return SCHEMA_TRANSITIONS
    elif ObjectType.is_block(object_type):
        return BLOCK_TRANSITIONS
    else:
        return STEP_TRANSITIONS
