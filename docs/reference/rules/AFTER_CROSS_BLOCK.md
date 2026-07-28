# AFTER_CROSS_BLOCK — `after` names a step outside this block

`after` follows exactly the scoping rules of step references: **same block
only**. A step in an enclosing block, or in a sibling `andThen` block, is not
nameable — see `REF_CROSS_BLOCK_STEP` and `REF_CONTAINING_STEP_BY_NAME`.

## Wrong

```ffl
namespace x {
    event facet Download() => (count: Int)
    event facet Fetch() => (items: Json)
    event facet Render(v: String) => (p: String)
    workflow W() => (p: String) andThen {
        data = Download()
        outer = Fetch() andThen foreach i in $.items {
            r = Render(v = $.i) after data       // ← 'data' is in the outer block
            yield W(p = r.p)
        }
    }
}
```

→ `'after data': 'data' is not defined in this block (it belongs to an enclosing
block, which already runs first)`

## Correct

Drop it. A nested block only runs once its containing step is running, so every
step of an enclosing block has **already** completed or is the container itself
— the ordering you were asking for is guaranteed:

```ffl
r = Render(v = $.i)
```

For a **sibling** `andThen` block the answer is different: those run
concurrently and genuinely cannot be ordered against each other. Restructure so
the dependent work is a step body of the step it depends on, exactly as
`REF_CROSS_BLOCK_STEP` describes.

## Why

Allowing `after` to reach across blocks would let it silently serialise
top-level blocks that the runtime deliberately runs in parallel — hiding the
cost in a one-word clause.

See [ffl-after-clause.md](../../architecture/ffl-after-clause.md).
