# PARSE_ERROR — FFL source failed the grammar parser

The source did not parse — there is a syntax error before semantic
validation can even run. This rule is reported by the MCP `fw_validate`
tool when it catches a `ParseError` from the parser; the validator
itself never emits it.

The parser's exception message includes the line, column, and the
expected tokens at the failure point. Read those carefully — they tell
you what the grammar wanted to see next.

## Common shapes

| Symptom | Likely cause |
|---------|--------------|
| `Expected one of: IDENT … FOREACH` then `(` | `andThen foreach (var in expr)` — drop the parens, the syntax is `andThen foreach var in expr { ... }`. |
| `Expected one of: DOT` after a bare identifier in an expression | Step references must be `step.field` — at least one `.field` segment is required. Bare step names are not valid expressions. |
| `Expected one of: LPAR` after a workflow signature `=>` clause split across lines | Keep `workflow X(...) => (...)` on one line. The grammar doesn't tolerate a newline between the return clause and the `andThen`/mixin tail. |
| `Unexpected token` on a value | Argument values must be a literal, an `$.input` reference, a `step.field` reference, or a parenthesised expression. Bare names are not allowed. |

## Where to look next

- Read [`fw://docs/grammar`](fw://docs/grammar) for the canonical FFL
  grammar.
- Skim [`fw://examples/canonical`](fw://examples/canonical) for short
  examples covering all the major syntactic forms.

## Why this is different from other rules

Other `rule_id`s are produced by the *semantic* validator after the AST
is built. `PARSE_ERROR` is structural — there's no AST to walk yet.
Once the source parses, downstream rules (`REF_*`, `WHEN_*`, `TYPE_*`,
…) will start firing for higher-level mistakes.
