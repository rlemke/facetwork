# AFL Agent Library for Go

The Go agent library provides an `AgentPoller` that connects to the Facetwork
runtime via MongoDB. It polls for pending event tasks, dispatches them to
registered handler functions, writes return values back to the step, and
inserts `afl:resume` tasks so the Python RunnerService can advance the
workflow.

## Dependencies

| Module | Version |
|--------|---------|
| `go.mongodb.org/mongo-driver` | v1.13.1 |
| `github.com/google/uuid` | v1.6.0 |

Requires Go 1.14 or later.

## Quick Start

```go
package main

import (
	"context"
	"log"

	aflagent "github.com/facetwork/afl-agent"
)

func main() {
	cfg := aflagent.FromEnvironment()
	poller := aflagent.NewAgentPoller(cfg)

	poller.Register("ns.MyFacet", func(params map[string]interface{}) (map[string]interface{}, error) {
		input := params["input"].(string)
		return map[string]interface{}{"result": input + " processed"}, nil
	})

	ctx := context.Background()
	if err := poller.Start(ctx); err != nil {
		log.Fatal(err)
	}
}
```

## Configuration

Configuration is resolved in the following order: explicit path, `AFL_CONFIG`
env var, `afl.config.json` in the working directory, `~/.afl/afl.config.json`,
`/etc/ffl/afl.config.json`, environment variables, then built-in defaults.

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `AFL_MONGODB_URL` | MongoDB connection string | `mongodb://localhost:27017` |
| `AFL_MONGODB_DATABASE` | MongoDB database name | `afl` |
| `AFL_CONFIG` | Path to `afl.config.json` | (none) |

The `afl.config.json` file format:

```json
{
  "mongodb": {
    "url": "mongodb://localhost:27017",
    "database": "afl"
  }
}
```

## Build & Test

```bash
go build ./...
go test ./...
```

## Protocol Reference

- [Agent Protocol Constants](../../protocol/README.md) -- collection names,
  state constants, document schemas, and MongoDB operation patterns.
- [Agent Template CLAUDE.md](../../templates/CLAUDE.md) -- full protocol
  context for building AFL agents from scratch in any language.
