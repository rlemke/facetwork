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

"""User CRUD, soft/force delete, and reference reassignment mixin for MongoStore.

Soft delete (the default) marks ``status=deleted`` and leaves the record in
place so historical runs keep their author. Force delete removes the user
entirely: optionally deletes the user's runs/flows, reassigns any remaining
references to the special ``deleted`` principal, and drops the record (which
also drops the user's team membership, since membership lives on the record).
"""

from collections.abc import Sequence
from dataclasses import asdict, fields

from ..entities import User, UserDefinition
from ..entities.user import (
    DELETED_USER_EMAIL,
    KIND_HUMAN,
    STATUS_ACTIVE,
    STATUS_DELETED,
    special_users,
)
from ._internals import _MixinBase
from .base import _current_time_ms

_USER_FIELDS = {f.name for f in fields(User)}


class UsersMixin(_MixinBase):
    """User CRUD, soft/force delete, and dangling-reference reassignment."""

    # =========================================================================
    # CRUD
    # =========================================================================

    def get_user(self, email: str) -> User | None:
        """Get a user by email."""
        return self._find_decoded(self._db.users, {"email": email}, self._doc_to_user)

    def list_users(
        self, include_deleted: bool = False, include_special: bool = True
    ) -> Sequence[User]:
        """List users, ordered by email.

        By default returns active users (humans + special principals). Set
        ``include_deleted`` to also return soft-deleted users, and
        ``include_special=False`` to exclude the system/claude/deleted/anonymous
        principals.
        """
        query: dict = {}
        if not include_deleted:
            query["status"] = STATUS_ACTIVE
        if not include_special:
            query["kind"] = KIND_HUMAN
        docs = self._db.users.find(query).sort("email", 1)
        return [self._doc_to_user(doc) for doc in docs]

    def save_user(self, user: User) -> None:
        """Create or update a user (upsert by email); stamps timestamps."""
        now = _current_time_ms()
        if not user.created_at:
            user.created_at = now
        user.updated_at = now
        self._db.users.replace_one({"email": user.email}, self._user_to_doc(user), upsert=True)

    # =========================================================================
    # Deletion
    # =========================================================================

    def soft_delete_user(self, email: str) -> dict:
        """Mark a user deleted without removing the record.

        Refuses to delete special principals. The user's runs/flows keep their
        embedded author snapshot intact.
        """
        user = self.get_user(email)
        if user is None:
            return {"found": False, "deleted": False}
        if user.is_special:
            return {"found": True, "deleted": False, "reason": "special user"}
        self._db.users.update_one(
            {"email": email},
            {"$set": {"status": STATUS_DELETED, "updated_at": _current_time_ms()}},
        )
        return {"found": True, "deleted": True, "soft": True}

    def reassign_user_references(self, email: str, to_email: str = DELETED_USER_EMAIL) -> dict:
        """Repoint every embedded reference to ``email`` at ``to_email``.

        Covers a run's ``user`` (started_by) and ``author``, and a flow's
        ``publisher`` and ``ownership.owner``.
        """
        target = self.get_user(to_email)
        repl = (
            target.to_user_definition()
            if target
            else UserDefinition(email=to_email, name="Deleted User")
        )
        repl_doc = asdict(repl)
        return {
            "runner_user": self._db.runners.update_many(
                {"user.email": email}, {"$set": {"user": repl_doc}}
            ).modified_count,
            "runner_author": self._db.runners.update_many(
                {"author.email": email}, {"$set": {"author": repl_doc}}
            ).modified_count,
            "flow_publisher": self._db.flows.update_many(
                {"publisher.email": email}, {"$set": {"publisher": repl_doc}}
            ).modified_count,
            "flow_owner": self._db.flows.update_many(
                {"ownership.owner.email": email}, {"$set": {"ownership.owner": repl_doc}}
            ).modified_count,
        }

    def force_delete_user(self, email: str, delete_work: bool = False) -> dict:
        """Permanently remove a user.

        Refuses special principals. When ``delete_work`` is set, deletes the
        runs the user started or authored (cascading via ``delete_runner``) and
        the flows the user published. Remaining references are reassigned to the
        ``deleted`` principal, then the user record (and thus its team
        membership) is removed.
        """
        user = self.get_user(email)
        if user is None:
            return {"found": False, "deleted": False}
        if user.is_special:
            return {"found": True, "deleted": False, "reason": "special user"}

        result: dict = {"found": True, "deleted": True, "force": True}

        if delete_work:
            runner_ids = {
                doc["uuid"]
                for doc in self._db.runners.find(
                    {"$or": [{"user.email": email}, {"author.email": email}]}, {"uuid": 1}
                )
            }
            for rid in runner_ids:
                self.delete_runner(rid)
            result["deleted_runs"] = len(runner_ids)
            result["deleted_flows"] = self._db.flows.delete_many(
                {"publisher.email": email}
            ).deleted_count

        result["reassigned"] = self.reassign_user_references(email)
        self._db.users.delete_one({"email": email})
        return result

    # =========================================================================
    # Seeding
    # =========================================================================

    def ensure_special_users(self) -> None:
        """Idempotently seed the four built-in principals if absent."""
        for user in special_users():
            if self._db.users.find_one({"email": user.email}) is None:
                self.save_user(user)

    # =========================================================================
    # Serialization
    # =========================================================================

    def _user_to_doc(self, user: User) -> dict:
        return asdict(user)

    def _doc_to_user(self, doc: dict) -> User:
        return User(**{k: v for k, v in doc.items() if k in _USER_FIELDS})
