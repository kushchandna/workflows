import re
from pathlib import Path

import pytest

from workflows.code_change import Commit


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

    def __init__(self) -> None:
        self._commits: list[Commit] = []
        self.dirty: list[str] = []
        self.unchanged_result = True
        self.reset_calls = 0
        self.reset_error: Exception | None = None

    def head(self) -> str:
        return self._commits[-1].id if self._commits else self.base

    def commit(self, message: str = "fix") -> str:
        commit = Commit(id=f"c{len(self._commits) + 1}", message=message)
        self._commits.append(commit)
        return commit.id

    def commit_problem(self, last_checked: str) -> str | None:
        if self.head() == last_checked:
            return "no new commit"
        if self.dirty:
            return f"work left uncommitted: {', '.join(self.dirty)}"
        return None

    def unchanged(self, head: str) -> bool:
        return self.unchanged_result

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


@pytest.fixture
def fake_repo() -> FakeRepository:
    return FakeRepository()
