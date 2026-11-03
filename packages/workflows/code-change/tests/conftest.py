import re
from pathlib import Path

import pytest


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
