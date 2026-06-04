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

Run everything as local Python processes — no Docker required. Only needs MongoDB reachable at `AFL_MONGODB_URL`.

```bash
# One command: stop old runners, verify MongoDB, seed examples, start runners + dashboard
scripts/easy-local.sh

# With options
scripts/easy-local.sh --example osm-geocoder          # single example
scripts/easy-local.sh --instances 3                    # 3 concurrent runners
scripts/easy-local.sh --no-seed                        # skip seeding
scripts/easy-local.sh -- --log-format text             # plain-text runner logs

# Open the dashboard
open http://localhost:8080
```

Or manage runners directly:

```bash
# Register handlers and start runner(s) + dashboard
scripts/start-runner -- --log-format text
scripts/start-runner --example hiv-drug-resistance --instances 3

# Stop all local runners
scripts/stop-runners
```

### Quick Start: Docker Mode

```bash
# Start the stack (dashboard, runner, agents)
docker compose up -d

# Or use the setup script for a guided bootstrap
scripts/setup                              # defaults: 1 runner, 1 agent
scripts/setup --runners 3 --agents 2       # scaled deployment
scripts/setup --build                      # rebuild images first

# One-command pipeline: teardown → rebuild → setup → seed
scripts/easy.sh

# Open the dashboard
open http://localhost:8080
```

For a richer dev environment that runs **one container per
standalone `fwh_*` example** (anthropic, osm-geocoder, osm-lz,
noaa-weather, jenkins, census-us, genomics, sensor-monitoring) on
top of MongoDB + PostGIS + Jenkins + the dashboard, use the
**full-stack compose**:

```bash
scripts/install-example --all     # clone + pip install every fwh_* repo
scripts/full-stack up             # boots all 13 services
open http://localhost:8080
```

See [Full-stack Docker Compose](full-stack-compose.md) for the full
guide (architecture, scripts, env knobs, per-runner notes,
troubleshooting).

### Comparing Docker vs Local Mode

| Aspect | Docker Mode | Local Mode |
|--------|-------------|------------|
| **Startup** | `scripts/easy.sh` or `docker compose up` | `scripts/easy-local.sh` or `scripts/start-runner` |
| **MongoDB** | Can run in Docker or external | Must be external (running separately) |
| **Handler loading** | Container-internal paths, `RegistryRunner` | Host filesystem paths, `RegistryRunner` |
| **Scaling** | `docker compose up --scale runner=N` | `scripts/start-runner --instances N` |
| **Process isolation** | Full container isolation | OS process isolation |
| **File paths** | Container paths (`/app/...`) | Host paths (`/Users/...`) |
| **Shared data** | Docker volumes or bind mounts | Direct filesystem access |
| **Log output** | `docker compose logs -f runner` | Inline in terminal (stdout/stderr) |
| **Stop** | `docker compose down` | `scripts/stop-runners` |
| **Dependencies** | Docker Desktop | Python 3 + `.venv` with FFL packages |

**Important**: Docker agents and local runners should not be mixed for the same handler registrations. Docker containers use container-internal `sys.path` and cannot load handler modules registered with host filesystem paths, and vice versa. Stop Docker agents/runners before starting local ones:

```bash
docker compose down          # stop all Docker services
scripts/easy-local.sh        # start local runners
```

### Multi-Node Distributed Execution

Both deployment models support horizontal scaling across multiple machines. Multiple runners on different hosts cooperate on the same workflow automatically — the MongoDB task queue ensures each task is claimed by exactly one runner.

**Requirements for multi-node:**

1. **Shared MongoDB**: All machines point to the same `AFL_MONGODB_URL` (use IP or DNS hostname accessible from all nodes)
2. **Handler code**: Same repo checkout with `.venv` and dependencies installed on each machine
3. **Shared data** (optional): NFS/SMB mount for `AFL_GEOFABRIK_MIRROR`, `AFL_DATA_ROOT`, etc. — or let each machine download its own copies (cache misses are handled automatically)

```bash
# On each machine: start local runner(s) pointing to shared MongoDB
AFL_MONGODB_URL=mongodb://db-server:27017 scripts/easy-local.sh --no-seed --instances 4

# Or with remote runner management (SSH-based)
scripts/start-runner --all --example osm-geocoder    # start on all AFL_RUNNER_HOSTS
scripts/start-runner --host worker1 --host worker2   # specific hosts
scripts/stop-runners --all                           # stop all remote runners
scripts/rolling-deploy --example osm-geocoder        # zero-downtime restart
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

### Local Scratch & Multi-Server Semantics

A common question when going multi-server: *each runner has its own local `/tmp` — doesn't that break distributed execution?* No: **temp is intentionally per-runner, per-task, local-only, and never crosses hosts.** Everything that needs to be shared crosses host boundaries through MongoDB (coordination) and the durable storage backend (data).

#### Three planes

| Plane | Where it lives | Who shares it |
|---|---|---|
| **Coordination** — workflows, runners, tasks, steps | MongoDB (`AFL_MONGODB_URL`) | All runners on all hosts |
| **Durable data** — caches (`network/`, PBFs), outputs (layers, routes, maps) | `AFL_DATA_ROOT` / `AFL_OSM_OUTPUT_BASE` (local path, `hdfs://`, `s3://`, or a shared mount) | All runners on all hosts |
| **Local scratch** — staging, in-flight temp files, the `localize()` warm cache | Per-host: `AFL_LOCAL_SCRATCH` (or system temp), `AFL_OUTPUT_BASE/tmp`, `AFL_OUTPUT_BASE/cache/osm-local` | **Only that one host** |

The pattern is **stage-locally → finalize-to-durable** (see [`finalize_output_file`](../../src/osm_geocoder/handlers/shared/_output.py) and `Storage.finalize_dir_from_local`). Object stores (S3/MinIO, WebHDFS) don't do streaming/partial writes, so handlers always write to a local temp first, then upload the complete object to the durable destination as the last step. The local temp is *workspace*, not state.

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

- `AFL_STORAGE=local` with `AFL_DATA_ROOT=/Volumes/afl_data` is single-host only (unless `/Volumes/afl_data` is the same shared mount on every host).
- `AFL_STORAGE=hdfs` or `AFL_STORAGE=s3` (with `AFL_DATA_ROOT=hdfs://…` or `s3://…`) yields step payloads with portable URIs — see [HDFS Integration](#hdfs-integration) and [S3 / MinIO Integration](#s3--minio-integration).

If you skip this — e.g. keep `AFL_OSM_OUTPUT_BASE` pointed at a *local* path while running on multiple hosts — host A writes `/var/afl/output/.../merged.geojson` on its own disk, host B can't see it, and the next step fails. The S3/MinIO/HDFS work is what turns those step references into something every runner can resolve.

#### Operator notes

- **Localize cache grows over time.** Each runner caches everything it has `localize()`d at `AFL_OUTPUT_BASE/cache/osm-local/…` (or the explicit `target_dir`). It's *just* a cache — safe to prune. Some replication across hosts is the trade-off for not requiring a shared mount.
- **Stale temps from dead tasks.** A crashed task can leave `tmp*.geojson` (or similar) under `AFL_LOCAL_SCRATCH` / `AFL_OUTPUT_BASE/tmp`. Periodic cleanup is the operator's job.
- **Workers per host.** `AFL_MAX_CONCURRENT` and `--instances` control concurrency on a host; tune for memory headroom (one routing handler can hold its loaded network in `_GRAPH_CACHE`, ~MBs to ~GBs depending on the artifact).

### Production Recommendations

- **MongoDB**: Dedicated server or managed service (MongoDB Atlas) with replica sets for HA
- **Dashboard**: Single instance behind a reverse proxy (nginx/caddy)
- **Runners**: Multiple instances per worker node, scaled via `--instances N` and `--max-concurrent M`
- **Monitoring**: Dashboard at `/v2/workflows` and `/v2/servers`; API at `/api/servers` for health checks
- **Crash recovery**: Orphan reaper automatically resets tasks from dead runners (configurable via `AFL_REAPER_TIMEOUT_MS`)

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AFL_MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `AFL_MONGODB_DATABASE` | `afl` | Database name |
| `AFL_MONGODB_USERNAME` | | MongoDB authentication username |
| `AFL_MONGODB_PASSWORD` | | MongoDB authentication password |
| `AFL_MONGODB_AUTH_SOURCE` | `admin` | MongoDB auth database |
| `AFL_CONFIG` | | Path to `afl.config.json` file |

### Config File (`afl.config.json`)

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

The config file is searched in order: `$AFL_CONFIG`, `./afl.config.json`, `~/.ffl/afl.config.json`, `/etc/ffl/afl.config.json`.

## Service Reference

### Dashboard

Web UI for monitoring and managing workflows.

```bash
# Docker
docker compose up -d dashboard

# Direct
python -m afl.dashboard --host 0.0.0.0 --port 8080
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
python -m afl.runtime.runner
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
python -m afl.mcp
```

| Option | Default | Description |
|--------|---------|-------------|
| `--transport` | `stdio` | MCP transport |
| `--config` | | Path to FFL config file |
| `--log-level` | `WARNING` | Log level |
| `--log-file` | | Log to file (recommended for stdio) |

## Monitoring

### Dashboard Pages

The main navigation uses a 2-tab layout (**Workflows** / **Servers**) with a **More** dropdown for secondary pages. `GET /` redirects to `/v2/workflows`.

| Page | URL | Content |
|------|-----|---------|
| Workflows (v2) | `/v2/workflows` | Namespace-grouped runners with Running/Completed/Failed sub-tabs, HTMX 5s auto-refresh |
| Workflow Detail (v2) | `/v2/workflows/{id}` | Step sub-tabs (Running/Error/Complete), inline step expansion, pause/cancel/resume actions |
| Servers (v2) | `/v2/servers` | Server-group accordion with Running/Startup/Error/Shutdown sub-tabs, HTMX 5s auto-refresh |
| Server Detail (v2) | `/v2/servers/{id}` | Details, topics, handlers, handled stats, error display with live polling |
| Runners | `/runners` | Active/completed/failed workflow executions (legacy) |
| Flows | `/flows` | Compiled workflow definitions and sources |
| Tasks | `/tasks` | Event task queue (pending, running, completed, failed) |
| Servers | `/servers` | Registered agent servers with heartbeat status (legacy) |
| Events | `/events` | Event lifecycle tracking |
| Handlers | `/handlers` | Registered handler modules |
| Sources | `/sources` | Published FFL source namespaces |
| Locks | `/locks` | Distributed lock status |
| Namespaces | `/namespaces` | Namespace definitions across flows |

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

fs = get_fs()                                  # backend chosen from config (below)
data = fs.read_bytes("regions/california.osm.pbf")
fs.write_json("out/summary.json", {"count": 42})
text = fs.read_text("s3://other-bucket/notes.txt")   # explicit URI overrides default
```

Convenience methods: `open / read_bytes / read_text / read_json / write_bytes /
write_text / write_json / exists / isfile / isdir / listdir / walk / makedirs /
getsize / localize`. Module-level shims (`read_text`, `write_text`, `fs_open`,
`fs_exists`, …) delegate to the process-global `get_fs()` for terse call sites.

### Selecting the default backend (`AFL_FS_BACKEND` / `AFL_FS_ROOT`)

The facade picks its default backend in the order **Hadoop → MinIO/S3 → local
file**:

| Variable | Meaning |
|----------|---------|
| `AFL_FS_ROOT` | Base URI/path bare paths resolve under. Its **URI scheme decides the backend** — `hdfs://nn/user/afl`, `s3://my-bucket/cache`, or a local dir. |
| `AFL_FS_BACKEND` | Explicit override: `auto` (default), `hdfs`, `s3`, or `local`. |

With `auto` (no `AFL_FS_ROOT`), the facade also honours the existing fleet
signals so it "just works" alongside the cache config: `AFL_STORAGE=hdfs` +
`AFL_HDFS_HOST` → HDFS; `AFL_STORAGE=s3` or `AFL_S3_BUCKET` → S3; otherwise
local. Backend credentials/endpoints are unchanged (`AFL_S3_ENDPOINT`,
`AFL_WEBHDFS_PORT`, the standard AWS chain, etc.).

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
scripts/setup --hdfs --osm-agents 2 --build
```

### Running OSM Agents with HDFS Cache

When using the override file, the following environment variables are set automatically on OSM agent containers:

| Variable | Value | Description |
|----------|-------|-------------|
| `AFL_CACHE_ROOT` | `hdfs://afl-hadoop-hdfs:8020/cache` | Sidecar cache root (OSM PBF + handler caches under `<root>/<namespace>/`). Or set `AFL_STORAGE=hdfs` to root everything at `/user/afl`. (Replaces the retired `AFL_CACHE_DIR`.) |
| `GRAPHHOPPER_GRAPH_DIR` | `hdfs://afl-hadoop-hdfs:8020/graphhopper` | GraphHopper routing graphs |
| `AFL_GTFS_CACHE_DIR` | `hdfs://afl-hadoop-hdfs:8020/gtfs-cache` | GTFS feed cache |

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
scripts/setup --hdfs \
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
point the fleet at a different MinIO/S3, override `AFL_S3_ENDPOINT` /
`AFL_S3_ACCESS_KEY` / `AFL_S3_SECRET_KEY` / `AFL_S3_BUCKET` in `.env`. The rest
of this section covers a standalone MinIO + the env contract in detail.

### Requirements

- **`boto3`** — install the `s3` extra: `pip install -e ".[s3]"` (boto3 is soft-imported, so it's only needed when an `s3://` path is used). The full-stack example-runner image already includes it.
- **A bucket** on the object store (the bundled `minio-setup` creates it; standalone instructions below).
- For MinIO: the **MinIO container** (below, or the bundled compose service). For AWS S3: nothing to run — just set credentials and omit `AFL_S3_ENDPOINT`.

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
AFL_STORAGE=s3
AFL_DATA_ROOT=s3://afl-cache                       # durable cache root → s3://afl-cache/cache/…
AFL_OSM_OUTPUT_BASE=s3://afl-cache/osm-output      # handler outputs (layers, networks, routes)
AFL_S3_ENDPOINT=http://<minio-host>:9000           # OMIT for real AWS S3
AFL_S3_ACCESS_KEY=minioadmin                        # or the standard AWS_ACCESS_KEY_ID
AFL_S3_SECRET_KEY=minioadmin                        # or AWS_SECRET_ACCESS_KEY
# AFL_S3_REGION=us-east-1                            # optional
AFL_OUTPUT_BASE=/var/afl/local                      # KEEP LOCAL — see gotcha below
```

| Variable | Example | Description |
|----------|---------|-------------|
| `AFL_STORAGE` | `s3` | Selects the backend (`local` \| `hdfs` \| `s3`). |
| `AFL_DATA_ROOT` | `s3://afl-cache` | Durable cache root; the sidecar cache lives under `<root>/cache/<namespace>/`. |
| `AFL_OSM_OUTPUT_BASE` | `s3://afl-cache/osm-output` | Where OSM handler outputs are written (so downstream step payloads carry `s3://`). |
| `AFL_S3_ENDPOINT` | `http://localhost:9000` | Object-store endpoint. **Unset → real AWS S3.** |
| `AFL_S3_ACCESS_KEY` / `AFL_S3_SECRET_KEY` | `minioadmin` | Credentials (or the standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` chain). |
| `AFL_S3_REGION` | `us-east-1` | Region (default `us-east-1`). |

> **Gotcha — keep `AFL_OUTPUT_BASE` local.** Scratch/staging/temp must live on a local filesystem (you stage locally, then finalize onto the object store). The cache's `staging`/`tmp`/`locks` roots fall back to a local base automatically when `AFL_DATA_ROOT` is remote (override with `AFL_LOCAL_SCRATCH`), but `AFL_OUTPUT_BASE` also feeds the runtime's temp dir — point it at a **local** path, not an `s3://` URI. Put the durable artifacts on S3 via `AFL_DATA_ROOT` + `AFL_OSM_OUTPUT_BASE`.

### Running S3 tests

```bash
# Path-helper/dispatch tests always run; the live round-trip is gated on AFL_S3_ENDPOINT:
AFL_S3_ENDPOINT=http://localhost:9000 \
  AFL_S3_ACCESS_KEY=minioadmin AFL_S3_SECRET_KEY=minioadmin \
  pytest tests/runtime/test_s3_storage.py -v
```

Without `AFL_S3_ENDPOINT`, the live round-trip is skipped automatically.

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
scripts/setup --jenkins                    # Jenkins only
scripts/setup --jenkins --build            # Rebuild images first
```

### External Storage for Jenkins

By default, Jenkins uses a Docker named volume (`jenkins_home`). To place Jenkins data on an external filesystem, set the `JENKINS_HOME_DIR` environment variable:

```bash
# Use an external directory for Jenkins data
export JENKINS_HOME_DIR=/mnt/ssd/jenkins
docker compose --profile jenkins up -d

# Or via the setup script
scripts/setup --jenkins --jenkins-home-dir /mnt/ssd/jenkins
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
| `AFL_POSTGIS_URL` | `postgresql://afl:afl@postgis:5432/afl_gis` | PostGIS connection string |

### External Storage for PostGIS

By default, PostGIS uses a Docker named volume (`postgis_data`). To place data on an external filesystem, set the `POSTGIS_DATA_DIR` environment variable:

```bash
# Use an external directory for PostGIS data
export POSTGIS_DATA_DIR=/mnt/ssd/postgis
docker compose --profile postgis up -d

# Or via the setup script
scripts/setup --postgis --postgis-data-dir /mnt/ssd/postgis
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
scripts/setup --mongodb-data-dir /mnt/ssd/mongodb
```

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_DATA_DIR` | `mongodb_data` (named volume) | Host path for MongoDB data files |

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
2. **Same repo layout**: the Facetwork repo must be checked out on every remote host at the same path (or set `AFL_REMOTE_PATH`)
3. **MongoDB reachable**: every runner host must be able to reach the MongoDB instance specified by `AFL_MONGODB_URL`
4. **Host inventory**: configure `AFL_RUNNER_HOSTS` in `.env` or pass `--host` flags

```bash
# .env
AFL_RUNNER_HOSTS=prod-runner-01 prod-runner-02 prod-runner-03
AFL_REMOTE_PATH=/opt/facetwork    # optional, defaults to local repo root
AFL_SSH_OPTS=-i ~/.ssh/deploy_key  # optional extra SSH flags
```

### Local runner lifecycle

```bash
# Register handlers and start runner + dashboard on this machine
scripts/start-runner --example hiv-drug-resistance -- --log-format text

# Register ALL examples, start 3 runner instances, skip dashboard
scripts/start-runner --instances 3 --no-dashboard

# Stop all local runners and dashboard
scripts/stop-runners
```

### Remote runner lifecycle

```bash
# Start runners on all configured hosts
scripts/start-runner --all --example hiv-drug-resistance -- --log-format text

# Start on specific hosts only
scripts/start-runner --host prod-runner-01 --host prod-runner-02 --example hiv-drug-resistance

# Stop all remote runners (queries MongoDB for running servers)
scripts/stop-runners --all

# Stop runners on specific hosts
scripts/stop-runners --host prod-runner-01 --host prod-runner-02

# Stop with longer drain timeout (default: 30s)
scripts/stop-runners --all --drain-timeout 60
```

### Rolling deploy (zero-downtime)

The `scripts/rolling-deploy` script performs a serial rolling restart: for each runner it drains the old process (SIGTERM → wait for SHUTDOWN), starts a new one, and waits for it to register in MongoDB before moving to the next. This ensures at least N-1 runners are always available.

```bash
# Rolling restart all servers, re-register all example handlers
scripts/rolling-deploy

# Rolling restart with specific handlers
scripts/rolling-deploy --example hiv-drug-resistance --example devops-deploy

# Target specific hosts
scripts/rolling-deploy --host prod-runner-01 --host prod-runner-02

# Custom timeouts
scripts/rolling-deploy --drain-timeout 90 --start-timeout 90

# Skip handler re-registration (code-only restart, handlers unchanged)
scripts/rolling-deploy --skip-registration

# Pass extra args to the runner service
scripts/rolling-deploy --example hiv-drug-resistance -- --log-format text --max-concurrent 10
```

**Rolling deploy flow per server:**
1. Send SIGTERM via SSH (triggers graceful drain — finishes current tasks, stops polling)
2. Poll MongoDB until server state = `shutdown` (timeout: `--drain-timeout`, default 60s)
3. If HTTP port is known (persisted in MongoDB), verify health endpoint is unreachable
4. Start new runner via SSH (`nohup scripts/runner --registry ...`)
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
- The dashboard Fleet page (`/v2/fleet`) shows servers in `down` state when their heartbeat is stale, providing visual confirmation

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
- Down timeout: 5 minutes (`SERVER_DOWN_TIMEOUT_MS` in `afl/dashboard/helpers.py`, reused in `reap_orphaned_tasks()`)
- Heartbeat interval: 10 seconds (configurable via `AFL_HEARTBEAT_INTERVAL_MS`)

### Verifying runner state

Each runner persists its HTTP status port in MongoDB (`ServerDefinition.http_port`), enabling remote health checks.

```bash
# List all running servers from MongoDB
python3 -c "
from afl.runtime.mongo_store import MongoStore
store = MongoStore('mongodb://afl-mongodb:27017')
for s in store.get_servers_by_state('running'):
    print(f'{s.server_name}: port={s.http_port}, state={s.state}, id={s.uuid}')
"

# Health-check a specific runner
curl http://prod-runner-01:8080/health

# Detailed status (uptime, active work items, handled counts)
curl http://prod-runner-01:8080/status
```

### Shared helpers (`scripts/_remote.sh`)

The remote management scripts share a common helper library sourced after `_env.sh`:

| Function | Purpose |
|----------|---------|
| `_afl_resolve_remote_env` | Resolves `AFL_RUNNER_HOSTS`, `AFL_REMOTE_PATH`, `AFL_SSH_OPTS` |
| `_afl_ssh <host> <cmd>` | SSH wrapper with `BatchMode=yes`, `ConnectTimeout=5` |
| `_afl_query_running_servers` | Queries MongoDB, outputs `server_name http_port uuid` per line |
| `_afl_get_server_state <uuid>` | Returns current state of a server by UUID |
| `_afl_poll_server_state <uuid> <state> <timeout>` | Polls until server reaches expected state |
| `_afl_poll_new_server <host> <state> <timeout> [exclude...]` | Polls until a new server appears on hostname |
| `_afl_resolve_hosts [hosts...]` | Resolves target hosts from args or `AFL_RUNNER_HOSTS` |
