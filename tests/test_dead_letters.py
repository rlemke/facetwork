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

"""Tests for the dead-letter sweep.

The point of this tool is that a dead letter stops telling anyone anything. So
the properties worth pinning are the ones that decide whether a human ever sees
it: age classification, grouping, and the retry-count reset — not the reporting
prose.
"""

from __future__ import annotations

import time

from facetwork.runtime.dead_letters import FORGOTTEN_HOURS, collect, retry


class _FakeTasks:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query):
        state = query.get("state")
        return [d for d in self._docs if state is None or d.get("state") == state]

    def update_many(self, query, update):
        uuids = set(query["uuid"]["$in"])
        state = query.get("state")
        n = 0
        for d in self._docs:
            if d.get("uuid") in uuids and (state is None or d.get("state") == state):
                d.update(update["$set"])
                n += 1
        return type("R", (), {"modified_count": n})()


class _FakeDB:
    def __init__(self, docs):
        self.tasks = _FakeTasks(docs)


def _task(name, *, hours_ago, uuid, error="boom", state="dead_letter"):
    now = int(time.time() * 1000)
    return {"uuid": uuid, "name": name, "state": state,
            "created": now - int(hours_ago * 3600000),
            "error": {"message": error}, "retry_count": 5}


# --- grouping ---------------------------------------------------------------


def test_tasks_are_grouped_by_facet_not_listed_flat():
    """One broken handler produces a dead letter per attempt across many
    workflows. A flat list of ninety rows hides that they are three problems,
    and a triage tool that buries the shape of the failure is not used twice."""
    db = _FakeDB([
        _task("a.B", hours_ago=50, uuid="1"),
        _task("a.B", hours_ago=40, uuid="2"),
        _task("c.D", hours_ago=10, uuid="3"),
    ])
    groups = collect(db)
    assert [g.name for g in groups] == ["a.B", "c.D"], "oldest group first"
    assert groups[0].count == 2
    assert set(groups[0].uuids) == {"1", "2"}


def test_only_dead_letters_are_collected():
    db = _FakeDB([
        _task("a.B", hours_ago=50, uuid="1"),
        _task("a.B", hours_ago=50, uuid="2", state="completed"),
        _task("a.B", hours_ago=50, uuid="3", state="pending"),
    ])
    assert collect(db)[0].count == 1


def test_distinct_errors_are_surfaced_and_bounded():
    """The error is what makes a dead letter triageable, but one facet failing
    fifty different ways should not print fifty lines."""
    db = _FakeDB([_task("a.B", hours_ago=50, uuid=str(i), error=f"e{i}") for i in range(6)])
    g = collect(db)[0]
    assert len(g.errors) == 3
    assert g.errors[0].startswith("e")


def test_identical_errors_are_not_repeated():
    db = _FakeDB([_task("a.B", hours_ago=50, uuid=str(i), error="same") for i in range(4)])
    assert collect(db)[0].errors == ["same"]


# --- the age judgement ------------------------------------------------------


def test_recent_dead_letters_are_not_forgotten():
    """Something that failed ten minutes ago may still have a human on it.
    Failing the check on those would make the check noise, and a noisy check
    gets ignored — which is how the original ten-day-old one survived."""
    db = _FakeDB([_task("a.B", hours_ago=1, uuid="1")])
    assert collect(db)[0].forgotten is False


def test_old_dead_letters_are_forgotten():
    db = _FakeDB([_task("a.B", hours_ago=FORGOTTEN_HOURS + 1, uuid="1")])
    assert collect(db)[0].forgotten is True


def test_a_group_is_forgotten_if_its_OLDEST_member_is():
    """A facet failing continuously has both recent and ancient dead letters.
    It is still unattended, so the oldest decides."""
    db = _FakeDB([
        _task("a.B", hours_ago=0.5, uuid="1"),
        _task("a.B", hours_ago=500, uuid="2"),
    ])
    g = collect(db)[0]
    assert g.forgotten is True
    assert g.newest_hours < 1 and g.oldest_hours > 400


# --- retry ------------------------------------------------------------------


def test_retry_resets_the_retry_budget_not_just_the_state():
    """Leaving retry_count at its exhausted value makes the task dead-letter
    again on its first hiccup, which reads as "the retry did not work" rather
    than "it was never given a budget"."""
    docs = [_task("a.B", hours_ago=50, uuid="1")]
    db = _FakeDB(docs)
    assert retry(db, ["1"]) == 1
    assert docs[0]["state"] == "pending"
    assert docs[0]["retry_count"] == 0
    assert docs[0]["next_retry_after"] == 0
    assert docs[0]["server_id"] is None, "must not stay bound to the runner that failed it"


def test_retry_only_touches_dead_letters():
    """A running task caught by a careless uuid list must not be reset under
    the runner executing it."""
    docs = [_task("a.B", hours_ago=1, uuid="1", state="running")]
    db = _FakeDB(docs)
    assert retry(db, ["1"]) == 0
    assert docs[0]["state"] == "running"


def test_retrying_nothing_is_a_noop():
    db = _FakeDB([_task("a.B", hours_ago=50, uuid="1")])
    assert retry(db, []) == 0
