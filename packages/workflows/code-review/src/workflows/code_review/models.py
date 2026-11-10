from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from workflows.harness import Event


class ReviewPass(StrEnum):
    """The review passes. The values double as folder names in the work folder."""

    PER_FILE = "per-file"
    OVERALL = "overall"
    TEST_COVERAGE = "test-coverage"
    CODE_PATTERNS = "code-patterns"
    CONSOLIDATION = "consolidation"


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


class PassCoverage(BaseModel):
    review_pass: ReviewPass
    state: Literal["ran", "skipped", "failed"]
    reason: str | None = None


class DroppedThreadAction(BaseModel):
    thread_id: str
    action: Literal["resolve", "reopen"]
    review_pass: ReviewPass
    reason: str


class Coverage(BaseModel):
    files: list[FileCoverage] = []
    passes: list[PassCoverage] = []
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


class ReviewSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    lines_per_batch: int = Field(default=1000, ge=1)
    files_per_batch: int = Field(default=10, ge=1)
    directory_break_min_fill: float = Field(default=0.5, ge=0, le=1)
    max_parallel_calls: int = Field(default=4, ge=1)
    harness_timeout_seconds: float | None = Field(default=1800, gt=0)
    work_folder: Path | None = None


class ReviewEvent(BaseModel):
    """A harness event, labeled with the call it came from."""

    review_pass: ReviewPass
    batch: int | None
    attempt: int
    event: Event
