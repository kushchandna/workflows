import asyncio
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from workflows.code_review import ReviewEvent
from workflows.code_review.calls import CallRunner, Step
from workflows.harness import Harness


class PromptInfo:
    """Reads the fixed lines of a prompt, like a harness would."""

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
        harness: Harness,
        work: Path,
        on_event: Callable[[ReviewEvent], None] | None = None,
        harnesses: Callable[[Step], Harness] | None = None,
    ) -> CallRunner:
        return CallRunner(
            harnesses=harnesses or (lambda step: harness),
            working_folder=work,
            work=work,
            timeout_seconds=None,
            semaphore=asyncio.Semaphore(4),
            on_event=on_event,
        )

    return make
