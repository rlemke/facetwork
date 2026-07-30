# FOREACH_LIMIT_NOT_POSITIVE — a foreach limit must be at least 1

## Wrong

```ffl
workflow BuildAll(counties: Json) => (out: Json)
    andThen foreach c in $.counties limit 0 {
        atlas = BuildCountyAtlas(fips = $.c)
        yield BuildAll(out = atlas.path)
    }
```

→ `foreach limit must be at least 1, got 0`

## Correct

```ffl
// Run one iteration at a time.
andThen foreach c in $.counties limit 1 { … }

// Or eight at a time.
andThen foreach c in $.counties limit 8 { … }

// Or omit the clause entirely for unbounded width.
andThen foreach c in $.counties { … }
```

## Why

`limit N` caps how many iterations are in flight at once. `limit 0` would
admit no iteration ever, so the fan-out could never finish — it is a stall
written as a cap, not a way to skip the loop.

`0` is the only non-positive value you can write as a literal (FFL has no
negative literals), but a *reference* can resolve to one at run time — that is
checked when the fan-out starts and fails the run rather than quietly
reverting to unbounded width.

If the intent is to run nothing, guard the fan-out with a `when` or pass an
empty collection; a zero-element `foreach` completes immediately and
materializes declared array returns as `[]`.

`limit 1` is the explicit "serialise this fan-out" knob, and omitting the
clause is the explicit "no cap" one — so every intent has a spelling that
isn't `0`.

See [ffl-foreach-limit.md](../../architecture/ffl-foreach-limit.md).
