# Lessons Learned: Building a Distributed Workflow Engine

Requirements and design decisions that would have saved significant debugging and rework if known upfront. Extracted from the Facetwork development history (v0.9 through v0.45).

Intended audience: teams building distributed task execution systems, workflow orchestrators, or multi-agent platforms.

---

## 1. Identity and Observability

**Requirement**: Every log message, error, and status display must include a human-readable qualified name that identifies the specific work item in context.

**What happened**: Early logs showed UUIDs like `Task 1c5552be-4545-419a-bf99-3b57d61d17e0 timed out`. Operators couldn't determine which step or region was affected without querying the database. This was fixed late by adding `_task_label()` which resolves ancestor step names into paths like `Kentucky.imp.imported (osm.ops.PostGisImport)`.

**Upfront requirement**:
- All runtime entities (steps, tasks, logs) must carry or resolve a **qualified display name** built from the hierarchy (e.g. `parent.child.step`).
- Log messages at WARNING and above must include this name, not just IDs.
- Dashboard views must show qualified names by default, not raw statement names.
- Task names should include the workflow or facet name for identification (e.g. `fw:execute:MyWorkflow` instead of `fw:execute`).

---

## 2. Execution Isolation

**Requirement**: Each workflow run must have a unique execution namespace. No two runs may share step or task state.

**What happened**: The dashboard reused the `WorkflowDefinition` UUID as the execution `workflow_id`. Two runs of the same workflow shared steps, causing parameter cross-contamination (user entered "Texas" but got results from a previous "CA" run).

**Upfront requirement**:
- Distinguish between **definition IDs** (immutable, shared) and **execution IDs** (unique per run).
- The workflow template/definition UUID is not the execution ID.
- Generate a fresh execution ID for every "Run" action.
- Validate at the persistence layer: `workflow_id` on steps/tasks must be unique per run.

---

## 3. Completion Invariants

**Requirement**: A workflow must not be marked complete until all its tasks and steps are in terminal states.

**What happened**: The evaluator finished its loop and marked the runner `completed` while tasks were still running asynchronously on servers. This left orphaned tasks that no mechanism cleaned up (the reaper checks for dead servers, not completed runners with live tasks).

**Upfront requirement**:
- **Completion guard**: Before transitioning to COMPLETED, verify: `for all tasks where workflow_id = W: task.state in {COMPLETED, FAILED, IGNORED, CANCELED}`.
- **Consistency check**: Steps marked Complete must have a corresponding completed task (not failed). Detect and flag steps with `state=Complete` but `task.state=Failed`.
- Document and enforce these invariants as preconditions on state transitions.

---

## 4. Failure Recovery as a First-Class Feature

**Requirement**: Design explicit recovery mechanisms for every failure mode before building the happy path.

**What happened**: Recovery was added reactively across five separate mechanisms over six months:
1. Orphan reaper (dead servers)
2. Stuck task watchdog (hung handlers on live servers)
3. Dashboard reaper (independent cleanup cycle)
4. Execution timeout (hung thread pool futures)
5. Workflow repair tool (catch-all diagnosis and fix)

Each was built in response to a production incident.

**Upfront requirement**:
- Perform a **Failure Mode and Effects Analysis (FMEA)** before implementation:
  - Server crashes mid-task
  - Database connectivity loss (transient and extended)
  - Handler hangs indefinitely
  - Runner shuts down with in-flight work
  - Network partition between runner and database
  - Concurrent runners claiming the same work
- For each failure mode, specify: detection mechanism, recovery action, and time-to-recovery target.
- Build a single `repair_workflow()` operation from day one that diagnoses and fixes all known inconsistencies.
- Expose repair via CLI, API, dashboard, and MCP from the start.

---

## 5. Heartbeat-Aware Timeouts

**Requirement**: All timeout mechanisms must respect handler heartbeats.

**What happened**: The runner's execution timeout killed tasks after 900s based solely on submission time, ignoring active heartbeats. Long-running PostGIS imports (hours) were repeatedly killed and restarted despite actively reporting progress.

**Upfront requirement**:
- **Timeout = time since last activity**, not time since start.
- "Activity" includes: task heartbeat, step log emission, progress callbacks.
- Three timeout layers, each heartbeat-aware:
  1. **Lease timeout** (5min default) — task ownership; renewed by heartbeat.
  2. **Execution timeout** (15min default) — thread pool capacity; reset by heartbeat.
  3. **Stuck task timeout** (30min default) — cross-runner watchdog; checks heartbeat.
- Handlers must be given a heartbeat callback and documentation on when to call it.

---

## 6. Multi-Server Networking from Day One

**Requirement**: All services must bind to `0.0.0.0` by default and document hostname resolution for multi-server deployments.

**What happened**: MongoDB startup script hardcoded `--bind_ip 127.0.0.1`. Remote runners got `Connection refused`. PostgreSQL had similar issues with `listen_addresses` and `pg_hba.conf`. Dozens of workflow steps errored with connection failures before the networking was fixed.

**Upfront requirement**:
- Default bind address: `0.0.0.0` for all services (MongoDB, PostgreSQL, dashboard, runner health endpoints).
- Document `/etc/hosts` entries needed on each server.
- Provide a `scripts/check-connectivity` script that verifies all services are reachable from the current host.
- Connection errors should be classified as **transient** and automatically retried.

---

## 7. Error Propagation Enforcement

**Requirement**: Handlers must never silently swallow errors. A handler that catches an exception and returns an empty result is worse than one that crashes.

**What happened**: 42 `except` blocks across 12 handlers caught exceptions and returned empty dicts. PostGIS imports "succeeded" with 0 features, causing downstream analysis to produce empty results without any error signal.

**Upfront requirement**:
- Handler templates must re-raise or explicitly fail: no bare `except: return {}`.
- Validation at task completion: if the handler returns a result with all-zero/empty values that match the default schema, emit a warning.
- Provide `fail_step()` as a first-class API that handlers are expected to use.
- Code review checklist item: "Does every except block either re-raise, call fail_step, or log at WARNING?"

---

## 8. Capacity Management

**Requirement**: Thread pool capacity must never depend on downstream operations succeeding.

**What happened**: Handler succeeds, `continue_step()` throws, future never completes, active work items counter never decrements, runner permanently at max capacity, never claims new work, never runs the reaper. Complete deadlock.

**Upfront requirement**:
- Capacity release must be in a `finally` block, independent of post-handler processing.
- Pattern: `try { dispatch handler } finally { release capacity } then { continue_step, resume }`.
- The handler result and the workflow state machine advancement are separate concerns.
- Test: kill the database after a handler completes but before resume. Runner must eventually recover capacity.

---

## 9. State Consistency Across Collections

**Requirement**: When multiple MongoDB collections represent related state (steps, tasks, runners, servers), define and enforce cross-collection invariants.

**What happened**:
- Steps marked `Complete` with failed tasks underneath (task failed, but evaluator advanced the step with empty defaults).
- Tasks in `running` state on servers marked `shutdown`.
- Runners marked `completed` with non-terminal steps.
- Tasks with empty `runner_id` after retry, invisible to queries filtered by runner.

**Upfront requirement**:
- Document invariants: "If step.state = Complete, then task.state must be Completed for the corresponding task."
- Build a consistency checker that runs periodically (or on-demand) and reports violations.
- The `repair_workflow()` function should check all cross-collection invariants.
- Query patterns must account for empty foreign keys (e.g. tasks with empty `runner_id` should still appear in workflow views).

---

## 10. Integration Tests Against Real Persistence

**Requirement**: Test full pipelines (compile, evaluate, dispatch, resume, complete) against the real database, not mocks.

**What happened**: Three critical bugs were only exposed by full-pipeline execution:
1. When-block deferred evaluation failed on 303-step workflows.
2. Cross-block step reference resolution failed on multi-namespace workflows.
3. Runner terminal state propagation didn't mark workflows COMPLETED.

Unit tests with MemoryStore missed all three because they test components in isolation.

**Upfront requirement**:
- Integration test suite that runs against MongoDB (use testcontainers or a test database).
- At least one test per workflow example that exercises: compile → create runner → execute → handler dispatch → continue_step → resume → verify completion.
- Test failure scenarios: kill runner mid-execution, restart, verify workflow completes.

---

## 11. Operational Scripts from the Start

**Requirement**: Build operational tooling alongside the runtime, not after incidents.

**What happened**: Scripts were added reactively: `drain-runners` after orphaned tasks, `repair-workflow` after inconsistent state, `postgis-vacuum` after slow queries, `list-runners` after fleet visibility needs.

**Upfront requirement**:
- Ship with these scripts from v1:
  - `check-health` — verify all services reachable
  - `db-stats` — document counts and state distributions
  - `list-runners` — fleet status with handler counts
  - `drain-runners` — graceful shutdown with task reset
  - `repair-workflow` — diagnose and fix stuck workflows
  - `list-tasks` — tasks by state with qualified step names

---

## 12. Reserved Protocol Namespace

**Requirement**: Internal protocol tasks must use a reserved prefix that user code cannot collide with.

**What happened**: The `afl:` prefix was used for internal tasks (`fw:execute`, `fw:resume`) but this wasn't documented or enforced until late. Task claiming logic had to be updated to handle both exact matches and prefix patterns when workflow names were appended.

**Upfront requirement**:
- Reserve `afl:` prefix for internal protocol tasks. Document this in the spec.
- Validate at task creation: user-created tasks must not start with `afl:`.
- Protocol task format: `afl:<action>:<context>` (e.g. `fw:execute:MyWorkflow`, `fw:resume:ns.Facet`).
- All agent SDKs must use constants from a shared protocol definition.

---

---

## Building a Composable Facet Library for LLM Composition (v0.46, 2026-05-25)

A multi-day session built out roadmap items 2–7 of [`composable-facet-library.md`](composable-facet-library.md) — the Clip / Spatial / Transform / Filter primitives, the Geocoding / Routing / Tiles service families, the discovery layer (`fw_capabilities` + `osm.Vocab`), reuse-first catalog matching (`fw_catalog_match`), and effect/cost annotations. The durable engineering lessons, distinct from the distributed-systems ones above:

### A. Define the shared result schemas before the engine adapters

**Requirement**: When several backends implement the same operation, the *result schema* is the interface; design it first, then the adapters are thin and interchangeable.

**What happened**: Routing was implemented across five engines (OSRM, the public API, Valhalla, GraphHopper, pgRouting). Because they all returned the pre-existing `osm.Routing.Types` schemas (`PointToPointResult`, `MatrixResult`, `IsochroneResult`), a workflow swaps engine by changing one namespace — `osm.Routing.OSRM.Route` → `osm.Routing.Valhalla.Route` — with no other change. Each new engine was a ~200-line adapter (request shaping + response marshalling), not a new contract.

**Upfront requirement**:
- Put the result types in their own namespace/module that all backends `use`; never let an engine's native response shape leak into the facet's return.
- A new backend implements the verbs it natively supports against those shared types; verb coverage may differ per engine (that's honest), but the schemas do not.

### B. External-engine facets degrade gracefully, never hard-fail

**Requirement**: A leaf facet that calls an external engine (HTTP server, DB, subprocess) must fall back to an explicit estimate when the engine is unreachable, and report which path it took.

**What happened**: Every routing/geocoding facet falls back to a great-circle estimate (or empty result) when its server is down, and every result carries a `backend` field (`osrm-local` / `valhalla` / `estimate` / `none`). This kept compositions runnable and the whole library mock-testable without standing up OSRM/Valhalla/pgRouting — only OSRM was ever provisioned live; the rest were proven by mocked-HTTP tests plus the uniform-schema guarantee.

**Upfront requirement**:
- Distinguish *"the engine answered"* from *"I estimated"* in the return (a `backend` field), so downstream and operators know the provenance.
- Never let an unreachable engine fail the step when a coarse answer is acceptable — but make the coarseness visible. (Contrast §7: a *missing input* should still fail explicitly.)

### C. Reuse existing extension points before adding syntax

**Requirement**: Before designing new language/grammar, check whether an existing extension point already carries the new metadata.

**What happened**: Effect/cost annotations (item 7) were assumed to "need net-new FFL effect-annotation syntax." They didn't: the existing `with`-mixin grammar accepts `with Effect(kind = "pure")` / `with Cost(tier = "cheap")` and the validator does not require a mixin target to be a declared facet, so annotation mixins parse and validate with zero compiler change. Cost was further *inferred* from the `with Timeout(minutes = …)` mixins facets already carried (≥30 → expensive) — signal derived from existing annotations rather than demanded anew.

**Upfront requirement**:
- Treat “we need new syntax” as a hypothesis to falsify, not a given. A general extension point (here, mixins as metadata) often already admits the new use.
- Prefer inferring from data already present over requiring every fact to be re-stated.

### D. Parameter type is a good effect classifier — with per-namespace overrides

**Requirement**: When bulk-classifying facets (e.g. effect/cost), a parameter-type heuristic scales, but the same parameter name can mean different things; budget for overrides.

**What happened**: Annotating all 247 facets used the rule *`cache: OSMCache` → external/expensive (scans the PBF); `input_path: String` → pure/cheap (operates on GeoJSON)*. It mis-classified two cases: `FilterByOSMTag`/`FilterByOSMType` take an `input_path` that is a **PBF** path (the ~54-minute full-scan trap), so they are external/expensive, not pure; and the census `*Districts` facets *return* `TIGERCache`, so a substring match on the type name misrouted them. Both were caught by re-running the capability index and reviewing the distribution + spot-checks, then fixed with name-based overrides.

**Upfront requirement**:
- Param-type heuristics are the right default for a bulk pass; pair them with a verification step (re-derive the classification, eyeball the distribution and the small/odd buckets) and a small set of explicit overrides.
- A parameter *name* (`input_path`) is not a type — the same name spanned GeoJSON (cheap) and PBF (expensive).

### E. Untested code harbors latent bugs that a new consumer surfaces

**Requirement**: Code with no tests should be assumed buggy until exercised; building a new caller (or a live run) is when those bugs appear — leave room for it.

**What happened**: Two latent bugs surfaced only when newer work exercised older code. (1) The pre-existing `pbf_clip` tool (no tests) failed the first live Clip run: `osmium extract` could not infer the output format from the `.staging` staging filename — fixed with an explicit `--output-format pbf`. (2) The capability indexer read facet mixins from `m["name"]`, but the emitter writes the key as `m["target"]`, so `FacetCapability.mixins` had been silently empty since it shipped — surfaced only when item 7 needed to read mixin args, and it passed every prior test because none asserted on that field.

**Upfront requirement**:
- When building on untested tool code, expect to be its first real test; verify end-to-end (a live run), don't trust that "it's been there a while."
- Verify field access against the *actual emitted shape*, not an assumed key. A field that is "always empty" passes any test that doesn't assert it is populated — add the assertion.

### F. Assert against derived structure, not hardcoded counts

**Requirement**: Registry/registration tests must compute their expectations from the registry, or they go stale the moment it grows.

**What happened**: A routing test hardcoded `assert poller.register.call_count == 6`. Adding the OSRM Matrix/Nearest/MapMatch/Trip verbs and then four more engines pushed it to 15; the stale assertion broke (and had silently been wrong across several earlier commits because that test was never run in those batches). The fix derives the expected count by summing `len(dispatch)` over the engine dispatch tables, so it can never go stale.

**Upfront requirement**:
- Tests over a growing registry assert *structure* (e.g. "every dispatch entry is registered exactly once"), not a magic number.
- Run the full affected test module before committing, not just the new test file — a passing new suite does not mean an old sibling still passes (see §10).

### G. Re-running by a reused execution id silently no-ops (reinforces §2)

**Requirement**: Re-submitting a workflow must create a fresh execution, or stale terminal steps shadow the new request.

**What happened**: `fw ffl run-workflow --workflow X` mints a *deterministic* execution id per workflow. The hospital-deserts run completed; resubmitting the same workflow with *food-desert* parameters returned instantly with the **stale hospital results**, because the prior execution's steps were already `Complete` under that id and nothing re-ran. The catalog `run` path avoids this (it mints a fresh execution id per run); `run-workflow` did not. Workaround: clear the prior execution's steps/tasks, or use the catalog.

**Upfront requirement**:
- This is §2 (execution isolation) seen from the *re-run* angle: a deterministic execution id is an execution-isolation bug waiting for the second run. Every submission path must mint a fresh execution id.
- Verify a re-run *actually ran* (check a leaf step's params match what you submitted), not merely that it reported success.

### H. Cross-validate a new primitive against an independent existing one

**Requirement**: Validate a new analysis primitive against a *different* primitive that should agree, on real data — agreement to a boundary case is strong evidence and catches systematic errors.

**What happened**: `Buffer` + `SpatialJoin(within)` and the earlier `BeyondDistance` are independent implementations of "near vs far." Buffering the 142 California hospitals by 10 mi and joining the 4,992 populated places found **1,768 within coverage**; the prior `BeyondDistance` run found **3,223 beyond**. `1,768 + 3,223 = 4,991` of 4,992 — the single discrepancy is one place near the 10-mi boundary, classified differently because one method uses point-distance in a reference-centred projection and the other point-in-buffered-circle. Two separate code paths agreeing to one boundary point is far stronger than either passing its own unit tests.

**Upfront requirement**:
- When two primitives compute complementary facts, assert the complement holds on real data; investigate (don't paper over) any gap — here it explained a real projection/discretization difference and confirmed both were correct.

### I. Operational footguns worth knowing (macOS / blocking subprocesses)

- **Port 5000 is taken on macOS.** `osrm-routed` crashed with `Address already in use` on `:5000` — macOS ControlCenter (AirPlay Receiver) listens there. Use a different port (`:5050`) for local engines.
- **Blocking-subprocess facets need a heartbeat pump.** Clip (`osmium extract`) and tile-build (`tippecanoe`) run a multi-minute blocking subprocess that cannot heartbeat from inside; each wraps the call in a daemon thread that pumps the task heartbeat every 30 s, or the 5-minute lease expires mid-build (the §5 lesson, applied to subprocesses rather than scans).
- **The dual-import trap.** A tool reachable both as `_osm_tools.geocode` (via a `sys.path` shim) and as `osm_geocoder.tools._osm_tools.geocode` is *two distinct module objects* with *two distinct exception classes*; a test that `pytest.raises(geocode.GeocodeError)` against the wrong one fails. Reference the module the handler actually imports (through the shim) in tests.

---

## Engine-free approximate routing — `osm.Network` (v0.47, 2026-05-26)

The composable library had full road routing via five external engines, but that path has a hard edge for continental, multi-server runs: the graph **build** is a heavy serial step, the **build-graph → running-engine lifecycle is not wired as facets**, and replicating a multi-GB graph across hosts is real work. `osm.Network` (`BuildNetwork` / `ApproxRoute` / `RouteMatrix` + the `CityRoutesByPopulation` / `RouteFanout` workflows) trades exactness for a tiny network and routes purely in-process. Lessons:

### A. When cross-server sharing is hard, shrink the shared artifact until it is trivial

**Requirement**: Before building distribution machinery (engine replicas, graph servers, transfer protocols), ask whether the shared state can be made small enough that the *existing* cache + a read-once load solves it.

**What happened**: Restricting the routable graph to interstate/freeway LineStrings made it ~MB (13,230 nodes for all of California), versus a full OSRM graph's tens of GB. At that size the graph is just another content-addressed `osm/network/` cache artifact: built once, written to a shared volume, and **read once per runner into an in-process `networkx` graph** (memoized by path + sidecar sha256). The whole "build-graph → running-daemon → replicate across K hosts" lifecycle — the part that didn't distribute — *disappeared*; routing became `effect=pure` and fanned out lock-free with no engine, no `AFL_*_URL`, no replica management. Proven live in Docker (2 runner containers + containerized Mongo + a shared volume distributed a 21-route fan-out 22/20, results identical to a single host).

**Upfront requirement**:
- Treat a large piece of shared mutable state as a design smell in a distributed system. A small, immutable, content-addressed artifact + pure compute is almost always easier to distribute than a stateful server you must replicate.
- The cheapest multi-server proof reuses the runtime you have: `docker compose --scale <runner>=N` + a `foreach` fan-out, then check tasks split across `server_id`s and results match the single-host run. No bespoke test harness.

### B. Result-schema names must be unique across the whole library — and only the full-library compile proves it

**Requirement**: A facet's return schema is part of the global namespace the composer resolves against; a name reused in two namespaces is ambiguous at any cross-namespace reference. Single-file validation will not catch it.

**What happened**: `osm.Network.RouteResult` / `MatrixResult` validated cleanly on their own, and every unit test passed — because the collision with the identically-named `osm.Routing.Types` schemas only surfaces when something references the facet's return *from another namespace*. The first such reference was the `CityRoutesByPopulation` workflow; compiling it against the **full 70-file osm FFL library** raised `Ambiguous schema reference 'MatrixResult'`. Renaming to `ApproxRouteResult` / `RouteMatrixResult` fixed it, and a regression test now compiles the workflow against the whole library and asserts it adds **zero** new validation errors.

**Upfront requirement**:
- Prefix/scope result-schema names so they are unique library-wide (`ApproxRouteResult`, not `RouteResult`), even though the namespace already disambiguates the *facet*.
- Add a test that compiles new cross-namespace workflows against the entire FFL set, diffing error count with/without the new file — single-file `--check` is necessary but not sufficient.

### C. On partial data, the honest answer is "how close did I get" — make it a first-class output

**Requirement**: A primitive that operates on an incomplete model (a freeways-only graph, a region with gaps) should return its best partial result plus a quantified residual, not an error or a silent wrong answer.

**What happened**: `ApproxRoute` always returns the closest reachable on-network point to B with `reached_b` + `gap_to_b_km`. SF→Yosemite reports a route to the central valley + a `143 km` gap; Fresno/Bakersfield (on SR-99, not an interstate) show 65 / 22 km access gaps in the matrix. The caller learns *exactly* how approximate the answer is. Separately, `BuildNetwork`'s sidecar carries `connected_components` / `largest_component_frac` as build-quality signals — 99.4 % in one component (the 7 tiny spurs being ramp-less segments, since `motorway_link` ramps carry no `ref`) confirmed routability *before any route ran*.

**Upfront requirement**:
- Return `(best_partial, residual, reached_flag)`, not just a value — degradation must be visible and quantified (echoes lesson B above, for graph-partition rather than engine-down).
- Put a cheap quality metric *in the artifact* so consumers can gate on it; a connectivity fraction catches a bad extraction/noding far earlier than a wrong route does.

### D. Operational footguns (this project)

- **Writable output base.** Handler tests and any facet calling `get_temp_dir` / `derive_output_path` resolve `FW_OUTPUT_BASE` (default `/Volumes/afl_data/output`). On a host without that mount they fail with `PermissionError`, which looks like a regression but is environmental — set `FW_OUTPUT_BASE` (+ `FW_DATA_ROOT`) to a writable dir. New pure facets should also fall back to the system temp for staging (as `BuildNetwork` does) so they are robust to a missing data mount.
- **`foreach` element field-access form.** Inside `andThen foreach p in $.xs { … }`, the documented form accesses the loop element as `$.p` and its fields as `$.p.field` (a `Json` element's fields are permissive). The whole element can also be passed as `p.value`. Match the namespace's existing usage.

---

## Silent failures at the handler-registry seam (v0.47.x, 2026-05-28)

Two startup gotchas burned ~50 minutes of a North-America tiled render this session. The workflow ran cleanly through `MergeLayers → BuildNetwork (1.19M nodes / 1.29M edges) → RouteLayer → BuildVectorTiles×2`, then `osm.viz.RenderTiledMap` sat at `state.EventTransmit` indefinitely while the runner idled at 0.6 % CPU. Diagnosis took longer than the actual fix. Both root causes are fundamentally **a fresh fact lives in source code but a stale fact lives in MongoDB, and the runtime trusts the stale one without comparing**.

### A. The `handler_registrations` collection is a write-once snapshot, not a source-of-truth mirror

**Requirement**: When the registry-mode dispatcher loads facets from MongoDB at startup, it must compare what it loaded against what the in-process example packages would have declared, and warn loudly if anything is missing. Otherwise a facet added in code but never re-seeded becomes a permanently-pending task with no claimer.

**What happened**: Commit `148bdc1` added `osm.viz.RenderTiledMap` to `VISUALIZATION_FACETS` in `fwh_osm`. The pre-existing runner (started before that commit) had registered the *5* old viz facets into `handler_registrations` at its launch. No one re-ran `python -m facetwork.examples osm-geocoder` after the commit, so the registry still held 5. The runner was restarted later but with a hand-crafted command (`python -m facetwork.runtime.runner --registry`) that *reads* the registry but does not re-publish — so it loaded the same stale 5. The dashboard step page showed `active = ['RenderTiledMap']` because the workflow had emitted the task; `claim_task()` is name-filtered server-side and no runner advertised the new facet name, so the task sat `pending` with `server_id=None` while every other prerequisite step was processed by the same runner.

**Upfront requirement**:
- **Compare in-process declarations against registry-loaded set at runner startup.** A duck-typed `_FacetNameCollector` (`register_handler(name, **_)` → set add) lets each example's `register_handlers(runner)` be run as a dry pass that yields the source-of-truth facet set without touching MongoDB. Diff against `dispatcher.dispatchable_facets()` and WARN per-package on facets present in code but missing from the registry. Per-package filtering matters: an example with *zero* of its facets in the registry is one the user did not seed (presumably on purpose) and should be quiet — only flag packages that are *partially* present (some facets registered, some not = drift).
- **The diagnostic must run on every `--registry` start, not just `fw runner start --example`.** The trap is that a bare `python -m facetwork.runtime.runner --registry` (used for hand-restarts, profilers, debugging) bypasses the seed step entirely. The drift check belongs in the runner itself, after `preload(verify=True)`.
- **The warning message must name the package, the missing facets, and the fix.** "Registry drift" alone is unactionable. The shipped form is:
  ```
  Registry drift: 1 facet(s) declared in installed example code but NOT in
  handler_registrations — tasks for these will sit pending with server_id=None.
  Re-seed with `python -m facetwork.examples <name>` (or `fw runner start
  --example <name>`). osm-geocoder: osm.viz.RenderTiledMap
  ```

**Implementation**: `facetwork/runtime/runner/__main__.py::_warn_registry_drift` (called after `RegistryDispatcher.preload(verify=True)`). Best-effort: any discovery/import error is debug-logged and swallowed so the diagnostic never blocks startup. Verified in-process against the exact gotcha — the simulation logs the WARNING naming `osm.viz.RenderTiledMap` against the `osm-geocoder` package.

### B. `FW_OUTPUT_BASE` is configurable per-shell, but the runner is silent about which value it inherited

**Requirement**: The runner banner must echo the effective `FW_OUTPUT_BASE` at startup so a silent fall-back to the `.env` default (or worse, the built-in `/Volumes/afl_data/output`) is visible *before* outputs land in the wrong place.

**What happened**: For ongoing OSM work, outputs are expected under `~/osm-route-cache/output/`. The user achieved this by exporting `FW_OUTPUT_BASE` in their interactive shell before launching the long-running runner. When I restarted the runner under `nohup` from a different subshell that hadn't re-exported it, `_env.sh` loaded the `.env` default (`/tmp/output`) and the restart proceeded with no message. The render's `RenderTiledMap` step succeeded but emitted its viewer to `/tmp/output/maps/tiled/<stem>/`, breaking the dashboard's "open in browser" link and leaving the index.html one reboot away from being wiped. The same pattern bit the original osm.Network proof — captured at the time as the §D footgun above — and is the longest-standing operational trap in the project.

**Upfront requirement**:
- **Print the effective value on the banner**, tagged with its source. The shipped form distinguishes `(env)` from `(default in .env)`:
  ```
  FW_OUTPUT_BASE: /Users/ralph_lemke/osm-route-cache/output  (env)
  FW_OUTPUT_BASE: /tmp/output  (default in .env)
  ```
- **Probe writability on the spot.** `mkdir -p` + `-w` test; a non-writable target emits a STDERR warning. Catches the original osm.Network case where `/Volumes/afl_data/output` doesn't exist on the host.
- The fix lives in `fw runner start` because the env resolution lives there (the runner only sees the env it was launched with). Hand-launched runners still get the silent fall-back; the documented happy path is `fw runner start --example <name>` with `FW_OUTPUT_BASE` either in `.env` or exported before the call.

### Why this section, not just code comments

Both fixes are diagnostic safety nets, not behavior changes. Their existence depends on future contributors understanding *why* they fire — otherwise the first time the drift warning fires for a real seeding mistake (or the first time `(default in .env)` shows up on a server that expected something else) someone will silence it without grasping the trap it's catching. The pattern generalizes: anywhere the runtime caches a fact that the example code can change unilaterally (registrations, env-derived paths, FFL schemas), the runtime must compare and warn — silently trusting the cached copy is the failure mode.

---

## Fleet uniformity and storage-volume failure modes (v0.48, 2026-06-01)

Surfaced building the cities-by-zoom + per-band-routes maps and running them across a scaled (1→8 replica) OSM runner fleet. Three failure modes, each of which *looked* like a workflow bug but was infrastructure.

### A. A scaled fleet is only as capable as its image — never hand-install into one container

`tippecanoe` (builds `.mbtiles`) and the `pmtiles` CLI (`.mbtiles`→`.pmtiles`) had each been installed by hand into one long-lived runner container. Scaling the service to 8 replicas added 7 that lacked them, so tile-build tasks **dead-lettered** where tippecanoe was missing, and **silently fell back to `.mbtiles`** where pmtiles was missing — and the browser PMTiles client cannot read `.mbtiles`, so route/line layers vanished while cities (whose `.pmtiles` were cached from an earlier build) still rendered. Any binary a handler shells out to must be in the **image**, with a build-time `--version` smoke check so a missing tool fails the build, not a production task. "Cattle, not pets" is not aesthetic here — a heterogeneous fleet makes task success depend on *which replica claimed it*, the hardest class of bug to reproduce.

### B. Long-running extractors must opt out of the default handler timeout

`osm.Source.PBF.Extract*` and the network primitives (`BuildNetwork`/`RouteLayer`/`RouteMatrix`) registered with the default 30 s handler timeout. Fine for a state-sized region; over a continental (~19 GB) PBF they blew past 30 s, timed out, retried, and **dead-lettered with no error recorded** — indistinguishable at a glance from a "no handler" release. Any handler that does a full-PBF osmium scan or a whole-graph traversal must register `timeout_ms=0` (fall back to the global execution timeout), exactly as `AllPopulatedPlaces` already did. The tell: dead-letters whose `timeout_ms` is the default and whose error is empty.

### C. An ejected external volume leaves a stale Docker mount that reads but cannot write

The cache + output volume (`/Volumes/afl_data`, bind-mounted into every container) ejected when the host slept overnight. Containers kept a **stale mount handle**: cache *reads* still returned bytes (Downloads "completed", cache hits succeeded), but every *write* failed with `[Errno 1] Operation not permitted` / `Not a directory`, dead-lettering tasks — while the host `diskutil list` showed the volume simply gone. A half-alive mount is worse than a cleanly-absent one because the read path masks the failure until the first write. Recovery is not graceful: reconnect the drive, then `docker compose down && up -d` (a stale handle does not self-heal). Corollary: serve user-facing artifacts from an isolated **local** copy, not directly off the shared volume — maps served off `/Volumes/afl_data` 404'd on eject; copies under `~/osm-route-cache` survived.

### Why this section

All three cost real debugging time because the symptom (dead-lettered tasks, a map with dots but no lines) pointed at the workflow, while the cause was the fleet image or the host's disk. When a task fails *non-deterministically across replicas* or *only at continental scale* or *only on writes*, suspect the substrate before the FFL.

---

## Large-foreach livelock: the continuation storm and a self-starving sweep (v0.48.x, 2026-06-09)

Surfaced converting all 255 cached OSM regions to GeoJSON in one run — a workflow (`osm.convert.ConvertAllRegionsToGeoJson`: `ListCachedRegions → foreach → Download → ToGeoJson`) whose `foreach` fans out to 255 sub-blocks, each running a multi-minute event facet (`osm.Source.PBF.ToGeoJson`). The runner **livelocked**: it spun on `resume_step` (logging `resume_step done iterations=0` hundreds of times/sec) and stopped claiming the pending event tasks, so only a handful of sub-blocks ever ran and the output count froze. Two compounding causes, both in the distributed step-processing layer (§10.3.1 / runtime-impl §17.5).

### A. A per-block continuation must be coalesced, not re-enqueued per child — at generation *and* at claim

**Requirement**: When N children of a block each notify the parent that they progressed, the runtime must collapse those N notifications into one re-evaluation. Enqueuing one continuation per child is O(N²) and floods the worker pool.

**What happened**: A foreach child progressing marks the parent foreach block dirty and enqueues a `_fw_continue` continuation targeting that parent. Continuations had a random uuid, were deduped only *within a single generate call*, and were marked COMPLETED (not deleted) after processing — so every one of the 255 children re-enqueued *another* continuation for the same parent block. The resulting O(N²) duplicate continuations saturated the runner thread pool and starved actual osmium execution: the runner was busy processing no-op re-evaluations of the same block instead of running handlers. The fix has two halves, by design: **generation-time dedup** — `process_single_step()` filters `remaining_dirty` against `Persistence.get_pending_continuation_step_ids(workflow_id)` so at most one PENDING continuation per block is ever enqueued; and **claim-time coalescing** — `_process_continuation()` calls `Persistence.delete_pending_continuations_for_step(step_id, except_task_id=…)` the moment it begins, deleting the redundant siblings (that single re-evaluation already reflects all children's current state) rather than letting each be claimed and processed to a no-op. The claim-time half closes the generation-time race (two enqueued concurrently → whichever is claimed first collapses the rest). Both check **PENDING state only**, so a child event arriving *after* a continuation is claimed still generates a fresh one — coalescing the redundant, never dropping the genuinely-new.

**Upfront requirement**:
- A notification that says "a child of block B changed" is *idempotent in B* — one re-evaluation of B sees all of B's children. Treat the pending continuation for a block as a **set membership** (one in flight), not a per-event message; enforce it both where they are created and where they are claimed.
- Coalesce at claim time as well as at generation: dedup-on-enqueue alone has a race (two enqueued before either is claimed). Deleting siblings on claim is cheap and makes the bound hold under concurrency.
- The discriminator is state: only collapse PENDING siblings. A continuation created *after* processing started reflects a newer child event and must survive, or you lose completions.

### B. A safety-net sweep that runs on the poll thread must yield to event claiming

**Requirement**: A periodic stuck-step sweep is a *safety net*; it must never run ahead of, or starve, the normal event-claiming path it shares a thread with.

**What happened**: `_maybe_sweep_stuck_steps()` ran every 5s and processed *every* stuck step synchronously on the poll thread via `process_single_step()`. On a 255-way fan-out the sweep ran longer than the 5s poll interval, so `_poll_cycle()` (which claims the event tasks) rarely got to run — the very steps the sweep tried to unstick never had their handler dispatched, so the next sweep re-found the same steps: a self-perpetuating livelock (runners busy sweeping, ~0 events claimed). The fix bounds the sweep (≤25 steps / ≤1.5s per invocation, remainder deferred to the next cycle) and **skips it entirely when all worker slots are busy** (`_active_count() >= max_concurrent`) — when there is real work in flight, claiming and running it always wins over sweeping for stuck ones.

**Upfront requirement**:
- Any recovery loop that shares a thread with the hot path must be **bounded** (cap count *and* wall-clock per invocation) and **preemptible by load** (skip when at capacity). A safety net that can consume the whole poll budget stops being a net and becomes the failure.
- This is §8 (capacity) seen from the scheduler side: don't just protect capacity *release*, protect the poll thread's *time* — background maintenance must defer to claiming when the system is busy.

Verified live: the `resume_step` storm dropped from hundreds/sec to ~0 and the bulk conversion resumed finalizing to MinIO steadily; full runtime+lifecycle suite green (1097 passed).

### C. Memory-heavy native-tool conversions need a disk-backed index and external staging

The facet that surfaced the livelock — `osm.Source.PBF.ToGeoJson` (full-region PBF→GeoJSON via `osmium export`, object-store-native) — carried its own operational lessons, independent of the runtime fix:

- **Disk-backed node index, not in-RAM.** osmium's default `flex_mem` node-location index grows unbounded and **OOM-kills** on large regions (a continent needs GBs just for the index) on a memory-constrained VM. Use a disk-backed index on local scratch (`osmium export -i sparse_file_array,<scratch>`, overridable via `FW_OSMIUM_INDEX_TYPE` — e.g. `flex_mem` for small regions where RAM is fine and speed matters).
- **Stage on the external/scratch disk, never the internal Docker disk.** The GeoJSON staging output *and* the `localize()` warm cache for the input PBF must sit on the external/scratch disk (`FW_OUTPUT_BASE` / `FW_LOCAL_SCRATCH`). On the internal Docker disk they share space with mongodb, and an ENOSPC there crashes mongo — turning a disk-full into a cluster outage. (Echoes the §A/§B storage-volume lessons of the v0.48 section: keep heavy I/O off the substrate the control plane lives on.)
- **Storage must be symmetric — `localize()` is the read-side counterpart of `finalize_from_local()`.** A native tool (osmium) only reads local files, so an object-store (S3/MinIO) input must be downloaded locally first. `Storage.localize()` (read side) mirrors `finalize_from_local()` (write side); a backend that has one without the other can write to the object store but can't feed the next native step its input.
- **Concurrency must match BOTH RAM and disk, not just core count.** Each disk-backed index is multi-GB, so too many concurrent osmium exports blow past the page cache and seek-thrash the single external disk (slower than serial); too few worker threads/runner starve task execution because orchestration shares the pool. Tune concurrency against the binding resource (disk bandwidth + page cache for these conversions), not nominal CPU parallelism.

---

## Converting at the edge of a host's memory — a size gate beats endless tuning (v0.47.x, 2026-06-10)

Surfaced finishing the all-255-region bulk conversion (`osm.convert.ConvertAllRegionsToGeoJson`) on a 32GB Mac under Docker Desktop, *after* the livelock fix above made the run actually progress. The remaining problem wasn't the runtime — it was that whole-region `osmium export` simply does not fit on this host for the largest extracts, and the tuning knobs that help one constraint hurt another. The lesson: when a workload has a hard memory floor above the host's ceiling, **skip the work you can't do** rather than tuning forever to make it fit.

### A. Whole-region export has a memory floor set by the node-location index

`osmium export` must hold a node-location index for the whole region. The disk-backed `sparse_file_array` index (from the §C lesson above) keeps it off the heap, but the OS still has to page it: for a multi-GB index on a small VM it spills the page cache and the external disk **seek-thrashes to ~KB/s** — the export technically runs but never finishes. Empirically on this host the largest region that converted cleanly was ~0.68GB PBF; regions ≥~1.4GB (china 1.4GB, india 1.6GB, japan 2.3GB … up to us 10GB, africa 7.4GB) thrash. That floor is a property of the data and the host, not a misconfiguration to tune away.

### B. The tuning constraints conflict on a marginal host — you cannot satisfy all four

Four levers, and moving any one to help its constraint hurts another:
- **A LARGE Docker VM** (e.g. 20GB on a 32GB host) gives page-cache room for the big indexes — but drives macOS into heavy memory compression and **crashes Docker Desktop**, and can wedge the VM's linuxkit network.
- **A SMALL VM** (14GB) keeps Docker stable — but the big regions' indexes spill cache and thrash (constraint A).
- **High osmium concurrency** finishes the small regions faster — but OOM-kills the large ones (osmium way-assembly needs ~3-8GB/region).
- **Too-low per-runner thread concurrency** avoids OOM — but starves task execution, because workflow orchestration shares the same worker pool as the exports.

There is no setting that converts every region on this host; the feasible region of the four-way tradeoff excludes the largest extracts entirely.

### C. A wedged Docker-Desktop VM network needs a FULL teardown, not a reopen

When the large-VM attempt crashed Docker Desktop, the VM's linuxkit network wedged: the host log (`~/Library/Containers/com.docker.docker/Data/log/host/*.log`) showed `no route to host 192.168.65.7:2376` and `tx dropped packets`. A simple quit-and-reopen of Docker Desktop did **not** clear it. Recovery required a full process teardown: quit Docker, `pkill com.docker`, `pkill com.docker.backend`, wait ~20s for the processes to actually exit, then `open -a Docker`. Worth knowing before you burn time reopening a half-dead daemon. (The VM `MemoryMiB` itself lives in `~/Library/Group Containers/group.com.docker/settings-store.json` — host config, not a repo file.)

### D. Resolution: a size gate makes the infeasible explicit instead of failing it slowly

The pragmatic fix is the `max_pbf_mb` parameter on `osm.Source.PBF.ToGeoJson` (with the `FW_OSM_MAX_PBF_MB` env fallback for capping a run already in flight): it checks the cached PBF's `size` **before** localizing or converting and returns `skipped = true` with an empty path for anything over the limit. Paired with a host-sustainable VM (14GB on 32GB) and `FW_MAX_CONCURRENT=2` (~2 concurrent exports at ~5-6GB each so the mid-size regions fit), the bulk run reaches its **achievable ceiling — ~226/255** here (138 already done + ~88 sub-1GB mediums) — and *cleanly skips* the ~25 regions ≥1GB rather than thrashing or OOM-killing on them. Those need a larger host (more RAM for a bigger VM) to convert; the size gate makes that boundary a deliberate, observable `skipped` result instead of a slow failure. The general principle: when part of a batch exceeds the host's hard limits, encode the limit as a first-class skip so the run completes deterministically and the deferred work is visible, rather than tuning the whole batch down to the lowest common denominator or letting the giants fail late.

---

## Future Requirements: From Distributed Systems Literature

The following requirements are drawn from *Designing Data-Intensive Applications* (Kleppmann), *Release It!* (Nygard), Temporal's durable execution model, and the Recovery Oriented Computing research (Patterson et al.). These represent gaps not yet addressed in Facetwork.

### 13. Dead Letter Queue and Poison Pill Detection

Tasks that fail repeatedly cycle forever: claim, fail, reap, claim, fail. A task that crashes every runner it touches keeps getting reclaimed in an infinite loop.

**Requirement**:
- Track `retry_count` on each task. After `max_retries` (default 5), move to a dead letter collection instead of resetting to pending.
- Exponential backoff: `next_retry_after = now + min(base_delay * 2^retry_count, max_delay)`.
- Dashboard DLQ page with "re-enqueue" and "discard" actions.
- `claim_task` skips tasks where `next_retry_after > now`.

**Where**: `TaskDefinition` fields, `claim_task()` filter, reaper/watchdog, dashboard DLQ tab.

**Effort**: Medium. **Priority**: Critical.

### 14. Cascading Failure Protection (Circuit Breaker)

When a downstream service (PostGIS, external API) goes down, all handlers for that service fail simultaneously, flooding the retry queue with identical failures.

**Requirement**:
- Per-handler-name circuit breaker with three states: CLOSED (normal), OPEN (failing, stop claiming), HALF_OPEN (allow one probe task after cooldown).
- Configurable thresholds: `failure_threshold=5` consecutive failures to open, `cooldown_ms=60000` before half-open, `success_threshold=2` to close.
- When OPEN, exclude that handler from task claiming.
- Expose breaker state on the dashboard server detail page.

**Where**: New `circuit_breaker.py` module, runner poll cycle, dashboard server view.

**Effort**: Medium. **Priority**: Critical.

### 15. Bulkheads (Thread Pool Isolation)

A shared thread pool means one slow handler type (PostGIS imports taking hours) starves fast handlers (route statistics taking seconds).

**Requirement**:
- Named thread pools with glob patterns matching handler names: `{"slow": {"patterns": ["*PostGis*"], "max_concurrent": 2}, "default": {"max_concurrent": 4}}`.
- Each pool has independent capacity tracking and cleanup.
- Task routing matches `task.name` against pool patterns; unmatched tasks go to "default".

**Where**: Runner `_executor` → `_executors` dict, `_poll_cycle` per-pool capacity, config.

**Effort**: Medium-Large. **Priority**: High.

### 16. Cancellation Propagation

Cancelling a workflow sets the runner state but doesn't reach in-flight handlers. Long-running imports continue consuming resources.

**Requirement**:
- Inject a `CancellationToken` into handler payloads (alongside `_task_heartbeat`). Handlers check `token.is_cancelled` periodically.
- Poll loop checks runner states each cycle. When cancelled: set token, cancel pending tasks in DB, cancel futures (best-effort).
- Non-cooperative handlers are killed by the execution timeout.

**Where**: Runner poll loop, `_process_event_task` payload injection, new `CancellationToken` class.

**Effort**: Medium. **Priority**: High.

### 17. Compensating Actions (Partial Rollback)

When a handler partially completes then fails (e.g. 10 of 50 tables imported into PostGIS), incomplete data stays. No cleanup mechanism exists.

**Requirement**:
- Handlers write `compensation_data` incrementally (e.g. list of imported tables) to the task data.
- A `CompensationRegistry` maps handler names to cleanup functions.
- When a catch block executes, the framework invokes the registered compensation.
- FFL support: `with Compensate(handler = "RollbackImport")` mixin syntax.

**Where**: Evaluator catch path (already exists), new compensation registry, handler convention.

**Effort**: Large. **Priority**: Low (complex, handler-specific).

### 18. Steady-State and Data Lifecycle

`step_logs`, completed tasks, finished workflow steps all grow unbounded. No TTL, rotation, or archival.

**Requirement**:
- MongoDB TTL indexes: `step_logs.time` (30 days default), completed runners/steps/tasks (90 days).
- Archive job that moves completed workflows older than retention period to `{collection}_archive`.
- Configuration: `FW_LOG_RETENTION_DAYS`, `FW_ARCHIVE_RETENTION_DAYS`.
- Dashboard "Storage" page showing collection sizes and last purge time.

**Where**: `_ensure_indexes()` TTL indexes, new `scripts/data-lifecycle` script, config.

**Effort**: Small-Medium. **Priority**: High.

### 19. Rate Limiting and Admission Control

No limit on workflow submissions. A flood of "Run" clicks or API calls can overwhelm the runner fleet.

**Requirement**:
- Queue depth limit: reject when `count(pending tasks) > MAX_PENDING_TASKS` (default 100).
- Per-workflow-type concurrency limit: at most N instances of the same workflow name.
- Return HTTP 429 with retry-after from dashboard, error from MCP tool.

**Where**: Dashboard `flow_run_execute`, MCP `afl_execute_workflow`, new admission controller.

**Effort**: Small. **Priority**: Medium.

### 20. Schema Evolution

Changing a facet's parameter schema while workflows are running leaves old tasks with old parameter shapes.

**Requirement**:
- Already partially solved: `RunnerDefinition` snapshots `compiled_ast` and `workflow_ast`, so running workflows use the schema they were compiled with.
- Add `schema_version` to `HandlerRegistration`. Warn (not error) on mismatch.
- Document the convention: handlers should accept both old and new parameter shapes during migration.

**Where**: `HandlerRegistration` version field, dispatcher version matching.

**Effort**: Small. **Priority**: Low (mostly solved by AST snapshotting).

### 21. Workflow Versioning

Deploying a new handler version while workflows are running the old version.

**Requirement**:
- Tasks carry `handler_version` from compile time. Multiple handler registrations per facet (one per version).
- `claim_task` matches both `name` and `handler_version`. Empty version matches any (backwards compatible).
- Blue-green deployment: register new version, old tasks drain with old handlers, new submissions use new version.

**Where**: `HandlerRegistration` unique index `(facet_name, version)`, task creation, claim_task.

**Effort**: Medium. **Priority**: Low.

### 22. Visibility Queries (Parameter Search)

Dashboard searches by workflow name and state but cannot search by input parameters (e.g. "find all Texas imports").

**Requirement**:
- MongoDB text index on `runners.parameters` for parameter value search.
- API: `GET /api/runners?param_name=region&param_value=Texas`.
- Dashboard: parameter search filters on the workflow list page.

**Where**: MongoStore index, search method, dashboard workflows list.

**Effort**: Small. **Priority**: Medium.

### 23. Distributed Catch Execution — the 2026-07 Verification Campaign

The FFL `catch` clause passed every in-process test yet had **never worked on
the fleet**. Verifying one workflow feature live (the osm.emergency region
catch) took four fix-and-rerun rounds and surfaced five independent runtime
bugs, every one invisible to MemoryStore-backed tests:

1. **Pre-execute advance skipped CatchBeginHandler** — the changer loop
   advances the state *before* executing when a change is requested, so
   requesting a transition right after `change_state(CATCH_BEGIN)` jumped
   straight to CATCH_CONTINUE and the AND_CATCH sub-block was never created
   (6750740).
2. **CatchContinue counted the errored body as its own sub-block** — a
   declaration-level catch fires *because* a sibling andThen block errored;
   counting it made every such catch report itself failed (6750740).
3. **`$.error` pseudo-returns weren't persisted before the sub-block ran** —
   the catch block resolves `$.error` against the *persisted* container step,
   possibly on another runner, before the batched update commits (d9225fa).
4. **Container-return `$`-refs resolved by statement *name*** — under a
   foreach every iteration has a same-named step, so the lookup found a
   completed *sibling* iteration's step; frames now carry the container's
   step id (37b3083). The same commit made AND_CATCH body ASTs re-derivable
   cross-runner (they existed only in the creating runner's memory — another
   runner re-executed the whole errored body inside the catch block).
5. **`get_blocks_by_step` never returned AndCatch blocks** — its hardcoded
   `object_type` filter predated the catch feature, so CatchContinue could
   not see a completed catch block on MongoDB at all (9409256). Combined
   with the wait-for-sub-block guard (21208c0) this parked steps forever.

**The lessons**:

- **MemoryStore parity gaps hide distributed bugs by construction.** It
  aliases live objects (mutations are visible before any "commit") and
  indexes blocks by `is_block` with no type filter (Mongo filters by a
  hardcoded type list). Three of the five bugs were exactly these two
  differences. When a store method has semantics ("returns blocks"), the
  memory and Mongo implementations must be behavior-tested against the SAME
  assertions, and Mongo-level regression tests are mandatory for anything a
  Continue handler polls.
- **A feature is not done until it has run distributed.** In-process
  execution collapses claim races, commit visibility, AST cache locality,
  and instance ambiguity into a single address space. The catch clause was
  "fully tested" and completely broken.
- **Verification runs are cheap bug finders.** Each of the four live rounds
  cost ~1h of cached re-execution and pinpointed its bug precisely from the
  step/task state it left behind.

**Status**: all five fixed with regression tests; verified end-to-end
2026-07-20 (25-region atlas: failed region degrades to a disclosed excluded
row, run completes).

### 24. Empty Results Are Data, Not Errors — Classify at the Source

`ApproxRoute` raised on an empty road network. For Haiti — which genuinely
has no motorway..secondary tier in OSM — that raise dead-lettered 150 route
tasks (×5 retries each), hard-failed the whole atlas through the fan-in, and
conflated two situations only the *producer* can distinguish: "the world is
empty here" (valid, measurable) vs "the pipeline broke" (must fail loud).

**Requirement**: classify emptiness where the source data is visible.
`BuildNetwork` publishes a validly-built 0-node artifact (world-fact,
sidecar-validated); `ApproxRoute` over it returns a *valid unreachable*
result; corruption (missing/invalid artifact) still fails loudly in the
loader. Downstream metrics disclose `network_unroutable` instead of erroring.
The region-level catch remains as the backstop for genuine failures.

**Corollary — retries need an escape hatch**: deterministic failures ride the
full retry budget today (there is no non-retryable error contract in the
handler SDK). A `PermanentError` that dead-letters immediately is still open
work — the routable-flag fix removed this instance's need for it.

---

## Implementation Roadmap

| Phase | Items | Duration | Focus |
|-------|-------|----------|-------|
| **Phase 1** | #13 Dead letter queue + #14 Circuit breaker | 1 week | Stop infinite retry loops and cascade floods |
| **Phase 2** | #15 Bulkheads + #16 Cancellation | 1 week | Resource control and isolation |
| **Phase 3** | #18 Data lifecycle + #19 Rate limiting + #22 Visibility queries | 3-4 days | Operational hygiene and quick wins |
| **Phase 4** | #20 Schema evolution + #21 Workflow versioning + #17 Compensating actions | 1 week | Deployment maturity |

---

## Summary: The Five Things That Matter Most

If you can only do five things upfront:

1. **Design recovery before the happy path.** Every state transition needs a "what if this crashes halfway?" answer.
2. **Make everything identifiable.** Qualified names in every log, every dashboard view, every error message.
3. **Test against real infrastructure.** Mocks hide the bugs that matter.
4. **Isolate execution runs.** Shared state between runs is a bug factory.
5. **Build operational tools alongside the product.** If operators can't diagnose and fix issues without a database CLI, the system isn't production-ready.
