"""An environment must be verified RUNNABLE here, not merely installed.

⚠️ Why this exists. `discover_provided_environments` used to advertise a hash
on one condition: that `$FW_ENV_ROOT/<hash>/bin/python` existed. That is a claim
about files on disk, and on real hardware it comes apart from "this host can run
them". macmini01 is a 2009 Intel Core 2 Duo with no SSE4.2/POPCNT, so it does not
meet x86-64-v2: `pip install numpy` there SUCCEEDS (the x86_64 wheel is valid)
and only `import numpy` raises. The host would materialize the venv, advertise
the hash honestly by the old definition, claim the task, and fail at dispatch —
which is indistinguishable from a healthy runner until the work lands on it.
"""
import json
import os
import sys
from pathlib import Path

from facetwork.environments import (
    VERIFIED_MARKER,
    discover_provided_environments,
    smoke_import,
)


def _installed_dist(root: Path, name: str, body: str) -> None:
    """A minimal importable distribution: a module plus its .dist-info."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.py").write_text(body)
    info = root / f"{name}-1.0.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    (info / "top_level.txt").write_text(f"{name}\n")


def test_a_package_that_installs_but_raises_on_import_is_rejected(tmp_path, monkeypatch):
    """THE macmini01 CASE. Installed, discoverable, and fatal when imported."""
    _installed_dist(tmp_path, "cpuboundpkg",
                    "raise ImportError('built with baseline optimizations (X86_V2) "
                    "but your machine does not support them')")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    ok, err = smoke_import(sys.executable, ["cpuboundpkg==1.0"])

    assert ok is False, "an environment whose package cannot be imported was accepted"
    assert "X86_V2" in err or "baseline" in err


def test_a_healthy_package_passes(tmp_path, monkeypatch):
    _installed_dist(tmp_path, "healthypkg", "VALUE = 1\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    ok, err = smoke_import(sys.executable, ["healthypkg==1.0"])
    assert ok is True, err


def test_a_declared_package_that_is_not_installed_fails(tmp_path, monkeypatch):
    """⚠️ Regression on my own first draft: the probe treated
    ModuleNotFoundError as benign (a top_level shim), so a pin pip never placed
    PASSED. Absence of the distribution is now checked before importing."""
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    ok, err = smoke_import(sys.executable, ["totally-absent-package==9.9"])
    assert ok is False
    assert "not installed" in err


def test_no_pins_is_vacuously_fine():
    assert smoke_import(sys.executable, []) == (True, "")


# --- discovery -------------------------------------------------------------

def _fake_env(root: Path, h: str, pins: list[str] | None) -> Path:
    d = root / h
    (d / "bin").mkdir(parents=True)
    os.symlink(sys.executable, d / "bin" / "python")
    if pins is not None:
        (d / "manifest.json").write_text(json.dumps({"language": "python", "pins": pins}))
    return d


def test_discovery_skips_an_env_whose_packages_do_not_import(tmp_path, monkeypatch):
    """⚠️ Verification MUST be persisted, because discovery is a filesystem scan.
    A failing env left on disk would otherwise be re-advertised every start."""
    envroot = tmp_path / "envs"
    _installed_dist(tmp_path / "site", "brokenpkg", "raise ImportError('nope')")
    _fake_env(envroot, "deadbeef", ["brokenpkg==1.0"])
    monkeypatch.setenv("FW_ENV_ROOT", str(envroot))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "site"))

    assert discover_provided_environments() == []
    assert not (envroot / "deadbeef" / VERIFIED_MARKER).exists()


def test_discovery_verifies_then_marks_a_good_env(tmp_path, monkeypatch):
    """An env baked by an older image carries no marker; it is verified lazily
    here and marked, so hosts converge without a rebuild."""
    envroot = tmp_path / "envs"
    _installed_dist(tmp_path / "site", "goodpkg", "VALUE = 1\n")
    _fake_env(envroot, "cafe1234", ["goodpkg==1.0"])
    monkeypatch.setenv("FW_ENV_ROOT", str(envroot))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "site"))

    assert discover_provided_environments() == ["cafe1234"]
    assert (envroot / "cafe1234" / VERIFIED_MARKER).exists(), "verification was not persisted"


def test_a_marked_env_is_not_re_verified(tmp_path, monkeypatch):
    """The marker is trusted: re-probing every env on every runner start would
    add a subprocess per env to startup for no new information."""
    envroot = tmp_path / "envs"
    d = _fake_env(envroot, "beef5678", ["a-package-that-does-not-exist==1.0"])
    (d / VERIFIED_MARKER).write_text("1 /x\n")
    monkeypatch.setenv("FW_ENV_ROOT", str(envroot))

    assert discover_provided_environments() == ["beef5678"]


def test_unknown_pins_are_advertised_unchanged(tmp_path, monkeypatch):
    """No manifest means the pins cannot be determined. Declining work over a
    missing bookkeeping file would be worse than the hole this closes."""
    envroot = tmp_path / "envs"
    _fake_env(envroot, "f00dface", None)
    monkeypatch.setenv("FW_ENV_ROOT", str(envroot))

    assert discover_provided_environments() == ["f00dface"]
