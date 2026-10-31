from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from workflows_agent.models import Usage


def _now() -> datetime:
    return datetime.now(UTC)


class BaseEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    timestamp: datetime = Field(default_factory=_now)
    raw: Any = None


class TextEvent(BaseEvent):
    type: Literal["text"] = "text"
    text: str


class ReasoningEvent(BaseEvent):
    type: Literal["reasoning"] = "reasoning"
    text: str


class ToolCallEvent(BaseEvent):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    name: str
    input: dict[str, Any]


class ToolResultEvent(BaseEvent):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    output: str
    is_error: bool = False


class UsageEvent(BaseEvent):
    type: Literal["usage"] = "usage"
    usage: Usage


class OtherEvent(BaseEvent):
    type: Literal["other"] = "other"


Event = Annotated[
    TextEvent
    | ReasoningEvent
    | ToolCallEvent
    | ToolResultEvent
    | UsageEvent
    | OtherEvent,
    Field(discriminator="type"),
]

EventHandler = Callable[[Event], None]
