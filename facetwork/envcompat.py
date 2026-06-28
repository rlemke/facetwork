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

"""Env-var prefix compatibility shim for the ``AFL_`` -> ``FW_`` migration.

The platform's environment variables are migrating off the legacy ``AFL_``
prefix (a relic of the pre-Facetwork name) onto ``FW_``, matching the ``fw`` CLI,
the ``fw_*`` MCP tools, and the ``fw:`` internal task prefix.

Rather than rewrite ~1,900 read-sites across the engine + domain packages in one
flag-day, this module mirrors the two prefixes in ``os.environ`` at process
startup. ``FW_`` is canonical: where both are set it wins; a lone ``FW_X`` is
copied down to ``AFL_X`` so existing ``os.environ["AFL_X"]`` readers keep working,
and a lone ``AFL_X`` is copied up to ``FW_X`` so new ``FW_``-aware code works
against old config. The result: operators can set **either** prefix during the
transition and everything resolves.

:func:`install` is called once at ``facetwork`` package import (see
``facetwork/__init__``). New code should read config via :func:`fw_getenv`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping

_LEGACY = "AFL_"
_CANON = "FW_"
_installed = False


def normalize_environ(env: MutableMapping[str, str] | None = None) -> int:
    """Mirror ``AFL_*`` <-> ``FW_*`` in ``env`` (default ``os.environ``).

    ``FW_`` is authoritative: every ``FW_X`` is pushed down to ``AFL_X``
    (overwriting), then every lone ``AFL_X`` (no ``FW_X``) is mirrored up to
    ``FW_X``. Idempotent. Returns the count of legacy ``AFL_*`` vars present
    (for the deprecation notice).
    """
    e = os.environ if env is None else env
    # Snapshot the operator-set keys BEFORE mirroring, so the legacy count
    # reflects what they actually set (not the copies we are about to make).
    canon_keys = [k for k in list(e) if k.startswith(_CANON)]
    legacy_keys = [k for k in list(e) if k.startswith(_LEGACY)]
    # FW_ wins: push every FW_X down onto AFL_X.
    for key in canon_keys:
        e[_LEGACY + key[len(_CANON) :]] = e[key]
    # Fill the gaps: any operator-set AFL_X without a FW_X gets mirrored up.
    for key in legacy_keys:
        e.setdefault(_CANON + key[len(_LEGACY) :], e[key])
    return len(legacy_keys)


def fw_getenv(base: str, default: str | None = None) -> str | None:
    """Resolve a base env name (no prefix), ``FW_`` taking precedence over ``AFL_``.

    Use this in new code instead of ``os.environ.get("AFL_X")`` so the read is
    correct even if :func:`install` has not run (e.g. a var set after startup).
    ``fw_getenv("MONGODB_URL")`` reads ``FW_MONGODB_URL`` then ``AFL_MONGODB_URL``.
    """
    return os.environ.get(_CANON + base, os.environ.get(_LEGACY + base, default))


def install(warn: bool = True) -> None:
    """Normalize ``os.environ`` once. Safe to call repeatedly."""
    global _installed
    if _installed:
        return
    _installed = True
    n_legacy = normalize_environ()
    if warn and n_legacy:
        logging.getLogger("facetwork.env").info(
            "%d legacy AFL_* env var(s) in use. FW_* is the new prefix (both "
            "work during migration); please migrate AFL_* -> FW_*.",
            n_legacy,
        )
