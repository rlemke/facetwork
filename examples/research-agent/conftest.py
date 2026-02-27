"""Root conftest for research-agent — cross-example module isolation."""

import os
import sys

import _pytest.python

_EXAMPLE_ROOT = os.path.dirname(os.path.abspath(__file__))


def _ensure_research_handlers():
    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            mod = sys.modules[key]
            mod_file = getattr(mod, "__file__", "") or ""
            if "research-agent" not in mod_file:
                del sys.modules[key]
    if _EXAMPLE_ROOT in sys.path:
        sys.path.remove(_EXAMPLE_ROOT)
    sys.path.insert(0, _EXAMPLE_ROOT)


_ensure_research_handlers()

_original_importtestmodule = _pytest.python.importtestmodule


def _patched_importtestmodule(path, config):
    if "research-agent" in str(path):
        _ensure_research_handlers()
    return _original_importtestmodule(path, config)


_pytest.python.importtestmodule = _patched_importtestmodule
