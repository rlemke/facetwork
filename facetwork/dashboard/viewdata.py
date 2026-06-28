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

"""View-data builders for the dashboard's Runs / Servers / Handlers / Fleet /
PostGIS views.

Pure data-shaping helpers (filter, count, enrich, summarize) shared by the v3
route handlers. Extracted from the retired v2 page module; they render nothing
themselves — callers pass the results to their own templates.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from facetwork.runtime.persistence import PersistenceAPI

from .helpers import (
    compute_step_progress,
    effective_server_state,
    extract_handler_prefix,
    group_tasks_by_runner,
    group_tasks_by_state,
)

# Runner states by tab
_RUNNING_STATES = {"created", "running", "paused"}
_COMPLETED_STATES = {"completed"}
_FAILED_STATES = {"failed", "cancelled"}

_TAB_STATES = {
    "running": _RUNNING_STATES,
    "completed": _COMPLETED_STATES,
    "failed": _FAILED_STATES,
}


def _filter_runners(runners: list, tab: str) -> list:
    """Filter runners by tab selection."""
    allowed = _TAB_STATES.get(tab, _RUNNING_STATES)
    return [r for r in runners if r.state in allowed]


def _count_by_tab(runners: list) -> dict[str, int]:
    """Count runners per tab."""
    counts = {"running": 0, "completed": 0, "failed": 0}
    for r in runners:
        if r.state in _RUNNING_STATES:
            counts["running"] += 1
        elif r.state in _COMPLETED_STATES:
            counts["completed"] += 1
        elif r.state in _FAILED_STATES:
            counts["failed"] += 1
    return counts


def _filter_runners_by_team(runners: list, team: str | None) -> list:
    """Keep only runs listed for ``team`` (run.teams contains it)."""
    if not team:
        return runners
    return [r for r in runners if team in (getattr(r, "teams", None) or [])]


# ---------------------------------------------------------------------------
# Server views
# ---------------------------------------------------------------------------

_SERVER_TAB_STATES = {
    "running": {"running"},
    "startup": {"startup"},
    "error": {"error"},
    "shutdown": {"shutdown"},
    "down": {"down"},
}


def _apply_effective_state(servers: list) -> list:
    """Mutate each server's state to its effective value (e.g. 'down')."""
    for s in servers:
        s.state = effective_server_state(s)
    return servers


def _filter_servers(servers: list, tab: str) -> list:
    """Filter servers by tab selection."""
    allowed = _SERVER_TAB_STATES.get(tab, {"running"})
    return [s for s in servers if s.state in allowed]


def _count_servers_by_tab(servers: list) -> dict[str, int]:
    """Count servers per tab."""
    counts = {"running": 0, "startup": 0, "error": 0, "shutdown": 0, "down": 0}
    for s in servers:
        if s.state in counts:
            counts[s.state] += 1
    return counts


def _enrich_servers_with_tasks(servers: list, store: Any) -> None:
    """Attach active tasks to each server, avoiding N+1 queries."""
    # Bulk-fetch running + pending tasks and distribute by server_id
    tasks_by_server: dict[str, list] = {}
    all_tasks: list = []
    for state in ("running", "pending"):
        for t in store.get_tasks_by_state(state):
            sid = getattr(t, "server_id", "") or ""
            if sid:
                tasks_by_server.setdefault(sid, []).append(t)
            all_tasks.append(t)

    # Bulk-resolve step paths for display
    step_ids = [t.step_id for t in all_tasks if t.step_id]
    if step_ids:
        _resolve_task_step_paths(all_tasks, step_ids, store)

    for s in servers:
        s.active_tasks = tasks_by_server.get(s.uuid, [])
        s.active_task_count = len(s.active_tasks)


def _resolve_task_step_paths(tasks: list, step_ids: list[str], store: Any) -> None:
    """Build a display path for each task from its step hierarchy."""
    # Batch-fetch all referenced steps
    steps_cache: dict = {}
    for sid in step_ids:
        try:
            step = store.get_step(sid)
            if step:
                steps_cache[sid] = step
        except Exception:
            pass

    # Fetch container steps (two levels up)
    for _level in range(2):
        new_ids = {
            s.container_id
            for s in steps_cache.values()
            if getattr(s, "container_id", None) and s.container_id not in steps_cache
        }
        for cid in new_ids:
            try:
                step = store.get_step(cid)
                if step:
                    steps_cache[cid] = step
            except Exception:
                pass

    # Build path: grandparent > parent > step
    for t in tasks:
        if not t.step_id or t.step_id not in steps_cache:
            t.step_path = None
            continue
        step = steps_cache[t.step_id]
        # Walk up the container chain collecting names
        chain: list[str] = []
        current = step
        for _depth in range(3):  # self + 2 ancestor levels
            name = getattr(current, "statement_name", None) or getattr(current, "facet_name", None)
            if name:
                chain.append(name)
            cid = getattr(current, "container_id", None)
            if not cid or cid not in steps_cache:
                break
            current = steps_cache[cid]
        chain.reverse()
        t.step_path = " > ".join(chain) if chain else None


def _build_server_detail_context(server: Any, store: Any) -> dict:
    """Build the template context for a server detail page."""
    tasks = list(store.get_tasks_by_server_id(server.uuid, limit=500))
    task_groups = group_tasks_by_runner(tasks, store)
    task_counts = group_tasks_by_state(tasks)
    return {
        "task_groups": task_groups,
        "task_counts": task_counts,
    }


# ---------------------------------------------------------------------------
# Handler views
# ---------------------------------------------------------------------------


def _count_handlers_by_prefix(handlers: list) -> dict[str, int]:
    """Count handlers per namespace prefix tab, including 'all'."""
    counts: dict[str, int] = {"all": len(handlers)}
    for h in handlers:
        prefix = extract_handler_prefix(h.facet_name)
        counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def _filter_handlers_by_prefix(
    handlers: list, tab: str, busy_handlers: set[str] | None = None
) -> list:
    """Filter handlers by namespace prefix tab."""
    if tab == "all":
        return handlers
    if tab == "working":
        return [h for h in handlers if busy_handlers and h.facet_name in busy_handlers]
    return [h for h in handlers if extract_handler_prefix(h.facet_name) == tab]


def _build_handler_stats(
    store: Any,
) -> tuple[set[str], set[str], dict[str, dict[str, int]]]:
    """Build active/busy handler sets and aggregate handled counts.

    Returns:
        (active_handlers, busy_handlers, handler_stats) where:
        - active_handlers: facet names with at least one live runner
        - busy_handlers: facet names currently processing a running task
        - handler_stats: facet_name -> {"handled": N, "not_handled": N}
    """
    now_ms = int(time.time() * 1000)
    active_handlers: set[str] = set()
    handler_stats: dict[str, dict[str, int]] = {}

    for srv in store._db.servers.find():
        is_alive = srv.get("state") == "running" and (now_ms - srv.get("ping_time", 0)) < 60_000
        if is_alive:
            for h_name in srv.get("handlers", []):
                active_handlers.add(h_name)

        for entry in srv.get("handled", []):
            name = entry.get("handler", "")
            if name not in handler_stats:
                handler_stats[name] = {"handled": 0, "not_handled": 0}
            handler_stats[name]["handled"] += entry.get("handled", 0)
            handler_stats[name]["not_handled"] += entry.get("not_handled", 0)

    # Busy handlers: currently processing at least one running task
    busy_handlers: set[str] = set()
    for task in store._db.tasks.find({"state": "running"}, {"name": 1}):
        busy_handlers.add(task.get("name", ""))

    return active_handlers, busy_handlers, handler_stats


def _read_handler_source(module_uri: str) -> str | None:
    """Read handler source code from module_uri. Returns None if unreadable."""
    import os

    path = module_uri
    if path.startswith("file://"):
        path = path[7:]
    if not path.endswith(".py"):
        return None
    try:
        path = os.path.abspath(path)
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# PostGIS Summary
# ---------------------------------------------------------------------------


def _get_postgis_summary() -> dict | None:
    """Query PostGIS for region summary data."""
    try:
        import psycopg2
    except ImportError:
        return None

    postgis_url = os.environ.get(
        "AFL_POSTGIS_URL", "postgresql://afl:afl@afl-postgres:5432/afl_gis"
    )
    try:
        conn = psycopg2.connect(postgis_url, gssencmode="disable")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT il.region, il.node_count, il.way_count,
                       il.imported_at::text
                FROM osm_import_log il
                WHERE il.id IN (
                    SELECT DISTINCT ON (region) id
                    FROM osm_import_log
                    ORDER BY region, imported_at DESC
                )
                ORDER BY il.region
            """)
            rows = cur.fetchall()

            cur.execute("""
                SELECT pg_size_pretty(pg_database_size(current_database()))
            """)
            db_size = cur.fetchone()[0]
        conn.close()

        regions = []
        total_nodes = 0
        total_ways = 0
        for region, nodes, ways, imported_at in rows:
            total_nodes += nodes or 0
            total_ways += ways or 0
            regions.append(
                {
                    "region": region,
                    "node_count": nodes or 0,
                    "way_count": ways or 0,
                    "total": (nodes or 0) + (ways or 0),
                    "imported_at": imported_at or "",
                }
            )

        return {
            "regions": regions,
            "total_regions": len(regions),
            "total_nodes": total_nodes,
            "total_ways": total_ways,
            "total_elements": total_nodes + total_ways,
            "db_size": db_size,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Global search API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Progress enrichment
# ---------------------------------------------------------------------------


def _enrich_runners_with_progress(
    runners: list,
    store: PersistenceAPI,
) -> dict[str, dict]:
    """Compute step progress for a list of runners.

    Returns a dict mapping runner UUID to progress info.
    """
    progress: dict[str, dict] = {}
    for r in runners:
        try:
            steps = list(store.get_steps_by_workflow(r.workflow_id))
            progress[r.uuid] = compute_step_progress(r, steps)
        except Exception:
            progress[r.uuid] = {"completed": 0, "total": 0, "pct": 0}
    return progress


# =============================================================================
# Fleet — aggregate task counts by facet across all servers
# =============================================================================


def _domain_role_task_lists() -> dict[str, str]:
    """Map a fleet role name -> its namespace (task_list) from the domain catalog.

    Keyed both by the domain name and by the ``runner-<name>`` service suffix, so
    a role like ``jenkins-example`` (catalog key ``jenkins``, service
    ``runner-jenkins-example``) resolves too. Empty if the catalog is unavailable.
    """
    try:
        from ..domains import catalog

        doms = catalog.domains()
    except Exception:  # noqa: BLE001 - catalog optional; degrade to no mapping
        return {}
    out: dict[str, str] = {}
    for name, spec in doms.items():
        tl = spec.get("task_list")
        if not tl:
            continue
        out[name] = tl
        svc = spec.get("service") or ""
        if svc.startswith("runner-"):
            out[svc[len("runner-") :]] = tl
    return out


def _fleet_controller_data(store) -> dict:
    """Central fleet config + per-host reconcile drift (from the `fleet`/
    `fleet-agent` controller's fleet_config + fleet_agents collections). Returns
    {} if the controller has never been used."""
    try:
        db = store._db
        cfg = db.fleet_config.find_one({"_id": "default"})
    except Exception:
        return {}
    if not cfg:
        return {}
    desired = cfg.get("version")
    minio = cfg.get("minio") or {}
    eps = cfg.get("endpoints") or {}
    # Infra services are identified by access URL only — not enumerated as fleet
    # servers. Each may be a single node, a cluster, or a managed service.
    services = [
        {"name": "MongoDB", "url": eps.get("mongodb") or "—"},
        {"name": "MinIO", "url": eps.get("minio") or minio.get("endpoint") or "—"},
        {"name": "Dashboard", "url": eps.get("dashboard") or "—"},
    ]
    # Domain-runner roles don't carry a task_list in fleet_config (their namespace
    # is derived at runtime from loaded handlers). Resolve it from the domain
    # catalog so every role can link to its namespace handlers on the Fleet page.
    catalog_tl = _domain_role_task_lists()
    roles = []
    for name, spec in (cfg.get("roles") or {}).items():
        tl = spec.get("task_list") or catalog_tl.get(name) or ""
        # Every role links somewhere real: namespace-scoped roles → their handlers;
        # gh-router (embedded GraphHopper agent) → the osm routing handlers; any
        # other role (ffl-runner orchestration tier, …) → its runner processes.
        if tl:
            link = f"/v3/handlers?ns={tl}"
        elif name == "gh-router":
            link = "/v3/handlers?ns=osm"
        else:
            link = f"/v3/servers?group={name}"
        roles.append(
            {
                "name": name,
                "replicas": spec.get("replicas", "—"),
                "image": spec.get("image") or "—",
                "task_list": tl or "—",
                "link": link,
            }
        )
    agents = []
    for a in db.fleet_agents.find({}):
        av = a.get("applied_version")
        agents.append(
            {
                "host": a.get("host") or a.get("_id"),
                "applied_version": av,
                "applied_image": a.get("applied_image") or "—",
                "applied_at": a.get("applied_at") or "—",
                "uptodate": av == desired,
            }
        )
    agents.sort(key=lambda x: str(x["host"]))
    return {
        "config": {
            "version": desired,
            "minio": minio.get("endpoint") or "—",
            "bucket": minio.get("bucket") or "—",
            "updated_at": cfg.get("updated_at") or "—",
            "roles": roles,
            "services": services,
        },
        "agents": agents,
    }
