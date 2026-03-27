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

"""MongoDB implementation of PersistenceAPI.

This module provides a MongoDB-backed persistence layer for the AFL runtime.
It requires the pymongo package to be installed.
"""

import logging
import os
import time
from collections.abc import Sequence
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, TypeVar

try:
    from pymongo import ASCENDING, MongoClient, ReturnDocument
    from pymongo.collection import Collection  # noqa: F401
    from pymongo.database import Database
    from pymongo.errors import DuplicateKeyError

    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False

    # Fallback constants so MongoStore works with mongomock without pymongo
    ASCENDING = 1

    try:
        from mongomock.collection import (  # type: ignore[no-redef]
            DuplicateKeyError,
            ReturnDocument,
        )
    except ImportError:

        class ReturnDocument:  # type: ignore[no-redef]
            AFTER = True
            BEFORE = False

        class DuplicateKeyError(Exception):  # type: ignore[no-redef]
            pass


if TYPE_CHECKING:
    from ..config import MongoDBConfig

from .entities import (
    FlowDefinition,
    FlowIdentity,
    HandledCount,
    HandlerRegistration,
    LogDefinition,
    Parameter,
    PublishedSource,
    RunnerDefinition,
    ServerDefinition,
    StepLogEntry,
    TaskDefinition,
    WorkflowDefinition,
)
from .persistence import IterationChanges, PersistenceAPI
from .states import StepState
from .step import StepDefinition
from .types import BlockId, StepId, VersionInfo, WorkflowId

T = TypeVar("T")
logger = logging.getLogger(__name__)


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


class MongoStore(PersistenceAPI):
    """MongoDB implementation of the persistence API.

    Provides full persistence to MongoDB with proper indexes,
    transactions, and serialization.

    Usage:
        store = MongoStore("mongodb://afl-mongodb:27017", "afl")
        store.get_step(step_id)

        # Or create from an AFLConfig / MongoDBConfig:
        from afl.config import load_config
        config = load_config()
        store = MongoStore.from_config(config.mongodb)
    """

    def __init__(
        self,
        connection_string: str = "",
        database_name: str = "afl",
        create_indexes: bool = True,
        client: Any = None,
    ):
        """Initialize the MongoDB store.

        Args:
            connection_string: MongoDB connection string
            database_name: Database name (default: "afl")
            create_indexes: Whether to create indexes on initialization
            client: Optional pre-built MongoClient (e.g. mongomock.MongoClient for testing)
        """
        if client is not None:
            self._client: Any = client
        else:
            if not PYMONGO_AVAILABLE:
                raise ImportError(
                    "pymongo is required for MongoStore. Install it with: pip install pymongo"
                )
            self._client = MongoClient(connection_string)

        self._db: Database = self._client[database_name]

        if create_indexes:
            self._ensure_indexes()

    @classmethod
    def from_config(
        cls,
        config: "MongoDBConfig",
        create_indexes: bool = True,
    ) -> "MongoStore":
        """Create a MongoStore from a MongoDBConfig instance.

        Args:
            config: MongoDB configuration with url, database, etc.
            create_indexes: Whether to create indexes on initialization

        Returns:
            A configured MongoStore instance.
        """
        return cls(
            connection_string=config.connection_string(),
            database_name=config.database,
            create_indexes=create_indexes,
        )

    def _ensure_indexes(self) -> None:
        """Create indexes on all collections."""
        # Steps collection
        steps = self._db.steps
        steps.create_index("uuid", unique=True, name="step_uuid_index")
        steps.create_index("workflow_id", name="step_workflow_id_index")
        steps.create_index("block_id", name="step_block_id_index")
        steps.create_index("container_id", name="step_container_id_index")
        steps.create_index("state", name="step_state_index")
        steps.create_index(
            [("statement_id", 1), ("block_id", 1), ("container_id", 1)],
            unique=True,
            partialFilterExpression={"statement_id": {"$type": "string"}},
            name="step_dedup_index",
        )

        # Flows collection
        flows = self._db.flows
        flows.create_index("uuid", unique=True, name="flow_uuid_index")
        flows.create_index("name.path", name="flow_path_index")
        flows.create_index("name.name", name="flow_name_index")

        # Workflows collection
        workflows = self._db.workflows
        workflows.create_index("uuid", unique=True, name="workflow_uuid_index")
        workflows.create_index("name", name="workflow_name_index")
        workflows.create_index("flow_id", name="workflow_flow_id_index")

        # Runners collection
        runners = self._db.runners
        runners.create_index("uuid", unique=True, name="runner_uuid_index")
        runners.create_index("workflow_id", name="runner_workflow_id_index")
        runners.create_index("state", name="runner_state_index")

        # Tasks collection
        tasks = self._db.tasks
        tasks.create_index("uuid", unique=True, name="task_uuid_index")
        tasks.create_index("runner_id", name="task_runner_id_index")
        tasks.create_index("step_id", name="task_step_id_index")
        tasks.create_index("task_list_name", name="task_list_name_index")
        tasks.create_index("state", name="task_state_index")
        tasks.create_index("name", name="task_name_index")
        # Partial unique index for running tasks
        tasks.create_index(
            "step_id",
            unique=True,
            partialFilterExpression={"state": "running"},
            name="task_step_id_running_unique_index",
        )
        # Compound index for efficient claim queries
        tasks.create_index(
            [("state", ASCENDING), ("name", ASCENDING), ("task_list_name", ASCENDING)],
            name="task_claim_index",
        )

        # Logs collection
        logs = self._db.logs
        logs.create_index("uuid", unique=True, name="log_uuid_index")
        logs.create_index("runner_id", name="log_runner_id_index")
        logs.create_index("object_id", name="log_object_id_index")

        # Servers collection
        servers = self._db.servers
        servers.create_index("uuid", unique=True, name="server_uuid_index")

        # Published sources collection
        sources = self._db.afl_sources
        sources.create_index("uuid", unique=True, name="source_uuid_index")
        sources.create_index(
            [("namespace_name", ASCENDING), ("version", ASCENDING)],
            unique=True,
            name="source_namespace_version_index",
        )
        sources.create_index("namespaces_defined", name="source_namespaces_defined_index")

        # Step logs collection
        step_logs = self._db.step_logs
        step_logs.create_index("uuid", unique=True, name="step_log_uuid_index")
        step_logs.create_index("step_id", name="step_log_step_id_index")
        step_logs.create_index("workflow_id", name="step_log_workflow_id_index")
        step_logs.create_index("facet_name", name="step_log_facet_name_index")

        # Handler registrations collection
        handler_regs = self._db.handler_registrations
        handler_regs.create_index("facet_name", unique=True, name="handler_reg_facet_name_index")

    # =========================================================================
    # Step Operations (PersistenceAPI)
    # =========================================================================

    def get_step(self, step_id: str) -> StepDefinition | None:
        """Fetch a step by ID."""
        doc = self._db.steps.find_one({"uuid": step_id})
        return self._doc_to_step(doc) if doc else None

    def get_steps_by_block(self, block_id: StepId | BlockId) -> Sequence[StepDefinition]:
        """Fetch all steps in a block."""
        docs = self._db.steps.find({"block_id": block_id})
        return [self._doc_to_step(doc) for doc in docs]

    def get_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps in a workflow."""
        docs = self._db.steps.find({"workflow_id": workflow_id})
        return [self._doc_to_step(doc) for doc in docs]

    def get_actionable_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch steps that need processing — DB-level filtering.

        Excludes terminal steps (Complete/Error) and EventTransmit steps
        that do not have ``request_transition`` set.
        """
        from .states import StepState

        docs = self._db.steps.find(
            {
                "workflow_id": workflow_id,
                "$nor": [
                    {"state": StepState.STATEMENT_COMPLETE},
                    {"state": StepState.STATEMENT_ERROR},
                    {"state": StepState.EVENT_TRANSMIT, "request_transition": {"$ne": True}},
                ],
            }
        )
        return [self._doc_to_step(doc) for doc in docs]

    def get_pending_resume_workflow_ids(self) -> list[str]:
        """Get workflow IDs with EventTransmit steps awaiting resume.

        Uses a MongoDB distinct query for efficiency.
        """
        return self._db.steps.distinct(
            "workflow_id",
            {"state": StepState.EVENT_TRANSMIT, "request_transition": True},
        )

    def get_steps_by_state(self, state: str) -> Sequence[StepDefinition]:
        """Fetch all steps in a given state."""
        docs = self._db.steps.find({"state": state})
        return [self._doc_to_step(doc) for doc in docs]

    def get_steps_by_container(self, container_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps with a given container."""
        docs = self._db.steps.find({"container_id": container_id})
        return [self._doc_to_step(doc) for doc in docs]

    def save_step(self, step: StepDefinition) -> None:
        """Save a step to the store."""
        now = _current_time_ms()
        if not step.start_time:
            step.start_time = now
        step.last_modified = now
        doc = self._step_to_doc(step)
        self._db.steps.replace_one({"uuid": step.id}, doc, upsert=True)

    def delete_steps(self, step_ids: Sequence[str]) -> int:
        """Delete steps by their UUIDs."""
        if not step_ids:
            return 0
        result = self._db.steps.delete_many({"uuid": {"$in": list(step_ids)}})
        return result.deleted_count

    def delete_tasks_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete tasks associated with the given step IDs."""
        if not step_ids:
            return 0
        result = self._db.tasks.delete_many({"step_id": {"$in": list(step_ids)}})
        return result.deleted_count

    def delete_step_logs_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete step log entries for the given step IDs."""
        if not step_ids:
            return 0
        result = self._db.step_logs.delete_many({"step_id": {"$in": list(step_ids)}})
        return result.deleted_count

    def get_blocks_by_step(self, step_id: str) -> Sequence[StepDefinition]:
        """Fetch all block steps for a containing step."""
        docs = self._db.steps.find(
            {
                "container_id": step_id,
                "object_type": {"$in": ["AndThen", "AndMap", "AndMatch", "Block"]},
            }
        )
        return [self._doc_to_step(doc) for doc in docs]

    def get_workflow_root(self, workflow_id: str) -> StepDefinition | None:
        """Get the root step of a workflow."""
        doc = self._db.steps.find_one(
            {"workflow_id": workflow_id, "root_id": None, "container_id": None}
        )
        return self._doc_to_step(doc) if doc else None

    def step_exists(self, statement_id: str, block_id: StepId | BlockId | None) -> bool:
        """Check if a step already exists for a statement in a block."""
        query: dict[str, str | None] = {"statement_id": statement_id}
        if block_id:
            query["block_id"] = block_id
        else:
            query["block_id"] = None
        return self._db.steps.count_documents(query, limit=1) > 0

    def block_step_exists(self, statement_id: str, container_id: StepId) -> bool:
        """Check if a block step already exists for a statement in a container."""
        return (
            self._db.steps.count_documents(
                {"statement_id": statement_id, "container_id": container_id}, limit=1
            )
            > 0
        )

    # =========================================================================
    # Atomic Commit (PersistenceAPI)
    # =========================================================================

    def commit(self, changes: IterationChanges) -> None:
        """Atomically commit all iteration changes.

        Uses a MongoDB transaction when the server supports it (replica set
        or mongos). Falls back to non-transactional writes for standalone
        servers and mongomock.
        """
        try:
            session = self._client.start_session()
        except (NotImplementedError, Exception):
            # mongomock raises NotImplementedError;
            # standalone servers may raise ConfigurationError
            self._commit_changes(changes, session=None)
            return

        try:
            with session:
                with session.start_transaction():
                    self._commit_changes(changes, session=session)
        except Exception as exc:
            # Standalone MongoDB raises OperationFailure (code 20) for
            # transactions. Fall back to non-transactional writes.
            exc_msg = str(exc).lower()
            if "transaction" in exc_msg or "replica set" in exc_msg:
                self._commit_changes(changes, session=None)
            else:
                raise

    def _commit_changes(self, changes: IterationChanges, session: Any = None) -> None:
        """Write all iteration changes to MongoDB."""
        kwargs: dict[str, Any] = {}
        if session is not None:
            kwargs["session"] = session

        skipped_step_ids: set[str] = set()

        for step in changes.created_steps:
            doc = self._step_to_doc(step)
            try:
                self._db.steps.insert_one(doc, **kwargs)
            except DuplicateKeyError:
                skipped_step_ids.add(str(step.id))
                logger.debug(
                    "Skipping duplicate step: statement_id=%s block_id=%s container_id=%s",
                    doc.get("statement_id"),
                    doc.get("block_id"),
                    doc.get("container_id"),
                )

        for step in changes.updated_steps:
            doc = self._step_to_doc(step)
            self._db.steps.replace_one({"uuid": step.id}, doc, **kwargs)

        for task in changes.created_tasks:
            if task.step_id and task.step_id in skipped_step_ids:
                logger.debug(
                    "Skipping orphan task: name=%s step_id=%s",
                    task.name,
                    task.step_id,
                )
                continue
            doc = self._task_to_doc(task)
            self._db.tasks.insert_one(doc, **kwargs)

    # =========================================================================
    # Runner Operations
    # =========================================================================

    def get_runner(self, runner_id: str) -> RunnerDefinition | None:
        """Get a runner by ID."""
        doc = self._db.runners.find_one({"uuid": runner_id})
        return self._doc_to_runner(doc) if doc else None

    def get_runners_by_workflow(self, workflow_id: str) -> Sequence[RunnerDefinition]:
        """Get all runners for a workflow."""
        docs = self._db.runners.find({"workflow_id": workflow_id})
        return [self._doc_to_runner(doc) for doc in docs]

    def get_runners_by_state(self, state: str) -> Sequence[RunnerDefinition]:
        """Get runners by state."""
        docs = self._db.runners.find({"state": state})
        return [self._doc_to_runner(doc) for doc in docs]

    def save_runner(self, runner: RunnerDefinition) -> None:
        """Save a runner."""
        doc = self._runner_to_doc(runner)
        self._db.runners.replace_one({"uuid": runner.uuid}, doc, upsert=True)

    def update_runner_state(self, runner_id: str, state: str) -> None:
        """Update runner state."""
        self._db.runners.update_one({"uuid": runner_id}, {"$set": {"state": state}})

    def get_all_runners(self, limit: int = 100) -> Sequence[RunnerDefinition]:
        """Get all runners, most recent first."""
        docs = self._db.runners.find().sort("start_time", -1).limit(limit)
        return [self._doc_to_runner(doc) for doc in docs]

    # =========================================================================
    # Workflow Operations (query helpers)
    # =========================================================================

    def get_all_workflows(self, limit: int = 100) -> Sequence[WorkflowDefinition]:
        """Get all workflows, most recently created first."""
        docs = self._db.workflows.find().sort("date", -1).limit(limit)
        return [self._doc_to_workflow(doc) for doc in docs]

    # =========================================================================
    # Task Operations
    # =========================================================================

    def get_task(self, task_id: str) -> TaskDefinition | None:
        """Get a task by ID."""
        doc = self._db.tasks.find_one({"uuid": task_id})
        return self._doc_to_task(doc) if doc else None

    def get_task_for_step(self, step_id: str) -> TaskDefinition | None:
        """Get the most recent task associated with a step."""
        doc = self._db.tasks.find_one(
            {"step_id": step_id},
            sort=[("created", -1)],
        )
        return self._doc_to_task(doc) if doc else None

    def get_pending_tasks(self, task_list: str) -> Sequence[TaskDefinition]:
        """Get pending tasks for a task list."""
        docs = self._db.tasks.find({"task_list_name": task_list, "state": "pending"})
        return [self._doc_to_task(doc) for doc in docs]

    def save_task(self, task: TaskDefinition) -> None:
        """Save a task."""
        doc = self._task_to_doc(task)
        self._db.tasks.replace_one({"uuid": task.uuid}, doc, upsert=True)

    # Default lease duration: 5 minutes. Handlers must renew via heartbeat.
    DEFAULT_LEASE_MS = 300_000

    def claim_task(
        self,
        task_names: list[str],
        task_list: str = "default",
        server_id: str = "",
    ) -> TaskDefinition | None:
        """Atomically claim a pending task matching one of the given names.

        Uses find_one_and_update for atomic PENDING → RUNNING transition.
        The partial unique index on (step_id, state=running) ensures only
        one agent processes an event per step.

        Also claims tasks whose lease has expired (i.e. still ``running``
        but ``lease_expires < now``), allowing automatic failover without
        relying solely on the orphan reaper.
        """
        now = _current_time_ms()
        lease_ms = int(os.environ.get("AFL_LEASE_DURATION_MS", str(self.DEFAULT_LEASE_MS)))
        update: dict[str, Any] = {
            "state": "running",
            "updated": now,
            "lease_expires": now + lease_ms,
        }
        if server_id:
            update["server_id"] = server_id

        # First try to claim a pending task
        doc = self._db.tasks.find_one_and_update(
            {
                "state": "pending",
                "name": {"$in": task_names},
                "task_list_name": task_list,
            },
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if doc:
            return self._doc_to_task(doc)

        # Then try to reclaim a running task whose lease has expired
        doc = self._db.tasks.find_one_and_update(
            {
                "state": "running",
                "name": {"$in": task_names},
                "task_list_name": task_list,
                "lease_expires": {"$lt": now, "$gt": 0},
            },
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        return self._doc_to_task(doc) if doc else None

    def update_task_state(self, task_id: str, state: str) -> None:
        """Update task state."""
        self._db.tasks.update_one(
            {"uuid": task_id}, {"$set": {"state": state, "updated": _current_time_ms()}}
        )

    def get_all_tasks(self, limit: int = 100) -> Sequence[TaskDefinition]:
        """Get all tasks, most recently created first."""
        docs = self._db.tasks.find().sort("created", -1).limit(limit)
        return [self._doc_to_task(doc) for doc in docs]

    def get_tasks_by_state(self, state: str) -> Sequence[TaskDefinition]:
        """Get tasks by state."""
        docs = self._db.tasks.find({"state": state}).sort("created", -1)
        return [self._doc_to_task(doc) for doc in docs]

    def get_tasks_by_runner(self, runner_id: str) -> Sequence[TaskDefinition]:
        """Get all tasks for a runner."""
        docs = self._db.tasks.find({"runner_id": runner_id})
        return [self._doc_to_task(doc) for doc in docs]

    def get_tasks_by_server_id(self, server_id: str, limit: int = 200) -> Sequence[TaskDefinition]:
        """Get tasks claimed by a specific server, most recent first."""
        docs = self._db.tasks.find({"server_id": server_id}).sort("updated", -1).limit(limit)
        return [self._doc_to_task(doc) for doc in docs]

    def reap_orphaned_tasks(self, down_timeout_ms: int = 300_000) -> list[dict[str, str]]:
        """Reset tasks whose claiming server is dead.

        A server is dead if its state is running/startup but its ping_time
        is older than *down_timeout_ms*.  Both running and pending tasks
        pinned to dead servers are reset — running tasks go back to PENDING,
        and pending tasks have their ``server_id`` cleared so any healthy
        runner can claim them.  Dead servers are also marked as ``shutdown``
        to prevent them from appearing as ghost runners.

        Returns a list of dicts describing each reaped task so callers can
        emit step logs.
        """
        now = _current_time_ms()
        cutoff = now - down_timeout_ms

        # Find servers that are effectively down
        dead_servers = list(
            self._db.servers.find(
                {
                    "state": {"$in": ["running", "startup"]},
                    "$or": [
                        {"ping_time": 0},
                        {"ping_time": {"$lt": cutoff}},
                    ],
                },
                {"uuid": 1, "ping_time": 1},
            )
        )
        dead_ids = [doc["uuid"] for doc in dead_servers]
        if not dead_ids:
            return []

        # Build server_id → last_ping lookup for diagnostics
        server_pings: dict[str, int] = {
            doc["uuid"]: doc.get("ping_time", 0) for doc in dead_servers
        }

        # Find running tasks whose server is dead AND whose task-level
        # heartbeat is also stale (or never set).  Tasks with a recent
        # task_heartbeat are still making progress even if the server
        # heartbeat is stale.
        heartbeat_cutoff = now - down_timeout_ms
        stale_heartbeat_filter = {
            "$or": [
                {"task_heartbeat": {"$exists": False}},
                {"task_heartbeat": 0},
                {"task_heartbeat": {"$lt": heartbeat_cutoff}},
            ],
        }
        orphan_cursor = self._db.tasks.find(
            {
                "state": "running",
                "server_id": {"$in": dead_ids},
                **stale_heartbeat_filter,
            },
            {"step_id": 1, "workflow_id": 1, "name": 1, "server_id": 1, "updated": 1},
        )
        reaped: list[dict[str, str]] = [
            {
                "step_id": doc.get("step_id", ""),
                "workflow_id": doc.get("workflow_id", ""),
                "name": doc.get("name", ""),
                "server_id": doc.get("server_id", ""),
                "task_started_ms": str(doc.get("updated", 0)),
                "last_ping_ms": str(server_pings.get(doc.get("server_id", ""), 0)),
            }
            for doc in orphan_cursor
        ]

        # Also find pending tasks pinned to dead servers — these are stuck
        # because only the (now-dead) server could claim them.
        pinned_cursor = self._db.tasks.find(
            {
                "state": "pending",
                "server_id": {"$in": dead_ids},
            },
            {"step_id": 1, "workflow_id": 1, "name": 1, "server_id": 1, "updated": 1},
        )
        for doc in pinned_cursor:
            reaped.append(
                {
                    "step_id": doc.get("step_id", ""),
                    "workflow_id": doc.get("workflow_id", ""),
                    "name": doc.get("name", ""),
                    "server_id": doc.get("server_id", ""),
                    "task_started_ms": str(doc.get("updated", 0)),
                    "last_ping_ms": str(server_pings.get(doc.get("server_id", ""), 0)),
                }
            )

        # Reset running tasks back to pending
        self._db.tasks.update_many(
            {
                "state": "running",
                "server_id": {"$in": dead_ids},
                **stale_heartbeat_filter,
            },
            {
                "$set": {
                    "state": "pending",
                    "server_id": "",
                    "task_heartbeat": 0,
                    "updated": now,
                },
            },
        )

        # Clear server_id on pending tasks pinned to dead servers
        self._db.tasks.update_many(
            {
                "state": "pending",
                "server_id": {"$in": dead_ids},
            },
            {
                "$set": {
                    "server_id": "",
                    "updated": now,
                },
            },
        )

        # Mark dead servers as shutdown so they don't appear as ghost runners
        self._db.servers.update_many(
            {"uuid": {"$in": dead_ids}},
            {"$set": {"state": "shutdown", "ping_time": now}},
        )

        return reaped

    def reap_stuck_tasks(self, default_stuck_ms: int = 14_400_000) -> list[dict[str, str]]:
        """Reset tasks stuck in RUNNING state beyond their timeout.

        Catches two cases:

        1. **Explicit timeout** – the task has ``timeout_ms > 0`` and its last
           activity (``max(task_heartbeat, updated)``) exceeds that timeout.
        2. **Default timeout** – the task has no explicit timeout (``timeout_ms``
           is 0 or missing) and its last activity exceeds *default_stuck_ms*.

        Unlike ``reap_orphaned_tasks`` (which checks for dead *servers*), this
        method catches tasks stuck on *live* servers — e.g. a handler blocked
        on an unresponsive downstream service.

        Returns a list of dicts describing each reaped task so callers can
        emit step logs.
        """
        now = _current_time_ms()
        reaped: list[dict[str, str]] = []
        stuck_uuids: list[str] = []

        # --- Pass 1: tasks with an explicit timeout_ms ---
        candidates = self._db.tasks.find(
            {"state": "running", "timeout_ms": {"$gt": 0}},
            {
                "uuid": 1,
                "step_id": 1,
                "workflow_id": 1,
                "name": 1,
                "server_id": 1,
                "updated": 1,
                "task_heartbeat": 1,
                "timeout_ms": 1,
            },
        )
        for doc in candidates:
            last_activity = max(doc.get("task_heartbeat", 0), doc.get("updated", 0))
            if now - last_activity > doc["timeout_ms"]:
                stuck_uuids.append(doc["uuid"])
                reaped.append(
                    {
                        "step_id": doc.get("step_id", ""),
                        "workflow_id": doc.get("workflow_id", ""),
                        "name": doc.get("name", ""),
                        "server_id": doc.get("server_id", ""),
                        "task_started_ms": str(doc.get("updated", 0)),
                        "reason": "timeout",
                        "timeout_ms": str(doc.get("timeout_ms", 0)),
                    }
                )

        # --- Pass 2: tasks without explicit timeout, using default ---
        cutoff = now - default_stuck_ms
        default_cursor = self._db.tasks.find(
            {
                "state": "running",
                "$or": [{"timeout_ms": 0}, {"timeout_ms": {"$exists": False}}],
                "updated": {"$lt": cutoff},
                "$and": [
                    {
                        "$or": [
                            {"task_heartbeat": {"$exists": False}},
                            {"task_heartbeat": 0},
                            {"task_heartbeat": {"$lt": cutoff}},
                        ]
                    },
                ],
            },
            {
                "uuid": 1,
                "step_id": 1,
                "workflow_id": 1,
                "name": 1,
                "server_id": 1,
                "updated": 1,
            },
        )
        for doc in default_cursor:
            stuck_uuids.append(doc["uuid"])
            reaped.append(
                {
                    "step_id": doc.get("step_id", ""),
                    "workflow_id": doc.get("workflow_id", ""),
                    "name": doc.get("name", ""),
                    "server_id": doc.get("server_id", ""),
                    "task_started_ms": str(doc.get("updated", 0)),
                    "reason": "stuck",
                    "timeout_ms": str(default_stuck_ms),
                }
            )

        if not stuck_uuids:
            return []

        # Reset stuck tasks back to pending
        self._db.tasks.update_many(
            {"uuid": {"$in": stuck_uuids}, "state": "running"},
            {
                "$set": {
                    "state": "pending",
                    "server_id": "",
                    "task_heartbeat": 0,
                    "updated": now,
                },
            },
        )
        return reaped

    # =========================================================================
    # Log Operations
    # =========================================================================

    def get_logs_by_runner(self, runner_id: str) -> Sequence[LogDefinition]:
        """Get logs for a runner."""
        docs = self._db.logs.find({"runner_id": runner_id}).sort("order", ASCENDING)
        return [self._doc_to_log(doc) for doc in docs]

    def save_log(self, log: LogDefinition) -> None:
        """Save a log entry."""
        doc = self._log_to_doc(log)
        self._db.logs.insert_one(doc)

    # =========================================================================
    # Step Log Operations
    # =========================================================================

    def save_step_log(self, entry: StepLogEntry) -> None:
        """Save a step log entry."""
        doc = self._step_log_to_doc(entry)
        self._db.step_logs.insert_one(doc)

    def get_step_logs_by_step(self, step_id: str) -> Sequence[StepLogEntry]:
        """Get step logs for a step, ordered by time ascending."""
        docs = self._db.step_logs.find({"step_id": step_id}).sort("time", ASCENDING)
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_step_logs_by_workflow(self, workflow_id: str) -> Sequence[StepLogEntry]:
        """Get step logs for a workflow, ordered by time ascending."""
        docs = self._db.step_logs.find({"workflow_id": workflow_id}).sort("time", ASCENDING)
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_tasks_by_facet_name(
        self, facet_name: str, states: list[str] | None = None
    ) -> Sequence[TaskDefinition]:
        """Get tasks matching a facet name, optionally filtered by states."""
        query: dict[str, Any] = {"name": facet_name}
        if states:
            query["state"] = {"$in": states}
        docs = self._db.tasks.find(query).sort("created", -1)
        return [self._doc_to_task(doc) for doc in docs]

    def get_step_logs_since(self, step_id: str, since_time: int) -> Sequence[StepLogEntry]:
        """Get step logs for a step newer than the given timestamp."""
        docs = self._db.step_logs.find({"step_id": step_id, "time": {"$gt": since_time}}).sort(
            "time", ASCENDING
        )
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_workflow_logs_since(self, workflow_id: str, since_time: int) -> Sequence[StepLogEntry]:
        """Get step logs for a workflow newer than the given timestamp."""
        docs = self._db.step_logs.find(
            {"workflow_id": workflow_id, "time": {"$gt": since_time}}
        ).sort("time", ASCENDING)
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_step_logs_by_facet(self, facet_name: str, limit: int = 20) -> Sequence[StepLogEntry]:
        """Get recent step logs for a facet, ordered by time descending."""
        docs = self._db.step_logs.find({"facet_name": facet_name}).sort("time", -1).limit(limit)
        return [self._doc_to_step_log(doc) for doc in docs]

    # =========================================================================
    # Handler Registration Operations
    # =========================================================================

    def save_handler_registration(self, registration: HandlerRegistration) -> None:
        """Upsert a handler registration by facet_name."""
        doc = self._handler_reg_to_doc(registration)
        self._db.handler_registrations.replace_one(
            {"facet_name": registration.facet_name}, doc, upsert=True
        )

    def get_handler_registration(self, facet_name: str) -> HandlerRegistration | None:
        """Get a handler registration by facet name."""
        doc = self._db.handler_registrations.find_one({"facet_name": facet_name})
        return self._doc_to_handler_reg(doc) if doc else None

    def list_handler_registrations(self) -> list[HandlerRegistration]:
        """List all handler registrations."""
        docs = self._db.handler_registrations.find()
        return [self._doc_to_handler_reg(doc) for doc in docs]

    def delete_handler_registration(self, facet_name: str) -> bool:
        """Delete a handler registration by facet name."""
        result = self._db.handler_registrations.delete_one({"facet_name": facet_name})
        return result.deleted_count > 0

    def _handler_reg_to_doc(self, reg: HandlerRegistration) -> dict:
        """Convert HandlerRegistration to MongoDB document."""
        return {
            "facet_name": reg.facet_name,
            "module_uri": reg.module_uri,
            "entrypoint": reg.entrypoint,
            "version": reg.version,
            "checksum": reg.checksum,
            "timeout_ms": reg.timeout_ms,
            "requirements": reg.requirements,
            "metadata": reg.metadata,
            "created": reg.created,
            "updated": reg.updated,
        }

    def _doc_to_handler_reg(self, doc: dict) -> HandlerRegistration:
        """Convert MongoDB document to HandlerRegistration."""
        return HandlerRegistration(
            facet_name=doc["facet_name"],
            module_uri=doc["module_uri"],
            entrypoint=doc.get("entrypoint", "handle"),
            version=doc.get("version", "1.0.0"),
            checksum=doc.get("checksum", ""),
            timeout_ms=doc.get("timeout_ms", 30000),
            requirements=doc.get("requirements", []),
            metadata=doc.get("metadata", {}),
            created=doc.get("created", 0),
            updated=doc.get("updated", 0),
        )

    # =========================================================================
    # Flow Operations
    # =========================================================================

    def get_flow(self, flow_id: str) -> FlowDefinition | None:
        """Get a flow by ID."""
        doc = self._db.flows.find_one({"uuid": flow_id})
        return self._doc_to_flow(doc) if doc else None

    def get_flow_by_path(self, path: str) -> FlowDefinition | None:
        """Get a flow by path."""
        doc = self._db.flows.find_one({"name.path": path})
        return self._doc_to_flow(doc) if doc else None

    def get_flow_by_name(self, name: str) -> FlowDefinition | None:
        """Get a flow by name."""
        doc = self._db.flows.find_one({"name.name": name})
        return self._doc_to_flow(doc) if doc else None

    def save_flow(self, flow: FlowDefinition) -> None:
        """Save a flow."""
        doc = self._flow_to_doc(flow)
        self._db.flows.replace_one({"uuid": flow.uuid}, doc, upsert=True)

    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow."""
        result = self._db.flows.delete_one({"uuid": flow_id})
        return result.deleted_count > 0

    def get_all_flows(self) -> Sequence[FlowDefinition]:
        """Get all flows."""
        docs = self._db.flows.find()
        return [self._doc_to_flow(doc) for doc in docs]

    # =========================================================================
    # Workflow Operations
    # =========================================================================

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        """Get a workflow by ID."""
        doc = self._db.workflows.find_one({"uuid": workflow_id})
        return self._doc_to_workflow(doc) if doc else None

    def get_workflow_by_name(self, name: str) -> WorkflowDefinition | None:
        """Get a workflow by name."""
        doc = self._db.workflows.find_one({"name": name})
        return self._doc_to_workflow(doc) if doc else None

    def get_workflows_by_flow(self, flow_id: str) -> Sequence[WorkflowDefinition]:
        """Get all workflows for a flow."""
        docs = self._db.workflows.find({"flow_id": flow_id})
        return [self._doc_to_workflow(doc) for doc in docs]

    def save_workflow(self, workflow: WorkflowDefinition) -> None:
        """Save a workflow."""
        doc = self._workflow_to_doc(workflow)
        self._db.workflows.replace_one({"uuid": workflow.uuid}, doc, upsert=True)

    # =========================================================================
    # Server Operations
    # =========================================================================

    def get_server(self, server_id: str) -> ServerDefinition | None:
        """Get a server by ID."""
        doc = self._db.servers.find_one({"uuid": server_id})
        return self._doc_to_server(doc) if doc else None

    def get_servers_by_state(self, state: str) -> Sequence[ServerDefinition]:
        """Get servers by state."""
        docs = self._db.servers.find({"state": state})
        return [self._doc_to_server(doc) for doc in docs]

    def get_all_servers(self) -> Sequence[ServerDefinition]:
        """Get all servers."""
        docs = self._db.servers.find()
        return [self._doc_to_server(doc) for doc in docs]

    def save_server(self, server: ServerDefinition) -> None:
        """Save a server."""
        doc = self._server_to_doc(server)
        self._db.servers.replace_one({"uuid": server.uuid}, doc, upsert=True)

    def update_server_ping(self, server_id: str, ping_time: int) -> None:
        """Update server ping time."""
        self._db.servers.update_one({"uuid": server_id}, {"$set": {"ping_time": ping_time}})

    def update_task_heartbeat(
        self,
        task_id: str,
        heartbeat_time: int,
        progress_pct: int | None = None,
        progress_message: str | None = None,
    ) -> None:
        """Update a running task's heartbeat timestamp and renew lease.

        Optionally records ``progress_pct`` (0-100) and ``progress_message``
        so the stuck-task watchdog can distinguish truly stuck handlers from
        those making slow but real progress.
        """
        lease_ms = int(os.environ.get("AFL_LEASE_DURATION_MS", str(self.DEFAULT_LEASE_MS)))
        update: dict[str, Any] = {
            "task_heartbeat": heartbeat_time,
            "lease_expires": heartbeat_time + lease_ms,
        }
        if progress_pct is not None:
            update["progress_pct"] = max(0, min(100, progress_pct))
        if progress_message is not None:
            update["progress_message"] = progress_message
        self._db.tasks.update_one(
            {"uuid": task_id, "state": "running"},
            {"$set": update},
        )

    # =========================================================================
    # Published Source Operations
    # =========================================================================

    def save_published_source(self, source: PublishedSource) -> None:
        """Save or update a published source.

        Upserts by (namespace_name, version).
        """
        doc = {
            "uuid": source.uuid,
            "namespace_name": source.namespace_name,
            "source_text": source.source_text,
            "namespaces_defined": source.namespaces_defined,
            "version": source.version,
            "published_at": source.published_at,
            "origin": source.origin,
            "checksum": source.checksum,
        }
        self._db.afl_sources.replace_one(
            {"namespace_name": source.namespace_name, "version": source.version},
            doc,
            upsert=True,
        )

    def get_source_by_namespace(self, name: str, version: str = "latest") -> PublishedSource | None:
        """Get a published source by namespace name and version."""
        doc = self._db.afl_sources.find_one({"namespace_name": name, "version": version})
        return self._doc_to_published_source(doc) if doc else None

    def get_sources_by_namespaces(
        self, names: set[str], version: str = "latest"
    ) -> dict[str, PublishedSource]:
        """Batch-fetch published sources by namespace names.

        Returns a dict mapping namespace_name → PublishedSource.
        """
        docs = self._db.afl_sources.find(
            {"namespace_name": {"$in": list(names)}, "version": version}
        )
        result: dict[str, PublishedSource] = {}
        for doc in docs:
            ps = self._doc_to_published_source(doc)
            result[ps.namespace_name] = ps
        return result

    def delete_published_source(self, name: str, version: str = "latest") -> bool:
        """Delete a published source by namespace name and version."""
        result = self._db.afl_sources.delete_one({"namespace_name": name, "version": version})
        return result.deleted_count > 0

    def list_published_sources(self) -> list[PublishedSource]:
        """List all published sources."""
        docs = self._db.afl_sources.find()
        return [self._doc_to_published_source(doc) for doc in docs]

    def _doc_to_published_source(self, doc: dict) -> PublishedSource:
        """Convert MongoDB document to PublishedSource."""
        return PublishedSource(
            uuid=doc["uuid"],
            namespace_name=doc["namespace_name"],
            source_text=doc["source_text"],
            namespaces_defined=doc.get("namespaces_defined", []),
            version=doc.get("version", "latest"),
            published_at=doc.get("published_at", 0),
            origin=doc.get("origin", ""),
            checksum=doc.get("checksum", ""),
        )

    # =========================================================================
    # Serialization Helpers
    # =========================================================================

    def _step_to_doc(self, step: StepDefinition) -> dict:
        """Convert StepDefinition to MongoDB document."""
        doc = {
            "uuid": step.id,
            "workflow_id": step.workflow_id,
            "object_type": step.object_type,
            "state": step.state,
            "statement_id": step.statement_id,
            "statement_name": step.statement_name,
            "container_id": step.container_id,
            "root_id": step.root_id,
            "block_id": step.block_id,
            "facet_name": step.facet_name or None,
            "is_block": step.is_block,
            "is_starting_step": getattr(step, "is_starting_step", False),
            "start_time": step.start_time,
            "last_modified": step.last_modified,
            "version": {
                "workflow_version": step.version.workflow_version,
                "step_schema_version": step.version.step_schema_version,
                "runtime_version": step.version.runtime_version,
            }
            if step.version
            else {},
        }

        if step.foreach_var is not None:
            doc["foreach_var"] = step.foreach_var
            doc["foreach_value"] = step.foreach_value

        if step.attributes:
            doc["attributes"] = {
                "params": {
                    k: {"name": v.name, "value": v.value, "type_hint": v.type_hint}
                    for k, v in step.attributes.params.items()
                },
                "returns": {
                    k: {"name": v.name, "value": v.value, "type_hint": v.type_hint}
                    for k, v in step.attributes.returns.items()
                },
            }

        # Persist transition flags so round-tripped steps retain their
        # state-machine control signals (e.g. continue_step setting
        # request_transition=True for an EventTransmit-blocked step).
        if hasattr(step, "transition"):
            doc["request_transition"] = step.transition.request_transition

        error = getattr(step, "error", None)
        if error is None and hasattr(step, "transition"):
            error = getattr(step.transition, "error", None)
        if error:
            doc["error"] = str(error)

        return doc

    def _doc_to_step(self, doc: dict) -> StepDefinition:
        """Convert MongoDB document to StepDefinition."""
        from .types import AttributeValue, FacetAttributes

        step = StepDefinition(
            id=StepId(doc["uuid"]),
            workflow_id=WorkflowId(doc["workflow_id"]),
            object_type=doc["object_type"],
            state=doc["state"],
        )

        # Restore the persisted request_transition flag.  Old documents
        # without the field default to False — the safe choice that lets
        # each handler decide when to advance.  StepTransition.initial()
        # sets request_transition=True which would cause the state changer
        # to skip the current state's handler and advance immediately,
        # bypassing EventTransmit blocking and block completion checks.
        step.transition.request_transition = doc.get("request_transition", False)

        step.statement_id = doc.get("statement_id")
        step.statement_name = doc.get("statement_name", "")
        step.container_id = StepId(doc["container_id"]) if doc.get("container_id") else None
        step.root_id = StepId(doc["root_id"]) if doc.get("root_id") else None
        step.block_id = BlockId(doc["block_id"]) if doc.get("block_id") else None
        step.facet_name = doc.get("facet_name") or ""
        # is_block is a computed property on StepDefinition; skip setting it.
        # is_starting_step may not exist on all StepDefinition versions.
        if hasattr(step, "is_starting_step"):
            step.is_starting_step = doc.get("is_starting_step", False)
        version_doc = doc.get("version")
        if isinstance(version_doc, dict):
            step.version = VersionInfo(
                workflow_version=version_doc.get("workflow_version", "1.0"),
                step_schema_version=version_doc.get("step_schema_version", "1.0"),
                runtime_version=version_doc.get("runtime_version", "0.1.0"),
            )
        else:
            step.version = VersionInfo()

        if "attributes" in doc:
            attrs = doc["attributes"]
            step.attributes = FacetAttributes()
            for k, v in attrs.get("params", {}).items():
                step.attributes.params[k] = AttributeValue(
                    v["name"], v["value"], v.get("type_hint", "Any")
                )
            for k, v in attrs.get("returns", {}).items():
                step.attributes.returns[k] = AttributeValue(
                    v["name"], v["value"], v.get("type_hint", "Any")
                )

        step.foreach_var = doc.get("foreach_var")
        step.foreach_value = doc.get("foreach_value")

        step.start_time = doc.get("start_time", 0)
        step.last_modified = doc.get("last_modified", 0)

        if doc.get("error") and hasattr(step, "transition"):
            step.transition.error = Exception(doc["error"])

        return step

    def _runner_to_doc(self, runner: RunnerDefinition) -> dict:
        """Convert RunnerDefinition to MongoDB document."""
        return {
            "uuid": runner.uuid,
            "workflow_id": runner.workflow_id,
            "workflow": self._workflow_to_doc(runner.workflow),
            "parameters": [asdict(p) for p in runner.parameters],
            "step_id": runner.step_id,
            "user": asdict(runner.user) if runner.user else None,
            "start_time": runner.start_time,
            "end_time": runner.end_time,
            "duration": runner.duration,
            "retain": runner.retain,
            "state": runner.state,
            "compiled_ast": runner.compiled_ast,
            "workflow_ast": runner.workflow_ast,
        }

    def _doc_to_runner(self, doc: dict) -> RunnerDefinition:
        """Convert MongoDB document to RunnerDefinition."""
        from .entities import UserDefinition

        workflow = self._doc_to_workflow(doc["workflow"])
        user = None
        if doc.get("user"):
            user = UserDefinition(**doc["user"])

        return RunnerDefinition(
            uuid=doc["uuid"],
            workflow_id=doc["workflow_id"],
            workflow=workflow,
            parameters=[Parameter(**p) for p in doc.get("parameters", [])],
            step_id=doc.get("step_id"),
            user=user,
            start_time=doc.get("start_time", 0),
            end_time=doc.get("end_time", 0),
            duration=doc.get("duration", 0),
            retain=doc.get("retain", 0),
            state=doc.get("state", "created"),
            compiled_ast=doc.get("compiled_ast"),
            workflow_ast=doc.get("workflow_ast"),
        )

    def _workflow_to_doc(self, workflow: WorkflowDefinition) -> dict:
        """Convert WorkflowDefinition to MongoDB document."""
        return {
            "uuid": workflow.uuid,
            "name": workflow.name,
            "namespace_id": workflow.namespace_id,
            "facet_id": workflow.facet_id,
            "flow_id": workflow.flow_id,
            "starting_step": workflow.starting_step,
            "version": workflow.version,
            "metadata": asdict(workflow.metadata) if workflow.metadata else None,
            "documentation": workflow.documentation,
            "date": workflow.date,
        }

    def _doc_to_workflow(self, doc: dict) -> WorkflowDefinition:
        """Convert MongoDB document to WorkflowDefinition."""
        from .entities import WorkflowMetaData

        metadata = None
        if doc.get("metadata"):
            metadata = WorkflowMetaData(**doc["metadata"])

        return WorkflowDefinition(
            uuid=doc["uuid"],
            name=doc["name"],
            namespace_id=doc["namespace_id"],
            facet_id=doc["facet_id"],
            flow_id=doc["flow_id"],
            starting_step=doc["starting_step"],
            version=doc["version"],
            metadata=metadata,
            documentation=doc.get("documentation"),
            date=doc.get("date", 0),
        )

    def _task_to_doc(self, task: TaskDefinition) -> dict:
        """Convert TaskDefinition to MongoDB document."""
        return {
            "uuid": task.uuid,
            "name": task.name,
            "runner_id": task.runner_id,
            "workflow_id": task.workflow_id,
            "flow_id": task.flow_id,
            "step_id": task.step_id,
            "state": task.state,
            "created": task.created,
            "updated": task.updated,
            "error": task.error,
            "task_list_name": task.task_list_name,
            "data_type": task.data_type,
            "data": task.data,
            "server_id": task.server_id,
            "timeout_ms": task.timeout_ms,
        }

    def _doc_to_task(self, doc: dict) -> TaskDefinition:
        """Convert MongoDB document to TaskDefinition."""
        return TaskDefinition(
            uuid=doc["uuid"],
            name=doc["name"],
            runner_id=doc["runner_id"],
            workflow_id=doc["workflow_id"],
            flow_id=doc["flow_id"],
            step_id=doc["step_id"],
            state=doc.get("state", "pending"),
            created=doc.get("created", 0),
            updated=doc.get("updated", 0),
            error=doc.get("error"),
            task_list_name=doc.get("task_list_name", "default"),
            data_type=doc.get("data_type", ""),
            data=doc.get("data"),
            server_id=doc.get("server_id", ""),
            timeout_ms=doc.get("timeout_ms", 0),
        )

    def _log_to_doc(self, log: LogDefinition) -> dict:
        """Convert LogDefinition to MongoDB document."""
        return {
            "uuid": log.uuid,
            "order": log.order,
            "runner_id": log.runner_id,
            "step_id": log.step_id,
            "object_id": log.object_id,
            "object_type": log.object_type,
            "note_originator": log.note_originator,
            "note_type": log.note_type,
            "note_importance": log.note_importance,
            "message": log.message,
            "state": log.state,
            "line": log.line,
            "file": log.file,
            "details": log.details,
            "time": log.time,
        }

    def _doc_to_log(self, doc: dict) -> LogDefinition:
        """Convert MongoDB document to LogDefinition."""
        return LogDefinition(
            uuid=doc["uuid"],
            order=doc["order"],
            runner_id=doc["runner_id"],
            step_id=doc.get("step_id"),
            object_id=doc.get("object_id", ""),
            object_type=doc.get("object_type", ""),
            note_originator=doc.get("note_originator", "workflow"),
            note_type=doc.get("note_type", "info"),
            note_importance=doc.get("note_importance", 5),
            message=doc.get("message", ""),
            state=doc.get("state", ""),
            line=doc.get("line", 0),
            file=doc.get("file", ""),
            details=doc.get("details", {}),
            time=doc.get("time", 0),
        )

    def _step_log_to_doc(self, entry: StepLogEntry) -> dict:
        """Convert StepLogEntry to MongoDB document."""
        return {
            "uuid": entry.uuid,
            "step_id": entry.step_id,
            "workflow_id": entry.workflow_id,
            "runner_id": entry.runner_id,
            "facet_name": entry.facet_name,
            "source": entry.source,
            "level": entry.level,
            "message": entry.message,
            "details": entry.details,
            "time": entry.time,
        }

    def _doc_to_step_log(self, doc: dict) -> StepLogEntry:
        """Convert MongoDB document to StepLogEntry."""
        return StepLogEntry(
            uuid=doc["uuid"],
            step_id=doc["step_id"],
            workflow_id=doc["workflow_id"],
            runner_id=doc.get("runner_id", ""),
            facet_name=doc.get("facet_name", ""),
            source=doc.get("source", "framework"),
            level=doc.get("level", "info"),
            message=doc.get("message", ""),
            details=doc.get("details", {}),
            time=doc.get("time", 0),
        )

    def _server_to_doc(self, server: ServerDefinition) -> dict:
        """Convert ServerDefinition to MongoDB document."""
        return {
            "uuid": server.uuid,
            "server_group": server.server_group,
            "service_name": server.service_name,
            "server_name": server.server_name,
            "server_ips": server.server_ips,
            "start_time": server.start_time,
            "ping_time": server.ping_time,
            "topics": server.topics,
            "handlers": server.handlers,
            "handled": [asdict(h) for h in server.handled],
            "state": server.state,
            "http_port": server.http_port,
            "version": server.version,
            "manager": server.manager,
            "error": server.error,
        }

    def _doc_to_server(self, doc: dict) -> ServerDefinition:
        """Convert MongoDB document to ServerDefinition."""
        return ServerDefinition(
            uuid=doc["uuid"],
            server_group=doc["server_group"],
            service_name=doc["service_name"],
            server_name=doc["server_name"],
            server_ips=doc.get("server_ips", []),
            start_time=doc.get("start_time", 0),
            ping_time=doc.get("ping_time", 0),
            topics=doc.get("topics", []),
            handlers=doc.get("handlers", []),
            handled=[HandledCount(**h) for h in doc.get("handled", [])],
            state=doc.get("state", "startup"),
            http_port=doc.get("http_port", 0),
            version=doc.get("version", ""),
            manager=doc.get("manager", ""),
            error=doc.get("error"),
        )

    def _flow_to_doc(self, flow: FlowDefinition) -> dict:
        """Convert FlowDefinition to MongoDB document."""
        return {
            "uuid": flow.uuid,
            "name": asdict(flow.name),
            "namespaces": [asdict(n) for n in flow.namespaces],
            "facets": [asdict(f) for f in flow.facets],
            "workflows": [self._workflow_to_doc(w) for w in flow.workflows],
            "mixins": [asdict(m) for m in flow.mixins],
            "blocks": [asdict(b) for b in flow.blocks],
            "statements": [asdict(s) for s in flow.statements],
            "arguments": [asdict(a) for a in flow.arguments],
            "references": [asdict(r) for r in flow.references],
            "script_code": [asdict(s) for s in flow.script_code],
            "file_artifacts": [asdict(f) for f in flow.file_artifacts],
            "jar_artifacts": [asdict(j) for j in flow.jar_artifacts],
            "resources": [asdict(r) for r in flow.resources],
            "text_sources": [asdict(t) for t in flow.text_sources],
            "inline": asdict(flow.inline) if flow.inline else None,
            "classification": asdict(flow.classification) if flow.classification else None,
            "publisher": asdict(flow.publisher) if flow.publisher else None,
            "ownership": asdict(flow.ownership) if flow.ownership else None,
            "compiled_sources": [asdict(s) for s in flow.compiled_sources],
            "compiled_ast": flow.compiled_ast,
        }

    def _doc_to_flow(self, doc: dict) -> FlowDefinition:
        """Convert MongoDB document to FlowDefinition."""
        from .entities import (
            BlockDefinition,
            Classifier,
            FacetDefinition,
            FileArtifact,
            InlineSource,
            JarArtifact,
            MixinDefinition,
            NamespaceDefinition,
            Ownership,
            ResourceSource,
            ScriptCode,
            SourceText,
            StatementArguments,
            StatementDefinition,
            StatementReferences,
            TextSource,
            UserDefinition,
        )

        name = FlowIdentity(**doc["name"])

        inline = None
        if doc.get("inline"):
            inline = InlineSource(**doc["inline"])

        classification = None
        if doc.get("classification"):
            classification = Classifier(**doc["classification"])

        publisher = None
        if doc.get("publisher"):
            publisher = UserDefinition(**doc["publisher"])

        ownership = None
        if doc.get("ownership"):
            ownership = Ownership(**doc["ownership"])

        return FlowDefinition(
            uuid=doc["uuid"],
            name=name,
            namespaces=[NamespaceDefinition(**n) for n in doc.get("namespaces", [])],
            facets=[FacetDefinition(**f) for f in doc.get("facets", [])],
            workflows=[self._doc_to_workflow(w) for w in doc.get("workflows", [])],
            mixins=[MixinDefinition(**m) for m in doc.get("mixins", [])],
            blocks=[BlockDefinition(**b) for b in doc.get("blocks", [])],
            statements=[StatementDefinition(**s) for s in doc.get("statements", [])],
            arguments=[StatementArguments(**a) for a in doc.get("arguments", [])],
            references=[StatementReferences(**r) for r in doc.get("references", [])],
            script_code=[ScriptCode(**s) for s in doc.get("script_code", [])],
            file_artifacts=[FileArtifact(**f) for f in doc.get("file_artifacts", [])],
            jar_artifacts=[JarArtifact(**j) for j in doc.get("jar_artifacts", [])],
            resources=[ResourceSource(**r) for r in doc.get("resources", [])],
            text_sources=[TextSource(**t) for t in doc.get("text_sources", [])],
            inline=inline,
            classification=classification,
            publisher=publisher,
            ownership=ownership,
            compiled_sources=[SourceText(**s) for s in doc.get("compiled_sources", [])],
            compiled_ast=doc.get("compiled_ast"),
        )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def close(self) -> None:
        """Close the MongoDB connection."""
        self._client.close()

    def drop_database(self) -> None:
        """Drop the entire database. Use with caution!"""
        self._client.drop_database(self._db.name)
