## Non-functional Requirements (90_nonfunctional.md)

---

## Dependencies

### Runtime Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| Python | ≥3.11 | Runtime |
| lark | ≥1.1.0 | Parser generator |

### Optional Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| pymongo | ≥4.0 | MongoDB connectivity |
| pyarrow | ≥14.0 | HDFS storage backend |

### Development Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| pytest | ≥7.0 | Test framework |
| pytest-cov | ≥4.0 | Coverage reporting |

### Forbidden Dependencies
No other parsing, compiler, or DSL libraries are permitted in v1:
- ❌ ANTLR
- ❌ PLY
- ❌ Parsimonious
- ❌ pyparsing
- ❌ regex-based parsers
- ❌ handwritten parsers

---

## Performance

### Parser Performance
- Grammar uses LALR mode (linear time parsing)
- No backtracking required
- Single-pass parsing

### Memory
- AST nodes use dataclasses (memory efficient)
- No caching of intermediate results
- Parse tree discarded after transformation

---

## Compatibility

### Python Version
- Minimum: Python 3.11
- Tested: Python 3.14
- Uses: dataclasses, type hints, `kw_only` parameter

### Platform
- OS-independent (pure Python)
- No native extensions
- No system dependencies

---

## Code Quality

### Style
- Type hints on all public functions
- Docstrings on all public classes and functions
- No global mutable state

### Testing
- 3065 tests collected (2981 passed, 84 skipped) as of v0.18.0
- Tests for all grammar constructs
- Tests for error reporting
- MongoDB store tests using mongomock (no real database required)

### Documentation
- README with usage examples
- Spec files for language definition
- CLAUDE.md for development guidance

---

## Security

### Input Handling
- All input treated as untrusted
- No eval() or exec() usage
- No file system access beyond reading input

### Error Messages
- No sensitive data in error messages
- Line/column info only (no source excerpts in errors)

---

## Versioning

### Current Version
- `0.1.0` (initial implementation)

### Semantic Versioning
- MAJOR: Breaking changes to AST structure or JSON format
- MINOR: New language features, new AST nodes
- PATCH: Bug fixes, performance improvements

### JSON Format Stability
- JSON output format is considered stable within MAJOR version
- `type` field present on all nodes
- Location fields optional (controlled by flag)
- As of v0.12.52, the emitter produces **declarations-only** format (no categorized `namespaces`/`facets`/`eventFacets`/`workflows`/`implicits`/`schemas` keys)
- `normalize_program_ast()` in `afl/ast_utils.py` handles backward compatibility for legacy JSON that uses categorized keys

