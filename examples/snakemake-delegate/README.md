# snakemake-delegate — running somebody else's Snakefile as one step

A working delegation adapter: one FFL event facet whose handler runs an existing
Snakefile to completion and reports what it did. Design and rationale:
[`docs/architecture/external-engine-delegation.md`](../../docs/architecture/external-engine-delegation.md) §4, depth **D2** (blocking with heartbeat).

## Why delegate rather than port

The other direction is already in this repo: [`fwh_pypsa_data`](https://github.com/rlemke/fwh_pypsa_data)
*ports* PyPSA-Eur's retrieve layer to FFL, and
[`docs/thesis/experiments/pypsa-retrieve-cost-of-change/`](../../docs/thesis/experiments/pypsa-retrieve-cost-of-change/)
prices what that buys. Porting is expensive and it is a commitment to chase
upstream forever.

Delegation asks a smaller question: a team has a working Snakefile and wants
durability, cancellation and a fleet — must they rewrite it? No. They keep the
Snakefile and gain a step around it. **Delegation is reach, not portability**
(§1): nothing here transpiles anything, and the moment Snakemake is running, its
semantics are in charge.

## Use it

```bash
pip install snakemake                      # ~20 MiB; a venv of its own is fine
export FW_SNAKEMAKE_BIN=/path/to/venv/bin/snakemake   # if it is not on PATH

fw runner start --example snakemake-delegate --no-dashboard
fw ffl run examples/snakemake-delegate/ffl/snakemake_delegate.ffl \
  --workflow snakemake.delegate.example.DelegatedStage \
  --inputs '{"snakefile":"'$PWD'/examples/snakemake-delegate/workflow/Snakefile",
             "workdir":"/abs/scratch/dir","cores":2}'
```

`workflow/Snakefile` is a 12-way fan-out and a merge — the shape the thesis's
engine comparison uses, with the payload removed so what is exercised is
delegation rather than arithmetic. It needs no data and no network.

## What the adapter guarantees

| Property | How |
|---|---|
| **Idempotency is the target engine's** | Snakemake records completion in the filesystem, so a redelivered task re-invokes it and gets "nothing to be done". The adapter *reports* which answer it got (`nothing_to_be_done`) instead of hiding it |
| **No clock reclaims the task** | the wait is declared with `ctx.stage()`, so the stuck watchdog, the lease and the reaper all stand off for its duration |
| **Terminate propagates to the children** | Snakemake *spawns* the rule jobs, so the adapter kills the process **group** — SIGTERM, then SIGKILL after a grace period |
| **A stale lock is not silently cleared** | a lock means *some process may be running here*, and a crashed owner is indistinguishable from a live one. Opt in with `unlock_stale` only when the workdir belongs to this step alone |
| **Failure is retryable, config error is not** | a failed rule raises normally (a full disk, a flaky download inside the Snakefile); a missing binary, a missing path or a non-positive timeout raises `PermanentError` and dead-letters immediately |

### Verified, not asserted

Run through a real runner (`DelegatedStage`, which calls the same targets twice):

```
snakemake.delegate.RunWorkflow  completed  4.57s   ← 14 jobs, 13 files produced
snakemake.delegate.RunWorkflow  completed  0.58s   ← nothing to be done
```

The second call is ~8× cheaper because Snakemake answered from disk. That is the
delegation contract working, and it is also the thing §13.3 warns about — the
answer comes from timestamps, so it is exactly as good as the target engine's
own staleness check and no better. `nothing_to_be_done` is returned so a
workflow can tell the two apart rather than assuming.

**Cancellation.** A 60-second Snakefile was started and the workflow terminated
mid-run. The process tree at that moment was

```
PID   PPID  PGID  COMMAND
6206  5389  6206  …/snakemake --snakefile slow60.smk …
6223  6206  6206  /bin/bash -c set -euo pipefail; sleep 60 && touch slow.txt
6224  6223  6206  sleep 60
```

— three processes, one group. Killing only the parent would have orphaned two.
After `fw maint terminate-workflow --force`, all three were gone **within one
second**, the task ended `canceled` with `retry_count` 0, and the runner logged
*"Handler 'snakemake.delegate.RunWorkflow' stopped on cancellation"*. A clean
stop, not a failure, and no retry storm.

Worth knowing: `terminate-workflow` **refuses** while the task is heartbeating
("1 task(s) heartbeating within the last 30s") and tells you to pass `--force`.
That guard is doing its job — a delegated step that is alive and reporting looks
exactly like one that is stuck unless you look at the heartbeat.

## Why this one is D2 and stays D2

This handler occupies a runner worker slot for the whole external run. With
`FW_MAX_CONCURRENT=2`, three concurrent four-hour Snakemake runs saturate two
runners. D3 (park and resume) exists to remove exactly that, and it *is*
implemented for Ray ([`watcher.py`](../ray-delegate/watcher.py)). It is not
implemented here, and the reason is worth stating because it generalises:

> **The depth you can use is a property of the target engine's handle, not a
> free choice.**

D3 parks a task and lets a *different* process — possibly on a different host,
after the reaper moves it — observe the external run and complete the step. That
requires a handle which is **durable and re-derivable from persistent state**
(§11, G5). Ray has one: a server-side `submission_id` the watcher recomputes
from the step id and asks the cluster about.

A local Snakemake run has no such handle. It is a subprocess, and the only
identifiers are a PID (not durable — the OS reuses them, so a restarted watcher
checking "is 6206 alive?" may be asking about something else entirely) and the
working directory's lock (which says *someone* is running, the ambiguity
`unlock_stale` already documents). Parking would leave a subprocess nothing
observes, and re-attaching after a restart would collide with the lock the
orphan still holds.

So the honest options for detached Snakemake are not "write a watcher" but:

- **D2**, as here — correct, at the cost of a worker slot; or
- give Snakemake a real scheduler (`--executor slurm`, `--executor kubernetes`)
  so the work lives somewhere with durable job ids, and delegate to *that* —
  at which point the handle exists and D3 applies exactly as it does for Ray.

One implementation detail that turned out to matter more than expected: the poll
loop uses **two clocks**. Process exit is checked every 50ms while the heartbeat
still goes out every 2s. Sleeping the heartbeat interval instead puts a floor
under *every* delegated call equal to that interval — measured, a "nothing to be
done" no-op cost 2.015s against a real 14-job run's 2.009s. The adapter's own
polling had hidden the very cheapness that makes a redelivered task cheap.

## Tests

```bash
python -m pytest examples/snakemake-delegate/tests -q      # 13 tests
```

The parsing and every refusal are pure and always run; the two that invoke the
real binary skip themselves when it is absent, so a runner host without
Snakemake still gets a green suite.
