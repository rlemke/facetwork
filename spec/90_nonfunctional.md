## Non-functional Requirements (90_nonfunctional.md)

---

## Dependencies

### Runtime Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| Python | ≥3.11 | Runtime |
| lark | ≥1.1.0 | Parser generator |

### Optional Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| pymongo | ≥4.0 | MongoDB connectivity |
| pyarrow | ≥14.0 | HDFS storage backend |

### Development Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| pytest | ≥7.0 | Test framework |
| pytest-cov | ≥4.0 | Coverage reporting |

### Forbidden Dependencies
No other parsing, compiler, or DSL libraries are permitted in v1:
- ❌ ANTLR
- ❌ PLY
- ❌ Parsimonious
- ❌ pyparsing
- ❌ regex-based parsers
- ❌ handwritten parsers

---

## Performance

### Parser Performance
- Grammar uses LALR mode (linear time parsing)
- No backtracking required
- Single-pass parsing

### Memory
- AST nodes use dataclasses (memory efficient)
- No caching of intermediate results
- Parse tree discarded after transformation

---

## Compatibility

### Python Version
- Minimum: Python 3.11
- Tested: Python 3.14
- Uses: dataclasses, type hints, `kw_only` parameter

### Platform
- OS-independent (pure Python)
- No native extensions
- No system dependencies

---

## Code Quality

### Style
- Type hints on all public functions
- Docstrings on all public classes and functions
- No global mutable state

### Testing
- 3065 tests collected (2981 passed, 84 skipped) as of v0.18.0
- Tests for all grammar constructs
- Tests for error reporting
- MongoDB store tests using mongomock (no real database required)

### Documentation
- README with usage examples
- Spec files for language definition
- CLAUDE.md for development guidance

---

## Security

### Input Handling
- All input treated as untrusted
- No eval() or exec() usage
- No file system access beyond reading input

### Error Messages
- No sensitive data in error messages
- Line/column info only (no source excerpts in errors)

---

## Versioning

### Current Version
- `0.1.0` (initial implementation)

### Semantic Versioning
- MAJOR: Breaking changes to AST structure or JSON format
- MINOR: New language features, new AST nodes
- PATCH: Bug fixes, performance improvements

### JSON Format Stability
- JSON output format is considered stable within MAJOR version
- `type` field present on all nodes
- Location fields optional (controlled by flag)
- As of v0.12.52, the emitter produces **declarations-only** format (no categorized `namespaces`/`facets`/`eventFacets`/`workflows`/`implicits`/`schemas` keys)
- `normalize_program_ast()` in `afl/ast_utils.py` handles backward compatibility for legacy JSON that uses categorized keys

---

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
| `mongodb` | 27018 | No | MongoDB 7 database |
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
| `MONGODB_PORT` | `27018` | Host port for MongoDB container |
| `AFL_MONGODB_URL` | `mongodb://localhost:27018` | MongoDB connection URL |
| `AFL_MONGODB_DATABASE` | `afl` | Database name (runtime: steps, tasks, runners, flows) |
| `AFL_EXAMPLES_DATABASE` | `afl_examples` | Database for example handler data (weather reports, census output) |
| `MONGODB_DATA_DIR` | *(Docker volume)* | Host path for MongoDB data |
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
| `HDFS_NAMENODE_DIR` | *(Docker volume)* | Host path for HDFS NameNode data |
| `HDFS_DATANODE_DIR` | *(Docker volume)* | Host path for HDFS DataNode data |
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

---

## Deployment Operations

AgentFlow runners can be managed locally (single machine) or remotely (multi-host production). All scripts support both modes — local is the default and remote is activated with `--all` or `--host`.

### Prerequisites for remote management

1. **SSH access**: current user must be able to `ssh <hostname>` to every runner host without a password prompt (SSH agent or key-based auth)
2. **Same repo layout**: the AgentFlow repo must be checked out on every remote host at the same path (or set `AFL_REMOTE_PATH`)
3. **MongoDB reachable**: every runner host must be able to reach the MongoDB instance specified by `AFL_MONGODB_URL`
4. **Host inventory**: configure `AFL_RUNNER_HOSTS` in `.env` or pass `--host` flags

```bash
# .env
AFL_RUNNER_HOSTS=prod-runner-01 prod-runner-02 prod-runner-03
AFL_REMOTE_PATH=/opt/agentflow    # optional, defaults to local repo root
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
store = MongoStore('mongodb://localhost:27018')
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
