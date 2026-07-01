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

"""Legacy ``AFL_`` env-var detector — the ``AFL_`` -> ``FW_`` migration is done.

``FW_`` is the ONLY accepted env-var prefix (matching the ``fw`` CLI, the ``fw_*``
MCP tools, and the ``fw:`` internal task prefix). The old ``AFL_`` <-> ``FW_``
mirror shim has been RETIRED: an ``AFL_*`` var is no longer promoted to ``FW_*``
and its value is IGNORED. The presence of any ``AFL_*`` var is treated as an
error and logged loudly at startup so it gets renamed.

:func:`install` runs once at ``facetwork`` package import (see ``facetwork/__init__``).
Read config with ``FW_`` names directly, or via :func:`fw_getenv`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping

_LEGACY = "AFL_"
_CANON = "FW_"
_installed = False


def legacy_vars(env: MutableMapping[str, str] | None = None) -> list[str]:
    """Return the sorted list of legacy ``AFL_*`` var names present in ``env``.

    These are unsupported — the value is not read anywhere. Used by :func:`install`
    to report them as an error.
    """
    e = os.environ if env is None else env
    return sorted(k for k in list(e) if k.startswith(_LEGACY))


def fw_getenv(base: str, default: str | None = None) -> str | None:
    """Resolve a base env name (no prefix) from ``FW_`` only.

    ``fw_getenv("MONGODB_URL")`` reads ``FW_MONGODB_URL``. The legacy ``AFL_``
    prefix is NOT consulted — it is retired.
    """
    return os.environ.get(_CANON + base, default)


def install(warn: bool = True) -> None:
    """Run once at import. Reports any lingering ``AFL_*`` vars as an error.

    Does NOT mutate ``os.environ`` — the mirror is gone. Safe to call repeatedly.
    """
    global _installed
    if _installed:
        return
    _installed = True
    if warn:
        legacy = legacy_vars()
        if legacy:
            logging.getLogger("facetwork.env").error(
                "Unsupported legacy AFL_* env var(s) present (the AFL_ prefix is "
                "retired — rename to FW_*; these values are IGNORED): %s",
                ", ".join(legacy),
            )
