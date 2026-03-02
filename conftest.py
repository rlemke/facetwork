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

"""Root pytest configuration for AFL tests."""


def _patch_mongomock_objectid():
    """Add ordering support to mongomock ObjectId for Python 3.14+.

    mongomock's ObjectId only defines __eq__/__ne__/__hash__ but not
    comparison operators.  Python 3.14 no longer falls back gracefully
    when __lt__ is missing, so sorted() on ObjectId values raises
    TypeError.  Patch in the missing operators so .sort("_id", …)
    works correctly.
    """
    try:
        from mongomock.object_id import ObjectId
    except ImportError:
        return

    if hasattr(ObjectId, "_cmp_patched"):
        return

    def _lt(self, other):
        if not isinstance(other, ObjectId):
            return NotImplemented
        return str(self._id) < str(other._id)

    def _le(self, other):
        if not isinstance(other, ObjectId):
            return NotImplemented
        return str(self._id) <= str(other._id)

    def _gt(self, other):
        if not isinstance(other, ObjectId):
            return NotImplemented
        return str(self._id) > str(other._id)

    def _ge(self, other):
        if not isinstance(other, ObjectId):
            return NotImplemented
        return str(self._id) >= str(other._id)

    ObjectId.__lt__ = _lt
    ObjectId.__le__ = _le
    ObjectId.__gt__ = _gt
    ObjectId.__ge__ = _ge
    ObjectId._cmp_patched = True


_patch_mongomock_objectid()


def pytest_addoption(parser):
    """Add custom command-line options."""
    parser.addoption(
        "--mongodb",
        action="store_true",
        default=False,
        help="Run MongoDB tests against a real server (uses AFL config for connection)",
    )
    parser.addoption(
        "--hdfs",
        action="store_true",
        default=False,
        help="Run HDFS integration tests against live containers (namenode on localhost:8020)",
    )
    parser.addoption(
        "--postgis",
        action="store_true",
        default=False,
        help="Run PostGIS integration tests against live containers (localhost:5432)",
    )
    parser.addoption(
        "--sra",
        action="store_true",
        default=False,
        help="Run SRA integration tests that download real FASTQ data from ENA",
    )
