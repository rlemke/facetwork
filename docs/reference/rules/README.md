# FFL Validation Rules

Each file in this directory documents one validator `rule_id`. The
`fw_validate` MCP tool returns each diagnostic with a `rule_id` and a
`docs_uri` pointing at the matching file under `fw://docs/rules/{rule_id}`.

## How to use

1. Run `fw_validate` on your FFL source.
2. For each error, fetch `fw://docs/rules/{rule_id}` to see paired
   wrong/right examples and a one-paragraph "why".
3. Apply the fix and re-validate.

## File template

Each rule file follows the same shape:

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

## Rules with full docs

See the listing under `fw://docs/rules` (the MCP server builds it from
the `*.md` files in this directory). Rule IDs without a dedicated file
still have a useful error message — open an issue or add the doc when
the gap matters.
