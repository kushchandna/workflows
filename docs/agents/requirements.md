# Agent Interface: Requirements

Date: 2026-11-01

## Purpose

A generic way for workflows to run an AI coding agent. A workflow uses only
this interface. The end product picks a concrete agent (Claude Code,
opencode, Codex), builds it, and passes it in.

- `workflows-agent` (core) defines the interface.
- `workflows-agent-claude-code`, `workflows-agent-opencode` and
  `workflows-agent-codex` each implement it for one tool.

## Building an agent

The end product builds an agent from one of the implementation packages. The
workflow never does this.

| Setting | Notes |
|---|---|
| **Model** | Required. The workflow never chooses a model. |
| **Permission mode** | `auto` or `bypass`. Every call made through this agent runs in this mode. |
| **Tool-specific options** | Whatever the tool needs to run, such as the path to its binary or extra environment variables. Each package defines its own. |

- The permission mode is set on the agent, not per call. Which mode is safe
  depends on where the end product runs (a throwaway container or a
  developer's machine), and workflows know nothing about deployment.
- A call never waits for a human to approve anything. Whatever the mode, the
  agent either goes ahead or fails.
- Each package maps the two modes to its tool's own settings and documents
  the mapping. Where a tool has no direct match for `auto`, the package uses
  the closest setting that still never waits for approval.

## Making a call

The workflow calls `await agent.run(request, on_event=...)`.

### Request

| Field | Notes |
|---|---|
| **Prompt** | Required. The task. |
| **Working folder** | Required. Where the agent starts and the code it works on. |
| **Extra folders** | Optional. Folders outside the working folder that the agent must be able to read and write, such as the folder where a workflow wants result files. |
| **System prompt** | Optional. Added to the tool's own system prompt. A tool that can't take one gets it placed before the prompt. |
| **Timeout** | Optional. When it runs out, the call is stopped and raises a timeout error. |

- Calls are async.
- One agent object can run many calls at the same time. The code reviewer
  runs its passes in parallel through the same default agent.
- Cancelling the call's task stops the agent's process.

### Result

| Field | Notes |
|---|---|
| **Call id** | The same id the call's events carry. |
| **Final message** | The agent's last text message. |
| **Session id** | The tool's id for the session, if it has one. |
| **Duration** | Wall-clock time of the call. |
| **Usage** | Input and output tokens and cost. Each is empty when the tool doesn't report it. |

### Errors

- A call that can't finish raises an error and never returns a result. This
  covers the tool failing to start, crashing, exiting with an error, or the
  timeout running out.
- The error carries the tool's error output and the session id when known.
- A timeout raises its own error type so callers can tell it apart.

## Action log

Every action the agent takes is streamed to the caller while the call runs.

- `on_event` is optional. When given, it is a plain function called once
  for each event, in order.
- Each package converts its tool's own stream into the shared event types
  below.
- Every event carries:
  - the call's id, unique per call, so events from parallel calls can be
    told apart,
  - a timestamp,
  - the raw event from the tool, so nothing is lost in conversion.

### Event types

| Event | Holds |
|---|---|
| **Text** | Text the agent wrote. |
| **Reasoning** | The agent's reasoning text, when the tool exposes it. |
| **Tool call** | Tool-call id, the tool's own name for the tool, and its input. |
| **Tool result** | The tool-call id it answers, the output, and whether it failed. |
| **Usage** | Token counts and cost reported so far. |
| **Other** | Any tool event that doesn't fit the types above, as raw data only. |

Tool names are each tool's own (for example Claude Code's `Read` or Codex's
`shell`), not generic names.

## Testing support

The core package ships a fake agent for workflow tests. The test gives it a
function that receives each request. That function can write files and
return a result or raise an error. The fake agent can also emit a scripted
list of events. Workflow tests use only this fake agent, never a real one.

## Out of scope for the first version

- **Choosing tools per call.** No list of allowed or available tools. Each
  agent uses its tool's normal set. Workflows keep agents on task through
  their prompts.
- **Permission limits.** No limits on which files an agent may write, and no
  sandbox settings beyond the permission mode.
- MCP servers.
- Resuming a previous session.
- Structured output. Workflows get results through files the agent writes.
- An agent asking the user questions during a call.

## Decisions

Decided by the user:

- Every call runs in `auto` or `bypass` permission mode.
- No per-call tool list in the first version. Workflows rely on their own
  prompts.
- No permission limits beyond the mode for now.
- Every action the agent takes is logged as a stream of events.

Claude's calls (overridable):

- The permission mode is set when the agent is built, not per call.
- Calls are async, and one agent object can run calls in parallel.
- Events are delivered through an `on_event` callback while `run` returns
  the result, rather than `run` returning a stream.
- If `on_event` raises, the call fails with that error.
- Failures raise errors and are never reported as a result.
- Events use the tools' own tool names and keep the raw event.
- The request has optional extra folders, so a workflow can write results
  outside the code under review without the `auto` mode or a tool's default
  sandbox blocking it.
- Request, result and event types are Pydantic models.
- The fake agent for tests lives in the core package.
