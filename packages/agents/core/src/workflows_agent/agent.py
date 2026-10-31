from abc import ABC, abstractmethod

from workflows_agent.events import EventHandler
from workflows_agent.models import AgentRequest, AgentResult


class Agent(ABC):
    """An AI coding agent that workflows run through.

    The end product builds a concrete agent (model, permission mode and
    tool-specific options) and passes it to a workflow.
    """

    @abstractmethod
    async def run(
        self, request: AgentRequest, on_event: EventHandler | None = None
    ) -> AgentResult:
        """Run one call and return its result.

        - The call never waits for a human to approve anything.
        - One agent can run many calls at the same time.
        - Every event goes to ``on_event`` in order, each carrying the
          result's ``call_id``.
        - If ``on_event`` raises, the call fails with that exception.
        - A call that can't finish, including a timeout, raises
          ``AgentError`` (``AgentTimeoutError`` for a timeout) and never
          returns a result.
        - Cancelling the call's task stops the agent's process.
        """
