---
description: Fulfill a request the FFL-first way — discover/reuse existing workflows, gate any new workflow or handler on the requestor's review, fetch data only via FFL, then run it on the runtime. Never hand-roll shell/Python/osmium.
argument-hint: <the request, e.g. "map routes between Asian cities over 5M">
---

# /ffl-first — fulfill a request as an FFL workflow, with review gates

The request: **$ARGUMENTS**

Fulfill it as an **FFL workflow on the Facetwork runtime** — by creating or reusing one,
**not** by hand-rolling shell/Python. Two rules define this command and must never be skipped:

> **Gate A — show the workflow before running it.** If you author a workflow, show the requestor
> the full FFL + a short rationale and **wait for approval** before running.
>
> **Gate B — show any new handler before using it.** If a primitive is missing, scaffold the
> facet+handler and show the requestor the **handler implementation** for comments **before**
> deploying or using it.

Each gate pauses for the requestor's comments. Track the steps with TodoWrite.

## 1. Fulfill it as a workflow
The deliverable is an FFL workflow run on the runtime. Do not satisfy the request with ad-hoc
shell/Python/osmium/curl. If you ever feel you must, that is a signal a capability is missing —
go to step 5, don't bypass FFL.

## 2. Discover before building — and show what you found
Search the existing library first, then report the candidates:
- **`fw_catalog_match`** (NL request → `reuse` / `review` / `author_new` verdict + best candidate's
  `param_schema`) and **`fw_catalog_search`** — is there a workflow that already does this?
- **`fw_capabilities`** (NL → facet, with effect/cost) — which primitives exist to compose?
- (Domain tags: **`osm.Vocab.ResolveTag`** for NL term → `key=value`.)
Show the matched workflow(s) and the relevant facets before deciding.

## 3. Reuse if it exists
If a suitable workflow already exists, fill its parameters and **go to step 6** (run it).
Do not author a new one.

## 4. Author — GATE A
If nothing fits, compose a new workflow from existing facets, then:
1. **Validate** it — `fw_validate` (MCP) or compile against the full FFL library.
2. **Show the requestor the full FFL + a short rationale, and STOP.** Do not run it until they
   approve. Apply their comments and re-validate.

## 5. Missing primitive — GATE B
If a needed facet doesn't exist, do **not** work around it with raw code:
1. Scaffold it — **`fw ffl scaffold`** (review-ready facet + handler + test stubs).
2. **Show the requestor the handler implementation for comments, and STOP**, before
   registering/deploying or using it. Apply feedback, then deploy.
- If raw `osmium`/`curl`/Python seems necessary at any point, **stop and explain which FFL
  capability is missing** rather than proceeding — treat it as a Gate-B item.

## 6. Data acquisition is ALWAYS FFL
- Fetch and enumerate regions via FFL only: **`osm.cache.Download`** + the region FFL
  (**`osm.Region.ListRegions`** to enumerate, e.g. `(continent="Europe", level="country")`;
  **`osm.Region.ResolveRegion`** to resolve a name). The cache facet auto-downloads anything not
  cached.
- **Never** use a local mirror (e.g. `~/osm-data`), **never** hardcode region/country lists,
  **never** use raw `osmium`/`curl`/manual downloads.

## 7. Run it on the runtime (only after approval) and report
- Ensure a runner is up (`fw runner start --example <name> ...`).
- Submit with **`fw ffl run <file.ffl> --workflow ns.Name [--inputs JSON] [--task-list X]`**
  — the FFL-first submit that creates the runner record, so the run is trackable in the dashboard.
  (Do **not** use `fw ffl run-workflow`: it runs in-process and is not dashboard-visible.)
- Track progress, then report the results **and any honest limitations** (unreachable nodes,
  network fragmentation, regions that failed to download, data excluded, etc.).
