"""Load the domain/example catalog (``domains.json``) with per-deployment override.

Single source of truth for the set of pluggable **domains** (the ``fwh_*``
production pipelines) and in-repo **examples** (teaching demos), plus their
attributes (repo, pip extras, compose service / task_list, …). Replaces the
hardcoded lists that used to live in ``scripts/lib/install/domain``,
``facetwork/domains/migrate.py``, and ``docker-compose.full-stack.yml``.

Resolution (a deployment overrides the catalog WITHOUT editing any command):

1. ``$FW_DOMAINS_FILE`` — if set, that file IS the catalog (full replace).
2. else ``domains.local.json`` next to ``domains.json`` — merged OVER the
   committed defaults: ``domains``/``examples`` entries add or replace by key,
   and a top-level ``"_remove": ["name", ...]`` drops standard entries.
3. else ``domains.json`` (the committed standard catalog).

Pure-stdlib (json/os/pathlib) so every consumer can import it cheaply.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = _REPO_ROOT / "domains.json"
LOCAL_OVERRIDE = _REPO_ROOT / "domains.local.json"


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _merge(base: dict, overlay: dict) -> dict:
    """Overlay ``domains``/``examples`` onto base by key; honor ``_remove``.

    Every other top-level key (``defaults``, ``index_dir``, ``version``, …)
    comes from base unless the overlay restates it. Starting from ``{}`` and
    copying only the overlay's keys — as this did — meant that merely HAVING a
    ``domains.local.json`` dropped the shared catalog's deployment-wide
    settings: ``defaults`` went None, so the fleet's replica counts silently
    reverted to the code fallbacks, and ``index_dir`` vanished, which desynced
    the generated compose file between hosts that had a local override and
    hosts that did not. An override is meant to be a DELTA, not a replacement.
    """
    out = {k: v for k, v in base.items() if k not in ("domains", "examples", "_remove")}
    out["domains"] = dict(base.get("domains", {}))
    out["examples"] = dict(base.get("examples", {}))
    for section in ("domains", "examples"):
        for name, spec in (overlay.get(section) or {}).items():
            out[section][name] = spec
    for name in overlay.get("_remove", []) or []:
        out["domains"].pop(name, None)
        out["examples"].pop(name, None)
    # overlay wins for any other top-level key it actually states
    for k, v in overlay.items():
        if k not in ("domains", "examples", "_remove"):
            out[k] = v
    return out


def catalog_source() -> Path:
    """The resolved catalog file actually in effect (for logging/diagnostics)."""
    env = os.environ.get("FW_DOMAINS_FILE")
    if env:
        return Path(env)
    if LOCAL_OVERRIDE.exists():
        return LOCAL_OVERRIDE
    return DEFAULT_CATALOG


def load_catalog() -> dict:
    """Return the effective catalog: ``{"domains": {...}, "examples": {...}, ...}``."""
    env = os.environ.get("FW_DOMAINS_FILE")
    if env:
        cat = _read(Path(env))  # full replace
    elif LOCAL_OVERRIDE.exists():
        cat = _merge(_read(DEFAULT_CATALOG), _read(LOCAL_OVERRIDE))
    else:
        cat = _read(DEFAULT_CATALOG)
    cat.setdefault("domains", {})
    cat.setdefault("examples", {})
    return cat


def domains() -> dict:
    """Mapping of domain name -> attributes."""
    return load_catalog()["domains"]


def examples() -> dict:
    """Mapping of example name -> attributes."""
    return load_catalog()["examples"]


def domain_names() -> list[str]:
    """Sorted domain short-names (the canonical list for migrate/seed)."""
    return sorted(domains())


def get_domain(name: str) -> dict | None:
    return domains().get(name)


def compose_domains() -> dict:
    """Domains that have a compose ``runner-<service>`` (subset with a ``service`` key)."""
    return {n: s for n, s in domains().items() if s.get("service")}


def consolidated_domains() -> list[str]:
    """Domains served by ONE generalist runner instead of a container each.

    A per-domain runner is right for a hot domain: it bulkheads a heavy handler
    and keeps a flooded queue off everyone else. It is poor value for a domain
    that runs a handful of tasks a month — an idle runner still costs ~150 MiB
    resident and polls MongoDB forever (measured here at ~8.6 findAndModify/s
    per runner, whether or not it has work).

    This is a **per-deployment** decision, not a property of the domain: the
    same domain is cold on one deployment and hot on another. So it is read
    from a top-level ``"consolidated"`` list, which belongs in the gitignored
    ``domains.local.json`` rather than the shared catalog — one host
    consolidating must not stop another host's runners when it pulls.

    Kept as a top-level key on purpose: the overlay merge REPLACES a domain
    entry wholesale, so a per-entry flag would force each consolidated domain
    to be copied in full into the override just to set one boolean.
    """
    names = load_catalog().get("consolidated") or []
    known = domains()
    return sorted(n for n in names if n in known)


def index_dir() -> str:
    """Host directory holding tag indexes, mounted READ-ONLY into runners.

    Per-deployment, like ``consolidated``: the indexes live wherever this host
    keeps bulk data, and another host may have none at all. Read from the
    catalog rather than the ambient environment so ``gen-compose`` produces the
    same file whoever runs it — a generated artefact that changes shape based on
    who happened to have a variable exported is a bad artefact.

    Empty (the default) emits no mount, so deployments without indexes are
    unaffected by regenerating.
    """
    return str(load_catalog().get("index_dir") or "").strip()


def compose_services() -> list[str]:
    """The ``runner-<service>`` names for all compose domains (sorted by domain)."""
    return [s["service"] for _n, s in sorted(compose_domains().items())]


def fleet_default_services() -> list[str]:
    """``runner-<service>`` names for domains flagged ``fleet_default`` (the default
    set a bare ``fw runner start --fleet`` / fleet-agent brings up)."""
    return [
        s["service"]
        for n, s in sorted(domains().items())
        if s.get("fleet_default") and s.get("service")
    ]


def _suffix(service: str) -> str:
    return service[len("runner-") :] if service.startswith("runner-") else service


def scaled_domain_suffixes() -> list[str]:
    """``--domain`` suffixes for compose domains flagged ``scaled`` — run at the
    throughput knob (FW_OSM_REPLICAS) rather than a single replica."""
    return [_suffix(s["service"]) for _n, s in sorted(compose_domains().items()) if s.get("scaled")]


def unscaled_domain_suffixes() -> list[str]:
    """``--domain`` suffixes for compose domains NOT flagged ``scaled`` (one replica each)."""
    return [
        _suffix(s["service"]) for _n, s in sorted(compose_domains().items()) if not s.get("scaled")
    ]


def _defaults() -> dict:
    return load_catalog().get("defaults", {}) or {}


def default_replicas() -> int:
    """Catalog default replica count for a (non-scaled) domain runner."""
    return int(_defaults().get("replicas", 1))


def scaled_replicas() -> int:
    """Catalog default replica count for the scaled tier (the throughput knob).
    The runtime env (FW_OSM_REPLICAS) still overrides this per host."""
    return int(_defaults().get("scaled_replicas", default_replicas()))
