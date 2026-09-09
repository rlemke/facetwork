"""Baking a PRIVATE domain repo, and making a skipped domain visible.

fwh_unimatch shipped MISSING from an image whose rollout reported success: the
bake clones anonymously, a private repo returns rc=128, continue-on-error skips
it, and the build exits 0. Every test here pins one half of that -- the auth path
that makes it bake, and the reporting that makes a miss impossible to overlook.
"""
import importlib.util
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _bake():
    spec = importlib.util.spec_from_file_location("bake_domains", REPO / "docker/bake-domains.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_anonymous_clone_is_tried_first(monkeypatch):
    """A public repo must never see the credential.

    Also keeps behaviour identical on build hosts with no token at all.
    """
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    b = _bake()
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    b._clone("fwh_public", "/opt/fwh_public", "tok_secret")
    assert len(calls) == 1, "a successful anonymous clone must not retry"
    assert not any("tok_secret" in str(x) for x in calls[0])


def test_failed_clone_retries_with_the_token(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "rm":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if len([c for c in calls if c[0] != "rm"]) == 1:      # anonymous attempt
            raise subprocess.CalledProcessError(128, cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    b = _bake()
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    b._clone("fwh_unimatch", "/opt/fwh_unimatch", "tok_secret")
    retry = [c for c in calls if c[0] == "git" and "-c" in c]
    assert retry, "expected an authenticated retry"
    hdr = retry[0][retry[0].index("-c") + 1]
    assert hdr.startswith("http.extraHeader=Authorization: Basic ")


def test_token_never_goes_into_the_url_or_git_config(monkeypatch):
    """The credential must not be able to survive into an image layer.

    `-c` values are command-line only and are NOT written to .git/config, unlike
    a token embedded in the clone URL -- which would persist in the repo config
    until the `rm -rf .git` step, and outlive it if any earlier step failed.
    """
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "rm":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if len([c for c in calls if c[0] != "rm"]) == 1:
            raise subprocess.CalledProcessError(128, cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    b = _bake()
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    b._clone("fwh_unimatch", "/opt/fwh_unimatch", "tok_secret")
    urls = [a for c in calls for a in c if str(a).startswith("https://")]
    assert urls, "expected a clone URL"
    for u in urls:
        assert "tok_secret" not in u and "@" not in u.split("://", 1)[1].split("/")[0]


def test_no_token_means_the_original_error_propagates(monkeypatch):
    """Without a secret the failure must stay a failure, not become a silent pass."""
    def fake_run(cmd, **kw):
        raise subprocess.CalledProcessError(128, cmd)

    b = _bake()
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        b._clone("fwh_unimatch", "/opt/fwh_unimatch", None)


def test_partial_clone_is_cleared_before_the_retry(monkeypatch):
    """git refuses to clone into a non-empty directory, so the retry would fail
    for the wrong reason and look like an auth problem."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "rm":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if len([c for c in calls if c[0] != "rm"]) == 1:
            raise subprocess.CalledProcessError(128, cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    b = _bake()
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    b._clone("fwh_x", "/opt/fwh_x", "tok")
    assert ["rm", "-rf", "/opt/fwh_x"] in calls


def test_a_skipped_domain_is_recorded_not_just_warned():
    """The defect was invisibility, not the skip itself.

    Continue-on-error is deliberate (one bad repo must not fail a 30-domain
    build), so the fix is to make the miss legible: written into the image and
    stated as 'NO fleet runner can claim their work'.
    """
    src = (REPO / "docker/bake-domains.py").read_text()
    assert "/etc/afl-bake-failures" in src
    assert "NOT BAKED" in src
    assert "no build secret mounted" in src, "the WARN must name the likely cause"


def test_secret_is_optional_so_a_tokenless_host_still_builds():
    df = (REPO / "docker/Dockerfile.domain-runner").read_text()
    assert "type=secret,id=gh_token,required=false" in df
    # An ARG/ENV would land in image history; a secret mount cannot.
    assert "ARG GITHUB_TOKEN" not in df and "ENV GITHUB_TOKEN" not in df


def test_rollout_passes_the_secret_by_file_not_env():
    """`env=` would put the token in this process's environment and in `ps`."""
    src = (REPO / "scripts/lib/fleet/rollout").read_text()
    assert "id=gh_token,src=" in src
    assert "id=gh_token,env=" not in src
    assert "umask 077" in src, "the secret file must not be world-readable"
    assert 'trap ' in src and '_SECRET_DIR' in src, "the secret file must be removed on exit"
