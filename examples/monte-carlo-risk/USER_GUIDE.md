# Monte Carlo Risk — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **financial simulation** or **risk analysis** pipelines
- Using **fan-out parallelism** for batch simulation (multiple seeds running concurrently)
- Learning how **pre-script blocks** and **concurrent andThen script blocks** work
- Implementing **pure-Python analytics** with no external dependencies

## What You'll Learn

1. How multiple `SimulateBatch` steps fan out in parallel with different random seeds
2. How pre-processing `script` blocks compute workflow metadata before andThen blocks
3. How concurrent `andThen script` blocks post-process results alongside the main pipeline
4. How the `yield` statement promotes specific step results as workflow output
5. How to implement GBM simulation, Cholesky decomposition, VaR/CVaR, and Greeks in pure Python

## Step-by-Step Walkthrough

### 1. The Problem

You want to assess the risk of a multi-asset portfolio. The pipeline loads positions, fetches historical correlations, runs thousands of Geometric Brownian Motion simulations across multiple batches, computes Value-at-Risk and Greeks, stress-tests against market shocks, and generates a consolidated report.

### 2. The Pipeline Structure

The `AnalyzePortfolio` workflow has three concurrent execution paths:

```
Pre-script (metadata)
    |
andThen block (main pipeline):
    LoadPortfolio → FetchHistoricalData
        → 5x SimulateBatch (parallel, different seeds)
        → ComputeVaR (from batch PnL distributions)
        → ComputeGreeks (from portfolio + correlations)
        → 3x SimulateStress (parallel stress scenarios)
        → GenerateReport
        → yield
    |
andThen script (post-processing summary)
```

### 3. Pre-Processing Script Block

The workflow starts with a `script` block that runs before any `andThen` blocks:

```afl
workflow AnalyzePortfolio(
    portfolio_name: String = "default",
    num_batches: Long = 10,
    paths_per_batch: Long = 1000,
    ...
) => (...) script {
    result["total_paths"] = int(params.get("num_batches", 10)) * int(params.get("paths_per_batch", 1000))
    result["sim_description"] = "Monte Carlo: " + str(result["total_paths"]) + " paths"
}
```

This computes `total_paths` and `sim_description` from inputs, making them available as `$.total_paths` in later blocks.

### 4. Fan-Out Parallelism

Five simulation batches execute concurrently with different random seeds:

```afl
andThen {
    port = LoadPortfolio(portfolio_name = $.portfolio_name)
    hist = FetchHistoricalData(asset_ids = port.portfolio.positions, lookback_days = $.lookback_days)

    b0 = SimulateBatch(positions = port.portfolio.positions, cholesky = hist.correlation.cholesky,
        num_paths = $.paths_per_batch, time_horizon_days = $.time_horizon_days, batch_id = 0, random_seed = 100)
    b1 = SimulateBatch(..., batch_id = 1, random_seed = 200)
    b2 = SimulateBatch(..., batch_id = 2, random_seed = 300)
    b3 = SimulateBatch(..., batch_id = 3, random_seed = 400)
    b4 = SimulateBatch(..., batch_id = 4, random_seed = 500)

    var = ComputeVaR(pnl_distributions = b0.result.pnl_distribution, ...)
    greeks = ComputeGreeks(positions = port.portfolio.positions, cholesky = hist.correlation.cholesky)

    stress_recession = SimulateStress(positions = port.portfolio.positions,
        scenario_name = "recession", shock_factors = [-0.2, -0.25, -0.15, 0.1, 0.05])
    // ... two more stress scenarios ...

    report = GenerateReport(portfolio = port.portfolio, metrics = var.metrics, ...)
    yield AnalyzePortfolio(metrics = var.metrics, greeks = greeks.greeks, ...)
}
```

All five batches share the same dependency (`port` + `hist`) and run concurrently. `ComputeGreeks` and stress tests also run independently of the batch pipeline.

### 5. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
afl examples/monte-carlo-risk/ffl/risk.ffl --check

# Run tests
pytest examples/monte-carlo-risk/tests/ -v
```

No external dependencies required — all math uses Python stdlib.

## Key Concepts

### GBM Simulation

Each batch simulates correlated price paths using Geometric Brownian Motion:

```python
dW = cholesky @ uncorrelated_normals  # Cholesky transform for correlation
S_t = S_0 * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * dW)
```

The `random_seed` parameter ensures reproducibility across runs.

### Risk Metrics

`ComputeVaR` calculates from the merged PnL distribution:

| Metric | Formula |
|--------|---------|
| VaR 95% | 5th percentile of PnL |
| VaR 99% | 1st percentile of PnL |
| CVaR 95% | Mean of losses beyond VaR 95% |
| Expected shortfall | Mean of worst 5% of outcomes |
| Max drawdown | Largest peak-to-trough decline |
| Sharpe ratio | mean(PnL) / std(PnL) |

### Greeks via Finite Differences

`ComputeGreeks` bumps each position price by 1% and re-values:

| Greek | Method |
|-------|--------|
| Delta | (V(S+h) - V(S-h)) / (2h) |
| Gamma | (V(S+h) - 2V(S) + V(S-h)) / h^2 |
| Vega | V * sqrt(T) * sigma |

### Stress Testing

Three scenarios apply proportional shocks to all positions simultaneously:

| Scenario | Shock Factors (SPY/AAPL/GOOGL/TLT/GLD) |
|----------|----------------------------------------|
| Recession | -20%, -25%, -15%, +10%, +5% |
| Rate hike | -10%, -15%, -12%, -20%, +3% |
| Tech crash | -5%, -35%, -30%, +5%, +2% |

### Multiple Concurrent andThen Blocks

A workflow can have multiple `andThen` blocks executing concurrently. `AnalyzePortfolio` has:
1. A `script` block (pre-processing)
2. An `andThen { ... }` block (main pipeline)
3. Two `andThen script { ... }` blocks (post-processing)

The runtime manages dependencies and merges results.

## Handler Design

All handlers use deterministic pure-Python stubs:

```python
def generate_correlated_gbm_paths(positions, cholesky, num_paths, horizon_days, seed):
    rng = random.Random(seed)  # reproducible
    # ... GBM with Cholesky-correlated shocks ...
    return pnl_distribution
```

The synthetic portfolio holds 5 positions (SPY, AAPL, GOOGL, TLT, GLD) totaling $331,750.

## Adapting for Your Use Case

### Use real market data

Replace `FetchHistoricalData` with an API call:

```python
def handle_fetch_historical_data(params):
    import yfinance as yf
    data = yf.download(asset_ids, period=f"{lookback_days}d")
    correlation = data.pct_change().corr().values.tolist()
    return {"correlation": {"matrix": correlation, ...}}
```

### Add more simulation batches

Change the workflow inputs:

```afl
run = AnalyzePortfolio(portfolio_name = "aggressive", num_batches = 50, paths_per_batch = 5000)
```

### Add custom stress scenarios

Define new stress tests in the workflow with different shock factors:

```afl
stress_pandemic = SimulateStress(positions = port.portfolio.positions,
    scenario_name = "pandemic", shock_factors = [-0.30, -0.20, -0.10, 0.15, 0.20])
```

## Next Steps

- **[ml-hyperparam-sweep](../ml-hyperparam-sweep/USER_GUIDE.md)** — statement-level andThen, prompt blocks, map literals
- **[research-agent](../research-agent/USER_GUIDE.md)** — LLM-driven research with ClaudeAgentRunner
- **[data-quality-pipeline](../data-quality-pipeline/USER_GUIDE.md)** — schema instantiation, array types, expression grouping
