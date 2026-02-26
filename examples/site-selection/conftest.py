"""Root conftest for site-selection example.

Ensures that the site-selection handlers package is active when tests in this
example tree are collected or run.  Other examples (osm-geocoder, census-us,
etc.) may load their own ``handlers`` package first; this conftest purges those
stale entries from ``sys.modules`` so that subsequent imports resolve to the
site-selection handlers.
"""

import os
import sys

import _pytest.python

_EXAMPLE_ROOT = os.path.dirname(os.path.abspath(__file__))


def _ensure_sitesel_handlers():
    """Purge stale handlers and ensure site-selection handlers are active."""
    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            mod = sys.modules[key]
            mod_file = getattr(mod, "__file__", "") or ""
            if "site-selection" not in mod_file:
                del sys.modules[key]
    if _EXAMPLE_ROOT in sys.path:
        sys.path.remove(_EXAMPLE_ROOT)
    sys.path.insert(0, _EXAMPLE_ROOT)


# Purge at conftest load time.
_ensure_sitesel_handlers()

# ---------------------------------------------------------------------------
# Monkey-patch importtestmodule to purge stale handlers right before each
# site-selection module is imported by pytest.
# ---------------------------------------------------------------------------
_original_importtestmodule = _pytest.python.importtestmodule


def _patched_importtestmodule(path, config):
    if "site-selection" in str(path):
        _ensure_sitesel_handlers()
    return _original_importtestmodule(path, config)


_pytest.python.importtestmodule = _patched_importtestmodule
