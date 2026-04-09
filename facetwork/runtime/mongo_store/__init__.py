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

"""MongoDB implementation of PersistenceAPI.

This package provides a MongoDB-backed persistence layer for the FFL runtime.
It requires the pymongo package to be installed.
"""

from ..persistence import PersistenceAPI
from .base import BaseMixin, _compute_next_retry_after, _current_time_ms
from .repair import RepairMixin
from .runners import RunnerMixin
from .servers import ServerMixin
from .steps import StepMixin
from .tasks import TaskMixin
from .workflows import WorkflowMixin


class MongoStore(
    BaseMixin,
    StepMixin,
    TaskMixin,
    RunnerMixin,
    WorkflowMixin,
    RepairMixin,
    ServerMixin,
    PersistenceAPI,
):
    """MongoDB implementation of the persistence API.

    Provides full persistence to MongoDB with proper indexes,
    transactions, and serialization.

    Usage:
        store = MongoStore("mongodb://afl-mongodb:27017", "afl")
        store.get_step(step_id)

        # Or create from an FFLConfig / MongoDBConfig:
        from facetwork.config import load_config
        config = load_config()
        store = MongoStore.from_config(config.mongodb)
    """

    pass


__all__ = [
    "MongoStore",
    "_current_time_ms",
    "_compute_next_retry_after",
]
