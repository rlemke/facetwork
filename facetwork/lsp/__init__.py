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

"""A Language Server for FFL.

The validator already produces everything an editor needs — a stable
``rule_id``, a ``docs_uri``, a message, a line and a column — but you only see
any of it by running a command. That gap is most of what makes FFL hard to
author: 63 rules, and you meet them one round-trip at a time. This surfaces the
same diagnostics as you type, adds the facet signatures as completions, and puts
the rule docs behind hover.

Run it::

    fw ffl lsp            # or: python -m facetwork.lsp

**Hand-rolled JSON-RPC, deliberately.** LSP framing is Content-Length headers
around JSON bodies, which is little enough code that taking a dependency
(``pygls``) to avoid it would cost more than it saves: the compiler's only
runtime dependency is lark, and an editor integration is a poor reason to
change that. The subset implemented here is the subset editors need:
diagnostics, completion, hover.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO

from facetwork import parse
from facetwork.parser import ParseError
from facetwork.validator import validate

logger = logging.getLogger("facetwork.lsp")

# LSP DiagnosticSeverity
_ERROR = 1
_WARNING = 2

# The docs live next to the rule ids; hover reads them straight off disk so the
# server and `fw://docs/rules/{id}` can never drift apart.
_RULES_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "reference" / "rules"

# A word under the cursor, for hover and for resolving a partial completion.
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def diagnostics_for(source: str) -> list[dict[str, Any]]:
    """Validate *source* and return LSP diagnostics.

    A parse error and a validation error are reported the same way, because to
    the person typing they are the same event: something is wrong on this line.
    """
    try:
        program = parse(source)
    except ParseError as exc:
        return [_diagnostic(exc.line, exc.column, str(exc), _ERROR, "PARSE_ERROR", len(source))]
    except Exception as exc:  # noqa: BLE001 - a server must not die on bad input
        logger.debug("parse failed", exc_info=True)
        return [_diagnostic(1, 1, f"could not parse: {exc}", _ERROR, "PARSE_ERROR", len(source))]

    try:
        result = validate(program)
    except Exception:  # noqa: BLE001
        logger.debug("validate failed", exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for item, severity in ((e, _ERROR) for e in result.errors):
        out.append(_from_validation(item, severity, source))
    for item, severity in ((w, _WARNING) for w in result.warnings):
        out.append(_from_validation(item, severity, source))
    return out


def _from_validation(item: Any, severity: int, source: str) -> dict[str, Any]:
    # The message may explain itself over several lines (the `$`-scoping
    # ladder). Editors show the whole thing in the hover/problems panel, so it
    # is passed through intact rather than truncated to the summary.
    return _diagnostic(
        item.line or 1,
        item.column or 1,
        item.message,
        severity,
        item.rule_id,
        len(source),
        docs_uri=item.docs_uri,
    )


def _diagnostic(
    line: int | None,
    column: int | None,
    message: str,
    severity: int,
    rule_id: str,
    _source_len: int,
    docs_uri: str | None = None,
) -> dict[str, Any]:
    # LSP is 0-based in both axes; the compiler is 1-based in both. Getting this
    # wrong puts every squiggle one line off, which looks like a broken server
    # rather than an off-by-one.
    ln = max((line or 1) - 1, 0)
    col = max((column or 1) - 1, 0)
    diag: dict[str, Any] = {
        "range": {
            "start": {"line": ln, "character": col},
            # The compiler reports a point, not a span. Underlining a single
            # character is nearly invisible, so extend to a short run — enough
            # to see, short enough not to swallow the rest of the line.
            "end": {"line": ln, "character": col + 1},
        },
        "severity": severity,
        "source": "ffl",
        "message": message,
    }
    if rule_id and rule_id != "UNKNOWN":
        diag["code"] = rule_id
        if docs_uri:
            diag["codeDescription"] = {"href": docs_uri}
    return diag


def facet_completions(source: str) -> list[dict[str, Any]]:
    """Completion items for every facet declared in *source*.

    Signatures come from the AST rather than from a name index, so what the
    editor offers is what this file actually declares — including the
    parameter list, which is the part an author is most likely to get wrong.
    """
    try:
        program = parse(source)
    except Exception:  # noqa: BLE001 - half-typed source is the normal case here
        return []

    items: list[dict[str, Any]] = []
    for ns in getattr(program, "namespaces", []) or []:
        ns_name = getattr(ns, "name", "") or ""
        # Declarations are grouped by kind on the namespace, not held in one
        # list, and the kind is worth showing: "event facet" tells the author a
        # handler is required, which "facet" does not.
        for attr, kind in (
            ("event_facets", "event facet"),
            ("facets", "facet"),
            ("workflows", "workflow"),
        ):
            for decl in getattr(ns, attr, None) or []:
                sig = getattr(decl, "sig", None)
                name = getattr(sig, "name", None)
                if not name:
                    continue
                params = ", ".join(
                    f"{p.name}: {_type_name(p.type)}" for p in (getattr(sig, "params", None) or [])
                )
                returns_clause = getattr(sig, "returns", None)
                returns = ", ".join(
                    f"{r.name}: {_type_name(r.type)}"
                    for r in (getattr(returns_clause, "params", None) or [])
                )
                detail = f"({params})" + (f" => ({returns})" if returns else "")
                items.append(
                    {
                        "label": name,
                        # 3 = Function. Facets are called, so the function icon
                        # is the honest one.
                        "kind": 3,
                        "detail": detail,
                        "documentation": {
                            "kind": "markdown",
                            "value": (
                                f"**{ns_name}.{name}** — {kind}\n\n"
                                f"```ffl\n{name}{detail}\n```"
                            ),
                        },
                        # Insert the call with its parens so the cursor lands
                        # where arguments go.
                        "insertText": f"{name}(",
                    }
                )
    return items


def _type_name(node: Any) -> str:
    for attr in ("name", "type_name", "value"):
        got = getattr(node, attr, None)
        if isinstance(got, str):
            return got
    return str(node)


def hover_for(source: str, line: int, character: int) -> str | None:
    """Markdown for the symbol at a position: a rule doc, or a facet signature."""
    lines = source.splitlines()
    if line >= len(lines):
        return None
    word = _word_at(lines[line], character)
    if not word:
        return None

    # A diagnostic on this line wins: if something is wrong here, the rule doc
    # is what the author needs, not the signature of whatever they hovered.
    for diag in diagnostics_for(source):
        if diag["range"]["start"]["line"] == line and diag.get("code"):
            doc = _rule_doc(str(diag["code"]))
            if doc:
                return doc

    for item in facet_completions(source):
        if item["label"] == word.rsplit(".", 1)[-1]:
            return item["documentation"]["value"]
    return None


def _word_at(line_text: str, character: int) -> str | None:
    for match in _WORD.finditer(line_text):
        if match.start() <= character <= match.end():
            return match.group(0)
    return None


def _rule_doc(rule_id: str) -> str | None:
    path = _RULES_DIR / f"{rule_id}.md"
    if not path.is_file():
        return None
    text = path.read_text()
    # Editors render a hover card, not a page. Keep the head — the rule name and
    # the explanation — and drop the long tail of examples.
    return "\n".join(text.splitlines()[:40])


# ---------------------------------------------------------------------------
# JSON-RPC over stdio
# ---------------------------------------------------------------------------


class LanguageServer:
    """Minimal LSP server: diagnostics, completion, hover."""

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO):
        self._in = stdin
        self._out = stdout
        self._docs: dict[str, str] = {}
        self._shutdown = False

    # -- framing -----------------------------------------------------------

    def _read_message(self) -> dict[str, Any] | None:
        """Read one Content-Length framed JSON body, or None at EOF."""
        headers: dict[str, str] = {}
        while True:
            raw = self._in.readline()
            if not raw:
                return None  # client closed the pipe
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                break  # blank line ends the header block
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            return None
        if length <= 0:
            return None
        body = self._in.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.debug("undecodable body: %r", body[:200])
            return None

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._out.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
        self._out.write(body)
        self._out.flush()

    def _respond(self, request_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- handlers ----------------------------------------------------------

    def _publish(self, uri: str) -> None:
        source = self._docs.get(uri, "")
        self._notify(
            "textDocument/publishDiagnostics",
            {"uri": uri, "diagnostics": diagnostics_for(source)},
        )

    def _handle(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        request_id = msg.get("id")

        if method == "initialize":
            self._respond(
                request_id,
                {
                    "capabilities": {
                        # 1 = full text on every change. FFL files are small and
                        # the validator is fast; incremental sync would be more
                        # code for no perceptible gain.
                        "textDocumentSync": 1,
                        "completionProvider": {"triggerCharacters": ["."]},
                        "hoverProvider": True,
                    },
                    "serverInfo": {"name": "facetwork-ffl", "version": "1"},
                },
            )
            return

        if method == "initialized":
            return

        if method == "shutdown":
            self._shutdown = True
            self._respond(request_id, None)
            return

        if method == "exit":
            raise SystemExit(0 if self._shutdown else 1)

        doc = (params.get("textDocument") or {})
        uri = doc.get("uri", "")

        if method == "textDocument/didOpen":
            self._docs[uri] = doc.get("text", "")
            self._publish(uri)
            return

        if method == "textDocument/didChange":
            changes = params.get("contentChanges") or []
            if changes:
                self._docs[uri] = changes[-1].get("text", "")
            self._publish(uri)
            return

        if method == "textDocument/didSave":
            if "text" in params:
                self._docs[uri] = params["text"]
            self._publish(uri)
            return

        if method == "textDocument/didClose":
            self._docs.pop(uri, None)
            # Clear the squiggles: a closed file's problems must not linger in
            # the panel as if they were still live.
            self._notify("textDocument/publishDiagnostics", {"uri": uri, "diagnostics": []})
            return

        if method == "textDocument/completion":
            self._respond(request_id, facet_completions(self._docs.get(uri, "")))
            return

        if method == "textDocument/hover":
            pos = params.get("position") or {}
            markdown = hover_for(
                self._docs.get(uri, ""), pos.get("line", 0), pos.get("character", 0)
            )
            self._respond(
                request_id,
                {"contents": {"kind": "markdown", "value": markdown}} if markdown else None,
            )
            return

        # Unknown request: answer anyway. Leaving a request outstanding hangs
        # some clients, which looks like the server crashed.
        if request_id is not None:
            self._respond(request_id, None)

    def serve(self) -> int:
        while True:
            msg = self._read_message()
            if msg is None:
                return 0
            try:
                self._handle(msg)
            except SystemExit:
                raise
            except Exception:  # noqa: BLE001 - one bad message must not end the session
                logger.debug("handler failed for %s", msg.get("method"), exc_info=True)
                if msg.get("id") is not None:
                    self._respond(msg["id"], None)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Language server for FFL")
    ap.add_argument("--log-level", default="WARNING")
    args = ap.parse_args(argv)
    # stdout is the protocol channel — anything else written there corrupts the
    # stream, so logs go to stderr.
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr)

    server = LanguageServer(sys.stdin.buffer, sys.stdout.buffer)
    try:
        return server.serve()
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        return 0
