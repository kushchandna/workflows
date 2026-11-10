from abc import ABC, abstractmethod

from workflows.harness.events import EventHandler
from workflows.harness.models import HarnessRequest, HarnessResult


class Harness(ABC):
    """A tool, such as Claude Code, that runs a model as a coding agent.

    The end product builds a concrete harness (model and tool-specific options)
    and passes it to a workflow. Every call runs in a mode that never waits for
    a human to approve anything.
    """

    @abstractmethod
    async def run(
        self, request: HarnessRequest, on_event: EventHandler | None = None
    ) -> HarnessResult:
        """Run one call and return its result.

        - The call never waits for a human to approve anything.
        - One harness can run many calls at the same time.
        - Every event goes to ``on_event`` in order, each carrying the
          result's ``call_id``.
        - If ``on_event`` raises, the call fails with that exception.
        - A call that can't finish, including a timeout, raises
          ``HarnessError`` (``HarnessTimeoutError`` for a timeout) and never
          returns a result.
        - Cancelling the call's task stops the harness's process.
        """
