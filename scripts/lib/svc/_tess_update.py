#!/usr/bin/env python3
"""Find TESS sectors MAST has released since the last run and submit their search.

Called by `fw svc tess-update`; stdlib only, so the timer needs no venv.

A sector counts as RELEASED when both halves of the comparison exist: SPOC's
single-sector TCE table is listed on MAST's bulk-download page AND its 2-minute
light curves are in the public bucket. Either alone cannot be searched and
scored, so a sector with only one is left for a later tick.

State lives in one JSON file (default ~/.facetwork/tess-update.json): every
sector already searched (by this timer or seeded from the local CLI runs),
the TCE table each was searched against, and when the last NEW sector
appeared. A second host with no state file is not the TESS host, and its
--check passes rather than alarming.

⚠️ MAST reprocesses sectors in place (48-55 and 56-65 were redone). A
sector whose TCE table name changes is REPORTED, never re-run unattended: a
reprocessing is a reason to look, and re-running blindly would silently
replace the numbers the published page was built from.

Exit codes for --check: 0 healthy, 1 attention, 2 could not verify.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

TCE_PAGE = "https://archive.stsci.edu/tess/bulk_downloads/bulk_downloads_tce.html"
MAST = "https://archive.stsci.edu"
BUCKET = (
    "https://stpubdata.s3.amazonaws.com/?list-type=2&max-keys=1&prefix=tess/public/tid/s{:04d}/"
)
DEFAULT_WORK_DIR = "s3://afl-cache/cache/tess/sectors"
# Sectors are ~27 days and the recent gap between releases is 3-6 weeks, so
# 70 days without a new one means the upstream stopped, the page changed
# shape, or this timer is not seeing it. All three are worth a look.
MAX_DAYS_WITHOUT_NEW = 70
# The timer runs daily; three missed days is a dead timer, not a sleepy laptop.
MAX_DAYS_SINCE_RUN = 3
UA = {"User-Agent": "facetwork-tess-update"}


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)


def fetch(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def tce_tables() -> dict[int, str]:
    """Single-sector TCE table URL per sector, from MAST's listing."""
    out: dict[int, str] = {}
    for href in re.findall(r'href="([^"]*_dvr-tcestats\.csv)"', fetch(TCE_PAGE)):
        m = re.search(r"-s(\d{4})-s(\d{4})_dvr", href)
        if m and m.group(1) == m.group(2):
            out[int(m.group(1))] = href if href.startswith("http") else MAST + href
    if not out:
        # An empty parse is a changed page, not "no sectors": never read it as
        # the second, or the timer would report healthy while seeing nothing.
        raise RuntimeError(f"no single-sector TCE tables parsed from {TCE_PAGE}")
    return out


def has_light_curves(sector: int) -> bool:
    return bool(re.search(r"<KeyCount>[1-9]", fetch(BUCKET.format(sector))))


def load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "sectors": {}, "reprocessed": []}


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


def released(tables: dict[int, str], known: set[int]) -> dict[int, str]:
    """TCE-listed sectors not yet known that also have light curves."""
    return {s: u for s, u in sorted(tables.items()) if s not in known and has_light_curves(s)}


def submit(fw: str, ffl: str, workflow: str, inputs: dict, dry: bool) -> dict:
    cmd = [
        fw,
        "ffl",
        "run",
        "--primary",
        ffl,
        "--workflow",
        workflow,
        "--inputs",
        json.dumps(inputs),
    ]
    if dry:
        print("  would run:", " ".join(cmd))
        return {"workflow": workflow, "dry": True}
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout + p.stderr).strip()
    if p.returncode != 0:
        raise RuntimeError(f"{workflow} submission failed (exit {p.returncode}): {out[-800:]}")
    ids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", out)
    return {"workflow": workflow, "ids": sorted(set(ids)), "output": out[-400:]}


def cmd_seed(a: argparse.Namespace) -> int:
    state = load(a.state)
    tables = tce_tables()
    t = iso(now())
    for s in range(a.first, a.through + 1):
        if str(s) in state["sectors"]:
            continue
        state["sectors"][str(s)] = {"tce_url": tables.get(s, ""), "how": a.note, "recorded_at": t}
    state.setdefault("last_new_sector_at", t)
    state["work_dir"] = a.work_dir
    save(a.state, state)
    print(f"seeded sectors {a.first}-{a.through} as searched ({a.note}) in {a.state}")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    state = load(a.state)
    if not state["sectors"] and not a.sectors:
        print(
            f"no state at {a.state}: seed it first (--seed-through N), or every "
            "sector MAST has would be submitted at once",
            file=sys.stderr,
        )
        return 1
    tables = tce_tables()
    t = iso(now())
    for s, rec in sorted(state["sectors"].items(), key=lambda kv: int(kv[0])):
        new_url = tables.get(int(s))
        if rec.get("tce_url") and new_url and new_url != rec["tce_url"]:
            if not any(r["sector"] == int(s) and r["new"] == new_url for r in state["reprocessed"]):
                state["reprocessed"].append(
                    {"sector": int(s), "old": rec["tce_url"], "new": new_url, "seen": t}
                )
                print(
                    f"WARNING: sector {s} was reprocessed ({rec['tce_url'].rsplit('/', 1)[1]} -> "
                    f"{new_url.rsplit('/', 1)[1]}); NOT re-run. Re-run it deliberately if wanted."
                )
    if a.sectors:
        new = {s: tables.get(s, "") for s in a.sectors}
    else:
        new = released(tables, {int(s) for s in state["sectors"]})
    if not new:
        print(f"[{t}] no new sector (newest searched: {max(map(int, state['sectors']))})")
    else:
        secs = sorted(new)
        print(f"[{t}] submitting sectors {secs} -> {a.work_dir}")
        inputs = {"sectors": secs, "work_dir": a.work_dir}
        runs = [
            submit(a.fw, a.ffl, w, inputs, a.dry)
            for w in ("tess.workflows.SearchSectors", "tess.workflows.FalseAlarms")
        ]
        for r in runs:
            print(f"  {r['workflow']}: {', '.join(r.get('ids', [])) or '(submitted)'}")
        if a.dry:
            return 0
        for s in secs:
            state["sectors"][str(s)] = {
                "tce_url": new[s],
                "how": "tess-update",
                "recorded_at": t,
                "runs": [{k: r[k] for k in ("workflow", "ids")} for r in runs],
            }
        if not a.sectors:
            state["last_new_sector_at"] = t
    state["last_run"] = t
    state["work_dir"] = a.work_dir
    save(a.state, state)
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    if not a.state.exists():
        print(f"no TESS update state on this host ({a.state}): not the TESS host, nothing to check")
        return 0
    try:
        state = load(a.state)
        last_run = parse_iso(state["last_run"]) if "last_run" in state else None
        last_new = parse_iso(state["last_new_sector_at"])
    except (ValueError, KeyError) as exc:
        print(f"cannot read {a.state}: {exc}")
        return 2
    rc = 0
    if last_run is None or (now() - last_run).days > MAX_DAYS_SINCE_RUN:
        print(
            f"ATTENTION: tess-update last ran {state.get('last_run', 'never')}; the daily timer is not running"
        )
        rc = 1
    if (now() - last_new).days > MAX_DAYS_WITHOUT_NEW:
        print(
            f"ATTENTION: no new TESS sector since {state['last_new_sector_at']} "
            f"(> {MAX_DAYS_WITHOUT_NEW} days): MAST stopped, its page changed, or the check is blind"
        )
        rc = 1
    for r in state.get("reprocessed", []):
        print(f"note: sector {r['sector']} reprocessed (seen {r['seen']}), not re-run")
    if rc == 0:
        print(
            f"OK: newest searched sector {max(map(int, state['sectors']))}, "
            f"last run {state.get('last_run')}, last new sector {state['last_new_sector_at']}"
        )
    return rc


def cmd_status(a: argparse.Namespace) -> int:
    state = load(a.state)
    secs = sorted(map(int, state["sectors"]))
    print(f"state:    {a.state}")
    print(f"searched: {len(secs)} sectors, {secs[0] if secs else '-'}-{secs[-1] if secs else '-'}")
    print(f"work dir: {state.get('work_dir', a.work_dir)}")
    print(
        f"last run: {state.get('last_run', 'never')}  last new sector: {state.get('last_new_sector_at', '-')}"
    )
    for s in secs[-5:]:
        rec = state["sectors"][str(s)]
        ids = "; ".join(
            f"{r['workflow'].rsplit('.', 1)[1]} {','.join(r['ids'])}" for r in rec.get("runs", [])
        )
        print(f"  s{s}: {rec.get('how')} {rec.get('recorded_at')} {ids}")
    for r in state.get("reprocessed", []):
        print(f"  reprocessed: s{r['sector']} seen {r['seen']}")
    try:
        tables = tce_tables()
        print(f"MAST:     newest TCE table is sector {max(tables)}")
    except Exception as exc:  # noqa: BLE001 - status is informational
        print(f"MAST:     unreachable ({exc})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["run", "check", "status", "seed"])
    ap.add_argument("--state", type=Path, default=Path.home() / ".facetwork" / "tess-update.json")
    ap.add_argument("--work-dir", default=DEFAULT_WORK_DIR)
    ap.add_argument("--fw", default="fw")
    ap.add_argument("--ffl", default="")
    ap.add_argument(
        "--sectors",
        type=int,
        nargs="*",
        default=[],
        help="submit exactly these sectors (a deliberate re-run), not the new ones",
    )
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--through", type=int, default=0)
    ap.add_argument("--note", default="seeded")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    try:
        return {"run": cmd_run, "check": cmd_check, "status": cmd_status, "seed": cmd_seed}[
            a.action
        ](a)
    except (OSError, RuntimeError) as exc:
        print(f"tess-update: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
