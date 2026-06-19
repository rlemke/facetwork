# `/ffl-first` — fulfill a request as a reviewed FFL workflow

`/ffl-first` is a Claude Code command (`.claude/commands/ffl-first.md`) that makes Claude
accomplish a domain request **as an FFL workflow on the Facetwork runtime** — discovering and
reusing what already exists, and **showing you anything new before it runs**. It is the
"do it the right way" entry point: never hand-rolled shell/Python/osmium/curl, never a local data
mirror, never a hardcoded region list.

## When to use it

Any request that should become a workflow — maps, routing, spatial analysis, data ingestion,
multi-region pipelines. Examples:

```
/ffl-first map the interstate routes between US cities over 1 million
/ffl-first hospital deserts in California — places over 10 miles from a hospital
/ffl-first routes between European cities over 1M
```

## What it does (the protocol)

1. **Fulfill it as a workflow.** The deliverable is an FFL workflow run on the runtime — not an
   ad-hoc script. If raw code ever seems necessary, that means a capability is missing (step 5),
   not that FFL should be bypassed.
2. **Discover before building — and show the candidates.**
   - `fw_catalog_match` (NL → `reuse`/`review`/`author_new` verdict + the candidate's
     `param_schema`) and `fw_catalog_search` find an existing workflow.
   - `fw_capabilities` (NL → facet, with effect/cost) finds the primitives to compose.
   - `osm.Vocab.ResolveTag` maps a natural-language term to an OSM `key=value`.
   Claude reports what it found before deciding.
3. **Reuse if it exists** — fill the workflow's parameters and run it.
4. **Author — Gate A** — if nothing fits, compose a new workflow from existing facets, validate it
   (`fw_validate` / compile against the library), then **show you the full FFL + a short rationale
   and wait for your approval before running it.**
5. **Missing primitive — Gate B** — if a needed facet doesn't exist, scaffold it with
   `fw ffl scaffold` and **show you the handler implementation for comments before it is
   deployed or used.** Raw `osmium`/`curl`/Python is never a workaround; if it seems necessary,
   Claude stops and names the missing FFL capability.
6. **Data acquisition is always FFL** — regions are fetched and enumerated via
   `osm.cache.Download` + the region FFL (`osm.Region.ListRegions`, `osm.Region.ResolveRegion`).
   Never a local mirror (`~/osm-data`), never a hardcoded country list, never manual downloads.
7. **Run it (after approval) and report** — submit via `fw ffl run` (below) and report the
   results plus honest limitations (unreachable nodes, fragmented networks, regions that failed to
   download, excluded data).

## The two review gates (the defining behavior)

- **Gate A:** the workflow is shown — full FFL + rationale — **before it is run**.
- **Gate B:** any new handler implementation is shown **before it is used**.

Each gate pauses for your comments. This is what distinguishes `/ffl-first` from "just go run
something": nothing new executes until you've seen it.

## `fw ffl run` — the run mechanism (step 7)

`/ffl-first` runs the approved workflow with **`fw ffl run`**, a thin wrapper over
`python -m facetwork.runtime.submit`:

```bash
fw ffl run workflow.ffl --workflow ns.WorkflowName
fw ffl run --primary wf.ffl --library types.ffl --workflow ns.Name --inputs '{"x": 1}'
fw ffl run wf.ffl --workflow ns.Name --task-list osm
```

It compiles the FFL, validates it, and creates the flow + workflow + **runner record** + the
`fw:execute` bootstrap task, then hands off to the runner fleet.

| | `fw ffl run-workflow` | **`fw ffl run`** |
|---|---|---|
| Input | a pre-seeded workflow, by name | **an `.ffl` file** (compiles it) |
| Execution | in-process evaluator | submits → **runner fleet** executes |
| Runner record | ✗ — **not** dashboard-visible | ✓ — **dashboard-visible** |
| Requires a runner up | no | yes (`fw runner start …`) |

Because `ffl-run` creates the runner record, the run appears in the dashboard's **Runs** list
(http://localhost:8080/v3/workflows) for live tracking — the same submission path the dashboard's
"New run" button uses.

## See also

- [Claude workflow catalog](../architecture/claude-workflow-catalog.md) — store/version/run
  LLM-authored FFL; `fw_catalog_*` MCP tools used in step 2/3.
- [Composable facet library](../architecture/composable-facet-library.md) — `fw_capabilities`,
  effect/cost, the primitive taxonomy composed in step 4.
- [Extending with new handlers](../architecture/extending-with-new-handlers.md) — the
  detect-gap → `fw ffl scaffold` flow behind Gate B (step 5).
