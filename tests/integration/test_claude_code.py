"""Runs the real ``claude`` CLI. Needs a logged-in CLI and costs a few cents."""

import os

import pytest

from workflows.harness import Event, HarnessRequest, ToolCallEvent
from workflows.harness_claude_code import ClaudeCodeHarness

pytestmark = pytest.mark.skipif(
    os.environ.get("WORKFLOWS_INTEGRATION") != "1",
    reason="set WORKFLOWS_INTEGRATION=1 to run against the real claude CLI",
)


async def test_reads_file_and_writes_result(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "note.txt").write_text("hello integration\n")
    out = tmp_path / "out"
    out.mkdir()

    seen: list[Event] = []
    result = await ClaudeCodeHarness("haiku").run(
        HarnessRequest(
            prompt=f"Read note.txt, then write its exact contents to {out}/copy.txt. "
            "Reply with DONE.",
            working_folder=work,
            extra_folders=[out],
            timeout_seconds=180,
        ),
        on_event=seen.append,
    )

    assert (out / "copy.txt").read_text().strip() == "hello integration"
    assert result.session_id
    assert result.usage.input_tokens and result.usage.input_tokens > 0
    assert any(isinstance(e, ToolCallEvent) for e in seen)
