"""The result files harnesses write, and the readers that check them."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChangeDone(BaseModel):
    outcome: Literal["done"]
    title: NonEmpty
    description: NonEmpty


class ChangeNeedsHuman(BaseModel):
    outcome: Literal["needs_human"]
    reason: NonEmpty


ChangeReport = Annotated[ChangeDone | ChangeNeedsHuman, Field(discriminator="outcome")]


class ReviewReport(BaseModel):
    verdict: Literal["pass", "changes_needed", "inconclusive"]
    comments: list[NonEmpty] = []
    reason: str | None = None

    @model_validator(mode="after")
    def _check_verdict(self) -> "ReviewReport":
        if self.verdict == "changes_needed" and not self.comments:
            raise ValueError("changes_needed needs at least one comment")
        if self.verdict == "inconclusive" and not (self.reason or "").strip():
            raise ValueError("inconclusive needs a reason")
        return self


_CHANGE_REPORT = TypeAdapter(ChangeReport)


def read_change_report(path: Path) -> ChangeDone | ChangeNeedsHuman | None:
    """The parsed change result, or ``None`` if it is missing, not JSON, or off-schema."""
    try:
        return _CHANGE_REPORT.validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):  # ValidationError is a ValueError
        return None


def read_review_report(path: Path) -> ReviewReport | None:
    """The parsed review result, or ``None`` if it is missing, not JSON, or off-schema."""
    try:
        return ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
