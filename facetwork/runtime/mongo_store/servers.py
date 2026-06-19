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

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from ..entities import HandledCount, ServerDefinition, ServerState
from ._internals import _MixinBase
from .base import _current_time_ms

# States a server only reaches once it is *explicitly* dead: a graceful
# `_deregister_server` or the orphan reaper set it, stamping a fresh
# `ping_time`. Such records carry no live work to protect, so they are pruned
# on a short window rather than lingering for the full live grace period.
_TERMINAL_SERVER_STATES = (ServerState.SHUTDOWN,)


class ServerMixin(_MixinBase):
    """Server CRUD and heartbeat operations."""

    def get_server(self, server_id: str) -> ServerDefinition | None:
        """Get a server by ID."""
        return self._find_decoded(self._db.servers, {"uuid": server_id}, self._doc_to_server)

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
        self._upsert_by_uuid(self._db.servers, self._server_to_doc(server))

    def prune_stale_servers(
        self,
        older_than_ms: int = 600_000,
        terminal_older_than_ms: int | None = None,
    ) -> int:
        """Delete ServerDefinition records whose last ping is stale.

        Long-stopped containers leave heartbeat-stale records in
        ``db.servers``. Periodic pruning keeps the collection bounded so
        debug queries (`db.servers.find()`) stay readable and the
        dashboard's grouped-by-task_list view doesn't accumulate dead
        servers in the "null task_list" bucket from older agent versions.

        Two windows, because the two cases want different grace periods:

        - **Live states** (``running``/``startup``): pruned only after the
          generous ``older_than_ms`` window. A live runner that goes briefly
          quiet (GC pause, slow Mongo) must NOT be deleted out from under
          itself, so this stays long (default 10 min).
        - **Terminal states** (``shutdown``): the server is *explicitly* dead
          — a graceful ``_deregister_server`` or the reaper marked it so, and
          it stamped a fresh ``ping_time`` doing it. There is nothing to
          protect, so these are pruned on the much shorter
          ``terminal_older_than_ms`` window (default = ``older_than_ms``, but
          callers pass the reaper timeout ~2 min) instead of lingering for the
          full live window and inflating ``list-runners`` / dashboard counts.

        Returns the number of records deleted.
        """
        now = _current_time_ms()
        if terminal_older_than_ms is None:
            terminal_older_than_ms = older_than_ms
        live_cutoff = now - older_than_ms
        terminal_cutoff = now - terminal_older_than_ms
        result = self._db.servers.delete_many(
            {
                "$or": [
                    # Live (or unknown-state) servers: long grace window.
                    {
                        "state": {"$nin": list(_TERMINAL_SERVER_STATES)},
                        "ping_time": {"$lt": live_cutoff},
                    },
                    # Explicitly-dead servers: short grace window.
                    {
                        "state": {"$in": list(_TERMINAL_SERVER_STATES)},
                        "ping_time": {"$lt": terminal_cutoff},
                    },
                ]
            }
        )
        return result.deleted_count

    def dispatchable_facet_names(self, fresh_window_ms: int = 60_000) -> set[str]:
        """Union of handler facet names across all live runners.

        Used by the dashboard to flag tasks whose ``name`` no live runner
        can dispatch — the "runners listening but none with the right
        handler set" failure mode, which is otherwise indistinguishable
        from a healthy idle queue. Protocol prefixes (``fw:execute``,
        ``fw:resume``) are always considered dispatchable: every runner
        claims them, so we can't infer "no handler" from their absence.
        """
        cutoff = _current_time_ms() - fresh_window_ms
        names: set[str] = set()
        for doc in self._db.servers.find(
            {"ping_time": {"$gt": cutoff}},
            {"handlers": 1, "_id": 0},
        ):
            for h in doc.get("handlers", []) or []:
                names.add(h)
        return names

    def runners_per_task_list(self, fresh_window_ms: int = 60_000) -> dict[str, int]:
        """Return ``{task_list: live_runner_count}``.

        A server is "live" if its ``ping_time`` is within the last
        ``fresh_window_ms`` (default 60s). Used by the dashboard to flag
        empty lists (work piling up with no listeners) and overloaded
        lists at a glance.
        """
        cutoff = _current_time_ms() - fresh_window_ms
        out: dict[str, int] = {}
        for doc in self._db.servers.aggregate(
            [
                {"$match": {"ping_time": {"$gt": cutoff}}},
                {"$group": {"_id": "$task_list", "count": {"$sum": 1}}},
            ]
        ):
            name = doc.get("_id") or "default"
            out[name] = doc["count"]
        return out

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
        lease_ms = self._lease_ms()
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
        lease_ms = self._lease_ms()
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
            "task_list": server.task_list,
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
            task_list=doc.get("task_list", "default"),
        )
