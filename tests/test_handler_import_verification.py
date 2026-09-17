"""A runner must not advertise a handler it cannot actually import.

⚠️ Why this exists. `registration_module_available` was `find_spec` alone, which
LOCATES a module and never executes it. A module whose first line is an
impossible import passed, so the function did not test the thing its own
docstring promised. That is how macmini01 advertised 265 facets it could not
execute: its 2009 CPU (no SSE4.2/POPCNT, so no x86-64-v2) cannot run the image's
numpy, every handler module importing numpy raises, and every one of them was
located successfully. The tasks then failed at DISPATCH, burning retry budget,
while the host reported healthy.
"""
import json
import os
import sys
import textwrap

import pytest

import facetwork.runtime.dispatcher as disp


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Each test gets its own cache file and a cold in-process memo."""
    monkeypatch.setenv("FW_LOCAL_SCRATCH", str(tmp_path))
    monkeypatch.setenv("FW_RUNNER_IMAGE", "reg:5050/facetwork-runner:testtag")
    monkeypatch.setattr(disp, "_IMPORT_VERIFY", None, raising=False)
    monkeypatch.setattr(disp, "_IMPORT_VERIFY_PATH", None, raising=False)
    yield


def _module(tmp_path, monkeypatch, name, body):
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    return name


def test_a_module_that_locates_but_cannot_import_is_rejected(tmp_path, monkeypatch):
    """THE macmini01 CASE — and the one find_spec alone passed."""
    n = _module(tmp_path, monkeypatch, "brokenhandler", """
        import a_module_that_cannot_possibly_exist
        def handle(params): return {}
    """)
    # find_spec alone would say yes; the real check must say no.
    assert disp.importlib.util.find_spec(n) is not None, "precondition: it IS locatable"
    assert disp.registration_module_available(n) is False


def test_a_healthy_module_is_accepted(tmp_path, monkeypatch):
    n = _module(tmp_path, monkeypatch, "goodhandler", """
        VALUE = 1
        def handle(params): return {"ok": True}
    """)
    assert disp.registration_module_available(n) is True


def test_a_module_that_cannot_be_located_is_rejected_without_importing():
    assert disp.registration_module_available("no_such_module_anywhere_xyz") is False


def test_the_result_is_cached_on_disk(tmp_path, monkeypatch):
    """A generalist runner carries ~265 registrations; the import pass must be
    paid once per image per host, not on every start."""
    n = _module(tmp_path, monkeypatch, "cachedhandler", "VALUE = 1\n")
    assert disp.registration_module_available(n) is True
    cache_file = tmp_path / "fw-import-verify-testtag.json"
    assert cache_file.exists(), "verification was not persisted"
    assert json.loads(cache_file.read_text())[n] is True


def test_failures_are_cached_too(tmp_path, monkeypatch):
    """An ImportError under a given image on a given host is a stable fact.
    Re-deriving it every start is pure cost."""
    n = _module(tmp_path, monkeypatch, "stillbroken", "import nope_not_here\n")
    assert disp.registration_module_available(n) is False
    cache_file = tmp_path / "fw-import-verify-testtag.json"
    assert json.loads(cache_file.read_text())[n] is False


def test_cache_is_keyed_by_image_tag(tmp_path, monkeypatch):
    """⚠️ A rollout must not inherit the previous image's answers — the new image
    is precisely what changed."""
    _module(tmp_path, monkeypatch, "taggedmod", "VALUE = 1\n")
    disp.registration_module_available("taggedmod")
    assert (tmp_path / "fw-import-verify-testtag.json").exists()

    monkeypatch.setenv("FW_RUNNER_IMAGE", "reg:5050/facetwork-runner:othertag")
    monkeypatch.setattr(disp, "_IMPORT_VERIFY", None, raising=False)
    monkeypatch.setattr(disp, "_IMPORT_VERIFY_PATH", None, raising=False)
    disp.registration_module_available("taggedmod")
    assert (tmp_path / "fw-import-verify-othertag.json").exists(), "tag not in the key"


def test_a_file_uri_is_still_a_path_check(tmp_path):
    """file:// registrations are bind-mounted sources; existence is the question."""
    f = tmp_path / "h.py"; f.write_text("def handle(p): return {}\n")
    assert disp.registration_module_available(f"file://{f}") is True
    assert disp.registration_module_available(f"file://{tmp_path}/absent.py") is False


def test_an_unwritable_cache_dir_costs_a_reverify_not_a_wrong_answer(tmp_path, monkeypatch):
    n = _module(tmp_path, monkeypatch, "rohandler", "VALUE = 1\n")
    monkeypatch.setenv("FW_LOCAL_SCRATCH", str(tmp_path / "does" / "not" / "exist"))
    monkeypatch.setattr(disp, "_IMPORT_VERIFY", None, raising=False)
    monkeypatch.setattr(disp, "_IMPORT_VERIFY_PATH", None, raising=False)
    assert disp.registration_module_available(n) is True     # still correct
