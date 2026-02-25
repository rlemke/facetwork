# CLAUDE.md — AgentFlow Repo Guide

## Purpose
This repository contains the **AgentFlow** platform:
- **AFL compiler**: parses AFL source (Lark LALR) and emits JSON workflow definitions (declarations-only format)
- **AFL runtime**: executes compiled workflows with iterative evaluation and dependency-driven step creation
- **Agent libraries**: Python, Scala, Go, TypeScript, Java
- **Dashboard, Runner, MCP server**: operational infrastructure

## Terminology
- **AgentFlow**: The platform for distributed workflow execution (compiler + runtime + agents)
- **AFL**: Agent Flow Language — the DSL for defining workflows (`.afl` files)
- **AFL Agent**: A service that processes event facet tasks. The **recommended approach** is `RegistryRunner`: register handler implementations in the database, then start the runner — it dynamically loads and dispatches handlers without requiring custom agent code.
- **RegistryRunner**: Universal runner that reads `HandlerRegistration` entries from persistence, dynamically loads Python modules, and dispatches event tasks. Handlers are registered via `register_handler()` or the MCP `afl_manage_handlers` tool.

---

## Conceptual model (use these terms consistently)

### Core constructs
- **Facet**: a typed attribute structure with parameters and optional return clause.
- **Event Facet**: a facet prefixed with `event` that triggers agent execution.
- **Workflow**: a facet designated as an entry point for execution.
- **Step**: an assignment of a call expression within an `andThen` block.
- **Schema**: a named typed structure (`schema Name { field: Type }`) used as a type in parameter signatures. **Schemas must be defined inside a namespace.** When referencing a schema from another namespace, either use a fully-qualified name (`ns.SchemaName`) or import the namespace with `use ns` (if unambiguous).

### Agent execution models
- **RegistryRunner** (recommended): auto-loads handlers from DB — no custom service code needed. Register handlers via `register_handler()` or MCP tool `afl_manage_handlers`.
- **AgentPoller**: standalone agent services with `register()` callback.
- **RunnerService**: distributed orchestration with locking, thread pool, and HTTP status.
- **ClaudeAgentRunner**: LLM-driven in-process execution via Claude API.

### Composition features
- **Mixins**: `with FacetA() with FacetB()` composes normalized facets.
- **Implicit facets**: `implicit name = Call()` declares default values.
- **andThen / yield blocks**: compose multi-step internal logic. Facets/workflows support multiple concurrent `andThen` blocks.
- **andThen foreach**: iterate over collections with parallel execution.
- **Statement-level andThen body**: steps can have inline `andThen` blocks (`s = F(x = 1) andThen { ... }`).

### Expression features
- **Arithmetic operators**: `+`, `-`, `*`, `/`, `%` with standard precedence (`*/%` > `+-` > `++`).
- **Concatenation**: `++` operator for string concatenation.
- **Collection literals**: arrays `[1, 2, 3]`, maps `#{"key": "value"}`, indexing `arr[0]`, grouping `(expr)`.
- **Type checking**: validator catches string+int and bool+arithmetic errors at compile time; unknown-type refs pass through.

---

## Quick commands

```bash
# Tests
pytest tests/ examples/ -v          # full suite
pytest tests/ examples/ -v -x      # stop on first failure
pytest tests/ examples/ --cov=afl --cov-report=term-missing  # with coverage
pytest tests/ examples/ --cov=afl --cov-report=html          # HTML coverage report
pytest tests/runtime/test_mongo_store.py --mongodb -v  # real MongoDB
pytest examples/osm-geocoder/tests/ -v               # single example

# CLI
afl input.afl -o output.json       # compile
afl input.afl --check              # syntax check only

# Services (--log-format json|text; default: json for Splunk)
python -m afl.dashboard             # web UI (port 8080)
python -m afl.dashboard --log-format text  # plain-text logs
python -m afl.runtime.runner        # runner service
python -m afl.mcp                   # MCP server (stdio)
```

### Environment configuration
Copy `.env.example` to `.env` and edit to configure MongoDB, scaling, overlays, and data directories. All `scripts/` commands source `_env.sh` which loads `.env` without overriding already-set vars. `scripts/easy.sh` runs the full pipeline (teardown → rebuild → setup → seed) from `.env` alone. See `spec/90_nonfunctional.md` for the full variable reference.

---

## Key directories

| Directory | Purpose |
|-----------|---------|
| `afl/` | Compiler package (parser, transformer, emitter, validator, AST, grammar) |
| `afl/runtime/` | Runtime engine (evaluator, state machine, persistence, changers, handlers) |
| `afl/runtime/runner/` | Distributed runner service |
| `afl/mcp/` | MCP server for LLM agents |
| `afl/dashboard/` | FastAPI web monitoring dashboard |
| `tests/` | Core tests (compiler, runtime, dashboard, MCP) |
| `agents/` | Multi-language agent libraries (Python, Scala, Go, TypeScript, Java) |
| `examples/osm-geocoder/` | OSM geocoding example (42 AFL files, 16 handler categories, ~80 handler modules) |
| `spec/` | Language and runtime specifications |
| `docker/` | Dockerfiles for all services |
| `scripts/` | Convenience scripts (setup, compile, runner, dashboard, etc.) |

---

## How Claude should review changes

### Language/compiler correctness
- Parsing errors must include line/column
- Grammar must be LALR-compatible (no conflicts)
- AST nodes must use dataclasses
- JSON output must be stable and consistent
- **Emitter output uses `declarations` only** — the emitter does not produce categorized keys (`namespaces`, `facets`, `eventFacets`, `workflows`, `implicits`, `schemas`). All declaration nodes appear in a single `declarations` list. `normalize_program_ast()` in `afl/ast_utils.py` still handles old/external JSON that uses categorized keys.

### Testing requirements
- All grammar constructs must have parser tests
- Error cases must verify line/column reporting
- Emitter must round-trip all AST node types

### Code quality
- Type hints on all functions
- Docstrings on public API
- No runtime dependencies beyond lark (dashboard, mcp deps are optional)

---

## Further reference
- `spec/00_overview.md` — implementation constraints and glossary
- `spec/10_language.md` — AFL syntax (EBNF grammar)
- `spec/11_semantics.md` — AST requirements
- `spec/12_validation.md` — semantic validation rules
- `spec/20_compiler.md` — compiler architecture
- `spec/30_runtime.md` — runtime specification
- `spec/50_event_system.md` — event lifecycle and task queue
- `spec/51_state_system.md` — state machine transitions
- `spec/60_agent_sdk.md` — agent integration SDK
- `spec/61_llm_agent_integration.md` — LLM integration and MCP protocol reference
- `spec/70_examples.md` — iteration traces for Examples 2, 3, 4
- `spec/80_acceptance_tests.md` — test requirements
- `spec/90_nonfunctional.md` — dependencies, build/run reference, Docker, configuration
- `spec/99_changelog.md` — implementation changelog (v0.1.0 through v0.12.93)
