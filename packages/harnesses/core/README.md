# workflows-harness

The generic interface that workflows use to run a harness: a tool, such as Claude Code, that
runs a model as a coding agent. A workflow depends on this package only; the end product
builds a concrete harness from an implementation package, such as
[`workflows-harness-claude-code`](../claude-code/README.md), and passes it in.

Import: `workflows.harness`

## Running a call

```python
from pathlib import Path

from workflows.harness import Harness, HarnessRequest


async def summarise(harness: Harness) -> str:
    result = await harness.run(
        HarnessRequest(prompt="Summarise README.md", working_folder=Path("/repo")),
        # Every action the harness takes, as it happens.
        on_event=print,
    )
    return result.final_message
```

`Harness.run(request, on_event=None)` is async, and one harness object can run many calls at
the same time. A call never waits for a human to approve anything: it either goes ahead or
fails.

**`HarnessRequest`**

| Field | Notes |
|---|---|
| `prompt` | Required. The task. |
| `working_folder` | Required. Where the harness starts and the code it works on. |
| `extra_folders` | Folders outside the working folder that the harness must be able to read and write. |
| `system_prompt` | Added to the tool's own system prompt. |
| `timeout_seconds` | When it runs out, the call is stopped and raises `HarnessTimeoutError`. |

**`HarnessResult`**: `call_id`, `final_message`, `session_id`, `duration_seconds` and `usage`
(input and output tokens and cost, each empty when the tool doesn't report it).

## The action log

`on_event` receives one event for every action, in order. Every event carries the call's id,
a timestamp and the tool's own `raw` event.

| Event | Holds |
|---|---|
| `TextEvent` | Text the model wrote. |
| `ReasoningEvent` | Reasoning text, when the tool exposes it. |
| `ToolCallEvent` | `tool_call_id`, the tool's own tool `name`, and its `input`. |
| `ToolResultEvent` | The `tool_call_id` it answers, the `output`, and `is_error`. |
| `UsageEvent` | Token counts and cost so far. |
| `OtherEvent` | Anything that fits none of the above, as raw data. |

`Event` is a discriminated union, so a stored log parses back with
`TypeAdapter(Event).validate_json(...)`. If `on_event` raises, the call fails with that error.

## Errors

A call that can't finish raises and never returns a result: `HarnessError` carries the tool's
`stderr` and the `session_id` when known, and `HarnessTimeoutError` is its subclass for a timeout.
Cancelling the task running a call stops the harness's process.

## Testing workflows

`workflows.harness.testing.FakeHarness` implements the interface for tests. Give it a `respond`
function that receives each request, may write files, and returns the final message (or a full
`HarnessResult`) or raises. It can also emit a scripted list of events.

```python
from workflows.harness.testing import FakeHarness

harness = FakeHarness(respond=lambda request: "done")
```

Workflow tests use only this fake, never a real harness.

## Not supported yet

Choosing tools per call, limits on what a harness may write, MCP servers, resuming a session,
structured output, and the model asking the user questions during a call.
