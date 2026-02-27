"""Core sensor utilities -- deterministic stubs for testing.

Uses only hashlib and json from stdlib.  8 functions that produce
consistent, reproducible output from the same inputs.

Designed for real sensor monitoring dispatch when a sensor library is
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

def ingest_reading(
    sensor_id: str,
    value: float,
    unit: str,
    last_reading: object = None,
) -> tuple[dict, str]:
    """Create a SensorReading; when last_reading is None, quality='initial'.

    Returns (reading, quality).
    """
    timestamp = _hash_int(f"ts:{sensor_id}", 1700000000, 1800000000)
    reading = {
        "sensor_id": sensor_id,
        "timestamp": timestamp,
        "value": value,
        "unit": unit,
    }
    quality = "initial" if last_reading is None else "continuous"
    return reading, quality


def validate_reading(
    reading: dict,
    sensor_config: dict | None = None,
) -> tuple[bool, float]:
    """Apply sensor_config calibration; handles config from map lookup.

    Returns (valid, calibrated_value).
    """
    value = reading.get("value", 0.0)
    if sensor_config:
        offset = sensor_config.get("calibration_offset", 0.0)
        scale = sensor_config.get("calibration_scale", 1.0)
        calibrated = value * scale + offset
        valid = sensor_config.get("min", -999) <= calibrated <= sensor_config.get("max", 999)
    else:
        calibrated = value
        valid = -100 <= value <= 100
    return valid, round(calibrated, 4)


def detect_anomaly(
    reading: dict,
    threshold_low: float,
    threshold_high: float,
    critical_low: float,
    critical_high: float,
) -> dict:
    """Check value against threshold config including negative thresholds.

    Returns AnomalyResult dict.
    """
    value = reading.get("value", 0.0)
    deviation = 0.0
    threshold_breached = "none"
    severity = "normal"

    if value <= critical_low:
        deviation = critical_low - value
        threshold_breached = "critical_low"
        severity = "critical"
    elif value >= critical_high:
        deviation = value - critical_high
        threshold_breached = "critical_high"
        severity = "critical"
    elif value <= threshold_low:
        deviation = threshold_low - value
        threshold_breached = "low"
        severity = "warning"
    elif value >= threshold_high:
        deviation = value - threshold_high
        threshold_breached = "high"
        severity = "warning"

    is_anomaly = severity != "normal"
    return {
        "is_anomaly": is_anomaly,
        "severity": severity,
        "deviation": round(deviation, 4),
        "threshold_breached": threshold_breached,
    }


def classify_alert(
    anomaly: dict,
    sensor_id: str,
    override_config: object = None,
) -> dict:
    """When override_config is None, use default priority mapping.

    Returns AlertPayload dict.
    """
    severity = anomaly.get("severity", "normal")
    if override_config is None:
        priority_map = {"critical": 1, "warning": 2, "normal": 3}
        channel = "default"
    else:
        priority_map = override_config.get("priority_map", {"critical": 1, "warning": 2, "normal": 3})
        channel = override_config.get("channel", "default")
    priority = priority_map.get(severity, 3)
    return {
        "sensor_id": sensor_id,
        "anomaly": anomaly,
        "priority": priority,
        "channel": channel,
    }


def run_diagnostics(
    sensor_id: str,
    anomaly_result: dict,
    reading: dict,
) -> dict:
    """Assemble DiagnosticReport.

    Returns DiagnosticReport dict.
    """
    readings_checked = _hash_int(f"diag:{sensor_id}", 10, 100)
    anomalies_found = _hash_int(f"diag_anom:{sensor_id}", 0, readings_checked // 4)
    is_anomaly = anomaly_result.get("is_anomaly", False)
    if is_anomaly:
        anomalies_found = max(anomalies_found, 1)
    health = "degraded" if anomalies_found > readings_checked * 0.1 else "healthy"
    return {
        "sensor_id": sensor_id,
        "readings_checked": readings_checked,
        "anomalies_found": anomalies_found,
        "health_status": health,
    }


def generate_summary(
    sensor_id: str,
    diagnostic: dict,
    alert: dict,
) -> dict:
    """Assemble MonitoringSummary with aggregated counts.

    Returns MonitoringSummary dict.
    """
    anomalies = diagnostic.get("anomalies_found", 0)
    critical = 1 if alert.get("priority", 3) == 1 else 0
    health = diagnostic.get("health_status", "unknown")
    return {
        "total_sensors": 1,
        "total_anomalies": anomalies,
        "critical_count": critical,
        "report": f"Sensor {sensor_id}: {health}, {anomalies} anomalies, {critical} critical",
    }
