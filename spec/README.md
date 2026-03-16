# AgentFlow Specification

This directory contains the authoritative specifications for the AgentFlow platform and the AFL (Agent Flow Language). These documents define the contract — all implementations must conform to these specifications.

## Reading Order

Start here and follow this sequence to understand the system from the ground up:

### 1. Language & Compiler
| Document | What You'll Learn |
|----------|-------------------|
| [10_language.md](10_language.md) | AFL syntax — lexical rules, EBNF grammar, all constructs |
| [11_semantics.md](11_semantics.md) | AST node types and what they mean |
| [12_validation.md](12_validation.md) | Semantic rules the compiler enforces |
| [20_compiler.md](20_compiler.md) | How AFL source becomes JSON workflow definitions |

### 2. Runtime & Execution
| Document | What You'll Learn |
|----------|-------------------|
| [30_runtime.md](30_runtime.md) | Execution model — iterations, determinism, idempotency |
| [31_runtime_impl.md](31_runtime_impl.md) | Python implementation — state changers, handlers, source file map |
| [51_state_system.md](51_state_system.md) | Step state machine — how steps transition through states |
| [40_database.md](40_database.md) | MongoDB schema — collections, indexes, atomic commits |

### 3. Agents & Events
| Document | What You'll Learn |
|----------|-------------------|
| [50_event_system.md](50_event_system.md) | Event lifecycle — dispatch, task queue, step locking |
| [60_agent_sdk.md](60_agent_sdk.md) | Building agents — protocol for external services |
| [61_llm_agent_integration.md](61_llm_agent_integration.md) | LLM agent patterns — prompts, tool use |

### 4. Reference
| Document | What You'll Learn |
|----------|-------------------|
| [00_overview.md](00_overview.md) | Terminology and implementation constraints |
| [70_examples.md](70_examples.md) | AFL code examples |
| [80_acceptance_tests.md](80_acceptance_tests.md) | Test requirements |
| [90_nonfunctional.md](90_nonfunctional.md) | Dependencies and non-functional requirements |

## Quick Reference

**If you want to...**

| Goal | Start With |
|------|------------|
| Write AFL workflows | [10_language.md](10_language.md) |
| Understand how execution works | [30_runtime.md](30_runtime.md) |
| Build an agent in any language | [60_agent_sdk.md](60_agent_sdk.md) |
| Understand the database schema | [40_database.md](40_database.md) |
| See the state machine | [51_state_system.md](51_state_system.md) |

## Terminology

- **AgentFlow**: The platform for distributed workflow execution
- **AFL**: Agent Flow Language — the DSL for defining workflows (`.afl` files)
- **Facet**: A typed attribute structure with parameters and optional returns
- **Event Facet**: A facet that triggers agent execution (external processing)
- **Workflow**: A facet designated as an entry point for execution
- **Step**: A runtime instance of a statement within a workflow
- **Agent**: An external service that processes event facet tasks
