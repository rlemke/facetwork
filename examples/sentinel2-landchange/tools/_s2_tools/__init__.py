"""Shared library behind the sentinel2-landchange tools and FFL handlers.

One code path: every CLI in ``tools/`` and every handler in ``handlers/`` calls
these functions, so the terminal and the runtime produce identical cache
effects. Modules:

- ``storage``    — backend abstraction + cache-root derivation
- ``sidecar``    — per-entry ``.meta.json`` cache protocol
- ``stac``       — STAC search for Sentinel-2 scenes (real + mock)
- ``raster``     — COG window-read, spectral index, composite, change (real + mock)
- ``map_render`` — change raster -> web tiles + MapLibre HTML
- ``s2_mocks``   — deterministic offline fixtures (``use_mock=True``)

The cache is rooted at ``$AFL_CACHE_ROOT/s2/`` (namespace ``s2``).
"""

NAMESPACE = "s2"
