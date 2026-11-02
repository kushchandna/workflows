from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workflows.agent import Event


class ChangeStage(StrEnum):
    """The stages of an attempt. The values double as folder names in the work folder."""

    CHANGE = "change"
    COMMIT_CHECK = "commit-check"
    CHECKS = "checks"
    REVIEW = "review"


class ChangeInput(BaseModel):
    """What to change. ``change()`` rejects a blank instruction with ``ChangeInputError``."""

    codebase: Path
    instruction: str
    commit_instructions: str | None = None
    checks: list[str] = []
    review: bool = False
    max_attempts: int = Field(default=3, ge=1)
    reset_on_failure: bool = True


class ChangeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_timeout_seconds: float | None = Field(default=1800, gt=0)
    check_timeout_seconds: float | None = Field(default=1800, gt=0)
    check_output_chars: int = Field(default=20000, ge=0)
    work_folder: Path | None = None


class Commit(BaseModel):
    id: str
    message: str


class CheckRun(BaseModel):
    command: str
    exit_code: int | None
    timed_out: bool = False
    output: str
    truncated: bool = False


class ReviewOutcome(BaseModel):
    verdict: Literal["pass", "changes_needed", "inconclusive"]
    comments: list[str] = []
    reason: str | None = None


class Attempt(BaseModel):
    number: int
    ended_at: ChangeStage | None = None
    reason: str | None = None
    checks: list[CheckRun] = []
    # What the checks left in the work tree (uncommitted and untracked paths), if anything.
    left_changes: str | None = None
    review: ReviewOutcome | None = None


class ChangeResult(BaseModel):
    status: Literal["completed", "needs_human", "inconclusive", "failed"]
    reason: str | None = None
    base_commit: str
    commits: list[Commit] = []
    title: str | None = None
    description: str | None = None
    attempts: list[Attempt] = []


class ChangeEvent(BaseModel):
    """An agent event, labeled with the call it came from."""

    stage: ChangeStage
    attempt: int
    call: int
    event: Event
