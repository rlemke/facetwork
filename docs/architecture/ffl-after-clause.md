# `after` — explicit ordering for invisible dependencies

**Status:** grammar + AST + emitter + source-reconstruction prototyped on branch
`ffl-after-clause`. Validator rules, runtime edge, and the `dependency_signal`
codemod are specified here but **not yet implemented**.

## 1. The problem

FFL derives execution order from **data references**. If a step consumes another
step's result, the runtime orders them and nothing else is needed:

```ffl
src = GitCheckout(repo = $.repo)
build = MavenBuild(workspace_path = src.info.workspace_path)   // ordered: reads src
```

`DependencyGraph.build` ([`facetwork/runtime/dependency.py`](../../facetwork/runtime/dependency.py))
walks each statement's args (and its mixin args) for `StepRef` nodes and unions
them into the edge set. That covers every dependency the compiler can *see*.

It does not cover dependencies that flow through a **side channel** — a shared
cache prefix, an object-store path, a scratch dir, a database table. There the
producer and consumer exchange no value at all, so the graph sees two independent
steps and is free to run them concurrently or in either order.

Two workarounds are in use across the fleet, and both are bad:

**(a) The `dependency_signal` idiom.** Declare a parameter nobody reads, and pass
an arbitrary field into it purely to manufacture an edge:

```ffl
data = DownloadMigration(force = $.force)
map = BuildMigrationMap(dependency_signal = data.country_count)   // value unused
```

35 call sites across 7 domains. It works, but it lies: the signature claims a data
dependency that does not exist, and every author has to know the convention.

**(b) Nothing at all.** When the downstream facet has no such parameter, the
ordering simply cannot be expressed. `county.atlas.BuildMasterIndex` and
`uspanel.data.BuildFindings` are in this position — their docs have to say *"line
order is not run order; run this as a separate submission."* That is a language
gap being papered over with prose.

## 2. The feature

```ffl
data = DownloadMigration(force = $.force)
map = BuildMigrationMap() after data
```

> `B after A` makes B eligible only once A has reached a **terminal successful**
> state, including every sub-block A spawned.

`after` adds an edge and nothing else. It does not change how existing
dependencies work, it is never required when a value flows between two steps, and
it introduces no new scoping concepts.

### Fan-in

The sub-block clause is what makes fan-in expressible for the first time. After a
`foreach`, the per-iteration results live in a cache, not in a value:

```ffl
counties = ListCounties(prefix = $.prefix) andThen foreach c in $.counties {
    atlas = BuildCountyAtlas(county_key = $.c, tier = $$.tier)
    yield Fanout(built = [atlas.html_path])
}

idx = BuildMasterIndex(prefix = $.prefix) after counties    // all 3,167 done
```

The runtime already reconciles a parent step against its children
(`block_execution.py` → `get_steps_by_block`), so the join point exists; `after`
exposes it to the language.

### Semantics table

| Case | Behavior |
|---|---|
| `after a, b` | conjunction — both must complete |
| A errors, no `catch` | B never runs; the block fails (identical to a data edge) |
| A errors, `catch` yields | the block terminates; B is moot |
| A errors, `catch` falls through | B runs — the catch *handled* it |
| `after` naming a step B already references | legal, idempotent (set union); warned as redundant |
| `after` on a `yield` | not supported in v1 |

Scoping is unchanged: **same block, backward only** — the rules that already
govern step references.

## 3. Grammar

```lark
step_stmt: IDENT "=" call_expr after_clause? step_body* catch_clause?

after_clause: IDENT after_targets
after_targets: IDENT ("," _NL* IDENT)*
```

Three decisions, each load-bearing:

**`after` is a contextual keyword.** It is matched as `IDENT` and validated in the
transformer, the same pattern `prompt_directive` and `script_dialect` use. Matching
it as a grammar literal would reserve the word across all of FFL — precisely the
regression that made `jenkins.deploy.DeployToEnvironment(environment: String)`
unparseable when `environment` was introduced (fixed in `aa4632d`). A parser test
pins this: `after` remains usable as a parameter, return-field and step name.

**Targets are bare step names, not expressions.** `after data`, never
`after data.count`. FFL otherwise has no bare-step-name expression
(`REF_INVALID_STEP_FORMAT`), so this is a deliberate, narrow exception rather than
a new expression form.

**A newline may follow a comma, never precede it.** `after_targets` with `_NL*`
*before* the comma is genuinely ambiguous under LALR(1) — after `after a\n` the
parser cannot tell a continued target list from the next statement. This was found
by prototyping, not by inspection; the grammar builds clean with the comma bound to
the preceding target.

## 4. AST, emitter, reconstruction

```python
@dataclass
class StepStmt(ASTNode):
    name: str
    call: CallExpr
    body: "AndThenBlock | None" = None
    catch: "CatchClause | None" = None
    extra_bodies: "list[AndThenBlock]" = field(default_factory=list)
    after: list[str] = field(default_factory=list)      # NEW
```

The emitter writes `"after": ["counties"]` **only when non-empty**, so every
existing compiled AST is byte-identical.

`workflow_source.py` (the reconstructor behind the dashboard's FFL view and the
catalog) renders the clause before any `andThen`/`catch`. This is not cosmetic:
without it the dashboard would display FFL that looks correct but has silently lost
its ordering edge. Verified round-trip: source → compile → reconstruct → re-parse
preserves `after` exactly.

## 5. Runtime

One change, in `DependencyGraph.build`'s second pass:

```python
deps = graph._extract_dependencies(stmt.args, workflow_inputs)
for mixin in stmt.mixins:
    deps |= graph._extract_dependencies(mixin.get("args", []), workflow_inputs)
deps |= {graph.name_to_id[n] for n in stmt.after}        # NEW
```

Everything downstream — readiness, the `_StepNotReady` deferral, the continuation
cascade — is untouched, because once an `after` edge is in the set it is
indistinguishable from a data edge.

**Open question to settle during implementation:** whether a parent step's terminal
state already implies all its sub-blocks are terminal, or whether `after` on a
fan-out step must wait on block reconciliation explicitly. This determines whether
fan-in works for free or needs a few lines in `block_execution.py`.

## 6. Validator rules

Six rules, each shipping with `docs/reference/rules/{rule_id}.md` in the same
change (rule-doc coverage is exact and must stay that way).

| rule_id | Trigger | Message |
|---|---|---|
| `AFTER_UNKNOWN_STEP` | target isn't a step in this block | `'after X': no step named 'X' in this block` |
| `AFTER_FORWARD_STEP` | target is declared later | mirrors `REF_FORWARD_STEP`; suggests reordering |
| `AFTER_SELF` | `s = F() after s` | `a step cannot follow itself` |
| `AFTER_CROSS_BLOCK` | target is a sibling block's step | mirrors `REF_CROSS_BLOCK_STEP`; points at `$`/`$$` |
| `AFTER_NOT_A_STEP` | `after data.count` | `'after' takes step names, not attributes — write 'after data'` |
| `AFTER_REDUNDANT` (warning) | already implied by a data reference | `'after data' is implied by data.country_count` |

`AFTER_NOT_A_STEP` and `AFTER_REDUNDANT` are the two that will fire in practice —
the first from `dependency_signal` muscle memory, the second during migration.

## 7. Migrating `dependency_signal`

Footprint: **35 call sites, 15 parameter declarations, 7 repos** (amr, conflict,
migration, noaa-weather, osm-mapping, save-earth, sentinel2) — plus county-atlas
and uspanel, which have no `dependency_signal` and gain an ordering they cannot
currently express.

### Phase 1 — ship `after` (purely additive)

No domain changes. All existing FFL compiles unchanged.

### Phase 2 — `fw ffl migrate-after` codemod

Modeled on [`facetwork/migration/relative_scope_migrator.py`](../../facetwork/migration/relative_scope_migrator.py):
same location-based rewriting so comments and formatting survive, same dry-run-diff
/ `--write` contract, same "report what I won't touch" discipline.

| Pattern | Rewrite |
|---|---|
| `F(dependency_signal = step.field)` | `F() after step` |
| `F(dependency_signal = 0)` (amr, osm-mapping) | drop the arg — a literal never created an edge |
| `F(a = 1, dependency_signal = s.x)` | `F(a = 1) after s` |
| value is a workflow input or a compound expression | **report, do not rewrite** — can't prove it's inert |

Keying on the parameter *name* is what makes this safe: `dependency_signal` is a
convention meaning "this value is never read."

### Phase 3 — remove the parameter (breaking; deliberately deferred)

Deleting it from 15 facet declarations changes handler kwargs, and domains are
**baked into the runner image** — so old image + new FFL is exactly the
`unexpected kwarg` skew this fleet has hit before, and hosts do get stranded on old
versions for weeks. Sequence:

1. Keep the parameter declared with its default; mark it deprecated in the docstring.
2. Migrate call sites (Phase 2) — old and new FFL both compile against it.
3. Only after a rollout where every live host is current, delete the parameter and
   the handler kwarg.

## 8. Deployment hazard

The catalog stores `compiled_ast`. A new-compiler AST carrying `after` executed by
an **old runner** would have the field silently ignored — the ordering vanishes and
the result is a race that looks exactly like the bug `after` exists to prevent.
Silent wrong-order is worse than a hard failure.

Mitigation: stamp a minimum-runtime marker on any flow whose AST uses `after`, and
have the submit path refuse rather than run it degraded. Cheap now, impossible to
retrofit after the first silent corruption.

## 9. Prototype status

On branch `ffl-after-clause`:

- ✅ Grammar — LALR-clean; the `_NL`-before-comma ambiguity found and fixed
- ✅ Contextual keyword — `after` still usable as an identifier (test-pinned)
- ✅ AST field, transformer, emitter (omitted when empty)
- ✅ `workflow_source` reconstruction + verified source→compile→reconstruct→reparse round-trip
- ✅ 7 parser tests; full suite green (2939 passed)
- ⬜ Validator rules + rule docs (§6)
- ⬜ Runtime edge union (§5) and the fan-out join question
- ⬜ `fw ffl migrate-after` (§7)
- ⬜ Min-runtime marker (§8)
