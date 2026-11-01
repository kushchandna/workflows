# Code Reviewer: Design

Date: 2026-11-01

How the reviewer described in `requirements.md` is built. The requirements
stay the authority. This document settles what they left open: the Python
API, how the reviewer uses agents, the file formats, and the mechanics of
each pass.

- Distribution `workflows-code-review`, import `workflows.code_review`,
  folder `packages/workflows/code-review/`.
- Depends on `workflows-agent` (the interface) and Pydantic. It never depends
  on a concrete agent package.

## Who calls it

The first two end products:

- **GitLab MR bot.** Has the repo checked out at the MR head. It passes:
  - the codebase with the merge-base commit,
  - the MR description,
  - the MR's threads, converted to the discussion format below,
  - the team's guidelines as review instructions.

  It turns comments into MR positions and applies the thread actions it is
  allowed to.
- **Developer review of uncommitted changes.** Passes the codebase with
  `HEAD` as the base, so uncommitted and untracked files form the change.
  Usually it passes only review instructions.

When something goes wrong, the end product investigates using the labeled
event log (see Events). Nothing else is needed for that.

## Python API

```python
from workflows.code_review import CodeReviewer, ReviewInput, Codebase, ReviewSettings, ReviewPass

reviewer = CodeReviewer(
    default_agent=agent,
    pass_agents={ReviewPass.CONSOLIDATION: other_agent},   # optional, per pass
    settings=ReviewSettings(),                             # optional
)
result = await reviewer.review(
    ReviewInput(
        codebase=Codebase(path=Path("/repo"), base_commit="abc123"),
        diffs=None,                       # or a folder of per-file diffs
        discussions=[Path("threads.json")],
        description="...",
        instructions="...",
    ),
    on_event=handle,                      # optional
)
```

### Inputs

| Type | Fields |
|---|---|
| `Codebase` | `path: Path`, `base_commit: str` |
| `ReviewInput` | `codebase: Codebase \| None`, `diffs: Path \| None`, `discussions: list[Path] = []`, `description: str \| None`, `instructions: str \| None`, `change_author: str \| None`, `reviewer_name: str \| None`. At least one of `codebase` and `diffs` is required. |
| `ReviewPass` | `PER_FILE`, `OVERALL`, `TEST_COVERAGE`, `CODE_PATTERNS`, `CONSOLIDATION` |
| `ReviewSettings` | `lines_per_batch: int = 1000`, `files_per_batch: int = 10`, `directory_break_min_fill: float = 0.5`, `max_parallel_calls: int = 4`, `agent_timeout_seconds: float \| None = 1800`, `work_folder: Path \| None = None` |

- `max_parallel_calls` caps how many agent calls run at once. A large MR can
  produce many batches, and each call is a full agent process.
- `agent_timeout_seconds` is passed as every call's timeout.
- `work_folder`, when given, is used instead of a temporary folder and is
  kept after the review, so its files can be inspected afterwards. Otherwise
  the reviewer creates a temporary folder and deletes it.

### Output

`ReviewResult` (Pydantic, so it converts to JSON):

| Field | Type |
|---|---|
| `status` | `"approved" \| "changes_requested" \| "inconclusive"` |
| `comments` | `list[Comment]` |
| `thread_actions` | `list[ThreadAction]` |
| `coverage` | `Coverage` |

- `Comment`: `target: Target`, `body: str`, `reply_to: str | None`.
- `ThreadAction`: `thread_id: str`, `action: "resolve" | "reopen"`.
- `Coverage`:
  - `files: list[FileCoverage]`, each with `path`, `state` (`reviewed`,
    `skipped` or `unreviewed`) and `reason: str | None`;
  - `passes: list[PassCoverage]`, each with `review_pass`, `state` (`ran`,
    `skipped` or `failed`) and `reason: str | None`;
  - `dropped_thread_actions: list[DroppedThreadAction]`, each with
    `thread_id`, `action`, `review_pass` and `reason`: thread actions that
    were still invalid after the retry, for example on a thread that call
    wasn't given.
- `Target`, one of:
  - `LinesTarget`: `type: "lines"`, `path`, `start_line`, `end_line`,
    `side: "new" | "old"`. `old` means line numbers from before the change,
    used for deleted lines.
  - `FileTarget`: `type: "file"`, `path`.
  - `ChangeTarget`: `type: "change"`.

### Errors

- `ReviewInputError` is raised before any agent runs for invalid input:
  - neither codebase nor diffs given;
  - the codebase isn't a git repository, or the base commit doesn't exist;
  - the diffs folder is missing;
  - a discussions file isn't valid JSON in the format below, or two threads
    share an id.
- `AgentError` from any agent call propagates. The other calls in flight are
  cancelled and the review raises. Failures are never reported as a status.

### Events

`on_event(ReviewEvent)` receives every agent event from every call:

- `ReviewEvent`: `review_pass: ReviewPass`, `batch: int | None` (the batch
  number for per-file calls, otherwise none), `attempt: int` (1 or 2), and
  `event`, the agent's own event.

## The change list

Workflow code builds the list of changed files before any agent runs. Each
entry has:

- `path` and `old_path` (set for renames);
- `status`: added, modified, deleted or renamed;
- `binary`;
- `changed_lines`: lines added plus lines removed;
- its diff file in `input/diffs/`.

**From a codebase:**
- `git diff --name-status -M <base>` lists tracked changes, committed and
  uncommitted. `git ls-files --others --exclude-standard` lists untracked
  files.
- `git diff --numstat -M <base>` gives line counts. A count of `-` means the
  file is binary.
- Each file's diff is written to `input/diffs/<path>.diff` in the work folder:
  - `git diff -M <base> -- <old> <new>` for tracked files,
  - `git diff --no-index /dev/null <path>` for untracked files.

  This gives the same layout as a diffs folder input.

**From a diffs folder:** each `.diff` file is read.
- Its path inside the folder, minus `.diff`, is the file path.
- The status comes from the diff header (`new file`, `deleted file`,
  `rename from`/`rename to`).
- A file is binary when the diff contains `Binary files` or
  `GIT binary patch`.
- The line count is the number of `+` and `-` lines, not counting the
  `+++`/`---` headers.
- The diffs are copied into `input/diffs/`.

When both inputs are given, the change list comes from the diffs folder. The
codebase is used for reading surrounding code. This is the requirements' rule.

Binary files are skipped, with the reason `binary file`. They are the only
automatic skips.

**No changes:** if the change list is empty, or holds only binary files, no
agent runs. The result is `approved`, with no comments, and every pass is
recorded as skipped with the reason `no changes` (or `only binary files`).

## Batching

Non-binary files are batched by the requirements' rules. The algorithm:

1. Sort files by path.
2. A file counts its `changed_lines` against `lines_per_batch` and counts as
   one file against `files_per_batch`. A file that is only renamed (no
   content change) or deleted counts toward neither limit. A rename that also
   changes content counts normally.
3. Every file stands alone. There is no grouping of tests with their source.
4. Walk the sorted list, adding files to the current batch. If adding the
   next file would breach either limit, start a new batch with it.
5. When the next file is in a different top-level directory, start a new
   batch if the current one is filled to at least `directory_break_min_fill`.
   Filled means the larger of lines used out of `lines_per_batch` and files
   used out of `files_per_batch`.
6. A single file larger than `lines_per_batch` gets its own batch. A file is
   never split.

## Work folder

```
<work>/
  input/                                       prepared by our code before any agent runs
    diffs/<path>.diff                          the change, one diff per file
    threads.json                               all threads, merged from the discussion files
  agent-output/                                raw files as agents wrote them
    per-file/batch-<k>/attempt-<a>/<n>.json    slot n of batch k
    test-coverage/attempt-<a>/result.json
    code-patterns/attempt-<a>/result.json
    overall/attempt-<a>/result.json
    consolidation/attempt-<a>/result.json
  review-passes/                               checked results only, written by our code
    per-file/<path>.json                       one per reviewed file, mirroring input/diffs/
    test-coverage/result.json
    code-patterns/result.json
    overall/result.json
    consolidation/result.json
```

- `input/` holds only the review's input. Our code writes it once, before any
  agent runs, and nothing writes to it afterwards.
- `agent-output/` is where agents write. Each call has its own folder, and
  `attempt-1` and `attempt-2` keep the first try and the retry apart. It's
  the raw record for investigating a review.
- `review-passes/` holds only checked results, written by our code after a
  call and its retry. Invalid comments are already moved to the change level
  and invalid thread actions are dropped, so a later pass can be pointed at
  the whole folder.
  - Per-file results from all batches are merged into `per-file/<path>.json`,
    so the result for `input/diffs/<path>.diff` is at the matching path.
    Deleted files are under their old path and renamed files under their new
    path, as with the diffs.
  - An unreviewed file has no result there.
- The codebase stays where it is and is never copied.

Each agent call gets:
- `working_folder`: the codebase path, or the work folder when only diffs
  were given;
- `extra_folders`: `[<work>]`.

Every agent call:
- reads from the codebase, from `input/` and from `review-passes/`;
- writes only to its own folder under `agent-output/`.

The calls run in bypass mode, so none of these reads or writes stops for
approval.

The prompt carries:
- the description and the review instructions, as text;
- the ids of the threads this call is responsible for;
- the absolute paths of the exact files to read and write.

Prompts name only result files that passed validation, so an invalid or
missing result is never fed to a later pass.

## File formats

All files are JSON and validated with Pydantic models. The schemas are part
of the package. Targets use the `Target` shape from Output in every file.

**Who wrote each message.** A message's `author` is matched by exact string
against the review input:
- `change_author`: the message is from the person who made the change, such
  as the MR author pushing back on a concern or saying it's fixed;
- `reviewer_name`: the message is the reviewer's own earlier comment or reply,
  from a previous run.

The prompts tell the passes to:
- weigh the change author's replies before resolving a thread;
- treat their own earlier comments as theirs: follow up on them and don't
  repeat them.

End products pass threads with the reviewer's own messages included, so the
reviewer can see what it said before.

**Discussions (input).** Each discussion file the end product passes holds a
list of threads. Our code merges them into `input/threads.json`, in the same
format:

```json
[{"id": "t1",
  "target": {"type": "lines", "path": "src/a.py", "start_line": 10, "end_line": 12, "side": "new"},
  "state": "open",
  "messages": [{"author": "alice", "body": "Why is this needed?"}]}]
```

**Per-file result.** Agents write it to
`agent-output/per-file/batch-<k>/attempt-<a>/<n>.json`, where `n` is the
file's position in the call's list in the prompt. The checked version goes
to `review-passes/per-file/<path>.json` in the same format:

```json
{"path": "src/a.py",
 "summary": "Adds retry to the HTTP client.",
 "comments": [{"target": {...}, "body": "...", "reply_to": null}],
 "thread_actions": [{"thread_id": "t1", "action": "resolve"}]}
```

**Overall, test coverage and code patterns results:**

```json
{"comments": [...], "thread_actions": [...]}
```

Only the overall pass may include thread actions; for the others it must be
empty.

**Consolidation input.** There is no prepared file. Consolidation reads
`review-passes/` and `input/threads.json`. The coverage so far, including the
list of unreviewed files and failed passes, is given in the prompt as text.

**Consolidation result** (written to
`agent-output/consolidation/attempt-<a>/result.json`, checked into
`review-passes/consolidation/result.json`):

```json
{"status": "changes_requested",
 "comments": [{"target": {...}, "body": "...", "reply_to": null}],
 "thread_actions": [...]}
```

Consolidation writes the final comments out in full, whether kept as they
were or merged into one with new text. It doesn't refer back to earlier
comments, so which ones were merged shows only in the event log. It may not
add new issues; its prompt says so.

## Validation

Validation works at two levels: the file and each comment.

**A result file** is valid when:
- it parses and matches its schema;
- for a per-file result, its `path` is the file the prompt assigned to that
  slot, and its `summary` isn't empty.

A file that fails these is unusable, like a missing one.

**A comment or thread action** is valid when:
- every target path is a changed file, or an existing file in the codebase.
  For per-file results it must be a file in the batch. Without a codebase it
  must be a changed file;
- for a line target, `start_line >= 1` and `end_line >= start_line`. On the
  `new` side, `end_line` is within the file's current length. That check
  needs a codebase and is skipped without one;
- a `reply_to` or thread action names a thread routed to that call. For
  consolidation it may name any thread.

**What happens:**
1. If a call's result file is unusable, or holds any invalid comment or
   thread action, the call is retried once.
2. After the retry, the best result is used: the retry's if it's usable,
   otherwise the first attempt's.
   - Valid comments and thread actions are kept.
   - An invalid comment isn't dropped. It's moved to the whole-change level:
     its target becomes `change`, and its body is prefixed with the location
     it claimed, for example `src/a.py:120-125: ...`. A comment whose
     `reply_to` was invalid loses the `reply_to` the same way.
   - An invalid thread action is dropped and recorded in the coverage
     report.
3. If neither attempt gave a usable file, the file is `unreviewed`, or the
   pass is `failed`, as described under Running the passes.
4. The checked result is written to `review-passes/`.

## Running the passes

1. Build the change list and the batches. Route the threads:
   - threads on a file or line go to the batch holding that file, matched on
    its path or, for a renamed file, its old path;
   - threads on the whole change, or on files outside the change, go to the
     overall pass.
2. In parallel, limited to `max_parallel_calls`:
   - every per-file batch;
   - test coverage;
   - code patterns, only when a codebase is given.

   Otherwise code patterns is skipped with the reason `no codebase`.
3. Per-file retries: after a batch's call, any file whose result is unusable
   or holds invalid comments gets one more call, covering only those files.
   That call writes to `attempt-2/` in the batch's folder, numbered by the
   retry's own list. Results are then handled as described under Validation,
   and every batch's checked results are merged into
   `review-passes/per-file/`. A file
   with no usable result after the retry is `unreviewed`, with the reason
   `no valid result after retry`.
4. Overall runs once every batch, retries included, is finished and merged.
   It reads the per-file results, including their summaries, from
   `review-passes/per-file/`. Its prompt lists the unreviewed files.
5. Retries for the other passes follow the same rules. A pass with no usable
   result after the retry is recorded as `failed` with the reason.
6. Consolidation runs last. A missing or invalid result is retried once. If
   it still fails, the review raises `AgentError`, since there is no result
   to report.
7. Final status: consolidation's status, except it becomes `inconclusive`
   when any file is `unreviewed` or any pass `failed`.

Every call gets the review instructions and the description as text in its
prompt. Each pass has its own prompt template in the package
(`prompts/<pass>.md`). The prompt states the pass's job and what is not its
job, as in the requirements' pass table.

## Testing

- Unit tests use only the core `FakeAgent`. Its `respond` function reads the
  request's prompt to find the output paths, and writes chosen result files:
  valid, partly invalid, unusable or missing. These tests cover:
  - retries;
  - moving invalid comments to the change level;
  - merging into `review-passes/per-file/`;
  - status and coverage.
- Only the code that builds the change list from a codebase runs `git`.
  Batching, validation, the passes, retries, merging and status are tested
  with change lists built directly in the test, or with diffs folders. Most
  tests never touch git.
- Git tests:
  - share one repository per test module, built once with every scenario:
    committed, staged, unstaged, untracked, ignored, renamed, deleted and
    binary files;
  - are marked `@pytest.mark.git` and left out of the default run (the root
    pytest config adds `-m "not git"`);
  - run with `uv run pytest -m git` when the change-list code changes.
- Diffs-folder parsing gets tests for each header form.
- Batching gets tests for the hard limits, free renames and deletions,
  oversized files and the top-level directory break.
- No integration test against a real agent for now.

## Decided by the user

- First callers: a GitLab MR bot with a checkout, and a developer reviewing
  uncommitted changes.
- The labeled event log is what's used to investigate a bad review. Nothing
  more is needed for that.
- Batching uses hard limits on lines and files. Pure renames and deletions
  count toward neither limit. There's no test-file grouping. The fill needed
  before breaking at a top-level directory is a setting, defaulting to 0.5.
- The reviewer's own code runs git and writes the diffs folder before any
  agent runs. Git-ignored files are left out.
- Work folder layout:
  - `input/` holds the diffs and the merged threads;
  - agents write raw files to their own folder under `agent-output/`, with
    one folder per attempt;
  - our code writes the checked results into `review-passes/`, with the
    per-file results merged into one tree that mirrors `input/diffs/`;
  - the codebase stays outside and is never copied;
  - agents read from the codebase, `input/` and `review-passes/`.
- The description and instructions go into prompts as text, not files.
- Invalid comments are kept after the retry by moving them to the
  whole-change level, with their claimed location in the body.
- Git-based tests are kept few, share one repository per module, and run
  only on request (`-m git`). No real-agent integration test for now.
- Proving that the agent read every hunk of a diff ("hunk receipts") is left
  for later. Coverage is proven by one result per file, with the right path
  and a summary.
- Passes read earlier passes' checked results from `review-passes/`. There
  are no prepared summary or consolidation input files.
- The workflow relies on its own internal prompts (the agent interface has no
  tool lists).
- `change_author` and `reviewer_name` inputs, so the reviewer knows which
  messages are the change author's and which are its own earlier comments
  and replies. It sees its own earlier messages.

## Claude's calls (overridable)

- The API shape: `CodeReviewer(default_agent, pass_agents, settings)` with an
  async `review(input, on_event)`.
- `max_parallel_calls` (default 4) and `agent_timeout_seconds` (default 30
  minutes) settings.
- An optional `work_folder` setting that keeps the review's files for
  inspection.
- In codebase mode, the reviewer generates a diffs folder, so every pass works
  from the same files whichever input was given.
- `LinesTarget.side` (`new`/`old`), so comments on deleted lines can be told
  apart.
- Comments may target unchanged files, such as a caller that wasn't updated.
  Per-file results may target only files in their batch.
- A failed overall, test coverage or code patterns pass is recorded as
  `failed` and makes the status `inconclusive`. A failed consolidation raises.
- An empty change gives `approved`, with all passes skipped.
- The name `directory_break_min_fill`, and defining "filled" as the larger
  of the line and file ratios.
