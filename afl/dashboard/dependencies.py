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

"""Dependency injection for the dashboard routes."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from afl.runtime.mongo_store import MongoStore


@lru_cache(maxsize=1)
def _get_store(config_path: str | None = None) -> MongoStore:
    """Create or return the singleton MongoStore instance."""
    from afl.config import load_config
    from afl.runtime.mongo_store import MongoStore

    config = load_config(config_path)
    print(f"Connecting to MongoDB: {config.mongodb.url}/{config.mongodb.database}")
    return MongoStore.from_config(config.mongodb)


def get_store(request: Request) -> MongoStore:
    """FastAPI dependency — returns the shared MongoStore."""
    config_path = getattr(request.app.state, "config_path", None)
    return _get_store(config_path)
