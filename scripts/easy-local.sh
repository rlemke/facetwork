#!/usr/bin/env bash
# Easy one-command pipeline for LOCAL runners (no Docker required).
#
# Equivalent of easy.sh but runs everything as local Python processes:
#   1. Stop any existing local runners/dashboard
#   2. Seed example workflows into MongoDB
#   3. Register handlers and start runner(s) + dashboard
#
# Only requires:
#   - MongoDB reachable at AFL_MONGODB_URL (default: mongodb://afl-mongodb:27017)
#   - Python 3 with AFL dependencies installed
#
# All configuration comes from .env (copy .env.example to .env and edit).
#
# Usage:
#   scripts/easy-local.sh                                   # all examples, 1 runner
#   scripts/easy-local.sh --instances 3                     # 3 runner processes
#   scripts/easy-local.sh --example osm-geocoder            # single example
#   scripts/easy-local.sh --no-seed                         # skip seeding
#   scripts/easy-local.sh --no-dashboard                    # skip dashboard
#   scripts/easy-local.sh -- --log-format text              # pass args to runner
#   scripts/easy-local.sh --example osm-geocoder --instances 3 -- --log-format text
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_env.sh"

INSTANCES="${AFL_RUNNERS:-1}"
EXAMPLES=()
SEED=true
DASHBOARD=true
CLEAN_SEED=true
RUNNER_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --instances)
            INSTANCES="$2"; shift 2 ;;
        --example)
            EXAMPLES+=("$2"); shift 2 ;;
        --no-seed)
            SEED=false; shift ;;
        --no-dashboard)
            DASHBOARD=false; shift ;;
        --no-clean)
            CLEAN_SEED=false; shift ;;
        -h|--help)
            sed -n '2,/^[^#]/{ /^#/s/^# \{0,1\}//p; }' "$0"
            exit 0 ;;
        --)
            shift; RUNNER_ARGS=("$@"); break ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: scripts/easy-local.sh [--instances N] [--example NAME]... [--no-seed] [--no-dashboard] [--no-clean] [-- RUNNER_ARGS...]" >&2
            exit 1 ;;
    esac
done

echo "=== Facetwork Local Pipeline ==="
echo ""
echo "  Instances:  $INSTANCES"
echo "  Seed:       $SEED"
echo "  Dashboard:  $DASHBOARD"
if [[ ${#EXAMPLES[@]} -gt 0 ]]; then
    echo "  Examples:   ${EXAMPLES[*]}"
else
    echo "  Examples:   all"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 1: Stop any existing runners/dashboard
# ---------------------------------------------------------------------------
echo "--- Stopping existing runners ---"
scripts/stop-runners 2>/dev/null || true
echo ""

# ---------------------------------------------------------------------------
# Step 2: Verify MongoDB is reachable
# ---------------------------------------------------------------------------
MONGO_URL="${AFL_MONGODB_URL:-mongodb://afl-mongodb:27017}"
echo "--- Checking MongoDB at $MONGO_URL ---"

PYTHON="${SCRIPT_DIR}/../.venv/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON=python3

if ! "$PYTHON" -c "
from pymongo import MongoClient
c = MongoClient('$MONGO_URL', serverSelectionTimeoutMS=3000)
c.admin.command('ping')
print('  MongoDB: OK')
" 2>/dev/null; then
    echo "  ERROR: MongoDB not reachable at $MONGO_URL" >&2
    echo "  Ensure MongoDB is running and AFL_MONGODB_URL is set correctly in .env" >&2
    exit 1
fi
echo ""

# ---------------------------------------------------------------------------
# Step 3: Seed example workflows
# ---------------------------------------------------------------------------
if [[ "$SEED" == "true" ]]; then
    echo "--- Seeding example workflows ---"
    SEED_ARGS=()
    if [[ "$CLEAN_SEED" == "true" ]]; then
        SEED_ARGS+=(--clean)
    fi
    if [[ ${#EXAMPLES[@]} -gt 0 ]]; then
        for ex in "${EXAMPLES[@]}"; do
            SEED_ARGS+=(--include "$ex")
        done
    fi
    scripts/seed-examples "${SEED_ARGS[@]}"
    echo ""
fi

# ---------------------------------------------------------------------------
# Step 4: Start runners + dashboard
# ---------------------------------------------------------------------------
echo "--- Starting runners ---"
START_ARGS=()
START_ARGS+=(--instances "$INSTANCES")
if [[ "$DASHBOARD" == "false" ]]; then
    START_ARGS+=(--no-dashboard)
fi
if [[ ${#EXAMPLES[@]} -gt 0 ]]; then
    for ex in "${EXAMPLES[@]}"; do
        START_ARGS+=(--example "$ex")
    done
fi
if [[ ${#RUNNER_ARGS[@]} -gt 0 ]]; then
    START_ARGS+=(--)
    START_ARGS+=("${RUNNER_ARGS[@]}")
fi

exec scripts/start-runner "${START_ARGS[@]}"
