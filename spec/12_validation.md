## Semantic Validation (12_validation.md)

The AFL compiler performs semantic validation after parsing to ensure program correctness.

---

## Validation Rules

### 1. Name Uniqueness

#### Within Top-Level Scope
All facet, event facet, and workflow names must be unique:
```afl
facet User(name: String)
facet User(email: String)  // ERROR: Duplicate facet name 'User'
```

#### Within a Namespace
Names must be unique within each namespace:
```afl
namespace team.data {
    facet User(name: String)
    facet User(email: String)  // ERROR: Duplicate facet name 'User'
}
```

Same names in different namespaces are allowed:
```afl
namespace team.a {
    facet User(name: String)  // OK
}
namespace team.b {
    facet User(name: String)  // OK - different namespace
}
```

#### Within a Block
Step names must be unique within each `andThen` block:
```afl
workflow Test(input: String) andThen {
    step1 = Process(value = $.input)
    step1 = Process(value = $.input)  // ERROR: Duplicate step name 'step1'
}
```

---

### 2. Step References

#### Input References (`$.attr`)
Must reference a valid parameter of the containing facet/workflow:
```afl
workflow Test(input: String) andThen {
    step1 = Process(value = $.input)      // OK
    step2 = Process(value = $.nonexistent) // ERROR: no parameter named 'nonexistent'
}
```

#### Step References (`step.attr`)
Must reference:
1. A step defined **before** the current step
2. A valid return attribute of that step's facet

```afl
facet Data(value: String) => (result: String)

workflow Test(input: String) andThen {
    step1 = Data(value = $.input)
    step2 = Data(value = step1.result)     // OK
    step3 = Data(value = step1.nonexistent) // ERROR: invalid attribute
    step4 = Data(value = step5.result)     // ERROR: undefined step
    step5 = Data(value = $.input)
}
```

#### Foreach Variables
The foreach iteration variable can be referenced within the block:
```afl
workflow Process(items: Json) andThen foreach item in $.items {
    step1 = Handle(data = item.value)  // OK - 'item' is the foreach variable
}
```

---

### 3. Yield Validation

#### Valid Targets
A yield must target either:
- The containing facet/workflow, OR
- One of its mixins

```afl
workflow Test(input: String) => (output: String) andThen {
    step1 = Process(value = $.input)
    yield Test(output = step1.result)      // OK
    yield WrongFacet(output = step1.result) // ERROR: invalid yield target
}
```

#### Multiple Yields
Multiple yields are allowed, each targeting a different facet/mixin:
```afl
workflow Test(input: String) => (output: String) with Extra(data = "x") andThen {
    step1 = Process(value = $.input)
    yield Test(output = step1.result)   // OK
    yield Extra(data = step1.result)    // OK - targets mixin
}
```

#### No Duplicate Targets
Each yield must reference a different target:
```afl
workflow Test(input: String) => (output: String) andThen {
    step1 = Process(value = $.input)
    yield Test(output = step1.result)
    yield Test(output = step1.result)  // ERROR: duplicate yield target 'Test'
}
```

---

### 4. Use Statement Validation

The `use` statement must reference an existing namespace:
```afl
namespace lib.utils {
    facet Helper(value: String)
}

namespace app {
    use lib.utils           // OK - namespace exists
    use nonexistent.module  // ERROR: namespace does not exist
}
```

---

### 5. Facet Name Resolution

#### Ambiguity Detection
When a facet name exists in multiple imported namespaces, it must be qualified:
```afl
namespace a.b {
    facet SomeFacet(input: String) => (result: String)
}
namespace c.d {
    facet SomeFacet(input: String) => (result: String)
}
namespace app {
    use a.b
    use c.d
    facet App(input: String) => (output: String) andThen {
        s = SomeFacet(input = $.input)      // ERROR: ambiguous reference
        s = a.b.SomeFacet(input = $.input)  // OK: fully qualified
        yield App(output = s.result)
    }
}
```

#### Local Precedence
Facets in the current namespace take precedence over imports:
```afl
namespace lib {
    facet Helper(value: String) => (result: String)
}
namespace app {
    use lib
    facet Helper(value: String) => (result: String)  // Local definition
    facet App(input: String) => (output: String) andThen {
        h = Helper(value = $.input)  // OK: uses local Helper, no ambiguity
        yield App(output = h.result)
    }
}
```

#### Global Ambiguity Detection
Even when only one imported namespace contains a facet name, the validator checks whether
the same short name exists in **any other namespace globally**. If it does, the reference
is flagged as ambiguous and the developer must use a fully qualified name:
```afl
namespace europe {
    facet Georgia() => (cache: [OSMCache])
}
namespace us.states {
    facet Georgia() => (cache: [OSMCache])
}
namespace app {
    use europe
    facet Run() => (cache: [OSMCache]) andThen {
        g = Georgia()                   // ERROR: globally ambiguous
        g = europe.Georgia()            // OK: fully qualified
        yield Run(cache = g.cache)
    }
}
```

**Exception:** A facet defined in the *current* namespace always takes precedence
(step 2 below), so local definitions are never ambiguous against global duplicates.

#### Resolution Order
1. Fully qualified name (exact match)
2. Current namespace (takes precedence — no global ambiguity check)
3. Imported namespaces (ambiguity check among imports **and** global duplicates)
4. Top-level declarations

---

### 6. Schema Instantiation Validation

Schemas can be instantiated in step statements, creating data objects.

#### Valid Schema Instantiation
```afl
schema Config {
    timeout: Long,
    retries: Long
}

workflow Test() => (output: Long) andThen {
    cfg = Config(timeout = 30, retries = 3)  // OK
    yield Test(output = cfg.timeout)          // OK - cfg.timeout is accessible
}
```

#### Field Validation
All arguments must be valid schema fields:
```afl
schema Config {
    timeout: Long
}

workflow Test() andThen {
    cfg = Config(timeout = 30, unknown = "bad")  // ERROR: unknown field 'unknown'
}
```

#### No Mixins Allowed
Schema instantiation cannot have mixins:
```afl
schema Config {
    timeout: Long
}
facet SomeMixin()

workflow Test() andThen {
    cfg = Config(timeout = 30) with SomeMixin()  // ERROR: cannot have mixins
}
```

#### Schema Fields as Returns
Schema fields are stored as **returns** (not params), making them accessible via `step.field`:
```afl
schema Request {
    url: String,
    method: String
}
facet Fetch(url: String, method: String) => (data: String)

workflow Test(input: String) => (result: String) andThen {
    req = Request(url = $.input, method = "GET")
    resp = Fetch(url = req.url, method = req.method)  // OK - req.url, req.method accessible
    yield Test(result = resp.data)
}
```

#### Schema Name Resolution
Schema names follow the same resolution order as facets:
1. Fully qualified name
2. Current namespace
3. Imported namespaces
4. Top-level

---

### 7. Expression Type Checking

The validator infers expression types and rejects type-incompatible operations.

#### Comparison Operators (`==`, `!=`, `>`, `<`, `>=`, `<=`)
- Return type: `Boolean`
- Equality operators (`==`, `!=`) accept any operand types
- Ordered comparison operators (`>`, `<`, `>=`, `<=`) reject `Boolean` operands

#### Boolean Operators (`&&`, `||`)
- Return type: `Boolean`
- Both operands must be `Boolean` type
- Runtime uses short-circuit evaluation (`&&` skips right if left is false; `||` skips right if left is true)

#### Logical NOT (`!`)
- Return type: `Boolean`
- Operand must be `Boolean` type

#### Arithmetic Operators (`+`, `-`, `*`, `/`, `%`)
- Reject `String` and `Boolean` operands
- Unknown-type operands pass through (no error)

---

### 8. Match Block Validation

#### Structure Rules
- At least one case required
- At most one default case (`case _`)
- Default case must be the last case

#### Condition Requirements
- Each non-default case condition must infer to `Boolean` type
- References in conditions are validated against the scope (step outputs, input params)

#### Body Validation
- Each case body (block) is validated as a normal block: steps, yields, references

```afl
// Valid match block
s1 = Classify(input = $.data) andThen match {
    case s1.score > 90 => {
        a = HighGrade(id = s1.id)
    }
    case s1.score > 50 && s1.score <= 90 => {
        b = MidGrade(id = s1.id)
    }
    case _ => {
        c = LowGrade(id = s1.id)
    }
}

// ERROR: condition not boolean
s2 = Process(input = $.data) andThen match {
    case s2.name => { ... }  // ERROR: String is not Boolean
}
```

---

## Implementation

### File: `afl/validator.py`

```python
from afl import parse, validate

ast = parse(source)
result = validate(ast)

if result.is_valid:
    print("Valid!")
else:
    for error in result.errors:
        print(f"Line {error.line}: {error.message}")
```

### Classes
| Class | Purpose |
|-------|---------|
| `AFLValidator` | Main validator class |
| `ValidationResult` | Contains list of errors, `is_valid` property |
| `ValidationError` | Error with message, line, column |

### CLI Integration
Validation runs by default during CLI compilation:
```bash
# Validation enabled (default)
afl input.afl

# Skip validation
afl input.afl --no-validate
```

---

## Error Messages

| Error | Example |
|-------|---------|
| Duplicate name | `Duplicate facet name 'User' (previously defined at line 1)` |
| Duplicate step | `Duplicate step name 'step1' (previously defined at line 3)` |
| Invalid input ref | `Invalid input reference '$.foo': no parameter named 'foo'` |
| Undefined step | `Reference to undefined step 'step2'` |
| Invalid attribute | `Invalid attribute 'foo' for step 'step1': valid attributes are ['result']` |
| Invalid yield | `Invalid yield target 'Wrong': must be the containing facet or one of its mixins. Valid targets are: ['Test']` |
| Duplicate yield | `Duplicate yield target 'Test': each yield must reference a different facet or mixin` |
| Invalid use | `Invalid use statement: namespace 'nonexistent' does not exist` |
| Ambiguous facet | `Ambiguous facet reference 'Facet': could be a.b.Facet, c.d.Facet. Use fully qualified name to disambiguate.` |
| Unknown facet | `Unknown facet 'nonexistent.Facet'` |
| Unknown schema field | `Unknown field 'foo' for schema 'Config'. Valid fields are: ['timeout', 'retries']` |
| Schema with mixins | `Schema instantiation 'Config' cannot have mixins. Schemas are simple data structures without mixin support.` |
| Ambiguous schema | `Ambiguous schema reference 'Config': could be a.Config, b.Config. Use fully qualified name to disambiguate.` |
| Boolean op on non-bool | `Operator '&&' requires Boolean operands, got String && Int` |
| Ordered comp on bool | `Operator '>' cannot compare Boolean values` |
| NOT on non-bool | `Operator '!' requires Boolean operand, got String` |
| Match empty | `Match block must have at least one case` |
| Match multiple defaults | `Match block has multiple default cases` |
| Match default not last | `Default case must be the last case in a match block` |
| Match non-bool condition | `Match case condition must be Boolean, got String` |
