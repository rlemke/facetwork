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

"""AFL step definition and transition management."""

import copy
from dataclasses import dataclass, field
from typing import Any

from .states import StepState, select_transitions
from .types import (
    BlockId,
    FacetAttributes,
    ObjectType,
    StepId,
    VersionInfo,
    step_id,
)


@dataclass
class StepTransition:
    """Manages state transition control for a step.

    Controls when and how a step transitions between states:
    - request_transition: Triggers selectState() to advance
    - push_me: Re-queues step for continued processing
    - changed: Marks step as modified for persistence
    """

    original_state: str
    current_state: str
    changed: bool = False
    request_transition: bool = False
    push_me: bool = False
    error: Exception | None = None

    @classmethod
    def initial(cls) -> "StepTransition":
        """Create initial transition state."""
        return cls(
            original_state=StepState.CREATED,
            current_state=StepState.CREATED,
            request_transition=True,  # Start by requesting first transition
        )

    def request_state_change(self, request: bool = True) -> None:
        """Request a state change on next iteration."""
        self.request_transition = request
        if request:
            self.changed = True

    def change_and_transition(self) -> None:
        """Mark as changed and request transition."""
        self.changed = True
        self.request_transition = True

    def set_push_me(self, push: bool) -> None:
        """Set whether to re-queue this step for continued processing."""
        self.push_me = push

    def set_error(self, error: Exception) -> None:
        """Set an error state."""
        self.error = error
        self.changed = True

    def clear_error(self) -> None:
        """Clear any error state."""
        self.error = None

    @property
    def is_requesting_state_change(self) -> bool:
        """Check if step is requesting a state change."""
        return self.request_transition

    @property
    def is_requesting_push(self) -> bool:
        """Check if step is requesting to be pushed again."""
        return self.push_me

    @property
    def has_error(self) -> bool:
        """Check if step has an error."""
        return self.error is not None

    def reset_for_iteration(self) -> None:
        """Reset flags for a new iteration."""
        self.push_me = False

    def commit(self) -> None:
        """Commit the state change."""
        self.original_state = self.current_state
        self.changed = False
        self.request_transition = False


@dataclass
class StepDefinition:
    """Persistent step definition representing a runtime step instance.

    Each step is an execution instance of a statement from the compiled AST.
    Steps progress through states via the state machine.
    """

    # Identification
    id: StepId
    object_type: str  # ObjectType constant

    # Hierarchy
    workflow_id: str  # WorkflowId (str-based NewType)
    statement_id: str | None = None  # StatementId
    statement_name: str = ""  # Human-readable name (e.g. "s1" from "s1 = Facet()")
    container_type: str | None = None  # ObjectType of container
    container_id: StepId | None = None  # Step ID of containing step
    block_id: StepId | BlockId | None = None  # Block containing this step
    root_id: StepId | None = None  # Root step in the flow

    # State machine
    state: str = field(default=StepState.CREATED)
    transition: StepTransition = field(default_factory=StepTransition.initial)

    # Data
    facet_name: str = ""  # Name of the facet being invoked
    attributes: FacetAttributes = field(default_factory=FacetAttributes)

    # Versioning
    version: VersionInfo = field(default_factory=VersionInfo)

    # Foreach iteration binding
    foreach_var: str | None = None
    foreach_value: Any = None

    # Metadata
    timestamp: str | None = None  # Last update timestamp
    start_time: int = 0  # Creation timestamp (ms epoch)
    last_modified: int = 0  # Last update timestamp (ms epoch)

    @classmethod
    def create(
        cls,
        workflow_id: str,
        object_type: str,
        facet_name: str = "",
        statement_id: str | None = None,
        statement_name: str = "",
        container_id: StepId | None = None,
        container_type: str | None = None,
        block_id: StepId | BlockId | None = None,
        root_id: StepId | None = None,
    ) -> "StepDefinition":
        """Create a new step in CREATED state.

        Args:
            workflow_id: Parent workflow ID
            object_type: Type of step (VariableAssignment, YieldAssignment, etc.)
            facet_name: Name of the facet being invoked
            statement_id: Link to statement definition
            statement_name: Human-readable statement name (e.g. "s1")
            container_id: Step containing this step
            container_type: Type of container
            block_id: Block containing this step
            root_id: Root step in the flow

        Returns:
            New StepDefinition in CREATED state
        """
        return cls(
            id=step_id(),
            object_type=object_type,
            workflow_id=workflow_id,
            statement_id=statement_id,
            statement_name=statement_name,
            container_id=container_id,
            container_type=container_type,
            block_id=block_id,
            root_id=root_id,
            facet_name=facet_name,
        )

    @property
    def current_state(self) -> str:
        """Get current state."""
        return self.state

    @property
    def is_complete(self) -> bool:
        """Check if step is complete."""
        return StepState.is_complete(self.state)

    @property
    def is_error(self) -> bool:
        """Check if step is in error state."""
        return StepState.is_error(self.state)

    @property
    def is_terminal(self) -> bool:
        """Check if step is in terminal state."""
        return StepState.is_terminal(self.state)

    @property
    def is_block(self) -> bool:
        """Check if this is a block step."""
        return ObjectType.is_block(self.object_type)

    @property
    def is_statement(self) -> bool:
        """Check if this is a statement step."""
        return ObjectType.is_statement(self.object_type)

    @property
    def is_requesting_state_change(self) -> bool:
        """Check if step wants to transition."""
        return self.transition.is_requesting_state_change

    def change_state(self, new_state: str) -> None:
        """Change to a new state.

        Args:
            new_state: The state to transition to
        """
        self.state = new_state
        self.transition.current_state = new_state
        self.transition.changed = True

    def request_state_change(self, request: bool = True) -> None:
        """Request a state change."""
        self.transition.request_state_change(request)

    def mark_error(self, error: Exception | None = None) -> None:
        """Mark step as errored."""
        self.state = StepState.STATEMENT_ERROR
        self.transition.current_state = StepState.STATEMENT_ERROR
        if error:
            self.transition.set_error(error)

    def mark_completed(self) -> None:
        """Mark step as completed."""
        self.state = StepState.STATEMENT_COMPLETE
        self.transition.current_state = StepState.STATEMENT_COMPLETE
        self.transition.request_transition = False

    def select_next_state(self) -> str | None:
        """Select the next state based on object type.

        Returns:
            The next state, or None if at terminal
        """
        transitions = select_transitions(self.object_type)
        return transitions.get(self.state)

    def clone(self) -> "StepDefinition":
        """Create a deep copy of this step."""
        return copy.deepcopy(self)

    def get_attribute(self, name: str) -> Any:
        """Get an attribute value (param or return).

        Args:
            name: Attribute name

        Returns:
            The attribute value, or None if not found
        """
        # Check returns first (for step references)
        value = self.attributes.get_return(name)
        if value is not None:
            return value
        # Then check params
        return self.attributes.get_param(name)

    def set_attribute(self, name: str, value: Any, is_return: bool = False) -> None:
        """Set an attribute value.

        Args:
            name: Attribute name
            value: The value to set
            is_return: True if this is a return attribute
        """
        if is_return:
            self.attributes.set_return(name, value)
        else:
            self.attributes.set_param(name, value)
