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

"""Task CRUD operations mixin for MongoStore."""

import logging
import os
from collections.abc import Sequence
from typing import Any

try:
    from pymongo import ReturnDocument
except ImportError:
    try:
        from mongomock.collection import ReturnDocument  # type: ignore[no-redef]
    except ImportError:
        class ReturnDocument:  # type: ignore[no-redef]
            AFTER = True
            BEFORE = False

from ..entities import TaskDefinition

from .base import _current_time_ms

logger = logging.getLogger(__name__)


class TaskMixin:
    """Task CRUD and reaping operations."""

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

    def claim_task(
        self,
        task_names: list[str],
        task_list: str = "default",
        server_id: str = "",
    ) -> TaskDefinition | None:
        """Atomically claim a pending task matching one of the given names.

        Uses find_one_and_update for atomic PENDING -> RUNNING transition.
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

        # Build a query that matches exact names or names that start with
        # one of the given prefixes (e.g. "fw:execute" matches "fw:execute:MyWorkflow")
        name_conditions: list[dict] = [{"name": {"$in": task_names}}]
        for tn in task_names:
            name_conditions.append({"name": {"$regex": f"^{tn}:"}})
        name_filter = {"$or": name_conditions} if len(name_conditions) > 1 else name_conditions[0]

        # Backoff filter: skip tasks still in their retry cooldown window
        retry_eligible = {"$or": [
            {"next_retry_after": {"$exists": False}},
            {"next_retry_after": 0},
            {"next_retry_after": {"$lte": now}},
        ]}

        # First try to claim a pending task
        doc = self._db.tasks.find_one_and_update(
            {
                "state": "pending",
                **name_filter,
                **retry_eligible,
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
                **name_filter,
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

    def get_tasks_by_workflow(self, workflow_id: str) -> Sequence[TaskDefinition]:
        """Get all tasks for a workflow."""
        docs = self._db.tasks.find({"workflow_id": workflow_id})
        return [self._doc_to_task(doc) for doc in docs]

    def get_tasks_by_server_id(self, server_id: str, limit: int = 200) -> Sequence[TaskDefinition]:
        """Get tasks claimed by a specific server, most recent first."""
        docs = self._db.tasks.find({"server_id": server_id}).sort("updated", -1).limit(limit)
        return [self._doc_to_task(doc) for doc in docs]

    def get_tasks_by_facet_name(
        self, facet_name: str, states: list[str] | None = None
    ) -> Sequence[TaskDefinition]:
        """Get tasks matching a facet name, optionally filtered by states."""
        query: dict[str, Any] = {"name": facet_name}
        if states:
            query["state"] = {"$in": states}
        docs = self._db.tasks.find(query).sort("created", -1)
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

        # Build server_id -> last_ping lookup for diagnostics
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

        # Reset running tasks: increment retry_count and set back to pending
        orphan_filter = {
            "state": "running",
            "server_id": {"$in": dead_ids},
            **stale_heartbeat_filter,
        }
        self._db.tasks.update_many(
            orphan_filter,
            {
                "$set": {
                    "state": "pending",
                    "server_id": "",
                    "task_heartbeat": 0,
                    "updated": now,
                },
                "$inc": {"retry_count": 1},
            },
        )
        # Dead-letter tasks that exceeded max_retries
        self._db.tasks.update_many(
            {
                "state": "pending",
                "max_retries": {"$gt": 0},
                "$expr": {"$gte": ["$retry_count", "$max_retries"]},
            },
            {"$set": {"state": "dead_letter", "updated": now}},
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

        1. **Explicit timeout** -- the task has ``timeout_ms > 0`` and its last
           activity (``max(task_heartbeat, updated)``) exceeds that timeout.
        2. **Default timeout** -- the task has no explicit timeout (``timeout_ms``
           is 0 or missing) and its last activity exceeds *default_stuck_ms*.

        Unlike ``reap_orphaned_tasks`` (which checks for dead *servers*), this
        method catches tasks stuck on *live* servers -- e.g. a handler blocked
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
                "stage_budget_expires": 1,
            },
        )
        for doc in candidates:
            last_activity = max(doc.get("task_heartbeat", 0), doc.get("updated", 0))
            stage_budget = doc.get("stage_budget_expires", 0) or 0
            if stage_budget > now:
                continue  # inside an active stage budget — don't reap
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
                    {
                        "$or": [
                            {"stage_budget_expires": {"$exists": False}},
                            {"stage_budget_expires": 0},
                            {"stage_budget_expires": {"$lt": now}},
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

        # Reset stuck tasks: increment retry_count and set back to pending
        self._db.tasks.update_many(
            {"uuid": {"$in": stuck_uuids}, "state": "running"},
            {
                "$set": {
                    "state": "pending",
                    "server_id": "",
                    "task_heartbeat": 0,
                    "updated": now,
                },
                "$inc": {"retry_count": 1},
            },
        )
        # Dead-letter tasks that exceeded max_retries
        self._db.tasks.update_many(
            {
                "uuid": {"$in": stuck_uuids},
                "state": "pending",
                "max_retries": {"$gt": 0},
                "$expr": {"$gte": ["$retry_count", "$max_retries"]},
            },
            {"$set": {"state": "dead_letter", "updated": now}},
        )
        return reaped

    # =========================================================================
    # Serialization Helpers — Tasks
    # =========================================================================

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
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "next_retry_after": task.next_retry_after,
            "stage_budget_expires": task.stage_budget_expires,
            "stage_name": task.stage_name,
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
            retry_count=doc.get("retry_count", 0),
            max_retries=doc.get("max_retries", 5),
            next_retry_after=doc.get("next_retry_after", 0),
            stage_budget_expires=doc.get("stage_budget_expires", 0),
            stage_name=doc.get("stage_name", ""),
        )
