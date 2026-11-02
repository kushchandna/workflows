# Code Change: Review Findings

Date: 2026-11-02

Six independent reviews (one per stacked branch, each by a separate Opus agent in its own git
worktree) of the `workflows-code-change` implementation. The reviewers worked read-only and
reproduced findings with scripts where they could. This file lists every finding in review order:
branch 1 to 6, most severe first within each branch, with the reviewer's own wording kept close.

- **CONFIRMED**: the reviewer reproduced it. **PLAUSIBLE**: reasoned only.
- **Disposition** says what happens next. `Fix (7)` means a fix in branch
  `code-change-7-review-fixes`. `Decision` means it waits for an answer from the user (see the end).
  `Deferred` means low severity, left for later.
- Scope chosen by the user: fix every **medium** and **high** finding in branch 7. Borderline
  ratings ("medium-low", "low-medium") are listed with the reason they were or weren't included.

Stack under review: `code-change-1-models` (base `dev`) → `-2-checks` → `-3-git` → `-4-prompts` →
`-5-loop` → `-6-changer`.

## Summary

| Branch | High | Medium | Low |
|---|---|---|---|
| 1 models | | | 2 |
| 2 checks | | 2 | 5 |
| 3 git | 1 | 5 | 4 |
| 4 prompts | | 2 | 7 |
| 5 loop | | 4 | 4 |
| 6 changer | | 2 | 3 |

On every branch the tests, ruff and pyright passed, and the isolated-install check passed on
Python 3.11 (on branch 6).

---

## Branch 1: `code-change-1-models` (Task 1)

No correctness bugs and no departures from the spec. Packaging, defaults, constraints, the
discriminated union, serialization round trips and the reader behaviour for most bad files all
matched.

### R1-1. Low, CONFIRMED: the readers trust the file kind and size
`formats.py:51`, `formats.py:59`. `read_change_report` and `read_review_report` call
`path.read_text()` without checking the file.
- A FIFO as `result.json` hangs the reader forever. A symlink to `/dev/zero` or a huge file raises
  `MemoryError`, which isn't caught.
- Contrived, since a normal agent writes a regular file. The plan says the readers return `None`
  for an "unreadable" file.
- Fix: require a regular file and cap the size, or at least catch `MemoryError`.
- **Disposition: Deferred.**

### R1-2. Low, CONFIRMED (test gap)
No test pins that `ChangeSettings` is frozen, that the review reader ignores extra keys and rejects
wrong types (`"comments": null`), or that a directory path returns `None`. The `"[" * 100000` case
is stopped by pydantic's own depth limit, so the `RecursionError` branch is never exercised.
- **Disposition: Deferred.**

---

## Branch 2: `code-change-2-checks` (Task 2)

Passed 15 runs in a row plus 24 parallel runs, no flakes. On Python 3.11 the branch fails
`test_background_child_does_not_hold_the_run` (the known `proc.wait()` problem, fixed by commit
`24a771b` in branch 6).

### R2-1. Medium, CONFIRMED: the output drain has no time limit
`checks.py:48` (`await reader`). `killpg` only reaches the shell's process group. Any descendant
that moved to another group or session still holds the pipe, and the run waits for it however long
that takes, whatever `check_timeout_seconds` says. Happens on every Python version and is not fixed
by the 3.11 polling fix.
- Scenarios (3.14, timeout 0.5 s): `echo start; timeout 5 sleep 5` returned after 5.0 s (coreutils
  `timeout` calls `setpgid`); `setsid sleep 4 & sleep 10` returned after 4.0 s; with no timeout,
  `setsid sleep 6 & sleep 0.1; exit 0` blocked 6 s after the shell had exited.
- Real cases: `timeout 3600 make test`, scripts using `set -m`, a daemon started with `setsid` that
  keeps stdout.
- Fix: put exit and EOF under one deadline. After `killpg`, wait for the reader only for a short
  grace period, then cancel it and close the pipe, keeping what was read.
- **Disposition: Fix (7).** Branch 6's reviewer reported the same bug (R6-1).

### R2-2. Medium, CONFIRMED: all output is buffered in memory
`checks.py:18-20`, `51`, `70-71`. `bytearray`, then `bytes(output)`, then `decode` keeps about 2 to
3 times the output in RAM, only to keep the last 20,000 characters. 500 MB of output took peak
memory from 33 MB to 1033 MB.
- Fix: stream chunks straight into `<k>.log`, keep only a bounded tail of bytes (about
  `4 * keep_chars + 4`), and set `truncated` from the total size.
- **Disposition: Fix (7).**

### R2-3. Low-medium, CONFIRMED: checks inherit the caller's stdin
`checks.py:27-33`. Scenario: `printf 'secret\n' | python run.py` with the check `read x; echo "got $x"`
printed the parent's input. An end product that speaks a protocol over stdin (MCP, LSP, a CI
runner) loses bytes to a check. A test that calls `input()` blocks until the 30-minute timeout
instead of failing at once with EOF. The design's literal `subprocess.run(...)` also inherits
stdin, so this is a design question.
- Fix: `stdin=asyncio.subprocess.DEVNULL`.
- **Disposition: Decision** (D2). Rated low-medium; included in branch 7 only if the user agrees.

### R2-4. Low, CONFIRMED (test gaps)
`tests/test_checks.py:51-59`, `33-36`. Truncation is only tested on ASCII (slicing bytes would pass
too); `test_timeout` doesn't check that output printed before the timeout is kept; nothing pins
`truncated=False` for an exact fit; the cancellation path (kill the group, re-raise
`CancelledError`) has no test. The reviewer checked by hand that the code is right.
- **Disposition: Deferred** (new tests for R2-1 and R2-2 will cover part of this).

### R2-5. Low, PLAUSIBLE: PID reuse before the kill
`checks.py:45`. On normal exit `killpg(proc.pid)` runs after asyncio has reaped the leader. If no
other group member is left, the PID number is free and could be reused by an unrelated process
that then calls `setpgid`/`setsid`. Tiny gap on this branch; up to 50 ms with the 3.11 polling fix.
- Fix: accept and note it in a comment, or poll more tightly.
- **Disposition: Deferred.**

### R2-6. Low, PLAUSIBLE: only `ProcessLookupError` is caught
`checks.py:12-15`. On macOS `killpg` is reported to raise `PermissionError` when the group holds
only zombies, which would crash the run. Not testable on Linux.
- Fix: also ignore `PermissionError` (or catch `OSError`) on the cleanup `killpg`.
- **Disposition: Deferred.**

### R2-7. Low, CONFIRMED: negative exit code when the shell dies from a signal
`checks.py:51`. `kill -TERM $$` gives `exit_code=-15` and a reason `... exited with -15`; a child
killed by a signal gives 128+n. Same as `subprocess`; the spec is silent. Noted as a conscious choice.
- **Disposition: Deferred** (no change planned).

Checked and fine: cancellation (group killed, `CancelledError` propagates, reader cancelled, no
leaked task or ResourceWarning), timeout results, `keep_chars=0`, non-UTF-8 decoding, the log files,
`checks_reason` strings, missing commands (127), `cwd` and environment.

---

## Branch 3: `code-change-3-git` (Task 3)

23 git tests pass. Every finding below was reproduced against throwaway repositories with git 2.43
and a hermetic config. Scratch prototype of a fixed reset: `e4.py`.

### R3-1. HIGH, CONFIRMED: the reset deletes git-ignored files the agent committed
`git.py:147-148`. `reset --hard` and `checkout -f` remove every file tracked in the current HEAD
that is absent from the base. If a git-ignored file existed before the run and the agent committed
it, the reset deletes it. This breaks the requirement that git-ignored files stay.
- Scenario: `.env` (ignored, `SECRET=1`) exists at the start. The agent runs `git add -f .env &&
  git commit`, or edits `.gitignore` and runs `git add -A`, on the starting branch or another.
  After `reset()` the tree is clean on `main` at the base, but `.env` is gone. Edits made after the
  agent's commit are lost for good; the committed copy only survives in the reflog.
- Fix: run `git read-tree <base>` before the checkout and reset, so the index no longer tracks
  those paths and they are left alone.
- **Disposition: Fix (7).**

### R3-2. Medium, CONFIRMED: the reset runs the repository's hooks
`git.py:145,147`. A failing `post-checkout` hook stops the reset halfway: `checkout` exits with the
hook's status and `_run` raises before `reset --hard` and `clean`.
- Scenario: a git-lfs `post-checkout` hook on a machine without git-lfs. The agent commits on `main`
  and then switches branch. `reset()` raises and `main` still holds the agent's commit. With a
  detached start the checkout runs on every reset, so a failing hook always leaves untracked files.
- Fix: pass `-c core.hooksPath=/dev/null` to the reset's commands. This also covers
  `reference-transaction` hooks and hooks the agent installed.
- **Disposition: Fix (7).**

### R3-3. Medium, CONFIRMED: a tag named like the branch breaks the reset and the commit check
`git.py:48`. `symbolic-ref --short` returns `heads/main` when a tag `main` also exists.
- Tag exists at the start: the recorded branch is `heads/main`; `git checkout -f heads/main --`
  detaches HEAD; the repo ends detached at the base while `refs/heads/main` keeps the agent's commits.
- The agent creates tag `main`: the commit check wrongly reports `switched from main to heads/main`.
- Fix: record and compare the full ref (`symbolic-ref -q HEAD`, which design.md says).
- **Disposition: Fix (7).**

### R3-4. Medium, CONFIRMED: `clean -fd` skips untracked nested repositories
`git.py:149`. Scenario: the agent clones or `git init`s into `vendor/lib/`. After `reset()`,
`git status` shows `?? vendor/lib/` and the next `open_repository` raises `work tree is not clean`.
Safe to remove: such a directory cannot have existed at the start, because the clean-tree check
would have rejected it, and ignored ones are untouched without `-x`.
- Fix: `git clean -q -ffd`.
- **Disposition: Fix (7).**

### R3-5. Medium, CONFIRMED: the reset fails if the agent renamed or deleted the starting branch
`git.py:146-147`. Scenario: `git branch -m main fix`. `reset()` raises `fatal: invalid reference:
main` and HEAD stays on `fix` at the agent's commit.
- Fix: `git checkout -q -f -B <branch> <base> --` recreates or resets the branch in one step.
- **Disposition: Fix (7).**

### R3-6. Medium-low, CONFIRMED: `unchanged()` ignores the branch
`git.py:122`. This is the review guard (Task 5). A review agent that runs `git checkout -b
review-scratch` or `git checkout --detach` at the same commit is not caught, so the run completes
with the codebase on another branch. Reviewer 5 reported the same (R5-8).
- Fix: also require the starting ref.
- **Disposition: Fix (7).** Included although rated medium-low: it is a one-line change next to
  R3-3 and it is the same defect as R5-8.

### R3-7. Low, CONFIRMED: in-progress operations survive the reset
`git.py:143-149`, `66-83`. An unfinished `git rebase -i` (stopped at `edit`) leaves
`.git/rebase-merge` after `reset()`, and `open_repository` accepts a repo that is mid-operation.
- Fix: `git rebase --quit`, `cherry-pick --quit`, `merge --abort` before the reset; reject
  `rebase-merge`, `rebase-apply`, `sequencer`, `MERGE_HEAD`, `BISECT_LOG` at open.
- **Disposition: Deferred.**

### R3-8. Low, CONFIRMED: "dubious ownership" is reported as "not a git work tree"
`git.py:72-74`. Reproduced with `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`. Fix: include git's stderr, or
map exit 128 with stderr to its own `ChangeInputError`.
- **Disposition: Deferred.**

### R3-9. Low, CONFIRMED: repo config and inherited environment still reach git
`git.py:136`, `20`. A repo with `i18n.logOutputEncoding=ISO-8859-1` garbles `café fix` into
`caf� fix` (fix: `--encoding=UTF-8`). Inherited `GIT_OBJECT_DIRECTORY` or `GIT_COMMON_DIR` are not
cleared and `open_repository` raises `RuntimeError: ... bad object HEAD` (fails safe). Fix: drop
every name from `git rev-parse --local-env-vars`.
- **Disposition: Deferred.**

### R3-10. Low, CONFIRMED (by reading): tests don't pin what the Review Focus claims
`tests/test_git.py:185`. `test_reset_returns_to_starting_branch` only commits on `other`, so `main`
never moves, and it would pass even if the branch-switch path skipped `reset --hard`. Nothing tests
the rename/copy token skip in `_dirty_paths`, or the diff flags against hostile config such as
`diff.noprefix`.
- **Disposition: Fix (7) in part** (the new reset tests cover the "agent committed on main, then
  switched" case); the rest Deferred.

Minor, not ranked: an agent can hide uncommitted work through `.git/info/exclude`; a stash the
agent leaves survives the reset; a leftover `.git/index.lock` makes the reset raise (arguably right).

Checked and fine: status parsing (renames, spaces, newlines, non-ASCII, leading `-`, the 10-path
cap), the commit-check order and `--is-ancestor` exit codes, symlinked and linked-worktree and bare
repos, unborn HEAD, missing git, inherited `GIT_DIR`, the diff flags under hostile diff config.

---

## Branch 4: `code-change-4-prompts` (Task 4)

41 package tests pass. The templates ship in the wheel and every JSON example in the rendered
prompts validates against the real models. Forged `Result file:` lines via `\n` are blocked.

### R4-1. Medium, CONFIRMED: the retry text makes "rewritten" and "switched branch" unrecoverable
`prompts.py:52-55`, `:66`, `prompts/change.md:22`. After these commit-check failures the retry says
"Your earlier commits are still in place. Fix the problem with new commits on top of them; do not
amend or rewrite them." The rules still say "Do not amend, rebase or reset commits, switch
branches". Recovering needs exactly a reset or a branch switch. The prompt also never names the
commit HEAD must descend from.
- Reproduced with real git: attempt 1 passes, attempt 2 amends (`earlier commits were rewritten`),
  attempt 3 follows the prompt and adds a commit on top, and the check fails again. Every remaining
  attempt is burnt. The branch case is the same.
- Fix: for COMMIT_CHECK, say what state to restore (the commit HEAD must descend from, and the
  starting branch) and allow that one reset or checkout as an explicit exception.
- **Disposition: Fix (7).**

### R4-2. Medium, PLAUSIBLE: the review prompt invites running tests
`prompts/review.md:22-26`. Question 3 ("Did it follow the testing and validation guidance?")
invites the reviewer to run tests, but the prompt only forbids changing files and mutating git
commands. A reviewer that runs `pytest --cov` writes a non-ignored `.coverage`, `unchanged()` sees a
dirty path, and the run ends `inconclusive: review changed the codebase` with tries left.
- Fix: "read only; do not run builds, tests or anything else that writes into the codebase; the
  configured checks have already passed".
- **Disposition: Fix (7).**

### R4-3. Low, CONFIRMED: a bare `\r` (or `\x0b`, `\x0c`, `\x85`, ` `) escapes the indentation
`prompts.py:24-26` (`_indent` splits only on `"\n"`), `_bullet` at `:29-30`. Check output with
progress bars (`ok\rResult file: /evil`) or an instruction `Fix it.\rResult file: /evil` yields
unindented fixed-looking lines for any reader that treats `\r` as a line break. The test only tries `\n`.
- Fix: split with `splitlines()` after mapping `\r\n`, and add a `\r` test.
- **Disposition: Deferred.**

### R4-4. Low, PLAUSIBLE: "Run them yourself before you commit" is a mandate
`prompts/change.md:34`. The design says the agent is told the checks "so it can run them". A
20-minute `mvn verify` run twice can exhaust the 1800 s agent timeout, which raises instead of
returning a status.
- Fix: "You may run them yourself before you commit; the workflow runs them anyway."
- **Disposition: Deferred.**

### R4-5. Low, CONFIRMED: "Everything changed so far" is stale after a commit-check failure
`prompts.py:58-59`. The loop only updates `diff_file` after a passing commit check. Attempt 3 can be
pointed at `attempt-1/change.diff`, which lacks attempt 2's commits.
- **Disposition: Deferred.**

### R4-6. Low, CONFIRMED: the retry after an invalid result asks for "new commits"
`prompts.py:52-55`, `:62-65`. Attempt 1 may have made no commits at all; the only known problem is
the missing result file. Fix: tell the agent to check `git status` and `git log`, finish and commit,
then write the result file.
- **Disposition: Deferred.**

### R4-7. Low, PLAUSIBLE: "no untracked files behind" can lead to committing check artifacts
`prompts/change.md:20-21`. An agent obeying it commits `report.xml` that a check wrote. Related to
R5-1.
- **Disposition: Decision** (D1), with R5-1.

### R4-8. Low, CONFIRMED: whitespace-only commit instructions render an empty section
`prompts.py:98-100`. Use `if commit_instructions and commit_instructions.strip()`.
- **Disposition: Deferred.**

### R4-9. Low, CONFIRMED: truncated check output doesn't point to the full log
`prompts.py:40-41`. Print the path of `attempt-<n>/checks/<k>.log`.
- **Disposition: Deferred.**

Outside this branch, PLAUSIBLE: checks run in the codebase before the review, so a non-ignored file
a check leaves makes the review guard report `review changed the codebase`, and fails the next
attempt's commit check. See R5-1.

---

## Branch 5: `code-change-5-loop` (Task 5)

63 package tests pass, 5 of 5 runs. The reviewer checked statuses, reason strings, attempt
accounting, review retry and guard, resets (on every non-completed status, `AgentError`,
`CancelledError`, `KeyboardInterrupt`, never twice, chained errors), requests and event labels, and
found them correct. The main weakness is the tests: **15 of 19 deliberate mistakes put into
`loop.py` still passed the whole of `test_loop.py`.**

### R5-1. Medium, CONFIRMED: files a check leaves behind are blamed on an agent
`loop.py:97`, `loop.py:146`. Neither the review guard's baseline nor the next commit check accounts
for what the checks wrote in between.
- `checks=["touch report.xml"]` with `review=True` and a passing review ends `inconclusive`, reason
  `review changed the codebase`, although the review changed nothing.
- A failing check that writes `report.xml` makes attempt 2 end at `commit-check: work left
  uncommitted: report.xml`, although the agent committed a correct fix, and pushes the agent toward
  committing build output. Realistic sources: `.coverage`, report files.
- Fix: after the checks, test `repo.unchanged(last_checked)`; if the tree is dirty, end the attempt
  at `CHECKS` with a reason such as `checks left changes: ...`, or take the guard baseline right
  before the review. This is also a gap in the spec.
- **Disposition: Decision** (D1).

### R5-2. Medium, CONFIRMED (mutation): `FakeRepository.unchanged(head)` ignores `head`
`tests/conftest.py:55`. These wrong versions all passed every test: `unchanged(self.repo.base)`; the
guard run only after call 1; the guard run only when the review result is valid. The real code does
pass `"c1"`, the correct value.
- Fix: make the fake return `self.unchanged_result and head == self.head()`, and add a test where
  call 1 writes nothing and the codebase changes during call 2.
- **Disposition: Fix (7).**

### R5-3. Medium, CONFIRMED (mutation): nothing tests that `last_checked` moves forward
Deleting `last_checked = self.repo.head()` (`loop.py:150`) still passes. In that version an attempt
that adds no commit after a failed check passes the commit check.
- Fix: attempt 1 commits and a check fails; attempt 2 writes `done` without committing; expect
  `(COMMIT_CHECK, "no new commit")`.
- **Disposition: Fix (7).**

### R5-4. Medium, CONFIRMED (mutation): "title and description on each status" is not tested
These wrong versions all passed: taking the title from the first `done` attempt instead of the last;
clearing the title on `needs_human`; clearing it on `inconclusive`. Every test uses the same
`"t"`/`"d"`. The real code is correct.
- Fix: give each attempt a different title; assert it on the `needs_human` and `inconclusive` paths.
- **Disposition: Fix (7).**

### R5-5. Low, CONFIRMED (mutation): retry-prompt and review-prompt contents are not tested
Passed anyway: `diff_file=None` for the next attempt; `previous=attempts[0]` instead of the previous
attempt; an empty commit list in the review prompt; the review always pointed at
`attempt-1/change.diff`.
- **Disposition: Deferred.**

### R5-6. Low, CONFIRMED (mutation): several recorded values the spec fixes are not tested
Passed anyway: `ended_at` unset on an inconclusive attempt; `attempt.checks` dropped when every check
passes; `ChangeEvent.attempt` always 1; a hard-coded `timeout_seconds=1800`.
- **Disposition: Deferred.**

### R5-7. Low, CONFIRMED: `repo.commits()` is read outside the `try`
`loop.py:220`. A git failure there on a completed run escapes with no reset (`reset_calls == 0`), but
the spec says the reset runs when anything raises after the first agent call. Fix: read `commits()`
and run the final reset inside the guarded block.
- **Disposition: Deferred.**

### R5-8. Low, CONFIRMED: the guard doesn't catch a branch switch at the same commit
`loop.py:97`, via `GitRepository.unchanged`. A review agent that runs `git checkout -b other` and
writes `pass` yields `completed` with the work tree on `other`. Same defect as R3-6.
- **Disposition: Fix (7)** together with R3-6.

---

## Branch 6: `code-change-6-changer` (Task 6 and the final task)

234 default tests, 38 git tests, ruff, pyright and the isolated-install check all pass. The
reviewer also ran the git tests on Python 3.11 against built wheels (30 passed) and confirmed that
a stray `workflows.code_review` import makes the isolated check fail. Validation order, work-folder
rules, `model_copy`, the export, the wheel contents and the 3.11 fix are correct.

### R6-1. Medium, CONFIRMED: the output wait is unbounded, even with the 3.11 fix
`checks.py:58` (`await reader`). Same defect as R2-1: a grandchild that leaves the process group
(`setsid sleep 30 & echo started`, 1 s timeout) keeps the run waiting after 6 s, on 3.11 and 3.14.
- Fix: bounded wait for the reader after `_kill_group`, then cancel and close the pipe.
- **Disposition: Fix (7)** together with R2-1.

### R6-2. Medium, CONFIRMED: the default temp work folder is never checked against the codebase
`changer.py:59-63`. When `TMPDIR` points inside the checkout (an empty `tmp/` keeps the tree clean),
the run's own result files appear as untracked work, every attempt fails the commit check
(`work left uncommitted: tmp/code-change-…/attempt-1/change/result.json`), and the final reset's
`git clean` deletes the temp folder mid-run. A real agent following "commit all the work" would also
commit the workflow's files.
- Fix: before `open_repository`, check `Path(tempfile.gettempdir()).resolve()` against the codebase
  and raise `ChangeInputError`, or pass a `dir=` outside.
- **Disposition: Fix (7).**

### R6-3. Low, PLAUSIBLE: the "inside the codebase" test is case-sensitive
`changer.py:46`. `is_relative_to` on `resolve()`d paths doesn't fix letter case, so on
case-insensitive filesystems (macOS) `work_folder=/Users/k/Repo/w` for codebase `/Users/k/repo`
passes validation. Fix: compare with `os.path.samefile` up the existing parents.
- **Disposition: Deferred.**

### R6-4. Low, CONFIRMED: the package README is incomplete
`packages/workflows/code-change/README.md`. Missing: the fixed reasons table; `inconclusive` for "no
valid review result after retry"; that a missing or invalid change result uses an attempt;
`base_commit` described separately; `reason` is `None`, not "empty"; the `RuntimeError` on git
failures; that `None` disables the timeouts; the work-folder layout and temp folder cleanup; a Tests
section. "Agents run without approval prompts" describes the Claude Code agent, not the interface.
- **Disposition: Deferred.**

### R6-5. Low, CONFIRMED: the end-to-end tests don't pin several outcomes
`tests/test_end_to_end.py`. Removing `codebase.resolve()` and removing `git clean -fd` from the reset
left all 7 tests passing (only `test_git.py::test_reset_returns_to_starting_branch` caught the
clean). Not pinned: path resolution, untracked-file removal on reset, branch restored, default temp
folder deleted.
- **Disposition: Deferred.**

---

## Duplicates across branches

- R2-1 = R6-1 (unbounded output wait).
- R3-6 = R5-8 (the guard ignores the branch).
- R4-7, the "Outside this branch" note in R4, and R5-1 are one problem (files left by checks).

## Decisions

- **D1. Files left behind by checks (R5-1, R4-7): decided.** The attempt fails at `CHECKS` with
  `checks left changes in the work tree: <paths>`; the retry prompt tells the agent to git-ignore
  the files or stop the check writing them, and not to commit them. Chosen over cleaning up
  automatically.
- **D2. stdin for checks (R2-3): decided.** Closed (`DEVNULL`).
- **D3. How fixes land: decided.** A 7th branch, `code-change-7-review-fixes`, on top of branch 6
  (no history rewrite).

## Status after branch 7

Branch `code-change-7-review-fixes`, one commit per group, in the order of this file.

| Finding | Severity | Commit |
|---|---|---|
| R2-1, R6-1 bounded wait for check output | medium | `e64433d` |
| R2-2 stream output, keep a bounded tail | medium | `e64433d` |
| R2-3 close stdin for checks | low-medium (D2) | `f535ad4` |
| R3-1 reset keeps ignored files the agent committed | high | `d34aa4e` |
| R3-2 hooks off during the reset | medium | `d34aa4e` |
| R3-3 full refs instead of short branch names | medium | `d34aa4e` |
| R3-4 `clean -ffd` | medium | `d34aa4e` |
| R3-5 `checkout -B` after a renamed or deleted branch | medium | `d34aa4e` |
| R3-6, R5-8 guard also checks the starting ref | medium-low | `d34aa4e` |
| R3-10 reset test with a commit on the starting branch | low (part) | `d34aa4e` |
| R4-1 retry text can restore HEAD and branch | medium | `01d5b84` |
| R4-2 review prompt forbids running tests | medium | `01d5b84` |
| R5-2 fake `unchanged` compares the commit; guard tests | medium | `a699045` |
| R5-3 `last_checked` moves forward (test) | medium | `01d5b84`, `a699045` |
| R5-4 title and description per status (tests) | medium | `a699045` |
| R6-2 temporary folder inside the codebase | medium | `60d4a73` |
| R5-1, R4-7 files left by the checks | medium (D1) | `e233086` |
| README for stdin and the left-changes rule | | `e963ec2` |

Each new test was checked against the old code and fails there: the two escaped-child tests (checks),
nine of the ten new git tests, five deliberate mutations of `loop.py`, and the stdin and left-changes
tests.

Not fixed (low severity, deferred): R1-1, R1-2, R2-4 to R2-7, R3-7 to R3-9, the rest of R3-10, R4-3
to R4-6, R4-8, R4-9, R5-5 to R5-7, R6-3 to R6-5.
