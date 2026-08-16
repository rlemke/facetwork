# What a change costs — PyPSA-Eur's retrieve layer, measured against its own history

[`fwh_pypsa_data`](https://github.com/rlemke/fwh_pypsa_data) ports PyPSA-Eur's
`rules/retrieve.smk` — 1,631 lines and 73 rules — to a single `foreach` over
`data/versions.csv`. The collapse ratio is the eye-catching number, and it is
also the least interesting one: nobody rewrites a retrieval layer, they *change*
it. So the question this experiment asks is the one that recurs:

> When a dataset is added, updated or moved, what does it cost in each system?

Both halves are measured rather than argued. The upstream half comes from the
23 commits in PyPSA-Eur's history that touch `data/versions.csv`; the port's
half comes from adding a dataset and running it.

## Method

```bash
gh api "repos/PyPSA/pypsa-eur/commits?path=data/versions.csv&per_page=100"
# then, per commit, the additions/deletions in versions.csv, retrieve.smk and
# config.default.yaml, and what the added rule body actually does
```

Line counts are upstream's own diffs, not a reconstruction.

## Upstream: it depends entirely on the kind of change

| kind of change | commits | `retrieve.smk` |
|---|---|---|
| data only — new URL, new version, moved mirror | 5 | **untouched** |
| config + data | 4 | untouched |
| needed rule code | 14 | +1 … +784 lines, median **+13** |

The +784 outlier is `151f6038`, which *introduced* the versions.csv layer — it
is the infrastructure commit, not a dataset change, and is excluded from the
median above.

The first row is the finding that matters most, because it is the one that
cuts against the port: **upstream is already data-driven for the common case.**
Bumping `technology-data` to v0.13.4 (`1e3e24b5`), fixing the SCIGRID gas
archive URL (`666fdf17`), fixing the WDPA links for Windows (`547e8e81`) — all
of these are edits to `versions.csv` and nothing else. The FFL port offers no
advantage here whatsoever. Any claim that it makes *change* cheap in general
would be false, and the history says so plainly.

## Where the 73 rules actually cost something

Of the 14 commits that did need rule code, what the added code *does* separates
them cleanly:

| what the added rule body does | commits | in the FFL port |
|---|---|---|
| a plain `storage(...)` + `copy2` rule for a new dataset | 3 | **0 lines** — add the CSV row |
| a bespoke request: `requests.get(params=…)`, `entsoe-py`, unpacking | 5 | **also needs code** |
| edits to existing rules / one-line touches | 6 | varies |

So the collapse pays off in exactly one place — a *new plain download* — where
upstream writes a rule (median +13 lines, e.g. `1fa9a4fb`, `5664e141`,
`5352136d` each adding one `storage()`+`copy2` rule) and the port writes
nothing. It does not pay off when the new dataset needs a request shape the CSV
has no column for, and there the port is currently *worse*: it cannot express
one at all, and its guard falls back to the archive rather than fetching the
wrong thing (see the domain README).

## The port's half: adding a dataset, measured

A row was appended to a copy of upstream's catalogue —

```csv
demo_new_dataset,v1.0,primary,latest supported,2026-08-16,…,https://…/versions.csv
```

— and run with no other change:

```bash
fw ffl run …/pypsa_data.ffl --workflow pypsa.data.RetrieveDatasets \
  --inputs '{"csv_path": "versions.plus.csv", "names": ["demo_new_dataset"], …}'
```

Result: `state: completed`, tasks `fw:execute` → `Catalog` → `fw.http.Fetch` →
`fw.file.WriteJson`, the file in the object store with its provenance sidecar,
and `git status` in the port reporting **0 files changed**. The fan-out picked
up a dataset that did not exist when the workflow was written, because the work
list is read at run time rather than compiled into rules.

## What this supports, and what it does not

**Supported.** For a new plain download, the port's marginal cost is zero and
upstream's is a rule — which is the same static-outputs property §13 describes,
now priced from upstream's own commits rather than asserted.

**Not supported.** "Change is cheaper in FFL" as a general claim. Five of 23
catalogue commits cost upstream nothing either, and five needed request logic
that costs the port at least as much. The honest summary is narrower and more
useful: *the rule-per-output tax is real but it is charged only on new
downloads, and a workflow whose work list is already a table has largely paid it
off already.*

The deeper point is the same one §13.3 makes about staleness: what a system can
express as data, it can change as data. PyPSA-Eur got there first for URLs and
versions, and its remaining 73 rules are what is left over — the fetches whose
*shape*, not whose address, varies.

## Reproducing

```bash
# upstream side (needs gh auth)
gh api "repos/PyPSA/pypsa-eur/commits?path=data/versions.csv&per_page=100" --jq '.[].sha'
gh api repos/PyPSA/pypsa-eur/commits/<sha> \
  --jq '.files[] | select(.filename|test("versions.csv|retrieve.smk|config.default.yaml"))
        | "\(.filename) +\(.additions)/-\(.deletions)"'

# port side
cp versions.csv versions.plus.csv && echo '<new row>' >> versions.plus.csv
fw runner start --domain pypsa-data --no-dashboard
fw ffl run src/pypsa_data/ffl/pypsa_data.ffl --workflow pypsa.data.RetrieveDatasets \
  --inputs '{"csv_path":"versions.plus.csv","out_dir":"…","names":["demo_new_dataset"]}'
git status --short   # expect: empty
```
