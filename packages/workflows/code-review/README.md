# workflows-code-review

Reviews a set of code changes and returns a verdict with comments. The workflow knows nothing
about platforms or team practice: an end product (a GitLab MR bot, an IDE plugin, a CLI)
converts its own data into the input below and applies the output itself.

Import: `workflows.code_review`. Depends on the [agent interface](../../agents/core/README.md)
only, never on a concrete agent.

```python
from pathlib import Path

# The end product picks the agent.
from workflows.agent_claude_code import ClaudeCodeAgent
from workflows.code_review import CodeReviewer, Codebase, ReviewInput, ReviewPass

reviewer = CodeReviewer(
    default_agent=ClaudeCodeAgent("sonnet"),
    # Optional: a different agent for particular passes.
    pass_agents={ReviewPass.CONSOLIDATION: ClaudeCodeAgent("opus")},
)
result = await reviewer.review(
    ReviewInput(
        codebase=Codebase(path=Path("/repo"), base_commit="abc123"),
        discussions=[Path("threads.json")],
        description="Adds retry to the HTTP client.",
        instructions="Prefer small functions.",
        change_author="alice",
        reviewer_name="review-bot",
    ),
    # Every agent action, labeled with its pass, batch and attempt.
    on_event=print,
)
print(result.status, len(result.comments))
```

## Input

At least one of `codebase` and `diffs` is required; every other field is optional.

| Field | Meaning |
|---|---|
| `codebase` | `Codebase(path, base_commit)`: the code with the change applied, committed or not, and the commit the change starts from. Uncommitted and untracked files count; git-ignored files don't. |
| `diffs` | A folder with one `git diff` per changed file, stored at `<path>.diff`. When both are given, the diffs decide what changed and the codebase is only read for context. |
| `discussions` | JSON files, read together as one list of existing threads (format below). |
| `description` | Free text describing the change. |
| `instructions` | Free text: what to focus on, or the team's review guidelines. |
| `change_author`, `reviewer_name` | Names matched against the `author` of thread messages, so the reviewer knows the author's replies and its own earlier comments. |

A thread:

```json
{"id": "t1",
 "target": {"type": "lines", "path": "src/a.py", "start_line": 10, "end_line": 12, "side": "new"},
 "state": "open",
 "messages": [{"author": "alice", "body": "Why is this needed?"}]}
```

A target is `lines` (with `side` `new` or `old`; `old` is for deleted lines), `file`, or
`change` (the whole change). Paths are relative to the codebase folder.

## Output

`ReviewResult` converts to JSON with `model_dump_json()`.

- **`status`**: `approved`, `changes_requested`, or `inconclusive` (the reviewer couldn't judge
  properly: a file couldn't be reviewed or a pass failed).
- **`comments`**: each has a `target`, a `body` and an optional `reply_to` (a thread id). A
  reply carries its thread's own target.
- **`thread_actions`**: `resolve` or `reopen` for a thread id. The end product decides whether to
  apply them.
- **`coverage`**: each changed file as `reviewed`, `skipped` (binary files) or `unreviewed`; each
  pass as `ran`, `skipped` or `failed`, with reasons; and any thread actions that were dropped.

## How a review runs

Workflow code, not the agent, guarantees that every changed file is reviewed: it builds the
change list from git or the diffs folder, splits the files into batches, and checks that each
file got a valid result. A missing or invalid result is retried once; a file still without one
is `unreviewed`, and the status can't be `approved`.

| Pass | Job |
|---|---|
| per file (in batches) | Bugs, logic and readability in each file's change; a summary of each file. |
| test coverage | Whether new and changed behavior is tested, judged by reading code. |
| code patterns | Whether the change follows the codebase's conventions (only with a codebase). |
| overall | Whether the change does what the description says, and what is missing across files. |
| consolidation | Removes duplicate comments, sets the final status. |

Per-file batches, test coverage and code patterns run in parallel, then overall, then
consolidation. A pass may use its own agent; the rest use the default agent.

Comments an agent got wrong aren't lost: after the retry, a comment with an invalid target moves
to the whole-change level with its claimed location in the body. Every pass reads all existing
threads, but only the pass a thread is routed to may reply to it, resolve it or reopen it.
Those replies and actions always reach the result.

## Settings

`ReviewSettings` (all optional):

| Setting | Default | Meaning |
|---|---|---|
| `lines_per_batch` | 1000 | Most changed lines in a per-file batch. |
| `files_per_batch` | 10 | Most files in a per-file batch. Pure renames and deletions count toward neither limit. |
| `directory_break_min_fill` | 0.5 | How full a batch must be before it breaks at a new top-level directory. |
| `max_parallel_calls` | 4 | Most agent calls running at once. |
| `agent_timeout_seconds` | 1800 | Timeout of every agent call. |
| `work_folder` | none | Keep the review's files here (it must be empty or missing). Otherwise a temporary folder is used and deleted. |

## Errors

`ReviewInputError` is raised before any agent runs: no codebase or diffs, a codebase that isn't a
git work tree or whose base commit doesn't exist, a missing diffs folder, a bad discussions file
or duplicate thread ids, or a `work_folder` that isn't empty. An `AgentError` from any call
propagates and cancels the calls still running. Failures are never reported as a status.

## The work folder

```
<work>/
  input/                 diffs/<path>.diff and threads.json, written before any agent runs
  agent-output/          raw files exactly as the agents wrote them, one folder per attempt
  review-passes/         checked results only; per-file results mirror input/diffs/
```

Agents read the codebase, `input/` and `review-passes/`, and write only inside their own folder
under `agent-output/`. Set `work_folder` to keep it all; together with the event log it is the
record for investigating a review.

## Tests

The unit tests use only the core `FakeAgent`. Tests that run `git` are marked `git`, left out
of the default run, and run with `uv run pytest -m git`.
