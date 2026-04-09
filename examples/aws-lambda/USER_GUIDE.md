# AWS Lambda + Step Functions — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building handlers that make **real API calls** to cloud services
- Combining **andThen chains**, **mixin composition**, and **foreach iteration** in one project
- Working with **LocalStack** for local AWS development
- Integrating **cross-namespace** workflows (Lambda + Step Functions)

## What You'll Learn

1. How to **encapsulate event facets** in composed facets that expose simple interfaces
2. How to write handlers that make real boto3 calls
3. How to configure handlers for LocalStack vs real AWS
4. How to combine mixins with cloud operations
5. How cross-namespace composition works (Lambda + Step Functions in one workflow)
6. How to use Docker Compose profiles for optional infrastructure

## Step-by-Step Walkthrough

### 1. Start LocalStack

```bash
docker compose --profile localstack up -d
```

This starts LocalStack with Lambda, Step Functions, S3, and IAM services on port 4566.

### 2. Verify LocalStack is Running

```bash
curl http://localhost:4566/_localstack/health
```

### 3. Understanding the FFL Structure

**Types** (`lambda_types.ffl`): 7 schemas for Lambda and Step Functions responses.

**Mixins** (`lambda_mixins.ffl`): 6 cross-cutting concerns (Retry, Timeout, DLQ, VpcConfig, Tracing, MemorySize) plus 3 implicit defaults.

**Event Facets**: 12 operations split across two namespaces:
- `aws.lambda` (7): CreateFunction, InvokeFunction, UpdateFunctionCode, DeleteFunction, ListFunctions, GetFunctionInfo, PublishLayer
- `aws.stepfunctions` (5): CreateStateMachine, StartExecution, DescribeExecution, DeleteStateMachine, ListExecutions

**Workflows** (`lambda_workflows.ffl`): 4 workflows demonstrating different composition patterns.

### 4. The Four Workflows

#### DeployAndInvoke — Facet Encapsulation

The raw Lambda event facets (`CreateFunction`, `InvokeFunction`, `GetFunctionInfo`) are low-level building blocks. Users shouldn't need to know about them. Instead, wrap them in a composed facet that exposes a simple, domain-focused interface:

```afl
// Composed facet: hides the three Lambda calls behind a simple interface.
// This is NOT an event facet — it doesn't pause for an agent.
// Its internal steps (CreateFunction, InvokeFunction, GetFunctionInfo)
// are the event facets that trigger agent execution.
facet DeployFunction(function_name: String, runtime: String = "python3.12",
    input_payload: String = "{}") => (function_arn: String,
        status_code: Long, response_payload: String,
        state: String) andThen {

    created = aws.lambda.CreateFunction(function_name = $.function_name,
        runtime = $.runtime)
    invoked = aws.lambda.InvokeFunction(function_name = $.function_name,
        input_payload = $.input_payload)
    info = aws.lambda.GetFunctionInfo(function_name = $.function_name)

    yield DeployFunction(
        function_arn = created.config.function_arn,
        status_code = invoked.result.status_code,
        response_payload = invoked.result.payload,
        state = info.info.state)
}

// Workflow: uses the composed facet — clean and simple
workflow DeployAndInvoke(function_name: String, runtime: String = "python3.12",
    input_payload: String = "{}") => (function_arn: String,
        status_code: Long, response_payload: String,
        duration_ms: Long) andThen {

    deployed = DeployFunction(function_name = $.function_name,
        runtime = $.runtime, input_payload = $.input_payload)

    yield DeployAndInvoke(
        function_arn = deployed.function_arn,
        status_code = deployed.status_code,
        response_payload = deployed.response_payload,
        duration_ms = 0)
}
```

**Why this matters**: The workflow calls `DeployFunction` as a single step. The user sees a clean interface — pass a function name, get back an ARN and response. The three underlying Lambda API calls are encapsulated inside the facet. You can change the implementation (add error handling, switch cloud providers) without changing the workflow.

This is the same pattern used in the [volcano-query](../volcano-query/USER_GUIDE.md) example, where `LoadVolcanoData` wraps `Cache` + `Download` behind a simple interface.

#### BlueGreenDeploy — andThen + Call-Time Mixins

```afl
updated = aws.lambda.UpdateFunctionCode(...) with Tracing(mode = "Active")
invoked = aws.lambda.InvokeFunction(...) with Retry(maxAttempts = 3, backoffMs = 2000) with Timeout(seconds = 60)
info = aws.lambda.GetFunctionInfo(...) with Timeout(seconds = 30)
```

Different mixins on each step: tracing on update, retry+timeout on invoke, timeout on verify.

#### StepFunctionPipeline — Cross-Namespace Composition

```afl
fn = aws.lambda.CreateFunction(...) with Timeout(seconds = 60)
sm = aws.stepfunctions.CreateStateMachine(...)
exec = aws.stepfunctions.StartExecution(state_machine_arn = sm.config.state_machine_arn, ...) with Retry(...)
result = aws.stepfunctions.DescribeExecution(execution_arn = exec.result.execution_arn) with Timeout(...)
```

Steps from two different namespaces in one workflow, with data flowing between them.

#### BatchProcessor — foreach + Per-Iteration Mixins

```afl
workflow BatchProcessor(function_name: String,
    items: Json) => (...) andThen foreach item in $.items {
    invoked = aws.lambda.InvokeFunction(function_name = $.function_name,
        input_payload = $.item.payload) with Retry(maxAttempts = 2) with Timeout(seconds = 60)
    yield BatchProcessor(...)
}
```

Each item in the batch gets its own invocation with retry and timeout.

### 5. How the Handlers Work

Handlers create boto3 clients pointing at LocalStack:

```python
LOCALSTACK_URL = os.environ.get("LOCALSTACK_URL", "http://localhost:4566")

def _lambda_client():
    return boto3.client("lambda",
        endpoint_url=LOCALSTACK_URL,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test")
```

For real AWS, unset `LOCALSTACK_URL` and configure proper credentials:

```bash
# LocalStack (default)
LOCALSTACK_URL=http://localhost:4566 PYTHONPATH=. python examples/aws-lambda/agent.py

# Real AWS (remove LOCALSTACK_URL, use AWS credentials)
AWS_ACCESS_KEY_ID=AKIA... AWS_SECRET_ACCESS_KEY=... AWS_REGION=us-east-1 \
    PYTHONPATH=. python examples/aws-lambda/agent.py
```

### 6. Running

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pip install -r examples/aws-lambda/requirements.txt

# Compile check
python -m afl.cli \
    --primary examples/aws-lambda/ffl/lambda_workflows.ffl \
    --library examples/aws-lambda/ffl/lambda_types.ffl \
    --library examples/aws-lambda/ffl/lambda_mixins.ffl \
    --library examples/aws-lambda/ffl/lambda_functions.ffl \
    --library examples/aws-lambda/ffl/lambda_stepfunctions.ffl \
    --check

# Start the agent
LOCALSTACK_URL=http://localhost:4566 PYTHONPATH=. python examples/aws-lambda/agent.py
```

## Key Concepts

### Facet Encapsulation

The most important pattern in this example: **wrap low-level event facets in composed facets** to give users a simple interface.

| Layer | What It Is | Who Sees It |
|-------|-----------|-------------|
| Event facets | `CreateFunction`, `InvokeFunction`, etc. | Agent developers |
| Composed facets | `DeployFunction`, `UpdateAndVerify`, etc. | Workflow authors |
| Workflows | `DeployAndInvoke`, `BlueGreenDeploy`, etc. | End users |

**Composed facets** (regular `facet` with `andThen`) act like functions — the runtime expands their steps inline. They don't pause for an agent; their internal event facet steps do. This lets you:

- **Hide complexity**: Users call one facet instead of three
- **Enforce patterns**: The composed facet always runs create-invoke-verify in order
- **Swap implementations**: Change the internal steps without changing the workflow
- **Reuse across workflows**: Multiple workflows can call the same composed facet

```afl
// Low-level: 3 separate event facets (agent developers write handlers for these)
created = aws.lambda.CreateFunction(...)
invoked = aws.lambda.InvokeFunction(...)
info = aws.lambda.GetFunctionInfo(...)

// Encapsulated: 1 composed facet (workflow authors use this)
deployed = DeployFunction(function_name = $.function_name, runtime = $.runtime)
```

### Real API Calls vs Simulated Handlers

| Example | Handler Approach |
|---------|-----------------|
| hello-agent | Inline lambda |
| genomics | Simulated with realistic output |
| jenkins | Simulated with realistic output |
| **aws-lambda** | **Real boto3 calls to LocalStack** |
| osm-geocoder | Real HTTP calls to Nominatim |

This example bridges the gap between simulated handlers and production cloud integration.

### LocalStack Configuration

The `LOCALSTACK_URL` environment variable controls where boto3 clients connect:

| Variable | Value | Effect |
|----------|-------|--------|
| Set | `http://localhost:4566` | Calls LocalStack |
| Unset | (default) | Falls back to `http://localhost:4566` |
| Custom | `http://localstack:4566` | Docker internal DNS |

### Docker Compose Profile

The LocalStack services use the `localstack` profile — they don't start by default:

```bash
# Start only LocalStack services
docker compose --profile localstack up -d

# Stop LocalStack services
docker compose --profile localstack down
```

## Adapting for Your Use Case

### Wrap Lambda operations for your domain

Create composed facets that hide Lambda complexity behind domain-specific interfaces:

```afl
namespace myapp.functions {
    use aws.lambda.types
    use aws.lambda.mixins

    // Encapsulate: create + configure + verify
    facet ProvisionFunction(service_name: String,
        environment: String = "staging") => (function_arn: String,
            ready: Boolean) andThen {

        fn = aws.lambda.CreateFunction(function_name = $.service_name ++ "-" ++ $.environment,
            runtime = "python3.12",
            memory_mb = 256,
            timeout_seconds = 60) with Timeout(seconds = 120)

        // Verify it's ready
        info = aws.lambda.GetFunctionInfo(function_name = $.service_name ++ "-" ++ $.environment) with Retry(maxAttempts = 3, backoffMs = 2000)

        yield ProvisionFunction(
            function_arn = fn.config.function_arn,
            ready = true)
    }

    // Encapsulate: invoke + parse response
    facet CallService(service_name: String, environment: String = "staging",
        request_body: String = "{}") => (status_code: Long,
            response_body: String) andThen {

        result = aws.lambda.InvokeFunction(function_name = $.service_name ++ "-" ++ $.environment,
            input_payload = $.request_body) with Retry(maxAttempts = 2) with Timeout(seconds = 30)

        yield CallService(
            status_code = result.result.status_code,
            response_body = result.result.payload)
    }

    // Workflow: uses the simple composed facets
    workflow DeployAndTest(service_name: String) => (function_arn: String,
        test_passed: Boolean) andThen {

        provisioned = ProvisionFunction(service_name = $.service_name, environment = "test")
        test_result = CallService(service_name = $.service_name, environment = "test",
            request_body = "{\"test\": true}")

        yield DeployAndTest(
            function_arn = provisioned.function_arn,
            test_passed = true)
    }
}
```

Users call `ProvisionFunction` and `CallService` — they never need to know about `CreateFunction`, `InvokeFunction`, or `GetFunctionInfo`.

### Add a new AWS service

1. Create a new event facet file (e.g., `lambda_s3.ffl`):
   ```afl
   namespace aws.s3 {
       use aws.lambda.types
       event facet CreateBucket(bucket_name: String) => (config: BucketConfig)
       event facet PutObject(bucket_name: String, key: String, body: String) => (result: PutResult)
   }
   ```

2. Create a handler module (`handlers/s3_handlers.py`) with boto3 S3 client
3. Wire into `handlers/__init__.py`
4. Add to workflow compositions

### Switch from LocalStack to real AWS

Modify the handler to use environment-based configuration:

```python
def _lambda_client():
    kwargs = {"region_name": os.environ.get("AWS_REGION", "us-east-1")}
    localstack_url = os.environ.get("LOCALSTACK_URL")
    if localstack_url:
        kwargs["endpoint_url"] = localstack_url
        kwargs["aws_access_key_id"] = "test"
        kwargs["aws_secret_access_key"] = "test"
    return boto3.client("lambda", **kwargs)
```

## Next Steps

- **[jenkins](../jenkins/USER_GUIDE.md)** — more mixin composition patterns
- **[genomics](../genomics/USER_GUIDE.md)** — factory-built handlers for large-scale fan-out
- **[osm-geocoder](../osm-geocoder/USER_GUIDE.md)** — production-scale agent with 580+ handlers
