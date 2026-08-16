"""Load the server catalog (``servers.json``) with per-deployment override.

Single source of truth for the deployment's MACHINES: each entry maps a
**stable resolvable name** (mDNS ``.local`` on a LAN, or a DNS name) to its
aliases (the conventional ``afl-mongodb``/``afl-minio`` service names), its
purpose, its ``FW_SERVER_GROUP`` role tag, and — only as a last resort — a
pinned IP. Consumers resolve names to the CURRENT address at startup or
reconcile time, so a DHCP-drifted infra host heals on the next resolution
instead of requiring ``/etc/hosts`` edits on every machine (the recurring
failure this file removes; containers can't resolve mDNS themselves, so the
host resolves for them and materializes the result via compose
``extra_hosts`` — see ``docker-compose.fleet.yml``).

Resolution (same contract as ``facetwork/domains/catalog.py``):

1. ``$FW_SERVERS_FILE`` — if set, that file IS the catalog (full replace).
2. else ``servers.local.json`` next to ``servers.json`` — merged OVER the
   committed defaults: entries add or replace by ``name``, and a top-level
   ``"_remove": ["name", ...]`` drops standard entries.
3. else ``servers.json`` (the committed catalog).

Pure-stdlib (json/os/socket/pathlib) so every consumer can import it cheaply.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = _REPO_ROOT / "servers.json"
LOCAL_OVERRIDE = _REPO_ROOT / "servers.local.json"


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _merge(base: dict, overlay: dict) -> dict:
    """Overlay ``servers`` entries onto base by ``name``; honor ``_remove``."""
    by_name = {s.get("name"): dict(s) for s in base.get("servers", []) if s.get("name")}
    for spec in overlay.get("servers") or []:
        name = spec.get("name")
        if name:
            by_name[name] = dict(spec)
    for name in overlay.get("_remove", []) or []:
        by_name.pop(name, None)
    out = {"servers": list(by_name.values())}
    for k, v in overlay.items():
        if k not in ("servers", "_remove"):
            out[k] = v
    return out


def catalog_source() -> Path:
    """The resolved catalog file actually in effect (for logging/diagnostics)."""
    env = os.environ.get("FW_SERVERS_FILE")
    if env:
        return Path(env)
    if LOCAL_OVERRIDE.exists():
        return LOCAL_OVERRIDE
    return DEFAULT_CATALOG


def load_catalog() -> dict:
    env = os.environ.get("FW_SERVERS_FILE")
    if env:
        return _read(Path(env))
    base = _read(DEFAULT_CATALOG) if DEFAULT_CATALOG.exists() else {"servers": []}
    if LOCAL_OVERRIDE.exists():
        return _merge(base, _read(LOCAL_OVERRIDE))
    return base


def servers() -> list[dict]:
    """All catalog entries (each: name/aliases/purpose/group/infra/ip_pin)."""
    return list(load_catalog().get("servers") or [])


def find(name_or_alias: str) -> dict | None:
    """Entry whose ``name`` or one of whose ``aliases`` matches (exact)."""
    for s in servers():
        if s.get("name") == name_or_alias or name_or_alias in (s.get("aliases") or []):
            return s
    return None


def infra() -> dict | None:
    """The entry marked ``"infra": true`` (the MongoDB/MinIO/dashboard host)."""
    for s in servers():
        if s.get("infra"):
            return s
    return None


def alias_map() -> dict[str, str]:
    """{alias: stable name} across all entries."""
    out: dict[str, str] = {}
    for s in servers():
        for a in s.get("aliases") or []:
            out[a] = s.get("name", "")
    return out


def resolve_ip(entry_or_name: dict | str) -> str | None:
    """Current IPv4 for an entry (or a name/alias). ``ip_pin`` wins when set;
    otherwise the stable name is resolved live (mDNS/DNS). ``None`` if the
    entry is unknown or resolution fails — callers keep their own fallback."""
    entry = entry_or_name if isinstance(entry_or_name, dict) else find(entry_or_name)
    if not entry:
        return None
    pin = entry.get("ip_pin")
    if pin:
        return str(pin)
    name = entry.get("name")
    if not name:
        return None
    try:
        return socket.gethostbyname(name)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Container-facing address
# ---------------------------------------------------------------------------

#: Docker's own alias for "the machine hosting this container". Valid wherever
#: ``extra_hosts`` / ``--add-host`` accepts an address (Docker >= 20.10, and
#: Docker Desktop), and maintained BY Docker — so unlike a resolved LAN address
#: it cannot go stale when this machine's DHCP lease changes, and it still
#: works with no network at all.
HOST_GATEWAY = "host-gateway"


def _local_addresses() -> set[str]:
    """Every IPv4 address this machine answers on, incl. loopback."""
    addrs = {"127.0.0.1"}
    try:
        for _fam, _typ, _proto, _canon, sa in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ):
            addrs.add(sa[0])
    except OSError:
        pass
    try:  # primary LAN address (routing lookup only — no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET
            addrs.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    return addrs


def is_self(entry_or_name: dict | str) -> bool:
    """True when the catalog entry names THIS machine.

    Checked by short hostname first (cheap, works offline) and then by address,
    so it holds whether the entry is written ``MaxPro.local`` or ``maxpro``."""
    entry = entry_or_name if isinstance(entry_or_name, dict) else find(entry_or_name)
    if not entry:
        return False
    name = entry.get("name") or ""
    if name:
        try:
            me = socket.gethostname().split(".")[0].lower()
        except OSError:
            me = ""
        if me and name.split(".")[0].lower() == me:
            return True
    ip = resolve_ip(entry)
    return bool(ip) and ip in _local_addresses()


def container_ip(entry_or_name: dict | str | None = None) -> str | None:
    """The address a CONTAINER should map the ``afl-*`` names to (compose
    ``extra_hosts``). Defaults to the infra entry.

    When infra runs on THIS machine — the standalone/`fw mode local` case — the
    answer is :data:`HOST_GATEWAY`, never an IP: the containers reach the host's
    published ports through Docker's own gateway, so a new DHCP lease after a
    reboot (or no network at all) cannot strand them on an address that no
    longer exists. Otherwise the infra host is remote and its stable name is
    resolved live, exactly as before. An explicit ``ip_pin`` still wins — a
    deployment that pinned an address asked for that address.

    Not for host-side use: probe the host's own services on 127.0.0.1 / the
    resolved IP (see :func:`resolve_ip`); ``host-gateway`` means nothing there."""
    if entry_or_name is None:
        entry = infra()
    elif isinstance(entry_or_name, dict):
        entry = entry_or_name
    else:
        # A name that isn't in the catalog is still resolvable — a deployment
        # may set FW_INFRA_HOST to a host it never catalogued. Treat it as a
        # one-off entry rather than refusing, so this call is a drop-in for the
        # plain name resolution it replaces.
        entry = find(entry_or_name) or {"name": entry_or_name}
    if not entry:
        return None
    if entry.get("ip_pin"):
        return str(entry["ip_pin"])
    if is_self(entry):
        return HOST_GATEWAY
    return resolve_ip(entry)
