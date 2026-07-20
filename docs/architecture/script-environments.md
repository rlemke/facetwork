# Script Environments (`environment` / `in environment`)

**Status:** design — no code yet. This doc is the contract to review before
grammar or runtime work.
**Problem owner:** scripts and the libraries they need. Handlers already have
an environment story (domain packages + the baked image); FFL `script` blocks
do not — they run in whatever interpreter the claiming runner happens to have.

## 1. What and why

An FFL `script` block executes in a subprocess spawned from the runner's own
interpreter (`sys.executable`, `script_executor.py`). Whether the libraries a
script imports are present is an accident of which runner claimed the task and
what its image bakes. There is no way to say "this script needs shapely and
networkx" — let alone "this script is Scala."

An **environment** makes that dependency set a first-class, named, reviewable
declaration — like a handler, not like an ambient property of the fleet:

```
environment PyGeo {
    language = "python",
    requires = ["shapely>=2.0", "networkx>=3", "pyproj"]
}

event facet ClusterFacilities(path: String) => (out: String)
    in environment PyGeo
    script {
        ...python using shapely/networkx...
    }
```

`in environment NAME` attaches to a declaration that carries a script block.
**Absent → the default environment** (the runner's native interpreter — exactly
today's behavior, so existing FFL compiles and runs unchanged).

`language` makes polyglot explicit. `python` environments a runner can
materialize itself (§4). `java` / `scala` / `typescript` environments formalize
what the embedded-GraphHopper Java agent already does ad hoc: a foreign-runtime
agent that advertises "I provide environment X" and participates through the
same Mongo claim protocol (see
[multi-language-handlers.md](../guides/multi-language-handlers.md)).

## 2. The unifying observation

Three existing mechanisms are partial environments already:

| Mechanism | What it declares | Gap |
|---|---|---|
| Domain packages (`domains.json` extras) | pip deps per handler package | handlers only; fleet-global bake; nothing per-script |
| Java gh-router agent | a JVM runtime providing specific facets | ad hoc; nothing names or types the arrangement |
| Catalog hermetic pinned deps | exact versions a catalog run used | workflows/`use` resolution only, not script deps |

The `environment` declaration is the named thing all three gesture at. It does
NOT replace domain packages (handlers keep their mechanism); it gives scripts
the same rigor and gives foreign-runtime agents a declared identity.

## 3. Design decision — environment is a claim-routing dimension

The central choice: what does "runs in" mean at runtime? Two candidate models:

- **(a) Placement:** the task can only be claimed by a runner that *provides*
  the environment.
- **(b) Materialization:** any runner can claim, and builds the environment
  on demand.

**Decision: (a) is the contract, (b) is one way a runner comes to provide.**
The task is the routing authority, reusing the platform's one honest principle
(the same intrinsic fact on both sides of the queue — as with task lists
derived from namespaces): the compiled script task carries
`environment: {name, manifest_hash}`; a runner advertises the set of
environment manifest-hashes it provides (on its `ServerDefinition`, next to
`handlers`); `claim_task` adds `env ∈ provided` to its filter. A runner that
cannot provide the environment never sees the task — no claim-and-release
churn, no wrong-host failures. This composes with server groups rather than
duplicating them: groups gate *roles per host*, environments gate *tasks per
runner*, and a host that can't materialize an environment (arch, toolchain)
simply never advertises it.

The default environment is claimable by every runner (it is what "no filter"
means today).

## 4. Materialization (how a runner comes to provide an environment)

For `language = "python"`:

1. **Pre-baked (preferred).** At image build time, every environment declared
   in the baked domains/flows is resolved and installed as a venv under
   `/opt/fw_envs/<manifest_hash>/`. The bake-all-domains lesson applies
   verbatim: never pip-install per task or per start — a cancer-domain start
   used to cost >13 min that way.
2. **Lazy with a wheelhouse (fallback).** A runner that sees demand for an
   unprovided environment (via a periodic scan of pending env-tagged tasks —
   NOT via claiming them) materializes a venv keyed by `manifest_hash`,
   installing from a MinIO wheelhouse bucket (`s3://afl-cache/wheels/`) so
   minis under qemu don't hammer PyPI, then re-registers with the new
   environment advertised. Materialization failures are logged and the
   environment is negative-cached — the task simply stays unclaimed by that
   runner (another runner, or the fleet operator, resolves it; a task no
   runner can serve dead-letters on the existing no-handler path).

Execution is a one-line change of insertion point: `script_executor.py` swaps
`sys.executable` for `/opt/fw_envs/<hash>/bin/python`. Everything else about
script execution (subprocess isolation, restricted builtins, timeout, the
`params`/`result` contract) is unchanged.

For `language != "python"`: no materialization. Foreign environments are
*provided only* — by an agent whose deployment is out of band (the gh-router
pattern). The declaration gives the agent something to advertise and the
validator something to check scripts against (a `scala` script with no
declared provider anywhere is a lint warning, not a compile error — the agent
may join later).

## 5. Design decision — resolve-and-freeze at publish

`requires` is a *request* (`shapely>=2.0`); a run must record an *answer*.
**Decision:** environments are resolved at publish/compile time into a frozen
manifest (exact versions + hashes), and `manifest_hash` — not the loose
spec — is what tasks carry and runners advertise. Same hermetic model as the
catalog's pinned `use` resolution: re-running a workflow next month uses the
versions it was published with, not whatever PyPI serves that day. A
re-publish re-resolves and yields a new hash; both environments can coexist on
the fleet during transition (they are different venvs).

Corollary: the **default environment gets a name and a recorded identity**
(`environment default`, manifest = the runner image's freeze). Runs currently
record nothing about the interpreter that executed their scripts; with this,
every run's provenance includes it for free.

## 6. Design decision — declared in FFL, scoped like everything else

Environment declarations live **in FFL, inside a namespace**, versioned with
the flows that use them (portable through the catalog, visible to the
dashboard's reconstructed source). Not in `domains.json` — that file is the
*deployment* catalog (which repos, which replicas, where); an environment is a
*language-level* contract the script's correctness depends on. Resolution
follows normal `use` rules; `in environment` takes a qualified or same-scope
name.

Grammar sketch (LALR-friendly, mirrors existing decl shapes):

```
environment_decl := "environment" IDENT "{" env_field ("," env_field)* "}"
env_field        := IDENT "=" (string | string_list)
facet_def_tail   := ... existing ... | "in" "environment" qualified_name
```

Validator rules (each with a `rule_id` + docs page, per house rule):
- `ENV_UNKNOWN` — `in environment` names no visible declaration.
- `ENV_MISSING_LANGUAGE` — declaration lacks `language`.
- `ENV_LANGUAGE_SCRIPT_MISMATCH` — script dialect tag (e.g. `script python`)
  contradicts the environment's language.
- `ENV_AT_TOP_LEVEL` — must live inside a namespace (consistency with
  workflows/schemas).

## 7. What this is NOT (scope fences)

- **Not for handlers (v1).** Handlers keep the domain-package mechanism; the
  baked image is their environment. If environments prove out for scripts,
  `in environment` on `event facet` declarations without script blocks is a
  natural v2 that would subsume the ad-hoc token/toolchain gating handlers do
  today (e.g. PublishWebBundle's GITHUB_TOKEN check) — deliberately deferred.
- **Not containers.** An environment is an interpreter + libraries, not an
  image. Per-task container spawn is a different (heavier) machine with its
  own design doc if ever needed.
- **Not resource limits.** CPU/memory/GPU stay with server groups and the
  capacity matrix (server-groups.md §6).

## 8. Execution plan

1. **Grammar + AST + emitter + validator** — `environment` decl,
   `in environment` tail, the four rules + docs pages. Round-trip tests.
2. **Resolution + freeze** — publish-time resolver producing the pinned
   manifest + hash into the compiled flow (pip-tools-style resolution for
   python; foreign languages carry the manifest opaque).
3. **Task tagging + claim filter** — `environment_hash` on script tasks;
   `provided_environments` on ServerDefinition; `claim_task` filter clause.
   Memory-store AND Mongo-store behavior tests against the same assertions
   (lessons-learned §23 — parity gaps hide exactly this class of bug).
4. **Executor swap + bake integration** — `script_executor` venv selection;
   image build materializes declared environments; `fw install check` learns
   to report environment coverage per host.
5. **Lazy materialization + wheelhouse** — the fallback path, feature-flagged.
6. **Pilot** — one real script (e.g. a geo clustering step in fwh_osm) moved
   into a `PyGeo` environment; verified distributed on the fleet before the
   feature is documented as available (lessons-learned §23 again: not done
   until it has run distributed).

## 9. Open questions (decide at review)

- Version pinning UX: is `requires = ["shapely>=2.0"]` + freeze enough, or do
  authors need to see/commit the frozen manifest (a lockfile artifact in the
  repo) for review?
- Does `environment` carry `python = "3.12"` (interpreter version) in v1, or
  is the image's interpreter the only one and version is deferred?
- Advertising granularity: per-hash (proposed) or per-name-and-hash so the
  dashboard can say "runner provides PyGeo (2 versions)"?
- Wheelhouse population: at publish time (publisher uploads wheels for all
  fleet arches) or first-materialization (runner uploads what it built)?
- Should `fw fleet status` / the dashboard Filters page surface environment
  coverage ("PyGeo: provided by 12 runners on 3 hosts")? (Probably yes,
  cheap — it is one more field on records already shown.)
