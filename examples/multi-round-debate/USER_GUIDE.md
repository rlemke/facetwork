# Multi-Round Debate — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **iterative multi-round systems** where rounds pass state forward
- Designing **composed facets** that encapsulate complex multi-step logic
- Working with **convergence metrics** to detect when iterative processes stabilize
- Implementing **cross-round state passing** (synthesis and scores flow from round to round)

## What You'll Learn

1. How to build composed facets that encapsulate 12+ steps as a reusable unit
2. How cross-round state flows through sequential facet calls (r1 → r2 → r3)
3. How convergence metrics use arithmetic (`/`, `%`) to detect stabilization
4. How `++` string concatenation builds yield summaries
5. How `andThen foreach` fans out per-agent post-processing
6. How mixins, pre-scripts, and andThen scripts integrate with composed facets

## Step-by-Step Walkthrough

### 1. The Problem

You want three debate agents to argue across **three rounds**, where each round builds on the previous round's synthesis and scores. A composed facet `DebateRound` encapsulates the full round logic (init → assign → 3× refine → 3× challenge → score → converge → summarize), and the workflow calls it three times with cross-round state.

### 2. The Composed Facet — DebateRound

The core architectural pattern is a **composed facet** that encapsulates 12 steps:

```afl
facet DebateRound(
    topic: String,
    round_num: Int = 1,
    num_agents: Int = 3,
    prev_synthesis: String = "",
    prev_scores: Json = []
) => (synthesis: String, scores: Json, convergence: ConvergenceMetrics, key_shifts: Json) andThen {
    init = InitiateRound(topic = $.topic, round_num = $.round_num, ...)
    assign = AssignPositions(round_state = init.round_state, round_num = $.round_num)
    a0 = RefineArgument(agent = "agent_0", ...) with RoundConfig()
    a1 = RefineArgument(agent = "agent_1", ...) with RoundConfig()
    a2 = RefineArgument(agent = "agent_2", ...) with RoundConfig()
    c0 = ChallengeArgument(agent = "agent_0", target_argument = a1.refined, ...)
    c1 = ChallengeArgument(agent = "agent_1", target_argument = a2.refined, ...)
    c2 = ChallengeArgument(agent = "agent_2", target_argument = a0.refined, ...)
    sc = ScoreRound(arguments = [...], challenges = [...], prev_scores = $.prev_scores, ...)
    conv = EvaluateConvergence(current_scores = sc.scores, prev_scores = $.prev_scores, ...)
    summ = SummarizeRound(arguments = [...], challenges = [...], scores = sc.scores, ...)
    yield DebateRound(synthesis = summ.synthesis, scores = sc.scores, convergence = conv.metrics, key_shifts = summ.key_shifts)
}
```

**Key points:**
- The facet takes `prev_synthesis` and `prev_scores` as inputs, enabling cross-round state
- All 12 steps are internal — callers just see inputs/outputs
- The `with RoundConfig()` mixin configures round parameters

### 3. Cross-Round State in IterativeDebate

The workflow calls `DebateRound` three times, threading state forward:

```afl
r1 = DebateRound(topic = $.topic, round_num = 1, num_agents = $.num_agents)
r2 = DebateRound(topic = $.topic, round_num = 2, prev_synthesis = r1.synthesis, prev_scores = r1.scores)
r3 = DebateRound(topic = $.topic, round_num = 3, prev_synthesis = r2.synthesis, prev_scores = r2.scores)
```

Round 2 receives round 1's synthesis and scores. Round 3 receives round 2's. The runtime enforces sequential execution via these dependencies.

### 4. Convergence Tracking

Each round evaluates convergence using arithmetic:

```python
score_delta = abs(avg_current - avg_prev) / max(avg_prev, 1.0)
converged = score_delta < 0.1
```

The convergence trajectory is collected across all rounds:

```afl
out = DeclareOutcome(convergence_trajectory = [r1.convergence, r2.convergence, r3.convergence], ...)
```

### 5. The AgentFocusedDebate Workflow

The second workflow uses `andThen foreach` to fan out per-agent post-processing:

```afl
} andThen foreach agent in $.agent_ids {
    ref = RefineArgument(agent = $.agent, ...) andThen {
        ch = ChallengeArgument(agent = $.agent, target_argument = ref.refined, ...)
    }
    yield AgentFocusedDebate(summary = $.agent ++ " processed")
}
```

Each agent independently refines and challenges in parallel iterations.

### 6. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
python -m afl.cli examples/multi-round-debate/ffl/rounds.afl --check

# Run tests
python -m pytest examples/multi-round-debate/tests/ -v
```

## Key Concepts

### Composed Facets

The PRIMARY architectural pattern. A composed facet wraps multiple steps into a reusable unit:

```afl
namespace multidebate.composition {
    facet DebateRound(...) => (...) andThen {
        // 12 internal steps
        yield DebateRound(...)
    }
}
```

Unlike a workflow, a composed facet can be called as a step inside other workflows or facets. The caller only sees the return clause.

### Stance Cycling with Modulo

Agents cycle through stances using `round_num % 3`:

```python
_STANCES = ["for", "against", "neutral"]
stance_idx = (agent_index + round_num) % 3
stance = _STANCES[stance_idx]
```

This ensures agents argue different positions in each round.

### Cross-Round State

State flows between rounds through explicit parameter passing:

```
Round 1 → (synthesis, scores) → Round 2 → (synthesis, scores) → Round 3
```

The runtime enforces ordering — round 2 cannot start until round 1's synthesis and scores are available.

### String Concatenation

The `++` operator builds yield summaries:

```afl
yield IterativeDebate(summary = "Debate on: " ++ $.topic ++ " completed 3 rounds")
```

## Handler Design

All handlers use deterministic stubs for testing — output is derived from MD5 hashes of inputs, making tests reproducible:

```python
def refine_argument(agent, topic, stance, round_num=1, prev_synthesis=""):
    seed = f"refine:{agent}:{topic}:{stance}:{round_num}"
    base_confidence = _hash_float(seed, 0.5, 0.8)
    confidence = min(1.0, base_confidence + round_num * 0.05)
    return {"agent": agent, "stance": stance, "argument": "...", "confidence": confidence}
```

For real LLM dispatch, replace the stub logic with Anthropic API calls.

## Adapting for Your Use Case

### Change the number of rounds

Adjust the workflow to add more `DebateRound` calls:

```afl
r4 = DebateRound(topic = $.topic, round_num = 4, prev_synthesis = r3.synthesis, prev_scores = r3.scores)
```

### Use convergence to stop early

Check `conv.metrics.converged` and skip subsequent rounds when the debate stabilizes.

### Connect to real LLMs

The prompt blocks are already defined on every event facet. Use `ClaudeAgentRunner`:

```python
from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry

registry = ToolRegistry()
runner = ClaudeAgentRunner(evaluator=evaluator, persistence=store, tool_registry=registry)
```

## Next Steps

- **[multi-agent-debate](../multi-agent-debate/USER_GUIDE.md)** — the predecessor: multi-agent personas and scoring
- **[tool-use-agent](../tool-use-agent/USER_GUIDE.md)** — tool-as-event-facet pattern with planned orchestration
- **[research-agent](../research-agent/USER_GUIDE.md)** — LLM-driven research with ClaudeAgentRunner
