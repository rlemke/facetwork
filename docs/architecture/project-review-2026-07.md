# Facetwork Project Review — July 2026

A full-project architecture and code review covering the FFL compiler/language
layer, the runtime engine, the dashboard/MCP/catalog layers, the domain-handler
ecosystem (`fwh_*`), and the operations/fleet layer. Produced by five parallel
review passes, each reading the code directly; every flaw is cited with file
evidence and marked **confirmed** (verified by reading or executing the code)
or **suspected** (reasoned but not executed).

---

## Executive summary

Facetwork is in strong architectural shape for what it is: a leaderless,
Mongo-coordinated distributed workflow platform with a purpose-built DSL and an
unusually deliberate LLM-facing surface. The best ideas are genuinely novel:

- **The rule_id + docs_uri error contract.** Every validator diagnostic carries
  a stable rule id and a fetchable doc with paired wrong/right examples. The
  FFL prior lives in the tool layer, not the prompt — the validate→fetch→fix
  loop is structural, not bolted on.
- **Reuse-first catalog matching** (`CatalogService.match`) — a working
  "memory of solved requests," with authoring summaries as the dominant match
  signal and an explicit reuse/review/author_new verdict.
- **Namespace-derived task routing** — the routing key is intrinsic to the
  facet's qualified name, used identically on the producing and consuming
  sides, so queues can't desync from handlers.
- **Defense-in-depth claiming** — atomic claim + lease + partial unique index
  on `(step_id, state=running)` + fenced terminal task writes.
- **A disciplined ops story** — filesystem-as-registry `fw` CLI, one
  `domains.json` catalog driving install/compose/fleet, an unusually hardened
  fleet-agent daemon where every mitigation cites the incident that motivated it.

The most important problems cluster into four themes:

1. **Secrets and security posture** (act now): decrypted MinIO credentials are
   sitting in un-gitignored `.env.fleet-agent.*` files in the repo root; the
   dashboard is a fully destructive unauthenticated surface whose handler
   registration is effectively fleet-wide RCE on the LAN; the PostGIS
   "read-only" regex guard is bypassable.
2. **At-least-once has drifted toward routinely-twice.** The default lease
   (5 min) is shorter than the default execution timeout (15 min) and lease
   renewal requires opt-in handler heartbeats, so slow handlers are commonly
   reclaimed and double-executed; post-handler step writes are not
   ownership-fenced, so zombie runners can still advance workflow state.
3. **Duplicated implementations disagree.** `RunnerService` vs
   `RegistryRunner` diverge on retry/failure semantics, sweep bounds, and
   resume locking; hand-written `docker-compose.fleet.yml` has drifted from
   the generated compose; `workflow_source.py` is a hand-maintained inverse of
   the grammar; and in the domain ecosystem, `sidecar.py`/`storage.py` exist
   as **14 forked, all-mutually-divergent copies**, plus ≥5 independently
   reimplemented MapLibre choropleth renderers.
4. **The domain contract is eroding at exactly the rate new domains are
   added.** The 7 newest map domains (health, conflict, migration, h1b,
   uspanel, osm-mapping, cancer) skip the tools-pattern contract almost
   entirely (no `tools/` layer, no sidecar cache, ≤1 test file each) — and
   the root cause is that `domain-template/` itself scaffolds a nonconforming
   package.
5. **The authoring loop's biggest validator gap**: calls to known facets are
   not checked against their parameters, unknown unqualified facets pass
   silently, and cross-file duplicate definitions silently last-win — the
   three most common LLM-authoring errors surface only at runtime.

### Immediate actions (this week)

1. **DONE (414213b)** — Gitignore `.env.fleet-agent.*` and `*.pre-fw-bak`; delete
   the stranded files (creds were default `minioadmin` — no rotation needed);
   move the fleet-agent's decrypted env file out of the worktree (mode-0600
   outside the repo, cleanup at startup).
2. **DONE (034a06d, opt-in)** — Shared bearer-token middleware (`DashboardAuthMiddleware`)
   for mutating routes + the handler registry; active when `FW_DASHBOARD_TOKEN`
   is set.
3. **DONE (2026-07-09)** — Fixed `scripts/lib/runner/start`: literal escaped
   quotes (`\"${DOMAINS[@]}\"`) wrapped each domain name in real quote chars
   so `filter_packages` matched nothing → "No domains/examples found" exit 1.
   Changed to the standard `set -u`-safe `${DOMAINS[@]+"${DOMAINS[@]}"}` idiom.
4. **DONE (034a06d)** — Default lease now `max(5min, exec_timeout+60s)` via
   `_lease_ms`; P1 also unified retry/timeout paths.
5. **DONE (034a06d)** — PostGIS guard `_reject_reason()`: single-statement +
   must-start-SELECT/WITH + expanded blocklist + read-only session with
   `statement_timeout`; 20 bypass regression tests.
6. **DONE (fwh_osm c334c13)** — Deleted + gitignored the committed `s3:` stub
   tree; `LocalStorage._reject_remote` guards `s3://`/`hdfs://` paths.

---

## 1. Compiler / language layer (FFL)

### Overview

A clean four-stage pipeline: **preprocess** (`facetwork/preprocess.py` —
character scanners that turn `script { … }` into string literals and stitch
`with`-continuations around LALR(1) limits) → **parse** (`facetwork/parser.py`
+ `facetwork/grammar/ffl.lark`, Lark LALR, `propagate_positions`, cached
parser) → **transform** (`facetwork/transformer.py` → dataclass AST in
`facetwork/ast.py`, every node carrying `node_id` + `SourceLocation` with a
provenance `source_id`) → **validate** (`facetwork/validator.py`, ~2,200
lines: names/references/yields/when/catch + light type inference, every
finding tagged `rule_id` + `docs_uri`) → **emit** (`facetwork/emitter.py`).
Multi-source compiles go through `parser.parse_sources` + `Program.merge`;
`facetwork/capabilities/index.py` builds the facet-capability index from the
emitted dict; `workflow_source.py` is a hand-written unparser.

### Strengths

- **rule_id + docs_uri as a first-class error contract** (`validator.py:62-93`,
  48 rule docs under `docs/reference/rules/`). The validate→fetch-doc→fix loop
  is structurally supported.
- **Soft keywords, deliberately** — `prompt`/`system`/`template`/`model`/
  `python` are `IDENT`s with semantic checks (`ffl.lark:48-78`,
  `transformer.py:658-742`); the grammar documents *why* for each trick.
- **Provenance-aware locations** — `SourceLocation.source_id` threads
  multi-source origin through to emitted JSON (`ast.py:27-34`,
  `emitter.py:220-259`).
- **Capability index decoupled via the emitted dict** — works identically on
  fresh compiles and catalog-stored `compiled_ast`; Effect/Cost/Author/Teams
  annotations reuse mixin machinery (`capabilities/index.py:167-217`).
- **Strong test volume where it matters**: 166 parser + 226 validator + 97
  emitter test functions; the preprocessor preserves line counts so errors
  report true line numbers (`preprocess.py:135-138`).
- **Type discipline**: no truthy coercion; arithmetic rejects String with a
  "use `++`" hint (`validator.py:1442-1602`).

### Flaws / risks (most severe first; confirmed by executing code unless noted)

1. ~~**Non-ASCII string literals are mangled**~~ — **FIXED (072d393).**
   `transformer.py` `STRING()` did `s.encode().decode("unicode_escape")` (UTF-8
   bytes reinterpreted as latin-1). Verified: `"em—dash"` → `'emâ\x80\x94dash'`.
   Corrupted every non-ASCII literal at the AST level (the FFL mojibake bug).
   Now `s.encode("latin-1", "backslashreplace").decode("unicode_escape")` —
   non-ASCII survives parse→emit→JSON, ASCII escapes unchanged; +3 tests.
2. **Transformer-raised `ParseError`s escape as raw `lark.exceptions.VisitError`**
   — `parser.py:126-146` catches only `UnexpectedInput`; all soft-keyword
   semantic checks hit this path, breaking the documented line/column error
   contract.
3. **No validation of call arguments against a known facet's parameters** —
   `F(nosuch = 1)` against `facet F(x: Int)` passes clean; missing required
   params likewise. The single most common authoring error has no rule.
4. **Unknown *unqualified* facet names pass silently** (`validator.py:626-627`
   "might be an external facet"); `REF_UNKNOWN_FACET` fires only for dotted
   names. Typos surface as unclaimed runtime tasks.
5. **Cross-file duplicate definitions silently last-win** — `Program.merge`
   concatenates namespaces (`ast.py:448-466`); the registry overwrites
   (`validator.py:307`); uniqueness is per-namespace-block only
   (`validator.py:673`). Real hazard for library/catalog compiles.
6. **A step can reference its own output** — `s1 = F(x = s1.y)` validates
   clean (step registered before args validated, `validator.py:1072` vs
   `:1099`; the forward-ref check exempts equal indices, `:1969-1980`).
7. **Float literals invisible to type inference** — transformer emits
   `kind="double"` but `_infer_type` knows only string/integer/boolean/null
   (`validator.py:1415-1424`): `1.5 + $.stringParam` passes.
8. **Validator is one 2,180-line class with order-dependent mutable state**
   (`validator.py:1387-1391` admits `_current_namespace` may be stale);
   `suggested_fix` is plumbed but never populated.
9. **The emitted JSON has no schema and polymorphic shapes** — `body`
   single-or-list (`emitter.py:327-333`), `"yield"` vs `"yields"`; consumers
   guess (`capabilities/index.py:101`).
10. *(Suspected)* `join_with_continuations`' paren-depth scan
    (`preprocess.py:244-252`) doesn't skip string literals — a `)` inside a
    string in a multi-line `with` clause can desynchronize the join.

### Improvements (prioritized)

1. ~~Fix string escape decoding~~ **DONE (072d393)** — latin-1+backslashreplace
   round-trip through `unicode_escape` (preserves escape semantics without the
   latin-1 corruption); non-ASCII regression tests added.
2. Catch `VisitError` in `parser.parse` and unwrap `orig_exc` into
   `ParseError` (three-line fix).
3. Add `CALL_UNKNOWN_PARAM` / `CALL_MISSING_REQUIRED_PARAM` + a
   warning-severity rule for unresolvable unqualified facet names; ship paired
   rule docs.
4. Consolidate namespaces in `Program.merge` so cross-file duplicates raise
   `DUPLICATE_NAME` with both locations.
5. Fix self-reference ordering and add `double` to inference.
6. Version + schema the emitted dict (JSON Schema in CI; always-list
   `body`/`yields`).
7. Decompose the validator into pass objects sharing an explicit
   `ScopeContext`.
8. Minor: Lark grammar caching; fix `grammar.md` §1.6 (wrongly lists soft
   keywords as reserved).

---

## 2. Runtime engine

### Overview

Workflows execute as persisted step state machines in MongoDB, advanced by
leaderless runners. `fw:execute:<Workflow>` bootstraps a run; handler tasks are
routed onto namespace-derived task lists (`task_list_routing.py`); runners
claim via atomic `find_one_and_update` with a lease
(`mongo_store/tasks.py:claim_task`), dispatch handlers, then advance via
`evaluator.process_single_step()` — cascading locally and enqueuing
`_fw_continue` continuations for the rest. Two runner implementations coexist:
`RunnerService` (`runner/service.py` — circuit breakers, watchdog, reapers,
reconcile) and `RegistryRunner` (`registry_runner.py` — continuation backlog
drain, bounded stuck-step sweep, per-handler timeouts). Recovery layers:
lease-expiry reclaim, dead-server orphan reaper, stuck-task watchdog, 5-minute
stuck-step sweep, manual `repair_workflow`.

### Strengths

- **Atomic, name-filtered claiming with defense in depth** — pending claim +
  lease-expired reclaim in one method with backoff awareness, plus a partial
  unique index guaranteeing one running task per step
  (`mongo_store/tasks.py:128-203`, `mongo_store/base.py:219-224`).
- **Fenced terminal writes** — `save_task_if_owned` stops a lease-reclaimed
  zombie overwriting the new claimer's task doc (`tasks.py:102-126`), with a
  `duplicate_completion_count()` invariant monitor.
- **Layered timeout semantics** — server heartbeat, per-task heartbeat with
  lease renewal + progress, stage budgets overriding the global watchdog
  (`mongo_store/servers.py:149-201`); reapers distinguish dead-server orphans
  from stuck-on-live tasks and respect active heartbeats.
- **Continuation-storm mitigation on both ends** — generation-time dedup
  (`evaluator.py:2203-2213`) and claim-time sibling coalescing
  (`registry_runner.py:638-650`).
- **The ffl-runner tier design is actually wired in** — `continuation_mode`
  inline/off/shared (`runner_config.py:43-79`); the runtime-version gate
  releases (not fails) incompatible continuations.
- **Real recovery-machinery tests** — 52 mongo-store tests including
  heartbeat-protects-from-reaping and dead-letter cases.

### Flaws / risks (most severe first)

> **Status (updated 2026-07-09) — P0 + P1 runtime-hardening tracks landed.**
> Resolved below: **#1** (lease default now `max(5min, exec_timeout+60s)`,
> P0 `_lease_ms`), **#3** (timeout path increments `retry_count`+backoff and no
> longer blocks the poll worker — `_transition_for_retry` + `shutdown(wait=False)`,
> commits 35fc512/b9af548), **#6** (retry semantics unified — RegistryRunner
> retries transient errors and dead-letters only at `max_retries`, 35fc512),
> **#8** (sweep now capacity/step/wall-clock bounded, 4696857), **#9**
> (non-blocking resume lock, b1168c8; both resume strategies now share it,
> e422c49). The two runner classes' shared logic is collapsed into
> `BaseRunner` (improvement #5, root cause of #6/#8/#9) — extraction 31fed2b,
> completed through 2b00b9e (`_load_workflow_ast`, `_emit_step_log`
> harmonized). **#4** (lease-reclaim retry_count), **#5** (CAS blind-replace
> clobber), **#10** (handled-stats RMW), **#12** (claim-query indexes), **#13**
> (storage partial-upload/error-masking), **#14a** (claim-regex escape) fixed
> 2026-07-09. **#2 FIXED** (both parts) — the terminal COMPLETED write is the
> ownership fence (continue_step/resume skipped when dropped), and
> `update_task_heartbeat` is now `expected_server_id`-gated so a zombie can't
> renew the new owner's lease. Still open: **#7** (`_fw_continue` drain on a
> RunnerService-only fleet), **#12 poll jitter**, **#14 (rest)**. **#11** (dead-letter
> resurrection) confirmed + fixed 2026-07-09.

1. **CONFIRMED — Default lease (5 min) < default execution timeout (15 min)
   with opt-in heartbeats ⇒ routine duplicate execution of slow handlers.**
   Lease renewal only happens if the handler calls `_task_heartbeat`
   (`runner/service.py:781-790`); nothing auto-renews for an active future.
   Any non-heartbeating handler running 5–15 min is reclaimed while still
   running (`base.py:105`, `service.py:236-238`).
2. ~~**CONFIRMED — Post-handler step writes are not ownership-fenced.**~~
   **FIXED 2026-07-09 (both parts).** (A) A zombie's `continue_step` +
   `resume_workflow` ran before the fenced save, advancing blocks concurrently
   with the new claimer — now the terminal COMPLETED write (via
   `save_task_if_owned`) is the FENCE: `_safe_save_task` returns whether it was
   accepted and BOTH runners skip `continue_step`/resume when it's dropped, so a
   reclaimed handler drops its result instead of advancing state. (B)
   `update_task_heartbeat` matched `{uuid, state:"running"}` with no server_id,
   so a zombie renewed the new owner's lease — it now takes an optional
   `expected_server_id` (threaded from all 4 callers) and gates the renewal on
   it. +3 tests.
3. **CONFIRMED — RegistryRunner's handler-timeout path is an infinite,
   backoff-free retry loop that also leaks the capacity slot.** Timeout resets
   the task to pending with no `retry_count` increment and no
   `next_retry_after` (`registry_runner.py:1036-1040`); the
   `ThreadPoolExecutor` context manager then blocks until the hung handler
   actually returns (`registry_runner.py:1018-1056`).
4. ~~**CONFIRMED — `claim_task`'s lease-expiry reclaim never increments
   `retry_count`**~~ **FIXED 2026-07-09.** A repeatedly-dying handler ping-ponged
   on lease expiry forever without reaching dead-letter. The reclaim branch now
   `$inc`s `retry_count` (it IS a retry) and only reclaims while the task has
   budget (`max_retries <= 0` = unlimited, else `retry_count < max_retries`, via
   `$ifNull`-guarded `$expr`). An exhausted task is left running-with-expired-
   lease for the reaper/stuck-watchdog → pending → `_dead_letter_overdue`; the
   ≥5min lease paces the failover. +3 tests.
5. ~~**CONFIRMED — Optimistic concurrency on steps is self-defeating.**~~
   **FIXED 2026-07-09 (updated-step clobber).** On a `version.sequence` CAS
   miss the code fell back to an unconditional `replace_one`
   (`mongo_store/steps.py`), clobbering the concurrent writer it just detected.
   Now a single atomic conditional replace writes ONLY when the DB row is
   strictly behind our sequence (`$or [version.sequence < seq, not exists]`) —
   covers normal advance / lost-update recovery / legacy rows, and drops (with
   a WARNING) a stale write when a concurrent writer already advanced to ≥ our
   sequence; +3 tests. *(Still open: the transaction fallback in `commit()` is
   chosen by string-matching the exception text, `steps.py:226-233` — a
   robustness nit, not data-loss.)*
6. **CONFIRMED — The two runner classes disagree on failure semantics.**
   `RunnerService` retries generic handler exceptions with backoff and
   dead-letters at `max_retries` (`service.py:1265-1299`); `RegistryRunner`
   marks the task FAILED permanently on first exception
   (`registry_runner.py:1153-1176`). Continuations: any exception permanently
   fails the `_fw_continue` task (`registry_runner.py:659-697`).
7. **CONFIRMED — On a `RunnerService`-only fleet, nobody drains `_fw_continue`
   at all** (`service.py:459-527` claims event names + `fw:*` only, while the
   evaluator still enqueues continuations) — cross-server cascades ride the
   5-minute sweep, and pending continuation rows accumulate unboundedly.
8. **CONFIRMED — `RunnerService`'s stuck-step sweep violates the spec's
   MUSTs** (`docs/reference/runtime.md` §10.4): no capacity check, no
   step/wall-clock cap, runs synchronously on the poll thread
   (`service.py:1569-1612`). `RegistryRunner` gets it right.
9. **CONFIRMED — Blocking resume lock convoy.** `_resume_workflow_for_step`
   docstring says "skipped if held" but does a blocking `lock.acquire()`
   (`service.py:1848-1852`) — a hot workflow queues every handler thread.
10. ~~**CONFIRMED — `_update_handled_stats` does an unguarded read-modify-write
    of the whole server doc per task**~~ **FIXED 2026-07-09.** It did
    get_server → set `.handled` → save_server, racing dashboard writes (silently
    reverting a QUARANTINE set in between). Added a targeted
    `update_server_handled` (persistence protocol + Mongo `$set` + MemoryStore)
    that writes ONLY the `handled` field; the runner now calls it. +1 regression
    test (concurrent QUARANTINE survives).
11. ~~**SUSPECTED — Mongo-side dead-lettering can be resurrected by the sweep**~~
    **CONFIRMED + FIXED 2026-07-09.** Verified: `_dead_letter_overdue` flips the
    task to `dead_letter` without failing the owning step (it has no evaluator),
    and `has_active_task_for_step` counts only pending/running — so the sweep saw
    "no active task" for the still-EventTransmit step and re-created a fresh
    `retry_count=0` task, looping forever. Added `has_dead_letter_task_for_step`
    (protocol + Mongo count + MemoryStore default); the sweep now `fail_step`s
    such a step (with AST → catch/error-propagation) and cascades, instead of
    resurrecting it. +1 test.
12. **CONFIRMED — Idle-fleet Mongo load** ~6-8 `find_one_and_update`s + a
    `get_server` per runner per 2 s; no jitter/adaptive backoff; no index on
    `lease_expires`/`next_retry_after`. **PARTIALLY FIXED 2026-07-09** — added
    `task_claim_pending_index` (state, task_list_name, name, next_retry_after)
    and `task_reclaim_index` (state, task_list_name, lease_expires) so both
    `claim_task` queries are index-covered incl. the range fields; dropped the
    superseded `task_claim_index`. Still open: poll jitter / adaptive backoff.
13. ~~**CONFIRMED — Storage streams finalize partial data and mask errors.**~~
    **FIXED 2026-07-09.** `_S3WriteStream.__exit__`/`_WebHDFSWriteStream.__exit__`
    unconditionally `close()`d — uploading the partial buffer even when the body
    raised (object stores have no partial-write semantics, so a consumer saw a
    "complete" truncated object). Both now take `(exc_type, …)` and ABORT
    (discard buffer + warn + propagate) when an exception is in flight, only
    finalizing on clean exit. `exists()`/`isfile()` swallowed every
    `ClientError` (auth/throttle ⇒ "not found"); they now re-raise unless
    `_s3_is_not_found` confirms a real 404/NoSuchKey. +5 tests.
14. **Minor, CONFIRMED** — ~~task names interpolated unescaped into the claim
    regex (`tasks.py:163`)~~ **FIXED 2026-07-09** (`re.escape` the literal prefix
    — a "." in a qualified name was a wildcard, and a "("/"$"/"+" could make
    Mongo's regex engine throw and fail the claim; +2 tests); unbounded
    `_ast_cache`/`_workflow_locks` (`service.py:225-232`); `_reconcile_with_db`
    snapshot-order race (`service.py:911-935`); circuit breakers only in
    `RunnerService`.

### Improvements (prioritized)

1. **Fence all post-claim writes on ownership** — thread the claiming
   `server_id` + a claim epoch/fencing token through `continue_step`, step
   commits, and heartbeats; reject stale writers.
2. **Auto-renew the lease while a future is active** (the runner already has a
   heartbeat thread), or raise default lease ≥ execution timeout.
3. **Unify retry semantics in one place** — route RegistryRunner timeout and
   exception paths through `_transition_for_retry`-equivalent; `$inc`
   retry_count on lease reclaim; retry-with-backoff for continuations.
4. ~~**Fix the CAS fallback**~~ **DONE (2026-07-09)** — the miss path no longer
   blind-replaces; one atomic conditional replace writes only when the DB row
   is strictly behind our sequence, else drops the stale write. *(Remaining:
   `$set` partial updates for one-field mutations; string-matched transaction
   fallback detection.)*
5. **Collapse the two runner classes' shared logic into one module** — the
   duplication is the root cause of findings 6/8/9.
6. Replace the timeout executor pattern with a persistent pool /
   `shutdown(wait=False)` + zombie tracking.
7. Run the ffl-runner tier by default on fleets (or stop generating
   `_fw_continue` from `RunnerService` evaluations); GC stale pending
   continuations.
8. `re.escape` claim names; TTL/LRU caches; indexes on
   `(state, next_retry_after)` / `lease_expires`; write streams abort (not
   finalize) on exception.

---

## 3. Dashboard, MCP server, catalog

### Overview

**Dashboard** (`facetwork/dashboard/`): FastAPI + Jinja2 + htmx; `app.py`
embeds a background reaper loop; cookie-based "acting as" identity with
explicitly no authentication; v3 UI is primary but legacy route families are
still registered; step-recovery mutations live in
`routes/execution/steps.py`. **MCP** (`facetwork/mcp/server.py`, ~1,800
lines): ~19 tools — compiler/validator, runtime ops, handler registry, PostGIS
read-only query, catalog family, `fw_capabilities`; static resources serve
rule docs and canonical examples. **Catalog** (`facetwork/catalog/`):
storage-agnostic `CatalogService` over a `CatalogStore` protocol —
content-hashed immutable revisions, pinned transitive deps, draft→published
review gate, handler-loadability preflight, intent-based reuse matching.

### Strengths

- **Self-repairing validator loop** — `fw_validate` returns
  `{rule_id, docs_uri, suggested_fix}` (`mcp/server.py:839-876`); rule docs +
  canonical examples served as resources (`server.py:1388-1490`); tool
  descriptions cross-reference each other ("prefer fw_repair_workflow…").
- **Reuse-first catalog matching** — field-weighted scoring with the authoring
  summary dominating (`catalog/service.py:260-311`, `:969-976`); summaries
  updatable without version bumps on dedup.
- **Cost/effect-aware capability index** — Effect/Cost from mixins, cost
  inferred from Timeout (`capabilities/index.py:188-217`), filterable
  (`index.py:303-360`).
- **Disciplined revision model** — content-hash dedup includes pinned dep
  hashes; cycle detection; definition-vs-execution IDs minted separately with
  the rationale documented inline (`service.py:461-485`).
- **Handler preflight** — only event facets transitively reachable from the
  entry workflow, verified importable before submit (`service.py:578-616`).
- **Step-recovery safety rails** — 409 + structured `blockers` when a RUNNING
  task would be orphaned; `force=true` override; WARNING-level audit logs
  (`routes/execution/steps.py:190-207`, `:455-479`).
- **XSS-conscious rendering** (`routes/execution/catalog.py:45-117`) and
  path-traversal checks on the examples resource (`server.py:1468-1471`).

### Flaws / risks (most severe first)

1. **CONFIRMED [security] — Zero authentication on a fully destructive
   surface.** Unauthenticated callers can bulk-delete runs
   (`routes/core/api.py:80-100`), delete users and their work
   (`routes/v3/admin.py:132-137`), and **register arbitrary handler
   `module_uri`s** (`api.py:414-440`; MCP `server.py:1191-1236`). Runners
   import registered modules ⇒ handler registration is effectively fleet-wide
   RCE for anyone who can reach port 8080. No middleware hook or token exists
   to turn on.
2. **SUSPECTED [security] — The PostGIS read-only guard is bypassable.**
   `_FORBIDDEN_SQL` (`server.py:1686-1692`) blocks `\bSET\b` but
   `set_config('transaction_read_only','off',false)` is one token;
   `default_transaction_read_only=on` is only a session default; no
   `statement_timeout` (pg_sleep DoS); multi-statement input unblocked; false
   positives on legitimate literals.
3. **CONFIRMED [security] — Hardcoded default DB credentials**
   (`postgresql://afl:afl@…` in `server.py:1757` and `viewdata.py:284`).
4. **CONFIRMED [correctness] — "Re-run From Here" deletes by timestamp, not
   dependency.** The dependency loop is dead code (`steps.py:779-789`, a `for`
   body of `pass`); fallback deletes every sibling with
   `start_time >= target's` (`steps.py:791-810`) — destroys unrelated
   completed work in parallel blocks.
5. **CONFIRMED [performance] — Sync Mongo inside async SSE generators**
   (`routes/core/api.py:193-227`) blocks the event loop every 1.5 s per open
   stream.
6. **CONFIRMED [performance] — Catalog search/match/list are N+1 and
   payload-heavy** — per entry, all revisions including full `ffl_source`
   fetched just to take the last (`service.py:243-256`, `store.py:191-193`).
7. **CONFIRMED [performance] — Runs list does a steps-scan per runner**
   (`viewdata.py:345-360`) — thousands of step docs per fan-out run per page
   view; point-lookup loops elsewhere (`viewdata.py:113-160`,
   `steps.py:36-42` loads all runners despite an index).
8. **CONFIRMED [correctness] — Dashboard reaper dies permanently on a
   transient startup failure** (`app.py:59-65`) — silently disables
   orphan/stuck recovery for the process lifetime.
9. **SUSPECTED [correctness] — `fw_manage_runner` unvalidated state
   transitions** (`server.py:1030-1067`): `resume` → RUNNING regardless of
   terminal state; catalog `save` read-then-increment race →
   `DuplicateKeyError` instead of retry (`service.py:146-147`).
10. **CONFIRMED [maintainability]** — two UI generations co-registered
    (`routes/__init__.py:54-79`); dead `_read_handler_source` with an
    arbitrary-file-read shape (`viewdata.py:255-269`); 854-line domain page in
    core (`routes/domain/census_maps.py`); raw `store._db` access bypassing
    the persistence API (`viewdata.py:234, 249, 399`).
11. **CONFIRMED [test gap] — Zero tests for the SQL guard**, the SSE
    endpoints, or the reaper loop.
12. **CONFIRMED [docs] — "41/41 rule docs" claim is stale** (48 rule docs
    exist); the diff script isn't in CI.

### Improvements (prioritized)

1. Opt-in auth: shared bearer token middleware on POST/PUT/DELETE + handler
   registry; keep cookie identity as attribution.
2. Structural SQL enforcement: read-only Postgres role +
   `options='-c default_transaction_read_only=on -c statement_timeout=15000'`
   + single-statement check; encode the bypasses as tests.
3. Fix `rerun` downstream detection using the compiled AST's `step.field`
   references; delete the dead loop.
4. Denormalize hot counters (per-run step-state counts; latest/published
   revision summaries on catalog entry docs; projections excluding
   `ffl_source`).
5. `asyncio.to_thread` (or Motor) in SSE; reaper retry-with-backoff.
6. Cache the capability index on a flows-collection change token.
7. Split `mcp/server.py`; finish the v2 cleanup; move domain pages behind
   `DomainPackage`-style registration.
8. Token-level scoring in catalog `search` (it substring-matches the whole
   query, `service.py:882-898`).

---

## 4. Domain handler ecosystem (fwh_*)

### Overview

19 standalone `fwh_*` packages (plus ~20 in-repo `examples/` and
`domain-template/`), nominally governed by
`agent-spec/tools-pattern.agent-spec.yaml` (one `tools/` CLI +
`_<pkg>_tools/` lib + thin handlers via a `handlers/shared` shim) and
`cache-layout.agent-spec.yaml` (staged writes, sidecar-last, per-entry
locks). In practice there are **two generations**: a contract-era cohort
(osm, noaa_weather, save_earth, census_us, anthropic, jenkins, genomics,
sensor_monitoring, peloton, groupphoto, sentinel2, osm_lz) that substantially
follows the pattern, and a newer map-domain cohort — **health, conflict,
migration, h1b, uspanel, osm_mapping, cancer** — that ignores it almost
entirely: each is a flat `src/<pkg>/_lib.py` monolith (3,876 lines across the
7) with no `tools/` CLIs, no sidecar cache, no vendored spec, ≤1 test file.
The pattern is sound and pays off where applied; it stopped being applied as
domain velocity increased, and its core primitives propagated by copy-paste
rather than a shared library.

### Strengths

- **The cache protocol is correctly implemented where it exists** — fwh_osm's
  `sidecar.py:159-202` does staged-write → sidecar-last faithfully;
  `LocalStorage.write_text_atomic` does mkstemp→fsync→`os.replace`; per-entry
  `fcntl` locks match the spec.
- **fwh_save_earth is best-in-class**: proper `handlers/shared` shim,
  genuinely thin handlers, offline `use_mock=True` tests, timeouts on every
  call including `(connect, read)` tuples, real 429/`Retry-After` backoff
  with Overpass mirror fallback (`_save_earth_tools/siting.py:122-139`).
  fwh_osm's `pbf_download.py:186-296` has equally good jittered 429/503
  handling.
- **fwh_anthropic's test-gating and secrets story is exemplary** — `-m 'not
  live'` addopts plus a `conftest.py` double gate (`--run-live` AND
  `ANTHROPIC_API_KEY`); mocks at the `get_client` seam; key only from env
  with a `redact_prompt` truncator.
- **Baseline hygiene is clean fleet-wide**: 0 bare `except:` across all 19
  repos, no hardcoded credentials, no committed venvs/caches (git-verified).

### Flaws / risks (most severe first)

1. **CONFIRMED — Contract collapse in the 7 newest domains, rooted in the
   template.** No `tools/` layer, no `.sh` wrappers, no sidecar/staged-write
   cache, no shim, no vendored spec, 0–1 test files (fwh_cancer: zero tests,
   no LICENSE, no agent-spec). Each uses the spec-forbidden bare `_lib`
   module name. Root cause: **`domain-template/` itself ships only `ffl/` +
   `handlers/` + `pyproject.toml`** — every scaffolded domain is
   nonconforming by construction.
2. **CONFIRMED — s3:// URLs treated as local paths, residue committed to
   git.** fwh_osm tracks a literal file
   `s3:/afl-cache/cache/osm/geojson/europe/germany-latest.geojson` (empty
   FeatureCollection stub). Cause: `LocalStorage`
   (`_osm_tools/storage.py:176,210`) has no guard rejecting a remote root —
   `os.makedirs("s3://…")` silently collapses to `s3:/…` on disk.
   *(Suspected)*: `sidecar.py:321-340` branches
   `isinstance(storage, LocalStorage)` else an HDFS `.walk` under
   `except Exception: return` — on the fleet's actual S3/MinIO backend,
   sidecar listing silently returns empty.
3. **CONFIRMED — Core primitives forked, not shared: 14 divergent copies.**
   `sidecar.py` in 6 repos with 6 distinct md5s; `storage.py` in 14
   locations, all 14 distinct. save_earth's copies already lack osm's
   `_is_remote_root()`. Any fix must be hand-applied N times and won't be.
4. **CONFIRMED — ≥5 independently reimplemented MapLibre choropleth
   renderers (~1,500+ lines)** — health's `choropleth.py`+
   `choropleth_time.py`, save_earth's `map_render.py` (775 lines), inline
   HTML in conflict/migration/h1b `_lib.py` — re-implementations with
   divergent CSS/legends/popups, so the facetwork-maps gallery drifts
   per-domain.
5. **CONFIRMED — The flagship violates its own thinness rule.** fwh_osm's
   domain logic lives in handler-tree god-modules unreachable from any CLI:
   `handlers/routes/gtfs_extractor.py` (1,396 lines), `network_ops.py`
   (1,200), `spatial_ops.py` (992). The mandated `handlers/shared/osm_utils.py`
   shim doesn't exist; `handlers/shared/` became a second library.
6. **CONFIRMED — 54 HTTP calls without timeouts across ~12 repos**
   (concentrated in the map cohort — `fwh_health/_lib.py:62,99,122` against
   429-prone CDC Socrata — but also osm's `gtfs_download.py` and noaa's
   downloaders). A hung endpoint blocks a runner slot until the coarse task
   timeout.
7. **CONFIRMED — Retry/backoff exists in only ~4 repos** (osm, save_earth,
   osm_mapping, jenkins). fwh_health makes ~19 single-attempt calls to
   CDC/WHO/ECDC with zero 429 handling.
8. **CONFIRMED — Silent error swallowing: 38 `except Exception:
   pass/continue` sites** plus ~36 swallow-to-`None` in fwh_osm handlers
   (`handlers/routing/api_router.py:117` collapses every ORS failure to
   `None`) — directly violating the project's "never silently return empty
   defaults" rule.
9. **CONFIRMED — The spec's `use_mock` offline mechanism is absent from the
   flagship and most repos** (0 occurrences in osm, genomics, jenkins,
   sensor_monitoring, osm_lz, cancer); osm's 87-file suite instead
   monkeypatches in 38 files — the anti-pattern the spec names as brittle.
10. **SUSPECTED (high value) — fwh_anthropic's flagship DocumentQA likely
    breaks live**: files upload via the beta namespace (`files.py:32-42`) but
    `create_message_with_file` sends the reference through non-beta
    `client.messages.create` (`messages.py:590`) without the files-API beta
    flag. The offline test mocks the same non-beta attribute so it can't
    catch it. Also CONFIRMED there: a phantom retry layer — `client.py:8` and
    the README advertise "retry/rate-limit helpers" that don't exist.
11. **CONFIRMED — fwh_health's only test is broken and stale** (asserts 3
    facets vs the actual 12 in `_DISPATCH`; failure recorded in
    `.pytest_cache`). Fleet-wide test cliff: osm 87 test files vs ≤1 for each
    of the 7 map domains.
12. **CONFIRMED — Vendored-spec drift is near-total**: 11 of 12 vendoring
    repos differ from canonical (5 still carry the 265-line v1; only peloton
    is byte-identical; the 7 map domains vendor nothing). Minor siblings:
    hardcoded `/Volumes/afl_data` in 3 storage copies; `convert_photos.py`
    duplicated (~353 lines each) between peloton and groupphoto.

### Improvements (prioritized)

1. **Extract `fwh-common` (or `facetwork.domainlib`) and stop vendoring core
   primitives**: `sidecar` + `storage` (with the remote-root guard and S3
   walk fix), an HTTP session helper (default timeout + `Retry-After`
   backoff — fixes flaws 6-7 in one place), and the MapLibre
   choropleth/time-slider renderer behind a LayerSpec-style API. Single
   highest-leverage change in the ecosystem.
2. **Fix `domain-template/` to scaffold the full contract** (tools/ with an
   example CLI pair, `_<pkg>_tools/` importing fwh-common, tests/ with an
   offline smoke test, spec reference). Until the template conforms, every
   new domain repeats the map-cohort drift.
3. **Build a conformance harness: `fw domain lint`.** Most contract rules are
   machine-checkable (tools/.sh pairing, naming, sidecar usage vs raw
   `open()`, `timeout=` grep, `use_mock` presence, spec-hash match, test
   count, swallow-site count). Score every repo, surface the matrix on the
   dashboard, gate `domains.json` registration on a floor.
4. **Decide the map-domain question explicitly instead of by drift** — either
   retrofit the 7 onto the pattern (save_earth as template) or amend the spec
   with a blessed "lite" variant that still mandates the non-negotiables
   (timeouts+retries, offline tests, storage via fwh-common, no bare `_lib`).
5. **Targeted fixes now**: delete/gitignore osm's committed `s3:` tree + the
   `LocalStorage` remote-root guard; repair fwh_health's failing test;
   implement or delete fwh_anthropic's phantom retry claims and route
   file-referencing messages through the beta endpoint (verify live).

---

## 5. Operations / fleet layer

### Overview

A thin-but-disciplined layer: the `fw` dispatcher `exec`s files under
`scripts/lib/<group>/<command>` (filesystem-as-registry, `# fw-summary:`
comments drive listings); the domain set lives once in `domains.json`
(`facetwork/domains/catalog.py`, bash access via `_helpers/_catalog.sh`);
`fw util gen-compose` regenerates per-domain runner blocks in
`docker-compose.full-stack.yml`. Fleet state is centralized in Mongo
(`fleet_config`, monotonic `version`), written by `scripts/lib/fleet/config`
and reconciled per-host by `scripts/lib/fleet/agent` under launchd (in-process
watchdog + external `agent-liveness`). MinIO creds are Fernet-encrypted in
`fleet_secrets` (`_helpers/_fleet_lib.py`). Rollouts: buildx → private HTTP
registry → `fleet set --image` → convergence poll. Per-role `server_groups`
gate role starts.

### Strengths

- **Filesystem-as-registry CLI is genuinely low-drift** (`fw:56-64`; typo
  suggestions `fw:67-80`; nested subgroups).
- **Consistent bash-3.2 portability** — `${arr[@]+"${arr[@]}"}` guards used
  systematically; zero bash-4isms in `scripts/lib`; portable `timeout`
  reimplementation (`rollout:39-51`).
- **fleet-agent unusually hardened** — all three Mongo timeout axes + cached
  client (`fleet/agent:56-99`), bounded docker calls, hard-exit watchdog,
  capped backoff resetting on new config versions, plus the external
  log-staleness watchdog (`fleet/agent-liveness`). Each hazard cites its
  incident.
- **Actionable preflights** (`runner/start:198-240` names the exact env var to
  fix) and **drain-aware rollouts** (`docker-compose.fleet.yml:48-55`,
  `stop_grace_period: 35s` above the 30s drain window, failure mode
  documented).
- **Orphan-replica recreate pattern** (`runner/start:271-281`) — a real
  production lesson encoded.
- **Catalog as a true single source** — compose services, fleet defaults,
  scaled tiers all derived (`catalog.py:95-129`); `fleet/config:60-78`
  validates role names against it.

### Flaws / risks (most severe first)

1. **CONFIRMED — Plaintext fleet secrets in the repo root, none gitignored.**
   `fleet/agent:264-268` writes decrypted MinIO creds to
   `mkstemp(prefix=".env.fleet-agent.", dir=REPO)`; cleanup is a `finally`
   that `os._exit(75)` (watchdog) and SIGKILL bypass — hence the four orphaned
   `.env.fleet-agent.*` files (Jun 30 frozen-daemon incident) with live S3
   creds. `git check-ignore` confirms none of `.env.fleet-agent.*` /
   `.env.pre-fw-bak` / `.env.fleet.pre-fw-bak` (contains `AFL_S3_*` keys) are
   ignored. One `git add -A` commits credentials.
2. **CONFIRMED — Insecure-by-default credentials end-to-end.** Silent
   `minioadmin`/`minioadmin` fallback (`fleet/agent:157-158`;
   `docker-compose.full-stack.yml:78-79,227-228`); `fleet secret set` takes
   secrets in argv (shell history / `ps`); the leaked temp files show the
   fleet actually runs on `minioadmin`.
3. **CONFIRMED — Quoting bug breaks `--domain`/`--example` in process mode.**
   `runner/start:322` passes `${DOMAINS[@]+\"${DOMAINS[@]}\"}` — the escaped
   quotes are literal (Python receives `['"peloton"']`); exact-match filtering
   (`facetwork/_pkg_discovery.py:183-186`) then resolves zero packages.
   Container mode exits earlier, which is why the fleet never noticed.
4. **CONFIRMED — fleet-agent "reconcile" is one-way; nothing is ever pruned.**
   `_apply` (`fleet/agent:161-347`) only brings roles up — removed roles keep
   running (and claiming tasks) with stale code indefinitely. `_record` also
   marks "applied vN" even when role starts failed with warnings.
5. **CONFIRMED — Rollout convergence detection can't fire.** `fleet/rollout:141-145`
   greps status output for `applied v`, but converged hosts print
   `up-to-date` (`fleet/config:207`) — when all converge, `total=0` fails the
   guard and the loop always runs to timeout. Also `applied v6` substring-matches
   inside v60.
6. **CONFIRMED — Hand-maintained `docker-compose.fleet.yml` has drifted**: no
   `runner-migration` stanza (generated full-stack has it) ⇒ `--fleet --domain
   migration` starts bundled Mongo/MinIO on a runner host — the exact trap the
   file's own comment warns about. `gen-compose --check` is wired into no CI.
   Catalog entries `sentinel2-landchange`, `peloton`, `groupphoto` have
   `service=None` so they silently can't be fleet roles.
7. **CONFIRMED — No health gate, no canary, all-at-once blast radius** —
   "converged" means compose returned 0, not that runners claim/complete
   tasks (honestly conceded in `docs/operations/fleet-rollouts.md:420-473`).
8. **CONFIRMED — One home fleet's IPs baked in as repo defaults** (registry
   `192.168.68.96:5050` in `fleet/rollout:25`, `fleet/registry-setup:12`;
   `server3.local`); single plain-HTTP unauthenticated registry as rollout
   SPOF.
9. **CONFIRMED — `_env.sh` silently retargets Mongo to localhost** on a 2 s
   unreachability (`_helpers/_env.sh:56-60`) — mutating commands can operate
   on the wrong database after a transient blip.
10. **CONFIRMED — `fleet set` is a lost-update race** — find_one → mutate →
    `replace_one` with no CAS (`fleet/config:100-173`); concurrent sets drop
    each other's changes and can mint duplicate versions (the agent's sole
    change-detection key).
11. **SUSPECTED — recreate gating watches only the osm image**
    (`fleet/agent:381,390`) — other role-image changes apply without
    `--recreate` (impact limited by compose's own image-diff recreate, except
    same-tag rebuilds with `pull_policy=missing`).
12. **CONFIRMED (minor)** — `.env` parser diverges from compose semantics (no
    quote-stripping, `_env.sh:14-24`); unquoted `cd $FW_REMOTE_PATH` in the
    ssh string (`runner/start:480`); `db/mongo-start` has no `set -e/-u`.

### Improvements (prioritized)

1. Stop materializing secrets in the worktree (gitignore now; write the
   agent's env file mode-0600 outside the repo; startup cleanup of stale
   files; `fleet secret set` from stdin/env).
2. Fix `runner/start:322` (drop the backslashes) + a smoke test that
   `--domain X` process-mode resolution yields ≥1 package.
3. Make the agent a real reconciler: diff running `runner-*` containers
   against desired roles and prune extras; per-role success in
   `fleet_agents`; gate recreate on all role images.
4. Generate `docker-compose.fleet.yml` from the catalog (every stanza is
   identical boilerplate) and add `gen-compose --check` to CI.
5. Machine-readable convergence (`fleet config status --json`) + a minimal
   health gate (N successful task claims on the new image) + staged rollout by
   `server_groups` (canary group first).
6. CAS the fleet-config write (`find_one_and_update` on `version` + retry).
7. De-hardcode the home fleet (`FW_FLEET_REGISTRY`/`FW_INFRA_IP` required or
   read from `fleet_config.endpoints`); make the `_env.sh` localhost fallback
   opt-in or read-only-commands-only.

---

## 6. Research directions (consolidated)

**Language / compiler**

1. **Effect/cost as a checked type system.** Promote the Effect/Cost mixins
   from strings to semantics: infer a workflow's effect/cost from its steps
   (pure∘pure=pure; max/sum of tiers), validate declared vs inferred, and make
   `fw_capabilities(max_cost=…)` a *sound* filter — a compile-time
   effect-and-budget calculus over distributed steps.
2. **Bidirectional grammar / verified round-trip.** Replace the hand-written
   unparser with a printer derived from (or property-tested against) the
   parser: `print(parse(x))` canonical, `parse(print(ast)) ≡ ast`, fuzzed via
   Hypothesis. Enables canonical formatting and minimal-diff LLM edits.
3. **Machine-applicable repairs.** Populate `suggested_fix` with structured
   edits (`{span, replacement}`) synthesized from rule + scope — the MCP loop
   becomes propose-and-apply, measurable on a corpus of LLM-generated FFL
   failures.
4. **Parametric collection types + gradual typing.** Carry element types
   through inference (`Json` as the explicit dynamic escape hatch) so
   `foreach` loop variables and index expressions are checkable — the biggest
   remaining source of runtime-only errors in fan-out workflows.

**Runtime**

5. **Fairness and scheduling on the shared claim queue** — weighted claiming
   across `workflow_id`, priority lanes for continuations vs bootstraps,
   starvation metrics; acute if the ffl-runner tier centralizes orchestration.
6. **Effective-exactly-once for state transitions** — claim-epoch fencing
   tokens on every step/task write + idempotency keys for handler side
   effects, so "idempotent recovery" holds under the lease-reclaim race, not
   just crash-restart.
7. **Backpressure and adaptive polling** — jittered idle backoff or Mongo
   change streams; admission control on `fw:execute` when pending depth or
   dead-letter rate crosses thresholds (the Docker-VM-disk incident is the
   motivating failure).
8. **The ffl-runner tier as the version-skew boundary** — extend versioning to
   bootstrap/resume payload schemas and task-doc shape; a drain-based cutover
   protocol validated in `fw fleet simulate`.

**Dashboard / MCP / catalog**

9. **Catalog as compounding memory** — log every `match` verdict + whether the
   user ran the suggestion; use (request → chosen slug) pairs to calibrate the
   0.6/0.3 thresholds empirically and turn failed matches into an authoring
   backlog.
10. **Typed plan search over the capability index** — find facet chains where
    returns unify with params (a small typed planner), pruned by the
    effect/cost lattice: "lookup-then-verify" instead of "lookup-then-compose."
11. **Verified review gates** — attach machine-checkable evidence to
    publication (handler preflight result, dry-run trace, param-schema
    fuzzing, effect-budget bounds) so "published" carries evidence, not just
    approval.
12. **Recovery-action correctness via the state machine** — a declarative
    legal-recovery-transitions table shared by evaluator, dashboard, and MCP,
    with property-based convergence tests.

**Domain ecosystem**

13. **Conformance-scored auto-generated domains** — run `fw domain lint` +
    the offline suite inside the scaffold/generation loop so Claude-authored
    domains are contract-conformant by construction (the map-cohort drift is
    exactly what unchecked generation produces).
14. **Contract property tests as a portable kit** — implement the
    cache-layout spec's declared `atomic_publish` / `staging_crash_safe` /
    `overwrite_serialized_per_key` properties once (hypothesis-based,
    parameterized over storage backends) and run them against every domain's
    sidecar implementation, especially on S3/MinIO where the code is weakest.
15. **Declarative map rendering as a facet library** — promote the shared
    renderer to FFL facets (`maps.Choropleth`, `maps.TimeSlider`,
    `maps.Publish`) so a new map domain is pure data adapters + a composed
    workflow, and "new world map" becomes a lookup-then-compose task for the
    catalog matcher.
16. **Cross-domain data-source registry** — a machine-readable per-source
    descriptor (endpoint, auth, rate limits, retry policy, known failure
    modes: UNAIDS/USCIS UA-gating, Overpass per-IP limits, Geofabrik bans,
    CDC Socrata throttling) shared via fwh-common, letting the HTTP helper
    auto-apply the right politeness policy.

**Operations**

17. **Declarative fleet reconciliation** — Kubernetes-style continuous diff of
    actual containers vs desired role set (converging additions *and*
    removals), config history for one-command rollback; the
    `fleet_config`/`fleet_agents` schema is ~80% there.
18. **Health-gated progressive delivery using the task fabric itself** —
    promote an image group-by-group only when the canary group's post-upgrade
    task success rate matches baseline; zero new infrastructure needed.
19. **Capability-aware scheduling beyond static `server_groups`** —
    runner-advertised capabilities (cores/RAM/disk/extras/GPU) matched against
    per-role resource hints in `domains.json`.
