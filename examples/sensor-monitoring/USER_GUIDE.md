# Sensor Monitoring — User Guide

## When to Use This Example

Use this example if you need to:

- **Monitor sensor data** with threshold-based anomaly detection
- **Use negative thresholds** (e.g., freezing point detection with `critical_low = -40.0`)
- **Handle null/missing values** as first-class concepts in your workflow (e.g., no prior reading)
- **Look up per-item configuration** via computed map keys (`$.configs[step.field]`)
- **Attach retry/alerting policies** to specific steps using mixin aliases
- **Run a RegistryRunner-first agent** (the recommended approach)

## What You'll Learn

1. **Unary negation** in schema instantiation (`low = -10.0`, `critical_low = -40.0`)
2. **Null literals** as call arguments (`last_reading = null`, `override_config = null`)
3. **Computed map indexing** (`$.sensor_configs[ingest.reading.sensor_id]`)
4. **Mixin alias** (`with RetryPolicy(...) as retry`, `with AlertConfig(...) as alertcfg`)
5. **Schema instantiation as steps** (`cfg = ThresholdConfig(...)` in andThen blocks)
6. **RegistryRunner as primary entry point** (`agent_registry.py`)
7. **Mixin args flowing to handlers** — the runtime evaluates and passes call-site mixin arguments

## Step-by-Step Walkthrough

### 1. Schemas Define Typed Data Structures

Six schemas in `monitor.types` define the domain model:

```afl
namespace monitor.types {
    schema ThresholdConfig {
        low: Double,
        high: Double,
        critical_low: Double,
        critical_high: Double
    }
    // ... SensorReading, AnomalyResult, AlertPayload, DiagnosticReport, MonitoringSummary
}
```

These schemas are instantiated as steps in workflows (see step 3).

### 2. Mixins and Implicits Provide Cross-Cutting Defaults

```afl
namespace monitor.mixins {
    facet RetryPolicy(max_retries: Int = 3, backoff_ms: Int = 1000)
    facet AlertConfig(channel: String = "default", escalate: Boolean = false)

    implicit defaultRetry = RetryPolicy(max_retries = 3, backoff_ms = 1000)
    implicit defaultAlert = AlertConfig(channel = "default", escalate = false)
}
```

Implicits provide fallback values when no explicit mixin is attached.

### 3. Schema Instantiation with Unary Negation

```afl
cfg = ThresholdConfig(low = -10.0, high = 50.0, critical_low = -40.0, critical_high = 80.0)
```

The `-10.0` and `-40.0` are **unary negation expressions** — the grammar parses `-` as a prefix operator. Downstream steps reference these values via `cfg.low`, `cfg.critical_low`, etc.

### 4. Null Literals in Call Arguments

```afl
ingest = IngestReading(sensor_id = $.sensor_id, ..., last_reading = null)
classify = ClassifyAlert(anomaly = detect.result, ..., override_config = null)
```

`null` is a first-class literal in AFL. Handlers receive `None` in Python and branch accordingly:

```python
def ingest_reading(sensor_id, value, unit, last_reading=None):
    quality = "initial" if last_reading is None else "continuous"
```

### 5. Computed Map Indexing

```afl
validate = ValidateReading(
    reading = ingest.reading,
    sensor_config = $.sensor_configs[ingest.reading.sensor_id]
)
```

The expression `$.sensor_configs[ingest.reading.sensor_id]` performs:
1. Evaluate `ingest.reading.sensor_id` → e.g., `"sensor_001"`
2. Use that as a key to index into `$.sensor_configs` (a map from workflow input)

This enables per-sensor configuration lookup without hardcoding sensor IDs.

### 6. Mixin Alias

```afl
ingest = IngestReading(...) with RetryPolicy(max_retries = 5, backoff_ms = 2000) as retry
classify = ClassifyAlert(...) with AlertConfig(channel = "alerts", escalate = true) as alertcfg
```

The `as retry` suffix creates a **nested namespace** in the handler's params:

```python
def handle_ingest_reading(params):
    retry = params.get("retry", {})       # {"max_retries": 5, "backoff_ms": 2000}
    max_retries = retry.get("max_retries") # 5
```

Without an alias, mixin args are **flat-merged** into params (but don't override explicit call args).

### 7. RegistryRunner as Primary Entry Point

`agent_registry.py` is the recommended way to run this agent:

```python
from afl.runtime.registry_runner import RegistryRunner
from handlers import register_all_registry_handlers

runner = RegistryRunner(service_name="sensor-monitoring")
register_all_registry_handlers(runner)
runner.run()
```

This registers handlers in the database and lets the runner dynamically dispatch tasks. The legacy `agent.py` (AgentPoller) is provided as an alternative.

## Handler Design

All handlers use deterministic stubs (`sensor_utils.py`) that produce reproducible output from MD5 hashes. Key patterns:

- **`ingest_reading`**: Returns `quality="initial"` when `last_reading is None`
- **`detect_anomaly`**: Checks value against four thresholds including negative ones
- **`classify_alert`**: Uses default priority mapping when `override_config is None`
- **`validate_reading`**: Applies calibration from sensor-specific config map

## Running

```bash
# Syntax check
python3 -m afl.cli examples/sensor-monitoring/afl/monitor.afl --check

# Run tests
python3 -m pytest examples/sensor-monitoring/tests/ -v

# Run agent (RegistryRunner — recommended)
PYTHONPATH=. python3 examples/sensor-monitoring/agent_registry.py

# Run agent (AgentPoller — alternative)
PYTHONPATH=. python3 examples/sensor-monitoring/agent.py
```

## Adapting for Your Use Case

- **Real sensor data**: Replace `sensor_utils.py` stubs with actual sensor API calls
- **Custom thresholds**: Add more schemas or change ThresholdConfig fields
- **Additional alerting**: Add more mixin facets (e.g., `EscalationPolicy`, `NotificationConfig`)
- **Multi-sensor batch**: Use the `BatchMonitor` workflow with `andThen foreach` over sensor IDs
- **Streaming**: Wrap `MonitorSensors` in a loop or connect to a message queue
