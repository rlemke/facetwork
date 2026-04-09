# Research Agent — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **LLM-driven workflows** where every step is powered by a prompt block
- Using **ClaudeAgentRunner** and **ToolRegistry** for in-process LLM execution
- Designing **chained AI research pipelines** (plan → gather → analyze → write → review)
- Learning how **statement-level andThen** parallelizes per-subtopic research
- Using **array indexing** (`decomp.subtopics[0]`) in step arguments

## What You'll Learn

1. How every event facet uses a `prompt` block to define LLM behavior
2. How statement-level andThen chains `GatherSources → ExtractFindings` per subtopic
3. How array indexing (`subtopics[0]`, `subtopics[1]`) selects items from step results
4. How array literals collect findings from parallel research branches
5. How `ClaudeAgentRunner` and `ToolRegistry` wire handlers for LLM execution
6. How call-site mixins (`with Retry() with Citation()`) configure individual steps

## Step-by-Step Walkthrough

### 1. The Problem

Given a research topic, you want an AI pipeline to plan the investigation, decompose it into subtopics, gather sources for each, extract findings, synthesize across all subtopics, identify gaps, draft a report, and review the draft. Each step should be driven by an LLM with a clear system prompt.

### 2. Prompt Blocks on Every Event Facet

Every event facet defines exactly what the LLM should do:

```afl
event facet PlanResearch(
    topic: String,
    depth: Long = 3,
    max_subtopics: Long = 5
) => (plan: Topic) prompt {
    system "You are a research planning assistant. Create detailed research plans with clear subtopic breakdowns."
    template "Plan a research investigation on '{topic}' at depth {depth}. Identify up to {max_subtopics} key subtopics."
    model "claude-sonnet-4-20250514"
}
```

The `prompt` block is the single source of truth — stubs use it for testing, `ClaudeAgentRunner` uses it for real LLM dispatch.

### 3. Parallel Gathering with Statement-Level andThen

Three subtopics are researched in parallel, each with its own gather+extract chain:

```afl
g0 = GatherSources(subtopic = decomp.subtopics[0], max_sources = $.sources_per_subtopic)
    with Retry(max_attempts = 3) andThen {
    f0 = ExtractFindings(subtopic = decomp.subtopics[0], sources = g0.sources)
}
g1 = GatherSources(subtopic = decomp.subtopics[1], ...) andThen {
    f1 = ExtractFindings(subtopic = decomp.subtopics[1], sources = g1.sources)
}
g2 = GatherSources(subtopic = decomp.subtopics[2], ...) andThen {
    f2 = ExtractFindings(subtopic = decomp.subtopics[2], sources = g2.sources)
}
```

Each `GatherSources` step starts as soon as `decomp` completes. The inline `andThen` block on each gather triggers extraction immediately — without waiting for other subtopics. All three chains run concurrently.

### 4. Array Indexing and Collection

Array indexing selects specific subtopics: `decomp.subtopics[0]`, `decomp.subtopics[1]`, `decomp.subtopics[2]`.

After extraction, results are collected into an array for synthesis:

```afl
synth = SynthesizeFindings(topic = plan.plan, all_findings = [f0.findings, f1.findings, f2.findings])
```

### 5. The Full Pipeline

```afl
workflow ResearchTopic(topic: String, ...) => (report: Draft, review: ReviewResult, ...) script {
    result["total_sources"] = max_subtopics * sources_per_subtopic
} andThen {
    plan = PlanResearch(topic = $.topic, ...) with Retry(...) with Citation(...)
    decomp = DecomposeIntoSubtopics(topic = plan.plan, ...)
    // 3 parallel gather+extract chains (see above)
    synth = SynthesizeFindings(topic = plan.plan, all_findings = [f0.findings, f1.findings, f2.findings])
    gapcheck = IdentifyGaps(analysis = synth.analysis, topic = plan.plan)
    report = DraftReport(topic = plan.plan, analysis = synth.analysis, gaps = gapcheck.gaps)
    rev = ReviewDraft(draft = report.draft, topic = plan.plan, analysis = synth.analysis)
    yield ResearchTopic(report = report.draft, review = rev.review, ...)
} andThen script {
    result["summary"] = "Completed research on: " + params.get("topic", "unknown")
}
```

### 6. Dynamic Fan-Out with DeepDive

The `DeepDive` workflow uses `andThen foreach` for unbounded subtopic parallelism:

```afl
workflow DeepDive(...) => (plan: Topic, subtopics: Json) andThen {
    plan = PlanResearch(topic = $.topic, ...) with Citation(style = "ieee", ...)
    decomp = DecomposeIntoSubtopics(topic = plan.plan, ...)
    yield DeepDive(plan = plan.plan, subtopics = decomp.subtopics)
} andThen foreach sub in $.subtopics {
    g = GatherSources(subtopic = $.sub, ...) with Retry(max_attempts = 5) andThen {
        f = ExtractFindings(subtopic = $.sub, sources = g.sources)
    }
    yield DeepDive(subtopics = f.findings)
}
```

Unlike `ResearchTopic`'s fixed 3 subtopics, `DeepDive` handles any number from the runtime.

### 7. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
afl examples/research-agent/ffl/research.ffl --check

# Run tests
pytest examples/research-agent/tests/ -v
```

No external dependencies — all research utilities use Python stdlib stubs.

## Key Concepts

### ClaudeAgentRunner Integration

The test suite demonstrates how to wire handlers for LLM execution:

```python
from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry

registry = ToolRegistry()
registry.register("PlanResearch", handle_plan_research)
# ... register other handlers ...

runner = ClaudeAgentRunner(
    evaluator=evaluator,
    persistence=store,
    tool_registry=registry,
)
result = runner.run(workflow_ast, inputs={"topic": "AI safety"}, program_ast=program_ast)
```

In production, `ClaudeAgentRunner` uses the prompt blocks to call Claude directly. For testing, the registered handler functions provide deterministic stubs.

### Call-Site Mixin Composition

Mixins decorate specific calls without modifying the event facet:

```afl
plan = PlanResearch(...) with Retry(max_attempts = 3, backoff_ms = 2000) with Citation(style = "apa", include_urls = true)
```

The handler receives mixin values in `params` and can use them to control behavior. Implicit defaults (`defaultRetry`, `defaultCitation`) provide fallback values.

### JSON Serialization of Schema Types

Handlers defensively handle both dict and JSON-string inputs:

```python
topic = params.get("topic", {})
if isinstance(topic, str):
    topic = json.loads(topic)
```

This is necessary because the runtime may serialize structured types as JSON strings when passing between steps.

### Deterministic Stubs

All utility functions produce reproducible output via MD5 hashing:

```python
def gather_sources(subtopic, max_sources=5):
    seed = f"{subtopic['name']}_source_{i}"
    relevance = _hash_float(seed, 0.4, 1.0)  # deterministic from seed
    return [{"title": f"Source {i}: {subtopic['name']}", "relevance_score": relevance, ...}]
```

## Adapting for Your Use Case

### Connect to Claude API

Replace stubs with real Anthropic API calls. The prompt blocks already define system prompts, templates, and models — `ClaudeAgentRunner` uses them directly.

### Add more research phases

Define new event facets with prompt blocks:

```afl
namespace research.Analysis {
    event facet FactCheck(findings: Json, sources: Json) => (verified: Json, confidence: Double) prompt {
        system "You are a fact-checking agent."
        template "Verify these findings against sources: {findings}"
        model "claude-sonnet-4-20250514"
    }
}
```

### Increase subtopic depth

Adjust `max_subtopics` and `sources_per_subtopic` in the workflow call, or use `DeepDive` for unbounded parallelism.

## Next Steps

- **[multi-agent-debate](../multi-agent-debate/USER_GUIDE.md)** — multi-agent personas with scoring and voting
- **[multi-round-debate](../multi-round-debate/USER_GUIDE.md)** — composed facets for iterative rounds
- **[tool-use-agent](../tool-use-agent/USER_GUIDE.md)** — tool-as-event-facet pattern with planning
