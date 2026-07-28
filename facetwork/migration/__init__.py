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

"""FFL source migrations."""

from .after_migrator import AfterMigrationResult
from .after_migrator import migrate_source as migrate_after_source
from .relative_scope_migrator import MigrationResult, migrate_source

__all__ = [
    "AfterMigrationResult",
    "MigrationResult",
    "migrate_after_source",
    "migrate_source",
]
