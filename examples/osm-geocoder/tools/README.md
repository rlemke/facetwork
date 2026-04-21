# OSM Geocoder — Tools

Standalone command-line utilities for OSM-related operations that are **not** part of a workflow. Each tool is a self-contained Python program paired with a shell wrapper that supplies its arguments.

Tools are intended for operator/developer use: one-off imports, diagnostics, data inspection, cache inspection, export helpers, etc. A handler may shell out to a tool if convenient, but the tool's primary audience is a human at the terminal.

## Directory layout

```
tools/
├── README.md                 ← this file
├── _lib/                     ← shared helpers (manifest I/O, checksums, etc.)
│   └── __init__.py
├── <tool_name>.py            ← Python entry point (CLI, argparse-based)
├── <tool_name>.sh            ← shell wrapper that invokes <tool_name>.py
└── ...
```

One `.py` + one `.sh` per tool. Shared code lives in `tools/_lib/` — anything reused by two or more tools (manifest read/modify/write, checksum helpers, Geofabrik URL resolution) belongs there. Tools may also import from `examples/osm-geocoder/handlers/` when reusing workflow-side logic is appropriate, but prefer `_lib/` for tool-only concerns.

## Conventions for adding a new tool

When Claude (or a human) adds a tool here, follow these rules:

### Python program (`<name>.py`)

- Runnable directly: `python tools/<name>.py --help` must work.
- Use `argparse` for argument parsing. Every tool exposes `--help`.
- Has a `main()` function and an `if __name__ == "__main__": main()` guard.
- Exit codes: `0` on success, non-zero on failure. Print errors to `stderr`.
- Log to `stderr`; reserve `stdout` for structured output (JSON, CSV, geometry, etc.) when applicable, so output can be piped.
- Read configuration from environment variables where possible (`AFL_POSTGIS_URL`, `AFL_IMPORT_POSTGIS_URL`, etc.) — match the names used by the rest of the project. CLI flags override env vars.
- Do **not** depend on the Facetwork runtime, MongoDB, or the dashboard. Tools should be usable without a running workflow stack. PostGIS, HDFS, and external APIs are fair game.
- If the tool needs code from a handler, import it from `handlers/...` rather than copying it.
- Type hints on every function. Docstring on `main()` describing purpose, inputs, and outputs.

### Shell wrapper (`<name>.sh`)

- Executable: `chmod +x <name>.sh`.
- Starts with `#!/usr/bin/env bash` and `set -euo pipefail`.
- Sources the repo's env loader so `.env` values are available:
  ```bash
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/scripts/_env.sh"
  ```
- Activates the project virtualenv if one exists at `${REPO_ROOT}/.venv`.
- Invokes the Python tool with `"$@"` so any extra flags pass through:
  ```bash
  exec python "$(dirname "${BASH_SOURCE[0]}")/<name>.py" "$@"
  ```
- The wrapper's job is *environment setup and defaults*, not argument parsing — parsing belongs in the Python program. The wrapper may set a handful of defaults (e.g. a default `--region` or `--pbf-path`) before `"$@"`, but complex option logic stays in Python.

### Naming

- Use kebab-case for shell wrappers (`dump-amenities.sh`), snake_case for Python files (`dump_amenities.py`). Keep the base name aligned so the pair is obvious.
- Name by *what it does*, not by *how*: `count-nodes-by-region.sh`, not `run-sql.sh`.

### Documentation

- Top of each `<name>.py`: a module docstring with one-line summary, a usage example, and a note on any external dependencies (PostGIS, osmium, network).
- If a tool grows non-trivial (multiple subcommands, complex output), add a `tools/<name>.md` next to it. For simple tools, `--help` output is sufficient.
- When adding a new tool, append a one-line entry to the **Available tools** section below.

### What does *not* belong here

- Workflow steps, event facets, or handler logic → `handlers/`.
- Operational scripts that manage the Facetwork stack itself (runners, Docker, databases) → repo-root `scripts/`.
- Test fixtures or pytest helpers → `tests/` or `conftest.py`.
- Anything that must run inside a Facetwork task — if it needs the runtime, it's a handler, not a tool.

## Cache layout and manifests

OSM-derived data is cached under a storage root, partitioned by type. Each cache type gets its own subdirectory and its own manifest file **inside** that subdirectory — the manifest travels with its data, so moving or deleting a cache subtree keeps the index consistent.

### Backends

Tools access the cache through a storage abstraction that supports two backends:

- `local` (default) — standard POSIX filesystem. Default root: `/Volumes/afl_data/osm`. Full atomic writes and `fcntl` advisory locking.
- `hdfs` — HDFS via WebHDFS (soft-imports `facetwork.runtime.storage`). Default root: `/user/afl/osm`. No advisory locking — **single-writer semantics assumed** (use from one coordinator process). Rename is atomic at the namenode; overwrite is not.

Select the backend per invocation with `--backend {local,hdfs}` or globally via `AFL_OSM_STORAGE`. Override the cache root with `AFL_OSM_CACHE_ROOT` (applies to the selected backend).

### Layout

```
/Volumes/afl_data/osm/
├── pbf/
│   ├── manifest.json
│   └── europe/germany/berlin-latest.osm.pbf        ← mirrors Geofabrik's path
├── geojson/
│   ├── manifest.json
│   └── ...
├── shapefiles/
│   ├── manifest.json
│   └── ...
└── ...
```

Tools must never hard-code the root — use `_lib.storage.get_storage()` and `_lib.manifest.cache_dir(cache_type, storage)`, both of which consult the env vars above.

### Manifest conventions

- **Name**: always `manifest.json` inside the cache-type subdir. Do not use `<type>_directory.json` — "directory" is ambiguous with "filesystem directory."
- **Path mirroring**: for remote sources with a natural hierarchy (Geofabrik: `europe/germany/berlin-latest.osm.pbf`), mirror that path under the cache subdir. Keeps diffs against upstream trivial and avoids filename-collision logic.
- **Entry shape** (baseline — extend per cache type as needed):
  ```json
  {
    "version": 1,
    "entries": {
      "europe/germany/berlin-latest.osm.pbf": {
        "relative_path": "europe/germany/berlin-latest.osm.pbf",
        "source_url": "https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf",
        "size_bytes": 123456789,
        "sha256": "…",
        "source_checksum": {"algo": "md5", "value": "…", "url": "…md5"},
        "downloaded_at": "2026-04-20T14:03:22Z",
        "source_timestamp": "2026-04-18T21:22:02Z",
        "extra": { /* cache-type specific: osmosis_replication_seq, region_code, etc. */ }
      }
    }
  }
  ```
  `downloaded_at` answers *"when did we fetch it?"*; `source_timestamp` (from the PBF header / upstream metadata) answers *"how fresh is the data?"* — keep them separate.
- **Checksums**: for Geofabrik, download the published `.md5` and record it under `source_checksum` for upstream-integrity verification. Also compute a local `sha256` after download for tamper/corruption detection. Verify `source_checksum` before writing the manifest entry; abort on mismatch.
- **Atomic writes**: write to `manifest.json.tmp` in the same directory, then `os.replace` to `manifest.json`. Never write the file in place.
- **Concurrent access**: hold an advisory lock (`fcntl.flock` on `manifest.json.lock`) for the entire read-modify-write cycle. Multiple simultaneous downloads are allowed — they must serialize around the manifest update, not the download itself.
- **Forward compatibility**: always include `"version": 1`. Readers must tolerate unknown fields in entries. Bump the version only for breaking layout changes.

`tools/_lib/manifest.py` is the single implementation of all of the above — new tools must use it rather than re-implementing JSON I/O.

## Available tools

<!-- Append one line per tool: `- **name** — short description.` -->

- **download-pbf** — download OSM PBF files from Geofabrik into `pbf/`, verified against upstream MD5. Sequential (Geofabrik rate-limits per IP), skips files already present with a matching checksum. Accepts region keys positionally, via `--regions-file`, or resolved from Geofabrik's `index-v1.json` with `--all` / `--all-under PREFIX` (leaves-only by default; add `--include-parents` for continent/country-level PBFs). Use `--list` to preview the resolved set. Supports `--backend {local,hdfs}` for either local filesystem or HDFS (WebHDFS) storage. See `download-pbf.sh --help`.
- **convert-pbf-geojson** — convert cached PBFs to GeoJSON via `osmium export` into `geojson/`. The geojson manifest records the source PBF's SHA-256, so reruns skip regions whose PBF hasn't changed; pass `--force` to reconvert. Parallelizes with `--jobs N` (each worker spawns an `osmium` subprocess). Region selection mirrors download-pbf: positional, `--regions-file`, `--all` / `--all-under PREFIX` (reading the **local pbf manifest**, not the Geofabrik index). Default output format is `geojsonseq`; pass `--format geojson` for a single FeatureCollection. Local backend only — `osmium` requires local files. Requires `osmium-tool` on PATH.
- **convert-pbf-shapefile** — convert cached PBFs to multi-layer ESRI Shapefile bundles via `ogr2ogr` into `shapefiles/`. Output for each region is a **directory** of bundles (one per geometry category: points, lines, multilinestrings, multipolygons). The `other_relations` GeometryCollection layer is skipped — shapefile can't represent it. Manifest records the source PBF's SHA-256 plus per-layer size and SHA-256, so reruns skip regions whose PBF hasn't changed. Same flag surface as `convert-pbf-geojson` (`--all` / `--all-under` / `--include-parents` / `--update-all` / `--jobs` / `--force` / `--dry-run` / `--list`). Local backend only — shapefile bundles are directory trees, and HDFS directory finalization isn't implemented. Requires GDAL's `ogr2ogr` on PATH.
- **build-graphhopper-graph** — build GraphHopper routing graphs from cached PBFs into `graphhopper/<region>-latest/<profile>/`. One directory per (region, profile); a region can have several profile graphs simultaneously. Manifest records source-PBF SHA-256 + GraphHopper version, so a jar upgrade invalidates all graphs automatically without per-region `--force`. `--profile NAME` / `--profiles LIST` / `--all-profiles` control the profile axis; standard region selection (`--all` / `--all-under` / `--update-all` / `--list-missing`) applies across (profile, region) pairs. Default `--jobs 1` because GraphHopper imports are CPU+RAM heavy (multi-GB heap per build). Local backend only. Requires Java 17+ and a GraphHopper 8.x `-web.jar` (path via `--jar` or `$GRAPHHOPPER_JAR`).
- **build-valhalla-tiles** — build Valhalla routing tilesets from cached PBFs into `valhalla/<region>-latest/`. Unlike GraphHopper, there is **no profile axis** — Valhalla profiles (auto, bicycle, pedestrian, truck, motor_scooter, motorcycle, bus, taxi) are query-time costing models, so one tileset serves every profile. Cross-region routing works natively within a tileset (Valhalla's hierarchical tiles transparently cross internal boundaries); for coverage across separately-built tilesets, build a parent region. Manifest records source-PBF SHA-256 + Valhalla version, so a toolchain upgrade invalidates all tilesets automatically. Standard region-selection flags (`--all` / `--all-under` / `--update-all` / `--list-missing`) apply. Default `--jobs 1` — Valhalla builds are CPU+RAM heavy. Local backend only. Requires `valhalla_build_config` and `valhalla_build_tiles` binaries (install via `install-tools.sh` or `brew install valhalla`).
- **install-tools** — one-shot installer for every binary the rest of the tools shell out to: `osmium-tool`, `gdal` (for `ogr2ogr`), `openjdk@17` (for the GraphHopper JAR), `valhalla` — all via Homebrew — plus the GraphHopper runnable JAR downloaded from GitHub releases to `~/.graphhopper/graphhopper-web.jar`. Idempotent (re-running only installs what's missing). Verifies each tool at the end and prints a short env-var cheat sheet. Run before using any of the other tools on a fresh machine.
- **extract** — extract category-specific feature layers (water, protected_areas, parks, forests, ...) from cached PBFs into one pre-filtered GeoJSONSeq per category. Each category gets its own cache subdirectory (e.g. `water/`, `parks/`) with its own manifest; downstream consumers read the small already-filtered file instead of re-parsing the PBF. Uses `osmium tags-filter | osmium export`. Positional first arg is the category; `--extract-all-categories` runs every category per resolved region. Categories are defined in `_lib/pbf_extract.py::CATEGORIES` — adding a new one is a single dict entry (tag filter, description, filter_version for cache invalidation). Same region-selection surface as the other tools (`--all` / `--all-under` / `--include-parents` / `--update-all` / `--jobs` / `--force` / `--dry-run` / `--list` / `--list-categories`). Requires `osmium-tool` on PATH.

## Running a tool

```bash
cd examples/osm-geocoder
./tools/<name>.sh --help
./tools/download-pbf.sh europe/liechtenstein               # local backend (default)
AFL_OSM_STORAGE=hdfs ./tools/download-pbf.sh europe/liechtenstein
./tools/download-pbf.sh --backend hdfs europe/liechtenstein  # same via flag
```

Direct Python invocation also works if the environment is already set up:

```bash
python tools/<name>.py --region berlin
```
