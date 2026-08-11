#!/usr/bin/env bash
# US state capitals: the same fan-out under Facetwork, Snakemake and Nextflow.
#
#   ./run_comparison.sh                # all three, then diff the results
#   ./run_comparison.sh --only nextflow
#   ./run_comparison.sh --jobs 4       # fan-out width for Snakemake/Nextflow
#
# All three read ONE shared, pre-warmed cache (cache/osm_states.json) and write
# their fan-out and fan-in output to their own directory. That split is the
# point: the cache keeps the network out of the measurement — the confound that
# made the nuclear-map live timings uncomparable — while separate outputs let
# the three answers be diffed.
#
# NOT SYMMETRIC, and worth knowing before reading any number: Snakemake's and
# Nextflow's wall-clock includes starting the engine, because for them that IS
# the run. Facetwork's excludes starting the runner, because a runner is a
# long-lived service you submit to. What is measured for Facetwork is submit ->
# workflow terminal.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python3}"
SNAKEMAKE="${SNAKEMAKE:-snakemake}"
NEXTFLOW="${NEXTFLOW:-nextflow}"
CACHE_DIR="${CAPITALS_CACHE_DIR:-$HERE/cache}"
JOBS=8
ONLY="facetwork,snakemake,nextflow"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --only) shift; ONLY="$1" ;;
        --jobs) shift; JOBS="$1" ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
runs() { [[ ",$ONLY," == *",$1,"* ]]; }

# Warm the shared cache ONCE, outside every measured window. Overpass is
# rate-limited and was returning 504s during this work, so a per-engine fetch
# would measure the API's mood rather than the engine.
if [[ ! -f "$CACHE_DIR/osm_states.json" ]]; then
    echo "warming the shared cache (the only networked step)..."
    "$PYTHON" "$HERE/scripts/fetch_states.py" --cache-dir "$CACHE_DIR"
fi
echo "shared cache: $CACHE_DIR/osm_states.json"
echo

declare -a RESULTS=()

# ---------------------------------------------------------------- Facetwork
if runs facetwork; then
    echo "=== Facetwork (foreach fan-out, after fan-in) ==="
    OUT="$HERE/runs/facetwork"; rm -rf "$OUT"; mkdir -p "$OUT"
    export FW_MONGODB_DATABASE="${FW_MONGODB_DATABASE:-fw_capitals_bench}"

    # Registered into an isolated database so this cannot disturb the fleet's
    # registry, whose module_uri values point at container paths anyway.
    "$PYTHON" - "$HERE" <<'PYEOF'
import os, sys, time
from pymongo import MongoClient
here = sys.argv[1]
db = MongoClient(os.environ.get("FW_MONGODB_URL", "mongodb://localhost:27017"))[
    os.environ["FW_MONGODB_DATABASE"]]
now = str(int(time.time() * 1000))
mod = f"{here}/handlers/capitals_handlers.py"
for facet in ("capitals.FetchStateData", "capitals.ResolveCapital", "capitals.CombineCapitals"):
    db.handler_registrations.update_one({"facet_name": facet}, {"$set": {
        "facet_name": facet, "module_uri": f"file://{mod}", "entrypoint": "handle",
        "version": "1.0.0", "checksum": "", "timeout_ms": "300000",
        "requirements": "[]", "metadata": "{}", "created": now, "updated": now}},
        upsert=True)
PYEOF

    FW_DATA_ROOT="$OUT" CAPITALS_CACHE_DIR="$CACHE_DIR" FW_STORAGE=local PYTHONPATH="$REPO" \
        "$PYTHON" -m facetwork.runtime.runner --registry \
        --task-list capitals --port 8096 --log-format text \
        > "$HERE/facetwork_runner.log" 2>&1 &
    RUNNER_PID=$!
    trap 'kill $RUNNER_PID 2>/dev/null || true' EXIT
    for _ in $(seq 1 90); do
        grep -q "Runner started" "$HERE/facetwork_runner.log" && break
        sleep 1
    done
    grep -q "Runner started" "$HERE/facetwork_runner.log" || {
        echo "runner did not start; see facetwork_runner.log" >&2; exit 1; }

    STATES=$("$PYTHON" -c "
import json, sys; sys.path.insert(0, '$HERE'); import capitals_lib
print(json.dumps({'states': capitals_lib.STATE_CODES}))")

    start=$(date +%s.%N)
    PYTHONPATH="$REPO" "$PYTHON" -m facetwork.runtime.submit \
        --primary "$HERE/ffl/capitals.ffl" --workflow capitals.FindStateCapitals \
        --inputs "$STATES" --log-format text > "$HERE/facetwork_submit.log" 2>&1
    RID=$(awk '/Runner ID:/{print $3}' "$HERE/facetwork_submit.log")
    for _ in $(seq 1 900); do
        STATE=$("$PYTHON" - "$RID" "$FW_MONGODB_DATABASE" <<'PYEOF'
import sys
from pymongo import MongoClient
db = MongoClient("mongodb://localhost:27017")[sys.argv[2]]
print((db.runners.find_one({"uuid": sys.argv[1]}) or {}).get("state", "?"))
PYEOF
)
        [[ "$STATE" == "running" || "$STATE" == "created" ]] || break
        sleep 1
    done
    echo "wall-clock: $(echo "$(date +%s.%N) - $start" | bc)s   (state: $STATE)"
    kill $RUNNER_PID 2>/dev/null || true
    RESULTS+=("facetwork:$OUT/capitals.csv")
fi

# ---------------------------------------------------------------- Snakemake
if runs snakemake; then
    echo
    echo "=== Snakemake (50 output files = the fan-out) ==="
    OUT="$HERE/runs/snakemake"; rm -rf "$OUT"
    start=$(date +%s.%N)
    "$SNAKEMAKE" --snakefile "$HERE/Snakefile" -j "$JOBS" \
        --config outdir="$OUT" cache_dir="$CACHE_DIR" python="$PYTHON" \
        > "$HERE/snakemake_run.log" 2>&1 || { tail -30 "$HERE/snakemake_run.log"; exit 1; }
    echo "wall-clock: $(echo "$(date +%s.%N) - $start" | bc)s   (-j $JOBS)"
    if compgen -G "$OUT/benchmarks/state_*.tsv" > /dev/null; then
        awk 'FNR==2 {t+=$1; n++} END {printf "per-state: %.3fs mean over %d tasks\n", t/n, n}' \
            "$OUT"/benchmarks/state_*.tsv
    fi
    RESULTS+=("snakemake:$OUT/capitals.csv")
fi

# ---------------------------------------------------------------- Nextflow
if runs nextflow; then
    echo
    echo "=== Nextflow (a channel of 50 = the fan-out) ==="
    OUT="$HERE/runs/nextflow"; rm -rf "$OUT"
    read -ra NF_CMD <<< "$NEXTFLOW"
    start=$(date +%s.%N)
    ( cd "$HERE" && "${NF_CMD[@]}" run main.nf \
        --outdir "$OUT" --cache_dir "$CACHE_DIR" --python "$PYTHON" \
        -ansi-log false ) > "$HERE/nextflow_run.log" 2>&1 \
        || { tail -30 "$HERE/nextflow_run.log"; exit 1; }
    echo "wall-clock: $(echo "$(date +%s.%N) - $start" | bc)s"
    if [[ -f "$OUT/pipeline_info/trace.txt" ]]; then
        awk 'NR>1 && $1 ~ /STATE_CAPITAL/ {n++} END {printf "fan-out tasks: %d\n", n}' \
            "$OUT/pipeline_info/trace.txt"
    fi
    RESULTS+=("nextflow:$OUT/capitals.csv")
fi

# ---------------------------------------------------------------- compare
echo
echo "=== Result comparison ==="
if [[ ${#RESULTS[@]} -lt 2 ]]; then
    echo "(only one engine ran — nothing to compare)"
    exit 0
fi
REF_LABEL="${RESULTS[0]%%:*}"; REF_FILE="${RESULTS[0]#*:}"
echo "reference: $REF_LABEL ($(( $(wc -l < "$REF_FILE") - 1 )) states)"
DIFFS=0
for entry in "${RESULTS[@]:1}"; do
    label="${entry%%:*}"; file="${entry#*:}"
    if diff -q "$REF_FILE" "$file" > /dev/null; then
        echo "  $label: identical"
    else
        echo "  $label: DIFFERS"
        diff "$REF_FILE" "$file" | head -10
        DIFFS=$((DIFFS + 1))
    fi
done
echo
[[ $DIFFS -eq 0 ]] && echo "all engines agree" || echo "$DIFFS engine(s) differ"
exit $DIFFS
