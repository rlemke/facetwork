# ENV_LANGUAGE_SCRIPT_MISMATCH — Script dialect contradicts its environment's language

A declaration is bound to an environment whose `language` differs from the
dialect of a script block on that declaration. A bare `script { … }` block is
implicitly `python`, so binding one to a non-python environment is a real
mismatch, not a technicality.

## Wrong

```ffl
namespace geo {
    environment Jvm {
        language = "scala"
    }

    event facet Score(x: String) => (y: String)
        in environment Jvm
        script { "result['y'] = params['x']" }
}
```

→ `Script language 'python' does not match environment 'Jvm' (language 'scala')`

## Correct

```ffl
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely>=2.0"]
    }

    event facet Score(x: String) => (y: String)
        in environment PyGeo
        script { "result['y'] = params['x']" }
}
```

## Why

The environment's language selects the interpreter the script subprocess runs
under. A python script dispatched into a scala environment (or vice versa)
would fail at execution time on whatever runner claimed it; the validator
catches the contradiction at compile time instead. See
[docs/architecture/script-environments.md](../../architecture/script-environments.md).
