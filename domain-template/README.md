# Facetwork Example Template

Skeleton for a **standalone Facetwork domain package**. Copy this directory
to a new git repo, rename `domain_template` to your example's import name,
and you have an installable example that Facetwork's runner and seeder will
discover automatically — no changes to the Facetwork repo required.

## Layout

```
domain-template/
├── pyproject.toml                          # declares facetwork.domains entry point
├── README.md
├── src/domain_template/
│   ├── __init__.py                         # exports `example: ExamplePackage`
│   ├── handlers/
│   │   ├── __init__.py                     # register_all_registry_handlers(runner)
│   │   └── greeter_handlers.py
│   └── ffl/
│       └── greeter.ffl
└── tests/
```

The package exposes a single attribute via the `facetwork.domains` entry
point group:

```toml
[project.entry-points."facetwork.domains"]
domain-template = "domain_template:example"
```

`example` is an `ExamplePackage` instance from `facetwork.domains` with the
example's display name, FFL directory, and a `register_handlers(runner)`
callable.

## Install & run

```bash
# In the example repo:
pip install -e .

# In the Facetwork repo (no edits needed):
fw ffl seed --include domain-template
fw runner start --example domain-template
```

`fw runner start --example NAME` and `fw ffl seed` discover
both in-repo `examples/<name>/` directories and pip-installed packages
declaring the `facetwork.domains` entry point — installed packages take
precedence on name collision.

## Optional: per-runner env overrides

If your example needs runner env overrides (e.g. longer execution timeouts),
populate `runner_env` on the `ExamplePackage`:

```python
example = ExamplePackage(
    name="domain-template",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=register_all_registry_handlers,
    runner_env={
        "AFL_TASK_EXECUTION_TIMEOUT_MS": "14400000",
    },
)
```

These are exported into the runner's environment by `fw runner start`.

## Migrating an existing `examples/<name>/` directory

1. Create a new git repo from this template.
2. Rename `domain_template` → your example's package name (e.g. `osm_geocoder_facetwork`).
3. Move `examples/<name>/handlers/` → `src/<package>/handlers/`.
4. Move `examples/<name>/ffl/` (and any nested `ffl/` dirs) → `src/<package>/ffl/`.
5. Update `module_uri` strings in your handler registrations from
   `file:///.../handlers/foo.py` (or relative names like `handlers.foo`) to
   the package-qualified form `<package>.handlers.foo`.
6. If `examples/<name>/runner.env` exists, copy its values into
   `runner_env={...}` on the `ExamplePackage`.
7. `pip install -e .` from your new repo, then verify with
   `fw ffl seed --include <name>`.
8. Remove `examples/<name>/` from the Facetwork repo.

## Seed-stability invariant

`fw ffl seed` and the per-runner entrypoint both call
`facetwork.domains.seed_example_flows`, which seeds your FFL under
`namespace_id = "example:<name>"`. The seeder is **UUID-stable across
re-runs**: re-seeding the same package reuses its existing
`FlowDefinition.uuid` and `WorkflowDefinition.uuid` rather than
regenerating them.

This matters when a runner restarts while a bootstrap task is in
flight — the task carries `flow_id` + `workflow_id` in its payload and
will fail with "Flow not found" if the seeder rotates them. If you
fork `seed_example_flows` for a custom seeding flow, preserve the
invariant: look up existing rows by `name.path` (for flows) and by
`name` within the flow (for workflows), and `replace_one(upsert=True)`
on the existing UUID rather than `generate_id()`.
