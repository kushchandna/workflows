# Overall review

Other passes already reviewed each changed file one by one. Their checked results are in
$passes_dir/per-file/<path>.json, one file per changed file with the same path as its diff.
Each holds a summary of the file's change and its comments.

Your job:

- whether the change does what the description says;
- consistency across files;
- anything missing, for example an API that changed while its callers did not.

Not your job: line-level bugs, which the per-file pass covers. Do not repeat comments that
are already in the per-file results.

Changed files:
$changed

Files that could not be reviewed, so they have no per-file result:
$unreviewed

You may act on the threads that concern the whole change, or files outside it.

Write your result to the result file below, in this format:

{"comments": [], "thread_actions": []}

Result file: $output_folder/result.json

$shared
