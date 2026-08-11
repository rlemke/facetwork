#!/usr/bin/env bash
# Run the nuclear-reactor map under BOTH engines and compare time + output.
#
#   ./run_comparison.sh              # live Overpass fetch (timing comparison)
#   ./run_comparison.sh --mock       # deterministic data (output comparison)
#   ./run_comparison.sh --snakemake-only
#
# Each engine writes to its own FW_DATA_ROOT, so neither can read the other's
# cache. That isolation is the whole basis of the timing number: with a shared
# cache the second engine would "win" by doing no download at all.
#
# The two runs are still not perfectly symmetric, and the README says how:
# Facetwork's number includes starting a runner process and a MongoDB
# round-trip per step, which is the durability it buys and Snakemake does not.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python3}"
SNAKEMAKE="${SNAKEMAKE:-snakemake}"
SAVE_EARTH_FFL="${SAVE_EARTH_FFL:-$HOME/fw_handlers/fwh_save_earth/src/save_earth/ffl/save_earth.ffl}"
WORKFLOW="save_earth.workflows.BuildNuclearReactorMap"

MOCK=""
MOCK_INPUT="false"
RUN_FW=1
RUN_SM=1
KEEP=0
for arg in "$@"; do
    case "$arg" in
        --mock) MOCK="use_mock=true"; MOCK_INPUT="true" ;;
        --snakemake-only) RUN_FW=0 ;;
        --facetwork-only) RUN_SM=0 ;;
        # Keep the previous run's outputs. Runs are wiped by default so every
        # measurement is cold and comparable; --keep is how you observe the
        # thing that only shows on a WARM run — Snakemake answering "nothing to
        # do" from files on disk, which Facetwork has no equivalent for.
        --keep) KEEP=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

FW_OUT="$HERE/runs/facetwork"
SM_OUT="$HERE/runs/snakemake"

[[ -f "$SAVE_EARTH_FFL" ]] || { echo "no FFL at $SAVE_EARTH_FFL — set SAVE_EARTH_FFL" >&2; exit 1; }

# ---------------------------------------------------------------- Snakemake
if [[ $RUN_SM -eq 1 ]]; then
    echo "=== Snakemake ==="
    [[ $KEEP -eq 1 ]] || rm -rf "$SM_OUT"
    sm_start=$(date +%s.%N)
    "$SNAKEMAKE" --snakefile "$HERE/Snakefile" -j 4 \
        --config outdir="$SM_OUT" python="$PYTHON" ${MOCK:+$MOCK} \
        > "$HERE/snakemake_run.log" 2>&1 || { tail -30 "$HERE/snakemake_run.log"; exit 1; }
    sm_end=$(date +%s.%N)
    SM_SECS=$(echo "$sm_end - $sm_start" | bc)
    echo "wall-clock: ${SM_SECS}s"
    # Snakemake only rewrites a rule's benchmark file when that rule actually
    # runs, so on a warm re-run these are LAST run's numbers. Saying so beats
    # printing a 10s download next to a 2s wall-clock and letting the reader
    # work out that they cannot both be from this run.
    echo "per-rule$([[ $KEEP -eq 1 ]] && echo ' (stale for rules that were skipped)'):"
    for tsv in "$SM_OUT"/benchmarks/*.tsv; do
        printf '  %-22s %ss\n' "$(basename "$tsv" .tsv)" "$(awk 'NR==2{print $1}' "$tsv")"
    done
fi

# ---------------------------------------------------------------- Facetwork
if [[ $RUN_FW -eq 1 ]]; then
    echo
    echo "=== Facetwork ==="
    [[ $KEEP -eq 1 ]] || rm -rf "$FW_OUT"
    mkdir -p "$FW_OUT"

    # Its OWN MongoDB database, for two reasons. It isolates the benchmark from
    # any fleet activity on this host, and — the part that actually bites — the
    # shared registry's module_uri values point at CONTAINER paths
    # (file:///opt/fwh_save_earth/...). A process runner on the host cannot
    # import those, so it silently never claims the task: no error, the work
    # just sits pending. Registering the local checkout here fixes that without
    # rewriting registrations the fleet containers depend on.
    export FW_MONGODB_DATABASE="${FW_MONGODB_DATABASE:-fw_nuclear_bench}"
    echo "mongo database: $FW_MONGODB_DATABASE (isolated from the fleet)"
    PYTHONPATH="$REPO" REPO_ROOT="$REPO" "$PYTHON" -m facetwork.domains save-earth \
        > "$HERE/facetwork_register.log" 2>&1 \
        || { tail -20 "$HERE/facetwork_register.log"; exit 1; }

    # A runner scoped to this experiment's storage root. --task-list save_earth
    # matches the workflow's top-level namespace, which is how tasks route.
    FW_DATA_ROOT="$FW_OUT" FW_STORAGE=local PYTHONPATH="$REPO" \
        "$PYTHON" -m facetwork.runtime.runner --registry \
        --task-list save_earth --port 8097 --log-format text \
        > "$HERE/facetwork_runner.log" 2>&1 &
    RUNNER_PID=$!
    trap 'kill $RUNNER_PID 2>/dev/null || true' EXIT

    # Wait for the runner to advertise itself rather than sleeping a guessed
    # interval — handler preload takes tens of seconds on a full registry.
    for _ in $(seq 1 90); do
        grep -q "Runner started" "$HERE/facetwork_runner.log" && break
        sleep 1
    done
    grep -q "Runner started" "$HERE/facetwork_runner.log" || {
        echo "runner did not start; see $HERE/facetwork_runner.log" >&2; exit 1; }

    fw_start=$(date +%s.%N)
    FW_DATA_ROOT="$FW_OUT" FW_STORAGE=local PYTHONPATH="$REPO" \
        "$PYTHON" -m facetwork.runtime.submit \
        --primary "$SAVE_EARTH_FFL" --workflow "$WORKFLOW" \
        --inputs "{\"use_mock\": $MOCK_INPUT}" --log-format text \
        > "$HERE/facetwork_submit.log" 2>&1
    RUNNER_ID=$(awk '/Runner ID:/{print $3}' "$HERE/facetwork_submit.log")
    echo "runner_id: $RUNNER_ID"

    # Poll to terminal. Facetwork's wall-clock legitimately includes this
    # bookkeeping — it is what the durable step store costs.
    for _ in $(seq 1 600); do
        STATE=$("$PYTHON" - "$RUNNER_ID" "$FW_MONGODB_DATABASE" <<'PYEOF'
import sys
from pymongo import MongoClient
db = MongoClient("mongodb://localhost:27017")[sys.argv[2]]
print((db.runners.find_one({"uuid": sys.argv[1]}) or {}).get("state", "?"))
PYEOF
)
        [[ "$STATE" == "running" || "$STATE" == "created" ]] || break
        sleep 2
    done
    fw_end=$(date +%s.%N)
    FW_SECS=$(echo "$fw_end - $fw_start" | bc)
    echo "wall-clock: ${FW_SECS}s   (final state: $STATE)"

    kill $RUNNER_PID 2>/dev/null || true
fi

# ---------------------------------------------------------------- compare
if [[ $RUN_FW -eq 1 && $RUN_SM -eq 1 ]]; then
    echo
    echo "=== Output comparison ==="
    "$PYTHON" "$HERE/compare_outputs.py" --facetwork "$FW_OUT" --snakemake "$SM_OUT"
fi
