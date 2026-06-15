"""Handler-side re-export of the s2 tool library.

The real implementation lives in ``tools/_s2_tools/`` (inside this example). It
is shared verbatim by the CLI tools (``tools/``) and the FFL handlers — both
read/write the same cache under ``$AFL_CACHE_ROOT/s2/``.

``parents[2]`` is the example root (``sentinel2-landchange/``); ``/ "tools"``
reaches the bundled tool library so ``from _s2_tools import ...`` resolves
whether a CLI runs standalone or a handler runs in the runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_ROOT = Path(__file__).resolve().parents[2] / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from _s2_tools import map_render, raster, sidecar, stac, storage  # noqa: E402,F401

__all__ = ["map_render", "raster", "sidecar", "stac", "storage"]
