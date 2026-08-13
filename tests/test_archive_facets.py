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

"""The built-in ``fw.archive`` facets.

The archives here are built for the test, including the hostile ones: a zip
whose member is an absolute path, one that climbs out with ``..``, and a tar
carrying a symlink.

What the stdlib actually does with those was measured, not assumed, because the
answer differs by format and by Python version. On 3.12, ``TarFile.extractall``
writes ``../x`` outside the destination and creates the symlink; ``ZipFile``
sanitises but silently flattens. Both behaviours are pinned below, so a future
Python that changes either is caught here rather than in a pipeline.
"""

from __future__ import annotations

import gzip
import io
import os
import tarfile
import zipfile

import pytest

from facetwork.handlers import archive_handlers as ah


def _call(facet: str, **params):
    return ah.handle({"_facet_name": f"fw.archive.{facet}", **params})


@pytest.fixture
def zip_archive(tmp_path):
    """A TIGER-shaped zip: several files, one of which the caller wants."""
    path = tmp_path / "tiger.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("tl_2024_us_county.shp", b"SHP-BYTES")
        zf.writestr("tl_2024_us_county.dbf", b"DBF-BYTES")
        zf.writestr("tl_2024_us_county.prj", b"PRJ")
        zf.writestr("readme.txt", "unpacked\n")
    return path


@pytest.fixture
def tar_archive(tmp_path):
    """A tar.gz wrapped in a version-named top directory, as releases ship."""
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, body in (
            ("compendium-v2/genes.tsv", b"gene\trole\nTP53\tLoF\n"),
            ("compendium-v2/README", b"docs\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    return path


# ---------------------------------------------------------------------------
# List — enumerate without unpacking
# ---------------------------------------------------------------------------


def test_list_names_every_member(zip_archive):
    out = _call("List", archive=str(zip_archive))
    assert out["count"] == 4
    assert "tl_2024_us_county.shp" in out["names"]


def test_list_filters_by_glob(zip_archive):
    out = _call("List", archive=str(zip_archive), pattern="*.shp")
    assert out["names"] == ["tl_2024_us_county.shp"]


def test_list_is_sorted(tar_archive):
    """An unsorted listing makes a fan-out over it irreproducible."""
    names = _call("List", archive=str(tar_archive))["names"]
    assert names == sorted(names)


def test_list_of_a_missing_archive_says_so(tmp_path):
    with pytest.raises(FileNotFoundError):
        _call("List", archive=str(tmp_path / "nope.zip"))


# ---------------------------------------------------------------------------
# ReadMember — the "one CSV inside, name moves each release" case
# ---------------------------------------------------------------------------


def test_read_member_by_exact_name(zip_archive):
    out = _call("ReadMember", archive=str(zip_archive), member="readme.txt")
    assert out["text"] == "unpacked\n"
    assert out["name"] == "readme.txt"


def test_read_member_by_glob_when_the_name_moves(tmp_path):
    """UcdpPrioConflict-v25.1.csv becomes -v25.2 next release. The glob is the
    stable reference; the literal name is not."""
    path = tmp_path / "ucdp.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("UcdpPrioConflict-v25.1.csv", "id,deaths\n1,4\n")
        zf.writestr("codebook.pdf", b"%PDF")
    out = _call("ReadMember", archive=str(path), pattern="*.csv")
    assert out["name"] == "UcdpPrioConflict-v25.1.csv"
    assert out["text"].startswith("id,deaths")


def test_read_member_names_what_is_actually_inside_when_it_misses(zip_archive):
    """A layout change should say what the archive holds, not raise a bare
    KeyError three frames down."""
    with pytest.raises(KeyError) as exc:
        _call("ReadMember", archive=str(zip_archive), pattern="*.csv")
    assert "tl_2024_us_county.shp" in str(exc.value)


def test_read_member_needs_a_selector_when_ambiguous(zip_archive):
    with pytest.raises(ValueError, match="pass `member` or `pattern`"):
        _call("ReadMember", archive=str(zip_archive))


def test_read_member_of_a_single_member_archive_needs_no_selector(tmp_path):
    path = tmp_path / "only.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("data.csv", "a,b\n")
    assert _call("ReadMember", archive=str(path))["name"] == "data.csv"


def test_read_member_reports_truncation_against_the_MEMBER_size(tmp_path):
    path = tmp_path / "big.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("big.csv", "x" * 5000)
    out = _call("ReadMember", archive=str(path), member="big.csv", max_bytes=100)
    assert out["truncated"] is True
    assert len(out["text"]) == 100
    assert out["size_bytes"] == 5000, "size must report the member, not the read"


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------


def test_extract_writes_every_member(zip_archive, tmp_path):
    dest = tmp_path / "out"
    out = _call("Extract", archive=str(zip_archive), dest=str(dest))
    assert out["count"] == 4 and out["format"] == "zip"
    assert (dest / "tl_2024_us_county.shp").read_bytes() == b"SHP-BYTES"


def test_extract_with_a_pattern_takes_only_what_was_wanted(zip_archive, tmp_path):
    """The census/county-atlas shape: an eight-file archive, one file used."""
    dest = tmp_path / "out"
    out = _call("Extract", archive=str(zip_archive), dest=str(dest), pattern="*.shp")
    assert out["count"] == 1
    assert os.listdir(dest) == ["tl_2024_us_county.shp"]


def test_extract_strips_leading_components(tar_archive, tmp_path):
    dest = tmp_path / "out"
    _call("Extract", archive=str(tar_archive), dest=str(dest), strip_components=1)
    assert (dest / "genes.tsv").exists()
    assert not (dest / "compendium-v2").exists()


def test_extract_can_keep_what_is_already_there(zip_archive, tmp_path):
    dest = tmp_path / "out"
    _call("Extract", archive=str(zip_archive), dest=str(dest))
    (dest / "readme.txt").write_text("edited\n")
    _call("Extract", archive=str(zip_archive), dest=str(dest), overwrite=False)
    assert (dest / "readme.txt").read_text() == "edited\n"


def test_extract_caps_are_enforced(zip_archive, tmp_path):
    with pytest.raises(ValueError, match="max_files"):
        _call("Extract", archive=str(zip_archive), dest=str(tmp_path / "o"), max_files=2)
    with pytest.raises(ValueError, match="max_bytes"):
        _call("Extract", archive=str(zip_archive), dest=str(tmp_path / "o2"), max_bytes=5)


# ---------------------------------------------------------------------------
# Escaping members — what extractall does not check
# ---------------------------------------------------------------------------


def test_a_traversing_zip_member_is_refused_rather_than_silently_flattened(tmp_path):
    """`ZipFile` does not let `../x` escape — it rewrites it to `x` inside the
    destination, with no report. That is not an escape, but it IS the archive's
    claimed layout quietly becoming a different one, which the next step reads
    as a missing file. Refusing says so."""
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("../escaped.txt", "pwned")
        zf.writestr("fine.txt", "ok")
    dest = tmp_path / "out"

    out = _call("Extract", archive=str(path), dest=str(dest))
    assert out["count"] == 1, "the traversing member was extracted"
    assert (dest / "fine.txt").exists()
    assert not (dest / "escaped.txt").exists(), "flattened in silently"
    assert not (tmp_path / "escaped.txt").exists()


def test_a_traversing_tar_member_really_would_escape(tmp_path):
    """Pins the stdlib behaviour the guard exists for, so a Python that changes
    it (3.14 makes filtering the default) is caught here.

    This is the case that is a genuine escape today, and the reason the check
    is not "zipfile already handles it".
    """
    path = tmp_path / "evil.tar"
    with tarfile.open(path, "w") as tf:
        body = b"pwned"
        info = tarfile.TarInfo("../escaped.txt")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))

    control = tmp_path / "control"
    (control / "dest").mkdir(parents=True)  # must exist: extractall mkdirs `dest/..`
    with tarfile.open(path) as tf:
        tf.extractall(control / "dest")  # noqa: S202 — demonstrating the hazard
    assert (control / "escaped.txt").exists(), "stdlib no longer escapes; revisit the guard"

    out = _call("Extract", archive=str(path), dest=str(tmp_path / "out"))
    assert out["count"] == 0
    assert not (tmp_path / "escaped.txt").exists()


def test_an_absolute_member_path_is_refused(tmp_path):
    path = tmp_path / "abs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("/etc/tricked.txt", "pwned")
        zf.writestr("fine.txt", "ok")
    out = _call("Extract", archive=str(path), dest=str(tmp_path / "out"))
    assert out["paths"] == [str(tmp_path / "out" / "fine.txt")]


def test_a_tar_symlink_member_is_refused(tmp_path):
    """A link is the other way out of `dest`, and on 3.12 `extractall` creates
    it — verified: the member below lands as a symlink to /etc/passwd. A data
    archive has no business containing one."""
    path = tmp_path / "link.tar"
    with tarfile.open(path, "w") as tf:
        body = b"real\n"
        info = tarfile.TarInfo("real.txt")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
        link = tarfile.TarInfo("sneaky")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)

    dest = tmp_path / "out"
    out = _call("Extract", archive=str(path), dest=str(dest))
    assert out["count"] == 1
    assert not (dest / "sneaky").exists()


# ---------------------------------------------------------------------------
# Format detection from content
# ---------------------------------------------------------------------------


def test_a_mislabelled_archive_still_works(tmp_path):
    """Publishers mislabel. Extension-based detection turns that into a
    per-source bug; sniffing the magic bytes does not."""
    real_zip = tmp_path / "actually_a_zip.gz"
    with zipfile.ZipFile(real_zip, "w") as zf:
        zf.writestr("a.txt", "hello")
    out = _call("Extract", archive=str(real_zip), dest=str(tmp_path / "out"))
    assert out["format"] == "zip" and out["count"] == 1


def test_plain_gzip_is_one_unnamed_member(tmp_path):
    """NOAA's .csv.gz: one file, no member list. The member takes the archive's
    name minus .gz, which is what every hand-rolled version assumes."""
    path = tmp_path / "station.csv.gz"
    path.write_bytes(gzip.compress(b"date,tmax\n2026-01-01,12\n"))

    listed = _call("List", archive=str(path))
    assert listed["names"] == ["station.csv"]

    out = _call("Extract", archive=str(path), dest=str(tmp_path / "out"))
    assert out["format"] == "gzip"
    assert (tmp_path / "out" / "station.csv").read_bytes().startswith(b"date,tmax")

    assert _call("ReadMember", archive=str(path))["text"].startswith("date,tmax")


def test_tar_gz_is_detected_as_a_tar_not_a_gzip(tar_archive):
    """A .tar.gz is both; the caller cares that it has members."""
    assert _call("List", archive=str(tar_archive))["count"] == 2


def test_a_non_archive_is_a_clear_error(tmp_path):
    path = tmp_path / "plain.txt"
    path.write_text("just text, no magic bytes\n")
    with pytest.raises(ValueError, match="unrecognised archive format"):
        _call("List", archive=str(path))


def test_unknown_facet_is_a_clear_error():
    with pytest.raises(KeyError):
        ah.handle({"_facet_name": "fw.archive.Nope"})
