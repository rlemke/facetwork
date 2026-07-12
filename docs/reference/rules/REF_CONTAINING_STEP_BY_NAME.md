# REF_CONTAINING_STEP_BY_NAME — Naming the enclosing step inside its own body

> **Relative-scoping only.** This rule fires under container-relative
> `$`-scoping (`FW_FFL_RELATIVE_SCOPING`; see
> [ffl-relative-scoping.md](../../architecture/ffl-relative-scoping.md)). Under
> the legacy resolver the enclosing step *is* nameable and this rule is silent.

Inside a step's body/clauses the step's own name is **not in scope** — its
name is declared in the *parent* block, not this one. Reach the step's
attributes (params and returns alike) through `$`; reach an outer container
with `$$`, `$$$`, …

## Wrong

```ffl
namespace x {
    event facet F(input: String) => (output: String)
    facet W(input: String) => (output: String) andThen {
        s = F(input = $.input) andThen {
            g = F(input = s.output)   // ← 's' is the enclosing step; not nameable here
        }
    }
}
```

→ `Cannot name the enclosing step 's' directly: its name is not defined in this
block. Reach its attributes via '$.output' ...`

## Correct

```ffl
namespace x {
    event facet F(input: String) => (output: String)
    facet W(input: String) => (output: String) andThen {
        s = F(input = $.input) andThen {
            g = F(input = $.output)   // ← $ = the containing step 's'
        }
    }
}
```

## Why

Under relative scoping, `$` denotes the **immediate lexical container** — for a
step body that container *is* the step, and its whole attribute surface (params
∪ returns) is available as `$.field`. Allowing the step to also refer to itself
by name would give two spellings for the same thing and blur the levels the `$`
/ `$$` chain makes explicit. Same-block sibling steps stay nameable; only the
containing (and any further-out) step must be reached via `$`.
