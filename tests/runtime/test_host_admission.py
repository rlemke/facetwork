"""Host-level admission: a host whose core stack cannot run must not advertise.

`registration_module_available` imports a handler MODULE, which is necessary but
not sufficient: a dependency imported inside a FUNCTION is never exercised by
importing the module. Measured on macmini01 (2009 Core 2 Duo, no SSE4.2/POPCNT):
`osm_geocoder` imported fine, `numpy` did not, and the host kept advertising 265
handlers of which 122 were numpy-dependent. No per-handler declaration fixes
that, because it depends on where an author put an import statement.

⚠️ The regression this must never cause: a deployment that simply HAS NO numpy
(someone running only their own handlers) must keep advertising normally.
"Absent" and "installed but broken" are different facts, and only the second is a
reason to refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from facetwork.runtime import dispatcher as disp
from facetwork.runtime.dispatcher import RegistryDispatcher
from facetwork.runtime.entities.server import HandlerRegistration
from facetwork.runtime.memory_store import MemoryStore


@pytest.fixture(autouse=True)
def _reset_verdict():
    """The verdict is cached per process (a CPU gains no instructions at runtime),
    so every test must start from unknown or it would read its neighbour's answer."""
    disp._CORE_STACK_VERDICT = None
    yield
    disp._CORE_STACK_VERDICT = None


@pytest.fixture
def handler_file(tmp_path: Path) -> Path:
    f = tmp_path / "ok_handler.py"
    f.write_text("def handle(params, ctx=None):\n    return {'ok': True}\n", encoding="utf-8")
    return f


def _broken_module(tmp_path: Path, name: str, exc: str) -> None:
    """An installed module that raises on import — the macmini01 shape."""
    (tmp_path / f"{name}.py").write_text(f"raise {exc}\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))


# --------------------------------------------------------------------------
# the verdict itself
# --------------------------------------------------------------------------

def test_healthy_host_has_no_reason(monkeypatch) -> None:
    monkeypatch.setenv("FW_CORE_IMPORTS", "json,os")   # always importable
    assert disp.core_stack_unusable() is None


def test_absent_module_is_NOT_a_reason(monkeypatch) -> None:
    """The critical case: no numpy installed is a legitimate deployment."""
    monkeypatch.setenv("FW_CORE_IMPORTS", "definitely_not_installed_xyz_9f2a")
    assert disp.core_stack_unusable() is None, (
        "an ABSENT core module must not silence a host — handlers that need it "
        "fail their own per-registration import check"
    )


def test_installed_but_unimportable_IS_a_reason(monkeypatch, tmp_path) -> None:
    _broken_module(tmp_path, "brokencore_a", "ImportError('simulated CPU baseline mismatch')")
    monkeypatch.setenv("FW_CORE_IMPORTS", "brokencore_a")
    reason = disp.core_stack_unusable()
    assert reason and "brokencore_a" in reason and "ImportError" in reason, reason


def test_verdict_does_not_depend_on_the_exception_type(monkeypatch, tmp_path) -> None:
    """numpy reports a CPU-baseline mismatch as ImportError in some versions and
    RuntimeError in others; the verdict must not hinge on which."""
    _broken_module(tmp_path, "brokencore_b", "RuntimeError('cpu baseline')")
    monkeypatch.setenv("FW_CORE_IMPORTS", "brokencore_b")
    reason = disp.core_stack_unusable()
    assert reason and "RuntimeError" in reason, reason


def test_sentinel_set_is_configurable(monkeypatch, tmp_path) -> None:
    _broken_module(tmp_path, "brokencore_c", "ImportError('x')")
    monkeypatch.setenv("FW_CORE_IMPORTS", "json")        # does not include the broken one
    assert disp.core_stack_unusable() is None


# --------------------------------------------------------------------------
# what it does to advertisement
# --------------------------------------------------------------------------

def _store_with(handler_file: Path) -> MemoryStore:
    store = MemoryStore()
    for name in ("osm.cache.Download", "census.acs.Fetch"):
        store.save_handler_registration(
            HandlerRegistration(
                facet_name=name, module_uri=f"file://{handler_file}", entrypoint="handle"
            )
        )
    # ambient: stdlib-only, genuinely runnable even on a broken-numpy host
    store.save_handler_registration(
        HandlerRegistration(
            facet_name="fw.file.ListFiles", module_uri=f"file://{handler_file}", entrypoint="handle"
        )
    )
    return store


def test_healthy_host_advertises_everything(monkeypatch, handler_file) -> None:
    monkeypatch.setenv("FW_CORE_IMPORTS", "json")
    d = RegistryDispatcher(persistence=_store_with(handler_file))
    assert d.preload(verify=True) == 3


def test_broken_core_keeps_only_the_ambient_facets(monkeypatch, tmp_path, handler_file) -> None:
    _broken_module(tmp_path, "brokencore_d", "ImportError('no SSE4.2')")
    monkeypatch.setenv("FW_CORE_IMPORTS", "brokencore_d")
    d = RegistryDispatcher(persistence=_store_with(handler_file))
    kept = d.preload(verify=True)
    facets = set(d.dispatchable_facets())
    assert facets == {"fw.file.ListFiles"}, facets
    assert kept == 1
    # and it must really decline the domain work, not merely omit it from a list
    assert d.can_dispatch("osm.cache.Download") is False
    assert d.can_dispatch("fw.file.ListFiles") is True


def test_admission_does_not_apply_without_verify(monkeypatch, tmp_path, handler_file) -> None:
    """preload() without verify is the legacy path used elsewhere; leave it alone."""
    _broken_module(tmp_path, "brokencore_e", "ImportError('x')")
    monkeypatch.setenv("FW_CORE_IMPORTS", "brokencore_e")
    d = RegistryDispatcher(persistence=_store_with(handler_file))
    assert d.preload() == 3


def test_the_refusal_is_logged_with_its_reason(monkeypatch, tmp_path, handler_file, caplog) -> None:
    """A host that silently stops advertising is indistinguishable from an idle
    one, which is the failure mode this whole item is about."""
    _broken_module(tmp_path, "brokencore_f", "ImportError('no POPCNT')")
    monkeypatch.setenv("FW_CORE_IMPORTS", "brokencore_f")
    d = RegistryDispatcher(persistence=_store_with(handler_file))
    with caplog.at_level("WARNING"):
        d.preload(verify=True)
    msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("NOT advertising domain handlers" in m for m in msgs), msgs
    assert any("brokencore_f" in m and "no POPCNT" in m for m in msgs), msgs
