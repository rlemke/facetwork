"""Read-only forensic probe for continuation-chain stalls.

Given a workflow_id, characterizes the stall structure WITHOUT mutating
anything: the non-terminal frontier, whether each frontier step has any
task/continuation targeting it, and — for one frontier step — a DRY-RUN of
what the evaluator would do if it processed it (against a throwaway
in-memory copy, so the real DB is untouched).

Usage: python stall_probe.py <workflow_id>
"""

import sys
import time
from collections import Counter

from pymongo import MongoClient

WID = sys.argv[1]
db = MongoClient("mongodb://server3.local:27017").facetwork
now = time.time() * 1000

TERMINAL = ("Complete", "Error", "Terminated")


def terminal(state):
    return any(state.endswith(t) for t in TERMINAL)


steps = list(db.steps.find({"workflow_id": WID}))
nonterm = [s for s in steps if not terminal(s["state"])]
print(f"steps: {len(steps)} total, {len(nonterm)} non-terminal")
print("non-terminal by state:", dict(Counter(s["state"].split(".", 2)[-1] for s in nonterm)))

# Tasks targeting this workflow
tasks = list(db.tasks.find({"workflow_id": WID, "state": {"$in": ["pending", "running"]}}))
print(
    f"in-flight tasks: {len(tasks)}",
    dict(Counter(t.get("name", "?").split(".")[-1] for t in tasks)),
)
conts = [t for t in tasks if t["name"] == "_fw_continue"]
print(f"  pending/running _fw_continue: {len(conts)}")

# Build container->children and step-id index
by_id = {s["uuid"]: s for s in steps}
children = {}
for s in steps:
    for key in ("container_id", "block_id"):
        cid = s.get(key)
        if cid:
            children.setdefault(cid, []).append(s)

# The FRONTIER: non-terminal steps whose children (if any) are ALL terminal
# — i.e. the deepest stuck points, the ones that *should* be advancing.
frontier = []
for s in nonterm:
    kids = children.get(s["uuid"], [])
    if all(terminal(k["state"]) for k in kids):
        frontier.append(s)
print(f"\nfrontier (non-terminal, all children terminal): {len(frontier)}")

# Does anything target each frontier step?
task_by_step = {}
for t in db.tasks.find({"workflow_id": WID}):
    task_by_step.setdefault(t.get("step_id"), []).append(t)

for s in frontier[:12]:
    sid = s["uuid"]
    ts = task_by_step.get(sid, [])
    live = [t for t in ts if t["state"] in ("pending", "running")]
    seq = (s.get("version") or {}).get("sequence")
    age = round((now - s.get("last_modified", 0)) / 1000)
    print(
        f"  {s['state'].split('.', 2)[-1]:<34} "
        f"obj={s.get('object_type'):<18} seq={seq} age={age}s "
        f"kids={len(children.get(sid, []))} "
        f"live_tasks={[t['name'].split('.')[-1] + ':' + t['state'] for t in live]}"
    )

# Dry-run: take the first frontier step and simulate one evaluator pass
# against a THROWAWAY memory store seeded with this workflow's steps.
if frontier:
    print("\n=== DRY-RUN one frontier step against a throwaway store ===")
    from facetwork.runtime.evaluator import Evaluator
    from facetwork.runtime.memory_store import MemoryStore
    from facetwork.runtime.mongo_store import MongoStore
    from facetwork.runtime.telemetry import Telemetry

    src = MongoStore("mongodb://server3.local:27017", "facetwork")
    r = db.runners.find_one({"workflow_id": WID})
    target = frontier[0]
    sid = target["uuid"]
    print(f"target frontier step {sid[:8]} state={target['state']}")

    mem = MemoryStore()
    for s in steps:  # seed the whole workflow so container/child lookups work
        mem.save_step(src.get_step(s["uuid"]))
    ev = Evaluator(persistence=mem, telemetry=Telemetry(enabled=False))
    before = mem.get_step(sid).state
    try:
        ev.process_single_step(
            step_id=sid,
            workflow_ast=r.get("workflow_ast"),
            program_ast=r.get("compiled_ast"),
            runner_id=r["uuid"],
        )
        after = mem.get_step(sid).state
        print(f"  {before}  ->  {after}   ({'ADVANCED' if after != before else 'NO CHANGE'})")
        # did the dry-run create tasks/continuations?
        new_tasks = list(mem._tasks.values())
        print(
            f"  dry-run created {len(new_tasks)} task(s):",
            dict(Counter(t.name.split(".")[-1] for t in new_tasks)),
        )
        # did any OTHER step advance (parent notified)?
        advanced = [
            s2
            for s2 in mem._steps.values()
            if not terminal(s2.state)
            and s2.id != sid
            and s2.state != (by_id.get(s2.id, {}).get("state"))
        ]
        print(f"  other steps that changed: {len(advanced)}")
    except Exception as exc:
        import traceback

        print("  DRY-RUN RAISED:", type(exc).__name__, str(exc)[:200])
        traceback.print_exc()
