import json

from workflows_agent import (
    OtherEvent,
    ReasoningEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
    UsageEvent,
)
from workflows_agent_claude_code.parse import events_from_line, usage_from_result


def line(obj) -> str:
    return json.dumps(obj)


def test_assistant_blocks_in_order_and_empty_thinking_dropped():
    data = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "thinking", "thinking": "plan"},
                {"type": "text", "text": "hi"},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"file_path": "a"},
                },
            ]
        },
    }
    events = events_from_line(line(data), "c")
    assert [type(e) for e in events] == [ReasoningEvent, TextEvent, ToolCallEvent]
    assert isinstance(events[0], ReasoningEvent) and events[0].text == "plan"
    assert isinstance(events[1], TextEvent) and events[1].text == "hi"
    call = events[2]
    assert isinstance(call, ToolCallEvent)
    assert (call.tool_call_id, call.name, call.input) == (
        "t1",
        "Read",
        {"file_path": "a"},
    )
    assert all(e.call_id == "c" and e.raw == data for e in events)


def test_tool_result_string_content():
    data = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "1\thello\n"}
            ]
        },
    }
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, ToolResultEvent)
    assert (e.tool_call_id, e.output, e.is_error) == ("t1", "1\thello\n", False)


def test_tool_result_list_content_keeps_text_parts():
    data = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "is_error": True,
                    "content": [
                        {"type": "text", "text": "one"},
                        {"type": "image", "source": {}},
                        {"type": "text", "text": "two"},
                    ],
                }
            ]
        },
    }
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, ToolResultEvent)
    assert (e.output, e.is_error) == ("one\ntwo", True)


def test_result_line_gives_one_usage_event_with_result_as_raw():
    data = {
        "type": "result",
        "is_error": False,
        "result": "done",
        "session_id": "s",
        "total_cost_usd": 0.0045,
        "usage": {
            "input_tokens": 4,
            "cache_creation_input_tokens": 20135,
            "cache_read_input_tokens": 37895,
            "output_tokens": 234,
        },
    }
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, UsageEvent) and e.raw == data
    assert e.usage == Usage(input_tokens=58034, output_tokens=234, cost_usd=0.0045)


def test_usage_from_result_missing_fields():
    assert usage_from_result({"type": "result"}) == Usage()
    assert (
        usage_from_result({"usage": {"cache_read_input_tokens": 7}}).input_tokens == 7
    )


def test_other_lines():
    data = {"type": "system", "subtype": "init", "session_id": "s"}
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, OtherEvent) and e.raw == data


def test_non_json_line_is_other_with_raw_text():
    [e] = events_from_line("not json {", "c")
    assert isinstance(e, OtherEvent) and e.raw == "not json {"


def test_json_that_is_not_an_object_is_other():
    [e] = events_from_line("[1, 2]", "c")
    assert isinstance(e, OtherEvent) and e.raw == "[1, 2]"


def test_line_with_no_mapped_blocks_is_other():
    data = {
        "type": "assistant",
        "message": {"content": [{"type": "thinking", "thinking": ""}]},
    }
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, OtherEvent) and e.raw == data
