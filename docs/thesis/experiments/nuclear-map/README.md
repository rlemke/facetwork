# The nuclear-reactor map, in Facetwork, Snakemake and Nextflow

A like-for-like reproduction of save_earth's `BuildNuclearReactorMap` as a
[Snakemake](https://snakemake.readthedocs.io) workflow and a
[Nextflow](https://nextflow.io) pipeline, so all three engines can be **timed**
and their **outputs diffed**. Supports the comparison in
[thesis](../../thesis.md) Chapter 13.

The Facetwork original is
[`save_earth.ffl`](https://github.com/rlemke/fwh_save_earth/blob/main/src/save_earth/ffl/save_earth.ffl)
§`workflows.BuildNuclearReactorMap`: download every OSM feature tagged
`generator:source=nuclear` or `plant:source=nuclear` worldwide, then render a
MapLibre HTML map of them.

## The experimental design, and why it is this way

**All three engines call the same handler functions.** The Snakemake rules and
Nextflow processes both shell out to `scripts/download_reactors.py` and
`scripts/build_map.py`, which call
`save_earth.handlers.sources.handle_download_nuclear_reactors` and
`save_earth.handlers.maps.handle_build_map` — byte-identical work to what a
Facetwork runner dispatches. Reimplementing the Overpass query per engine would
have compared three pieces of my code and told us nothing about the
orchestrators. The scripts are shared, not copied, so they cannot drift apart.

**Each run gets its own `FW_DATA_ROOT`** (`runs/facetwork/`, `runs/snakemake/`,
`runs/nextflow/`). This is not tidiness: with a shared cache, whichever engine
ran last would skip the download and "win" by ten seconds.

## Two asymmetries you must know before reading any number

**1. Engine startup is inside two of the three numbers.** Snakemake's and
Nextflow's wall-clock includes starting the engine — a JVM boot for Nextflow, a
DAG build for Snakemake — because for them that *is* the run. Facetwork's
excludes starting the runner, because a runner is a long-lived **service** you
submit to, not a per-run process; what is measured is submit → workflow
terminal. That is a real architectural difference rather than a thumb on the
scale, but the totals answer slightly different questions and should not be read
as a pure head-to-head.

**2. Live runs are order-sensitive.** Overpass rate-limits: across four
consecutive live runs during this work, one download went from 10s to 97s. The
engine that runs later pays more, so **live numbers cannot be compared between
engines**. Use `--mock` (no network, deterministic) to compare orchestration,
and live runs only to confirm the outputs agree on real data.

## Run it

```bash
pip install snakemake
curl -s https://get.nextflow.io | bash      # or set NEXTFLOW=/path/to/nextflow
fw install domain save-earth --check

./run_comparison.sh --mock          # orchestration timing + output equality
./run_comparison.sh --mock --keep   # warm re-run: incremental behaviour
./run_comparison.sh                 # live: confirms agreement on real data
./run_comparison.sh --only snakemake,nextflow
```

Individual pieces, when something misbehaves:

```bash
snakemake -j 4 --config outdir=runs/snakemake use_mock=true
nextflow run main.nf --outdir "$PWD/runs/nextflow" --use_mock true
python scripts/download_reactors.py --outdir runs/x --use-mock   # no engine at all
python compare_outputs.py --run facetwork=runs/facetwork --run nextflow=runs/nextflow
```

## Measured, 2026-08-11 (MaxPro, local Mongo, ~15 fleet runners also live)

Mock data, so no network and no throttling — this isolates orchestration:

| | cold | warm re-run | per-stage (cold) |
|---|---|---|---|
| **Facetwork** | 4.8s | 4.7s | — |
| **Snakemake** | 5.9s | 3.7s | download 0.68s, map 0.94s |
| **Nextflow** | 3.9s | 2.8s (`cached=2`) | download 625ms, map 592ms |

**All three produce identical output** on `--mock`, cold and warm: GeoJSON
identical, HTML identical once the render timestamp is masked.

**On live data, agreement depends on how far apart the fetches are, and we saw
both outcomes.** Snakemake and Nextflow ran seconds apart and matched exactly
(1,031 features each). Facetwork's fetch was about a minute earlier and returned
1,027 — 33 features unique to it, 37 unique to the others, including a reactor
that had been renamed `O3` → `Oskarshamn 3` in between. That is OpenStreetMap
being edited live, not a pipeline difference, and it is exactly why strict
equality is checked with `--mock`. The comparison tool naming the renamed
reactor is what makes the distinction visible rather than a mystery.

**A live run also caught a real bug in the Nextflow pipeline**, which the
all-mock run could not. `--use_mock false` reaches Groovy as the *string*
`"false"`, which is truthy, so `params.use_mock ? '--use-mock' : ''` passed
`--use-mock` on **every** run: the pipeline reported success while quietly
producing a 5-feature mock dataset. `main.nf` now coerces explicitly via
`asBool()`. Worth remembering both as a Nextflow gotcha and as evidence for
running the comparison on real data even when equality cannot be asserted.

**On a two-step pipeline the engines are within ~2s of each other**, and most of
that spread is startup cost rather than per-step overhead. Chapter 13's argument
is not visible at this scale and was never going to be: Facetwork's ~2.6s of
per-step engine overhead is what matters at thousands of steps, and two steps
cannot show it. What this experiment establishes is that the three agree on the
*answer* — the durability machinery changes what a run costs and what survives a
crash, not what it produces.

**The warm re-run is where they genuinely differ, and the result is not the
expected one.** Chapter 13 says Facetwork has no incremental recomputation, and
at the *runtime* level that is true — re-running a workflow re-runs every step.
But save_earth's handlers keep their own sidecar cache, so Facetwork's re-run
steps did nothing and it finished within a second of the others. Incremental
recomputation is **handler-author work repeated per domain**, not a runtime
guarantee, and where a domain has done that work the practical difference
disappears.

**But "faster on a warm re-run" is the wrong thing to be pleased about, and this
is the important half.** An output that exists is not evidence that it is
complete, current, or produced by the code running now. A path and an mtime say
only that something was written there once. Skipping on that basis is sound
while the recipe is fixed and unsound the moment it is not — and when it is
unsound it is *silent*: the run reports success and serves the previous answer.

The companion experiment demonstrates it. In
[`state-capitals/`](../state-capitals/), editing the module that computes every
result and re-running Snakemake yields "Nothing to be done (all requested files
are present and up to date)". A rerun-trigger sees the rule's own directive text
and nothing transitively behind it, so an algorithm invoked through `shell:` is
invisible to it — and `shell:` is exactly how an algorithm team is separated
from a workflow team.

**So the safe default is the opposite of the make model: assume the algorithm
has changed and re-run, unless a key positively identifies the output.** That is
Facetwork's default, and its cost is time, not correctness. A handler may
override it — but only by answering what a timestamp cannot: was this produced
by *this version* of the code, from an upstream that is *still* what it was
(re-checked, not remembered), within a freshness window the *domain* defines?
save_earth's sidecars answer the last two; `osm` answers all three, re-fetching
Geofabrik's published `.md5` every run rather than trusting that a download once
finished. The contract is
[`cache-layout.agent-spec.yaml`](../../../../agent-spec/cache-layout.agent-spec.yaml)
under `read_protocol.reuse_decision`.

Note also that Nextflow needs `-resume` to be incremental at all, while
Snakemake decides from the output files themselves. That is a real difference
between the two, not an artifact of this harness: a plain `nextflow run` repeats
everything even with its work dir intact.

## Comparing the outputs

`compare_outputs.py` compares every run against the first one given and exits
non-zero on any difference. Two normalisations are applied, and it says so
rather than quietly claiming a byte-identical result:

| Normalised | Why |
|---|---|
| the `YYYY-MM-DD HH:MM UTC` render stamp in the HTML | `map_render` stamps generation time, so two runs a minute apart always differ |
| GeoJSON feature **order** | nothing guarantees a stable order across independent Overpass responses, and order is meaningless for a map |

Feature *content* is not normalised: if two runs disagree about a reactor's tags,
that shows up.

**The caveat the tool cannot fix:** live runs query Overpass at different
moments, so a genuine upstream edit between them is indistinguishable from a
pipeline difference — as happened here, where a minute's gap cost four features
and renamed a reactor. Strict equality is therefore checked with `--mock`. What
the tool *can* do is name the differing features, which is usually enough to
recognise an upstream edit for what it is.

## A note on the Nextflow pipeline specifically

The handlers own the on-disk layout — that contract is identical for all three
engines — so the processes write into the storage root rather than into their
task work dirs, and what they emit is a **path handle**, not a staged file.
Ordering and `-resume` work correctly, but Nextflow's file-staging and
`publishDir` machinery is bypassed, so this does not exercise its output-tracking
the way a natively-written pipeline would. The Snakemake version declares the
real files as rule outputs and does exercise Snakemake's, which is why its
incremental behaviour is file-driven above.

## Files

| | |
|---|---|
| `Snakefile` | two rules, mirroring the FFL workflow's parameters |
| `main.nf` + `nextflow.config` | the same two stages as Nextflow processes, with a trace for per-process timing |
| `scripts/download_reactors.py` | `DownloadNuclearReactors` as a standalone CLI — shared by both engines |
| `scripts/build_map.py` | `BuildMap` as a standalone CLI — shared by both engines |
| `compare_outputs.py` | N-way GeoJSON + HTML comparison, exits non-zero on difference |
| `run_comparison.sh` | runs all three under equal isolation, then compares |

`runs/` and the logs are generated; they are gitignored.
