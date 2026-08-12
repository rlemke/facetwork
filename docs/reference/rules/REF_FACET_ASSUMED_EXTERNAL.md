# REF_FACET_ASSUMED_EXTERNAL

**Severity: warning.** The source still compiles.

A facet is called by a name that is not declared anywhere the compiler can see,
*and* a very similar name is declared. The call is being treated as a reference
to an external facet — which is legal — but the near-match suggests a typo.

## Why this is a warning and not an error

Calling a facet that is not in the current compilation unit is legitimate: a
workflow may reference a library compiled separately, and the runtime resolves
handlers by facet name at execution time, not at compile time. Making it an
error would break that.

But a **typo takes exactly the same path**. `ResolveCaptal` is not declared,
so it is assumed external, so it compiles clean — and then fails at run time
with "no handler for facet `demo.ResolveCaptal`", on a different machine,
minutes or hours later, with nothing pointing back at the line that caused it.

The warning fires **only when there is a close candidate to name**. With no
near-match the reference really is external and nothing is reported, which is
what keeps this signal worth reading rather than something to filter out.

## Wrong

```ffl
namespace demo {
    event facet ResolveCapital(code: String) => (out: String)

    workflow W() => (n: String) andThen {
        r = ResolveCaptal(code = "US-CA")   // typo: compiles, fails at run time
        yield W(n = r.out)
    }
}
```

```
Warning: Facet 'ResolveCaptal' is not declared here and is being treated as
external — did you mean 'demo.ResolveCapital'? (an undeclared facet compiles,
then fails at run time with no handler) at line 5, column 13
```

## Right

```ffl
r = ResolveCapital(code = "US-CA")
```

## When the warning is expected

If the facet really does live in another compilation unit and merely *resembles*
a local name, the warning is noise. Two ways to silence it honestly:

- **Qualify the call** — `other.ns.ResolveCaptal(...)`. A qualified name that is
  not found is reported as `REF_UNKNOWN_FACET` only when its namespace is known,
  so an genuinely external namespace passes quietly.
- **Compile the library alongside it** — pass the declaring source with
  `--library`, and the facet resolves normally.

## See also

- [`REF_UNKNOWN_FACET`](REF_UNKNOWN_FACET.md) — a *qualified* name that does not resolve
- [`REF_AMBIGUOUS_FACET`](REF_AMBIGUOUS_FACET.md) — a short name matching several namespaces
