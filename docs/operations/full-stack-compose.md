# Full-stack Docker Compose

A one-command development environment: MongoDB + PostGIS + Jenkins +
the Facetwork dashboard + the main runner + **one dedicated runner per
fwh_\* example repo**. Everything wired together on a single Docker
network, with `/Volumes/afl_data` (or your chosen path) mounted into
every service that needs it.

| Group | Services |
|-------|----------|
| Infrastructure | `mongodb`, `postgis`, `jenkins` |
| Facetwork core | `dashboard` (port 8080), `runner` (for in-repo examples), `seed` (one-shot) |
| Per fwh_* repo | `runner-anthropic`, `runner-osm-geocoder`, `runner-osm-lz`, `runner-noaa-weather`, `runner-jenkins-example`, `runner-census-us`, `runner-genomics`, `runner-sensor-monitoring` |

The compose file is [`docker-compose.full-stack.yml`](../../docker-compose.full-stack.yml);
the wrapper script is [`fw single full-stack`](../../scripts/lib/single/full-stack).

## Why one runner per example

Each standalone `fwh_*` repo has its own pip dependencies (e.g.
`fwh_osm` pulls `osmium`, `shapely`, `pyproj`, `folium`; `fwh_anthropic`
pulls `anthropic` + optional `claude-agent-sdk` + `mcp`). Combining
them into one container would force the union of everyone's deps on
every restart and couple their upgrade schedules. Per-runner containers
keep each example's environment small, independently rebuildable, and
runnable in isolation (you can `docker compose up runner-anthropic`
without booting the rest).

Each example runner uses one generic image
(`docker/Dockerfile.example-runner`) and bind-mounts
`~/fw_handlers/<repo>` into `/handlers/<repo>`. At container startup
the entrypoint (`docker/entrypoint-example-runner.sh`) does:

1. `pip install -e /handlers/<repo>[<extras>]` (editable, so handler
   edits on the host land in the container with no rebuild)
2. `python -m facetwork.domains --seed <name>` — registers handler
   routing in MongoDB **and** compiles the example's FFL into a
   `FlowDefinition` + `WorkflowDefinition`s so its workflows show up in
   the dashboard's Flows tab. `--seed` is idempotent: seeded under
   `example:<name>`, and re-running **reuses the existing flow + workflow
   UUIDs** rather than regenerating them. This is load-bearing for
   resilience — any in-flight bootstrap task (`fw:execute:<Workflow>`)
   carries `flow_id` + `workflow_id` in its payload; if a runner
   restarts (and re-seeds) while that task is queued, regenerating IDs
   would orphan it with "Flow not found". The seed code lives in
   [`facetwork/examples/__init__.py:seed_example_flows`](../../facetwork/examples/__init__.py);
   the invariant is locked by `test_reseed_preserves_uuids`.
3. `exec python -m facetwork.runtime.runner --registry`

In `--registry` mode the runner loads handler registrations from the DB but
only advertises (and therefore claims tasks for) facets whose handler module
is *importable in this container*. Since each example runner only bind-mounts
its own `fwh_<repo>`, `runner-anthropic` claims `anthropic.*` tasks,
`runner-osm-geocoder` claims `osm.*` tasks, etc. — a runner never picks up a
task it can't run. (Built-in `fw:execute` / `fw:resume` tasks are claimed by
every runner.) See [runtime-impl.md §17.1.1](../reference/runtime-impl.md).

## Prerequisites

- **Docker** with `docker compose` (Docker Desktop on macOS works; the
  stack uses ~3 GB of images on first build).
- **`~/fw_handlers/fwh_*` directories** — clone the standalone example
  repos with the install helper:

  ```bash
  fw install example --all
  ```

  Each fwh_* repo can be in any state (clean clone, dirty edits, etc.).
  The example runner mounts it read-write so pip can write its
  `egg-info`. See [Installing example packages](#installing-example-packages)
  below.

- **External data dir** — `/Volumes/afl_data` on macOS by default, or
  any host path with enough space. Override via `FW_DATA_DIR` in
  `.env.full-stack`. Subdirs the runners use:

  | Path | Used by |
  |------|---------|
  | `cache/` | Generic handler caches |
  | `output/` | All workflow outputs (visible in the dashboard) |
  | `osm/` | Geofabrik PBF mirror (fwh_osm) |
  | `osm-output/` | OSM-derived artifacts (fwh_osm) |

## First-time setup

```bash
# 1. Clone + install every example
fw install example --all

# 2. (Optional) Copy the env template and edit for secrets
cp .env.full-stack.example .env.full-stack
$EDITOR .env.full-stack         # set ANTHROPIC_API_KEY, CENSUS_API_KEY, etc.

# 3. Bring up the full stack (builds images on first run)
fw single full-stack up

# 4. Open the dashboard
open http://localhost:8080
```

First boot pulls `mongo:7`, `postgis/postgis:16-3.4`, and
`jenkins/jenkins:lts`, then builds three custom images (dashboard,
runner, example-runner) — count on 3–5 minutes. Subsequent `up`
runs use cached images and are fast.

After `up`, each example runner spends ~10–30 seconds pip-installing
its bind-mounted repo. Watch the progress with:

```bash
fw single full-stack logs            # tail all services
fw single full-stack logs runner-anthropic   # one service
```

## fw single full-stack

One entry point for everything. Picks `.env.full-stack` from the repo
root when present, otherwise falls back to the checked-in
`.env.full-stack.example`.

```text
fw single full-stack                       start every service (detached) [default]
fw single full-stack up                    same as no args
fw single full-stack up runner-anthropic   start specific services only
fw single full-stack down                  stop + remove containers (keeps named volumes)
fw single full-stack down --volumes        also wipe mongodb/postgis/jenkins data
fw single full-stack stop [SERVICE...]     stop without removing
fw single full-stack restart [SERVICE...]  restart selected (or all) services
fw single full-stack status                ps with service / status / ports
fw single full-stack logs                  tail -f all services
fw single full-stack logs SERVICE          tail -f one service
fw single full-stack logs --tail 100 SVC   snapshot
fw single full-stack build [SERVICE...]    docker compose build
fw single full-stack rebuild [--no-cache] [SERVICE...]   build + recreate containers
fw single full-stack pull                  pull mongo / postgis / jenkins images
fw single full-stack config                resolved compose config (env expanded)
fw single full-stack exec SERVICE          /bin/bash shell inside a container
fw single full-stack exec SERVICE -- CMD   run a one-shot command
fw single full-stack help                  this list
```

### Environment knobs

All defaults documented in [`.env.full-stack.example`](../../.env.full-stack.example).
Copy that file to `.env.full-stack` and edit; the wrapper picks it up
automatically.

| Variable | Default | Purpose |
|----------|---------|---------|
| `FWH_HANDLERS_ROOT` | `$HOME/fw_handlers` | Where the fwh_* clones live |
| `FW_DATA_DIR` | `/Volumes/afl_data` | External disk mount point |
| `FW_MONGODB_DATABASE` | `facetwork` | Mongo DB used by all services |
| `DASHBOARD_PORT` | `8080` | Dashboard UI |
| `FW_MONGODB_PORT` | `27017` | MongoDB |
| `POSTGRES_PORT` | `5432` | PostGIS |
| `JENKINS_UI_PORT` | `9090` | Jenkins web UI |
| `JENKINS_AGENT_PORT` | `50000` | Jenkins agent port |
| `POSTGRES_DB / USER / PASSWORD` | `afl_gis` / `afl` / `afl` | PostGIS creds |
| `FW_MAX_CONCURRENT` | `4` | Max concurrent tasks per runner |
| `FW_POLL_INTERVAL_MS` | `1000` | Runner poll interval |
| `ANTHROPIC_API_KEY` | _(unset)_ | Required for live Claude calls |
| `ANTHROPIC_EXTRAS` | `agent_sdk,mcp` | fwh_anthropic pip extras |
| `CENSUS_API_KEY` | _(unset)_ | Required for fwh_census_us live calls |
| `FW_OSM_REPLICAS` | `1` | # of `runner-osm-geocoder` replicas |
| `FW_OSM_LZ_REPLICAS` | `1` | # of `runner-osm-lz` replicas |
| `FW_ANTHROPIC_REPLICAS` | `1` | # of `runner-anthropic` replicas |
| `FW_WEATHER_REPLICAS` | `1` | # of `runner-noaa-weather` replicas |
| `FW_JENKINS_REPLICAS` | `1` | # of `runner-jenkins-example` replicas |
| `FW_CENSUS_REPLICAS` | `1` | # of `runner-census-us` replicas |
| `FW_GENOMICS_REPLICAS` | `1` | # of `runner-genomics` replicas |
| `FW_MONITOR_REPLICAS` | `1` | # of `runner-sensor-monitoring` replicas |
| `FW_RUNNER_REPLICAS` | `1` | # of main `runner` replicas |

### Workload partitioning (task lists)

A task's `task_list_name` is **derived from the facet's top-level namespace** —
not configured. A task for `osm.cache.Download` is tagged with list `osm`; a
runner serving `osm.*` facets polls `osm` (the namespaces of the handlers it
loaded). Because both sides compute the label from the same qualified name,
they can never disagree, and per-domain isolation is automatic: an `osm.*`
workflow's tasks all land on `osm` (where only the osm runners listen), an
`anthropic.*` workflow's on `anthropic`, etc.

The consequence is that a flooded queue (say, an hour-long OSM import) can never
starve unrelated work — the `runner-anthropic` pool's `find_one_and_update`
query never matches the `osm` backlog. See
[runtime-impl.md §17.1.2](../reference/runtime-impl.md) and
[thesis §5.4](../thesis/thesis.md).

There is nothing to configure or keep in sync — partitioning follows the
namespaces in the handler set. (Removed in productionization: the old
`FW_WORKFLOW_TASK_LIST_MAP` prefix map and per-runner `--task-list` routing.)

### Scaling runners

Every `runner-*` service is scalable: N replicas all bind-mount the same
`fwh_*` repo, all poll the same task list, all race for tasks via
MongoDB's atomic claim. There is no leader, no membership protocol —
adding a replica is one more concurrent caller of `find_one_and_update`.
Empirically, throughput is near-uniform: three osm-geocoder replicas on
10 concurrent `AnalyzeRegion` runs distributed 330 step tasks 112 / 109 /
109 with no fairness mechanism (thesis §5.4.1).

**Persistent scaling — edit the env file.** `FW_<EXAMPLE>_REPLICAS`
checkpoints the topology in `.env.full-stack`. `fw single full-stack up`
sources the file and emits the matching `--scale runner-<name>=N` flag
on every invocation. The defaults are all `1`, commented out — uncomment
and adjust:

```bash
# .env.full-stack
FW_OSM_REPLICAS=3            # 3 osm-geocoder replicas
FW_GENOMICS_REPLICAS=2       # 2 genomics replicas
```

Then any `fw single full-stack up` or `rebuild` produces the desired fleet
shape. Replicas auto-name as `facetwork-runner-osm-geocoder-1/-2/-3` —
use the service name (not container name) with logs/exec:

```bash
fw single full-stack logs runner-osm-geocoder    # all 3 replicas multiplexed
fw single full-stack exec runner-osm-geocoder /bin/bash   # picks one
```

**Ad-hoc scaling — `--scale` on the command line.** Anything after `up`
or `rebuild` passes through to `docker compose`:

```bash
fw single full-stack up -d --scale runner-osm-geocoder=5 runner-osm-geocoder
```

Caveat: this is *not sticky*. A subsequent plain `fw single full-stack up`
re-applies the env-file's `FW_OSM_REPLICAS` (default `1`) and scales
back down. Use the env-file form unless you want a one-shot experiment.

**Why `container_name:` is unset on the runner services.** Docker
Compose can't scale a service with a pinned `container_name`. The compose
file deliberately omits the pin from every `runner*` service so they're
scalable; the infra services (`mongodb`, `postgis`, `jenkins`,
`dashboard`, `seed`) keep their pins so `docker logs facetwork-mongodb`
etc. continue to work.

## Using `docker compose` directly

`fw single full-stack` is a thin wrapper around
`docker compose --env-file <env-file> -f docker-compose.full-stack.yml …`.
If you'd rather drive Compose yourself, two flags do the work the wrapper
does for you:

- **`-f docker-compose.full-stack.yml`** — on *every* command. Without it
  Compose picks up the default `docker-compose.yml` (the minimal stack:
  just MongoDB + dashboard + one runner), not the full stack.
- **`--env-file .env.full-stack`** — Compose only auto-loads `.env`, not
  `.env.full-stack`. Without it you still get a working stack from the
  `${VAR:-default}` fallbacks baked into the compose file, but secrets
  (`ANTHROPIC_API_KEY`, `CENSUS_API_KEY`) and any non-default ports/paths
  won't be applied. (You can also just `export` those vars in your shell.)

A session alias keeps the examples short:

```bash
alias fsc='docker compose --env-file .env.full-stack -f docker-compose.full-stack.yml'
```

```bash
# --- start ---
fsc up -d                              # everything, detached (incl. the one-shot seed)
fsc up -d mongodb postgis dashboard    # just infra + dashboard
fsc up -d --no-deps runner-anthropic   # one runner, skip its depends_on chain
fsc up -d --build dashboard runner     # rebuild custom images, then (re)start those

# --- inspect ---
fsc ps                                 # services / status / ports
fsc logs -f                            # tail everything
fsc logs -f runner-anthropic           # one service
fsc logs --tail 200 runner             # snapshot of recent lines
fsc config                             # resolved config with all env vars expanded

# --- iterate ---
fsc build dashboard runner             # rebuild the custom images
fsc restart runner-anthropic           # pick up edits in ~/fw_handlers/fwh_anthropic
fsc exec runner-anthropic /bin/bash    # shell into a container
fsc exec runner-osm-geocoder python -c "import osm_geocoder; print('ok')"
fsc run --rm seed                      # re-run the dashboard seeding on demand

# --- stop / tear down ---
fsc stop                               # stop containers, keep them
fsc stop runner-anthropic              # stop just one
fsc down                               # remove containers, KEEP named volumes
fsc down -v                            # also wipe mongo / postgis / jenkins data
```

Throwaway run with no env file at all (relies on the compose file's
built-in defaults):

```bash
docker compose -f docker-compose.full-stack.yml up -d
docker compose -f docker-compose.full-stack.yml down
```

Override individual knobs inline instead of editing the env file:

```bash
DASHBOARD_PORT=9000 ANTHROPIC_API_KEY=sk-ant-... \
  docker compose -f docker-compose.full-stack.yml up -d dashboard runner-anthropic
```

## Installing example packages

The full stack needs the 8 standalone `fwh_*` repos cloned under
`$FWH_HANDLERS_ROOT`. Two helpers handle this:

### fw install example

Registry-driven; knows all 8 example repos.

```bash
fw install example --list              # see the registry
fw install example anthropic --check   # clone, pip install -e, verify
fw install example --all               # clone+install every example
fw install example osm-geocoder noaa-weather   # one or more by name
fw install example anthropic --extras agent_sdk,mcp
fw install example --pull-only anthropic       # just clone/git-pull
fw install example --skip-pull anthropic       # pip install existing clone only
```

Defaults clone into `~/fw_handlers/` and pip-install into the local
`.venv/` (auto-detected). With `--check`, verifies the example is
discoverable via `facetwork.domains.discover_entry_point_domains()`.

### fw install anthropic

Thin wrapper for `fwh_anthropic` specifically. Maps `--agent-sdk` /
`--mcp` / `--all` to the matching pip extras, then (with `--check`)
also reports whether the `claude` CLI is on `PATH` and whether
`ANTHROPIC_API_KEY` is set — the two things that aren't pip-installable
but the package needs at runtime.

```bash
fw install anthropic                   # core (anthropic SDK only)
fw install anthropic --all --check     # + agent_sdk + mcp + env probes
fw install anthropic --mcp             # core + mcp extras only
```

## Service architecture

```text
┌──────────────────────────────────────────────────────────────────────┐
│ host                                                                 │
│                                                                      │
│   ~/fw_handlers/                              /Volumes/afl_data/     │
│     ├ fwh_anthropic/                            ├ cache/             │
│     ├ fwh_osm/                                  ├ output/            │
│     ├ fwh_osm_lz/                               ├ osm/               │
│     ├ fwh_noaa_weather/                         └ osm-output/        │
│     ├ fwh_jenkins/                                                   │
│     ├ fwh_census_us/                                                 │
│     ├ fwh_genomics/                                                  │
│     └ fwh_sensor_monitoring/                                         │
│       │                                                              │
│       │ bind-mount  (rw)                                             │
│       ▼                                                              │
│ ┌────────────────────────────────────────────────────────────────┐   │
│ │ docker network: facetwork_default                              │   │
│ │                                                                │   │
│ │  mongodb ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │   │
│ │     ▲                                                          │   │
│ │     │  reads handler_registrations / flows / workflows         │   │
│ │     │                                                          │   │
│ │  dashboard ◄──── HTTP :8080 ─────── host                       │   │
│ │     │                                                          │   │
│ │     │ FW_POSTGIS_URL                                          │   │
│ │     ▼                                                          │   │
│ │  postgis ─◄── osm-geocoder / osm-lz runners write here         │   │
│ │     │                                                          │   │
│ │     │  init scripts in docker/postgis-init/ create empty       │   │
│ │     │  osm_import_log + osm_nodes + osm_ways tables            │   │
│ │     │                                                          │   │
│ │  jenkins ─── runner-jenkins-example talks to it via            │   │
│ │     │       JENKINS_URL=http://jenkins:8080                    │   │
│ │     │                                                          │   │
│ │  runner ─── built-in examples (hello-agent, aws-lambda, etc.)  │   │
│ │                                                                │   │
│ │  runner-anthropic, runner-osm-geocoder, runner-osm-lz,         │   │
│ │  runner-noaa-weather, runner-jenkins-example, runner-census-us,│   │
│ │  runner-genomics, runner-sensor-monitoring                     │   │
│ │     │                                                          │   │
│ │     │ each one pip-installs its bind-mounted fwh_* repo,       │   │
│ │     │ registers handlers + seeds its workflows in MongoDB,     │   │
│ │     │ then polls for tasks                                     │   │
│ │     ▼                                                          │   │
│ │  seed (one-shot) ─── seeds the in-repo example flows on first  │   │
│ │                       boot so the dashboard isn't empty        │   │
│ └────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

## Common operations

### Run a workflow

1. Open http://localhost:8080
2. Click **Workflows** → pick one (any from a discoverable example)
3. **New** to create a run, fill parameters, **Run**
4. Watch step-by-step progress on the detail page

The dashboard dispatches tasks to MongoDB; the matching example runner
picks them up automatically.

### Inspect task-list health

`/tasks` shows a filter row with one pill per task list, formatted as
`<name> <task-count> ·<runner-count>r`:

- A red `·0r` badge means tasks exist but no runner is polling that
  namespace — the silent-idleness failure mode (start a runner whose
  handler set covers that namespace).
- A pill with `0 ·Nr` means runners poll a namespace nobody submits work
  to — usually fine (an idle example).
- Click any pill to filter the task table to that list; combines with
  the state subnav (`/tasks?task_list=osm&state=pending`).

### Tail logs across runners

```bash
fw single full-stack logs                    # everything
fw single full-stack logs runner-anthropic   # one service
fw single full-stack logs --tail 200 runner  # built-in runner snapshot
```

### Shell into a runner

```bash
fw single full-stack exec runner-anthropic              # /bin/bash
fw single full-stack exec runner-osm-geocoder -- python -c \
    "from osm_geocoder.handlers.downloads.postgis_importer import *; print('ok')"
```

### Rebuild an image after editing facetwork itself

The `facetwork/` source and the entrypoints are baked into the images, so
changes to them need a rebuild + container recreate. `rebuild` does both:

```bash
fw single full-stack rebuild                    # rebuild every custom image, recreate containers
fw single full-stack rebuild dashboard runner   # just those services
fw single full-stack rebuild --no-cache runner-anthropic   # from-scratch rebuild of one

# (lower level — the two steps `rebuild` runs for you)
fw single full-stack build dashboard runner
fw single full-stack up -d dashboard runner
```

A plain `fw single full-stack up` after a `down`/`down --volumes` does **not**
rebuild — it only builds images that are missing — so use `rebuild` when
you've changed `facetwork/` or a Dockerfile/entrypoint.

Editing files inside `~/fw_handlers/<repo>` does **not** require a
rebuild — they're bind-mounted as editable installs. Just restart the
specific runner:

```bash
fw single full-stack restart runner-anthropic
```

### Tear down

```bash
fw single full-stack down              # stop + remove containers, KEEP volumes
fw single full-stack down --volumes    # also wipe mongo / postgis / jenkins data
```

Named volumes (`facetwork_mongodb_data`, `facetwork_postgis_data`,
`facetwork_jenkins_home`) persist handler registrations, seeded flows,
imported OSM data, and Jenkins job history across restarts. Use
`--volumes` for a clean slate.

## Per-runner notes

### runner-anthropic

- Pulls `anthropic` Python SDK transitively. Extras gated by
  `ANTHROPIC_EXTRAS` (default `agent_sdk,mcp`).
- Set `ANTHROPIC_API_KEY` for live API calls. Without it, the runner
  starts fine but Claude-calling workflows fail at dispatch.
- Registers 16 facets across Messages / Batch / Files / Agent SDK /
  Claude Code / Computer Use + the `DocumentQA` composition workflow.

### runner-osm-geocoder / runner-osm-lz

- Both runners get `FW_POSTGIS_URL=postgresql://afl:afl@postgis:5432/afl_gis`.
- `runner-osm-lz` is a pure workflow catalog (0 handlers); it
  dispatches to handlers registered by `runner-osm-geocoder`, so the
  compose `depends_on:` orders them correctly.
- `/Volumes/afl_data/osm/` should contain the Geofabrik PBFs you want
  to import (the runner won't fetch them — there are dedicated handler
  facets for that).

### runner-jenkins-example

- Talks to the in-stack Jenkins via `JENKINS_URL=http://jenkins:8080`.
- For local-only Jenkins testing, default creds work out of the box;
  for a real CI server, mount Jenkins config or set `JENKINS_USER` +
  `JENKINS_TOKEN`.

### runner-census-us

- `CENSUS_API_KEY` needed for live API calls. Free key from
  https://api.census.gov/data/key_signup.html.

## Troubleshooting

### Dashboard is empty (no flows / no handlers)

Check that the seed service finished and the runners registered:

```bash
fw single full-stack logs seed | tail -20
docker exec facetwork-mongodb mongosh --quiet facetwork --eval \
    'print("flows:", db.flows.countDocuments(),
           "workflows:", db.workflows.countDocuments(),
           "handler_registrations:", db.handler_registrations.countDocuments())'
```

If counts are non-zero but the dashboard is still empty, hard-refresh
the browser tab (Cmd+R or Ctrl+R).

### PostGIS panel shows "could not connect"

```bash
# Are the OSM tables present?
docker exec facetwork-postgis psql -U afl -d afl_gis -c "\dt"
# Should list osm_import_log, osm_nodes, osm_ways, spatial_ref_sys.
```

If those are missing, the postgis-init scripts didn't run. Recreate
the postgis volume:

```bash
fw single full-stack down --volumes
fw single full-stack up
```

Init scripts in `docker/postgis-init/` only run on first volume
init — they're idempotent across container restarts but ignored if
the volume already has a database.

### Runner can't reach MongoDB / PostGIS

Service-to-service traffic uses the in-stack network. Use service
names (`mongodb`, `postgis`, `jenkins`) — not `localhost` or
`afl-mongodb` — in connection strings inside containers. From the
host, use `localhost:<port>` where `<port>` is whatever's mapped in
`.env.full-stack`.

### Example runner keeps restarting

```bash
fw single full-stack logs runner-anthropic | tail -50
```

Common causes:
1. `~/fw_handlers/fwh_<name>/` doesn't exist (run
   `fw install example <name>`).
2. The bind-mount source path is wrong (check `FWH_HANDLERS_ROOT`).
3. Pip install failed — usually network issue or a syntax error in
   the example's source. Fix on the host, then
   `fw single full-stack restart runner-<name>`.

### "Container name already in use" on up

A prior `docker compose down` left an orphan container. Clean up:

```bash
docker rm -f $(docker ps -aq --filter "name=facetwork-")
fw single full-stack up
```

### Need to point at a remote MongoDB / PostGIS

Edit `.env.full-stack`:

```bash
# Skip the in-stack mongodb/postgis services
FW_MONGODB_URL=mongodb://my-mongo-host:27017
FW_POSTGIS_URL=postgresql://user:pass@my-postgis-host:5432/afl_gis
```

Then start only the services you need (omit `mongodb` / `postgis`):

```bash
fw single full-stack up dashboard runner runner-anthropic
```

## Related

- [`docs/operations/deployment.md`](deployment.md) — non-Docker / multi-host deployment
- [`docs/getting-started/beginners-guide.md`](../getting-started/beginners-guide.md) — workflow basics
- [`fw install example`](../../scripts/lib/install/domain) — registry of standalone example repos
- [`docker-compose.full-stack.yml`](../../docker-compose.full-stack.yml) — the source of truth
