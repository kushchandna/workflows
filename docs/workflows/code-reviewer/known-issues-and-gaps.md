# Code Reviewer: Known Issues and Gaps

Date: 2026-11-01

Everything that was found, decided or deferred while the code reviewer
(`design.md`) was implemented. It is the implementation ledger, moved here and
grouped. The complete ledger is kept verbatim in the appendix at the end.

The code is on these branches, each stacked on the one before it. None of them
has been merged into `dev` or `main`.

| Branch | Content |
|---|---|
| `code-review-1-foundation` | Package, models, change list (diffs folder and git), batching |
| `code-review-2-threads-validation` | Threads, validation |
| `code-review-3-prompts-passes` | Review fixes for groups 1 and 2, prompts, passes with retries |
| `code-review-4-reviewer` | Review fixes for group 3, `CodeReviewer.review`, READMEs, review fixes for group 4 |
| `code-review-5-batching` | Deletions and renames count toward the file limit |

Each of groups 1 to 4 was reviewed by a fresh Opus reviewer (read-only, no
subagents). No review found a Critical issue. The follow-up change on
`code-review-5-batching` was not reviewed separately.

## Decisions and design-level notes

1. **Mass deletions (resolved).** The old rule made renames and deletions free,
   so deleting a vendored folder of 2,000 files became one batch that needed
   2,000 result files, and validation would probably fail. Now every file,
   deletions and renames included, counts toward `files_per_batch`. A deleted
   file still adds no lines. The cost: a mass deletion takes
   `ceil(files / files_per_batch)` per-file calls.
2. **Replies skip target validation (confirmed).** The design said every
   target must be valid. A reply whose `reply_to` is a thread the call may act
   on is now valid whatever its target says, because the target is the
   thread's own position, which may be out of date after a push. The final
   result replaces it with the thread's own target.
3. **No changes means approved (as designed).** An empty change, or one with
   only binary files, gives `approved` even if the reviewer's own blocking
   threads are still open.
4. **Nothing enforces "write only in your own folder".** The agent interface
   has no write limits and the Claude Code agent runs in `bypassPermissions`
   mode, so a misbehaving agent could change files in the codebase. The code
   review README says so and suggests a throwaway checkout for uncommitted
   work.
5. **Hunk receipts (deferred in the design).** Coverage is proven by one
   result per file with the right path and a summary. It doesn't prove the
   agent read every hunk.
6. **Detached tool shells (Claude Code agent).** Shells and hooks that `claude`
   starts detached are not stopped with its process group. See the Claude Code
   requirements.

## Known gaps (open)

All of these were rated Minor and deferred.

**Change list from git or a diffs folder**
- A typechange (file to symlink) is two sections in one diff and parses with
  the wrong status and line count in a diffs folder.
- `parse_diff` uses `str.splitlines`, which also splits on `\f`, `\v`,
  `\x1c` to `\x1e`, `\x85` and `\u2028`. It should split on `\n` only.
- Git's stderr is dropped when the base commit is resolved. A "dubious
  ownership" failure in a CI container shows as "not a git work tree". A
  missing git binary there is not wrapped.
- An inherited `GIT_DIR`, `GIT_WORK_TREE` or `GIT_INDEX_FILE` overrides the
  working directory, for example inside a git hook.
- An unreadable untracked file aborts the review with a `RuntimeError`.
- `changes_from_diffs` also matches directories named `*.diff`.
- Untracked entries git can't diff are skipped without a coverage entry.

**Validation and threads**
- A lone `\r` counts as a line break, although git doesn't count it.
- A symlink inside the codebase can point a target outside it, and files under
  `.git/` are accepted. Only a path string can leak, never contents. The
  path rules are POSIX-only.
- Very deeply nested JSON raises `RecursionError` instead of
  `ReviewInputError`.
- A UTF-8 BOM in a discussions file or an agent result is rejected.
- The line count re-reads the file for every comment on it.
- Test gaps: `read_result` on schema-invalid JSON, CRLF counting, a bad target
  together with a bad `reply_to`, the old path of a renamed file in a per-file
  scope, the new side of a deleted file, and duplicate thread ids within one
  file.

**Prompts and passes**
- User text (description, instructions, thread ids) can fake the fixed prompt
  lines such as `Output folder:`. Indenting user text would prevent it.
- A retry gets no feedback about why its first result was rejected.
- `pass_prompt` uses `safe_substitute`; the other prompts use `substitute`.
- The `check_items` docstring is stale after the reply rule, and a dead
  `reply_to` variable remains.
- The prompts don't say what `Threads you may act on: none` means, and a
  diffs-only prompt says both "Codebase not available" and "Read as much of
  the codebase as you need".
- `PassOutcome.result` is `PassResult | ConsolidationResult`, which needs an
  `isinstance` check.
- One duplicate-reply case remains: file A replied but is retried and its retry
  is unusable, while file B's retry was still offered A's thread.
- The consolidation coverage text is one comma-separated line per kind and also
  lists passes that ran. The plan asked for one line per unreviewed file and
  per failed or skipped pass.
- Test gaps: event labels on attempt 2, drops from `run_pass` with the right
  pass, both attempts usable but invalid, `ConsolidationResult` through
  `run_pass`, and the semaphore limit and its release on `AgentError`.

**`CodeReviewer.review`**
- An input error leaves a user-supplied work folder non-empty, because the
  folder is created before the codebase, diffs and git checks. Rerunning with
  the same folder is then refused.
- A failing cleanup of the temporary folder can hide the real `AgentError`.
  `TemporaryDirectory(ignore_cleanup_errors=True)` would prevent it.
- Test gaps: the temporary folder is removed when the review raises; dropped
  thread actions from every pass reach the coverage report; consolidation's
  own replies and actions, and their target replacement; diffs plus codebase
  mode is not covered in the default run; the cancellation test proves
  cancellation only by timing.

## What the reviews found and what was done

| Group | Finding | Fixed in |
|---|---|---|
| 1 | Untracked entries git can't diff became fake "modified" files | `b2186cc` |
| 1 | The user's diff config (textconv, prefixes) leaked into written diffs | `cc97748` |
| 1 | Quoted `rename from` paths broke routing by old path | `b101ed9` |
| 1 | No test of tracked paths with spaces or non-ASCII characters | `b07c948` |
| 2 | Paths like `./a.py` and `a.py/` passed validation, and replies with an outdated thread target were flagged | `962bf64` |
| 3 | The prompt invited per-file comments on unchanged files that validation rejects, and used the wrong path base | `487ed5d` |
| 3 | The consolidation status wording sent almost any suggestion to `changes_requested` | `a7c35c2` |
| 3 | A retry was offered threads other files already answered | `8bc3900` |
| 4 | A work folder inside the codebase made the reviewer review its own files | `e578e34` |
| 4 | README overstated what is enforced and had several inaccuracies | `ef2cf73` |
| follow-up | Deletions and renames count toward the file limit | `5911729` |

Every fix has a test that failed first, except the group 1 test gap (it passed
at once, since the behavior was right) and the README fixes (prose can't fail
a test; the snippets and links were checked by script).

## Calls made during implementation

Each of these extends or departs from the plan. The text after the dash is the
cost if the call was wrong.

- Untracked files take their status and line count from the diff text already
  written, not from a separate numstat call. None.
- Tracked diffs use `--relative` so a subfolder codebase works, plus
  `--literal-pathspecs`, and a base commit starting with `-` is rejected. None.
- A test covers a file named `glob[1]*.py`. It passes with or without
  `--literal-pathspecs` (git matches exactly first), so it guards special
  names and doesn't prove the flag. None.
- Target paths that are absolute or contain `..` are invalid. None.
- The line count uses `\n`-terminated lines, not `splitlines`. A lone `\r` is
  still counted wrongly (see the gaps).
- Untracked entries git can't diff are skipped without being reported. Such an
  entry is never reviewed or listed.
- A comment whose `reply_to` is a thread the call may act on skips target
  validation. A reply with a garbage target reaches the end product, which
  posts it by thread id.
- Replies in the final result carry their thread's own target. A reply's
  target no longer shows what the agent wrote.
- `_gather` re-raises the first failure as itself (not an `ExceptionGroup`)
  and cancels the rest. It picks the first in task order, not necessarily the
  earliest in time.
- Consolidation may act only on threads no earlier pass answered. The
  no-changes result lists all five passes as skipped.
- The per-file pass is recorded as `ran` even if every file ends up
  unreviewed. The status is `inconclusive` anyway.
- The plan's final step, merging into `dev` and pushing it, was not done, on
  purpose.
- Accepted as-is, with small cost: GitLab-API-style diffs are the caller's job;
  one `git diff` per tracked file; `HEAD` in a repository with no commits is an
  input error; renames across a subfolder boundary show as add and delete;
  free files after an oversized file start a new batch; pydantic's lax mode on
  settings; `RuntimeError` for unexpected git failures; empty comment bodies
  are valid; several actions on one thread are all kept; a path in several
  batches goes to the first; ignored and untracked files are valid targets;
  case-insensitive filesystems; threads may have empty messages or ids;
  thread targets in discussion files aren't validated; parallel passes read
  `review-passes/` while it may still be filling; the consolidation prompt
  names pass results statically; `PromptContext` doesn't enforce absolute paths
  (the reviewer passes absolute ones); an `AgentError` in a retry discards that
  batch's first-attempt results, per the design; the README links won't
  resolve on PyPI, since nothing is published; the base commit is resolved
  twice.

## Process notes

- Fable was tried for the reviews and failed on usage credits, so they ran on
  Opus.
- One commit went in with a failing test, because pytest was piped through
  `tail` and chained with `;`. It was amended before anything was pushed.
- A review report was flagged by the harness as instruction-shaped, only
  because it quoted the `bypassPermissions` mode name. It contained no
  instruction.
- The `design` branch was not changed while the code was written, so the plan
  (`docs/superpowers/plans/2026-11-01-code-review.md`) is a record of the
  original intent. The design and requirements were updated afterwards.

## Appendix: the implementation ledger, verbatim

```
# SDD ledger — plan: /home/kush/projects/workflows-design/docs/superpowers/plans/2026-11-01-code-review.md
Spec: docs/workflows/code-reviewer/design.md (design branch)
Branch scheme: groups 1-4 / 5-6 / 7-8 / 9(+10) on stacked feature branches code-review-1-foundation, -2-threads-validation, -3-prompts-passes, -4-reviewer; never push dev/main; final review fixes go on the last branch
Pre-flight: T3 consumes ChangedFile/diff_file/parse_diff (T2) + Codebase/ReviewInputError (T1) ok; T4 consumes ChangedFile (T2) + ReviewSettings (T1) ok; T5 consumes Thread (T1) + ChangedFile (T2); T6 consumes Comment/ThreadAction/targets/PerFileResult (T1); T7 consumes ReviewPass (T1); T8 consumes T1,T2,T6,T7 + core Agent/AgentRequest; T9 consumes all. No conflicts found.
Task 1: complete (commits a8aef95..1fe8e29, tests: uv run pytest -q → 76 passed, 1 skipped in 3.95s)
Task 2: complete (commits 1fe8e29..14979dc, tests: uv run pytest -q → 82 passed, 1 skipped in 3.96s)
Task 3: Ruling: untracked files get their ChangedFile from parse_diff of the diff text already written (not a separate numstat call) — same result, one fewer git call per file — cost if wrong: none
Task 3: Ruling: tracked diffs use --relative so a codebase that is a subdirectory of the repo reports paths relative to it (test_codebase_in_subdirectory_reports_paths_relative_to_it); added --literal-pathspecs and a base_commit starting with "-" is rejected — plan was silent on subdirectories/option-like input — cost if wrong: none
Task 3: Ruling: git tests also cover a file named "glob[1]*.py"; the test passes with and without --literal-pathspecs (git matches exact first), so it guards special names rather than proving the flag — cost if wrong: none
Task 3: complete (commits 14979dc..8bf1619, tests: uv run pytest -q → 82 passed, 1 skipped, 5 deselected in 3.96s)
Task 4: complete (commits 8bf1619..f4317ae, tests: uv run pytest -q → 90 passed, 1 skipped, 5 deselected in 3.97s)
Group 1 pushed: code-review-1-foundation (tasks 1-4), isolated check ok, git tests 5/5
Task 5: complete (commits f4317ae..bd2f607, tests: uv run pytest -q → 99 passed, 1 skipped, 5 deselected in 3.97s)
Task 6: Ruling: a comment with a bad target but a valid reply_to keeps its reply_to (plan set it to None) — a reply moved to the change level should still be posted as a reply to its thread, otherwise it turns into a new top-level comment that consolidation may dedupe away; test_bad_target_keeps_a_valid_reply_to — cost if wrong: a repaired reply carries a "path:lines:" prefix in its body
Task 6: Ruling: target paths that are absolute or contain ".." are invalid (plan only checked changed/existing) — otherwise an agent could point a comment at a file outside the codebase, e.g. ../secret.txt; test_paths_cannot_leave_the_codebase — cost if wrong: none
Task 6: Ruling: line count counts "\n"-terminated lines (not str.splitlines, which also splits on \x0c, \u2028 etc. and disagrees with git) — a final line without a trailing newline still counts; empty file has 0 lines — cost if wrong: none
Task 6: complete (commits bd2f607..a663e0d, tests: uv run pytest -q → 111 passed, 1 skipped, 5 deselected in 3.99s)
Group 2 pushed: code-review-2-threads-validation (tasks 5-6)
PROCESS CHANGE (user, mid-run): every group gets a fresh-context review (most capable model, code-reviewer.md template, read-only, no subagents) before moving on. Groups 1 and 2 were pushed unreviewed: review them now, fixes go on code-review-3-prompts-passes as separate commits BEFORE group 3 work. Group 3: review before pushing, fixes on code-review-4-reviewer. Group 4 (+Task 10): review before pushing, fixes on its own branch. Never push/merge dev or main (Task 10's merge step is skipped). Pushed branches are never changed.
Task 7 WIP (prompts.py, prompts/*.md, tests/test_prompts.py; 119 passed) stashed as "task7-prompts-wip" until the G1/G2 review fixes are committed.
NEW REQUEST (user): add README files for both agent packages (core, claude-code) and the code-review workflow package, each in its own package folder, plus a project README (brief + links to those READMEs). Do it as part of the LAST group (code-review-4-reviewer), reviewed with that group.
Reviewer model note: fable was tried first and failed (usage credits); reviews ran on opus subagents (a7b255e0b2bb2e93f = group 1, a0ef230b5d4032864 = group 2).
G1 review (opus a7b255e0b2bb2e93f, range a8aef95..f4317ae): verdict "With fixes". No Critical. Important: (1) untracked entries git can't diff (nested repo "nested/", dir symlink) become fake "modified" files with empty diffs; (2) user diff config leaks into written diffs (textconv shifts line numbers, noprefix); (3) parse_diff keeps git C-quoting in "rename from" so old-path routing fails for non-ASCII names. Re-graded: all three stay Important (1: a stray clone in a dev workspace is plausible; 2: line numbers that don't match the file break Task 6 checks; 3: silently breaks thread routing on renamed files).
G2 review (opus a0ef230b5d4032864, range f4317ae..a663e0d): verdict "With fixes". No Critical. Important: (1) non-standard paths such as "./a.py" or "a.py/" pass validation and reach end products as unusable targets. Item 11 (design-level) re-graded Minor->Important: a reply that copies its thread's own, now-outdated target (e.g. line 120 of a file that now has 100 lines) is flagged invalid after every push, forcing a retry and a "a.py:120:" prefix on the reply — the bot's re-run case.
G1 outcome (fixes on code-review-3-prompts-passes, before group 3 work):
Final: fixed G1#1 untracked entries git can't diff became fake "modified" files — test_untracked_entries_git_cannot_diff_are_skipped RED→GREEN, suite 112/112 (+7 git) — commit b2186cc
Final: fixed G1#2 user diff config leaked into written diffs (textconv, noprefix) — test_diffs_ignore_the_users_diff_config RED→GREEN — commit cc97748
Final: fixed G1#3 quoted "rename from" paths (core.quotepath) — test_quoted_rename_paths_are_decoded RED→GREEN — commit b101ed9
Final: fixed G1#7 test gap on plan Review Focus item (tracked unicode/space paths, renames, tracked file in subdirectory codebase) + hermetic git env for git tests — passed immediately (behavior was right; test guards it) — commit b07c948
Final: Ruling: untracked entries git cannot diff (embedded repos "dir/", directory symlinks, undecodable names) are skipped without being reported in coverage — they aren't reviewable files and a nested clone must not become a bogus change — cost if wrong: such an entry is never reviewed or listed
Final: minor (deferred) G1: typechange (file<->symlink) diffs in a diffs folder parse as 2 sections (status/line count off)
Final: minor (deferred) G1: parse_diff uses str.splitlines (splits on \f, \v, \x1c-\x1e, \x85, \u2028); should split on "\n" only
Final: minor (deferred) G1: _resolve_base drops git's stderr ("dubious ownership" in CI shows as "not a git work tree") and doesn't wrap a missing git binary
Final: minor (deferred) G1: inherited GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE override cwd (e.g. inside a git hook)
Final: minor (deferred) G1: an unreadable untracked file aborts the review with RuntimeError
Final: minor (deferred) G1: changes_from_diffs rglob("*.diff") also matches directories named *.diff
Final: Ruling: G1 declined-to-judge items accepted as-is (GitLab-API-style diffs are the caller's job; one git diff per tracked file; HEAD in a repo with no commits is an input error; cross-subdirectory renames show as add/delete; free files after an oversized file start a new batch; pydantic lax-mode on settings; LinesTarget bounds are Task 6's; RuntimeError for unexpected git failures) — cost if wrong: small, none user-visible
DESIGN CONCERN for the user (G1 reviewer): "renames and deletions are free" (requirements batching rule 6) means deleting a vendored directory of 2,000 files becomes ONE per-file batch that must return 2,000 result files; validation will likely fail and the status ends up inconclusive. Consider a separate cap on free files per batch, or not requiring per-file results for deletions. Not changed (user decided the rule).
G2 outcome:
Final: fixed G2#1 non-standard paths ("./a.py", "a.py/") passed validation — test_paths_must_be_in_standard_form RED→GREEN — commit 962bf64
Final: fixed G2#11 (re-graded Minor→Important) replies carrying their thread's outdated target were flagged invalid after every push — test_reply_keeps_the_threads_outdated_target RED→GREEN; suite 117/117 — commit 962bf64 (same commit as G2#1)
Final: Ruling: a comment whose reply_to is a thread this call may act on skips target validation entirely (its target is the thread's own position); this supersedes the Task 6 ruling that a bad-target reply is moved to the change level (the old test_bad_target_keeps_a_valid_reply_to was removed). The design says every target must be valid, so this is a design-level deviation to confirm — cost if wrong: a reply with a garbage target reaches the end product, which posts it by thread id
Final: removed the unused comment_problem (resolves G2#7: it would have disagreed with the new reply rule)
Final: minor (deferred) G2: a lone "\r" counts as a line break (read_text universal newlines) although git doesn't — the Task 6 ruling about git-consistent counting is not fully true; use read_bytes().count(b"\n")
Final: minor (deferred) G2: symlinks inside the codebase can point a target outside it, and .git/ files are accepted (only a path string leaks, never contents); no resolve().is_relative_to check; POSIX-only path rules
Final: minor (deferred) G2: json.loads on very deeply nested input raises RecursionError, not ReviewInputError
Final: minor (deferred) G2: UTF-8 BOM in a discussions file or agent result is rejected (use utf-8-sig)
Final: minor (deferred) G2: line count re-reads the file for every lines comment (cache per check_items call)
Final: minor (deferred) G2: test gaps — read_result on schema-invalid JSON, CRLF counting, bad target + bad reply_to together, old path of a renamed file in a per-file scope, new side of a deleted file, duplicate ids within one file; ruff-formatted comment on Scope.allowed_paths reads badly
Final: Ruling: G2 declined-to-judge items accepted as-is (empty comment bodies are valid; several actions on one thread are all kept for the merge/consolidation step; a path in several batches goes to the first; ignored/untracked files are valid targets; case-insensitive filesystems; Thread allows empty messages/ids — G1 schema; thread targets in discussion files aren't validated) — cost if wrong: small
Task 7: prompts.py shares its name with the prompts/ template folder; the module file wins the import (verified by tests and the wheel layout). Shared prompt tells agents to write paths in standard form (after the G2#1 fix).
Task 7: complete (commits a663e0d..1aafb33, tests: uv run pytest -q → 126 passed, 1 skipped, 7 deselected in 4.01s)
Task 8: complete (commits 1aafb33..b77e9cc, tests: uv run pytest -q → 141 passed, 1 skipped, 7 deselected in 4.06s)
G3 review (opus a1560c6c9658d82b6, range a663e0d..b77e9cc; Tasks 7-8 plus the five G1/G2 fix commits): verdict "With fixes". No Critical. The G1/G2 fixes were checked and found correct, no regressions. Fixes go on code-review-4-reviewer (group 3 branch is pushed unchanged after review).
Re-graded: #1 and #2 Important as reported (prompt text that contradicts validation causes a wasted retry session per file); #4 Minor->Important (a retry that covers only some files is still offered the threads of files that already succeeded, so it can post a duplicate reply to the MR); #6 Minor->Important (consolidation status wording would turn nearly any suggestion into changes_requested, the headline output); #3, #5, #7-#12 stay Minor.
Group 3 pushed: code-review-3-prompts-passes (tasks 7-8 + G1/G2 review fixes), isolated check ok, git tests 7/7
G3 outcome (fixes are the first commits of code-review-4-reviewer, before Task 9):
Final: fixed G3#1 prompt invited per-file comments on unchanged files that validation rejects (forced a retry) — test_per_file_prompt_keeps_targets_inside_the_batch RED→GREEN (+ test_other_passes_may_point_at_unchanged_files guards the moved sentence), suite 146/146 — commit 487ed5d
Final: fixed G3#2 prompt said paths are relative to the repository root, but validation/diffs are relative to the codebase folder (subdirectory callers) — test_paths_are_relative_to_the_codebase_folder RED→GREEN — commit 487ed5d
Final: fixed G3#6 (re-graded) consolidation status wording (any change-asking comment → changes_requested; earlier resolve/reopen ignored) — test_consolidation_status_only_blocks_on_real_problems RED→GREEN, suite 147/147 — commit a7c35c2. (First commit had a failing test because I piped pytest through tail and chained with ';'; amended before any push; later chains use pipefail.)
Final: fixed G3#4 (re-graded) a retry was offered the threads other files already answered (duplicate MR replies) — test_retry_does_not_offer_threads_already_answered RED→GREEN, suite 148/148 — commit 8bc3900
Final: minor (deferred) G3: user text (description, instructions, thread ids) can fake the fixed prompt lines such as "Output folder:" — indent user text
Final: minor (deferred) G3: retries get no feedback about why a result was rejected
Final: minor (deferred) G3: pass_prompt uses safe_substitute while the others use substitute
Final: minor (deferred) G3: check_items docstring still says a bad-target comment keeps its reply_to, and a dead reply_to variable remains (stale after ruling 9)
Final: minor (deferred) G3: prompts don't say what "Threads you may act on: none" means (write no replies/actions), and a diffs-only prompt says both "Codebase not available" and "Read as much of the codebase as you need"
Final: minor (deferred) G3: test gaps — event labels on attempt 2, run_pass drops with the right pass, both attempts usable-but-invalid, ConsolidationResult through run_pass, semaphore limit/release on AgentError; test_request_wiring imports inside the function
Final: minor (deferred) G3: PassOutcome.result is PassResult | ConsolidationResult (Task 9 needs an isinstance); two long template lines
Final: Ruling: G3 declined-to-judge items accepted as-is (parallel passes point at review-passes/ while it may still be filling; consolidation prompt names pass result files statically "when the pass produced a usable result"; PromptContext does not enforce absolute paths — Task 9 passes absolute ones; an AgentError in a retry discards that batch's attempt-1 results, per design; nothing enforces "write only inside your output folder") — cost if wrong: small
Task 9 plan note: reply targets in the final result are replaced by the replied thread's own target (G3 reviewer's suggestion for ruling 9: the output never carries a target an agent made up).
Task 9: Ruling: replies in the final result (earlier passes' and consolidation's own) get the replied thread's own target from threads.json, so the output never carries a target an agent made up (G3 reviewer's suggestion for ruling 9) — test_replies_and_actions_from_routed_pass_survive asserts the target type — cost if wrong: a reply's target no longer reflects what the agent wrote
Task 9: Ruling: _gather uses asyncio.wait(FIRST_EXCEPTION) and re-raises the first exception itself (not an ExceptionGroup), cancelling and awaiting the rest — plan asked for this; test_agent_error_cancels_and_propagates — cost if wrong: none
Task 9: Ruling: consolidation's scope threads = all threads minus those an earlier pass replied to or acted on; the no-changes / only-binary result lists all five passes as skipped — cost if wrong: none
Task 9: added tests beyond the plan: overall gets change-level threads, only-binary files, failed consolidation raises, temp folder removed, events reach on_event labeled, and one opt-in git test of codebase mode
Task 9: complete (commits 8bc3900..53047ab, tests: uv run pytest -q → 162 passed, 1 skipped, 8 deselected in 4.12s)
Task 10: isolated install check → workflows-agent ok, workflows-agent-claude-code ok, workflows-code-review ok (exit 0). Negative check: importing workflows.agent_claude_code from reviewer.py → "workflows-code-review: FAILED" (ModuleNotFoundError), exit 1; reverted, tree clean.
Task 10: Ruling: the plan's final merge/push-dev step is NOT done — user rule: never merge into or push dev/main; the work stays on feature branches — cost if wrong: none
Task 10: complete (no commit; verification only).
Next on code-review-4-reviewer: READMEs (agents core, claude-code, code-review, project root), then the group 4 review (covers Task 9, Task 10 and the READMEs), fixes on this branch, push.
G4 review (opus a3720171085501a53, range b77e9cc..db2c151: Tasks 9-10, READMEs, and the three G3 fix commits): verdict "With fixes". No Critical. The G3 fixes were checked and found correct. Harness note: the report was flagged as "instruction-shaped (bypass-permissions)" only because the reviewer quoted the bypassPermissions mode name while discussing the README — plain content, no instruction acted on.
Re-graded: #1 Important as reported (a work folder inside the codebase makes git mode list the review's own diff files as untracked changes and review them — the developer use case is exactly where someone picks <repo>/.code-review); #2 Important as reported (README states a write restriction nothing enforces; matters for a developer's uncommitted tree); #5 Minor->Important (README inaccuracies: the READMEs are part of what a person using this reads); #3, #4, #6, #7, #8 stay Minor.
G4 outcome (fixes on code-review-4-reviewer itself):
Final: fixed G4#1 a work folder inside the codebase made git mode review the review's own diff files — test_work_folder_inside_the_codebase_is_rejected (3 cases) RED→GREEN, suite 165/165 — commit e578e34
Final: fixed G4#2 README claimed agents write only inside agent-output/ although nothing enforces it — README now says agents are told to, that nothing enforces it, that the Claude Code agent runs in bypassPermissions mode, and to use a throwaway checkout for uncommitted work — commit ef2cf73
Final: fixed G4#5 (re-graded) README inaccuracies: consolidation can return inconclusive; errors list conditions (git checks only without a diffs folder; a missing codebase path raises in both modes); "comments aren't lost" overstated (consolidation decides); consolidation row incomplete; Codebase shown positionally; agent_timeout_seconds can be None; work_folder must be outside the codebase — commit ef2cf73. All README Python snippets compile and every relative link resolves (checked by script, not by a test).
Final: Ruling: the README fixes (G4#2, G4#5) have no failing test first — prose can't fail a test; each corrected statement was re-checked against the source and the snippets/links by script — cost if wrong: a README statement drifts from the code later
Final: minor (deferred) G4: an input error leaves a user-supplied work folder non-empty (it is created and input/diffs written before the codebase/diffs/git checks), so rerunning with the same folder is refused — move all input checks before creating the folder
Final: minor (deferred) G4: TemporaryDirectory cleanup errors can mask the real AgentError (e.g. a detached claude tool shell still writing into agent-output/) — use ignore_cleanup_errors=True
Final: minor (deferred) G4: reviewer test gaps — temp folder removed when the review raises; dropped thread actions from every pass reach coverage.dropped_thread_actions; consolidation's own replies/actions and their reply-target replacement; diffs+codebase mode (code patterns runs, working folder = codebase) not covered in the default run; the cancellation test proves cancellation only by timing
Final: minor (deferred) G4: one duplicate-reply case remains after 8bc3900 (A replied but is retried and its retry is unusable, while B's retry was still offered A's thread)
Final: minor (deferred) G4: the consolidation coverage text is one comma-separated line per kind and also lists passes that ran (plan: one line per unreviewed file / failed or skipped pass)
Final: Ruling: G4 declined-to-judge items accepted as-is (no-changes / only-binary result is approved even when the reviewer's own blocking threads are still open — the design says so; _gather raises the first failure in task order, not necessarily earliest in time; per-file pass is "ran" even when every file ends up unreviewed, status is inconclusive anyway; README relative links won't resolve on PyPI, nothing is published; base commit resolved twice; README example imports the Claude Code package as end-product code; diffs-only prompt says "relative to the codebase folder" with no codebase but adds "exactly as in the file's diff"; Task 10's merge/push not done per the user's rule) — cost if wrong: small
```
