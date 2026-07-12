# REF_DOLLAR_OVERFLOW — `$$…` walks up past the outermost container

> **Relative-scoping only.** This rule fires under container-relative
> `$`-scoping (`FW_FFL_RELATIVE_SCOPING`; see
> [ffl-relative-scoping.md](../../architecture/ffl-relative-scoping.md)).

Each extra `$` in a reference walks up one lexical container: `$` = the
immediate container, `$$` = its container, `$$$` = one more, … A chain that
walks up more levels than there are enclosing containers — past the outermost
facet/workflow — is a compile error.

## Wrong

```ffl
namespace x {
    event facet F(input: String) => (output: String)
    facet W(input: String) => (output: String) andThen {
        s = F(input = $.input) andThen {
            g = F(input = $$$.input)   // ← only two levels exist (s, W); $$$ is one too far
        }
    }
}
```

Inside `s`'s body there are two containers: `s` (reached with `$`) and the
workflow `W` (reached with `$$`). `$$$` asks for a third, non-existent level.

→ `'$$$.input' walks up 2 container level(s), past the outermost container ...`

## Correct

```ffl
namespace x {
    event facet F(input: String) => (output: String)
    facet W(input: String) => (output: String) andThen {
        s = F(input = $.input) andThen {
            g = F(input = $$.input)    // ← $$ = the workflow W's input
        }
    }
}
```

## Why

The `$` chain is a lexical, count-what-you-see operator — the depth must match
the actual nesting. Silently clamping an over-deep chain to the outermost frame
would hide a real authoring mistake (usually a miscount of the nesting), so the
compiler rejects it and tells you how many enclosing levels are available.
