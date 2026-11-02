from pathlib import Path
from typing import Protocol

from workflows.code_change.models import Commit


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
        """Why the agent's commits since ``last_checked`` are not acceptable, or ``None``."""
        ...

    def dirty_summary(self) -> str | None:
        """The uncommitted and untracked paths as one line (capped), or ``None`` when clean."""
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
