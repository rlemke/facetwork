# REF_CROSS_BLOCK_STEP — Reference to a step in a sibling `andThen` block

Sibling `andThen` blocks run concurrently and cannot see each other's
steps. The named step does exist — but in a different block.

## Wrong

```ffl
namespace x {
    facet A() => (out: String)
    facet B(in: String) => (r: String)
    workflow W() => (r: String) andThen {
        a = A()
    } andThen {
        b = B(in = a.out)         // ← 'a' is in the sibling block
        yield W(r = b.r)
    }
}
```

→ `Cross-block step reference: 'a' is defined in a sibling andThen block ...`

## Correct

Use a step body — `andThen` attached to a step expression — to compose
steps that depend on each other. Step bodies see the parent step's
context.

```ffl
namespace x {
    facet A() => (out: String)
    facet B(in: String) => (r: String)
    workflow W() => (r: String) andThen {
        a = A() andThen {
            b = B(in = a.out)
            yield W(r = b.r)
        }
    }
}
```

## Also applies to `foreach` collections

The collection of a `foreach` clause is a reference too, and it is subject to
the same rule — it is evaluated at block entry and can only name a step visible
in **this** block (or an enclosing one), never a sibling block's step.

```ffl
// WRONG — 'sub' is in the sibling block
workflow W(seeds: [String]) => (r: [String]) andThen {
    sub = Expand(seeds = $.seeds)
} andThen foreach item in sub.items {          // ← cross-block foreach collection
    p = Process(id = $.item)
    yield W(r = [p.out])
}
```

Fix it the same way — put the `foreach` in a **step body** so the collection
names the containing step, or split the second fan-out into its own workflow
that iterates its own input:

```ffl
// CORRECT — foreach is 'sub's step body; 'sub' is the containing step
workflow W(seeds: [String]) => (r: [String]) andThen {
    sub = Expand(seeds = $.seeds) andThen foreach item in sub.items {
        p = Process(id = $.item)
        yield W(r = [p.out])
    }
}
```

(`foreach v in $.attr` over a container attribute — an input or a pre-script
result — is always fine; only a **step** reference to a sibling block is
rejected.)

## Why

The runtime executes top-level `andThen` siblings in parallel. Allowing
cross-block references would force serialisation and make the parallelism
silent — instead the language requires you to be explicit about
ordering with a step body or `andThen when`.
