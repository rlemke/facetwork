# Facetwork Deployment & Operations Guide

This guide covers deploying, configuring, monitoring, and operating Facetwork in development and production environments.

## Deployment Models

Facetwork supports two equivalent deployment models — **Docker** and **Local (non-Docker)**. Both use the same microservice architecture and coordinate through a shared MongoDB instance. You can mix them freely: run MongoDB externally, some runners in Docker, others as local processes.

### Microservice Architecture

Regardless of deployment model, the architecture is the same:

```
                 +-----------+
  Browser ------>| Dashboard |
                 |  (8080)   |
                 +-----+-----+
                       |
    +--------+---------+---------+--------+
    |        |                   |        |
+---v--+ +---v---+          +---v---+ +--v---+
|Runner| |Runner |   ...    |Runner | |Runner|
+---+--+ +---+---+          +---+---+ +--+---+
    |         |                  |        |
    +---------+------------------+--------+
                       |
              +--------v--------+
              |     MongoDB     |
              +-----------------+
```

**Key coordination mechanisms** (identical in Docker and local mode):

- **Atomic task claiming**: `claim_task()` uses MongoDB `find_one_and_update` — only one runner claims each task, regardless of where it runs
- **Server registration**: Each runner registers in the `servers` collection with a unique UUID and hostname, and sends periodic heartbeats
- **Handler registrations**: Shared in MongoDB — all runners see the same handler modules
- **Orphan reaper**: If any runner dies (stale heartbeat), other runners reset its in-progress tasks back to `pending` for retry

### Quick Start: Local Mode (Recommended for Development)

Run everything as local Python processes — no Docker required. Only needs MongoDB reachable at `FW_MONGODB_URL`.

```bash
# One command: stop old runners, verify MongoDB, seed examples, start runners + dashboard
fw single up-local

# With options
fw single up-local --example osm-geocoder          # single example
fw single up-local --instances 3                    # 3 concurrent runners
fw single up-local --no-seed                        # skip seeding
fw single up-local -- --log-format text             # plain-text runner logs

# Open the dashboard
open http://localhost:8080
```

Or manage runners directly:

```bash
# Register handlers and start runner(s) + dashboard
fw runner start -- --log-format text
fw runner start --example hiv-drug-resistance --instances 3

# Stop all local runners
fw runner stop
```

### Quick Start: Docker Mode

```bash
# Start the stack (dashboard, runner, agents)
docker compose up -d

# Or use the setup script for a guided bootstrap
fw install setup                              # defaults: 1 runner, 1 agent
fw install setup --runners 3 --agents 2       # scaled deployment
fw install setup --build                      # rebuild images first

# One-command pipeline: teardown → rebuild → setup → seed
fw single up

# Open the dashboard
open http://localhost:8080
```

For a richer dev environment that runs **one container per
standalone `fwh_*` example** (anthropic, osm-geocoder, osm-lz,
noaa-weather, jenkins, census-us, genomics, sensor-monitoring) on
top of MongoDB + PostGIS + Jenkins + the dashboard, use the
**full-stack compose**:

```bash
fw install example --all     # clone + pip install every fwh_* repo
fw single full-stack up             # boots all 13 services
open http://localhost:8080
```

See [Full-stack Docker Compose](full-stack-compose.md) for the full
guide (architecture, scripts, env knobs, per-runner notes,
troubleshooting).

### Comparing Docker vs Local Mode

| Aspect | Docker Mode | Local Mode |
|--------|-------------|------------|
| **Startup** | `fw single up` or `docker compose up` | `fw single up-local` or `fw runner start` |
| **MongoDB** | Can run in Docker or external | Must be external (running separately) |
| **Handler loading** | Container-internal paths, `RegistryRunner` | Host filesystem paths, `RegistryRunner` |
| **Scaling** | `docker compose up --scale runner=N` | `fw runner start --instances N` |
| **Process isolation** | Full container isolation | OS process isolation |
| **File paths** | Container paths (`/app/...`) | Host paths (`/Users/...`) |
| **Shared data** | Docker volumes or bind mounts | Direct filesystem access |
| **Log output** | `docker compose logs -f runner` | Inline in terminal (stdout/stderr) |
| **Stop** | `docker compose down` | `fw runner stop` |
| **Dependencies** | Docker Desktop | Python 3 + `.venv` with FFL packages |

**Important**: Docker agents and local runners should not be mixed for the same handler registrations. Docker containers use container-internal `sys.path` and cannot load handler modules registered with host filesystem paths, and vice versa. Stop Docker agents/runners before starting local ones:

```bash
docker compose down          # stop all Docker services
fw single up-local        # start local runners
```

### Multi-Node Distributed Execution

Both deployment models support horizontal scaling across multiple machines. Multiple runners on different hosts cooperate on the same workflow automatically — the MongoDB task queue ensures each task is claimed by exactly one runner.

**Requirements for multi-node:**

1. **Shared MongoDB**: All machines point to the same `FW_MONGODB_URL` (use IP or DNS hostname accessible from all nodes)
2. **Handler code**: Same repo checkout with `.venv` and dependencies installed on each machine
3. **Shared data** (optional): NFS/SMB mount for `FW_GEOFABRIK_MIRROR`, `FW_DATA_ROOT`, etc. — or let each machine download its own copies (cache misses are handled automatically)

```bash
# On each machine: start local runner(s) pointing to shared MongoDB
FW_MONGODB_URL=mongodb://db-server:27017 fw single up-local --no-seed --instances 4

# Or with remote runner management (SSH-based)
fw runner start --all --example osm-geocoder    # start on all FW_RUNNER_HOSTS
fw runner start --host worker1 --host worker2   # specific hosts
fw runner stop --all                           # stop all remote runners
fw fleet rolling-deploy --example osm-geocoder        # zero-downtime restart
```

**How it works:**

```
  Machine A                Machine B                Machine C
  +---------+              +---------+              +---------+
  |Runner x4|              |Runner x4|              |Runner x4|
  |Dashboard|              |         |              |         |
  +---------+              +---------+              +---------+
       |                        |                        |
       +------------------------+------------------------+
                                |
                       +--------v--------+
                       |   MongoDB       |
                       |  (db-server)    |
                       +-----------------+
```

Each runner independently polls the shared task queue. When a workflow creates 100 event tasks, all 12 runners (4 per machine) compete for tasks via atomic `claim_task()`. The workload distributes naturally across all available runners.

### Adding a runner server to the fleet (shared external MinIO + MongoDB)

**Server-role model — there is no "master".** A fleet has just two kinds of participant: **infra services** (MongoDB, MinIO, and the Dashboard), each identified by its **access URL only** — the fleet never enumerates their cluster members, so each can be a single node, a replica set / distributed deployment, or a managed service — and **runner servers**, which are homogeneous and stateless (`FW_SERVER_GROUP` defaults to `runner`). Runners are leaderless and don't contend (coordination is the atomic `claim_task()` in Mongo), so no box is privileged: any runner with Mongo access can also seed workflow definitions.

The full-stack compose **bundles a MinIO and a MongoDB per host**. That is wrong for a multi-server fleet: if you bring it up independently on N servers, each gets its *own* MinIO, so outputs are siloed and a merge on one server can't see another's. A fleet needs **one shared MinIO and one shared MongoDB**, reachable from every server; each additional server runs *runners only*.

Use **`docker-compose.fleet.yml`** (a thin override that drops the local-infra dependency and points the runners at the shared services — applied to **every** per-example runner via a YAML anchor) and **`fw runner start --fleet`** (the container/fleet mode converged from the old `start-worker`):

```bash
# On the server hosting the shared services (once):
docker compose -f docker-compose.full-stack.yml up -d        # MinIO :9000, MongoDB :27017
#   ensure both bind 0.0.0.0 and the ports are reachable from the other servers

# On each ADDITIONAL runner server:
cp .env.fleet.example .env.fleet
#   edit FW_MONGODB_URL + FW_S3_ENDPOINT to the shared host, FW_DATA_DIR to a
#   large LOCAL disk, FW_OSM_REPLICAS to this host's runner count
fw runner start --fleet                       # preflight both shared services, then start runners (default: osm-geocoder + osm-lz)
fw runner start --fleet --check               # preflight + validate only
fw runner start --fleet --replicas 6          # override the runner count
fw runner start --fleet --example noaa-weather --example census-us   # run ANY per-example runner in the fleet
#   (fw runner start --fleet still works — it's a back-compat shim for `start-runner --fleet`)
#   All per-example runners default to S3 storage, so every example's output finalizes
#   to the shared MinIO (nothing siloed per host) — no extra config needed.
```

`start-runner --fleet` fails fast with a clear message if the shared MongoDB or MinIO is not reachable (host/port, `/etc/hosts`, `0.0.0.0` binding, firewall), instead of letting the runners crash-loop. Under the hood it runs:

```bash
docker compose -f docker-compose.full-stack.yml -f docker-compose.fleet.yml \
  --env-file .env.fleet up -d --scale runner-osm-geocoder=$FW_OSM_REPLICAS \
  runner-osm-geocoder runner-osm-lz
```

Because the storage backend is `s3`, every step payload carries a **portable `s3://` URI any runner on any host can resolve**. So a leaf extracted on server B writes its filtered GeoJSON to the shared MinIO, the foreach aggregates the URIs through MongoDB, and the `MergeLayers` step — claimed by *whichever* runner anywhere — localizes every leaf from MinIO and merges. **Scratch stays local per server** (`FW_DATA_DIR` → a large local disk); only finalized objects cross to MinIO. This is exactly what makes the `ContinentHeatmap` subregion fan-out scale across servers: N servers × M runners each pull leaves from the shared queue in parallel, and the single merge sees them all.

#### Viewing the final map from MinIO in a browser

The merged heat-map HTML lives in MinIO. Objects are now uploaded with a correct
`Content-Type` (`.html → text/html`, `.geojson → application/geo+json`), so a
browser renders the map instead of downloading it. MinIO buckets are private, so
the raw URL returns 403 — open it one of two ways, pointing at the **shared
MinIO's network address** (not `localhost` from another machine):

```bash
# Presigned URL — time-limited, embeds the signature, no bucket change:
mc share download --expire 24h <alias>/afl-cache/osm-output/.../map.html

# Or make a prefix public-read for a shareable map gallery:
mc anonymous set download <alias>/afl-cache/osm-output
#   then: http://<minio-host>:9000/afl-cache/osm-output/.../map.html
```

The MinIO console (`http://<minio-host>:9001`) also browses/previews objects after login.

### Central fleet config — join + manage servers without per-host setup

Editing `.env.fleet` on every server doesn't scale. The fleet controller
(Phase 1) keeps the desired state **once, centrally, in MongoDB** (the
`fleet_config` collection) and has each server pull it. The only thing a server
needs to know is **how to reach Mongo** — the MinIO endpoint and replica counts
come from the config, so there is no per-server editing.

```bash
# Admin (run anywhere with Mongo access) — set the fleet config ONCE:
fw fleet set --mongo mongodb://afl-mongodb:27017 \
    --minio http://afl-minio:9000 --bucket afl-cache --osm-replicas 4 --task-list osm

fw fleet status --mongo mongodb://afl-mongodb:27017   # config version + live runners by host
fw fleet get    --mongo mongodb://afl-mongodb:27017   # show the config

# On EACH server — the same one command joins the fleet (bootstrap = Mongo URL):
mkdir -p "$HOME/afl_data"                                   # big LOCAL scratch on THIS server
fw fleet agent apply --mongo mongodb://afl-mongodb:27017 --data-dir "$HOME/afl_data"
fw fleet agent apply --dry-run                        # show the plan, start nothing
#   (with a resolvable `afl-mongodb` hostname or FW_MONGODB_URL set, the --mongo is
#    optional, but --data-dir stays — it is REQUIRED, see below.)
```

`--data-dir` is **required and per-server** — there is no `/Volumes/afl_data`
default (that path is the infra host's disk; on macOS only root can `mkdir`
under `/Volumes`). Prefer the `--data-dir` flag over exporting `FW_DATA_DIR`:
an exported env var isn't inherited by an already-running `watch` daemon or
under `sudo`/`launchd`/`systemd`, so the flag is the reliable knob. If it's
unset, `fleet-agent` fails fast naming the right knob (rather than crashing
deep in `docker compose up`), and `start-runner --fleet` separately preflights
the scratch dir for writability.

`fleet-agent apply` reads `fleet_config`, **discovers the MinIO endpoint + replica
count from it**, uses this host's `--data-dir` as local scratch, and runs `start-runner
--fleet` (via the `start-worker` shim) — which preflight-checks both shared
services and the local scratch dir, then brings the runners up. To
change the whole fleet — more runners, a different MinIO — run one `fleet set …`
(it bumps `fleet_config.version`), then reconcile on each server. Secrets stay
**local** to each host: MinIO credentials come from the host environment
(`FW_S3_ACCESS_KEY` / `FW_S3_SECRET_KEY`); only endpoints/replicas/image live in
Mongo.

**Auto-reconcile + rolling image updates (Phase 2).** Instead of re-running
`apply` by hand, run the agent as a **daemon** on each server — it watches
`fleet_config.version` and reconciles whenever it changes:

```bash
fw fleet agent watch --data-dir "$HOME/afl_data" --interval 30   # daemon (systemd/nohup); --data-dir REQUIRED
# Mongo is discovered: FW_MONGODB_URL (if valid) → server catalog (servers.json
# infra entry, resolved live — see docs/reference/server-catalog.md) → mDNS →
# the afl-mongodb /etc/hosts convention. Pass --mongo URL only to force one.

# Then drive the WHOLE fleet from one place:
fw fleet set --osm-replicas 8                       # every host rescales to 8
fw fleet set --image registry/facetwork-runner:v2  # every host pulls v2 + recreates (rolling)
fw fleet status                                     # per-host: up-to-date vs LAGGING (applied version)
```

A replica-only change just rescales (no downtime for running runners); an
**image** change triggers a pull + force-recreate. Each agent records its applied
version/image into the `fleet_agents` collection, so `fleet status` shows which
hosts are current and which are still catching up. The agent survives transient
Mongo/MinIO blips (logs and retries on the next poll) and leaves runners running
when stopped.

**Watch-daemon resilience.** The `watch` loop is hardened against the "alive but
hung" failure mode (a process that's up but has stopped reconciling): it reuses a
single Mongo client across polls (rather than leaking one per poll, which over
hours exhausts threads/sockets), bounds every Mongo op with `connectTimeoutMS` +
`socketTimeoutMS` (server-selection timeout alone doesn't cap a wedged socket
read), bounds each `docker compose` call, and arms a per-poll **watchdog** that
`os._exit`s if a reconcile blows past a ceiling — so a supervisor restarts a fresh
process and recovers any hang. Knobs: `FW_FLEET_AGENT_WATCHDOG_SECONDS` (default
900), `FW_FLEET_AGENT_DOCKER_TIMEOUT` (default 600).

**Supervised self-heal (macOS / launchd).** The watchdog only helps with a
supervisor that restarts the process. On macOS, run `fleet agent watch` under a
**launchd LaunchAgent** (`RunAtLoad` + `KeepAlive`) so it starts at login and
restarts on exit. A robust wrapper: export `FW_DATA_DIR` (+ `FW_SERVER_GROUP` if
this host isn't the default tier), start Docker Desktop if its daemon is down,
then `exec fw fleet agent watch --data-dir …`. Do NOT pin `--mongo`/
`FW_MONGODB_URL` in the wrapper: discovery falls through to the **server
catalog** (the infra entry's stable name, resolved live), so a stale address —
including a `.env` `…localhost` value on a non-infra host — simply loses the
reachability check instead of wedging the agent; a pinned URL is a break-glass
override only. A non-supervised manual `watch` (`nohup`/background) is fine too
but won't auto-restart. To
non-disruptively prove every host's daemon is live, re-set the **current** image
(`fleet set --image <current>`) — it bumps `fleet_config.version` with no container
churn and every healthy daemon advances its `applied vN` in `fleet status`.

> **Full rollout runbook** — building/pushing the runner image (buildx → registry),
> the exact `fleet set --image` flow, **what happens to running tasks during a
> recreate** (graceful drain → reaper recovery → retry/dead-letter), starting /
> stopping / draining runners from the CLI, and a comparison to
> Kubernetes/Temporal-grade pipelines: see
> [fleet-rollouts.md](fleet-rollouts.md).

> The MinIO endpoint in the config must be reachable both from the host running
> the agent (for preflight) and from the runner containers — i.e. a real network
> address or DNS name (`http://afl-minio:9000`), not `localhost`. `--image`
> should be a registry tag every server can pull.

**Zero-config discovery + a secret store (Phase 3).** A server no longer needs the
Mongo URL spelled out, and MinIO credentials no longer live in each host's env:

```bash
# Discovery: --mongo is now OPTIONAL on every fleet/fleet-agent command. The agent
# finds Mongo by: explicit --mongo → FW_MONGODB_URL → mDNS → the conventional
# `afl-mongodb` hostname. MinIO is read from the config (afl-minio fallback). So
# with a resolvable afl-mongodb (DNS/hosts) or mDNS, a server joins with just
# (--data-dir is still REQUIRED — there is no /Volumes/afl_data default):
fw fleet agent watch --data-dir "$HOME/afl_data"

# mDNS advertiser — run on the infra host so agents find Mongo+MinIO with no
# /etc/hosts at all (needs `pip install zeroconf`):
fw fleet advertise

# Secret store: MinIO creds are encrypted (Fernet) in the `fleet_secrets`
# collection. Each host needs only the fleet key — ONE bootstrap secret — not the
# actual creds, which are set once centrally:
fw fleet secret gen-key                 # generate FW_FLEET_KEY (export on admin + each host)
fw fleet secret set --minio-access KEY --minio-secret SECRET   # encrypt + store (admin)
fw fleet secret show                    # decrypt + show (masked)
```

When `fleet_secrets` holds a credential and `FW_FLEET_KEY` is set, the agent
**decrypts the creds at apply time** and injects them transiently — they never sit
in a per-host file or env. The fleet key is one rotatable secret per host instead
of the actual MinIO credentials; if a secret is stored but `FW_FLEET_KEY` is
missing, the agent fails with a clear message rather than starting mis-credentialed
runners. Hosts without the secret store fall back to `FW_S3_ACCESS_KEY` /
`FW_S3_SECRET_KEY` from the env.

### Local Scratch & Multi-Server Semantics

A common question when going multi-server: *each runner has its own local `/tmp` — doesn't that break distributed execution?* No: **temp is intentionally per-runner, per-task, local-only, and never crosses hosts.** Everything that needs to be shared crosses host boundaries through MongoDB (coordination) and the durable storage backend (data).

#### Three planes

| Plane | Where it lives | Who shares it |
|---|---|---|
| **Coordination** — workflows, runners, tasks, steps | MongoDB (`FW_MONGODB_URL`) | All runners on all hosts |
| **Durable data** — caches (`network/`, PBFs), outputs (layers, routes, maps) | `FW_DATA_ROOT` / `FW_OSM_OUTPUT_BASE` (local path, `hdfs://`, `s3://`, or a shared mount) | All runners on all hosts |
| **Local scratch** — staging, in-flight temp files, the `localize()` warm cache | Per-host: `FW_LOCAL_SCRATCH` (or system temp), `FW_OUTPUT_BASE/tmp`, `FW_OUTPUT_BASE/cache/osm-local` | **Only that one host** |

The pattern is **stage-locally → finalize-to-durable** (see [`finalize_output_file`](https://github.com/rlemke/fwh_osm/blob/main/handlers/shared/_output.py) and `Storage.finalize_dir_from_local`). Object stores (S3/MinIO, WebHDFS) don't do streaming/partial writes, so handlers always write to a local temp first, then upload the complete object to the durable destination as the last step. The local temp is *workspace*, not state.

#### Why this doesn't violate multi-server execution

A step's payload references its inputs and outputs by **URI**, not by host-local path (`s3://afl-cache/…/merged.geojson`, not `/Users/.../merged.geojson`). Whichever runner claims the next step resolves the URI on its own filesystem via `localize()`. Worked example with two runners on two hosts:

```
Runner A (host-A)                              Runner B (host-B)
─────────────────                              ─────────────────
1. claim_task() -> MergeLayers
2. localize(s3://…/inputs) into
   host-A's /…/cache/osm-local/…
3. write streaming output to
   host-A's /…/output/tmp/tmpXXX.geojson
4. finalize_output_file(tmpXXX,
     s3://afl-cache/…/merged.geojson)
   -> object PUT, then unlink the local temp
5. mark step complete in Mongo;
   result payload = s3://afl-cache/…/merged.geojson
                                               6. claim_task() -> RouteLayer
                                                  (could just as easily have been A)
                                               7. localize(s3://…/merged.geojson) into
                                                  host-B's /…/cache/osm-local/…
                                               8. write streaming routes to
                                                  host-B's /…/output/tmp/tmpYYY.geojson
                                               9. finalize_output_file(tmpYYY,
                                                    s3://afl-cache/…/routes_N.geojson)
```

`tmpXXX` on host A and `tmpYYY` on host B are on different filesystems on different hosts. They never see each other and don't need to: only what goes through the durable backend crosses the host boundary.

Same principle holds within a single runner: an 8-worker runner gives each worker its own `tempfile.mkstemp(...)`, which is unique by construction, so concurrent tasks on the same host don't collide either.

#### Crash and retry semantics

- If a runner dies mid-task, the task lease expires; the reaper resets the task to `pending`; another runner claims it from any host and **re-runs the whole step from scratch**. The partial local temp on the dead host is harmless — it was never visible to anyone and the durable destination hasn't seen anything yet.
- If a runner finalizes the output but crashes before marking the step complete in Mongo, the next runner that claims it re-runs and overwrites; outputs are content-addressed and writes are idempotent, so the re-run produces a bit-identical artifact at the same URI.

#### Why the storage backend matters here

This whole model only works if the durable references in step payloads are resolvable on *every* host. That's exactly what the storage layer provides:

- `FW_STORAGE=local` with `FW_DATA_ROOT=/Volumes/afl_data` is single-host only (unless `/Volumes/afl_data` is the same shared mount on every host).
- `FW_STORAGE=hdfs` or `FW_STORAGE=s3` (with `FW_DATA_ROOT=hdfs://…` or `s3://…`) yields step payloads with portable URIs — see [HDFS Integration](#hdfs-integration) and [S3 / MinIO Integration](#s3--minio-integration).

If you skip this — e.g. keep `FW_OSM_OUTPUT_BASE` pointed at a *local* path while running on multiple hosts — host A writes `/var/afl/output/.../merged.geojson` on its own disk, host B can't see it, and the next step fails. The S3/MinIO/HDFS work is what turns those step references into something every runner can resolve.

#### Operator notes

- **Localize cache grows over time.** Each runner caches everything it has `localize()`d at `FW_OUTPUT_BASE/cache/osm-local/…` (or the explicit `target_dir`). It's *just* a cache — safe to prune. Some replication across hosts is the trade-off for not requiring a shared mount.
- **Stale temps from dead tasks.** A crashed task can leave `tmp*.geojson` (or similar) under `FW_LOCAL_SCRATCH` / `FW_OUTPUT_BASE/tmp`. Periodic cleanup is the operator's job.
- **Workers per host.** `FW_MAX_CONCURRENT` and `--instances` control concurrency on a host; tune for memory headroom (one routing handler can hold its loaded network in `_GRAPH_CACHE`, ~MBs to ~GBs depending on the artifact).
- **Skipping oversized regions in bulk GeoJSON conversion (`FW_OSM_MAX_PBF_MB`).** Whole-region `osm.Source.PBF.ToGeoJson` (`osmium export`) is dominated by its node-location index, which seek-thrashes or OOMs on the largest extracts once it no longer fits memory. Set `FW_OSM_MAX_PBF_MB` (megabytes) to **skip any region whose cached PBF exceeds that size** — the handler checks the cached size *before* downloading or converting and returns `skipped = true` with an empty path. It's the env fallback for the `max_pbf_mb` workflow parameter (`ConvertAllRegionsToGeoJson(max_pbf_mb = …)` / `ConvertRegionToGeoJson(...)`), so an operator can cap a bulk run already in flight without re-authoring. On a memory-constrained host the gate interacts with two other knobs: keep `FW_MAX_CONCURRENT` low enough that each concurrent export gets enough RAM for osmium way-assembly (~3-8GB/region for big extracts — e.g. `FW_MAX_CONCURRENT=2` on a 14GB VM), and size the **Docker Desktop VM** so the host stays stable (a too-large VM crashes Docker Desktop via macOS memory compression; a too-small one thrashes the disk-backed index). In `docker-compose.full-stack.yml` the OSM runners default to `FW_OSM_MAX_PBF_MB=1024` + `FW_MAX_CONCURRENT=2` on a 14GB VM, which converts ~226/255 cached regions and skips the ~25 ≥1GB extracts (continents, big countries) that need a larger host. See [docs/architecture/lessons-learned.md](../architecture/lessons-learned.md) → *Converting at the edge of a host's memory*.
- **Recovering a wedged Docker Desktop VM network.** If an over-large VM crashes Docker Desktop and the linuxkit network wedges (host log under `~/Library/Containers/com.docker.docker/Data/log/host/*.log` shows `no route to host …:2376` / `tx dropped packets`), a simple reopen does **not** fix it — do a full teardown: quit Docker, `pkill com.docker`, `pkill com.docker.backend`, wait ~20s, then `open -a Docker`. (The VM `MemoryMiB` lives in `~/Library/Group Containers/group.com.docker/settings-store.json`, a host file, not a repo file.)

### Production Recommendations

- **MongoDB**: Dedicated server or managed service (MongoDB Atlas) with replica sets for HA
- **Dashboard**: Single instance behind a reverse proxy (nginx/caddy)
- **Runners**: Multiple instances per runner server, scaled via `--instances N` and `--max-concurrent M`
- **Monitoring**: Dashboard at `/v3/workflows` and `/v3/servers`; API at `/api/servers` for health checks
- **Crash recovery**: Orphan reaper automatically resets tasks from dead runners (configurable via `FW_REAPER_TIMEOUT_MS`)

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FW_MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `FW_MONGODB_DATABASE` | `afl` | Database name |
| `FW_MONGODB_USERNAME` | | MongoDB authentication username |
| `FW_MONGODB_PASSWORD` | | MongoDB authentication password |
| `FW_MONGODB_AUTH_SOURCE` | `admin` | MongoDB auth database |
| `FW_CONFIG` | | Path to `facetwork.config.json` file |

### Config File (`facetwork.config.json`)

```json
{
  "mongodb": {
    "url": "mongodb://localhost:27017",
    "database": "afl",
    "username": "",
    "password": "",
    "auth_source": "admin"
  },
  "resolver": {
    "auto_resolve": false,
    "source_paths": [],
    "mongodb_resolve": false
  }
}
```

The config file is searched in order: `$FW_CONFIG`, `./facetwork.config.json`, `~/.ffl/facetwork.config.json`, `/etc/ffl/facetwork.config.json`.

## Service Reference

### Dashboard

Web UI for monitoring and managing workflows.

```bash
# Docker
docker compose up -d dashboard

# Direct
python -m facetwork.dashboard --host 0.0.0.0 --port 8080
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8080` | Listen port |
| `--config` | | Path to FFL config file |
| `--reload` | | Enable auto-reload (development) |
| `--log-level` | `INFO` | Log level |

**Health check:** `GET /health` returns `200 OK` with JSON body.

### Runner Service

Distributed runner that orchestrates workflow execution with locking and concurrent processing.

```bash
# Docker (scalable)
docker compose up -d --scale runner=3

# Direct
python -m facetwork.runtime.runner
```

| Option | Default | Description |
|--------|---------|-------------|
| `--server-group` | `default` | Server group name |
| `--service-name` | `afl-runner` | Service identifier |
| `--topics` | (all) | Event facet names to handle |
| `--task-list` | `default` | Task list to poll |
| `--poll-interval` | `2000` | Poll interval in ms |
| `--max-concurrent` | `5` | Max concurrent work items |
| `--lock-duration` | `60000` | Lock TTL in ms |
| `--port` | `8080` | HTTP status port (auto-increments) |

### MCP Server

Model Context Protocol server for LLM agent integration.

```bash
# Docker (stdio transport)
docker compose --profile mcp run --rm mcp

# Direct
python -m facetwork.mcp
```

| Option | Default | Description |
|--------|---------|-------------|
| `--transport` | `stdio` | MCP transport |
| `--config` | | Path to FFL config file |
| `--log-level` | `WARNING` | Log level |
| `--log-file` | | Log to file (recommended for stdio) |

## Monitoring

### Dashboard Pages

The UI is **v3** and is the default — `GET /` redirects to `/v3/workflows`. Every
page lives under `/v3/…` on a shared sidebar shell (the v2 UI and the original
non-prefixed page routes were removed). Full reference:
[../reference/dashboard.md](../reference/dashboard.md).

| Page | URL | Content |
|------|-----|---------|
| Runs | `/v3/workflows` | Namespace-grouped runners with Running/Completed/Failed tabs and a name filter |
| Run Detail | `/v3/workflows/{id}` | Live execution graph, SSE step logs, progress, pause/cancel/resume + per-step recovery |
| Library | `/v3/flows` | Compiled flows (namespaces/facets/workflows); shows each flow's `created_at` |
| Catalog | `/v3/catalog` | Reusable, versioned workflows/libraries |
| Filters | `/v3/filters` | Persistent global Flow/Workflow filters applied to the Library and Runs lists |
| Servers | `/v3/servers` | Registered runner servers with heartbeat status |
| Handlers | `/v3/handlers` | Registered handler modules |
| Fleet | `/v3/fleet` | Central `fleet_config`: **Infra services** (MongoDB/MinIO/Dashboard, by URL) + **Runner roles** (no master) |
| Tasks | `/v3/tasks` | Event task queue (pending, running, completed, failed) |
| Events | `/v3/events` | Event-facet work dispatched to agents |
| Output | `/v3/output` | Handler output / cached artifacts |
| PostGIS | `/v3/postgis` | PostGIS database summary |
| Users / Teams | `/v3/users`, `/v3/teams` | Identity + team management and the acting-as selector |
| Namespaces | `/namespaces` | Namespace definitions across flows (no v3 page yet) |
| Sources | `/sources` | Published FFL source namespaces (no v3 page yet) |

### API Endpoints

All dashboard pages have corresponding JSON API endpoints at `/api/*`:

```bash
curl http://localhost:8080/api/runners
curl http://localhost:8080/api/runners?state=running
curl http://localhost:8080/api/tasks?state=pending
curl http://localhost:8080/api/servers
curl http://localhost:8080/api/flows
```

### Health Checks

| Service | Endpoint | Method |
|---------|----------|--------|
| Dashboard | `/health` | HTTP GET |
| MongoDB | `mongosh --eval "db.runCommand('ping')"` | CLI |

## Scaling Guidelines

### MongoDB

- Use **replica sets** for high availability
- Enable **WiredTiger** cache sizing for write-heavy workloads
- Index the `tasks` collection on `state` and `task_list_name`
- Monitor `tasks` collection size; completed tasks accumulate

### Runners

- Scale horizontally: each runner coordinates via atomic `claim_task()`
- Set `--max-concurrent` based on available CPU/memory (default: 5)
- Set `--poll-interval` lower (500ms) for latency-sensitive workloads
- Use `--topics` to partition work across runner groups

### Agents

- Scale by workload type: different agents handle different event facets
- Each agent instance registers as a server with heartbeat
- Failed agents are detected via heartbeat timeout
- Use the `RegistryRunner` model for simpler deployment (handlers in database)

## Unified file access — the `FileSystem` facade

Every module that reads or writes **data files** should go through one interface
rather than calling `open()` / `pathlib` directly, so the same code works against
local disk, HDFS, or S3/MinIO. There are two complementary entry points in
[`facetwork/runtime/storage.py`](../../facetwork/runtime/storage.py):

- **`get_storage_backend(path)`** — per-path dispatch *by URI scheme*. A path
  starting with `hdfs://` → HDFS, `s3://` → S3, otherwise local. Use it when you
  already hold a fully-qualified path/URI.
- **`get_fs()` / `FileSystem`** — an **init/config-driven facade**. It selects
  *one default backend* at startup and resolves bare, scheme-less paths under a
  configured root, so handler code reads `regions/california.osm.pbf` and the
  facade decides where that lives. An explicit `hdfs://` / `s3://` path still
  overrides per call.

```python
from facetwork.runtime.storage import get_fs

fs = get_fs()  # backend chosen from config (below)
data = fs.read_bytes("regions/california.osm.pbf")
fs.write_json("out/summary.json", {"count": 42})
text = fs.read_text("s3://other-bucket/notes.txt")  # explicit URI overrides default
```

Convenience methods: `open / read_bytes / read_text / read_json / write_bytes /
write_text / write_json / exists / isfile / isdir / listdir / walk / makedirs /
getsize / localize`. Module-level shims (`read_text`, `write_text`, `fs_open`,
`fs_exists`, …) delegate to the process-global `get_fs()` for terse call sites.

### Selecting the default backend (`FW_FS_BACKEND` / `FW_FS_ROOT`)

The facade picks its default backend in the order **Hadoop → MinIO/S3 → local
file**:

| Variable | Meaning |
|----------|---------|
| `FW_FS_ROOT` | Base URI/path bare paths resolve under. Its **URI scheme decides the backend** — `hdfs://nn/user/afl`, `s3://my-bucket/cache`, or a local dir. |
| `FW_FS_BACKEND` | Explicit override: `auto` (default), `hdfs`, `s3`, or `local`. |

With `auto` (no `FW_FS_ROOT`), the facade also honours the existing fleet
signals so it "just works" alongside the cache config: `FW_STORAGE=hdfs` +
`FW_HDFS_HOST` → HDFS; `FW_STORAGE=s3` or `FW_S3_BUCKET` → S3; otherwise
local. Backend credentials/endpoints are unchanged (`FW_S3_ENDPOINT`,
`FW_WEBHDFS_PORT`, the standard AWS chain, etc.).

> **Large-file caveat — stage locally, then finalize.** The HDFS/S3 backends
> buffer a whole object in memory on write, so multi-GB artifacts (streamed
> GeoJSON, tile pyramids) are **not** written through the facade. They are
> streamed to a local temp and published with `finalize_output_file` /
> `Storage.finalize_dir_from_local` (the OSM tools' `get_storage()` interface).
> Use the facade for *modest, durable* artifacts (JSON summaries, graphs,
> sidecars, small GeoJSON, HTML maps); keep big streaming I/O local-then-finalize.

## HDFS Integration

Facetwork supports HDFS as a storage backend for OSM handler caches. When enabled, OSM agents read and write cache data (PBF files, GraphHopper graphs, GTFS feeds) to HDFS instead of local disk.

### Starting HDFS

```bash
# Start the HDFS namenode and datanode
docker compose --profile hdfs up -d

# Verify namenode is healthy
docker compose --profile hdfs ps
```

The HDFS Web UI is available at `http://localhost:9870` and the RPC endpoint at `hdfs://localhost:8020`.

### Building with HDFS Support

Use the `docker-compose.hdfs.yml` override file to build OSM agent images with `pyarrow` (required for HDFS):

```bash
docker compose -f docker-compose.yml -f docker-compose.hdfs.yml --profile hdfs build
```

Or use the setup script:

```bash
fw install setup --hdfs --osm-agents 2 --build
```

### Running OSM Agents with HDFS Cache

When using the override file, the following environment variables are set automatically on OSM agent containers:

| Variable | Value | Description |
|----------|-------|-------------|
| `FW_CACHE_ROOT` | `hdfs://afl-hadoop-hdfs:8020/cache` | Sidecar cache root (OSM PBF + handler caches under `<root>/<namespace>/`). Or set `FW_STORAGE=hdfs` to root everything at `/user/afl`. (Replaces the retired `FW_CACHE_DIR`.) |
| `GRAPHHOPPER_GRAPH_DIR` | `hdfs://afl-hadoop-hdfs:8020/graphhopper` | GraphHopper routing graphs |
| `FW_GTFS_CACHE_DIR` | `hdfs://afl-hadoop-hdfs:8020/gtfs-cache` | GTFS feed cache |

The `get_storage_backend()` factory detects `hdfs://` URIs and returns an `HDFSStorageBackend` (backed by pyarrow) instead of the default `LocalStorageBackend`.

### Running HDFS Tests

```bash
# Existing HDFS storage tests
pytest tests/runtime/test_hdfs_storage.py --hdfs -v

# OSM handler HDFS integration tests
pytest tests/test_osm_handlers_hdfs.py --hdfs -v

# All HDFS tests
pytest tests/ --hdfs -v -k hdfs
```

Without the `--hdfs` flag, all HDFS tests are skipped automatically.

### External Storage for HDFS

By default, HDFS uses Docker named volumes (`hadoop_namenode`, `hadoop_datanode`). To place HDFS data on an external filesystem (e.g., a large SSD, NFS mount, or dedicated disk), set the `HDFS_NAMENODE_DIR` and `HDFS_DATANODE_DIR` environment variables to host paths:

```bash
# Use external directories for HDFS data
export HDFS_NAMENODE_DIR=/mnt/hdfs/namenode
export HDFS_DATANODE_DIR=/mnt/hdfs/datanode
docker compose --profile hdfs up -d

# Or via the setup script
fw install setup --hdfs \
  --hdfs-namenode-dir /mnt/hdfs/namenode \
  --hdfs-datanode-dir /mnt/hdfs/datanode
```

| Variable | Default | Description |
|----------|---------|-------------|
| `HDFS_NAMENODE_DIR` | `hadoop_namenode` (named volume) | Host path for NameNode metadata |
| `HDFS_DATANODE_DIR` | `hadoop_datanode` (named volume) | Host path for DataNode block storage |

When the variables are unset, Docker uses named volumes (the original behavior). When set to a host path (e.g., `/mnt/hdfs/datanode`), Docker creates a bind mount instead. Ensure the target directories exist and have appropriate permissions before starting the containers.

## S3 / MinIO Integration

Facetwork also supports any **S3-compatible object store** (AWS S3, or a self-hosted **MinIO** surfacing the cache over HTTP) as a storage backend. This is the simplest way to make handler caches and outputs **portable across a multi-server runner fleet**: a task's step payload carries `s3://…` URIs that any runner on any host can resolve, instead of host-local paths like `/Volumes/afl_data/…`. `get_storage_backend()` detects `s3://` URIs and returns an `S3StorageBackend` (backed by `boto3`); reads are localized to a per-runner cache, writes upload on close.

### Bundled in the full-stack compose (OSM fleet)

`docker-compose.full-stack.yml` **already bundles MinIO** and the OSM runners
(`osm-geocoder`, `osm-lz`) default to it — their durable cache + output go to
`s3://afl-cache` with **no external disk**, and staging/tmp/locks stay on a
local `afl_scratch` volume:

```bash
docker compose -f docker-compose.full-stack.yml up -d \
    mongodb postgis minio minio-setup dashboard runner-osm-geocoder
# minio-setup creates the bucket and exits 0; the runner waits for it.
# MinIO console: http://localhost:9001 (minioadmin / minioadmin)
```

The example-runner image bakes in `boto3` (the `s3` extra), so scaled replicas
all have it — a replica without it would dead-letter every storage write. To
point the fleet at a different MinIO/S3, override `FW_S3_ENDPOINT` /
`FW_S3_ACCESS_KEY` / `FW_S3_SECRET_KEY` / `FW_S3_BUCKET` in `.env`. The rest
of this section covers a standalone MinIO + the env contract in detail.

### Requirements

- **`boto3`** — install the `s3` extra: `pip install -e ".[s3]"` (boto3 is soft-imported, so it's only needed when an `s3://` path is used). The full-stack example-runner image already includes it.
- **A bucket** on the object store (the bundled `minio-setup` creates it; standalone instructions below).
- For MinIO: the **MinIO container** (below, or the bundled compose service). For AWS S3: nothing to run — just set credentials and omit `FW_S3_ENDPOINT`.

### Starting MinIO (what the container requires)

MinIO is a single self-contained container. It needs a data directory, the S3 API + console ports, and root credentials:

```bash
docker run -d --name afl-minio \
  -p 9000:9000 \                       # S3 API (the endpoint runners talk to)
  -p 9001:9001 \                       # web console (http://localhost:9001)
  -e MINIO_ROOT_USER=minioadmin \      # access key
  -e MINIO_ROOT_PASSWORD=minioadmin \  # secret key (change for anything shared)
  -v afl-minio-data:/data \            # persist objects across restarts
  minio/minio server /data --console-address ":9001"
```

Create the bucket once (via the AWS CLI, `mc`, or boto3):

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
  aws --endpoint-url http://localhost:9000 s3 mb s3://afl-cache
```

Tear down with `docker rm -f afl-minio` (add `-v afl-minio-data` removal to discard objects).

### Configuring runners to use S3 / MinIO

Set these on every runner (and on submission, for parity). See the env table below for the full list:

```bash
FW_STORAGE=s3
FW_DATA_ROOT=s3://afl-cache                       # durable cache root → s3://afl-cache/cache/…
FW_OSM_OUTPUT_BASE=s3://afl-cache/osm-output      # handler outputs (layers, networks, routes)
FW_S3_ENDPOINT=http://<minio-host>:9000           # OMIT for real AWS S3
FW_S3_ACCESS_KEY=minioadmin                        # or the standard AWS_ACCESS_KEY_ID
FW_S3_SECRET_KEY=minioadmin                        # or AWS_SECRET_ACCESS_KEY
# FW_S3_REGION=us-east-1                            # optional
FW_OUTPUT_BASE=/var/afl/local                      # KEEP LOCAL — see gotcha below
```

| Variable | Example | Description |
|----------|---------|-------------|
| `FW_STORAGE` | `s3` | Selects the backend (`local` \| `hdfs` \| `s3`). |
| `FW_DATA_ROOT` | `s3://afl-cache` | Durable cache root; the sidecar cache lives under `<root>/cache/<namespace>/`. |
| `FW_OSM_OUTPUT_BASE` | `s3://afl-cache/osm-output` | Where OSM handler outputs are written (so downstream step payloads carry `s3://`). |
| `FW_S3_ENDPOINT` | `http://localhost:9000` | Object-store endpoint. **Unset → real AWS S3.** |
| `FW_S3_ACCESS_KEY` / `FW_S3_SECRET_KEY` | `minioadmin` | Credentials (or the standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` chain). |
| `FW_S3_REGION` | `us-east-1` | Region (default `us-east-1`). |

> **Gotcha — keep `FW_OUTPUT_BASE` local.** Scratch/staging/temp must live on a local filesystem (you stage locally, then finalize onto the object store). The cache's `staging`/`tmp`/`locks` roots fall back to a local base automatically when `FW_DATA_ROOT` is remote (override with `FW_LOCAL_SCRATCH`), but `FW_OUTPUT_BASE` also feeds the runtime's temp dir — point it at a **local** path, not an `s3://` URI. Put the durable artifacts on S3 via `FW_DATA_ROOT` + `FW_OSM_OUTPUT_BASE`.

> **Symmetric storage — `localize()` is the read-side counterpart of `finalize_from_local()`.** Native tools (osmium, tippecanoe) only read and write local files, so an S3/MinIO *input* must be downloaded locally before the tool runs. `Storage.localize()` (read side) mirrors `finalize_from_local()` (write side): the input URI is localized into the warm cache, the tool runs against local files, and the complete output is finalized back to the object store. A backend that has one without the other can write to S3 but can't feed the next native step its input.

> **Memory-heavy conversions — disk-backed osmium index + external staging.** A full-region PBF→GeoJSON conversion (`osm.Source.PBF.ToGeoJson`) runs `osmium export`, whose default in-RAM (`flex_mem`) node-location index grows unbounded and **OOM-kills** on large regions (a continent needs GBs just for the index) on a small VM. Use a disk-backed index on local scratch (`osmium export -i sparse_file_array,<scratch>`), overridable via **`FW_OSMIUM_INDEX_TYPE`** (e.g. `flex_mem` for small regions where RAM is fine and speed matters). The GeoJSON staging *and* the `localize()` warm cache must sit on the external/scratch disk (`FW_OUTPUT_BASE` / `FW_LOCAL_SCRATCH`), **never** the internal Docker disk shared with mongodb — an ENOSPC there crashes mongo, turning a disk-full into a cluster outage. Tune concurrency against disk bandwidth + page cache (each disk index is multi-GB), not nominal CPU count: too many concurrent exports seek-thrash the single external disk; too few starve task execution because orchestration shares the pool.

### Migrating an existing local cache into MinIO

If you already have a populated local cache (e.g. the legacy `/Volumes/afl_data/cache`) and want to move it into the bundled MinIO so the fleet reads it over S3, use the host-driven migrator `scripts/lib/_helpers/_cache_to_minio_move.py`. It streams each file `/Volumes/afl_data/cache/<X>` → `s3://afl-cache/cache/<X>`, **verifies the uploaded object's size, then deletes the local source** — a true move that is idempotent and restart-safe (a file already in the bucket at the matching size is skipped, never re-uploaded). Because it verifies before deleting, it is safe to kill at any time.

```bash
docker compose -f docker-compose.full-stack.yml up -d minio
SKIP_PATH_SUBSTR="osm/geojson/" .venv/bin/python scripts/lib/_helpers/_cache_to_minio_move.py
# On "=== ALL MOVED CLEAN ===", sweep the emptied source dirs:
find /Volumes/afl_data/cache -type d -empty -delete
```

- **Run it from the host, not `mc` inside a container.** Docker Desktop's virtiofs mishandles deep recursion over an external USB/APFS disk — directory walks return zero files (silent no-op "complete") and multipart parts short-read. Host-side reads are reliable; it uploads straight to `localhost:9000`.
- **`SKIP_PATH_SUBSTR`** leaves any source path containing the substring in place — e.g. `osm/geojson/` skips the large regenerable whole-region `*.geojsonseq` dumps (recreate them from PBFs on next use). `SKIP_LARGER_THAN_GB` caps by size instead. Files move smallest-first, so a late "skip the giants" decision wastes almost nothing.
- **Spinning / USB source disks — serialize multipart reads.** Large uploads can fail `UploadPart` with `IncompleteBody: You did not provide the number of bytes specified by the Content-Length HTTP header`: boto3's default `TransferConfig` reads several parts concurrently and the interleaved head-seeks on a spinning disk short-read, which MinIO rejects. Set `XFER_MAX_CONCURRENCY=1` to serialize part reads (reliable, somewhat slower since source and the MinIO data dir may share one disk); `XFER_CHUNK_MB` tunes the multipart chunk size.

> The migrator only moves the durable `cache/` tree. Throughput is bounded by the slowest disk in the path — on a USB spinning disk where the MinIO data dir lives on the *same* drive, every byte is read once and written once on that one drive, so batching/SSD-staging doesn't beat it; the only real lever is **scope** (skip regenerable artifacts via `SKIP_PATH_SUBSTR`).

### Running S3 tests

```bash
# Path-helper/dispatch tests always run; the live round-trip is gated on FW_S3_ENDPOINT:
FW_S3_ENDPOINT=http://localhost:9000 \
  FW_S3_ACCESS_KEY=minioadmin FW_S3_SECRET_KEY=minioadmin \
  pytest tests/runtime/test_s3_storage.py -v
```

Without `FW_S3_ENDPOINT`, the live round-trip is skipped automatically.

## Jenkins CI/CD

Facetwork includes an optional Jenkins service for CI/CD pipelines. Jenkins runs with Docker socket access, allowing it to build and test Facetwork Docker images.

### Starting Jenkins

```bash
# Start Jenkins
docker compose --profile jenkins up -d

# Check health
docker compose --profile jenkins ps
```

The Jenkins Web UI is available at `http://localhost:9090`.

### Initial Setup

Retrieve the initial admin password:

```bash
docker compose exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

### Setup Script

```bash
fw install setup --jenkins                    # Jenkins only
fw install setup --jenkins --build            # Rebuild images first
```

### External Storage for Jenkins

By default, Jenkins uses a Docker named volume (`jenkins_home`). To place Jenkins data on an external filesystem, set the `JENKINS_HOME_DIR` environment variable:

```bash
# Use an external directory for Jenkins data
export JENKINS_HOME_DIR=/mnt/ssd/jenkins
docker compose --profile jenkins up -d

# Or via the setup script
fw install setup --jenkins --jenkins-home-dir /mnt/ssd/jenkins
```

| Variable | Default | Description |
|----------|---------|-------------|
| `JENKINS_HOME_DIR` | `jenkins_home` (named volume) | Host path for Jenkins home directory |

## PostGIS Integration

Facetwork supports PostGIS as a spatial database for OSM geocoder agents. The OSM geocoder defines a `PostGisImport` event facet for importing geospatial data into PostGIS.

### Starting PostGIS

```bash
# Start the PostGIS database
docker compose --profile postgis up -d

# Verify PostGIS is ready
docker compose exec postgis pg_isready -U afl
```

### Connection Details

| Property | Value |
|----------|-------|
| Host | `localhost` |
| Port | `5432` |
| Database | `afl_gis` |
| User | `afl` |
| Password | `afl` |

### Building OSM Agents with PostGIS

OSM agent images and their PostGIS-specific build args live in the
standalone osm-geocoder repo: https://github.com/rlemke/fwh_osm.

### Environment Variables

When using the override file, the following environment variable is set automatically on OSM agent containers:

| Variable | Value | Description |
|----------|-------|-------------|
| `FW_POSTGIS_URL` | `postgresql://afl:afl@postgis:5432/afl_gis` | PostGIS connection string |

### External Storage for PostGIS

By default, PostGIS uses a Docker named volume (`postgis_data`). To place data on an external filesystem, set the `POSTGIS_DATA_DIR` environment variable:

```bash
# Use an external directory for PostGIS data
export POSTGIS_DATA_DIR=/mnt/ssd/postgis
docker compose --profile postgis up -d

# Or via the setup script
fw install setup --postgis --postgis-data-dir /mnt/ssd/postgis
```

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGIS_DATA_DIR` | `postgis_data` (named volume) | Host path for PostgreSQL/PostGIS data |

### External Storage for MongoDB

By default, MongoDB uses a Docker named volume (`mongodb_data`). To place data on an external filesystem, set the `MONGODB_DATA_DIR` environment variable:

```bash
# Use an external directory for MongoDB data
export MONGODB_DATA_DIR=/mnt/ssd/mongodb
docker compose up -d

# Or via the setup script
fw install setup --mongodb-data-dir /mnt/ssd/mongodb
```

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_DATA_DIR` | `mongodb_data` (named volume) | Host path for MongoDB data files |

#### Docker VM disk guard (prevent ENOSPC mongo crashes)

On Docker Desktop, the bundled mongo/minio live on the **Docker VM's** internal
disk (a fixed-size overlay, *not* the host's free space). Heavy image building —
e.g. repeated `buildx`/`rebuild-workers` during a rollout — can fill that VM
disk to 100%, at which point WiredTiger can't write its journal and **mongo
crash-loops, taking the whole fleet down** (the cause is invisible in `df` on the
host, which still shows free space — check `docker run --rm busybox df /`).

`fw maint disk-guard` reclaims build cache + unused images (re-pullable
from the registry) + stopped containers when the VM disk crosses a threshold
(default 80%); it never touches volumes (your mongo/minio/postgis data). Install
it on the **infra host** (and any host that builds images) via cron:

```bash
( crontab -l 2>/dev/null | grep -v docker-disk-guard; \
  echo "*/30 * * * * $PWD/fw maint disk-guard >/dev/null 2>&1" ) | crontab -
# tune with DOCKER_DISK_THRESHOLD; log at ~/.docker-disk-guard.log
```

If the VM-disk *baseline* (after pruning) is itself high, that's durable data
(the `minio`/`mongodb` volumes) — enlarge the Docker VM disk or move mongo/minio
to dedicated infra (`MONGODB_DATA_DIR`, external MinIO) rather than relying on
the guard.

### External Storage for GraphHopper

GraphHopper graph storage is configured by the OSM example package itself
(see https://github.com/rlemke/fwh_osm) — set `GRAPHHOPPER_DATA_DIR` in your
shell or `.env` and the OSM agent's docker-compose entry will mount it.

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPHHOPPER_DATA_DIR` | `graphhopper_data` (named volume) | Host path for GraphHopper routing graph data |

Ensure target directories exist and have appropriate permissions before starting the containers.

## Security

### MongoDB Authentication

Enable authentication in production:

```json
{
  "mongodb": {
    "url": "mongodb://mongo-host:27017",
    "database": "afl",
    "username": "afl_user",
    "password": "secure_password",
    "auth_source": "admin"
  }
}
```

### Network Recommendations

- Run MongoDB on a private network, not exposed to the internet
- Use TLS for MongoDB connections (`mongodb+srv://` or `?tls=true`)
- Place the dashboard behind a reverse proxy (nginx/caddy) with authentication
- MCP server uses stdio transport — no network exposure

### Docker Security

- Use non-root users in Docker images (already configured)
- Pin image versions in production
- Scan images for vulnerabilities
- Use Docker secrets for credentials

## Backup & Recovery

### MongoDB Backup

```bash
# Dump the database
mongodump --uri="mongodb://afl-mongodb:27017" --db=afl --out=/backup/

# Restore
mongorestore --uri="mongodb://afl-mongodb:27017" --db=afl /backup/ffl/
```

### Key Collections

| Collection | Content | Backup Priority |
|------------|---------|-----------------|
| `flows` | Compiled workflow definitions | High |
| `sources` | Published FFL source code | High |
| `handler_registrations` | Registered handlers | High |
| `runners` | Execution history | Medium |
| `steps` | Step state and data | Medium |
| `tasks` | Task queue | Low (transient) |
| `servers` | Server registrations | Low (transient) |
| `locks` | Distributed locks | Low (ephemeral) |

## Troubleshooting

### Common Issues

**Services can't connect to MongoDB:**
```bash
docker compose ps                    # Check service health
docker compose logs mongodb          # Check MongoDB logs
docker compose exec mongodb mongosh  # Test connection directly
```

**Workflows stuck in PAUSED state:**
- Check that agents/runners are running: `GET /api/servers`
- Verify handler registrations: `GET /api/handlers`
- Check task queue: `GET /api/tasks?state=pending`
- Look for failed tasks: `GET /api/tasks?state=failed`

**Steps stuck in EVENT_TRANSMIT:**
- No agent is registered for the event facet
- Agent crashed after claiming the task
- Check locks: `GET /api/locks` (expired locks block progress)

**High memory usage:**
- Reduce `--max-concurrent` on runners
- Check for large step attribute payloads
- Archive old runner/step records

### Diagnostics

```bash
# Service status
docker compose ps

# Service logs (follow)
docker compose logs -f runner

# MongoDB collection stats
docker compose exec mongodb mongosh afl --eval "db.stats()"

# Task queue depth
docker compose exec mongodb mongosh afl --eval "db.tasks.countDocuments({state: 'pending'})"

# Active locks
docker compose exec mongodb mongosh afl --eval "db.locks.find().toArray()"
```

### Clearing State

```bash
# Remove all data (development only)
docker compose down -v

# Reset task queue only
docker compose exec mongodb mongosh afl --eval "db.tasks.deleteMany({state: {\\$in: ['completed', 'failed']}})"
```


## Deployment Operations

Facetwork runners can be managed locally (single machine) or remotely (multi-host production). All scripts support both modes — local is the default and remote is activated with `--all` or `--host`.

### Prerequisites for remote management

1. **SSH access**: current user must be able to `ssh <hostname>` to every runner host without a password prompt (SSH agent or key-based auth)
2. **Same repo layout**: the Facetwork repo must be checked out on every remote host at the same path (or set `FW_REMOTE_PATH`)
3. **MongoDB reachable**: every runner host must be able to reach the MongoDB instance specified by `FW_MONGODB_URL`
4. **Host inventory**: configure `FW_RUNNER_HOSTS` in `.env` or pass `--host` flags

```bash
# .env
FW_RUNNER_HOSTS=prod-runner-01 prod-runner-02 prod-runner-03
FW_REMOTE_PATH=/opt/facetwork    # optional, defaults to local repo root
FW_SSH_OPTS=-i ~/.ssh/deploy_key  # optional extra SSH flags
```

### Local runner lifecycle

```bash
# Register handlers and start runner + dashboard on this machine
fw runner start --example hiv-drug-resistance -- --log-format text

# Register ALL examples, start 3 runner instances, skip dashboard
fw runner start --instances 3 --no-dashboard

# Stop all local runners and dashboard
fw runner stop
```

### Remote runner lifecycle

```bash
# Start runners on all configured hosts
fw runner start --all --example hiv-drug-resistance -- --log-format text

# Start on specific hosts only
fw runner start --host prod-runner-01 --host prod-runner-02 --example hiv-drug-resistance

# Stop all remote runners (queries MongoDB for running servers)
fw runner stop --all

# Stop runners on specific hosts
fw runner stop --host prod-runner-01 --host prod-runner-02

# Stop with longer drain timeout (default: 30s)
fw runner stop --all --drain-timeout 60
```

### Rolling deploy (zero-downtime)

The `fw fleet rolling-deploy` script performs a serial rolling restart: for each runner it drains the old process (SIGTERM → wait for SHUTDOWN), starts a new one, and waits for it to register in MongoDB before moving to the next. This ensures at least N-1 runners are always available.

```bash
# Rolling restart all servers, re-register all example handlers
fw fleet rolling-deploy

# Rolling restart with specific handlers
fw fleet rolling-deploy --example hiv-drug-resistance --example devops-deploy

# Target specific hosts
fw fleet rolling-deploy --host prod-runner-01 --host prod-runner-02

# Custom timeouts
fw fleet rolling-deploy --drain-timeout 90 --start-timeout 90

# Skip handler re-registration (code-only restart, handlers unchanged)
fw fleet rolling-deploy --skip-registration

# Pass extra args to the runner service
fw fleet rolling-deploy --example hiv-drug-resistance -- --log-format text --max-concurrent 10
```

**Rolling deploy flow per server:**
1. Send SIGTERM via SSH (triggers graceful drain — finishes current tasks, stops polling)
2. Poll MongoDB until server state = `shutdown` (timeout: `--drain-timeout`, default 60s)
3. If HTTP port is known (persisted in MongoDB), verify health endpoint is unreachable
4. Start new runner via SSH (`nohup fw runner exec --registry ...`)
5. Poll MongoDB until new server registers with state = `running` (timeout: `--start-timeout`, default 60s)
6. If HTTP port is known, health-check `http://<host>:<port>/health` for 200 OK
7. On **any failure**, the deploy aborts immediately — remaining servers are left untouched

**Safety properties:**
- Only one server is restarted at a time (serial, never parallel)
- Abort-on-failure prevents cascading outages
- SIGTERM triggers graceful drain: the runner finishes in-flight tasks before exiting
- Handlers are re-registered once centrally (in MongoDB) before the rolling restart begins, so all restarted runners pick up the new handler code

### Crash recovery — orphaned task reaper

When a runner crashes (e.g. OOM, SIGKILL, network partition) without graceful shutdown, its in-flight tasks remain stuck in `running` state forever — no healthy runner will pick them up because they are not `pending`.

The **orphaned task reaper** runs automatically inside every `RunnerService` and `AgentPoller`:

1. Every `claim_task()` call stamps the task document with the claiming server's `server_id`
2. Every 60 seconds, the reaper queries for servers whose `ping_time` is >5 minutes stale while their state is still `running` or `startup` (i.e., crashed without deregistering)
3. All tasks in `running` state with a `server_id` matching a dead server are atomically reset to `pending`
4. Healthy runners pick them up on the next poll cycle

**Safety:**
- Gracefully shut-down servers (state = `shutdown`) are NOT reaped — only servers that died without completing their drain
- The 5-minute stale threshold (matching `SERVER_DOWN_TIMEOUT_MS`) avoids false positives from brief network hiccups or GC pauses
- The dashboard Fleet page (`/v3/fleet`) shows servers in `down` state when their heartbeat is stale, providing visual confirmation

**Manual recovery** (for tasks without `server_id`, e.g. from before the reaper was added):
```bash
docker exec afl-mongodb mongosh afl --eval "
  db.tasks.updateMany(
    {state: 'running', workflow_id: '<wf_id>'},
    {\$set: {state: 'pending', server_id: ''}}
  )
"
```

**Configuration:**
- Reap interval: 60 seconds (hardcoded, `_reap_interval_ms`)
- Down timeout: 5 minutes (`SERVER_DOWN_TIMEOUT_MS` in `facetwork/dashboard/helpers.py`, reused in `reap_orphaned_tasks()`)
- Heartbeat interval: 10 seconds (configurable via `FW_HEARTBEAT_INTERVAL_MS`)

### Verifying runner state

Each runner persists its HTTP status port in MongoDB (`ServerDefinition.http_port`), enabling remote health checks.

```bash
# List all running servers from MongoDB
python3 -c "
from facetwork.runtime.mongo_store import MongoStore
store = MongoStore('mongodb://afl-mongodb:27017')
for s in store.get_servers_by_state('running'):
    print(f'{s.server_name}: port={s.http_port}, state={s.state}, id={s.uuid}')
"

# Health-check a specific runner
curl http://prod-runner-01:8080/health

# Detailed status (uptime, active work items, handled counts)
curl http://prod-runner-01:8080/status
```

### Shared helpers (`scripts/lib/_helpers/_remote.sh`)

The remote management scripts share a common helper library sourced after `_env.sh`:

| Function | Purpose |
|----------|---------|
| `_afl_resolve_remote_env` | Resolves `FW_RUNNER_HOSTS`, `FW_REMOTE_PATH`, `FW_SSH_OPTS` |
| `_afl_ssh <host> <cmd>` | SSH wrapper with `BatchMode=yes`, `ConnectTimeout=5` |
| `_afl_query_running_servers` | Queries MongoDB, outputs `server_name http_port uuid` per line |
| `_afl_get_server_state <uuid>` | Returns current state of a server by UUID |
| `_afl_poll_server_state <uuid> <state> <timeout>` | Polls until server reaches expected state |
| `_afl_poll_new_server <host> <state> <timeout> [exclude...]` | Polls until a new server appears on hostname |
| `_afl_resolve_hosts [hosts...]` | Resolves target hosts from args or `FW_RUNNER_HOSTS` |


## The external data disk dropped — `fw maint disk-recover`

The volume backing the local registry and MinIO (`/Volumes/afl_data_local`) can
disappear: a sleeping drive, a flaky cable, a machine that moved networks. When
it returns **the stack does not recover on its own**, and both symptoms are
quiet:

| | |
|---|---|
| registry | `HTTP 503` — fails `fw fleet rollout` at the image **push** |
| MinIO | `/minio/health/live` still answers **200** while every object listing raises `InternalError` |

That second one is the dangerous one: nothing alarms, and runs simply fail to
read their data.

**Why restarting the containers is not enough.** A bind mount is resolved when a
container *starts*, and Docker Desktop's VM keeps a dead mount point at
`/host_mnt/<path>` once the host path has gone — so a restarted container hits
the same dead mount. Clearing it from inside the VM does not work either:
`umount -l` fails and `rmdir` reports `Device or resource busy`, because
Docker's own host-mount layer holds it, and it keeps holding it even with the
containers stopped. Only restarting Docker reinitialises that layer.

```bash
fw maint disk-recover --check   # diagnose only; exit 1 if anything is broken
fw maint disk-recover           # repair what is broken
fw maint disk-recover --dry     # show the steps without running them
```

The repair is: restart Docker (`docker desktop restart`, ~10s) → **recreate**
the registry and MinIO (recreate, not restart, so the bind mounts resolve
afresh) → kickstart the fleet-agent so it rebuilds the runners → verify.

Verification deliberately uses a **real object listing** rather than MinIO's
health endpoint, because the health endpoint is exactly what lies in this
failure. Reconnect the drive first: the command stops immediately if the path is
absent, since nothing else can work without it.

After recovering, re-run whatever was interrupted — typically `fw fleet rollout`.
