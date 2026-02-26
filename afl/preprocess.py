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

"""Pre-lex preprocessor for brace-delimited script blocks.

Converts ``script { code }`` and ``script python { code }`` to
``script "escaped_code"`` and ``script python "escaped_code"`` before
the LALR parser sees the source.  This allows users to write raw Python
inside braces while keeping the Lark grammar simple (string-only).
"""


class PreprocessError(Exception):
    """Error during script brace preprocessing."""

    def __init__(self, message: str, line: int | None = None):
        self.line = line
        loc = f" at line {line}" if line is not None else ""
        super().__init__(f"{message}{loc}")


def preprocess_script_braces(source: str) -> str:
    """Convert brace-delimited script blocks to quoted-string form.

    Scans *source* for ``script {`` and ``script python {`` outside AFL
    comments and string literals, extracts the Python code between the
    matching braces (using depth tracking that respects Python string
    literals), escapes it, and replaces the block with a quoted string.

    Line count is preserved by padding consumed lines with blanks so that
    error messages from the LALR parser still report correct line numbers.

    Args:
        source: AFL source code (may contain brace-delimited scripts).

    Returns:
        Transformed source with all brace-delimited scripts replaced by
        quoted-string scripts.

    Raises:
        PreprocessError: On unbalanced braces or unterminated strings.
    """
    result: list[str] = []
    i = 0
    length = len(source)

    while i < length:
        # --- skip AFL line comments ---
        if source[i] == "/" and i + 1 < length and source[i + 1] == "/":
            start = i
            while i < length and source[i] != "\n":
                i += 1
            result.append(source[start:i])
            continue

        # --- skip AFL block comments ---
        if source[i] == "/" and i + 1 < length and source[i + 1] == "*":
            start = i
            i += 2
            while i + 1 < length and not (source[i] == "*" and source[i + 1] == "/"):
                i += 1
            i += 2  # skip */
            result.append(source[start:i])
            continue

        # --- skip AFL string literals ---
        if source[i] == '"':
            start = i
            i += 1
            while i < length and source[i] != '"':
                if source[i] == "\\":
                    i += 1  # skip escaped char
                i += 1
            i += 1  # skip closing quote
            result.append(source[start:i])
            continue

        # --- detect `script` keyword ---
        if _match_keyword(source, i, "script"):
            kw_start = i
            i += 6  # len("script")

            # skip whitespace (not newlines — AFL rule)
            while i < length and source[i] in " \t":
                i += 1

            # optional `python` keyword
            has_python = False
            if _match_keyword(source, i, "python"):
                has_python = True
                i += 6
                while i < length and source[i] in " \t":
                    i += 1

            # skip optional newlines before brace
            while i < length and source[i] in " \t\r\n":
                i += 1

            # check for opening brace
            if i < length and source[i] == "{":
                # Count how many lines the `script ... {` prefix consumed
                prefix_text = source[kw_start:i + 1]
                prefix_lines = prefix_text.count("\n")

                brace_open_line = source[:i].count("\n") + 1
                code, end_pos = _extract_brace_block(source, i, brace_open_line)

                # Count lines consumed by the braced block (including braces)
                block_text = source[i:end_pos]
                block_lines = block_text.count("\n")

                # Total lines consumed by the whole construct
                total_lines = prefix_lines + block_lines

                # Escape the code for embedding in a double-quoted string
                escaped = _escape_for_string(code)

                # Build replacement
                if has_python:
                    replacement = f'script python "{escaped}"'
                else:
                    replacement = f'script "{escaped}"'

                # Pad with newlines to preserve line count
                replacement_lines = replacement.count("\n")
                padding = "\n" * (total_lines - replacement_lines)
                result.append(replacement + padding)

                i = end_pos
                continue
            else:
                # Not a brace — emit keyword as-is, let parser handle
                result.append(source[kw_start:i])
                continue

        result.append(source[i])
        i += 1

    return "".join(result)


def _match_keyword(source: str, pos: int, keyword: str) -> bool:
    """Check if *keyword* starts at *pos* and is not part of a larger identifier."""
    end = pos + len(keyword)
    if end > len(source):
        return False
    if source[pos:end] != keyword:
        return False
    # Must not be preceded by an identifier char
    if pos > 0 and (source[pos - 1].isalnum() or source[pos - 1] == "_"):
        return False
    # Must not be followed by an identifier char
    if end < len(source) and (source[end].isalnum() or source[end] == "_"):
        return False
    return True


def _extract_brace_block(source: str, open_pos: int, open_line: int) -> tuple[str, int]:
    """Extract the content of a brace-delimited block.

    Starts at the ``{`` at *open_pos*, tracks depth (respecting Python
    string literals), and returns ``(code_content, end_position)`` where
    *end_position* points just past the closing ``}``.

    Args:
        source: Full source text.
        open_pos: Index of the opening ``{``.
        open_line: Line number of the opening brace (for error messages).

    Returns:
        Tuple of (extracted code string, position after closing brace).

    Raises:
        PreprocessError: On unbalanced braces.
    """
    assert source[open_pos] == "{"
    i = open_pos + 1
    depth = 1
    length = len(source)

    while i < length and depth > 0:
        ch = source[i]

        # Skip Python string literals (single, double, triple-quoted)
        if ch in ('"', "'"):
            i = _skip_python_string(source, i)
            continue

        # Skip Python comments
        if ch == "#":
            while i < length and source[i] != "\n":
                i += 1
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

        i += 1

    if depth != 0:
        raise PreprocessError("Unbalanced brace in script block", line=open_line)

    # i is now past the closing }
    # Code is everything between the braces (exclusive)
    code = source[open_pos + 1 : i - 1]

    # Strip one leading/trailing newline if present for cleaner code
    if code.startswith("\n"):
        code = code[1:]
    if code.endswith("\n"):
        code = code[:-1]

    # For single-line code (no newlines), strip leading/trailing whitespace
    if "\n" not in code:
        code = code.strip()
    else:
        # Dedent: find minimum indentation and strip it
        code = _dedent(code)

    return code, i


def _skip_python_string(source: str, pos: int) -> int:
    """Skip past a Python string literal starting at *pos*.

    Handles single/double quotes, triple-quoted strings, and escape
    sequences.  Returns the index just past the closing delimiter.
    """
    length = len(source)
    quote_char = source[pos]

    # Check for triple-quote
    if pos + 2 < length and source[pos + 1] == quote_char and source[pos + 2] == quote_char:
        # Triple-quoted string
        end_delim = quote_char * 3
        i = pos + 3
        while i < length:
            if source[i] == "\\" and i + 1 < length:
                i += 2
                continue
            if source[i:i + 3] == end_delim:
                return i + 3
            i += 1
        return length  # unterminated — let Python's own parser complain

    # Single-quoted string
    i = pos + 1
    while i < length:
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == quote_char:
            return i + 1
        if source[i] == "\n":
            return i  # unterminated single-line string
        i += 1
    return length


def _dedent(code: str) -> str:
    """Remove common leading whitespace from all non-empty lines."""
    lines = code.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return code
    min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    if min_indent == 0:
        return code
    return "\n".join(line[min_indent:] if len(line) >= min_indent else line for line in lines)


def _escape_for_string(code: str) -> str:
    """Escape code content for embedding in a double-quoted AFL string literal."""
    code = code.replace("\\", "\\\\")
    code = code.replace('"', '\\"')
    code = code.replace("\n", "\\n")
    code = code.replace("\t", "\\t")
    return code
