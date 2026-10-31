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

### Review instructions

- Free text telling the reviewer what to focus on or which rules to apply.
- A team's own review guidelines also go here.

## Output

The output is returned as Python objects through the API and can be
converted to JSON.

### Status

| Status | Meaning |
|---|---|
| `approved` | Nothing blocks the change. Comments may still be present. |
| `changes_requested` | The change needs work before it is accepted. |
| `inconclusive` | The reviewer could not judge properly, for example because context was missing or the change was too large. |

The reviewer sets the status directly. Failures such as invalid input or an
agent crash raise errors and are never reported as a status.

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

## Out of scope for now

- Comment severity levels.

## Still to design

- The Python API surface.
- The agent interface and how the reviewer uses it.
- Exact JSON schemas for discussions and output.
