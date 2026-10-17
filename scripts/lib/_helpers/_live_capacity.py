"""Live-memory starvation verdict for `fw maint unsatisfiable`.

A memory floor can fit some host's CAPACITY and still never be claimed: a
runner offers ``min(capacity, free-now)`` when claiming (``claim_resources``),
and idle runners hold their baseline footprint, so no host may ever have the
floor FREE. Measured 2026-10-16: africa/uganda BuildZoomLayers
(``memory_gb=13``) sat pending 111 min with zero tasks running fleet-wide while
the capacity-only check reported OK.

Runners publish live availability on each heartbeat as ``free_resources``
(``{"memory_gb": x, "at": ping_ms}``). This module only READS those records; it
is pure so it can be tested without Mongo.
"""
from __future__ import annotations


def host_free_memory(live_servers: list[dict], now_ms: float,
                     max_age_ms: float) -> dict[str, float]:
    """Best fresh free-memory reading per host.

    Every runner on a host reports (nearly) the same host number, capped by its
    own cgroup headroom, so the host's offer is the MAX across its runners.
    Readings older than ``max_age_ms`` are ignored; a host with none is absent
    — unknown, never assumed zero.
    """
    out: dict[str, float] = {}
    for s in live_servers:
        fr = s.get("free_resources") or {}
        gb, at = fr.get("memory_gb"), fr.get("at")
        if not isinstance(gb, (int, float)) or not isinstance(at, (int, float)):
            continue
        if now_ms - at > max_age_ms:
            continue
        host = str(s.get("server_name") or "?")
        out[host] = max(out.get(host, 0.0), float(gb))
    return out


def starved(want_gb: float, free_by_host: dict[str, float],
            capable_hosts: set[str]) -> bool | None:
    """Is a floor of ``want_gb`` currently unclaimable for lack of FREE memory?

    Only hosts whose CAPACITY fits are considered (the capacity dimension is
    judged separately). Returns None — could not tell — when none of those
    hosts publishes a live reading (older images), and also when ANY of them
    is unread: the unread one may have the memory free, and absence of
    evidence must not raise an alarm. Mid-rollout this reports less, never
    falsely.
    """
    if not capable_hosts or any(h not in free_by_host for h in capable_hosts):
        return None
    return max(free_by_host[h] for h in capable_hosts) < want_gb
