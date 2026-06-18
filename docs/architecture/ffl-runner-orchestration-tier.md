# Dedicated `ffl-runner` orchestration tier (design)

**Status:** Steps 1 & 2 of the rollout (§7) are **implemented** (the `--continuation` mode flag + the `runtime_version` gate, both no-ops by default). Steps 3–5 (the `ffl-runner` fleet role + flipping handler runners to `off`) are not yet done.

**Implementation note (code vs this doc's model):** the two runner classes drain continuations differently — `RegistryRunner` polls the shared `_fw_continue` backlog directly, while `RunnerService` (the fleet's "runner service in registry mode") relies on the inline cascade after its own handler plus the stuck-step sweep. The `--continuation` flag therefore gates *wherever each mechanism exists*: the `_fw_continue` poll loops in `RegistryRunner` and the stuck-step sweep in both classes. Mode `off` skips both; `inline`/`shared` keep them.
**Related:** [runtime.md §10.3.1](../reference/runtime.md) (per-step processing + continuation events), [runtime.md §10.4](../reference/runtime.md) (stuck-step sweep), [fleet-rollouts.md](../operations/fleet-rollouts.md) (image rollout), [task_list_routing.py](../../facetwork/runtime/task_list_routing.py).

## 1. Problem

Today the workflow **orchestration** logic — "given a step that just progressed, which steps run next?" — is **embedded in every runner** and run in-process by whichever runner claims the next continuation event. Concretely:

- `continuation.py` emits lightweight tasks on the internal `_fw_continue` task list when a step progresses.
- The **step state machine** (`StepState` in `states.py`, the `*ContinueHandler` classes in `runtime/handlers/`, driven by `StepStateChanger` in `changers/step_changer.py`) plus the **evaluator** (`evaluator.py`) advance blocks and create the next handler tasks.
- Every `RegistryRunner` / `RunnerService` does the same two things each poll cycle: (1) claim handler tasks for facets it can load, and (2) claim `_fw_continue` and advance the workflow.

This is leaderless and has no single point of failure — but it has two operational costs, both hit in practice:

1. **Fleet-wide blast radius for engine changes.** Because *every* runner runs the state machine and **any** runner can advance **any** workflow, a change to the continuation/state-machine code must reach *every* runner — not just the ones serving a given facet (the way a handler change does). There is also **no version gate**: `runtime_version` / `step_schema_version` are stored on steps but never compared, and `VersionMismatchError` (`errors.py`) is defined but never raised. So during a rolling update, old- and new-engine runners both process the same workflows' continuations with zero protection. For incompatible changes (new step states, changed step-doc shape, changed continuation schema or dedup semantics) that can mis-advance or livelock a workflow.

2. **No bulkhead between orchestration and handler work.** Orchestration is CPU-light, latency-sensitive, and Mongo-chatty; handler work (e.g. a multi-GB `osmium` export) is heavy, long, and bursty. They share the same process and worker slots, so heavy handlers can starve continuation processing — which is why `AFL_MAX_CONCURRENT` must be tuned down for OSM runners and why the stuck-step sweep (§10.4) must explicitly *yield to claiming* and bound its work to avoid livelock.

## 2. Proposal

Introduce a dedicated **`ffl-runner`** role that owns the shared orchestration backlog, while keeping the system decentralized: run **one or more `ffl-runners` per machine / across the fleet**, leaderless, coordinating through the same atomic `claim_task()`. This is *role* separation, not centralization — there is still no master.

It mirrors a well-established pattern: Temporal's split between **workflow-task workers** (advance workflow state) and **activity-task workers** (run the actual work). Here, `ffl-runners` ≈ workflow-task workers; handler runners ≈ activity-task workers.

### 2.1 Hybrid model (recommended)

A *strict* split (handler runners never touch the state machine) would forfeit the **inline-dispatch** optimization: today `process_single_step()` cascades up parent blocks and dispatches child handlers **inline, in the same process, bypassing the queue** when a local handler is available (`dispatcher.py`). A pure orchestration tier has no handlers, so every child becomes a queue round-trip (extra latency + Mongo ops), penalizing cheap/fast facets.

So keep the fast path; offload only the backlog:

- **Handler runners** still call `process_single_step()` **inline** right after their own handler completes (preserves inline dispatch and throughput on the common local case) — but they **stop polling the shared `_fw_continue` backlog**.
- **`ffl-runners`** own (a) the shared `_fw_continue` list (the cross-server continuations any runner picks up today) and (b) the **stuck-step sweep** (§10.4).
- Both gate on `runtime_version` (see §3.3).

```
                MongoDB — atomic claim_task(); single source of truth
  ┌────────────────────────────────────────────────────────────────────────┐
  │ tasks                                                                    │
  │  • fw:execute:<Workflow>        bootstrap                                │
  │  • <facet handler tasks>        real work                               │
  │  • fw:resume:<Facet>            external agent finished                  │
  │  • _fw_continue                 shared orchestration backlog            │
  └────────────────────────────────────────────────────────────────────────┘
        ▲ handler tasks + inline                 ▲ _fw_continue + sweep
        │ process_single_step() after own handler│ (shared backlog only)
        │                                         │
  ┌─────┴───────────────┐                   ┌─────┴───────────────────┐
  │ HANDLER runners      │                   │ ffl-runners (N, leaderless)
  │ (anthropic, osm, …)  │  many, hetero     │ small, homogeneous       │
  │ • run handlers       │                   │ • drain _fw_continue      │
  │ • inline cascade     │                   │ • stuck-step sweep        │
  │   after own handler  │                   │ • version-gated           │
  │ • do NOT poll the    │                   │ • engine tier of record   │
  │   shared backlog     │                   │                          │
  └──────────────────────┘                   └──────────────────────────┘
```

## 3. Design details

### 3.1 Runner mode flag

Add a runner mode controlling continuation behaviour (mechanism is ~90% there — `_fw_continue` is already a dedicated task list; only *who polls it* changes):

| Mode | Polls shared `_fw_continue`? | Inline `process_single_step()` after own handler? | Runs stuck-step sweep? |
|------|------------------------------|---------------------------------------------------|------------------------|
| `inline` (default, = today) | yes | yes | yes |
| `off` (handler-only) | **no** | yes | no |
| `shared` (ffl-runner) | yes | n/a (no handlers) | yes |

e.g. `--continuation {inline|off|shared}` (or `AFL_CONTINUATION_MODE`). Handler runners on the fleet run `off`; `ffl-runners` run `shared`; single-box dev keeps `inline` (no separate tier needed). The state-machine code stays in the shared `facetwork` package — only its *execution* (polling/sweep) is gated, so a handler runner can still inline-dispatch after its own handler without owning the backlog.

### 3.2 Fleet config + deploy

- New `fleet-runner` role analogue in `fleet_config.roles` — `ffl-runner` with its own `replicas` and `image`. Deployed via the existing `buildx → registry → fleet set --image` path.
- **Blast radius shrinks:** a state-machine change only needs to roll to the `ffl-runner` role (a small, identical set), not to every per-example handler runner. Handler changes never touch the `ffl-runner` tier.

### 3.3 Version gating (do this regardless)

The tier still processes shared continuations concurrently, so mixed versions are still possible *within* the tier during a roll. Enforce the currently-dormant gate:

- On claiming a `_fw_continue` (or processing a step), compare the step's stored `runtime_version` against the runner's; on incompatibility, **release the task** (don't fail it) and let a compatible runner take it — raising/handling `VersionMismatchError` instead of silently processing.
- This makes an incompatible engine cutover safe to do as: deploy new `ffl-runners` alongside old, new ones ignore old-version steps and vice versa, drain, retire old. (Compatible/bugfix changes need no gate and roll freely.)

### 3.4 Liveness

- If all `ffl-runners` are down, workflows stall at `Continue` until one returns — no corruption (idempotent + optimistic `version.sequence`), and the sweep recovers on resume. Run ≥2 `ffl-runners` across ≥2 machines.
- The stuck-step sweep moves to the `ffl-runner` tier (it's orchestration, not handler work).

## 4. Trade-offs

| | Today (fused) | Hybrid `ffl-runner` tier |
|---|---|---|
| Engine-change blast radius | every runner | small `ffl-runner` tier only |
| Orchestration vs handler isolation | shared process (starvation risk) | bulkheaded |
| Inline dispatch (fast path) | yes | yes (kept on handler runners) |
| Shared-backlog latency | any runner picks up | dedicated tier (slightly more queue hops for cross-server cascades) |
| Mixed-version safety | none (ungated, fleet-wide) | gated, small tier — tractable cutover |
| Moving parts | one role | one extra role + a mode flag |

## 5. Non-goals

- Not a centralized scheduler / leader. Still N leaderless `ffl-runners`.
- No data-model change — continuations still flow through `_fw_continue` in Mongo.
- Not removing inline dispatch; the hybrid preserves it.

## 6. Open questions

- Should handler runners in `off` mode still emit continuations for *non-local* parent blocks (and let the tier drain them), or always inline-cascade as far as possible first? (Lean: inline as far as local, emit the remainder — same as today, just don't *poll* the backlog.)
- Co-locate an `ffl-runner` on every machine for locality, or a smaller dedicated set? (Per-machine keeps it simple and matches the "one or more per machine" intent; a smaller set is easier to version-cut-over.)
- Does the dashboard/`fw:execute` bootstrap path need any change? (No — bootstrap creates the first task; the tier takes it from there.)

## 7. Rollout plan (incremental, low-risk)

1. ✅ **Done** — `--continuation {inline,off,shared}` mode flag (`AFL_CONTINUATION_MODE`), default `inline` = today's behaviour. Gates the `_fw_continue` poll (`RegistryRunner`) and the stuck-step sweep (both runner classes) via `BaseRunnerConfig.polls_shared_continuations()` / `runs_stuck_step_sweep()`.
2. ✅ **Done** — `runtime_version` gate (§3.3): `STEP_RUNTIME_VERSION` single-source constant + `is_runtime_compatible()` (`types.py`), enforced in `process_single_step()` (leaves an incompatible step untouched) and `RegistryRunner._process_continuation()` (releases the continuation back to pending for a compatible runner). No-op until `STEP_RUNTIME_VERSION` is bumped. Tests: `tests/test_continuation_mode_and_version_gate.py`.
3. Add the `ffl-runner` role to `fleet_config` + compose; bring up ≥2.
4. Flip the per-example handler runners to `--continuation off`.
5. Observe: continuation latency, `ffl-runner` queue depth, sweep cadence. Tune `ffl-runner` replicas.

Each step is reversible (flip the flag back), so it can be trialed on the local full-stack before the fleet.
