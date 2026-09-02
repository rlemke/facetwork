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

"""Script execution for FFL script blocks.

Provides sandboxed execution of Python scripts defined in facet script blocks.
Scripts run in a subprocess with enforced timeout. They receive input via
``params`` dict and return output via ``result`` dict.

Example usage::

    executor = ScriptExecutor()
    result = executor.execute(
        code='result["output"] = params["input"].upper()',
        params={"input": "hello"},
    )
    # result == {"output": "HELLO"}

Security Note:
    Scripts execute in a subprocess with a restricted global namespace that
    excludes dangerous builtins. The timeout is enforced via
    ``subprocess.run(timeout=...)``.
"""

from __future__ import annotations

import base64
import builtins as _builtins_mod
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


class ScriptError(Exception):
    """Error raised during script execution."""

    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message)
        self.original = original


@dataclass
class ScriptResult:
    """Result of script execution."""

    success: bool
    result: dict[str, Any]
    error: str | None = None


# Names of builtins allowed in sandboxed execution
_SAFE_BUILTIN_NAMES: list[str] = [
    # Types
    "bool",
    "int",
    "float",
    "str",
    "list",
    "dict",
    "tuple",
    "set",
    "frozenset",
    "bytes",
    "bytearray",
    # Functions
    "len",
    "range",
    "enumerate",
    "zip",
    "map",
    "filter",
    "sorted",
    "reversed",
    "min",
    "max",
    "sum",
    "abs",
    "round",
    "all",
    "any",
    "isinstance",
    "issubclass",
    "hasattr",
    "getattr",
    "setattr",
    "type",
    "repr",
    "print",
    # Constants
    "None",
    "True",
    "False",
    # Exceptions (for catching)
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
]

# Allowed builtins for sandboxed execution (used by tests and reference)
_SAFE_BUILTINS = {name: getattr(_builtins_mod, name) for name in _SAFE_BUILTIN_NAMES}

# Modules allowed in script blocks via `import`
_SAFE_IMPORT_MODULES: list[str] = [
    # Standard library
    "json",
    "math",
    "re",
    "copy",
    "hashlib",
    "collections",
    # ⚠️ `csv` was missing while `json` was present, and the omission is not
    # neutral: a script that must read a CSV hand-rolls a parser instead.
    # Measured 2026-09-02 — a regex "parser" (`re.findall(r'"([^"]*)"')`) over
    # GHCN-Daily silently shifted every field, because the rows carry 124 CSV
    # fields of which only 30 are quoted; the unquoted empties were skipped.
    # It produced plausible, wrong numbers rather than an error. `csv` is pure
    # stdlib parsing with no filesystem or network reach — exactly like `json`.
    "csv",
    "itertools",
    "functools",
    "datetime",
    "statistics",
    "string",
    # Third-party (optional — ImportError if not installed)
    "anthropic",
]


def _build_worker_script(
    code: str,
    params_json: str | None = None,
    extra_import_modules: list[str] | None = None,
) -> str:
    """Build the Python source for the subprocess worker.

    The worker reconstructs the safe-builtins sandbox, deserializes params,
    executes the user code, and writes the result dict as JSON to stdout.
    User ``print()`` calls are captured via ``io.StringIO`` so they don't
    corrupt the JSON output protocol.

    When *params_json* is ``None`` the worker reads JSON from stdin,
    avoiding OS ``ARG_MAX`` limits for large parameter payloads.

    Args:
        code: User script source code
        params_json: JSON-serialized params dict, or None to read from stdin

    Returns:
        Python source string suitable for ``python -c``
    """
    code_b64 = base64.b64encode(code.encode()).decode()
    names_repr = repr(_SAFE_BUILTIN_NAMES)
    allowed_repr = repr(_SAFE_IMPORT_MODULES + list(extra_import_modules or []))
    code_repr = repr(code_b64)
    if params_json is not None:
        params_line = f"_params = _json.loads({repr(params_json)})"
    else:
        params_line = "_params = _json.loads(_sys.stdin.read())"
    lines = [
        "import builtins as _b, base64 as _base64, io as _io, json as _json, sys as _sys",
        f"_names = {names_repr}",
        f"_allowed_modules = {allowed_repr}",
        "_safe = {n: getattr(_b, n) for n in _names}",
        # ⚠️ A SUBMODULE OF AN ALLOWED PACKAGE IS ALLOWED. Matching the exact
        # name only was equivalent to banning most real libraries: an allowlist
        # entry of `astropy` still refused `from astropy.io import fits`,
        # because Python imports the SUBMODULE `astropy.io`. Same for
        # numpy.linalg, scipy.ndimage, collections.abc. The allowlist is about
        # which PACKAGES a script may reach, so the root decides; a package that
        # is not allowed still has every submodule refused.
        "def _restricted_import(name, *a, **kw):",
        "    if name not in _allowed_modules and name.split('.')[0] not in _allowed_modules:",
        "        raise ImportError(f'import of {name!r} is not allowed in script blocks')",
        "    return __import__(name, *a, **kw)",
        "_safe['__import__'] = _restricted_import",
        "_real_stdout = _sys.stdout",
        "_capture = _io.StringIO()",
        "_safe['print'] = lambda *a, **kw: print(*a, **kw, file=_capture)",
        "_result = {}",
        params_line,
        "_sandbox = {'__builtins__': _safe, 'params': _params, 'result': _result, 'json': _json}",
        f"_code = _base64.b64decode({code_repr}).decode()",
        "try:",
        "    _compiled = compile(_code, '<script>', 'exec')",
        "    exec(_compiled, _sandbox)",
        "    _json.dump({'success': True, 'result': _result}, _real_stdout)",
        "except SyntaxError as _e:",
        "    _json.dump({'success': False, 'error': f'Syntax error in script: {_e}'}, _real_stdout)",
        "except Exception as _e:",
        "    _json.dump({'success': False, 'error': f'Script execution error: {type(_e).__name__}: {_e}'}, _real_stdout)",
    ]
    return "\n".join(lines) + "\n"


def _parse_worker_output(stdout: str, stderr: str) -> ScriptResult:
    """Parse JSON output from the subprocess worker.

    Args:
        stdout: Subprocess stdout (should contain JSON)
        stderr: Subprocess stderr (used for error context)

    Returns:
        ScriptResult parsed from the worker output
    """
    if not stdout.strip():
        err_detail = stderr.strip() if stderr.strip() else "no output from script subprocess"
        return ScriptResult(
            success=False,
            result={},
            error=f"Script execution error: {err_detail}",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return ScriptResult(
            success=False,
            result={},
            error="Script execution error: invalid output from script subprocess",
        )

    if data.get("success"):
        return ScriptResult(success=True, result=data.get("result", {}))

    return ScriptResult(
        success=False,
        result={},
        error=data.get("error", "Unknown script error"),
    )


class ScriptExecutor:
    """Executes Python scripts in a sandboxed subprocess.

    Scripts have access to:
    - ``params``: Input parameters (read-only dict)
    - ``result``: Output dict (should be populated by the script)
    - Safe builtins (no __import__, exec, eval, open, etc.)

    The timeout is enforced via ``subprocess.run(timeout=...)``.

    Attributes:
        timeout: Execution timeout in seconds (enforced via subprocess)
    """

    def __init__(self, timeout: float = 30.0):
        """Initialize the executor.

        Args:
            timeout: Maximum execution time in seconds (enforced via subprocess)
        """
        self.timeout = timeout

    def execute(
        self,
        code: str,
        params: dict[str, Any] | None = None,
        language: str = "python",
        python_executable: str | None = None,
        extra_import_modules: list[str] | None = None,
    ) -> ScriptResult:
        """Execute a script with the given parameters.

        Args:
            code: The script source code
            params: Input parameters (available as ``params`` in script)
            language: Script language (only "python" supported)
            python_executable: Interpreter to run the script under — a
                materialized environment venv's python
                (script-environments.md §4). Defaults to this process's
                interpreter (the default environment).

        Returns:
            ScriptResult with success status and result dict

        Raises:
            ScriptError: If language is not supported
        """
        if language != "python":
            return ScriptResult(
                success=False,
                result={},
                error=f"Unsupported script language: {language}",
            )

        return self._execute_python(code, params or {}, python_executable, extra_import_modules)

    def _execute_python(
        self,
        code: str,
        params: dict[str, Any],
        python_executable: str | None = None,
        extra_import_modules: list[str] | None = None,
    ) -> ScriptResult:
        """Execute Python code in a sandboxed subprocess.

        Args:
            code: Python source code
            params: Input parameters

        Returns:
            ScriptResult with execution outcome
        """
        # Serialize params — fail early if not JSON-serializable
        try:
            params_json = json.dumps(params)
        except (TypeError, ValueError) as e:
            return ScriptResult(
                success=False,
                result={},
                error=f"Script execution error: params not serializable: {e}",
            )

        # Pass params via stdin to avoid OS ARG_MAX limits for large payloads
        worker_code = _build_worker_script(code, extra_import_modules=extra_import_modules)

        # ⚠️ `fw.file.FileOps` bundles s3fs SO THAT a script can do
        # storage-portable I/O — but s3fs/boto3 read AWS_*, and the fleet
        # supplies FW_S3_*. Without the alias `fsspec.open("s3://…")` fails with
        # NoCredentialsError inside a script while the identical path works in a
        # handler, which makes the advertised capability a promise nothing keeps.
        #
        # This is NOT new exposure: the worker already inherits the parent
        # environment wholesale (there is no `env=` below without this), so
        # FW_S3_* were always visible to script code. The alias only puts the
        # SAME secrets into the names the bundled library actually reads.
        # An explicit AWS_* already in the environment always wins.
        env = dict(os.environ)
        for aws_name, fw_name in (
            ("AWS_ACCESS_KEY_ID", "FW_S3_ACCESS_KEY"),
            ("AWS_SECRET_ACCESS_KEY", "FW_S3_SECRET_KEY"),
            ("AWS_ENDPOINT_URL", "FW_S3_ENDPOINT"),
            ("AWS_DEFAULT_REGION", "FW_S3_REGION"),
        ):
            fw_val = os.environ.get(fw_name)
            if fw_val and not env.get(aws_name):
                env[aws_name] = fw_val

        try:
            proc = subprocess.run(
                [python_executable or sys.executable, "-c", worker_code],
                input=params_json,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ScriptResult(
                success=False,
                result={},
                error=f"Script timed out after {self.timeout}s",
            )

        return _parse_worker_output(proc.stdout, proc.stderr)


def execute_script(
    code: str,
    params: dict[str, Any] | None = None,
    language: str = "python",
) -> dict[str, Any]:
    """Convenience function to execute a script and return results.

    Args:
        code: The script source code
        params: Input parameters
        language: Script language

    Returns:
        Result dict from script execution

    Raises:
        ScriptError: If script execution fails
    """
    executor = ScriptExecutor()
    result = executor.execute(code, params, language)

    if not result.success:
        raise ScriptError(result.error or "Unknown error")

    return result.result
