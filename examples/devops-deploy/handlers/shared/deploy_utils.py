"""Shared utility functions for the devops-deploy example.

All functions are pure and deterministic — they use hashlib for reproducible
test outputs rather than random data or real I/O.
"""

from __future__ import annotations

import hashlib


def _hash_int(seed: str, lo: int, hi: int) -> int:
    """Deterministic integer from a seed string."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % (hi - lo))


def _hash_hex(seed: str, length: int = 12) -> str:
    """Deterministic hex string from a seed."""
    return hashlib.sha256(seed.encode()).hexdigest()[:length]


def build_image(service: str, version: str, registry: str) -> dict:
    """Simulate building a container image.

    Returns image tag, digest, and size.
    """
    tag = f"{registry}/{service}:{version}"
    digest = "sha256:" + _hash_hex(f"build:{service}:{version}", 64)
    size_mb = _hash_int(f"size:{service}:{version}", 50, 500)
    return {
        "image_tag": tag,
        "digest": digest,
        "size_mb": size_mb,
    }


def run_tests(service: str, version: str) -> tuple[bool, int, int]:
    """Simulate running a test suite.

    Returns (passed, total_tests, failed_count).
    """
    total = _hash_int(f"tests:{service}:{version}", 20, 200)
    failed = _hash_int(f"fail:{service}:{version}", 0, 5)
    return failed == 0, total, failed


def analyze_deploy_risk(
    service: str,
    version: str,
    environment: str,
    change_size: int,
) -> tuple[str, int, list[str]]:
    """Analyze deployment risk.

    Returns (risk_level, risk_score, risk_factors).
    """
    score = _hash_int(f"risk:{service}:{version}:{environment}", 0, 100)
    factors: list[str] = []
    if score > 80:
        factors.append("large change set")
    if change_size > 100:
        factors.append("high line count")
    if environment == "production":
        factors.append("production target")
        score = min(100, score + 15)

    if score >= 80:
        level = "critical"
    elif score >= 50:
        level = "medium"
    else:
        level = "low"

    return level, score, factors


def normalize_config(
    service: str,
    environment: str,
    replicas: int,
    cpu_limit: str,
    memory_limit: str,
) -> dict:
    """Normalize deployment configuration.

    Returns a structured config dict.
    """
    ns = f"{service}-{environment}"
    labels = {
        "app": service,
        "env": environment,
        "managed-by": "facetwork",
    }
    return {
        "namespace": ns,
        "replicas": max(1, replicas),
        "cpu_limit": cpu_limit or "500m",
        "memory_limit": memory_limit or "512Mi",
        "labels": labels,
        "image_pull_policy": "Always" if environment == "production" else "IfNotPresent",
    }


def apply_deployment(
    service: str,
    image_tag: str,
    config: dict,
) -> tuple[str, str]:
    """Simulate applying a Kubernetes deployment.

    Returns (deployment_id, status).
    """
    dep_id = "deploy-" + _hash_hex(f"apply:{service}:{image_tag}", 8)
    return dep_id, "applied"


def wait_for_rollout(
    deployment_id: str,
    timeout_seconds: int,
) -> tuple[bool, int, str]:
    """Simulate waiting for rollout to complete.

    Returns (ready, ready_replicas, message).
    """
    ready_replicas = _hash_int(f"rollout:{deployment_id}", 1, 5)
    elapsed = _hash_int(f"elapsed:{deployment_id}", 10, timeout_seconds)
    return True, ready_replicas, f"Rollout complete in {elapsed}s"


def check_health(
    service: str,
    deployment_id: str,
    checks: list[str] | None = None,
) -> tuple[bool, dict]:
    """Run health checks on a deployed service.

    Returns (healthy, results_map).
    """
    checks = checks or ["readiness", "liveness", "connectivity"]
    results: dict[str, str] = {}
    all_ok = True
    for check in checks:
        status_val = _hash_int(f"health:{service}:{deployment_id}:{check}", 0, 10)
        ok = status_val > 1  # ~80% pass rate per check
        results[check] = "pass" if ok else "fail"
        if not ok:
            all_ok = False
    return all_ok, results


def triage_incident(
    service: str,
    health_results: dict,
    deployment_id: str,
) -> tuple[str, str, list[str]]:
    """Triage a failed health check incident.

    Returns (severity, recommendation, failed_checks).
    """
    failed = [k for k, v in health_results.items() if v != "pass"]
    if len(failed) >= 3:
        severity = "critical"
        recommendation = "Immediate rollback required"
    elif len(failed) >= 1:
        severity = "warning"
        recommendation = "Rollback recommended"
    else:
        severity = "info"
        recommendation = "No action needed"
    return severity, recommendation, failed


def rollback_deployment(
    service: str,
    deployment_id: str,
    reason: str,
) -> dict:
    """Simulate rolling back a deployment.

    Returns a RollbackReport dict.
    """
    rollback_id = "rb-" + _hash_hex(f"rollback:{service}:{deployment_id}", 8)
    previous_version = "v" + str(_hash_int(f"prev:{service}", 1, 50))
    return {
        "rollback_id": rollback_id,
        "previous_version": previous_version,
        "service": service,
        "reason": reason,
        "status": "rolled_back",
    }


def verify_rollback(
    service: str,
    rollback_id: str,
) -> tuple[bool, str]:
    """Verify that rollback completed successfully.

    Returns (verified, message).
    """
    ok = _hash_int(f"verify:{service}:{rollback_id}", 0, 10) > 0  # ~90% success
    msg = f"Rollback {rollback_id} {'verified' if ok else 'verification failed'}"
    return ok, msg
