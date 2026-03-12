"""Streaming GeoJSON FeatureCollection writer.

Writes features one at a time to disk instead of accumulating in memory.
Produces valid GeoJSON output identical to ``json.dump({"type":
"FeatureCollection", "features": [...]}, f)``.
"""

from __future__ import annotations

import json
from typing import IO

from ._output import ensure_dir, open_output


class GeoJSONStreamWriter:
    """Write GeoJSON features to a file incrementally.

    Usage::

        with GeoJSONStreamWriter("/tmp/roads.geojson") as w:
            w.write_feature({"type": "Feature", ...})
        print(w.feature_count)
    """

    def __init__(self, path: str) -> None:
        ensure_dir(path)
        self._f: IO = open_output(path)
        self._f.write('{"type": "FeatureCollection", "features": [')
        self._count = 0
        self._closed = False
        self.path = path

    @property
    def feature_count(self) -> int:
        return self._count

    def write_feature(self, feature: dict) -> None:
        """Serialize and write a single GeoJSON Feature."""
        if self._closed:
            raise RuntimeError("Writer is closed")
        if self._count > 0:
            self._f.write(", ")
        self._f.write(json.dumps(feature, ensure_ascii=False))
        self._count += 1

    def close(self) -> None:
        """Write closing brackets and close the file handle."""
        if self._closed:
            return
        self._closed = True
        self._f.write("]}")
        self._f.close()

    def __enter__(self) -> GeoJSONStreamWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
