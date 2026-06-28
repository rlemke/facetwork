# REF_UNKNOWN_FACET — Qualified facet name doesn't resolve

A fully-qualified facet name (e.g. `osm.DownloadRegion`) doesn't match any
declared facet, event facet, or workflow.

## Wrong

```ffl
namespace x {
    workflow W() => (r: String) andThen {
        d = osm.DownloadRegion(region = "ny")   // ← no such facet
        yield W(r = d.path)
    }
}
```

→ `Unknown facet 'osm.DownloadRegion'`

## Correct

Either declare the facet in the appropriate namespace, register a handler
for it (so the runtime knows how to dispatch), or — most commonly — fix
the name. Use `fw_list_handlers` (or read `fw://handlers`) to see what
the runtime knows about.

```ffl
namespace osm {
    event facet DownloadRegion(region: String) => (path: String)
}
namespace x {
    use osm
    workflow W() => (r: String) andThen {
        d = DownloadRegion(region = "ny")
        yield W(r = d.path)
    }
}
```

## Why

Qualified names look up directly into the global facet table — if it's
not there, the validator cannot tell whether you meant a typo, a missing
import, or a missing handler registration. Calling `fw_list_handlers`
before writing a step that uses a handler is the surest way to avoid this.
