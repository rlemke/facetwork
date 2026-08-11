#!/usr/bin/env bash
# Run the nuclear-reactor map under Facetwork, Snakemake and Nextflow, then
# compare wall-clock and output.
#
#   ./run_comparison.sh                  # live Overpass — timing
#   ./run_comparison.sh --mock           # deterministic data — output equality
#   ./run_comparison.sh --keep           # warm re-run (incremental behaviour)
#   ./run_comparison.sh --only snakemake,nextflow
#
# Each engine writes to its own FW_DATA_ROOT, so none can read another's cache.
# That isolation is the whole basis of the timing: with a shared cache whichever
# engine ran last would "win" by doing no download at all.
#
# THE NUMBERS ARE NOT SYMMETRIC, and pretending otherwise would be the easiest
# way to mislead with this harness. Two asymmetries matter:
#
#   1. ENGINE STARTUP. Snakemake's and Nextflow's wall-clock includes starting
#      the engine (JVM boot for Nextflow, DAG build for Snakemake) because for
#      them that IS the run. Facetwork's excludes starting the runner, because a
#      runner is a long-lived service you submit to, not a per-run process. What
#      is measured for Facetwork is submit -> workflow terminal. This is a real
#      architectural difference, not a thumb on the scale, but it means the
#      totals answer slightly different questions.
#   2. OVERPASS THROTTLING on live runs. Repeated live runs get rate-limited, so
#      whichever engine runs LATER pays more — a 13s download became 97s across
#      four runs in testing. Live numbers are therefore order-sensitive and not
#      comparable between engines; use --mock to compare orchestration, and live
#      only to confirm the outputs agree on real data.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python3}"
SNAKEMAKE="${SNAKEMAKE:-snakemake}"
NEXTFLOW="${NEXTFLOW:-nextflow}"
SAVE_EARTH_FFL="${SAVE_EARTH_FFL:-$HOME/fw_handlers/fwh_save_earth/src/save_earth/ffl/save_earth.ffl}"
WORKFLOW="save_earth.workflows.BuildNuclearReactorMap"

MOCK_INPUT="false"
KEEP=0
ONLY="facetwork,snakemake,nextflow"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mock) MOCK_INPUT="true" ;;
        # Keep the previous run's outputs. Runs are wiped by default so every
        # measurement is cold and comparable; --keep is how you observe what only
        # shows on a WARM run — an engine deciding there is nothing to do.
        --keep) KEEP=1 ;;
        --only) shift; ONLY="$1" ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

runs() { [[ ",$ONLY," == *",$1,"* ]]; }

FW_OUT="$HERE/runs/facetwork"
SM_OUT="$HERE/runs/snakemake"
NF_OUT="$HERE/runs/nextflow"

[[ -f "$SAVE_EARTH_FFL" ]] || { echo "no FFL at $SAVE_EARTH_FFL — set SAVE_EARTH_FFL" >&2; exit 1; }

declare -a COMPARE=()

# ---------------------------------------------------------------- Facetwork
if runs facetwork; then
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
    echo "wall-clock: $(echo "$fw_end - $fw_start" | bc)s   (state: $STATE, runner $RUNNER_ID)"
    kill $RUNNER_PID 2>/dev/null || true
    COMPARE+=(--run "facetwork=$FW_OUT")
fi

# ---------------------------------------------------------------- Snakemake
if runs snakemake; then
    echo
    echo "=== Snakemake ==="
    [[ $KEEP -eq 1 ]] || rm -rf "$SM_OUT"
    sm_start=$(date +%s.%N)
    "$SNAKEMAKE" --snakefile "$HERE/Snakefile" -j 4 \
        --config outdir="$SM_OUT" python="$PYTHON" use_mock="$MOCK_INPUT" \
        > "$HERE/snakemake_run.log" 2>&1 || { tail -30 "$HERE/snakemake_run.log"; exit 1; }
    sm_end=$(date +%s.%N)
    echo "wall-clock: $(echo "$sm_end - $sm_start" | bc)s"
    # Snakemake only rewrites a rule's benchmark file when that rule actually
    # runs, so on a warm re-run these are LAST run's numbers. Saying so beats
    # printing a 10s download next to a 2s wall-clock and leaving the reader to
    # work out that they cannot both be from this run.
    echo "per-rule$([[ $KEEP -eq 1 ]] && echo ' (stale for rules that were skipped)'):"
    for tsv in "$SM_OUT"/benchmarks/*.tsv; do
        [[ -f "$tsv" ]] || continue
        printf '  %-22s %ss\n' "$(basename "$tsv" .tsv)" "$(awk 'NR==2{print $1}' "$tsv")"
    done
    COMPARE+=(--run "snakemake=$SM_OUT")
fi

# ---------------------------------------------------------------- Nextflow
if runs nextflow; then
    echo
    echo "=== Nextflow ==="
    # -resume is what makes a warm Nextflow run incremental; without it Nextflow
    # re-executes every task even though its work dir survives. Snakemake needs
    # no equivalent flag because it decides from the output files themselves —
    # a real difference between the two, not a detail of this harness.
    RESUME=""
    if [[ $KEEP -eq 1 ]]; then RESUME="-resume"; else rm -rf "$NF_OUT"; fi
    # NEXTFLOW may carry arguments (e.g. "bash /path/to/nextflow.sh"), so split
    # it into a command array rather than invoking it as a single word.
    read -ra NF_CMD <<< "$NEXTFLOW"
    nf_start=$(date +%s.%N)
    ( cd "$HERE" && "${NF_CMD[@]}" run main.nf \
        --outdir "$NF_OUT" --python "$PYTHON" --use_mock "$MOCK_INPUT" \
        -ansi-log false $RESUME ) > "$HERE/nextflow_run.log" 2>&1 \
        || { tail -30 "$HERE/nextflow_run.log"; exit 1; }
    nf_end=$(date +%s.%N)
    echo "wall-clock: $(echo "$nf_end - $nf_start" | bc)s"
    if [[ -f "$NF_OUT/pipeline_info/trace.txt" ]]; then
        echo "per-process:"
        awk 'NR>1 {printf "  %-22s %s  (%s)\n", $1, $4, $2}' "$NF_OUT/pipeline_info/trace.txt"
    fi
    COMPARE+=(--run "nextflow=$NF_OUT")
fi

# ---------------------------------------------------------------- compare
if [[ ${#COMPARE[@]} -ge 4 ]]; then   # two --run pairs = 4 array entries
    echo
    echo "=== Output comparison ==="
    "$PYTHON" "$HERE/compare_outputs.py" "${COMPARE[@]}"
fi
