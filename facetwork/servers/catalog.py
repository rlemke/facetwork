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
