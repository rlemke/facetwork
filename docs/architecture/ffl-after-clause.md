# `after` — explicit ordering for invisible dependencies

**Status:** implemented on branch `ffl-after-clause` — grammar, AST, emitter,
source reconstruction, six validator rules, the runtime edge, a forward-compat
feature gate, and the `fw ffl migrate-after` codemod. Not merged; the
`dependency_signal` parameter removal (§7 phase 3) is deliberately not done.

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

**Fan-in works for free.** The open question — whether a fan-out step's terminal
state already implies its sub-blocks are terminal — is answered by
`block_execution.py`: a foreach parent transitions only when
`terminal == total` over its sub-blocks, and marks itself errored if any child
errored. So `after <fan-out step>` is a true join, and a failed iteration
propagates to the dependent exactly like a failed data producer. No change was
needed there.

An `after` target that doesn't resolve is skipped rather than fatal: the
validator rejects those at compile time, so reaching one at runtime means an AST
from elsewhere — and §8's gate is the right place to refuse that, not a crash
mid-run.

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
| `F(dependency_signal = a.n + b.n + c.n)` (save-earth) | `F() after a, b, c` |
| value reads a workflow input (`$.x`) | **report, do not rewrite** — can't prove it's inert |

Keying on the parameter *name* is what makes this safe: `dependency_signal` is a
convention meaning "this value is never read."

The compound case earns its keep: save-earth's `BuildGlobalMap` passes
`olm.feature_count + superfund.feature_count + brownfields.feature_count` — a sum
whose value is discarded and whose only purpose is the three edges. `after olm,
superfund, brownfields` says that directly, and it is the fan-in shape a
total-order construct could not express.

**Verified against the real fleet:** all 7 affected domains migrate with zero
manual sites, produce 48 `after` clauses, leave no `dependency_signal` call
sites, and compile clean. Three source shapes broke a naive line-rewrite and are
now regression-tested: a sole argument on its own line (`F(\n)` is invalid FFL —
the parens must collapse), a closing paren sharing a line with `catch` (the
clause must be inserted at the `)`, not appended after `catch {`), and a
preceding argument's now-dangling comma.

### Phase 3 — remove the parameter (breaking; deliberately deferred)

Deleting it from 15 facet declarations changes handler kwargs, and domains are
**baked into the runner image** — so old image + new FFL is exactly the
`unexpected kwarg` skew this fleet has hit before, and hosts do get stranded on old
versions for weeks. Sequence:

1. Keep the parameter declared with its default; mark it deprecated in the docstring.
2. Migrate call sites (Phase 2) — old and new FFL both compile against it.
3. Only after a rollout where every live host is current, delete the parameter and
   the handler kwarg.

## 8. Deployment hazard and the feature gate

The catalog stores `compiled_ast`. A new-compiler AST carrying `after` executed by
an **old runner** would have the field silently ignored — the ordering vanishes and
the result is a race that looks exactly like the bug `after` exists to prevent.
Silent wrong-order is worse than a hard failure.

`facetwork/ast_features.py` implements the guard. The emitter stamps
`"features": ["after"]` on any program that uses such a construct — derived by
walking the emitted tree, so the marker cannot drift from the code — and
`check_supported()` refuses a program declaring features this runtime doesn't
implement. `fw ffl run` calls it before submitting and exits with a message
telling the operator to roll the runner rather than execute degraded.

**Be clear about what this cannot do.** It protects every runtime that *has* the
guard. It cannot protect runtimes older than the guard itself — they predate the
check and ignore both the feature list and the field it describes. For those the
operational rule is unchanged and unavoidable: **roll the fleet before using a new
construct.** That is not hypothetical here — two hosts sat on a months-old image
while the others moved on. The guard's value is that from now on, every *future*
construct gets refusal instead of silent misbehavior.

The `KNOWN_AST_FEATURES` set is the contract: add a name there in the same change
that teaches the runtime to honor it, never earlier.

## 9. Status

On branch `ffl-after-clause` (not merged):

- ✅ Grammar — LALR-clean; the `_NL`-before-comma ambiguity found by prototyping
- ✅ Contextual keyword — `after` still usable as an identifier (test-pinned)
- ✅ AST field, transformer, emitter (omitted when empty, so old ASTs are unchanged)
- ✅ `workflow_source` reconstruction + source→compile→reconstruct→reparse round-trip
- ✅ Six validator rules + six rule docs; rule-doc coverage still exact
- ✅ Runtime edge; fan-in confirmed to work without touching `block_execution.py`
- ✅ Feature gate (`facetwork/ast_features.py`) + submit-time refusal
- ✅ `fw ffl migrate-after` — all 7 domains migrate clean, compile, 0 manual sites
- ✅ 33 new tests (7 parser, 10 validator, 6 runtime, 10 migrator); suite green (2965)
- ⬜ **Phase 3 not done on purpose:** removing the `dependency_signal` parameter
  from the 15 facet declarations and their handlers. That is the breaking half and
  must wait for a fleet where every live host runs an image that no longer passes
  it (§7).

### Deliberately left undone

`after` on a `yield`, and any cross-block form. Both would need new scope rules;
neither has a motivating call site in the current domains.
