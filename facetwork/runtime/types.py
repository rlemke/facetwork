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


# Stable namespace for deriving deterministic, block-scoped step ids.
_STEP_ID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def deterministic_step_id(
    workflow_id: str,
    block_id: str | None,
    statement_id: str | None,
    container_id: str | None,
) -> StepId:
    """Derive a deterministic StepId for a block-scoped child step.

    Two runners that concurrently create the *same* logical step (same
    ``workflow_id``/``block_id``/``statement_id``/``container_id``) produce the
    **same** id, so the unique ``uuid`` index — and the
    ``(statement_id, block_id, container_id)`` dedup index — collapse the
    duplicates instead of letting losers survive with fresh random ids.

    This mirrors the dedup index key (plus ``workflow_id`` so re-runs with a
    fresh execution workflow id never collide with a prior run's steps).
    """
    key = f"{workflow_id}|{container_id}|{block_id}|{statement_id}"
    return StepId(str(uuid.uuid5(_STEP_ID_NAMESPACE, key)))


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
    # Inline diagnostic statements. They live in the block alongside
    # step assignments but skip the full STEP_TRANSITIONS lifecycle:
    # a tiny SYS_TRANSITIONS table walks them through a single
    # execution step then to STATEMENT_COMPLETE (or STATEMENT_ERROR
    # for a failing sys.assert).
    SYS_LOG = "SysLog"
    SYS_ASSERT = "SysAssert"

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


# Step-state-machine / continuation runtime version stamped on persisted steps.
# Bump ONLY for a step-state change an older runner cannot safely process (a
# new/renamed StepState, a changed step-doc shape, or changed continuation
# semantics). is_runtime_compatible() then makes mixed-version runners refuse
# each other's steps, so an incompatible engine change is cut over by draining
# rather than rolled blindly. Backward-compatible changes leave it unchanged, so
# the gate stays a no-op (every step today carries this same value).
# See docs/architecture/ffl-runner-orchestration-tier.md §3.3.
STEP_RUNTIME_VERSION = "0.1.0"


@dataclass
class VersionInfo:
    """Version information for persisted artifacts."""

    workflow_version: str = "1.0"
    step_schema_version: str = "1.0"
    runtime_version: str = STEP_RUNTIME_VERSION
    sequence: int = 0  # Monotonic version for optimistic concurrency

    def increment(self) -> None:
        """Bump the optimistic concurrency counter."""
        self.sequence += 1


def is_runtime_compatible(step_runtime_version: str | None) -> bool:
    """Whether a step stamped with ``step_runtime_version`` may be processed by
    this runtime build.

    Compatible iff it equals this build's ``STEP_RUNTIME_VERSION``; an
    empty/unknown value is permissive (legacy steps are never stranded). Today
    every step carries ``STEP_RUNTIME_VERSION`` so this always returns True — it
    only starts gating once the constant is bumped for an incompatible change.
    """
    if not step_runtime_version:
        return True
    return step_runtime_version == STEP_RUNTIME_VERSION


@dataclass
class StepReference:
    """Reference to a step passed as a parameter value.

    Created when an FFL argument is a bare step name (`ds = s1`). Carries
    enough information for both the evaluator (resolving `$.ds.field` lazily)
    and handlers (calling `fetch_step(ref)` via the agent SDK) to retrieve
    the referenced step's data.

    Read-only by convention — `fetch_step()` returns a dict snapshot; there
    is no write API for the referenced step.
    """

    step_id: str
    workflow_id: str
    facet_name: str

    REF_TAG = "_facet_ref"

    def to_json(self) -> dict:
        """Serialize for handler payloads."""
        return {
            self.REF_TAG: True,
            "step_id": self.step_id,
            "workflow_id": self.workflow_id,
            "facet_name": self.facet_name,
        }

    @classmethod
    def from_json(cls, data: dict) -> "StepReference":
        """Deserialize from a tagged JSON dict."""
        if not isinstance(data, dict) or not data.get(cls.REF_TAG):
            raise ValueError(f"Not a step reference: {data!r}")
        return cls(
            step_id=data["step_id"],
            workflow_id=data["workflow_id"],
            facet_name=data["facet_name"],
        )

    @classmethod
    def is_ref(cls, value: object) -> bool:
        """True if the value is a StepReference or its tagged JSON form."""
        if isinstance(value, cls):
            return True
        return isinstance(value, dict) and bool(value.get(cls.REF_TAG))


def serialize_attribute_value(value: object) -> object:
    """Convert StepReference instances to tagged JSON for storage/transport.

    Recurses into lists, tuples, and dicts so a StepReference nested in
    a collection is also serialized.
    """
    if isinstance(value, StepReference):
        return value.to_json()
    if isinstance(value, list):
        return [serialize_attribute_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(serialize_attribute_value(v) for v in value)
    if isinstance(value, dict):
        # Don't touch dicts that are already tagged refs — they're valid as-is.
        if value.get(StepReference.REF_TAG):
            return value
        return {k: serialize_attribute_value(v) for k, v in value.items()}
    return value


def deserialize_attribute_value(value: object) -> object:
    """Inverse of serialize_attribute_value — restores StepReference dataclasses.

    Used when loading persisted step attributes so the evaluator's
    isinstance(value, StepReference) checks fire correctly.
    """
    if isinstance(value, dict) and value.get(StepReference.REF_TAG):
        return StepReference.from_json(value)
    if isinstance(value, list):
        return [deserialize_attribute_value(v) for v in value]
    if isinstance(value, dict):
        return {k: deserialize_attribute_value(v) for k, v in value.items()}
    return value


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
