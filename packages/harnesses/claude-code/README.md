# workflows-harness-claude-code

An implementation of the [harness interface](../core/README.md) that runs the
[Claude Code](https://docs.claude.com/en/docs/claude-code) CLI.

Import: `workflows.harness_claude_code`

```python
from pathlib import Path

from workflows.harness import HarnessRequest
from workflows.harness_claude_code import ClaudeCodeHarness

harness = ClaudeCodeHarness(model="sonnet")  # claude_path="claude" by default
result = await harness.run(
    HarnessRequest(prompt="List the TODOs", working_folder=Path("/repo"))
)
```

| Setting | Notes |
|---|---|
| `model` | Required. Passed to `--model` unchanged, so aliases such as `opus` work. |
| `claude_path` | The `claude` executable. A bare name is looked up on `PATH`. |

## How a call runs

Each call is one `claude` process started in the request's working folder:

```
claude -p --output-format stream-json --verbose --model <model> --permission-mode bypassPermissions
       [--add-dir <folder>]...  [--append-system-prompt-file <file>]
```

- **Permission mode.** Every call runs in `bypassPermissions` mode, because a headless call has
  no one to approve anything. It is not a setting.
- **The machine's setup is used as it is.** Login, settings, hooks, `CLAUDE.md` files, MCP
  servers and plugins all apply. Preparing the machine is the end product's job.
- **Prompt and system prompt.** The prompt goes through stdin and the system prompt through a
  temporary file, so large prompts don't hit the command-line size limit.
- **Sessions are kept**, so you can open one later with `claude --resume <session id>`.

## Events and results

Each line of the CLI's `stream-json` output becomes events: assistant text, thinking, tool
calls and their results, and usage. Anything else becomes an `OtherEvent`, so nothing is lost,
and every event keeps the parsed line as `raw`.

The result comes from the CLI's final `result` line. Input tokens add up the cached and
uncached counts. The call raises `HarnessError` when the CLI reports an error, exits non-zero,
ends without a result, or when `claude` can't be found or run. A timeout raises
`HarnessTimeoutError`.

## Stopping

`claude` runs in its own process group. When a call ends for any reason (success, error,
timeout, cancellation, or an error in `on_event`), the group gets SIGTERM and then SIGKILL
after 5 seconds. The group is also stopped as soon as `claude` exits, so a leftover child
can't keep the call waiting.

**Known limit:** `claude` starts its Bash-tool shells and hook commands detached, in their own
session, so stopping the group doesn't reach them. Their cleanup relies on `claude` handling
SIGTERM. If `claude` has to be killed, a running tool command can be left behind.

POSIX only (it uses process groups).

## Tests

The unit tests run a small fake `claude` script (`tests/fake_claude.py`) against recorded-style
output fixtures. A test against the real CLI lives in the repository's `tests/integration/`
and runs only with `WORKFLOWS_INTEGRATION=1`, because it needs a logged-in CLI and costs money.
