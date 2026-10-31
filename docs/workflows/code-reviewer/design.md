# Code Reviewer: Design

Date: 2026-11-01

How the reviewer described in `requirements.md` is built. The requirements
stay the authority. This document settles what they left open: the Python
API, how the reviewer uses agents, the file formats, and the mechanics of
each pass.

- Distribution `workflows-code-review`, import `workflows_code_review`,
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
from workflows_code_review import CodeReviewer, ReviewInput, Codebase, ReviewSettings, ReviewPass

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
| `ReviewInput` | `codebase: Codebase \| None`, `diffs: Path \| None`, `discussions: list[Path] = []`, `description: str \| None`, `instructions: str \| None`. At least one of `codebase` and `diffs` is required. |
| `ReviewPass` | `PER_FILE`, `OVERALL`, `TEST_COVERAGE`, `CODE_PATTERNS`, `CONSOLIDATION` |
| `ReviewSettings` | `lines_per_batch: int = 1000`, `files_per_batch: int = 10`, `max_parallel_calls: int = 4`, `agent_timeout_seconds: float \| None = 1800`, `work_folder: Path \| None = None` |

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
    `skipped` or `failed`) and `reason: str | None`.
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
- its diff file in the work folder.

**From a codebase:**
- `git diff --name-status -M <base>` lists tracked changes, committed and
  uncommitted. `git ls-files --others --exclude-standard` lists untracked
  files.
- `git diff --numstat -M <base>` gives line counts. A count of `-` means the
  file is binary.
- Each file's diff is written to `diffs/<path>.diff` in the work folder:
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
- The diffs are copied into the work folder.

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
2. Cost of a file: its `changed_lines`, or 10 if it is only renamed (no
   content change) or deleted.
3. Pair each test file with its source file. A test file is one whose name
   matches `test_*`, `*_test.*`, `*.test.*` or `*.spec.*`, or which sits
   under a `tests/` or `test/` folder. Its source is the changed file with
   the same base name minus the test marker. A pair stays together in one
   batch; its cost is the sum of both.
4. Walk the sorted list, adding files or pairs to the current batch. Start a
   new batch when the next item would exceed `lines_per_batch` or
   `files_per_batch`. Also start a new one when the next item is in a
   different top-level folder and the current batch is at least half full by
   cost.
5. A single item larger than `lines_per_batch` gets its own batch. A file is
   never split.

## Work folder

```
<work>/
  diffs/<path>.diff                     the change, one diff per file
  input/description.md                  when given
  input/instructions.md                 when given
  input/threads/<pass or batch>.json    the threads routed to that call
  input/summaries.json                  per-file summaries, for the overall pass
  input/consolidation.json              everything consolidation needs
  output/per-file/<batch>/<n>.json      one result per file in the batch
  output/overall.json
  output/test-coverage.json
  output/code-patterns.json
  output/final.json                     consolidation's result
```

Each agent call gets:
- `working_folder`: the codebase path, or the work folder when only diffs
  were given;
- `extra_folders`: `[<work>]`.

The prompt names the exact input files to read and the exact output files to
write, with absolute paths.

## File formats

All files are JSON and validated with Pydantic models. The schemas are part
of the package. Targets use the `Target` shape from Output in every file.

**Discussions (input).** Each file holds a list of threads:

```json
[{"id": "t1",
  "target": {"type": "lines", "path": "src/a.py", "start_line": 10, "end_line": 12, "side": "new"},
  "state": "open",
  "messages": [{"author": "alice", "body": "Why is this needed?"}]}]
```

**Per-file result.** `output/per-file/<batch>/<n>.json`, where `n` is the
file's position in the batch's list in the prompt:

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

**Consolidation input** (`input/consolidation.json`):
- every comment from every pass, each with an id (`c1`, `c2`, ...) and the
  pass it came from;
- every thread action, each with the pass it came from;
- the threads;
- the coverage so far.

**Consolidation result** (`output/final.json`):

```json
{"status": "changes_requested",
 "comments": [{"target": {...}, "body": "...", "reply_to": null}],
 "thread_actions": [...]}
```

Consolidation may merge duplicate comments into one with new text. It may not
add new issues; its prompt says so.

## Validation

A result file is valid only if all of these hold. Otherwise the whole file is
invalid.

- It parses and matches its schema.
- A per-file result's `path` is the file the prompt assigned to that slot,
  and its `summary` isn't empty.
- Every target path is a changed file or an existing file in the codebase.
  For per-file results it must be a file in the batch.
- For line targets, `start_line >= 1`, `end_line >= start_line`, and on the
  `new` side `end_line` is within the file's current length. The length check
  needs a codebase and is skipped without one.
- Without a codebase, every target path must be a changed file.
- Every `reply_to` and every thread action names a thread routed to that
  call. For consolidation, it names any thread.

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
3. Per-file retries: after a batch's call, any file without a valid result
   gets one more call covering only those files. That call writes to
   `output/per-file/<batch>/retry/<n>.json`, numbered by the retry's own
   list. A file still without a
   valid result is `unreviewed`, with the reason `no valid result after
   retry`.
4. Overall runs with the per-file summaries of the reviewed files.
5. Retries for the other passes: the same pass is called once more when its
   output is missing or invalid. If it still fails, the pass is recorded as
   `failed` with the reason, and its comments are lost.
6. Consolidation runs last. A missing or invalid result is retried once. If
   it still fails, the review raises `AgentError`, since there is no result
   to report.
7. Final status: consolidation's status, except it becomes `inconclusive`
   when any file is `unreviewed` or any pass `failed`.

Every call gets the review instructions and the description through the
input files. Each pass has its own prompt template in the package
(`prompts/<pass>.md`). The prompt states the pass's job and what is not its
job, as in the requirements' pass table.

## Testing

- Unit tests use only the core `FakeAgent`. Its `respond` function reads the
  request's prompt to find the output paths, and writes chosen result files:
  valid, invalid or missing.
- Git-based tests create small real repositories in `tmp_path`, covering:
  committed, staged, unstaged, untracked, renamed, deleted and binary files.
- Diffs-folder parsing gets tests for each header form.
- A gated integration test in the repo root's `tests/integration/` runs a
  real review with `ClaudeCodeAgent` on a tiny repository. It's skipped
  unless `WORKFLOWS_INTEGRATION=1` is set.

## Decided by the user

- First callers: a GitLab MR bot with a checkout, and a developer reviewing
  uncommitted changes.
- The labeled event log is what's used to investigate a bad review. Nothing
  more is needed for that.
- The workflow relies on its own internal prompts (the agent interface has no
  tool lists).

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
- A result file is valid or invalid as a whole. There is no partial salvage.
- A failed overall, test coverage or code patterns pass is recorded as
  `failed` and makes the status `inconclusive`. A failed consolidation raises.
- An empty change gives `approved`, with all passes skipped.
- The batching details: a rename or delete costs 10, the test-file naming
  rules, and the top-level folder break once a batch is half full.
