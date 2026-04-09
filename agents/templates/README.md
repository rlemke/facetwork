# FFL Agent Templates

This directory contains standalone files that can be copied into a separate repository to bootstrap a new FFL agent project. They give Claude (or any developer) all the context needed to build an agent that integrates with the Facetwork platform.

## Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Drop into your repo root. Gives Claude full context on the FFL agent protocol, MongoDB schemas, operations, configuration, and existing libraries. |
| `constants.json` | Copy of the protocol constants. Machine-readable reference for collection names, state values, document schemas, and MongoDB operation patterns. |

## Usage

```bash
# From the facetwork repo
cp agents/templates/CLAUDE.md /path/to/my-agent/CLAUDE.md
cp agents/protocol/constants.json /path/to/my-agent/constants.json
```

Then start Claude Code in your agent's directory — it will pick up `CLAUDE.md` automatically and understand how to build an FFL agent.

## What your agent needs

At minimum, an FFL agent needs:

1. **A MongoDB driver** for your language
2. **A poll loop** that claims tasks and dispatches to handlers
3. **Handlers** for each event facet name your agent supports
4. **An `afl.config.json`** (or environment variables) for the MongoDB connection

The `CLAUDE.md` file documents the complete protocol so Claude can help you implement all of this from scratch in any language.

## Optional: Use an existing library

If your agent is in Python or Scala, you can use the pre-built libraries instead of implementing the protocol from scratch:

- **Python**: `afl.runtime.agent_poller.AgentPoller` (built into the Facetwork runtime)
- **Scala**: `agents/scala/fw-agent/` (standalone sbt library)

The `CLAUDE.md` includes usage examples for both.
