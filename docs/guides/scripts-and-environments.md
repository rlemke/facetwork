# Python Scripts and Environments — and When to Use Them vs Handlers

Facetwork gives an event facet two ways to have an implementation:

- a **handler** — a Python module in a domain package, deployed in the runner
  image, claimed and executed through the task queue with the full agent SDK;
- a **script** — inline Python inside the FFL declaration itself, executed in
  a sandboxed subprocess, optionally bound to a named **environment** that
  supplies its libraries.

They are not interchangeable styles; they are **tiers with different
capabilities**. This guide gives the decision rule, the mechanics of each,
and a worked production migration. Design rationale:
[script-environments.md](../architecture/script-environments.md); language
reference: [grammar.md](../reference/language/grammar.md).

## 1. The decision rule

**Handlers are the capability tier. Scripts are the logic tier.** Ask, in
order:

| Question | If yes → |
|---|---|
| Does it read or write files, caches, or object storage (paths, sidecars, S3)? | **Handler** |
| Does it run an external binary (osmium, tippecanoe, ffmpeg…)? | **Handler** |
| Can it run longer than a task lease (~5 min) or need progress heartbeats? | **Handler** |
| Does it need the agent SDK — `fetch_step`, `_step_log`, per-handler retry/circuit-breaker tuning? | **Handler** |
| Is it a pure function over its parameters, finishing in well under the lease? | **Script** |
| …and does it need third-party libraries? | **Script `in environment`** |
| …stdlib only? | **Plain script** (default environment) |

The sandbox *enforces* this split rather than trusting convention: scripts
cannot import `os` or `subprocess`, cannot touch the filesystem or network
(except through an environment-declared library that does), receive only
JSON-serializable `params`, and run under the task timeout with no lease
renewal. If a facet fights those limits, it is telling you it belongs in the
capability tier.

A useful shortcut: facets already annotated `with Effect(kind = "pure")` and
`with Cost(tier = "cheap")` are the natural script candidates —
`fw_capabilities(effect="pure", max_cost="cheap")` enumerates them.

## 2. Why move logic to scripts at all

Three concrete wins, all verified in production (§6):

1. **Zero-deployment updates.** Script code lives in the flow and rides its
   compiled snapshot. Publish a revision and the new logic runs on the next
   submission — no image rebake, no fleet rollout, no container restart.
   Handler changes require all three.
2. **Fewer tasks.** A plain script facet executes *inline* during step
   processing — no task is created, claimed, or continued. In fan-out-heavy
   workflows the per-leaf handler round-trips dominate the continuation
   cascade; removing them shrinks both latency and the surface where
   liveness problems live.
3. **Fewer dedicated runners.** Handler facets need a runner with that
   handler loaded, which is why each domain runs its own runner container.
   Script facets need either nothing special (default environment) or any
   runner *providing* their environment — capacity consolidates onto generic
   runners.

## 3. Writing a script facet

```ffl
namespace geo {
    /** Pure fan-in: score a category from routed distances. */
    facet CategoryScore(distances: [Double], count: Long) => (score_json: String)
        with Effect(kind = "pure") with Cost(tier = "cheap")
        script {
import json
ds = sorted(d for d in (params.get('distances') or []) if d >= 0)
nearest = ds[0] if ds else None
result['score_json'] = json.dumps({'nearest': nearest, 'count': params.get('count')})
        }
}
```

The contract:

- **`params`** — the facet's input parameters, as a dict of
  JSON-serializable values. **`result`** — write your return values here;
  keys must match the declared returns.
- **Braces take RAW code.** `script { result['x'] = 1 }` is code;
  `script { "result['x'] = 1" }` is a *string expression that does nothing*
  and fails silently. The one-line string form `script "…"` also exists;
  prefer braces for anything non-trivial.
- **Imports** are allowlisted: a fixed stdlib set (`json`, `math`, `re`,
  `statistics`, `datetime`, `collections`, `itertools`, `functools`,
  `hashlib`, `copy`, `string`) plus — for env-bound scripts — the
  environment's declared packages. `os`, `subprocess`, `open` are blocked.
- **Timeout**: the task's timeout (or 30s default) via subprocess kill. No
  heartbeat — stay well under the lease.
- **Placement**: a plain (env-less) script runs *inline* on whichever runner
  processes the step — fine, because it can only compute. An env-bound
  script **defers**: it becomes an environment-routed task executed on a
  runner providing that environment, under that environment's interpreter.

Canonical template:
[`examples/canonical/11-environment-script.ffl`](../../examples/canonical/11-environment-script.ffl).

## 4. Environments — when the script needs libraries

Declare the dependency set once, inside a namespace:

```ffl
environment PyGeo {
    language = "python",
    requires = ["shapely==2.0.4", "networkx==3.3"]
}

facet Cluster(points: Json) => (clusters: Json)
    in environment PyGeo
    script {
import json, shapely
...
    }
```

What happens, end to end:

- **Publish** resolves `requires` into exact pins and content-addresses them
  as a *manifest hash* — re-running the flow next month uses the versions it
  was published with, not whatever PyPI serves that day. Exact (`==`) pins
  resolve without network.
- **Placement** — the script's tasks carry the hash; only runners
  *advertising* it can claim. There is no wrong-host failure mode and no
  claim churn: non-providers never see the task.
- **Provisioning** — two paths, both automatic:
  - *Baked*: the image build sweeps all baked FFL for environment
    declarations and materializes each as a venv (`facetwork.envbake`;
    host-side: `fw ffl bake-envs [--check]`). Every runner provides the
    fleet's declared environments from second one.
  - *Lazy*: an environment published **after** the image was baked is built
    on demand — runners scan pending env-tagged tasks, materialize the venv
    from the flow's frozen manifest, re-register, and claim. Kill switch:
    `FW_ENV_LAZY=off`; failures back off 10 minutes per hash.
- **Imports** — the environment's declared packages join the script's import
  allowlist; the declaration is the review gate.
- `language = "java" | "scala" | …` environments are *provided-only* by
  foreign-runtime agents (the polyglot agent protocol) — no venvs.

Don't reach for an environment when the script is stdlib-only: a plain
script is strictly better (inline, no task at all).

## 5. What stays a handler — with reasons

| Facet type | Why it must be a handler |
|---|---|
| Extracts, downloads, cache reads/writes | File/S3 I/O and the sidecar cache protocol are blocked in the sandbox |
| Tiling, routing-graph builds, media processing | External binaries; multi-minute runtimes need heartbeats |
| Renderers writing output artifacts | Storage writes via `resolve_output_dir`/finalize |
| Anything needing `fetch_step`, step logs, tuned retries/timeouts | Agent SDK is handler-side |
| Long-running work of any kind | Scripts have no lease renewal — the reaper will take the task back |

Handlers keep their own guides:
[agent-sdk.md](../reference/agent-sdk.md),
[long-running-handlers.md](long-running-handlers.md),
[multi-language-handlers.md](multi-language-handlers.md).

## 6. Worked example — the osm.emergency migration

The emergency atlas had nine event facets. A capability audit split them:

- **Stayed handlers (5)**: `TopCities`, `BuildCategorySet`,
  `NearestCandidates` (read GeoJSON layers), `RegionReadiness` (writes the
  region layer), `RenderAtlas` (renders HTML).
- **Became inline scripts (4)**: `CategoryMetrics`, `CityReadiness`,
  `RankRegions`, `RegionFailure` — pure functions over params, stdlib only.

Verified on a full 25-region continental run (fleet v93): **identical
rankings** to the handler implementation, **zero tasks** for the migrated
facets, and **~23% fewer tasks overall** (3,266 vs 4,223) — every
city×category metrics round-trip vanished from the continuation cascade,
and the failed-region marker now runs task-free even inside the region's
`catch`. Scoring changes now ship by publishing the flow.

The migration tests execute *the actual FFL script code* through the
runtime's `ScriptExecutor` — extract the script from the parsed flow and
assert on its results, so there is one source of truth and real coverage.

## 7. Gotchas

- **`script { "…" }` is a silent no-op** — braces take raw code (§3). This
  is the most common authoring mistake.
- **Everything through JSON**: params and results must be JSON-serializable;
  step references arrive as their serialized form.
- **A brand-new namespace with no handlers anywhere** routes its `fw:execute`
  bootstrap to the `default` list automatically (submit detects it and
  prints a note) — pure-script flows just work, but know why the note
  appears.
- **Bind scripts to environments only for dependencies**, not for placement
  control — placement belongs to server groups and (for handlers) task-list
  routing.
- **Validator rules** will catch the rest: `ENV_UNKNOWN`,
  `ENV_MISSING_LANGUAGE`, `ENV_LANGUAGE_SCRIPT_MISMATCH`, `ENV_AT_TOP_LEVEL`
  — each with a paired docs page under
  [docs/reference/rules/](../reference/rules/).
