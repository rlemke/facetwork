# US state capitals — the same fan-out in three engines

Find the capital of every US state from **cached** OpenStreetMap data, expressed
three times: as an FFL workflow, a Snakemake workflow and a Nextflow pipeline.
The shape is one cached fetch → a **50-way fan-out** → a fan-in.

Companion to [`nuclear-map/`](../nuclear-map/), which compares the same three
engines on a two-step pipeline. Two steps could not show what fan-out costs;
fifty can. Supports [thesis](../../thesis.md) Chapter 13.

## How the capital is determined

Not from a bundled list — that would make OSM decorative. Every US state is an
OSM relation with `boundary=administrative`, `admin_level=4` and an `ISO3166-2`
code, and each carries a member with role **`admin_centre`** pointing at the
capital's node. That link is the answer; the node supplies name, coordinates and
population. All 56 such relations (50 states, DC, territories) have one.

An earlier attempt queried `capital=4` nodes directly. It returns exactly 53
nodes, but **none of them carry a state tag** — no `is_in:state`, no
`addr:state` — so there is nothing to join on. The relation's `admin_centre`
member is the join, and it is authoritative rather than inferred.

## "Only cached data" is enforced, not just documented

`fetch_state_data()` is the only function that touches the network, and it is a
no-op when the cache is present. `resolve_capital()` **raises `CacheMissing`**
if the cache is absent rather than fetching, so a 50-way fan-out cannot silently
become 50 Overpass queries. The distinct exception type exists so no code path
can quietly fall back to the network.

All three engines read **one shared, pre-warmed cache** (`cache/osm_states.json`,
16 KB) and write their fan-out and fan-in output to their own directory. The
shared cache keeps the network out of the measurement — the confound that made
nuclear-map's live timings uncomparable, and a live problem here: Overpass
returned `504 Gateway Timeout` during this work. Separate outputs let the three
answers be diffed.

## The same fan-out, three notations

| | fan-out | fan-in |
|---|---|---|
| **FFL** | `foreach s in $.states { … }` inside a facet, so the fan-out is a named *step* | `CombineCapitals() after resolved` — naming a foreach step waits for every iteration |
| **Snakemake** | `expand(STATE_JSON, state=STATE_CODES)` — asking for 50 files *is* the fan-out; there is no fan-out construct | the `combine` rule taking all 50 as `input:` |
| **Nextflow** | `Channel.fromList(states)` feeding a process — 50 elements, 50 tasks | `.collect()`, which waits for every element |

Three notations for one idea. The FFL version needs the fan-out to be a *step*
(hence a composed facet rather than a bare `andThen foreach`) because `after`
takes step names — that is what makes the fan-in expressible.

All three call the same `capitals_lib.py`, so what differs between the runs is
only what decides when each step executes. The 50 state codes come from that one
module too, so the three fan-outs cannot drift to different widths.

## Run it

```bash
./run_comparison.sh                 # all three, then diff the results
./run_comparison.sh --only nextflow
./run_comparison.sh --jobs 4        # fan-out width for Snakemake/Nextflow
```

The pieces, when something misbehaves:

```bash
python scripts/fetch_states.py  --cache-dir cache                    # the one networked step
python scripts/state_capital.py --cache-dir cache --state US-CA --out-dir /tmp/s
python scripts/combine.py       --states-dir /tmp/s --out /tmp/capitals.json
fw ffl compile --check --primary ffl/capitals.ffl
```

Facetwork's variant with a width cap is `capitals.FindStateCapitalsCapped`
(`foreach … limit $.width`) — same 50 states, same result, only concurrency
changes.

## Measured, 2026-08-11 (MaxPro, local Mongo, shared warm cache)

| | wall-clock | notes |
|---|---|---|
| **Nextflow** | **3.8s** | 50 fan-out tasks |
| **Snakemake** | 5.2s | `-j 8`; 0.065s mean per state |
| **Facetwork** | 10.0s | `foreach` fan-out + `after` fan-in |

**All three produce identical output** — the same 50 states and capitals, byte
for byte.

Read the totals knowing they are not symmetric: Snakemake's and Nextflow's
wall-clock includes starting the engine, because for them that *is* the run.
Facetwork's excludes starting the runner, because a runner is a long-lived
**service** you submit to; what is measured is submit → workflow terminal.

Facetwork is ~2× the others here, on 52 steps. That is the per-step durability
cost the thesis argues for, and at this width it is a couple of seconds rather
than the order-of-magnitude gap Chapter 13 predicts at thousands of steps.

### The same workflow, before today's cascade fix: 294.1s

Run against the commit before
[`69753bb`](../../thesis.md) (deferred resumes keyed by workflow rather than by
step), this identical workflow took **294.1 seconds instead of 10.0 — 29×** —
and produced byte-identical output. The extra 284 seconds were the stuck-step
sweep discovering, in batches, the sibling resumes that had been dropped.

Two things make this worth recording. It is the fix measured on **real work**
rather than the synthetic no-op fan-out of thesis §13.3, and it shows the failure
mode's real shape: the answer was never wrong, so nothing in the output would
have revealed the defect. Only the clock did.

## Files

| | |
|---|---|
| `capitals_lib.py` | all the logic — the network fetch, the per-state resolve, the fan-in |
| `ffl/capitals.ffl` | the FFL workflow (plus a `limit`-capped variant) |
| `handlers/capitals_handlers.py` | Facetwork handlers, thin wrappers over the lib |
| `Snakefile` | the same three stages as Snakemake rules |
| `main.nf` + `nextflow.config` | the same three stages as Nextflow processes |
| `scripts/` | CLIs over the lib, shared by Snakemake and Nextflow |
| `run_comparison.sh` | runs all three against the shared cache, then diffs |

`runs/`, `cache/` and the logs are generated; they are gitignored.
