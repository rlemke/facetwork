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

"""Server CRUD operations mixin for MongoStore."""

import os
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from ..entities import HandledCount, ServerDefinition

from .base import _current_time_ms


class ServerMixin:
    """Server CRUD and heartbeat operations."""

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

    def update_task_stage_budget(
        self,
        task_id: str,
        budget_expires: int,
        stage_name: str = "",
    ) -> None:
        """Set the stage-budget deadline for a running task.

        The runner watchdog treats ``stage_budget_expires`` as an override
        on the global execution timeout: a task is only killed when *both*
        the global deadline (``now - last_activity > execution_timeout_ms``)
        and the stage deadline (``now > stage_budget_expires``) have passed.
        Also renews the lease so the task isn't reclaimed while the stage runs.
        """
        lease_ms = int(os.environ.get("AFL_LEASE_DURATION_MS", str(self.DEFAULT_LEASE_MS)))
        now = _current_time_ms()
        update: dict[str, Any] = {
            "stage_budget_expires": budget_expires,
            "stage_name": stage_name,
            "task_heartbeat": now,
            "lease_expires": max(now + lease_ms, budget_expires),
        }
        self._db.tasks.update_one(
            {"uuid": task_id, "state": "running"},
            {"$set": update},
        )

    # =========================================================================
    # Serialization Helpers — Servers
    # =========================================================================

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
