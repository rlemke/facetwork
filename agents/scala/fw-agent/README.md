# FFL Agent Library for Scala 3

The Scala agent library provides an `AgentPoller` that connects to the
Facetwork runtime via MongoDB. It polls for pending event tasks, dispatches
them to registered handler functions, writes return values back to the step,
and inserts `fw:resume` tasks so the Python RunnerService can advance the
workflow.

## Dependencies

| Library | Version |
|---------|---------|
| `org.mongodb.scala:mongo-scala-driver` | 5.3.1 (cross-compiled for 2.13) |
| `ch.qos.logback:logback-classic` | 1.5.18 |
| `org.scalatest:scalatest` (test) | 3.2.19 |

Requires Scala 3.3.4 and JDK 17 or later.

## Quick Start

```scala
import afl.agent.{AgentPoller, AgentPollerConfig}

@main def run(): Unit =
  val config = AgentPollerConfig.fromEnvironment()
  val poller = AgentPoller(config)

  poller.register("ns.MyFacet") { params =>
    val input = params("input").toString
    Map("result" -> (input + " processed"))
  }

  poller.start()  // blocks until stop() is called
```

To stop the poller gracefully:

```scala
sys.addShutdownHook {
  poller.stop()
}
```

## Configuration

Configuration is resolved in the following order: explicit path, `AFL_CONFIG`
env var, `afl.config.json` in the working directory, `~/.ffl/afl.config.json`,
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
sbt compile
sbt test
sbt package
```

## Protocol Reference

- [Agent Protocol Constants](../../protocol/README.md) -- collection names,
  state constants, document schemas, and MongoDB operation patterns.
- [Agent Template CLAUDE.md](../../templates/CLAUDE.md) -- full protocol
  context for building FFL agents from scratch in any language.
