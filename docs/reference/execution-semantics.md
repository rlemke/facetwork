# Execution semantics: what the runtime guarantees, and what your handler must

This states the delivery contract precisely, because "distributed workflow
engine" implies things Facetwork does not do. Every claim here was checked
against the code on 2026-09-03; the reproduction command is given wherever a
number appears.

> **In one line:** task delivery is **at-least-once**, there is **no fencing
> token**, and the control plane (MongoDB) and the data plane (object store) are
> **separate systems with no transaction between them**. Handler idempotency is
> therefore a requirement, not an optimisation.

---

## 1. Delivery is at-least-once

A task can execute more than once. There are four paths, and they are not
equally likely.

### 1.1 Lease expiry — guarded, but only by an ordering invariant

A claimed task carries a lease. If it expires, another runner may claim the task
while the first is still executing it.

That is prevented by making the lease outlast the execution timeout:

```
lease = max(DEFAULT_LEASE_MS, execution_timeout + RECLAIM_GRACE_MS)
      = max(5 min,            15 min          + 1 min)             = 16 min
```

An explicit `FW_LEASE_DURATION_MS` below that floor is **clamped up**, with a
warning, rather than honoured — see `facetwork/runtime/mongo_store/base.py`
(`_lease_ms`). This is a correctness invariant, not a preference: a 5-minute
lease beside a 15-minute execution timeout means the work runs twice, and
"reasonable-looking" settings produced exactly that. The floor is recomputed
from the *current* execution timeout on every call, so raising the timeout for
one domain raises that domain's lease floor too.

The same grace is held above **all three** reclaim paths (lease expiry, the
per-task stuck reap, the default stuck reap) so the owning runner always acts
first on its own wedged task.

### 1.2 The dead-server reaper — the real at-least-once window

`reap_orphaned_tasks()` resets tasks whose claiming server has not pinged within
`FW_REAPER_TIMEOUT_MS` (default 2 min), and marks that server `shutdown`.

⚠️ **"Has not pinged" is not the same as "is not running."** A runner that is
alive but partitioned from MongoDB, or paused, or whose host suspended, stops
pinging while its handler keeps working. The reaper then hands the task to
another runner, and **nothing stops the first one's writes** — there is no
fencing token, no epoch, no generation number on handler output. Both
executions are equally entitled to write.

This is not hypothetical: a host hibernated for 7.4 h mid-run during a
county-atlas fan-out, and from MongoDB that is indistinguishable from death.

The reaper is what makes the informal fleet work — laptops genuinely do
disappear — so this is a deliberate trade, not an oversight. But it means
**a handler must be safe to run twice concurrently**, not merely twice in
sequence.

### 1.3 Retry after failure

A failed task is retried with backoff up to `max_retries`, then dead-lettered.
A handler that failed *after* performing a side effect will repeat that side
effect on retry.

### 1.4 Deliberate re-drive

`fw maint repair-workflow`, `fw maint dead-letters --retry`, and the dashboard's
Retry / Re-run From Here all re-enqueue work on purpose. These are operator
actions, but they land on the same handler with the same guarantees.

---

## 2. What the platform does guarantee

- **A single claimant at a time, under normal operation.** `claim_task()` is one
  atomic `find_one_and_update`; concurrent runners race and exactly one wins.
  There is no queue lock, no leader, no dispatcher.
- **Claims are capability-filtered.** A runner claims only work it can run,
  across three dimensions: the facet **name** (it has the handler), the script
  **`environment_hash`**, and the resource floor in **`requires`**. Absent means
  unconstrained. See `fw maint unsatisfiable` for the failure mode where nothing
  matches — such a task is never claimed, so it never errors and never
  dead-letters.
- **Ordering follows data.** A step that consumes another's result runs after
  it. Where state flows invisibly (a shared cache, an object store, a scratch
  dir), you must say so with `after`; the compiler cannot see it.
- **Cooperative cancellation.** `ctx.raise_if_cancelled()` / `ctx.is_cancelled`
  let a long handler stop cleanly when the run is terminated, when a watchdog
  already failed the task, or when a reclaim made this execution a zombie. A
  cancelled handler is a clean stop: no retry, no failed step.

---

## 3. What the platform does NOT guarantee

| Not guaranteed | What that means for you |
|---|---|
| Exactly-once execution | Make handlers idempotent. See §4. |
| Fencing of a superseded executor | Two executions can write concurrently. Prefer content-addressed or step-id-derived output paths over "latest wins". |
| Atomicity across MongoDB and the object store | A task can complete in Mongo after its data write, or die between them. Write data first, then record completion; treat a recorded completion as the only source of truth. |
| Ordering between steps that share only storage | Use `after`. |
| That a task will ever be claimed | A requirement no runner meets strands it silently. `fw maint unsatisfiable`. |
| Cross-run reproducibility of an LLM or external API step | Record the model/version/parameters in the output if you need it. |

---

## 4. Effect classes — declare what your facet does

A facet may declare its effect, which is what tells a reader (and an
LLM composing workflows) whether repeating it is safe:

```
event facet Download(url: String) => (path: String)
    with Effect(kind = "io") with Cost(tier = "moderate")
```

`kind` is one of `pure`, `external`, `io` (`facetwork/capabilities/index.py`).
`fw_capabilities(effect=…, max_cost=…)` filters on it.

**Adoption is currently partial: 610 of 1,226 facets declare an effect.**
Reproduce with:

```bash
grep -rho 'with Effect(' ~/fw_handlers/fwh_*/ --include='*.ffl' facetwork/ffl/*.ffl | wc -l
grep -rhoE '^\s*(event )?facet [A-Za-z]' ~/fw_handlers/fwh_*/ --include='*.ffl' facetwork/ffl/*.ffl | wc -l
```

An undeclared facet is not assumed pure — it is simply unknown, and a composer
has to treat it as the worst case.

### Idempotency by effect class

| Effect | Safe to repeat? | What to do |
|---|---|---|
| `pure` | Yes | Nothing. |
| `io` (read) | Yes | Cache on a content hash, not a timestamp. |
| `io` (write) | Only if the write is addressed by content or `step_id` | Derive the output path from `step_id`; never append to a shared file. |
| `external` (read) | Usually, at a cost | Respect rate limits; a duplicate execution doubles spend. |
| `external` (mutate) | **No** | Derive an idempotency key from `step_id` and let the remote service dedupe. This is what the external-engine adapters do. |

⚠️ **The cases that actually bite** are external API mutations, publishing,
file promotion, database writes, long downloads, and paid LLM calls. A retry of
any of these is a second charge, a second row, or a second published artifact.

---

## 5. Cache and storage boundaries

- Handler caches and outputs live on a backend chosen by `FW_STORAGE` +
  `FW_DATA_ROOT`: `local`, `hdfs://`, or `s3://`.
- **Object stores do not do partial writes.** Handlers stage to a local scratch
  dir and finalize on close, so `FW_OUTPUT_BASE` / `FW_LOCAL_SCRATCH` must stay
  local. A crash mid-stage leaves scratch, not a corrupt object.
- **A cached artifact is reused only when provably current.** The built-in
  `fw.http` facets do this properly: conditional GET (`If-None-Match` → 304), a
  `checksum_url` where the publisher offers one, or a domain-set `max_age_hours`
  — and integrity is checked before reuse. Every fetch writes
  `<dest>.meta.json` with the digest, source URL, validators and `fetched_at`,
  so a derived artifact can detect that its input changed.
- ⚠️ A cache keyed on a **moving target** (`-latest`) freezes silently. One
  domain did exactly that, and another recorded a checksum it never compared.

---

## 6. Evaluating this for production

The claims above are about the code, not about scale. What has actually been
run is four machines and ~85 runners. Before depending on this in production,
measure, do not extrapolate:

- MongoDB replica-set failover, write concern, and task leases *during*
  failover.
- Behaviour under a network partition — specifically §1.2, since that is where
  duplicate execution comes from.
- Clock behaviour across hosts (leases and reaping are wall-clock based).
- Claims and completions per second against Mongo CPU, IOPS and lock time.
- How often duplicate execution actually happens under your workload.

See [informal-fleet.md](../operations/informal-fleet.md), which is explicit that
data-centre-scale operation is untested.
