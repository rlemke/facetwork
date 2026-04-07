# 61 — LLM Agent Integration

This document analyzes what changes are required to make AgentFlow a platform
for building AI agents backed by large language models, what kinds of prompts
and tasks the resulting system could handle, and how the platform accelerates
agent development compared to building from scratch.

---

## 1. Current Foundation

AgentFlow already provides the core primitives for AI agent orchestration:

| Primitive | Purpose |
|-----------|---------|
| **Event facets** | Declare an external capability with typed inputs and outputs |
| **Task queue** | Atomic claiming ensures exactly one agent handles each event |
| **Pause/resume** | Workflows block at event steps and resume when an agent responds |
| **MCP server** | Exposes 6 tools so an LLM can compile, execute, continue, and manage workflows |
| **AgentPoller** | Lightweight library to build agent services that poll for work |

The AddOne agent test (`tests/runtime/test_addone_agent.py`) demonstrates the
full loop: workflow executes, pauses at event facet, agent claims task, agent
returns result, workflow resumes.

---

## 2. Required Changes

### 2.1 LLM Dispatch in Event Handlers

Currently event handlers are plain Python functions. An AI agent needs an LLM
call in the handler:

```python
# Today: deterministic handler
poller.register("SummarizeDoc", lambda p: {"summary": p["text"][:100]})

# Needed: LLM-backed handler
async def summarize(payload):
    response = await claude.messages.create(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": f"Summarize: {payload['text']}"}]
    )
    return {"summary": response.content[0].text}

poller.register("SummarizeDoc", summarize)
```

**Changes required:**

- `AgentPoller` needs async callback support (currently synchronous
  `ThreadPoolExecutor`).
- A built-in `LLMHandler` base class that wraps prompt construction, API
  calls, and response parsing.
- Configurable model/provider (Claude, OpenAI, local models).
- Token budget and retry logic.

### 2.2 Prompt Templates Bound to Event Facets

Event facets define typed parameters but have no way to specify *how* those
parameters become a prompt:

```afl
// Current: just types
event facet SummarizeDoc(text: String, max_length: Int) => (summary: String)

// Needed: prompt template as part of the facet definition
event facet SummarizeDoc(text: String, max_length: Int) => (summary: String)
    prompt """
        Summarize the following document in at most {max_length} words:
        {text}
    """
```

**Changes required:**

- Grammar extension: `prompt` block on event facets (`afl/grammar/afl.lark`).
- New AST node: `PromptTemplate` with interpolation slots.
- Transformer and emitter support for the new node.
- Runtime evaluation of template expressions against step params.

### 2.3 Tool Use and Multi-Turn Agent Loops

A single LLM call is often not enough. Agents need to call tools, inspect
results, and iterate:

```afl
event facet ResearchTopic(query: String) => (report: String)
    tools [WebSearch, ReadFile, Calculator]
    max_turns 5
```

**Changes required:**

- Tool registry concept at the AFL language level (not just runtime).
- An agent executor loop: LLM call → tool use → LLM call → ... → final answer.
- Integration with MCP tool calling (the MCP server already exists, so an agent
  could use AFL's own tools recursively).

### 2.4 Context and Memory Across Steps

Multi-step workflows need to pass conversation history or accumulated context
between steps:

```afl
workflow ResearchAndWrite(topic: String) => (article: String) andThen {
    research = ResearchTopic(query = $.topic)
    outline = CreateOutline(topic = $.topic, sources = research.report)
    article = WriteArticle(outline = outline.text, sources = research.report)
    yield ResearchAndWrite(article = article.text)
}
```

The data flow (`$.x`, `step.field`) already works for structured data. What is
missing:

- A `Context` or `Memory` type that accumulates across steps.
- Support for list and map types in expressions (currently only scalars).
- Possibly a vector store integration for retrieval.

### 2.5 Streaming and Partial Results

LLM responses are often streamed. The current model is fire-and-forget with a
single `continue_step()` call.

**Changes required:**

- `update_step()` for partial/streaming results alongside `continue_step()`
  for final results.
- Dashboard support for showing in-progress agent output.
- Event state extension: `Processing` could have sub-states.

### 2.6 Error Recovery and Retries with LLM Awareness

The runtime has `fail_step()` but no intelligent retry. An AI agent platform
needs:

- Retry with modified prompts (e.g., "Your previous answer was invalid JSON,
  try again").
- Fallback to a different model.
- Human escalation when confidence is low.

### 2.7 Implementation Status

Many features described above are now implemented and used in production examples.

| Feature | Status | Reference |
|---------|--------|-----------|
| **Prompt blocks** (§2.2) | Done | Grammar (`afl/grammar/afl.lark`), AST (`PromptBlock`), emitter, runtime evaluation |
| **LLMHandler + ClaudeAgentRunner** (§2.1, §2.3) | Done | Multi-turn tool use, retry, prompt template interpolation (`afl/runtime/agent.py`) |
| **Token tracking** (§2.1) | Done | `TokenUsage` dataclass, `token_budget` param, `TokenBudgetExceededError` |
| **List/map types** (§2.4) | Done | Array literals `[1, 2]`, map literals `#{"k": "v"}`, array indexing `a[0]`, array type annotations `[Type]` |
| **Streaming** (§2.5) | Not started | |
| **Intelligent retry** (§2.6) | Partial | `catch` blocks provide error recovery; prompt-level retry not yet automatic |

#### ANTHROPIC_API_KEY

The `ANTHROPIC_API_KEY` environment variable enables live Claude API calls for prompt-block event facets. When unset, LLM handlers fall back to deterministic stubs, keeping tests and CI green without API access.

#### Token Usage Tracking

`TokenUsage` (`afl/runtime/agent.py`) accumulates input/output tokens across API calls:

```python
@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    api_calls: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None: ...
    def to_dict(self) -> dict: ...
```

`ClaudeAgentRunner` and `LLMHandler` accept an optional `token_budget` parameter. When set, a `TokenBudgetExceededError` is raised before any API call that would exceed the budget:

```python
runner = ClaudeAgentRunner(evaluator=ev, persistence=store, token_budget=50000)
```

#### Real Example: Research Agent (LLM Integration)

The `research-agent` example showcases 8 prompt-block event facets chained together via `ClaudeAgentRunner`. Each step uses a prompt block to drive Claude API calls for research synthesis, citation extraction, and summary generation. When run with `ANTHROPIC_API_KEY` set, Claude generates structured research output. Without the API key, handlers return deterministic fallback responses.

> **Note:** The `noaa-weather` example previously used a `GenerateNarrative` prompt block for meteorologist-style summaries, but this was removed in the v0.38.0 GHCN-Daily redesign. The NOAA pipeline now uses `ComputeRegionTrend` to generate data-driven narrative summaries via linear regression analysis without LLM calls.

---

## 3. Prompt and Task Categories

Given the workflow DSL, AgentFlow is best suited for **structured multi-step AI
tasks** rather than single-shot chat.

### 3.1 Document Processing Pipelines

```afl
workflow ProcessInvoice(pdf: Binary) => (structured: Json) andThen {
    extracted = OCR(image = $.pdf)
    parsed = ExtractFields(text = extracted.text)
    validated = ValidateInvoice(fields = parsed.data)
    yield ProcessInvoice(structured = validated.result)
}
```

### 3.2 Research and Analysis

```afl
workflow CompetitorAnalysis(company: String) => (report: String) andThen {
    search = WebResearch(query = $.company)
    financials = AnalyzeFinancials(data = search.results)
    summary = WriteBrief(analysis = financials.insights)
    yield CompetitorAnalysis(report = summary.text)
}
```

### 3.3 Code Generation with Validation

```afl
workflow GenerateFeature(spec: String) => (code: String) andThen {
    design = ArchitectSolution(requirements = $.spec)
    impl = WriteCode(design = design.plan)
    reviewed = ReviewCode(code = impl.source)
    yield GenerateFeature(code = reviewed.final_code)
}
```

### 3.4 Multi-Agent Collaboration

```afl
workflow PeerReview(paper: String) => (verdict: String)
    andThen foreach reviewer in ["Expert1", "Expert2", "Expert3"] {
    review = ReviewPaper(text = $.paper, persona = reviewer)
    yield PeerReview(verdict = review.assessment)
}
```

### 3.5 Tasks Not Well Suited

- **Real-time conversational chat** — no streaming, no session state.
- **Single-shot Q&A** — overhead of compilation and evaluation is not
  justified.
- **Unstructured exploration** — the DSL requires known steps upfront.

---

## 4. How AgentFlow Accelerates Agent Development

Without AgentFlow, building a multi-step AI agent means writing custom
orchestration logic, state persistence, concurrency control, and monitoring.
With AgentFlow, these concerns are handled by the platform.

| Concern | Without AFL | With AFL |
|---------|------------|----------|
| Step sequencing | Custom code per workflow | Declare in `.afl`, compiler handles it |
| Data flow | Manual plumbing between steps | `step.field` references, type-checked |
| Persistence | Build your own | MongoDB store with atomic commits |
| Concurrency | Locks, queues, dedup | Task queue with atomic claiming |
| Monitoring | Build dashboard | Dashboard and MCP resources included |
| Error handling | Per-agent custom logic | State machine with `fail_step()` |
| Parallel execution | Threading/async code | `andThen foreach` in the DSL |

The key value proposition is the **separation of workflow logic (AFL) from
agent logic (Python handlers)**. A new agent is a handler function registered
against an event facet name. The platform handles everything else.

### 4.1 Authoring Roles

AgentFlow's separation of concerns maps naturally to distinct authoring roles:

| Role | Writes | Skills required |
|------|--------|-----------------|
| **Domain programmer** | AFL source (`.afl` files) — workflows, facets, schemas, composition | AFL syntax; no Python needed |
| **Service provider programmer** | Handler implementations (Python modules) for event facets | Python; domain-specific APIs |
| **Claude** | Both AFL definitions and handler implementations | Given a natural-language description of the desired workflow or service behavior |

Domain programmers focus on *what* the workflow does — its steps, data flow,
and composition. Service provider programmers focus on *how* each event facet
is fulfilled — the actual computation, API call, or LLM inference. Claude can
fill either or both roles, generating `.afl` files from requirements,
scaffolding handler modules with correct signatures and registration, or
building complete end-to-end examples including tests.

Once prompt templates (§2.2) and an async LLM handler (§2.1) are added,
defining an AI agent becomes: write the AFL workflow, write the prompt
templates, deploy. No orchestration code required.

---

## 5. MCP Protocol Reference

### 5.1 Protocol Messages

The MCP server uses JSON-RPC 2.0 over stdio. The following protocol messages are relevant:

| Message | Direction | AFL Handler |
|---------|-----------|-------------|
| `initialize` | Client → Server | SDK auto-handles; advertises `tools` + `resources` capabilities |
| `notifications/initialized` | Client → Server | SDK auto-handles |
| `ping` | Bidirectional | SDK auto-handles |
| `tools/list` | Client → Server | `list_tools()` → returns 6 Tool definitions with JSON Schema |
| `tools/call` | Client → Server | `call_tool(name, arguments)` → dispatches to `_tool_*` functions |
| `resources/list` | Client → Server | `list_resources()` → returns 10 Resource definitions with `afl://` URIs |
| `resources/read` | Client → Server | `read_resource(uri)` → routes to `_handle_resource()` |

Messages **not implemented**: `prompts/*`, `completion`, `resources/subscribe`, `sampling/createMessage`, `logging/setLevel`.

### 5.2 MCP Tools — Parameters and Returns

| Tool | Parameters | Returns |
|------|-----------|---------|
| `afl_compile` | `source: str` | `{ success, json?, errors? }` |
| `afl_validate` | `source: str` | `{ valid, errors: [{ message, line?, column? }] }` |
| `afl_execute_workflow` | `source: str`, `workflow_name: str`, `inputs?: dict` | `{ success, workflow_id, status, iterations, outputs, error? }` |
| `afl_continue_step` | `step_id: str`, `result?: dict` | `{ success, error? }` |
| `afl_resume_workflow` | `workflow_id: str`, `source: str`, `workflow_name: str`, `inputs?: dict` | `{ success, workflow_id, status, iterations, outputs, error? }` |
| `afl_manage_runner` | `runner_id: str`, `action: str` (cancel/pause/resume) | `{ success, error? }` |

### 5.3 MCP Resources — URI Patterns and Response Schemas

| URI Pattern | Response Shape |
|-------------|---------------|
| `afl://runners` | `[{ uuid, workflow_id, workflow_name, state, start_time, end_time, duration, parameters }]` |
| `afl://runners/{id}` | `{ uuid, workflow_id, workflow_name, state, start_time, end_time, duration, parameters }` |
| `afl://runners/{id}/steps` | `[{ id, workflow_id, object_type, state, statement_id, container_id, block_id, facet_name?, params?, returns? }]` |
| `afl://runners/{id}/logs` | `[{ uuid, order, runner_id, step_id, note_type, note_originator, note_importance, message, state, time }]` |
| `afl://steps/{id}` | `{ id, workflow_id, object_type, state, statement_id, container_id, block_id, facet_name?, params?, returns? }` |
| `afl://flows` | `[{ uuid, name, path, workflows: [{ uuid, name, version }], sources, facets }]` |
| `afl://flows/{id}` | `{ uuid, name, path, workflows, sources, facets }` |
| `afl://flows/{id}/source` | `{ uuid, name, sources: [{ name, content, language }] }` |
| `afl://servers` | `[{ uuid, server_group, service_name, server_name, state, start_time, ping_time, topics, handlers, handled }]` |
| `afl://tasks` | `[{ uuid, name, runner_id, workflow_id, flow_id, step_id, state, created, updated, task_list_name, data_type }]` |

---

## 6. Implementation Priority

Suggested ordering based on impact and dependency:

1. **Async callback support in AgentPoller** (§2.1) — unblocks all LLM
   integration work.
2. **LLMHandler base class** (§2.1) — provides the standard pattern for
   connecting models.
3. **Prompt templates in grammar** (§2.2) — makes workflows self-contained
   and declarative.
4. **List/map expression types** (§2.4) — needed for real-world data flow
   between steps.
5. **Tool use loops** (§2.3) — enables agentic behavior beyond single-call
   handlers.
6. **Streaming** (§2.5) — improves user experience for long-running agents.
7. **Intelligent retry** (§2.6) — improves reliability in production.
