# Tool-Use Agent — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **tool-use agents** where an LLM plans and executes tool invocations
- Modeling **tools as event facets** (search, calculate, code execution)
- Designing **planning facets** that orchestrate tool order and selection
- Implementing **multi-tool pipelines** with search, compute, and synthesis

## What You'll Learn

1. How to model tools (web search, calculator, code executor) as event facets
2. How a planning facet (`PlanToolUse`) decides tool order and strategy
3. How statement-level andThen chains dependent tool calls
4. How `++` concatenation and `%`/`/` arithmetic work in expressions
5. How `andThen foreach` fans out parallel searches across subtopics
6. How mixins (`ToolConfig`, `SafetyCheck`) configure tool behavior

## Step-by-Step Walkthrough

### 1. The Problem

You want an LLM agent to solve a query by planning which tools to use, invoking them (web search, deep search, calculator, code executor), synthesizing all results, and formatting a final answer with citations.

### 2. Tools as Event Facets

Each tool is modeled as an event facet with a prompt block:

```afl
event facet WebSearch(
    query: String,
    max_results: Int = 5
) => (search_result: SearchResult) prompt {
    system "You are a web search tool that finds relevant information."
    template "Search the web for: '{query}'. Return up to {max_results} results."
    model "claude-sonnet-4-20250514"
}
```

This pattern makes tools first-class workflow participants — they have typed inputs and outputs, can be composed, and can be dispatched to LLMs or deterministic handlers.

### 3. Planning the Tool Strategy

The workflow starts with a planning step:

```afl
plan = PlanToolUse(query = $.query)
```

The planner produces a `ToolPlan` with search queries, calculation expressions, code snippets, and the tool execution order. This separates **planning** from **execution**.

### 4. Chaining Tools with Statement-Level andThen

The `SolveWithTools` workflow chains tools using statement-level andThen:

```afl
ws = WebSearch(query = $.query, max_results = $.max_results) andThen {
    ds = DeepSearch(query = $.query, initial_results = [ws.search_result], depth = 2)
    calc = Calculate(expression = "relevance / 100", precision = 2)
    code = ExecuteCode(code = "print('analysis')", language = "python")
}
```

After the web search completes, three tools run in parallel: deep search, calculator, and code execution. This demonstrates the tool-as-event-facet pattern in action.

### 5. Synthesis and Formatting

All tool results feed into synthesis and formatting:

```afl
synth = SynthesizeResults(search_results = [ws.search_result], calculations = [calc.calculation], code_results = [code.code_result], query = $.query)
fmt = FormatAnswer(synthesis = synth.synthesis, key_findings = synth.key_findings, confidence = synth.confidence, query = $.query)
```

The final yield uses `++` concatenation:

```afl
yield SolveWithTools(answer = fmt.answer, tools_used = 4, summary = "Solved: " ++ $.query ++ " using 4 tools")
```

### 6. Parallel Research with andThen foreach

The `ResearchAndCompute` workflow fans out searches across subtopics:

```afl
} andThen foreach sq in $.subtopics {
    search = WebSearch(query = $.query ++ " " ++ $.sq, max_results = 3) andThen {
        deep = DeepSearch(query = $.query ++ " " ++ $.sq, initial_results = [search.search_result])
    }
    yield ResearchAndCompute(summary = "Searched: " ++ $.sq)
}
```

Each subtopic gets its own search + deep search chain, all running in parallel.

### 7. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
python -m afl.cli examples/tool-use-agent/ffl/toolbox.ffl --check

# Run tests
python -m pytest examples/tool-use-agent/tests/ -v
```

## Key Concepts

### Tool-as-Event-Facet Pattern

The central design: each tool is an event facet. This provides:
- **Typed interfaces** — inputs/outputs are declared in the FFL signature
- **Prompt blocks** — LLMs can drive tools natively
- **Composability** — tools chain via step references like any other facet
- **Handler flexibility** — use stubs for testing, LLMs for production

### Tool Planning

The `PlanToolUse` facet analyzes the query and produces a strategy:

```python
{
    "strategy": "multi-tool approach for 'quantum computing'",
    "search_queries": ["quantum computing aspect_0", ...],
    "calculation": "relevance(quantum computing)",
    "code_snippet": "analyze('quantum computing')",
    "tool_order": ["search", "calculate", "execute"]
}
```

`SelectNextTool` uses modulo cycling to pick the next tool:

```python
idx = len(completed_tools) % len(tools)
```

### Safety Mixins

The `SafetyCheck` mixin controls which tools are allowed:

```afl
facet SafetyCheck(allow_code_exec: Boolean = false, allow_web: Boolean = true)
implicit defaultSafetyCheck = SafetyCheck(allow_code_exec = false, allow_web = true)
```

Handlers can check mixin parameters to gate tool execution.

### Arithmetic in Expressions

The FFL uses `/` for normalization and `%` for modulo in tool cycling:

```afl
calc = Calculate(expression = "confidence % 100 / 10", precision = 2)
```

## Handler Design

All handlers use deterministic stubs for testing:

```python
def web_search(query, max_results=5):
    seed = f"search:{query}"
    n_results = min(_hash_int(seed, 2, 5), max_results)
    results = [{"title": f"Result {i} for '{query}'", ...} for i in range(n_results)]
    return {"query": query, "results": results, "relevance": ..., "source_count": len(results)}
```

For real dispatch, connect to search APIs, code sandboxes, and LLMs.

## Adapting for Your Use Case

### Add new tools

Define a new event facet and handler:

```afl
namespace tools.Database {
    event facet QueryDatabase(sql: String) => (rows: Json, count: Int) prompt { ... }
}
```

### Connect to real APIs

Replace stubs with API clients:

```python
def web_search(query, max_results=5):
    response = requests.get("https://api.search.com/search", params={"q": query, "limit": max_results})
    return response.json()
```

### Use with ClaudeAgentRunner

```python
from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry

registry = ToolRegistry()
# Register tool handlers
runner = ClaudeAgentRunner(evaluator=evaluator, persistence=store, tool_registry=registry)
```

## Next Steps

- **[multi-round-debate](../multi-round-debate/USER_GUIDE.md)** — composed facets for iterative multi-round debates
- **[multi-agent-debate](../multi-agent-debate/USER_GUIDE.md)** — multi-agent personas and scoring
- **[research-agent](../research-agent/USER_GUIDE.md)** — LLM-driven research with ClaudeAgentRunner
