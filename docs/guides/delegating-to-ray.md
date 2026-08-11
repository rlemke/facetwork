# Delegating a step to Ray

How to hand the parallel interior of one FFL step to a [Ray](https://docs.ray.io)
cluster, and when not to. Design rationale:
[`external-engine-delegation.md`](../architecture/external-engine-delegation.md) §8.4.
Working code: [`examples/ray-delegate`](../../examples/ray-delegate).

## When this is the right tool

Facetwork owns durable coarse structure — persisted steps, retry, `catch`,
repair, survival across restarts and machine sleep. Ray owns fine-grained
parallel compute. The seam between them is a step.

A Facetwork step costs roughly **2.6s of engine overhead** (bootstrap, a
persisted step row, a task row, a claim poll —
[`paper-environment-provisioning.md`](../thesis/paper-environment-provisioning.md) §4).
So:

| Work | Shape it as |
|---|---|
| 3,000 counties, ~20s each | 3,000 Facetwork steps (`foreach … limit N`) — durable, resumable, repairable |
| 3,000 geometry ops, ~5ms each | **one** Ray job inside one step — as steps this is hours of pure bookkeeping |
| a 40-minute model fit | one step delegating to Ray |
| distributed *training* across GPUs | Ray, and only Ray — see below |

**The delegated work must itself be substantial.** Ray packs before it spreads:
a job of 24 microsecond tasks ran entirely on one node in testing, while the same
job with 3-second tasks used all three. Delegating trivial work buys a job
submission and nothing else.

**Not a fit.** Distributed training needs a co-scheduled gang — N workers alive
simultaneously with a rendezvous and collective communication. A leaderless
first-come-first-served claim protocol cannot express that, and adding it would
mean adding a scheduler. Serving is likewise out of scope. Delegate those
wholesale; do not model them as steps.

## Set up a cluster

Production wants a **long-lived head node** — starting a cluster per job costs
the init plus environment provisioning, which erases the benefit unless the job
is large. For development, [`docker-compose.ray.yml`](../../docker-compose.ray.yml)
simulates a multi-node cluster on one host:

```bash
docker compose -f docker-compose.ray.yml -p ray up -d --scale worker=3
open http://localhost:8265          # dashboard AND the Job Submission API
```

Point Facetwork at it:

```bash
export FW_RAY_ADDRESS=http://localhost:8265
```

## Write the workflow

```ffl
use ray.delegate

workflow FineTune(dataset: String) => (status: String) andThen {
    shards  = PrepareShards(dataset = $.dataset)     // ordinary durable steps
    trained = SubmitJob(
        entrypoint       = "python train.py",
        working_dir      = "/abs/path/to/code",
        runtime_env_json = "{\"pip\": [\"torch==2.4.0\"]}"
    )
    report  = Evaluate() after trained               // invisible dependency
    yield FineTune(status = trained.status)
}
```

`runtime_env_json` is Ray's dependency spec for the job. **Thread it explicitly**
if your workflow exposes it as a parameter — a workflow that accepts a spec and
forgets to pass it down leaves the facet default (`"{}"`) in force, and the job
dies on its first import with an error three layers from the omission.

## Choose a depth

| | Who runs it | Holds a worker slot? | Use when |
|---|---|---|---|
| **D2** | a normal handler on any runner | yes, for the whole external run | short jobs, or few of them |
| **D3** | a standalone watcher process | **no** | long jobs, or many concurrently |

**D2** — nothing to deploy beyond the handler:

```bash
pip install 'ray[default]'            # on the runner
fw runner start --example ray-delegate --no-dashboard
```

**D3** — run the watcher instead. It claims the tasks, submits, parks, and
completes them out of band via the polyglot agent protocol, so N concurrent
external runs cost N dict entries rather than N worker slots:

```bash
FW_RAY_ADDRESS=http://localhost:8265 python examples/ray-delegate/watcher.py --task-list ray
```

> **D3 needs a runner polling that task list too** — not for the facet (the
> watcher owns that) but to process the `fw:resume` the watcher inserts. Any
> runner will do; it needs neither Ray nor the handler. Without one the job
> completes, the step's returns are written, and the workflow sits at `running`
> with a pending `fw:resume` and no obvious cause.

## Run it

```bash
fw ffl run --primary examples/ray-delegate/ffl/ray_delegate.ffl \
    --workflow ray.delegate.example.DelegatedCompute \
    --inputs "$(cat inputs.json)"
```

Put the inputs in a file. The spec is JSON *inside* JSON, and shell escaping
mangles it silently — the symptom is a job that runs with no dependencies.

## What you get, and what to watch

* **Idempotent submission.** The Ray `submission_id` is derived from the step id
  (`fw-<step_id>`), so a redelivered task attaches to the running job instead of
  starting a second one. A retry whose previous attempt *terminally failed* gets
  a fresh id rather than inheriting that failure forever.
* **No clock reclaims the task.** The wait is declared with `ctx.stage()`, so the
  stuck watchdog, the lease and the reaper all stand off for its duration.
* **Terminate propagates.** `fw maint terminate-workflow --force` reaches the Ray
  job: measured at four seconds from operator command to `STOPPED`. Note the
  `--force` — a healthy delegated step heartbeats, and the terminate guard
  refuses while anything heartbeats within 30s.
* **Completion is not instant.** A workflow whose steps and tasks are all
  terminal is finalised by a periodic reconciliation pass that deliberately
  waits **15 minutes** before touching it, so a live workflow is never raced.
  A D3 run can therefore read `running` for a quarter of an hour after its work
  is demonstrably done. That is by design, not a stall.

## Failure semantics

A failed Ray job raises normally and consumes the retry budget — a worker can
die transiently, and marking that permanent would turn a blip into lost work. A
missing step id or a non-positive timeout raises `PermanentError` and
dead-letters immediately, because no retry fixes a bad parameter.

## See also

- [`external-engine-delegation.md`](../architecture/external-engine-delegation.md) — the general design, and the Temporal/Airflow equivalents
- [`long-running-handlers.md`](long-running-handlers.md) — heartbeats, stage budgets, cooperative cancellation
- [`paper-environment-provisioning.md`](../thesis/paper-environment-provisioning.md) — the measurements behind the granularity argument
- thesis §2.6 — why Ray is adjacent to the workflow-engine comparison rather than in it
