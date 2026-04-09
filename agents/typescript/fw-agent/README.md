# FFL Agent Library for TypeScript/Node.js

The `@afl/agent` package provides an `AgentPoller` that connects to the
Facetwork runtime via MongoDB. It polls for pending event tasks, dispatches
them to registered async handler functions, writes return values back to the
step, and inserts `fw:resume` tasks so the Python RunnerService can advance
the workflow.

## Dependencies

| Package | Version |
|---------|---------|
| `mongodb` | ^6.3.0 |
| `uuid` | ^9.0.0 |
| `typescript` (dev) | ^5.3.0 |

Requires Node.js 18 or later.

## Quick Start

```typescript
import { AgentPoller, resolveConfig, Handler } from "@afl/agent";

const config = resolveConfig();
const poller = new AgentPoller(config);

const myHandler: Handler = async (params) => {
  const input = params.input as string;
  return { result: input + " processed" };
};

poller.register("ns.MyFacet", myHandler);
await poller.start();
```

To stop the poller gracefully:

```typescript
process.on("SIGINT", async () => {
  await poller.stop();
  process.exit(0);
});
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
npm install
npm run build
npm test
npm run lint
```

## Protocol Reference

- [Agent Protocol Constants](../../protocol/README.md) -- collection names,
  state constants, document schemas, and MongoDB operation patterns.
- [Agent Template CLAUDE.md](../../templates/CLAUDE.md) -- full protocol
  context for building FFL agents from scratch in any language.
