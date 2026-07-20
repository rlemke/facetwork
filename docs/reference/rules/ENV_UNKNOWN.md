# ENV_UNKNOWN — `in environment` names no visible environment declaration

A declaration's `in environment NAME` clause references an environment that is
not declared in the current namespace, any `use`d namespace, or by qualified
name.

## Wrong

```ffl
namespace geo {
    event facet Cluster(path: String) => (out: String)
        in environment PyGeo
        script { "result['out'] = params['path']" }
}
```

→ `Unknown environment 'PyGeo' — no environment declaration with this name is visible from namespace 'geo'`

## Correct

```ffl
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely>=2.0", "networkx"]
    }

    event facet Cluster(path: String) => (out: String)
        in environment PyGeo
        script { "result['out'] = params['path']" }
}
```

## Why

The environment is the contract the script's correctness depends on — which
interpreter and which libraries it runs against. An unresolvable reference
means the runtime could not tag the script's tasks with a manifest, so no
runner could ever be matched to them. Environments resolve like schemas and
facets: current namespace first, then `use`d namespaces, or fully qualified.
See [docs/architecture/script-environments.md](../../architecture/script-environments.md).
