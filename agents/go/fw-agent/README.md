# FFL Agent Library for Go

The Go agent library provides an `AgentPoller` that connects to the Facetwork
runtime via MongoDB. It polls for pending event tasks, dispatches them to
registered handler functions, writes return values back to the step, and
inserts `fw:resume` tasks so the Python RunnerService can advance the
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

	aflagent "github.com/facetwork/fw-agent"
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

## Retries, leases and cancellation (protocol v1.2)

The poller follows the full claim contract in
[`../../protocol/constants.json`](../../protocol/constants.json): it records this
server on the task, holds a lease and renews it every heartbeat, offers the
memory free on this host (so a task with a `requires(memory_gb=…)` floor waits
for a host that has it), and skips retries still cooling down. Completion is
gated on still holding the task, and the `fw:resume` task is inserted only
when it succeeds.

A handler can use these from `params`:

| Key | Type | Use |
|---|---|---|
| `_task_heartbeat` | `func(progressPct int, message string)` | renews the lease and records progress (`progressPct < 0` = none); call it from long loops |
| `_is_cancelled` | `func() bool` | true once the run was terminated, the task finished elsewhere, or it was reclaimed — return early |
| `_step_log` | `func(message, level string)` | a line on the dashboard step |

Return `fwagent.NewPermanentError(msg)` (or wrap one in a `*PermanentError`)
when retrying cannot help: the task is dead-lettered at once and the step
fails. Any other error retries with exponential backoff (5 s doubling, capped
at 5 min) until `max_retries` (default 5), then dead-letters. The library never
writes `failed`, which the runtime would recreate at retry 0 forever.

`ClaimTask`, `MarkTaskCompleted`, `MarkTaskFailed` and `Heartbeat` remain for
source compatibility but are deprecated; the poller uses `ClaimTaskFor`,
`CompleteTask`, `FailTask` and `Ping`.

## Configuration

Configuration is resolved in the following order: explicit path, `FW_CONFIG`
env var, `afl.config.json` in the working directory, `~/.ffl/afl.config.json`,
`/etc/ffl/afl.config.json`, environment variables, then built-in defaults.

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `FW_MONGODB_URL` | MongoDB connection string | `mongodb://localhost:27017` |
| `FW_MONGODB_DATABASE` | MongoDB database name | `afl` |
| `FW_CONFIG` | Path to `afl.config.json` | (none) |

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

`conformance_test.go` runs the protocol's shared cases
(`agents/protocol/conformance/`). The retry cases need nothing; the claim and
lifecycle cases need a MongoDB and are skipped without one:

```bash
docker run -d --rm --name fw-agent-conformance-go -p 127.0.0.1:28001:27017 mongo:7
FW_AGENT_TEST_MONGO_URL=mongodb://127.0.0.1:28001 go test ./...
docker stop fw-agent-conformance-go
```

Use Go 1.21 or newer (CI uses 1.21): older toolchains do not read the
`//go:build` lines in the driver's dependencies.

## Protocol Reference

- [Agent Protocol Constants](../../protocol/README.md) -- collection names,
  state constants, document schemas, and MongoDB operation patterns.
- [Agent Template CLAUDE.md](../../templates/CLAUDE.md) -- full protocol
  context for building FFL agents from scratch in any language.
