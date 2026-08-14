# PyPSA grid-builder — porting a real Snakemake workflow

The other two experiments here ([`nuclear-map/`](../nuclear-map/),
[`state-capitals/`](../state-capitals/)) compare engines on workflows **I wrote
three times**. That is the right way to isolate the orchestrator, and it has an
obvious weakness: I chose the shape, so it can flatter the system I am arguing
for.

This one starts from somebody else's workflow.
[PyPSA/grid-builder](https://github.com/PyPSA/grid-builder) is a Snakemake
workflow in the PyPSA organisation that builds power-grid models from
OpenStreetMap for PyPSA-Eur and PyPSA-Earth. It was ported to FFL as
[`fwh_gridbuilder`](https://github.com/rlemke/fwh_gridbuilder), calling the
**same extractor** (`earth_osm`) through the same options, and both were run.

## Scope — smaller than the README suggests

Upstream's README describes four stages (retrieve → clean/infer → topology →
validate). At commit `2f20a35` only **retrieve** is implemented: `rule
retrieve_osm` and the `rule retrieve_osm_all` aggregate, wrapping `earth_osm`.
That is what was ported. Nothing here claims anything about the three stages
neither project has built.

## Result: same data

Both workflows were run for real on `LU`, `power=substation`, Geofabrik source:

```
columns:  same (12)
rows:     snakemake 832, facetwork 832
row data: 0 differing (after normalising tag-key order)
bytes:    differ (earth_osm tag-key order is unstable)
RESULT: same data
```

The byte difference is **not** a difference between the orchestrators.
`earth_osm` does not serialise its nested `other_tags` dict in a stable key
order, so two runs of the *same* workflow differ byte-wise too. The consequence
falls on both projects equally: neither can use a content hash of these CSVs as
a cache key. `tools/compare_with_snakemake.py` in the port makes the check
reproducible rather than a claim.

## What the ports do differently

| | grid-builder (Snakemake) | fwh_gridbuilder (FFL) |
|---|---|---|
| **Fan-out** | double `expand()` over countries × features, resolved at DAG-construction time on the submitting machine | two nested `foreach` blocks over values that arrive at run time |
| **Bounding** | `--cores`, global | `limit` per loop — see the race below |
| **Reuse** | outputs exist and are newer than inputs | recorded provenance: extractor version, tool version, source, country/feature/primary, target date, size |
| **Where outputs live** | local filesystem paths | a storage backend — local or `s3://`, unchanged |

The reuse row is the substantive one, and it is §13.3's argument in a real
codebase rather than a constructed example. `rule retrieve_osm` declares **no
file inputs**. Nothing can therefore make its outputs look stale: after the
first successful run the rule is permanently "up to date", and re-running the
workflow a year later reports nothing to do and serves year-old OpenStreetMap
data. The escape hatch is `force_redownload: true` in the config — set by a
human who already suspected. The FFL port re-derives on its own when
`earth-osm 3.0.2` no longer matches the `3.0.1` recorded in the sidecar.

That is not free. It is strictly more bookkeeping, and it is only as good as the
keys the author chose to record — which is exactly the cost §13.3 states.

## What the port got wrong, and what that says

Three defects only appeared when the port ran on a fleet rather than a laptop,
and they are worth recording because they are the actual cost of the
distributed model this thesis argues for:

1. **A storage claim that was false.** The FFL said `out_dir` was storage-aware;
   the implementation used `pathlib`. On a laptop that is invisible. On a fleet
   the outputs land on a container filesystem that disappears with the
   container, and `Path("s3://…")` silently creates a directory named `s3:`.
2. **A same-region race.** Every feature of one country reads the *same*
   regional PBF from `earth_osm`'s shared cache. Run concurrently — which is
   what a fan-out does — one re-downloads the extract while another reads it,
   and the loser dies with `FileNotFoundError` on a path that existed a moment
   earlier. `limit 1` on the feature loop fixes it: the PBF is the contended
   resource, so the loop that shares it is the one that gets bounded, while
   countries still fan out because each pulls a different extract. This is the
   case [`ffl-foreach-limit.md`](../../../architecture/ffl-foreach-limit.md)
   describes, arrived at from the failure rather than the documentation.
3. **A fork deadlock.** `earth_osm`'s multiprocessing forks. Forking a runner
   that holds logging and MongoDB locks in other threads deadlocks the children,
   and the parent then waits on them forever. It presented as a task "running"
   for twenty minutes at **0.6% CPU** with nineteen live processes and no
   output — considerably harder to read than a crash, and a good example of why
   §13.4's "what does the engine trust as evidence of completion" question
   matters. The port now runs the extractor single-process under the runtime and
   keeps multiprocessing for its CLI, which is single-threaded.

None of the three is a Snakemake-versus-Facetwork difference. All three are the
price of executing on a fleet of containers against an object store instead of
in one process against a local disk — the setting this thesis is about, and the
setting in which the reuse advantage above is also claimed.

## Reproducing

```bash
git clone https://github.com/PyPSA/grid-builder && cd grid-builder
# upstream pins software-deployment-method: conda in workflow/profiles/default/
snakemake -s workflow/Snakefile --configfile <(echo 'countries: [LU]') --cores 2

git clone https://github.com/rlemke/fwh_gridbuilder && cd fwh_gridbuilder
pip install -e .
python src/gridbuilder/tools/retrieve_osm.py --countries LU --features substation --out-dir out/
python tools/compare_with_snakemake.py \
    --snakemake-dir ../grid-builder/resources/osm/out \
    --facetwork-dir out/ --country LU --feature substation
```
