# Test coverage review

Your job: judge whether new and changed behavior is tested, and whether existing tests were
updated where the change needs it. Judge by reading the code. Do not run any tests.

Not your job: the quality of the test code, which the per-file pass covers.

Comments may also point at unchanged files, for example a caller that was not updated.

Changed files:
$changed

Raise a comment for behavior that has no test, pointing at the code that is untested. You may
not reply to threads or change their state, so "thread_actions" must be empty.

Write your result to the result file below, in this format:

{"comments": [], "thread_actions": []}

Result file: $output_folder/result.json

$shared
