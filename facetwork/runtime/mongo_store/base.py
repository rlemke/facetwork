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

"""MongoDB store base: connection management and index creation."""

import logging
import os
import time
from typing import TYPE_CHECKING, Any, Self

try:
    from pymongo import ASCENDING, MongoClient
    from pymongo.database import Database

    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False

    # Fallback constants so MongoStore works with mongomock without pymongo
    ASCENDING = 1

if TYPE_CHECKING:
    from ...config import MongoDBConfig


logger = logging.getLogger(__name__)


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


def _compute_next_retry_after(retry_count: int, now_ms: int) -> int:
    """Compute the next eligible retry time with exponential backoff.

    Backoff: 5s, 10s, 20s, 40s, 80s, 160s, 300s (capped at 5 minutes).
    """
    delay = min(5000 * (2**retry_count), 300_000)
    return now_ms + delay


def _stale_heartbeat_or(cutoff_ms: int) -> dict:
    """Mongo filter clause matching tasks with no recent heartbeat.

    A task's heartbeat is considered stale if ``task_heartbeat`` is absent,
    zero, or older than ``cutoff_ms``. Used by both reapers to avoid
    resetting tasks that are actively making progress.
    """
    return {
        "$or": [
            {"task_heartbeat": {"$exists": False}},
            {"task_heartbeat": 0},
            {"task_heartbeat": {"$lt": cutoff_ms}},
        ]
    }


def _reset_task_to_pending(
    now_ms: int,
    *,
    clear_error: bool = False,
    reset_retries: bool = False,
) -> dict:
    """Standard ``$set`` payload for resetting a task to ``pending``.

    Used by the orphan/stuck reapers and by the workflow-repair flow when
    a task needs to go back into the claim queue. The two flags cover the
    variations: ``clear_error`` blanks the prior error string, and
    ``reset_retries`` zeros ``retry_count`` (used only by the dead-letter
    re-enqueue path, which deliberately gives the task a fresh budget).
    """
    payload: dict[str, Any] = {
        "state": "pending",
        "server_id": "",
        "task_heartbeat": 0,
        "updated": now_ms,
    }
    if clear_error:
        payload["error"] = None
    if reset_retries:
        payload["retry_count"] = 0
    return payload


class BaseMixin:
    """Connection management, index creation, and from_config classmethod.

    This mixin is intended to be inherited by the final MongoStore class
    along with the other domain-specific mixins.
    """

    # Default lease duration: 5 minutes. Handlers must renew via heartbeat.
    DEFAULT_LEASE_MS = 300_000

    def _lease_ms(self) -> int:
        """Read the active lease duration, honoring ``AFL_LEASE_DURATION_MS``."""
        return int(os.environ.get("AFL_LEASE_DURATION_MS", str(self.DEFAULT_LEASE_MS)))

    @staticmethod
    def _find_decoded(collection: Any, query: dict, decoder: Any) -> Any:
        """``find_one(query)`` and decode, or return ``None`` if no match.

        Collapses the ``doc = …find_one(…); return decoder(doc) if doc else None``
        pattern that the per-entity ``get_*`` methods repeat verbatim.
        """
        doc = collection.find_one(query)
        return decoder(doc) if doc else None

    @staticmethod
    def _upsert_by_uuid(collection: Any, doc: dict) -> None:
        """``replace_one({"uuid": doc["uuid"]}, doc, upsert=True)``."""
        collection.replace_one({"uuid": doc["uuid"]}, doc, upsert=True)

    def __init__(
        self,
        connection_string: str = "",
        database_name: str = "facetwork",
        create_indexes: bool = True,
        client: Any = None,
    ):
        """Initialize the MongoDB store.

        Args:
            connection_string: MongoDB connection string
            database_name: Database name (default: "facetwork")
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
    ) -> Self:
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

        # Users collection
        users = self._db.users
        users.create_index("email", unique=True, name="user_email_index")
        users.create_index("status", name="user_status_index")
        users.create_index("teams", name="user_teams_index")

        # Teams collection
        teams = self._db.teams
        teams.create_index("uuid", unique=True, name="team_uuid_index")
        teams.create_index("name", unique=True, name="team_name_index")

        # Seed the built-in principals (system / claude / deleted / anonymous).
        # ``ensure_special_users`` is contributed by UsersMixin; it is always
        # present on the assembled MongoStore and is idempotent.
        self.ensure_special_users()  # type: ignore[attr-defined]

    def close(self) -> None:
        """Close the MongoDB connection."""
        self._client.close()

    def drop_database(self) -> None:
        """Drop the entire database. Use with caution!"""
        self._client.drop_database(self._db.name)
