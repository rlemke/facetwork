# The nuclear-reactor map, in Snakemake and in Facetwork

A like-for-like reproduction of save_earth's `BuildNuclearReactorMap` as a
[Snakemake](https://snakemake.readthedocs.io) workflow, so the two engines can
be **timed** against each other and their **outputs diffed**. Supports the
Nextflow/Snakemake comparison in [thesis](../../thesis.md) Chapter 13.

The Facetwork original is
[`save_earth.ffl`](https://github.com/rlemke/fwh_save_earth/blob/main/src/save_earth/ffl/save_earth.ffl)
§`workflows.BuildNuclearReactorMap`: download every OSM feature tagged
`generator:source=nuclear` or `plant:source=nuclear` worldwide, then render a
MapLibre HTML map of them.

## The experimental design, and why it is this way

**Both engines call the same handler functions.** The Snakemake rules invoke
`save_earth.handlers.sources.handle_download_nuclear_reactors` and
`save_earth.handlers.maps.handle_build_map` — byte-identical work to what a
Facetwork runner dispatches. Reimplementing the Overpass query in Snakemake
would have compared *my* Python to the domain's and told us nothing about the
orchestrators. The only thing that differs between the runs is what decides when
each step executes.

**Each run gets its own `FW_DATA_ROOT`.** `runs/facetwork/` and
`runs/snakemake/` are separate storage roots, so neither engine can read the
other's cache. This is not tidiness: with a shared cache whichever engine ran
second would skip the download entirely and "win" by 20 seconds.

**What is deliberately NOT equalised** is the durability machinery. Facetwork's
wall-clock includes starting a runner, writing a step row and a task row per
step, and a claim poll — roughly 2.6s of engine overhead per step
([`paper-environment-provisioning.md`](../../paper-environment-provisioning.md) §4).
That is the cost of the durable, distributed, resumable execution Snakemake does
not attempt, and hiding it would make the comparison flattering and useless. A
two-step pipeline is precisely the shape where that overhead is worst.

## Run it

```bash
# Snakemake needs to exist; the handlers need the venv that has fwh_save_earth.
pip install snakemake
fw install domain save-earth --check

./run_comparison.sh --mock     # deterministic — for comparing OUTPUT
./run_comparison.sh            # live Overpass  — for comparing TIME
```

Use `--mock` when you care whether the outputs match and `--mock`-free when you
care how long each takes. The reason is in the next section.

Individual pieces, when something misbehaves:

```bash
snakemake -j 4 --config outdir=runs/snakemake use_mock=true
python scripts/download_reactors.py --outdir runs/x --use-mock   # no Snakemake at all
python compare_outputs.py --facetwork runs/facetwork --snakemake runs/snakemake
```

## Comparing the outputs

`compare_outputs.py` compares the GeoJSON and the rendered HTML and exits
non-zero on any difference. Two normalisations are applied, and it says so
rather than quietly claiming a byte-identical result:

| Normalised | Why |
|---|---|
| the `YYYY-MM-DD HH:MM UTC` render stamp in the HTML | `map_render` stamps generation time, so two runs a minute apart always differ |
| GeoJSON feature **order** | nothing guarantees a stable order across two independent Overpass responses, and order is meaningless for a map |

Feature *content* is not normalised: if the runs disagree about a reactor's tags,
that shows up.

**The caveat the tool cannot fix:** two live runs query Overpass at different
moments, so a real upstream edit between them is indistinguishable from a
pipeline difference. That is why strict equality is checked with `--mock`, whose
data is deterministic, and timing is measured live.

## Measured, 2026-08-11 (MaxPro, local Mongo, ~15 fleet runners also live)

| | Snakemake | Facetwork | outputs |
|---|---|---|---|
| cold, live Overpass | **12.6s** (download 10.1s, map 0.7s) | 13.6s | agree |
| cold, `--mock` | **2.9s** | 4.7s | agree |
| warm re-run (`--keep`, live) | 2.8s | **2.2s** | agree |

**The outputs agree, which is the headline.** Not only under `--mock` but on
the live runs too — the two engines fetched Overpass independently, seconds
apart, and produced identical GeoJSON and (timestamp aside) identical HTML. The
durability machinery changes what the run costs and what survives a crash, not
the answer.

**Cold, Snakemake wins by ~1 second**, and that is the whole of Facetwork's
per-step overhead for a two-step pipeline. Note how small the gap is against a
10-second download: the overhead is fixed per step, so it is ruinous for
thousands of millisecond steps (Chapter 13) and nearly invisible here.

**The warm re-run inverts the expected result, and the reason is worth stating
plainly.** Chapter 13 says Facetwork has no incremental-recomputation
capability, and at the *runtime* level that is true — re-running a workflow
re-runs every step. But save_earth's handlers keep their own sidecar cache, so
the re-run's steps each did nothing and finished faster than Snakemake's own
startup. The chapter's claim should therefore be read precisely: incremental
recomputation is **handler-author work repeated per domain**, not a runtime
guarantee, and where a domain has done that work the practical difference
disappears. It is a real gap in the runtime, not in every workflow built on it.

A caution about the per-rule numbers on a warm run: Snakemake rewrites a rule's
benchmark file only when the rule actually executes, so a skipped rule's line is
last run's. The script labels them stale rather than quietly reprinting them.

## Files

| | |
|---|---|
| `Snakefile` | the two rules, mirroring the FFL workflow's parameters |
| `scripts/download_reactors.py` | `DownloadNuclearReactors` as a standalone CLI |
| `scripts/build_map.py` | `BuildMap` as a standalone CLI |
| `compare_outputs.py` | GeoJSON + HTML comparison, exits non-zero on difference |
| `run_comparison.sh` | runs both engines under equal isolation, then compares |

`runs/` and the logs are generated; they are gitignored.
