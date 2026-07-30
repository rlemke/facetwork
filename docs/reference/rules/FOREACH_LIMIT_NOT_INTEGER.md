# FOREACH_LIMIT_NOT_INTEGER — a foreach limit must be a whole number

## Wrong

```ffl
andThen foreach c in $.counties limit "8" {
    atlas = BuildCountyAtlas(fips = $.c)
    yield BuildAll(out = atlas.path)
}
```

→ `foreach limit must be an integer literal, got string`

## Correct

```ffl
// A literal…
andThen foreach c in $.counties limit 8 { … }

// …or a parameter, so the cap can be tuned per run.
workflow BuildAll(counties: Json, width: Int) => (out: Json)
    andThen foreach c in $.counties limit $.width { … }
```

## Why

The limit is a count of concurrent iterations, so it has to be a whole number.
A string or float cannot be one, and guessing a coercion would hide a typo in
the one place the author was explicitly asking for a safety bound.

A reference is allowed and is *not* checked by this rule — its value is only
known at run time, and is verified when the fan-out starts. A reference that
resolves to a non-integer, or to anything below 1, fails the run rather than
silently reverting to unbounded width.

See [ffl-foreach-limit.md](../../architecture/ffl-foreach-limit.md).
