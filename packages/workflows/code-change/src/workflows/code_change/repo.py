from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from workflows.code_change.models import Commit

_MAX_LISTED = 10


def summarize(paths: list[str]) -> str | None:
    """The paths as one line, at most 10 of them, or ``None`` when there are none."""
    if not paths:
        return None
    listed = ", ".join(paths[:_MAX_LISTED])
    more = len(paths) - _MAX_LISTED
    return listed + (f" and {more} more" if more > 0 else "")


@dataclass(frozen=True)
class Dirty:
    """The uncommitted and untracked paths in the work tree. Git-ignored files don't count."""

    paths: list[str] = field(default_factory=list)  # all of them, in git's order
    untracked: list[str] = field(default_factory=list)  # new files, staged or not

    @property
    def tracked(self) -> list[str]:
        """The paths of tracked files that have changes."""
        new = set(self.untracked)
        return [p for p in self.paths if p not in new]

    def summary(self) -> str | None:
        return summarize(self.paths)


class Repository(Protocol):
    """What the run loop needs from the codebase's git repository."""

    @property
    def base(self) -> str:
        """The full id of HEAD when the run started."""
        ...

    @property
    def starting_branch(self) -> str | None:
        """The branch HEAD was on when the run started, or ``None`` if it was detached."""
        ...

    def head(self) -> str: ...

    def commit_problem(self, last_checked: str) -> str | None:
        """Why the harness's commits since ``last_checked`` are not acceptable, or ``None``."""
        ...

    def dirty_paths(self) -> Dirty:
        """What is uncommitted or untracked in the work tree."""
        ...

    def unchanged(self, head: str) -> bool:
        """Whether HEAD is ``head`` and the work tree is clean."""
        ...

    def write_diff(self, dest: Path) -> None:
        """Writes the whole change, from the base to HEAD, to ``dest``."""
        ...

    def commits(self) -> list[Commit]:
        """The commits from the base to HEAD, oldest first."""
        ...

    def reset(self) -> None:
        """Puts the work tree back on the starting branch at the base commit."""
        ...
