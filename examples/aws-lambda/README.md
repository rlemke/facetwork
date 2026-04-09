# AWS Lambda + Step Functions Agent

An AWS serverless pipeline agent demonstrating FFL's **andThen chains**, **mixin composition**, and **foreach iteration** with real **boto3 calls** against a **LocalStack** Docker environment.

## What it does

This example demonstrates:
- **Real cloud service integration** via boto3 calls to LocalStack
- **andThen chains** for sequential Lambda deployment and invocation workflows
- **Call-time mixin composition** with Retry, Timeout, Tracing, and more
- **Cross-namespace composition** combining Lambda and Step Functions facets
- **Foreach iteration** for batch processing with per-iteration mixins
- **Dual-mode agent** supporting both AgentPoller and RegistryRunner

### Mixin Composition Patterns

```afl
// Call-time mixins: retry + timeout on invocation
invoked = aws.lambda.InvokeFunction(function_name = $.function_name,
    input_payload = $.input_payload) with Retry(maxAttempts = 3, backoffMs = 2000) with Timeout(seconds = 60)

// Tracing mixin on code update
updated = aws.lambda.UpdateFunctionCode(function_name = $.function_name,
    s3_bucket = $.s3_bucket,
    s3_key = $.s3_key) with Tracing(mode = "Active")

// Foreach with per-iteration mixins
workflow BatchProcessor(...) => (...) andThen foreach item in $.items {
    invoked = aws.lambda.InvokeFunction(function_name = $.function_name,
        input_payload = $.item.payload) with Retry(maxAttempts = 2, backoffMs = 500) with Timeout(seconds = 60)
}
```

### Execution flow

1. A workflow (e.g., `DeployAndInvoke`) receives inputs like function name and runtime
2. Each step creates an event task — the runtime pauses and waits for an agent
3. The agent picks up the task, makes a real boto3 call to LocalStack, and writes results back
4. Mixin facets (Retry, Timeout, Tracing, etc.) are composed onto each step
5. The workflow resumes, feeds outputs to the next step, and eventually yields final results

## Pipelines

### Pipeline 1: DeployAndInvoke

Pure andThen chain — creates a Lambda function, invokes it, and verifies.

```
CreateFunction  -->  InvokeFunction  -->  GetFunctionInfo
```

**Inputs**: `function_name`, `runtime`, `input_payload`
**Outputs**: `function_arn`, `status_code`, `response_payload`, `duration_ms`

### Pipeline 2: BlueGreenDeploy

andThen chain with call-time mixins — updates function code with tracing, invokes with retry+timeout.

```
UpdateFunctionCode + Tracing  -->  InvokeFunction + Retry + Timeout  -->  GetFunctionInfo + Timeout
```

**Inputs**: `function_name`, `s3_bucket`, `s3_key`, `input_payload`
**Outputs**: `function_arn`, `status_code`, `response_payload`, `state`

### Pipeline 3: StepFunctionPipeline

Cross-namespace andThen chain — creates a Lambda, creates a state machine, starts and monitors execution.

```
CreateFunction + Timeout  -->  CreateStateMachine  -->  StartExecution + Retry  -->  DescribeExecution + Timeout
```

**Inputs**: `function_name`, `state_machine_name`, `input_payload`
**Outputs**: `function_arn`, `state_machine_arn`, `execution_status`, `output_payload`

### Pipeline 4: BatchProcessor

Foreach iteration with per-iteration mixins — invokes a Lambda for each item in a batch.

```
foreach item:
    InvokeFunction + Retry + Timeout
```

**Inputs**: `function_name`, `items` (JSON array of `{payload}`)
**Outputs**: per-item `function_name`, `status_code`, `response_payload`, `duration_ms`

## Prerequisites

```bash
# From the repo root
source .venv/bin/activate
pip install -e ".[dev]"
pip install -r examples/aws-lambda/requirements.txt

# Start LocalStack
docker compose --profile localstack up -d
```

## Running

### Compile check

```bash
# Check all FFL sources (individual files need --library for cross-references)
for f in examples/aws-lambda/ffl/*.ffl; do
    python -m afl.cli "$f" --check 2>/dev/null && echo "OK: $f" || echo "NEEDS DEPS: $f"
done

# Compile the workflows with all dependencies
python -m afl.cli \
    --primary examples/aws-lambda/ffl/lambda_workflows.ffl \
    --library examples/aws-lambda/ffl/lambda_types.ffl \
    --library examples/aws-lambda/ffl/lambda_mixins.ffl \
    --library examples/aws-lambda/ffl/lambda_functions.ffl \
    --library examples/aws-lambda/ffl/lambda_stepfunctions.ffl \
    --check
```

### AgentPoller mode (default)

```bash
LOCALSTACK_URL=http://localhost:4566 PYTHONPATH=. python examples/aws-lambda/agent.py
```

### RegistryRunner mode (recommended for production)

```bash
AFL_USE_REGISTRY=1 LOCALSTACK_URL=http://localhost:4566 \
    PYTHONPATH=. python examples/aws-lambda/agent.py
```

### With MongoDB persistence

```bash
AFL_MONGODB_URL=mongodb://localhost:27017 AFL_MONGODB_DATABASE=afl \
    LOCALSTACK_URL=http://localhost:4566 \
    PYTHONPATH=. python examples/aws-lambda/agent.py
```

### With topic filtering

```bash
AFL_USE_REGISTRY=1 AFL_RUNNER_TOPICS=aws.lambda,aws.stepfunctions \
    LOCALSTACK_URL=http://localhost:4566 \
    PYTHONPATH=. python examples/aws-lambda/agent.py
```

### Run tests

```bash
# AWS Lambda-specific tests
pytest tests/test_aws_lambda_compilation.py tests/test_handler_dispatch_aws_lambda.py -v

# Full suite
pytest tests/ -v
```

## Mixin Facets

| Facet | Parameters | Purpose |
|-------|-----------|---------|
| `Retry` | `maxAttempts` (default 3), `backoffMs` (default 1000) | Retry failed invocations with backoff |
| `Timeout` | `seconds` (default 300) | Maximum execution time for a step |
| `DLQ` | `dlq_arn` | Dead letter queue for failed invocations |
| `VpcConfig` | `subnet_ids`, `security_group_ids` | VPC configuration for Lambda functions |
| `Tracing` | `mode` (default "Active") | X-Ray tracing mode |
| `MemorySize` | `mb` (default 128) | Memory size override |

### Implicit defaults

```afl
implicit defaultRetry = Retry(maxAttempts = 3, backoffMs = 1000)
implicit defaultTimeout = Timeout(seconds = 300)
implicit defaultTracing = Tracing(mode = "Active")
```

## Handler modules

| Module | Namespace | Event Facets | Description |
|--------|-----------|--------------|-------------|
| `lambda_handlers.py` | `aws.lambda` | CreateFunction, InvokeFunction, UpdateFunctionCode, DeleteFunction, ListFunctions, GetFunctionInfo, PublishLayer | Real boto3 Lambda operations via LocalStack |
| `stepfunctions_handlers.py` | `aws.stepfunctions` | CreateStateMachine, StartExecution, DescribeExecution, DeleteStateMachine, ListExecutions | Real boto3 Step Functions operations via LocalStack |

**Total**: 12 handler dispatch keys

## FFL source files

| File | Namespace(s) | Description |
|------|-------------|-------------|
| `lambda_types.ffl` | `aws.lambda.types` | 7 schemas (FunctionConfig, InvokeResult, FunctionInfo, LayerInfo, StateMachineConfig, ExecutionResult, ExecutionInfo) |
| `lambda_mixins.ffl` | `aws.lambda.mixins` | 6 mixin facets + 3 implicit defaults |
| `lambda_functions.ffl` | `aws.lambda` | 7 Lambda event facets |
| `lambda_stepfunctions.ffl` | `aws.stepfunctions` | 5 Step Functions event facets |
| `lambda_workflows.ffl` | `aws.lambda.workflows` | 4 workflows demonstrating andThen, mixins, foreach |

## Type schemas

| Schema | Namespace | Fields |
|--------|-----------|--------|
| `FunctionConfig` | `aws.lambda.types` | function_name, function_arn, runtime, handler, role_arn, memory_mb, timeout_seconds, code_size, last_modified |
| `InvokeResult` | `aws.lambda.types` | function_name, status_code, payload, executed_version, log_result, duration_ms |
| `FunctionInfo` | `aws.lambda.types` | function_name, function_arn, runtime, state, code_size, memory_mb, last_modified |
| `LayerInfo` | `aws.lambda.types` | layer_name, layer_arn, version, code_size, compatible_runtimes |
| `StateMachineConfig` | `aws.lambda.types` | state_machine_arn, state_machine_name, definition, role_arn, creation_date, status |
| `ExecutionResult` | `aws.lambda.types` | execution_arn, state_machine_arn, status, start_date, stop_date, input_payload, output_payload |
| `ExecutionInfo` | `aws.lambda.types` | execution_arn, state_machine_arn, status, start_date, name |

## Docker Integration

LocalStack runs as a Docker service (profile: `localstack`):

```bash
# Start LocalStack and the agent
docker compose --profile localstack up -d

# Check LocalStack health
curl http://localhost:4566/_localstack/health
```

The `localstack` service provides Lambda, Step Functions, S3, and IAM APIs. The `LAMBDA_EXECUTOR=local` setting uses the free tier executor.
