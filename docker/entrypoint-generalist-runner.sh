#!/usr/bin/env bash
# Entrypoint for a GENERALIST Facetwork runner: one container serving several
# cold domains instead of one container per domain.
#
# Why this exists. A per-domain runner is right for a hot domain — it isolates
# a heavy handler and keeps a flooded queue off everyone else. It is poor value
# for a domain that runs a handful of tasks a month: each idle container still
# costs ~160 MiB resident and polls MongoDB once a second forever. Measured on
# this deployment, 13 of 16 domain runners handled ZERO tasks in seven days.
#
# Nothing new is needed to fix that. The image already bakes every domain, and
# `--topics` already scopes which namespaces a runner claims — so a single
# runner can advertise the union of several domains' facets. This entrypoint is
# the per-domain one with the singular made plural.
#
# What it does NOT change: routing correctness. Task claiming is name-filtered
# server-side, so a generalist claims exactly the namespaces it advertises and
# nothing else. Consolidating cold domains cannot make a hot domain's queue
# reachable from here.
#
# Required env:
#   FW_DOMAIN_NAMES     comma- or space-separated short names
#                       (e.g. "anthropic,cancer,genomics")
#   FW_MONGODB_URL      mongodb://mongodb:27017
#
# Optional env:
#   FW_HANDLERS_ROOT         default /handlers — bind-mount root for fwh_* repos
#   FW_REGISTRY_RUNNER_ARGS  extra args to forward to the registry runner

set -euo pipefail

: "${FW_DOMAIN_NAMES:?must be set (FW_DOMAIN_NAMES), e.g. \"anthropic,cancer\"}"
: "${FW_MONGODB_URL:?must be set}"

# Accept commas or spaces so the compose file can read either way.
read -r -a DOMAINS <<< "${FW_DOMAIN_NAMES//,/ }"

echo "==> generalist runner over ${#DOMAINS[@]} domain(s): ${DOMAINS[*]}"
echo "    mongodb=$FW_MONGODB_URL"

# Every domain must be baked. A generalist deliberately does NOT fall back to
# pip-installing a bind-mount the way the per-domain entrypoint does: that
# fallback exists for local iteration on ONE domain, and doing it for a list
# would turn container start into a serial pip run whose failure mode is a
# runner that silently advertises a subset of what it was asked to cover.
# Fail loudly instead, and name every domain that is missing rather than only
# the first.
missing=()
for d in "${DOMAINS[@]}"; do
    if [[ -f /etc/afl-baked-domains ]] && grep -qxF "$d" /etc/afl-baked-domains; then
        continue
    fi
    missing+=("$d")
done
if (( ${#missing[@]} )); then
    echo "ERROR: not baked into this image: ${missing[*]}" >&2
    echo "       A generalist runner serves baked domains only. Either bake them" >&2
    echo "       (docker/bake-domains.py + domains.json) or run those as their own" >&2
    echo "       per-domain runner with a bind-mount." >&2
    exit 1
fi

# One process seeds every domain AND emits the UNION of their topic globs —
# `facetwork.domains` already accepts a list and unions the globs, so this is
# a single interpreter startup regardless of how many domains are consolidated.
echo "    Registering handlers + seeding workflows for ${#DOMAINS[@]} domain(s)"
SEED_OUT="$(python -m facetwork.domains --seed --emit-topics "${DOMAINS[@]}" 2>&1)"
echo "$SEED_OUT" | grep -v '^FW_TOPICS='
DOMAIN_TOPICS="$(printf '%s\n' "$SEED_OUT" | sed -n 's/^FW_TOPICS=//p' | head -1)"

# An empty union would make the runner unscoped, and an unscoped registry runner
# loads every importable handler in the image and claims EVERY namespace's work
# — including the heavy domains this consolidation is meant to stay away from.
# Refuse instead: "scoped to nothing" must never silently become "scoped to all".
if [ -z "$DOMAIN_TOPICS" ]; then
    echo "ERROR: the named domains declare no facet namespaces, so this runner" >&2
    echo "       would be unscoped and would claim every domain's work." >&2
    exit 1
fi
echo "    Scoping runner to topics: $DOMAIN_TOPICS"

# `set -f` so the topic globs (e.g. cancer.*) are word-split into argv but NOT
# filename-expanded by the shell.
echo "    Starting runner (registry mode)"
set -f
exec python -m facetwork.runtime.runner --registry --topics $DOMAIN_TOPICS ${FW_REGISTRY_RUNNER_ARGS:-}
