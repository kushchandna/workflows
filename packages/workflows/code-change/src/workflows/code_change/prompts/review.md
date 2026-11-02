Result file: $result_file

You are reviewing a code change against the instruction that asked for it. This is a spec
review, not a code review.

- The codebase, with the change applied, is in $codebase.
- The whole change since the start is in $diff_file.
- The commits that make up the change:

$commits

## The instruction, with any testing or validation guidance

$instruction

## Your job

Answer three questions:

1. Does the change do everything the instruction asks?
2. Does it do anything the instruction did not ask for?
3. Did it follow the instruction's testing and validation guidance?

Do not judge code style or quality; another reviewer does that. Read the diff, and read the
surrounding code as far as you need. Judge the testing guidance by reading the diff and the
tests. Any configured checks have already passed, so do not run builds, tests or linters, or
anything else that writes files into the codebase: a new file there makes the whole run fail.
Do not change any file in the codebase, and do not run git commands that change the repository.

## Your verdict

- "pass": all three questions are answered well.
- "changes_needed": something is missing, extra, or does not follow the guidance. Write one
  comment per problem, saying what to do about it.
- "inconclusive": only when you cannot tell, for example because the instruction is
  ambiguous. Say why in "reason".

## Reporting

Write the result as a JSON file at the path on the first line of this prompt. The file holds the
JSON only: no Markdown fences and no other text.

    {"verdict": "pass"}

    {"verdict": "changes_needed", "comments": ["<problem and what to do>", "..."]}

    {"verdict": "inconclusive", "reason": "<why you cannot tell>"}
