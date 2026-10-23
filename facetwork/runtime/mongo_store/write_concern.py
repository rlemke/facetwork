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

"""Start-up check: is the write concern on the task collection safe?

A claim is a ``find_one_and_update`` on one task document. Whether a claim the
runner was TOLD succeeded actually survives depends on the store's topology:

- **Unacknowledged (``w=0``)** — the runner never learns whether its claim
  landed, so two runners can each believe they own a task. Refused on every
  topology.
- **Replica set / sharded, below majority** — an acknowledged claim can be
  rolled back when the primary steps down; the new primary hands the task to a
  second runner while the first is still executing it. Refused.
- **Standalone** — write concern does not arise: there is one copy and it is
  either written or not. The exposure is different and larger: the node is a
  single point of failure. Reported, and refused only when
  ``FW_REQUIRE_REPLICATED`` is set (a production profile).

A write concern that cannot be determined (the server default is not readable)
is reported as *unverified*, never as safe.

See docs/reference/execution-semantics.md and thesis §15.10.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


class UnsafeWriteConcernError(RuntimeError):
    """The store's write concern would let an acknowledged claim be lost."""


@dataclass(frozen=True)
class WriteConcernReport:
    """What the check found. ``verdict`` is ok | standalone | unverified | unsafe."""

    topology: str  # standalone | replica_set | sharded | unknown
    write_concern: dict
    source: str  # collection | server-default | implicit | unknown
    verdict: str
    detail: str


def _required_replication() -> bool:
    return os.environ.get("FW_REQUIRE_REPLICATED", "").strip().lower() in _TRUTHY


def _topology(hello: dict) -> str:
    if hello.get("msg") == "isdbgrid":
        return "sharded"
    if hello.get("setName"):
        return "replica_set"
    if "isWritablePrimary" in hello or "ismaster" in hello:
        return "standalone"
    return "unknown"


def _majority_of(hello: dict) -> int:
    """Votes needed for a majority, from the members ``hello`` lists as electable."""
    return len(hello.get("hosts") or []) // 2 + 1


def _is_majority(w: Any, hello: dict) -> bool:
    if w == "majority":
        return True
    if isinstance(w, int) and not isinstance(w, bool):
        return bool(hello.get("hosts")) and w >= _majority_of(hello)
    # A custom tag set (getLastErrorModes) cannot be judged from here.
    return False


def assess_write_concern(db: Any, collection: str = "tasks") -> WriteConcernReport:
    """Inspect the topology and the effective write concern of ``collection``.

    Never raises for an unsafe concern; :func:`check_write_concern` decides
    what to do with the verdict.
    """
    client = db.client
    hello = client.admin.command("hello")
    topology = _topology(hello)
    wc = dict(db[collection].write_concern.document or {})
    source = "collection" if wc else "unknown"

    if wc.get("w") == 0:
        return WriteConcernReport(
            topology,
            wc,
            source,
            "unsafe",
            "w=0 (unacknowledged): a runner cannot know whether its claim landed",
        )

    if topology == "standalone":
        return WriteConcernReport(
            topology,
            wc,
            source,
            "standalone",
            "standalone node: no replication, so no rollback, but the store is a "
            "single point of failure",
        )

    if not wc:
        # The client sets nothing: the server's default applies. MongoDB 4.4+
        # exposes it (5.0+ also reports the IMPLICIT default it computed).
        try:
            res = client.admin.command("getDefaultRWConcern")
            wc = dict(res.get("defaultWriteConcern") or {})
            source = (
                "implicit" if res.get("defaultWriteConcernSource") == "implicit" else "server-default"
            )
        except Exception as exc:  # noqa: BLE001 — old server or no privilege
            return WriteConcernReport(
                topology,
                {},
                "unknown",
                "unverified",
                f"no write concern on the client and the server default is unreadable ({exc})",
            )
        if not wc:
            return WriteConcernReport(
                topology,
                {},
                "unknown",
                "unverified",
                "no write concern on the client and the server reports no default",
            )

    if _is_majority(wc.get("w"), hello):
        return WriteConcernReport(topology, wc, source, "ok", f"w={wc.get('w')!r} ({source})")
    return WriteConcernReport(
        topology,
        wc,
        source,
        "unsafe",
        f"w={wc.get('w')!r} ({source}) is below majority: an acknowledged claim can be "
        "rolled back on a primary step-down and the task handed to a second runner",
    )


def check_write_concern(db: Any, collection: str = "tasks") -> WriteConcernReport:
    """Run :func:`assess_write_concern` and act on it.

    Raises :class:`UnsafeWriteConcernError` for an unsafe concern, and for a
    standalone node when ``FW_REQUIRE_REPLICATED`` is set. Logs a warning for
    a standalone node or an unverified concern otherwise.
    """
    report = assess_write_concern(db, collection)
    fix = (
        " Fix the connection string (e.g. w=majority) or the server default "
        "(setDefaultRWConcern)."
    )
    if report.verdict == "unsafe":
        raise UnsafeWriteConcernError(
            f"Refusing to start: unsafe write concern on '{collection}' "
            f"({report.topology}): {report.detail}.{fix}"
        )
    if report.verdict == "standalone":
        if _required_replication():
            raise UnsafeWriteConcernError(
                f"Refusing to start: FW_REQUIRE_REPLICATED is set and the store is a "
                f"{report.detail}. Point FW_MONGODB_URL at a replica set."
            )
        logger.warning("MongoDB store: %s.", report.detail)
    elif report.verdict == "unverified":
        logger.warning(
            "MongoDB store (%s): write concern on '%s' UNVERIFIED — %s.",
            report.topology,
            collection,
            report.detail,
        )
    return report
