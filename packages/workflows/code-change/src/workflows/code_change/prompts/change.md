Result file: $result_file
Attempt: $attempt of $max_attempts

You are making one change to the code in $codebase. The person who wrote the instruction
below has already decided what the change is. Carry it out; do not redesign it.

## The instruction

$instruction

## Commit instructions

$commit_instructions

## Rules

- Make the smallest change that does everything the instruction asks, and nothing else. No
  unrelated refactoring, renaming or reformatting.
- Follow any testing or validation guidance in the instruction.
- Commit all of your work, in one or more commits, following the commit instructions. Leave
  nothing uncommitted and no untracked files behind.
- Do not amend, rebase or reset commits, switch branches, push, or touch any remote. The one
  exception is putting the repository back when a later section tells you to.
- Stop and report "needs_human" instead of guessing when any of these is true:
  - the change needs a design decision that the instruction does not make;
  - the change turns out to be much larger than the instruction suggests;
  - the instruction does not make sense against the code;
  - no change is needed, for example because it is already fixed or was a false positive.

  When you stop, leave the code exactly as it was.

## Checks

When you are done, the workflow runs these commands from $codebase. Each must exit with 0.
Run them yourself before you commit. If a check writes files that git does not ignore (reports,
coverage, caches), the attempt fails: do not commit those files, git-ignore them.

$checks

$retry## Reporting

Write the result as a JSON file at the path on the first line of this prompt. The file holds the
JSON only: no Markdown fences and no other text.

If you made the change and committed it:

    {"outcome": "done", "title": "<one line summary>", "description": "<what changed and why, written for a merge request>"}

If you stopped:

    {"outcome": "needs_human", "reason": "<what a human has to decide, and why>"}
