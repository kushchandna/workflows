import asyncio
from pathlib import Path

import pytest

from workflows_agent import (
    AgentError,
    AgentRequest,
    Event,
    OtherEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
    UsageEvent,
)
from workflows_agent_claude_code import ClaudeCodeAgent


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
    result = await ClaudeCodeAgent("haiku", fake_claude).run(
        AgentRequest(prompt="p", working_folder=tmp_path), on_event=seen.append
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
    await ClaudeCodeAgent("opus", fake_claude).run(
        AgentRequest(
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
    await ClaudeCodeAgent("haiku", fake_claude).run(
        AgentRequest(prompt=prompt, working_folder=tmp_path)
    )
    assert record()["stdin"] == prompt


async def test_is_error_result_raises_with_details(tmp_path, fake_claude, use_fixture):
    use_fixture("error.jsonl", exit_code=1, stderr="[claude-code:unrecognized_model]")
    with pytest.raises(AgentError, match="no-such-model") as info:
        await ClaudeCodeAgent("no-such-model", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path)
        )
    assert info.value.session_id == "sess-2"
    assert "unrecognized_model" in (info.value.stderr or "")


async def test_non_zero_exit_without_result(
    tmp_path, fake_claude, no_output, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")
    with pytest.raises(AgentError, match="exited with code 3"):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path)
        )


async def test_clean_exit_without_result(tmp_path, fake_claude, no_output):
    with pytest.raises(AgentError, match="without a result"):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path)
        )


async def test_early_exit_before_reading_stdin(
    tmp_path, fake_claude, no_output, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_SKIP_STDIN", "1")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "2")
    with pytest.raises(AgentError, match="exited with code 2"):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="x" * 1_000_000, working_folder=tmp_path)
        )


async def test_missing_executable(tmp_path):
    with pytest.raises(AgentError, match="not found"):
        await ClaudeCodeAgent("haiku", tmp_path / "nope").run(
            AgentRequest(prompt="p", working_folder=tmp_path)
        )


async def test_missing_working_folder(tmp_path, fake_claude):
    missing = tmp_path / "missing"
    with pytest.raises(AgentError, match="working folder does not exist") as info:
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=missing)
        )
    assert str(missing) in str(info.value)


async def test_parallel_calls_have_distinct_ids(tmp_path, fake_claude, use_fixture):
    use_fixture("success.jsonl")
    agent = ClaudeCodeAgent("haiku", fake_claude)
    results = await asyncio.gather(
        *(
            agent.run(AgentRequest(prompt="p", working_folder=tmp_path))
            for _ in range(4)
        )
    )
    assert len({r.call_id for r in results}) == 4
