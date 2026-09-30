"""A role's image must carry the architecture of every host that runs it.

Measured 2026-09-29: the Java gh-router image was pushed single-arch (arm64)
while the heavy group it runs in held two x86 hosts. A registry serves a
single-arch manifest to any client, so the pull succeeded and the container
crash-looped `exec format error` for 11 days while `fleet status` read
up-to-date. These tests pin the three places that now stop that:

- the registry read (``image_archs``) that sees an image's platforms;
- the per-host refusal the fleet-agent applies before starting a role;
- the fleet-wide requirement ``fleet set`` checks against the catalog;

plus the agent's drift check, which used to skip foreign-repo agents entirely
and so never retried a gh-router re-pin whose pull had timed out.
"""
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_lib():
    path = REPO / "scripts/lib/_helpers/_fleet_lib.py"
    spec = importlib.util.spec_from_file_location("_fleet_lib_arch_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = _load_lib()


# --- normalisation ---------------------------------------------------------


@pytest.mark.parametrize("raw,want", [
    ("x86_64", "amd64"), ("amd64", "amd64"), ("linux/amd64", "amd64"),
    ("aarch64", "arm64"), ("arm64", "arm64"), ("ARM64\n", "arm64"),
    ("", None), (None, None),
])
def test_normalize_arch(raw, want):
    # The catalog says x86_64, Docker says amd64, `uname -m` on Linux says
    # aarch64: every source has to land on Docker's names or nothing compares.
    assert fl.normalize_arch(raw) == want


# --- image references ------------------------------------------------------


def test_private_registry_ref_is_split():
    assert fl._split_image_ref("server3.local:5050/osm-gh-router:3c2e7fd") == (
        "server3.local:5050", "osm-gh-router", "3c2e7fd")
    assert fl._split_image_ref("localhost/x") == ("localhost", "x", "latest")
    assert fl._split_image_ref("reg.example.com/a/b@sha256:ab") == (
        "reg.example.com", "a/b", "sha256:ab")


def test_docker_hub_ref_is_not_ours_to_read():
    # Hub needs token auth; returning None makes the check permissive for it.
    assert fl._split_image_ref("eclipse-temurin:17-jre") is None
    assert fl._split_image_ref("library/ubuntu:24.04") is None


# --- manifests ---------------------------------------------------------------


def test_manifest_list_platforms_ignore_attestations():
    index = {"manifests": [
        {"platform": {"os": "linux", "architecture": "amd64"}},
        {"platform": {"os": "linux", "architecture": "arm64"}},
        # buildx provenance/SBOM entries: not a runnable platform.
        {"platform": {"os": "unknown", "architecture": "unknown"}},
    ]}
    assert fl.archs_from_manifest(index) == {"amd64", "arm64"}


def test_single_manifest_defers_to_its_config_blob():
    assert fl.archs_from_manifest({"config": {"digest": "sha256:x"}}) is None


def test_image_archs_reads_single_arch_from_config_blob(monkeypatch):
    # A single-arch push -- exactly the gh-router image that crash-looped.
    def fake(url, accept, timeout):
        if "/manifests/" in url:
            return {"config": {"digest": "sha256:cfg"}}
        assert url.endswith("/blobs/sha256:cfg")
        return {"architecture": "arm64", "os": "linux"}
    monkeypatch.setattr(fl, "_registry_json", fake)
    assert fl.image_archs("server3.local:5050/osm-gh-router:f2ee20c") == {"arm64"}


def test_image_archs_unreachable_registry_is_unknown(monkeypatch):
    def boom(url, accept, timeout):
        raise OSError("connection refused")
    monkeypatch.setattr(fl, "_registry_json", boom)
    assert fl.image_archs("server3.local:5050/osm-gh-router:x") is None


def test_host_docker_internal_is_read_via_localhost(monkeypatch):
    # Only containers resolve host.docker.internal; the agent is a host process.
    seen = []

    def fake(url, accept, timeout):
        seen.append(url)
        return {"manifests": [{"platform": {"os": "linux", "architecture": "arm64"}}]}
    monkeypatch.setattr(fl, "_registry_json", fake)
    fl.image_archs("host.docker.internal:5050/osm-gh-router:t")
    assert seen[0].startswith("http://localhost:5050/v2/osm-gh-router/manifests/t")


# --- the per-host refusal ------------------------------------------------------


def test_refuses_on_positive_evidence_only(monkeypatch):
    monkeypatch.setattr(fl, "image_archs", lambda image, **k: {"arm64"})
    why = fl.image_arch_refusal("r:5050/osm-gh-router:t", host_arch="amd64")
    assert why and "arm64" in why and "linux/amd64" in why
    assert fl.image_arch_refusal("r:5050/osm-gh-router:t", host_arch="arm64") is None


def test_unknown_platforms_permit(monkeypatch):
    # An unreadable registry is already fatal to the pull that follows; refusing
    # here too would only swap compose's error for a vaguer one.
    monkeypatch.setattr(fl, "image_archs", lambda image, **k: None)
    assert fl.image_arch_refusal("r:5050/x:t", host_arch="amd64") is None


def test_multi_arch_image_is_never_refused(monkeypatch):
    monkeypatch.setattr(fl, "image_archs", lambda image, **k: {"amd64", "arm64"})
    for arch in ("amd64", "arm64"):
        assert fl.image_arch_refusal("r:5050/x:t", host_arch=arch) is None


# --- the fleet-wide requirement ----------------------------------------------


def _catalog(tmp_path, monkeypatch, servers):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps({"servers": servers}))
    monkeypatch.setenv("FW_SERVERS_FILE", str(p))


def test_fleet_archs_follow_the_roles_server_groups(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch, [
        {"name": "mac.local", "group": "heavy", "capacity": {"arch": "arm64"}},
        {"name": "nuc.local", "group": "heavy", "capacity": {"arch": "x86_64"}},
        {"name": "tiny.local", "group": "runner", "capacity": {"arch": "x86_64"}},
        {"name": "unknown.local", "group": "heavy"},
    ])
    assert fl.fleet_archs(["heavy"]) == {"mac.local": "arm64", "nuc.local": "amd64"}
    # No server_groups = every host, the same rule role_in_group applies.
    assert set(fl.fleet_archs(None)) == {"mac.local", "nuc.local", "tiny.local"}


# --- the agent's drift check -------------------------------------------------


def _load_agent_fn():
    """Pull `_containers_on_wrong_image` out of the agent without running it.

    The agent script re-execs under the venv and imports pymongo at module
    level, so it is compiled from source and only this function is taken."""
    import ast
    src = (REPO / "scripts/lib/fleet/agent").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_containers_on_wrong_image")
    ns = {"subprocess": subprocess}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "agent", "exec"), ns)
    return ns["_containers_on_wrong_image"]


def _ps(monkeypatch, lines):
    class R:
        stdout = "\n".join(lines)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())


def test_drift_compares_each_container_with_its_own_repo(monkeypatch):
    wrong = _load_agent_fn()
    _ps(monkeypatch, [
        "facetwork-runner-ffl-1\treg:5050/facetwork-runner:new",
        "facetwork-runner-gh-router-1\treg:5050/osm-gh-router:old",
    ])
    # The runner is on its tag; the gh-router is on the OLD pin -> repaired.
    assert wrong("reg:5050/facetwork-runner:new", "reg:5050/osm-gh-router:new") == [
        "facetwork-runner-gh-router-1"]


def test_unpinned_foreign_repo_is_left_alone(monkeypatch):
    wrong = _load_agent_fn()
    _ps(monkeypatch, [
        "facetwork-runner-ffl-1\treg:5050/facetwork-runner:new",
        "facetwork-runner-gh-router-1\treg:5050/osm-gh-router:whatever",
    ])
    # No gh-router pin for this host (gated out): never a recreate loop.
    assert wrong("reg:5050/facetwork-runner:new", None) == []


def test_registry_port_is_not_mistaken_for_a_tag(monkeypatch):
    wrong = _load_agent_fn()
    _ps(monkeypatch, ["facetwork-runner-x-1\treg:5050/facetwork-runner",
                      "facetwork-runner-y-1\treg:5050/facetwork-runner:latest"])
    # Untagged means :latest -- repo "reg:5050/facetwork-runner", never repo
    # "reg" with tag "5050/facetwork-runner".
    assert wrong("reg:5050/facetwork-runner:latest") == []
    assert wrong("reg:5050/facetwork-runner:new") == [
        "facetwork-runner-x-1", "facetwork-runner-y-1"]
