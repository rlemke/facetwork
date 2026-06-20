# Research Agent — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **LLM-driven workflows** where each step calls Claude via fwh_anthropic's `anthropic.messages.CreateMessage` facet
- Composing **cross-package FFL** — a domain example that consumes facets from a separate package (`fwh_anthropic`)
- Designing **chained AI research pipelines** (plan → gather → analyze → write → review)
- Learning the **composed-facet + typed-parser** pattern for getting structured data out of an LLM
- Using **statement-level andThen** to parallelize per-subtopic research and **array indexing** to thread step results

## What You'll Learn

1. How to compose a domain workflow against `anthropic.messages.CreateMessage` from `fwh_anthropic`
2. The **composed facet + typed parser** pattern — wrap a `CreateMessage` call and a `Parse<Schema>` event facet inside a regular `facet`, exposing a clean typed interface upstream
3. How statement-level andThen chains `GatherSources → ExtractFindings` per subtopic
4. How array indexing (`subtopics[0]`, `subtopics[1]`) selects items from step results
5. How array literals collect findings from parallel research branches
6. How call-site mixins (`with Retry() with Citation()`) configure individual steps
7. How to write tolerant JSON parsers that handle fences, prefatory prose, and the occasional bad response

## Step-by-Step Walkthrough

### 1. The Problem

Given a research topic, you want an AI pipeline to plan the investigation, decompose it into subtopics, gather sources for each, extract findings, synthesize across subtopics, identify gaps, draft a report, and review the draft. Each step is one Claude call returning JSON; the framework parses and types the response.

### 2. Composed Facet + Typed Parser

Each domain step is a regular **composed facet** (not `event facet`) that:
1. calls `anthropic.messages.CreateMessage` with a domain-specific system prompt that asks for JSON,
2. hands the response `text` to a small `Parse<X>` event facet whose only job is JSON-to-schema conversion.

```afl
namespace research.Planning {
    use anthropic.messages
    use research.types

    event facet ParseTopic(
        text: String, topic_name: String, depth: Long, max_subtopics: Long
    ) => (plan: Topic)

    facet PlanResearch(
        topic: String, depth: Long = 3, max_subtopics: Long = 5
    ) => (plan: Topic) andThen {
        reply = anthropic.messages.CreateMessage(
            system = "You are a research planning assistant. Reply with one JSON object only ...",
            prompt = "Plan a research investigation on '" ++ $.topic ++ "' ...",
            max_tokens = 1024)
        parsed = ParseTopic(
            text = reply.result.text,
            topic_name = $.topic,
            depth = $.depth,
            max_subtopics = $.max_subtopics)
        yield PlanResearch(plan = parsed.plan)
    }
}
```

Upstream workflows still call `PlanResearch(topic = ...)` and receive a typed `Topic`. The two-step internal mechanics (CreateMessage + ParseTopic) are an implementation detail.

### 3. Parallel Gathering with Statement-Level andThen

Three subtopics research in parallel, each with its own gather+extract chain:

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

Each `GatherSources` step starts as soon as `decomp` completes. The inline `andThen` block triggers extraction immediately — without waiting for other subtopics. All three chains run concurrently.

### 4. Array Indexing and Collection

Array indexing selects specific subtopics: `decomp.subtopics[0]`, `decomp.subtopics[1]`, `decomp.subtopics[2]`. After extraction, results are collected into an array for synthesis:

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
# Install fwh_anthropic so anthropic.messages.* facets are discoverable
pip install -e ~/fw_handlers/fwh_anthropic

# From the facetwork repo
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check (research.ffl references anthropic.messages.* — pass it as a library)
afl --primary examples/research-agent/ffl/research.ffl \
    --library ~/fw_handlers/fwh_anthropic/src/anthropic_handlers/ffl/messages.ffl \
    --check

# Run tests (no API key needed — parsers fall back to synthetic when text isn't JSON)
pytest examples/research-agent/tests/ -v

# Real run requires ANTHROPIC_API_KEY (fwh_anthropic's CreateMessage handler uses it)
export ANTHROPIC_API_KEY=sk-...
fw runner start --example anthropic -- --log-format text
# then trigger the workflow from the dashboard
```

## Key Concepts

### Why typed parsers instead of free-form prompt blocks

The original version of this example used `prompt { ... }` blocks on every event facet. That ties the example to `ClaudeAgentRunner` (which dispatches prompt blocks against Claude) and the response is opaque text — the workflow has nothing to type-check against.

The current version pushes prompt + JSON-shape contract into the system prompt, then declares a typed parser event facet (`ParseTopic`, `ParseSubtopics`, …) that mediates the conversion. Benefits:

- **Typed handoff.** `ParseTopic.plan: Topic` — downstream steps get a real schema, not a string.
- **Runner-agnostic.** Any `RegistryRunner` works; no special `ClaudeAgentRunner` wiring needed.
- **Auditable.** When Claude returns garbage, the parser falls back to a deterministic synthetic shape and emits a `synthetic fallback` step-log entry — workflow keeps moving, you see exactly which step degraded.

### Tolerant JSON extraction

`extract_json_payload(text)` tries, in order:
1. Bare `json.loads(text)`.
2. ```` ```json ... ``` ```` (or unlabelled) code-fence content.
3. The first balanced `{...}` or `[...]` substring that parses.

This handles the common shapes Claude actually returns: bare JSON, fenced JSON, JSON with prefatory prose ("Sure! Here's the JSON: ..."). When all three fail, the parser handler falls back to a deterministic stub and warns.

### Call-Site Mixin Composition

Mixins decorate specific calls without modifying the composed facet:

```afl
plan = PlanResearch(...) with Retry(max_attempts = 3, backoff_ms = 2000) with Citation(style = "apa", include_urls = true)
```

The handler receives mixin values in `params` and can use them to control behavior. Implicit defaults (`defaultRetry`, `defaultCitation`) provide fallback values.

## Adapting for Your Use Case

### Tighter schema enforcement

Strengthen `coerce_*` helpers in `handlers/shared/research_utils.py` to raise on missing keys instead of filling defaults. The parser handlers will catch the `ValueError` and emit a warning step-log entry — adjust to taste.

### Add more research phases

Define a new composed facet + parser pair in the matching namespace, e.g. a `FactCheck` step under `research.Analysis`:

```afl
event facet ParseFactCheck(text: String, topic_name: String) => (verified: Json, confidence: Double)

facet FactCheck(findings: Json, sources: Json) => (verified: Json, confidence: Double) andThen {
    reply = anthropic.messages.CreateMessage(
        system = "You are a fact-checking agent. Return JSON {\"verified\": [...], \"confidence\": number}.",
        prompt = "Verify these findings against the sources: ...",
        max_tokens = 1024)
    parsed = ParseFactCheck(text = reply.result.text, topic_name = "fact-check")
    yield FactCheck(verified = parsed.verified, confidence = parsed.confidence)
}
```

### Increase subtopic depth

Adjust `max_subtopics` and `sources_per_subtopic` in the workflow call, or use `DeepDive` for unbounded parallelism.

## Next Steps

- **[multi-agent-debate](../multi-agent-debate/USER_GUIDE.md)** — multi-agent personas with scoring and voting
- **[multi-round-debate](../multi-round-debate/USER_GUIDE.md)** — composed facets for iterative rounds
- **[tool-use-agent](../tool-use-agent/USER_GUIDE.md)** — tool-as-event-facet pattern with planning
- **[fwh_anthropic](https://github.com/rlemke/fwh_anthropic)** — the package this example consumes
