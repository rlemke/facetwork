"""Test conftest for noaa-weather example.

Ensures handler imports resolve correctly when running as part of the full
test suite alongside other examples that share the 'handlers' package name.
"""

from __future__ import annotations

import os
import sys

import pytest

_EXAMPLE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_weather_handlers():
    """Purge non-weather handler modules from sys.modules."""
    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            mod = sys.modules[key]
            mod_file = getattr(mod, "__file__", "") or ""
            if "noaa-weather" not in mod_file:
                del sys.modules[key]
    if _EXAMPLE_ROOT in sys.path:
        sys.path.remove(_EXAMPLE_ROOT)
    sys.path.insert(0, _EXAMPLE_ROOT)


@pytest.fixture(autouse=True)
def _weather_handler_isolation():
    """Ensure weather handlers are available for every test."""
    _ensure_weather_handlers()
