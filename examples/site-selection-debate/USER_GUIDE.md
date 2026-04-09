# Site-Selection Debate — User Guide

## When to Use This Example

Use this example if you need to:

- **Combine spatial analysis with LLM-driven research and multi-agent debate**
- **Score and rank candidate sites** using weighted quantitative metrics
- **Enrich decisions with web research** (market trends, regulations, competitors)
- **Run adversarial multi-agent evaluation** with 3 specialist roles across multiple rounds
- **Track cross-round convergence** with state passing between debate rounds
- **Use composed facets** to encapsulate complex multi-step processes

## What You'll Learn

1. **12 prompt-block event facets** across 4 domain namespaces (Spatial, Research, Debate, Synthesis)
2. **Composed facet** — `EvaluationRound` encapsulates 10 internal steps (3 present + 3 challenge + score + summarize + yield)
3. **Cross-round state** — `r1.synthesis → r2.prev_synthesis`, `r1.rankings → r2.prev_rankings`
4. **Statement-level andThen** — `access = ComputeAccessibility(...) andThen { regs = ... }`
5. **andThen foreach** — `BatchEvaluate` iterates `$.candidate_ids`
6. **Map literals** — `#{market: ..., regulations: ..., accessibility: ..., competitors: ...}`
7. **Null literals** — `prev_rankings = null`, `weights = null`
8. **Unary negation** — `below_market_penalty = -0.15`
9. **Mixin alias** — `with ResearchConfig(...) as research_cfg`
10. **Schema instantiation** — `cfg = DebateConfig(below_market_penalty = -0.15, ...)`
11. **String concatenation** (`++`) and **arithmetic expressions**
12. **RegistryRunner as primary entry point** (`agent_registry.py`)

## Step-by-Step Walkthrough

### 1. Schemas Define the Domain Model

Eight schemas in `siteselection.types` cover the full pipeline:

```afl
namespace siteselection.types {
    schema SpatialScore { candidate_id: String, overall_score: Double, ... }
    schema MarketResearch { growth_rate: Double, risk_factors: Json, ... }
    schema AgentPosition { agent_role: String, confidence: Double, ... }
    schema RoundRankings { consensus_level: Double, converged: Boolean, ... }
    schema DebateConfig { below_market_penalty: Double, max_rounds: Int, ... }
    schema AccessibilityMetrics { walk_score: Int, transit_coverage: Double, ... }
    schema RegulationSummary { permit_difficulty: Int, zoning_compatible: Boolean, ... }
    schema FinalReport { top_candidate: String, confidence: Double, recommendation: String, ... }
}
```

### 2. Mixins Provide Reusable Configuration

```afl
namespace siteselection.mixins {
    facet ResearchConfig(depth: Int = 3, market_area: String = "default")
    facet DebatePolicy(max_challenges: Int = 3, require_evidence: Boolean = true)
    implicit defaultResearch = ResearchConfig(...)
    implicit defaultDebatePolicy = DebatePolicy(...)
}
```

### 3. Four Namespaces of Event Facets

Each event facet has a prompt block for LLM execution:

- **siteselection.Spatial**: `ScoreCandidate`, `RankCandidates`, `ComputeAccessibility`
- **siteselection.Research**: `SearchMarketTrends`, `GatherRegulations`, `AnalyzeCompetitors`
- **siteselection.Debate**: `PresentAnalysis`, `ChallengePosition`, `ScoreArguments`
- **siteselection.Synthesis**: `SummarizeRound`, `ProduceRanking`, `GenerateReport`

### 4. Composed Facet Encapsulates a Debate Round

```afl
facet EvaluationRound(...) => (synthesis, rankings, key_arguments) andThen {
    fa = PresentAnalysis(agent_role = "financial_analyst", ...)
    ca = PresentAnalysis(agent_role = "community_analyst", ...)
    cs = PresentAnalysis(agent_role = "competitive_strategist", ...)
    ch_fa = ChallengePosition(agent_role = "financial_analyst", target_role = "community_analyst", ...)
    ch_ca = ChallengePosition(agent_role = "community_analyst", target_role = "competitive_strategist", ...)
    ch_cs = ChallengePosition(agent_role = "competitive_strategist", target_role = "financial_analyst", ...)
    sc = ScoreArguments(positions = [...], challenges = [...], ...)
    summ = SummarizeRound(positions = [...], challenges = [...], rankings = sc.rankings, ...)
    yield EvaluationRound(synthesis = summ.synthesis, rankings = sc.rankings, key_arguments = summ.key_arguments)
}
```

### 5. Workflow Chains Everything Together

`EvaluateSites` follows this pipeline:

1. Schema instantiation with unary negation: `cfg = DebateConfig(below_market_penalty = -0.15, ...)`
2. Spatial scoring with mixin alias: `spatial = ScoreCandidate(...) with ResearchConfig(...) as research_cfg`
3. Parallel research chains: `access = ComputeAccessibility(...) andThen { regs = ... }` and `market = SearchMarketTrends(...) andThen { competitors = ... }`
4. Map literal aggregation: `enrichment = #{market: ..., regulations: ..., ...}`
5. Three-round debate with cross-round state: `r1 = EvaluationRound(..., prev_rankings = null)` → `r2 = EvaluationRound(..., prev_rankings = r1.rankings)` → `r3`
6. Final ranking and report generation

## Adapting This Example

### Adding a New Agent Role

1. Add a new `PresentAnalysis` step in `EvaluationRound` (e.g., `legal = PresentAnalysis(agent_role = "legal_counsel", ...)`)
2. Add a corresponding `ChallengePosition` step
3. Update `ScoreArguments` and `SummarizeRound` to include the new position/rebuttal
4. Add handler logic in `debate_utils.py`

### Adding More Debate Rounds

Cross-round state makes this straightforward:
```afl
r4 = EvaluationRound(..., prev_synthesis = r3.synthesis, prev_rankings = r3.rankings)
```

### Using Real LLM Integration

Replace the deterministic stubs in `debate_utils.py` with actual API calls, or use `ClaudeAgentRunner` which dispatches to the Anthropic API via the prompt blocks defined in the FFL file.

## Running

```bash
# Compile and check syntax
afl examples/site-selection-debate/ffl/sitesel_debate.ffl --check

# Run tests
pytest examples/site-selection-debate/ -v

# Start the agent (RegistryRunner — recommended)
PYTHONPATH=. python examples/site-selection-debate/agent_registry.py

# Start the agent (AgentPoller — alternative)
PYTHONPATH=. python examples/site-selection-debate/agent.py
```
