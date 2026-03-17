# AgentFlow Self-Assessment

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

## Runtime — B+

The most ambitious and complex area. The iterative state machine with ~25 states, multiple changers, handler dispatch, dependency graphs, block hierarchies, and persistence abstraction is architecturally sound. But it's also where the hardest bugs have lived: step deduplication races (v0.12.72), dirty block tracking (v0.12.70), `resume_step` scaling (v0.12.64), yield deferral timing (v0.12.83), error propagation through block hierarchies (v0.12.75). Each fix was correct but reactive. The catch implementation went smoothly because the pattern was well-established by then — but that pattern itself took many iterations to stabilize.

The v0.32.0+real integration tests exposed and fixed three runtime issues that only manifested in full pipeline execution: (1) when-block deferred evaluation — when blocks referencing steps from prior `andThen` blocks must defer until those steps complete, fixed via `_step_not_ready` flag; (2) cross-block step reference resolution — `get_completed_step_by_name` now falls back to workflow-wide search with direct `statement_name` lookup; (3) runner terminal state propagation — `_resume_workflow` now updates the runner entity to COMPLETED/FAILED when the workflow finishes, so the dashboard reflects final status. These were real bugs that unit tests didn't catch because they span the compile→evaluate→dispatch→resume→completion cycle. The fact that the integration tests found them validates the approach. The B → B+ upgrade reflects both the fixes and the proof that the full pipeline now works end-to-end through MemoryStore (tests), MongoStore (direct), and the dashboard runner service.

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

## Infrastructure (MCP, SDKs, CI) — B+

Broad coverage — MCP server, 4 non-Python SDKs, Docker, CI pipeline. But depth varies: the Python SDK is production-quality while the others maintain feature parity through parallel implementation rather than shared abstractions.

The v0.33.0 operations tooling is a meaningful step toward production readiness. Runner HTTP ports are now persisted in MongoDB (`ServerDefinition.http_port`), enabling remote health checks without out-of-band port discovery. The shared `_remote.sh` helpers (SSH wrapper, MongoDB query, state polling) provide a consistent foundation for multi-host management. `stop-runners` and `start-runner` gained `--all`/`--host` remote modes while preserving backward-compatible local behavior. `rolling-deploy` implements zero-downtime serial restart with drain → wait → start → health-check per server, abort-on-failure safety, and configurable timeouts. `list-runners` gives fleet visibility with a tree view (servers → runners → handlers) including uptime, last ping, namespace-grouped handler lists, and handled/skipped stats. The B → B+ upgrade reflects that the infrastructure now covers not just "how to run" but "how to operate" — deploying, inspecting, and cycling a multi-host runner fleet without downtime.

The v0.39.0 orphaned task reaper closes the last major gap in crash recovery. Previously, if a runner crashed (OOM, SIGKILL, network partition) its in-flight tasks were stuck in `running` state forever — requiring manual MongoDB intervention (`db.tasks.updateMany`). Now every runner automatically detects dead peers via stale heartbeats and resets their orphaned tasks to `pending`. The reaper uses the same 5-minute `SERVER_DOWN_TIMEOUT_MS` threshold as the dashboard's `effective_server_state()`, ensuring consistent behavior. `claim_task()` stamps each task with the claiming server's `server_id`, and the reaper only touches servers that crashed without deregistering (not gracefully shut-down ones). The Fleet dashboard page (`/v2/fleet`) provides visual confirmation of server health with per-server task counts by event facet.

---

## Overall — B+

The system is genuinely functional and has grown from a parser experiment to a full compiler + runtime + multi-language platform. The strongest areas are where discipline was highest (emitter, tests, examples). The weakest are where complexity accumulated organically (runtime edge cases, dashboard frontend architecture). The v0.31.0 release addressed the two weakest areas identified in this assessment: the validator now infers parameter types (B- → B), and the dashboard has shared component partials (C+ → B-). The v0.32.0 HIV drug resistance example validates that the three most recent language features (`andThen when`, `catch`, `andThen foreach`) compose cleanly in a realistic domain.

The v0.32.0+real work upgraded two grades: runtime B → B+ (3 cross-cutting bugs fixed by integration tests, full pipeline verified through MemoryStore, MongoStore, and dashboard runner) and testing A- → A (13 integration tests covering the compile→evaluate→dispatch→resume→completion cycle, plus script sandbox hardening with restricted `__import__`). The v0.33.0 work upgraded infrastructure B → B+ with production operations tooling: remote runner management (`--all`/`--host` on start/stop), zero-downtime rolling deploy, fleet inspection (`list-runners`), HTTP port persistence, and shared SSH/MongoDB helpers. The remaining systemic gap is step reference type resolution — the validator still returns "Unknown" for `step.field`, deferring return-type errors to runtime.
