# Multi-Agent Debate — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **multi-agent systems** where agents interact and respond to each other
- Designing **scoring and voting mechanisms** that rank agent outputs
- Working with **agent-to-agent output dependency** (one agent's output feeds another)
- Implementing **parallel agent execution** with distinct personas

## What You'll Learn

1. How to give agents distinct personas using call-site mixins (`with AgentRole(...)`)
2. How agent-to-agent dependency works — rebuttals consume other agents' arguments
3. How to implement scoring/voting to rank agent outputs
4. How statement-level andThen chains agent actions (rebuttal → score)
5. How `andThen foreach` fans out work across agents in parallel
6. How pre-scripts and andThen scripts add computed values to workflows

## Step-by-Step Walkthrough

### 1. The Problem

You want three debate agents — a **proposer**, a **critic**, and a **synthesizer** — to argue positions on a topic, rebut each other, get scored, and arrive at a verdict. Each agent has a distinct persona and sees the other agents' outputs.

### 2. Framing the Debate

The workflow starts by framing the topic and assigning roles:

```afl
frame = FrameDebate(topic = $.topic, num_agents = $.num_agents)
roles = AssignRoles(topic_analysis = frame.topic_analysis, positions = frame.positions)
```

`FrameDebate` analyzes the topic and identifies positions (for, against, neutral). `AssignRoles` maps those positions to agent personas.

### 3. Parallel Arguments with Distinct Personas

Three agents generate arguments in parallel, each with a different persona via call-site mixins:

```afl
a0 = GenerateArgument(role = roles.assignments[0], topic = $.topic, context = frame.stakes) with AgentRole(persona = "proposer", expertise = "advocacy")
a1 = GenerateArgument(role = roles.assignments[1], topic = $.topic, context = frame.stakes) with AgentRole(persona = "critic", expertise = "analysis")
a2 = GenerateArgument(role = roles.assignments[2], topic = $.topic, context = frame.stakes) with AgentRole(persona = "synthesizer", expertise = "integration")
```

**Key points:**
- `roles.assignments[0]` uses **array indexing** to select each agent's role
- `with AgentRole(persona = "proposer")` attaches a **mixin** that identifies the agent's persona
- All three steps can execute in parallel since they only depend on `roles`

### 4. Agent-to-Agent Dependency — Rebuttals

Each agent rebuts the *other* agents' arguments, creating cross-agent dependencies:

```afl
r0 = GenerateRebuttal(role = roles.assignments[0], arguments = [a1.argument, a2.argument]) with AgentRole(persona = "proposer") andThen {
    s0 = ScoreArguments(arguments = [a0.argument], rebuttals = [r0.rebuttal])
}
```

**Key points:**
- `arguments = [a1.argument, a2.argument]` uses **array literals** to pass the other agents' outputs
- Agent 0 rebuts agents 1 and 2; agent 1 rebuts agents 0 and 2; agent 2 rebuts agents 0 and 1
- The `andThen` block on each rebuttal triggers **statement-level scoring** — each rebuttal is immediately scored

### 5. Synthesis and Verdict

All arguments, rebuttals, and scores flow into synthesis and judgment:

```afl
synth = SynthesizePositions(arguments = [a0.argument, a1.argument, a2.argument], rebuttals = [r0.rebuttal, r1.rebuttal, r2.rebuttal], scores = [s0.scores, s1.scores, s2.scores])
verdict = JudgeDebate(topic = $.topic, synthesis = synth.synthesis, scores = [s0.scores, s1.scores, s2.scores])
```

The synthesizer finds common ground and themes. The judge picks a winner based on cumulative scores.

### 6. The ConsensusDebate Workflow

The second workflow uses `andThen foreach` to fan out work across agents:

```afl
workflow ConsensusDebate(...) => (...) andThen {
    // ... frame, roles, parallel arguments, synthesize, judge ...
    cons = BuildConsensus(verdict = verdict.verdict, synthesis = synth.synthesis, themes = synth.themes) with Timeout(minutes = 60)
    yield ConsensusDebate(...)
} andThen foreach agent in $.assignments {
    arg = GenerateArgument(role = $.agent, topic = $.topic) with AgentRole(persona = "debater") andThen {
        reb = GenerateRebuttal(role = $.agent, arguments = [arg.argument])
    }
    yield ConsensusDebate(synthesis = reb.rebuttal)
}
```

Each agent independently generates an argument and rebuttal in parallel iterations.

### 7. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
python -m afl.cli examples/multi-agent-debate/ffl/debate.afl --check

# Run tests
python -m pytest examples/multi-agent-debate/tests/ -v
```

## Key Concepts

### Multi-Agent Personas

Agents are differentiated by call-site mixins, not by separate handler implementations. The same `GenerateArgument` event facet is called three times with different `AgentRole` mixins:

```afl
// Mixin definition
namespace debate.mixins {
    facet AgentRole(persona: String = "neutral", expertise: String = "general")
    implicit defaultRole = AgentRole(persona = "neutral", expertise = "general")
}

// Call-site persona assignment
a0 = GenerateArgument(...) with AgentRole(persona = "proposer", expertise = "advocacy")
a1 = GenerateArgument(...) with AgentRole(persona = "critic", expertise = "analysis")
```

The mixin data is available to the handler and to LLM prompt blocks, allowing each agent to behave differently while sharing the same handler code.

### Agent-to-Agent Output Dependency

The central pattern: one agent's output feeds into another agent's input via step references:

```
a0 = GenerateArgument(...)   a1 = GenerateArgument(...)   a2 = GenerateArgument(...)
         |                            |                            |
         v                            v                            v
r0 = GenerateRebuttal(              r1 = GenerateRebuttal(        r2 = GenerateRebuttal(
    arguments = [a1, a2])               arguments = [a0, a2])        arguments = [a0, a1])
```

The runtime resolves these dependencies automatically — rebuttals only execute after the referenced arguments are complete.

### Scoring and Voting

Each agent's argument is scored on three dimensions:

```python
scores.append({
    "agent_role": agent_role,
    "clarity": _hash_int(..., 40, 95),
    "evidence_quality": _hash_int(..., 35, 90),
    "persuasiveness": _hash_int(..., 30, 95),
    "overall": (clarity + evidence_quality + persuasiveness) // 3,
})
```

The judge picks the winner by highest overall score and computes the margin of victory.

### Statement-Level andThen

Attach inline processing to a step without a separate block:

```afl
r0 = GenerateRebuttal(...) andThen {
    s0 = ScoreArguments(arguments = [a0.argument], rebuttals = [r0.rebuttal])
}
```

The scoring step only runs after the rebuttal completes — it's scoped to that specific rebuttal's lifecycle.

### Pre-Scripts and andThen Scripts

**Pre-script** — computes values before the workflow body runs:

```afl
workflow StructuredDebate(...) => (...) script {
    result["total_arguments"] = int(params.get("num_agents", 3)) * 2
    result["summary"] = "Structured debate on: " + params.get("topic", "unknown")
} andThen { ... }
```

**andThen script** — runs after the main body for post-processing:

```afl
} andThen script {
    result["summary"] = "Completed debate on: " + params.get("topic", "unknown")
        + " with " + str(params.get("total_arguments", 0)) + " total arguments"
}
```

Both receive `params` (workflow inputs + computed values) and write to `result`.

## Handler Design

All handlers use deterministic stubs for testing — output is derived from MD5 hashes of inputs, making tests reproducible. The stubs follow the pattern:

```python
def generate_argument(role, topic, context=""):
    persona = role.get("persona", "neutral")
    seed = f"{persona}_{topic}_{context}"
    confidence = round(_hash_float(seed, 0.4, 0.95), 2)
    claims = [f"Claim {i} by {persona}: ..." for i in range(3)]
    return {"agent_role": persona, "claims": claims, "confidence": confidence, ...}
```

For real LLM dispatch, replace the stub logic with Anthropic API calls — the prompt blocks in each event facet provide system prompts and templates.

## Adapting for Your Use Case

### Change the number of agents

Adjust `num_agents` and add/remove argument and rebuttal steps:

```afl
a3 = GenerateArgument(role = roles.assignments[3], topic = $.topic) with AgentRole(persona = "mediator", expertise = "negotiation")
r3 = GenerateRebuttal(role = roles.assignments[3], arguments = [a0.argument, a1.argument, a2.argument]) andThen {
    s3 = ScoreArguments(arguments = [a3.argument], rebuttals = [r3.rebuttal])
}
```

### Add iterative rounds

Wrap the argument/rebuttal cycle in a composed facet and call it multiple times:

```afl
facet DebateRound(roles: Json, topic: String, round_num: Int) => (arguments: Json, rebuttals: Json, scores: Json) andThen {
    // argument + rebuttal + scoring steps
}

workflow MultiRoundDebate(...) => (...) andThen {
    round1 = DebateRound(roles = roles.assignments, topic = $.topic, round_num = 1)
    round2 = DebateRound(roles = roles.assignments, topic = $.topic, round_num = 2)
    // synthesize across rounds
}
```

### Connect to real LLMs

The prompt blocks are already defined on every event facet. Use `ClaudeAgentRunner` to dispatch to Claude:

```python
from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry

registry = ToolRegistry()
# Register handlers that call the Anthropic API instead of stubs
runner = ClaudeAgentRunner(evaluator=evaluator, persistence=store, tool_registry=registry)
```

## Next Steps

- **[research-agent](../research-agent/USER_GUIDE.md)** — LLM-driven research with ClaudeAgentRunner end-to-end
- **[osm-geocoder](../osm-geocoder/USER_GUIDE.md)** — production-scale agent with 580+ handlers
- **[continental-lz](../continental-lz/USER_GUIDE.md)** — Docker-orchestrated multi-region pipeline
