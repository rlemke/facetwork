# CLAUDE.md — Facetwork

Facetwork is a platform for defining and executing distributed workflows. You write workflows in **FFL** (Facetwork Flow Language), and the runtime handles execution, dependency resolution, retries, and monitoring.

## Getting Started

| Guide | Audience | What You'll Learn |
|-------|----------|-------------------|
| **[First-time Install](docs/getting-started/install.md)** | Brand-new users (no tools, nothing cloned) | Zero-to-running from a blank machine — what the pieces are, prerequisites, which repo to clone, and four setups: one machine, your-machine-as-hub + teammate runners, shared infra on dedicated servers, and company/cloud deployment |
| **[Beginner's Guide](docs/getting-started/beginners-guide.md)** | New users | Local setup, running your first workflow from the UI, writing basic FFL |
| **[README](README.md)** | Developers | Installation, Docker setup, parser/emitter API, CLI usage |
| **[Full Technical Reference](#full-technical-reference)** | Contributors | Compiler internals, runtime architecture, all commands, code conventions |

## The `fw` CLI — single front-door for every operation

**All operational tooling lives behind one command: `fw <group> <command>`** (the
`fw` executable is at the repo root). The old flat `scripts/<name>` paths **no
longer exist** — `scripts/` now contains only `scripts/lib/<group>/<command>`,
which `fw` dispatches to. Start with `fw help` to see the groups, `fw <group>` to
list a group's commands:

```bash
fw help                       # list all groups
fw fleet                      # list the fleet commands (with one-line blurbs)
fw runner start --domain osm-geocoder -- --log-format text
fw runner list                # the fleet (was scripts/list-runners)
fw ffl seed                   # seed examples (was scripts/seed-examples)
fw db postgis vacuum          # nested groups work
```

Groups: **`install`** (toolchain/venv/examples/**check**) · **`single`** (local dev stack:
up/down/rebuild) · **`db`** (mongo/postgres/import-pg/check + `postgis` subgroup) ·
**`runner`** (start/stop/drain/list/**scale**) · **`fleet`** (status/get/set/secret/
agent/**rollout**/**scale**/**registry-setup**/rolling-deploy/simulate) · **`ffl`**
(compile/run/publish/seed/scaffold/catalog/**bake-envs**/**lsp**) · **`maint`** (disk-guard/**disk-recover**/repair-workflow/
terminate-workflow/cache-index/**purge-servers**) · **`svc`** (dashboard/mcp/grafana/maps/**stocks-snapshot**/**osm-extracts**/**osm-replicate**/**osm-watchdog**) ·
**`util`** (check-doc-links/serve-map/thesis-pdf/**memory-sync**/**gen-compose**/**ffl-audit**) ·
**`mode`** (day-cluster/night-local switch: status/local/cluster/join/leave).

Notes for working with `fw`:
- It's just a thin dispatcher: each command is a file under `scripts/lib/<group>/`,
  `exec`'d with your args verbatim. The filesystem is the registry; `# fw-summary:`
  comments drive the listings. Path resolution is via `scripts/lib/_helpers/_bootstrap.sh`.
- The bolded commands above (`fw fleet rollout`, `fw fleet scale`, `fw fleet
  registry-setup`, `fw runner scale`, `fw maint purge-servers`) are the standard
  fleet/runner ops — prefer them over raw `docker`/`pymongo`. Most take `--dry`.
- Mongo-touching commands default to `localhost:27017`; on a runner host that isn't
  the DB, pass `--mongo mongodb://server3.local:27017` (or set `FW_MONGODB_URL`).
- **`fw install check [--install]`** — host-readiness analyzer, three sections.
  (1) Statically scans `tests/` + `examples/` for imported modules (incl.
  `pytest.importorskip`) and reports what isn't importable here. (2) Lists the
  catalog's domain packages vs what's installed (read from `domains.json`, same
  source as `fw install domain`). (3) **Script-environment coverage** — every
  declared `environment` (swept from `examples/` + the domain checkouts) against
  what this host actually provides under `FW_ENV_ROOT`. That third one is the
  silent gap: an environment is a claim-routing dimension, so a host missing one
  never *claims* its script tasks — no error, the work just doesn't run here.
  `--install` fixes all of it (domain modules via `fw install domain`, `boto3` via
  the `[s3]` extra, else pip; missing environments materialized the same way
  `fw ffl bake-envs` and the image bake do, so every host converges on the same
  content-addressed venv; PEP 668-aware). Run it to bring a new runner host to
  full coverage instead of discovering gaps through failing test runs. A host
  with no repo `.venv` (system Homebrew python is PEP 668-locked) needs `fw install repo`
  first to create one; if `FW_ENV_ROOT` (default `/opt/fw_envs`) isn't writable,
  point it somewhere that is and export the same value for the runners.
- **`domains.json` (catalog) + `fw util gen-compose`** — the domain/example set is
  defined ONCE in `domains.json`, not hardcoded in scripts. Each domain entry:
  `repo`, `extras`, `description`, and (for domains with a compose runner)
  `service`, `task_list`, `compose_extras`/`extras_var`, `server_group_arg`,
  `fleet_default` (in the default `--fleet` set), `scaled` (runs at the throughput
  knob vs one replica). A top-level **`defaults` block** sets replica counts:
  `{"replicas": 1, "scaled_replicas": 3}` — `replicas` for single-replica domain
  runners, `scaled_replicas` for the `scaled` tier (the env `FW_OSM_REPLICAS` still
  overrides the scaled tier per host). `fw install domain`, `migrate.py`,
  `runner/start`/`start-all` defaults, `rebuild-runners`, and fleet validation all
  read the catalog via `facetwork/domains/catalog.py`; `fw util gen-compose [--check]`
  **generates** each domain's full `runner-<service>` block (from its `compose`
  object) into `docker-compose.full-stack.yml` between the GENERATED markers
  (non-domain services — `runner-gh-router`, `runner-ffl` — stay hand-written).
  **Per-deployment
  override without editing any command:** set `FW_DOMAINS_FILE=/path` (full replace)
  or drop a gitignored `domains.local.json` (merged over the defaults — entries
  add/replace by key; top-level `"_remove": […]` drops standard ones). Run
  `gen-compose` after editing the catalog.
- **`fw svc osm-watchdog [--install] [--every-hours N] [--status]`** — an
  INDEPENDENT agent (12h) that runs the chain check and alarms if the OSM
  stream or an index has stalled. It exists because `osm-replicate`'s own
  post-publish check catches a job that RUNS and finds a problem, but cannot
  catch a job that never runs — a process cannot observe its own absence, and
  that failure has occurred here (a fixed nightly time never fired on a
  sleeping laptop). Deliberately trivial: it publishes nothing and mutates
  nothing. ⚠️ Exit codes are load-bearing — `--check` returns **0** healthy,
  **1** stalled, **2** could-not-verify (offline), and only **1** alarms.
  Alarming when merely offline would train you to ignore the alarm, which is
  how the original silent failure survived in the first place.
- **`fw svc osm-replicate [--days N] [--install] [--status]`** — publishes new
  per-region OSM replication diffs (fwh_osm's producer); `--install` adds a
  **nightly launchd timer at 03:15 local**, which is after OSM's ~00:18 UTC
  daily publish in both DST halves. Being current is a RATE, not a state:
  without this the stream simply stops advancing and nothing reports it, which
  is how the extracts silently reached 39 days stale in the first place.
  Defaults to `--days 4` so a missed night heals itself. ⚠️ Two launchd traps,
  both hit here: a bare `python3` resolves to **Xcode's sandboxed 3.9**, which
  raises "Operation not permitted" on an external volume and has none of the
  deps — always exec the venv interpreter; and `Path.glob` silently returns
  EMPTY when a directory cannot be enumerated, so set
  `FW_OSM_SELFHOST_REGIONS` and never let a scheduled job depend on readdir.
- **`fw svc osm-extracts [--install|--uninstall|--status] [--port N]`** — serves
  the self-hosted planet-split tree (`<region>-latest.osm.pbf` +
  `<region>-updates/`) over HTTP: **our Geofabrik**. This is what the
  `osmosis_replication_base_url` stamped into every extract points at, so
  without it the stamp is a promise nothing keeps — a consumer reads our URL
  from the PBF header, gets connection-refused, and silently falls back to
  re-downloading whole multi-GB extracts from a third party. Binds `0.0.0.0`
  because the stamped URL is a hostname other fleet members fetch from.
  ⚠️ The launchd plist deliberately sets **no `WorkingDirectory`**: the tree is
  on an external volume and launchd chdir-ing there before the job starts fails
  with *"getcwd: Operation not permitted"*; the wrapper cd's itself after
  waiting for the mount. Producer side: `fwh_osm`'s
  `publish-replication.sh` (see that repo's `docs/replication-publishing.md`).
- **`fw svc stocks-snapshot [--install|--uninstall|--status] [--at HH:MM]`** —
  marks every OPEN paper-trading group to market (submits
  `stocks.workflows.SnapshotStockGroups` with a blank `group_id`, the form the
  FFL calls "the daily cron entry"). `--install` writes a weekday **launchd**
  timer (`com.facetwork.stocks-snapshot`) firing at **23:30 local**, which is
  after the US close in every DST alignment — ET and Central European time
  normally sit 6h apart but switch on different dates, so the gap is 5h for a
  few weeks each spring/autumn. Safe to re-run: the snapshot key is the
  US/Eastern calendar date and the write is an **upsert**, so a second run the
  same day REPLACES that day's mark instead of adding a point. ⚠️ Without this
  timer nothing marks the groups — they simply stop having a series, with no
  error anywhere.
- **`fw util ffl-audit [--root PATH] [--json] [--no-contracts]`** — sweep every
  `fwh_*` domain repo for drift against the CURRENT runtime, and exit non-zero
  if any is found (CI-usable). Domain packages live in their own repos and
  deploy baked into an image, so nothing forces them to keep up when a rule
  changes: they keep compiling, keep deploying, and fail only when someone
  finally runs the workflow. Two checks. (1) **FFL validation** — catches rules
  that shipped after the FFL was written (relative `$`-scoping, `after`, yield
  aggregation). Choosing the library set is the hard part and both naive answers
  produce ~90 phantom errors, so it is **derived** per repo (supply only repos
  providing a namespace this one *uses* but does not *define*) and the audit
  prints what it supplied. (2) **Handler contracts** — obsolete runtime APIs in
  handler Python; currently `_step_log.append(…)`, since `_step_log` is a
  **callback** (`step_log(msg, level=…)`) and `.append` kills the handler at its
  first log line. ⚠️ That second check exists because a domain's OWN TESTS DO
  NOT CATCH IT — they pass `params` without `_step_log`, so the branch never
  runs; `fwh_sensor_monitoring` had all six handlers dead with 48 tests green.
  Use `--root /opt` **inside a runner container** to audit the BAKED domains,
  which is what the fleet actually executes — local checkouts can be behind.
- **`fw util memory-sync [-m MSG] [--dry]`** — commit + push this project's Claude
  memory directory (`~/.claude/projects/<slug>/memory`, where `<slug>` is the repo
  path with `/` and `_` replaced by `-`; override with `FW_MEMORY_DIR`) to its
  `origin` remote. The memory repo is version-controlled separately from this repo
  (private GitHub repo `rlemke/claude-memory-facetwork`). No-op when nothing changed,
  but still pushes any unpushed commits; `-m` sets the commit message, `--dry`
  previews. Run it after editing memory so changes are backed up — it is **not**
  automatic. Only does real work on the host where Claude runs (it errors cleanly on
  hosts with no memory repo).

## Running Workflows from the Dashboard

Open http://localhost:8080 → **New run** → pick a workflow, fill parameters, watch the live execution graph. Full UI reference: [docs/reference/dashboard.md](docs/reference/dashboard.md).

## Key Concepts

| Term | Meaning |
|------|---------|
| **Workflow** | Entry point for execution — defined in FFL with `andThen` steps |
| **Event Facet** | A step that requires a handler (external code) to execute |
| **Handler** | Python module that implements an event facet's logic |
| **Runner** | Service that picks up tasks and dispatches them to handlers |
| **Step** | A single unit of work within a workflow execution |
| **Source Adapter** | Namespace that extracts features from a specific data source (PBF, PostGIS, GeoJSON) into GeoJSON for downstream analysis |

### Source Adapter Pattern

The osm-geocoder example demonstrates a **source adapter pattern** that decouples data extraction (PBF / PostGIS / GeoJSON inputs) from analysis (statistics, filtering, rendering). The detailed contract — namespace layout, schema unification, and composed workflows — lives in the [osm-geocoder repo's CLAUDE.md](https://github.com/rlemke/fwh_osm/blob/main/CLAUDE.md).

When building a new domain pipeline that ingests from multiple data sources, mirror this pattern: one source namespace per input format, all producing the same category-specific output schemas so downstream analysis is source-agnostic.

## Documentation Map

| Topic | Document |
|-------|----------|
| **Dashboard (v3 UI)** — navigation, pages, the global **Filters** page (persisted Flow/Workflow filters), Users/Teams + acting-as, domain apps; v3 is the default and the only UI (v2 + legacy non-prefixed pages removed; only `/namespaces` + `/sources` remain outside `/v3`) | [docs/reference/dashboard.md](docs/reference/dashboard.md) |
| FFL syntax | [docs/reference/language/grammar.md](docs/reference/language/grammar.md) |
| **Editing FFL (`fw ffl lsp`)** — language server: validator diagnostics inline as you type (with `rule_id` + rule-doc link), completion of declared facets with signatures, hover showing a signature or the rule doc; Neovim/VS Code/Helix setup | [docs/guides/editor-setup.md](docs/guides/editor-setup.md) |
| **`foreach … limit N`** (**cleared** — leaf-stranding root cause fixed; the 3,167-county national fan-out that originally froze at 628 was re-run capped and completed unattended: 3,173/3,173 tasks, 0 failed, 0 non-terminal steps, surviving a 7.4h host hibernation mid-run. county-atlas carries its cap again. Untested since the fix: multi-**host** contention — both acceptance runs were single-host) — bulkhead on fan-out width: at most N iterations in flight, slots refilled as they finish. Same elements, same result — only concurrency changes. Cap may be a literal or a reference (`limit $.width`); `limit 1` serialises, omitting is unbounded. `limit` is a **contextual** keyword (still valid as a param name). Canonical example `13-foreach-limit.ffl` | [docs/architecture/ffl-foreach-limit.md](docs/architecture/ffl-foreach-limit.md) |
| **`after` clause** — explicit ordering for **invisible** dependencies (state passed through a shared cache/object store/scratch dir, so no value flows and the compiler can't see the edge). Replaces the `dependency_signal` idiom; expresses fan-in after a `foreach`. Canonical example `12-step-after.ffl`; codemod: `fw ffl migrate-after` | [docs/architecture/ffl-after-clause.md](docs/architecture/ffl-after-clause.md) |
| Runtime execution model | [docs/reference/runtime.md](docs/reference/runtime.md) |
| Distributed step processing | [docs/reference/runtime.md §10.3.1](docs/reference/runtime.md) |
| Runtime implementation details | [docs/reference/runtime-impl.md](docs/reference/runtime-impl.md) |
| Building handlers | [docs/reference/agent-sdk.md](docs/reference/agent-sdk.md) |
| **Python scripts & environments — when to use them vs handlers** — the capability/logic tier decision rule, script contract + sandbox, environment declaration/freeze/bake/lazy lifecycle, the osm.emergency migration numbers, gotchas | [docs/guides/scripts-and-environments.md](docs/guides/scripts-and-environments.md) |
| **Delegating a step to Ray** — hand one step's parallel interior to a Ray cluster while Facetwork keeps durable structure; when it pays (step overhead ~2.6s, so 5ms×3,000 belongs in one job) and when it does not (training needs gang scheduling); D2 blocking handler vs D3 watcher; ⚠️ D3 also needs a runner polling that task list for `fw:resume`, and a finished workflow reads `running` for up to 15min until the finalisation pass | [docs/guides/delegating-to-ray.md](docs/guides/delegating-to-ray.md) |
| **Multi-language (polyglot) handlers** — Java/Go/Scala/TS handlers alongside Python via the Mongo-coordinated agent protocol (name-filtered claim → coexistence); worked example: the embedded-GraphHopper Java agent (`fwh_osm/java/osm-gh-router`) | [docs/guides/multi-language-handlers.md](docs/guides/multi-language-handlers.md) |
| LLM integration | [docs/guides/llm-integration.md](docs/guides/llm-integration.md) |
| **Long-running handlers** — heartbeats, batch boundaries, staging tables, and **cooperative cancellation** (`ctx.raise_if_cancelled()` / `ctx.is_cancelled`: stop when a run is terminated, a watchdog already failed the task, or a reclaim made this execution a zombie; a cancelled handler is a clean stop — no retry, no failed step) | [docs/guides/long-running-handlers.md](docs/guides/long-running-handlers.md) |
| FFL examples | [docs/reference/examples.md](docs/reference/examples.md) |
| Execution traces | [docs/reference/execution-traces.md](docs/reference/execution-traces.md) |
| Build & run reference | [docs/reference/cli.md](docs/reference/cli.md) |
| Non-functional requirements | [docs/reference/nonfunctional.md](docs/reference/nonfunctional.md) |
| Architecture overview | [docs/architecture/overview.md](docs/architecture/overview.md) |
| **`/ffl-first`** — fulfill a request as a reviewed FFL workflow (discover→reuse→gated-author→gated-scaffold→FFL-only data→run); two review gates (workflow shown before run, handler shown before use); `fw ffl run` is its dashboard-visible run mechanism | [docs/guides/ffl-first.md](docs/guides/ffl-first.md) |
| Claude workflow catalog (store/version/run FFL with no file; `fw_catalog_*` MCP tools) | [docs/architecture/claude-workflow-catalog.md](docs/architecture/claude-workflow-catalog.md) |
| `use` resolution: file-based compile vs. the catalog (hermetic pinned-dep model) | [docs/architecture/catalog-use-resolution.md](docs/architecture/catalog-use-resolution.md) |
| Extending with new handlers (NL needs a capability no facet provides → detect gap → scaffold facet+handler+test) — `fw ffl scaffold` | [docs/architecture/extending-with-new-handlers.md](docs/architecture/extending-with-new-handlers.md) |
| **Server catalog (`servers.json`)** — machines by STABLE NAME + aliases (`afl-mongodb`…); resolved to the current IP at startup/reconcile so DHCP drift self-heals (no `/etc/hosts` edits). `fw fleet servers` | [docs/reference/server-catalog.md](docs/reference/server-catalog.md) |
| **Domain/example catalog (`domains.json`)** — single source of truth for the domain set + per-domain attributes (repo/extras/service/task_list/scaled/fleet_default…) + `defaults` replica counts; field reference, file resolution + `domains.local.json`/`FW_DOMAINS_FILE` override, and add-a-domain / per-deployment walkthroughs. Read by install/migrate/gen-compose/runner-start/fleet | [docs/reference/domain-catalog.md](docs/reference/domain-catalog.md) |
| Composable facet library (design): orthogonal/complete/discoverable/distributed primitives for LLM-composed workflows + the memory-of-solved-requests moat | [docs/architecture/composable-facet-library.md](docs/architecture/composable-facet-library.md) |
| **Script environments (SHIPPED + fleet-verified)**: named `environment` decls (language + frozen dep manifest) + `in environment` on script-bearing decls; environment = claim-routing dimension (tasks carry the manifest hash, runners advertise provided envs); venvs baked at image build (`facetwork.envbake`) or lazily materialized on demand; foreign languages ride the polyglot agent protocol; canonical example `11-environment-script.ffl` | [docs/architecture/script-environments.md](docs/architecture/script-environments.md) |
| Approximate freeway routing (`osm.Network`, design): in-process graph search over a tiny noded-freeway artifact — no engine daemon, read-once-per-runner | [docs/architecture/approximate-freeway-routing.md](docs/architecture/approximate-freeway-routing.md) |
| Emergency-Access Atlas (`osm.emergency`, design): 4-level fan-out/fan-in showcase; key decisions locked in the doc | [docs/architecture/emergency-access-atlas.md](docs/architecture/emergency-access-atlas.md) |
| **County Atlas (`fwh_county_atlas`, design + as-built)** — a map per US county over one catalog-driven generic renderer (`layers.json` = the whole integration surface); §9 records the shipped reality: 8 source adapters / 7 fetch patterns, self-contained SVG renderer, the **3,167-county national fan-out** (0 failed), fleet bake, MinIO full-set + curated GitHub subset, and operational lessons | [docs/architecture/county-atlas.md](docs/architecture/county-atlas.md) |
| **`ffl-runner` orchestration tier (design)**: split the step-state-machine/continuation processing into a dedicated leaderless `ffl-runner` role (hybrid: handler runners keep inline dispatch but stop polling the shared `_fw_continue` backlog; the tier owns the backlog + stuck-step sweep + version gate) → shrinks the fleet-wide blast radius of engine changes and bulkheads orchestration from heavy handlers, still decentralized | [docs/architecture/ffl-runner-orchestration-tier.md](docs/architecture/ffl-runner-orchestration-tier.md) |
| **FFL as a front end to other workflow systems (design)** — **delegation**: Facetwork stays the orchestrator and an event facet's handler submits to Temporal/Airflow and waits. Key insight: an adapter is the **polyglot agent protocol pointed at a workflow engine** (claim → submit → park → `fw:resume`), so no new runtime concept is needed. Covers the three delegation depths (D1 fire-and-describe, as `examples/aws-lambda` does today / D2 blocking+heartbeat / **D3 detached, recommended**), parking a task safely via the **stage budget**, and **idempotent submission by deriving the external run id from `step_id`**. ⚠️ Delegation gives reach, **not portability** (that is transpilation, a non-goal here); cancellation propagation is a stated prerequisite. **Two D2 adapters ship**: `examples/ray-delegate` (a job API — derived submission id, `stop_job`) and `examples/snakemake-delegate` (**no job registry at all** — a local process recording completion in the FILESYSTEM, so idempotency is the target engine's own incremental model and terminate means killing a process *group*). The contrast between the two is the useful part when writing a third | [docs/architecture/external-engine-delegation.md](docs/architecture/external-engine-delegation.md) |
| **Capability-tiered server groups (heterogeneous fleet)** — per-role `server_groups` in the central fleet config (`fw fleet set --role-groups ROLE:g1,g2` / `--server-groups`) + a `fleet-agent` gate so a host only *starts* roles whose group includes its `FW_SERVER_GROUP`; keeps heavy domains (osm PBF/wide fan-outs) off under-provisioned hosts. No-op by default (no groups → every role everywhere); routing already capability-scoped so correctness is unchanged. **§6 "Assigning handlers to servers by capability"** is the capacity-aware placement policy: profile each server (CPU/RAM/scratch-disk/arch-emulation/binaries/credentials), the per-domain **resource matrix** (demand + tier), the placement procedure, and the current fleet's worked assignment (osm/gh-router→`heavy`, light domains everywhere) | [docs/architecture/server-groups.md](docs/architecture/server-groups.md) |
| Deployment guide | [docs/operations/deployment.md](docs/operations/deployment.md) |
| Full-stack Docker Compose (one runner per fwh_* domain) | [docs/operations/full-stack-compose.md](docs/operations/full-stack-compose.md) |
| **Multi-server fleet** (`fleet`/`fleet-agent`/`start-runner --fleet`: shared external MinIO+MongoDB, central config, encrypted secrets, discovery) **+ local simulation** (`fw fleet simulate`) | [#multi-server-runner-fleet--local-simulation](#multi-server-runner-fleet--local-simulation) · [docs/operations/deployment.md](docs/operations/deployment.md) |
| **Fleet rollouts & runner lifecycle** — image-based change deployment (buildx→registry→`fleet set --image`), what happens to running tasks during a rollout (graceful drain → reaper recovery → retry/dead-letter), start/stop/drain runners from the CLI, and how this auto-deploy compares to Kubernetes/Temporal-grade pipelines | [docs/operations/fleet-rollouts.md](docs/operations/fleet-rollouts.md) |
| **Informal fleet — your team's own machines as the cluster** — only central MongoDB+MinIO must be stable; runner machines (desktops/laptops) are stateless & disposable and can come/go (reaper → re-claim); who this model is for (small/research teams); large-scale data-center operation is architecturally **possible but untested** — treat it as a real engineering investment (hardening/scheduling/observability), not a config change | [docs/operations/informal-fleet.md](docs/operations/informal-fleet.md) |
| **Local maps gallery (`fw svc maps`)** — browse every map straight from MinIO (read-only proxy, streamed on demand; no git repo, no bucket-policy change), grouped by domain. Decouples *keeping* maps (big/tiled/data stay in the object store) from *publishing* selected ones to the public `facetwork-maps` site. `http://localhost:8090/` | [docs/operations/local-maps-gallery.md](docs/operations/local-maps-gallery.md) |
| **`fw mode` — day-cluster / night-local switch** — two SEPARATE models: **join/leave** (Model A — stop/start lending this machine to the cluster as a runner; near-free, reaper re-claims; the common "night" case) and **local/cluster** (Model B — flip WHERE infra lives via gitignored `mode.{local,cluster}.json` profiles + recreate runners; `cluster` REFUSES if the target infra is unreachable so it can't strand the box). ⚠️ Model B does NOT merge state — local and cluster are separate Mongo+object-store worlds. Built on the MaxPro standalone setup | [docs/operations/fw-mode.md](docs/operations/fw-mode.md) |
| Tutorial | [docs/getting-started/tutorial.md](docs/getting-started/tutorial.md) |
| Tools + handlers pattern (per-domain CLI + `_<pkg>_tools/` + shim) | [agent-spec/tools-pattern.agent-spec.yaml](agent-spec/tools-pattern.agent-spec.yaml) |
| Cache layout (sidecars, namespaces, cache types) | [agent-spec/cache-layout.agent-spec.yaml](agent-spec/cache-layout.agent-spec.yaml) |
| MCP context-engineering design (rule_ids, docs URIs) | [docs/architecture/mcp-context-engineering.md](docs/architecture/mcp-context-engineering.md) |
| Validator rule docs (one file per rule_id) | [docs/reference/rules/](docs/reference/rules/) |
| **Built-in `fw.file` facets** — list/read/write/copy/hash/delete with NO handler to write; storage-aware (same workflow on local, `s3://`, `hdfs://`); `List` is the enumerate-then-`foreach` primitive. Ships in `facetwork/ffl/file.ffl`, auto-registered by every runner. A `script {}` block CANNOT do file I/O (no `open`, no `os`/`pathlib` import) — bind `in environment fw.file.FileOps` to get `fsspec` in a script | [examples/canonical/14-builtin-file-facets.ffl](examples/canonical/14-builtin-file-facets.ffl) |
| **Built-in `fw.http` facets** — `Fetch` / `Get` / `GetJson` / `Provenance`, also no handler to write. The value is the **reuse decision**: a cached artifact is reused only when provably current — conditional GET (`If-None-Match` → 304), or `checksum_url` for publishers that ship a .md5/.sha256, or `max_age_hours` the domain sets; integrity checked before reuse. Every fetch writes `<dest>.meta.json` (digest, source URL, validators, `fetched_at`) so a DERIVED artifact can detect its input changed. Motivated by the audit: 14 of 23 domains hand-rolled this, one froze on a moving `-latest` target, one recorded a key it never compared. Storage-aware `dest`; stdlib-only (no `requests`) | [examples/canonical/15-builtin-http-facets.ffl](examples/canonical/15-builtin-http-facets.ffl) |
| **Built-in `fw.archive` facets** — `Extract` / `List` / `ReadMember` (9 of 23 domains hand-roll this across 17 sites). Three recurring shapes: extract-then-glob, read the one member whose name moves each release (`pattern`, not a pinned literal), walk a tar by suffix. Format detected from **content** not extension; members that would land outside `dest` refused (on 3.12 a tar `../x` really escapes and a symlink member is created — zip sanitises but silently flattens). Storage-aware; composes as **fetch → extract → `foreach`** | [examples/canonical/16-builtin-archive-facets.ffl](examples/canonical/16-builtin-archive-facets.ffl) |
| **Built-in `fw.exec` facets** — `Run` / `Which`: Facetwork's answer to Snakemake's `shell:`. **argv list, never a shell** (so a path containing a space/quote/`$( )` can't change what runs), and **host-gated**: `command` must be a bare name listed in that runner's `FW_EXEC_ALLOW` (unset = refuse, as a `PermanentError` so a policy refusal doesn't burn the retry budget). The gate exists because the `script {}` sandbox already decided the FFL author isn't fully trusted — bare names only, else `fw.file.WriteText` could plant a binary. Child env is **scrubbed** (PATH/HOME/LANG/TMPDIR + explicit `env`) so runner credentials don't leak into wrapped tools; output capped-and-drained; timeout kills the process **group**. Registered with `timeout_ms=0` (a wrapped tool legitimately runs for hours) | [examples/canonical/17-builtin-exec-facets.ffl](examples/canonical/17-builtin-exec-facets.ffl) |
| Canonical FFL examples | [examples/canonical/](examples/canonical/) |

## Writing and fixing FFL — the validator-driven loop

The validator emits structured `rule_id`s with `docs_uri` pointers. Use this loop instead of guessing:

1. Run `fw_validate` (MCP) or `afl <file> --check` on the source.
2. For each error, fetch `fw://docs/rules/{rule_id}` for paired wrong/right examples and a "why".
3. Apply the fix and re-validate.

When writing new FFL, start from a canonical example under [`examples/canonical/`](examples/canonical/) — every file there is a small, validator-clean template. The MCP server also exposes them at `fw://examples/canonical/{name}`.

Key constraints the rule docs cover (and the language enforces):

- **Workflows and schemas must live inside a namespace** (`WORKFLOW_AT_TOP_LEVEL`, `SCHEMA_AT_TOP_LEVEL`).
- **References are always `step.field` form** — bare step names are not valid expressions (`REF_INVALID_STEP_FORMAT`).
- **Relative `$`-scoping** — `$` is a block's immediate container; `$.attr` reads its params/returns, `$$`/`$$$`… walk up (past the outermost = `REF_DOLLAR_OVERFLOW`). A block sees only `$…` container attributes + **same-block** steps; it may not name the containing step (`REF_CONTAINING_STEP_BY_NAME` — use `$`) or a sibling block's step (`REF_CROSS_BLOCK_STEP`, incl. `andThen when`). Gate on several prior steps by passing them as facet-typed params, not by nesting. Default on; `FW_FFL_RELATIVE_SCOPING=0` for the legacy flat model. See [ffl-relative-scoping.md](docs/architecture/ffl-relative-scoping.md).
- **`foreach` loop variables are accessed via `$.<varname>`** inside the block (bound on the loop body's `$` surface). Add **`limit N`** to bound how many iterations run at once (`foreach c in $.counties limit 8`) — reach for it when iterations contend for a shared resource (scratch disk, an API rate limit, PostGIS connections), not merely because the fan-out is wide. A literal cap is checked at compile time (`FOREACH_LIMIT_NOT_POSITIVE`, `FOREACH_LIMIT_NOT_INTEGER`); an unresolvable cap **fails the run** rather than silently reverting to unbounded. A cap taken as a **parameter** treats `0` as unbounded (`width: Int = 0`), so one facet covers capped and uncapped — a literal `limit 0` is still an error.
- **Yield targets** must be the containing facet OR one of its declared mixins (`YIELD_INVALID_TARGET`).
- **Yielding from inside a `foreach` — use `+=` to AGGREGATE.** `yield F(xs += item)` accumulates across iterations (a scalar appends, a list extends, so a nested fan-out composes without wrapping); `yield F(xs = item)` *assigns*, so each iteration overwrites the last and the result is the **final** iteration's value. Both compile and every iteration's step still runs, so the bare form silently under-reports a fan-out (measured: 3 files → 1 digest vs 3) — the validator warns on it for step-derived values (`YIELD_FOREACH_OVERWRITES`); literals like `status = "partial"` are deliberate last-wins and not flagged. `+=` outside a yield is an error (`YIELD_APPEND_OUTSIDE_YIELD`). Aggregation follows **iteration order**, not completion order, so a fan-out's result is reproducible. For a fan-in over *artifacts* rather than returned values, order a later step with `after` and have it read the store.
- **`when` blocks need a default case, last** (`WHEN_MISSING_DEFAULT`, `WHEN_DEFAULT_NOT_LAST`); attach a `when` to the step it gates.
- **No truthy/falsy coercion** — comparisons return Boolean, and `&&`/`||`/`!` only accept Boolean operands.
- **Ordering comes from references — `after` is only for what the compiler can't see.** A step that consumes another's result is already ordered behind it; never write `after` for that (`AFTER_REDUNDANT`). Use `step = F(…) after a, b` **only** when the dependency is invisible — the producer wrote a shared cache/object store/scratch dir and no value flows — including fan-in after a `foreach`, where `after <fan-out step>` waits for every iteration. Same scoping as step references: same block, backward only (`AFTER_UNKNOWN_STEP`, `AFTER_FORWARD_STEP`, `AFTER_SELF`, `AFTER_CROSS_BLOCK`); targets are bare step names, never `step.field` (`AFTER_NOT_A_STEP`). The old workaround — passing an unread `dependency_signal` value — is **removed** (gone from all 7 domains and their docs); migrate legacy sources with `fw ffl migrate-after`. Start from [`examples/canonical/12-step-after.ffl`](examples/canonical/12-step-after.ffl). See [ffl-after-clause.md](docs/architecture/ffl-after-clause.md).
- **Script environments** — `environment Name { language, requires }` (inside a namespace) + `in environment Name` on script-bearing declarations; absent = default env. `script { … }` braces take RAW code (a quoted string inside braces is a silent no-op). Rules: `ENV_UNKNOWN`, `ENV_MISSING_LANGUAGE`, `ENV_LANGUAGE_SCRIPT_MISMATCH`, `ENV_AT_TOP_LEVEL`. See [script-environments.md](docs/architecture/script-environments.md).

When adding a new validator check, give it a `rule_id` AND write the matching `docs/reference/rules/{rule_id}.md` in the same change. Coverage is currently exact (all 61 emitted rule_ids documented); the script that diffs them lives in [docs/architecture/mcp-context-engineering.md](docs/architecture/mcp-context-engineering.md).

## Standalone domain packages

Domain pipelines ship as separate pip-installable packages declaring the
`facetwork.domains` entry point (`DomainPackage`), discovered by
`fw runner start --domain <name>` and `fw ffl seed`. (In-repo teaching demos
under `examples/<name>/` are the separate "examples" family — `--example <name>`,
`example:` seed prefix; the pluggable `fwh_*` production pipelines are "domains".)
See `domain-template/` for the standalone package layout (incl. the **GitHub
topics convention** — tag every new `fwh_*` repo `facetwork` + `facetwork-domain`
+ its domain topics, and give it a one-line source→output description, so it
surfaces under `topic:facetwork` alongside the framework). To clone + install one
(or all) of the standalone domains, use the registry-driven helper:

```bash
fw install domain --list              # see what's registered
fw install domain anthropic --check   # clone, pip install -e, verify
fw install domain --all               # install every registered domain
fw install anthropic --all --check     # convenience wrapper for the
                                            # anthropic package — picks pip
                                            # extras (agent_sdk, mcp) and
                                            # reports env readiness
```

It clones into `~/fw_handlers/` (override via `--dir` or
`FWH_HANDLERS_ROOT`), `pip install -e`s each repo against the current
venv (auto-detected as `.venv/` if present), and the `--check` flag
confirms the domain surfaces in `facetwork.domains`'s entry-point
discovery.

The following domains have been extracted into their own repos and
surface as `--domain <name>`:

- [osm-geocoder](https://github.com/rlemke/fwh_osm) — production-scale OSM ingestion
- [osm-lz](https://github.com/rlemke/fwh_osm_lz) — pure-FFL workflow catalog over `fwh_osm`
- [noaa-weather](https://github.com/rlemke/fwh_noaa_weather) — NOAA GHCN/NDBC climate trends
- [jenkins](https://github.com/rlemke/fwh_jenkins) — CI/CD mixin composition demo
- [census-us](https://github.com/rlemke/fwh_census_us) — US Census ACS + TIGER
- [genomics](https://github.com/rlemke/fwh_genomics) — foreach fan-out, joint genotyping
- [sensor-monitoring](https://github.com/rlemke/fwh_sensor_monitoring) — sensor pipelines, RegistryRunner-first
- [anthropic](https://github.com/rlemke/fwh_anthropic) — wrappers for Anthropic surfaces (16 facets + `DocumentQA` composition workflow)
- [save-earth](https://github.com/rlemke/fwh_save_earth) — open environmental + infrastructure datasets → cached GeoJSON → MapLibre HTML maps
- [sentinel2-landchange](https://github.com/rlemke/fwh_sentinel2) — Sentinel-2 land-cover change → NDVI/classify → XYZ-tiled MapLibre map
- [conflict](https://github.com/rlemke/fwh_conflict) — UCDP armed-conflict world choropleth (5 metrics + displacement overlays)
- [osm-mapping](https://github.com/rlemke/fwh_osm_mapping) — OSM mapping-equity maps: "under-mapping vs population"
- [h1b](https://github.com/rlemke/fwh_h1b) — US H-1B visa approvals by state & county, FY2009-2023
- [health](https://github.com/rlemke/fwh_health) — disease-burden choropleths (US + world) incl. a 5-map NHSN respiratory time-slider family
- [migration](https://github.com/rlemke/fwh_migration) — world net-migration choropleth with year slider + play, 1960-2025
- [cancer](https://github.com/rlemke/fwh_cancer) — cancer gene-prioritization evidence graph (not a map): ranked, explainable per-type gene table from open genomics (TCGA/GDC, GTEx, intOGen)
- [congress](https://github.com/rlemke/fwh_congress) — US congressional (voting) districts colored by sitting-rep party (Census CD119 + congress-legislators)
- [amr](https://github.com/rlemke/fwh_amr) — antibiotic-resistance gene spread (country × year × species) from NCBI Pathogen Detection: world resistance-prevalence map (year slider + drug-class dropdown) + ranked gene × species table; ingests precomputed AMRFinderPlus calls (no assemblies)
- [pypsa-data](https://github.com/rlemke/fwh_pypsa_data) — PyPSA-Eur's data-retrieval layer as FFL: its `data/versions.csv` catalogue (59 datasets × primary/archive) drives ONE `foreach` over the built-in `fw.http.Fetch` / `fw.archive.Extract`, replacing **1,631 lines and 73 Snakemake rules** — the rules are near-copies because a rule's outputs are static. `Catalog`/`MirrorPairs` are the only handlers. Beyond parity: `retrieve.smk` has no checksum/`ancient()`/`protected()` and its rules declare no file inputs, so nothing can make an output stale; `VerifyMirror` compares the archive mirror against its primary (**59 pairs nothing checks**). ⚠️ Tested offline against a checked-in catalogue + ONE live 26MB fetch — a full run is several GB, none of it OSM
- [pypsa-earth](https://github.com/rlemke/fwh_pypsa_earth) — PyPSA-Earth's **OSM stage** as FFL (`build_shapes` → `download_osm_data` → `clean_osm_data` → `build_osm_network`): raw OSM power infrastructure → grid topology (buses/lines/converters/transformers). **3 rules of ~60** — the rest is sector coupling/solving and needs ERA5 + an LP solver. ⚠️ **No collapse ratio, unlike pypsa-data**: these are three distinct algorithms (2,168 lines), so it is ~1 facet per rule — exactly what the cost-of-change experiment predicted about `script:`-shaped rules. Handlers CALL upstream's `clean_data`/`built_network`, so the payload stays theirs — which surfaced that those cores are **not actually callable** (two module-globals + one attribute access that only Snakemake satisfies). Local-planet workarounds for both blocked sources: `shape_source="osm"` (GADM unreachable here) and `local_extracts` (serve earth_osm from our own planet split). NOT fleet-deployed — needs a checkout (`FW_PYPSA_EARTH_DIR`) + ~178 MiB of upstream deps
- [gridbuilder](https://github.com/rlemke/fwh_gridbuilder) — OSM power infrastructure (substations, transmission lines) → model-ready grid CSV/GeoJSON per country, for PyPSA-Eur/PyPSA-Earth. The **FFL equivalent of [PyPSA grid-builder](https://github.com/PyPSA/grid-builder)** (Snakemake), calling the same `earth_osm` extractor so the comparison is between orchestrators, not payloads — verified same-data against it (832 rows, 0 differing). Ports only the *retrieve* stage, which is all upstream implements. Not fleet-deployed (no `service` in the catalog)
- stocks (`~/fw_handlers/fwh_stocks`, local) — **paper-trading** stock-group tracker (not a map): factor screen + analyst consensus + optional LLM re-rank picks a group, entry prices are recorded, daily snapshots mark it to market, and close writes per-position and overall P/L **vs a benchmark**. Dashboard app at `/stocks`; pluggable `PriceProvider` (yfinance default, `stub` for offline tests); the full ranked candidate list is retained so unpicked names stay comparable

Full per-domain descriptions live in `domains.json` and each repo's README/CLAUDE.md.

**Every `fwh_*` repo carries an FFL example gallery.** Its README opens with an
"FFL at a glance" section (one short, runnable snippet against that domain's own
facets) linking to **`docs/ffl-examples.md`** — 6–9 complete, compile-checked
scenarios per domain: the minimal workflow, `$`-scoping, `foreach` fan-out,
`dependency_signal` sequencing, call-time mixins, `catch`, `when`, cross-domain
composition, plus a syntax cheat sheet. Point people there for "show me the
language" before the grammar reference. Keep the convention when adding a domain,
and re-check the blocks after changing a facet signature — every fenced ```ffl
block is meant to compile against the repo's own FFL (`fw ffl compile --check
--primary <block> --library <domain .ffl>`).

## Domain pipelines — tools / handlers / cache pattern

Every domain ingestion pipeline (osm-geocoder, noaa-weather, …) follows one contract: a `tools/` dir of Python CLIs + shell wrappers backed by `tools/_<pkg>_tools/`, FFL handlers that call into the same `_<pkg>_tools/` via a `handlers/shared/<domain>_utils.py` shim, and a sidecar-backed cache under `$FW_CACHE_ROOT/<namespace>/`. Canonical examples:

- [github.com/rlemke/fwh_osm](https://github.com/rlemke/fwh_osm) — OSM PBF → GeoJSON → tiles → HTML maps (standalone repo)
- [github.com/rlemke/fwh_noaa_weather](https://github.com/rlemke/fwh_noaa_weather) — NOAA GHCN → station CSVs → climate trends (standalone repo; `pip install -e ~/fw_handlers/fwh_noaa_weather`)

When building or modifying a domain pipeline, read [`agent-spec/tools-pattern.agent-spec.yaml`](agent-spec/tools-pattern.agent-spec.yaml) first — it defines the directory layout, CLI contract (argparse / stderr / stdout / exit codes), `_<pkg>_tools/` vs handler split, cache sidecar protocol, and test-writing rules.

---

## Full Technical Reference

*Everything below is detailed reference for contributors and operators.*

### Terminology
- **Facetwork**: The platform (compiler + runtime + agents)
- **FFL**: Facetwork Flow Language — the `.ffl` DSL for defining workflows
- **RegistryRunner** (recommended): Universal runner that auto-loads handlers from DB. Register handlers via `register_handler()` or the MCP `afl_manage_handlers` tool.

### Authoring roles

- **Domain programmers** write FFL to define workflows, facets, schemas, and composition logic — no Python needed.
- **Service provider programmers** write handler implementations (Python modules) for event facets.
- **Claude** can author both FFL definitions and handler implementations from requirements descriptions.

### Core constructs
- **Facet**: typed attribute structure with parameters and optional return clause
- **Event Facet**: facet prefixed with `event` — triggers agent execution
- **Workflow**: facet designated as an entry point for execution
- **Step**: assignment of a call expression within an `andThen` block
- **Schema**: named typed structure (`schema Name { field: Type }`) — must be defined inside a namespace

### Reserved task names

The `afl:` prefix is reserved for internal runtime tasks. User workflows, handlers, and external processes must **not** create tasks with names starting with `afl:`. The runner treats these as built-in protocol tasks with special dispatch and claiming logic.

Current internal tasks:
- `fw:execute:<WorkflowName>` — bootstrap task that starts a workflow execution (created by dashboard/CLI)
- `fw:resume:<FacetName>` — signals the RunnerService to resume a workflow after an external agent has completed a step (created by agent SDKs)

### Task claiming — a runner only takes tasks it can run

A runner **only claims tasks for facets it has a handler for**, plus the
`fw:execute` / `fw:resume` protocol tasks (every runner can bootstrap a
workflow and process resume tasks). `claim_task()` is name-filtered server-side
(exact match or `<name>:` prefix), so a runner never picks up a task outside the
set of names it polls for. A `--registry` runner advertises a facet only if its
handler module is *importable in that process* (`preload(verify=True)`), so in a
one-runner-per-example deployment each runner claims just its own example's
facets. If a task does land on a runner with no handler for it, the runner
releases it back to `pending` (with backoff) rather than failing it — it is
failed only after retries are exhausted, i.e. no runner in the fleet could
service it. See [docs/reference/runtime-impl.md §17.1.1](docs/reference/runtime-impl.md).

### Per-runner polling — runners do not contend

Each runner is an autonomous process; all coordination goes through MongoDB.
`claim_task()` is a single atomic `find_one_and_update` — there is no shared
in-process dispatcher, no leader, no queue lock. Two consequences:

- **Different filters → disjoint pools.** RunnerA polling for `osm.*` and
  RunnerB polling for `anthropic.*` issue different Mongo queries and never
  see each other's tasks. A 1000-task `osm` backlog adds zero latency to an
  `anthropic` claim.
- **Same filters → race, not queue.** When multiple runners can handle a task,
  they race on the atomic claim; exactly one wins, the rest get `None` and
  poll again. There is no fairness, no FIFO across runners — just per-poll
  first-come-first-served on whatever is pending.

Task creation is also lock-free: each task has a unique `uuid` and is written
once by its creator, so there's no write contention either. See
[docs/reference/runtime-impl.md §17.1.3](docs/reference/runtime-impl.md).

### Task-list routing — derived from the facet namespace

Tasks carry a `task_list_name` for per-domain queue isolation (so a flooded
`osm` queue can't starve `anthropic` claims), and it is **derived from the
facet's top-level namespace** — never configured. The same intrinsic fact (the
qualified name) is used on **both** sides of the queue, so the label can't
desync from where the handler lives:

- **Producing** — a task for `osm.cache.Download` is tagged with list `osm`
  (`namespace_of`); submission tags the `fw:execute:<Workflow>` bootstrap with
  the workflow's namespace.
- **Consuming** — a runner polls the namespaces of the handlers it loaded
  (`namespaces_for`) plus its `--task-list` (default `"default"`, for shared/
  unnamespaced work). An `osm.*` runner polls `osm`.

A task is claimed only if it's on one of the runner's namespace lists **and**
the runner has its handler, so work always reaches a runner that can serve it.
Continuation tasks (`_fw_continue`) stay on a shared internal list every runner
polls (step-state-machine machinery, not handler work). There is **no**
`FW_WORKFLOW_TASK_LIST_MAP` and no `--task-list` plumbing for routing — the
namespace is the routing key. See
[facetwork/runtime/task_list_routing.py](facetwork/runtime/task_list_routing.py).

### Agent execution models
- **RegistryRunner** (recommended): auto-loads handlers from DB
- **AgentPoller**: standalone agent services with `register()` callback
- **RunnerService**: distributed orchestration with thread pool and heartbeat
- **ClaudeAgentRunner**: LLM-driven in-process execution via Claude API

### Language feature reference

Composition (mixins, implicit facets, andThen/yield/foreach, catch, prompt/script blocks) and expression syntax are specified in [docs/reference/language/grammar.md](docs/reference/language/grammar.md); start new FFL from [examples/canonical/](examples/canonical/).

### All commands

`fw help` / `fw <group>` are self-listing; the full build & run reference is [docs/reference/cli.md](docs/reference/cli.md).

### Environment configuration
Copy `.env.example` to `.env` to configure MongoDB, scaling, overlays, and data directories. All `scripts/` commands source `_env.sh` which loads `.env` without overriding already-set vars. See `docs/reference/cli.md` for the full variable reference.

MongoDB, HDFS, PostGIS, and the shared MinIO object store run on external/infra servers (defined in `/etc/hosts`): `afl-mongodb`, `afl-hadoop-hdfs`, `afl-hadoop-yarn`, `afl-postgres`, `afl-minio` — they are **not** managed by Docker Compose. `afl-minio` is the fleet-wide S3 endpoint default (`FW_S3_ENDPOINT=http://afl-minio:9000`); add an `/etc/hosts` entry on each server pointing it at the infra host (`<infra-host-ip> afl-minio`, where `<infra-host-ip>` is your fleet's infra host — the `FW_INFRA_IP` from `.env.fleet`). For Docker runner containers (which don't read the host's `/etc/hosts`), `docker-compose.fleet.yml` maps `afl-minio`/`afl-mongodb` to the infra host via `extra_hosts` (defaulting `FW_INFRA_IP` to the infra IP), and the bundled `docker-compose.full-stack.yml` exposes MinIO under the `afl-minio` network alias.

### Durable storage backend (cache + outputs)

Handler caches and outputs live on a backend selected by `FW_STORAGE` + `FW_DATA_ROOT`: `local` (a path such as `/Volumes/afl_data`), `hdfs://`, or `s3://` — AWS S3 or a self-hosted **MinIO**. On `s3`/`hdfs`, step payloads carry portable `s3://`/`hdfs://` URIs that any runner on any host can resolve, so a multi-server fleet needs no shared disk; object stores don't do partial writes, so handlers stage to a local scratch dir and finalize on close (keep `FW_OUTPUT_BASE`/`FW_LOCAL_SCRATCH` **local**).

`docker-compose.full-stack.yml` **bundles a MinIO service** and the OSM runners (`osm-geocoder`, `osm-lz`) default to it — durable cache + output go to `s3://afl-cache` (console http://localhost:9001, `minioadmin`/`minioadmin`), **no external disk**. The legacy local cache at `/Volumes/afl_data/cache` was migrated into the bundled MinIO with `scripts/_cache_to_minio_move.py` (host-driven, verify-before-delete, idempotent). Full setup, the env contract, and the cache-migration recipe live in [docs/operations/deployment.md](docs/operations/deployment.md) → **S3 / MinIO Integration**.

### Multi-server operation + fleet

There is no "master": infra services (MongoDB, MinIO, Dashboard) are URL-addressed only, and every Facetwork server is a homogeneous, stateless, leaderless runner — any runner with Mongo access can seed (`fw ffl seed`). Remote DB access (bind `0.0.0.0`, `/etc/hosts` entries for `afl-mongodb`/`afl-postgres`), the fleet controller (`fw runner start --fleet`, `fw fleet set/status/secret`, `fw fleet agent apply|watch`), joining a new server, and the one-box simulation (`fw fleet simulate`) are all in [docs/operations/deployment.md](docs/operations/deployment.md) and [docs/operations/join-fleet-from-new-server.md](docs/operations/join-fleet-from-new-server.md).

Set `ANTHROPIC_API_KEY` to enable live Claude API calls for prompt-block event facets. Set `FW_POSTGIS_URL` (e.g. `postgresql://afl:afl@afl-postgres:5432/afl_gis`) for PostGIS imports — without it the importer falls back to a hardcoded default that may not match your setup.

### Runner resilience tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `FW_REAPER_TIMEOUT_MS` | `120000` (2min) | Dead-server detection threshold |
| `FW_STUCK_TIMEOUT_MS` | `1800000` (30min) | Stuck-task watchdog timeout |
| `FW_TASK_EXECUTION_TIMEOUT_MS` | `900000` (15min) | Per-task execution timeout; timed-out tasks reset to pending |
| `FW_LEASE_DURATION_MS` | derived: `max(5min, execution_timeout + 1min)` = **16min** by default | Task lease; renewed by handler heartbeat. The 5min is only a *floor* — the lease must outlast the execution timeout so the owning runner's watchdog resets a wedged task before another runner's lease-expiry reclaim runs it a **second time**. An explicit value below that floor is **clamped up** (with a one-time warning) rather than honoured: it's a correctness invariant, not a preference. See [paper-timeout-interactions.md](docs/thesis/paper-timeout-interactions.md) §4.1 |

Examples/domains can override these defaults — local examples via a `runner.env` file in their directory, standalone domain packages via `runner_env={...}` on their `DomainPackage`. The `start-runner` script applies them automatically. Handlers that perform blocking I/O (where heartbeats cannot fire) should register with `timeout_ms=0` and rely on the global execution timeout instead.

### MCP server

The MCP server (`python -m facetwork.mcp`) exposes FFL compiler tools, runtime management, and a PostGIS query tool. Configure it in `.mcp.json` for Claude Code integration.

**Facet capability index** (`fw_capabilities`): the facet-level analogue of `fw_catalog_search` (which finds workflows). Searches the deployed/seeded facet library — or an FFL `source` snippet you're authoring — and returns ranked facets `{qualified_name, purpose, signature, params, returns, namespace, is_event, effect, cost}` so an LLM discovers composable primitives by *intent* ("what operates on a GeoJSON path?", "reverse geocode") instead of guessing names. Facets may declare **effect/cost** via `with Effect(kind="pure"|"external"|"io")` / `with Cost(tier="free".."expensive")` mixins (cost is also inferred from a `with Timeout(minutes=…)`); `fw_capabilities(effect=, max_cost=)` filters on them so the composer prefers pure/cheap primitives and sees which steps hit an engine. Built from the compiled FFL (`emit_dict` / a flow's `compiled_ast`) by `facetwork/capabilities/`; pairs with a domain tag vocabulary (e.g. `osm.Vocab.ResolveTag`, NL term → `key=value`) for NL→tag, together turning compose-a-workflow into *lookup-then-compose*. See [docs/architecture/composable-facet-library.md](docs/architecture/composable-facet-library.md) §6.

**Reuse-first catalog matching** (`fw_catalog_match`): the workflow-level counterpart — before authoring, match an NL `request` against the catalog **by intent** (each revision's authoring `summary`, weighted over title/tags/description/facets) and get a `reuse` / `review` / `author_new` verdict + the best candidate's `param_schema` to fill. Reuse an existing workflow (run via `fw_catalog_run`) instead of re-authoring; a miss → author one, which then joins the catalog. See `CatalogService.match`.

**PostGIS query tool** (`afl_postgis_query`): runs read-only SQL against the OSM database. Write operations are blocked at two levels (SQL keyword filter + `default_transaction_read_only=on`). Schema:
- `osm_nodes` (osm_id, region, tags JSONB, geom Point)
- `osm_ways` (osm_id, region, tags JSONB, geom LineString)
- `osm_import_log` (region, node_count, way_count, imported_at)

Tags are JSONB — query with `tags->>'key'` or `tags?'key'`. Common tags: `amenity`, `shop`, `highway`, `building`, `name`, `cuisine`. Use `ST_*` functions for spatial queries.

osm2pgsql-compatible views (zero-storage, auto-created by `ensure_schema`):
- `planet_osm_point` — nodes with flattened tag columns (name, amenity, shop, highway, building, tourism, place, etc.)
- `planet_osm_line` — ways with flattened tag columns (highway, railway, waterway, surface, lanes, etc.)
- `planet_osm_roads` — filtered ways where `highway` or `railway` is present

### Step recovery actions

The dashboard step detail page has four recovery actions (Retry / Retry All Errors / Reset Block / Re-run From Here) — see [docs/reference/dashboard.md](docs/reference/dashboard.md). **Re-run From Here** is the primary tool after changing data or handler code: downstream steps are deleted and cleanly re-created.

### PostGIS data management + local-first import

Moved to the `postgis-import` skill (`.claude/skills/postgis-import/SKILL.md`) — vacuum/tune/kill-vacuum and the disposable local import instance (`FW_IMPORT_POSTGIS_URL`).

### Workflow repair

When a workflow gets stuck (e.g. after server restarts, MongoDB downtime, or premature runner completion), use `repair-workflow` to diagnose and fix all issues at once:

```bash
fw maint repair-workflow <runner_id>         # apply repairs
fw maint repair-workflow --dry <runner_id>   # preview without changes
```

Also available as a dashboard button ("Repair Workflow" on workflow detail page) and MCP tool (`afl_repair_workflow`).

The repair performs seven checks:
1. **Runner state** — if completed/failed but has non-terminal work, resets to running
2. **Orphaned tasks** — running tasks on dead/shutdown servers → pending
3. **Transient step errors** — connection/timeout errors → retry (EventTransmit)
4. **Ancestor blocks** — resets errored ancestors so execution resumes
5. **Dead-lettered tasks** — re-enqueued with the retry count reset, steps + ancestors fixed
6. **Inconsistent steps** — steps marked Complete but with failed tasks → reset to EventTransmit
7. **Stranded block steps** — every task terminal, nothing errored, yet block-level steps
   never cascaded, so the runner stays `running` indefinitely. Checks 1–6 all look
   elsewhere, so this state used to report *"No issues found"*: the national
   county-atlas fan-out sat like that for **49 hours** with 3,290/3,290 tasks complete
   and 314 steps at `blocks.Begin`/`block.execution.Continue`. Detection is
   conservative — it requires EVERY task terminal, excludes `EventTransmit` (that means
   "awaiting a task"), and ignores steps younger than 15 min so a live cascade is never
   mistaken for a stall. It also terminalises the runner once every step and task is
   terminal, so a repaired run stops showing as active. The CLI exits non-zero if it
   detects stranded steps it could not resume.

All three surfaces — CLI, dashboard button, `fw_repair_workflow` MCP tool — go through
`facetwork.runtime.workflow_repair.repair_workflow`, which is where the resume lives
(the store only *detects* check 7; advancing a step needs the evaluator, which the
persistence layer must not import). Call that helper, never `store.repair_workflow`
directly, or the surface will report stranded steps without fixing them.

**Preventative**: Runners now verify all tasks are terminal before marking a workflow as completed.

### Terminating abandoned workflows

For runs that retry/repair can't recover (e.g. a test that failed at the
handler layer because of a missing API key or removed binary), use
`fw maint terminate-workflow` to mark the runner, all non-terminal steps,
and pending/running tasks as terminal so the stuck-step sweep stops
re-processing them.

```bash
fw maint terminate-workflow <runner_id> [<runner_id> ...]
fw maint terminate-workflow --workflow osm.UnitedStates.analysis.AnalyzeRegion  # all stuck runs of this workflow
fw maint terminate-workflow --workflow Foo --workflow Bar --dry                 # preview
```

Use this only after deciding the work isn't recoverable — `repair-workflow`
is the right call for transient failures.

### Graceful runner shutdown
Use `fw runner drain` instead of `fw runner stop` when you need running tasks reset to pending. Each drained task gets a step log entry for audit visibility.

### How Claude should build and review

**Proactive design review:** When building or modifying any component, apply distributed systems best practices (Kleppmann, Nygard, Temporal) proactively. Before implementing, flag design decisions that violate known patterns — don't wait for bugs to surface. Specifically:
- For every state transition: "what if this crashes halfway?" Design the recovery path.
- For every timeout: make it heartbeat-aware. Distinguish start-to-close from last-activity.
- For every retry: add a max count and backoff. No infinite loops.
- For every shared resource (thread pool, connection, queue): consider isolation/bulkheads.
- For every log message at WARNING+: include a qualified human-readable name, not just IDs.
- For every ID: distinguish definition IDs (shared/immutable) from execution IDs (unique per run).
- For every network binding: default to `0.0.0.0`, not `127.0.0.1`.
- For every error handler: never silently return empty defaults. Fail explicitly or re-raise.

**Domain research before implementation:** Before building any handler, workflow, or integration, research the domain's established best practices, data models, and known pitfalls. For example:
- OSM/geospatial: osmium processing patterns, PostGIS indexing strategies, coordinate system conventions, bulk import best practices (COPY vs INSERT, WAL tuning, autovacuum management).
- Genomics: GATK best practices, reference genome conventions, VCF format requirements.
- Financial/risk: market data vendor conventions, time series storage patterns, numerical precision requirements.
- ETL/data pipelines: idempotent loads, schema-on-read vs schema-on-write, backfill strategies.

Apply this research to the design before writing code. Flag domain-specific constraints that affect architecture (e.g. "PostGIS bulk imports should disable autovacuum during load" or "OSM PBF files must be processed in a single pass — no random access").

See `docs/architecture/lessons-learned.md` for the full catalogue of requirements and the future roadmap.

### How Claude should review changes

**Language/compiler correctness:**
- Parsing errors must include line/column
- Grammar must be LALR-compatible (no conflicts)
- AST nodes must use dataclasses
- Emitter output uses `declarations` only (not categorized keys)

**Testing requirements:**
- All grammar constructs must have parser tests
- Error cases must verify line/column reporting
- Emitter must round-trip all AST node types

**Code quality:**
- Type hints on all functions
- Docstrings on public API
- No runtime dependencies beyond lark (dashboard, mcp deps are optional)
