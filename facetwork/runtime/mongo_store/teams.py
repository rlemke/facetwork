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

"""Team CRUD operations mixin for MongoStore.

Membership is stored on the ``User`` record (``User.teams`` /
``User.default_team``), so a team's members are queried, and deleting a team
scrubs its name from every user's membership.
"""

from collections.abc import Sequence
from dataclasses import asdict, fields

from ..entities import TeamDefinition, User
from .base import _current_time_ms
from ._internals import _MixinBase

_TEAM_FIELDS = {f.name for f in fields(TeamDefinition)}


class TeamsMixin(_MixinBase):
    """Team CRUD; membership is derived from the users collection."""

    def get_team(self, name_or_uuid: str) -> TeamDefinition | None:
        """Get a team by its (unique) name or uuid."""
        doc = self._db.teams.find_one(
            {"$or": [{"name": name_or_uuid}, {"uuid": name_or_uuid}]}
        )
        return self._doc_to_team(doc) if doc else None

    def list_teams(self) -> Sequence[TeamDefinition]:
        """List all teams, ordered by name."""
        docs = self._db.teams.find().sort("name", 1)
        return [self._doc_to_team(doc) for doc in docs]

    def save_team(self, team: TeamDefinition) -> None:
        """Create or update a team (upsert by uuid); stamps timestamps."""
        now = _current_time_ms()
        if not team.created_at:
            team.created_at = now
        team.updated_at = now
        self._db.teams.replace_one({"uuid": team.uuid}, self._team_to_doc(team), upsert=True)

    def delete_team(self, name_or_uuid: str) -> dict:
        """Delete a team and scrub its name from all users' membership."""
        team = self.get_team(name_or_uuid)
        if team is None:
            return {"found": False, "deleted": False}
        members_updated = self._db.users.update_many(
            {"teams": team.name}, {"$pull": {"teams": team.name}}
        ).modified_count
        default_cleared = self._db.users.update_many(
            {"default_team": team.name}, {"$set": {"default_team": ""}}
        ).modified_count
        self._db.teams.delete_one({"uuid": team.uuid})
        return {
            "found": True,
            "deleted": True,
            "members_updated": members_updated,
            "default_cleared": default_cleared,
        }

    def set_team_members(self, team_name: str, emails: list[str]) -> dict:
        """Make exactly ``emails`` the members of ``team_name``.

        Adds the team to every listed user's membership and removes it from any
        user no longer listed — membership lives on the user record.
        """
        wanted = list(dict.fromkeys(emails))  # dedupe, preserve order
        added = self._db.users.update_many(
            {"email": {"$in": wanted}, "teams": {"$ne": team_name}},
            {"$push": {"teams": team_name}},
        ).modified_count
        removed = self._db.users.update_many(
            {"teams": team_name, "email": {"$nin": wanted}},
            {"$pull": {"teams": team_name}},
        ).modified_count
        return {"added": added, "removed": removed}

    def get_team_members(self, name: str, include_deleted: bool = False) -> Sequence[User]:
        """List the active users who belong to a team (queried from users)."""
        query: dict = {"teams": name}
        if not include_deleted:
            query["status"] = "active"
        docs = self._db.users.find(query).sort("email", 1)
        return [self._doc_to_user(doc) for doc in docs]

    # ── Serialization ────────────────────────────────────────────────────────

    def _team_to_doc(self, team: TeamDefinition) -> dict:
        return asdict(team)

    def _doc_to_team(self, doc: dict) -> TeamDefinition:
        return TeamDefinition(**{k: v for k, v in doc.items() if k in _TEAM_FIELDS})
