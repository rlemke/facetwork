# AFL v1 — Language Syntax Specification (10_language.md)

This document specifies the **AFL v1 concrete syntax**. It defines:
- lexical rules (identifiers, literals, comments),
- grammar (EBNF-style), and
- canonical examples (valid and invalid).

**Source of truth:** This document is the authoritative definition of AFL v1 syntax.
**Implementation constraint:** The reference parser SHALL be implemented in **Python 3.11+** using **Lark (LALR)** and a `.lark` grammar file.

Semantic rules (e.g., dependency scheduling, single-writer, yield merge semantics) are defined in `spec/11_semantics.md` and are not part of this syntax file unless they affect parsing.

---

## 1. Lexical Rules

### 1.1 Whitespace
- AFL is **whitespace-insensitive** except where whitespace separates tokens.
- Newlines do not carry meaning; statements are delimited by line breaks **or** `;`.
- Parsers SHALL accept both of the following as statement separators:
  - newline
  - semicolon (`;`)

### 1.2 Comments
- Line comment: `//` to end of line
- Block comment: `/* ... */` (non-nested)
- Doc comment: `/** ... */` — Javadoc-style documentation comment. Preserved in the AST and emitted as `"doc"` in JSON output. May be attached to `namespace`, `facet`, `event facet`, `workflow`, and `schema` declarations. Leading `*` prefixes on each line are stripped. Tags like `@param` and `@return` are preserved as-is in the doc string.

### 1.3 Identifiers
- `ident` matches: `[A-Za-z_][A-Za-z0-9_]*`
- Identifiers are case-sensitive.
- Reserved keywords MAY NOT be used as identifiers.

### 1.4 Qualified Names
- `qname` is one or more identifiers separated by dots:
  - `team.a.osm.conversions`
  - `RunASparkJob`
  - `fms.services.event.osm.POIs`

### 1.5 Literals
- String literal: double-quoted, supports escapes:
  - `"hello"`
  - `"quote: \" ok"`
  - `"newline:\n"`
- Integer literal:
  - decimal digits only (v1): `0`, `123`, `9001`
- Boolean literal:
  - `true` or `false`
- Null literal:
  - `null`

### 1.6 Reserved Keywords
The following tokens are reserved:
- `namespace`, `uses`
- `facet`, `event`, `workflow`, `implicit`, `schema`
- `with`, `as`
- `andThen`, `yield`
- `foreach`, `in`
- `prompt`, `script`, `python`
- `true`, `false`, `null`

---

## 2. Grammar (EBNF)

Notation:
- `*` = zero or more
- `+` = one or more
- `?` = optional
- Parentheses group expressions
- Terminals are quoted

### 2.1 Program Structure

```ebnf
program            := (namespace_block | top_level_decl)* ;

namespace_block    := "namespace" qname "{" namespace_body "}" ;

namespace_body     := (uses_decl | facet_decl | event_facet_decl | workflow_decl | implicit_decl | schema_decl)* ;

uses_decl          := "uses" qname (stmt_sep)? ;

top_level_decl     := facet_decl | event_facet_decl | workflow_decl | implicit_decl | schema_decl ;

schema_decl        := "schema" ident "{" schema_field* "}" (stmt_sep)? ;

schema_field       := ident ":" type (stmt_sep)? ;

stmt_sep           := ";" | NEWLINE+ ;

facet_decl         := "facet" facet_sig facet_def_tail? (stmt_sep)? ;

event_facet_decl   := "event" "facet" facet_sig facet_def_tail? (stmt_sep)? ;

workflow_decl      := "workflow" facet_sig facet_def_tail? (stmt_sep)? ;

facet_sig          := ident "(" params? ")" return_clause? mixin_sig* ;

return_clause      := "=>" "(" params? ")" ;

params             := param ("," param)* ;
param              := ident ":" type ("=" expr)? ;

type               := qname
                   | "String" | "Long" | "Int" | "Boolean" | "Json" ;

mixin_sig          := "with" qname "(" named_args? ")" ;

mixin_call         := "with" qname "(" named_args? ")" ("as" ident)? ;

step_stmt          := ident "=" call_expr (stmt_sep)? ;

call_expr          := qname "(" named_args? ")" mixin_call* ;

facet_def_tail     := ("script" script_block andthen_clause*)
                   | andthen_clause+
                   | ("prompt" prompt_block) ;

andthen_clause     := "andThen" foreach_clause? block
                   | "andThen" "script" script_block ;

foreach_clause     := "foreach" ident "in" reference ;

prompt_block       := "{" prompt_directive* "}" ;
prompt_directive   := "system" string
                   | "template" string
                   | "model" string ;

script_block       := string
                   | "python" string
                   | "{" raw_python_code "}"
                   | "python" "{" raw_python_code "}" ;

block              := "{" block_stmt* yield_stmt? "}" ;

block_stmt         := step_stmt ;

yield_stmt         := "yield" call_expr (stmt_sep)? ;

named_args          := named_arg ("," named_arg)* ;
named_arg           := ident "=" expr ;

expr                := literal | reference ;

reference           := "$." ident ( "." ident )*
                    | ident "." ident ( "." ident )* ;

literal             := string | integer | boolean | "null" ;

implicit_decl       := "implicit" ident "=" call_expr (stmt_sep)? ;


### Valid Syntaxes:

### Facet and Step
facet SomeData(num: Long)

step1 = SomeData(num = 1)

### Event and steps

facet SomeData(num: Long)

event facet Sub(input1: Long, input2: Long) => (output: Long)

step1 = SomeData(num = 30)
step2 = SomeData(num = 20)
step3 = Sub(input1 = step1.num, input2 = step2.num)
step4 = SomeData(num = step3.output)

### Namespace
namespace team.a.osm.conversions {

  uses team.b.osm.streets

  facet ConvertToGeoJson(input: String) => (output: String)

  workflow GetStreets(input: String) => (output: String) andThen {
    step    = ConvertToGeoJson(input = $.input)
    streets = FilterStreets(input = step.output)
    yield GetStreets(output = streets.output)
  }
}

### Default parameter values
facet Config(host: String = "localhost", port: Int = 8080)
workflow MyFlow(input: Long = 1) => (output: Long = 0)

### implicit
facet User(name: String, email: String)
implicit user = User(name = "John", email = "john@example.com")

### Foreach iteration
facet Region(name: String)
facet ProcessRegion(region: String) => (result: String)

workflow ProcessAllRegions(regions: Json) => (results: Json) andThen foreach r in $.regions {
    processed = ProcessRegion(region = r.name)
    yield ProcessAllRegions(results = processed.result)
}

### Prompt block (LLM-driven event facet)
event facet Summarize(text: String) => (summary: String) prompt {
    system "You are a concise summarizer."
    template "Summarize: {text}"
    model "claude-sonnet-4-20250514"
}

### Script blocks (inline Python execution)

Script blocks embed sandboxed Python code directly in AFL declarations. There are **two distinct uses**, each with different placement, timing, and semantics.

---

#### 1. Pre-processing script

A **pre-script** appears immediately after the signature (before any `andThen` blocks). It runs once during the facet scripts phase — after `FacetInitialization` and before event transmission or block execution. The script receives the facet's parameters in a `params` dict and writes computed values back via a `result` dict. Values written to `result` become **additional params** available to downstream `andThen` blocks via `$.field` references.

**Quoted string form:**
```afl
event facet AddOne(input: Long) => (output: Long) script python "result['output'] = params['input'] + 1"
```

**Brace-delimited form** (preferred for multi-line code):
```afl
facet Transform(input: String) => (output: String) script {
    result["output"] = params["input"].upper()
}
```

**Pre-script followed by andThen blocks** — the script runs first, then all andThen blocks execute concurrently:
```afl
workflow AnalyzeState(
    state_fips: String,
    state_name: String
) => (label: String, summary: Json)
script {
    // Normalize inputs into a derived param
    result["state_label"] = params["state_name"].upper() + " (" + params["state_fips"] + ")"
}
andThen {
    data = FetchData(fips = $.state_fips)
    yield AnalyzeState(summary = data.result)
}
```

In this example, `$.state_label` is available inside the `andThen` block because the pre-script wrote it to `result["state_label"]`.

**With explicit `python` keyword:**
```afl
facet Prepare(x: Long) script python {
    result["x"] = params["x"] * 2
    result["label"] = f"doubled-{params['x']}"
}
```

---

#### 2. andThen script block

An **andThen script** is a concurrent block variant that replaces the `{ steps... }` body with inline Python code. It runs in parallel with other `andThen` blocks (both regular and script). The script receives the **container step's params** in the `params` dict and writes outputs via `result`. Values written to `result` become **return values** on the workflow/facet, merged during the capture phase alongside yield results from regular blocks.

**Basic andThen script:**
```afl
facet Pipeline() => (computed: Long) andThen script {
    result["computed"] = 42
}
```

**Mixed regular and script blocks** — all run concurrently:
```afl
workflow ProcessData(input: String) => (
    processed: String,
    checksum: String,
    audit: String
)
andThen {
    p = Transform(data = $.input)
    yield ProcessData(processed = p.output)
}
andThen script {
    import hashlib
    result["checksum"] = hashlib.md5(params["input"].encode()).hexdigest()
}
andThen script {
    result["audit"] = "Processed input: " + params["input"][:50]
}
```

---

#### Combining pre-script with andThen scripts

A declaration can have **all three**: a pre-script, regular andThen blocks, and andThen script blocks. Execution order:

1. Pre-script runs first (modifies params)
2. All andThen blocks (regular + script) run concurrently
3. Results merge: yields from regular blocks + `result` dict from script blocks

```afl
workflow FullPipeline(
    state_fips: String,
    state_name: String
) => (
    summary: Json,
    pop_total: Long,
    report: String,
    audit: String
)
script {
    // Step 1: pre-processing — creates derived params
    result["state_label"] = params["state_name"].upper() + " (" + params["state_fips"] + ")"
}
andThen {
    // Step 2a: regular block — event facets with yield
    data = FetchCensus(fips = $.state_fips)
    yield FullPipeline(summary = data.result)
}
andThen script {
    // Step 2b: concurrent script — uses pre-script's derived param
    label = params.get("state_label", params["state_name"])
    result["pop_total"] = 5000000
    result["report"] = "Population report for " + label
}
andThen script {
    // Step 2c: concurrent script — audit trail
    label = params.get("state_label", params["state_name"])
    result["audit"] = "Audit complete for " + label + " at fips=" + params["state_fips"]
}
```

---

#### Script block syntax forms

All four syntactic forms are equivalent:

| Form | Example |
|------|---------|
| Quoted string | `script "result['x'] = 1"` |
| `python` + quoted string | `script python "result['x'] = 1"` |
| Brace-delimited | `script { result["x"] = 1 }` |
| `python` + brace-delimited | `script python { result["x"] = 1 }` |

Brace-delimited blocks are converted to quoted strings by a pre-lex preprocessor before LALR parsing. The preprocessor:
- Tracks brace depth (handles nested Python dicts/sets)
- Respects Python string literals (braces inside strings are ignored)
- Strips common leading indentation (dedent)
- Preserves line numbers for error reporting

#### Script execution API

Scripts execute in a sandboxed Python environment with two pre-defined variables:

| Variable | Type | Description |
|----------|------|-------------|
| `params` | `dict` | Input parameters (read-only by convention) |
| `result` | `dict` | Output values (write to this) |

- **Pre-script**: `params` contains the facet/workflow's input parameters. Values written to `result` become additional params for downstream blocks.
- **andThen script**: `params` contains the container step's params (including any values added by a pre-script). Values written to `result` become return values on the workflow/facet.

Scripts may use Python standard library imports. Execution errors are captured and reported as step failures.

### Schema declaration and instantiation
schema Config {
    timeout: Long,
    retries: Long
}

event facet DoSomething(config: Config) => (result: String)

workflow Example() => (output: String) andThen {
    cfg = Config(timeout = 30, retries = 3)
    result = DoSomething(config = cfg.timeout)
    yield Example(output = result.result)
}

### Invalid: 4.1 Missing parentheses on mixin . mixins must be written as with Name(...)
job = RunASparkJob(input = "x") with User as user

### Invalid return clause must be => ( ... )
event facet Sub(input1: Long, input2: Long) => output: Long


add a parse code verification. It should check for the following.

### Name Uniqueness
Within a namespace all facet, workflow, and event names must be unique.
within a block all step names must be unique.
No step can reference a step outside its block.

###step references
A step may reference attributes of the step containing the block using the "$". For example:

    s1 = SomeFacet(input = "this") andThen {
       s2 = AnotherFacet(input = $.input)

If a reference to a step references an attribute it must be a valid attribute. For example, the following is valid:

    s1 = SomeFacet(input = "this")
    s2 = AnotherFacet(input = s1.input)
the following is not valid
    s1 = SomeFacet(input = "this")
    s2 = AnotherFacet(input = s1.otherAttribute)

### Yields
A yield must have the name of a facet in the containing step. For example:
    s1 = SomeFacet(input = "this") andThen {
       s2 = AnotherFacet(input = "that")
       yield SomeFacet(input = s2.input)
        }

There can be more than one yield. Each one referencing a different mixin in the containing Step
    s1 = SomeFacet(input = "this") with AnotherFacet(x = "this") andThen {
       s2 = AnotherFacet(input = "that")
       yield SomeFacet(input = s2.input)
       yield AnotherFacet(x = s2.input)
        }

