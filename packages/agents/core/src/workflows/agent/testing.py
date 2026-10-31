"""A fake agent for workflow tests."""

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from uuid import uuid4

from workflows.agent.agent import Agent
from workflows.agent.errors import AgentTimeoutError
from workflows.agent.events import Event, EventHandler
from workflows.agent.models import AgentRequest, AgentResult

Respond = Callable[[AgentRequest], str | Awaitable[str]]


class FakeAgent(Agent):
    """Runs calls through a test-supplied ``respond`` function.

    ``respond`` gets each request, may write files or raise, and returns the
    final message. ``events`` are emitted to ``on_event`` before it runs, with
    their ``call_id`` replaced by the call's own.
    """

    def __init__(
        self, respond: Respond | None = None, events: Sequence[Event] = ()
    ) -> None:
        self.requests: list[AgentRequest] = []
        self._respond = respond
        self._events = list(events)

    async def run(
        self, request: AgentRequest, on_event: EventHandler | None = None
    ) -> AgentResult:
        self.requests.append(request)
        call_id = uuid4().hex
        start = time.monotonic()
        try:
            async with asyncio.timeout(request.timeout_seconds):
                final_message = await self._call(request, call_id, on_event)
        except TimeoutError as e:
            raise AgentTimeoutError(
                f"call timed out after {request.timeout_seconds} seconds"
            ) from e
        return AgentResult(
            call_id=call_id,
            final_message=final_message,
            duration_seconds=time.monotonic() - start,
        )

    async def _call(
        self, request: AgentRequest, call_id: str, on_event: EventHandler | None
    ) -> str:
        if on_event is not None:
            for event in self._events:
                on_event(event.model_copy(update={"call_id": call_id}))
        if self._respond is None:
            return ""
        message = self._respond(request)
        if inspect.isawaitable(message):
            message = await message
        return message
