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

"""AFL runtime error types."""

from dataclasses import dataclass

from .types import BlockId, StepId


class RuntimeError(Exception):
    """Base class for all FFL runtime errors."""

    pass


@dataclass
class InvalidStepStateError(RuntimeError):
    """Raised when a step is in an invalid state for an operation."""

    step_id: StepId
    current_state: str
    expected_states: list[str]

    def __str__(self) -> str:
        return (
            f"Step {self.step_id} is in state '{self.current_state}', "
            f"expected one of: {self.expected_states}"
        )


@dataclass
class StepNotFoundError(RuntimeError):
    """Raised when a step cannot be found."""

    step_id: StepId

    def __str__(self) -> str:
        return f"Step not found: {self.step_id}"


@dataclass
class BlockNotFoundError(RuntimeError):
    """Raised when a block cannot be found."""

    block_id: BlockId

    def __str__(self) -> str:
        return f"Block not found: {self.block_id}"


@dataclass
class DependencyNotSatisfiedError(RuntimeError):
    """Raised when a step's dependencies are not satisfied."""

    step_id: StepId
    missing_dependencies: list[StepId]

    def __str__(self) -> str:
        deps = ", ".join(str(d) for d in self.missing_dependencies)
        return f"Step {self.step_id} has unsatisfied dependencies: {deps}"


@dataclass
class EvaluationError(RuntimeError):
    """Raised when expression evaluation fails."""

    expression: str
    message: str
    step_id: StepId | None = None

    def __str__(self) -> str:
        loc = f" in step {self.step_id}" if self.step_id else ""
        return f"Evaluation error{loc}: {self.message} (expression: {self.expression})"


@dataclass
class ReferenceError(RuntimeError):
    """Raised when a reference cannot be resolved."""

    reference: str
    message: str
    step_id: StepId | None = None

    def __str__(self) -> str:
        loc = f" in step {self.step_id}" if self.step_id else ""
        return f"Reference error{loc}: {self.message} (reference: {self.reference})"


@dataclass
class InvalidTransitionError(RuntimeError):
    """Raised when an invalid state transition is attempted."""

    step_id: StepId
    from_state: str
    to_state: str

    def __str__(self) -> str:
        return (
            f"Invalid transition for step {self.step_id}: "
            f"cannot transition from '{self.from_state}' to '{self.to_state}'"
        )


@dataclass
class ConcurrencyError(RuntimeError):
    """Raised when a concurrency conflict is detected."""

    step_id: StepId
    message: str

    def __str__(self) -> str:
        return f"Concurrency error for step {self.step_id}: {self.message}"


@dataclass
class VersionMismatchError(RuntimeError):
    """Raised when version incompatibility is detected."""

    expected_version: str
    actual_version: str
    component: str

    def __str__(self) -> str:
        return (
            f"Version mismatch for {self.component}: "
            f"expected {self.expected_version}, got {self.actual_version}"
        )


@dataclass
class TokenBudgetExceededError(RuntimeError):
    """Raised when cumulative token usage exceeds the configured budget."""

    budget: int
    used: int
    step_id: StepId | None = None

    def __str__(self) -> str:
        loc = f" at step {self.step_id}" if self.step_id else ""
        return f"Token budget exceeded{loc}: used {self.used} of {self.budget} tokens"
