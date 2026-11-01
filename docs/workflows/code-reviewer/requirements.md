# Code Reviewer: Requirements

Date: 2026-10-31

## Purpose

Review a set of code changes and return a verdict with comments. The reviewer
doesn't know anything about platforms or team practice. End products such as
a GitLab MR bot or an IDE plugin translate their own data into this input and
apply the output themselves.

## Input

Every input is optional, but at least one of **diffs** or **codebase** must be
present.

### Diffs

- A folder that mirrors the repository layout.
- It holds one diff per changed file, in `git diff` (unified diff) format.
  Example: `diffs/src/app/main.py.diff`.
- A renamed file's diff is stored under its new path. A deleted file's diff
  is stored under its old path.

### Codebase

- A path to the code with the changes applied, committed or uncommitted.
- The id of the commit the changes start from.
- With both, the reviewer can compare the current state against the base
  commit, uncommitted edits included, and read the surrounding code.

### Discussions

- One or more JSON files in a format this workflow defines. All files are
  read as one list of threads.
- Each thread has:
  - **id**: lets output comments and actions refer to the thread.
  - **target**: a line or range in a file, a whole file, or the whole change.
  - **state**: `open` or `resolved`.
  - **messages**: the comments in the thread.
- End products convert platform threads (GitLab, GitHub) into this format.

### Description

Free text describing the change.

### Change author and reviewer name

- **Change author:** the name of the person who made the change, for example
  the MR author. Messages in threads by this name are the author's replies.
- **Reviewer name:** the name the reviewer's own comments appear under, for
  example the bot's account. Messages by this name are the reviewer's own
  earlier comments and replies, which it can see and follow up on.
- Both are matched against the `author` of thread messages.

### Review instructions

- Free text telling the reviewer what to focus on or which rules to apply.
- A team's own review guidelines also go here.

## Agents

- The end product picks the agents and models and passes them in. The
  workflow never chooses a model.
- The reviewer takes one required **default agent**.
- It also takes an optional **agent for each pass**: per-file, overall, test
  coverage, code patterns and consolidation. A pass without its own agent
  uses the default.
- Every agent must be able to write result files into a folder the workflow
  chooses.

## Review passes

The review is split into passes. Each pass has a narrow job so that two
passes don't raise the same issue.

| Pass | Runs | Job | Not its job |
|---|---|---|---|
| **Per file** | In batches covering every changed file (see below) | Bugs, logic and readability in each file's change, reading surrounding code as needed. Writes a short summary of what changed in each file. | Problems that span files |
| **Overall** | Once | Whether the change does what the description says, consistency across files, and anything missing (e.g. an API changed but its callers weren't). Gets the per-file summaries as input. | Line-level bugs |
| **Test coverage** | Once | Whether new and changed behavior is tested and whether tests were updated where needed. Judged by reading code, not by running tests. | Test code quality, which the per-file pass covers |
| **Code patterns** | Once, only when a codebase is given | Whether the change follows the codebase's existing conventions: naming, error handling, structure, and how similar things are done elsewhere. | General correctness |
| **Consolidation** | Once, last | Removes duplicate comments across passes, and new comments that repeat an existing open thread. Sets the final status, counting the reviewer's own unresolved threads. | Finding new issues, or removing replies and thread actions from earlier passes |

### Order

1. Per-file batches, test coverage and code patterns run in parallel.
2. Overall runs next, because it needs the per-file summaries.
3. Consolidation runs last.

### Rules that apply to every pass

- Every pass receives the review instructions.
- Existing discussion threads go to the pass that matches their target:
  - Threads on a file or a line go to the per-file batch holding that file.
  - Threads on the whole change go to the overall pass.

  That pass decides whether to reply to, resolve or reopen each thread.
- Every pass can read all existing threads as context, so it doesn't raise
  again what's already been raised. Only the pass a thread is routed to may
  reply to it or act on it.
- Replies and thread actions from the routed passes always reach the output.
  Consolidation can't remove them.
- When a pass is skipped, for example code patterns with no codebase, the
  skip is recorded in the coverage report.

## Every changed file must be reviewed

The workflow guarantees coverage. It does not rely on the agent remembering
to review every file.

1. **Workflow code builds the list of changed files.**
   - It uses the diffs folder when one is given.
   - Otherwise it uses the codebase: everything changed since the base
     commit, plus uncommitted and untracked files.
2. **Files are split into batches** (see below). Each batch is one agent
   session.
3. **The agent writes one result file per reviewed file.** It holds the file
   path, a short summary of the change, and the file's comments (possibly
   none). The summary serves as proof that the file was read.
4. **After each session, the workflow checks the result files.** Every file
   in the batch must have a valid result file.
5. **Missing or invalid results are retried once.** The agent runs again on
   only the files that lack a valid result.
6. **A file that still has no valid result is recorded as unreviewed.** If
   any file is unreviewed, the status cannot be `approved`. It becomes
   `inconclusive`.
7. **Binary files are the only files skipped automatically.** Each skip is
   recorded with its reason.

## Batching

1. **Size budget.** Each batch is filled until it reaches a limit on changed
   lines (added plus removed).
2. **File cap.** Each batch holds at most a set number of files.
3. **Hard limits.** A file that would breach either limit goes into the next
   batch.
4. **Related files stay together.** Files are sorted by path, and batches
   break at top-level directory boundaries once they are filled enough.
5. **Large files go alone.** A file whose diff exceeds the size budget by
   itself gets its own batch. A single file is never split across sessions.
6. **Renames and deletions count as files.** Every file, a deleted or
   renamed one included, counts toward the file limit. A deleted file adds no
   lines to the line limit.

### Settings

These have defaults and can be set per workflow instance.

| Setting | Default |
|---|---|
| Changed lines per batch | 1,000 |
| Files per batch | 10 |
| How full a batch must be before it breaks at a top-level directory | 0.5 |

The defaults are starting points and will be tuned after measuring results.

## Output

The output is returned as Python objects through the API and can be
converted to JSON.

### Status

| Status | Meaning |
|---|---|
| `approved` | Nothing blocks the change. Comments may still be present. |
| `changes_requested` | The change needs work before it is accepted. |
| `inconclusive` | The reviewer could not judge properly, for example because context was missing, the change was too large, or some files could not be reviewed. |

- The consolidation pass sets the status.
- The status cannot be `approved` while any changed file is unreviewed.
- Failures such as invalid input or an agent crash raise errors and are never
  reported as a status.

### Comments

A list. Each comment has:

- **target**:
  - a line or range of lines in a file,
  - a whole file,
  - or the whole change.
- **body**: the comment text.
- **reply_to** (optional): the id of an existing discussion thread this
  comment replies to.

Line numbers refer to the file after the change. Comments on deleted lines
use the line numbers from before the change.

### Thread actions

- A list, separate from comments.
- Each action is a thread id plus `resolve` or `reopen`.
- To reply and resolve in one go, the output contains a reply comment and a
  `resolve` action on the same thread.
- The end product decides whether to apply an action, for example when the
  reviewer isn't allowed to resolve other people's threads.

### Coverage report

- **For each changed file:**
  - `reviewed`, `skipped` or `unreviewed`,
  - the reason, for skipped and unreviewed files.
- **For each pass:** whether it ran or was skipped, and why.

## Out of scope for now

- Comment severity levels.
- Keeping a source file and its test file in the same batch. Teams name tests
  differently, so this can't be done reliably without an agent.
- Splitting a single large file across sessions.
- Running tests. The test coverage pass judges by reading code only.

## Still to design

- The Python API surface, including how agents and settings are passed to a
  workflow instance.
- The agent interface and how the reviewer uses it.
- Exact JSON schemas for discussions, per-file result files and output.
