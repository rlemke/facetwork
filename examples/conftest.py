"""Root conftest for the in-repo examples.

Every example ships a top-level ``handlers/`` package, so they all collide on the
module name ``handlers`` in ``sys.modules`` once the suite runs more than one of
them. Whichever example is imported first owns the name; another example's
``from handlers.shared.X import ...`` then fails with ``ModuleNotFoundError`` (or
silently imports the wrong example's code).

This autouse fixture rebinds ``handlers`` to the example that owns the
*currently running* test, **before each test** — so both import-time and the
lazy ``from handlers... import`` calls inside test bodies resolve to the right
example regardless of collection/run order. Examples without a ``handlers/``
package (or tests outside an example dir) are left untouched.
"""

from __future__ import annotations

import os
import sys

import pytest

_EXAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))


def _example_root_for(path: str) -> str | None:
    """Return ``examples/<name>/`` for a test file path, else ``None``."""
    parent = os.path.dirname(os.path.abspath(path))
    while parent and parent != "/":
        if os.path.dirname(parent) == _EXAMPLES_DIR:
            return parent
        if parent == _EXAMPLES_DIR:
            return None
        parent = os.path.dirname(parent)
    return None


def _bind_handlers_to(example_root: str) -> None:
    # Drop any handlers/* module not belonging to this example.
    for key in list(sys.modules):
        if key == "handlers" or key.startswith("handlers."):
            mod = sys.modules[key]
            mod_file = getattr(mod, "__file__", "") or ""
            if not mod_file.startswith(example_root + os.sep):
                del sys.modules[key]
    # Make this example's dir the first place `import handlers` looks.
    if example_root in sys.path:
        sys.path.remove(example_root)
    sys.path.insert(0, example_root)


@pytest.fixture(autouse=True)
def _isolate_example_handlers(request):
    root = _example_root_for(str(request.path))
    if root and os.path.isdir(os.path.join(root, "handlers")):
        _bind_handlers_to(root)
    yield
