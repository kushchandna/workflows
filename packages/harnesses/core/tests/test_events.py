from datetime import UTC

import pytest
from pydantic import TypeAdapter

from workflows.harness import (
    Event,
    OtherEvent,
    ReasoningEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
    UsageEvent,
)

SAMPLES = [
    TextEvent(call_id="c", text="hi"),
    ReasoningEvent(call_id="c", text="thinking"),
    ToolCallEvent(
        call_id="c", tool_call_id="t1", name="Read", input={"file_path": "a.py"}
    ),
    ToolResultEvent(call_id="c", tool_call_id="t1", output="contents", is_error=True),
    UsageEvent(call_id="c", usage=Usage(input_tokens=5)),
    OtherEvent(call_id="c", raw={"kind": "unknown"}),
]


def test_event_type_tags():
    assert [e.type for e in SAMPLES] == [
        "text",
        "reasoning",
        "tool_call",
        "tool_result",
        "usage",
        "other",
    ]


@pytest.mark.parametrize("event", SAMPLES)
def test_event_json_round_trip(event):
    parsed = TypeAdapter(Event).validate_json(event.model_dump_json())
    assert type(parsed) is type(event)
    assert parsed == event


def test_timestamp_defaults_to_utc():
    assert TextEvent(call_id="c", text="x").timestamp.tzinfo == UTC


def test_raw_defaults_to_none():
    assert TextEvent(call_id="c", text="x").raw is None
