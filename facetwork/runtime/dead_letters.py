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

"""Surface tasks that exhausted their retries and were then forgotten.

A dead letter is not a bug report — it is a task that failed, retried its
budget, gave up, and stopped telling anyone. `repair-workflow` can re-enqueue
them, but it is invoked per workflow by an operator who already suspects
something. Nothing ever asks the standing question: *is anything dead and
unattended?*

That gap has a concrete cost here. A `save_earth.sources.DownloadNuclearReactors`
task met a single transient `503 HeadObject` from the object store on
2026-08-11 — MinIO was not down, seven other tasks completed in the same
window — retried five times, dead-lettered, and sat untouched for ten days. It
was found only because someone happened to sort steps by recency while looking
at something else. A momentary blip had become a permanent failure, silently.

So this reports, and deliberately does NOT retry by default. A dead letter has
already proved it fails; re-driving the whole set unattended would spend the
retry budget again on whatever is genuinely broken and bury the transient ones
it was meant to rescue. The operator picks, with the facet name and the error
in front of them.

Age is the signal that matters. A dead letter from ten minutes ago may still
have someone watching it; one from three weeks ago certainly does not.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeadLetterGroup:
    """Dead letters sharing a facet name — the useful unit for triage."""

    name: str
    count: int
    oldest_hours: float
    newest_hours: float
    errors: list[str] = field(default_factory=list)
    uuids: list[str] = field(default_factory=list)

    @property
    def forgotten(self) -> bool:
        """Old enough that nobody is plausibly still watching it."""
        return self.oldest_hours >= FORGOTTEN_HOURS


#: Past this, a dead letter is not "being worked on", it is abandoned.
FORGOTTEN_HOURS = 24.0


def _error_text(task: dict[str, Any]) -> str:
    err = task.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err or "")


def collect(db, *, now_ms: int | None = None) -> list[DeadLetterGroup]:
    """Group every dead-lettered task by facet name, newest group first.

    Grouped rather than listed flat because the same broken handler produces
    one dead letter per attempt across many workflows: a flat list of ninety
    rows hides that they are three problems, and a triage tool that buries the
    shape of the failure is one nobody uses twice.
    """
    now = now_ms or int(time.time() * 1000)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in db.tasks.find({"state": "dead_letter"}):
        buckets[str(t.get("name") or "(unnamed)")].append(t)

    groups: list[DeadLetterGroup] = []
    for name, tasks in buckets.items():
        ages = [(now - (t.get("created") or now)) / 3600000.0 for t in tasks]
        errs: list[str] = []
        for t in tasks:
            e = _error_text(t)
            if e and e not in errs:
                errs.append(e)
        groups.append(DeadLetterGroup(
            name=name,
            count=len(tasks),
            oldest_hours=max(ages) if ages else 0.0,
            newest_hours=min(ages) if ages else 0.0,
            errors=errs[:3],
            uuids=[str(t.get("uuid")) for t in tasks],
        ))
    groups.sort(key=lambda g: -g.oldest_hours)
    return groups


def retry(db, uuids: list[str], *, now_ms: int | None = None) -> int:
    """Re-enqueue specific dead letters. Returns how many were reset.

    Resets ``retry_count`` as well as state: leaving it at the exhausted value
    would make the task dead-letter again on its first hiccup, which looks like
    "the retry did not work" rather than "it was never given a budget".

    ``next_retry_after`` is cleared so a task does not sit in a cooldown window
    inherited from the failure that killed it.
    """
    if not uuids:
        return 0
    now = now_ms or int(time.time() * 1000)
    res = db.tasks.update_many(
        {"uuid": {"$in": list(uuids)}, "state": "dead_letter"},
        {"$set": {"state": "pending", "retry_count": 0,
                  "next_retry_after": 0, "updated": now,
                  "server_id": None}},
    )
    return int(res.modified_count)
