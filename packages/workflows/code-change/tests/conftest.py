import re
from pathlib import Path

import pytest

from workflows.code_change import Commit
from workflows.code_change.repo import Dirty


class PromptInfo:
    """Reads the fixed lines of a prompt, like an agent would."""

    @staticmethod
    def result_file(prompt: str) -> Path:
        [path] = re.findall(r"^Result file: (.+)$", prompt, re.MULTILINE)
        return Path(path)

    @staticmethod
    def attempt(prompt: str) -> int:
        [n] = re.findall(r"^Attempt: (\d+) of \d+$", prompt, re.MULTILINE)
        return int(n)


@pytest.fixture
def prompt_info() -> type[PromptInfo]:
    return PromptInfo


class FakeRepository:
    """An in-memory ``Repository``, so the run loop is tested without git."""

    base = "base0"
    starting_branch: str | None = "main"

    def __init__(self) -> None:
        self._commits: list[Commit] = []
        self.dirty: list[str] = []  # untracked files
        self.dirty_tracked: list[str] = []  # tracked files with changes
        # Paths that count as dirty while the file exists, to stand in for what a check wrote.
        self.dirty_when_exists: list[Path] = []
        # The same for tracked files that a check modified.
        self.tracked_when_exists: list[Path] = []
        self.unchanged_result = True
        self.reset_calls = 0
        self.reset_error: Exception | None = None

    def head(self) -> str:
        return self._commits[-1].id if self._commits else self.base

    def commit(self, message: str = "fix") -> str:
        commit = Commit(id=f"c{len(self._commits) + 1}", message=message)
        self._commits.append(commit)
        return commit.id

    def dirty_paths(self) -> Dirty:
        untracked = self.dirty + [p.name for p in self.dirty_when_exists if p.exists()]
        tracked = self.dirty_tracked + [
            p.name for p in self.tracked_when_exists if p.exists()
        ]
        return Dirty(paths=tracked + untracked, untracked=untracked)

    def commit_problem(self, last_checked: str) -> str | None:
        if self.head() == last_checked:
            return "no new commit"
        dirty = self.dirty_paths().summary()
        return f"work left uncommitted: {dirty}" if dirty else None

    def unchanged(self, head: str) -> bool:
        return (
            self.unchanged_result
            and head == self.head()
            and not self.dirty_paths().paths
        )

    def write_diff(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("diff\n")

    def commits(self) -> list[Commit]:
        return list(self._commits)

    def reset(self) -> None:
        self.reset_calls += 1
        if self.reset_error is not None:
            raise self.reset_error
        self._commits.clear()
        self.dirty.clear()
        self.dirty_tracked.clear()


@pytest.fixture
def fake_repo() -> FakeRepository:
    return FakeRepository()
