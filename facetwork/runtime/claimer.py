"""Per-host claimer: one database poller for every runner on a machine.

Every runner used to poll MongoDB for itself. After the single-query change
(``claim_any``) an idle runner still costs ~0.2-1 query/s, and the runners on a
host decide independently, so three can each see "15 GB free" and claim three
12 GB builds at once (measured 2026-10-16 and 2026-10-19). The claimer fixes
both by owning the claim for the whole host:

* A runner with a free slot long-polls ``POST /next`` with the SAME spec it
  would pass to ``claim_any`` (claims, known features, resources, script
  environments). The open request is its registration: a busy or dead runner is
  simply not asking, so nothing goes stale.
* Each cycle the claimer merges every waiting runner's spec into ONE
  ``claim_any`` query. Each entry carries its own runner's capabilities, so the
  merged query is exactly the union of what the runners would have claimed.
* It claims under its OWN server record (which it heartbeats), then hands the
  task to a matching runner with an ownership-gated write. If the claimer dies
  between the two, the dead-server reaper resets the task as for any runner.
* It keeps a memory LEDGER: memory floors of tasks it handed out recently are
  subtracted from what later claims offer, until the task finishes or the hold
  expires. A claim checks free memory but reserves nothing, so without this
  several big tasks fit into the same gap.

A runner finds the claimer through ``FW_CLAIM_PROXY_URL``; without it, or when
the claimer is unreachable, the runner claims directly as before, so a missing
claimer makes a host noisier, never stuck.

The API is plain JSON over HTTP so an agent in any language can use it::

    POST /next  {"server_id": "...", "claims": [[["ns.Facet"], ["ns"]], ...],
                 "known_features": [...], "resources": {"memory_gb": 12.5},
                 "script_environments": [...], "wait_s": 5, "kick": false}
             -> {"task": "<uuid>"} | {"task": null}
    POST /kick  -> {}            (work may be waiting: query now)
    GET  /status, GET /health

The answer is a task UUID; the runner reads the task itself (one indexed read,
only on a hit), so the claimer never serialises a task document.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import threading
import time
import uuid as _uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .entities import ServerDefinition, ServerState, TaskState


def _current_time_ms() -> int:
    return int(time.time() * 1000)


logger = logging.getLogger(__name__)

DEFAULT_PORT = 8790
SERVICE_NAME = "claimer"


def _name_matches(name: str, names: list[str]) -> bool:
    """``claim_task`` name matching: exact, or the ``<n>:`` prefix form."""
    return any(name == n or name.startswith(f"{n}:") for n in names)


def _list_matches(task_list: str, tl: str | list[str]) -> bool:
    return task_list in tl if isinstance(tl, (list, tuple, set)) else task_list == tl


@dataclass
class Waiter:
    """One runner's open request for a task."""

    server_id: str
    claims: list[tuple[list[str], Any]]
    known_features: list[str] = field(default_factory=list)
    resources: dict = field(default_factory=dict)
    script_environments: list[str] = field(default_factory=list)
    since: float = 0.0
    deadline: float = 0.0
    min_cycle: int = 0  # answer no earlier than the end of this query cycle
    task: Any = None
    # The resources offered for this waiter in the cycle that claimed its task:
    # matching uses the SAME numbers the query used.
    offered: dict = field(default_factory=dict)


def spec_accepts(waiter: Waiter, task: Any, resources: dict | None = None) -> bool:
    """Whether ``claim_any`` with this waiter's spec could have claimed ``task``.

    Mirrors the filters in ``MongoStore.claim_any``: feature and resource
    routing for every task, then the script branch (by environment) or the name
    branches (by name and list). Used to pick which waiting runner receives a
    task the merged query returned.
    """
    needed = set(getattr(task, "required_features", None) or [])
    if not needed <= set(waiter.known_features or []):
        return False
    have = resources if resources is not None else waiter.resources
    for dim, want in (getattr(task, "requires", None) or {}).items():
        cap = (have or {}).get(dim)
        if not isinstance(cap, (int, float)) or want > cap:
            return False
    env = getattr(task, "environment_hash", "") or ""
    if getattr(task, "kind", "") == "script":
        return bool(env) and env in waiter.script_environments
    if env:  # runners claim name branches with no provided_environments
        return False
    return any(
        names and _name_matches(task.name, list(names)) and _list_matches(task.task_list_name, tl)
        for names, tl in waiter.claims
    )


class Claimer:
    """Claims for every runner on this host; see the module docstring."""

    def __init__(
        self,
        store: Any,
        *,
        server_name: str = "",
        server_group: str = "claimer",
        idle_max_s: float | None = None,
        min_gap_s: float = 0.25,
        reserve_hold_s: float | None = None,
        reclaim_scan_s: float = 30.0,
        heartbeat_s: float = 10.0,
        clock=time.monotonic,
    ) -> None:
        self._store = store
        self.server_id = str(_uuid.uuid4())
        self._server_name = server_name or socket.gethostname()
        self._server_group = server_group
        self._idle_max_s = (
            idle_max_s
            if idle_max_s is not None
            else float(os.environ.get("FW_CLAIMER_IDLE_MAX_MS", "5000")) / 1000.0
        )
        self._min_gap_s = min_gap_s
        # How long a handed-out memory floor stays reserved. A task's process
        # takes minutes to grow to its floor; until it does, live free memory
        # still shows the space and a second big task would fit into it.
        self._reserve_hold_s = (
            reserve_hold_s
            if reserve_hold_s is not None
            else float(os.environ.get("FW_CLAIMER_RESERVE_HOLD_S", "600"))
        )
        self._reclaim_scan_s = reclaim_scan_s
        self._heartbeat_s = heartbeat_s
        self._clock = clock

        self._cond = threading.Condition()
        self._waiters: list[Waiter] = []
        self._cycle_no = 0
        self._kicked = False
        self._idle = 0
        self._next_query_at = 0.0
        self._last_query = -1e9
        self._last_reclaim_scan = -1e9
        self._reserved: dict[str, tuple[float, float]] = {}  # uuid -> (gb, at)
        self._last_reserve_check = 0.0
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []
        self.stats = {"queries": 0, "claimed": 0, "handed": 0, "released": 0, "requests": 0}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._register()
        for target, name in ((self._loop, "claimer-loop"), (self._heartbeat_loop, "claimer-hb")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stopping.set()
        with self._cond:
            self._cond.notify_all()
        for t in self._threads:
            t.join(timeout=5)
        try:
            server = self._store.get_server(self.server_id)
            if server:
                server.state = ServerState.SHUTDOWN
                server.ping_time = _current_time_ms()
                self._store.save_server(server)
        except Exception:  # noqa: BLE001 — best effort on the way out
            logger.debug("claimer: deregister failed", exc_info=True)

    def _register(self) -> None:
        now = _current_time_ms()
        self._store.save_server(
            ServerDefinition(
                uuid=self.server_id,
                server_group=self._server_group,
                service_name=SERVICE_NAME,
                server_name=self._server_name,
                start_time=now,
                ping_time=now,
                state=ServerState.RUNNING,
                task_list="",
            )
        )

    def _heartbeat_loop(self) -> None:
        # The claimer holds tasks for a few milliseconds between claim and
        # hand-over. If it dies in that window, the reaper resets those tasks
        # only because this record goes silent, so it must heartbeat like a runner.
        while not self._stopping.wait(self._heartbeat_s):
            try:
                if not self._store.heartbeat_server(self.server_id, _current_time_ms()):
                    self._register()
            except Exception:  # noqa: BLE001 — a heartbeat must not die
                logger.warning("claimer: heartbeat failed", exc_info=True)

    # -- runner side -----------------------------------------------------------

    def request(self, spec: dict, wait_s: float = 5.0, kick: bool = False) -> str | None:
        """Block until a task is handed to this runner, or ``wait_s`` passes.

        Returns the task UUID, already owned by ``spec["server_id"]``.
        ``kick`` (or ``wait_s == 0``) asks for a query now and waits for it.
        """
        now = self._clock()
        w = Waiter(
            server_id=spec["server_id"],
            claims=[(list(c[0]), c[1]) for c in spec.get("claims") or [] if c and c[0]],
            known_features=list(spec.get("known_features") or []),
            resources=dict(spec.get("resources") or {}),
            script_environments=list(spec.get("script_environments") or []),
            since=now,
            deadline=now + max(0.0, wait_s),
        )
        if not w.claims and not w.script_environments:
            return None
        with self._cond:
            self.stats["requests"] += 1
            # A runner asks again only after it gave up or took a task, so an
            # older request from it is dead: drop it rather than route to it.
            self._waiters = [x for x in self._waiters if x.server_id != w.server_id]
            if kick or wait_s <= 0:
                w.min_cycle = self._cycle_no + 1
                self._kicked = True
            self._waiters.append(w)
            self._cond.notify_all()
            while w.task is None and not self._stopping.is_set():
                left = w.deadline - self._clock()
                if left <= 0 and self._cycle_no >= w.min_cycle:
                    break
                self._cond.wait(timeout=max(0.05, left) if left > 0 else 0.5)
            if w.task is None and w in self._waiters:
                self._waiters.remove(w)
            task = w.task
        if task is None:
            return None
        return task.uuid if self._hand_over(task, w.server_id) else None

    def kick(self) -> None:
        with self._cond:
            self._kicked = True
            self._idle = 0
            self._cond.notify_all()

    def _hand_over(self, task: Any, server_id: str) -> bool:
        """Re-own the task from the claimer to the runner, gated on ownership.

        False when the task stopped being ours in between (reaped or
        reclaimed): the runner then gets nothing, which is correct.
        """
        task.server_id = server_id
        try:
            ok = self._store.save_task_if_owned(task, self.server_id)
        except Exception:  # noqa: BLE001
            logger.warning("claimer: hand-over of %s failed", task.name, exc_info=True)
            ok = False
        if ok:
            self.stats["handed"] += 1
        return ok

    def _release(self, task: Any) -> None:
        """Give a claimed task back to the queue (no runner can take it)."""
        task.state = TaskState.PENDING
        task.server_id = ""
        try:
            if self._store.save_task_if_owned(task, self.server_id):
                self.stats["released"] += 1
        except Exception:  # noqa: BLE001 — the reaper recovers it if this fails
            logger.warning("claimer: release of %s failed", task.name, exc_info=True)

    # -- the poll loop -----------------------------------------------------------

    def _loop(self) -> None:
        while not self._stopping.is_set():
            with self._cond:
                if not self._waiters:
                    self._cond.wait(timeout=1.0)
                    continue
                now = self._clock()
                if not self._kicked and now < self._next_query_at:
                    self._cond.wait(timeout=self._next_query_at - now)
                    continue
                self._kicked = False
            gap = self._min_gap_s - (self._clock() - self._last_query)
            if gap > 0:
                self._stopping.wait(gap)
            try:
                got = self.cycle()
            except Exception:  # noqa: BLE001 — a Mongo blip must not kill the loop
                logger.warning("claimer: claim cycle failed", exc_info=True)
                got = False
            with self._cond:
                if got:
                    self._idle = 0
                    self._next_query_at = self._clock()
                else:
                    self._idle = min(self._idle + 1, 16)
                    self._next_query_at = self._clock() + min(
                        self._idle_max_s, 1.0 * 2 ** (self._idle - 1)
                    )

    def cycle(self) -> bool:
        """One merged claim for the current waiters. True if a task was handed out."""
        with self._cond:
            waiters = list(self._waiters)
        if not waiters:
            return False
        reserved = self.reserved_gb()
        entries: list[tuple] = []
        seen: set[str] = set()
        scripts: list[tuple] = []
        for w in waiters:
            offered = dict(w.resources)
            if isinstance(offered.get("memory_gb"), (int, float)):
                offered["memory_gb"] = max(0.0, offered["memory_gb"] - reserved)
            w.offered = offered
            caps = {"known_features": w.known_features, "resources": offered}
            for names, tl in w.claims:
                key = json.dumps([sorted(names), tl, caps], sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    entries.append((names, tl, caps))
            if w.script_environments:
                # Per runner, like the name entries: a script task's memory
                # floor must be checked against THIS runner's offer, never a
                # pooled list that any runner's environments could satisfy.
                envs = sorted(w.script_environments)
                key = json.dumps(["<script>", envs, caps], sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    scripts.append((envs, caps))

        now = self._clock()
        reclaim = now - self._last_reclaim_scan >= self._reclaim_scan_s
        self._last_query = now
        self.stats["queries"] += 1
        task = self._store.claim_any(
            entries,
            server_id=self.server_id,
            # Every entry carries its own runner's capabilities. These defaults
            # are the strictest possible, so an entry that somehow lacked them
            # would claim only unconstrained work, never too much.
            resources={},
            known_features=[],
            script_claims=scripts,
            reclaim_expired=reclaim,
        )
        if task is None:
            if reclaim:
                self._last_reclaim_scan = now
            self._finish_cycle()
            return False
        self.stats["claimed"] += 1

        with self._cond:
            owner = next(
                (w for w in self._waiters if w.task is None and spec_accepts(w, task, w.offered)),
                None,
            )
            if owner is not None:
                self._waiters.remove(owner)
        if owner is None:
            # Every runner that could take it gave up while we queried.
            self._release(task)
            self._finish_cycle()
            return False
        mem = (getattr(task, "requires", None) or {}).get("memory_gb")
        if isinstance(mem, (int, float)) and mem > 0:
            self._reserved[task.uuid] = (float(mem), self._clock())
        with self._cond:
            owner.task = task
        self._finish_cycle()
        return True

    def _finish_cycle(self) -> None:
        with self._cond:
            self._cycle_no += 1
            self._cond.notify_all()

    # -- memory ledger -------------------------------------------------------------

    def reserved_gb(self) -> float:
        """Memory promised to tasks handed out recently and still running."""
        now = self._clock()
        self._reserved = {
            k: v for k, v in self._reserved.items() if now - v[1] < self._reserve_hold_s
        }
        if self._reserved and now - self._last_reserve_check >= 30.0:
            self._last_reserve_check = now
            for uid in list(self._reserved):
                try:
                    t = self._store.get_task(uid)
                except Exception:  # noqa: BLE001 — keep the reservation on doubt
                    continue
                if t is None or t.state != TaskState.RUNNING:
                    self._reserved.pop(uid, None)
        return round(sum(gb for gb, _ in self._reserved.values()), 2)

    def status(self) -> dict:
        with self._cond:
            waiting = [
                {"server_id": w.server_id, "waiting_s": round(self._clock() - w.since, 1)}
                for w in self._waiters
            ]
        return {
            "server_id": self.server_id,
            "waiting": waiting,
            "idle_cycles": self._idle,
            "reserved_gb": self.reserved_gb(),
            "reservations": len(self._reserved),
            **self.stats,
        }


# -- HTTP ---------------------------------------------------------------------------


def make_handler(claimer: Claimer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet: one line per poll
            logger.debug("claimer http: " + fmt, *args)

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 — http.server API
            if self.path == "/health":
                self._send(200, {"ok": True})
            elif self.path == "/status":
                self._send(200, claimer.status())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 — http.server API
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"error": "body must be JSON"})
                return
            if self.path == "/kick":
                claimer.kick()
                self._send(200, {})
                return
            if self.path != "/next":
                self._send(404, {"error": "not found"})
                return
            if not body.get("server_id") or not isinstance(body.get("claims", []), list):
                self._send(400, {"error": "server_id and claims are required"})
                return
            wait_s = min(30.0, max(0.0, float(body.get("wait_s", 5.0))))
            uid = claimer.request(body, wait_s=wait_s, kick=bool(body.get("kick")))
            try:
                self._send(200, {"task": uid})
            except OSError:
                # The runner went away after the hand-over. Its reconcile resets
                # a running task it does not hold, so nothing is lost.
                logger.warning("claimer: runner %s gone before reply", body.get("server_id"))

    return Handler


def serve(claimer: Claimer, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(claimer))
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--port", type=int, default=int(os.environ.get("FW_CLAIMER_PORT", DEFAULT_PORT))
    )
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--server-name", default=os.environ.get("FW_FLEET_HOST", ""))
    ap.add_argument("--log-level", default=os.environ.get("FW_LOG_LEVEL", "INFO"))
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s"
    )

    from ..config import load_config
    from .mongo_store import MongoStore

    store = MongoStore.from_config(load_config().mongodb)
    claimer = Claimer(store, server_name=args.server_name)
    claimer.start()
    httpd = serve(claimer, args.host, args.port)
    logger.info("claimer %s listening on %s:%d", claimer.server_id, args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        claimer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
