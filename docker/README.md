# AgentFlow Docker Stack

This directory contains Docker configurations for running the AgentFlow development stack.

## Quick Start

```bash
# Bootstrap: install Docker (if needed), build, and start
scripts/setup

# Or start manually
docker compose up -d

# View the dashboard
open http://localhost:8080

# Seed example workflows
docker compose --profile seed run --rm seed

# View logs
docker compose logs -f
```

## Setup Script

The `scripts/setup` bootstrap script handles Docker installation, image building, and service scaling.

```bash
# Start with defaults (1 runner, 1 addone agent)
scripts/setup

# Scale runners and agents
scripts/setup --runners 3 --agents 2

# Include OSM geocoder agents (full image with Java/GraphHopper)
scripts/setup --osm-agents 2

# Include lightweight OSM agents (no Java/GraphHopper)
scripts/setup --osm-lite-agents 2

# Force rebuild before starting
scripts/setup --build --runners 3

# Just verify Docker is installed
scripts/setup --check-only
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--runners N` | 1 | Number of runner service instances |
| `--agents N` | 1 | Number of AddOne agent instances |
| `--osm-agents N` | 0 | Number of OSM Geocoder agents (full image) |
| `--osm-lite-agents N` | 0 | Number of OSM Geocoder lite agents |
| `--build` | - | Force rebuild of all images before starting |
| `--check-only` | - | Verify Docker is available, then exit |

## Services

### Core Services (always started)

| Service | Port | Scalable | Description |
|---------|------|----------|-------------|
| `mongodb` | 27018 | No | MongoDB database (27018 externally to avoid conflicts) |
| `dashboard` | 8080 | No | Web dashboard for monitoring workflows |
| `runner` | - | Yes | Distributed runner service |
| `agent-addone` | - | Yes | Sample agent handling AddOne/Multiply/Greet events |

### OSM Agents (started when count > 0)

| Service | Scalable | Description |
|---------|----------|-------------|
| `agent-osm-geocoder` | Yes | Full OSM agent with Java, GraphHopper, pyosmium, shapely, folium |
| `agent-osm-geocoder-lite` | Yes | Lightweight OSM agent (download, cache, region resolution only) |

### Optional Services (profiles)

#### Seed Profile
Populates the database with example workflows:

```bash
docker compose --profile seed run --rm seed
```

#### MCP Profile
Runs the MCP (Model Context Protocol) server for LLM agent integration:

```bash
# MCP uses stdio transport, run interactively
docker compose --profile mcp run --rm mcp
```

## Scaling

Runner and agent services can be horizontally scaled. Each instance connects to the same MongoDB and coordinates via atomic task claiming (`claim_task()`).

```bash
# Scale using docker compose directly
docker compose up -d --scale runner=3 --scale agent-addone=2

# Or use the setup script
scripts/setup --runners 3 --agents 2 --osm-agents 1
```

Verify scaling:

```bash
docker compose ps runner         # Should list 3 instances
docker compose ps agent-addone   # Should list 2 instances
```

## OSM Agent Images

Two Dockerfile variants exist for the OSM Geocoder agent:

### Full Image (`Dockerfile.osm-geocoder`)
Includes everything needed for geographic data processing and routing:
- **Python**: pyosmium, shapely, pyproj, folium, requests
- **System**: build-essential, cmake, libboost, libgeos, libproj
- **Java**: default-jre-headless (for GraphHopper)
- **GraphHopper**: JAR downloaded at build time to `/opt/graphhopper/`

### Lite Image (`Dockerfile.osm-geocoder-lite`)
Minimal image for basic operations (download, cache, region resolution):
- **Python**: requests
- No Java, no C++ build tools, no geospatial libraries

Choose the full image when you need routing graph operations (BuildGraph, ComputePairwiseRoutes). Use the lite image for cache management and region resolution.

## Configuration

Environment variables can be set in a `.env` file:

```env
MONGODB_PORT=27018
AFL_MONGODB_DATABASE=afl
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Dashboard │     │   Runner    │     │   Agents    │
│   (8080)    │     │  (×N)       │     │  (×N each)  │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┴───────────────────┘
                           │
                    ┌──────┴──────┐
                    │   MongoDB   │
                    │   (27018)   │
                    └─────────────┘
```

## MCP Server Usage

The MCP (Model Context Protocol) server allows LLM agents to interact with AgentFlow.

### Starting the MCP Server

```bash
# Run interactively (stdio transport)
docker compose --profile mcp run --rm mcp
```

### Connecting from Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agentflow": {
      "command": "docker",
      "args": ["compose", "-f", "/path/to/agentflow/docker-compose.yml", "--profile", "mcp", "run", "--rm", "mcp"]
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `afl_compile` | Compile AFL source to JSON |
| `afl_validate` | Validate AFL source |
| `afl_execute_workflow` | Execute a workflow |
| `afl_continue_step` | Continue a paused step |
| `afl_resume_workflow` | Resume a paused workflow |
| `afl_manage_runner` | Cancel/pause/resume runners |

### Available MCP Resources

| URI Pattern | Description |
|-------------|-------------|
| `afl://runners` | List all runners |
| `afl://runners/{id}` | Get runner details |
| `afl://runners/{id}/steps` | Get runner steps |
| `afl://runners/{id}/logs` | Get runner logs |
| `afl://steps/{id}` | Get step details |
| `afl://flows` | List all flows |
| `afl://flows/{id}` | Get flow details |
| `afl://flows/{id}/source` | Get flow AFL source |
| `afl://servers` | List all servers |
| `afl://tasks` | List all tasks |

## Troubleshooting

### MongoDB Connection Issues

If services can't connect to MongoDB:

```bash
# Check MongoDB is healthy
docker compose ps

# View MongoDB logs
docker compose logs mongodb
```

### Rebuilding Images

After code changes:

```bash
docker compose build --no-cache
docker compose up -d
```

### Clearing Data

```bash
# Stop and remove containers and volumes
docker compose down -v
```

### Verifying OSM Dependencies

```bash
# Check Python packages in full OSM image
docker compose exec agent-osm-geocoder python -c "import osmium; print('osmium OK')"
docker compose exec agent-osm-geocoder python -c "import shapely; print('shapely OK')"
docker compose exec agent-osm-geocoder python -c "import folium; print('folium OK')"

# Check Java
docker compose exec agent-osm-geocoder java -version

# Check GraphHopper JAR
docker compose exec agent-osm-geocoder ls -la /opt/graphhopper/graphhopper-web.jar
```
