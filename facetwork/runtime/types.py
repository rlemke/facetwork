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

"""AFL runtime core type definitions."""

import uuid
from dataclasses import dataclass, field
from typing import NewType

# Type aliases for IDs
StepId = NewType("StepId", str)
BlockId = NewType("BlockId", str)
WorkflowId = NewType("WorkflowId", str)
StatementId = NewType("StatementId", str)


def generate_id() -> str:
    """Generate a unique ID."""
    return str(uuid.uuid4())


def step_id() -> StepId:
    """Generate a new StepId."""
    return StepId(generate_id())


def block_id() -> BlockId:
    """Generate a new BlockId."""
    return BlockId(generate_id())


def workflow_id() -> WorkflowId:
    """Generate a new WorkflowId."""
    return WorkflowId(generate_id())


class ObjectType:
    """Object type constants for step classification.

    Determines which StateChanger is used for execution:
    - VariableAssignment: Full state machine (StepStateChanger)
    - YieldAssignment: Minimal state machine (YieldStateChanger)
    - AndThen/Block: Block state machine (BlockStateChanger)
    - Workflow: Entry point, uses full state machine
    - SchemaInstantiation: Simplified state machine for schema instantiation
    """

    VARIABLE_ASSIGNMENT = "VariableAssignment"
    YIELD_ASSIGNMENT = "YieldAssignment"
    WORKFLOW = "Workflow"
    FACET = "Facet"
    SCHEMA_INSTANTIATION = "SchemaInstantiation"

    # Block types
    AND_THEN = "AndThen"
    AND_MAP = "AndMap"
    AND_WHEN = "AndWhen"
    AND_CATCH = "AndCatch"
    BLOCK = "Block"

    # Mixin hooks
    BEFORE = "Before"
    AFTER = "After"

    @classmethod
    def is_block(cls, object_type: str) -> bool:
        """Check if object type is a block type."""
        return object_type in (cls.AND_THEN, cls.AND_MAP, cls.AND_WHEN, cls.AND_CATCH, cls.BLOCK)

    @classmethod
    def is_statement(cls, object_type: str) -> bool:
        """Check if object type is a statement type."""
        return object_type in (cls.VARIABLE_ASSIGNMENT, cls.YIELD_ASSIGNMENT)


@dataclass
class VersionInfo:
    """Version information for persisted artifacts."""

    workflow_version: str = "1.0"
    step_schema_version: str = "1.0"
    runtime_version: str = "0.1.0"
    sequence: int = 0  # Monotonic version for optimistic concurrency

    def increment(self) -> None:
        """Bump the optimistic concurrency counter."""
        self.sequence += 1


@dataclass
class AttributeValue:
    """A computed attribute value.

    Represents a value that has been evaluated from an expression.
    """

    name: str
    value: object
    type_hint: str = "Any"

    def __post_init__(self):
        """Infer type hint from value if not provided."""
        if self.type_hint == "Any" and self.value is not None:
            if isinstance(self.value, bool):
                self.type_hint = "Boolean"
            elif isinstance(self.value, int):
                self.type_hint = "Long"
            elif isinstance(self.value, float):
                self.type_hint = "Double"
            elif isinstance(self.value, str):
                self.type_hint = "String"
            elif isinstance(self.value, list):
                self.type_hint = "List"
            elif isinstance(self.value, dict):
                self.type_hint = "Map"


@dataclass
class FacetAttributes:
    """Computed attributes for a facet instance.

    Contains both input parameters and return values.
    """

    params: dict[str, AttributeValue] = field(default_factory=dict)
    returns: dict[str, AttributeValue] = field(default_factory=dict)

    def get_param(self, name: str) -> object:
        """Get a parameter value by name."""
        attr = self.params.get(name)
        return attr.value if attr else None

    def get_return(self, name: str) -> object:
        """Get a return value by name."""
        attr = self.returns.get(name)
        return attr.value if attr else None

    def set_param(self, name: str, value: object, type_hint: str = "Any") -> None:
        """Set a parameter value."""
        self.params[name] = AttributeValue(name, value, type_hint)

    def set_return(self, name: str, value: object, type_hint: str = "Any") -> None:
        """Set a return value."""
        self.returns[name] = AttributeValue(name, value, type_hint)

    def merge(self, other: "FacetAttributes") -> None:
        """Merge another FacetAttributes into this one.

        Used for yield capture to merge results.
        """
        for name, attr in other.params.items():
            if name not in self.params:
                self.params[name] = attr
        for name, attr in other.returns.items():
            self.returns[name] = attr
