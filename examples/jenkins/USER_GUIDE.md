# Jenkins CI/CD Pipelines — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- Adding **cross-cutting concerns** (retry, timeout, credentials, notifications) to workflow steps
- Learning FFL's **mixin composition** pattern (`with`)
- Building workflows where **reusable behaviors** need to be attached flexibly per-step
- Designing **implicit defaults** for namespace-wide configuration

## What You'll Learn

1. How mixin facets define reusable cross-cutting behaviors
2. How to attach mixins at **call time** (per-step) with `with`
3. How to attach mixins at **signature level** (always applied)
4. How `implicit` declarations provide namespace-level defaults
5. How to combine foreach iteration with per-iteration mixins
6. How to **encapsulate complex pipelines** behind simple composed facets with baked-in mixins

## Step-by-Step Walkthrough

### 1. Define Mixin Facets

Mixins are regular facets (not event facets) that represent cross-cutting concerns:

```afl
namespace jenkins.mixins {
    facet Retry(maxAttempts: Int = 3, backoffSeconds: Int = 30)
    facet Timeout(minutes: Int = 30)
    facet Credentials(credentialId: String, type: String = "token")
    facet Notification(channel: String, onSuccess: Boolean = true, onFailure: Boolean = true)
    facet AgentLabel(label: String = "any")
    facet Stash(name: String, includes: String = "**/*", excludes: String = "")
}
```

These carry configuration data — the runtime and agent can inspect them when processing a step.

### 2. Attach Mixins at Call Time

Add one or more mixins to any step:

```afl
// Single mixin
tests = RunTests(workspace_path = src.info.workspace_path,
    framework = "junit",
    suite = "unit") with Timeout(minutes = 15)

// Multiple mixins
build = MavenBuild(workspace_path = src.info.workspace_path,
    goals = "clean package") with Timeout(minutes = 20) with Retry(maxAttempts = 2, backoffSeconds = 60)
```

**Grammar rule**: The `) with` must be on the same line as the closing `)`. Newlines between `)` and `with` cause parse errors.

### 3. Attach Mixins at Signature Level

Bake a mixin into a facet definition so it's always applied:

```afl
event facet GitCheckout(repo: String, branch: String = "main",
    depth: Int = 0,
    submodules: Boolean = false) => (info: ScmInfo) with jenkins.mixins.Timeout(minutes = 10)
```

**Grammar rule**: The return clause (`=> (...)`) must come before `with` in the signature.

### 4. Define Implicit Defaults

Set namespace-wide default values:

```afl
implicit defaultRetry = Retry(maxAttempts = 3, backoffSeconds = 30)
implicit defaultTimeout = Timeout(minutes = 30)
implicit defaultAgent = AgentLabel(label = "linux")
```

### 5. Combine with foreach

Attach mixins to steps inside foreach iteration:

```afl
workflow MultiModuleBuild(repo: String, branch: String = "main",
    modules: Json) => (...) andThen foreach mod in $.modules {

    src = jenkins.scm.GitCheckout(repo = $.repo, branch = $.branch)

    build = jenkins.build.GradleBuild(workspace_path = src.info.workspace_path,
        tasks = $.mod.build_task) with Timeout(minutes = 20) with Stash(name = $.mod.name ++ "-build", includes = $.mod.output_pattern)

    yield MultiModuleBuild(...)
}
```

Note: Mixin arguments can use dynamic expressions like `$.mod.name ++ "-build"`.

### 6. Running

```bash
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
for f in examples/jenkins/ffl/*.ffl; do
    python -m afl.cli "$f" --check && echo "OK: $f"
done

# Compile workflows with all dependencies
python -m afl.cli \
    --primary examples/jenkins/ffl/jenkins_pipelines.ffl \
    --library examples/jenkins/ffl/jenkins_types.ffl \
    --library examples/jenkins/ffl/jenkins_mixins.ffl \
    --library examples/jenkins/ffl/jenkins_scm.ffl \
    --library examples/jenkins/ffl/jenkins_build.ffl \
    --library examples/jenkins/ffl/jenkins_test.ffl \
    --library examples/jenkins/ffl/jenkins_artifacts.ffl \
    --library examples/jenkins/ffl/jenkins_deploy.ffl \
    --library examples/jenkins/ffl/jenkins_notify.ffl \
    --check

# Run the agent
PYTHONPATH=. python examples/jenkins/agent.py
```

## Key Concepts

### Facet Encapsulation — Hiding Pipeline Complexity

The `JavaMavenCI` workflow has four steps with credentials, timeouts, and retries scattered across each one. A workflow author shouldn't need to know about all that. Instead, **wrap the pipeline in a composed facet** that bakes in the mixins and exposes a simple interface:

```afl
namespace jenkins.library {
    use jenkins.types
    use jenkins.mixins

    // Composed facet: encapsulates checkout + build + test with all mixins baked in.
    // Users never see Credentials, Timeout, or Retry — they just call BuildAndTest.
    facet BuildAndTest(repo: String, branch: String = "main",
        goals: String = "clean package",
        test_suite: String = "unit") => (artifact_path: String,
            version: String, test_passed: Long,
            test_total: Long) andThen {

        src = jenkins.scm.GitCheckout(repo = $.repo,
            branch = $.branch) with Credentials(credentialId = "git-ssh-key", type = "ssh")

        build = jenkins.build.MavenBuild(workspace_path = src.info.workspace_path,
            goals = $.goals) with Timeout(minutes = 20) with Retry(maxAttempts = 2, backoffSeconds = 60)

        tests = jenkins.test.RunTests(workspace_path = src.info.workspace_path,
            framework = "junit",
            suite = $.test_suite) with Timeout(minutes = 15)

        yield BuildAndTest(
            artifact_path = build.result.artifact_path,
            version = build.result.version,
            test_passed = tests.report.passed,
            test_total = tests.report.total)
    }

    // Composed facet: encapsulates deploy + notification with credentials baked in
    facet DeployWithNotification(artifact_path: String, environment: String,
        version: String,
        notify_channel: String = "#deployments") => (deploy_url: String,
            healthy: Boolean) andThen {

        deploy = jenkins.deploy.DeployToEnvironment(artifact_path = $.artifact_path,
            environment = $.environment,
            version = $.version) with Credentials(credentialId = "deploy-token") with Notification(channel = $.notify_channel)

        yield DeployWithNotification(
            deploy_url = deploy.result.url,
            healthy = deploy.result.healthy)
    }

    // Workflow: clean and simple — two composed facets instead of four raw steps + mixins
    workflow SimpleMavenCI(repo: String, branch: String = "main",
        environment: String = "staging") => (deploy_url: String,
            version: String, test_passed: Long) andThen {

        built = BuildAndTest(repo = $.repo, branch = $.branch)

        deployed = DeployWithNotification(artifact_path = built.artifact_path,
            environment = $.environment, version = built.version)

        yield SimpleMavenCI(
            deploy_url = deployed.deploy_url,
            version = built.version,
            test_passed = built.test_passed)
    }
}
```

**Why this matters:**

| Layer | What the User Sees | What's Hidden |
|-------|-------------------|---------------|
| Event facets | `GitCheckout`, `MavenBuild`, `RunTests`, `DeployToEnvironment` | Handler implementations |
| Composed facets | `BuildAndTest(repo, branch)`, `DeployWithNotification(artifact, env)` | Mixins, credential IDs, timeout values, retry policies |
| Workflows | `SimpleMavenCI(repo, environment)` | The entire pipeline structure |

This is the **library facet** pattern — composed facets become reusable building blocks that teams share. The CI team defines `BuildAndTest` with the right mixins; application teams call it without knowing about retry policies or credential management.

### Mixin Composition Patterns

| Pattern | Syntax | When to Use |
|---------|--------|-------------|
| Call-time single | `step = F(...) with M(...)` | One behavior for one step |
| Call-time multiple | `step = F(...) with M1(...) with M2(...)` | Multiple behaviors for one step |
| Signature-level | `event facet F(...) => (...) with M(...)` | Always-on behavior for a facet |
| Implicit default | `implicit name = M(...)` | Namespace-wide default |

### FFL Grammar Constraints

These are critical when writing FFL with mixins:

1. **`) with` on same line**: `step = F(x = 1) with M()` — no newline between `)` and `with`
2. **`) =>` on same line**: `event facet F(x: String) => (y: String)` — no newline between `)` and `=>`
3. **Return before mixin in signature**: `=> (result: Type) with M()` — return clause first
4. **Reserved keywords**: `script` and `namespace` cannot be used as parameter names

### Handler Dispatch Pattern

Each handler module follows the same dispatch adapter:

```python
NAMESPACE = "jenkins.build"

_DISPATCH = {
    f"{NAMESPACE}.MavenBuild": _maven_build_handler,
    f"{NAMESPACE}.GradleBuild": _gradle_build_handler,
    # ...
}

def handle(payload: dict) -> dict:
    handler = _DISPATCH[payload["_facet_name"]]
    return handler(payload)
```

Handlers are pure functions: receive a payload dict, return a result dict. The mixin data is available in the payload for handlers that need to inspect it.

## Adapting for Your Use Case

### Define your own mixins

```afl
namespace myapp.mixins {
    facet RateLimit(requests_per_second: Int = 10)
    facet Cache(ttl_seconds: Int = 300)
    facet Auth(provider: String, scope: String = "read")

    implicit defaultRateLimit = RateLimit(requests_per_second = 10)
}
```

### Apply mixins to your event facets

```afl
namespace myapp.api {
    use myapp.mixins

    event facet FetchUser(user_id: String) => (user: UserInfo)
    event facet UpdateUser(user_id: String, data: String) => (user: UserInfo)

    workflow GetAndUpdateUser(user_id: String, new_data: String) => (...) andThen {
        current = FetchUser(user_id = $.user_id) with Cache(ttl_seconds = 60) with Auth(provider = "oauth")
        updated = UpdateUser(user_id = $.user_id,
            data = $.new_data) with RateLimit(requests_per_second = 5) with Auth(provider = "oauth", scope = "write")
        yield GetAndUpdateUser(...)
    }
}
```

### Add a new handler module

1. Create `handlers/my_handlers.py` with `NAMESPACE`, `_DISPATCH`, `handle()`, `register_handlers()`, and `register_my_handlers()`
2. Wire it into `handlers/__init__.py`
3. Add the corresponding FFL event facet file

## Next Steps

- **[aws-lambda](../aws-lambda/USER_GUIDE.md)** — combine mixins with real cloud API calls
- **[genomics](../genomics/USER_GUIDE.md)** — foreach fan-out patterns for parallel processing
- **[osm-geocoder](../osm-geocoder/USER_GUIDE.md)** — see how a large-scale agent organizes hundreds of handlers
