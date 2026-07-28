# AFTER_REDUNDANT — the ordering already exists (warning)

A **warning**, not an error: the workflow is correct, but the `after` is doing
nothing.

Two cases raise it:

1. The step already **references** the step it names — a data edge orders them.
2. The same target is listed twice.

## Warned

```ffl
data = Download()
map = BuildMap(count = data.count) after data   // ← 'data.count' already orders it
```

→ `'after data' is redundant: step 'map' already references 'data', which orders it`

## Correct

```ffl
map = BuildMap(count = data.count)
```

## Why

`after` is meant to be **rare** and to carry information: "these steps are
coupled through something the compiler cannot see." When it decorates a
dependency that is already visible, it stops meaning that — and a reader can no
longer trust that an `after` marks a hidden coupling.

Keeping this a warning rather than an error is deliberate: the code is not
wrong, and mechanical migrations from `dependency_signal` can legitimately
produce a redundant clause on the way to being cleaned up.

See [ffl-after-clause.md](../../architecture/ffl-after-clause.md).
