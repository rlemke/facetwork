# AFTER_NOT_A_STEP — `after` takes step names, not attributes

`after data` — never `after data.count`.

## Wrong

```ffl
data = Download()
map = BuildMap() after data.count               // ← attribute, not a step
```

→ `'after data.count': 'after' takes step names, not attributes`

## Correct

```ffl
map = BuildMap() after data
```

## Why

`after` exists precisely for dependencies where **no value flows** — the
producer wrote a shared cache, object store or scratch dir, and the consumer
reads it back out of band. Naming a field would suggest a data dependency; if
you genuinely need the value, pass it as an argument instead and the ordering
edge comes for free:

```ffl
map = BuildMap(count = data.count)              // data edge — no 'after' needed
```

This is the mistake the old `dependency_signal` idiom trained people into:
passing `dependency_signal = data.count` to fake an edge. With `after` the value
is no longer part of the story.

See [ffl-after-clause.md](../../architecture/ffl-after-clause.md).
