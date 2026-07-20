# ENV_MISSING_LANGUAGE — Environment declarations must declare a language

An `environment` declaration has no `language` field.

## Wrong

```ffl
namespace geo {
    environment PyGeo {
        requires = ["shapely>=2.0"]
    }
}
```

→ `Environment 'PyGeo' must declare a language (e.g. language = "python")`

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

The language decides everything downstream: whether a runner can materialize
the environment itself (python → a venv from the frozen manifest) or whether
it is provided-only by a foreign-runtime agent (java/scala — the polyglot
agent protocol), and which scripts may bind to it
(`ENV_LANGUAGE_SCRIPT_MISMATCH`). A language-less environment cannot be
placed. See [docs/architecture/script-environments.md](../../architecture/script-environments.md).
