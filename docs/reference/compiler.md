## Compiler Architecture (20_compiler.md)

The FFL compiler transforms FFL (Facetwork Flow Language) source code into a JSON workflow definition for execution by the Facetwork runtime.

---

## Pipeline

```
FFL Source → Lark Parser → Parse Tree → Transformer → AST → Emitter → JSON
```
### Stage 1: Input
The input to the compiler takes two lists of source files:
- **Primary sources**: The main FFL source files for this agent
- **Library sources**: Dependencies referenced by the primary sources

Each entry contains the source text plus provenance metadata indicating where
the source was obtained:

| Origin Type | Provenance Data |
|-------------|-----------------|
| File | File path |
| MongoDB | Collection ID + display name |
| Maven | Group ID, artifact ID, version, optional classifier |

#### Implementation

The `CompilerInput` structure holds source entries:

```python
from afl import CompilerInput, SourceEntry, FileOrigin, SourceLoader

# Load from files
entry1 = SourceLoader.load_file("main.ffl")
entry2 = SourceLoader.load_file("lib.ffl", is_library=True)

# Build compiler input
compiler_input = CompilerInput(
    primary_sources=[entry1],
    library_sources=[entry2]
)

# Parse with provenance tracking
parser = AFLParser()
ast, registry = parser.parse_sources(compiler_input)
```

The `SourceRegistry` maps source IDs to their origins for provenance lookup.

### Stage 2: Parsing
- **Input**: FFL source code (string)
- **Tool**: Lark with LALR parser
- **Output**: Lark parse tree
- **Errors**: `ParseError` with line/column

### Stage 3: AST Construction
- **Input**: Lark parse tree
- **Tool**: `AFLTransformer` (extends `lark.Transformer`)
- **Output**: `Program` AST root node
- **Features**: Source location tracking via `propagate_positions=True`

The transformer uses internal helper methods to extract typed items from heterogeneous
child lists produced by Lark:

| Helper | Purpose |
|--------|---------|
| `_find_one(items, cls)` | Extract the first item of a given type, or `None` |
| `_find_all(items, cls)` | Extract all items of a given type |
| `_left_assoc_fixed_op(meta, items, operator)` | Build a left-associative `BinaryExpr` chain for a single operator (e.g. `||`, `&&`) |
| `_left_assoc_interleaved(meta, items)` | Build a left-associative `BinaryExpr` chain where operator tokens are interleaved with operands (e.g. `add_expr`, `mul_expr`) |

The `CATCH_KW` terminal handler discards the `catch` keyword token, preventing it
from appearing as a raw string in child item lists. The `prompt_block` rule uses
dict-based dispatch to map prompt directive names (`system`, `template`, `model`,
`max_tokens`, `stop_sequences`) to `PromptBlock` fields.

### Stage 4: JSON Emission
- **Input**: `Program` AST
- **Tool**: `JSONEmitter`
- **Output**: JSON string or dictionary
- **Options**: Include/exclude source locations, indentation

---

## Parser Requirements

The FFL compiler uses Lark with the following configuration:

```python
Lark(
    grammar,
    parser="lalr",
    propagate_positions=True,
    maybe_placeholders=False,
)
```

### Files
| File | Purpose |
|------|---------|
| `afl/grammar/afl.lark` | Lark EBNF grammar (87 lines) |
| `afl/parser.py` | Parser wrapper with error handling |
| `afl/transformer.py` | Parse tree to AST conversion |

### Error Handling
All syntax errors include:
- Error message describing the issue
- Line number (1-indexed)
- Column number (1-indexed)
- Expected tokens (when applicable)

---

## JSON Emitter

The emitter converts AST nodes to JSON with consistent structure.

### Configuration
| Option | Default | Description |
|--------|---------|-------------|
| `include_locations` | `True` | Include source locations |
| `include_provenance` | `False` | Include source provenance in locations |
| `source_registry` | `None` | Registry for provenance lookup |
| `indent` | `2` | JSON indentation (None for compact) |

When `include_provenance=True`, locations include `sourceId` and `provenance`:

```json
{
  "location": {
    "line": 1,
    "column": 1,
    "sourceId": "file:///path/to/file.ffl",
    "provenance": {
      "type": "file",
      "path": "/path/to/file.ffl"
    }
  }
}
```

### Declarations-Only Output Format

As of v0.12.52, the emitter produces a **declarations-only** JSON format:

- The `Program` node contains a single `declarations` list — there are no separate `namespaces`, `facets`, `eventFacets`, `workflows`, `implicits`, or `schemas` keys
- `Namespace` nodes also use a `declarations` list internally
- All declaration types (`FacetDecl`, `EventFacetDecl`, `WorkflowDecl`, `ImplicitDecl`, `SchemaDecl`, `Namespace`) appear in the unified `declarations` list

For backward compatibility with legacy/external JSON that uses categorized keys, `normalize_program_ast()` in `afl/ast_utils.py` converts categorized-key JSON into declarations-only format.

Example:
```json
{
  "type": "Program",
  "declarations": [
    {"type": "Namespace", "name": "ns", "declarations": [
      {"type": "FacetDecl", "name": "MyFacet", ...},
      {"type": "ImplicitDecl", "name": "defaults", ...}
    ]},
    {"type": "WorkflowDecl", "name": "Main", ...}
  ]
}
```

### Node Type Mapping
| AST Node | JSON `type` field |
|----------|-------------------|
| `Program` | `"Program"` |
| `FacetDecl` | `"FacetDecl"` |
| `EventFacetDecl` | `"EventFacetDecl"` |
| `WorkflowDecl` | `"WorkflowDecl"` |
| `Namespace` | `"Namespace"` |
| `ImplicitDecl` | `"ImplicitDecl"` |
| `AndThenBlock` | `"AndThenBlock"` |
| `StepStmt` | `"StepStmt"` |
| `YieldStmt` | `"YieldStmt"` |
| `CallExpr` | `"CallExpr"` |
| `Reference` (input) | `"InputRef"` |
| `Reference` (step) | `"StepRef"` |
| `Literal` (string) | `"String"` |
| `Literal` (int) | `"Int"` |
| `Literal` (bool) | `"Boolean"` |
| `Literal` (null) | `"Null"` |

---

## Command-Line Interface

```bash
afl [options] [input_file]
```

### Options
| Flag | Description |
|------|-------------|
| `-o, --output FILE` | Output file (default: stdout) |
| `--primary FILE` | Primary source file (repeatable) |
| `--library FILE` | Library source file (repeatable) |
| `--mongo ID:NAME` | MongoDB source |
| `--maven G:A:V[:CLASSIFIER]` | Maven artifact |
| `--no-locations` | Exclude source locations |
| `--include-provenance` | Include source provenance in locations |
| `--compact` | Compact JSON (no indentation) |
| `--check` | Syntax check only, no output |
| `--no-validate` | Skip semantic validation |

### Examples
```bash
# Parse file to stdout (legacy single-file input)
afl input.ffl

# Parse to file
afl input.ffl -o output.json

# Multi-source input
afl --primary main.ffl --primary util.ffl --library lib.ffl

# Include provenance in output
afl --primary main.ffl --include-provenance

# Compact output
afl input.ffl --compact --no-locations

# Syntax check
afl input.ffl --check

# From stdin
echo 'facet Test()' | afl
```

---

## API Usage

### Python API
```python
from afl import (
    parse, emit_json, emit_dict, AFLParser, ParseError,
    CompilerInput, SourceEntry, SourceRegistry, SourceLoader,
    FileOrigin, JSONEmitter,
)

# Simple single-source parsing (legacy API)
ast = parse("facet User(name: String)")
json_str = emit_json(ast)

# Multi-source parsing with provenance
entry1 = SourceLoader.load_file("main.ffl")
entry2 = SourceLoader.load_file("lib.ffl", is_library=True)

compiler_input = CompilerInput(
    primary_sources=[entry1],
    library_sources=[entry2]
)

parser = AFLParser()
ast, registry = parser.parse_sources(compiler_input)

# Emit with provenance
emitter = JSONEmitter(
    include_provenance=True,
    source_registry=registry,
)
json_str = emitter.emit(ast)

# Error handling
try:
    ast = parse("invalid (")
except ParseError as e:
    print(f"Error at line {e.line}, column {e.column}: {e}")
```

---

## Grammar File

Location: `afl/grammar/afl.lark`

### Structure
1. Program structure (start, namespace, declarations)
2. Facet signatures (params, returns, mixins)
3. AndThen blocks (foreach, steps, yield)
4. Expressions (calls, references, literals)
5. Terminals (identifiers, types, literals)
6. Whitespace and comments

### Parameter Defaults
Parameters support optional default values:
```
param: IDENT ":" type ("=" expr)?
```

When a default is present, the emitter adds a `"default"` key to the parameter JSON:
```json
{
  "name": "input",
  "type": "String",
  "default": {"type": "String", "value": "hello"}
}
```

Without a default, the key is omitted:
```json
{"name": "input", "type": "String"}
```

### Terminal Priorities
- `BOOLEAN`, `NULL`, `TYPE_BUILTIN` have priority `.2`
- `IDENT`, `QNAME` have default priority
- This ensures `true`/`false` parse as booleans, not identifiers
