# AFTER_UNKNOWN_STEP — `after` names a step that doesn't exist here

`after` takes the names of steps **in the same block**. The named step isn't
one of them — usually a typo, or a step that lives in a nested block.

## Wrong

```ffl
namespace x {
    event facet Download() => (count: Int)
    event facet BuildMap() => (html_path: String)
    workflow W() => (p: String) andThen {
        data = Download()
        map = BuildMap() after dowload          // ← typo
        yield W(p = map.html_path)
    }
}
```

→ `'after dowload': no step named 'dowload' in this block`

## Correct

```ffl
map = BuildMap() after data
```

## Why

`after` is resolved statically so the ordering edge can be built before
anything runs. A name that resolves to nothing would silently produce **no
edge** — leaving exactly the race the clause was written to prevent — so it is
an error rather than a warning.

See [ffl-after-clause.md](../../architecture/ffl-after-clause.md).
