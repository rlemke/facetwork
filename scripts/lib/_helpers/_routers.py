"""Per-region GraphHopper routers: desired regions -> leased slots -> containers.

graphhopper-web serves ONE graph per process, and the fleet used to run one
region everywhere (`roles.graphhopper.region`), so two low-zoom builds of
different regions could not run at once -- every build first repointed the
whole fleet's router and waited for it.

Here each region is a separate router, placed on whichever host claims it:

* ``fleet_routers`` (desired state, written by ``fw fleet router``)
      {_id: "<region>|<profile>", region, profile, replicas, heap,
       server_groups, added}
* ``fleet_router_slots`` (one per replica, written by fleet-agents)
      {_id: "<region>|<profile>|<i>", region, profile, replica, host, port,
       url, state: starting|ready|failed, bbox, lease_expires, updated}

Every agent runs :meth:`RouterReconciler.reconcile` each cycle. It renews or
releases the slots it holds, and claims AT MOST ONE free slot per cycle -- a
slot nobody holds, or whose lease has expired because its host went away. The
claim is a single conditional update, so two agents cannot both win, and
"one per cycle" spreads regions across hosts instead of letting the first
agent to wake take them all. There is no leader, the same shape as task
claiming.

Handlers find a router by region (fwh_osm `zoom_router.router_url`): a READY
slot whose lease is fresh. A host that dies stops renewing; its slots expire
after LEASE_S and another host claims them.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

DESIRED = "fleet_routers"
SLOTS = "fleet_router_slots"
LEASE_S = 300
# Published host ports. Kept well clear of the 8989 fleet-wide router and of
# 9000/9001, which a host running MinIO already holds (measured on the first
# placement: the claim got 9001 and the container failed "port is already
# allocated").
PORT_BASE = 18990
PORT_SPAN = 99
DEFAULT_HEAP = "4g"
DEFAULT_MAX_PER_HOST = 2
PROJECT_PREFIX = "fw-gh-"


def slug(region: str, profile: str) -> str:
    """Compose project name for a region+profile (lowercase, [a-z0-9_-])."""
    s = re.sub(r"[^a-z0-9]+", "-", f"{region}-{profile}".lower()).strip("-")
    return PROJECT_PREFIX + s[:50]


def desired_key(region: str, profile: str) -> str:
    return f"{region}|{profile}"


@dataclass
class Docker:
    """The container operations the reconciler needs (injected, so it is testable).

    start(project, region, profile, port, heap) -> bool
    state(project) -> "running" | "restarting" | "exited" | None (no container)
    probe(port) -> dict | None   (graphhopper /info when healthy, else None)
    stop(project) -> None
    projects() -> set[str]       (fw-gh-* projects with a container on this host)
    port_busy(port) -> bool      (something on this host already listens there)
    """

    start: Callable[[str, str, str, int, str], bool]
    state: Callable[[str], str | None]
    probe: Callable[[int], dict | None]
    stop: Callable[[str], None]
    projects: Callable[[], set]
    port_busy: Callable[[int], bool] = lambda port: False


class RouterReconciler:
    def __init__(self, db, host: str, group: str, address: str, docker: Docker,
                 max_here: int = DEFAULT_MAX_PER_HOST, default_groups: list | None = None,
                 log: Callable[[str], None] = print, now: Callable[[], float] = time.time) -> None:
        self.db = db
        self.host = host
        self.group = group
        self.address = address
        self.docker = docker
        self.max_here = max_here
        self.default_groups = default_groups or []
        self.log = log
        self.now = now

    def _in_group(self, spec: dict) -> bool:
        groups = spec.get("server_groups") or self.default_groups
        return (not groups) or (self.group in groups)

    def reconcile(self) -> dict:
        """One pass. Returns a summary {held, started, stopped, claimed}."""
        now = self.now()
        specs = {d["_id"]: d for d in self.db[DESIRED].find({})}
        slots = self.db[SLOTS]
        summary = {"held": [], "started": [], "stopped": [], "claimed": None}

        mine = list(slots.find({"host": self.host}))
        kept = []
        for s in mine:
            key = desired_key(s["region"], s["profile"])
            spec = specs.get(key)
            proj = slug(s["region"], s["profile"])
            if (spec is None or s["replica"] >= int(spec.get("replicas", 1))
                    or not self._in_group(spec)):
                self.docker.stop(proj)
                slots.delete_one({"_id": s["_id"], "host": self.host})
                summary["stopped"].append(proj)
                self.log(f"router {key}: released ({'removed' if spec is None else 'not wanted here'})")
                continue
            kept.append((s, spec, proj))

        for s, spec, proj in kept:
            st = self.docker.state(proj)
            # Anything but running/restarting is replaced: a container left
            # "created" by a failed start is never retried by Docker itself.
            if st not in ("running", "restarting"):
                if st is not None:
                    self.docker.stop(proj)
                ok = self.docker.start(proj, s["region"], s["profile"], s["port"],
                                       spec.get("heap") or DEFAULT_HEAP)
                summary["started"].append(proj)
                st = "running" if ok else "start-failed"
            info = self.docker.probe(s["port"]) if st == "running" else None
            # restarting = the entrypoint keeps exiting (e.g. no graph built)
            state = "ready" if info else ("failed" if st in ("restarting", "start-failed")
                                          else "starting")
            upd = {"lease_expires": now + LEASE_S, "updated": now, "state": state,
                   "url": f"http://{self.address}:{s['port']}"}
            if info and info.get("bbox"):
                upd["bbox"] = info["bbox"]
            if state != s.get("state"):
                self.log(f"router {s['_id']}: {s.get('state')} -> {state} ({upd['url']})")
            # Conditional on still holding it: another host may have taken an
            # expired lease while this agent was away.
            r = slots.update_one({"_id": s["_id"], "host": self.host}, {"$set": upd})
            if r.matched_count:
                summary["held"].append(s["_id"])
            else:
                self.docker.stop(proj)
                summary["stopped"].append(proj)
                self.log(f"router {s['_id']}: lost to another host; stopped here")

        # Containers this host runs but holds no slot for (a lost lease, a crash
        # between start and record): stop them, or two hosts serve one slot.
        held_projects = {slug(s["region"], s["profile"]) for s, _, _ in kept}
        for proj in self.docker.projects() - held_projects:
            self.docker.stop(proj)
            summary["stopped"].append(proj)
            self.log(f"router project {proj}: stray container (no slot held) -- stopped")

        if len(summary["held"]) < self.max_here:
            summary["claimed"] = self._claim_one(specs, {s["region"] + "|" + s["profile"]
                                                         for s, _, _ in kept}, now)
        return summary

    def _free_port(self) -> int:
        used = {s.get("port") for s in self.db[SLOTS].find({"host": self.host}, {"port": 1})}
        for p in range(PORT_BASE, PORT_BASE + PORT_SPAN):
            if p not in used and not self.docker.port_busy(p):
                return p
        raise RuntimeError("no free router port")

    def _claim_one(self, specs: dict, holding: set, now: float) -> str | None:
        slots = self.db[SLOTS]
        # Fewest ready replicas first, so a region with none is served before a
        # second replica of one that already works.
        def ready_count(key):
            return slots.count_documents({"_id": {"$regex": "^" + re.escape(key) + r"\|"},
                                          "state": "ready", "lease_expires": {"$gt": now}})
        for key in sorted(specs, key=ready_count):
            spec = specs[key]
            if key in holding or not self._in_group(spec):
                continue  # one replica of a region per host: a second adds no fault tolerance
            for i in range(int(spec.get("replicas", 1))):
                sid = f"{key}|{i}"
                doc = {"region": spec["region"], "profile": spec["profile"], "replica": i,
                       "host": self.host, "port": self._free_port(), "state": "starting",
                       "url": None, "lease_expires": now + LEASE_S, "updated": now,
                       "claimed": now}
                got = slots.find_one_and_update(
                    {"_id": sid, "lease_expires": {"$lt": now}}, {"$set": doc})
                if got is None:
                    try:
                        slots.insert_one({"_id": sid, **doc})
                    except Exception as exc:  # DuplicateKeyError: someone holds it
                        if "duplicate" not in str(exc).lower() and "E11000" not in str(exc):
                            raise
                        continue
                prev = (got or {}).get("host")
                self.log(f"router {sid}: claimed on port {doc['port']}"
                         + (f" (lease of {prev} expired)" if prev and prev != self.host else ""))
                proj = slug(spec["region"], spec["profile"])
                self.docker.start(proj, spec["region"], spec["profile"], doc["port"],
                                  spec.get("heap") or DEFAULT_HEAP)
                return sid
        return None
