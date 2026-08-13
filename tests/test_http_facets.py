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

"""The built-in ``fw.http`` facets.

Tested against a REAL HTTP server on localhost rather than a mocked
``urlopen``. The whole value of this facet is the conditional-GET conversation —
sending ``If-None-Match``, reading a 304, deciding on that basis — and a mock
would be asserting that the code sends what the mock was written to expect. The
server here counts request bodies served, so "the network was avoided" is
observed rather than inferred.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from facetwork.handlers import http_handlers as hh


class _State:
    """What the fake origin is currently publishing."""

    def __init__(self) -> None:
        self.body = b"version one\n"
        self.etag = '"v1"'
        self.last_modified = "Wed, 21 Oct 2026 07:28:00 GMT"
        self.bodies_served = 0  # full 200-with-body responses
        self.conditional_hits = 0  # requests that carried a validator
        self.checksum_available = True

    def publish(self, body: bytes, etag: str) -> None:
        self.body, self.etag = body, etag

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


@pytest.fixture
def origin():
    state = _State()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep pytest output readable
            pass

        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's contract
            if self.path == "/checksum":
                if not state.checksum_available:
                    self.send_error(503)
                    return
                payload = f"{state.sha}  data\n".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if self.path == "/json":
                payload = json.dumps({"items": [1, 2, 3]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            inm = self.headers.get("If-None-Match")
            if inm or self.headers.get("If-Modified-Since"):
                state.conditional_hits += 1
            if inm == state.etag:
                self.send_response(304)
                self.send_header("ETag", state.etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            state.bodies_served += 1
            self.send_response(200)
            self.send_header("ETag", state.etag)
            self.send_header("Last-Modified", state.last_modified)
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}/data"
    state.checksum_url = f"http://127.0.0.1:{server.server_address[1]}/checksum"
    state.json_url = f"http://127.0.0.1:{server.server_address[1]}/json"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _fetch(origin, dest, **params):
    return hh.handle(
        {"_facet_name": "fw.http.Fetch", "url": origin.url, "dest": str(dest), **params}
    )


# ---------------------------------------------------------------------------
# First fetch
# ---------------------------------------------------------------------------


def test_first_fetch_downloads_and_records_provenance(origin, tmp_path):
    dest = tmp_path / "sub" / "data.bin"  # parent does not exist yet
    out = _fetch(origin, dest)

    assert out["was_cached"] is False
    assert out["sha256"] == origin.sha
    assert dest.read_bytes() == origin.body
    assert origin.bodies_served == 1

    side = json.loads((tmp_path / "sub" / "data.bin.meta.json").read_text())
    assert side["url"] == origin.url
    assert side["sha256"] == origin.sha
    assert side["etag"] == origin.etag
    assert side["size_bytes"] == len(origin.body)
    assert side["fetched_at"]


def test_no_partial_file_survives_a_failed_download(tmp_path):
    """The failure this prevents: an interrupted download left at the final path,
    which the next run reuses as if it were complete.

    A mid-transfer connection drop cannot be provoked from the server side
    reliably, so the response object is the one thing faked here.
    """

    class Dropped:
        headers: dict[str, str] = {}
        status = 200
        _calls = 0

        def read(self, _n=None):
            Dropped._calls += 1
            if Dropped._calls == 1:
                return b"first chunk"
            raise OSError("connection reset by peer")

    dest = tmp_path / "data.bin"
    with pytest.raises(OSError):
        hh._store(str(dest), Dropped(), "http://example.invalid/data", None)

    assert not dest.exists(), "a partial download was left at the final path"
    assert not (tmp_path / "data.bin.part").exists(), "staging file not cleaned up"


# ---------------------------------------------------------------------------
# Reuse — the decision this facet exists to get right
# ---------------------------------------------------------------------------


def test_unchanged_upstream_revalidates_instead_of_redownloading(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest)
    out = _fetch(origin, dest)

    assert out["was_cached"] is True
    assert out["revalidated"] is True, "reused without asking upstream"
    assert out["status"] == 304
    assert origin.conditional_hits == 1
    assert origin.bodies_served == 1, "re-downloaded a body that had not changed"


def test_changed_upstream_is_refetched(origin, tmp_path):
    """The moving-target failure: a source that publishes under a stable URL
    (`…-latest.osm.pbf`) is frozen forever by an existence-only cache check."""
    dest = tmp_path / "data.bin"
    first = _fetch(origin, dest)

    origin.publish(b"version two, longer\n", '"v2"')
    second = _fetch(origin, dest)

    assert second["was_cached"] is False
    assert second["sha256"] != first["sha256"]
    assert dest.read_bytes() == b"version two, longer\n"
    assert origin.bodies_served == 2


def test_max_age_reuses_without_touching_the_network(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, max_age_hours=24)
    out = _fetch(origin, dest, max_age_hours=24)

    assert out["was_cached"] is True
    assert out["revalidated"] is False, "within max_age must not cost a round trip"
    assert origin.conditional_hits == 0


def test_expired_max_age_falls_through_to_revalidation(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, max_age_hours=24)

    side_path = tmp_path / "data.bin.meta.json"
    side = json.loads(side_path.read_text())
    side["fetched_at"] = "2020-01-01T00:00:00+00:00"
    side_path.write_text(json.dumps(side))

    out = _fetch(origin, dest, max_age_hours=24)
    assert out["revalidated"] is True and origin.bodies_served == 1


def test_force_ignores_a_valid_cache(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, max_age_hours=24)
    out = _fetch(origin, dest, max_age_hours=24, force=True)
    assert out["was_cached"] is False and origin.bodies_served == 2


def test_a_truncated_cache_is_never_served_as_complete(origin, tmp_path):
    """Size is checked against the sidecar before reuse. Existence alone would
    hand a half-written file downstream as if it were the whole artifact."""
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, max_age_hours=24)
    dest.write_bytes(origin.body[:3])

    out = _fetch(origin, dest, max_age_hours=24)
    assert out["was_cached"] is False
    assert dest.read_bytes() == origin.body


def test_same_dest_different_url_is_refetched(origin, tmp_path):
    """What is cached must be what was ASKED for, not merely something."""
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, max_age_hours=24)
    out = hh.handle({
        "_facet_name": "fw.http.Fetch",
        "url": origin.url + "?variant=b",
        "dest": str(dest),
        "max_age_hours": 24,
    })
    assert out["was_cached"] is False


def test_a_cache_from_another_fetcher_version_is_not_trusted(origin, tmp_path):
    """The ALGORITHM half of the cache key: if this module's rules changed, what
    the previous version decided to store cannot be assumed still valid."""
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, max_age_hours=24)
    side_path = tmp_path / "data.bin.meta.json"
    side = json.loads(side_path.read_text())
    side["tool_version"] = "0.9"
    side_path.write_text(json.dumps(side))

    assert _fetch(origin, dest, max_age_hours=24)["was_cached"] is False


def test_a_corrupt_sidecar_means_refetch_not_crash(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, max_age_hours=24)
    (tmp_path / "data.bin.meta.json").write_text("{ not json")
    assert _fetch(origin, dest, max_age_hours=24)["was_cached"] is False


# ---------------------------------------------------------------------------
# Published checksums (the Geofabrik pattern)
# ---------------------------------------------------------------------------


def test_published_checksum_unchanged_means_reuse(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, checksum_url=origin.checksum_url)
    out = _fetch(origin, dest, checksum_url=origin.checksum_url)

    assert out["was_cached"] and out["revalidated"]
    assert origin.bodies_served == 1


def test_published_checksum_changed_means_refetch(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, checksum_url=origin.checksum_url)
    origin.publish(b"version two\n", '"v2"')

    out = _fetch(origin, dest, checksum_url=origin.checksum_url)
    assert out["was_cached"] is False and origin.bodies_served == 2


def test_an_unreachable_checksum_falls_back_rather_than_redownloading(origin, tmp_path):
    """"Could not ask" is not "changed". Re-downloading a multi-GB PBF because a
    checksum endpoint blipped is the expensive wrong answer; the conditional GET
    still settles it correctly."""
    dest = tmp_path / "data.bin"
    _fetch(origin, dest, checksum_url=origin.checksum_url)
    origin.checksum_available = False

    out = _fetch(origin, dest, checksum_url=origin.checksum_url)
    assert out["was_cached"] is True and out["revalidated"] is True
    assert origin.bodies_served == 1


# ---------------------------------------------------------------------------
# Pinned digests
# ---------------------------------------------------------------------------


def test_expected_sha256_mismatch_fails_and_leaves_nothing_behind(origin, tmp_path):
    dest = tmp_path / "data.bin"
    with pytest.raises(ValueError, match="sha256 mismatch"):
        _fetch(origin, dest, expected_sha256="0" * 64)
    assert not dest.exists(), "wrong bytes left where the next run would reuse them"


def test_expected_sha256_match_is_accepted(origin, tmp_path):
    dest = tmp_path / "data.bin"
    out = _fetch(origin, dest, expected_sha256=origin.sha)
    assert out["sha256"] == origin.sha


# ---------------------------------------------------------------------------
# Get / GetJson / Provenance
# ---------------------------------------------------------------------------


def test_get_returns_the_body(origin):
    out = hh.handle({"_facet_name": "fw.http.Get", "url": origin.url})
    assert out["body"] == "version one\n" and out["status"] == 200
    assert out["truncated"] is False


def test_get_reports_truncation(origin):
    """A silently clipped body is treated by the caller as the whole response."""
    out = hh.handle({"_facet_name": "fw.http.Get", "url": origin.url, "max_bytes": 4})
    assert out["truncated"] is True and len(out["body"]) == 4


def test_get_json_returns_a_parsed_value_not_a_string(origin):
    """A re-serialised collection makes a downstream `foreach` iterate the
    CHARACTERS of the JSON text — which is exactly how fw.file.List produced 360
    tasks from 3 files."""
    out = hh.handle({"_facet_name": "fw.http.GetJson", "url": origin.json_url})
    assert out["data"] == {"items": [1, 2, 3]}
    assert isinstance(out["data"], dict)


def test_get_json_says_so_when_the_cap_cut_the_body(origin):
    with pytest.raises(ValueError, match="max_bytes"):
        hh.handle({"_facet_name": "fw.http.GetJson", "url": origin.json_url, "max_bytes": 5})


def test_provenance_reads_back_what_fetch_recorded(origin, tmp_path):
    dest = tmp_path / "data.bin"
    _fetch(origin, dest)
    out = hh.handle({"_facet_name": "fw.http.Provenance", "path": str(dest)})
    assert out["exists"] is True
    assert out["sha256"] == origin.sha and out["url"] == origin.url


def test_provenance_of_an_unfetched_path_reports_absence(tmp_path):
    """Reported, not raised, so a workflow can branch with `when` rather than
    wrapping every provenance read in a `catch`."""
    out = hh.handle({"_facet_name": "fw.http.Provenance", "path": str(tmp_path / "nope")})
    assert out["exists"] is False and out["sha256"] == ""


def test_unknown_facet_is_a_clear_error():
    with pytest.raises(KeyError):
        hh.handle({"_facet_name": "fw.http.Nope"})
