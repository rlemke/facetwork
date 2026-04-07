"""Log and step log entity definitions."""

from dataclasses import dataclass, field


class NoteType:
    """Log note type constants."""

    ERROR = "error"
    INFO = "info"
    WARNING = "warning"


class NoteOriginator:
    """Log note originator constants."""

    WORKFLOW = "workflow"
    AGENT = "agent"


class NoteImportance:
    """Log importance level constants."""

    HIGH = 1
    NORMAL = 5
    LOW = 10


@dataclass
class LogDefinition:
    """Audit and execution log entry.

    Stored in the `logs` collection.
    """

    uuid: str
    order: int
    runner_id: str
    step_id: str | None = None
    object_id: str = ""
    object_type: str = ""
    note_originator: str = NoteOriginator.WORKFLOW
    note_type: str = NoteType.INFO
    note_importance: int = NoteImportance.NORMAL
    message: str = ""
    state: str = ""
    line: int = 0
    file: str = ""
    details: dict = field(default_factory=dict)
    time: int = 0  # Log timestamp (ms)


class StepLogLevel:
    """Step log level constants."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class StepLogSource:
    """Step log source constants."""

    FRAMEWORK = "framework"
    HANDLER = "handler"


@dataclass
class StepLogEntry:
    """Step-level lifecycle log entry.

    Captures key milestones during event facet handler execution,
    such as task claimed, handler dispatching, handler completed,
    and handler-emitted messages.

    Stored in the ``step_logs`` collection.
    """

    uuid: str
    step_id: str
    workflow_id: str
    runner_id: str = ""
    facet_name: str = ""
    source: str = StepLogSource.FRAMEWORK
    level: str = StepLogLevel.INFO
    message: str = ""
    details: dict = field(default_factory=dict)
    time: int = 0
