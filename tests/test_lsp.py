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

"""The FFL language server.

Two layers are tested separately because they fail differently. The analysis
functions can be wrong about FFL; the JSON-RPC layer can be wrong about the
protocol, and a protocol bug looks to the user like "the extension does
nothing" — no error, no diagnostics, no clue. The framing is hand-rolled (no
pygls, to keep lark the compiler's only runtime dependency), so it is exercised
end to end over real byte streams rather than by calling handlers directly.
"""

from __future__ import annotations

import io
import json

import pytest

from facetwork.lsp import LanguageServer, diagnostics_for, facet_completions, hover_for

GOOD = """
namespace demo {
    event facet Fetch(code: String) => (path: String, count: Int)
    workflow W(code: String) => (n: Int) andThen {
        fetched = Fetch(code = $.code)
        yield W(n = fetched.count)
    }
}
"""

BAD = """
namespace demo {
    event facet Fetch(code: String) => (path: String, count: Int)
    workflow W(code: String) => (n: Int) andThen {
        fetched = Fetch(code = $.code)
        yield W(n = fetchd.count)
    }
}
"""


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def test_valid_source_reports_nothing():
    assert diagnostics_for(GOOD) == []


def test_a_bad_reference_is_reported_with_its_rule_id_and_docs():
    diags = diagnostics_for(BAD)
    assert len(diags) == 1
    d = diags[0]
    assert d["code"] == "REF_UNDEFINED_STEP"
    assert d["severity"] == 1
    assert d["codeDescription"]["href"].endswith("REF_UNDEFINED_STEP")
    assert "did you mean 'fetched'?" in d["message"]


def test_positions_are_zero_based():
    """LSP counts from 0, the compiler from 1.

    Getting this wrong puts every squiggle one line off, which reads as a broken
    extension rather than as an off-by-one.
    """
    d = diagnostics_for(BAD)[0]
    # 'fetchd' is on source line 6, so LSP line 5.
    assert d["range"]["start"]["line"] == 5
    assert d["range"]["start"]["character"] >= 0


def test_a_syntax_error_is_a_diagnostic_not_a_crash():
    """Half-typed source is the normal state of a file being edited."""
    diags = diagnostics_for("namespace demo { workflow W( }")
    assert diags and diags[0]["code"] == "PARSE_ERROR"


def test_unparseable_source_still_offers_no_completions_rather_than_raising():
    assert facet_completions("namespace demo { workflow W( }") == []


def test_completions_carry_the_signature():
    labels = {c["label"]: c["detail"] for c in facet_completions(GOOD)}
    assert labels["Fetch"] == "(code: String) => (path: String, count: Int)"
    assert "W" in labels


def test_hover_on_a_facet_shows_its_signature():
    # line 2 (0-based), column 16 — the `Fetch` in its declaration.
    text = hover_for(GOOD, 2, 16)
    assert text and "Fetch(code: String)" in text


def test_hover_works_at_the_call_site_too():
    """Where it is actually wanted: reading someone else's workflow and asking
    "what does this facet take?" without navigating to the declaration."""
    text = hover_for(GOOD, 4, 20)
    assert text and "event facet" in text


def test_hover_on_a_parameter_name_says_nothing():
    """Silence beats guessing. A parameter is not a facet, and returning the
    enclosing facet's card here would be confidently wrong."""
    assert hover_for(GOOD, 2, 22) is None


def test_hover_on_a_bad_line_prefers_the_rule_doc():
    """When something is wrong here, the rule doc is what the author needs —
    not the signature of whatever token is under the cursor."""
    text = hover_for(BAD, 5, 22)
    assert text and "REF_UNDEFINED_STEP" in text


# ---------------------------------------------------------------------------
# The protocol, over real byte streams
# ---------------------------------------------------------------------------

def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def _run(messages: list[dict]) -> list[dict]:
    """Feed framed messages to a server and decode everything it writes back."""
    stdin = io.BytesIO(b"".join(_frame(m) for m in messages))
    stdout = io.BytesIO()
    server = LanguageServer(stdin, stdout)
    try:
        server.serve()
    except SystemExit:
        pass

    out, raw = [], stdout.getvalue()
    while b"Content-Length:" in raw:
        header, _, rest = raw.partition(b"\r\n\r\n")
        length = int(header.split(b"Content-Length:")[1].split(b"\r\n")[0])
        out.append(json.loads(rest[:length]))
        raw = rest[length:]
    return out


def test_initialize_advertises_the_capabilities_it_implements():
    replies = _run([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}])
    caps = replies[0]["result"]["capabilities"]
    assert caps["textDocumentSync"] == 1
    assert caps["hoverProvider"] is True
    assert "completionProvider" in caps


def test_opening_a_document_publishes_diagnostics():
    replies = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": "file:///a.ffl", "text": BAD}}},
    ])
    published = [r for r in replies if r.get("method") == "textDocument/publishDiagnostics"]
    assert published, "no diagnostics were published on open"
    assert published[0]["params"]["diagnostics"][0]["code"] == "REF_UNDEFINED_STEP"


def test_editing_republishes_against_the_new_text():
    replies = _run([
        {"jsonrpc": "2.0", "method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": "file:///a.ffl", "text": BAD}}},
        {"jsonrpc": "2.0", "method": "textDocument/didChange",
         "params": {"textDocument": {"uri": "file:///a.ffl"},
                    "contentChanges": [{"text": GOOD}]}},
    ])
    published = [r for r in replies if r.get("method") == "textDocument/publishDiagnostics"]
    assert len(published) == 2
    assert published[0]["params"]["diagnostics"], "the broken version reported nothing"
    assert published[1]["params"]["diagnostics"] == [], "the fix did not clear the squiggle"


def test_closing_clears_the_diagnostics():
    """A closed file's problems must not linger in the panel as if still live."""
    replies = _run([
        {"jsonrpc": "2.0", "method": "textDocument/didOpen",
         "params": {"textDocument": {"uri": "file:///a.ffl", "text": BAD}}},
        {"jsonrpc": "2.0", "method": "textDocument/didClose",
         "params": {"textDocument": {"uri": "file:///a.ffl"}}},
    ])
    published = [r for r in replies if r.get("method") == "textDocument/publishDiagnostics"]
    assert published[-1]["params"]["diagnostics"] == []


def test_an_unknown_request_is_still_answered():
    """Leaving a request outstanding hangs some clients, which the user sees as
    a crashed server rather than an unimplemented method."""
    replies = _run([{"jsonrpc": "2.0", "id": 7, "method": "textDocument/formatting"}])
    assert replies and replies[0]["id"] == 7


def test_a_malformed_message_does_not_end_the_session():
    """One bad message must not take the server down mid-edit."""
    replies = _run([
        {"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}},
    ])
    assert any(r.get("id") == 2 for r in replies), "the server stopped after a bad message"


def test_exit_after_shutdown_is_a_clean_stop():
    with pytest.raises(SystemExit) as caught:
        stdin = io.BytesIO(
            _frame({"jsonrpc": "2.0", "id": 1, "method": "shutdown"})
            + _frame({"jsonrpc": "2.0", "method": "exit"})
        )
        LanguageServer(stdin, io.BytesIO()).serve()
    assert caught.value.code == 0
