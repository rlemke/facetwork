# AFL Agent Library for Java

The Java agent library provides an `AgentPoller` that connects to the
Facetwork runtime via MongoDB. It polls for pending event tasks, dispatches
them to registered `Handler` implementations, writes return values back to the
step, and inserts `afl:resume` tasks so the Python RunnerService can advance
the workflow.

## Dependencies

| Library | Version |
|---------|---------|
| `org.mongodb:mongodb-driver-sync` | 4.11.0 |
| `com.fasterxml.jackson.core:jackson-databind` | 2.15.2 |
| `org.junit.jupiter:junit-jupiter` (test) | 5.10.0 |

Maven coordinates: `afl:afl-agent:0.1.0`. Requires Java 17 or later.

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
mvn compile
mvn test
mvn package
```

## Protocol Reference

- [Agent Protocol Constants](../../protocol/README.md) -- collection names,
  state constants, document schemas, and MongoDB operation patterns.
- [Agent Template CLAUDE.md](../../templates/CLAUDE.md) -- full protocol
  context for building AFL agents from scratch in any language.
