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

"""Global Flow / Workflow view filters.

A single persisted cookie (``afl_filters``) holds the user's global filter
selections for the Library (Flows) and Runs (Workflows) views. The filter page
writes the cookie; the two list routes read it and narrow what they show.

An empty selection means **Any** — it matches everything. So a fresh cookie (or
no cookie) shows everything, exactly as before.
"""

from __future__ import annotations

import calendar
import json
import time
from typing import Any

FILTERS_COOKIE = "afl_filters"

# ── shape ────────────────────────────────────────────────────────────────────────
_EMPTY: dict[str, dict[str, Any]] = {
    "flows": {"teams": [], "author": "", "created_from": "", "created_to": "", "tag": ""},
    "workflows": {
        "teams": [],
        "workflows": [],
        "user": "",
        "run_from": "",
        "run_to": "",
        "state": "any",
        "purpose": "any",
    },
}

# state filter value → matching runner states
_STATE_GROUPS = {
    "running": {"created", "running", "paused"},
    "completed": {"completed"},
    "failed": {"failed", "cancelled"},
}


def empty_filters() -> dict[str, Any]:
    """A fresh, match-everything filter set."""
    return json.loads(json.dumps(_EMPTY))


def read_filters(request) -> dict[str, Any]:
    """Parse the filter cookie into the normalized shape (Any-everywhere default)."""
    raw = request.cookies.get(FILTERS_COOKIE)
    f = empty_filters()
    if not raw:
        return f
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return f
    if not isinstance(data, dict):
        return f
    for section in ("flows", "workflows"):
        sec = data.get(section) or {}
        for k, default in _EMPTY[section].items():
            v = sec.get(k, default)
            if isinstance(default, list):
                f[section][k] = [str(x) for x in v] if isinstance(v, list) else []
            else:
                f[section][k] = str(v) if v is not None else ""
    return f


def is_active(f: dict[str, Any]) -> bool:
    """True when any non-default selection is present."""
    return bool(flows_active(f) or workflows_active(f))


# ── date helpers ────────────────────────────────────────────────────────────────


def _day_start_ms(yyyy_mm_dd: str) -> int | None:
    try:
        t = time.strptime(yyyy_mm_dd, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return calendar.timegm(t) * 1000


def _in_range_ms(ts_ms: int | None, frm: str, to: str) -> bool:
    """Is ``ts_ms`` within [frm, to] (inclusive, day-granular)? Unset bounds = open."""
    f_ms = _day_start_ms(frm) if frm else None
    to_start = _day_start_ms(to) if to else None
    t_ms = (to_start + 86_400_000 - 1) if to_start is not None else None
    if f_ms is None and t_ms is None:
        return True
    if not ts_ms:  # unknown timestamp can't satisfy an explicit bound
        return False
    if f_ms is not None and ts_ms < f_ms:
        return False
    if t_ms is not None and ts_ms > t_ms:
        return False
    return True


# ── derived attributes ───────────────────────────────────────────────────────────


def flow_teams(flow) -> set[str]:
    from .routes.execution.flows import _flow_teams

    return _flow_teams(flow)


def flow_author(flow) -> str:
    """Best-effort author email/name for a flow."""
    pub = getattr(flow, "publisher", None)
    if pub and getattr(pub, "email", ""):
        return pub.email
    own = getattr(flow, "ownership", None)
    if own and getattr(own, "owner", None) and getattr(own.owner, "email", ""):
        return own.owner.email
    ast = getattr(flow, "compiled_ast", None)
    if ast:
        try:
            from facetwork.capabilities import author_and_teams

            def _walk(decls):
                for d in decls or []:
                    if not isinstance(d, dict):
                        continue
                    if d.get("type") == "Namespace":
                        r = _walk(d.get("declarations") or d.get("body") or [])
                        if r:
                            return r
                    elif d.get("type") in ("WorkflowDecl", "Workflow"):
                        a, _t = author_and_teams(d.get("mixins"))
                        if a:
                            return a
                return ""

            a = _walk(ast.get("declarations"))
            if a:
                return a
        except Exception:
            pass
    for wf in getattr(flow, "workflows", []) or []:
        md = getattr(wf, "metadata", None)
        if md and getattr(md, "author", ""):
            return md.author
    return ""


def flow_tags(flow) -> set[str]:
    tags: set[str] = set()
    cls = getattr(flow, "classification", None)
    if cls and getattr(cls, "tags", None):
        tags.update(cls.tags)
    for wf in getattr(flow, "workflows", []) or []:
        md = getattr(wf, "metadata", None)
        if md and getattr(md, "tags", None):
            tags.update(md.tags)
    return tags


def flow_created_ms(flow) -> int | None:
    """A flow's creation time (epoch ms).

    Prefer the real ``created_at`` stamped at publish time; for legacy flows
    saved before that field existed, fall back to the earliest workflow date.
    """
    created = int(getattr(flow, "created_at", 0) or 0)
    if created > 0:
        return created
    dates = [int(getattr(wf, "date", 0) or 0) for wf in getattr(flow, "workflows", []) or []]
    dates = [d for d in dates if d > 0]
    return min(dates) if dates else None


def runner_user(runner) -> str:
    u = getattr(runner, "user", None) or getattr(runner, "author", None)
    return getattr(u, "email", "") if u else ""


# ── matching ──────────────────────────────────────────────────────────────────────


def flow_matches(flow, f: dict[str, Any]) -> bool:
    s = f["flows"]
    if s["teams"] and not (flow_teams(flow) & set(s["teams"])):
        return False
    if s["author"] and flow_author(flow) != s["author"]:
        return False
    if s["tag"] and s["tag"] not in flow_tags(flow):
        return False
    if (s["created_from"] or s["created_to"]) and not _in_range_ms(
        flow_created_ms(flow), s["created_from"], s["created_to"]
    ):
        return False
    return True


def runner_matches(runner, f: dict[str, Any]) -> bool:
    s = f["workflows"]
    if s["teams"] and not (set(getattr(runner, "teams", []) or []) & set(s["teams"])):
        return False
    if s["workflows"]:
        wf = getattr(runner, "workflow", None)
        name = getattr(wf, "name", "") if wf else ""
        if name not in s["workflows"]:
            return False
    if s["user"] and runner_user(runner) != s["user"]:
        return False
    if s["purpose"] and s["purpose"] != "any" and getattr(runner, "purpose", "") != s["purpose"]:
        return False
    if s["state"] and s["state"] != "any":
        if getattr(runner, "state", "") not in _STATE_GROUPS.get(s["state"], set()):
            return False
    if (s["run_from"] or s["run_to"]) and not _in_range_ms(
        getattr(runner, "start_time", 0), s["run_from"], s["run_to"]
    ):
        return False
    return True


# ── option-list builders (populate the filter page selects) ────────────────────────


def build_options(store) -> dict[str, Any]:
    """Gather the selectable values for the filter page from live data."""
    flows = [f for f in store.get_all_flows() if getattr(f.name, "path", "") != "cli:submit"]
    runners = store.get_all_runners(limit=1000)

    team_names: set[str] = {t.name for t in store.list_teams()}
    flow_authors: set[str] = set()
    flow_tagset: set[str] = set()
    for fl in flows:
        team_names |= flow_teams(fl)
        a = flow_author(fl)
        if a:
            flow_authors.add(a)
        flow_tagset |= flow_tags(fl)

    run_users: set[str] = set()
    # workflow name -> set of teams it has run under (for the team-dependent list)
    wf_teams: dict[str, set[str]] = {}
    purposes: set[str] = set()
    for r in runners:
        team_names |= set(getattr(r, "teams", []) or [])
        u = runner_user(r)
        if u:
            run_users.add(u)
        wf = getattr(r, "workflow", None)
        name = getattr(wf, "name", "") if wf else ""
        if name:
            wf_teams.setdefault(name, set()).update(getattr(r, "teams", []) or [])
        if getattr(r, "purpose", ""):
            purposes.add(r.purpose)

    return {
        "teams": sorted(team_names),
        "flow_authors": sorted(flow_authors),
        "flow_tags": sorted(flow_tagset),
        "run_users": sorted(run_users),
        "workflows": sorted(
            ({"name": n, "teams": sorted(t)} for n, t in wf_teams.items()),
            key=lambda x: x["name"],
        ),
        "purposes": sorted(purposes),
    }


# ── active-filter summaries (for the banner on each list page) ─────────────────────


def flows_active(f: dict[str, Any]) -> list[str]:
    s = f["flows"]
    out = []
    if s["teams"]:
        out.append("teams: " + ", ".join(s["teams"]))
    if s["author"]:
        out.append("author: " + s["author"])
    if s["tag"]:
        out.append("tag: " + s["tag"])
    if s["created_from"] or s["created_to"]:
        out.append("created " + (s["created_from"] or "…") + " → " + (s["created_to"] or "…"))
    return out


def workflows_active(f: dict[str, Any]) -> list[str]:
    s = f["workflows"]
    out = []
    if s["teams"]:
        out.append("teams: " + ", ".join(s["teams"]))
    if s["workflows"]:
        out.append(f"{len(s['workflows'])} workflow(s)")
    if s["user"]:
        out.append("ran by: " + s["user"])
    if s["state"] and s["state"] != "any":
        out.append("state: " + s["state"])
    if s["purpose"] and s["purpose"] != "any":
        out.append("purpose: " + s["purpose"])
    if s["run_from"] or s["run_to"]:
        out.append("run " + (s["run_from"] or "…") + " → " + (s["run_to"] or "…"))
    return out
