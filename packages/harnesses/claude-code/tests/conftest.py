import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

TESTS = Path(__file__).resolve().parent


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    """An executable ``claude`` that runs fake_claude.py."""
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(
        f"#!{sys.executable}\n"
        "import runpy\n"
        f"runpy.run_path({str(TESTS / 'fake_claude.py')!r}, run_name='__main__')\n"
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[], dict[str, Any]]:
    """Make the fake record what it received, and return a reader for it."""
    path = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(path))
    return lambda: json.loads(path.read_text())


@pytest.fixture
def fixture_path() -> Callable[[str], Path]:
    return lambda name: TESTS / "fixtures" / name
