# Maven Artifact Runner — User Guide

> See also: [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- Exploring the **MavenArtifactRunner** execution model — running JVM programs as Maven artifacts
- Building workflows that launch **JVM subprocesses** from AFL event facets
- Integrating **Java/Scala/Kotlin handlers** packaged as Maven artifacts into AFL workflows

## What You'll Learn

1. How the MavenArtifactRunner resolves Maven artifacts and launches JVM subprocesses
2. How `RunMavenArtifact` and `RunMavenPlugin` event facets model JVM execution
3. How to register handlers with `mvn:` URI schemes
4. How to configure the runner with custom Maven repositories and JDK paths

## Step-by-Step Walkthrough

### 1. Define Schemas

Return types are schemas defined in a namespace:

```afl
namespace maven.types {
    schema ExecutionResult {
        exit_code: Long,
        success: Boolean,
        duration_ms: Long,
        stdout: String,
        stderr: String,
        artifact_path: String
    }

    schema PluginExecutionResult {
        plugin_key: String,
        goal: String,
        phase: String,
        exit_code: Long,
        success: Boolean,
        duration_ms: Long,
        output: String,
        artifact_path: String
    }
}
```

### 2. Define Event Facets

The `RunMavenArtifact` event facet models the core MavenArtifactRunner operation — resolving a Maven artifact and launching it as a JVM subprocess:

```afl
namespace maven.runner {
    use maven.types

    event facet RunMavenArtifact(step_id: String,
        group_id: String, artifact_id: String, version: String,
        classifier: String = "",
        entrypoint: String = "",
        jvm_args: String = "",
        workflow_id: String = "",
        runner_id: String = "") => (result: ExecutionResult)

    event facet RunMavenPlugin(workspace_path: String,
        plugin_group_id: String, plugin_artifact_id: String,
        plugin_version: String,
        goal: String,
        phase: String = "",
        jvm_args: String = "",
        properties: String = "") => (result: PluginExecutionResult)
}
```

`RunMavenArtifact` launches an executable JAR as a JVM subprocess. `RunMavenPlugin` invokes a Maven plugin goal (like `checkstyle:check` or `spotbugs:check`) within a workspace.

### 3. Use in Workflows

```afl
// Run an artifact
run = maven.runner.RunMavenArtifact(step_id = $.step_id,
    group_id = $.group_id, artifact_id = $.artifact_id,
    version = $.version)

// Run a plugin goal
checkstyle = maven.runner.RunMavenPlugin(workspace_path = $.workspace_path,
    plugin_group_id = "org.apache.maven.plugins",
    plugin_artifact_id = "maven-checkstyle-plugin",
    plugin_version = "3.3.1",
    goal = "check")
```

### 4. Running

```bash
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
afl --primary examples/maven/ffl/maven_runner.afl \
    --library examples/maven/ffl/maven_types.afl \
    --check

# Run the agent
PYTHONPATH=. python examples/maven/agent.py
```

## Key Concepts

### MavenArtifactRunner Execution Model

The MavenArtifactRunner bridges AFL workflows with JVM programs:

1. **Handler registration** — Register event facets with `mvn:` URI schemes:
   ```python
   runner.register_handler(
       facet_name="maven.runner.RunMavenArtifact",
       module_uri="mvn:com.example:maven-handler:1.0.0",
   )
   ```

2. **Artifact resolution** — When a task arrives, the runner parses the `mvn:groupId:artifactId:version[:classifier]` URI, downloads the JAR from the configured Maven repository (or uses the local cache), and stores it at `{cache_dir}/{groupPath}/{artifactId}/{version}/{name}.jar`.

3. **JVM subprocess** — The runner launches:
   - `java -jar artifact.jar <stepId>` (executable JAR)
   - `java -cp artifact.jar MainClass <stepId>` (with entrypoint)
   - JVM args from `metadata["jvm_args"]` are prepended
   - Environment variables: `AFL_STEP_ID`, `AFL_MONGODB_URL`, `AFL_MONGODB_DATABASE`

4. **Step continuation** — After the JVM program exits successfully (exit 0), the runner reads return values from MongoDB and calls `evaluator.continue_step()` + `evaluator.resume()` to advance the workflow.

This model is ideal when your event facet handlers are implemented in Java/Scala/Kotlin and packaged as Maven artifacts.

### Handler Dispatch Pattern

The runner handler module follows the dispatch adapter pattern:

```python
NAMESPACE = "maven.runner"

_DISPATCH = {
    f"{NAMESPACE}.RunMavenArtifact": _run_maven_artifact_handler,
    f"{NAMESPACE}.RunMavenPlugin": _run_maven_plugin_handler,
}

def handle(payload: dict) -> dict:
    handler = _DISPATCH[payload["_facet_name"]]
    return handler(payload)
```

Handlers are pure functions: receive a payload dict, return a result dict.

### How the JVM Program Reads and Returns Step Data

The MavenArtifactRunner passes step and database information to the JVM subprocess via environment variables:

| Variable | Description |
|---|---|
| `AFL_STEP_ID` | The step ID — use this to look up step parameters in MongoDB |
| `AFL_MONGODB_URL` | MongoDB connection string (e.g. `mongodb://localhost:27017`) |
| `AFL_MONGODB_DATABASE` | Database name (e.g. `afl`) |

The step ID is also passed as the first command-line argument.

**Reading parameters**: The JVM program connects to MongoDB using `AFL_MONGODB_URL` and `AFL_MONGODB_DATABASE`, looks up the step document by `AFL_STEP_ID`, and reads input parameters from `attributes.params`.

**Writing returns**: Before exiting, the program writes its return values to `attributes.returns` on the same step document. After the program exits with code 0, the runner reads those returns and uses them to continue the workflow.

```java
// Minimal Java example
public class MyHandler {
    public static void main(String[] args) {
        String stepId = System.getenv("AFL_STEP_ID");
        String mongoUrl = System.getenv("AFL_MONGODB_URL");
        String dbName = System.getenv("AFL_MONGODB_DATABASE");

        // Connect to MongoDB, read step params
        MongoClient client = MongoClients.create(mongoUrl);
        MongoDatabase db = client.getDatabase(dbName);
        Document step = db.getCollection("steps").find(eq("_id", stepId)).first();
        Document params = step.get("attributes", Document.class)
                              .get("params", Document.class);

        String input = params.get("input", Document.class).getString("value");

        // ... do work ...

        // Write returns back to the step document
        Document returns = new Document("output", new Document()
            .append("name", "output")
            .append("value", result)
            .append("type_hint", "String"));
        db.getCollection("steps").updateOne(
            eq("_id", stepId),
            set("attributes.returns", returns));
    }
}
```

If your JVM program does not need to read step parameters or return values — for example, a fire-and-forget task — it can ignore the environment variables entirely. The runner will still mark the step as completed when the process exits with code 0.

### Use the MavenArtifactRunner with real JVM handlers

1. Package your Java handler as an executable JAR
2. Publish it to a Maven repository (local Nexus, Artifactory, or Maven Central)
3. Register it with the runner:
   ```python
   runner.register_handler(
       facet_name="maven.runner.RunMavenArtifact",
       module_uri="mvn:com.mycompany:maven-handler:1.0.0",
       metadata={"jvm_args": ["-Xmx1g"]},
   )
   ```
4. Run the agent

## Next Steps

- **[jenkins](../jenkins/USER_GUIDE.md)** — see mixin composition example with Jenkins CI/CD
- **[aws-lambda](../aws-lambda/USER_GUIDE.md)** — combine mixins with real cloud API calls
- **[genomics](../genomics/USER_GUIDE.md)** — foreach fan-out patterns for parallel processing
