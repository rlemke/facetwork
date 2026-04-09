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

"""Tests for FFL runtime step definition."""

from facetwork.runtime import (
    ObjectType,
    StepDefinition,
    StepState,
    StepTransition,
    workflow_id,
)


class TestStepTransition:
    """Tests for StepTransition control."""

    def test_initial_state(self):
        """Test initial transition state."""
        transition = StepTransition.initial()
        assert transition.original_state == StepState.CREATED
        assert transition.current_state == StepState.CREATED
        assert transition.request_transition is True
        assert transition.changed is False
        assert transition.push_me is False
        assert transition.error is None

    def test_request_state_change(self):
        """Test requesting state change."""
        transition = StepTransition.initial()
        transition.request_state_change(True)
        assert transition.is_requesting_state_change is True
        assert transition.changed is True

    def test_push_me(self):
        """Test push_me flag."""
        transition = StepTransition.initial()
        transition.set_push_me(True)
        assert transition.is_requesting_push is True

    def test_error_handling(self):
        """Test error state."""
        transition = StepTransition.initial()
        error = Exception("test error")
        transition.set_error(error)
        assert transition.has_error is True
        assert transition.error == error
        assert transition.changed is True

    def test_commit(self):
        """Test committing changes."""
        transition = StepTransition.initial()
        transition.current_state = StepState.FACET_INIT_BEGIN
        transition.changed = True
        transition.request_transition = True

        transition.commit()

        assert transition.original_state == StepState.FACET_INIT_BEGIN
        assert transition.changed is False
        assert transition.request_transition is False


class TestStepDefinition:
    """Tests for StepDefinition."""

    def test_create_step(self):
        """Test creating a new step."""
        wf_id = workflow_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="TestFacet",
        )

        assert step.id is not None
        assert step.workflow_id == wf_id
        assert step.object_type == ObjectType.VARIABLE_ASSIGNMENT
        assert step.facet_name == "TestFacet"
        assert step.state == StepState.CREATED
        assert not step.is_complete
        assert not step.is_error

    def test_state_changes(self):
        """Test changing step state."""
        step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )

        step.change_state(StepState.FACET_INIT_BEGIN)
        assert step.state == StepState.FACET_INIT_BEGIN
        assert step.transition.changed is True

    def test_mark_completed(self):
        """Test marking step complete."""
        step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )

        step.mark_completed()
        assert step.is_complete is True
        assert step.state == StepState.STATEMENT_COMPLETE

    def test_mark_error(self):
        """Test marking step as error."""
        step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )

        error = Exception("test error")
        step.mark_error(error)
        assert step.is_error is True
        assert step.state == StepState.STATEMENT_ERROR
        assert step.transition.error == error

    def test_attributes(self):
        """Test attribute management."""
        step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )

        step.set_attribute("input", 42)
        step.set_attribute("output", 100, is_return=True)

        assert step.get_attribute("input") == 42
        assert step.get_attribute("output") == 100

    def test_is_block(self):
        """Test block detection."""
        block_step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.AND_THEN,
        )
        assert block_step.is_block is True

        regular_step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        assert regular_step.is_block is False

    def test_clone(self):
        """Test step cloning."""
        step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )
        step.set_attribute("value", 42)

        clone = step.clone()
        assert clone.id == step.id
        assert clone is not step
        assert clone.get_attribute("value") == 42

    def test_select_next_state(self):
        """Test state selection."""
        step = StepDefinition.create(
            workflow_id=workflow_id(),
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
        )

        next_state = step.select_next_state()
        assert next_state == StepState.FACET_INIT_BEGIN
