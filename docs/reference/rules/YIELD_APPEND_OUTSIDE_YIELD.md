# YIELD_APPEND_OUTSIDE_YIELD — `+=` used somewhere it cannot mean anything

`+=` marks a yield argument as **aggregating**: when the same target is yielded
to repeatedly — once per `foreach` iteration — the values accumulate instead of
the last overwriting the rest.

A call argument is evaluated exactly once, so there is nothing to accumulate
with. Accepting `+=` there would make it a second spelling of `=`, and a reader
would reasonably expect it to mean something.

## Wrong

```ffl
namespace x {
    event facet Fetch(url: String) => (body: String)

    workflow W(u: String) => (out: String) andThen {
        got = Fetch(url += $.u)      // ← a step call, not a yield
        yield W(out = got.body)
    }
}
```

→ `'url += …' is only meaningful in a yield, where repeated yields to the same target (one per foreach iteration) aggregate. A call argument is evaluated once — use '='.`

The same applies to `sys.log(...)` arguments and to mixin arguments on a step
call.

## Correct

```ffl
namespace x {
    event facet Fetch(url: String) => (body: String)

    facet FetchAll(urls: Json) => (bodies: Json)
        andThen foreach u in $.urls {
            got = Fetch(url = $.u)           // `=` — evaluated once per iteration
            yield FetchAll(bodies += got.body)   // `+=` — aggregates across them
        }
}
```

## Where `+=` *is* allowed

Only in a `yield`, including on a yield's mixin arguments, because those route
to a mixin sub-step that repeated yields also accumulate into:

```ffl
        yield W(total += x.value) with Report(lines += x.line)
```

Outside a `foreach` a `+=` yield is legal but pointless — one yield, one value,
wrapped in a one-element list. It is not flagged, because a facet whose body
gains a `foreach` later should not have to change its yields.

Related: [YIELD_FOREACH_OVERWRITES](YIELD_FOREACH_OVERWRITES.md).
