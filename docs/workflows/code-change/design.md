# Code Change: Design

Date: 2026-11-02

How the workflow described in `requirements.md` is built. The requirements
stay the authority. This document settles what they left open: the Python
API, how the agent reports back, the prompts, timeouts, and how check output
is kept.

- Distribution `workflows-code-change`, import `workflows.code_change`,
  folder `packages/workflows/code-change/`.
- Depends on `workflows-agent` (the interface) and Pydantic. It never depends
  on a concrete agent package, and not on `workflows-code-review` either.
- Added to the root workspace's dependencies and `tool.uv.sources`, like the
  code reviewer.
- Ships a `README.md` in the style of the code reviewer's: a usage example,
  the input and output tables, and how a run works.

## Who calls it

The first two end products:

- **Sonar fixer.** Checks out a fresh branch from the target branch, turns one
  Sonar issue (rule, file, line, message) and the fix it wants into an
  instruction, and passes the project's build and test commands as checks.
  On `completed` it pushes the branch and raises a merge request from the
  title and description.
- **Jira bot.** Same shape, with the ticket's described change as the
  instruction and any testing notes from the ticket inside it.

Both create the branch before calling and push after. The workflow never
touches remotes.

When something goes wrong, the end product investigates with the labeled
event log, the `attempts` in the result and, with `reset_on_failure` off, the
commits left in place. With a `work_folder` set, the raw files are kept too.

## Python API

```python
from workflows.code_change import CodeChanger, ChangeInput, ChangeSettings

changer = CodeChanger(
    change_agent=agent,
    review_agent=other_agent,          # optional; defaults to change_agent
    settings=ChangeSettings(),         # optional
)
result = await changer.change(
    ChangeInput(
        codebase=Path("/repo"),
        instruction="Sonar python:S1481 in src/a.py:42: remove the unused local 'tmp'.",
        commit_instructions="Subject: '<JIRA-KEY> <summary>', at most 72 characters.",
        checks=["uv run ruff check .", "uv run pytest -q"],
        review=True,
        max_attempts=3,
        reset_on_failure=True,
    ),
    on_event=handle,                   # optional
)
```

`attempt` is used in the code for what the requirements call a try, since
`try` is a Python keyword.

### Inputs

| Type | Fields |
|---|---|
| `ChangeInput` | `codebase: Path`, `instruction: str`, `commit_instructions: str \| None = None`, `checks: list[str] = []`, `review: bool = False`, `max_attempts: int = 3` (`ge=1`), `reset_on_failure: bool = True` |
| `ChangeSettings` | `agent_timeout_seconds: float \| None = 1800`, `check_timeout_seconds: float \| None = 1800`, `check_output_chars: int = 20000`, `work_folder: Path \| None = None` |

- Everything the requirements say is set per run is in `ChangeInput`.
  `ChangeSettings` holds what an end product sets once: timeouts, how much
  check output is kept, and where the run's files go.
- `agent_timeout_seconds` is passed as every agent call's timeout.
- `check_timeout_seconds` applies to each check command separately.
- `check_output_chars` is how much of each check's output, counted from the
  end, is kept in the result and given to the agent. The full output stays in
  the work folder.
- `work_folder`, when given, is used instead of a temporary folder and kept
  after the run. It must be empty or missing and must not be inside the
  codebase, where it would show up as untracked files. Otherwise a temporary
  folder is created and deleted, ignoring cleanup errors so they can't hide
  the real error.
- Paths are made absolute before they reach a prompt.

### Output

`ChangeResult` (Pydantic, so it converts to JSON):

| Field | Type |
|---|---|
| `status` | `"completed" \| "needs_human" \| "inconclusive" \| "failed"` |
| `reason` | `str \| None`: set for every status but `completed` |
| `base_commit` | `str`: the full id of HEAD when the run started |
| `commits` | `list[Commit]`: commits since the base, oldest first. Empty after a reset. |
| `title` | `str \| None` |
| `description` | `str \| None` |
| `attempts` | `list[Attempt]` |

- `Commit`: `id: str`, `message: str` (the full message).
- `title` and `description` come from the last attempt whose change result
  said `done`. So they're set on `completed`, and may also be set on `failed`
  or `inconclusive`. They're kept after a reset, as a record of what was
  tried.
- `Attempt`:
  - `number: int`, starting at 1;
  - `ended_at: ChangeStage | None`: the stage that ended it, or none when
    every stage passed;
  - `reason: str | None`: why it ended there;
  - `checks: list[CheckRun]`: empty when checks didn't run;
  - `left_changes: str | None`: the paths the checks left in the work tree
    (capped at 10, then `and <n> more`), or none;
  - `review: ReviewOutcome | None`: none when review didn't run.
- `CheckRun`: `command`, `exit_code: int | None` (none on a timeout),
  `timed_out: bool`, `output: str` (the last `check_output_chars` characters
  of stdout and stderr together), `truncated: bool`.
- `ReviewOutcome`: `verdict: "pass" | "changes_needed" | "inconclusive"`,
  `comments: list[str]`, `reason: str | None`.
- `ChangeStage`: `CHANGE = "change"`, `COMMIT_CHECK = "commit-check"`,
  `CHECKS = "checks"`, `REVIEW = "review"`. The values double as folder
  names.

### Errors

- `ChangeInputError(ValueError)` is raised before any agent runs:
  - the codebase path doesn't exist, isn't a git work tree, or isn't the top
    of its work tree;
  - HEAD has no commit yet;
  - the work tree isn't clean: `git status --porcelain --untracked-files=all`
    prints anything. Git-ignored files don't count;
  - the instruction is empty or only whitespace;
  - `work_folder` isn't empty, or is inside the codebase;
  - no `work_folder` is given and the system temporary folder is inside the
    codebase (the default work folder would be).
- `AgentError` from any agent call propagates after the reset described
  below.
- A git command failing in our own code raises `RuntimeError` with git's
  stderr, after the reset if one is possible.
- Failures are never reported as a status.

### Events

`on_event(ChangeEvent)` receives every agent event from every call:

- `ChangeEvent`: `stage: ChangeStage` (`CHANGE` or `REVIEW`),
  `attempt: int`, `call: int` (1, or 2 for a review retry), and `event`, the
  agent's own event.

## Git

One module, `git.py`, is the only one that runs `git`. Its commands:

- clear `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE` from the environment,
  so an inherited value (inside a git hook, for example) can't point them at
  another repository;
- pass `--literal-pathspecs` and `-c core.quotepath=off`, and raise
  `RuntimeError` with git's stderr on failure.

At the start, it records:

- the **base commit**: `git rev-parse HEAD`;
- the **starting ref**: the branch from `git symbolic-ref -q HEAD`, or none
  when HEAD is detached.

It provides:

- **commits since the base**: `git log --reverse --format=... <base>..HEAD`;
- **the change as a diff**: `git diff <base> HEAD`, with the same flags the
  code reviewer uses (no color, no external diff, no textconv, `a/` and `b/`
  prefixes), written to the work folder;
- **the commit check** (below);
- **the reset** (below).

### The commit check

After each change call, our code checks, in this order, and stops at the
first failure:

1. HEAD is still on the starting ref: the same branch, or still detached when
   the run started detached. The full ref is compared (`refs/heads/main`), not
   the short name, which is ambiguous when a tag has the same name.
2. The **last checked commit** is an ancestor of HEAD
   (`git merge-base --is-ancestor`). That's HEAD after the latest passing
   commit check, or the base when none has passed yet. This catches amended,
   rebased or reset commits.
3. HEAD is not the last checked commit: there is at least one new commit.
4. The work tree is clean, as at the start.

A failure ends the attempt at `COMMIT_CHECK` with a plain reason, such as
`work left uncommitted: src/a.py, src/b.py`. The next attempt gets that
reason.

### The reset

`reset(base, starting_ref)`, with repository hooks turned off
(`-c core.hooksPath=/dev/null`) for the commands that move HEAD:

1. `git read-tree <base>`. The index goes first. Otherwise a file the agent
   committed is deleted along with its commit, even a git-ignored one that
   existed before the run (`.env`). With the index at the base such a file is
   merely untracked, and the ignore rules restored in the next steps keep it.
2. `git checkout -f -B <branch> <base>`, which also recreates the branch if
   the agent renamed or deleted it, or `git checkout -f --detach <base>` when
   the run started detached.
3. `git reset --hard <base>`.
4. `git clean -ffd`. The doubled `f` also removes untracked nested
   repositories. Not `-x`, so git-ignored files such as build output and
   virtual environments stay.

It runs when the run ends in any status but `completed`, and when anything
raises after the first agent call has started (`AgentError`, a git failure,
or cancellation). It doesn't run when `reset_on_failure` is false. A failure
during the reset is raised, chained to the original error if there was one.
Commits the agent made on other branches are not removed.

## Work folder

```
<work>/
  attempt-<n>/
    change/result.json            written by the change agent
    change.diff                   written by our code after a passing commit check
    checks/<k>.log                full output of check k (1-based), written by our code
    review/call-<c>/result.json   written by the review agent; call 2 is the retry
```

- The codebase stays where it is and is never copied.
- Each agent call gets `working_folder` set to the codebase and
  `extra_folders` set to `[<work>]`.
- The change agent writes to the codebase and to its own `change/` folder.
  The review agent writes only to its own `review/call-<c>/` folder.
- `change.diff` is the whole change since the base, every attempt so far
  included. Both the review and the next attempt's change call get its path.

## File formats

All files are JSON and validated with Pydantic models. The schemas are part
of the package.

**Change result** (`attempt-<n>/change/result.json`), one of:

```json
{"outcome": "done", "title": "Remove unused local in parse_header",
 "description": "Sonar flagged `tmp` in src/a.py as unused (python:S1481). Removed it; nothing read it."}
```

```json
{"outcome": "needs_human",
 "reason": "Fixing this needs the public signature of Client.send to change, which affects 14 callers."}
```

- `title` and `description` must not be empty when `done`. `reason` must not
  be empty when `needs_human`.
- A missing or invalid file ends the attempt at `CHANGE` with the reason
  `no valid change result`, and uses up the attempt. The change call is not
  retried on its own: the code changes it made are still there, and the next
  attempt can carry on from them.
- `needs_human` ends the run at once, whatever the codebase looks like. No
  commit check, checks or review run.

**Review result** (`attempt-<n>/review/call-<c>/result.json`):

```json
{"verdict": "changes_needed",
 "comments": ["The instruction asks for the null check in both parse_header and parse_body; only parse_header has it."],
 "reason": null}
```

- `comments` must not be empty for `changes_needed`. `reason` must not be
  empty for `inconclusive`.
- Comments are plain text. They go back to the change agent, not to a merge
  request, so they need no line targets.
- A missing or invalid file is retried once (`call-2`). If the retry fails
  too, the review counts as `inconclusive` with the reason
  `no valid review result after retry`.

## Running a change

```
validate input, record base and starting ref
for n in 1..max_attempts:
    change call                        -> needs_human: stop, status needs_human
                                       -> no valid result: next attempt
    commit check                       -> fails: next attempt
    write change.diff
    every check, in order              -> any fails: next attempt
    review (if on)                     -> changes_needed: next attempt
                                       -> inconclusive: stop, status inconclusive
    -> status completed
out of attempts -> status failed, reason from the last attempt
reset unless completed or reset_on_failure is off
```

- **Checks** run one at a time, in the given order, each through the shell
  (`asyncio` subprocess, `cwd=codebase`) in its own process group, with the
  environment inherited and **stdin closed**, so a check can't read or hold up
  the caller's input. Every check runs even after one fails, so the next
  attempt sees every failure. A check that exits non-zero or times out fails.
  A command that can't be found fails through the shell's own exit code.
  - A timeout kills the whole process group. After the process exits or is
    killed, the wait for the end of its output is bounded (1 second), so a
    descendant that left the group (`setsid`, `timeout`) can't hold the run.
  - Output streams straight into `checks/<k>.log`; only the last
    `check_output_chars` characters are kept in memory.
- **Files left by the checks.** The commit check left the work tree clean, so
  anything dirty after the checks came from them (reports, coverage,
  caches). The attempt then ends at `CHECKS` with the reason
  `checks left changes in the work tree: <paths>`, joined with `; ` to any
  failing check's reason, and `left_changes` holds the paths. The retry prompt
  tells the agent to git-ignore those files (and commit that change) or make
  the check stop writing them, and not to commit them. This keeps the blame on
  the checks: otherwise the review guard, or the next attempt's commit check,
  would report the agent for files it didn't write.
- **The review guard.** After a review call, our code confirms that HEAD
  hasn't moved, is still on the starting ref, and the work tree is still
  clean. If the review changed
  anything, the review is `inconclusive` with the reason
  `review changed the codebase`, and the run stops there.
- **The `failed` reason** is the last attempt's reason, prefixed with the
  stage, for example `checks: uv run pytest -q exited with 1`.
- Every agent call is sequential. There's no parallelism to limit.

## Prompts

Templates in the package, `prompts/change.md` and `prompts/review.md`,
filled with `string.Template.substitute`.

The fixed lines the code and tests rely on are `Result file: <path>` and,
for the change prompt, `Attempt: <n> of <max>`. They come before any text
from the end product. User text (the instruction and commit instructions) is
indented by four spaces, so a line inside it can never look like a fixed
line. The code-reviewer notes list this as a gap there.

**The change prompt** carries:

- the instruction and the commit instructions;
- the rules:
  - make the smallest change that does everything the instruction asks, and
    nothing else: no unrelated refactoring, renaming or reformatting;
  - commit all the work, in one or more commits, following the commit
    instructions. Don't amend, rebase, reset, switch branches, push or touch
    remotes;
  - follow any testing and validation guidance in the instruction;
  - stop with `needs_human` instead of guessing when the change needs a design
    choice, turns out much larger than the instruction suggests, doesn't make
    sense against the code, or isn't needed at all (already fixed, or a false
    positive). Leave the code as it is when stopping;
- the checks the workflow will run, so the agent can run them before it
  commits;
- the result file's path and both formats.

From the second attempt on, it also carries:

- that the earlier attempts' commits are in place, and that fixes go in as
  new commits on top;
- the path of the latest `change.diff`, when there is one;
- what went wrong last time: the commit-check reason, each failing check's
  command, exit code and kept output, or the review's comments.

Only the previous attempt's failure is included, not every attempt's, so the
prompt doesn't grow with each attempt.

**The review prompt** carries:

- the instruction, testing guidance included;
- the path of `change.diff` and the list of commits;
- the codebase path, to read around the change;
- the three questions from the requirements, and that it doesn't judge style
  or quality;
- that it must not change any file in the codebase or run git commands that
  change the repository;
- when each verdict applies: `pass` when all three questions are answered
  well; `changes_needed` when something is missing, extra or untested
  against the guidance, with one comment per problem; `inconclusive` only
  when it can't tell, for example because the instruction is ambiguous;
- the result file's path and format.

## Testing

- **The run loop is tested without git.** `CodeChanger` uses a small
  repository interface (record start, commit check, write diff, list
  commits, reset) that `git.py` implements. Loop tests use an in-memory fake
  of it, with the core `FakeAgent` writing change and review result files.
  They cover:
  - each status and its reason;
  - the attempt budget, including `max_attempts=1`;
  - what each retry prompt carries after each kind of failure;
  - the review retry and the review guard;
  - reset on each non-completed status, on an agent error, on cancellation,
    and not when `reset_on_failure` is off;
  - title and description on each status.
- **Check running** is tested with real small shell commands: pass, fail,
  timeout, missing command, output truncation from the end.
- **Git tests** run real git in temporary repositories, ignore the
  developer's global git config, and are marked `@pytest.mark.git`, as in the
  code reviewer. They cover:
  - input checks: dirty tree, untracked file, ignored file allowed, not the
    top of the work tree, no commits;
  - every commit-check failure: switched branch, amended commit, no new
    commit, uncommitted and untracked work;
  - reset from a switched branch and from a detached start, keeping ignored
    files;
  - a few end-to-end runs where `FakeAgent` commits for real.
- No integration test against a real agent for now.

## Decided by the user

- The name is `code-change`.
- One run handles one instruction, set per run along with checks, review,
  the number of tries and reset on failure.
- The agent commits, in as many commits as it likes. The workflow checks the
  result.
- Pushing, branches and merge requests are left to the end product.
- The description comes back in the result, not in a file.
- A change that needs human input stops the whole run.
- Checks and the review are optional. A failure in either gets another try,
  up to 3 tries in total by default.
- An inconclusive review stops the run.
- The review is a spec review against the instruction, not a code review.
- Resetting to the base commit when a run doesn't complete can be turned off.
- Files that checks leave in the work tree fail the attempt at `CHECKS` (see
  Running a change), with the agent told to git-ignore them. Chosen over
  cleaning them up automatically, which would hide output and formatter
  changes silently.
- Checks run with stdin closed.
- The fixes from the independent reviews, listed in `review-findings.md`,
  including the reset changes above, the bounded output wait, the temporary
  folder rule, and the retry text that lets the agent restore HEAD after a
  commit-check failure about rewritten commits or a switched branch.

## Claude's calls (overridable)

From the requirements:

- The work tree must be clean at the start, so a reset can't destroy
  someone's work.
- A failed commit check uses a try.
- Every check runs on every try, even after one fails.
- Checks and review share one try budget.
- Fixes go in as new commits, and the commit check enforces it.
- An instruction that needs no change ends as `needs_human`.
- Checks aren't run on the base commit first.
- Review is off by default, and the review agent defaults to the change
  agent.
- A reset also runs when the run raises after the first agent call.

New in this design:

- The API shape: `CodeChanger(change_agent, review_agent, settings)` with an
  async `change(input, on_event)`, and `max_attempts` for the number of tries.
- Settings: agent timeout 30 minutes, check timeout 30 minutes per check,
  20,000 characters of check output kept, an optional `work_folder`.
- The codebase must be the top of its git work tree, since commits, the
  commit check and the reset cover the whole repository.
- The agent reports back through a result file, as in the code reviewer.
- A missing or invalid change result uses up the attempt instead of retrying
  the call on its own. A missing or invalid review result is retried once,
  then counts as inconclusive.
- The commit check also requires HEAD to stay on the starting branch.
- The reset returns to the starting branch, then resets hard and runs
  `git clean -fd`, keeping git-ignored files.
- The review guard: a review that changes the codebase makes the run
  `inconclusive`.
- The change agent is told the checks, so it can run them before committing.
- Retry prompts carry only the previous attempt's failure.
- User text in prompts is indented, so it can't fake a fixed line.
- Title and description are kept on non-completed statuses as a record.
- The run loop sits behind a small repository interface, so most tests never
  run git.

## Not covered

- Submodules, and commits the agent made on other branches.
- Enforcing that the review agent writes nowhere else. The guard only
  catches changes to HEAD and the work tree, as in the code reviewer's
  known issues.
