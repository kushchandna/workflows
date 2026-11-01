# workflows-agent

The generic interface that workflows use to run an AI coding agent. A workflow depends on
this package only; the end product builds a concrete agent from an implementation package,
such as [`workflows-agent-claude-code`](../claude-code/README.md), and passes it in.

Import: `workflows.agent`

## Running a call

```python
from pathlib import Path

from workflows.agent import Agent, AgentRequest


async def summarise(agent: Agent) -> str:
    result = await agent.run(
        AgentRequest(prompt="Summarise README.md", working_folder=Path("/repo")),
        # Every action the agent takes, as it happens.
        on_event=print,
    )
    return result.final_message
```

`Agent.run(request, on_event=None)` is async, and one agent object can run many calls at
the same time. A call never waits for a human to approve anything: it either goes ahead or
fails.

**`AgentRequest`**

| Field | Notes |
|---|---|
| `prompt` | Required. The task. |
| `working_folder` | Required. Where the agent starts and the code it works on. |
| `extra_folders` | Folders outside the working folder that the agent must be able to read and write. |
| `system_prompt` | Added to the tool's own system prompt. |
| `timeout_seconds` | When it runs out, the call is stopped and raises `AgentTimeoutError`. |

**`AgentResult`**: `call_id`, `final_message`, `session_id`, `duration_seconds` and `usage`
(input and output tokens and cost, each empty when the tool doesn't report it).

## The action log

`on_event` receives one event for every action, in order. Every event carries the call's id,
a timestamp and the tool's own `raw` event.

| Event | Holds |
|---|---|
| `TextEvent` | Text the agent wrote. |
| `ReasoningEvent` | Reasoning text, when the tool exposes it. |
| `ToolCallEvent` | `tool_call_id`, the tool's own tool `name`, and its `input`. |
| `ToolResultEvent` | The `tool_call_id` it answers, the `output`, and `is_error`. |
| `UsageEvent` | Token counts and cost so far. |
| `OtherEvent` | Anything that fits none of the above, as raw data. |

`Event` is a discriminated union, so a stored log parses back with
`TypeAdapter(Event).validate_json(...)`. If `on_event` raises, the call fails with that error.

## Errors

A call that can't finish raises and never returns a result: `AgentError` carries the tool's
`stderr` and the `session_id` when known, and `AgentTimeoutError` is its subclass for a timeout.
Cancelling the task running a call stops the agent's process.

## Testing workflows

`workflows.agent.testing.FakeAgent` implements the interface for tests. Give it a `respond`
function that receives each request, may write files, and returns the final message (or a full
`AgentResult`) or raises. It can also emit a scripted list of events.

```python
from workflows.agent.testing import FakeAgent

agent = FakeAgent(respond=lambda request: "done")
```

Workflow tests use only this fake, never a real agent.

## Not supported yet

Choosing tools per call, limits on what an agent may write, MCP servers, resuming a session,
structured output, and an agent asking the user questions during a call.
