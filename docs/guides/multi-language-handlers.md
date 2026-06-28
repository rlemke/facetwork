# Multi-language (polyglot) handlers

Facetwork cleanly separates two roles:

- **The runtime** (Python) owns *orchestration* — parsing FFL, the step state
  machine, dependency-driven step creation, retries, and resume.
- **Handlers** own the *work* an event facet does — an API call, a data
  transform, a routing computation. A handler can be written in **any
  language**, because the runtime and the handlers never call each other
  directly: they coordinate entirely through **MongoDB**.

That decoupling is what makes a Java, Go, Scala, or TypeScript handler a
first-class citizen of the fleet, running side by side with the Python ones.

## When to reach for another language

Write a handler in another language when the *work* is best done there — not as
a matter of taste:

- **A library lives in that ecosystem.** Embedded GraphHopper / JTS / Lucene
  (JVM), a Go binary you already ship, a TypeScript SDK, native bindings.
- **Team expertise / existing code.** A service team owns the logic in their
  language and wants to expose it as an event facet without rewriting it.
- **Performance.** A hot path that benefits from the JVM/Go runtime.

The FFL workflow stays the same; only the handler implementation changes
language. A common shape is **Python plans, another language computes** — e.g.
a Python facet assembles work items and a JVM facet crunches them.

## The contract (language-agnostic)

An external agent processes one event task with six MongoDB steps — the same
whether it's Python, Java, or Go (`agents/protocol/README.md`,
`agents/protocol/constants.json`):

1. **Claim** an event task — atomic `findOneAndUpdate` on the `tasks`
   collection, `pending → running`, filtered by **task name** (the facets you
   handle) **and `task_list_name`**.
2. **Read step params** from the `steps` collection via the task's `step_id`.
3. **Do the work.**
4. **Write return attributes** back onto the step document.
5. **Mark the task `completed`.**
6. **Insert an `fw:resume` task.** The Python `RunnerService` picks it up,
   validates the transition, and advances the workflow.

The agent never runs the evaluator or touches workflow state — it just does its
facet's work and signals resume. Everything else is the Python runtime's job.

## How polyglot handlers coexist (no contention)

Task claiming is a single atomic, **name-filtered** `claim_task`. Each agent
advertises only the facets it `register`s, so:

- **Different facets → disjoint pools.** A Java agent advertising
  `osm.Routing.GraphHopper.RouteBatch` and the Python osm runners advertising
  everything else issue *different* Mongo queries; neither ever claims the
  other's tasks. They run on the same fleet, against the same Mongo + MinIO,
  with zero coordination beyond the atomic claim.
- **Routing is by namespace.** A task's `task_list_name` is derived from the
  facet's top-level namespace, and an agent polls the namespaces of the facets
  it loaded. So a `RouteBatch` task (namespace `osm`) is claimed by any runner
  polling `osm` **that also has that handler** — which, since only the Java
  agent registered it, is the Java agent.

You do **not** register a handler in two languages. Each facet has exactly one
handler, in one language, advertised by whatever process loaded it.

## SDKs

Each SDK wraps the six-step protocol behind a `register(facetName, handler)` +
poll loop. Pick your language's library, or implement the protocol directly from
`agents/protocol/constants.json` with any MongoDB driver.

| Language   | Location                      | Notes |
|------------|-------------------------------|-------|
| Python     | built-in (`facetwork.runtime`) | Reference; full evaluator integration (RegistryRunner / AgentPoller). |
| Java       | `agents/java/fw-agent/`        | `AgentPoller` + `Handler`; Maven `afl:fw-agent`. Validated end-to-end by `osm-gh-router` (below). |
| Go         | `agents/go/fw-agent/`          | `poller.go` / `mongo_ops.go`. |
| TypeScript | `agents/typescript/fw-agent/`  | `poller.ts` / `registry-runner.ts`. |
| Scala      | `agents/scala/fw-agent/`       | Lightweight; delegates resume to the Python RunnerService. |
| any other  | `agents/protocol/` + `agents/templates/` | `constants.json` + a starter `CLAUDE.md` to build an agent from scratch. |

### Java quickstart

```java
import afl.agent.AgentPoller;
import afl.agent.AgentPollerConfig;
import java.util.Map;

AgentPoller poller = new AgentPoller(config);          // mongo url, db, task list
poller.register("ns.MyFacet", params -> {
    Object input = params.get("input");                // step params (the facet args)
    return Map.of("result", compute(input));           // return clause -> step returns
});
poller.start();                                        // claim/work/return/resume loop; blocks
```

The poller injects helpers into `params`: `_step_log` (a `(message, level)`
callback for dashboard step logs), `_facet_name`, and `_update_step` (stream
partial results).

## Deploying a polyglot handler

A non-Python handler is its own process (a JVM, a Go binary, a Node process)
configured with the **same `FW_*` environment the Python runners use** — at
minimum `FW_MONGODB_URL` and, if it touches durable storage, the `FW_S3_*`
endpoint/credentials. Then it joins the fleet by registering its facet(s) and
polling.

- **Dev / single box:** run it directly alongside the Python runner(s).
- **Fleet:** package it as its own container image and run it as an additional
  fleet role (a sibling to the per-example Python runners). One such agent
  process is usually enough — it advertises only its facet, so all of that
  facet's tasks route to it; scale it like any other runner if needed.

Because coordination is only the atomic Mongo claim, a polyglot agent on a box
with Mongo + MinIO access *is* a fleet participant — see
[informal-fleet.md](../operations/informal-fleet.md).

## Worked example: embedded GraphHopper (Java) + Python

`osm-gh-router` (`fwh_osm/java/osm-gh-router/`) is the reference polyglot
handler. The pipeline:

```
osm.Routing.PlanCityPairs   (Python) — turn >100k-pop city centroids into O/D pairs
osm.Routing.GraphHopper.RouteBatch  (Java) — route each pair over a state's graph
osm.viz.RenderMap           (Python) — draw the routes
```

The Java agent (built on `fw-agent`'s `AgentPoller`) registers **only**
`RouteBatch`, polls the `osm` task list, loads a state's GraphHopper graph into
its **own JVM** as an embedded library (no routing daemon), keeps it warm per
region, routes each pair in-process, and finalizes the routes GeoJSON to MinIO.
It runs next to the Python `osm-geocoder` runners on the same fleet; the
name-filtered claim keeps the two from stepping on each other.

### Lessons worth copying

These bit during the first polyglot build and are generic to JVM/native agents:

- **Read numeric BSON fields defensively.** Mongo may store a number as int32
  or int64; a hard `getLong()` throws `ClassCastException` on an `Integer`. Use
  `((Number) v).longValue()`.
- **Catch `Throwable`, not just `Exception`, around the heavy library call.** A
  `NoClassDefFoundError`/`LinkageError` from a shaded jar would otherwise kill
  the worker thread silently and leave the task stuck `running`. Convert it to a
  clear task failure so the runtime can retry.
- **Pin transitive dependency versions in a fat jar.** A library (e.g. AWS SDK)
  pulled an older Jackson that won the merge and broke GraphHopper's loader
  (`NoClassDefFoundError: StreamConstraintsException`). Use a BOM / dependency
  management to force one version.
- **Engine artifacts are version- and config-specific.** A GraphHopper graph
  built by one tool/config won't load under a differently-configured engine
  ("Profiles do not match"). Build/import with the *same* code and config you
  load with — the simplest guarantee is to have the handler build (import) the
  artifact itself and then route, rather than loading one produced elsewhere.
- **Fail loudly on a missing native/runtime dependency** rather than returning
  an empty result — otherwise a misconfigured agent silently produces wrong
  output instead of failing so the task retries on a capable runner.

## See also

- [agent-sdk.md](../reference/agent-sdk.md) — building handlers (Python).
- [agents/protocol/](../../agents/protocol/) — the wire protocol + constants.
- [informal-fleet.md](../operations/informal-fleet.md) — running the fleet on
  your team's own machines.
