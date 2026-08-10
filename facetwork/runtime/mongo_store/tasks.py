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
import re
from collections.abc import Sequence
from typing import Any

try:
    from pymongo import ReturnDocument
except ImportError:
    try:
        from mongomock.collection import ReturnDocument
    except ImportError:

        class ReturnDocument:  # type: ignore[no-redef]
            AFTER = True
            BEFORE = False


from ..entities import TaskDefinition
from ._internals import _MixinBase
from .base import _current_time_ms, _reset_task_to_pending, _stale_heartbeat_or

logger = logging.getLogger(__name__)


class TaskMixin(_MixinBase):
    """Task CRUD and reaping operations."""

    def get_task(self, task_id: str) -> TaskDefinition | None:
        """Get a task by ID."""
        return self._find_decoded(self._db.tasks, {"uuid": task_id}, self._doc_to_task)

    def get_task_for_step(self, step_id: str) -> TaskDefinition | None:
        """Get the most recent task associated with a step.

        Uses ``find_one`` with a sort rather than ``_find_decoded`` so the
        ``sort=`` kwarg is preserved — the helper only takes a query.
        """
        doc = self._db.tasks.find_one(
            {"step_id": step_id},
            sort=[("created", -1)],
        )
        return self._doc_to_task(doc) if doc else None

    def get_tasks_by_step(self, step_id: str) -> Sequence[TaskDefinition]:
        """Get all tasks associated with a step."""
        docs = self._db.tasks.find({"step_id": step_id})
        return [self._doc_to_task(doc) for doc in docs]

    def has_active_task_for_step(self, step_id: str) -> bool:
        """Return True if any task for ``step_id`` is pending or running.

        Uses ``count_documents(limit=1)`` so the check is short-circuited
        on the first match instead of materializing the whole result set.
        """
        return (
            self._db.tasks.count_documents(
                {"step_id": step_id, "state": {"$in": ["pending", "running"]}},
                limit=1,
            )
            > 0
        )

    def has_dead_letter_task_for_step(self, step_id: str) -> bool:
        """Return True if ``step_id`` has a dead-lettered task (see base docstring).

        Lets the sweep fail a permanently-abandoned step instead of resurrecting
        it with a fresh retry_count=0 task.
        """
        return (
            self._db.tasks.count_documents(
                {"step_id": step_id, "state": "dead_letter"},
                limit=1,
            )
            > 0
        )

    def delete_pending_continuations_for_step(self, step_id: str, except_task_id: str = "") -> int:
        """Delete PENDING continuation tasks for ``step_id`` except the given one
        (claim-time continuation coalescing). See the base-class docstring."""
        from ..continuation import CONTINUATION_TASK_NAME

        query: dict = {
            "step_id": step_id,
            "name": CONTINUATION_TASK_NAME,
            "state": "pending",
        }
        if except_task_id:
            query["uuid"] = {"$ne": except_task_id}
        result = self._db.tasks.delete_many(query)
        return result.deleted_count

    def get_pending_tasks(self, task_list: str) -> Sequence[TaskDefinition]:
        """Get pending tasks for a task list."""
        docs = self._db.tasks.find({"task_list_name": task_list, "state": "pending"})
        return [self._doc_to_task(doc) for doc in docs]

    def save_task(self, task: TaskDefinition) -> None:
        """Save a task."""
        self._upsert_by_uuid(self._db.tasks, self._task_to_doc(task))

    def save_task_if_owned(self, task: TaskDefinition, expected_server_id: str) -> bool:
        """Save a task only if its current ``server_id`` still matches.

        Used by handlers writing terminal state (completed/failed) to
        prevent the documented lease-reclaim race (thesis §5.6): a handler
        whose lease expired while it was running shouldn't be able to
        overwrite the task document the reclaimer has since taken ownership
        of. Returns ``True`` if the write went through, ``False`` if the
        task was reclaimed (caller should treat as silently dropped).

        For initial-create-and-claim flows (``expected_server_id == ""``)
        this is equivalent to a plain ``save_task`` upsert.
        """
        doc = self._task_to_doc(task)
        # Strict ownership: write only if the doc still has expected_server_id.
        # If expected is "", we allow the upsert path so initial creation works.
        if expected_server_id == "":
            self._upsert_by_uuid(self._db.tasks, doc)
            return True
        result = self._db.tasks.replace_one(
            {"uuid": task.uuid, "server_id": expected_server_id},
            doc,
            upsert=False,
        )
        return result.matched_count > 0

    def claim_task(
        self,
        task_names: list[str],
        task_list: str | list[str] = "default",
        server_id: str = "",
        provided_environments: list[str] | None = None,
        known_features: list[str] | None = None,
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
        lease_ms = self._lease_ms()
        # A runner may poll several lists at once (the namespaces of its
        # handlers) — match any of them.
        tl_filter: Any = (
            {"$in": list(task_list)} if isinstance(task_list, (list, tuple, set)) else task_list
        )
        update: dict[str, Any] = {
            "state": "running",
            "updated": now,
            "lease_expires": now + lease_ms,
        }
        if server_id:
            update["server_id"] = server_id

        # Build a query that matches exact names or names that start with
        # one of the given prefixes (e.g. "fw:execute" matches "fw:execute:MyWorkflow").
        # The prefix is a LITERAL, so re.escape it: qualified names contain "."
        # (a regex wildcard — "osm.cache.Download:" would else also match
        # "osmXcacheXDownload:…"), and a name with an unbalanced "(" or a "$"/"+"
        # would make MongoDB's regex engine throw and fail the whole claim.
        name_conditions: list[dict] = [{"name": {"$in": task_names}}]
        for tn in task_names:
            name_conditions.append({"name": {"$regex": f"^{re.escape(tn)}:"}})
        name_filter = {"$or": name_conditions} if len(name_conditions) > 1 else name_conditions[0]

        # Environment routing (script-environments.md §3): tasks tagged with a
        # non-default environment_hash are claimable only by runners providing
        # that manifest; untagged tasks by everyone. Keep in behavioral
        # lockstep with MemoryStore.claim_task.
        env_ok: dict[str, Any] = {
            "$or": [
                {"environment_hash": {"$exists": False}},
                {"environment_hash": ""},
            ]
        }
        if provided_environments:
            env_ok["$or"].append({"environment_hash": {"$in": list(provided_environments)}})

        # Feature routing (ffl-after-clause.md §8): a task whose workflow uses AST
        # constructs carries them in `required_features`; a runner may claim it
        # only if it understands EVERY one. Otherwise an executor that doesn't
        # know a construct runs the workflow with those semantics SILENTLY
        # DROPPED — e.g. missing `after` edges become a race. Untagged tasks are
        # claimable by everyone, so this is inert for existing work.
        # `$not: {$elemMatch: {$nin: known}}` == "no element outside known" == subset.
        # Keep in behavioral lockstep with MemoryStore.claim_task.
        feat_ok: dict[str, Any] = {
            "$or": [
                {"required_features": {"$exists": False}},
                {"required_features": []},
            ]
        }
        if known_features:
            feat_ok["$or"].append(
                {"required_features": {"$not": {"$elemMatch": {"$nin": list(known_features)}}}}
            )

        # Backoff filter: skip tasks still in their retry cooldown window.
        retry_eligible = {
            "$or": [
                {"next_retry_after": {"$exists": False}},
                {"next_retry_after": 0},
                {"next_retry_after": {"$lte": now}},
            ]
        }

        # NOTE: ``name_filter`` and ``retry_eligible`` both use ``$or`` at the
        # top level, so they MUST be combined with ``$and`` — spreading both
        # into one dict would silently drop the name filter (duplicate key),
        # making the runner claim *any* pending task regardless of name.
        # First try to claim a pending task.
        doc = self._db.tasks.find_one_and_update(
            {
                "state": "pending",
                "task_list_name": tl_filter,
                "$and": [name_filter, retry_eligible, env_ok, feat_ok],
            },
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if doc:
            return self._doc_to_task(doc)

        # Then try to reclaim a running task whose lease has expired. This is a
        # RETRY (the previous holder died without renewing its lease), so bump
        # retry_count — otherwise a handler that keeps dying ping-pongs on lease
        # expiry forever and never reaches dead-letter. Only reclaim while the
        # task still has budget (max_retries <= 0 means unlimited); an exhausted
        # task is left running-with-expired-lease for the orphan reaper /
        # stuck-watchdog, which reset it to pending -> _dead_letter_overdue.
        # The lease duration itself (>= 5 min) paces this failover.
        doc = self._db.tasks.find_one_and_update(
            {
                "state": "running",
                "task_list_name": tl_filter,
                "lease_expires": {"$lt": now, "$gt": 0},
                "$and": [
                    name_filter,
                    env_ok,
                    {
                        "$or": [
                            {"max_retries": {"$lte": 0}},
                            {
                                "$expr": {
                                    "$lt": [
                                        {"$ifNull": ["$retry_count", 0]},
                                        {"$ifNull": ["$max_retries", 5]},
                                    ]
                                }
                            },
                        ]
                    },
                ],
            },
            {"$set": update, "$inc": {"retry_count": 1}},
            return_document=ReturnDocument.AFTER,
        )
        return self._doc_to_task(doc) if doc else None

    def update_task_state(self, task_id: str, state: str) -> None:
        """Update task state."""
        self._db.tasks.update_one(
            {"uuid": task_id}, {"$set": {"state": state, "updated": _current_time_ms()}}
        )

    def get_all_tasks(
        self, limit: int = 100, task_list: str | None = None
    ) -> Sequence[TaskDefinition]:
        """Get all tasks, most recently created first.

        When *task_list* is given, only tasks on that list are returned.
        """
        query: dict[str, Any] = {}
        if task_list:
            query["task_list_name"] = task_list
        docs = self._db.tasks.find(query).sort("created", -1).limit(limit)
        return [self._doc_to_task(doc) for doc in docs]

    def get_tasks_by_state(
        self, state: str, task_list: str | None = None
    ) -> Sequence[TaskDefinition]:
        """Get tasks by state, optionally narrowed to a single task list."""
        query: dict[str, Any] = {"state": state}
        if task_list:
            query["task_list_name"] = task_list
        docs = self._db.tasks.find(query).sort("created", -1)
        return [self._doc_to_task(doc) for doc in docs]

    def duplicate_completion_count(self) -> int:
        """Count step_ids that have more than one ``completed`` task document.

        This is the exactly-once-completion check: in normal operation
        the partial unique index on ``(step_id, state="running")`` plus
        ownership-gated terminal writes (``save_task_if_owned``) keep
        this at 0. Bootstrap tasks (which have empty ``step_id``) are
        excluded so they aren't conflated as duplicates.
        """
        pipeline: list[dict[str, Any]] = [
            {"$match": {"state": "completed", "step_id": {"$ne": ""}}},
            {"$group": {"_id": "$step_id", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
            {"$count": "dupes"},
        ]
        for doc in self._db.tasks.aggregate(pipeline):
            return int(doc.get("dupes", 0))
        return 0

    def task_list_counts(self) -> dict[str, int]:
        """Return ``{task_list_name: count}`` across all tasks.

        Used by the dashboard to render a per-list filter with sizes so
        operators can see how routing is distributing work at a glance.
        """
        out: dict[str, int] = {}
        for doc in self._db.tasks.aggregate(
            [{"$group": {"_id": "$task_list_name", "count": {"$sum": 1}}}]
        ):
            name = doc.get("_id") or "default"
            out[name] = doc["count"]
        return out

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
        stale_heartbeat_filter = _stale_heartbeat_or(heartbeat_cutoff)
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
                "$set": _reset_task_to_pending(now),
                "$inc": {"retry_count": 1},
            },
        )
        self._dead_letter_overdue(now)

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

    def get_pending_script_environment_demand(self) -> list[tuple[str, str]]:
        """Distinct (environment_hash, workflow_id) of pending script tasks.

        Keep in behavioral lockstep with MemoryStore."""
        out: list[tuple[str, str]] = []
        for doc in self._db.tasks.aggregate(
            [
                {
                    "$match": {
                        "state": "pending",
                        "kind": "script",
                        "environment_hash": {"$nin": ["", None]},
                    }
                },
                {"$group": {"_id": "$environment_hash", "workflow_id": {"$first": "$workflow_id"}}},
            ]
        ):
            out.append((doc["_id"], doc.get("workflow_id") or ""))
        return sorted(out)

    def claim_script_task(
        self,
        provided_environments,
        server_id: str = "",
    ) -> TaskDefinition | None:
        """Atomically claim a pending env-routed script task (see base class).

        Keep in behavioral lockstep with MemoryStore.claim_script_task.
        """
        if not provided_environments:
            return None
        now = _current_time_ms()
        update: dict[str, Any] = {
            "state": "running",
            "updated": now,
            "lease_expires": now + self._lease_ms(),
        }
        if server_id:
            update["server_id"] = server_id
        doc = self._db.tasks.find_one_and_update(
            {
                "state": "pending",
                "kind": "script",
                "environment_hash": {"$in": list(provided_environments)},
                "$or": [
                    {"next_retry_after": {"$exists": False}},
                    {"next_retry_after": 0},
                    {"next_retry_after": {"$lte": now}},
                ],
            },
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        return self._doc_to_task(doc) if doc else None

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
            # + grace: this threshold is the SAME timeout_ms the owning runner
            # dispatches on, so without it the reap and the owner's own timeout
            # fire together and the task can be handed to a second runner while
            # the first is still winding down. The owner acts first, always.
            if now - last_activity > doc["timeout_ms"] + self.RECLAIM_GRACE_MS:
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
        # Clamp the default up to the execution timeout + grace, the same
        # derivation the lease uses. These are independently-configured knobs
        # (FW_STUCK_TIMEOUT_MS vs FW_TASK_EXECUTION_TIMEOUT_MS) with a required
        # ordering: a stuck-timeout at or below the execution timeout lets this
        # reaper reset a task that its owner is still legitimately executing —
        # the same double-execution hazard as a short lease. The stock defaults
        # (30min vs 15min) happen to satisfy it; a deployment that raises the
        # execution timeout without raising this one does not.
        exec_timeout_ms = int(
            os.environ.get("FW_TASK_EXECUTION_TIMEOUT_MS", str(self.DEFAULT_EXECUTION_TIMEOUT_MS))
        )
        effective_stuck_ms = max(default_stuck_ms, exec_timeout_ms + self.RECLAIM_GRACE_MS)
        cutoff = now - effective_stuck_ms
        default_cursor = self._db.tasks.find(
            {
                "state": "running",
                "$or": [{"timeout_ms": 0}, {"timeout_ms": {"$exists": False}}],
                "updated": {"$lt": cutoff},
                "$and": [
                    _stale_heartbeat_or(cutoff),
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
                    "timeout_ms": str(effective_stuck_ms),
                }
            )

        if not stuck_uuids:
            return []

        # Reset stuck tasks: increment retry_count and set back to pending
        self._db.tasks.update_many(
            {"uuid": {"$in": stuck_uuids}, "state": "running"},
            {
                "$set": _reset_task_to_pending(now),
                "$inc": {"retry_count": 1},
            },
        )
        self._dead_letter_overdue(now, uuids=stuck_uuids)
        return reaped

    def _dead_letter_overdue(self, now_ms: int, uuids: list[str] | None = None) -> None:
        """Move pending tasks whose ``retry_count`` reached ``max_retries`` to
        ``dead_letter``. Optionally narrowed to a specific set of UUIDs (used
        by ``reap_stuck_tasks`` so it doesn't sweep tasks reset by other
        passes in the same tick).
        """
        query: dict[str, Any] = {
            "state": "pending",
            "max_retries": {"$gt": 0},
            "$expr": {"$gte": ["$retry_count", "$max_retries"]},
        }
        if uuids is not None:
            query["uuid"] = {"$in": uuids}
        self._db.tasks.update_many(
            query,
            {"$set": {"state": "dead_letter", "updated": now_ms}},
        )

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
            "environment_hash": task.environment_hash,
            "kind": task.kind,
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
            environment_hash=doc.get("environment_hash", ""),
            kind=doc.get("kind", ""),
        )
