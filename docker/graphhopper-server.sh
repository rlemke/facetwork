#!/usr/bin/env bash
# graphhopper-server — serve ONE prebuilt GraphHopper routing graph over HTTP.
#
# The low-zoom builder (osm.Roads.ZoomBuilder, `zoom_sbs.py`) computes road
# importance by ROUTING sampled city pairs against a graphhopper-web server at
# GRAPHHOPPER_API_URL. Without one, every route fails at DEBUG level and the step
# still reports success — measured 2026-09-22 on Washington: "Completed routing:
# 0 / 114163 pairs succeeded", zero betweenness, empty bypass/ring flags.
#
# This wraps the SAME graphhopper-web jar the build facet used for `import`
# (graphhopper-web can only load a graph it imported itself — the profile hash
# must match, so the profile block below mirrors _build_config_yaml in
# fwh_osm's graphhopper_build.py exactly). The graph is pulled from the object
# store cache (`cache/osm/graphhopper/<region>-latest/<profile>/`) into local
# scratch; a missing graph is a REFUSAL, not an empty server — a server that
# answers 200 with no graph would look exactly like today's silent zero.
#
# One server serves ONE region+profile (GraphHopper's model). FW_GRAPHHOPPER_REGION
# picks it; the fleet role carries it (fw fleet set --graphhopper-region …).
set -euo pipefail

REGION="${FW_GRAPHHOPPER_REGION:?FW_GRAPHHOPPER_REGION must name a built region, e.g. north-america/us/washington}"
PROFILE="${FW_GRAPHHOPPER_PROFILE:-car}"
JAR="${GRAPHHOPPER_JAR:-/opt/graphhopper/graphhopper-web.jar}"
PORT="${FW_GRAPHHOPPER_LISTEN_PORT:-8989}"
BUCKET="${FW_S3_BUCKET:-}"
if [ -z "$BUCKET" ]; then
    case "${FW_DATA_ROOT:-}" in s3://*) BUCKET="${FW_DATA_ROOT#s3://}"; BUCKET="${BUCKET%%/*}" ;; esac
fi
BUCKET="${BUCKET:-afl-cache}"
SCRATCH="${FW_LOCAL_SCRATCH:-/scratch}"
GRAPH_DIR="$SCRATCH/graphhopper-server/${REGION//\//_}/$PROFILE"
KEY_PREFIX="cache/osm/graphhopper/${REGION}-latest/${PROFILE}/"

[ -f "$JAR" ] || { echo "graphhopper-server: jar not found at $JAR" >&2; exit 1; }
mkdir -p "$GRAPH_DIR"

echo "graphhopper-server: region=$REGION profile=$PROFILE bucket=$BUCKET"
echo "graphhopper-server: syncing s3://$BUCKET/$KEY_PREFIX -> $GRAPH_DIR"
python3 - "$BUCKET" "$KEY_PREFIX" "$GRAPH_DIR" <<'PY'
import os, sys, boto3
from botocore.config import Config
bucket, prefix, dest = sys.argv[1:4]
s3 = boto3.client("s3", endpoint_url=os.environ.get("FW_S3_ENDPOINT"),
                  aws_access_key_id=os.environ.get("FW_S3_ACCESS_KEY"),
                  aws_secret_access_key=os.environ.get("FW_S3_SECRET_KEY"),
                  region_name=os.environ.get("FW_S3_REGION", "us-east-1"),
                  config=Config(retries={"max_attempts": 3}))
objs = []
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
    objs += page.get("Contents", [])
files = {o["Key"][len(prefix):]: o for o in objs if o["Key"] != prefix and "/" not in o["Key"][len(prefix):]}
if not files or "nodes" not in files or "properties" not in files:
    print(f"graphhopper-server: NO BUILT GRAPH at s3://{bucket}/{prefix} "
          f"(have: {sorted(files) or 'nothing'}). Build it first: "
          f"osm.cache.GraphHopper.<Region>(cache = …) — refusing to serve an empty graph.", file=sys.stderr)
    sys.exit(2)
synced = kept = 0
for name, o in files.items():
    path = os.path.join(dest, name)
    if os.path.exists(path) and os.path.getsize(path) == o["Size"]:
        kept += 1; continue
    s3.download_file(bucket, o["Key"], path + ".part")
    os.replace(path + ".part", path); synced += 1
print(f"graphhopper-server: graph ready ({len(files)} files, {synced} fetched, {kept} already local, "
      f"{sum(o['Size'] for o in files.values())/1e6:.0f} MB)")
PY

# Mirror fwh_osm graphhopper_build._build_config_yaml: the profile block (and the
# import-time ignored_highways) must match what built the graph, or graphhopper-web
# refuses to load it ("Profiles do not match").
case "$PROFILE" in
    car|truck|motorcycle|small_truck|scooter) IGNORED="footway,cycleway,path,pedestrian,steps" ;;
    foot|hike|bike|mtb|racingbike|wheelchair) IGNORED="motorway,trunk" ;;
    *) IGNORED="" ;;
esac
# Contraction hierarchies: prepared ON LOAD when configured but absent (GraphHopper
# 8 `loadOrPrepareCH`), persisted next to the graph, then every route is a
# shortcut search instead of a full Dijkstra. Measured without CH on Washington
# (2.2M edges, 14 cores saturated): ~40 routes/s, i.e. ~2.5 h for the low-zoom
# builder's ~280k sampled pairs; with CH the same work is minutes. Preparation
# costs a few minutes once per graph. FW_GRAPHHOPPER_CH=0 disables it.
CH_BLOCK=""
if [ "${FW_GRAPHHOPPER_CH:-1}" != "0" ]; then
    CH_THREADS="${FW_GRAPHHOPPER_CH_THREADS:-$(( $(nproc 2>/dev/null || echo 4) / 2 ))}"
    [ "$CH_THREADS" -ge 1 ] || CH_THREADS=1
    CH_BLOCK="  profiles_ch:
    - profile: $PROFILE
  prepare.ch.threads: $CH_THREADS"
fi
CFG="$(mktemp -t gh-server-XXXXXX.yml)"
cat > "$CFG" <<YML
graphhopper:
  graph.location: $GRAPH_DIR
  import.osm.ignored_highways: $IGNORED
  profiles:
    - name: $PROFILE
      vehicle: $PROFILE
      custom_model_files: []
$CH_BLOCK
server:
  application_connectors:
    - type: http
      port: $PORT
      bind_host: 0.0.0.0
  admin_connectors:
    - type: http
      port: $((PORT + 1))
      bind_host: 127.0.0.1
YML
echo "graphhopper-server: starting graphhopper-web on :$PORT (graph $GRAPH_DIR)"
exec java ${JAVA_OPTS:--Xmx2g} -jar "$JAR" server "$CFG"
