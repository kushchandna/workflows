"""A fake harness for workflow tests."""

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from uuid import uuid4

from workflows.harness.errors import HarnessTimeoutError
from workflows.harness.events import Event, EventHandler
from workflows.harness.harness import Harness
from workflows.harness.models import HarnessRequest, HarnessResult

Respond = Callable[
    [HarnessRequest], str | HarnessResult | Awaitable[str | HarnessResult]
]


class FakeHarness(Harness):
    """Runs calls through a test-supplied ``respond`` function.

    ``respond`` gets each request, may write files or raise, and returns the
    final message or a full ``HarnessResult`` (its ``call_id`` and
    ``duration_seconds`` are replaced by the call's own). ``events`` are emitted to ``on_event`` before it runs, with
    their ``call_id`` replaced by the call's own.
    """

    def __init__(
        self, respond: Respond | None = None, events: Sequence[Event] = ()
    ) -> None:
        self.requests: list[HarnessRequest] = []
        self._respond = respond
        self._events = list(events)

    async def run(
        self, request: HarnessRequest, on_event: EventHandler | None = None
    ) -> HarnessResult:
        self.requests.append(request)
        call_id = uuid4().hex
        start = time.monotonic()
        timeout = asyncio.timeout(request.timeout_seconds)
        try:
            async with timeout:
                reply = await self._call(request, call_id, on_event)
        except TimeoutError as e:
            if not timeout.expired():
                raise
            raise HarnessTimeoutError(
                f"call timed out after {request.timeout_seconds} seconds"
            ) from e
        duration = time.monotonic() - start
        if isinstance(reply, HarnessResult):
            return reply.model_copy(
                update={"call_id": call_id, "duration_seconds": duration}
            )
        return HarnessResult(
            call_id=call_id, final_message=reply, duration_seconds=duration
        )

    async def _call(
        self, request: HarnessRequest, call_id: str, on_event: EventHandler | None
    ) -> str | HarnessResult:
        if on_event is not None:
            for event in self._events:
                on_event(event.model_copy(update={"call_id": call_id}))
        if self._respond is None:
            return ""
        message = self._respond(request)
        if inspect.isawaitable(message):
            message = await message
        return message
