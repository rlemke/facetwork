"""Host names and addresses for tests — no test file spells one itself.

Tests must not publish the names or addresses of any real deployment, and must
run unchanged on any cloud, network or service configuration. So a test never
writes a host name or an IP: it asks this module for one, by ROLE.

Where the values come from, in order:

1. An UNCOMMITTED site file — ``$FW_TEST_SITE``, else ``tests/site.local.json``
   (gitignored). A deployment that wants its own naming in test output puts it
   there; the repo never carries it. Shape::

       {"hosts": {"infra": "...", "worker": "..."},
        "addresses": {"infra": "...", "mongo": "..."},
        "forbidden_names": ["...", "..."]}

   ``forbidden_names`` feeds the guard in ``test_no_site_identifiers.py``: names
   that must never appear in a checked-in test, kept here so the guard itself
   names nothing.

2. Otherwise GENERATED per session: host names under the RFC 6761 ``.test``
   TLD, addresses from the RFC 5737 documentation networks — reserved so they
   can never name a real machine — with a per-session random component, so no
   test can come to depend on a particular value. These reserved ranges are the
   only literals, and this is the only file allowed to hold them.

A role maps to the same value for the whole session and different roles never
collide, so a test can compare "the address Mongo moved to" with "the address
MinIO stayed at" without knowing either.
"""

from __future__ import annotations

import ipaddress
import json
import os
import random
from pathlib import Path

# RFC 5737 TEST-NET-1/2/3, the RFC 3849 IPv6 documentation prefix and the
# RFC 6761 reserved TLD: the ONLY literals.
_DOC_NETS = tuple(ipaddress.ip_network(n) for n in
                  ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
_DOC_NET6 = ipaddress.ip_network("2001:db8::/32")
_TEST_TLD = "test"

_rng = random.Random(os.environ.get("FW_TEST_SITE_SEED"))  # seed to reproduce a run
_SESSION = f"{_rng.getrandbits(24):06x}"


def _load_site() -> dict:
    path = os.environ.get("FW_TEST_SITE") or str(Path(__file__).with_name("site.local.json"))
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_SITE = _load_site()
_hosts: dict[str, str] = dict(_SITE.get("hosts") or {})
_addrs: dict[str, str] = dict(_SITE.get("addresses") or {})
_pool = [str(h) for net in _DOC_NETS for h in net.hosts()]
_rng.shuffle(_pool)
_pool = [a for a in _pool if a not in set(_addrs.values())]


def host(role: str) -> str:
    """A host name for ``role`` (e.g. ``host("infra")``), stable for the session."""
    if role not in _hosts:
        _hosts[role] = f"{role}-{_SESSION}.{_TEST_TLD}"
    return _hosts[role]


def email(role: str) -> str:
    """A mailbox for a test USER in ``role`` -- no real person's name or domain."""
    return f"{role}@{host('mail')}"


def ip(role: str) -> str:
    """An IPv4 address for ``role``, stable for the session, distinct per role.

    Never loopback, unspecified or a Docker container-network address, so code
    that treats those specially (and skips them) sees an ordinary host address."""
    if role not in _addrs:
        _addrs[role] = _pool.pop()
    return _addrs[role]


_addrs6: dict[str, str] = {}


def ip6(role: str) -> str:
    """An IPv6 address for ``role``, stable for the session, distinct per role."""
    if role not in _addrs6:
        while True:
            a = str(_DOC_NET6.network_address + _rng.getrandbits(64))
            if a not in _addrs6.values():
                _addrs6[role] = a
                break
    return _addrs6[role]


def loopback() -> str:
    """The IPv4 loopback address — a protocol constant, not a machine."""
    return str(ipaddress.IPv4Address(0x7F000001))


def unspecified() -> str:
    """The IPv4 'any address' used for bind-all — a protocol constant."""
    return str(ipaddress.IPv4Address(0))


def container_net_ip() -> str:
    """An address inside Docker's default container-network space (172.16/12).

    Code that must ignore Docker-assigned addresses keys on that range, so a test
    of it needs one; generated, because no particular value is meaningful."""
    net = ipaddress.ip_network((0xAC100000, 12))                # 172.16.0.0/12
    return str(net.network_address + _rng.randrange(2, net.num_addresses - 2))


def forbidden_names() -> list[str]:
    """Names that must not appear in checked-in tests (from the uncommitted site
    file, plus the host names of the server catalog this checkout is using)."""
    names = {str(n) for n in (_SITE.get("forbidden_names") or []) if n}
    try:
        import sys
        repo = Path(__file__).resolve().parents[1]
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from facetwork.servers import catalog

        for s in catalog.servers():
            for n in [s.get("name"), *(s.get("aliases") or [])]:
                if n and not str(n).startswith("afl-"):  # afl-* are product service names
                    names.add(str(n))
                    names.add(str(n).split(".")[0])
    except Exception:  # noqa: BLE001 - no catalog is fine: nothing to forbid from it
        pass
    return sorted(n for n in names if len(n) >= 4)
