# FFL Agent Protocol Constants

This directory contains shared protocol constants for building FFL agents in any language (Java, Scala, Go, Python, etc.).

## Purpose

The Python FFL runtime manages workflow execution, state machines, and dependency-driven step creation. External agents handle **event facets** — they perform the actual work (API calls, data processing, etc.) and signal the runtime to continue.

## External Agent Workflow

An external agent processes an event step by:

1. **Claim the event task** from the `tasks` collection (atomic `findOneAndUpdate` from `pending` → `running`)
2. **Read step params** from `steps` collection using `step_id` from the task
3. **Perform the work** (call APIs, process data, etc.)
4. **Write return attributes** to the step document in the `steps` collection
5. **Mark the event task as `completed`** in the `tasks` collection
6. **Insert an `fw:resume` task** into the `tasks` collection

The Python `RunnerService` polls for `fw:resume` tasks, calls `evaluator.continue_step()` to validate and transition the step, then calls `evaluator.resume()` to advance the workflow.

## Key Insight

`request_transition` is not persisted to MongoDB. When a step is loaded from MongoDB, `StepTransition.initial()` sets `request_transition=True` by default. This means any step at `EVENT_TRANSMIT` loaded from MongoDB is automatically eligible for processing by `evaluator.resume()`. External agents just need to write return attributes and signal for resume.

## Example: MongoDB Operations

See `constants.json` for complete field schemas and example MongoDB operations:
- `claim_task` — atomic task claiming
- `update_step_returns` — writing return values to a step
- `create_resume_task` — inserting the resume signal

## Language Libraries

| Language | Location | Status |
|----------|----------|--------|
| Python | `afl/runtime/agent_poller.py` (built-in) | Full evaluator integration |
| Scala | `agents/scala/fw-agent/` | Lightweight — delegates resume to Python RunnerService via `fw:resume` |

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
