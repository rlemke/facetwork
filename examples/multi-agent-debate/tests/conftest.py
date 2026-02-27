"""Test-level conftest for multi-agent-debate — handler path setup."""

import os
import sys

import pytest

_EXAMPLE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _ensure_debate_handlers():
    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            mod = sys.modules[key]
            mod_file = getattr(mod, "__file__", "") or ""
            if "multi-agent-debate" not in mod_file:
                del sys.modules[key]
    if _EXAMPLE_ROOT in sys.path:
        sys.path.remove(_EXAMPLE_ROOT)
    sys.path.insert(0, _EXAMPLE_ROOT)


_ensure_debate_handlers()


@pytest.fixture(autouse=True)
def _debate_handlers_on_path():
    """Ensure handlers are on sys.path before each test."""
    _ensure_debate_handlers()
