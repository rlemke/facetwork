## AST Semantics (11_semantics.md)

The reference implementation SHALL:
- use Lark with `parser="lalr"` and `propagate_positions=True`
- generate clear syntax errors with line and column
- preserve statement separation by NEWLINE and/or semicolons
- produce an AST using dataclasses (not raw parse trees)

---

## AST Node Types

The parser produces an AST with the following dataclass nodes.

### Node Identity Requirement

All AST nodes MUST have a unique UUID (v4) stored in the `node_id` field. This ID:
- Is automatically generated when the node is created
- Is unique across all nodes in the AST
- Is included in JSON output as the `id` field
- Enables stable references to specific nodes across tools and systems

### Root Node
| Node | Description |
|------|-------------|
| `Program` | Root containing namespaces, facets, event_facets, workflows, implicits |

> **Note**: The Python AST dataclass `Program` has separate fields (`namespaces`, `facets`, `event_facets`, `workflows`, `implicits`, `schemas`). The JSON serialization flattens these into a unified `declarations` list. Both representations carry the same information.

### Declaration Nodes
| Node | Description |
|------|-------------|
| `Namespace` | `namespace qname { body }` |
| `UsesDecl` | `uses qname` |
| `FacetDecl` | `facet Name(params) => (returns) body?` |
| `EventFacetDecl` | `event facet Name(params) => (returns) body?` |
| `WorkflowDecl` | `workflow Name(params) => (returns) body?` |
| `ImplicitDecl` | `implicit name = CallExpr` |
| `SchemaDecl` | `schema Name { fields }` |
| `SchemaField` | `name: Type` field within a schema |

### Signature Nodes
| Node | Description |
|------|-------------|
| `FacetSig` | Name, params, returns, mixins |
| `Parameter` | `name: Type = default?` |
| `TypeRef` | Type name (builtin or qualified) |
| `ReturnClause` | `=> (params)` |
| `MixinSig` | `with Name(args)` in signature |
| `MixinCall` | `with Name(args) as alias` in call |

### Block Nodes
| Node | Description |
|------|-------------|
| `AndThenBlock` | `andThen [foreach] { block }`, `andThen script "code"`, or `andThen when { cases }` |
| `Block` | `{ steps* yield? }` |
| `ForeachClause` | `foreach var in reference` |
| `StepStmt` | `name = CallExpr` |
| `YieldStmt` | `yield CallExpr` |
| `PromptBlock` | `prompt { system/template/model directives }` for LLM-based facets |
| `ScriptBlock` | `script [python] "code..."` or `script { code }` for inline sandboxed Python execution |
| `WhenBlock` | `when { cases }` — conditional branching within andThen |
| `WhenCase` | `case expr => { block }` or `case _ => { block }` |

### Expression Nodes
| Node | Description |
|------|-------------|
| `CallExpr` | `Name(args) mixins*` |
| `NamedArg` | `name = expr` |
| `Reference` | `$.path` (input) or `step.path` (step output) |
| `Literal` | String, Integer, Double, Boolean, or Null |
| `BinaryExpr` | `left op right` — operators: `+`, `-`, `*`, `/`, `%`, `++`, `==`, `!=`, `>`, `<`, `>=`, `<=`, `&&`, `\|\|` |
| `UnaryExpr` | `op operand` — operators: `-` (negation), `!` (logical NOT) |
| `ConcatExpr` | String concatenation via `++` (legacy; new code uses `BinaryExpr`) |
| `ArrayLiteral` | `[elem, ...]` |
| `MapLiteral` | `#{"key": value, ...}` |
| `IndexExpr` | `target[index]` |

### Metadata Nodes
| Node | Description |
|------|-------------|
| `SourceLocation` | line, column, end_line, end_column, source_id |
| `ASTNode` | Base class with node_id (UUID) and optional location |

---

## Node Relationships

```
Program
├── namespaces: list[Namespace]
│   ├── name: str
│   ├── uses: list[UsesDecl]
│   ├── facets: list[FacetDecl]
│   ├── event_facets: list[EventFacetDecl]
│   ├── workflows: list[WorkflowDecl]
│   └── implicits: list[ImplicitDecl]
├── facets: list[FacetDecl]
│   ├── sig: FacetSig
│   │   ├── name: str
│   │   ├── params: list[Parameter]
│   │   ├── returns: ReturnClause?
│   │   └── mixins: list[MixinSig]
│   ├── pre_script: ScriptBlock?       # pre-processing script (runs before event/begins)
│   └── body: AndThenBlock? | PromptBlock?
│       # AndThenBlock (regular):
│       ├── foreach: ForeachClause?
│       ├── block: Block?
│       │   ├── steps: list[StepStmt]
│       │   └── yield_stmt: YieldStmt?
│       ├── script: ScriptBlock?       # andThen script variant (mutually exclusive with block/when)
│       └── when: WhenBlock?          # andThen when variant (mutually exclusive with block/script)
│           └── cases: list[WhenCase]
│               ├── condition: expr?  # None for default case
│               ├── block: Block
│               └── is_default: bool
│       # PromptBlock:
│       ├── system: str?
│       ├── template: str?
│       └── model: str?
│       # ScriptBlock:
│       ├── language: str (default "python")
│       └── code: str
├── event_facets: list[EventFacetDecl]
├── workflows: list[WorkflowDecl]
├── implicits: list[ImplicitDecl]
└── schemas: list[SchemaDecl]
    ├── name: str
    └── fields: list[SchemaField]
        ├── name: str
        └── type: TypeRef | ArrayType
```

---

## Semantic Rules

### Type System
- Built-in types: `String`, `Long`, `Int`, `Double`, `Boolean`, `Json`
- Qualified types: `namespace.TypeName`

### Reference Resolution
- Input references (`$.field`) refer to workflow/facet parameters
- Step references (`step.field`) refer to outputs of previous steps
- Nested paths (`$.data.nested.field`) supported

### Default Parameter Values
- Parameters can have optional default values: `name: Type = expr`
- Supported default expressions: literals (`"hello"`, `42`, `3.14`, `true`, `null`), references, and concat expressions
- The `Parameter` AST node has an optional `default` field
- The emitter produces a `"default"` key in the JSON AST when a default is present
- The runtime evaluator uses defaults for any parameters not supplied in the `inputs` dict

### Scope Rules
- Steps within a block can reference earlier steps
- Yield statements merge outputs back to containing facet
- Implicit declarations provide default values

### Schema Instantiation
- Schemas can be instantiated in step statements: `cfg = Config(timeout = 30)`
- Schema fields become the step's returns (accessible via `step.field`)
- Schema instantiation uses the same `CallExpr` AST node as facet calls
- Schemas cannot have mixins; `Config() with Mixin()` is a validation error
- Schema fields are validated at compile time (unknown fields produce errors)

### Script Execution Semantics

Script blocks embed sandboxed Python code. There are two distinct uses with different timing and data flow.

#### Pre-processing script (`pre_script`)
- **Placement**: `facet/event facet/workflow Name(...) script { code }`
- **Timing**: Runs during `state.facet.scripts.Begin`, after `FacetInitialization` and before event transmission or block execution
- **Input**: `params` dict contains the declaration's input parameters
- **Output**: Values written to `result` dict are stored as **params** (not returns) on the step, making them available via `$.field` in downstream `andThen` blocks
- **Cardinality**: At most one pre-script per declaration

#### andThen script block (`AndThenBlock.script`)
- **Placement**: `andThen script { code }` — appears where a regular `andThen { steps }` block would
- **Timing**: Runs during `state.block.execution.Begin`, concurrently with other `andThen` blocks (both regular and script)
- **Input**: `params` dict contains the container step's params (including any values added by a pre-script)
- **Output**: Values written to `result` dict are stored as **returns** on the block step, merged into the containing declaration's outputs during the capture phase (`state.statement.capture.Begin`) alongside yield results from regular blocks
- **Cardinality**: Zero or more andThen script blocks per declaration, interleaved freely with regular andThen blocks

#### Execution environment
- Scripts receive two pre-defined variables: `params` (dict, input) and `result` (dict, output)
- Python standard library imports are available
- Execution errors are captured and reported as step failures (the step transitions to an error state)
- Scripts are executed via `ScriptExecutor` which uses `exec()` in a restricted namespace

#### Data flow summary
```
Declaration params
    │
    ▼
┌─────────────────┐
│   Pre-script    │  writes to result → stored as params
└────────┬────────┘
         │ params (original + pre-script additions)
         ▼
┌─────────────────────────────────────────────┐
│         All andThen blocks (concurrent)      │
│                                              │
│  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Regular block │  │ andThen script block │ │
│  │ steps + yield │  │ params → result dict │ │
│  └──────┬───────┘  └──────────┬───────────┘ │
│         │                     │              │
│    yield results        result dict values   │
└─────────┼─────────────────────┼──────────────┘
          │                     │
          ▼                     ▼
┌──────────────────────────────────────────────┐
│  Capture phase: merge all into declaration   │
│  outputs (yield params + block returns)      │
└──────────────────────────────────────────────┘
```

---

## Implementation Details

### File: `afl/ast.py`
- All nodes are `@dataclass` decorated
- Base `ASTNode` class with:
  - `node_id: str` - Auto-generated UUID (v4) for unique identification
  - `location: Optional[SourceLocation]` - Source position for error reporting
- Both fields use `kw_only=True` for inheritance compatibility
- UUIDs are generated via `uuid.uuid4()` at node creation time

### File: `afl/transformer.py`
- Extends `lark.Transformer`
- Uses `@v_args(meta=True)` for location tracking
- Converts Lark parse tree to AST nodes

### File: `afl/preprocess.py`
- `preprocess_script_braces()` converts brace-delimited `script { code }` to `script "escaped_code"` before LALR parsing
- Tracks brace depth to handle nested Python dicts/sets
- Respects Python string literals (single, double, triple-quoted) and AFL comments
- Strips common indentation (dedent) and preserves line numbers via blank-line padding
- `PreprocessError` exception for unbalanced braces

### File: `afl/parser.py`
- `AFLParser` class wraps Lark parser
- Calls `preprocess_script_braces()` before Lark parsing
- `ParseError` exception with line/column
- `parse()` convenience function
