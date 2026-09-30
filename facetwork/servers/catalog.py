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
3. else ``servers.json`` -- SITE configuration, never committed (template:
   ``servers.example.json``; distribute with ``fw fleet servers --push``).

Pure-stdlib (json/os/socket/pathlib) so every consumer can import it cheaply.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = _REPO_ROOT / "servers.json"
LOCAL_OVERRIDE = _REPO_ROOT / "servers.local.json"


def _read(path: Path) -> dict:
    """A catalog file, or an EMPTY catalog when the file does not exist.

    The catalog is site configuration and never committed, so a fresh clone (or
    a host that has not been given one yet) has none -- including where
    ``FW_SERVERS_FILE`` names the default path explicitly, as ``fw mode`` does.
    Absent means "no machines known", which every consumer already handles;
    crashing them all would turn a missing file into a broken CLI. A file that
    exists but is not valid JSON still raises: that is a real error."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"servers": []}


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


def service_host(service: str) -> str | None:
    """Stable NAME of the host that serves ``service`` (an ``afl-*`` alias).

    The entry claiming the alias wins; an unclaimed service falls back to the
    ``infra: true`` host -- the one-box default. This is how a script names the
    registry, the object store or the extract server WITHOUT a host name
    written into the repo: the catalog is site configuration, never committed
    (see servers.example.json). ``None`` when there is no catalog at all."""
    entry = find(service) or infra()
    return (entry or {}).get("name") or None


def resolve_url(url: str) -> str:
    """``url`` with its host replaced by the catalog's address when this machine
    cannot resolve that host itself; unchanged otherwise.

    Host-side tools address shared services by their ``afl-*`` names, which
    containers map through ``extra_hosts`` but a host resolves only if something
    pinned them in /etc/hosts -- which the fleet deliberately stopped doing. The
    bash helpers already fell back to the catalog; Python commands run by ``fw``
    did not, so e.g. ``fw maint workflow-stats`` could not reach MongoDB from a
    laptop that every other command reached fine."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = parts.hostname
    if not host or find(host) is None:
        return url
    try:
        socket.gethostbyname(host)
        return url  # the system resolves it: leave it alone
    except OSError:
        pass
    ip = resolve_ip(host)
    if not ip:
        return url
    netloc = parts.netloc.replace(host, ip, 1)
    return urlunsplit(parts._replace(netloc=netloc))


def host_key(name: object) -> str:
    """The key two records of the SAME machine agree on, whatever their naming.

    A fleet-agent records its FQDN while a runner registers its short hostname,
    so a join on the raw strings never matches. This used to strip a literal
    ``.local`` -- correct only on an mDNS network; on a cloud VM
    (``ip-10-0-0-5.ec2.internal`` vs ``ip-10-0-0-5``) every host would have
    shown "no fleet-agent record". The first DNS label, lowercased, is the
    general form. An IP address is kept whole: its first "label" is an octet.
    """
    s = str(name or "").strip().lower()
    if not s:
        return "?"
    try:
        ipaddress.ip_address(s)
        return s
    except ValueError:
        return s.split(".", 1)[0]


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
    # THIS machine: ask the routing table, not mDNS. avahi advertises a host's
    # name on EVERY interface, Docker bridges included, so resolving our own name
    # can answer the docker0 bridge address -- measured 2026-09-30 on the database host, where
    # the fleet-agent then judged every correctly-pinned runner "drifted" and
    # recreated all 23, killing a 30-minute planet rewrite in its last 2%.
    if _names_this_host(name):
        lan = _primary_lan_ip()
        if lan:
            return lan
    try:
        return socket.gethostbyname(name)
    except OSError:
        return None


def _names_this_host(name: str) -> bool:
    try:
        me = socket.gethostname().split(".")[0].lower()
    except OSError:
        return False
    return bool(me) and name.split(".")[0].lower() == me


def _primary_lan_ip() -> str | None:
    """The address this machine uses to reach the network (a routing lookup; no
    packet is sent). None when there is no route at all."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET: never contacted, only routed
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


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
            addrs.add(str(sa[0]))
    except OSError:
        pass
    lan = _primary_lan_ip()
    if lan:
        addrs.add(lan)
    return addrs


def is_self(entry_or_name: dict | str) -> bool:
    """True when the catalog entry names THIS machine.

    Checked by short hostname first (cheap, works offline) and then by address,
    so it holds whether the entry is written ``host.local`` or ``host``."""
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
