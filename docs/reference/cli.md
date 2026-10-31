## Build & Run Reference

### Setup virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"                           # dev only
pip install -e ".[dev,test,dashboard,mcp,mongodb]" # full stack
```

### CLI usage
```bash
afl input.ffl -o output.json       # compile to JSON
echo 'facet Test()' | afl          # parse from stdin
afl input.ffl --check              # syntax check only
afl input.ffl --config config.json # custom config
```

### Services
```bash
python -m facetwork.dashboard                              # dashboard (port 8080)
python -m facetwork.dashboard --port 9000 --reload         # dev mode
python -m facetwork.runtime.runner                         # runner service
python -m facetwork.runtime.runner --topics TopicA TopicB  # filtered topics
python -m facetwork.runtime.runner --max-concurrent 10     # increase concurrency
python -m facetwork.mcp                                    # MCP server (stdio)
```

### Scala agent library
```bash
cd agents/scala/fw-agent && sbt compile  # compile
cd agents/scala/fw-agent && sbt test     # run tests
cd agents/scala/fw-agent && sbt package  # package JAR
```

### Convenience scripts
Every operational command goes through `fw <group> <command>`; each is a file under
`scripts/lib/<group>/` (shared helpers live in `scripts/lib/_helpers/`):
```bash
scripts/lib/_helpers/_env.sh                                # shared env loader (sourced by other scripts)
scripts/lib/_helpers/_remote.sh                             # shared SSH/MongoDB helpers for remote management
fw single up                                # one-command pipeline (teardown → rebuild → setup → seed)
fw install setup                                  # bootstrap Docker stack
fw install setup --runners 3 --agents 2           # start with scaling
fw ffl compile input.ffl -o output.json       # compile FFL
fw ffl publish input.ffl                      # compile + publish to MongoDB
fw ffl publish input.ffl --auto-resolve       # with dependency resolution
fw ffl run-workflow                           # interactive workflow execution
fw ffl bake-envs --check                      # list declared script environments
fw ffl bake-envs                              # materialize their venvs (FW_ENV_ROOT)
fw ffl run-workflow --workflow Name            # run specific workflow
fw runner server --workflow MyWorkflow           # execute workflow (server mode)
fw runner exec                                 # start runner
fw svc dashboard                              # start dashboard
fw svc mcp                             # start MCP server
fw db stats                               # show DB statistics
fw runner start                           # register handlers + start runner locally
fw runner start --all                     # start runners on all remote hosts
fw runner stop                           # stop local runners
fw runner stop --all                     # stop runners on all remote hosts
fw fleet rolling-deploy                         # zero-downtime rolling restart
fw runner list                           # tree view: servers → runners → handlers
fw runner list --state running           # filter by state
fw runner list --json                    # machine-readable output
```

### Command index (`fw`)

Every command `fw` dispatches to, with its `# fw-summary:` blurb (`fw <group>` prints the same
list on the command line; run a command with `--help` for its options).

**db**

| Command | What it does |
|---|---|
| `fw db check` | Check connectivity to the three databases Facetwork depends on (MongoDB, PostGIS, the object store). |
| `fw db drop-collections` | Empty all collections in the afl MongoDB database. |
| `fw db import-pg` | Start a disposable Docker-based PostgreSQL/PostGIS instance for OSM import staging. |
| `fw db mongo-start` | Start a local mongod (outside Docker); stop it with `fw db mongo-stop`. |
| `fw db mongo-stop` | Stop the local mongod process started by `fw db mongo-start`. |
| `fw db pg-start` | Start a local PostgreSQL/PostGIS server (outside Docker). |
| `fw db postgis drop-tables` | Drop OSM import tables from PostGIS (osm_nodes, osm_ways, osm_import_log). |
| `fw db postgis kill-vacuum` | Kill running autovacuum processes on PostGIS OSM tables. |
| `fw db postgis tune` | Tune PostgreSQL for bulk OSM imports on a 32GB machine. |
| `fw db postgis vacuum` | Run VACUUM ANALYZE on PostGIS OSM tables. |
| `fw db postgis vacuum-status` | Check if a vacuum or analyze is currently running on PostGIS tables. |
| `fw db stats` | Display Facetwork database statistics. |

**ffl**

| Command | What it does |
|---|---|
| `fw ffl bake-envs` | sweep FFL for `environment` decls and materialize their venvs (FW_ENV_ROOT) |
| `fw ffl catalog` | Claude workflow catalog: backup / restore / import. |
| `fw ffl catalog-import-all` | Import EVERY discoverable example package's FFL into the Claude workflow catalog. |
| `fw ffl compile` | Compile FFL source files to JSON workflow definitions. |
| `fw ffl lsp` | Start the FFL language server (LSP) on stdio, for editors. |
| `fw ffl migrate-after` | rewrite the dependency_signal idiom to the `after` ordering clause |
| `fw ffl migrate-scoping` | migrate legacy FFL to the relative $-scoping model (dry-run/--write) |
| `fw ffl publish` | Compile an FFL file and publish it to MongoDB. |
| `fw ffl run` | ffl-run — FFL-first workflow submission. |
| `fw ffl run-workflow` | Run a workflow stored in MongoDB. |
| `fw ffl scaffold` | Scaffold a new handler (FFL facet + Python handler + test stubs) for review. |
| `fw ffl seed` | Compile all example FFL files and push the flow + workflow definitions to MongoDB. |

**fleet**

| Command | What it does |
|---|---|
| `fw fleet advertise` | advertise Mongo+MinIO over mDNS for zero-config fleet discovery |
| `fw fleet agent` | per-server daemon: reconcile this host to fleet_config (apply/watch) |
| `fw fleet agent-cmd` | send a command (status/recreate/restart) to a host's fleet-agent |
| `fw fleet agent-image` | build+push a polyglot agent's OWN image multi-arch (gh-router), verify every fleet arch, pin its role |
| `fw fleet agent-liveness` | external liveness watchdog — kickstart a FROZEN fleet-agent (its log went stale) |
| `fw fleet allow-restart` | let this host's owner restart the fleet-agent without sudo (polkit) |
| `fw fleet config` | manage central fleet_config — set/get/status/secret |
| `fw fleet env-migrate` | fleet-env-migrate — split this server's .env.fleet into the shared base |
| `fw fleet get` | get the central fleet_config (forwards to the fleet config admin) |
| `fw fleet registry-setup` | add the fleet's HTTP registry to a host's docker insecure-registries (+restart) |
| `fw fleet rolling-deploy` | Rolling deploy: zero-downtime serial restart of remote AFL runners. |
| `fw fleet rollout` | build+push the runner image at HEAD, point fleet_config at it, wait for converge |
| `fw fleet router` | per-region GraphHopper routers: add/remove/list/wait (placed by fleet-agents) |
| `fw fleet scale` | set the central osm-geocoder replica count (fleet_config); agents reconcile |
| `fw fleet secret` | manage encrypted MinIO creds in fleet_config (gen-key/set/show) |
| `fw fleet servers` | server catalog (servers.json) — list machines + live-resolved IPs; --env/--infra-name/--resolve/--service-host/--container-ip; --push distributes it |
| `fw fleet set` | set the central fleet_config (forwards to the fleet config admin) |
| `fw fleet simulate` | simulate-fleet — verify the multi-server fleet controller END-TO-END on one box. |
| `fw fleet status` | status the central fleet_config (forwards to the fleet config admin) |
| `fw fleet unregistered` | name the runner containers on this host that have no server record |
| `fw fleet verify-domain-parity` | verify every architecture of an image contains the SAME domain commits |

**install**

| Command | What it does |
|---|---|
| `fw install anthropic` | Convenience wrapper: clone + install fwh_anthropic with per-area extras. |
| `fw install check` | analyze missing test/example dependencies on this host (optionally --install them) |
| `fw install clone-handlers` | git clone every catalog domain repo into ~/fw_handlers (skip existing) |
| `fw install domain` | Clone + install a standalone Facetwork domain package. |
| `fw install pull-handlers` | git pull every catalog domain repo in ~/fw_handlers (no pip reinstall) |
| `fw install rebuild` | AgentFlow Docker Rebuild Script |
| `fw install repair-venv` | Repair a virtualenv whose console-script shebangs / activate scripts point at a moved path. |
| `fw install repo` | Bootstrap a local Python development environment for Facetwork. |
| `fw install setup` | AgentFlow Docker Bootstrap Script |
| `fw install teardown` | AgentFlow Docker Teardown Script |
| `fw install tools` | Install all system-level tools required for Facetwork development via Homebrew. |

**maint**

| Command | What it does |
|---|---|
| `fw maint backup-mongo` | dump the control-plane database to the archive host, and verify it |
| `fw maint cache-index` | render the durable-cache landing-page index.html |
| `fw maint dead-letters` | report tasks that exhausted their retries and were then forgotten |
| `fw maint disk-guard` | Docker VM disk guard — keep the Docker Desktop VM's disk from filling up. |
| `fw maint disk-recover` | recover the stack after the external data disk dropped and returned |
| `fw maint migrate-seed-prefix` | migrate seeded domain flows from the example: prefix to domain: (default dry-run) |
| `fw maint migrate-task-index` | widen the running-task unique index to exclude stepless tasks |
| `fw maint path-check` | find absolute paths in configs that do not resolve on this host |
| `fw maint purge-servers` | delete stale shutdown-state server docs from Mongo (default: dry-run count) |
| `fw maint repair-workflow` | Diagnose and repair a stuck AFL workflow. |
| `fw maint task-liveness` | show whether RUNNING tasks are actually alive (heartbeat, not last-update) |
| `fw maint terminate-workflow` | Terminate a stuck/abandoned workflow by marking its runner, steps, and tasks terminal. |
| `fw maint unsatisfiable` | find pending tasks whose resource requirements no runner can ever meet |
| `fw maint workflow-stats` | per-server statistics for a finished (or running) workflow run |

**mode**

| Command | What it does |
|---|---|
| `fw mode cluster` | rejoin the shared fleet — infra lives on the infra host; recreate runners (Model B) |
| `fw mode join` | (re)start THIS host as a cluster runner after a `leave` (Model A; no infra change) |
| `fw mode leave` | stop THIS host contributing to the cluster as a runner (Model A; no infra change) |
| `fw mode local` | run the WHOLE stack on this machine (own Mongo/MinIO/registry); recreate runners (Model B) |
| `fw mode status` | show the active mode (local/cluster) + where infra resolves + runner state |

**runner**

| Command | What it does |
|---|---|
| `fw runner add-local` | add a couple of local handler runners (no dashboard), convenience shorthand |
| `fw runner drain` | Gracefully drain AFL runners: stop processes and reset running tasks to pending. |
| `fw runner exec` | Start the AFL distributed runner service. |
| `fw runner list` | List servers, runners, and handlers in a tree view. |
| `fw runner scale` | scale a per-domain runner service on THIS host (docker compose --scale) |
| `fw runner server` | Start the AFL agent runner server. |
| `fw runner start` | Register handlers for examples, then start the runner and dashboard. |
| `fw runner start-all` | Start the full fleet on THIS host: dashboard + the ffl-runner orchestration tier + the catalog domain runners. |
| `fw runner stop` | Stop all running AFL runner and dashboard processes. |
| `fw runner sys` | send a system command (status/pause/resume) to specific runners |

**single**

| Command | What it does |
|---|---|
| `fw single docker-dev` | Start the ephemeral dev stack: MongoDB + PostGIS in Docker. |
| `fw single full-stack` | Manage the full-stack development environment defined by `docker-compose.full-stack.yml`. |
| `fw single reboot` | Reboot the AgentFlow dev environment: stop everything, clear MongoDB, start again. |
| `fw single rebuild-runners` | Rebuild the local per-example runner ("worker") images for the fleet stack. |
| `fw single restart` | Pull latest code + handlers, then rebuild and cleanly recreate the fleet's runners. |
| `fw single up` | Easy one-command pipeline: teardown, rebuild, setup, seed, run. |
| `fw single up-local` | Easy one-command pipeline for LOCAL runners (no Docker required). |

**svc**

| Command | What it does |
|---|---|
| `fw svc airisk-update` | refresh the AI Risk Clock (pace of AI-risk drivers + the self-updating paper) and republish it; --install adds the daily timer |
| `fw svc dashboard` | Start the Facetwork web dashboard. |
| `fw svc dashboard-stop` | Stop the AFL dashboard process. |
| `fw svc grafana` | Start Grafana for AgentFlow operational monitoring. |
| `fw svc maps` | local maps gallery — browse the maps sitting in MinIO (no git repo) |
| `fw svc maps-install` | install/uninstall a launchd unit that keeps `fw svc maps` always up |
| `fw svc mcp` | Start the AFL MCP server. |
| `fw svc nfl-update` | refresh NFL data, ratings, predictions and odds, then republish the static site; --install adds the timer |
| `fw svc osm-admin-regen` | regenerate admin-level OSM sub-region extracts; --install adds the weekly launchd timer |
| `fw svc osm-extracts` | serve the self-hosted OSM extracts + replication diffs (our Geofabrik) |
| `fw svc osm-replicate` | publish new per-region OSM replication diffs; --install adds the nightly timer |
| `fw svc osm-report` | status report on the OSM extract stores (sizes, ages, missing regions) |
| `fw svc osm-watchdog` | independent watchdog that alarms if OSM replication or its indexes stall |
| `fw svc selfcheck` | periodic sweep of the checks that catch SILENT failures, alarming only on a real one |
| `fw svc sports-update` | refresh NBA/WNBA/MLB/MLS/NWSL data, ratings, predictions and odds, then republish the five league sites; --install adds the timer |
| `fw svc stocks-snapshot` | mark open paper-trading groups to market; --install adds the weekday launchd timer |

**util**

| Command | What it does |
|---|---|
| `fw util check-doc-links` | Validate internal markdown cross-references. |
| `fw util ffl-audit` | validate every domain repo's FFL + handler contracts against the current runtime |
| `fw util gen-compose` | sync per-domain compose ENV from the catalog into docker-compose.full-stack.yml |
| `fw util memory-sync` | commit + push this project's Claude memory repo (~/.claude/.../memory) |
| `fw util serve-map` | serve PMTiles/HTML maps over range-capable HTTP |
| `fw util site-scan` | find host names, IPs, home paths and personal emails in the repo (and --domains: every fwh_* repo) |
| `fw util thesis-pdf` | Re-render the thesis Markdown sources as PDFs. |


### Docker stack
The `docker-compose.yml` defines the full development stack:
```bash
fw install setup                                               # bootstrap
fw install setup --runners 3 --agents 2 --osm-agents 1        # with scaling
docker compose up -d                                        # start directly
docker compose --profile seed run --rm seed                 # seed workflows
docker compose --profile mcp run --rm mcp                   # MCP server
docker compose --profile hdfs up -d                         # start HDFS
fw install setup --hdfs                                        # bootstrap with HDFS
docker compose down                                         # stop
docker compose down -v                                      # stop + remove volumes
```

#### Services

| Service | Port | Scalable | Description |
|---------|------|----------|-------------|
| `dashboard` | 8080 | No | Web dashboard |
| `runner` | - | Yes | Distributed runner service |
| `agent-addone` | - | Yes | Sample AddOne agent |
| `seed` | - | No | One-shot workflow seeder (profile: seed) |
| `mcp` | - | No | MCP server, stdio transport (profile: mcp) |
| `namenode` | 9870, 8020 | No | HDFS NameNode (profile: hdfs) |
| `datanode` | - | No | HDFS DataNode (profile: hdfs) |

#### Setup script options

| Option | Default | Description |
|--------|---------|-------------|
| `--runners N` | 1 | Runner service instances |
| `--agents N` | 1 | AddOne agent instances |
| `--osm-agents N` | 0 | Full OSM Geocoder agent instances |
| `--osm-lite-agents N` | 0 | Lightweight OSM agent instances |
| `--hdfs` | - | Start HDFS namenode + datanode services |
| `--build` | - | Force image rebuild before starting |
| `--check-only` | - | Verify Docker availability, then exit |

#### Rebuilding images — `rebuild` vs `rebuild-workers`

Two rebuild scripts exist; they target **different stacks** and read **different
compose files + env**, so picking the wrong one rebuilds the wrong images.

| | `fw install rebuild` | `fw single rebuild-runners` |
|---|---|---|
| Stack | base / overlay dev stack | fleet / full-stack |
| Compose files | `docker-compose.yml` + overlays (from `.afl-active-config`/`.env`, via `_compute_compose_args`) | `docker-compose.full-stack.yml` + `docker-compose.fleet.yml` |
| Env | `.env` (via `_env.sh`) | `.env.fleet` then `.env.fleet.override` (per-host, wins) |
| Cache | **off** by default (`--cached` to reuse) | **on** by default (`--no-cache` to disable) |
| Restart | `--up` restarts the stack | `--up` **recreates** the runner containers (removes existing first → no orphan replicas) |
| Pin baked code | n/a | `--ref <sha\|branch>` → `--build-arg FWH_OSM_REF` (default `main`) |
| Scope | this host | this host |

**When to use which:**

- **`fw install rebuild`** — you changed **core engine / dashboard / compiler** code
  (the `facetwork/` package or the dashboard image), toggled an **overlay** or
  `INSTALL_HDFS`, and run the base stack (`fw install setup` → `docker-compose.yml`).
  Defaults to a full no-cache rebuild; add `--up` to restart.
  ```bash
  fw install rebuild            # full rebuild of the base stack
  fw install rebuild --cached   # reuse layer cache
  fw install rebuild --up       # rebuild + restart
  ```

- **`fw single rebuild-runners`** — you changed **handler code in an `fwh_*` example**
  (osm, anthropic, …) baked into the per-example runner ("worker") images, or you
  need to **recreate the runners cleanly** on the fleet/full-stack host.
  ```bash
  fw single rebuild-runners                 # build all worker images (cached)
  fw single rebuild-runners --ref <sha> --up  # bake a specific fwh_osm commit, then clean-recreate
  ```
  The `--up` path removes the existing worker containers **first**, then
  `up -d --remove-orphans` — so orphan replicas left by a plain `--force-recreate`
  don't linger and keep serving stale code/env.

**Neither deploys across machines.** To roll new handler code to the
`osm-geocoder` / `gh-router` **role on every fleet server**, use the registry
path instead — `buildx → push → fw fleet set --image` — see
[fleet-rollouts.md](../operations/fleet-rollouts.md). `rebuild`/`rebuild-workers`
only build (and optionally recreate) on the **local** host.

> Unsure which stack is up? `docker compose ls` shows the project's config
> files. If they're `full-stack.yml` + `fleet.yml`, you're on the fleet → use
> `rebuild-workers`; otherwise it's the base stack → use `rebuild`.

### macOS Docker Desktop: Volume Mounts and Network Storage

Docker Desktop for Mac uses VirtioFS to share host directories with containers. This introduces filesystem semantics differences when mounting network-attached storage (NAS) volumes.

#### SMB mounts (macOS → NAS)

SMB (Samba/CIFS) volumes mounted on macOS (e.g. `/Volumes/afl_data/`) exhibit a specific bug when bind-mounted into Docker containers via VirtioFS:

- **Writes work correctly**: Files created by the container are tracked by VirtioFS and fully accessible (open, stat, read).
- **Pre-existing files in subdirectories fail**: `os.path.isfile()`, `os.stat()`, and `open()` return errors for files that existed on the SMB share before the container started. `os.listdir()` (readdir) succeeds — the filenames are visible, but `stat()` on individual files fails.
- **Root-level files work**: Only files in subdirectories are affected.

**Impact on Facetwork**: The Geofabrik mirror (`FW_GEOFABRIK_MIRROR`) contains pre-existing `.osm.pbf` files in nested directories (e.g. `north-america/us/alabama-latest.osm.pbf`). When mounted from an SMB share, containers cannot read these files even though `listdir()` shows them.

**Workarounds**:
1. **Use a local APFS drive for the mirror** (recommended): Set `FW_GEOFABRIK_MIRROR` to a local or directly-attached drive (e.g. `/Volumes/afl_data_local/osm`). SMB is fine for write targets (`FW_DATA_ROOT`, `FW_OSM_OUTPUT_BASE`, `FW_LOCAL_OUTPUT_DIR`) since containers create those files.
2. **NFS export from the NAS**: NFS does not have this VirtioFS bug. If your NAS supports NFS, export the data directory and mount via NFS on macOS.
3. **readdir fallback**: The downloader (`https://github.com/rlemke/fwh_osm/blob/main/src/osm_geocoder/handlers/shared/downloader.py`) includes `_mirror_file_exists()` which falls back to `os.listdir()` when `os.path.isfile()` fails. This detects file presence but cannot fix the `open()` failure for actual reads.

**Summary of storage type behavior in Docker Desktop (macOS)**:

| Storage Type | readdir | stat/open (pre-existing) | stat/open (container-created) | Recommended Use |
|-------------|---------|--------------------------|-------------------------------|-----------------|
| Local APFS | ✅ | ✅ | ✅ | Mirror (read-only data) |
| SMB mount | ✅ | ❌ (subdirectory files) | ✅ | Write targets (cache, output) |
| NFS mount | ✅ | ✅ | ✅ | All purposes |
| Docker volume | ✅ | ✅ | ✅ | MongoDB data, ephemeral |

#### MongoDB cannot use SMB mounts

`MONGODB_DATA_DIR` on an SMB share causes MongoDB to crash on startup — the entrypoint `chown`/`find` fails on `.smbdelete` ghost files. Leave `MONGODB_DATA_DIR` unset (uses a Docker volume) or point it to a local drive.

#### Recommended `.env` for macOS with NAS

```bash
# Mirror on local drive (pre-existing PBF files need direct access)
FW_GEOFABRIK_MIRROR=/Volumes/afl_data_local/osm

# Write targets on NAS (SMB is fine for container-created files)
FW_DATA_ROOT=/Volumes/afl_data
FW_OSM_OUTPUT_BASE=/Volumes/afl_data/osm-output
FW_LOCAL_OUTPUT_DIR=/Volumes/afl_data/output

# MongoDB on Docker volume (never SMB)
# MONGODB_DATA_DIR=   (leave commented out)
```

### Environment Configuration

The `.env` file is the primary way to configure the Docker stack and convenience scripts.

**Setup:**
```bash
cp .env.example .env   # one-time copy
# Edit .env to set MongoDB port, scaling, overlays, data directories
fw single up        # runs the full pipeline using .env values
```

**How it works:**
- `scripts/lib/_helpers/_env.sh` is sourced by every convenience script. It reads `.env` from the project root and exports each variable **only if it is not already set** in the environment.
- `fw single up` translates `.env` variables into `fw install setup` CLI flags and runs the full pipeline (teardown → rebuild → setup → seed).
- Precedence: **CLI flags > env vars > `.env` > defaults**

**Variable reference:**

| Variable | Default | Description |
|----------|---------|-------------|
| **MongoDB** | | |
| `FW_MONGODB_URL` | `mongodb://afl-mongodb:27017` | MongoDB connection URL (external server, defined in `/etc/hosts`) |
| `FW_MONGODB_DATABASE` | `afl` | Database name (runtime: steps, tasks, runners, flows) |
| `FW_EXAMPLES_DATABASE` | `afl_examples` | Database for example handler data (weather reports, census output) |
| **Scaling** | | |
| `FW_RUNNERS` | `1` | Number of runner service instances |
| `FW_AGENTS` | `1` | Number of AddOne agent instances |
| `FW_OSM_AGENTS` | `0` | Full OSM Geocoder agent instances |
| `FW_OSM_LITE_AGENTS` | `0` | Lightweight OSM agent instances |
| **Overlays** | | |
| `FW_HDFS` | `false` | Enable HDFS overlay compose file and profile |
| `FW_POSTGIS` | `false` | Enable PostGIS overlay compose file and profile |
| `FW_JENKINS` | `false` | Enable Jenkins profile |
| `FW_GEOFABRIK_MIRROR` | `/Volumes/afl_data/osm` | Path to local Geofabrik mirror; mounted read-only at `/data/osm-mirror` in containers |
| **OSM data paths** | | |
| `FW_DATA_ROOT` | `/Volumes/afl_data` | Unified data root; OSM/handler caches live under `$FW_DATA_ROOT/cache/<namespace>/`. Override just the cache with `FW_CACHE_ROOT`; set `FW_STORAGE=hdfs` or `s3` for remote storage. (Replaces the retired `FW_CACHE_DIR`.) |
| `FW_STORAGE` | `local` | Storage backend: `local` \| `hdfs` \| `s3`. `s3` (AWS S3 / MinIO) makes cache + outputs portable across the fleet — see [S3 / MinIO Integration](../operations/deployment.md#s3--minio-integration). |
| `FW_FS_BACKEND` | `auto` | Default backend for the `get_fs()` [`FileSystem` facade](../operations/deployment.md#unified-file-access--the-filesystem-facade): `auto` \| `hdfs` \| `s3` \| `local`. `auto` infers from `FW_FS_ROOT`'s scheme (Hadoop → S3 → local). |
| `FW_FS_ROOT` | *(empty)* | Base URI/path bare paths resolve under for the facade, e.g. `hdfs://nn/user/afl`, `s3://my-bucket/cache`, or a local dir. The URI scheme selects the backend. |
| `FW_OSM_OUTPUT_BASE` | `/tmp` | OSM extractor output base (local path, `hdfs://`, or `s3://` URI) |
| **S3 / MinIO (when `FW_STORAGE=s3`)** | | Requires the `s3` extra (boto3). Keep `FW_OUTPUT_BASE` local. |
| `FW_S3_ENDPOINT` | *(AWS S3)* | Object-store endpoint, e.g. `http://localhost:9000` for MinIO. Unset → real AWS S3. |
| `FW_S3_ACCESS_KEY` / `FW_S3_SECRET_KEY` | *(AWS chain)* | Credentials (or the standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`). |
| `FW_S3_REGION` | `us-east-1` | S3 region. |
| `FW_LOCAL_SCRATCH` | *(system temp)* | Local base for staging/tmp/locks when `FW_DATA_ROOT` is remote. |
| `FW_LOCAL_OUTPUT_DIR` | `/Volumes/afl_data/output` | Handler output files (reports, maps, stats, GeoJSON). Used by all examples: osm-geocoder, census-us, hiv-drug-resistance, monte-carlo-risk, maven. Falls back to `/tmp` when unset. |
| `FW_LOCALIZE_MOUNTS` | *(empty)* | Comma-separated path prefixes for Docker mount paths that `localize()` should copy to container-local storage before processing. Avoids VirtioFS hangs on large files. Example: `/data/osm-mirror` |
| **Remote runner management** | | |
| `FW_RUNNER_HOSTS` | *(empty)* | Space-separated hostnames for remote runner management |
| `FW_REMOTE_PATH` | *(same as local)* | Repo path on remote hosts |
| `FW_SSH_OPTS` | *(empty)* | Extra SSH options (e.g. `-i ~/.ssh/deploy_key`) |
| **Runner tuning** | | |
| `FW_MAX_CONCURRENT` | `2` | Max concurrent work items per runner |
| `FW_POLL_INTERVAL_MS` | `1000` | Runner poll interval in milliseconds |
| `FW_POLL_IDLE_MAX_MS` | `5000` | Cap on idle backoff: consecutive empty polls double the wait up to this |
| `FW_CLAIM_PROXY_URL` | unset | Claim through the per-host claimer (`python -m facetwork.runtime.claimer`); unset or unreachable = claim directly. Fleet: `fw fleet set --claimer HOST,...\|all\|off` |
| `FW_CLAIMER_IDLE_MAX_MS` | `5000` | Claimer: cap on its idle backoff between merged claims |
| `FW_CLAIMER_RESERVE_HOLD_S` | `600` | Claimer: how long a handed-out memory floor stays reserved while the task runs |
| **LLM / Claude API** | | |
| `ANTHROPIC_API_KEY` | *(empty)* | Anthropic API key for Claude-powered prompt blocks. When unset, LLM handlers fall back to deterministic stubs. Required by: `ClaudeAgentRunner`, `LLMHandler`, and example handlers like `noaa-weather` GenerateNarrative. |
| **Data directories** | | |
| `GRAPHHOPPER_DATA_DIR` | *(Docker volume)* | Host path for GraphHopper data |
| `POSTGIS_DATA_DIR` | *(Docker volume)* | Host path for PostGIS data |
| `JENKINS_HOME_DIR` | *(Docker volume)* | Host path for Jenkins home |

### Configuration

FFL uses a JSON config file (`facetwork.config.json`) for service connections. Resolution order:

1. Explicit `--config FILE` CLI argument
2. `FW_CONFIG` environment variable
3. `facetwork.config.json` in the current directory, `~/.ffl/`, or `/etc/ffl/`
4. Environment variables (`FW_MONGODB_*`)
5. Built-in defaults

**Example configuration:**
```json
{
  "mongodb": {
    "url": "mongodb://localhost:27017",
    "username": "",
    "password": "",
    "authSource": "admin",
    "database": "afl"
  }
}
```

**Environment variables:**
| Variable | Default |
|----------|---------|
| `FW_MONGODB_URL` | `mongodb://localhost:27017` |
| `FW_MONGODB_USERNAME` | (empty) |
| `FW_MONGODB_PASSWORD` | (empty) |
| `FW_MONGODB_AUTH_SOURCE` | `admin` |
| `FW_MONGODB_DATABASE` | `afl` |

