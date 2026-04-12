# DevOps Deploy Pipeline

A Kubernetes deployment pipeline that demonstrates **`andThen when` blocks** (conditional branching) as the primary feature, along with foreach iteration, schemas, prompt blocks, script blocks, mixins, and implicits.

## What it does

This example models: build, test, risk-analyze, deploy, health-check, and rollback — with conditional branching at two levels:

1. **After risk analysis** — block deployment if risk is critical, fail if tests didn't pass, or proceed to deploy (workflow-level `andThen when`)
2. **After health check** — succeed if healthy, or triage + rollback if unhealthy (nested statement-level `andThen when`)
3. **Batch deploy** — deploy multiple services in parallel (`andThen foreach`)

### Execution flow (DeployService)

```
BuildImage ──┐
RunTests ────┤
AnalyzeRisk ─┘
     │
     ▼
 andThen when
 ├─ risk == "critical"  → yield "blocked"
 ├─ tests.passed == false → yield "failed"
 └─ default →
       NormalizeConfig → ApplyDeployment → WaitForRollout → CheckHealth
                                                               │
                                                          andThen when
                                                          ├─ healthy → yield "success"
                                                          └─ default → Triage → Rollback → VerifyRollback
                                                                       yield "rolled_back"
```

## FFL structure

| Namespace | Contents |
|-----------|----------|
| `deploy.types` | 3 schemas: `DeploymentConfig`, `HealthCheckResult`, `RollbackReport` |
| `deploy.mixins` | 3 mixins + 3 implicits: `RetryPolicy`, `Timeout`, `Credentials` |
| `deploy.Build` | 3 event facets: `BuildImage`, `RunTests`, `AnalyzeDeployRisk` |
| `deploy.Deploy` | 3 event facets: `NormalizeConfig`, `ApplyDeployment`, `WaitForRollout` |
| `deploy.Monitor` | 2 event facets: `CheckHealth`, `TriageIncident` |
| `deploy.Rollback` | 2 event facets: `RollbackDeployment`, `VerifyRollback` |
| `deploy.workflows` | 2 workflows: `DeployService`, `BatchDeploy` |

## FFL syntax quick-reference

Every construct below appears in [`ffl/deploy.ffl`](ffl/deploy.ffl).

### Comments

```afl
// Line comment
/** Doc comment (must immediately precede a declaration) */
```

### Namespaces and imports

```afl
namespace deploy.types { ... }

namespace deploy.Build {
    use deploy.types          // import schemas from another namespace
}
```

### Schemas (typed structures)

Schemas must be defined inside a namespace.

```afl
namespace deploy.types {
    schema DeploymentConfig {
        namespace_name: String,
        replicas: Int,
        cpu_limit: String,
        labels: Json,
        image_pull_policy: String
    }
}
```

**Types**: `String`, `Int`, `Boolean`, `Json`, `[Type]` (array), or a schema name.

### Facets (typed attribute signatures)

```afl
facet RetryPolicy(max_retries: Int = 3, backoff_ms: Int = 1000) => (max_retries: Int, backoff_ms: Int)
```

- Parameters: `name: Type` with optional `= default`
- Returns: `=> (name: Type, ...)`

### Event facets (trigger agent execution)

```afl
event facet BuildImage(service: String, version: String) => (image_tag: String, digest: String)
```

Event facets pause workflow execution and create a task for an agent to process.

### Mixins (`with`)

Compose facets into event facets:

```afl
event facet BuildImage(...) => (...) with RetryPolicy() with Credentials()
```

### Implicits (default values)

```afl
implicit default_retry = RetryPolicy()
implicit default_timeout = Timeout()
```

### Prompt blocks (LLM-driven event facets)

```afl
event facet AnalyzeDeployRisk(...) => (...) prompt {
    system "You are a deployment risk analyst."
    template "Analyze risk for deploying {service}:{version} to {environment}"
    model "claude-sonnet-4-20250514"
}
```

### Script blocks (inline Python)

Code receives `params` dict and writes to `result` dict:

```afl
event facet NormalizeConfig(...) => (config: DeploymentConfig) script python "
config_ns = params.get('service', '') + '-' + params.get('environment', '')
result['config'] = {
    'namespace_name': config_ns,
    'replicas': max(1, params.get('replicas', 2)),
}
"
```

### Workflows and `andThen` blocks

Workflows are entry points. Steps are assignments inside `andThen` blocks:

```afl
workflow DeployService(service: String, version: String) => (status: String) andThen {
    build = BuildImage(service = $.service, version = $.version)
    tests = RunTests(service = $.service, version = $.version)
    yield DeployService(status = "done")
}
```

- `$.param` references a workflow input
- `step.field` references a step's return value
- `yield` writes the workflow's return values

### Statement-level `andThen`

A step can have its own inline `andThen` block:

```afl
val = ValidateSchema(records = ext.records) andThen {
    tx = TransformRecords(records = val.valid_records)
}
```

### `andThen when` (conditional branching)

Branch on step outputs or workflow inputs. All matching cases run concurrently. Default case (`case _ =>`) is required.

**Workflow-level:**

```afl
workflow DeployService(...) => (...) andThen {
    build = BuildImage(...)
    risk = AnalyzeDeployRisk(...)
} andThen when {
    case risk.risk_level == "critical" => {
        yield DeployService(status = "blocked", detail = "...")
    }
    case tests.passed == false => {
        yield DeployService(status = "failed", detail = "...")
    }
    case _ => {
        dep = ApplyDeployment(...)
    }
}
```

**Statement-level (nested):**

```afl
health = CheckHealth(...) andThen when {
    case health.result.healthy == true => {
        yield DeployService(status = "success", detail = "...")
    }
    case _ => {
        triage = TriageIncident(...)
        rb = RollbackDeployment(...)
    }
}
```

### `andThen foreach` (parallel iteration)

```afl
workflow BatchDeploy(services: [String], version: String) => (status: String) andThen foreach svc in $.services {
    build = BuildImage(service = $.svc, version = $.version)
    dep = ApplyDeployment(service = $.svc, image_tag = build.image_tag, config = cfg.config)
}
```

### Operators

| Operator | Purpose | Example |
|----------|---------|---------|
| `++` | String concatenation | `"Deployed " ++ build.image_tag ++ " successfully"` |
| `==`, `!=` | Equality comparison | `risk.risk_level == "critical"` |
| `>`, `<`, `>=`, `<=` | Ordered comparison | `risk.risk_score > 80` |
| `&&`, `\|\|` | Boolean logic | `tests.passed == true && risk.risk_level != "critical"` |
| `!` | Boolean negation | `!tests.passed` |
| `+`, `-`, `*`, `/`, `%` | Arithmetic | `change_size * 2` |

### Collection literals

```afl
// Array
checks: [String] = ["readiness", "liveness", "connectivity"]

// Map
rename_map = #{"old_key": "new_key"}
```

## Running

```bash
# Compile
afl examples/devops-deploy/ffl/deploy.ffl -o deploy.json

# Syntax check only
afl examples/devops-deploy/ffl/deploy.ffl --check

# Tests
pytest examples/devops-deploy/ -v

# Agent entry points (require MongoDB)
PYTHONPATH=. python examples/devops-deploy/agent_registry.py   # RegistryRunner (recommended)
PYTHONPATH=. python examples/devops-deploy/agent.py            # AgentPoller (legacy)
```

## Handler categories

| Category | Handlers | Namespace |
|----------|----------|-----------|
| Build | `BuildImage`, `RunTests`, `AnalyzeDeployRisk` | `deploy.Build` |
| Deploy | `NormalizeConfig`, `ApplyDeployment`, `WaitForRollout` | `deploy.Deploy` |
| Monitor | `CheckHealth`, `TriageIncident` | `deploy.Monitor` |
| Rollback | `RollbackDeployment`, `VerifyRollback` | `deploy.Rollback` |

All handlers are deterministic (hashlib-based) with no external dependencies.
