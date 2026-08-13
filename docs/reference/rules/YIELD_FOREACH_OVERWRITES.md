# YIELD_FOREACH_OVERWRITES — a `foreach` yield keeps only its last iteration

**Warning**, not an error: the code runs, and for a small number of workflows
last-wins is what the author wanted. It is reported because when it is *not*
wanted, nothing else tells you.

Inside a `foreach`, the yield executes once per iteration. A bare `=` **assigns**
the containing facet's return, so each iteration overwrites the previous one and
the fan-out's result is the **final** iteration's value alone. Every iteration's
step still ran, every one succeeded, and the workflow reports success — the other
N−1 results are simply not in the return.

## Wrong

```ffl
namespace x {
    event facet Hash(path: String) => (digest: String)

    facet HashEach(paths: Json) => (digests: Json)
        andThen foreach p in $.paths {
            h = Hash(path = $.p)
            yield HashEach(digests = h.digest)   // ← assigns; last iteration wins
        }
}
```

→ `'digests = h.digest' inside a foreach keeps only the LAST iteration — each one overwrites the previous, and every iteration still runs, so the loss is silent. Use 'digests += …' to aggregate across iterations, or a literal if last-wins is intended.`

Measured: over three files this returns **one** digest, while all three `Hash`
steps execute and the workflow completes successfully.

## Correct

```ffl
namespace x {
    event facet Hash(path: String) => (digest: String)

    facet HashEach(paths: Json) => (digests: Json)
        andThen foreach p in $.paths {
            h = Hash(path = $.p)
            yield HashEach(digests += h.digest)  // ← aggregates across iterations
        }
}
```

`+=` appends a scalar and extends a list, so a nested fan-out composes without
wrapping:

```ffl
        yield BuildGrid(sets += country.sets)    // country.sets is a list → extends
        yield BuildGrid(sets += [country.sets])  // append the list AS one element
```

Aggregation follows **iteration order**, not completion order, so a run is
reproducible even when iterations finish out of order.

## When last-wins is what you meant

The warning fires only for **step-derived** values (`h.digest`, `got.result`).
A literal is the same on every iteration and is left alone:

```ffl
        yield Scan(status = "partial_failure")   // no warning — deliberate
        yield Scan(count = 1)                    // no warning
```

If you genuinely want the last iteration's *computed* value, yield it to a
differently-named field, or aggregate with `+=` and read the last element
downstream — both say so in the source.

## Fanning in over artifacts rather than values

When the iterations write files or objects rather than returning values, do not
aggregate at all: order a later step with `after` and have it read the store.

```ffl
        built = BuildAll(items = $.items)          // foreach inside
        index = BuildIndex(prefix = $.prefix) after built
```

See [ffl-after-clause.md](../../architecture/ffl-after-clause.md).

## Why a warning and not an error

The installed domains contain many deliberate `yield F(status = "partial")`
literals, which this rule does not flag. Promoting the step-derived case to an
error is possible later; it starts as a warning so that discovering the problem
does not require anyone's workflow to stop compiling.
