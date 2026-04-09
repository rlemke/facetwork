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
- Configuration: `AFL_LOG_RETENTION_DAYS`, `AFL_ARCHIVE_RETENTION_DAYS`.
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
