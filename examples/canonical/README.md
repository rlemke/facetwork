# Canonical FFL Examples

Small, validator-clean FFL files demonstrating the language's core patterns.
Use these as templates when authoring new workflows. Each file is short and
fully self-contained (no `use` statements, no external handlers required to
parse + validate).

These files are also exposed via the MCP server at `fw://examples/canonical/`.

| File | Pattern |
|------|---------|
| [`01-namespace-facet.ffl`](01-namespace-facet.ffl) | Smallest valid file: a namespace with a single facet. |
| [`02-event-facet-handler.ffl`](02-event-facet-handler.ffl) | Event facet — the canonical agent boundary. |
| [`03-workflow-andthen.ffl`](03-workflow-andthen.ffl) | Workflow with two sequential steps in one `andThen`. |
| [`04-workflow-foreach.ffl`](04-workflow-foreach.ffl) | `andThen foreach` to fan out a step across a collection. |
| [`05-workflow-when.ffl`](05-workflow-when.ffl) | `when` block with a default branch. |
| [`06-workflow-mixin.ffl`](06-workflow-mixin.ffl) | Mixin composition (`with Timestamp()`) on a workflow signature. |
| [`07-schema-instantiation.ffl`](07-schema-instantiation.ffl) | Schema definition + instantiation, with field-by-field access. |
| [`08-step-reference.ffl`](08-step-reference.ffl) | Pass a whole step by reference (FacetRef): `$.<fref>.field` reads the upstream step's attributes. |
| [`09-facetref-mixin-alias.ffl`](09-facetref-mixin-alias.ffl) | Mixin aliases on FacetRef consumers: `with M() as m1` enables `$.<fref>.m1.field`. |
| [`10-sys-log-assert.ffl`](10-sys-log-assert.ffl) | Inline diagnostic statements: `sys.log(...)` writes Splunk JSON, `sys.assert(...)` enforces runtime invariants. Demonstrates new operators `in`, `not in`, `contains`, `startsWith`, `endsWith`. |
| [`11-environment-script.ffl`](11-environment-script.ffl) | Named execution environment (`environment` + `in environment`): a script bound to a frozen dependency manifest, executed only on runners providing it. Braces take raw code. |
| [`11-author-teams.ffl`](11-author-teams.ffl) | Ownership annotation mixins: `with Author(email = …)` / `with Teams(names = […])` tag a workflow's author and teams (read by the runtime to attribute and team-filter runs). |
| [`12-step-after.ffl`](12-step-after.ffl) | `after` clause: explicit ordering for an **invisible** dependency (producer wrote a shared cache/object store, no value flows). Shows the `after a, b` fan-in and, by contrast, the value reference that orders itself — writing `after` for that is `AFTER_REDUNDANT`. |
| [`13-foreach-limit.ffl`](13-foreach-limit.ffl) | `foreach … limit N`: a bulkhead on fan-out width — at most N iterations in flight, slots refilled as they finish. Same elements, same result; only concurrency changes. |
| [`14-builtin-file-facets.ffl`](14-builtin-file-facets.ffl) | Built-in `fw.file.*` — list/read/write/copy/hash with **no handler to write**, and storage-aware (local, `s3://`, `hdfs://`). `List` is the enumerate-then-fan-out primitive: the width is discovered at run time. |
| [`15-builtin-http-facets.ffl`](15-builtin-http-facets.ffl) | Built-in `fw.http.*` — fetching whose **reuse decision is sound by default** (conditional GET / published checksum / `max_age_hours`), the provenance sidecar every fetch leaves, and gating derived work on `was_cached`. |

## Conventions worth noticing

- **Workflows and facets must live inside a namespace.** Top-level
  declarations of either are flagged by `WORKFLOW_AT_TOP_LEVEL` /
  `SCHEMA_AT_TOP_LEVEL`.
- **References are always `step.field` or `$.input` form.** A bare step name
  (`order` instead of `order.sku`) is not a valid expression — the grammar
  requires at least one `.field` segment.
- **`foreach` variables are accessed via `$.<varname>`** inside the block,
  the same way as workflow inputs.
- **Yield targets** must be the containing facet *or* one of its declared
  mixins — yields cannot reference arbitrary other facets.
