# ENV_AT_TOP_LEVEL — Environments must be defined inside a namespace

An `environment` declaration appears at the top level. All environments must
live inside a namespace.

## Wrong

```ffl
environment PyGeo {
    language = "python",
    requires = ["shapely>=2.0"]
}
```

→ `Environment 'PyGeo' must be defined inside a namespace. Top-level environments are not allowed.`

## Correct

```ffl
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely>=2.0"]
    }
}
```

## Why

Environment references resolve through the namespace import graph (`use`),
exactly like schemas (`SCHEMA_AT_TOP_LEVEL`) and workflows
(`WORKFLOW_AT_TOP_LEVEL`). A top-level environment would live in a global
flat namespace with no resolution rules. The grammar rejects the top-level
form at parse time; this rule also guards merged or hand-built ASTs. See
[docs/architecture/script-environments.md](../../architecture/script-environments.md).
