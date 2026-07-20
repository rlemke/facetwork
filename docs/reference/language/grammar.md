# FFL v1 — Language Syntax Specification (10_language.md)

This document specifies the **FFL v1 concrete syntax**. It defines:
- lexical rules (identifiers, literals, comments),
- grammar (EBNF-style), and
- canonical examples (valid and invalid).

**Source of truth:** This document is the authoritative definition of FFL v1 syntax.
**Implementation constraint:** The reference parser SHALL be implemented in **Python 3.11+** using **Lark (LALR)** and a `.lark` grammar file.

Semantic rules (e.g., dependency scheduling, single-writer, yield merge semantics) are defined in `spec/11_semantics.md` and are not part of this syntax file unless they affect parsing.

---

## 1. Lexical Rules

### 1.1 Whitespace
- FFL is **whitespace-insensitive** except where whitespace separates tokens.
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
- Float literal:
  - decimal digits with dot, optional exponent: `3.14`, `0.5`, `1.0e10`, `2.5E-3`
  - maps to `Double` type in the AST
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
- `when`, `case`
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

facet_decl         := "facet" facet_sig facet_def_tail? catch_clause? (stmt_sep)? ;

event_facet_decl   := "event" "facet" facet_sig facet_def_tail? catch_clause? (stmt_sep)? ;

workflow_decl      := "workflow" facet_sig facet_def_tail? catch_clause? (stmt_sep)? ;

facet_sig          := ident "(" params? ")" return_clause? mixin_sig* ;

return_clause      := "=>" "(" params? ")" ;

params             := param ("," param)* ;
param              := ident ":" type ("=" expr)? ;

type               := qname
                   | "String" | "Long" | "Int" | "Double" | "Boolean" | "Json" ;

mixin_sig          := "with" qname "(" named_args? ")" ;

mixin_call         := "with" qname "(" named_args? ")" ("as" ident)? ;

step_stmt          := ident "=" call_expr step_body? catch_clause? (stmt_sep)? ;

step_body          := andthen_clause+ ;

call_expr          := qname "(" named_args? ")" mixin_call* ;

facet_def_tail     := ("script" script_block andthen_clause*)
                   | andthen_clause+
                   | ("prompt" prompt_block) ;

catch_clause       := "catch" block
                   | "catch" "when" when_block ;

andthen_clause     := "andThen" foreach_clause? block
                   | "andThen" "script" script_block
                   | "andThen" "when" when_block ;

when_block          := "{" when_case+ "}" ;
when_case           := "case" expr "=>" block
                    | "case" "_" "=>" block ;

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

block_stmt         := step_stmt
                    | sys_log_stmt
                    | sys_assert_stmt ;

yield_stmt         := "yield" call_expr (stmt_sep)? ;

// Inline diagnostic statements — side-effect only, no return value,
// no place in expression position.  Live alongside step_stmt inside
// an andThen block; the runtime executes them inline via a minimal
// transition table (CREATED → FACET_INIT_BEGIN → STATEMENT_COMPLETE).
sys_log_stmt       := "sys" "." "log" "(" named_args? ")" ;
sys_assert_stmt    := "sys" "." "assert" "(" expr ")" ;

named_args          := named_arg ("," named_arg)* ;
named_arg           := ident "=" expr ;

expr                := or_expr ;

or_expr             := and_expr ("||" and_expr)* ;
and_expr            := comparison_expr ("&&" comparison_expr)* ;
comparison_expr     := concat_expr (compare_op concat_expr)? ;
concat_expr         := additive_expr ("++" additive_expr)* ;
additive_expr       := multiplicative_expr (ADD_OP multiplicative_expr)* ;
multiplicative_expr := unary_expr (MUL_OP unary_expr)* ;
unary_expr          := ADD_OP unary_expr | "!" unary_expr | postfix_expr ;
postfix_expr        := atom_expr ("[" expr "]")* ;
atom_expr           := literal | reference | array_literal | map_literal | "(" expr ")" ;

// Comparison operators include the standard relational set plus the
// containment/string-match keywords.  All are non-associative — only
// one operator may appear in a comparison_expr.
compare_op         := COMP_OP
                    | "in"
                    | "not" "in"
                    | "contains"
                    | "startsWith"
                    | "endsWith" ;
COMP_OP             := "==" | "!=" | ">=" | "<=" | ">" | "<" ;
ADD_OP              := "+" | "-" ;
MUL_OP              := "*" | "/" | "%" ;

reference           := "$"+ "." ident ( "." ident )*
                    | ident "." ident ( "." ident )* ;

literal             := string | float | integer | boolean | "null" ;

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

Script blocks embed sandboxed Python code directly in FFL declarations. There are **two distinct uses**, each with different placement, timing, and semantics.

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

### Environment declarations (`environment` / `in environment`)

A named execution environment binds scripts to a language plus the exact
libraries they depend on ([full design](../../architecture/script-environments.md)):

```afl
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely==2.0.4", "networkx==3.3"]
    }

    facet Cluster(path: String) => (out: String)
        in environment PyGeo
        script {
import shapely
result['out'] = params['path']
        }
}
```

Grammar:

```
environment_decl  := "environment" IDENT "{" env_field ("," env_field)* "}"
env_field         := IDENT "=" (string | "[" string ("," string)* "]")
in_env_clause     := "in" "environment" qualified_name    // after the signature
```

Semantics:

- **Declared inside a namespace** (like schemas — `ENV_AT_TOP_LEVEL`);
  resolves local → `use`d namespaces → fully qualified (`ENV_UNKNOWN`).
- **`language` is required** (`ENV_MISSING_LANGUAGE`). A bare `script { }`
  block is implicitly python, so it may only bind to a python environment
  (`ENV_LANGUAGE_SCRIPT_MISMATCH`). Unknown fields (e.g. `python = "3.12"`)
  are preserved for forward compatibility.
- **Absent `in environment` = the default environment** — the runner's own
  interpreter, exactly the historical behavior.
- At publish, `requires` is **resolved and frozen** into a pinned manifest;
  its content hash is what tasks carry and runners advertise, so re-runs use
  the versions the flow was published with. At runtime the script executes
  **on a runner providing that manifest** (baked venv or lazily
  materialized), under the environment's interpreter, and may import the
  environment's declared packages in addition to the stdlib allowlist.
- Environment-bound scripts **defer** — they run on the claiming runner as an
  env-routed task, not inline on whichever runner processes the step.

Canonical example: [`examples/canonical/11-environment-script.ffl`](../../../examples/canonical/11-environment-script.ffl).

### Expression operators

FFL supports arithmetic, concatenation, comparison, and boolean operators with the following precedence (lowest to highest):

| Level | Operators | Description |
|-------|-----------|-------------|
| 1 (lowest) | `\|\|` | Logical OR |
| 2 | `&&` | Logical AND |
| 3 | `==` `!=` `>` `<` `>=` `<=` | Comparison |
| 4 | `++` | String concatenation |
| 5 | `+` `-` | Addition, subtraction |
| 6 | `*` `/` `%` | Multiplication, division, modulo |
| 7 (highest) | `-` `!` (unary) | Negation, logical NOT |

Comparison operators are non-chainable — `a > b > c` is a syntax error. Use `a > b && b > c`.

```afl
// Comparison operators in call arguments
s1 = SomeFacet(x = 1)
s2 = AnotherFacet(eq = s1.x == 10, gt = s1.x > 5)

// Boolean operators
s3 = Check(result = s1.x > 0 && s1.x < 100)
s4 = Check(result = s1.done || s2.done)

// Logical NOT
s5 = Check(result = !s1.done)

// Precedence: comparison binds tighter than boolean
s6 = Check(result = s1.x > 5 && s2.x < 10 || s1.done)
```

### andThen when blocks

Conditional branching on a step's outputs. **Attach the `when` to the step it
gates**: inside the cases `$` is that step, so its returns are `$.field` (and
`$$.field` reaches the workflow input). A default case (`case _`) is **required**
and executes only if no other case matched. Multiple matching cases execute
concurrently (non-exclusive). A `when` may reference only `$` (the step) and its
own same-block steps — not a sibling `andThen` block's step.

```afl
workflow ProcessOrder(amount: Long) => (result: String) andThen {
    order = CreateOrder(amount = $.amount) andThen when {
        case $.status == "success" => {
            a = NotifySuccess(id = $.id)
            yield ProcessOrder(result = a.message)
        }
        case $.amount > 1000 => {
            b = FlagForReview(id = $.id)
        }
        case _ => {
            c = HandleDefault(id = $.id)
            yield ProcessOrder(result = c.message)
        }
    }
}
```

To gate on **several** prior steps at once, pass them as facet-typed parameters
to a gating facet (see [ffl-relative-scoping.md](../../architecture/ffl-relative-scoping.md))
rather than referencing sibling blocks — that keeps the steps flat/parallel and
every reference at `$.param.field`.

### catch blocks

Error recovery for steps, facets, and workflows. Where `andThen` runs on success, `catch` runs on error. Two forms: simple `catch { ... }` and conditional `catch when { case ... }`.

Simple catch — single recovery block:
```afl
s = RiskyCall(input = $.data)
    andThen { processed = Transform(data = s.output) }
    catch {
        fallback = SafeDefault(reason = s.error)
    }
```

Conditional catch — reuses when/case syntax:
```afl
s = RiskyCall(input = $.data)
    catch when {
        case s.error_type == "timeout" => { r = Retry(input = $.data) }
        case _ => { r = LogAndSkip(error = s.error) }
    }
```

Workflow-level catch — catches any unhandled error from body:
```afl
workflow Deploy(service: String) => (status: String)
    andThen {
        build = BuildImage(service = $.service)
        deploy = ApplyDeployment(image = build.image)
    }
    catch {
        fallback = NotifyFailure(service = $.service, error = $.error)
    }
```

Rules:
- `catch` is a clause at the same level as `andThen` — on steps, facets, and workflows
- One `catch` per step / per declaration (at most)
- `catch` covers the step's event facet AND all its `andThen` children; at declaration-level it covers the entire body
- `catch when` reuses existing when/case syntax — same validation rules (default case required)
- On success: catch block is dormant, step completes normally
- On error: catch block runs; if catch succeeds, step completes; if catch fails, step errors
- Error data accessible via `s.error` (message) and `s.error_type` (exception class name)
- At workflow level, error data is accessible via `$.error` and `$.error_type`

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

###step references — relative `$`-scoping
`$` is the **immediate container** of a block — the step/facet/workflow whose
body or clause the block is. `$.attr` reads one of its attributes (params and
returns alike). `$$` walks up one container, `$$$` two, … (walking past the
outermost is a compile error). A block may reference only `$`/`$$`… container
attributes and steps declared in the **same** block; it cannot name the
containing step by its own name (reach it via `$`), nor a sibling block's step.
Full model: [ffl-relative-scoping.md](../../architecture/ffl-relative-scoping.md).

    s1 = SomeFacet(input = "this") andThen {
       s2 = AnotherFacet(input = $.input)

If a reference to a step references an attribute it must be a valid attribute. For example, the following is valid:

    s1 = SomeFacet(input = "this")
    s2 = AnotherFacet(input = s1.input)
the following is not valid
    s1 = SomeFacet(input = "this")
    s2 = AnotherFacet(input = s1.otherAttribute)

#### Pass-by-step (FacetRef)

A parameter typed as a **facet name** receives an entire step by
reference, not a single field. The grammar accepts a bare step name
(no `.field`) wherever a value is expected:

    facet Value(input: String) => (output: String)
    facet Consumer(ds: Value) => (output: String) andThen {
        yield Consumer(output = $.ds.output)
    }

    workflow Demo(input: String) => (output: String) andThen {
        s1 = Value(input = $.input)
        s2 = Consumer(ds = s1)        // s1 itself, not s1.field
        yield Demo(output = s2.output)
    }

Constraints enforced by the validator:

- The source step's facet must equal the parameter's declared facet
  exactly (rule `STEP_REF_FACET_MISMATCH`). Mixin compatibility is not
  considered.
- Inside the consuming `andThen` body, `$.ds.<field>` reads attributes
  of the referenced step's persisted record — both bound inputs and
  computed outputs are accessible; on name collision the return wins.

The grammar rule is `step_ref: IDENT ("." IDENT)*` — the field portion
is optional. A bare reference round-trips to AST `StepRef(path=["s1"])`.

##### Mixin aliases on facet signatures

A mixin in a facet signature may be given an alias with `as <name>`.
The grammar rule is `mixin_sig: "with" QNAME "(" [named_args] ")" ["as" IDENT]`.
Signature mixins may be placed on the same line as the return clause or
on their own lines:

    facet F2(input: String) => (output: String)
        with M1() as m1
        with M2() as m2
        andThen {
            yield F2(output = $.input)
                with M1(output = $.input)
                with M2(output = $.input)
        }

(`with` chains on a `yield` statement add additional targets — see
"Multi-target yields" below.)

The alias becomes the consumer-side name for that mixin's sub-step on a
FacetRef:

    facet M1(input: String) => (output: String)
    facet M2(input: String) => (output: String)

    facet F1(input: String) => (output: String) with M1() with M2()
    facet F2(input: String) => (output: String) with M1() as m1 with M2() as m2

    facet S1(f1: F1) => (output: String) andThen {
        v1 = Value(input = $.f1.output)
        // M1 / M2 cannot be referenced from here — F1's mixins have no alias.
    }

    facet S2(f2: F2) => (output: String) andThen {
        v1 = Value(input = $.f2.output)
        v2 = Value(input = $.f2.m1.output)
        v3 = Value(input = $.f2.m2.output)
    }

Aliases share the consumer-side namespace with the facet's own params
and returns; collisions (alias vs. param, alias vs. return, alias vs.
another alias) are rejected by rule `MIXIN_ALIAS_NAME_CONFLICT`. For
example, `facet F3(m1: String) with M1() as m1` is illegal because the
alias `m1` collides with the param `m1`.

Mixins without an `as` alias are unreachable through a FacetRef by
design — see [REF_INVALID_FACET_REF_ATTRIBUTE](../rules/REF_INVALID_FACET_REF_ATTRIBUTE.md)
for the consumer-side rule.

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

#### Multi-target yields

A single `yield` statement may name multiple targets by chaining
`with` clauses — symmetric with how a facet signature attaches
mixins. The parser unpacks the chain into one yield per target. Each
target must still resolve to a distinct facet or mixin (rule
`YIELD_DUPLICATE_TARGET`):

    yield F(out = v1.output) with M1(out = v2.output) with M2(out = v3.output)

Authors may stack the `with` clauses on separate lines — a small
preprocessor stitches continuation lines back onto the prior line so
the LALR grammar sees them inline. The mixin sigs on a facet
signature can be split across lines the same way:

    facet F(input: String) => (output: String)
        with M1() as m1
        with M2() as m2
        andThen {
            yield F(out = v1.output)
                with M1(out = v2.output)
                with M2(out = v3.output)
        }

