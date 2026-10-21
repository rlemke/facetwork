# FFL Agent Library for Java

The Java agent library provides an `AgentPoller` that connects to the
Facetwork runtime via MongoDB. It polls for pending event tasks, dispatches
them to registered `Handler` implementations, writes return values back to the
step, and inserts `fw:resume` tasks so the Python RunnerService can advance
the workflow.

## Dependencies

| Library | Version |
|---------|---------|
| `org.mongodb:mongodb-driver-sync` | 4.11.0 |
| `com.fasterxml.jackson.core:jackson-databind` | 2.15.2 |
| `org.junit.jupiter:junit-jupiter` (test) | 5.10.0 |

Maven coordinates: `facetwork:fw-agent:0.1.0`. Requires Java 17 or later.

## Quick Start

```java
import afl.agent.AgentPoller;
import afl.agent.AgentPollerConfig;
import afl.agent.Handler;
import java.util.Map;

public class MyAgent {
    public static void main(String[] args) throws Exception {
        AgentPollerConfig config = AgentPollerConfig.fromEnvironment();
        AgentPoller poller = new AgentPoller(config);

        poller.register("ns.MyFacet", params -> {
            String input = (String) params.get("input");
            return Map.of("result", input + " processed");
        });

        poller.start();  // blocks until stop() is called
    }
}
```

The `Handler` functional interface:

```java
@FunctionalInterface
public interface Handler {
    Map<String, Object> handle(Map<String, Object> params) throws Exception;
}
```

To stop the poller gracefully:

```java
Runtime.getRuntime().addShutdownHook(new Thread(poller::stop));
```

## Retries, leases and cancellation (protocol v1.2)

The poller follows the full claim contract in
[`../../protocol/constants.json`](../../protocol/constants.json): it records this
server on the task, holds a lease and renews it every heartbeat, offers the
memory free on this host (so a task with a `requires(memory_gb=…)` floor waits
for a host that has it), and skips retries still cooling down.

A handler can use three more things from `params`:

| Key | Type | Use |
|---|---|---|
| `_task_heartbeat` | `BiConsumer<Integer, String>` | `(progressPct, message)` — renews the lease and records progress; call it from long loops |
| `_is_cancelled` | `Supplier<Boolean>` | true once the run was terminated, the task finished elsewhere, or it was reclaimed — return early |
| `_step_log` | `BiConsumer<String, String>` | `(message, level)` — a line on the dashboard step |

Throw `afl.agent.PermanentError` when retrying cannot help (bad input): the task
is dead-lettered at once and the step fails. Any other exception retries with
exponential backoff (5 s doubling, capped at 5 min) until `max_retries` (default
5), then dead-letters.

## Configuration

Configuration is resolved in the following order: explicit path, `FW_CONFIG`
env var, `afl.config.json` in the working directory, `~/.ffl/afl.config.json`,
`/etc/ffl/afl.config.json`, environment variables, then built-in defaults.

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `FW_MONGODB_URL` | MongoDB connection string | `mongodb://localhost:27017` |
| `FW_MONGODB_DATABASE` | MongoDB database name | `afl` |
| `FW_CONFIG` | Path to `afl.config.json` | (none) |
| `FW_CLAIM_PROXY_URL` | Claim through the host's claimer (long-poll `POST /next`); unreachable = claim directly, retried after 60 s. Set by the fleet-agent on claimer hosts | (none: claim directly) |

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
mvn compile
mvn test
mvn package
```

`ConformanceTest` runs the protocol's shared cases (`agents/protocol/conformance/`).
The retry cases need nothing; the claim and lifecycle cases need a MongoDB and
are skipped without one:

```bash
docker run -d --rm --name fw-agent-conformance -p 127.0.0.1:27999:27017 mongo:7
FW_AGENT_TEST_MONGO_URL=mongodb://127.0.0.1:27999 mvn test
docker stop fw-agent-conformance
```

## Protocol Reference

- [Agent Protocol Constants](../../protocol/README.md) -- collection names,
  state constants, document schemas, and MongoDB operation patterns.
- [Agent Template CLAUDE.md](../../templates/CLAUDE.md) -- full protocol
  context for building FFL agents from scratch in any language.
