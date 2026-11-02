# workflows-code-change

Applies one instruction to a codebase and commits the result. The change is already decided by
whoever wrote the instruction, such as fixing an issue Sonar raised or a Jira item whose change is
known; the workflow doesn't plan or design. It knows nothing about platforms or team practice: an
end product (a Sonar fixer, a Jira bot) turns its own data into the input below, then pushes the
commits and raises a merge request itself.

Import: `workflows.code_change`. Depends on the [agent interface](../../agents/core/README.md)
only, never on a concrete agent.

```python
from pathlib import Path

# The end product picks the agent.
from workflows.agent_claude_code import ClaudeCodeAgent
from workflows.code_change import ChangeInput, CodeChanger

changer = CodeChanger(
    change_agent=ClaudeCodeAgent("sonnet"),
    # Optional: a different agent for the review. Defaults to the change agent.
    review_agent=ClaudeCodeAgent("opus"),
)
result = await changer.change(
    ChangeInput(
        codebase=Path("/repo"),
        instruction="Sonar python:S1481 in src/a.py:42: remove the unused local 'tmp'.",
        commit_instructions="Subject: '<JIRA-KEY> <summary>', at most 72 characters.",
        checks=["uv run ruff check .", "uv run pytest -q"],
        review=True,
    ),
    # Every agent action, labeled with its stage, attempt and call.
    on_event=print,
)
if result.status == "completed":
    ...  # push the branch and raise a merge request from result.title and result.description
```

## Input

`ChangeInput` is set per run. One run handles one instruction.

| Field | Meaning |
|---|---|
| `codebase` | A git work tree, at its top level and **clean**: no uncommitted or untracked files (git-ignored files don't count). HEAD when the run starts is the base commit. The workflow commits on whatever is checked out, so create the branch first. |
| `instruction` | Free text describing the change. It may include testing and validation guidance, which the agent follows. |
| `commit_instructions` | Optional. The commit message template or rules. |
| `checks` | Shell commands the workflow runs itself, from the codebase folder. Exit code 0 is a pass. Checks aren't run on the base commit first, so make sure they pass there. |
| `review` | Run a spec review of the commits against the instruction. Default `False`. |
| `max_attempts` | Total tries, the first one included. Default 3. |
| `reset_on_failure` | Put the work tree back on the base commit when the run doesn't complete. Default `True`; turn it off to look into why a change didn't work. |

`ChangeSettings` is for what an end product sets once:

| Field | Default | Meaning |
|---|---|---|
| `agent_timeout_seconds` | 1800 | Timeout for every agent call. |
| `check_timeout_seconds` | 1800 | Timeout for each check command. |
| `check_output_chars` | 20000 | How much of each check's output, from the end, is kept in the result and given to the agent. |
| `work_folder` | none | Keeps the run's files after it ends. Must be empty or missing and outside the codebase. |

`ChangeInputError` is raised before any agent runs for invalid input: a blank instruction, a codebase
that isn't the top of a git work tree or has no commits or isn't clean, or a bad `work_folder`.

## Output

`ChangeResult` converts to JSON with `model_dump_json()`.

- **`status`**:
  - `completed`: every stage passed.
  - `needs_human`: the agent stopped the run because the change needs human input: a design
    choice, a change much larger than it looked, an instruction that doesn't make sense against the
    code, or nothing to change (for example a false positive).
  - `inconclusive`: the review couldn't decide, or it changed the codebase. The run stops at once.
  - `failed`: the last attempt still failed the commit check, a check or the review.
- **`reason`**: why the run stopped or failed. For `failed` it is `<stage>: <reason>`. Empty when
  completed.
- **`base_commit`**, **`commits`**: the commits since the base, oldest first, each with its id and
  message. Empty after a reset.
- **`title`**, **`description`**: the agent's account of what changed and why, from the last
  attempt that reported `done`. Kept on other statuses as a record.
- **`attempts`**: for each attempt, the stage it ended at and why, each check's command, exit code
  and output, what the checks left in the work tree (`left_changes`), and the review's verdict and
  comments.

Failures such as an agent crash raise `AgentError` and are never reported as a status.

## How a run works

Each attempt runs these stages in order; a stage that fails ends the attempt.

1. **Change** (agent). Makes the change and commits it, in one or more commits, then writes a result
   file: `done` with a title and description, or `needs_human` with a reason. `needs_human` ends the
   whole run.
2. **Commit check** (code). HEAD is still on the starting branch, the last checked commit is still an
   ancestor of HEAD (nothing was amended or rebased), there is at least one new commit, and nothing
   is left uncommitted.
3. **Checks** (code). Every check runs, even after one fails, so the next attempt sees every failure.
   Checks run with their input closed, and each check's full output is kept in the work folder.
   The commit check left the work tree clean, so anything dirty after the checks came from them
   (reports, coverage, caches). That fails the attempt at `checks`, with the reason `checks left
   changes in the work tree: <paths>`, and the agent is told to git-ignore those files or stop the
   check writing them. Without this rule the files would be blamed on the review or on the next
   commit check.
4. **Review** (agent, optional). A spec review: does the change do everything the instruction asks,
   does it do anything it didn't ask for, and did it follow the testing guidance? It doesn't judge
   style or quality; run `workflows-code-review` on the commits for that. A result that is missing or
   invalid is retried once.

When the commit check fails, a check fails, or the review asks for changes, and attempts remain, the
agent gets what went wrong and fixes it with **new commits on top**. Commits from all attempts
together form the change.

When a run ends in any status but `completed` (or raises after the first agent call), the workflow
resets the work tree: back on the starting branch at the base commit, with untracked files removed.
Git-ignored files stay. Commits the agent made on other branches are not removed.

## What it doesn't do

- Push, create branches or merge requests. The end product does that.
- Stop an agent from writing outside its job. Agents run without approval prompts. The review
  agent is told not to change anything, and the workflow checks that HEAD and the work tree are
  unchanged after a review, but it can't stop writes elsewhere. Run it on a throwaway checkout if
  that matters.
- Handle submodules, or two runs on the same work tree at once.
