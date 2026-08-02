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

"""Claim-side AST-feature routing (docs/architecture/ffl-after-clause.md §8).

The failure this prevents is specific and was observed live: a runner that
doesn't implement a construct rebuilds the dependency graph WITHOUT it, sees no
dependency, and runs steps in the wrong order — silently. Refusing the claim
turns that into "waits for a capable runner".
"""

from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.memory_store import MemoryStore


def _task(name="osm.Do", features=None, task_list="osm"):
    return TaskDefinition(
        uuid=f"t-{name}-{','.join(features or []) or 'none'}",
        name=name,
        runner_id="r",
        workflow_id="w",
        flow_id="f",
        step_id=f"s-{','.join(features or []) or 'none'}",
        state=TaskState.PENDING,
        task_list_name=task_list,
        required_features=list(features or []),
    )


class TestFeatureRouting:
    def test_untagged_task_is_claimable_by_anyone(self):
        """Existing work must be unaffected — the filter is opt-in per task."""
        store = MemoryStore()
        store.save_task(_task())
        claimed = store.claim_task(task_names=["osm.Do"], task_list="osm", known_features=[])
        assert claimed is not None

    def test_runner_lacking_the_feature_does_not_claim(self):
        store = MemoryStore()
        store.save_task(_task(features=["after"]))
        assert store.claim_task(task_names=["osm.Do"], task_list="osm", known_features=[]) is None
        # ...and the task is still pending, not lost
        assert store.get_pending_tasks("osm")[0].required_features == ["after"]

    def test_runner_with_the_feature_claims_it(self):
        store = MemoryStore()
        store.save_task(_task(features=["after"]))
        claimed = store.claim_task(task_names=["osm.Do"], task_list="osm", known_features=["after"])
        assert claimed is not None and claimed.state == "running"

    def test_all_required_features_must_be_known(self):
        """Subset semantics: knowing one of two is not enough."""
        store = MemoryStore()
        store.save_task(_task(features=["after", "future_thing"]))
        assert (
            store.claim_task(task_names=["osm.Do"], task_list="osm", known_features=["after"])
            is None
        )
        assert (
            store.claim_task(
                task_names=["osm.Do"],
                task_list="osm",
                known_features=["after", "future_thing"],
            )
            is not None
        )

    def test_extra_runner_features_are_harmless(self):
        store = MemoryStore()
        store.save_task(_task(features=["after"]))
        claimed = store.claim_task(
            task_names=["osm.Do"], task_list="osm", known_features=["after", "something_else"]
        )
        assert claimed is not None

    def test_a_capable_runner_still_gets_it_after_one_declines(self):
        """The point of the gate: the work waits, it is not dropped."""
        store = MemoryStore()
        store.save_task(_task(features=["after"]))
        assert store.claim_task(task_names=["osm.Do"], task_list="osm", known_features=[]) is None
        assert (
            store.claim_task(task_names=["osm.Do"], task_list="osm", known_features=["after"])
            is not None
        )


class TestRunnerAdvertisement:
    def test_known_features_matches_the_gate(self):
        """A runner must advertise exactly what ast_features.py says it honors."""
        from facetwork.ast_features import KNOWN_AST_FEATURES
        from facetwork.runtime.base_runner import BaseRunner

        assert BaseRunner._known_ast_features(object()) == sorted(KNOWN_AST_FEATURES)

    def test_after_is_a_known_feature(self):
        from facetwork.ast_features import KNOWN_AST_FEATURES

        assert "after" in KNOWN_AST_FEATURES
