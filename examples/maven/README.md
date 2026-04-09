# Maven Artifact Runner

A Maven artifact runner agent demonstrating AFL's **MavenArtifactRunner** execution model — resolving Maven artifacts and launching JVM subprocesses.

## What it does

This example demonstrates:
- **MavenArtifactRunner** — JVM subprocess execution model (resolves Maven artifacts, launches `java -jar` subprocesses)
- **RunMavenArtifact** event facet for running executable JARs
- **RunMavenPlugin** event facet for running Maven plugin goals within a workspace

### Execution flow

1. An event task arrives with Maven coordinates (group ID, artifact ID, version)
2. The runner resolves the artifact from a Maven repository (or uses local cache)
3. The runner launches a JVM subprocess (`java -jar artifact.jar <stepId>`)
4. After the subprocess exits, the runner reads return values and advances the workflow

## Prerequisites

```bash
# From the repo root
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running

### Compile check

```bash
# Check runner AFL sources
afl --primary examples/maven/ffl/maven_runner.afl \
    --library examples/maven/ffl/maven_types.afl \
    --check
```

### Start the agent

```bash
PYTHONPATH=. python examples/maven/agent.py
```

With custom Maven repository and JDK:

```bash
AFL_MAVEN_REPOSITORY=https://nexus.example.com/repository/maven-public \
    AFL_JAVA_COMMAND=/usr/lib/jvm/java-17/bin/java \
    PYTHONPATH=. python examples/maven/agent.py
```

### With MongoDB persistence

```bash
AFL_MONGODB_URL=mongodb://localhost:27017 AFL_MONGODB_DATABASE=afl \
    PYTHONPATH=. python examples/maven/agent.py
```

### With topic filtering

```bash
AFL_RUNNER_TOPICS=maven.runner \
    PYTHONPATH=. python examples/maven/agent.py
```

### Run tests

```bash
# Maven-specific tests
pytest tests/test_maven_compilation.py tests/test_handler_dispatch_maven.py tests/test_maven_runner.py -v

# Full suite
pytest tests/ -v
```

## AFL source files

| File | Namespace | Description |
|------|-----------|-------------|
| `maven_types.afl` | `maven.types` | 2 schemas (ExecutionResult, PluginExecutionResult) |
| `maven_runner.afl` | `maven.runner` | 2 event facets for JVM execution (RunMavenArtifact, RunMavenPlugin) |

## Handler modules

| Module | Namespace | Event Facets | Description |
|--------|-----------|--------------|-------------|
| `runner_handlers.py` | `maven.runner` | RunMavenArtifact, RunMavenPlugin | JVM subprocess execution and Maven plugin goals |

## MavenArtifactRunner Execution Model

The MavenArtifactRunner bridges AFL workflows with JVM programs:

1. **Handler registration** — Register event facets with `mvn:` URI schemes:
   ```python
   runner.register_handler(
       facet_name="maven.runner.RunMavenArtifact",
       module_uri="mvn:com.example:maven-handler:1.0.0",
   )
   ```

2. **Artifact resolution** — When a task arrives, the runner parses the `mvn:groupId:artifactId:version[:classifier]` URI, downloads the JAR from the configured Maven repository (or uses the local cache).

3. **JVM subprocess** — The runner launches:
   - `java -jar artifact.jar <stepId>` (executable JAR)
   - `java -cp artifact.jar MainClass <stepId>` (with entrypoint)
   - JVM args from `metadata["jvm_args"]` are prepended
   - Environment variables: `AFL_STEP_ID`, `AFL_MONGODB_URL`, `AFL_MONGODB_DATABASE`

4. **Step continuation** — After the JVM program exits successfully (exit 0), the runner reads return values from MongoDB and calls `evaluator.continue_step()` + `evaluator.resume()` to advance the workflow.

This model is ideal when your event facet handlers are implemented in Java/Scala/Kotlin and packaged as Maven artifacts.
