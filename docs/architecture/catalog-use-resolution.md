# `use` resolution: file-based compilation vs. the catalog

How FFL `use` statements resolve, why the [Claude workflow
catalog](claude-workflow-catalog.md) resolves them differently from a file-based
compile, and what happens when a catalog workflow `use`s a namespace that lives
only in a `.ffl` file.

## `use` is declarative — it never opens a file by itself

`use some.namespace` parses to a `UsesDecl` on a `Namespace`
([`ast.py`](../../facetwork/ast.py)); the grammar rule is
`uses_decl: ("uses" | "use") QNAME`. It records a namespace name — nothing more.

Compilation always works over a **set** of already-parsed programs that are
unioned with `Program.merge` (which simply concatenates their namespaces). The
validator then checks every `use` target against the names that are actually
present in that merged set:

```python
# facetwork/validator.py
for uses_decl in namespace.uses:
    if uses_decl.name not in self._namespaces:          # not in the merged set
        self._result.add_error(
            f"Invalid use statement: namespace '{uses_decl.name}' does not exist",
            rule_id="USE_UNKNOWN_NAMESPACE",
        )
```

An unresolved `use` is an **error**, so the whole compile reports
`is_valid = False` (see [`USE_UNKNOWN_NAMESPACE`](../reference/rules/USE_UNKNOWN_NAMESPACE.md)).

So the real question is never "what does `use` load" — it's **who decides which
sources go into the merged set.** That answer differs by compile context.

## The three compile contexts

| Context | Entry point | Sources merged | Chases `use` to *load* more? |
|---|---|---|---|
| **CLI `afl file.ffl --auto-resolve`** | `FFLParser.parse_and_resolve` ([`parser.py`](../../facetwork/parser.py)) | the file **+ whatever the resolver loads** | **Yes.** `DependencyResolver` ([`resolver.py`](../../facetwork/resolver.py)) reads each `ns.uses`, then loads the missing namespace from sibling directories + `config.resolver.source_paths` (a filesystem `NamespaceIndex`) and, with `--mongo-resolve`, from the `afl_sources` MongoDB collection. |
| **Example seeder** | `_compile_ffl_files` ([`examples/__init__.py`](../../facetwork/examples/__init__.py)) | **every `.ffl` in the package** (`collect_ffl_files`), plain parse + merge | No. The caller pre-selects the whole package; cross-file `use`s resolve against that fixed set. |
| **Catalog** | `CatalogService._compile` ([`catalog/service.py`](../../facetwork/catalog/service.py)) | the revision's **own FFL + its pinned library dependencies** (`_gather_sources`) | No. Plain `FFLParser().parse(s)` — it never scans the filesystem and never reads `afl_sources`. |

Only the CLI `--auto-resolve` path *chases* a `use` out to find a definition.
The seeder and the catalog both resolve `use` purely against "the set I was
handed": for the seeder that set is *the whole package's files*; for the catalog
it is *own source + pinned dependency revisions*.

## How the catalog assembles its set

When a revision is saved, the catalog walks its `depends_on` pins — each pinned
to an exact, content-hashed library `revision_id` — and gathers their FFL
(deepest dependency first, deduped by slug):

```
merged program = [ pinned-library FFL … ]  +  [ this revision's own FFL ]
```

That merged set is what `use` resolves against. There is **no filesystem access
and no `afl_sources` lookup** — the `afl_sources` collection the CLI resolver
queries is a different collection from the catalog's `claude_workflow_revisions`
/ materialized `flows`, so the two systems never see each other's namespaces.

## What if a catalog workflow `use`s a file-based `.ffl`?

It does **not** resolve. The catalog compile only ever sees the revision's own
FFL plus its pinned catalog-library dependencies. A `use file.based.namespace`
that is defined only in an on-disk `.ffl` (and not imported into the catalog,
not pinned as a dependency) is absent from the merged program, so:

1. validation emits `USE_UNKNOWN_NAMESPACE`,
2. the revision is **saved as an invalid draft** (`is_valid = False`),
3. `publish()` refuses it, and `run()` refuses it — it can never run unattended.

### The fix: import the dependency into the catalog, then pin it

Bring the file-based FFL into the catalog as a library, then declare it as a
pinned dependency:

```bash
# 1. register the file-based namespace as a catalog library
fw ffl catalog import path/to/lib.ffl --slug some.lib --kind library --publish

# 2. author the workflow against it (depends_on pins it by revision)
#    fw_catalog_save(..., depends_on=[{"slug": "some.lib"}])
```

Now `_gather_sources` includes the library's FFL in the merge and the `use`
resolves. This is exactly what [`import-package`](claude-workflow-catalog.md)
does at scale: it merges an entire multi-file package into **one** library so all
the cross-file `use`s resolve *internally* to that library, then creates one thin
workflow entry per workflow that simply pins it.

## Why the catalog is deliberately hermetic

This is by design, not an omission. A catalog revision is content-hashed over
`ffl_source + sorted pinned-dependency hashes` and pins each dependency to a
specific immutable `revision_id`. If the catalog chased `use` out to the
filesystem (which changes) or to the mutable `afl_sources` collection, the *same*
revision could compile differently later — which is precisely the
"the workflow body changed underneath an existing run" failure the catalog exists
to prevent. The catalog trades the CLI's convenient auto-resolution for
**reproducible, pinned, self-contained** compilation.

Practical implication for authoring (including Claude): you cannot `use` a
file-based namespace directly from a catalog workflow. Either inline the
dependency, or — better — import it once as a catalog library and pin it, after
which every dependent revision is frozen against that exact library revision.

## See also

- [Claude workflow catalog](claude-workflow-catalog.md) — the design, immutability
  model, library composition, and `import` / `import-package`.
- [`USE_UNKNOWN_NAMESPACE`](../reference/rules/USE_UNKNOWN_NAMESPACE.md) — the
  validator rule emitted for an unresolved `use`.
- [FFL grammar](../reference/language/grammar.md) — `use` / `uses` syntax.
