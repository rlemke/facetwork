# FFL as a front end to other workflow systems — delegation

**Status: DESIGN.** No adapter code exists yet. One shallow precedent ships
today ([`examples/aws-lambda`](../../examples/aws-lambda)); this document
generalises it and works out the part that example leaves unsolved.

## 1. What this is, and what it is not

The question: *what would it look like for FFL to be a front end to other
workflow systems?* There are two very different answers, and this document is
about the second one only.

**Transpilation** compiles FFL into the target engine's own format — an Airflow
DAG, Temporal workflow code, an ASL state machine — and the Facetwork runtime
plays no part at run time. FFL becomes a portable source language.

**Delegation** keeps Facetwork as the orchestrator. An event facet's handler
submits work to Temporal or Airflow and waits for it. FFL becomes a
meta-orchestrator sitting *above* engines you already run.

This document designs **delegation**.

### 1.1 The honest scope note

Delegation buys three things:

- **Reuse.** Get at Spark clusters, managed schedulers, Kubernetes autoscaling
  and existing DAGs without rebuilding any of it.
- **Composition.** One FFL workflow can span a Temporal workflow, an Airflow DAG
  and a local Python handler, with one execution graph and one repair surface
  over all of it.
- **Adoption without rewriting.** A team keeps its existing DAGs and adopts FFL
  *above* them.

It does **not** buy language portability. An FFL workflow that delegates still
requires the Facetwork runtime to run; nothing here makes FFL executable
elsewhere. If the goal is "author in FFL, run on their engine with no Facetwork
in the picture", that is transpilation and this design does not deliver it. See
[§10 Non-goals](#10-non-goals).

The reverse direction — an Airflow DAG or Temporal workflow invoking a Facetwork
run as one of *its* tasks — is the complementary adoption path and is also out of
scope here, though it is strictly easier (it is a REST call to submit plus a poll,
and `fw ffl run` already exists).

## 2. The precedent, and the gap it leaves

`examples/aws-lambda` already does FFL-orchestrates-an-external-engine, for AWS
Step Functions
([`ffl/lambda_stepfunctions.ffl`](../../examples/aws-lambda/ffl/lambda_stepfunctions.ffl),
[`handlers/stepfunctions_handlers.py`](../../examples/aws-lambda/handlers/stepfunctions_handlers.py)):

```ffl
namespace aws.stepfunctions {

    /** Abridged from aws.lambda.types, so this excerpt stands alone. */
    schema ExecutionResult {
        execution_arn: String,
        status: String,
        output_payload: String
    }

    event facet StartExecution(state_machine_arn: String,
        input_payload: String = "{}",
        execution_name: String = "") => (result: ExecutionResult)

    event facet DescribeExecution(execution_arn: String) => (result: ExecutionResult)
}
```

The handler calls `boto3` and returns. `StartExecution` returns immediately with
`status: "RUNNING"`; `DescribeExecution` reports whatever the status is *at that
moment*. **Nothing ever waits for the external run to finish.**

That is fine for a demo and wrong for real work, and FFL cannot paper over it:
the language has no loop construct other than `foreach` over a known collection,
so there is no way to express "poll until done" in FFL itself. Waiting has to
happen inside the adapter.

**Waiting is the whole problem.** Everything below is about it.

## 3. The key insight: this is the polyglot agent protocol

Facetwork already has a versioned, language-agnostic contract for *work executed
outside the runner fleet*:
[`agents/protocol/constants.json`](../../agents/protocol/constants.json) and
[`docs/guides/multi-language-handlers.md`](../guides/multi-language-handlers.md).
Six steps against MongoDB:

1. **Claim** an event task (atomic `findOneAndUpdate`, filtered by task name and
   `task_list_name`).
2. **Read step params** from the `steps` collection via the task's `step_id`.
3. **Do the work.**
4. **Write return attributes** onto the step document.
5. **Mark the task `completed`.**
6. **Insert an `fw:resume` task.** The Python `RunnerService` picks it up and
   advances the workflow.

That contract says nothing about *what* does the work, or *when*. A Java agent
uses it to run GraphHopper in a JVM. The thesis (§5.6) already describes it for
"external agents that execute a facet outside the runner fleet".

> **A Temporal or Airflow adapter is this protocol pointed at a workflow engine
> instead of a language runtime.** Steps 3–6 simply happen later, in a different
> process, once the external run finishes.
>
> No new runtime concept is required. That is the central claim of this design.

## 4. Three depths of delegation

|      | Pattern | Waits? | Holds a worker slot? | Status |
|------|---------|--------|----------------------|--------|
| **D1** | Fire-and-describe — separate `Submit` / `Describe` facets | No | No | What `examples/aws-lambda` does today |
| **D2** | Blocking adapter with heartbeat | Yes | Yes, for the whole external run | Works today, no new code |
| **D3** | Detached submit, resume on completion | Yes | No | **Recommended** |

### 4.1 D2 — blocking adapter

The handler submits, then polls in a loop until the external run finishes,
heartbeating between polls. It works with today's runtime, and for short external
runs (minutes) it is the right answer because it is trivial.

Two configuration requirements, both easy to get wrong
([`docs/guides/long-running-handlers.md`](../guides/long-running-handlers.md)):

- Register with `timeout_ms=0` so the per-handler timeout does not apply.
- Raise **both** `FW_TASK_EXECUTION_TIMEOUT_MS` *and* `FW_STUCK_TIMEOUT_MS`.
  Setting only the first leaves the stuck reaper on its 30-minute default, which
  will kill the task while the work is still running.

Better still, use the **staged timeout** API rather than raising global env vars
([`facetwork/runtime/handler_context.py`](../../facetwork/runtime/handler_context.py)):

```python
with ctx.stage("temporal-run", timeout_ms=30 * 60_000) as s:
    while not done:
        s.heartbeat(progress_message=f"{status}")
        if slower_than_expected:
            s.extend(15 * 60_000)      # grow the budget mid-flight
```

`ctx.stage()` is a context manager that sets `stage_budget_expires` on the task
and **clears it on exit**, so the scope is exactly the external call.

The cost of D2 is real: one runner worker slot is occupied for the entire
external run. With `FW_MAX_CONCURRENT` defaulting to 2, three concurrent
four-hour Temporal workflows saturate two runners.

### 4.2 D3 — detached submit and resume (recommended)

The adapter claims the task, submits to the external engine, records the external
handle on the step, and **parks** — it does not complete the task and does not
hold a thread. When the external run finishes, a completion path performs steps
4–6 of the protocol: write returns, mark completed, insert `fw:resume`.

```
  Facetwork                          Adapter                    Temporal/Airflow
  ─────────                          ───────                    ────────────────
  task pending  ──────claim─────────▶
                                     derive external id
                                     submit ──────────────────▶ run starts
                                     set stage budget
                                     park (handler returns)
                                                                   … hours …
                                                                run completes
                     ◀──── write returns + completed ────────── callback/reconciler
                     ◀──── insert fw:resume ────────────────────
  evaluator advances the workflow
```

Two completion paths, and a design should support **both**:

- **Callback** — Temporal's final activity, or Airflow's `on_success_callback`,
  writes the result. Low latency.
- **Reconciler** — a small process that lists in-flight external runs and
  completes the ones that have finished. This is the safety net: a lost callback
  must not strand a workflow forever, and callbacks are lost.

## 5. The parked-task problem

A D3 task sits in state `running` for hours with no thread behind it. Three
independent mechanisms want to reclaim it.

| Mechanism | Trigger | Does a stage budget protect it? |
|---|---|---|
| Lease | `lease_expires` passes; another runner reclaims | **Yes** — the budget extends the lease |
| Stuck watchdog `reap_stuck_tasks` | inactivity beyond `timeout_ms` / `FW_STUCK_TIMEOUT_MS` | **Yes** |
| Orphan reaper `reap_orphaned_tasks` | the *claiming server* stops heartbeating | **No** |

There is no `awaiting_external` task state. The states are
`pending | running | completed | failed | ignored | canceled` (+ `dead_letter`,
see §5.2). Adding one would touch the state model, the claim filter and every
reaper — and it turns out not to be necessary.

### 5.1 Reuse the stage budget; do not invent a state

[`facetwork/runtime/mongo_store/tasks.py`](../../facetwork/runtime/mongo_store/tasks.py)
skips any running task whose stage budget has not expired:

```python
stage_budget = doc.get("stage_budget_expires", 0) or 0
if stage_budget > now:
    continue  # inside an active stage budget — don't reap
```

and `update_task_stage_budget()`
([`mongo_store/servers.py`](../../facetwork/runtime/mongo_store/servers.py))
renews the lease to cover it:

```python
"lease_expires": max(now + lease_ms, budget_expires)
```

So the parking primitive already exists. Note the distinction:

- **D2 (blocking)** uses the `ctx.stage()` **context manager** — scoped,
  self-clearing.
- **D3 (parked)** must call `update_task_stage_budget(task_id, expires, name)`
  **directly**, because the handler returns while the budget must persist. The
  context manager clears the budget in its `finally` block, which is exactly
  wrong for a park. The watcher extends the budget periodically for as long as
  the external run is alive.

**The orphan reaper deliberately still applies.** If the adapter host dies, the
parked task returns to `pending` and is re-delivered. That is correct recovery,
not a bug — *provided submission is idempotent*, which is §6. Recovery and
idempotency are the same design decision viewed from two sides.

### 5.2 A protocol drift — fixed (kept as a standing rule)

This section used to record a prerequisite: `TaskState` in
[`facetwork/runtime/entities/task.py`](../../facetwork/runtime/entities/task.py)
defines `DEAD_LETTER = "dead_letter"`, but the published contract in
`agents/protocol/constants.json` (then v1.0) listed only six states and omitted
it — so an adapter written against the published contract would meet a
dead-lettered task as an unknown value.

**Resolved** (d12a389): `constants.json` is now **v1.1** and publishes all seven
states — `pending`, `running`, `completed`, `failed`, `ignored`, `canceled`,
`dead_letter`. No action is outstanding here; the remaining prerequisite for
delegation is cancellation propagation (§7.2).

The rule it leaves behind still applies, because the failure was silent in both
directions: **`constants.json` is the contract an out-of-process adapter compiles
against, so a new `TaskState` is not shipped until the same change publishes it
there.** An adapter should read the state vocabulary from the contract rather
than hardcode it, and treat an unrecognised state as non-terminal-unknown
(log and re-poll) rather than as a terminal value — that way a future addition
degrades instead of silently mis-deciding.

## 6. Idempotent submission: derive the id, do not configure it

Facetwork delivers **at least once**. `max_retries` defaults to 5; the orphan
reaper resets tasks from dead servers; the lease-reclaim path picks up tasks
whose lease expired. Any of these can re-deliver a task whose handler *already
submitted to the external engine*. A naive adapter double-submits, and now two
Temporal workflows are doing the same work.

The fix is to make the external run id a **derived, intrinsic fact** rather than
a generated or configured one:

```
external_id = f"fw-{step_id}"
```

`step_id` is unique per step and stable across retries, so every re-delivery
computes the same id.

| Engine | Mechanism | Duplicate submit behaves as |
|---|---|---|
| Temporal | `workflow_id=f"fw-{step_id}"` with `WorkflowIdReusePolicy.REJECT_DUPLICATE` | Deterministic rejection; adapter attaches to the existing handle |
| Airflow | `dag_run_id=f"fw-{step_id}"` | 409 Conflict; treated as "already submitted, attach" |
| Step Functions | `name=f"fw-{step_id}"` on `StartExecution` | `ExecutionAlreadyExists`; attach |

This converts at-least-once *delivery* into effectively-once *external
execution*, without any distributed-transaction machinery.

It is also the same discipline
[`paper-intrinsic-routing.md`](../thesis/paper-intrinsic-routing.md) argues for
in routing: the correlation key is computed from a fact both sides already
possess, so it cannot desync. A configured or randomly-generated id would have to
be stored and looked up, and the store-then-crash window is precisely where
duplicates come from.

**Stated honestly:** thesis §14.2 lists "no idempotence enforcement" as an open
problem — handlers own idempotency and the compiler does not check it. The above
is an adapter *convention*, not a platform guarantee. An adapter that ignores it
will double-submit and nothing will stop it.

## 7. Operational hazards

### 7.1 Credential gating — a documented incident, not a hypothetical

Handler *importability* is the intrinsic capability signal: a runner advertises a
facet only if its module imports in that process
(`RegistryDispatcher.preload(verify=True)`). Importability does **not** capture
"holds Temporal mTLS certs" or "can reach the Airflow API".

So every runner that installs the adapter package advertises its facets, and
uncredentialed runners claim tasks they cannot serve. This has already happened
here: roughly thirteen runners advertised `PublishWebBundle` while only one host
held `GITHUB_TOKEN`; the others claimed, failed, and dead-lettered the work.

Mitigations, in order of preference:

1. **Give adapters their own namespace** (`temporal.*`, `airflow.*`). Task lists
   are derived from the top-level namespace
   ([`task_list_routing.py`](../../facetwork/runtime/task_list_routing.py)), so a
   distinct namespace isolates the claim pool for free, with no configuration.
2. **Gate the role with `server_groups`**
   ([`server-groups.md`](server-groups.md)) so only credentialed hosts start the
   adapter runner at all.
3. Do not rely on the handler failing fast — by then the task has been claimed
   and burned a retry.

### 7.2 Cancellation — the in-process half is now implemented

Delegation makes cancellation materially worse than it is locally:
`fw maint terminate-workflow` marks Facetwork state terminal, and a delegated
Temporal workflow or Airflow DAG would keep running — and keep billing —
indefinitely, with nothing left pointing at it.

**Half of this is now done.** Cooperative cancellation shipped
([`lessons-learned.md`](lessons-learned.md) §16,
`facetwork/runtime/cancellation.py`): every dispatch carries a token, and a
handler asks it whether its result would still be accepted — operator terminate,
a watchdog that already failed the task, or a reclaim that turned it into a
zombie. That is exactly the signal a D3 adapter's watcher needs: it is the thing
already polling while the task is parked, so it is the natural place to call
`terminate(external_id)` and stop waiting.

**What an adapter still owes:**

- a `terminate(external_id)` hook in its contract, and
- the wiring so a cancelled park actually invokes it — the watcher acts on the
  token, and `fw maint terminate-workflow` reaches parked steps that no runner is
  currently watching (a host may have died holding the park).

The second point is the sharp edge: a park whose watcher is gone has no process
left to notice the cancellation, so terminate must be able to act from the
external handle recorded in persistent state (§6's derived id is what makes that
possible — the id is re-derivable, not held only in the dead process's memory).
**Until an adapter implements both, delegation still leaks by construction**; the
runtime no longer stands in the way.

## 8. The boundary contract

### 8.1 What may cross

**URIs, never blobs.** Temporal's default gRPC payload limit and Airflow's XCom
backend both bite well below the size of Facetwork step payloads. Facetwork
already passes `s3://` URIs between steps on the fleet; keep exactly that
convention at the external boundary.

**FacetRef must not cross.** A facet-typed parameter binds a `StepReference` and
`$.ds.field` is dereferenced lazily against the upstream step's persisted record.
That is pass-by-reference into Facetwork's own store and is meaningless in
another engine. Project the specific fields into explicit parameters instead.

**Errors map onto `catch`.** A failed external run becomes a step error, and the
runtime stores `$.error` and `$.error_type` as pseudo-returns — so `catch when`
over external failure classes works with no adapter-specific machinery:

```ffl
namespace temporal.example {

    event facet RunTemporalWorkflow(workflow_type: String) => (output_uri: String)
    event facet Requeue(reason: String) => (ok: Boolean)
    event facet Alert(reason: String) => (ok: Boolean)

    workflow Ingest(kind: String = "IngestBatch") => (ok: Boolean) andThen {
        result = RunTemporalWorkflow(workflow_type = $.kind) catch when {
            case $.error_type == "TimeoutError" => {
                requeued = Requeue(reason = $.error)
                yield Ingest(ok = requeued.ok)
            }
            case _ => {
                alerted = Alert(reason = $.error)
                yield Ingest(ok = alerted.ok)
            }
        }
    }
}
```

Note the syntax: cases are `case <cond> => { … }` and the mandatory default is
`case _ => { … }`, last. Each case body is a block, not a bare call.

### 8.2 Worked example — Temporal

```ffl
namespace temporal.delegate {

    /** Run a Temporal workflow and wait for it. The adapter derives the
      * Temporal workflow id from the step id, so a redelivered task attaches
      * to the running workflow instead of starting a second one. */
    event facet RunWorkflow(
        workflow_type: String,
        task_queue: String,
        input_uri: String = "",
        run_timeout_minutes: Int = 240
    ) => (
        run_id: String,
        status: String,
        output_uri: String
    ) with Effect(kind = "external") with Cost(tier = "expensive")
      with Timeout(minutes = 240)
}
```

Adapter, in outline:

```python
def handle(payload):
    ctx = HandlerContext.from_payload(payload)
    external_id = f"fw-{payload['_step_id']}"          # derived, not generated

    handle = temporal.start_workflow(                   # idempotent by policy
        payload["workflow_type"],
        id=external_id,
        task_queue=payload["task_queue"],
        id_reuse_policy=REJECT_DUPLICATE,
    )                                                   # duplicate -> attach

    store.update_task_stage_budget(                     # park, do not block
        payload["_task_id"],
        budget_expires=now_ms() + payload["run_timeout_minutes"] * 60_000,
        stage_name="temporal-run",
    )
    return PARKED          # no returns written yet; the watcher finishes it
```

The watcher (or Temporal's final activity) then performs protocol steps 4–6.

### 8.3 Worked example — Airflow

Airflow's DAG is a unit of *deployment*, not of invocation, so the natural grain
is one FFL step per DAG **run**:

Where the DAG writes to shared storage and returns no value, the ordering is
invisible to the compiler and must be stated with `after`:

```ffl
namespace airflow.delegate {

    /** Trigger an Airflow DAG run and wait for it. A duplicate trigger returns
      * 409 from the REST API, which the adapter reads as "already submitted"
      * and attaches to. */
    event facet RunDag(
        dag_id: String,
        conf_json: String = "{}",
        run_timeout_minutes: Int = 120
    ) => (
        dag_run_id: String,
        state: String
    ) with Effect(kind = "external") with Cost(tier = "expensive")
      with Timeout(minutes = 120)
}

namespace airflow.example {
    use airflow.delegate

    event facet BuildReport(title: String) => (path: String)

    workflow NightlyReport(title: String = "nightly") => (path: String) andThen {
        ingest = RunDag(dag_id = "warehouse_ingest")

        // INVISIBLE dependency: the DAG wrote to the warehouse and no value
        // flows back, so the edge has to be stated.
        report = BuildReport(title = $.title) after ingest

        yield NightlyReport(path = report.path)
    }
}
```

## 9. What does not cross the boundary

Delegation is also a way of finding out which parts of Facetwork are genuinely
distinctive: they are the ones that stop working at the boundary.

| Capability | Crosses? | Why not |
|---|---|---|
| Staged timeouts with mid-flight `extend()` | No | Temporal's `StartToCloseTimeout` is flat per-activity; splitting a handler to approximate it interacts badly with the determinism constraint (thesis §8.7) |
| Live handler updatability | No | Airflow keeps the DAG a run started with; Temporal needs a worker restart |
| Claim-side capability veto | No | Queues give *placement*; none of these engines lets a worker **decline** work it does not understand. Step Functions has no worker concept at all |
| Intrinsic routing | No | The external queue/DAG name is free-floating configuration on both sides — the drift class `paper-intrinsic-routing.md` §4 warns about |
| `Re-run From Here` / `workflow_repair` | Partially | You can re-run the FFL step, which resubmits the whole external run. You cannot re-run from the middle of it |
| Relative scoping, `after`, yield merge | N/A | These are evaluated inside Facetwork; the external engine never sees them |

The honest reading: delegation gives Facetwork **reach**, not **portability**.
Everything genuinely novel about the platform stays on the Facetwork side of the
line, and each delegated step is a small hole in the model where the external
engine's semantics apply instead.

## 10. Non-goals

- **Transpiling FFL** to native Airflow/Temporal artifacts. That is the
  portability story and a different design.
- **Running the FFL evaluator *on* Temporal** as a fourth continuation mode.
  This is the most promising follow-on —
  [`ffl-runner-orchestration-tier.md`](ffl-runner-orchestration-tier.md) §2
  already draws the equivalence (`ffl-runner` ≈ Temporal workflow-task worker,
  handler runner ≈ activity worker) and the `--continuation` flag already exists.
  Out of scope here.
- **External engines invoking Facetwork.** The complementary adoption path.
- **Production adapter code.** This document is a design.

## 11. If this is prototyped: acceptance checklist

Adapters park work across processes and machines, so the failure modes in
[`paper-parity-gaps.md`](../thesis/paper-parity-gaps.md) §4 apply directly. Any
prototype should be held to:

- **G4 (name vs identity).** Under `foreach`, a step *name* is a role shared by
  many step instances. Every external correlation must key on `step_id`, never a
  step name. §6's derived id already satisfies this — do not weaken it.
- **G5 (re-derivability).** Every artifact the adapter holds must be
  re-derivable from persistent state on another node, because the orphan reaper
  will move a parked task to a different host mid-flight.
- **G3 (commit visibility).** Protocol steps 4–6 are separate writes. What does
  the evaluator see if the process dies between "write returns" and "insert
  `fw:resume`"? (The stuck-step sweep should recover it — verify, do not assume.)
- **§M2 policy: a feature is not done until it has run distributed.** Five of
  five bugs in the distributed-catch campaign were invisible in a
  single-process test double.

Plus two adapter-specific tests:

- **Duplicate delivery.** Deliver the same task twice; assert exactly one
  external run exists.
- **Terminate propagation.** `fw maint terminate-workflow` on a run with a
  parked step; assert the external run is terminated, not orphaned.

## See also

- [`docs/guides/multi-language-handlers.md`](../guides/multi-language-handlers.md) — the six-step protocol this builds on
- [`docs/guides/long-running-handlers.md`](../guides/long-running-handlers.md) — heartbeats, and the two timeout variables
- [`ffl-runner-orchestration-tier.md`](ffl-runner-orchestration-tier.md) — the orchestration/execution split, done once internally
- [`ffl-after-clause.md`](ffl-after-clause.md) — ordering for invisible dependencies
- [`server-groups.md`](server-groups.md) — capability-tiered placement
- [`docs/thesis/thesis.md`](../thesis/thesis.md) Ch. 9 (Temporal), Ch. 11 (Airflow), §14 (limits)
