# ML Hyperparameter Sweep — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **ML training pipelines** with parallel hyperparameter configurations
- Using **statement-level andThen** to chain dependent steps (train then evaluate)
- Using **map literals** (`#{"key": value}`) for inline structured configuration
- Using **andThen foreach** for dynamic fan-out over runtime-supplied config lists
- Learning how **prompt blocks** define LLM-driven event facets

## What You'll Learn

1. How statement-level andThen chains `TrainModel → EvaluateModel` per configuration
2. How map literals `#{"lr": 0.01, "epochs": 50}` pass inline structured data
3. How array literals collect evaluation results from parallel runs
4. How `andThen foreach` fans out training across a dynamic config list
5. How pre-script and andThen script blocks compute metadata
6. How mixin composition (`with Timeout() with GPU()`) decorates steps

## Step-by-Step Walkthrough

### 1. The Problem

You want to sweep hyperparameters for an ML model: prepare a dataset, split it, train multiple configurations in parallel, evaluate each, pick the best by F1 score, and generate a report. Two sweep strategies are provided — fixed grid and dynamic foreach.

### 2. Statement-Level andThen

Each training step has its own inline andThen block that triggers evaluation immediately after training completes:

```afl
t0 = TrainModel(
    dataset = split.result,
    hyperparams = #{"learning_rate": 0.001, "epochs": 100, "dropout": 0.2, "batch_size": 32},
    model_config = #{"architecture": "mlp", "hidden_layers": 3, "activation": "relu"}
) with Timeout(minutes = 30) with GPU(device_id = 0) andThen {
    e0 = EvaluateModel(model_id = t0.result.model_id, test_data = split.result)
}
```

Four such `(train → evaluate)` chains run in parallel. The `with Timeout() with GPU()` mixins decorate the training step without changing the event facet definition.

### 3. Map Literals

Map literals use the `#{}` syntax to pass inline structured data:

```afl
hyperparams = #{"learning_rate": 0.01, "epochs": 50, "dropout": 0.3, "batch_size": 64}
model_config = #{"architecture": "mlp", "hidden_layers": 3, "activation": "relu"}
```

This avoids defining a schema for every parameter combination. The handler receives them as Python dicts.

### 4. Array Literals for Result Collection

After all four evaluations complete, their results are collected into an array:

```afl
best = CompareToBestModel(eval_results = [e0.result, e1.result, e2.result, e3.result])
```

`CompareToBestModel` sorts by F1 score and returns the winning model ID.

### 5. Dynamic Fan-Out with andThen foreach

The `GridSearchSweep` workflow iterates over a runtime-supplied config list:

```afl
workflow GridSearchSweep(
    dataset_name: String = "synthetic",
    configs: Json = [...]
) => (...) andThen foreach cfg in $.configs {
    t = TrainModel(dataset = split.result, hyperparams = $.cfg,
        model_config = #{"architecture": "mlp", "hidden_layers": 2, "activation": "relu"}) andThen {
        e = EvaluateModel(model_id = t.result.model_id, test_data = split.result)
    }
    yield GridSearchSweep(summary = "Trained config")
}
```

Each element in `$.configs` spawns its own train+evaluate chain, all running in parallel.

### 6. Pre-Script and andThen Script

The `HyperparamSweep` workflow uses both:

```afl
workflow HyperparamSweep(...) => (...) script {
    // Pre-script: runs before andThen, computes metadata
    result["total_runs"] = 4
    result["grid_description"] = "4 learning rates: 0.001, 0.01, 0.05, 0.1"
} andThen {
    // Main pipeline with 4 parallel train+evaluate chains
    ...
} andThen script {
    // Post-processing: runs concurrently with main block
    result["grid_description"] = "Sweep complete: " + str(params.get("total_runs", 4)) + " configurations"
}
```

### 7. Prompt Block

`GenerateSweepReport` has a prompt block for LLM-driven report generation:

```afl
event facet GenerateSweepReport(
    best_model: String, best_metric: Double, total_configs: Long, dataset_name: String
) => (report: SweepReport) prompt {
    system "You are an ML experiment analyst. Generate concise sweep reports."
    template "Generate a sweep report: best model {best_model}, metric {best_metric}, ..."
    model "claude-sonnet-4-20250514"
}
```

The handler provides a deterministic fallback; in production the prompt block routes to Claude.

### 8. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
afl examples/ml-hyperparam-sweep/ffl/sweep.ffl --check

# Run tests
pytest examples/ml-hyperparam-sweep/tests/ -v
```

No external dependencies — all ML computation uses Python stdlib stubs.

## Key Concepts

### Mixin Composition at Call Site

Mixins attach cross-cutting behavior to individual steps:

```afl
prep = PrepareDataset(...) with Retry(max_attempts = 3, backoff_ms = 2000)
t0 = TrainModel(...) with Timeout(minutes = 30) with GPU(device_id = 0)
```

Implicit defaults (`defaultRetry`, `defaultTimeout`, `defaultGPU`) provide fallback values.

### Deterministic Training Stubs

The training stub derives loss and accuracy from hyperparameters:

```python
raw_loss = lr * 10.0 + 1/(epochs+1) + abs(dropout - 0.25) * 0.5
accuracy = sigmoid(1 / raw_loss)  # higher lr → higher loss → lower accuracy
```

This ensures tests are reproducible and highlight how different hyperparameters affect outcomes.

### Multiple Concurrent andThen Blocks

`HyperparamSweep` has three concurrent execution paths:
1. `script { ... }` — pre-processing (metadata)
2. `andThen { ... }` — main pipeline (train, evaluate, compare, report)
3. `andThen script { ... }` — post-processing summary

All three run concurrently; the runtime merges their results.

## Adapting for Your Use Case

### Add real ML training

Replace `train_model_stub` with actual training:

```python
def train_model(dataset, hyperparams, model_config):
    import torch
    model = build_model(model_config)
    optimizer = torch.optim.Adam(model.parameters(), lr=hyperparams["learning_rate"])
    # ... training loop ...
    return {"model_id": save_checkpoint(model), "loss": final_loss, "accuracy": final_acc}
```

### Expand the sweep grid

Add more configurations to the fixed grid or supply a larger `configs` list to `GridSearchSweep`.

### Add early stopping

Create a new mixin:

```afl
facet EarlyStopping(patience: Int = 5, min_delta: Double = 0.001)
```

Handlers can read mixin parameters from `params` to implement stopping logic.

## Next Steps

- **[research-agent](../research-agent/USER_GUIDE.md)** — LLM-driven research with prompt blocks and ClaudeAgentRunner
- **[monte-carlo-risk](../monte-carlo-risk/USER_GUIDE.md)** — financial simulation with fan-out parallelism
- **[data-quality-pipeline](../data-quality-pipeline/USER_GUIDE.md)** — schema instantiation, array types, expression grouping
