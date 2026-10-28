import re
from dataclasses import dataclass, field
from datetime import date

VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+){0,2}$")


def release_type_for(version: str) -> str:
    """x.y / x.y.0 is a major (GA) release; anything with a non-zero patch is maintenance."""
    parts = version.split(".")
    return "maintenance" if any(p != "0" for p in parts[2:]) else "major"


@dataclass
class ReleaseRequest:
    product: str
    version: str
    ga_date: date | None = None
    platforms: list[str] | None = None  # None = every platform the product ships on
    release_type: str | None = None  # inferred from version when not given
    epic_key: str | None = None  # attach to this existing epic instead of searching
    qe_matrix: dict[str, str] = field(default_factory=dict)  # platform -> SGW/dataset versions

    def __post_init__(self):
        if not VERSION_RE.match(self.version):
            raise ValueError(f"Invalid version '{self.version}' (expected e.g. 4.1.3)")
        if self.release_type is None:
            self.release_type = release_type_for(self.version)


@dataclass
class PlannedIssue:
    task_id: str | None  # None for the epic
    summary: str
    description: str
    labels: list[str]
    assignee_id: str | None = None
    owner_function: str | None = None
    due_date: date | None = None
    existing_key: str | None = None  # already in Jira -> nothing to create

    @property
    def is_new(self) -> bool:
        return self.existing_key is None


@dataclass
class ReleasePlan:
    request: ReleaseRequest
    epic: PlannedIssue
    tasks: list[PlannedIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False  # e.g. duplicate epics found; refuse to write

    @property
    def to_create(self) -> list[PlannedIssue]:
        return [i for i in [self.epic, *self.tasks] if i.is_new]
