from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from workflows.harness import Event


class ReviewStage(StrEnum):
    """The fixed stages of every review. The values double as folder names."""

    PER_FILE = "per-file"
    OVERALL = "overall"
    CONSOLIDATION = "consolidation"


class ReviewAgent(BaseModel):
    """A reviewer with one focus, such as security. It reviews the whole change."""

    model_config = ConfigDict(frozen=True)

    # Doubles as a folder name in the work folder.
    name: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)
    instructions: str  # what to look for, and what to leave to others
    needs_codebase: bool = False  # skipped when only diffs are given

    @field_validator("instructions")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instructions must not be blank")
        return value


class LinesTarget(BaseModel):
    type: Literal["lines"] = "lines"
    path: str
    start_line: int
    end_line: int
    side: Literal["new", "old"] = "new"


class FileTarget(BaseModel):
    type: Literal["file"] = "file"
    path: str


class ChangeTarget(BaseModel):
    type: Literal["change"] = "change"


Target = Annotated[LinesTarget | FileTarget | ChangeTarget, Field(discriminator="type")]


class Comment(BaseModel):
    target: Target
    body: str
    reply_to: str | None = None


class ThreadAction(BaseModel):
    thread_id: str
    action: Literal["resolve", "reopen"]


class FileCoverage(BaseModel):
    path: str
    state: Literal["reviewed", "skipped", "unreviewed"]
    reason: str | None = None


class StageCoverage(BaseModel):
    stage: ReviewStage
    state: Literal["ran", "skipped", "failed"]
    reason: str | None = None


class AgentCoverage(BaseModel):
    agent: str
    state: Literal["ran", "skipped", "failed"]
    reason: str | None = None


class DroppedThreadAction(BaseModel):
    """A thread action that was not applied. Exactly one of ``stage`` and ``agent`` is set."""

    thread_id: str
    action: Literal["resolve", "reopen"]
    stage: ReviewStage | None = None
    agent: str | None = None
    reason: str

    @model_validator(mode="after")
    def check_one_source(self) -> Self:
        if (self.stage is None) == (self.agent is None):
            raise ValueError("set exactly one of stage and agent")
        return self


class Coverage(BaseModel):
    files: list[FileCoverage] = []
    stages: list[StageCoverage] = []
    agents: list[AgentCoverage] = []
    dropped_thread_actions: list[DroppedThreadAction] = []


class ReviewResult(BaseModel):
    status: Literal["approved", "changes_requested", "inconclusive"]
    comments: list[Comment] = []
    thread_actions: list[ThreadAction] = []
    coverage: Coverage = Field(default_factory=Coverage)


class Codebase(BaseModel):
    path: Path
    base_commit: str


class ReviewInput(BaseModel):
    """What to review. ``review()`` requires at least one of ``codebase`` and ``diffs``."""

    codebase: Codebase | None = None
    diffs: Path | None = None
    discussions: list[Path] = []
    description: str | None = None
    instructions: str | None = None
    change_author: str | None = None
    reviewer_name: str | None = None
    # Review agents to run: built-in names and custom agents. None runs DEFAULT_AGENTS.
    agents: list[str | ReviewAgent] | None = None


class ReviewSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    lines_per_batch: int = Field(default=1000, ge=1)
    files_per_batch: int = Field(default=10, ge=1)
    directory_break_min_fill: float = Field(default=0.5, ge=0, le=1)
    max_parallel_calls: int = Field(default=4, ge=1)
    harness_timeout_seconds: float | None = Field(default=1800, gt=0)
    work_folder: Path | None = None


class ReviewEvent(BaseModel):
    """A harness event, labeled with the call it came from.

    Exactly one of ``stage`` and ``agent`` is set.
    """

    stage: ReviewStage | None = None
    agent: str | None = None
    batch: int | None
    attempt: int
    event: Event

    @model_validator(mode="after")
    def check_one_source(self) -> Self:
        if (self.stage is None) == (self.agent is None):
            raise ValueError("set exactly one of stage and agent")
        return self
