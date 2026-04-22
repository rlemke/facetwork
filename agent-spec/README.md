# Agent-first specifications

Files in this directory — and files throughout the repo ending in
`.agent-spec.yaml` — are the canonical descriptions of each component's
behavior, shape, side effects, cache semantics, failure modes, concurrency
guarantees, cost model, and invariants.

**Audience:** AI agents generating, validating, composing, or porting
implementations. Human-readable by design, but optimized for machine
ingestion: structured, ambiguity-free, effect-complete, content-addressed.

**Relationship to source code.** Under this convention, the
`.py` / `.sh` / `.ffl` files in this repo are committed artifacts of these
specs plus operational choices (language, framework, log format, CLI flag
ordering). Any regenerated implementation that satisfies the spec and
passes its property assertions is equivalent. Today we keep the committed
impl around because it's faster to step-debug, captures operational
details the spec doesn't, and is the only artifact humans can directly
modify in a PR. Over time the weight shifts: specs become the primary
artifact, impls become a deterministic projection.

**Relationship to [`docs/`](../docs).** `docs/` is for humans — tutorials,
rationale, walkthroughs, design history, thesis material. `agent-spec/`
is the ground truth for *what the system does*, in machine-checkable
form. Each spec file has a `human_docs:` field linking back to the
relevant narrative docs so the two layers cross-reference rather than
duplicate.

## File layout

Two places specs live:

1. **Cross-cutting system specs** — in this directory. These describe
   contracts that span multiple components: the manifest schema every
   cache type follows, the Storage abstraction, the cache-invalidation
   dependency graph between tools, shared vocabularies.

2. **Per-component specs** — co-located with the source. Each tool,
   handler module, or library file that has a distinct external behavior
   carries a sibling file named `<basename>.agent-spec.yaml`. Example:

   ```
   examples/osm-geocoder/tools/_lib/
       pbf_download.py              ← the committed implementation
       pbf_download.agent-spec.yaml ← the spec it satisfies
   ```

Crawl all specs with:

```bash
find . -name '*.agent-spec.yaml' -o -path '*/agent-spec/*.yaml'
```

## Index of current specs

### System-level (this directory)

| Spec | Kind | Describes |
|---|---|---|
| [facetwork](facetwork.agent-spec.yaml) | system-contract | Umbrella: product overview, component map, system-wide invariants, non-goals. Start here. |
| [ffl-language](ffl-language.agent-spec.yaml) | language-spec | FFL surface language — lexical rules, grammar, AST node shapes, scoping, types, composition (mixins / andThen / foreach / catch / prompt / script), validation rules, JSON emission. |
| [ffl-compiler](ffl-compiler.agent-spec.yaml) | library | The 6-stage compiler pipeline (preprocess / parse / transform / resolve / validate / emit), the `afl` CLI, error contract, determinism requirements. |
| [runtime](runtime.agent-spec.yaml) | system-contract | Single-process execution — workflow/block/step/task/event model, the 24-state step state machine, iterative fixed-point evaluation, yield merging, block creation, catch blocks, schema instantiation, persistence contract. |
| [runtime-distributed](runtime-distributed.agent-spec.yaml) | system-contract | Multi-runner coordination — atomic task claim, lease renewal via heartbeats, reaper for dead runners, stuck-task watchdog, per-step atomic processing with continuation events, runner lifecycle, workflow repair. |
| [agent-sdk](agent-sdk.agent-spec.yaml) | system-contract | Handler authoring — four execution models (RegistryRunner / AgentPoller / RunnerService / ClaudeAgentRunner), payload and response contract, registration paths, reserved task prefixes, multi-language libraries. |
| [manifest-schema](manifest-schema.agent-spec.yaml) | system-contract | JSON manifest format every cache type uses — file shape, entry baseline fields, read-modify-write protocol, forward-compat rules, per-cache-type extensions. |
| [storage](storage.agent-spec.yaml) | library | Two-backend abstraction (local POSIX filesystem / HDFS WebHDFS), method signatures, backend selection, finalize-from-local semantics, xattr handling. |

### Per-component (co-located with source)

| Spec | Location | Describes |
|---|---|---|
| [pbf_download](../examples/osm-geocoder/tools/_lib/pbf_download.agent-spec.yaml) | `examples/osm-geocoder/tools/_lib/` | Geofabrik PBF downloader — MD5 verification, manifest-based caching, thread-safe per region. Pilot spec demonstrating the format on a concrete tool. |

More per-component specs will appear alongside other tools (`pbf_extract`, `graphhopper_build`, `valhalla_build`, `osrm_build`, `vector_tiles_build`, `html_render`, etc.) as each is formally specified.

### Reading order for a rebuild

An agent tasked with rebuilding Facetwork from scratch should ingest specs in this order:

1. [`facetwork`](facetwork.agent-spec.yaml) — understand the system scope and invariants.
2. [`ffl-language`](ffl-language.agent-spec.yaml) — the language that drives everything.
3. [`ffl-compiler`](ffl-compiler.agent-spec.yaml) — how source becomes the executable JSON AST.
4. [`runtime`](runtime.agent-spec.yaml) — how the JSON AST executes (single-process model).
5. [`runtime-distributed`](runtime-distributed.agent-spec.yaml) — how the model scales across runners.
6. [`agent-sdk`](agent-sdk.agent-spec.yaml) — how external code plugs into the runtime.
7. [`manifest-schema`](manifest-schema.agent-spec.yaml) and [`storage`](storage.agent-spec.yaml) — the cache substrate used by the tool set and handlers.
8. Per-component specs as needed for specific tools.

The authoritative human-prose references (`docs/reference/*.md`) are linked from each spec's `human_docs:` field and contain fuller grammars, transition tables, and narrative rationale that the YAML deliberately does not try to duplicate.

## File format

YAML. Flat-ish structure. Fields below; `?` marks optional.

```yaml
identity:
  name: string              # canonical human-readable name, stable across renames
  kind: tool | library | handler | ffl-facet | schema | system-contract
  canonical_id: string      # sha256:<hex> — content hash of this spec (set by tool,
                            # or recorded manually on publication)
  version: int              # monotonic; bump on every breaking change
  supersedes: [string]?     # canonical_ids of prior versions

purpose: |
  One-paragraph description. First sentence should stand alone — agents
  may ingest only that when short on context.

inputs:
  <field_name>:
    type: string | int | float | bool | path | hex_string | enum | array | struct
    description: string
    constraint: string?     # regex, range, or predicate
    default: any?
    examples: [any]?

outputs:
  <field_name>:
    type: ...
    description: string
    shape: string?          # for structs/arrays, inline schema or $ref to another spec

side_effects:
  network: [string]?        # "HTTP GET <url-pattern>", "DNS lookup", etc.
  filesystem:
    reads: [string]?
    writes: [string]?       # paths / glob patterns; note atomicity
    deletes: [string]?
  subprocess: [string]?     # external binaries invoked (osmium, ogr2ogr, ...)
  stdout: string?           # what gets written to stdout (structured? log?)
  stderr: string?

cache_validity:             # for any component that caches
  keyed_on: [string]
  skip_when: string         # boolean predicate
  invalidates: [string]?    # canonical names of downstream specs whose caches
                            # should be treated as stale when this produces new output

failure_modes:
  - kind: string            # enum: upstream_404, corrupt_input, timeout, permission_denied, ...
    trigger: string
    behavior: string        # how the impl handles it (retry, fail-fast, skip, log, ...)
    user_visible: string    # what the caller sees (exit code, exception, partial result)

concurrency:
  threads: safe | safe_per_<key> | unsafe
  processes: safe | unsafe
  parallelism_limit: string?  # "1 per IP", "RAM-bound", etc.
  rationale: string?

cost_model:                 # order-of-magnitude is enough
  cpu: negligible | low | high | O(N)
  memory: string
  disk: string
  network: string
  wall_clock: string?       # representative numbers for a known input size

properties:                 # invariants any conforming impl must satisfy
  - name: string
    statement: |
      natural-language or pseudo-code assertion.
    checkable_via: property_test | example_suite | formal_proof | manual_inspection

dependencies:
  libraries: [string]?      # Python modules, external packages
  tools: [string]?          # other agent-specs by canonical name
  binaries: [string]?       # osmium, gdal, java, etc.
  environment: [string]?    # env vars consulted

implementation_notes: |
  Free-form notes about operational details the spec doesn't constrain but
  that existing impls care about. Not part of the contract.

human_docs:
  - <path/to/README.md>
  - <path/to/docs/section.md#anchor>
```

## Adding a new spec

1. Pick the file it describes. For a new tool, write the spec *first*;
   for an existing tool, write the spec to match current behavior.
2. Fill in every section. `N/A` is a valid value — explicitly saying
   "this has no network effects" is more useful than omitting the field.
3. Link back to human docs via `human_docs:`.
4. Compute a content hash of the spec and record it in
   `identity.canonical_id`:

   ```bash
   sha256sum pbf_download.agent-spec.yaml | awk '{print "sha256:" $1}'
   ```

   Only update `canonical_id` when the spec changes intentionally; a drift
   means someone edited the spec without bumping the hash, which is a
   review red flag.
5. Bump `identity.version` when making a breaking change. Record the
   previous `canonical_id` under `supersedes:`.

## Validating a spec against its impl

Each property in `properties:` should be paired with an executable check
— a property test (`hypothesis`), an example-suite assertion, or a
reference to a formal proof. The `checkable_via` field tells a CI agent
which mode to run. Unchecked properties are aspirational, not binding.

## What this is *not*

- **Not documentation.** `docs/` still covers narrative, rationale,
  tutorials. Specs are for machine ingestion and implementation
  regeneration, not for onboarding a new human contributor.
- **Not OpenAPI or JSON Schema.** Those describe data shapes; specs here
  describe full behaviors, including side effects and invariants.
- **Not formal verification yet.** The `properties:` field admits
  property-test or example-suite checking in addition to formal proofs,
  so specs are useful long before the whole codebase is formally
  verified. But nothing here prevents escalating specific properties to
  TLA+ / Dafny / Lean when the payoff justifies it.
