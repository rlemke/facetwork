# Future Thoughts: An AI-Native Workflow System

*Forward-looking design exploration, companion to the Facetwork thesis.*

This document captures a design exploration that asks: *if the audience for a workflow language were AI agents rather than humans, and we weren't bound to Python / JVM / any particular host runtime, what would the language, handlers, distributed execution, and UI look like?*

Facetwork today is human-first — FFL is a readable DSL, handlers are Python modules, the dashboard is a human's window into runs. The exercise below inverts that: humans become verifiers, not authors; agents become authors, not just consumers. It is a design exploration, not an implementation plan. No code changes are proposed.

---

## 1. The language

Human languages optimize for **readability, stability, and cognitive chunking**: whitespace, keywords, scoped names, familiar control flow. An agent doesn't need any of that. Agents need:

- **Structural unambiguity.** No parser error recovery, no "did you mean," no context-sensitive indentation. A graph or tree is enough. The "source" should be a canonical serialized IR, not a DSL.
- **Content-addressed immutability.** Every node (step, schema, expression) is identified by a hash of its definition. An agent proposing "the same step as run #4729 but with `region=bavaria`" can express that by reference + delta instead of re-emitting text. Plans become cheap to compose, diff, and cache.
- **Typed holes and refinement.** An agent often knows *shape* before *content*: "a step that produces `RouteFeatures`, implementation TBD." The language should have first-class holes with type constraints, and execution should be able to pause at a hole, ask a planner to fill it, and resume.
- **Effects in the type.** "This step reads PostGIS," "this step spends tokens," "this step is non-idempotent," "this step costs money" — encoded as effect rows, not discovered from docs. The scheduler and the human verifier both read effects.
- **No sugar.** Mixins, `andThen`, `implicit`, `catch when` — all great for humans, all noise for agents. Desugar everything to: *node, dependency, effect, schema*.

So the "language" is really two things: a **canonical IR** (what executes) and a **projection layer** (what a human sees when verifying). FFL-style surface syntax becomes one of many projections, alongside a graph view, a natural-language paraphrase, and a diff-against-prior-run view.

## 2. Plans as data, stored and fetched dynamically

Agents don't ship code; they **write rows**. A plan is a set of content-addressed nodes in a store (Mongo, Postgres, a Merkle store — doesn't matter). Consequences:

- An agent composes a plan by `INSERT ... ON CONFLICT DO NOTHING` over node hashes. Two agents proposing the same subgraph converge for free.
- A library of **reusable subgraphs** emerges naturally — no package manager, no import paths, just hashes. "Use the OSM-extract-and-render subgraph from run #4729" is a single reference.
- Execution is: walk the DAG, for each ready node look up handler-by-hash, dispatch. The plan can be extended mid-run by an agent appending new nodes downstream of a completed one, as long as dependencies point backward. This is how you get "planned actions known at plan time, exact mechanics at runtime."
- **Speculative branches** are first-class: an agent can emit a plan with three alternative subtrees and a `choose-at-runtime` node whose handler is "ask the planner, given these intermediate results." This subsumes `catch when`, A/B tests, and human approval gates into one primitive.

## 3. Handlers

Today a handler is "a Python function on a runner." For an agent-native system:

- **Handlers are capabilities, not code.** A handler is declared by the effects it provides and the schemas it consumes/produces. The implementation may be a Python module, a container image, a shell command, a SQL query, an HTTP call, a prompt to another model, or a subgraph of other handlers. The runtime picks an implementation at dispatch time based on cost, latency, availability, and trust.
- **Handler selection is itself a scheduled decision.** "Extract routes from PBF" could be served by osmium locally (fast, cheap, trusted) or by a Claude prompt (slow, expensive, flexible). The scheduler picks; the plan doesn't hardcode.
- **Sandboxing by effect, not by language.** A handler declaring `reads: postgis` gets a read-only connection injected; one declaring `spends: tokens<=5000` gets a token-budgeted client. The runtime enforces; the handler doesn't have to be careful.
- **Idempotency as a type.** Non-idempotent handlers are opt-in and must declare a compensation. The runtime refuses to auto-retry anything not marked idempotent — no more "did that retry double-charge the user?"

## 4. Distributed execution

Most of Facetwork's runtime is already aligned with what's needed — leases, heartbeats, reapers, drain, repair. The agent-native shifts are:

- **The scheduler is a first-class agent, not a thread pool.** It reads the plan, open holes, current resource state, budget, and deadlines, and emits dispatch decisions. It can itself be paused, resumed, and replaced mid-run.
- **Every state transition is an append to a log**, and the log is the source of truth. Current execution state is a fold over the log. This makes "re-run from here," time-travel debugging, and cross-run analysis uniform — they're all queries against the log.
- **Resource budgets are explicit and hierarchical.** A plan declares "≤ \$5, ≤ 10min, ≤ 100k tokens, ≤ 3 retries per node." The scheduler enforces. Running out of budget pauses the plan and notifies the verifier. No surprise bills.
- **Cross-plan composition.** A step can *await* another plan's output by hash. This replaces "workflow triggers workflow" with a single substrate.

## 5. Human verification mode

This is the load-bearing piece. If agents write plans and pick handlers at runtime, how does a human stay in control?

- **Verification is a projection over the plan + budget + effects**, not over code. The human sees: "this plan will read PostGIS (read-only), spend up to \$2 in Claude tokens, write 3 files under `/data/osm/bavaria/`, and take ~8 minutes. Here's the natural-language paraphrase. Here's the graph. Here's the diff against the last approved plan of this shape."
- **Approve at the level of effects and budgets**, not at the level of nodes. "I approve any plan that reads-only from PostGIS, spends ≤ \$2, and writes only under `/data/osm/`" becomes a reusable policy. The scheduler checks plans against policies; only policy-violating plans escalate.
- **Runtime-known mechanics are fine if the effects are bounded.** The human doesn't need to know *which* SQL query will be generated, only that whatever is generated will be read-only and will time out at 30s. This is the key unlock: the human verifies **envelopes**, the agent fills in **contents**.
- **Pause points are declared in the plan**, not bolted on. A node can declare "requires human confirmation before dispatch" with a natural-language summary. The verifier sees a queue of such confirmations, not a firehose of every step.
- **Explain-then-act is the default for novel plans.** First run of a new plan shape: dry-run, show effects, ask. Subsequent runs within policy: auto.

## 6. UI interaction

The dashboard today is step-centric because humans author step-by-step. For an agent-native system:

- **Primary view: the plan, not the run.** A graph/outline of what's going to happen, with effect and budget annotations, and a paraphrase. Runs are instances of plans; the plan is the noun.
- **Secondary view: the policy inbox.** Plans awaiting approval, plans that broke budget, plans that hit a declared pause point. Everything else runs without interrupting.
- **Tertiary view: the log explorer.** Queries over the append-only log — "show me all plans that touched this table in the last week," "diff this run's effects against the last approved run of the same shape."
- **No "New Run" button for humans.** Humans write *intents* ("geocode Bavaria, render amenities"); a planning agent proposes a plan; the human verifies the envelope; execution starts.

## 7. Consequences and tradeoffs

- **Debuggability shifts.** "Read the code" stops being the first move. "Read the plan, read the log, read the policy" replaces it. Tooling has to make those as fluent as a stack trace.
- **Trust boundary moves up.** You no longer trust handlers to be well-behaved — you trust the effect system and the sandbox. Bugs in the effect system are catastrophic in a way that bugs in a single Python handler are not.
- **Human skill shifts.** Authoring FFL → writing policies, reviewing envelopes, debugging plans. This is closer to an SRE / compliance role than to a programmer role.
- **Dead weight to drop.** Surface syntax sugar, handler discovery by import path, per-handler retry configuration, most of the dashboard's step-level affordances. All replaced by plan + policy + log.
- **The biggest risk** is the effect system being wrong or under-specified. If "reads PostGIS" doesn't actually constrain what a handler does, the whole verification story collapses. This is where most of the engineering goes.

## 8. Relationship to Facetwork today

Most of the existing runtime infrastructure survives: the task/lease/heartbeat substrate, the runner fleet, the step log, the repair machinery. What changes is the layer above:

- FFL stays as one projection (the human-readable one), but the IR becomes canonical and content-addressed.
- Handlers gain effect declarations and become capability-typed.
- The scheduler becomes pluggable and agent-driven.
- The dashboard grows a plan view, a policy inbox, and a log explorer; the step-detail page becomes a debugging tool, not the primary UI.

A plausible incremental path: add effect annotations to existing event facets → add a policy language → add a content-addressed IR alongside FFL → add a planning agent that emits IR → move the dashboard's primary view to plans. Each step is useful standalone; the endpoint is the agent-native system.

---

*This is a design exploration, not an implementation plan. No code changes are proposed.*
