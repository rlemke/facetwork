# AFTER_SELF — a step cannot follow itself

## Wrong

```ffl
map = BuildMap() after map
```

→ `Step 'map' cannot follow itself`

## Correct

Name the step that actually has to run first:

```ffl
data = Download()
map = BuildMap() after data
```

## Why

A self-edge is a cycle: the step could never become ready. It is almost always
a copy-paste of the step's own name where the producer's name belongs.

See [ffl-after-clause.md](../../architecture/ffl-after-clause.md).
