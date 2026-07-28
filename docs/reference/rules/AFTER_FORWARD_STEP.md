# AFTER_FORWARD_STEP — `after` names a step declared later

A step may only follow steps declared **before** it, the same rule that governs
step references (`REF_FORWARD_STEP`).

## Wrong

```ffl
namespace x {
    event facet Download() => (count: Int)
    event facet BuildMap() => (html_path: String)
    workflow W() => (p: String) andThen {
        map = BuildMap() after data             // ← 'data' comes later
        data = Download()
        yield W(p = map.html_path)
    }
}
```

→ `Step 'map' cannot follow step 'data', which is not defined before it`

## Correct

Put the producer first — declaration order is how FFL expresses "this exists
already":

```ffl
data = Download()
map = BuildMap() after data
```

## Why

Ordering edges are built in declaration order, and a backward-only rule keeps
the graph acyclic by construction. Note that declaration order alone does **not**
create ordering — `after` is what does. See
[ffl-after-clause.md](../../architecture/ffl-after-clause.md).
