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

## An existing output is not a reason to skip the step

This experiment is also where the reuse question got settled, because the
workflow is small enough to perturb deliberately.

Editing `capitals_lib.py` — the module that computes every per-state result —
and re-running Snakemake produces:

```
Nothing to be done (all requested files are present and up to date).
```

The algorithm had changed. Every one of the fifty outputs was stale. Nothing
said so, and the run reported success. Editing the rule's own `shell:` string
instead re-ran all fifty immediately, which locates the boundary exactly:
**a rerun-trigger sees the rule's own directive text and nothing transitively
behind it.** It cannot read the contents of a program it merely invokes — and
that blind spot falls on `shell:` calling an external CLI, which is the pattern
that separates an algorithm team from a workflow team.

So the presence of an output tells you that something was written there once.
It does not tell you the output is complete, or current, or produced by the code
that is in the file now.

**The safe default is therefore the opposite of the make model: assume the
algorithm has changed, and re-run, unless a key positively identifies the
output.** That posture costs time and cannot cost correctness. Skipping by
default costs nothing until it is wrong, and when it is wrong it is silent.

A key that discharges the assumption has to answer three questions a timestamp
cannot even express:

| | |
|---|---|
| **algorithm** | was this produced by the version of the code running now? |
| **upstream** | is the input still what it was — re-checked, not remembered? |
| **freshness** | has the source had a chance to change since (a domain fact, not a scheduling one)? |

Facetwork's runtime makes no attempt to answer them and re-runs, which is the
safe half of the trade; its cost is measured above. Handlers that *can* answer
them do, and then genuinely skip: `osm` re-fetches Geofabrik's published `.md5`
every run rather than trusting that a download once finished, and its derived
artifacts break the chain on a `source.sha256` mismatch. The contract is written
up in [`cache-layout.agent-spec.yaml`](../../../../agent-spec/cache-layout.agent-spec.yaml)
under `read_protocol.reuse_decision`.

This experiment's own fetch step is a small instance of the same discipline: it
records the Overpass query text alongside the data, so a changed query is
visible in the artifact rather than inferred from its age.

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

### Where the list comes from, and why it matters here

This experiment hands all three engines the same fixed list of 50 codes, so the
comparison is about scheduling rather than discovery. Real pipelines discover
their work instead, and that is where the engines diverge:

| | discovery |
|---|---|
| **Snakemake** | `glob_wildcards("data/{sample}.txt")` — evaluated at DAG-construction time, on the submitting machine, against its local filesystem |
| **Nextflow** | `Channel.fromPath(...)`, likewise resolved by the driver process |
| **FFL** | `fw.file.List(path = …, pattern = …)` — a **step**, executed on a runner, resolving the path through the storage abstraction |

The FFL form is a built-in (`facetwork/ffl/file.ffl`), so it costs one line and
no handler:

```ffl
found = fw.file.List(path = $.dir, pattern = "*.csv")
    andThen foreach p in found.paths { … }
```

It takes a local directory, an `s3://` prefix or `hdfs://` without the workflow
changing. Pointing `glob_wildcards` at `s3://…` returns an empty list — no error
— so the run builds zero jobs and reports success. On a fleet whose data lives
in MinIO rather than on the laptop that launched the run, that difference is the
whole ballgame: enumeration has to be *work*, done where the credentials and the
data are, not parse-time metaprogramming on the submitting host.

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
| **Facetwork** | 4.4s | `foreach` fan-out + `after` fan-in (6.5s before the read caching below) |

**All three produce identical output** — the same 50 states and capitals, byte
for byte.

Read the totals knowing they are not symmetric: Snakemake's and Nextflow's
wall-clock includes starting the engine, because for them that *is* the run.
Facetwork's excludes starting the runner, because a runner is a long-lived
**service** you submit to; what is measured is submit → workflow terminal.

All three land within ~1.5s of each other on 52 steps. That is closer than
Chapter 13 would lead you to expect, and the reason is that at this width the
per-step durability cost is small in absolute terms; it is what dominates at
thousands of steps, not at fifty.

### Profiling the fan-out, and what it cost

This workflow's time was almost entirely bookkeeping, not work: **execution 0.63s,
cascade 5.78s**. Instrumenting the persistence layer showed why — **11,709
`get_step` calls over a 157-step workflow**, about 75 re-reads per step, plus
3,883 block queries. The cascade was reading the same documents over and over
within a single resume.

Caching those reads for the duration of one iteration (and indexing the
pending-changes lookup, which was a linear scan) took the run from **6.5s to
4.4s** with byte-identical output, and the 200-wide no-op fan-out of thesis
§13.3 from 71.6s to 55.8s.

Worth noting what profiling *disproved*: the suspected culprit was a
full-workflow step scan in the block handler. It was called **once**. Reading the
code suggested one answer and measuring gave another.

### The same workflow, before today's cascade fix: 294.1s

Run against the commit before `69753bb` (deferred resumes keyed by workflow
rather than by step), this identical workflow took **294.1 seconds** against the
**6.5s** the same build measured afterwards — **45×** — and produced
byte-identical output. The extra 288 seconds were the stuck-step sweep
discovering, in batches, the sibling resumes that had been dropped. (Against
today's 4.4s the ratio is 67×, but that mixes in the read caching above; 45× is
the honest figure for the resume fix alone.)

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
