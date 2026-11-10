import asyncio
from pathlib import Path

import pytest

from workflows.harness import (
    Event,
    HarnessError,
    HarnessRequest,
    HarnessTimeoutError,
    OtherEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
    UsageEvent,
)
from workflows.harness_claude_code import ClaudeCodeHarness


@pytest.fixture
def use_fixture(monkeypatch, fixture_path):
    def use(name, exit_code=0, stderr=""):
        monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(fixture_path(name)))
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))
        monkeypatch.setenv("FAKE_CLAUDE_STDERR", stderr)

    return use


@pytest.fixture
def no_output(tmp_path, monkeypatch):
    (tmp_path / "empty.jsonl").write_text("")
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(tmp_path / "empty.jsonl"))


async def test_success_events_and_result(tmp_path, fake_claude, use_fixture):
    use_fixture("success.jsonl")
    seen: list[Event] = []
    result = await ClaudeCodeHarness("haiku", fake_claude).run(
        HarnessRequest(prompt="p", working_folder=tmp_path), on_event=seen.append
    )
    assert [type(e) for e in seen] == [
        OtherEvent,
        ToolCallEvent,
        ToolResultEvent,
        TextEvent,
        UsageEvent,
        OtherEvent,
    ]
    assert all(e.call_id == result.call_id for e in seen)
    assert result.final_message == "hello probe"
    assert result.session_id == "sess-1"
    assert result.usage == Usage(input_tokens=58034, output_tokens=234, cost_usd=0.0045)
    assert result.duration_seconds >= 0


async def test_passes_prompt_cwd_flags_and_system_prompt(
    tmp_path, fake_claude, use_fixture, record
):
    use_fixture("success.jsonl")
    extra = tmp_path / "out"
    extra.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    await ClaudeCodeHarness("opus", fake_claude).run(
        HarnessRequest(
            prompt="review this",
            working_folder=work,
            extra_folders=[extra],
            system_prompt="be strict",
        )
    )
    rec = record()
    assert rec["stdin"] == "review this"
    assert rec["cwd"] == str(work)
    assert rec["system_prompt"] == "be strict"
    assert rec["argv"][:8] == [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "opus",
        "--permission-mode",
        "bypassPermissions",
    ]
    assert rec["argv"][8:10] == ["--add-dir", str(extra)]
    sp_file = rec["argv"][rec["argv"].index("--append-system-prompt-file") + 1]
    assert not Path(sp_file).exists()  # removed after the call


async def test_large_prompt_goes_through_stdin(
    tmp_path, fake_claude, use_fixture, record
):
    use_fixture("success.jsonl")
    prompt = "x" * 300_000
    await ClaudeCodeHarness("haiku", fake_claude).run(
        HarnessRequest(prompt=prompt, working_folder=tmp_path)
    )
    assert record()["stdin"] == prompt


async def test_is_error_result_raises_with_details(tmp_path, fake_claude, use_fixture):
    use_fixture("error.jsonl", exit_code=1, stderr="[claude-code:unrecognized_model]")
    with pytest.raises(HarnessError, match="no-such-model") as info:
        await ClaudeCodeHarness("no-such-model", fake_claude).run(
            HarnessRequest(prompt="p", working_folder=tmp_path)
        )
    assert info.value.session_id == "sess-2"
    assert "unrecognized_model" in (info.value.stderr or "")


async def test_non_zero_exit_without_result(
    tmp_path, fake_claude, no_output, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")
    with pytest.raises(HarnessError, match="exited with code 3"):
        await ClaudeCodeHarness("haiku", fake_claude).run(
            HarnessRequest(prompt="p", working_folder=tmp_path)
        )


async def test_clean_exit_without_result(tmp_path, fake_claude, no_output):
    with pytest.raises(HarnessError, match="without a result"):
        await ClaudeCodeHarness("haiku", fake_claude).run(
            HarnessRequest(prompt="p", working_folder=tmp_path)
        )


async def test_early_exit_before_reading_stdin(
    tmp_path, fake_claude, no_output, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SKIP_STDIN", "1")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "2")
    with pytest.raises(HarnessError, match="exited with code 2"):
        await ClaudeCodeHarness("haiku", fake_claude).run(
            HarnessRequest(prompt="x" * 1_000_000, working_folder=tmp_path)
        )


async def test_missing_executable(tmp_path):
    with pytest.raises(HarnessError, match="not found"):
        await ClaudeCodeHarness("haiku", tmp_path / "nope").run(
            HarnessRequest(prompt="p", working_folder=tmp_path)
        )


async def test_missing_working_folder(tmp_path, fake_claude):
    missing = tmp_path / "missing"
    with pytest.raises(HarnessError, match="working folder does not exist") as info:
        await ClaudeCodeHarness("haiku", fake_claude).run(
            HarnessRequest(prompt="p", working_folder=missing)
        )
    assert str(missing) in str(info.value)


async def test_parallel_calls_have_distinct_ids(tmp_path, fake_claude, use_fixture):
    use_fixture("success.jsonl")
    harness = ClaudeCodeHarness("haiku", fake_claude)
    results = await asyncio.gather(
        *(
            harness.run(HarnessRequest(prompt="p", working_folder=tmp_path))
            for _ in range(4)
        )
    )
    assert len({r.call_id for r in results}) == 4


async def test_executable_that_cannot_be_run(tmp_path):
    not_runnable = tmp_path / "claude"
    not_runnable.write_text("no shebang here\n")
    not_runnable.chmod(0o755)
    with pytest.raises(HarnessError, match="not runnable"):
        await ClaudeCodeHarness("haiku", not_runnable).run(
            HarnessRequest(prompt="p", working_folder=tmp_path)
        )


async def test_timeout_error_carries_stderr(
    tmp_path, fake_claude, no_output, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_STDERR", "overloaded, retrying")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    with pytest.raises(HarnessTimeoutError) as info:
        await ClaudeCodeHarness("haiku", fake_claude).run(
            HarnessRequest(prompt="p", working_folder=tmp_path, timeout_seconds=1)
        )
    assert "overloaded, retrying" in (info.value.stderr or "")


async def test_stdout_read_while_prompt_is_written(
    tmp_path, fake_claude, use_fixture, monkeypatch
):
    # claude fills the stdout pipe before reading stdin: both sides must not block.
    use_fixture("success.jsonl")
    monkeypatch.setenv("FAKE_CLAUDE_EARLY_STDOUT", "2000000")
    result = await ClaudeCodeHarness("haiku", fake_claude).run(
        HarnessRequest(
            prompt="x" * 1_000_000, working_folder=tmp_path, timeout_seconds=10
        )
    )
    assert result.final_message == "hello probe"
