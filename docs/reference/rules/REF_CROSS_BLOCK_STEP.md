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

## `andThen when` obeys the same rule — attach it to the step it gates

Under relative scoping (the default), an **`andThen when`** may reference only
`$` (the step/container it is attached to) and its own same-block steps — it is
**not** exempt. A `when` that reads a *prior sibling block's* step is rejected
just like any other cross-block reference.

```ffl
// WRONG — the top-level when reads `s1` from a sibling andThen block
namespace x {
    event facet GetRisk(id: Int) => (score: Int)
    facet HandleHigh(x: Int)
    workflow W(id: Int) andThen {
        s1 = GetRisk(id = $.id)
    } andThen when {
        case s1.score == 10 => { h = HandleHigh(x = s1.score) }   // ← cross-block
        case _ => {}
    }
}
```

Attach the `when` to the step it gates. Inside the cases, `$` is that step, so
its returns are `$.field` (and `$$.field` reaches the workflow input):

```ffl
// CORRECT — the when is a step body on `s1`; $ = s1
namespace x {
    event facet GetRisk(id: Int) => (score: Int)
    facet HandleHigh(x: Int)
    workflow W(id: Int) andThen {
        s1 = GetRisk(id = $.id) andThen when {
            case $.score == 10 => { h = HandleHigh(x = $.score) }
            case _ => {}
        }
    }
}
```

To gate on **several** prior steps, pass them as facet-typed parameters to a
gating facet rather than nesting (see
[ffl-relative-scoping.md](../../architecture/ffl-relative-scoping.md) §5.7).

## Why

The runtime executes top-level `andThen` siblings in parallel. Allowing
cross-block references would force serialisation and make the parallelism silent
— instead the language requires you to be explicit about ordering with a step
body. The same reasoning applies to `andThen when`: gate on a step's result by
attaching the `when` to that step, where the result is in scope as `$`.

(The runtime's `_StepNotReady` deferral is still live — it now resolves `$.field`
references to a containing step's return that is produced just before the body
runs, rather than the old cross-block-sibling case.)
