"""The router entrypoint's graph sync must never mix files from two builds.

Measured 2026-10-15: a rebuilt graph's location_index had the SAME SIZE as the
old one (GraphHopper preallocates), the size-only check kept the stale file, and
every replica crash-looped "location index was opened with incorrect graph".
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "docker" / "graphhopper-server.sh"


def _sync_code() -> str:
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", SCRIPT.read_text(), re.S)
    assert m, "embedded sync not found"
    return m.group(1)


FAKE_BOTO = textwrap.dedent('''
    import json, os, shutil
    STORE = os.environ["FAKE_STORE"]
    def _objs():
        return json.load(open(os.path.join(STORE, "index.json")))
    class _Pager:
        def paginate(self, Bucket, Prefix):
            yield {"Contents": [o for o in _objs() if o["Key"].startswith(Prefix)]}
    class _Client:
        def get_paginator(self, name): return _Pager()
        def download_file(self, bucket, key, path):
            shutil.copy(os.path.join(STORE, key.replace("/", "_")), path)
    def client(*a, **k): return _Client()
''')


def _store(tmp: Path, build: str, files: dict) -> Path:
    import json

    store = tmp / "store"
    store.mkdir(exist_ok=True)
    objs = []
    for name, body in files.items():
        key = f"p/{name}"
        (store / key.replace("/", "_")).write_text(body)
        objs.append({"Key": key, "Size": len(body), "ETag": f"{build}-{name}"})
    (store / "index.json").write_text(json.dumps(objs))
    return store


def _run(tmp: Path, store: Path, dest: Path) -> str:
    mods = tmp / "mods"
    (mods / "boto3").mkdir(parents=True, exist_ok=True)
    (mods / "boto3" / "__init__.py").write_text(FAKE_BOTO)
    (mods / "botocore").mkdir(exist_ok=True)
    (mods / "botocore" / "__init__.py").write_text("")
    (mods / "botocore" / "config.py").write_text("class Config:\n    def __init__(self, **k): pass\n")
    env = {**os.environ, "PYTHONPATH": str(mods), "FAKE_STORE": str(store)}
    r = subprocess.run([sys.executable, "-c", _sync_code(), "b", "p/", str(dest)],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_a_rebuilt_graph_replaces_same_size_files_and_local_ch(tmp_path):
    dest = tmp_path / "graph"
    dest.mkdir()
    _run(tmp_path, _store(tmp_path, "old", {"nodes": "N1", "properties": "P1",
                                            "location_index": "OLD"}), dest)
    (dest / "ch_car").mkdir()  # prepared on load, never uploaded
    out = _run(tmp_path, _store(tmp_path, "new", {"nodes": "N2", "properties": "P2",
                                                  "location_index": "NEW"}), dest)
    assert (dest / "location_index").read_text() == "NEW"
    assert not (dest / "ch_car").exists()
    assert "3 fetched" in out


def test_the_same_build_is_reused(tmp_path):
    dest = tmp_path / "graph"
    dest.mkdir()
    store = _store(tmp_path, "b1", {"nodes": "N", "properties": "P"})
    _run(tmp_path, store, dest)
    (dest / "ch_car").mkdir()
    out = _run(tmp_path, store, dest)
    assert "0 fetched, 2 already local" in out
    assert (dest / "ch_car").exists()
