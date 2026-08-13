# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The built-in ``fw.file`` facets.

These ship with the framework so a workflow can do file work without anyone
writing a handler. They go through the storage abstraction rather than
``pathlib``, so the same workflow runs against a local path, ``s3://`` or
``hdfs://`` — the distinction that matters on a fleet, where the data is in the
object store and not on the machine that launched the run.

The tests below exercise the local backend, and pin the behavioural choices that
are easy to get wrong later: sorted listings (so a fan-out is reproducible),
absence reported rather than raised (so callers can branch instead of catching),
and reads capped (so a step's return value cannot be an arbitrarily large file).
"""

from __future__ import annotations

import json
import os

import pytest

from facetwork.handlers import file_handlers as fh


def _call(facet: str, **params):
    return fh.handle({"_facet_name": f"fw.file.{facet}", **params})


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a.csv").write_text("x,y\n1,2\n")
    (tmp_path / "b.csv").write_text("x,y\n3,4\n")
    (tmp_path / "notes.txt").write_text("hello\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.csv").write_text("x,y\n5,6\n")
    return tmp_path


# ---------------------------------------------------------------------------
# List — the enumerate-then-fan-out primitive
# ---------------------------------------------------------------------------

def test_list_filters_by_glob(tree):
    out = _call("List", path=str(tree), pattern="*.csv")
    assert out["count"] == 2
    assert [os.path.basename(p) for p in out["paths"]] == ["a.csv", "b.csv"]


def test_list_is_sorted(tree):
    """An unsorted listing makes a fan-out over it irreproducible — iterations
    would be built in a different order each run, and correlating a failure
    across runs becomes guesswork."""
    paths = _call("List", path=str(tree))["paths"]
    assert paths == sorted(paths)


def test_list_recursive_descends(tree):
    flat = _call("List", path=str(tree), pattern="*.csv")["count"]
    deep = _call("List", path=str(tree), pattern="*.csv", recursive=True)["count"]
    assert flat == 2 and deep == 3


def test_list_excludes_directories_by_default(tree):
    names = [os.path.basename(p) for p in _call("List", path=str(tree))["paths"]]
    assert "sub" not in names
    with_dirs = _call("List", path=str(tree), files_only=False)
    assert "sub" in [os.path.basename(p) for p in with_dirs["paths"]]


def test_listing_a_missing_path_is_empty_not_an_error(tmp_path):
    """A fan-out over nothing is a legitimate outcome; the caller can assert on
    `count` if it expected otherwise. Raising here would force a `catch` around
    every discovery step."""
    out = _call("List", path=str(tmp_path / "nope"))
    assert out == {"paths": [], "count": 0}


def test_filter_narrows_without_a_second_listing(tree):
    listed = _call("List", path=str(tree))["paths"]
    out = _call("Filter", paths=listed, pattern="*.txt")
    assert out["count"] == 1
    assert os.path.basename(out["paths"][0]) == "notes.txt"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_exists(tree):
    assert _call("Exists", path=str(tree / "a.csv"))["exists"] is True
    assert _call("Exists", path=str(tree / "gone"))["exists"] is False


def test_stat_reports_absence_rather_than_raising(tree):
    """So a workflow can branch with `when` instead of wrapping in `catch`."""
    out = _call("Stat", path=str(tree / "gone"))
    assert out["exists"] is False and out["size_bytes"] == 0


def test_stat_of_a_file(tree):
    out = _call("Stat", path=str(tree / "a.csv"))
    assert out["exists"] and out["is_dir"] is False and out["size_bytes"] > 0
    assert out["modified_at"] > 0


def test_stat_of_a_directory_reports_no_size(tree):
    """A directory has no size that means the same thing across backends — an
    S3 prefix has none at all — so 0 beats a backend-specific number."""
    out = _call("Stat", path=str(tree / "sub"))
    assert out["is_dir"] is True and out["size_bytes"] == 0


def test_hash_is_content_addressed(tree):
    a = _call("Hash", path=str(tree / "a.csv"))
    again = _call("Hash", path=str(tree / "a.csv"))
    b = _call("Hash", path=str(tree / "b.csv"))
    assert a["digest"] == again["digest"], "not deterministic"
    assert a["digest"] != b["digest"], "different content hashed the same"
    assert a["algorithm"] == "sha256" and a["size_bytes"] > 0


def test_hash_rejects_an_unknown_algorithm(tree):
    with pytest.raises(ValueError):
        _call("Hash", path=str(tree / "a.csv"), algorithm="not-a-hash")


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def test_read_and_write_text(tmp_path):
    target = str(tmp_path / "deep" / "nested" / "out.txt")
    written = _call("WriteText", path=target, text="line one\nline two\n")
    assert written["size_bytes"] > 0
    assert _call("ReadText", path=target)["text"] == "line one\nline two\n"


def test_read_is_capped_and_says_so(tmp_path):
    """A step's return value lives in the step store, which is not a blob store.
    Truncating silently would let a caller treat half a file as the whole one."""
    p = str(tmp_path / "big.txt")
    _call("WriteText", path=p, text="abcdefghij" * 100)
    out = _call("ReadText", path=p, max_bytes=25)
    assert out["truncated"] is True
    assert len(out["text"]) == 25
    assert out["size_bytes"] == 1000, "size must report the FILE, not the read"


def test_max_bytes_zero_means_no_cap(tmp_path):
    """An explicit 0 is the caller's decision and must not be rewritten into
    the default — the same trap as the foreach limit sentinel."""
    p = str(tmp_path / "big.txt")
    body = "z" * 50_000
    _call("WriteText", path=p, text=body)
    out = _call("ReadText", path=p, max_bytes=0)
    assert out["truncated"] is False and len(out["text"]) == 50_000


def test_json_round_trip(tmp_path):
    p = str(tmp_path / "d.json")
    _call("WriteJson", path=p, data={"b": 2, "a": [1, 2, 3]})
    back = _call("ReadJson", path=p)["data"]
    assert back == {"a": [1, 2, 3], "b": 2}


def test_write_json_accepts_a_json_string(tmp_path):
    """Json-typed step values arrive as strings, so the handler must take both
    rather than double-encoding one of them."""
    p = str(tmp_path / "d.json")
    _call("WriteJson", path=p, data='{"a": 1}')
    assert _call("ReadJson", path=p)["data"] == {"a": 1}


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------

def test_copy_preserves_content(tree, tmp_path):
    dest = str(tmp_path / "copied" / "a.csv")
    out = _call("Copy", source=str(tree / "a.csv"), dest=dest)
    assert out["size_bytes"] > 0
    assert _call("ReadText", path=dest)["text"] == "x,y\n1,2\n"


def test_delete_of_an_absent_path_is_not_an_error(tmp_path):
    """Removing something already gone IS the caller's desired outcome."""
    assert _call("Delete", path=str(tmp_path / "gone"))["deleted"] is False


def test_deleting_a_directory_needs_recursive(tree):
    """Refusing by default: a workflow that meant to delete one file should not
    take a tree with it because a path happened to be a directory."""
    with pytest.raises(IsADirectoryError):
        _call("Delete", path=str(tree / "sub"))
    assert _call("Delete", path=str(tree / "sub"), recursive=True)["deleted"] is True
    assert _call("Exists", path=str(tree / "sub"))["exists"] is False


def test_make_dir_is_idempotent(tmp_path):
    p = str(tmp_path / "x" / "y")
    _call("MakeDir", path=p)
    _call("MakeDir", path=p)
    assert _call("Stat", path=p)["is_dir"] is True


def test_unknown_facet_is_a_clear_error():
    with pytest.raises(KeyError):
        fh.handle({"_facet_name": "fw.file.Nope"})


# ---------------------------------------------------------------------------
# The FFL and the handlers must not drift
# ---------------------------------------------------------------------------

def test_every_declared_facet_has_a_handler():
    """The failure this prevents: a facet that compiles, dispatches, and then
    dies on a runner with "no handler for facet", hours from the change."""
    from facetwork.handlers import builtin_facets, declared_facets

    declared, handled = declared_facets(), set(builtin_facets())
    assert declared, "no facets parsed from the shipped FFL"
    assert declared == handled, (
        f"declared without a handler: {sorted(declared - handled)}; "
        f"handled but not declared: {sorted(handled - declared)}"
    )


def test_registration_is_idempotent():
    from facetwork.handlers import register_builtin_handlers
    from facetwork.runtime.memory_store import MemoryStore

    store = MemoryStore()
    first = register_builtin_handlers(store)
    register_builtin_handlers(store)
    names = {r.facet_name for r in store.list_handler_registrations()}
    assert len(names) == first, "re-registering duplicated entries"
