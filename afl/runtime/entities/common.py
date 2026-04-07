"""Common supporting types used across entity definitions."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Parameter:
    """Runtime parameter for workflows and steps."""

    name: str
    value: Any
    type_hint: str = "Any"


@dataclass
class UserDefinition:
    """User information."""

    email: str
    name: str = ""
    avatar: str = ""


@dataclass
class Ownership:
    """Ownership information for flows."""

    owner: UserDefinition | None = None
    group: str = ""


@dataclass
class Classifier:
    """Flow classification."""

    category: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class SourceText:
    """Compiled source text."""

    name: str
    content: str
    language: str = "afl"


@dataclass
class InlineSource:
    """Inline source code."""

    content: str
    language: str = "afl"


@dataclass
class FileArtifact:
    """File artifact reference."""

    path: str
    checksum: str = ""


@dataclass
class JarArtifact:
    """JAR artifact reference."""

    group_id: str
    artifact_id: str
    version: str


@dataclass
class ResourceSource:
    """Resource source reference."""

    name: str
    path: str


@dataclass
class TextSource:
    """Text source reference."""

    name: str
    content: str


@dataclass
class ScriptCode:
    """Generated script code."""

    name: str
    code: str
    language: str = "python"


@dataclass
class WorkflowMetaData:
    """Workflow metadata."""

    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
