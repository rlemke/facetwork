# Data Quality Pipeline — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **data quality assessment** pipelines with profiling, validation, scoring, and remediation
- Using **schema instantiation as steps** (`cfg = QualityConfig(...)` in andThen blocks)
- Leveraging **array type annotations** (`[ColumnProfile]`, `[ValidationResult]`, etc.)
- Using **parenthesized expression grouping** to override operator precedence

## What You'll Learn

1. How schema instantiation creates configuration steps (`cfg = QualityConfig(...)`)
2. How array type annotations express typed collections in facet signatures
3. How parenthesized grouping `(a + b) * c` overrides operator precedence
4. How to build a multi-stage quality pipeline (profile → validate → score → remediate → report)
5. How statement-level andThen chains dependent validation steps
6. How `andThen foreach` fans out per-dataset profiling in batch mode

## Step-by-Step Walkthrough

### 1. The Problem

You want to assess the quality of one or more datasets by profiling columns, detecting anomalies, validating completeness and accuracy, scoring quality dimensions with configurable weights, and generating remediation actions for failing checks.

### 2. Configuration via Schema Instantiation

Instead of passing configuration values as individual parameters, define schemas for configuration and instantiate them as steps:

```afl
schema QualityConfig {
    missing_threshold: Double,
    type_error_max: Int,
    freshness_hours: Int
}

schema ScoringWeights {
    completeness: Double,
    accuracy: Double,
    freshness: Double
}
```

In the workflow's andThen block, instantiate them as steps:

```afl
cfg = QualityConfig(missing_threshold = 0.05, type_error_max = 3, freshness_hours = 48)
weights = ScoringWeights(completeness = 0.4, accuracy = 0.35, freshness = 0.25)
```

The runtime stores schema args as returns, so downstream steps reference fields via dot notation: `cfg.missing_threshold`, `weights.completeness`.

### 3. Array Type Annotations

Facet signatures use `[Type]` to express typed collections:

```afl
event facet ProfileDataset(
    dataset: String,
    columns: [String]
) => (profiles: [ColumnProfile], row_count: Int)
```

This declares that `columns` is an array of strings and `profiles` is an array of `ColumnProfile` schemas. The compiler validates these annotations at parse time.

### 4. Parenthesized Expression Grouping

The yield expression uses `(expr)` to override operator precedence:

```afl
yield AssessQuality(
    summary = "Quality " ++ gr.grade ++ " for " ++ $.dataset
        ++ " (" ++ (anom.anomaly_count + prof.row_count) * 1 ++ ")"
)
```

Without parentheses, `anom.anomaly_count + prof.row_count * 1` would multiply `row_count` by `1` first (since `*` has higher precedence than `+`). The parentheses force addition first.

### 5. Multi-Stage Pipeline

The `AssessQuality` workflow chains eight event facets:

```
pre-script → cfg=QualityConfig → weights=ScoringWeights
→ ProfileDataset → DetectAnomalies
→ ValidateCompleteness andThen { ValidateAccuracy }
→ ComputeScores → AssignGrade
→ PlanRemediation → GenerateReport
→ yield with ++ and (expr) → andThen script
```

Key dependencies:
- `DetectAnomalies` uses `cfg.missing_threshold` from the schema instantiation
- `ValidateAccuracy` chains after `ValidateCompleteness` via statement-level andThen
- `ComputeScores` receives individual weight fields from `weights.completeness`, etc.

### 6. Batch Processing with andThen foreach

The `BatchAssessment` workflow profiles multiple datasets in parallel:

```afl
} andThen foreach ds in $.datasets {
    per = ProfileDataset(dataset = $.ds, columns = $.columns) andThen {
        per_anom = DetectAnomalies(profiles = per.profiles, row_count = per.row_count)
    }
    yield BatchAssessment(summary = "Profiled: " ++ $.ds)
}
```

Each dataset gets its own profiling + anomaly detection chain, all running in parallel.

### 7. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
python -m afl.cli examples/data-quality-pipeline/ffl/quality.afl --check

# Run tests
python -m pytest examples/data-quality-pipeline/tests/ -v
```

## Key Concepts

### Schema Instantiation as Steps

Schema instantiation (`cfg = QualityConfig(...)`) is a unique step type:
- The runtime creates a step with `object_type: SCHEMA_INSTANTIATION`
- Arguments are stored directly as returns (no agent dispatch needed)
- Fields resolve via dot notation: `cfg.missing_threshold`
- This enables typed, reusable configuration within workflows

### Array Types in AFL

Array types are declared with bracket syntax:

| Syntax | Meaning |
|--------|---------|
| `[String]` | Array of strings |
| `[ColumnProfile]` | Array of ColumnProfile schemas |
| `[ValidationResult]` | Array of ValidationResult schemas |

The compiler validates element types against available schemas.

### Expression Precedence

AFL operator precedence (highest to lowest):
1. `*`, `/`, `%` — multiplication, division, modulo
2. `+`, `-` — addition, subtraction
3. `++` — string concatenation

Use parentheses `(expr)` to override: `(a + b) * c` forces addition before multiplication.

## Handler Design

All handlers use deterministic stubs for testing:

```python
def profile_dataset(dataset, columns=None):
    seed = f"profile:{dataset}"
    row_count = _hash_int(seed, 100, 10000)
    profiles = [{"column_name": col, "missing_count": _hash_int(...), ...} for col in columns]
    return profiles, row_count
```

The quality scoring uses a weighted formula:

```python
weighted = (raw * weight) / total_weight
```

And grading maps scores to letters: A >= 0.9, B >= 0.8, C >= 0.7, D >= 0.6, F < 0.6.

## Adapting for Your Use Case

### Connect to real profiling libraries

Replace stubs with actual profilers:

```python
def profile_dataset(dataset, columns=None):
    import pandas as pd
    df = pd.read_parquet(dataset)
    profiles = [{"column_name": col, "missing_count": df[col].isna().sum(), ...} for col in df.columns]
    return profiles, len(df)
```

### Add custom validation checks

Define a new event facet:

```afl
namespace dataquality.Validation {
    event facet ValidateFreshness(
        dataset: String,
        max_age_hours: Int = 24
    ) => (results: [ValidationResult], freshness_score: Double) prompt { ... }
}
```

### Adjust scoring weights

Change the schema instantiation in the workflow:

```afl
weights = ScoringWeights(completeness = 0.6, accuracy = 0.3, freshness = 0.1)
```

## Next Steps

- **[tool-use-agent](../tool-use-agent/USER_GUIDE.md)** — tool-as-event-facet pattern with planning
- **[multi-round-debate](../multi-round-debate/USER_GUIDE.md)** — composed facets for iterative rounds
- **[research-agent](../research-agent/USER_GUIDE.md)** — LLM-driven research with ClaudeAgentRunner
