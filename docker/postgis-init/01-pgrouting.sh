#!/bin/bash
# Enable pgRouting extension. The pgrouting/pgrouting image ships the package;
# the extension just needs to be created in the target database.

set -e

echo "Enabling PostGIS + pgRouting extensions in $POSTGRES_DB..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS pgrouting;
EOSQL

echo "Extensions enabled."
