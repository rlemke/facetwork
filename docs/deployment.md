# AgentFlow Deployment & Operations Guide

This guide covers deploying, configuring, monitoring, and operating AgentFlow in development and production environments.

## Quick Start

The fastest way to run AgentFlow is with Docker Compose:

```bash
# Clone the repository
git clone <repo-url>
cd agentflow

# Start the stack (MongoDB, dashboard, runner, sample agent)
docker compose up -d

# Open the dashboard
open http://localhost:8080

# Seed example workflows (optional)
docker compose --profile seed run --rm seed
```

Or use the setup script for a guided bootstrap:

```bash
scripts/setup                              # defaults: 1 runner, 1 agent
scripts/setup --runners 3 --agents 2       # scaled deployment
scripts/setup --build                      # rebuild images first
```

## Architecture

### Single-Node (Development)

All services run on one machine via Docker Compose:

```
                 +-----------+
  Browser ------>| Dashboard |
                 |  (8080)   |
                 +-----+-----+
                       |
    +--------+---------+---------+--------+
    |        |                   |        |
+---v--+ +---v---+          +---v---+ +--v---+
|Runner| |Runner |   ...    | Agent | |Agent |
+---+--+ +---+---+          +---+---+ +--+---+
    |         |                  |        |
    +---------+------------------+--------+
                       |
                +------v------+
                |   MongoDB   |
                |   (27018)   |
                +-----------  +
```

### Multi-Node (Production)

For production, run MongoDB on dedicated infrastructure and distribute services across nodes:

- **MongoDB**: Dedicated server or managed service (MongoDB Atlas)
- **Dashboard**: Single instance behind a reverse proxy
- **Runners**: Multiple instances on worker nodes
- **Agents**: Multiple instances, scaled per workload

All services connect to the same MongoDB instance and coordinate via atomic task claiming (`claim_task()`).

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

The config file is searched in order: `$AFL_CONFIG`, `./afl.config.json`, `~/.afl/afl.config.json`, `/etc/afl/afl.config.json`.

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
| `--config` | | Path to AFL config file |
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
| `--config` | | Path to AFL config file |
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
| Sources | `/sources` | Published AFL source namespaces |
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

## HDFS Integration

AgentFlow supports HDFS as a storage backend for OSM handler caches. When enabled, OSM agents read and write cache data (PBF files, GraphHopper graphs, GTFS feeds) to HDFS instead of local disk.

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
| `AFL_CACHE_DIR` | `hdfs://namenode:8020/osm-cache` | OSM PBF download cache |
| `GRAPHHOPPER_GRAPH_DIR` | `hdfs://namenode:8020/graphhopper` | GraphHopper routing graphs |
| `AFL_GTFS_CACHE_DIR` | `hdfs://namenode:8020/gtfs-cache` | GTFS feed cache |

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

## Jenkins CI/CD

AgentFlow includes an optional Jenkins service for CI/CD pipelines. Jenkins runs with Docker socket access, allowing it to build and test AgentFlow Docker images.

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

AgentFlow supports PostGIS as a spatial database for OSM geocoder agents. The OSM geocoder defines a `PostGisImport` event facet for importing geospatial data into PostGIS.

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

Use the `docker-compose.postgis.yml` override file to build OSM agent images with `psycopg2-binary` (required for PostGIS):

```bash
docker compose -f docker-compose.yml -f docker-compose.postgis.yml --profile postgis build
```

Or use the setup script:

```bash
scripts/setup --postgis --osm-agents 2 --build
```

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

By default, the OSM Geocoder agent uses a Docker named volume (`graphhopper_data`) for GraphHopper routing graphs. To place GraphHopper data on an external filesystem, set the `GRAPHHOPPER_DATA_DIR` environment variable:

```bash
# Use an external directory for GraphHopper data
export GRAPHHOPPER_DATA_DIR=/mnt/ssd/graphhopper
docker compose up -d agent-osm-geocoder

# Or via the setup script
scripts/setup --osm-agents 2 --graphhopper-data-dir /mnt/ssd/graphhopper
```

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
mongodump --uri="mongodb://localhost:27018" --db=afl --out=/backup/

# Restore
mongorestore --uri="mongodb://localhost:27018" --db=afl /backup/afl/
```

### Key Collections

| Collection | Content | Backup Priority |
|------------|---------|-----------------|
| `flows` | Compiled workflow definitions | High |
| `sources` | Published AFL source code | High |
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
