# FFL Agent Protocol Constants

This directory contains shared protocol constants for building FFL agents in any language (Java, Scala, Go, Python, etc.).

## Purpose

The Python FFL runtime manages workflow execution, state machines, and dependency-driven step creation. External agents handle **event facets** — they perform the actual work (API calls, data processing, etc.) and signal the runtime to continue.

## External Agent Workflow (protocol v1.2)

An external agent processes an event step by:

1. **Claim the event task** — atomic `findOneAndUpdate` `pending` → `running`
   (`claim_task`). The filter is not just name + task list: it also skips tasks
   in their retry cooldown, tasks needing a script environment or FFL feature,
   and tasks whose `requires` floor exceeds what this host has free. The update
   records **`server_id`** and a **`lease_expires`**. At most every 30 s, also
   try `reclaim_expired_task` (a running task whose holder stopped renewing).
2. **Renew the lease** while the handler runs (`renew_task_lease`, gated on
   `server_id`), every heartbeat.
3. **Read step params** from `steps` using the task's `step_id`, and do the work.
4. **Check cancellation** (`check_cancellation`) before writing results: stop
   if the task was canceled, already terminal, or reclaimed by another server.
5. **Write return attributes** to the step document.
6. **Mark the task `completed`** — gated on `server_id` (`complete_task`).
7. **Insert an `fw:resume` task**, only if step 6 matched a document.

On a handler error, apply `fail_task`: back to `pending` with exponential
backoff while retry budget remains, otherwise `dead_letter` (a *permanent*
error dead-letters at once). ⚠️ Never write `failed` for a handler error: the
runtime's stuck-step sweep sees no active and no dead-lettered task and creates
a fresh one with `retry_count` 0, so the failure retries forever.

The server record carries measured `resources` and is heartbeated with
`free_resources`; when the heartbeat matches nothing (record pruned or marked
`shutdown` by a reaper false positive) the agent registers again.

The Python `RunnerService` polls for `fw:resume` tasks, calls `evaluator.continue_step()` to validate and transition the step, then calls `evaluator.resume()` to advance the workflow.

## Conformance cases

`conformance/claim_cases.json` (which tasks a server may claim) and
`conformance/retry_cases.json` (the failure transition) state the rules as
data. The Python runtime runs them in `tests/runtime/test_agent_protocol_conformance.py`;
every agent library runs the same files against its own implementation. Change
a rule in the runtime and that test fails until the cases — and so every
library — change with it.

## Key Insight

`request_transition` is not persisted to MongoDB. When a step is loaded from MongoDB, `StepTransition.initial()` sets `request_transition=True` by default. This means any step at `EVENT_TRANSMIT` loaded from MongoDB is automatically eligible for processing by `evaluator.resume()`. External agents just need to write return attributes and signal for resume.

## Example: MongoDB Operations

See `constants.json` for complete field schemas and example MongoDB operations:
- `claim_task` / `reclaim_expired_task` — atomic task claiming
- `renew_task_lease` / `check_cancellation` — while the handler runs
- `update_step_returns`, `complete_task`, `create_resume_task` — success
- `fail_task` — retry or dead-letter
- `register_server` / `heartbeat_server` — the server record

## Language Libraries

| Language | Location | Status |
|----------|----------|--------|
| Python | `facetwork/runtime/agent_poller.py` (built-in) | Full evaluator integration |
| Java | `agents/java/fw-agent/` | v1.2 |
| Scala | `agents/scala/fw-agent/` | v1.2 |
| Go | `agents/go/fw-agent/` | v1.2 |
| TypeScript | `agents/typescript/fw-agent/` | v1.2 |

Additional languages can implement the protocol using `constants.json` and any MongoDB driver.

## Starting a new agent in a separate repo

Copy the template files from `agents/templates/` into your project:

```bash
cp agents/templates/CLAUDE.md /path/to/my-agent/CLAUDE.md
cp agents/protocol/constants.json /path/to/my-agent/constants.json
```

The `CLAUDE.md` gives Claude full context on the protocol, schemas, and operations needed to build an FFL agent from scratch in any language. See `agents/templates/README.md` for details.

## File Reference

- `constants.json` — All collection names, state constants, document schemas, and MongoDB operation patterns
