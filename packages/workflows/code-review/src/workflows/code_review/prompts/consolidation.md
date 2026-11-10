# Consolidation

All other stages and review agents have finished. Their checked results are under
$results_dir, each one only when it produced a usable result:

$result_files

This is the state of the review so far:

$coverage

Your job:

- Write the final list of new comments, which are the comments without a "reply_to". Keep
  each one as it is, or merge duplicates into one comment with new text.
- Drop any new comment that repeats an existing open thread.
- Set the final status.

Not your job: finding new issues. Never add a comment about something no earlier stage or
agent raised.

Replies and thread actions from the earlier stages are kept automatically. Do not copy them
and do not remove them. You may add your own replies and thread actions, but only on the
threads you may act on.

The status is one of:

- "changes_requested": the change needs work. Choose it when a remaining comment is a problem
  that must be fixed before the change can be accepted, or when one of the reviewer's own
  threads (its first message is by the reviewer) is still open and blocking, even if nothing
  new was found. Judge a thread after applying the thread actions in the earlier results: a
  thread an earlier stage resolved no longer blocks, and one it reopened does.
- "approved": nothing blocks the change. Comments may still be present; suggestions and small
  remarks alone do not block.
- "inconclusive": only when you cannot judge at all.

Write your result to the result file below, in this format:

{"status": "approved", "comments": [], "thread_actions": []}

Result file: $output_folder/result.json

$shared
