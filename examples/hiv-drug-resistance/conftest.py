"""Root conftest for hiv-drug-resistance example.

Ensures that handler imports resolve to this example's handlers, not
another example's handlers that may share the 'handlers' package name.
"""

from __future__ import annotations

import os
import sys

import _pytest.python

_EXAMPLE_ROOT = os.path.dirname(os.path.abspath(__file__))


def _ensure_resistance_handlers():
    """Purge non-resistance handler modules from sys.modules."""
    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            mod = sys.modules[key]
            mod_file = getattr(mod, "__file__", "") or ""
            if "hiv-drug-resistance" not in mod_file:
                del sys.modules[key]
    if _EXAMPLE_ROOT in sys.path:
        sys.path.remove(_EXAMPLE_ROOT)
    sys.path.insert(0, _EXAMPLE_ROOT)


_ensure_resistance_handlers()

_original_importtestmodule = _pytest.python.importtestmodule


def _patched_importtestmodule(path, config):
    if "hiv-drug-resistance" in str(path):
        _ensure_resistance_handlers()
    return _original_importtestmodule(path, config)


_pytest.python.importtestmodule = _patched_importtestmodule
