# workflows-code-change

Applies one instruction to a codebase and commits the result. The change is already decided by
whoever wrote the instruction, such as fixing an issue Sonar raised or a Jira item whose change is
known; the workflow doesn't plan or design. It knows nothing about platforms or team practice: an
end product (a Sonar fixer, a Jira bot) turns its own data into the input below, then pushes the
commits and raises a merge request itself.

Import: `workflows.code_change`. Depends on the [harness interface](../../harnesses/core/README.md)
only, never on a concrete harness.

```python
from pathlib import Path

# The end product picks the harness.
from workflows.harness_claude_code import ClaudeCodeHarness
from workflows.code_change import ChangeInput, CodeChanger

changer = CodeChanger(
    change_harness=ClaudeCodeHarness("sonnet"),
    # Optional: a different harness for the review. Defaults to the change harness.
    review_harness=ClaudeCodeHarness("opus"),
)
result = await changer.change(
    ChangeInput(
        codebase=Path("/repo"),
        instruction="Sonar python:S1481 in src/a.py:42: remove the unused local 'tmp'.",
        commit_instructions="Subject: '<JIRA-KEY> <summary>', at most 72 characters.",
        checks=["uv run ruff check .", "uv run pytest -q"],
        review=True,
    ),
    # Every harness action, labeled with its stage, attempt and call.
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
| `instruction` | Free text describing the change. It may include testing and validation guidance, which the model follows. |
| `commit_instructions` | Optional. The commit message template or rules. |
| `checks` | Shell commands the workflow runs itself, from the codebase folder. Exit code 0 is a pass. Checks aren't run on the base commit first, so make sure they pass there. |
| `review` | Run a spec review of the commits against the instruction. Default `False`. |
| `max_attempts` | Total tries, the first one included. Default 3. |
| `reset_on_failure` | Put the work tree back on the base commit when the run doesn't complete. Default `True`; turn it off to look into why a change didn't work. |

`ChangeSettings` is for what an end product sets once:

| Field | Default | Meaning |
|---|---|---|
| `harness_timeout_seconds` | 1800 | Timeout for every harness call. |
| `check_timeout_seconds` | 1800 | Timeout for each check command. |
| `check_output_chars` | 20000 | How much of each check's output, from the end, is kept in the result and given to the harness. |
| `work_folder` | none | Keeps the run's files after it ends. Must be empty or missing and outside the codebase. |

`ChangeInputError` is raised before any harness runs for invalid input: a blank instruction, a
codebase that isn't the top of a git work tree or has no commits or isn't clean, a bad
`work_folder`, or no `work_folder` while the system temporary folder (`TMPDIR`) is inside the
codebase. A git failure in the workflow's own code raises `RuntimeError`, and a check's log that
can't be written (a full disk) raises `OSError`; both reset the work tree first unless
`reset_on_failure` is off.

## Output

`ChangeResult` converts to JSON with `model_dump_json()`.

- **`status`**:
  - `completed`: every stage passed.
  - `needs_human`: the model stopped the run because the change needs human input: a design
    choice, a change much larger than it looked, an instruction that doesn't make sense against the
    code, or nothing to change (for example a false positive).
  - `inconclusive`: the review couldn't decide, or it changed the codebase. The run stops at once.
  - `failed`: the last attempt still failed the commit check, a check or the review.
- **`reason`**: why the run stopped or failed. For `failed` it is `<stage>: <reason>`. Empty when
  completed.
- **`base_commit`**, **`commits`**: the commits since the base, oldest first, each with its id and
  message. Empty after a reset.
- **`title`**, **`description`**: the model's account of what changed and why, from the last
  attempt that reported `done`. Kept on other statuses as a record.
- **`attempts`**: for each attempt, the stage it ended at and why, each check's command, exit code
  and output, what the checks left in the work tree (`left_new` for new files, staged or not, `left_modified` for tracked files
  a check changed), and the review's verdict and
  comments.

Failures such as a harness crash raise `HarnessError` and are never reported as a status.

## How a run works

Each attempt runs these stages in order; a stage that fails ends the attempt.

1. **Change** (harness). Makes the change and commits it, in one or more commits, then writes a
   result file: `done` with a title and description, or `needs_human` with a reason. `needs_human`
   ends the whole run.
2. **Commit check** (code). HEAD is still on the starting branch, the last checked commit is still an
   ancestor of HEAD (nothing was amended or rebased), there is at least one new commit, and nothing
   is left uncommitted.
3. **Checks** (code). Every check runs, even after one fails, so the next attempt sees every failure.
   Checks run with their input closed, and each check's full output is kept in the work folder.
   The commit check left the work tree clean, so anything dirty after the checks came from them
   (reports, coverage, caches, a formatter's rewrite). That fails the attempt at `checks`, with the
   reason `checks left changes in the work tree: <paths>`. For new files the model is told to
   git-ignore them or stop the check writing them; for tracked files, to commit the change if it
   belongs to the fix and revert it otherwise. A check that moves HEAD or switches branch
   (`moved_head`) fails the attempt too; a commit the check made is not the harness's work, so the
   next attempt must add one beyond it, and the model is told how to put the repository back.
   Without these rules the changes would be blamed on the review or on the next commit check.
4. **Review** (harness, optional). A spec review: does the change do everything the instruction asks,
   does it do anything it didn't ask for, and did it follow the testing guidance? It doesn't judge
   style or quality; run `workflows-code-review` on the commits for that. A result that is missing or
   invalid is retried once. After each review call the workflow checks that HEAD, the branch and
   the work tree are unchanged.

When the commit check fails, a check fails, or the review asks for changes, and attempts remain, the
model gets what went wrong and fixes it with **new commits on top**. Commits from all attempts
together form the change. The one exception is a commit check that failed because commits were
rewritten or the model left the starting branch: the model is then told which commit HEAD must
descend from and how to put the repository back (`git checkout -f -B <branch> <commit>`, or
`git checkout -f --detach <commit>` when the run started detached), and may reset for that.

When a run ends in any status but `completed` (or raises after the first harness call), the workflow
resets the work tree: back on the starting branch at the base commit, with untracked files removed
(including nested repositories) and the repository's hooks turned off. Git-ignored files stay,
including ones the model committed or force-added. Commits the model made on other branches are
not removed, and neither are a stash or an unfinished rebase.

## What it doesn't do

- Push, create branches or merge requests. The end product does that.
- Stop a harness from writing outside its job. Harnesses run without approval prompts. The review
  model is told not to change anything, and the workflow checks that HEAD and the work tree are
  unchanged after a review, but it can't stop writes elsewhere. Run it on a throwaway checkout if
  that matters.
- Stop a check's descendants that left its process group (`setsid`, coreutils `timeout`). After the
  check's group is killed the workflow waits a second for its output, then lets go of the pipe; such
  a process keeps running and may still write into the work tree later.
- Handle submodules, or two runs on the same work tree at once.
