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

"""DAO protocol definitions for MongoDB collections.

These protocols define the data access interfaces for each collection.
Implementations can be in-memory (for testing) or MongoDB-backed.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .entities import (
    FlowDefinition,
    LogDefinition,
    RunnerDefinition,
    ServerDefinition,
    TaskDefinition,
    WorkflowDefinition,
)
from .step import StepDefinition


@runtime_checkable
class FlowDefinitionDAO(Protocol):
    """Data access for flows collection."""

    def get_by_id(self, uuid: str) -> FlowDefinition | None:
        """Get flow by UUID."""
        ...

    def get_by_path(self, path: str) -> FlowDefinition | None:
        """Get flow by path."""
        ...

    def get_by_name(self, name: str) -> FlowDefinition | None:
        """Get flow by name."""
        ...

    def save(self, flow: FlowDefinition) -> None:
        """Save or update a flow."""
        ...

    def delete(self, uuid: str) -> bool:
        """Delete a flow by UUID. Returns True if deleted."""
        ...


@runtime_checkable
class WorkflowDefinitionDAO(Protocol):
    """Data access for workflows collection."""

    def get_by_id(self, uuid: str) -> WorkflowDefinition | None:
        """Get workflow by UUID."""
        ...

    def get_by_name(self, name: str) -> WorkflowDefinition | None:
        """Get workflow by name."""
        ...

    def get_by_flow(self, flow_id: str) -> Sequence[WorkflowDefinition]:
        """Get all workflows for a flow."""
        ...

    def save(self, workflow: WorkflowDefinition) -> None:
        """Save or update a workflow."""
        ...


@runtime_checkable
class RunnerDefinitionDAO(Protocol):
    """Data access for runners collection."""

    def get_by_id(self, uuid: str) -> RunnerDefinition | None:
        """Get runner by UUID."""
        ...

    def get_by_workflow(self, workflow_id: str) -> Sequence[RunnerDefinition]:
        """Get all runners for a workflow."""
        ...

    def get_by_state(self, state: str) -> Sequence[RunnerDefinition]:
        """Get runners by state."""
        ...

    def save(self, runner: RunnerDefinition) -> None:
        """Save or update a runner."""
        ...

    def update_state(self, uuid: str, state: str) -> None:
        """Update runner state."""
        ...


@runtime_checkable
class StepDefinitionDAO(Protocol):
    """Data access for steps collection."""

    def get_by_id(self, uuid: str) -> StepDefinition | None:
        """Get step by UUID."""
        ...

    def get_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Get all steps for a workflow."""
        ...

    def get_by_runner(self, runner_id: str) -> Sequence[StepDefinition]:
        """Get all steps for a runner."""
        ...

    def get_by_state(self, state: str) -> Sequence[StepDefinition]:
        """Get steps by state."""
        ...

    def save(self, step: StepDefinition) -> None:
        """Save or update a step."""
        ...


@runtime_checkable
class TaskDefinitionDAO(Protocol):
    """Data access for tasks collection."""

    def get_by_id(self, uuid: str) -> TaskDefinition | None:
        """Get task by UUID."""
        ...

    def get_pending(self, task_list: str) -> Sequence[TaskDefinition]:
        """Get pending tasks for a task list."""
        ...

    def get_by_runner(self, runner_id: str) -> Sequence[TaskDefinition]:
        """Get all tasks for a runner."""
        ...

    def get_by_step(self, step_id: str) -> Sequence[TaskDefinition]:
        """Get all tasks for a step."""
        ...

    def save(self, task: TaskDefinition) -> None:
        """Save or update a task."""
        ...

    def update_state(self, uuid: str, state: str) -> None:
        """Update task state."""
        ...


@runtime_checkable
class LogDefinitionDAO(Protocol):
    """Data access for logs collection."""

    def get_by_runner(self, runner_id: str) -> Sequence[LogDefinition]:
        """Get logs for a runner."""
        ...

    def get_by_step(self, step_id: str) -> Sequence[LogDefinition]:
        """Get logs for a step."""
        ...

    def save(self, log: LogDefinition) -> None:
        """Save a log entry."""
        ...


@runtime_checkable
class ServerDefinitionDAO(Protocol):
    """Data access for servers collection."""

    def get_by_id(self, uuid: str) -> ServerDefinition | None:
        """Get server by UUID."""
        ...

    def get_by_state(self, state: str) -> Sequence[ServerDefinition]:
        """Get servers by state."""
        ...

    def get_all(self) -> Sequence[ServerDefinition]:
        """Get all servers."""
        ...

    def save(self, server: ServerDefinition) -> None:
        """Save or update a server."""
        ...

    def update_ping(self, uuid: str, ping_time: int) -> None:
        """Update server ping time."""
        ...


@runtime_checkable
class DataServices(Protocol):
    """Protocol providing access to all DAOs.

    This is the main entry point for data access in the runtime.
    """

    @property
    def step(self) -> StepDefinitionDAO:
        """Step execution records."""
        ...

    @property
    def flow(self) -> FlowDefinitionDAO:
        """Flow definitions."""
        ...

    @property
    def workflow(self) -> WorkflowDefinitionDAO:
        """Workflow definitions."""
        ...

    @property
    def runner(self) -> RunnerDefinitionDAO:
        """Execution instances."""
        ...

    @property
    def task(self) -> TaskDefinitionDAO:
        """Task queue."""
        ...

    @property
    def log(self) -> LogDefinitionDAO:
        """Audit logs."""
        ...

    @property
    def server(self) -> ServerDefinitionDAO:
        """Server registration."""
        ...
