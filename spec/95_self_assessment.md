# AgentFlow Self-Assessment

A self-assessment across all fundamental areas, drawing on the full arc from v0.11 through v0.30.

---

## Grammar / Parser — B+

The Lark LALR grammar works and has grown to handle a complex language. But it's accumulated workarounds: priority tokens (`CATCH_KW.2`, `NULL.2`, `WHEN_KW.2`) to avoid IDENT conflicts, a preprocessor for brace-delimited scripts, and rigid same-line constraints (no newlines between `)` and `andThen`, `=>`, `with`, `catch`). Each feature addition requires careful token choreography. It works, but a more experienced language designer might have structured the lexical rules to avoid this accumulation.

---

## AST — A-

Clean dataclass design with consistent patterns. New nodes like `CatchClause`, `WhenBlock`, `ScriptBlock` all follow the same shape. The `node_id` UUID on every node was a good early decision. Minor knock: the `field(default=None, kw_only=True)` dance to add optional fields to existing dataclasses (like `catch` on `StepStmt`) is fragile if field ordering changes.

---

## Transformer — B

Works but has become brittle. The `items` list filtering by type (as we saw with `CATCH_KW` token leaking through) is a recurring pattern that's error-prone. Each new clause means updating multiple methods to iterate items and isinstance-check. It's functional but not elegant — a visitor pattern or explicit tree-walking might have scaled better.

---

## Emitter — A

Solid. The `declarations`-only JSON format is clean. `_convert()` dispatch is straightforward. Round-trip testing catches regressions. The `_add_metadata` pattern ensures consistent location data. This is probably the most maintainable layer.

---

## Validator — B-

Good coverage of structural rules (duplicates, reference validity, yield targets, when block constraints). But type inference is shallow — `_infer_type` returns `"Unknown"` for any reference (`$.param`, `step.field`), which means expressions like `$.input + 1` used as a boolean condition pass validation silently. We hit this exact gap this session and had to use a literal `42` instead. A proper type-tracking scope would catch significantly more errors at compile time.

---

## Runtime — B

The most ambitious and complex area. The iterative state machine with ~25 states, multiple changers, handler dispatch, dependency graphs, block hierarchies, and persistence abstraction is architecturally sound. But it's also where the hardest bugs have lived: step deduplication races (v0.12.72), dirty block tracking (v0.12.70), `resume_step` scaling (v0.12.64), yield deferral timing (v0.12.83), error propagation through block hierarchies (v0.12.75). Each fix was correct but reactive. The catch implementation this session went smoothly because the pattern was well-established by then — but that pattern itself took many iterations to stabilize.

---

## Testing — A-

3,409 tests with good discipline: every grammar construct has parser tests, emitter round-trips, validator error cases, and runtime behavior tests. The structure (test classes per feature, consistent fixtures) is consistent. Knock: runtime tests sometimes don't match actual runtime patterns (the `_create_context` mistake this session), suggesting the test infrastructure could use helper factories that mirror real execution more closely.

---

## Specs / Documentation — B+

Comprehensive — 10 spec files covering language, semantics, validation, runtime, events, states, agents, examples, acceptance, and ops. They're the source of truth and generally accurate. But they lag behind implementation (I'm always updating them after the code) and the runtime spec (`30_runtime.md`) has grown to 1,400+ lines mixing formal specification with implementation details.

---

## Examples — A

12 examples forming a genuine progression from simple (`monte-carlo-risk`) to complex (`site-selection-debate`, `devops-deploy`). Each showcases specific features and has full handler implementations plus tests. The RegistryRunner-first pattern in later examples is a good architectural evolution. These are probably the strongest proof that the system works end-to-end.

---

## UI / Dashboard — C+

The dashboard is functional but it's the area where the approach has scaled least gracefully.

### What works

- Server-rendered FastAPI + Jinja2 gets features shipped fast — workflow browser, runner detail, step trees, handler management, output browser all exist and work
- SSE log streaming (v0.27.0), DAG visualization, timeline charts add real operational value
- Cmd+K command palette and sidebar nav (v0.23.0) improved navigation significantly
- Census maps with GeoJSON rendering show it can handle domain-specific visualization
- Auto-refresh partials avoid full page reloads
- Python tests cover routes and HTML output reliably

### What doesn't

- **No frontend architecture.** It's templates with inline JavaScript, jQuery-style event handlers, and CSS classes scattered across Jinja2 files. Every new feature is another template with its own JS blob
- **No component reuse.** The step tree, progress bars, state badges — all reimplemented per page rather than shared
- **One-off visualizations.** The DAG visualization and timeline chart are standalone JavaScript implementations, not backed by a charting library with consistent styling
- **No responsive design discipline.** It works on desktop but that's about it
- **Ad-hoc state management.** Some pages auto-refresh, some use SSE, some are static — no consistent pattern
- **Basic forms.** The handler create/edit forms (v0.26.0) are plain HTML forms without client-side validation or feedback
- **No design system.** Visual design is utilitarian Bootstrap without coherent theming

### The core issue

I treated the dashboard as "add the next feature to the template" rather than building a proper frontend foundation. A monitoring dashboard for a workflow engine is actually a significant UI challenge — step hierarchies, real-time state, dependency graphs, log streaming — and it deserved either a proper React/Vue SPA or at least a disciplined HTMX + server-components approach from the start. Instead it grew organically and each feature is its own island.

---

## Infrastructure (MCP, SDKs, CI) — B

Broad coverage — MCP server, 4 non-Python SDKs, Docker, CI pipeline. But depth varies: the Python SDK is production-quality while the others maintain feature parity through parallel implementation rather than shared abstractions.

---

## Overall — B+

The system is genuinely functional and has grown from a parser experiment to a full compiler + runtime + multi-language platform. The strongest areas are where discipline was highest (emitter, tests, examples). The weakest are where complexity accumulated organically (transformer, validator type inference, runtime edge cases). The biggest systemic gap is that the validator doesn't track types through references, which means the runtime catches errors that the compiler should.
