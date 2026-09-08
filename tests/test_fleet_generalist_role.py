"""The generalist tier as a fleet role.

One runner fronting several cold domains, instead of a container each. The role
carries a MEMBERSHIP LIST, which no other role has, and every test here pins a
failure that list makes possible.
"""
import importlib.util
import os
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(rel):
    """Load one of the extension-less `fw` command scripts as a module."""
    path = REPO / rel
    spec = importlib.util.spec_from_loader(
        rel.replace("/", "_"),
        importlib.machinery.SourceFileLoader(rel.replace("/", "_"), str(path)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# compose generation
# --------------------------------------------------------------------------
def test_domain_names_is_env_overridable_not_baked():
    """A per-host choice must not be frozen into a shared, committed file.

    `consolidated` is per-deployment (the same domain is cold on one and hot on
    another) but docker-compose.full-stack.yml is generated once and committed.
    Baked in bare, the generating host's list reaches every host -- and when that
    host consolidates nothing the value is EMPTY, which the entrypoint refuses
    (`${FW_DOMAIN_NAMES:?}`), so every other host's generalist crash-loops.
    """
    from facetwork.domains.compose_gen import _render_generalist

    out = _render_generalist([])
    assert "FW_DOMAIN_NAMES: ${FW_DOMAIN_NAMES:-}" in out, out
    # ...and the catalog value stays the DEFAULT, not a replacement for it.
    out2 = _render_generalist(["anthropic", "cancer"])
    assert "FW_DOMAIN_NAMES: ${FW_DOMAIN_NAMES:-anthropic,cancer}" in out2, out2


def test_empty_membership_stays_profile_gated():
    """`docker compose up -d` must not start a generalist with no members."""
    from facetwork.domains.compose_gen import _render_generalist

    assert 'profiles: ["generalist"]' in _render_generalist([])
    assert 'profiles: ["generalist"]' not in _render_generalist(["cancer"])


# --------------------------------------------------------------------------
# the availability listing that the profile gate broke
# --------------------------------------------------------------------------
def test_service_validation_enables_every_profile():
    """`config --services` lists services ACTIVE in the current profile set, not
    services that EXIST. runner-generalist is profile-gated when the catalog
    consolidates nothing, so validating against the default listing rejects it
    as "no compose service" -- which reads as compose-file drift, not a profile.
    """
    src = (REPO / "scripts/lib/runner/start").read_text()
    assert "config --profiles" in src
    assert 'COMPOSE_PROFILES="$_PROFILES"' in src
    # The bare form must be gone, or the gate silently returns.
    assert 'AVAIL="$(docker compose "${COMPOSE_F[@]}" ${COMPOSE_ENV[@]+"${COMPOSE_ENV[@]}"} config --services' not in src


# --------------------------------------------------------------------------
# fleet_config mutation
# --------------------------------------------------------------------------
def test_empty_generalist_removes_the_role_rather_than_emptying_it():
    """`--generalist ""` must delete the role.

    Leaving it present with `domains: []` produces a role the agent refuses to
    start -- configured, gated in, and permanently inert.
    """
    src = (REPO / "scripts/lib/fleet/config").read_text()
    assert 'pop("generalist", None)' in src


def test_generalist_membership_is_validated_against_the_catalog():
    """A typo must warn at `fleet set` time.

    Unvalidated, it surfaces much later inside a container that refuses to start
    because the name is not baked -- far from the command that caused it.
    """
    src = (REPO / "scripts/lib/fleet/config").read_text()
    i = src.index("if a.generalist is not None:")
    assert "_warn_unknown_domain_runners(names)" in src[i:i + 900]


# --------------------------------------------------------------------------
# the agent
# --------------------------------------------------------------------------
def test_agent_refuses_an_empty_membership_list_loudly():
    """Configured-but-empty is a mistake worth a WARNING, not a silent skip.

    The role existing means someone meant to consolidate here; starting it with
    an empty list would crash-loop the container instead.
    """
    src = (REPO / "scripts/lib/fleet/agent").read_text()
    assert "gen_replicas > 0 and gen_here and gen_domains" in src
    assert "EMPTY" in src and "fw fleet set --generalist" in src


def test_generalist_counts_as_one_runner_not_one_per_domain():
    """Convergence bookkeeping must not scale with membership.

    A generalist over 20 domains registers ONE runner -- that is the point of
    consolidating. Counting members would understate convergence by
    len(domains)-1 forever, and a host would never report as converged.
    """
    src = (REPO / "scripts/lib/fleet/agent").read_text()
    assert "+ (gen_replicas if (gen_here and gen_domains) else 0)" in src


def test_generalist_obeys_the_server_group_gate():
    """The whole reason it exists here: keep 15 containers off a small host."""
    src = (REPO / "scripts/lib/fleet/agent").read_text()
    assert "gen_here = _fleet_lib.role_in_group(gen_role, grp)" in src
    # ...and a gated-out generalist is REPORTED, not silently absent.
    assert '"generalist")' in src


def test_membership_rides_the_child_env_not_the_shared_env_file():
    """FW_DOMAIN_NAMES is read by exactly one service.

    Writing it into the shared env file would hand a fleet-wide value to every
    role, so a future service reading the same name would inherit one host's
    consolidation choice by accident.
    """
    src = (REPO / "scripts/lib/fleet/agent").read_text()
    assert 'dict(child_env, FW_DOMAIN_NAMES=",".join(gen_domains))' in src
