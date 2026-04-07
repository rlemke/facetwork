## Implementation Constraints (Mandatory)

### Terminology

- **AgentFlow**: The platform for distributed workflow execution (compiler + runtime + agents)
- **AFL**: Agent Flow Language — the DSL for defining workflows (`.afl` files)
- **AFL Agent**: A service that polls the task queue for event facet tasks, performs the required action (API call, data processing, etc.), writes the result back to the step, and signals the workflow to continue. Agents can be built using `AgentPoller` (callback-based), `RegistryRunner` (persistence-based auto-loading), or `RunnerService` (distributed orchestration). The **recommended approach** is `RegistryRunner`: register handler implementations in the database via `register_handler()` or the MCP `afl_manage_handlers` tool, then start the runner service — it dynamically loads and dispatches handlers without requiring custom agent code. Multiple agents can run concurrently, each handling different event facet types.

### Authoring Roles

AgentFlow separates workflow design from handler implementation into distinct authoring roles:

- **Domain programmers** author AFL source (`.afl` files). They define namespaces, facets, event facets, workflows, schemas, and composition logic (mixins, andThen blocks, foreach, when blocks). No Python or handler code is required — the compiled JSON workflow definition is sufficient for the runtime to execute.
- **Service provider programmers** author handler implementations (Python modules) for event facets. A handler receives typed parameters from the task queue, performs the required action (computation, API call, data processing, LLM inference), and returns typed results. Handlers are registered via `register_handler()` or the MCP `afl_manage_handlers` tool and executed by the RegistryRunner.
- **Claude** (or other LLM agents) can author both AFL definitions and handler implementations when given a description of the desired workflow or service behavior. Claude can generate `.afl` files from natural-language requirements, scaffold handler modules with correct signatures and registration, or build complete end-to-end examples including tests.

### Language Requirements

The AFL v1 reference implementation SHALL be written in **Python 3.11+**.

The language parser SHALL be implemented using **Lark**:
- Lark grammar format (.lark)
- LALR parser mode
- Explicit lexer rules
- Line and column error reporting

ANTLR, PLY, Parsimonious, regex-based parsers, or handwritten parsers SHALL NOT be used.

### Implementation Status (v0.10.12)

All specified runtime features are implemented:

| Feature | Spec Reference | Implementation |
|---------|---------------|----------------|
| EventTransmit blocking for event facets | `30_runtime.md` §8.1, `50_event_system.md` §6 | `EventTransmitHandler` blocks for `EventFacetDecl`, passes through for regular facets |
| StepContinue event handling | `30_runtime.md` §12.1, `50_event_system.md` §7 | `Evaluator.continue_step()` resumes event-blocked steps with result data |
| Facet definition resolution | `30_runtime.md` §11.1 | `get_facet_definition()` performs qualified and short-name lookups across the Program AST |
| Statement-level block creation | `30_runtime.md` §8.2, `51_state_system.md` | `StatementBlocksBeginHandler` creates blocks from workflow root, inline statement, or facet-level bodies |
| Nested block AST resolution | `30_runtime.md` §8.3, `51_state_system.md` | `get_block_ast()` resolves workflow root, statement-level, and facet-level block ASTs |
| Multi-run execution model | `30_runtime.md` §10.2, `50_event_system.md` §8 | Evaluator returns `PAUSED` at fixed point with event-blocked steps; `resume()` re-enters the iteration loop |

See `spec/70_examples.md` Examples 2–4 for detailed execution traces demonstrating these features.

---

## Glossary

### Compiler terms
- **AgentFlow**: The platform for distributed workflow execution
- **AFL**: Agent Flow Language — the DSL for defining workflows
- **Facet**: Base declaration type with parameters
- **Event Facet**: Facet that triggers external execution
- **Workflow**: Entry point facet with execution body
- **Mixin**: Composable capability attached to facets/calls
- **Step**: Named assignment in an `andThen` block
- **Yield**: Final output merge statement in a block
- **Schema**: Named typed structure for defining JSON shapes; must be defined inside a namespace; usable as a type in parameter signatures (with qualified name or `use` import)
- **ArrayType**: Array type syntax `[ElementType]` for schema fields and parameters
- **PromptBlock**: Block syntax for LLM-based event facets with `system`, `template`, and `model` directives
- **ScriptBlock**: Block syntax for inline sandboxed Python execution in facets
- **BinaryExpr**: Arithmetic expression node (`+`, `-`, `*`, `/`, `%`) with operator precedence
- **ArrayLiteral**: Array literal expression `[elem, ...]`
- **MapLiteral**: Map literal expression `#{"key": value, ...}`
- **IndexExpr**: Index/subscript expression `target[index]`
- **Provenance**: Metadata tracking where source code originated (file, MongoDB, Maven)
- **Source Loader**: Utility for loading AFL sources from different locations (file, MongoDB, Maven Central)

### Runtime terms
- **StepDefinition**: Runtime representation of a step with state and attributes
- **StepState**: Current execution state (e.g., `state.facet.initialization.Begin`)
- **StateChanger**: Drives step through state machine transitions
- **StateHandler**: Processes specific states (initialization, execution, completion)
- **Evaluator**: Main execution loop; runs iterations until fixed point
- **Iteration**: Single pass over all eligible steps; changes committed atomically
- **DependencyGraph**: Maps step references to determine creation order
- **PersistenceAPI**: Protocol for step/event storage (in-memory or database)
- **EventDefinition**: Domain lifecycle record for external work — tracks what needs to happen, the payload, and outcome (Created → Dispatched → Processing → Completed/Error)
- **TaskDefinition**: Claimable work item in the distributed queue — provides routing, atomic claiming, and locking so runners can compete safely. Created alongside an EventDefinition at EVENT_TRANSMIT; consumed by runners/agents (see `spec/50_event_system.md` §9)
- **RunnerService**: Long-lived distributed process that polls for blocked steps and tasks
- **RunnerConfig**: Configuration dataclass for runner service parameters
- **ToolRegistry**: Registry of handler functions for event facet dispatch
- **AFL Agent**: A service that accepts events/tasks, performs the required action, updates the step, and signals the step to continue
- **AgentPoller**: Standalone polling library for building AFL Agent services without the full RunnerService
- **AgentPollerConfig**: Configuration dataclass for AgentPoller parameters
- **RegistryRunner**: Universal runner that reads `HandlerRegistration` entries from persistence, dynamically loads Python modules, and dispatches event tasks — eliminates the need for custom agent services. Handlers are registered via `register_handler()` or the MCP `afl_manage_handlers` tool and are auto-loaded at runtime.
- **RegistryRunnerConfig**: Configuration dataclass for RegistryRunner (service_name, topics, poll_interval_ms, registry_refresh_interval_ms, etc.)
- **HandlerRegistration**: Persisted mapping of a qualified facet name to a Python module + entrypoint; stored in the `handler_registrations` collection and loaded by RegistryRunner on demand
- **Foreach execution**: Runtime model for `andThen foreach var in expr { ... }` — creates N sub-block steps (one per array element), each with `foreach_var`/`foreach_value` bound and a cached body AST; sub-block completion tracked directly without DependencyGraph
- **Lazy yield creation**: Yield steps are created in the iteration when their dependencies become available, not eagerly in iteration 0; this means total step counts grow over iterations
- **Block AST cache**: `ExecutionContext._block_ast_cache` stores body AST overrides for foreach sub-blocks and multi-block workflows, checked before hierarchy traversal in `get_block_ast()`
- **Multi-block index**: When a workflow body is a list of `andThen` blocks, each block step gets `statement_id="block-N"` so `get_block_ast()` can select the correct body element
- **MCP Server**: Model Context Protocol server exposing AFL tools and resources to LLM agents

### Agent integration library terms
- **Agent Integration Library**: Language-specific library for building AFL agents (Python, Scala, Go, TypeScript, Java)
- **Protocol Constants**: Shared constants (`agents/protocol/constants.json`) defining collection names, state values, document schemas, and MongoDB operations for cross-language interoperability
- **afl:resume**: Protocol task inserted by external agents after writing step returns; signals the Python RunnerService to resume the workflow
- **afl:execute**: Protocol task for executing a compiled workflow from a flow stored in MongoDB
- **Async Handler**: Handler function that returns a coroutine/Promise/Future; supported in Python (`register_async`), TypeScript, and Java
- **Region Resolver**: Pure Python module (`region_resolver.py`) that maps human-friendly region names to Geofabrik download paths using an inverted index of `REGION_REGISTRY`, aliases, and geographic features

### Docker terms
- **Docker stack**: `docker-compose.yml` defining MongoDB, Dashboard, Runner, Agents, Seed, and MCP services
- **Setup script**: `scripts/setup` — bootstraps Docker (install check, image build, service start with scaling)
- **Scalable services**: Runner, AddOne agent, OSM geocoder agents — no `container_name`, support `--scale N`
- **OSM Geocoder (full)**: `Dockerfile.osm-geocoder` — Python osmium, shapely, pyproj, folium + Java JRE + GraphHopper JAR
- **OSM Geocoder (lite)**: `Dockerfile.osm-geocoder-lite` — requests only, no Java or geospatial C libraries
- **GraphHopper JAR**: Downloaded at Docker build time from Maven Central to `/opt/graphhopper/graphhopper-web.jar`

### MCP terms
- **MCP**: Model Context Protocol — JSON-RPC 2.0 protocol for LLM agent ↔ tool server communication
- **Tool**: MCP action endpoint (has side effects); invoked via `tools/call` with name + arguments
- **Resource**: MCP read-only data endpoint; accessed via `resources/read` with a URI
- **stdio transport**: Default MCP transport; server reads/writes JSON-RPC on stdin/stdout
- **TextContent**: MCP response content type; all AFL tools return `TextContent` with JSON payloads
- **inputSchema**: JSON Schema attached to each Tool definition; SDK validates arguments before dispatch
