"""Core quality utilities -- deterministic stubs for testing.

Uses only hashlib and json from stdlib.  8 functions that produce
consistent, reproducible output from the same inputs.

Designed for real data-quality dispatch when a profiling library is
available; these stubs provide synthetic fallback for testing.
"""

from __future__ import annotations

import hashlib
import json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_int(seed: str, low: int = 0, high: int = 100) -> int:
    """Deterministic integer from seed string."""
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return low + (h % (high - low + 1))


def _hash_float(seed: str, low: float = 0.0, high: float = 1.0) -> float:
    """Deterministic float from seed string."""
    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    ratio = h / 0xFFFFFFFF
    return low + ratio * (high - low)


# ---------------------------------------------------------------------------
# Public API (one per event facet)
# ---------------------------------------------------------------------------

def profile_dataset(
    dataset: str,
    columns: list | None = None,
) -> tuple[list, int]:
    """Per-column stats: missing_count, distinct_count, dtype from hash.

    Returns (profiles, row_count).
    """
    if columns is None:
        columns = ["col_a", "col_b", "col_c"]
    seed = f"profile:{dataset}"
    row_count = _hash_int(seed, 100, 10000)
    profiles = []
    for col in columns:
        col_seed = f"{seed}:{col}"
        missing = _hash_int(col_seed, 0, row_count // 5)
        distinct = _hash_int(f"{col_seed}:distinct", 1, row_count)
        dtype_idx = _hash_int(f"{col_seed}:dtype", 0, 3)
        dtype = ["string", "integer", "float", "boolean"][dtype_idx]
        profiles.append({
            "column_name": col,
            "missing_count": missing,
            "distinct_count": distinct,
            "dtype": dtype,
        })
    return profiles, row_count


def detect_anomalies(
    profiles: list,
    row_count: int,
    missing_threshold: float = 0.1,
) -> tuple[int, list]:
    """Flag columns where missing_count / total_rows > threshold.

    Returns (anomaly_count, flagged_columns).
    """
    flagged = []
    for p in profiles:
        missing_rate = p.get("missing_count", 0) / max(row_count, 1)
        if missing_rate > missing_threshold:
            flagged.append({
                "column": p["column_name"],
                "missing_rate": round(missing_rate, 4),
                "threshold": missing_threshold,
            })
    return len(flagged), flagged


def validate_completeness(
    profiles: list,
    row_count: int,
    missing_threshold: float = 0.1,
) -> tuple[list, float]:
    """Missing rate per column vs threshold, completeness_score.

    Returns (results, completeness_score).
    """
    results = []
    scores = []
    for p in profiles:
        missing_rate = p.get("missing_count", 0) / max(row_count, 1)
        passed = missing_rate <= missing_threshold
        score = 1.0 - missing_rate
        scores.append(score)
        results.append({
            "check_name": f"completeness:{p['column_name']}",
            "passed": passed,
            "score": round(score, 4),
            "details": {
                "missing_rate": round(missing_rate, 4),
                "threshold": missing_threshold,
            },
        })
    completeness_score = round(sum(scores) / max(len(scores), 1), 4)
    return results, completeness_score


def validate_accuracy(
    profiles: list,
    type_error_max: int = 5,
) -> tuple[list, float]:
    """Type error count check, accuracy_score.

    Returns (results, accuracy_score).
    """
    results = []
    scores = []
    for p in profiles:
        seed = f"accuracy:{p['column_name']}:{p.get('dtype', 'string')}"
        type_errors = _hash_int(seed, 0, 10)
        passed = type_errors <= type_error_max
        score = max(0.0, 1.0 - (type_errors / max(type_error_max * 2, 1)))
        scores.append(score)
        results.append({
            "check_name": f"accuracy:{p['column_name']}",
            "passed": passed,
            "score": round(score, 4),
            "details": {
                "type_errors": type_errors,
                "max_allowed": type_error_max,
            },
        })
    accuracy_score = round(sum(scores) / max(len(scores), 1), 4)
    return results, accuracy_score


def compute_scores(
    completeness_score: float,
    accuracy_score: float,
    w_completeness: float = 0.4,
    w_accuracy: float = 0.35,
    w_freshness: float = 0.25,
) -> tuple[list, float]:
    """Weighted formula: (raw * weight) / total_weight.

    Returns (scores, overall).
    """
    total_weight = w_completeness + w_accuracy + w_freshness
    freshness_raw = _hash_float("freshness:default", 0.7, 1.0)

    dimensions = [
        ("completeness", completeness_score, w_completeness),
        ("accuracy", accuracy_score, w_accuracy),
        ("freshness", freshness_raw, w_freshness),
    ]

    scores = []
    weighted_sum = 0.0
    for dim, raw, weight in dimensions:
        weighted = (raw * weight) / max(total_weight, 0.001)
        weighted_sum += weighted
        scores.append({
            "dimension": dim,
            "raw_score": round(raw, 4),
            "weighted_score": round(weighted, 4),
        })

    overall = round(weighted_sum, 4)
    return scores, overall


def assign_grade(
    overall: float,
    min_score: float = 0.7,
) -> tuple[str, bool]:
    """Score -> A/B/C/D/F mapping, passed boolean.

    Returns (grade, passed).
    """
    if overall >= 0.9:
        grade = "A"
    elif overall >= 0.8:
        grade = "B"
    elif overall >= 0.7:
        grade = "C"
    elif overall >= 0.6:
        grade = "D"
    else:
        grade = "F"
    passed = overall >= min_score
    return grade, passed


def plan_remediation(
    results: list,
    flagged_columns: list | None = None,
) -> list:
    """Prioritized actions for failed checks.

    Returns list of RemediationAction dicts.
    """
    if flagged_columns is None:
        flagged_columns = []
    actions = []
    priority = 1

    # Actions from flagged columns (anomalies)
    for fc in flagged_columns:
        col = fc.get("column", "unknown")
        actions.append({
            "priority": priority,
            "action": "fill_missing",
            "target_column": col,
            "reason": f"Missing rate {fc.get('missing_rate', 0)} exceeds threshold",
        })
        priority += 1

    # Actions from failed validation results
    for r in results:
        if not r.get("passed", True):
            check = r.get("check_name", "unknown")
            col = check.split(":")[-1] if ":" in check else "unknown"
            action = "fix_type_errors" if "accuracy" in check else "fill_missing"
            actions.append({
                "priority": priority,
                "action": action,
                "target_column": col,
                "reason": f"Check '{check}' failed with score {r.get('score', 0)}",
            })
            priority += 1

    return actions


def generate_report(
    dataset: str,
    grade: str,
    passed: bool,
    overall: float,
    scores: list,
    actions: list,
) -> dict:
    """Assemble QualityReport dict."""
    return {
        "dataset": dataset,
        "grade": grade,
        "passed": passed,
        "overall_score": overall,
        "dimensions": {s["dimension"]: s["weighted_score"] for s in scores},
        "actions": actions,
        "summary": f"Dataset '{dataset}' received grade {grade} "
                   f"(score: {overall:.2f}, {'PASSED' if passed else 'FAILED'})",
    }
