# FFL Tutorial -- Facetwork Flow Language

This tutorial walks through FFL (Facetwork Flow Language) in eight progressive
parts.  Each part introduces a small set of constructs, shows complete
examples, and explains how the pieces fit together.  By the end you will be
able to write multi-step workflows with schemas, event facets, composition,
parallel iteration, and expressions.

Prerequisites: Python 3.11+ with the `afl` package installed
(`pip install -e .` from the repository root).

---

## Part 1 -- Hello World

### Your first facet

A **facet** is a typed structure with named parameters.  It is the fundamental
building block in FFL.

```afl
facet Hello(name: String)
```

This declares a facet called `Hello` with a single `String` parameter.  Facets
do not execute anything on their own -- they describe data shapes that the
runtime can create, pass around, and compose.

### Your first workflow

A **workflow** is a facet that serves as an entry point for execution.  It can
declare a return clause with `=>` to specify its output parameters.

```afl
workflow Greet(name: String) => (greeting: String)
```

Workflows are what you submit to the runtime.  When a workflow runs, the
runtime creates a root step for it, evaluates its parameters, and (if it has an
`andThen` block) executes its inner steps.

### Adding logic with andThen

To make a workflow do something, attach an `andThen` block.  Inside the block
you create **steps** (variable assignments) and a **yield** statement that
produces the workflow output.

```afl
facet Hello(name: String)

workflow Greet(name: String) => (greeting: String) andThen {
    h = Hello(name = $.name)
    yield Greet(greeting = h.name)
}
```

Key points:

- `$.name` refers to the parameter `name` on the enclosing step (the workflow
  itself).
- `h = Hello(name = $.name)` creates a child step named `h`.
- `yield Greet(greeting = h.name)` merges the result back into the workflow
  output.  The yield must reference the facet of the enclosing step.

### Compiling and checking

Save the file as `hello.ffl` and compile it:

```bash
# Compile to JSON workflow definition
afl hello.ffl -o hello.json

# Syntax check only (no output file)
afl hello.ffl --check
```

The `--check` flag parses and validates the file without emitting JSON.  Use it
for quick feedback while editing.

---

## Part 2 -- Event Facets

### What is an event facet?

An **event facet** is a facet prefixed with the `event` keyword.  When the
runtime encounters an event facet during execution, it **pauses** the step and
creates a **task** in the task queue.  An external agent picks up the task,
performs work, and submits the result.  Only then does the step resume.

```afl
event facet AddOne(input: Long) => (output: Long)
```

This declares an event facet that accepts a `Long` and returns a `Long`.  The
runtime will not compute `output` itself -- it waits for an agent to provide
the value.

### Using an event facet in a workflow

```afl
event facet AddOne(input: Long) => (output: Long)

workflow Increment(value: Long = 0) => (result: Long) andThen {
    added = AddOne(input = $.value)
    yield Increment(result = added.output)
}
```

When `Increment` runs:

1. The runtime creates a step for `added = AddOne(input = 0)`.
2. Because `AddOne` is an event facet, the step pauses at the `EventTransmit`
   state and a task is placed on the queue.
3. An agent (such as a RegistryRunner handler or a standalone AgentPoller)
   picks up the task, computes `output = input + 1`, and submits the result.
4. The step resumes, `added.output` becomes `1`, and the yield completes the
   workflow with `result = 1`.

### A two-step example with dependencies

Steps within an `andThen` block execute in parallel when they have no
data dependencies.  Dependencies are expressed through step references.

```afl
event facet Multiply(a: Long, b: Long) => (product: Long)
event facet AddOne(input: Long) => (output: Long)

workflow MultiplyThenAdd(x: Long = 3, y: Long = 4) => (result: Long) andThen {
    mul = Multiply(a = $.x, b = $.y)
    inc = AddOne(input = mul.product)
    yield MultiplyThenAdd(result = inc.output)
}
```

Here `inc` depends on `mul.product`, so `inc` will not start until an agent
completes the `Multiply` task.  The runtime handles this ordering
automatically.

---

## Part 3 -- Namespaces and Schemas

### Namespaces

A **namespace** groups related facets, workflows, schemas, and imports into a
named scope.  Namespaces use dot-separated qualified names.

```afl
namespace geo {
    facet Geocode(address: String) => (lat: Double, lon: Double)
}
```

Everything inside the braces belongs to the `geo` namespace.  From outside, you
reference it as `geo.Geocode`.

### Schemas

A **schema** defines a named typed structure that can be used as a parameter or
return type.  Schemas must be defined inside a namespace.

```afl
namespace geo {
    schema Location {
        lat: Double,
        lon: Double
    }

    facet Geocode(address: String) => (location: Location)
}
```

Inside the same namespace, you can reference `Location` by its simple name.
The schema fields use the same type system as facet parameters: `String`,
`Long`, `Int`, `Double`, `Boolean`, `Json`, or another schema name.

### Cross-namespace references

When a facet in one namespace needs to reference a schema from another
namespace, use a fully-qualified name or a `uses` import.

**Fully-qualified reference:**

```afl
namespace geo {
    schema Location {
        lat: Double,
        lon: Double
    }
}

namespace app {
    workflow FindPlace(address: String) => (result: geo.Location) andThen {
        g = geo.Geocode(address = $.address)
        yield FindPlace(result = g.location)
    }
}
```

**Using `uses` import:**

```afl
namespace geo {
    schema Location {
        lat: Double,
        lon: Double
    }
}

namespace app {
    uses geo

    // Now Location resolves to geo.Location
    workflow FindPlace(address: String) => (result: Location) andThen {
        g = Geocode(address = $.address)
        yield FindPlace(result = g.location)
    }
}
```

The `uses` declaration makes all names from the imported namespace available
without qualification, as long as there is no ambiguity.

### Real-world example: OSM Geocoder

The `examples/osm-geocoder/ffl/geocoder.ffl` file in this repository shows
schemas and event facets working together:

```afl
namespace osm.geocode {
    schema GeoCoordinate {
        lat: String,
        lon: String,
        display_name: String
    }

    event facet Geocode(address: String) => (result: GeoCoordinate)

    workflow GeocodeAddress(address: String) => (location: GeoCoordinate) andThen {
        geo = Geocode(address = $.address)
        yield GeocodeAddress(location = geo.result)
    }
}
```

---

## Part 4 -- Composition

### Mixins

Mixins let you compose facets together using the `with` keyword.  A mixin
attaches additional facet behavior to a step.

**Declaring a facet with mixin slots:**

```afl
facet Retry(maxAttempts: Long = 3)
facet Timeout(seconds: Long = 30)

facet Job(input: String) with Retry(maxAttempts = 3)
```

The facet `Job` is composed with `Retry`.  At runtime, the mixin's parameters
are evaluated and attached to the step alongside the primary facet.

**Mixin alias with `as`:**

When a step uses a mixin, you can give it an alias so its attributes are
accessible under a specific name:

```afl
workflow Process(input: String) => (output: String) andThen {
    job = RunTask(input = $.input) with User(name = "admin") as user
    yield Process(output = job.output)
}
```

Here `user` becomes a named reference for the `User` mixin on the `job` step.
A yield can reference the mixin separately:

```afl
    yield RunTask(output = job.output)
    yield User(name = user.name)
```

### Implicit facets

An **implicit** declaration provides a default facet value that is available
throughout the scope.

```afl
facet User(name: String, email: String)
implicit currentUser = User(name = "system", email = "system@example.com")
```

Implicit facets are useful for injecting configuration or context values that
many steps need without repeating the arguments.

### Multiple andThen blocks

Facets and workflows can have more than one `andThen` block.  Each block
executes concurrently and independently.

```afl
facet LogEntry(message: String)
event facet SendEmail(to: String, body: String) => (sent: Boolean)
event facet WriteLog(entry: String) => (written: Boolean)

workflow Notify(to: String, message: String) => (emailed: Boolean, logged: Boolean)
    andThen {
        e = SendEmail(to = $.to, body = $.message)
        yield Notify(emailed = e.sent)
    }
    andThen {
        w = WriteLog(entry = $.message)
        yield Notify(logged = w.written)
    }
```

The two `andThen` blocks run in parallel.  Each yields into a different output
parameter.  The workflow completes when both blocks finish.

---

## Part 5 -- Foreach and Collections

### andThen foreach

The `andThen foreach` construct iterates over a collection and creates one
set of steps per element.  All iterations execute in parallel.

```afl
facet Region(name: String)
event facet ProcessRegion(region: String) => (result: String)

workflow ProcessAllRegions(regions: Json) => (results: Json)
    andThen foreach r in $.regions {
        processed = ProcessRegion(region = r.name)
        yield ProcessAllRegions(results = processed.result)
    }
```

Key points:

- `foreach r in $.regions` iterates over the `regions` parameter (a JSON
  array).
- Each element is bound to `r`, and the block body is instantiated once per
  element.
- All iterations are independent and execute in parallel.
- Each iteration's yield contributes to the collected `results` output.

### Batch geocoding example

The OSM geocoder example uses `foreach` for batch processing:

```afl
namespace osm.geocode {
    schema GeoCoordinate {
        lat: String,
        lon: String,
        display_name: String
    }

    event facet Geocode(address: String) => (result: GeoCoordinate)

    workflow GeocodeAll(addresses: Json) => (locations: Json)
        andThen foreach addr in $.addresses {
            geo = Geocode(address = addr.value)
            yield GeocodeAll(locations = geo.result)
        }
}
```

If you submit this workflow with a list of 100 addresses, the runtime creates
100 independent `Geocode` tasks.  Agents can process them concurrently, and the
workflow completes when all tasks are done.

---

## Part 6 -- Expressions and Operators

FFL supports expressions in step arguments, including arithmetic, string
concatenation, collection literals, and indexing.

### Arithmetic operators

The standard arithmetic operators work on numeric types (`Long`, `Int`,
`Double`):

```afl
facet Value(input: Long)

workflow Math(x: Long = 10, y: Long = 3) => (sum: Long, diff: Long, prod: Long, quot: Long, rem: Long)
    andThen {
        a = Value(input = $.x + $.y)
        b = Value(input = $.x - $.y)
        c = Value(input = $.x * $.y)
        d = Value(input = $.x / $.y)
        e = Value(input = $.x % $.y)
        yield Math(sum = a.input, diff = b.input, prod = c.input, quot = d.input, rem = e.input)
    }
```

Operator precedence follows standard rules: `*`, `/`, `%` bind tighter than
`+`, `-`, which bind tighter than `++` (concatenation).

### String concatenation with ++

The `++` operator concatenates strings:

```afl
facet Label(text: String)

workflow MakeLabel(first: String = "Hello", second: String = "World") => (label: String)
    andThen {
        l = Label(text = $.first ++ " " ++ $.second)
        yield MakeLabel(label = l.text)
    }
```

You can mix `++` with arithmetic in the same expression.  Arithmetic is
evaluated first due to higher precedence:

```afl
    s = Label(text = "Width: " ++ $.width * 2 ++ "px")
```

### Array literals

Arrays are written with square brackets:

```afl
facet Data(items: Json)

workflow Example() => (output: Json) andThen {
    d = Data(items = [1, 2, 3])
    yield Example(output = d.items)
}
```

### Map literals

Maps use the `#{}` syntax with string keys:

```afl
facet Config(settings: Json)

workflow Example() => (output: Json) andThen {
    c = Config(settings = #{"host": "localhost", "port": 8080})
    yield Example(output = c.settings)
}
```

### Indexing

Access array elements by index with square brackets:

```afl
    first = Data(items = arr.items[0])
```

### Grouping with parentheses

Use parentheses to override operator precedence:

```afl
    total = Value(input = ($.a + $.b) * $.c)
```

---

## Part 7 -- Putting It Together

This part combines namespaces, schemas, event facets, composition, and
multi-step logic into a complete workflow.

### A data processing pipeline

```afl
namespace pipeline {

    // --- Schemas ---

    schema DataSource {
        url: String,
        format: String
    }

    schema ProcessedResult {
        record_count: Long,
        output_path: String,
        status: String
    }

    // --- Event facets (handled by agents) ---

    event facet FetchData(source: DataSource) => (raw_path: String, size: Long)

    event facet TransformData(
        input_path: String,
        format: String
    ) => (result: ProcessedResult)

    event facet PublishReport(
        input_path: String,
        title: String
    ) => (report_url: String)

    // --- Regular facets ---

    facet Config(timeout: Long = 30, retries: Long = 3)

    // --- Workflow ---

    workflow RunPipeline(
        url: String = "https://data.example.com/feed.csv",
        format: String = "csv",
        title: String = "Daily Report"
    ) => (
        report_url: String,
        records: Long
    ) andThen {
        // Step 1: Fetch the raw data
        src = DataSource(url = $.url, format = $.format)
        fetched = FetchData(source = src)

        // Step 2: Transform the data
        transformed = TransformData(
            input_path = fetched.raw_path,
            format = $.format
        )

        // Step 3: Publish the report
        report = PublishReport(
            input_path = transformed.result.output_path,
            title = $.title
        )

        yield RunPipeline(
            report_url = report.report_url,
            records = transformed.result.record_count
        )
    }
}
```

This pipeline has three sequential stages.  Each event facet pauses for an
agent.  The dependency chain (fetched -> transformed -> report) is expressed
through step references, and the runtime handles the ordering.

### Parallel extraction pattern

When steps are independent, they run in parallel.  This pattern is common in
the OSM geocoder examples:

```afl
namespace analysis {

    uses pipeline

    event facet ExtractMetrics(input_path: String) => (metrics: Json)
    event facet ExtractSummary(input_path: String) => (summary: Json)

    workflow Analyze(input_path: String) => (metrics: Json, summary: Json) andThen {
        // These two steps have no mutual dependencies -- they run in parallel
        m = ExtractMetrics(input_path = $.input_path)
        s = ExtractSummary(input_path = $.input_path)

        yield Analyze(metrics = m.metrics, summary = s.summary)
    }
}
```

### Where to go from here

The `examples/osm-geocoder/` directory contains a real-world FFL project with
over 40 FFL files and hundreds of handler implementations.  Notable files:

| File | What it demonstrates |
|------|---------------------|
| `afl/geocoder.ffl` | Schemas, event facets, `andThen foreach` |
| `afl/osmtypes.ffl` | Shared schema definitions across namespaces |
| `afl/osmworkflows_composed.ffl` | Multi-stage pipeline composition patterns |
| `afl/osmcontinents.ffl` | Parameterized regional workflows |
| `afl/osmroutes.ffl` | Route extraction with parallel steps |

To run a workflow, compile it and submit it to the runtime:

```bash
# Compile
afl examples/osm-geocoder/ffl/geocoder.ffl -o geocoder.json

# Start the runtime runner (requires MongoDB)
python -m afl.runtime.runner

# Start the dashboard to monitor execution
python -m afl.dashboard
```

For agent development, see the `agents/` directory which contains client
libraries in Python, Scala, Go, TypeScript, and Java.  The recommended
approach for most use cases is the **RegistryRunner**: register handler
functions in the database and the runner loads and dispatches them
automatically without requiring custom agent code.

---

## Part 8 -- Facet Encapsulation

### The problem

As workflows grow, calling event facets directly becomes unwieldy.  A workflow
that orchestrates five or six event facets in sequence turns into a long,
flat list of steps.  Worse, if two workflows need the same sequence of event
facets, the logic is duplicated — and changes must be made in both places.

### The solution: composed facets

A regular `facet` (not `event facet`, not `workflow`) can have its own
`andThen` body.  Inside the body it calls event facets as steps, wiring
their inputs and outputs together.  The composed facet doesn't pause
itself — its internal event facets do.  From the outside, calling the
composed facet looks like calling a traditional subroutine.

### Simple example

Suppose you have two event facets — one that fetches raw data and one that
transforms it:

```afl
namespace pipeline {

    schema ProcessedResult {
        record_count: Long,
        output_path: String
    }

    event facet FetchData(url: String) => (raw_path: String)
    event facet TransformData(input_path: String, format: String) => (result: ProcessedResult)
}
```

**Before** — the workflow calls both event facets directly:

```afl
namespace pipeline {

    schema ProcessedResult {
        record_count: Long,
        output_path: String
    }

    event facet FetchData(url: String) => (raw_path: String)
    event facet TransformData(input_path: String, format: String) => (result: ProcessedResult)

    workflow Ingest(url: String, format: String = "csv") => (output_path: String) andThen {
        fetched = FetchData(url = $.url)
        transformed = TransformData(input_path = fetched.raw_path, format = $.format)
        yield Ingest(output_path = transformed.result.output_path)
    }
}
```

This works, but every workflow that needs fetch-then-transform must repeat
the same two steps.

**After** — wrap the two steps into a composed facet:

```afl
namespace pipeline {

    schema ProcessedResult {
        record_count: Long,
        output_path: String
    }

    event facet FetchData(url: String) => (raw_path: String)
    event facet TransformData(input_path: String, format: String) => (result: ProcessedResult)

    facet FetchAndTransform(url: String, format: String = "csv") => (result: ProcessedResult) andThen {
        fetched = FetchData(url = $.url)
        transformed = TransformData(input_path = fetched.raw_path, format = $.format)
        yield FetchAndTransform(result = transformed.result)
    }

    workflow Ingest(url: String, format: String = "csv") => (output_path: String) andThen {
        ft = FetchAndTransform(url = $.url, format = $.format)
        yield Ingest(output_path = ft.result.output_path)
    }
}
```

Now `FetchAndTransform` is a reusable unit.  The workflow reads like a single
function call, and any changes to the fetch-transform logic happen in one
place.

Key points:

- `FetchAndTransform` is a regular `facet`, not an `event facet`.  It does not
  pause for an agent itself.
- Its internal steps (`FetchData`, `TransformData`) are event facets, so the
  runtime pauses at each one to wait for agent results.
- The composed facet has a `=>` return clause and a `yield`, just like a
  workflow.
- Callers see a simple interface: give me a URL and format, get back a result.

### Real-world example: volcano data loading

The `examples/volcano-query/` example defines a `LoadVolcanoData` facet that
wraps cache lookup and data download into a single reusable step:

```afl
facet LoadVolcanoData(region: String = "US") => (cache: OSMCache) andThen {
    c = CacheRegion(region = $.region)
    d = osm.ops.DownloadPBF(cache = c.cache)
    yield LoadVolcanoData(cache = d.downloadCache)
}
```

The workflow `FindVolcanoes` calls it as if it were a simple function:

```afl
workflow FindVolcanoes(region: String = "US") => (results: Json) andThen {
    data = LoadVolcanoData(region = $.region)
    query = QueryVolcanoes(cache = data.cache)
    yield FindVolcanoes(results = query.results)
}
```

The caller never needs to know that `LoadVolcanoData` internally coordinates
a cache lookup followed by a download.  If the caching strategy changes, only
the composed facet needs updating.

### Benefits

| Benefit | How it helps |
|---------|-------------|
| Hide complexity | Callers see one step instead of many |
| Enforce ordering | The composed facet wires step dependencies correctly once |
| Swap implementations | Change internal event facets without touching callers |
| Reuse across workflows | Multiple workflows share the same composed facet |
| Layer abstractions | Composed facets can call other composed facets |

### Baking in mixins

Composed facets can attach mixins to their internal steps, so callers
never need to think about retry policies, timeouts, or credentials.

The `examples/jenkins/` example demonstrates this with a `BuildAndTest` facet
that bakes in credentials, timeouts, and retries:

```afl
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
```

Callers just write:

```afl
    build = BuildAndTest(repo = "github.com/team/app", branch = "release")
```

They never see `Credentials`, `Timeout`, or `Retry` — those cross-cutting
concerns are the composed facet's responsibility.

Key points:

- Mixins on internal steps are invisible to the caller.
- The composed facet's parameter list is the public API; everything else is
  an implementation detail.
- This is especially useful for teams where platform engineers define composed
  facets and application developers consume them.

---

## Quick Reference

| Construct | Syntax |
|-----------|--------|
| Facet | `facet Name(param: Type)` |
| Event facet | `event facet Name(p: Type) => (r: Type)` |
| Workflow | `workflow Name(p: Type) => (r: Type) andThen { ... }` |
| Schema | `schema Name { field: Type }` (must be inside a namespace) |
| Namespace | `namespace a.b.c { ... }` |
| Import | `uses other.namespace` |
| Step | `name = FacetCall(arg = value)` |
| Yield | `yield FacetName(output = step.attr)` |
| Self-reference | `$.paramName` |
| Step reference | `stepName.attrName` |
| Mixin | `with Facet(arg = val)` / `with Facet(arg = val) as alias` |
| Implicit | `implicit name = Facet(arg = val)` |
| Composed facet | `facet Name(p: Type) => (r: Type) andThen { ... }` |
| Foreach | `andThen foreach item in $.list { ... }` |
| Arithmetic | `+`, `-`, `*`, `/`, `%` |
| Concatenation | `++` |
| Array literal | `[1, 2, 3]` |
| Map literal | `#{"key": "value"}` |
| Indexing | `arr[0]` |
| Line comment | `// comment` |
| Block comment | `/* comment */` |
| Default value | `param: Type = defaultValue` |
