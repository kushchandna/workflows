"""Turn ``claude`` stream-json output lines into agent events."""

import json
from typing import Any

from workflows.agent import (
    Event,
    OtherEvent,
    ReasoningEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
    UsageEvent,
)


def events_from_line(line: str, call_id: str) -> list[Event]:
    """The events for one output line, each carrying the parsed line as ``raw``.

    A ``result`` line gives exactly one ``UsageEvent`` whose ``raw`` is the
    result object. A line nothing else maps to gives an ``OtherEvent``, so no
    line is lost from the log.
    """
    try:
        data = json.loads(line)
    except ValueError:
        return [OtherEvent(call_id=call_id, raw=line)]
    if not isinstance(data, dict):
        return [OtherEvent(call_id=call_id, raw=line)]

    kind = data.get("type")
    events: list[Event] = []
    if kind == "assistant":
        for block in _blocks(data):
            events += _assistant_block(block, call_id, data)
    elif kind == "user":
        for block in _blocks(data):
            events += _user_block(block, call_id, data)
    elif kind == "result":
        events.append(
            UsageEvent(call_id=call_id, usage=usage_from_result(data), raw=data)
        )
    return events or [OtherEvent(call_id=call_id, raw=data)]


def usage_from_result(result: dict[str, Any]) -> Usage:
    """Totals from a ``result`` line. Input counts cached input too."""
    usage = result.get("usage") or {}
    input_parts: list[int] = [
        usage[key]
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        if usage.get(key) is not None
    ]
    return Usage(
        input_tokens=sum(input_parts) if input_parts else None,
        output_tokens=usage.get("output_tokens"),
        cost_usd=result.get("total_cost_usd"),
    )


def _blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    content = (data.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _assistant_block(block: dict[str, Any], call_id: str, raw: Any) -> list[Event]:
    kind = block.get("type")
    if kind == "text":
        return [TextEvent(call_id=call_id, text=block.get("text", ""), raw=raw)]
    if kind == "thinking" and block.get("thinking"):
        return [ReasoningEvent(call_id=call_id, text=block["thinking"], raw=raw)]
    if kind == "tool_use":
        return [
            ToolCallEvent(
                call_id=call_id,
                tool_call_id=block.get("id", ""),
                name=block.get("name", ""),
                input=block.get("input") or {},
                raw=raw,
            )
        ]
    return []


def _user_block(block: dict[str, Any], call_id: str, raw: Any) -> list[Event]:
    if block.get("type") != "tool_result":
        return []
    content = block.get("content")
    if isinstance(content, list):
        output = "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        output = content if isinstance(content, str) else ""
    return [
        ToolResultEvent(
            call_id=call_id,
            tool_call_id=block.get("tool_use_id", ""),
            output=output,
            is_error=bool(block.get("is_error", False)),
            raw=raw,
        )
    ]
