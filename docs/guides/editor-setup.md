# Writing FFL in an editor

`fw ffl lsp` starts a language server for FFL. It speaks LSP over stdio, so any
editor with an LSP client can show FFL problems **as you type** rather than when
you next run the compiler.

That gap is most of what makes FFL hard to author. The validator has always
produced everything an editor needs — a stable `rule_id`, a `docs_uri`, a
message, a line and a column — but you only saw any of it by running a command,
so you met the 63 rules one round-trip at a time.

## What it gives you

| | |
|---|---|
| **Diagnostics** | every validator error and warning, inline, with its `rule_id` as the diagnostic code and a link to the rule doc |
| **Completion** | the facets, event facets and workflows the file declares, each with its full signature |
| **Hover** | a facet's signature at any call site; on a line with a problem, the rule doc instead |

Hover prefers the rule doc when the line has a diagnostic, on the assumption
that if something is wrong here that is what you need — not the signature of
whatever token happens to be under the cursor.

## Neovim

```lua
vim.filetype.add({ extension = { ffl = "ffl" } })

vim.api.nvim_create_autocmd("FileType", {
  pattern = "ffl",
  callback = function(args)
    vim.lsp.start({
      name = "facetwork-ffl",
      cmd = { "fw", "ffl", "lsp" },
      root_dir = vim.fs.dirname(vim.fs.find({ ".git" }, { upward = true })[1]),
    }, { bufnr = args.buf })
  end,
})
```

## VS Code

VS Code needs a small extension to register the `ffl` language id and launch the
server; there is no way to add a language from settings alone. The client half
is the standard `LanguageClient` boilerplate with:

```ts
serverOptions = { command: "fw", args: ["ffl", "lsp"], transport: TransportKind.stdio }
clientOptions = { documentSelector: [{ scheme: "file", language: "ffl" }] }
```

## Helix

```toml
# languages.toml
[[language]]
name = "ffl"
scope = "source.ffl"
file-types = ["ffl"]
roots = [".git"]
language-servers = ["facetwork-ffl"]

[language-server.facetwork-ffl]
command = "fw"
args = ["ffl", "lsp"]
```

## Checking it works

The server is not meant to be run by hand — an editor drives it — but you can
prove the plumbing end to end:

```bash
python - <<'PY' | fw ffl lsp
import json, sys
def frame(m):
    b = json.dumps(m).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(b)}\r\n\r\n".encode() + b)
frame({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
frame({"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{
    "uri":"file:///t.ffl","text":"namespace d { workflow W() => (n: Int) andThen { yield W(n = nope.x) } }"}}})
frame({"jsonrpc":"2.0","id":2,"method":"shutdown"})
frame({"jsonrpc":"2.0","method":"exit"})
sys.stdout.buffer.flush()
PY
```

You should see an `initialize` reply followed by a `publishDiagnostics`
notification carrying `REF_UNDEFINED_STEP`.

## Notes

- **Logs go to stderr, never stdout.** stdout is the protocol channel; anything
  else written there corrupts the stream. `--log-level DEBUG` is safe.
- **No new dependency.** The JSON-RPC framing is implemented directly rather
  than via `pygls`, so the compiler's only runtime dependency is still lark.
- **Whole-file sync.** FFL files are small and validation is fast, so the server
  re-validates the whole buffer on each change; there is no incremental parsing
  to get subtly wrong.
- The server analyses **one file at a time**. A facet declared in another file
  is not an error (it may be a library compiled separately), but if it closely
  resembles a name in this file you get `REF_FACET_ASSUMED_EXTERNAL` — the
  warning that catches a typo which would otherwise compile and fail at run time
  with no handler.

## See also

- [`docs/reference/rules/`](../reference/rules/) — one page per `rule_id`, which is what hover shows
- [`examples/canonical/`](../../examples/canonical/) — validator-clean templates to start from
- [grammar.md](../reference/language/grammar.md) — the full syntax reference
