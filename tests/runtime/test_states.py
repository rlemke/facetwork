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

"""Tests for FFL runtime state machine."""

from facetwork.runtime import (
    BLOCK_TRANSITIONS,
    SCHEMA_TRANSITIONS,
    STEP_TRANSITIONS,
    YIELD_TRANSITIONS,
    ObjectType,
    StepState,
    get_next_state,
    select_transitions,
)


class TestStepState:
    """Tests for StepState constants."""

    def test_terminal_states(self):
        """Test terminal state detection."""
        assert StepState.is_terminal(StepState.STATEMENT_COMPLETE)
        assert StepState.is_terminal(StepState.STATEMENT_ERROR)
        assert not StepState.is_terminal(StepState.CREATED)
        assert not StepState.is_terminal(StepState.FACET_INIT_BEGIN)

    def test_complete_state(self):
        """Test complete state detection."""
        assert StepState.is_complete(StepState.STATEMENT_COMPLETE)
        assert not StepState.is_complete(StepState.STATEMENT_ERROR)
        assert not StepState.is_complete(StepState.CREATED)

    def test_error_state(self):
        """Test error state detection."""
        assert StepState.is_error(StepState.STATEMENT_ERROR)
        assert not StepState.is_error(StepState.STATEMENT_COMPLETE)


class TestStepTransitions:
    """Tests for step state transitions."""

    def test_full_step_path(self):
        """Test complete path through step state machine."""
        state = StepState.CREATED
        visited = [state]

        while state in STEP_TRANSITIONS:
            state = STEP_TRANSITIONS[state]
            visited.append(state)

        # Should end at STATEMENT_COMPLETE
        assert visited[-1] == StepState.STATEMENT_COMPLETE

        # Should pass through all major phases
        assert StepState.FACET_INIT_BEGIN in visited
        assert StepState.FACET_INIT_END in visited
        assert StepState.MIXIN_BLOCKS_BEGIN in visited
        assert StepState.STATEMENT_BLOCKS_BEGIN in visited
        assert StepState.STATEMENT_CAPTURE_BEGIN in visited

    def test_block_step_path(self):
        """Test path through block state machine."""
        state = StepState.CREATED
        visited = [state]

        while state in BLOCK_TRANSITIONS:
            state = BLOCK_TRANSITIONS[state]
            visited.append(state)

        # Should end at STATEMENT_COMPLETE
        assert visited[-1] == StepState.STATEMENT_COMPLETE

        # Should use block execution states
        assert StepState.BLOCK_EXECUTION_BEGIN in visited
        assert StepState.BLOCK_EXECUTION_CONTINUE in visited
        assert StepState.BLOCK_EXECUTION_END in visited

    def test_yield_step_path(self):
        """Test path through yield state machine."""
        state = StepState.CREATED
        visited = [state]

        while state in YIELD_TRANSITIONS:
            state = YIELD_TRANSITIONS[state]
            visited.append(state)

        # Should end at STATEMENT_COMPLETE
        assert visited[-1] == StepState.STATEMENT_COMPLETE

        # Should skip blocks
        assert StepState.MIXIN_BLOCKS_BEGIN not in visited
        assert StepState.STATEMENT_BLOCKS_BEGIN not in visited
        assert StepState.BLOCK_EXECUTION_BEGIN not in visited

    def test_schema_step_path(self):
        """Test path through schema instantiation state machine."""
        state = StepState.CREATED
        visited = [state]

        while state in SCHEMA_TRANSITIONS:
            state = SCHEMA_TRANSITIONS[state]
            visited.append(state)

        # Should end at STATEMENT_COMPLETE
        assert visited[-1] == StepState.STATEMENT_COMPLETE

        # Should only have: CREATED -> FACET_INIT_BEGIN -> FACET_INIT_END -> STATEMENT_END -> COMPLETE
        assert len(visited) == 5
        assert StepState.FACET_INIT_BEGIN in visited
        assert StepState.FACET_INIT_END in visited
        assert StepState.STATEMENT_END in visited

        # Should skip all other phases
        assert StepState.FACET_SCRIPTS_BEGIN not in visited
        assert StepState.MIXIN_BLOCKS_BEGIN not in visited
        assert StepState.EVENT_TRANSMIT not in visited
        assert StepState.STATEMENT_BLOCKS_BEGIN not in visited


class TestSelectTransitions:
    """Tests for transition table selection."""

    def test_variable_assignment_uses_step(self):
        """Test VariableAssignment uses full state machine."""
        transitions = select_transitions(ObjectType.VARIABLE_ASSIGNMENT)
        assert transitions == STEP_TRANSITIONS

    def test_yield_assignment_uses_yield(self):
        """Test YieldAssignment uses yield state machine."""
        transitions = select_transitions(ObjectType.YIELD_ASSIGNMENT)
        assert transitions == YIELD_TRANSITIONS

    def test_and_then_uses_block(self):
        """Test AndThen uses block state machine."""
        transitions = select_transitions(ObjectType.AND_THEN)
        assert transitions == BLOCK_TRANSITIONS

    def test_workflow_uses_step(self):
        """Test Workflow uses full state machine."""
        transitions = select_transitions(ObjectType.WORKFLOW)
        assert transitions == STEP_TRANSITIONS

    def test_schema_instantiation_uses_schema(self):
        """Test SchemaInstantiation uses schema state machine."""
        transitions = select_transitions(ObjectType.SCHEMA_INSTANTIATION)
        assert transitions == SCHEMA_TRANSITIONS


class TestGetNextState:
    """Tests for get_next_state function."""

    def test_returns_next_state(self):
        """Test getting next state."""
        next_state = get_next_state(StepState.CREATED, STEP_TRANSITIONS)
        assert next_state == StepState.FACET_INIT_BEGIN

    def test_returns_none_for_terminal(self):
        """Test returning None at terminal state."""
        next_state = get_next_state(StepState.STATEMENT_COMPLETE, STEP_TRANSITIONS)
        assert next_state is None

    def test_returns_none_for_unknown(self):
        """Test returning None for unknown state."""
        next_state = get_next_state("unknown.state", STEP_TRANSITIONS)
        assert next_state is None
