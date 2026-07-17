"""User and Team entity definitions.

Two-layer user model:

- ``UserDefinition`` (in :mod:`common`) is the lightweight, immutable snapshot
  (email / name / avatar) embedded on a runner (``user`` = who started the run,
  ``author`` = who authored the workflow) and on a flow (``publisher``).
- ``User`` (here) is the rich record stored in the ``users`` collection, keyed
  by ``email`` — first/last name, phone, team membership, default team, kind,
  and soft-delete status.

Team membership is recorded ON the user (``User.teams`` / ``User.default_team``)
so there is a single source of truth; a team's members are queried, not stored
on the team.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from .common import UserDefinition

# ── User kinds ───────────────────────────────────────────────────────────────
# Humans are ordinary, deletable users. The other kinds are special principals
# that always exist and cannot be deleted. The `deleted` user is the
# reassignment target when a real user is force-deleted.
KIND_HUMAN = "human"
KIND_SYSTEM = "system"
KIND_CLAUDE = "claude"
KIND_DELETED = "deleted"
KIND_ANONYMOUS = "anonymous"

SPECIAL_KINDS = frozenset({KIND_SYSTEM, KIND_CLAUDE, KIND_DELETED, KIND_ANONYMOUS})

# ── Soft-delete status ───────────────────────────────────────────────────────
STATUS_ACTIVE = "active"
STATUS_DELETED = "deleted"

# ── Rights ───────────────────────────────────────────────────────────────────
# Per-user capability grants, stored as a list of right strings on the user.
# The dashboard has no authentication (acting-as attributes runs), so rights
# gate DESTRUCTIVE actions only — they are guard rails, not a security
# boundary. Grant via the Users admin page or by setting ``rights`` on the
# user document.
RIGHT_DELETE_RUNS = "delete_runs"

KNOWN_RIGHTS: tuple[str, ...] = (RIGHT_DELETE_RUNS,)

# Stable email of the special "deleted" principal that dangling references are
# reassigned to on force-delete.
DELETED_USER_EMAIL = "deleted@facetwork.local"


@dataclass
class User:
    """A person (or special principal) who authors and runs workflows.

    Stored in the ``users`` collection, keyed by ``email``.
    """

    email: str
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    title: str = ""
    teams: list[str] = field(default_factory=list)
    default_team: str = ""
    kind: str = KIND_HUMAN
    status: str = STATUS_ACTIVE
    avatar: str = ""
    rights: list[str] = field(default_factory=list)
    password_hash: str = ""  # "scrypt$<salt-hex>$<hash-hex>"; "" = no password set
    created_at: int = 0  # ms since epoch
    updated_at: int = 0  # ms since epoch

    def has_right(self, right: str) -> bool:
        """True when this user holds the given right (explicit grants only)."""
        return right in (self.rights or [])

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)

    def set_password(self, password: str) -> None:
        """Hash and store ``password`` (stdlib scrypt, per-user random salt)."""
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
        self.password_hash = f"scrypt${salt.hex()}${digest.hex()}"

    def verify_password(self, password: str) -> bool:
        """Constant-time check of ``password`` against the stored hash."""
        try:
            scheme, salt_hex, hash_hex = self.password_hash.split("$", 2)
            if scheme != "scrypt":
                return False
            digest = hashlib.scrypt(
                password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1
            )
            return hmac.compare_digest(digest.hex(), hash_hex)
        except (ValueError, AttributeError):
            return False

    @property
    def display_name(self) -> str:
        """Human-friendly name: "First Last" if set, else the email."""
        name = f"{self.first_name} {self.last_name}".strip()
        return name or self.email

    @property
    def is_special(self) -> bool:
        """True for the system / claude / deleted / anonymous principals."""
        return self.kind in SPECIAL_KINDS

    @property
    def is_deleted(self) -> bool:
        """True once the user has been soft-deleted."""
        return self.status == STATUS_DELETED

    def to_user_definition(self) -> UserDefinition:
        """Project to the lightweight embedded snapshot stored on runs/flows."""
        return UserDefinition(email=self.email, name=self.display_name, avatar=self.avatar)


@dataclass
class TeamDefinition:
    """A named group of users.

    Stored in the ``teams`` collection. Membership lives on the ``User`` record
    (``User.teams`` / ``User.default_team``), so a team's members are queried —
    not stored here — which keeps one source of truth.
    """

    uuid: str
    name: str  # unique
    description: str = ""
    leader_email: str = ""  # contact User for the team's purpose
    created_by: str = ""  # email of the user who created the team
    created_at: int = 0  # ms since epoch
    updated_at: int = 0  # ms since epoch


def special_users() -> list[User]:
    """The four built-in principals seeded into every database.

    ``system`` (runtime-internal work), ``claude`` (LLM-authored/run work),
    ``deleted`` (reassignment target for force-deleted users), and
    ``anonymous`` (runs with no identified user).
    """
    return [
        User(email="system@facetwork.local", first_name="System", kind=KIND_SYSTEM),
        User(email="claude@facetwork.local", first_name="Claude", kind=KIND_CLAUDE),
        User(
            email=DELETED_USER_EMAIL,
            first_name="Deleted",
            last_name="User",
            kind=KIND_DELETED,
        ),
        User(email="anonymous@facetwork.local", first_name="Anonymous", kind=KIND_ANONYMOUS),
    ]
