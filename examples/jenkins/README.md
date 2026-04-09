# Jenkins CI/CD Pipeline Agent

A Jenkins-style CI/CD pipeline agent demonstrating FFL's **mixin composition** — small reusable facets (Retry, Timeout, Credentials, Notification, AgentLabel, Stash) that attach cross-cutting behaviors to event facets at both signature and call time.

## What it does

This example demonstrates:
- **Mixin facets** as reusable cross-cutting concerns (`with Timeout(minutes = 20)`)
- **Signature-level mixins** baked into facet definitions (`event facet GitCheckout(...) => (...) with Timeout(minutes = 10)`)
- **Call-time mixin composition** attaching one or more mixins per step
- **Implicit declarations** providing namespace-level defaults (`implicit defaultRetry = Retry(...)`)
- **Foreach iteration** for multi-module parallel builds
- **Dual-mode agent** supporting both AgentPoller and RegistryRunner

### Mixin Composition Patterns

```afl
// Single call-time mixin
tests = jenkins.test.RunTests(
    workspace_path = src.info.workspace_path,
    framework = "junit",
    suite = "unit") with Timeout(minutes = 15)

// Multiple call-time mixins
build = jenkins.build.MavenBuild(workspace_path = src.info.workspace_path,
    goals = "clean package -DskipTests") with Timeout(minutes = 20) with Retry(maxAttempts = 2, backoffSeconds = 60)

// Signature-level mixin (always applied)
event facet GitCheckout(repo: String, branch: String = "main",
    depth: Int = 0,
    submodules: Boolean = false) => (info: ScmInfo) with jenkins.mixins.Timeout(minutes = 10)

// Foreach with per-iteration mixins
workflow MultiModuleBuild(...) => (...) andThen foreach mod in $.modules {
    build = jenkins.build.GradleBuild(workspace_path = src.info.workspace_path,
        tasks = $.mod.build_task) with Timeout(minutes = 20) with Stash(name = $.mod.name ++ "-build", includes = $.mod.output_pattern)
}
```

### Execution flow

1. A pipeline workflow (e.g., `JavaMavenCI`) receives inputs like repo URL and branch
2. Each step creates an event task — the runtime pauses and waits for an agent
3. The Jenkins agent picks up the task, processes it, and writes results back
4. Mixin facets (Retry, Timeout, Credentials, etc.) are composed onto each step
5. The workflow resumes, feeds outputs to the next step, and eventually yields final results

## Pipelines

### Pipeline 1: JavaMavenCI

Standard Java Maven build-test-deploy cycle with call-time mixins.

```
GitCheckout + Credentials  -->  MavenBuild + Timeout + Retry  -->  RunTests + Timeout  -->  DeployToEnvironment + Credentials + Notification
```

**Inputs**: `repo`, `branch`, `environment`
**Outputs**: `deploy_url`, `test_passed`, `test_total`, `version`

### Pipeline 2: DockerK8sDeploy

Docker container build, security scan, push, and Kubernetes deployment.

```
GitCheckout + AgentLabel  -->  DockerBuild + Timeout + AgentLabel  -->  SecurityScan  -->  DockerPush + Credentials + Retry  -->  DeployToK8s + Credentials + Timeout  -->  SlackNotify
```

**Inputs**: `repo`, `branch`, `image_tag`, `registry_url`, `k8s_namespace`, `replicas`
**Outputs**: `deploy_url`, `image`, `healthy`

### Pipeline 3: MultiModuleBuild

Parallel per-module builds using `andThen foreach` with Stash mixins for workspace sharing.

```
foreach module:
    GitCheckout  -->  GradleBuild + Timeout + Stash  -->  RunTests + Timeout + Retry  -->  ArchiveArtifacts
```

**Inputs**: `repo`, `branch`, `modules` (JSON array of `{name, build_task, test_suite, output_pattern}`)
**Outputs**: per-module `artifact_path`, `module_name`, `test_passed`

### Pipeline 4: FullCIPipeline

Comprehensive pipeline with parallel quality gates and every mixin type.

```
GitCheckout + Credentials + Timeout
    --> MavenBuild + Timeout + Retry + AgentLabel
    --> [parallel] RunTests + Timeout | CodeQuality + Timeout + Credentials | SecurityScan + Timeout
    --> ArchiveArtifacts + Stash
    --> DeployToEnvironment + Credentials + Timeout + Notification
    --> SlackNotify
```

**Inputs**: `repo`, `branch`, `deploy_env`, `notify_channel`
**Outputs**: `deploy_url`, `version`, `test_coverage`, `quality_issues`, `security_critical`

## Prerequisites

```bash
# From the repo root
source .venv/bin/activate
pip install -e ".[dev]"
```

No additional dependencies are required — all handlers simulate Jenkins operations with realistic output structures.

## Running

### Compile check

```bash
# Check all FFL sources
for f in examples/jenkins/ffl/*.ffl; do
    afl "$f" --check && echo "OK: $f"
done

# Compile the pipelines with all dependencies
afl --primary examples/jenkins/ffl/jenkins_pipelines.ffl \
    --library examples/jenkins/ffl/jenkins_types.ffl \
    --library examples/jenkins/ffl/jenkins_mixins.ffl \
    --library examples/jenkins/ffl/jenkins_scm.ffl \
    --library examples/jenkins/ffl/jenkins_build.ffl \
    --library examples/jenkins/ffl/jenkins_test.ffl \
    --library examples/jenkins/ffl/jenkins_artifacts.ffl \
    --library examples/jenkins/ffl/jenkins_deploy.ffl \
    --library examples/jenkins/ffl/jenkins_notify.ffl \
    --check
```

### AgentPoller mode (default)

```bash
PYTHONPATH=. python examples/jenkins/agent.py
```

### RegistryRunner mode (recommended for production)

```bash
AFL_USE_REGISTRY=1 PYTHONPATH=. python examples/jenkins/agent.py
```

### With MongoDB persistence

```bash
AFL_MONGODB_URL=mongodb://localhost:27017 AFL_MONGODB_DATABASE=afl \
    PYTHONPATH=. python examples/jenkins/agent.py
```

### With topic filtering

```bash
AFL_USE_REGISTRY=1 AFL_RUNNER_TOPICS=jenkins.build,jenkins.test \
    PYTHONPATH=. python examples/jenkins/agent.py
```

### Run tests

```bash
# Jenkins-specific tests
pytest tests/test_jenkins_compilation.py tests/test_handler_dispatch_jenkins.py -v

# Full suite
pytest tests/ -v
```

## Mixin Facets

| Facet | Parameters | Purpose |
|-------|-----------|---------|
| `Retry` | `maxAttempts` (default 3), `backoffSeconds` (default 30) | Retry failed steps with configurable backoff |
| `Timeout` | `minutes` (default 30) | Maximum execution time for a step |
| `Credentials` | `credentialId`, `type` (default "token") | Attach authentication context (SSH, token, password) |
| `Notification` | `channel`, `onSuccess` (default true), `onFailure` (default true) | Notify a channel on completion or failure |
| `AgentLabel` | `label` (default "any") | Select which Jenkins agent/node to run on |
| `Stash` | `name`, `includes` (default "\*\*/\*"), `excludes` (default "") | Stash workspace files for sharing between stages |

### Implicit defaults

```afl
implicit defaultRetry = Retry(maxAttempts = 3, backoffSeconds = 30)
implicit defaultTimeout = Timeout(minutes = 30)
implicit defaultAgent = AgentLabel(label = "linux")
```

## Handler modules

| Module | Namespace | Event Facets | Description |
|--------|-----------|--------------|-------------|
| `scm_handlers.py` | `jenkins.scm` | GitCheckout, GitMerge | Git clone/checkout and branch merge |
| `build_handlers.py` | `jenkins.build` | MavenBuild, GradleBuild, NpmBuild, DockerBuild | Build tool integrations |
| `test_handlers.py` | `jenkins.test` | RunTests, CodeQuality, SecurityScan | Test execution and quality analysis |
| `artifact_handlers.py` | `jenkins.artifact` | ArchiveArtifacts, PublishToRegistry, DockerPush | Artifact archiving and registry publishing |
| `deploy_handlers.py` | `jenkins.deploy` | DeployToEnvironment, DeployToK8s, RollbackDeploy | Environment and Kubernetes deployment |
| `notify_handlers.py` | `jenkins.notify` | SlackNotify, EmailNotify | Slack and email notifications |

## FFL source files

| File | Namespace | Description |
|------|-----------|-------------|
| `jenkins_types.ffl` | `jenkins.types` | 7 schemas (ScmInfo, BuildResult, TestReport, QualityReport, Artifact, DeployResult, PipelineStatus) |
| `jenkins_mixins.ffl` | `jenkins.mixins` | 6 mixin facets + 3 implicit defaults |
| `jenkins_scm.ffl` | `jenkins.scm` | 2 SCM event facets (GitCheckout has signature-level mixin) |
| `jenkins_build.ffl` | `jenkins.build` | 4 build event facets |
| `jenkins_test.ffl` | `jenkins.test` | 3 test/quality event facets |
| `jenkins_artifacts.ffl` | `jenkins.artifact` | 3 artifact event facets |
| `jenkins_deploy.ffl` | `jenkins.deploy` | 3 deployment event facets |
| `jenkins_notify.ffl` | `jenkins.notify` | 2 notification event facets |
| `jenkins_pipelines.ffl` | `jenkins.pipeline` | 4 workflow pipelines demonstrating mixin composition |
