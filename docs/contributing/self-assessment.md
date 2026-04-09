# Facetwork Self-Assessment

A self-assessment across all fundamental areas, drawing on the full arc from v0.11 through v0.33.0.

---

## Grammar / Parser — B+

The Lark LALR grammar is compact at 160 lines and handles a complex language. Priority tokens (`CATCH_KW.2`, `NULL.2`, `BOOLEAN.2`, `TYPE_BUILTIN.2`, `COMP_OP.2`, `FLOAT.2`, `DOC_COMMENT.3`) are standard LALR practice for keyword/identifier disambiguation — they're necessary, not workarounds. The preprocessor for brace-delimited scripts (`preprocess.py`) is the right approach for embedding Python inside an LALR-parsed language. The rigid same-line constraints (no newlines between `)` and `andThen`, `=>`, `with`, `catch`) are the one real ergonomic limitation — relaxing them would require `_NL*` insertions that risk LALR conflicts.

---

## AST — A-

Clean dataclass design with consistent patterns. New nodes like `CatchClause`, `WhenBlock`, `ScriptBlock` all follow the same shape. The `node_id` UUID on every node was a good early decision. Minor knock: the `field(default=None, kw_only=True)` dance to add optional fields to existing dataclasses (like `catch` on `StepStmt`) is fragile if field ordering changes.

---

## Transformer — A-

Improved through v0.41.0. The v0.30.1 refactoring extracted `_find_one`/`_find_all`/`_find_rest` helpers, consolidated declaration logic into `_build_declaration`, and unified item segregation via `_segregate_declarations`. The v0.41.0 cleanup went further: extracted `_left_assoc_fixed_op` and `_left_assoc_interleaved` helpers to deduplicate 4 binary expression builders, added explicit `CATCH_KW` terminal handler to prevent token-leaking, refactored `facet_sig`/`mixin_call`/`block` to use helpers consistently, and simplified `prompt_block` directive dispatch to dict-based lookup. All methods that previously used manual isinstance loops now use the shared helpers. The remaining risk (Lark's untyped item lists) is well-mitigated by the helper pattern.

---

## Emitter — A

Solid. The `declarations`-only JSON format is clean. `_convert()` dispatch is straightforward. Round-trip testing catches regressions. The `_add_metadata` pattern ensures consistent location data. This is probably the most maintainable layer.

---

## Validator — B+

Good coverage of structural rules (duplicates, reference validity, yield targets, when block constraints). Phase 1 (v0.31.0): `_infer_type` resolves input reference types (`$.param`) from signature parameters via `_param_scope`. Phase 2a (v0.36.0): step return types resolved within andThen blocks via `step_returns_types` built from `FacetInfo.returns_types` and `SchemaInfo.fields_types`. Phase 2b (v0.40.1): step return types threaded into when block conditions and cross-block step visibility — `andThen { s1 = F() } andThen when { case s1.field == "x" }` now resolves types and validates step references. Remaining gap: schema-typed return fields resolve to "Unknown" (not the schema name), and deeply nested field access (`step.field.subfield`) is not supported.

---

## Runtime — A-

The most ambitious and complex area. The iterative state machine with ~25 states, multiple changers, handler dispatch, dependency graphs, block hierarchies, and persistence abstraction is architecturally sound. But it's also where the hardest bugs have lived: step deduplication races (v0.12.72), dirty block tracking (v0.12.70), `resume_step` scaling (v0.12.64), yield deferral timing (v0.12.83), error propagation through block hierarchies (v0.12.75). Each fix was correct but reactive. The catch implementation went smoothly because the pattern was well-established by then — but that pattern itself took many iterations to stabilize.

The v0.32.0+real integration tests exposed and fixed three runtime issues that only manifested in full pipeline execution: (1) when-block deferred evaluation — when blocks referencing steps from prior `andThen` blocks must defer until those steps complete, fixed via `_step_not_ready` flag; (2) cross-block step reference resolution — `get_completed_step_by_name` now falls back to workflow-wide search with direct `statement_name` lookup; (3) runner terminal state propagation — `_resume_workflow` now updates the runner entity to COMPLETED/FAILED when the workflow finishes, so the dashboard reflects final status. These were real bugs that unit tests didn't catch because they span the compile→evaluate→dispatch→resume→completion cycle. The fact that the integration tests found them validates the approach.

The v0.42.0–v0.44.0 releases addressed the critical gap in long-running workflow resilience — making it possible for a 103-task, multi-day Africa-wide OSM import workflow to run to completion despite PostgreSQL restarts, network interruptions, missing dependencies on remote servers, and concurrent extension creation races. The specific mechanisms are documented below; the B+ → A- upgrade reflects the transition from "works when everything goes right" to "recovers and completes when things go wrong."

### Workflow resilience: the problem

Long-running distributed workflows face compounding failure modes that don't appear in short test runs:

1. **Transient infrastructure failures** — database restarts (WAL recovery), network partitions, garbage collection pauses, disk full conditions. These cause handler errors that are not bugs — the operation would succeed if retried.
2. **Stale process state** — a runner started before a dependency was installed (e.g. `psycopg2`) will reject tasks forever because module-level `HAS_PSYCOPG2 = False` is evaluated at import time. Restart is the only fix.
3. **Concurrent resource contention** — multiple runners calling `CREATE EXTENSION IF NOT EXISTS` simultaneously can trigger `UniqueViolation` instead of the expected `DuplicateObject`, crashing the handler despite correct SQL.
4. **Capacity deadlock** — a handler succeeds but `continue_step` skips (step already in error from a prior attempt) → the thread future stays active → `active_work_items` never decrements → the runner thinks it's at capacity → it never claims new work → it never runs the reaper → orphaned tasks are never cleaned up.
5. **Cascading state inconsistency** — a step is manually retried via the dashboard, the handler succeeds, but the step remains in `STATEMENT_ERROR` because `continue_step` treated terminal states as no-ops. Downstream steps never execute. The workflow appears stuck with no errors in the logs.

### Recovery mechanisms (v0.39.0–v0.44.0)

**Layer 1: Orphan reaper (v0.39.0)** — detects dead *servers* and reclaims their tasks.
- Every 60s, each runner queries for servers with stale heartbeats (>5 min) but state still `running`
- Tasks claimed by dead servers are atomically reset to `pending`
- Step log entries record each reaped task for audit visibility
- Safety: shutdown servers (graceful drain) are not reaped; only servers that crashed without deregistering

**Layer 2: Stuck task watchdog (v0.42.0)** — detects tasks stuck on *live* servers.
- Two-pass detection: (a) tasks with explicit `timeout_ms` exceeded, (b) tasks with no progress beyond default threshold (4h)
- Heartbeat-aware: handlers calling `update_task_heartbeat()` keep their tasks alive even if the server heartbeat is stale
- Catches the case where the server is alive and pinging but the handler is blocked (e.g. waiting for a database connection that will never come)
- Configurable via `AFL_STUCK_TIMEOUT_MS` (default: 4 hours)

**Layer 3: Lease-based task ownership (v0.43.0)** — prevents dual-claiming and stale ownership.
- Tasks have a `lease_expires` timestamp set at claim time
- Expired leases allow other runners to reclaim without waiting for the reaper cycle
- Execution timeout (default: 15 min) kills hung futures and releases capacity

**Layer 4: Errored step recovery (v0.44.0)** — breaks the terminal-state deadlock.
- `continue_step()` detects when a step is in `STATEMENT_ERROR` but a result is provided (retry succeeded)
- Resets the step to `EVENT_TRANSMIT`, clears the error, applies the result, and continues normal processing
- Previously this was a silent no-op that left workflows permanently stuck

**Layer 5: Dashboard reaper (v0.44.0)** — independent cleanup when all runners are incapacitated.
- Background asyncio task in the dashboard process runs orphan reaper + stuck watchdog every 60s
- Independent of runners: if every runner is at capacity with stale futures, the dashboard still cleans up
- Breaks the capacity deadlock: dashboard resets orphaned tasks → a runner slot frees up → runner resumes claiming

### What can be improved

1. **Automatic runner restart** — when a handler fails due to stale module state (e.g. missing dependency installed after startup), the runner should detect this pattern and restart itself rather than permanently rejecting tasks.

2. **Task routing / affinity** — tasks should be routable to runners with specific capabilities. Currently all runners register all handlers, so a task requiring PostGIS can be claimed by a runner without `psycopg2`. Capability-based routing would avoid wasted claims.

3. **Progressive backoff on repeated failures** — a task that fails 3 times on the same error should be parked with exponentially increasing retry delays rather than immediately re-queued. Currently it cycles through claim → fail → reap → claim → fail indefinitely.

4. **Distributed reaper consistency** — multiple runners each run their own reaper, potentially resetting the same orphaned tasks simultaneously. This is safe (atomic MongoDB updates) but wasteful. A leader-election or distributed lock for the reaper cycle would reduce redundant work at scale.

5. **Handler health probes** — before claiming a task, the runner should verify that the handler's dependencies are available (database reachable, required modules importable). This would prevent the "claim then immediately fail" pattern.

6. **Workflow-level circuit breaker** — if >50% of a workflow's tasks fail with the same error class, pause the workflow and alert rather than continuing to cycle through failures. This would catch systemic issues (database down, misconfigured credentials) faster.

7. **Step-level retry policy in AFL** — allow workflow authors to specify retry behavior declaratively: `event MyEvent() with Retry(max = 3, backoff = "exponential", on = ["ConnectionError", "TimeoutError"])`. Currently all retry logic is operational (reaper thresholds, manual dashboard retries) rather than part of the workflow definition.

---

## Testing — A

3,483 tests (3,566 collected, 84 skipped) with good discipline: every grammar construct has parser tests, emitter round-trips, validator error cases, and runtime behavior tests. Each example ships with a full test suite (8 classes per example is now standard: utils, per-category handlers, dispatch, compilation, agent integration). The structure is consistent.

The v0.32.0+real integration tests add a new tier: full-pipeline tests that exercise compile→evaluate→dispatch→resume→completion through MemoryStore. 13 tests across 3 classes (TestCompilation, TestAnalyzeSample, TestBatchAnalysis) cover: AFL compilation, workflow extraction, single-sample full pipeline, output verification, step counts, QC fail branching, downloaded reference data, batch with 3/5 samples, mixed QC outcomes, single-sample foreach, and 5-sample batch with real patient IDs. These found 3 runtime bugs that unit tests missed. The A- → A upgrade reflects this: the test suite now covers not just units and components but the full execution pipeline end-to-end, including `andThen when` branching, `catch` error recovery, and `andThen foreach` iteration — validated through the actual evaluator + poller loop, not mocked handlers.

---

## Specs / Documentation — B+

Comprehensive — 11 spec files covering language, semantics, validation, runtime, events, states, agents, examples, acceptance, and ops. They're the source of truth and generally accurate. But they lag behind implementation (I'm always updating them after the code). The runtime spec was split in v0.40.1: `30_runtime.md` (~730 lines) contains the formal specification with catch block semantics (§8.4) and schema instantiation semantics (§8.5) promoted to normative sections; `31_runtime_impl.md` (~620 lines) contains the Python implementation guide with state changers, transition tables, handler code, and source file map.

---

## Examples — A

13 examples forming a genuine progression from simple (`monte-carlo-risk`) to complex (`site-selection-debate`, `devops-deploy`, `hiv-drug-resistance`). Each showcases specific features and has full handler implementations plus tests. The RegistryRunner-first pattern in later examples is a good architectural evolution. The v0.32.0 HIV drug resistance example is the first to combine `andThen when`, `catch`, and `andThen foreach` in a single example — demonstrating that these features compose naturally for real-world bioinformatics pipelines (QC branching, per-sample error recovery, batch processing). The example pattern is now well-templated: 8 test classes, 4 handler categories, shared utils, dual agent entry points.

The HIV example now has real integration tests (`tests/real/`) that run the full pipeline through the evaluator + poller loop, plus verified execution through the dashboard runner service with MongoStore persistence. BatchAnalysis with 3 patient samples completes in ~14 seconds through the dashboard (56 steps, all 9 handlers dispatched per sample). This is the first example validated end-to-end through the production execution path (seed → dashboard submit → runner service → handler dispatch → completion).

---

## UI / Dashboard — B

The dashboard has improved significantly through v0.40.1. The earlier B- reflected two coexisting navigation paradigms, inline JS blobs, duplicate old/v2 pages, and inconsistent auto-refresh. These have been addressed.

### What works

- Server-rendered FastAPI + Jinja2 gets features shipped fast — workflow browser, runner detail, step trees, handler management, output browser all exist and work
- SSE log streaming (v0.27.0), DAG visualization, timeline charts add real operational value
- Cmd+K command palette and sidebar nav (v0.23.0) improved navigation significantly
- Census maps with GeoJSON rendering show it can handle domain-specific visualization
- Python tests cover routes and HTML output reliably
- **Shared components (v0.31.0):** `_state_badge.html`, `_empty_state.html`, `_attrs_table.html` partials replace 30 duplicated patterns across 22 templates
- **ES modules + CSS design tokens (v0.36.0):** frontend modernization step
- **Unified navigation (v0.40.1):** Old runner/handler routes redirect to v2 equivalents; tasks and events pages migrated to v2 subnav pattern with breadcrumbs and tab counts
- **JS extraction (v0.40.1):** Inline JS extracted into shared ES modules (`state_filter.js`, `view_toggle.js`); fixed double-loading of `step_tree.js`
- **HTMX auto-refresh (v0.40.1):** Tasks and events pages now auto-refresh via HTMX partials
- **Search XSS fix (v0.40.1):** Global search results rendered via Jinja2 partial instead of f-string HTML concatenation

### Remaining gaps

- **One-off visualizations.** The DAG visualization and timeline chart are standalone JavaScript implementations, not backed by a charting library with consistent styling
- **No responsive design discipline.** It works on desktop but that's about it
- **Basic forms.** The handler create/edit forms are plain HTML forms without client-side validation or feedback

---

## Infrastructure (MCP, SDKs, CI) — A-

Broad coverage — MCP server, 4 non-Python SDKs, Docker, CI pipeline. But depth varies: the Python SDK is production-quality while the others maintain feature parity through parallel implementation rather than shared abstractions.

The v0.33.0 operations tooling is a meaningful step toward production readiness. Runner HTTP ports are now persisted in MongoDB (`ServerDefinition.http_port`), enabling remote health checks without out-of-band port discovery. The shared `_remote.sh` helpers (SSH wrapper, MongoDB query, state polling) provide a consistent foundation for multi-host management. `stop-runners` and `start-runner` gained `--all`/`--host` remote modes while preserving backward-compatible local behavior. `rolling-deploy` implements zero-downtime serial restart with drain → wait → start → health-check per server, abort-on-failure safety, and configurable timeouts. `list-runners` gives fleet visibility with a tree view (servers → runners → handlers) including uptime, last ping, namespace-grouped handler lists, and handled/skipped stats.

The v0.39.0 orphaned task reaper closes the last major gap in crash recovery. Previously, if a runner crashed (OOM, SIGKILL, network partition) its in-flight tasks were stuck in `running` state forever — requiring manual MongoDB intervention (`db.tasks.updateMany`). Now every runner automatically detects dead peers via stale heartbeats and resets their orphaned tasks to `pending`. The reaper uses the same 5-minute `SERVER_DOWN_TIMEOUT_MS` threshold as the dashboard's `effective_server_state()`, ensuring consistent behavior. `claim_task()` stamps each task with the claiming server's `server_id`, and the reaper only touches servers that crashed without deregistering (not gracefully shut-down ones). The Fleet dashboard page (`/v2/fleet`) provides visual confirmation of server health with per-server task counts by event facet.

The v0.42.0–v0.44.0 releases added three layers of autonomous recovery: (1) a stuck task watchdog that catches tasks blocked on live servers (not just dead ones), (2) a dashboard-hosted reaper that runs independently of runners to break capacity deadlocks, and (3) errored step recovery so retried tasks that succeed actually update the workflow state. These were validated against a real 103-task, multi-day workflow (Africa-wide OSM PostGIS import) that encountered PostgreSQL WAL recovery, network interruptions between machines, missing `psycopg2` on remote runners, concurrent `CREATE EXTENSION` races, and Kerberos/GSSAPI authentication failures. Each failure mode was resolved with a code-level fix and an automatic recovery mechanism so similar failures self-heal. The B+ → A- upgrade reflects the shift from "operational tooling exists" to "operational tooling handles real multi-day, multi-machine production failures autonomously."

---

## Overall — A-

The system is genuinely functional and has grown from a parser experiment to a full compiler + runtime + multi-language platform. The strongest areas are where discipline was highest (emitter, tests, examples). The weakest are where complexity accumulated organically (runtime edge cases, dashboard frontend architecture). The v0.31.0 release addressed the two weakest areas identified in this assessment: the validator now infers parameter types (B- → B), and the dashboard has shared component partials (C+ → B-). The v0.32.0 HIV drug resistance example validates that the three most recent language features (`andThen when`, `catch`, `andThen foreach`) compose cleanly in a realistic domain.

The v0.32.0+real work upgraded two grades: runtime B → B+ (3 cross-cutting bugs fixed by integration tests, full pipeline verified through MemoryStore, MongoStore, and dashboard runner) and testing A- → A (13 integration tests covering the compile→evaluate→dispatch→resume→completion cycle, plus script sandbox hardening with restricted `__import__`). The v0.33.0 work upgraded infrastructure B → B+ with production operations tooling: remote runner management (`--all`/`--host` on start/stop), zero-downtime rolling deploy, fleet inspection (`list-runners`), HTTP port persistence, and shared SSH/MongoDB helpers.

The v0.42.0–v0.44.0 releases represent the transition from "works in controlled conditions" to "recovers from real-world failures." A 103-task Africa-wide OSM import — running across two machines over multiple days — exercised every failure mode: database restarts, network partitions, missing dependencies, concurrent races, stale process state, and capacity deadlocks. Each was resolved with both an immediate fix and an automatic recovery mechanism. The runtime (B+ → A-) and infrastructure (B+ → A-) upgrades reflect this: five layers of autonomous recovery (orphan reaper, stuck watchdog, lease-based ownership, errored step recovery, dashboard reaper) ensure that long-running workflows self-heal from transient and semi-permanent failures without manual intervention. The remaining systemic gaps are: step reference type resolution (validator returns "Unknown" for `step.field`), capability-based task routing (tasks can be claimed by runners that lack the required dependencies), and declarative retry policy (retry behavior is operational, not part of the workflow definition).
