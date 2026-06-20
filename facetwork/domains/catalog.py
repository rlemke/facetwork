"""Load the domain/example catalog (``domains.json``) with per-deployment override.

Single source of truth for the set of pluggable **domains** (the ``fwh_*``
production pipelines) and in-repo **examples** (teaching demos), plus their
attributes (repo, pip extras, compose service / task_list, …). Replaces the
hardcoded lists that used to live in ``scripts/lib/install/domain``,
``facetwork/domains/migrate.py``, and ``docker-compose.full-stack.yml``.

Resolution (a deployment overrides the catalog WITHOUT editing any command):

1. ``$AFL_DOMAINS_FILE`` — if set, that file IS the catalog (full replace).
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
    """Overlay ``domains``/``examples`` onto base by key; honor ``_remove``."""
    out = {"domains": dict(base.get("domains", {})), "examples": dict(base.get("examples", {}))}
    for section in ("domains", "examples"):
        for name, spec in (overlay.get(section) or {}).items():
            out[section][name] = spec
    for name in overlay.get("_remove", []) or []:
        out["domains"].pop(name, None)
        out["examples"].pop(name, None)
    # carry through any other top-level keys from the overlay (e.g. version)
    for k, v in overlay.items():
        if k not in ("domains", "examples", "_remove"):
            out[k] = v
    return out


def catalog_source() -> Path:
    """The resolved catalog file actually in effect (for logging/diagnostics)."""
    env = os.environ.get("AFL_DOMAINS_FILE")
    if env:
        return Path(env)
    if LOCAL_OVERRIDE.exists():
        return LOCAL_OVERRIDE
    return DEFAULT_CATALOG


def load_catalog() -> dict:
    """Return the effective catalog: ``{"domains": {...}, "examples": {...}, ...}``."""
    env = os.environ.get("AFL_DOMAINS_FILE")
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
