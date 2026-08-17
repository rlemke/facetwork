# ray-delegate — handing a compute-heavy step to Ray

A working delegation adapter: one FFL event facet whose handler submits a job to
a [Ray](https://docs.ray.io) cluster, waits for it, and returns the outcome.
Design and rationale: [`docs/architecture/external-engine-delegation.md`](../../docs/architecture/external-engine-delegation.md) §8.4.

## Why delegate at all

Facetwork owns durable coarse structure — persisted steps, retry, `catch`,
repair, survival across restarts. Ray owns fine-grained parallel compute. The
seam is a step.

A Facetwork step costs roughly **2.6s of engine overhead** (bootstrap, a
persisted step row, a task row, a claim poll — measured in
[`paper-environment-provisioning.md`](../../docs/thesis/paper-environment-provisioning.md) §4).
That is nothing for a step that scans a PBF for thirty minutes, and ruinous for
3,000 geometry operations of 5ms each: as steps that is hours of pure
bookkeeping, as one Ray job it is seconds.

**The inner work must itself be substantial.** A first experiment ran 24
trivially-short tasks and Ray put all of them on one node — it packs before it
spreads. The same job with tasks holding a CPU for three seconds used all three
nodes. Delegating trivial work buys a job submission and nothing else.

Not a fit: distributed *training* (needs a co-scheduled gang and collective
communication, which a leaderless claim model cannot express) or serving.

## Use it

```ffl
use ray.delegate

workflow Pipeline() => (status: String) andThen {
    prepared = PrepareShards()                    // ordinary Facetwork steps
    job = SubmitJob(entrypoint = "python train.py", working_dir = "/abs/path")
    yield Pipeline(status = job.status)
}
```

```bash
# a cluster to talk to (docker-compose.ray.yml simulates a 3-node one)
docker compose -f ../../docker-compose.ray.yml -p ray up -d
export FW_RAY_ADDRESS=http://localhost:8265        # the Job Submission API

pip install 'ray[default]'                          # on the runner
fw runner start --example ray-delegate --no-dashboard
fw ffl run --primary ffl/ray_delegate.ffl \
    --workflow ray.delegate.example.DelegatedCompute \
    --inputs '{"entrypoint":"python job.py","working_dir":"/abs/path/to/job"}'
```

## What the adapter guarantees

| Property | How |
|---|---|
| **Idempotent submission** | the Ray `submission_id` is *derived* from the step id (`fw-<step_id>`), so a redelivered task attaches to the running job instead of starting a second one |
| **Retry is not poisoned** | a retry whose previous attempt is *terminally failed* gets a fresh id (`…-r<n>`) rather than attaching to that failure forever |
| **No clock reclaims the task** | the wait is declared with `ctx.stage()`, so the stuck watchdog, the lease and the reaper all stand off for its duration |
| **Terminate propagates** | on cancellation *or* timeout the adapter calls `stop_job` before unwinding, so the external run dies with the workflow instead of billing on unattended |
| **Failure is retryable, config error is not** | a failed Ray job raises normally (a worker can die transiently); a missing step id or a non-positive timeout raises `PermanentError` and dead-letters immediately |

## Verified end to end

Driven by a real workflow, not called directly — `fw ffl run` → step → task →
claim → handler → Ray job → returns → workflow `completed`, the Ray job carrying
the derived id `fw-<step_id>` and running across all three nodes, task retries
zero.

Terminate propagation, the case the design was blocked on:

```
12:45:15  fw maint terminate-workflow --force <runner_id>
12:45:19  Handler 'ray.delegate.SubmitJob' stopped on cancellation
          → Ray job fw-c6edc09d-… : STOPPED
```

Four seconds from operator command to a dead external job.

Two bugs surfaced only by running it, neither visible to the unit tests:
`RunnerService` was not injecting `_step_id` into the handler payload (a
framework parity gap, since fixed), and this example's workflow was not
threading `runtime_env_json` down to the facet, so the delegated job ran with no
dependencies and failed on its first import.

## Depth: both D2 and D3 are here

§4 of the design describes three depths. The **handler** (`handlers/ray_handlers.py`)
is **D2 — blocking with heartbeat**: it waits, so it occupies a runner worker
slot for the whole external run. With `FW_MAX_CONCURRENT=2`, three concurrent
four-hour jobs saturate two runners.

**D3** (park and resume) removes that, and ships alongside it as
[`watcher.py`](watcher.py): it claims the task, submits, parks via the stage
budget, and completes the step out of band with an `fw:resume`, so N concurrent
external runs cost N dict entries rather than N worker slots. Pick by shape —
D2 for short jobs or few of them, D3 for long ones or many. Usage and the
deployment caveat (**D3 needs some runner polling that task list to process the
`fw:resume`**) are in [the guide](../../docs/guides/delegating-to-ray.md).

## Tests

```bash
pytest examples/ray-delegate/tests/           # fakes; no cluster, no ray needed
FW_RAY_LIVE=1 pytest examples/ray-delegate/tests/   # + a real round-trip
```

The unit tests run against a fake `JobSubmissionClient`, so they pin the
adapter's *decisions* (attach vs resubmit, stop-on-cancel, permanent vs
retryable) without a cluster. The live test is skipped unless `FW_RAY_LIVE` is
set.
