import asyncio
import os
import sys
import time

import pytest

from workflows_agent import AgentRequest, AgentTimeoutError
from workflows_agent_claude_code import ClaudeCodeAgent
from workflows_agent_claude_code.process import stop_process_group


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_dead(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.fixture
def child_pid_file(tmp_path, monkeypatch, fixture_path):
    path = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CLAUDE_CHILD_PID_FILE", str(path))
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(fixture_path("success.jsonl")))
    return path


async def test_children_killed_after_normal_exit(tmp_path, fake_claude, child_pid_file):
    # The child holds claude's stdout open; the call must not wait for it.
    start = time.monotonic()
    await ClaudeCodeAgent("haiku", fake_claude).run(
        AgentRequest(prompt="p", working_folder=tmp_path, timeout_seconds=20)
    )
    assert time.monotonic() - start < 10
    assert await wait_dead(int(child_pid_file.read_text()))


async def test_timeout_kills_process_group(
    tmp_path, fake_claude, child_pid_file, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    with pytest.raises(AgentTimeoutError):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path, timeout_seconds=1)
        )
    assert await wait_dead(int(child_pid_file.read_text()))


async def test_cancel_kills_process_group(
    tmp_path, fake_claude, child_pid_file, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    task = asyncio.create_task(
        ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path)
        )
    )
    while not child_pid_file.exists():
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await wait_dead(int(child_pid_file.read_text()))


async def test_on_event_error_kills_and_propagates(
    tmp_path, fake_claude, child_pid_file, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")

    def boom(e):
        raise RuntimeError("sink broke")

    with pytest.raises(RuntimeError, match="sink broke"):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path), on_event=boom
        )
    assert await wait_dead(int(child_pid_file.read_text()))


async def test_sigkill_after_grace_when_term_ignored():
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        start_new_session=True,
    )
    await asyncio.sleep(0.2)
    start = time.monotonic()
    await stop_process_group(proc, grace_seconds=0.3)
    assert proc.returncode is not None
    assert time.monotonic() - start < 3


async def test_second_cancel_still_kills(
    tmp_path, fake_claude, child_pid_file, record, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    monkeypatch.setenv("FAKE_CLAUDE_IGNORE_TERM", "1")
    task = asyncio.create_task(
        ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path)
        )
    )
    while not child_pid_file.exists() or not child_pid_file.read_text():
        await asyncio.sleep(0.05)
    claude_pid = record()["pid"]
    task.cancel()
    await asyncio.sleep(0.3)  # inside the SIGTERM grace period
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await wait_dead(claude_pid)
