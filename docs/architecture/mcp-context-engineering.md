# MCP context engineering for FFL

**Status:** shipped, May 2026.
**Companion to:** [`llm-ffl-fluency.md`](llm-ffl-fluency.md) — that doc is the design exploration; this one records what actually got built.

## TL;DR

Claude is fluent in public languages because it saw their grammar, idioms, errors, and recovery patterns during training. For a custom language like FFL, the only way to manufacture that prior is to put it behind the MCP layer:

1. **Validator emits structured `rule_id`s** — every diagnostic carries a stable identifier and a `docs_uri`, not just a human-readable string.
2. **MCP serves rule docs and canonical examples as resources** — paired wrong/right examples keyed by `rule_id`, plus a small set of validator-clean templates for the major language features.
3. **Tool descriptions are mini-specs** — every MCP tool description says when to call it, what's returned, and how it relates to other tools.

The combination lets Claude run a tight `validate → fetch docs → fix → re-validate` loop instead of guessing.

## The four-layer model

A model interacting with a custom language needs four layers of support:

| Layer | What's needed | Where it lives in this repo |
|-------|---------------|------------------------------|
| Concepts | What the system *means* | `CLAUDE.md`, `docs/architecture/overview.md` |
| Syntax | How to write valid code | `docs/reference/language/grammar.md`, `examples/canonical/` |
| Operations | What actions can be performed | MCP tools (`fw_*`) |
| Feedback | How to recover from mistakes | Validator `rule_id`s + `docs/reference/rules/{rule_id}.md` |

A naive MCP only covers layer 3. The most leverage comes from layer 4: a tight validate/fix loop with structured diagnostics is more useful than any number of "describe the language" tools.

## What's in the repo

### Validator (`facetwork/validator.py`)

`ValidationError` carries `rule_id`, `severity`, `line`, `column`, `docs_uri`, `suggested_fix`. The `add_error` and `add_warning` methods accept `rule_id` as a keyword-only argument with a default of `"UNKNOWN"` — backward-compatible with any caller that doesn't pass one. The `docs_uri` is auto-populated from the `rule_id` as `fw://docs/rules/{rule_id}` when not explicitly provided.

The validator emits **47 distinct rule_ids** across these categories:

- **Placement** — `WORKFLOW_AT_TOP_LEVEL`, `SCHEMA_AT_TOP_LEVEL`
- **Reference resolution** — `REF_UNKNOWN_FACET`, `REF_AMBIGUOUS_FACET`, `REF_UNKNOWN_SCHEMA`, `REF_AMBIGUOUS_SCHEMA`, `REF_UNKNOWN_TYPE`, `REF_UNDEFINED_STEP`, `REF_INVALID_STEP_FORMAT`, `REF_INVALID_STEP_ATTRIBUTE`, `REF_INVALID_INPUT`, `REF_CROSS_BLOCK_STEP`, `REF_FORWARD_STEP`, `STEP_REF_FACET_MISMATCH`, `REF_INVALID_FACET_REF_ATTRIBUTE`
- **Naming/uniqueness** — `DUPLICATE_NAME`, `DUPLICATE_STEP_NAME`, `YIELD_DUPLICATE_TARGET`, `USE_UNKNOWN_NAMESPACE`, `MIXIN_ALIAS_NAME_CONFLICT`
- **Yield** — `YIELD_INVALID_TARGET`, `YIELD_TARGET_AMBIGUOUS`
- **`when` blocks** — `WHEN_NO_CASES`, `WHEN_MULTIPLE_DEFAULTS`, `WHEN_MISSING_DEFAULT`, `WHEN_DEFAULT_NOT_LAST`, `WHEN_CONDITION_NOT_BOOLEAN`
- **Prompt blocks** — `PROMPT_MISSING_TEMPLATE`, `PROMPT_INVALID_PLACEHOLDER`
- **Script blocks** — `SCRIPT_EMPTY`, `SCRIPT_UNSUPPORTED_LANGUAGE`
- **Schemas** — `SCHEMA_INSTANTIATION_NO_MIXINS`, `SCHEMA_UNKNOWN_FIELD`
- **Type checking** — `TYPE_BOOLEAN_OPERAND_REQUIRED`, `TYPE_ORDERED_COMPARISON_BOOLEAN`, `TYPE_ORDERED_COMPARISON_SCHEMA`, `TYPE_ARITHMETIC_STRING`, `TYPE_ARITHMETIC_BOOLEAN`, `TYPE_ARITHMETIC_SCHEMA`, `TYPE_NOT_OPERAND_REQUIRED`, `TYPE_NEGATE_STRING`, `TYPE_NEGATE_BOOLEAN`, `TYPE_NEGATE_SCHEMA`, `TYPE_CONTAINMENT_OPERAND`, `TYPE_STRING_MATCH_OPERAND`
- **Inline diagnostics** — `SYS_ASSERT_NOT_BOOLEAN`
- **Implicits** — `IMPLICIT_UNKNOWN_PARAM`

`PARSE_ERROR` is reported by the MCP serializer when the parser itself fails (the validator never emits it — there's no AST to walk).

### Rule documentation (`docs/reference/rules/{rule_id}.md`)

48 files (47 rules + `PARSE_ERROR`), one per emitted rule_id, all following the same template:

```markdown
# RULE_ID — One-line summary

Short paragraph explaining what's wrong.

## Wrong
```ffl
[broken code]
```
→ `[error message the validator emits]`

## Correct
```ffl
[fixed code]
```

## Why
Short paragraph explaining the constraint.
```

Coverage is verified by this script (run from repo root):

```python
import re
from pathlib import Path
src = Path('facetwork/validator.py').read_text()
emitted = set(re.findall(r'rule_id="([A-Z_]+)"', src))
emitted.add('PARSE_ERROR')
documented = {p.stem for p in Path('docs/reference/rules').glob('*.md')
              if p.stem.lower() != 'readme'}
assert emitted == documented, f"missing: {emitted - documented} extra: {documented - emitted}"
```

### Canonical FFL examples (`examples/canonical/`)

Seven small, validator-clean files demonstrating one feature each. Every workflow lives inside a namespace; every reference is `step.field` form; every example parses and validates without error.

| File | Pattern |
|------|---------|
| `01-namespace-facet.ffl` | Smallest valid namespace + facet |
| `02-event-facet-handler.ffl` | Event facet (the agent boundary) |
| `03-workflow-andthen.ffl` | Two sequential steps in one `andThen` |
| `04-workflow-foreach.ffl` | `andThen foreach` fan-out |
| `05-workflow-when.ffl` | `when` block with default |
| `06-workflow-mixin.ffl` | Mixin on workflow signature |
| `07-schema-instantiation.ffl` | Schema definition + field-by-field access |

### MCP layer (`facetwork/mcp/server.py`)

12 tools, all with mini-spec descriptions (when to call, what's returned, relation to other tools). The split that matters for context engineering:

- `fw_validate` — returns full structured diagnostics including `rule_id` and `docs_uri`. The single most important tool for any FFL-authoring task.
- `fw_list_handlers` / `fw_describe_handler` — read-only handler registry tools, split out of `fw_manage_handlers` so Claude can discover what handlers exist before writing FFL that references them.
- `fw_manage_handlers` — keeps the mutation paths (register, delete) and still serves list/get for backward compat, but its description nudges callers toward the dedicated read tools.

The static MCP resources are served directly from on-disk files — no Mongo needed. Path-traversal is rejected at the resource handler.

| URI pattern | What it serves |
|-------------|----------------|
| `fw://docs/rules` | Index of all documented rule_ids |
| `fw://docs/rules/{rule_id}` | Paired wrong/right examples for one rule |
| `fw://docs/grammar` | Full FFL grammar reference (file-backed) |
| `fw://docs/execution-model` | Runtime execution model (file-backed) |
| `fw://examples/canonical` | Index of canonical examples |
| `fw://examples/canonical/{name}` | A single example file (lookup by name or stem) |

## How to extend it

When you add a new validator check:

1. Pass an explicit `rule_id` to `add_error`/`add_warning` (uppercase, snake_case, prefix matches the category — `REF_*`, `WHEN_*`, `TYPE_*`, etc.).
2. Add `docs/reference/rules/{rule_id}.md` in the same change, following the template above. Use the actual error message from the validator in the "→ ..." line.
3. The MCP `fw://docs/rules` index picks it up automatically — no code change required there.

When you add a new language feature:

1. Add a small canonical example to `examples/canonical/` demonstrating it. Keep the file under ~20 lines and self-contained (no `use` statements, no external handlers).
2. Validate it (`afl <file> --check`) before committing.
3. Update `examples/canonical/README.md` with a one-line description.

## What's deliberately *not* here

- **No separate `doctor`/`suggest_fix` tool.** The validator's structured output + the per-rule docs already let Claude do the repair itself. A separate tool would either wrap an LLM (extra moving parts) or be a thin alias for `validate` (no value).
- **No second MCP server for "language" vs. "runtime".** Tool naming gives Claude the mental separation; splitting processes only adds ops cost.
- **No MCP-resource version of the canonical examples.** They live as files in the repo (version-controlled, diffable, grep-able). The MCP layer just exposes them via the protocol for clients that prefer resources to direct file reads.

## Why this matters

For SQL, HTTP, Python — Claude has an enormous prior. Validation, repair, and idiom selection happen "in the model". For FFL, none of that prior exists. The repo replaces it with **explicit affordances**: a typed error vocabulary (`rule_id`), retrievable docs at stable URIs, and a small set of canonical templates. Claude's job is reduced from "guess at the language" to "apply the system's own diagnostics" — the same job it does well for languages it was trained on.
