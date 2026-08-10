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

## ⚠️ Status: root cause fixed; cleared for moderate fan-outs, not yet for 3,000-wide

The clause is implemented, tested and shipped. It used to amplify a pre-existing defect —
leaf steps parked at `state.statement.blocks.Begin` after their task completed and were
never cascaded onward — which **has since been root-caused and fixed** (see below); a
capped fan-out now survives a runner kill unattended at moderate scale. What has *not* been
re-run since the fix is the 3,000-wide national atlas that originally froze, so that one
still needs its own confirming run.

Unbounded, that stranding is survivable — the 3,167-county national run stranded ~10% and
still finished. Capped, it is fatal: stranded sub-blocks hold every window slot, so
admission stops and the run freezes (observed at 628/3,167, then again at 1,368).

`fwh_county_atlas.BuildAtlasFanout` was retrofitted with a cap and **reverted** for this
reason. Safe uses today are short or narrow fan-outs where a runner restart is unlikely
mid-run. See the liveness section below and county-atlas §9.7.

### Root cause found and fixed — fleet verification still outstanding

The stranding was root-caused to **two** defects that had to hold together, both now
fixed with regression tests (`tests/runtime/test_deferred_step_wakeup.py`):

1. **The deferral was dropped.** A handler that cannot proceed parks its step with
   `stay(push=True)` and deliberately does *not* mark a parent dirty — the parent is
   waiting on that very step, so notifying it only yields a paused re-evaluation
   (measured: resuming the sub-block gives `iterations=0`/PAUSED, resuming the parked
   step itself completes it). But `_process_step`'s "step was modified" branch was
   guarded on `not result.continue_processing`, which is exactly false for a deferral,
   so the update was discarded: `push_me` never reached the store, no parent was
   dirtied, and `has_changes` stayed false — a sweep over such a step committed
   *nothing*, which is why the stuck-step sweep appeared to have no effect on them.
2. **`push_me` was not persisted at all.** It was absent from the step document, so
   `resume_step`'s stuck-sibling scan — the one mechanism designed to re-queue a parked
   step, which tests `transition.push_me` read back off the store — saw False for every
   step. The rescue path was dead on the Mongo store regardless of (1).

With both fixed, a parked step is recorded once (on the False→True edge, so re-sweeping
is not a write storm) and is found by the sibling scan when a sibling lands, which is the
wake-up the tail was missing.

Two earlier fixes for this same stall (the window nudge and the program-AST fallback) were
plausible code-reading stories that each cost a bake+rollout and changed nothing, so the
bar here is empirical.

### Acceptance run — passed at small scale (2026-08-10)

The stated criterion is *"a capped fan-out survives a runner restart unattended"*. On the
deployed fix (v146):

| | |
|---|---|
| Workload | 60 iterations, `limit 6`, one runner (`--max-concurrent 4`) |
| Interruption | runner **SIGKILL**ed mid-flight — 1 task in flight, 29 iterations not yet admitted |
| Result | restarted once, then left alone: **completed in ~135s**, workflow `completed` |
| Tasks | 61/61 completed, **0** dead-lettered or failed |
| Recovery | exactly **1** task re-claimed (`retry_count=1`) — the killed one, recovered by the reaper |

Pre-fix this is the shape that stalled: the killed iteration's leaf parks, holds a window
slot, and admission stops. It did not — no watchdog, no `repair-workflow`.

**What this does and does not license.** The criterion is met, so the mechanism is sound
end to end under a restart. It is *not* the scale that broke: the original failures were a
3,167-county fan-out over hours across ~21 runners (froze at 628, then 1,368), and a 60-item
run on one runner cannot exercise the contention, sweep cadence and multi-runner
re-claim that made stranding common there. Treat the cap as safe for moderate fan-outs;
before re-enabling it on `fwh_county_atlas.BuildAtlasFanout`, run the national atlas capped
and confirm it finishes without an external watchdog.

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

The intended self-heal — **which does not currently work.** When every slot is occupied,
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

**Verified ineffective in production.** The nudge never fires on the real stall: it sits
behind the foreach block's Continue handler, and the resume that would invoke that handler
returns `iterations=0` before reaching it. A second attempt (`_get_program_ast`, a
persistence fallback for the program AST) also failed — the AST was never missing.

What the evidence actually shows, from testing rather than reading the code:

| Resume target | Result |
|---|---|
| Sub-block (`block.execution.Continue`) | `iterations=0`, `PAUSED` — correct, it waits on its child |
| **Leaf (`statement.blocks.Begin`)** | **`iterations=4` → `Complete` immediately** |

The sweep classifies all of these as block steps (`0 leaf + 68 block steps` in its own
log) and resumes the parents, which cannot move. The real fix belongs in that
classification and ordering, and must be validated against a capped fan-out surviving a
runner restart unattended.

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
