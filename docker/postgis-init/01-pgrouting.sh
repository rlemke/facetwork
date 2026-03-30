#!/bin/bash
# Install pgRouting extension in the PostGIS container and enable it.
# This runs on first container start via /docker-entrypoint-initdb.d/.

set -e

echo "Installing pgRouting..."
apt-get update -qq && apt-get install -y --no-install-recommends postgresql-16-pgrouting >/dev/null 2>&1

echo "Enabling pgRouting extension in $POSTGRES_DB..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS pgrouting;
EOSQL

echo "pgRouting extension enabled."
