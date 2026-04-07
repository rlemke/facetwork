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
afl input.afl -o output.json       # compile to JSON
echo 'facet Test()' | afl          # parse from stdin
afl input.afl --check              # syntax check only
afl input.afl --config config.json # custom config
```

### Services
```bash
python -m afl.dashboard                              # dashboard (port 8080)
python -m afl.dashboard --port 9000 --reload         # dev mode
python -m afl.runtime.runner                         # runner service
python -m afl.runtime.runner --topics TopicA TopicB  # filtered topics
python -m afl.runtime.runner --max-concurrent 10     # increase concurrency
python -m afl.mcp                                    # MCP server (stdio)
```

### Scala agent library
```bash
cd agents/scala/afl-agent && sbt compile  # compile
cd agents/scala/afl-agent && sbt test     # run tests
cd agents/scala/afl-agent && sbt package  # package JAR
```

### Convenience scripts
All scripts are in `scripts/` and are self-contained:
```bash
scripts/_env.sh                                # shared env loader (sourced by other scripts)
scripts/_remote.sh                             # shared SSH/MongoDB helpers for remote management
scripts/easy.sh                                # one-command pipeline (teardown → rebuild → setup → seed)
scripts/setup                                  # bootstrap Docker stack
scripts/setup --runners 3 --agents 2           # start with scaling
scripts/compile input.afl -o output.json       # compile AFL
scripts/publish input.afl                      # compile + publish to MongoDB
scripts/publish input.afl --auto-resolve       # with dependency resolution
scripts/run-workflow                           # interactive workflow execution
scripts/run-workflow --workflow Name            # run specific workflow
scripts/server --workflow MyWorkflow           # execute workflow (server mode)
scripts/runner                                 # start runner
scripts/dashboard                              # start dashboard
scripts/mcp-server                             # start MCP server
scripts/db-stats                               # show DB statistics
scripts/start-runner                           # register handlers + start runner locally
scripts/start-runner --all                     # start runners on all remote hosts
scripts/stop-runners                           # stop local runners
scripts/stop-runners --all                     # stop runners on all remote hosts
scripts/rolling-deploy                         # zero-downtime rolling restart
scripts/list-runners                           # tree view: servers → runners → handlers
scripts/list-runners --state running           # filter by state
scripts/list-runners --json                    # machine-readable output
```

### Docker stack
The `docker-compose.yml` defines the full development stack:
```bash
scripts/setup                                               # bootstrap
scripts/setup --runners 3 --agents 2 --osm-agents 1        # with scaling
docker compose up -d                                        # start directly
docker compose --profile seed run --rm seed                 # seed workflows
docker compose --profile mcp run --rm mcp                   # MCP server
docker compose --profile hdfs up -d                         # start HDFS
scripts/setup --hdfs                                        # bootstrap with HDFS
docker compose down                                         # stop
docker compose down -v                                      # stop + remove volumes
```

#### Services

| Service | Port | Scalable | Description |
|---------|------|----------|-------------|
| `dashboard` | 8080 | No | Web dashboard |
| `runner` | - | Yes | Distributed runner service |
| `agent-addone` | - | Yes | Sample AddOne agent |
| `agent-osm-geocoder` | - | Yes | Full OSM agent (osmium, Java, GraphHopper) |
| `agent-osm-geocoder-lite` | - | Yes | Lightweight OSM agent (requests only) |
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

### macOS Docker Desktop: Volume Mounts and Network Storage

Docker Desktop for Mac uses VirtioFS to share host directories with containers. This introduces filesystem semantics differences when mounting network-attached storage (NAS) volumes.

#### SMB mounts (macOS → NAS)

SMB (Samba/CIFS) volumes mounted on macOS (e.g. `/Volumes/afl_data/`) exhibit a specific bug when bind-mounted into Docker containers via VirtioFS:

- **Writes work correctly**: Files created by the container are tracked by VirtioFS and fully accessible (open, stat, read).
- **Pre-existing files in subdirectories fail**: `os.path.isfile()`, `os.stat()`, and `open()` return errors for files that existed on the SMB share before the container started. `os.listdir()` (readdir) succeeds — the filenames are visible, but `stat()` on individual files fails.
- **Root-level files work**: Only files in subdirectories are affected.

**Impact on AgentFlow**: The Geofabrik mirror (`AFL_GEOFABRIK_MIRROR`) contains pre-existing `.osm.pbf` files in nested directories (e.g. `north-america/us/alabama-latest.osm.pbf`). When mounted from an SMB share, containers cannot read these files even though `listdir()` shows them.

**Workarounds**:
1. **Use a local APFS drive for the mirror** (recommended): Set `AFL_GEOFABRIK_MIRROR` to a local or directly-attached drive (e.g. `/Volumes/afl_data_local/osm`). SMB is fine for write targets (`AFL_CACHE_DIR`, `AFL_OSM_OUTPUT_BASE`, `AFL_LOCAL_OUTPUT_DIR`) since containers create those files.
2. **NFS export from the NAS**: NFS does not have this VirtioFS bug. If your NAS supports NFS, export the data directory and mount via NFS on macOS.
3. **readdir fallback**: The downloader (`examples/osm-geocoder/handlers/shared/downloader.py`) includes `_mirror_file_exists()` which falls back to `os.listdir()` when `os.path.isfile()` fails. This detects file presence but cannot fix the `open()` failure for actual reads.

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
AFL_GEOFABRIK_MIRROR=/Volumes/afl_data_local/osm

# Write targets on NAS (SMB is fine for container-created files)
AFL_CACHE_DIR=/Volumes/afl_data/osm-cache
AFL_OSM_OUTPUT_BASE=/Volumes/afl_data/osm-output
AFL_LOCAL_OUTPUT_DIR=/Volumes/afl_data/output

# MongoDB on Docker volume (never SMB)
# MONGODB_DATA_DIR=   (leave commented out)
```

### Environment Configuration

The `.env` file is the primary way to configure the Docker stack and convenience scripts.

**Setup:**
```bash
cp .env.example .env   # one-time copy
# Edit .env to set MongoDB port, scaling, overlays, data directories
scripts/easy.sh        # runs the full pipeline using .env values
```

**How it works:**
- `scripts/_env.sh` is sourced by every convenience script. It reads `.env` from the project root and exports each variable **only if it is not already set** in the environment.
- `scripts/easy.sh` translates `.env` variables into `scripts/setup` CLI flags and runs the full pipeline (teardown → rebuild → setup → seed).
- Precedence: **CLI flags > env vars > `.env` > defaults**

**Variable reference:**

| Variable | Default | Description |
|----------|---------|-------------|
| **MongoDB** | | |
| `AFL_MONGODB_URL` | `mongodb://afl-mongodb:27017` | MongoDB connection URL (external server, defined in `/etc/hosts`) |
| `AFL_MONGODB_DATABASE` | `afl` | Database name (runtime: steps, tasks, runners, flows) |
| `AFL_EXAMPLES_DATABASE` | `afl_examples` | Database for example handler data (weather reports, census output) |
| **Scaling** | | |
| `AFL_RUNNERS` | `1` | Number of runner service instances |
| `AFL_AGENTS` | `1` | Number of AddOne agent instances |
| `AFL_OSM_AGENTS` | `0` | Full OSM Geocoder agent instances |
| `AFL_OSM_LITE_AGENTS` | `0` | Lightweight OSM agent instances |
| **Overlays** | | |
| `AFL_HDFS` | `false` | Enable HDFS overlay compose file and profile |
| `AFL_POSTGIS` | `false` | Enable PostGIS overlay compose file and profile |
| `AFL_JENKINS` | `false` | Enable Jenkins profile |
| `AFL_GEOFABRIK_MIRROR` | `/Volumes/afl_data/osm` | Path to local Geofabrik mirror; mounted read-only at `/data/osm-mirror` in containers |
| **OSM data paths** | | |
| `AFL_CACHE_DIR` | `/tmp/osm-cache` | OSM cache directory (local path or HDFS URI) |
| `AFL_OSM_OUTPUT_BASE` | `/tmp` | OSM extractor output base (local path or HDFS URI) |
| `AFL_LOCAL_OUTPUT_DIR` | `/Volumes/afl_data/output` | Handler output files (reports, maps, stats, GeoJSON). Used by all examples: osm-geocoder, census-us, hiv-drug-resistance, monte-carlo-risk, maven. Falls back to `/tmp` when unset. |
| `AFL_LOCALIZE_MOUNTS` | *(empty)* | Comma-separated path prefixes for Docker mount paths that `localize()` should copy to container-local storage before processing. Avoids VirtioFS hangs on large files. Example: `/data/osm-mirror` |
| **Remote runner management** | | |
| `AFL_RUNNER_HOSTS` | *(empty)* | Space-separated hostnames for remote runner management |
| `AFL_REMOTE_PATH` | *(same as local)* | Repo path on remote hosts |
| `AFL_SSH_OPTS` | *(empty)* | Extra SSH options (e.g. `-i ~/.ssh/deploy_key`) |
| **Runner tuning** | | |
| `AFL_MAX_CONCURRENT` | `2` | Max concurrent work items per runner |
| `AFL_POLL_INTERVAL_MS` | `1000` | Runner poll interval in milliseconds |
| **LLM / Claude API** | | |
| `ANTHROPIC_API_KEY` | *(empty)* | Anthropic API key for Claude-powered prompt blocks. When unset, LLM handlers fall back to deterministic stubs. Required by: `ClaudeAgentRunner`, `LLMHandler`, and example handlers like `noaa-weather` GenerateNarrative. |
| **Data directories** | | |
| `GRAPHHOPPER_DATA_DIR` | *(Docker volume)* | Host path for GraphHopper data |
| `POSTGIS_DATA_DIR` | *(Docker volume)* | Host path for PostGIS data |
| `JENKINS_HOME_DIR` | *(Docker volume)* | Host path for Jenkins home |

### Configuration

AFL uses a JSON config file (`afl.config.json`) for service connections. Resolution order:

1. Explicit `--config FILE` CLI argument
2. `AFL_CONFIG` environment variable
3. `afl.config.json` in the current directory, `~/.afl/`, or `/etc/afl/`
4. Environment variables (`AFL_MONGODB_*`)
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
| `AFL_MONGODB_URL` | `mongodb://localhost:27017` |
| `AFL_MONGODB_USERNAME` | (empty) |
| `AFL_MONGODB_PASSWORD` | (empty) |
| `AFL_MONGODB_AUTH_SOURCE` | `admin` |
| `AFL_MONGODB_DATABASE` | `afl` |

