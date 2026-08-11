"""Facetwork handlers for capitals.ffl.

Thin wrappers over ``capitals_lib`` — the same functions the Snakemake rules and
Nextflow processes call through ``scripts/``. Nothing about finding a capital is
implemented here; if it were, the three-engine comparison would be measuring
three different implementations instead of three orchestrators.

Output location comes from ``FW_DATA_ROOT``, the same variable the runner uses
for every other storage decision, so a run is isolated by pointing it somewhere
private. The CACHE is separate and shared:

    CAPITALS_CACHE_DIR/osm_states.json     the cached OSM data — SHARED
    FW_DATA_ROOT/states/US-XX.json         one per fan-out iteration
    FW_DATA_ROOT/capitals.json (+ .csv)    the fan-in result

The cache is deliberately not under ``FW_DATA_ROOT``: all three engines read one
pre-warmed copy, so no run re-downloads and the fan-out comparison has no
network in the middle of it. Outputs stay per-engine so they can be diffed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import capitals_lib as lib  # noqa: E402

NAMESPACE = "capitals"


def _root() -> Path:
    # No silent default into the shared cache: an unset FW_DATA_ROOT would make
    # every "isolated" run write to the same place and quietly invalidate the
    # timing comparison.
    root = os.environ.get("FW_DATA_ROOT")
    if not root:
        raise RuntimeError(
            "FW_DATA_ROOT is not set — the capitals handlers write everything "
            "beneath it, and defaulting would let two runs share a cache."
        )
    return Path(root)


def _cache_dir() -> Path:
    """The SHARED cache — one warmed copy read by all three engines.

    Defaults to ``cache/`` beside this experiment, which is where the Snakemake
    and Nextflow versions look too. Overridable so a run can be pointed at a
    cold cache deliberately.
    """
    env = os.environ.get("CAPITALS_CACHE_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "cache"


def _log(step_log: Any, message: str, level: str = "info") -> None:
    if callable(step_log):
        step_log(message, level)


def handle_fetch_state_data(params: dict[str, Any]) -> dict[str, Any]:
    force = bool(params.get("force", False))
    step_log = params.get("_step_log")
    res = lib.fetch_state_data(_cache_dir(), force=force)
    _log(
        step_log,
        f"[{'cached' if res['was_cached'] else 'downloaded'}] "
        f"{res['state_count']} state relations",
        "success",
    )
    return {
        "cache_path": res["cache_path"],
        "state_count": res["state_count"],
        "was_cached": res["was_cached"],
    }


def handle_resolve_capital(params: dict[str, Any]) -> dict[str, Any]:
    state_code = params["state_code"]
    step_log = params.get("_step_log")
    root = _root()
    # Deliberately NOT caught: a missing cache means the fan-out ran before the
    # fetch, which is an ordering bug in the workflow. Failing the step surfaces
    # it; fetching here would hide it behind 50 Overpass queries.
    result = lib.resolve_capital(_cache_dir(), state_code)
    path = lib.write_state_result(root / "states", result)
    _log(step_log, f"{result['state']}: {result['capital']}")
    return {
        "state": result["state"],
        "capital": result["capital"],
        "out_path": str(path),
    }


def handle_combine_capitals(params: dict[str, Any]) -> dict[str, Any]:
    step_log = params.get("_step_log")
    root = _root()
    res = lib.combine(root / "states", root / "capitals.json")
    _log(step_log, f"{res['count']} capitals -> {res['out_path']}", "success")
    return {"out_path": res["out_path"], "count": res["count"]}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.FetchStateData": handle_fetch_state_data,
    f"{NAMESPACE}.ResolveCapital": handle_resolve_capital,
    f"{NAMESPACE}.CombineCapitals": handle_combine_capitals,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint.

    One entrypoint dispatching on ``_facet_name`` — registering several facets
    against separate entrypoints in one module collides in the dispatcher's
    cache (see docs; fixed in #32, but the single-entrypoint form is the one
    that is safe by construction).
    """
    facet = payload["_facet_name"]
    return _DISPATCH[facet](payload)


def register_handlers(runner) -> None:
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )
