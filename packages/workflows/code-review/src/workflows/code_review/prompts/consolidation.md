# Consolidation

All other passes have finished. Their checked results are under $passes_dir:

- per-file/<path>.json: one per reviewed file;
- test-coverage/result.json, code-patterns/result.json and overall/result.json, when the pass
  produced a usable result.

This is the state of the review so far:

$coverage

Your job:

- Write the final list of new comments, which are the comments without a "reply_to". Keep
  each one as it is, or merge duplicates into one comment with new text.
- Drop any new comment that repeats an existing open thread.
- Set the final status.

Not your job: finding new issues. Never add a comment about something no earlier pass raised.

Replies and thread actions from the earlier passes are kept automatically. Do not copy them
and do not remove them. You may add your own replies and thread actions, but only on the
threads you may act on.

The status is one of:

- "changes_requested": the change needs work. Choose it when any remaining comment asks for
  a change, or when one of the reviewer's own open threads (its first message is by the
  reviewer) still blocks, even if nothing new was found.
- "approved": nothing blocks the change. Comments may still be present.
- "inconclusive": only when you cannot judge at all.

Write your result to the result file below, in this format:

{"status": "approved", "comments": [], "thread_actions": []}

Result file: $output_folder/result.json

$shared
