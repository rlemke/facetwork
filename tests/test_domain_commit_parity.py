"""Every architecture of one image tag must contain the same domain code.

Each platform of a multi-arch build clones the fwh_* repos INDEPENDENTLY, at
different wall-clock times, and `git clone` in a RUN layer is not
content-addressed. FW_FLEET_DOMAINS_REF busts the cache; it does not PIN a commit.

Measured 2026-09-08 in production: tag e20e532d-droadsafety-e carried unimatch
@3d6d8a9 on amd64 and @5822d35 on arm64 -- the fixed and the broken version of the
same domain, in the same tag, on a fleet where 46 of 48 runners are arm64. Nothing
prevented it and nothing reported it; it was found by hand-comparing containers.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts/lib/fleet/verify-domain-parity"
BAKE = (REPO / "docker/bake-domains.py").read_text()
ROLLOUT = (REPO / "scripts/lib/fleet/rollout").read_text()


def test_the_bake_records_a_commit_per_domain():
    """Names alone cannot answer 'is it the same CODE on both arches?'."""
    assert "/etc/afl-domain-commits" in BAKE
    assert 'commits[name] = Path(dest + ".commit").read_text().strip()' in BAKE


def test_checker_exists_and_is_executable():
    assert CHECK.exists()
    import os
    assert os.access(CHECK, os.X_OK)


def test_checker_does_not_execute_the_foreign_image():
    """`docker create` + `docker cp` inspect a foreign arch with NO emulation, and
    work even on an image that crash-loops when run."""
    src = CHECK.read_text()
    assert "docker create --platform" in src
    assert "docker cp" in src
    assert "docker run" not in src


def test_checker_reads_the_manifest_over_http_not_imagetools():
    """`docker buildx imagetools` forces HTTPS and this registry is plain HTTP:
    'server gave HTTP response to HTTPS client'. The daemon is fine with it
    (insecure-registries), so only the manifest read needed replacing."""
    src = CHECK.read_text()
    # The COMMAND must be gone; the comment explaining why may of course remain.
    assert "docker buildx imagetools inspect" not in src
    assert "/v2/$_REPO/manifests/$_TAG" in src


def test_checker_resolves_the_venv_interpreter():
    """A bare python3 is the SYSTEM one, which has no pymongo -- the check would
    report 'cannot verify' on a host where verification is perfectly possible."""
    src = CHECK.read_text()
    assert '.venv/bin/python3' in src


def test_cannot_verify_is_distinct_from_passing():
    """Exit 2 must never be mistaken for 0. An unreachable registry or an image
    predating the manifest is NOT evidence of parity."""
    src = CHECK.read_text()
    assert "exit 2" in src and "exit 1" in src and "exit 0" in src
    assert "0 identical" in src and "1 DIVERGED" in src and "2 could not verify" in src


def test_divergence_names_the_domains_not_just_the_fact():
    """Knowing WHICH domain differs is the actionable part."""
    src = CHECK.read_text()
    assert "join -j1" in src


def test_rollout_runs_the_check_before_pointing_the_fleet_at_the_image():
    # Anchor on the ACTION, not the first mention of the word: "fleet_config"
    # appears in a header comment on line 2, which would make this pass trivially.
    i = ROLLOUT.index("verify-domain-parity")
    j = ROLLOUT.index('"$FW_LIB/fleet/config" set --mongo')
    assert i < j, "parity must be checked before fleet_config is pointed at the image"


def test_rollout_does_not_hard_fail_on_a_pre_manifest_image():
    """The checker is newer than existing images; a rollout must not start failing
    merely because the check cannot apply yet."""
    seg = ROLLOUT[ROLLOUT.index("domain-commit parity"):][:1600]
    assert "parity unverified" in seg
