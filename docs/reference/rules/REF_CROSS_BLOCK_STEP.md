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

## Exception: `andThen when` blocks *may* reference prior blocks

A **`andThen when`** block is the deliberate exception — its case conditions and
case bodies **can** name steps from prior regular `andThen` blocks. A `when` block
is a conditional gate on what already happened, so referencing an earlier block's
result is the whole point.

```ffl
// OK — the when block reads `s1` from the prior andThen block
namespace x {
    event facet GetRisk(id: Int) => (score: Int)
    facet HandleHigh(x: Int)
    workflow W(id: Int) andThen {
        s1 = GetRisk(id = $.id)
    } andThen when {
        case s1.score == 10 => { h = HandleHigh(x = s1.score) }   // ← 's1' from the sibling block
        case _ => {}
    }
}
```

The runtime makes this safe: when the `when` block initializes and a referenced
step isn't complete yet, it **defers** (raises `_StepNotReady` and re-queues)
rather than erroring, so the case is evaluated only once `s1` is ready. This is
the one place the cross-block deferral machinery is reachable from valid FFL — so
it is **not** dead code, even though regular blocks and `foreach` collections
reject the same references.

The distinction: a **regular block** or a **`foreach` collection** naming a
sibling step is rejected (this rule); a **`when` case/body** naming a prior
block's step is allowed.

## Why

The runtime executes top-level `andThen` siblings in parallel. Allowing
cross-block references from a *regular* block would force serialisation and make
the parallelism silent — instead the language requires you to be explicit about
ordering with a step body. `andThen when` is exempt because it is by definition a
gate that runs after, and on the results of, prior blocks.
