"""Weight long CPU-bound work toward fast hosts (``Placement(prefer = "fast")``).

Measured 2026-11-17 on the fleet stitched TESS search, once CPU booking had
removed oversubscription: one star-pass took a median 635 s on the fastest host
and 1,772-4,481 s on the other four, with every host given whole cores. Chunks
were spread evenly, so the run ended with the slowest hosts holding the last
chunks (7.5 h each on one of them) while the fast host sat idle.

The rule is the earliest-finish-time test of heterogeneous list scheduling
(HEFT, Topcuoglu et al. 2002): a host claims a task only if it would finish it
sooner than the hosts faster than it could get to it. With ``pending`` tasks
waiting and faster hosts working through them at ``sum(slots / seconds)``
tasks per second, a task left to them finishes after about
``pending / rate + fastest_seconds``. A slower host whose own median is
below that claims; otherwise it declines. So early in a run, with a long queue,
every host works; near the end only the fast ones take work.

Inputs, all measured rather than configured:

* seconds per task, per host: the handler run time the executing runner
  records for every completed ``Placement`` task (``task_timings``, keyed by
  host name so it survives runner restarts), compared only between tasks with
  the same ``requires.cpus``;
* slots per host: live advertised CPUs // the task's ``requires.cpus`` (1 when
  the task names no CPU floor), counting only hosts with a live runner that
  has the handler;
* pending: tasks of that facet waiting now.

Absent means unconstrained, as for every routing dimension: a host with no
timing yet (or no faster host with one) claims, so a new host or a new facet
behaves exactly as before. A store that cannot answer, or
``FW_CLAIM_PLACEMENT=0``, disables the gate. It is advisory: it only removes
names from what a host asks for, so it can delay a task but never strand it
while any host is willing (the fastest host never declines).
"""

from __future__ import annotations

import logging
import os
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

PLACEMENT_TTL_S = 30.0
PREFER_FAST = "fast"


def should_claim(mine: float | None, faster: Iterable[tuple[float, float]], pending: int) -> bool:
    """Earliest-finish test for one host.

    Args:
        mine: this host's median seconds for the task, or None if unmeasured.
        faster: ``(slots, median_seconds)`` of each live host faster than this one.
        pending: tasks of this kind waiting, including the one being considered.
    """
    if mine is None:
        return True
    hosts = [(s, d) for s, d in faster if s > 0 and d > 0]
    if not hosts:
        return True
    rate = sum(s / d for s, d in hosts)
    fastest = min(d for _, d in hosts)
    return mine <= max(pending, 1) / rate + fastest


def declined_facets(store: Any, server_name: str) -> set[str]:
    """Facets this host should not claim now (see module docstring)."""
    out: set[str] = set()
    if not server_name:
        return out
    for facet, (pending, cpus) in store.prefer_fast_demand().items():
        medians = {
            host: statistics.median(secs)
            for host, secs in store.facet_timings(facet, cpus).items()
            if secs
        }
        mine = medians.get(server_name)
        if mine is None:
            continue
        live = store.live_host_cpus(facet)
        faster: list[tuple[float, float]] = []
        for host, secs in medians.items():
            if host == server_name or secs >= mine or host not in live:
                continue
            cap = live[host]
            slots = int(cap // cpus) if cpus and cap else 1
            if slots > 0:
                faster.append((slots, secs))
        if not should_claim(mine, faster, pending):
            out.add(facet)
    return out


def record_timing(store: Any, task: Any, server_name: str, duration_ms: float) -> None:
    """Record a completed placement task's handler time; never raises."""
    if not (getattr(task, "placement", None) or {}).get("prefer"):
        return
    fn = getattr(store, "record_task_timing", None)
    if fn is None or not server_name:
        return
    cpus = (getattr(task, "requires", None) or {}).get("cpus") or 0.0
    try:
        fn(task.name, server_name, duration_ms / 1000.0, float(cpus))
    except Exception as exc:  # noqa: BLE001 — bookkeeping, not the task's outcome
        logger.debug("Could not record timing for %s: %s", task.name, exc)


class PlacementGate:
    """Cached ``declined_facets`` for one host, safe to call every claim cycle."""

    def __init__(
        self,
        store: Any,
        server_name: str,
        *,
        ttl_s: float = PLACEMENT_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._server_name = server_name
        self._ttl_s = ttl_s
        self._clock = clock
        self._declined: set[str] = set()
        self._at = float("-inf")
        self._warned = False

    def declined(self) -> set[str]:
        if os.environ.get("FW_CLAIM_PLACEMENT", "1") == "0" or not self._server_name:
            return set()
        if not all(
            hasattr(self._store, m)
            for m in ("prefer_fast_demand", "facet_timings", "live_host_cpus")
        ):
            return set()
        now = self._clock()
        if now - self._at < self._ttl_s:
            return self._declined
        try:
            declined = declined_facets(self._store, self._server_name)
        except Exception as exc:  # noqa: BLE001 — a claim must never die on this
            if not self._warned:
                self._warned = True
                logger.warning(
                    "Cannot evaluate fast-host placement on %s (%s); claiming without it",
                    self._server_name,
                    exc,
                )
            declined = set()
        if declined != self._declined and declined:
            logger.info(
                "Placement: %s leaves %s to faster hosts", self._server_name, sorted(declined)
            )
        self._declined, self._at = declined, now
        return declined

    def strip(self, claims: Sequence[tuple]) -> list[tuple]:
        """``claims`` with declined facet names removed (entries keep their shape)."""
        declined = self.declined()
        if not declined:
            return list(claims)
        out: list[tuple] = []
        for entry in claims:
            names = [n for n in entry[0] if n not in declined]
            out.append((names, *entry[1:]))
        return out
