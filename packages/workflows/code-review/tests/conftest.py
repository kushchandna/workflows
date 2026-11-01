import asyncio
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from workflows.agent import Agent
from workflows.code_review import ReviewEvent, ReviewPass
from workflows.code_review.calls import CallRunner


class PromptInfo:
    """Reads the fixed lines of a prompt, like an agent would."""

    @staticmethod
    def output_folder(prompt: str) -> Path:
        [folder] = re.findall(r"^Output folder: (.+)$", prompt, re.MULTILINE)
        return Path(folder)

    @staticmethod
    def slots(prompt: str) -> list[tuple[int, str]]:
        found = re.findall(r"^Slot (\d+): (.+)$", prompt, re.MULTILINE)
        return [(int(n), path) for n, path in found]

    @staticmethod
    def result_file(prompt: str) -> Path:
        [path] = re.findall(r"^Result file: (.+)$", prompt, re.MULTILINE)
        return Path(path)


@pytest.fixture
def prompt_info() -> type[PromptInfo]:
    return PromptInfo


@pytest.fixture
def runner_for() -> Callable[..., CallRunner]:
    def make(
        agent: Agent,
        work: Path,
        on_event: Callable[[ReviewEvent], None] | None = None,
        agents: Callable[[ReviewPass], Agent] | None = None,
    ) -> CallRunner:
        return CallRunner(
            agents=agents or (lambda review_pass: agent),
            working_folder=work,
            work=work,
            timeout_seconds=None,
            semaphore=asyncio.Semaphore(4),
            on_event=on_event,
        )

    return make
