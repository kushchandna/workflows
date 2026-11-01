## The change under review

Description of the change:
$description

Review instructions from the team (follow them):
$instructions

## Where to read

- Codebase, with the change applied: $codebase
- The change as diffs, one per changed file: $diffs_dir/<path>.diff
- All existing discussion threads (JSON): $threads_file
- Checked results of the passes that already finished: $passes_dir

Read as much of the codebase as you need. Never run the code or its tests, and never
change any file in the codebase.

## Discussion threads

$people

Read all existing threads as context, so that you do not raise again what has already been
raised. You may only reply to, resolve or reopen the threads on the "Threads you may act on"
line below.

- To reply to a thread, write a comment whose "reply_to" is the thread id. Give it the
  thread's own target.
- To resolve or reopen a thread, add a thread action: {"thread_id": "<id>", "action": "resolve"}
  or "reopen". Resolve a thread only when the change really addresses it.
- Weigh the change author's replies before resolving a thread.
- Comments by the reviewer are your own earlier comments. Follow up on them and do not
  repeat them.

Threads you may act on: $thread_ids

## Writing your result

Write only inside your output folder. Every result is a plain JSON file. Write the JSON only,
with no Markdown fences and no other text in the file.

Output folder: $output_folder

A comment is {"target": <target>, "body": "<text>", "reply_to": null}. A target is one of:

- {"type": "lines", "path": "<path relative to the repository root>", "start_line": 10,
  "end_line": 12, "side": "new"}: lines of the file after the change. For deleted lines use
  "side": "old" with the line numbers from before the change.
- {"type": "file", "path": "<path>"}: a whole file.
- {"type": "change"}: the whole change.

Write paths plainly, relative to the repository root, such as src/a.py: no leading "./",
no trailing "/" and no "..". Point at lines whenever you can. Comments may also point at unchanged files, for example a
caller that was not updated. Every line number must exist in the file.
