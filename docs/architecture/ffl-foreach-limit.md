# `foreach … limit N` — bounding fan-out width

## The problem

FFL could express unbounded fan-out and nothing else. `foreach c in $.counties`
materializes one sub-block per element immediately, so a 3,167-county atlas run
creates 3,167 concurrent units of work the moment the collection resolves.

That is usually fine — the fleet is leaderless and each runner claims only what
it can serve, so wide fan-out mostly translates into healthy parallelism. It stops
being fine when each iteration consumes a **shared, non-elastic resource**:

- scratch disk on whichever runner claims it (rasters, PBF extracts, tiles);
- an external API's rate limit (Overpass, ArcGIS, Census);
- PostGIS connections.

Concurrent 51-state fan-outs once filled the Docker VM disk and put MongoDB into
a crash-loop (`reference_docker_vm_disk_incident`). The mitigation to date has
been operator discipline: serialise the heavy runs by hand and watch
`fw maint disk-guard`. That puts the knowledge in the operator's head when it
belongs in the workflow, next to the fan-out it governs — and CLAUDE.md already
asks for exactly this: *"for every shared resource (thread pool, connection,
queue): consider isolation/bulkheads."*

## The clause

```ffl
workflow BuildAll(counties: Json) => (paths: Json)
    andThen foreach c in $.counties limit 8 {
        built = BuildCounty(fips = $.c)
        yield BuildAll(paths = built.path)
    }
```

At most 8 iterations are in flight at once; a slot is refilled as each finishes.

The cap may be a literal or a reference, so it can be tuned per run without
editing the workflow:

```ffl
workflow BuildAll(counties: Json, width: Int) => (paths: Json)
    andThen foreach c in $.counties limit $.width { … }
```

`limit 1` is the explicit "serialise this fan-out" spelling. Omitting the clause
is the explicit "no cap" one — the historical default, unchanged.

## What it does and does not change

**The same elements run, in the same order, with the same aggregate result.**
Only the number in flight at once differs. A capped and an uncapped run of the
same workflow produce the same yields and the same errors; this is pinned by a
differential test that runs both and compares.

**An errored iteration still frees its slot** and refilling continues. One
failure cannot wedge the window and strand the remainder, and the block still
reports the same aggregate error at the end that an uncapped fan-out would.

**It is not a retry, a timeout, or a rate limit over time.** It bounds
concurrency only. Iterations still race for claims exactly as before, and
routing is untouched — a capped foreach is still served by whichever runners
advertise the facet.

## Implementation

Two places in `facetwork/runtime/handlers/block_execution.py`:

- `BlockExecutionBeginHandler._process_foreach` resolves the cap and creates
  only the first N sub-blocks. It stashes the full element list and the cap on
  the block step (`_foreach_values` / `_foreach_limit`).
- `BlockExecutionContinueHandler._refill_foreach_window` tops the window back
  up on each poll and reports how many elements are still unstarted, so the
  block completes only when every element has had its turn — not merely when
  everything materialized so far is terminal.

The elements are stashed rather than re-derived because re-evaluating the
iterable on each poll would re-scan every step in the workflow — ruinous on
precisely the wide fan-outs this feature exists for. The cost is paid only when
a limit is present; an uncapped foreach stores nothing and takes the original
path.

Sub-blocks are always created as a contiguous prefix (`foreach-0 … foreach-N`),
so the count of existing ones is the next index — no cursor to persist and no
way for a crash mid-refill to skip an element.

### Liveness: a stalled slot must not wedge the window

A sub-block whose work finished but whose block never cascaded to `Complete`
stays non-terminal indefinitely. Uncapped this is a slow tail — the other
iterations still run, and the national atlas run stranded ~10% this way. **Capped
it is fatal**: N stalled sub-blocks *are* the entire window, so nothing further
is ever admitted.

That happened on the first capped 3,167-county run, which stopped dead at 628
with 31 of 32 slots held by sub-blocks that had no live task. The cap converted
a latency bug into a liveness bug.

Nothing else rescues it in time:

* **repair check 7** requires *every* task terminal before it reports anything —
  one task was still running, so it correctly said "Stranded block steps: 0";
* **the stuck-step sweep** runs every 5 min, capped at 25 steps / 1.5 s per pass,
  and `get_pending_resume_workflow_ids` deliberately excludes block-`Continue`
  states, so the workflow may not even be selected.

So the window heals itself. When every slot is occupied,
`_nudge_stalled_sub_blocks` re-notifies any sub-block that has sat non-terminal
with no progress for longer than `_FOREACH_STALL_GRACE_MS` (60 s — far below the
5-minute sweep, far above a sub-second live cascade). The nudge is an ordinary
continuation task, the same signal a completing child sends, so the step
re-enters the normal state machine on whichever runner claims it. No direct
state surgery, any runner can service it, and continuations dedupe by target
step id so re-nudging cannot pile up.

The same nudge runs once every element is admitted, where a stalled straggler
blocks *completion* rather than admission — otherwise a fan-out sits at
3166/3167 forever.

It is only reached when the window is full, so a healthy run pays nothing.

### Failure handling

A limit that cannot be resolved to a positive integer **fails the run**. It does
not fall back to unbounded: the author asked for a bound precisely because
unbounded was dangerous, so silently removing it would reintroduce the stampede
at the worst moment.

Literal caps are caught at compile time
([`FOREACH_LIMIT_NOT_POSITIVE`](../reference/rules/FOREACH_LIMIT_NOT_POSITIVE.md),
[`FOREACH_LIMIT_NOT_INTEGER`](../reference/rules/FOREACH_LIMIT_NOT_INTEGER.md)).
A reference is checked for resolvability at compile time and for its value when
the fan-out starts, since only then is it known.

## Compatibility

`limit` is a **contextual keyword**, matched as a plain `IDENT` and validated in
the transformer — the same trick `in environment` uses. Six existing domain
`.ffl` files use `limit` as an ordinary parameter name, and reserving it would
have broken every one of them. It remains usable as a parameter, schema field,
and named argument.

The emitted AST omits the `limit` key entirely when the clause is absent, so an
uncapped `foreach` emits byte-identical JSON to before and no existing consumer
sees a new key.

## See also

- Canonical example: [`examples/canonical/13-foreach-limit.ffl`](../../examples/canonical/13-foreach-limit.ffl)
- Tests: `tests/runtime/test_foreach_limit.py`
