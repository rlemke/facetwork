"""Find the capital of each US state from OpenStreetMap — the shared logic.

ALL THREE ENGINES CALL THIS MODULE. Facetwork's handlers, the Snakemake rules
and the Nextflow processes are thin wrappers over the functions here, so the
only thing that differs between the three runs is what decides when each step
executes. Reimplementing the work per engine would compare three pieces of my
code rather than three orchestrators.

**How the capital is determined.** Not from a bundled list — that would make
OSM decorative. Every US state is an OSM relation with
``boundary=administrative``, ``admin_level=4`` and an ``ISO3166-2`` code, and
each carries a member with role ``admin_centre`` pointing at the capital's node.
That link is the answer; the node supplies the name, position and population.
All 56 such relations (50 states, DC, and territories) have one.

**Only cached data is read during the fan-out.** ``fetch_state_data`` is the one
function that touches the network, and it is a no-op when the cache is present.
``resolve_capital`` RAISES if the cache is missing rather than fetching, so a
50-way fan-out can never turn into 50 Overpass queries — the constraint is
enforced in code, not just documented.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
CACHE_FILE = "osm_states.json"
USER_AGENT = "facetwork-state-capitals-experiment"

# One query for both halves of the answer: the state relations (whose members
# carry the admin_centre link) and the capital nodes themselves.
OVERPASS_QUERY = """[out:json][timeout:300];
rel["boundary"="administrative"]["admin_level"="4"]["ISO3166-2"~"^US-"]->.states;
.states out body;
node(r.states:"admin_centre");
out tags center;
"""

# The 50 states. Fixed rather than derived from the download, because all three
# engines need the fan-out width BEFORE the fetch has run — Snakemake and
# Nextflow would otherwise need a dynamic-DAG construct that Facetwork's foreach
# does not, which would make the comparison about that instead of about fan-out.
# DC and the territories are in the OSM data but are not states, so they are not
# fanned out over.
STATE_CODES = [
    "US-AL", "US-AK", "US-AZ", "US-AR", "US-CA", "US-CO", "US-CT", "US-DE",
    "US-FL", "US-GA", "US-HI", "US-ID", "US-IL", "US-IN", "US-IA", "US-KS",
    "US-KY", "US-LA", "US-ME", "US-MD", "US-MA", "US-MI", "US-MN", "US-MS",
    "US-MO", "US-MT", "US-NE", "US-NV", "US-NH", "US-NJ", "US-NM", "US-NY",
    "US-NC", "US-ND", "US-OH", "US-OK", "US-OR", "US-PA", "US-RI", "US-SC",
    "US-SD", "US-TN", "US-TX", "US-UT", "US-VT", "US-VA", "US-WA", "US-WV",
    "US-WI", "US-WY",
]


class CacheMissing(RuntimeError):
    """Raised when a cache-only step finds no cache.

    Its own type so callers can distinguish "you skipped the fetch" from a
    genuine data problem, and so no code path can quietly fall back to the
    network.
    """


def cache_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / CACHE_FILE


# --------------------------------------------------------------------------
# The one networked step
# --------------------------------------------------------------------------

def fetch_state_data(cache_dir: str | Path, force: bool = False) -> dict:
    """Download the OSM state relations + capital nodes, once.

    Returns immediately when the cache is present unless ``force``. Writes
    atomically via a temp file, so an interrupted download can never leave a
    truncated cache that later steps would read as valid.
    """
    dest = cache_path(cache_dir)
    if dest.exists() and not force:
        payload = json.loads(dest.read_text())
        return {
            "cache_path": str(dest),
            "state_count": len(payload.get("states", {})),
            "was_cached": True,
        }

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        OVERPASS_URL, data=OVERPASS_QUERY.encode(), headers={"User-Agent": USER_AGENT}
    )
    started = time.time()
    raw = urllib.request.urlopen(req, timeout=300).read()
    elements = json.loads(raw)["elements"]

    nodes = {e["id"]: e for e in elements if e["type"] == "node"}
    states: dict[str, dict] = {}
    for el in elements:
        if el["type"] != "relation":
            continue
        tags = el.get("tags", {})
        code = tags.get("ISO3166-2")
        if not code:
            continue
        centre = next(
            (m for m in el.get("members", []) if m.get("role") == "admin_centre"), None
        )
        states[code] = {
            "iso": code,
            "name": tags.get("name", ""),
            "relation_id": el["id"],
            "admin_centre_ref": centre["ref"] if centre else None,
        }

    payload = {
        "source": "OpenStreetMap via Overpass",
        "query": OVERPASS_QUERY,
        "fetched_at": int(started),
        "fetch_seconds": round(time.time() - started, 2),
        "states": states,
        # Only the nodes referenced as an admin_centre are kept. The rest of the
        # relation payload (thousands of boundary way ids) is dropped: it is
        # ~90% of the download and nothing downstream reads it.
        "nodes": {
            str(n["id"]): {
                "id": n["id"],
                "name": n.get("tags", {}).get("name", ""),
                "lat": n.get("lat") or (n.get("center") or {}).get("lat"),
                "lon": n.get("lon") or (n.get("center") or {}).get("lon"),
                "population": n.get("tags", {}).get("population"),
                "wikidata": n.get("tags", {}).get("wikidata"),
            }
            for n in nodes.values()
        },
    }

    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
    tmp.replace(dest)
    return {
        "cache_path": str(dest),
        "state_count": len(states),
        "was_cached": False,
    }


# --------------------------------------------------------------------------
# The fan-out step — cache only, never the network
# --------------------------------------------------------------------------

def resolve_capital(cache_dir: str | Path, state_code: str) -> dict:
    """Resolve one state's capital from the cache. Never touches the network."""
    src = cache_path(cache_dir)
    if not src.exists():
        raise CacheMissing(
            f"no cached OSM data at {src} — run the fetch step first. This step "
            "reads only the cache by design, so that a 50-way fan-out cannot "
            "become 50 Overpass queries."
        )

    payload = json.loads(src.read_text())
    state = payload["states"].get(state_code)
    if state is None:
        raise KeyError(f"{state_code} not present in the cached OSM data")

    ref = state.get("admin_centre_ref")
    if ref is None:
        raise ValueError(f"{state_code} ({state['name']}) has no admin_centre member")
    node = payload["nodes"].get(str(ref))
    if node is None:
        raise ValueError(f"admin_centre node {ref} for {state_code} is not in the cache")

    return {
        "state_code": state_code,
        "state": state["name"],
        "capital": node["name"],
        "lat": node["lat"],
        "lon": node["lon"],
        "population": node.get("population"),
        "osm_node_id": node["id"],
        "osm_relation_id": state["relation_id"],
    }


def write_state_result(out_dir: str | Path, result: dict) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result['state_code']}.json"
    path.write_text(json.dumps(result, indent=1, sort_keys=True))
    return path


# --------------------------------------------------------------------------
# Fan-in
# --------------------------------------------------------------------------

def combine(states_dir: str | Path, out_path: str | Path) -> dict:
    """Merge the per-state results into one table (JSON + CSV beside it)."""
    src = Path(states_dir)
    rows = [json.loads(p.read_text()) for p in sorted(src.glob("US-*.json"))]
    if not rows:
        raise RuntimeError(f"no per-state results under {src} — nothing to combine")

    missing = sorted(set(STATE_CODES) - {r["state_code"] for r in rows})
    if missing:
        # A partial fan-in is a silent wrong answer: 47 states looks like a
        # table, not like a failure. Refuse it.
        raise RuntimeError(
            f"fan-in is incomplete — {len(missing)} state(s) missing: {', '.join(missing)}"
        )

    rows.sort(key=lambda r: r["state"])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1, sort_keys=True))

    csv_path = out.with_suffix(".csv")
    lines = ["state_code,state,capital,lat,lon,population,osm_node_id"]
    for r in rows:
        lines.append(
            f"{r['state_code']},{r['state']},{r['capital']},{r['lat']},{r['lon']},"
            f"{r.get('population') or ''},{r['osm_node_id']}"
        )
    csv_path.write_text("\n".join(lines) + "\n")

    return {"out_path": str(out), "csv_path": str(csv_path), "count": len(rows)}
