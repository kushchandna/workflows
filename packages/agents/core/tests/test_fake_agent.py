import asyncio
from pathlib import Path

import pytest

from workflows.agent import (
    Agent,
    AgentError,
    AgentRequest,
    AgentTimeoutError,
    Event,
    TextEvent,
    ToolCallEvent,
)
from workflows.agent.testing import FakeAgent


def req(tmp_path: Path, **kw) -> AgentRequest:
    return AgentRequest(prompt="review", working_folder=tmp_path, **kw)


async def test_is_an_agent():
    assert isinstance(FakeAgent(), Agent)


async def test_returns_final_message_and_records_requests(tmp_path):
    agent = FakeAgent(respond=lambda r: f"got {r.prompt}")
    result = await agent.run(req(tmp_path))
    assert result.final_message == "got review"
    assert agent.requests == [req(tmp_path)]
    assert result.duration_seconds >= 0


async def test_default_respond_returns_empty_message(tmp_path):
    assert (await FakeAgent().run(req(tmp_path))).final_message == ""


async def test_async_respond_supported(tmp_path):
    async def respond(r):
        await asyncio.sleep(0)
        return "async"

    assert (
        await FakeAgent(respond=respond).run(req(tmp_path))
    ).final_message == "async"


async def test_respond_can_write_files(tmp_path):
    out = tmp_path / "out"
    out.mkdir()

    def respond(r):
        (r.extra_folders[0] / "a.json").write_text("{}")
        return "ok"

    await FakeAgent(respond=respond).run(req(tmp_path, extra_folders=[out]))
    assert (out / "a.json").read_text() == "{}"


async def test_respond_error_propagates(tmp_path):
    def respond(r):
        raise AgentError("crashed", stderr="trace")

    with pytest.raises(AgentError, match="crashed"):
        await FakeAgent(respond=respond).run(req(tmp_path))


async def test_emits_scripted_events_with_call_id(tmp_path):
    scripted = [
        TextEvent(call_id="x", text="hi"),
        ToolCallEvent(call_id="x", tool_call_id="t", name="Read", input={}),
    ]
    seen: list[Event] = []
    result = await FakeAgent(events=scripted).run(req(tmp_path), on_event=seen.append)
    assert [e.call_id for e in seen] == [result.call_id] * 2
    assert [e.model_copy(update={"call_id": "x"}) for e in seen] == scripted


async def test_on_event_error_fails_call(tmp_path):
    def boom(e):
        raise RuntimeError("sink broke")

    agent = FakeAgent(events=[TextEvent(call_id="x", text="hi")])
    with pytest.raises(RuntimeError, match="sink broke"):
        await agent.run(req(tmp_path), on_event=boom)


async def test_timeout_raises_agent_timeout_error(tmp_path):
    async def slow(r):
        await asyncio.sleep(1)
        return "late"

    with pytest.raises(AgentTimeoutError):
        await FakeAgent(respond=slow).run(req(tmp_path, timeout_seconds=0.01))


async def test_parallel_calls_have_distinct_ids(tmp_path):
    agent = FakeAgent(events=[TextEvent(call_id="x", text="hi")])
    seen: dict[str, list[Event]] = {}

    async def call():
        events: list[Event] = []
        result = await agent.run(req(tmp_path), on_event=events.append)
        seen[result.call_id] = events

    await asyncio.gather(*(call() for _ in range(5)))
    assert len(seen) == 5
    assert all(e.call_id == cid for cid, evs in seen.items() for e in evs)
