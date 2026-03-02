# AgentFlow Self-Assessment

A self-assessment across all fundamental areas, drawing on the full arc from v0.11 through v0.33.0.

---

## Grammar / Parser — B+

The Lark LALR grammar works and has grown to handle a complex language. But it's accumulated workarounds: priority tokens (`CATCH_KW.2`, `NULL.2`, `WHEN_KW.2`) to avoid IDENT conflicts, a preprocessor for brace-delimited scripts, and rigid same-line constraints (no newlines between `)` and `andThen`, `=>`, `with`, `catch`). Each feature addition requires careful token choreography. It works, but a more experienced language designer might have structured the lexical rules to avoid this accumulation.

---

## AST — A-

Clean dataclass design with consistent patterns. New nodes like `CatchClause`, `WhenBlock`, `ScriptBlock` all follow the same shape. The `node_id` UUID on every node was a good early decision. Minor knock: the `field(default=None, kw_only=True)` dance to add optional fields to existing dataclasses (like `catch` on `StepStmt`) is fragile if field ordering changes.

---

## Transformer — B+

Improved in v0.30.1. The `items` list filtering by type was a recurring error-prone pattern — each new clause meant updating multiple methods with ad-hoc isinstance loops. The v0.30.1 refactoring extracted `_find_one`/`_find_all`/`_find_rest` helpers, consolidated the triplicated declaration logic into `_build_declaration`, and unified item segregation via `_segregate_declarations` with a type map. This removed 72 lines of duplication and means adding future grammar clauses touches fewer methods. The `CATCH_KW` token-leaking class of bug is still possible (items are untyped lists from Lark), but the helpers make the filtering consistent and less likely to diverge across methods.

---

## Emitter — A

Solid. The `declarations`-only JSON format is clean. `_convert()` dispatch is straightforward. Round-trip testing catches regressions. The `_add_metadata` pattern ensures consistent location data. This is probably the most maintainable layer.

---

## Validator — B

Good coverage of structural rules (duplicates, reference validity, yield targets, when block constraints). In v0.31.0, `_infer_type` now resolves input reference types (`$.param`) from signature parameters via `_param_scope`, catching errors like `$.text + 1` where `text: String` and `$.flag > 0` where `flag: Boolean`. This closes the biggest type inference gap. Step references (`step.field`) still return `"Unknown"` — resolving return types through the call graph is the remaining Phase 2 work. The improvement is meaningful: the 5 primitive types (`String`, `Int`, `Long`, `Double`, `Boolean`) cover the vast majority of parameter declarations.

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

Comprehensive — 10 spec files covering language, semantics, validation, runtime, events, states, agents, examples, acceptance, and ops. They're the source of truth and generally accurate. But they lag behind implementation (I'm always updating them after the code) and the runtime spec (`30_runtime.md`) has grown to 1,400+ lines mixing formal specification with implementation details.

---

## Examples — A

13 examples forming a genuine progression from simple (`monte-carlo-risk`) to complex (`site-selection-debate`, `devops-deploy`, `hiv-drug-resistance`). Each showcases specific features and has full handler implementations plus tests. The RegistryRunner-first pattern in later examples is a good architectural evolution. The v0.32.0 HIV drug resistance example is the first to combine `andThen when`, `catch`, and `andThen foreach` in a single example — demonstrating that these features compose naturally for real-world bioinformatics pipelines (QC branching, per-sample error recovery, batch processing). The example pattern is now well-templated: 8 test classes, 4 handler categories, shared utils, dual agent entry points.

The HIV example now has real integration tests (`tests/real/`) that run the full pipeline through the evaluator + poller loop, plus verified execution through the dashboard runner service with MongoStore persistence. BatchAnalysis with 3 patient samples completes in ~14 seconds through the dashboard (56 steps, all 9 handlers dispatched per sample). This is the first example validated end-to-end through the production execution path (seed → dashboard submit → runner service → handler dispatch → completion).

---

## UI / Dashboard — B-

The dashboard is functional but it's the area where the approach has scaled least gracefully. The v0.31.0 shared component extraction was a meaningful step toward component reuse.

### What works

- Server-rendered FastAPI + Jinja2 gets features shipped fast — workflow browser, runner detail, step trees, handler management, output browser all exist and work
- SSE log streaming (v0.27.0), DAG visualization, timeline charts add real operational value
- Cmd+K command palette and sidebar nav (v0.23.0) improved navigation significantly
- Census maps with GeoJSON rendering show it can handle domain-specific visualization
- Auto-refresh partials avoid full page reloads
- Python tests cover routes and HTML output reliably
- **Shared components (v0.31.0):** `_state_badge.html`, `_empty_state.html`, `_attrs_table.html` partials replace 30 duplicated patterns across 22 templates. State badges now have a single source of truth. This is the template-level component reuse the dashboard lacked

### What doesn't

- **No frontend architecture.** It's templates with inline JavaScript, jQuery-style event handlers, and CSS classes scattered across Jinja2 files. Every new feature is another template with its own JS blob
- **One-off visualizations.** The DAG visualization and timeline chart are standalone JavaScript implementations, not backed by a charting library with consistent styling
- **No responsive design discipline.** It works on desktop but that's about it
- **Ad-hoc state management.** Some pages auto-refresh, some use SSE, some are static — no consistent pattern
- **Basic forms.** The handler create/edit forms (v0.26.0) are plain HTML forms without client-side validation or feedback
- **No design system.** Visual design is utilitarian Bootstrap without coherent theming

### The core issue

I treated the dashboard as "add the next feature to the template" rather than building a proper frontend foundation. A monitoring dashboard for a workflow engine is actually a significant UI challenge — step hierarchies, real-time state, dependency graphs, log streaming — and it deserved either a proper React/Vue SPA or at least a disciplined HTMX + server-components approach from the start. The v0.31.0 shared partials are a step in the right direction — extracting reusable components from the template soup — but the deeper architectural issues (no JS module system, no design system, no consistent state management) remain.

---

## Infrastructure (MCP, SDKs, CI) — B+

Broad coverage — MCP server, 4 non-Python SDKs, Docker, CI pipeline. But depth varies: the Python SDK is production-quality while the others maintain feature parity through parallel implementation rather than shared abstractions.

The v0.33.0 operations tooling is a meaningful step toward production readiness. Runner HTTP ports are now persisted in MongoDB (`ServerDefinition.http_port`), enabling remote health checks without out-of-band port discovery. The shared `_remote.sh` helpers (SSH wrapper, MongoDB query, state polling) provide a consistent foundation for multi-host management. `stop-runners` and `start-runner` gained `--all`/`--host` remote modes while preserving backward-compatible local behavior. `rolling-deploy` implements zero-downtime serial restart with drain → wait → start → health-check per server, abort-on-failure safety, and configurable timeouts. `list-runners` gives fleet visibility with a tree view (servers → runners → handlers) including uptime, last ping, namespace-grouped handler lists, and handled/skipped stats. The B → B+ upgrade reflects that the infrastructure now covers not just "how to run" but "how to operate" — deploying, inspecting, and cycling a multi-host runner fleet without downtime.

---

## Overall — B+

The system is genuinely functional and has grown from a parser experiment to a full compiler + runtime + multi-language platform. The strongest areas are where discipline was highest (emitter, tests, examples). The weakest are where complexity accumulated organically (runtime edge cases, dashboard frontend architecture). The v0.31.0 release addressed the two weakest areas identified in this assessment: the validator now infers parameter types (B- → B), and the dashboard has shared component partials (C+ → B-). The v0.32.0 HIV drug resistance example validates that the three most recent language features (`andThen when`, `catch`, `andThen foreach`) compose cleanly in a realistic domain.

The v0.32.0+real work upgraded two grades: runtime B → B+ (3 cross-cutting bugs fixed by integration tests, full pipeline verified through MemoryStore, MongoStore, and dashboard runner) and testing A- → A (13 integration tests covering the compile→evaluate→dispatch→resume→completion cycle, plus script sandbox hardening with restricted `__import__`). The v0.33.0 work upgraded infrastructure B → B+ with production operations tooling: remote runner management (`--all`/`--host` on start/stop), zero-downtime rolling deploy, fleet inspection (`list-runners`), HTTP port persistence, and shared SSH/MongoDB helpers. The remaining systemic gap is step reference type resolution — the validator still returns "Unknown" for `step.field`, deferring return-type errors to runtime.
