#!/usr/bin/env bash
# Shared helper: read the domain/example catalog (domains.json + overrides) from
# bash by shelling out to the Python loader (facetwork.domains.catalog), so shell
# commands and Python consumers see the SAME resolved catalog (incl. FW_DOMAINS_FILE
# / domains.local.json overrides). Source AFTER _bootstrap.sh (needs FW_ROOT).

_catalog_python() {
    local py="$FW_ROOT/.venv/bin/python3"
    [ -x "$py" ] || py="$FW_ROOT/.venv/bin/python"
    [ -x "$py" ] || py="$(command -v python3 || command -v python)"
    PYTHONPATH="$FW_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$py" "$@"
}

# All domain short-names, one per line (sorted).
catalog_domain_names() {
    _catalog_python -c 'from facetwork.domains.catalog import domain_names; print("\n".join(domain_names()))'
}

# Registry rows: name␟repo␟extras_csv␟description, one per line. Fields are joined
# with US (\x1f, non-whitespace) so empty fields survive `read` (a whitespace IFS
# like TAB coalesces empties and shifts columns).
catalog_domain_rows() {
    _catalog_python -c '
from facetwork.domains.catalog import domains
for n, s in sorted(domains().items()):
    print("\x1f".join([n, s.get("repo",""), ",".join(s.get("extras",[])), s.get("description","")]))
'
}

# One field of one domain (empty if absent): catalog_domain_field <name> <field>
catalog_domain_field() {
    _catalog_python -c '
import sys
from facetwork.domains.catalog import get_domain
d = get_domain(sys.argv[1]) or {}
v = d.get(sys.argv[2], "")
print(",".join(v) if isinstance(v, list) else v)
' "$1" "$2"
}

# All compose runner-<service> names (one per line, sorted by domain).
catalog_compose_services() {
    _catalog_python -c 'from facetwork.domains.catalog import compose_services; print("\n".join(compose_services()))'
}

# runner-<service> names for fleet_default domains (the default --fleet set).
catalog_fleet_default_services() {
    _catalog_python -c 'from facetwork.domains.catalog import fleet_default_services; print("\n".join(fleet_default_services()))'
}

# --domain suffixes for scaled compose domains (run at the throughput knob).
catalog_scaled_domain_suffixes() {
    _catalog_python -c 'from facetwork.domains.catalog import scaled_domain_suffixes; print("\n".join(scaled_domain_suffixes()))'
}

# --domain suffixes for non-scaled compose domains (one replica each).
catalog_unscaled_domain_suffixes() {
    _catalog_python -c 'from facetwork.domains.catalog import unscaled_domain_suffixes; print("\n".join(unscaled_domain_suffixes()))'
}

# Catalog default replica count for non-scaled domain runners.
catalog_default_replicas() {
    _catalog_python -c 'from facetwork.domains.catalog import default_replicas; print(default_replicas())'
}

# Catalog default replica count for the scaled tier (FW_OSM_REPLICAS overrides).
catalog_scaled_replicas() {
    _catalog_python -c 'from facetwork.domains.catalog import scaled_replicas; print(scaled_replicas())'
}

# The resolved catalog source path (for diagnostics).
catalog_source() {
    _catalog_python -c 'from facetwork.domains.catalog import catalog_source; print(catalog_source())'
}
