# Code Change Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `workflows-code-change`. It applies one instruction to a git codebase through an agent, checks the agent's commits, runs the given checks, optionally runs a spec review, retries on failure, and returns a status with the commits, a title and a description.

**Architecture:** Workflow code does everything that must be guaranteed:
- validates the input and the work tree before any agent runs;
- runs the commit check, the checks and the review guard itself;
- decides every status and reason;
- resets the repository when a run doesn't complete.

Agents edit and commit (the change agent) or only read (the review agent), and report through JSON result files in the work folder. `git.py` is the only module that runs git, behind a small `Repository` protocol, so the run loop (`loop.py`) is tested with an in-memory fake. `CodeChanger.change` validates, opens the repository and calls the loop.

**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, `git` CLI (subprocess), `workflows-agent` core (`Agent`, `FakeAgent`), pytest + pytest-asyncio.

**Spec:** `docs/workflows/code-change/design.md` (on the `design` branch). It builds on `docs/workflows/code-change/requirements.md`, which remains the authority.

## Branches

Stacked branches, one per task, each branched from the one before:

| Task | Branch | Based on |
|---|---|---|
| 1 | `code-change-1-models` | `origin/dev` |
| 2 | `code-change-2-checks` | `code-change-1-models` |
| 3 | `code-change-3-git` | `code-change-2-checks` |
| 4 | `code-change-4-prompts` | `code-change-3-git` |
| 5 | `code-change-5-loop` | `code-change-4-prompts` |
| 6 | `code-change-6-changer` | `code-change-5-loop` |

- If `dev` doesn't exist locally or on origin, run `git checkout main && git pull origin main`, then `git checkout -b dev && git push -u origin dev`.
- Push each branch with `git push -u origin <branch>` when its task is done.
- Never merge into `dev` or `main`, and never open pull requests. The user raises and reviews them one by one.
- If a later task needs a fix in an earlier branch, commit it on the earlier branch and rebase the branches above it.

## Global Constraints

- Distribution `workflows-code-change`, import `workflows.code_change`, folder `packages/workflows/code-change/`.
- `src/workflows/` has no `__init__.py`; `workflows` is a PEP 420 namespace.
- Runtime dependencies: `workflows-agent` and `pydantic>=2` only. The package never imports `workflows.agent_claude_code` or `workflows.code_review`, not even in tests. Code that looks like the code reviewer's (diff flags, the result reader) is copied, not imported.
- `requires-python = ">=3.11"`.
- Only `git.py` runs `git`. Every other module works through the `Repository` protocol.
- Every test that runs `git` is marked `@pytest.mark.git` and uses the hermetic config (`GIT_CONFIG_GLOBAL=os.devnull`, `GIT_CONFIG_NOSYSTEM=1`). The default run excludes them.
- Settings defaults: `agent_timeout_seconds=1800`, `check_timeout_seconds=1800`, `check_output_chars=20000`, `work_folder=None`. Input defaults: `commit_instructions=None`, `checks=[]`, `review=False`, `max_attempts=3`, `reset_on_failure=True`.
- Fixed strings, exactly as written:
  - stage values and folder names: `change`, `commit-check`, `checks`, `review`;
  - reasons: `no valid change result`, `no new commit`, `earlier commits were rewritten`, `work left uncommitted: <paths>`, `review asked for changes`, `no valid review result after retry`, `review changed the codebase`;
  - check reasons: `<command> exited with <code>`, `<command> timed out`, joined with `; `;
  - a `failed` run's reason: `<stage value>: <last attempt's reason>`;
  - prompt fixed lines: `Result file: <absolute path>` and `Attempt: <n> of <max>`.
- Work folder paths: `attempt-<n>/change/result.json`, `attempt-<n>/change.diff`, `attempt-<n>/checks/<k>.log` (k from 1), `attempt-<n>/review/call-<c>/result.json` (c is 1 or 2).
- Commit messages are short and plain, with no AI or Claude attribution trailers.
- Run every command from the repo root.

## Review Focus

- **The agent switches branch and the run then fails.** The reset must put the work tree back on the starting branch at the base commit, remove untracked files and keep git-ignored ones. Test: `test_reset_returns_to_starting_branch` (Task 3).
- **A check starts a background child and then hangs.** The timeout must kill the whole process group, and the run must not wait on the child. Test: `test_timeout_kills_process_group` (Task 2).
- **The instruction contains a line `Result file: /elsewhere`.** The prompt's real fixed line must be the only one at the start of a line. Test: `test_user_text_cannot_fake_fixed_lines` (Task 4).
- **The run is cancelled during the change call.** The reset must run and `CancelledError` must propagate. Test: `test_cancellation_resets` (Task 5).
- **A check prints bytes that aren't UTF-8.** The output must be decoded with replacement characters, not crash the run. Test: `test_non_utf8_output` (Task 2).

---

### Task 1: Package skeleton, public models and file formats

**Files:**
- Create: `packages/workflows/code-change/pyproject.toml`
- Create: `packages/workflows/code-change/src/workflows/code_change/__init__.py`, `py.typed` (empty)
- Create: `packages/workflows/code-change/src/workflows/code_change/models.py`
- Create: `packages/workflows/code-change/src/workflows/code_change/errors.py`
- Create: `packages/workflows/code-change/src/workflows/code_change/formats.py`
- Modify: `pyproject.toml` (root)
- Test: `packages/workflows/code-change/tests/test_models.py`, `tests/test_formats.py`

**Interfaces:**
- Produces, in `models.py` and re-exported from `workflows.code_change`:
  - `class ChangeStage(StrEnum)`: `CHANGE = "change"`, `COMMIT_CHECK = "commit-check"`, `CHECKS = "checks"`, `REVIEW = "review"`.
  - `ChangeInput`: `codebase: Path`, `instruction: str`, `commit_instructions: str | None = None`, `checks: list[str] = []`, `review: bool = False`, `max_attempts: int = Field(3, ge=1)`, `reset_on_failure: bool = True`. An empty instruction is not a validation error here; `change()` raises `ChangeInputError` for it (Task 6).
  - `ChangeSettings`, frozen: `agent_timeout_seconds: float | None = Field(1800, gt=0)`, `check_timeout_seconds: float | None = Field(1800, gt=0)`, `check_output_chars: int = Field(20000, ge=0)`, `work_folder: Path | None = None`.
  - `Commit`: `id: str`, `message: str`.
  - `CheckRun`: `command: str`, `exit_code: int | None`, `timed_out: bool = False`, `output: str`, `truncated: bool = False`.
  - `ReviewOutcome`: `verdict: Literal["pass", "changes_needed", "inconclusive"]`, `comments: list[str] = []`, `reason: str | None = None`.
  - `Attempt`: `number: int`, `ended_at: ChangeStage | None = None`, `reason: str | None = None`, `checks: list[CheckRun] = []`, `review: ReviewOutcome | None = None`.
  - `ChangeResult`: `status: Literal["completed", "needs_human", "inconclusive", "failed"]`, `reason: str | None = None`, `base_commit: str`, `commits: list[Commit] = []`, `title: str | None = None`, `description: str | None = None`, `attempts: list[Attempt] = []`.
  - `ChangeEvent`: `stage: ChangeStage`, `attempt: int`, `call: int`, `event: workflows.agent.Event`.
- Produces `errors.py`: `class ChangeInputError(ValueError)`, re-exported.
- Produces `formats.py`, not re-exported:
  - `NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]`.
  - `ChangeDone`: `outcome: Literal["done"]`, `title: NonEmpty`, `description: NonEmpty`.
  - `ChangeNeedsHuman`: `outcome: Literal["needs_human"]`, `reason: NonEmpty`.
  - `ChangeReport = Annotated[ChangeDone | ChangeNeedsHuman, Field(discriminator="outcome")]`.
  - `ReviewReport`: `verdict: Literal["pass", "changes_needed", "inconclusive"]`, `comments: list[NonEmpty] = []`, `reason: str | None = None`, with a model validator: `changes_needed` needs at least one comment; `inconclusive` needs a non-blank `reason`.
  - `read_change_report(path: Path) -> ChangeDone | ChangeNeedsHuman | None` and `read_review_report(path: Path) -> ReviewReport | None`: `None` when the file is missing, unreadable, not UTF-8, not JSON, nested too deeply (`RecursionError`) or off-schema. Extra keys are ignored.

- [ ] **Step 1: Set up the package and wire it into the workspace**

`packages/workflows/code-change/pyproject.toml`: the same shape as `packages/workflows/code-review/pyproject.toml`, with name `workflows-code-change`. In the root `pyproject.toml`, add `"workflows-code-change"` to `dependencies` and `workflows-code-change = { workspace = true }` to `[tool.uv.sources]`. Run `uv sync`.

- [ ] **Step 2: Write the failing tests**

`tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError
from workflows.code_change import (Attempt, ChangeInput, ChangeResult, ChangeSettings, ChangeStage,
                                   CheckRun, Commit, ReviewOutcome)

def test_input_defaults(tmp_path):
    i = ChangeInput(codebase=tmp_path, instruction="x")
    assert (i.commit_instructions, i.checks, i.review, i.max_attempts, i.reset_on_failure) == (None, [], False, 3, True)

def test_settings_defaults():
    s = ChangeSettings()
    assert (s.agent_timeout_seconds, s.check_timeout_seconds, s.check_output_chars, s.work_folder) == (1800, 1800, 20000, None)

@pytest.mark.parametrize("bad", [{"agent_timeout_seconds": 0}, {"check_timeout_seconds": 0}, {"check_output_chars": -1}])
def test_settings_reject_bad_values(bad):
    with pytest.raises(ValidationError):
        ChangeSettings(**bad)

def test_max_attempts_at_least_one(tmp_path):
    with pytest.raises(ValidationError):
        ChangeInput(codebase=tmp_path, instruction="x", max_attempts=0)

def test_stage_values():
    assert [s.value for s in ChangeStage] == ["change", "commit-check", "checks", "review"]

def test_result_json_round_trip():
    r = ChangeResult(status="failed", reason="checks: t exited with 1", base_commit="b",
                     commits=[Commit(id="c", message="m")],
                     attempts=[Attempt(number=1, ended_at=ChangeStage.CHECKS, reason="t exited with 1",
                                       checks=[CheckRun(command="t", exit_code=1, output="no")],
                                       review=ReviewOutcome(verdict="pass"))])
    assert ChangeResult.model_validate_json(r.model_dump_json()) == r
```

`tests/test_formats.py`:

```python
import pytest
from workflows.code_change.formats import ChangeDone, ChangeNeedsHuman, read_change_report, read_review_report

def write(tmp_path, text):
    p = tmp_path / "result.json"; p.write_text(text, encoding="utf-8"); return p

def test_reads_done_and_needs_human(tmp_path):
    assert read_change_report(write(tmp_path, '{"outcome": "done", "title": "t", "description": "d", "x": 1}')) == ChangeDone(outcome="done", title="t", description="d")
    assert isinstance(read_change_report(write(tmp_path, '{"outcome": "needs_human", "reason": "r"}')), ChangeNeedsHuman)

@pytest.mark.parametrize("text", ['{"outcome": "done", "title": " ", "description": "d"}',
    '{"outcome": "needs_human", "reason": ""}', '{"outcome": "maybe"}', "not json", "[" * 100000])
def test_bad_change_reports(tmp_path, text):
    assert read_change_report(write(tmp_path, text)) is None

def test_missing_and_non_utf8(tmp_path):
    assert read_change_report(tmp_path / "nope.json") is None
    p = tmp_path / "b.json"; p.write_bytes(b'{"outcome": "done", "title": "\xff", "description": "d"}')
    assert read_change_report(p) is None

@pytest.mark.parametrize("text,ok", [('{"verdict": "pass"}', True),
    ('{"verdict": "changes_needed", "comments": ["c"]}', True),
    ('{"verdict": "changes_needed", "comments": []}', False),
    ('{"verdict": "inconclusive", "reason": "r"}', True),
    ('{"verdict": "inconclusive", "reason": "  "}', False)])
def test_review_reports(tmp_path, text, ok):
    assert (read_review_report(write(tmp_path, text)) is not None) == ok
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest packages/workflows/code-change -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_change'`.

- [ ] **Step 4: Implement `models.py`, `errors.py`, `formats.py` and `__init__.py`**

The readers catch `OSError`, `ValueError` (which covers `UnicodeDecodeError` and `ValidationError`) and `RecursionError`, and return `None`. `__init__.py` re-exports everything in `models.py`, `ChangeInputError`, and (from Task 6) `CodeChanger`, with an `__all__`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/workflows/code-change -q && uv run ruff check . && uv run pyright`
Expected: all pass, no lint or type errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock packages/workflows/code-change
git commit -m "Add code change package with models and file formats"
```

---

### Task 2: Running checks

**Files:**
- Create: `packages/workflows/code-change/src/workflows/code_change/checks.py`
- Test: `packages/workflows/code-change/tests/test_checks.py`

**Interfaces:**
- Consumes: `CheckRun` (Task 1).
- Produces:
  - `async def run_checks(commands: list[str], cwd: Path, log_dir: Path, timeout_seconds: float | None, keep_chars: int) -> list[CheckRun]`. Runs every command, in order, even after one fails. Creates `log_dir` and writes each command's full output to `log_dir / f"{k}.log"` (k from 1) as raw bytes.
  - `def checks_reason(runs: list[CheckRun]) -> str | None`: `None` when every run has `exit_code == 0`; otherwise the failing runs as `<command> exited with <code>` or `<command> timed out`, joined with `; `.

- [ ] **Step 1: Write the failing tests in `tests/test_checks.py`**

```python
import time
import pytest
from workflows.code_change.checks import checks_reason, run_checks

async def run(tmp_path, commands, timeout=None, keep=20000):
    return await run_checks(commands, tmp_path, tmp_path / "logs", timeout, keep)

async def test_runs_every_check_in_order(tmp_path):
    runs = await run(tmp_path, ["echo one", "exit 3", "echo two >&2"])
    assert [(r.command, r.exit_code) for r in runs] == [("echo one", 0), ("exit 3", 3), ("echo two >&2", 0)]
    assert runs[0].output == "one\n" and runs[2].output == "two\n"
    assert (tmp_path / "logs" / "1.log").read_bytes() == b"one\n"
    assert checks_reason(runs) == "exit 3 exited with 3"

async def test_runs_in_cwd(tmp_path):
    (tmp_path / "marker").write_text("")
    [r] = await run(tmp_path, ["test -f marker"])
    assert r.exit_code == 0

async def test_missing_command_fails(tmp_path):
    [r] = await run(tmp_path, ["no-such-command-xyz"])
    assert r.exit_code == 127

async def test_timeout(tmp_path):
    [r] = await run(tmp_path, ["sleep 5"], timeout=0.2)
    assert (r.exit_code, r.timed_out) == (None, True)
    assert checks_reason([r]) == "sleep 5 timed out"

async def test_timeout_kills_process_group(tmp_path):
    start = time.monotonic()
    [r] = await run(tmp_path, ["(sleep 30 &) ; sleep 30"], timeout=0.2)
    assert r.timed_out and time.monotonic() - start < 5

async def test_output_kept_from_the_end(tmp_path):
    [r] = await run(tmp_path, ["printf abcdef"], keep=3)
    assert (r.output, r.truncated) == ("def", True)
    assert (tmp_path / "logs" / "1.log").read_bytes() == b"abcdef"

async def test_keep_zero(tmp_path):
    [r] = await run(tmp_path, ["printf abc"], keep=0)
    assert (r.output, r.truncated) == ("", True)

async def test_non_utf8_output(tmp_path):
    [r] = await run(tmp_path, ["printf '\\377ok'"])
    assert r.output == "�ok"

def test_reason_none_when_all_pass():
    assert checks_reason([]) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/workflows/code-change/tests/test_checks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_change.checks'`.

- [ ] **Step 3: Implement `checks.py`**

Each command runs through `asyncio.create_subprocess_shell(command, cwd=cwd, stdout=PIPE, stderr=STDOUT, start_new_session=True)`, with the environment inherited. Wait with `asyncio.wait_for(proc.communicate(), timeout_seconds)`. On a timeout or cancellation, `os.killpg(proc.pid, SIGKILL)` (ignoring `ProcessLookupError`), then reap the process. On a timeout, record the output read so far if any, otherwise empty. Re-raise cancellation. Decode with `errors="replace"`. `truncated` is true when the decoded output is longer than `keep_chars`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/workflows/code-change/tests/test_checks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/workflows/code-change
git commit -m "Run checks for code change"
```

---

### Task 3: The git repository

**Files:**
- Create: `packages/workflows/code-change/src/workflows/code_change/repo.py`
- Create: `packages/workflows/code-change/src/workflows/code_change/git.py`
- Test: `packages/workflows/code-change/tests/test_git.py` (all `@pytest.mark.git`)

**Interfaces:**
- Consumes: `Commit`, `ChangeInputError` (Task 1).
- Produces, in `repo.py`:

  ```python
  class Repository(Protocol):
      @property
      def base(self) -> str: ...                              # full id of HEAD at the start
      def head(self) -> str: ...
      def commit_problem(self, last_checked: str) -> str | None: ...
      def unchanged(self, head: str) -> bool: ...             # HEAD == head and the work tree is clean
      def write_diff(self, dest: Path) -> None: ...           # base..HEAD
      def commits(self) -> list[Commit]: ...                  # base..HEAD, oldest first
      def reset(self) -> None: ...
  ```

- Produces, in `git.py`: `def open_repository(path: Path) -> GitRepository`, and `class GitRepository` implementing `Repository`. `open_repository` raises `ChangeInputError` with these messages, checked in this order:
  - `codebase does not exist: <path>`;
  - `codebase is not a git work tree: <path>`;
  - `codebase must be the top of its git work tree: <path>` (`git rev-parse --show-toplevel`, compared after `resolve()`);
  - `codebase has no commits: <path>`;
  - `work tree is not clean: <path>`.

  It records the base (`git rev-parse HEAD`) and the starting branch (`git symbolic-ref -q --short HEAD`, or `None` when detached).

- [ ] **Step 1: Write the failing tests in `tests/test_git.py`**

Use the code reviewer's `hermetic_git` fixture and `git()` helper, copied into this file. A `repo` fixture (function scope) makes a repository with `.gitignore` holding `ignored.txt`, a committed `a.py`, and one commit `base`, on branch `main` (`git init -q -b main`).

```python
pytestmark = pytest.mark.git

def test_open_records_base_and_branch(repo):
    r = open_repository(repo)
    assert r.base == git(repo, "rev-parse", "HEAD").strip() and r.head() == r.base

@pytest.mark.parametrize("make,message", [
    (lambda p: p / "missing", "codebase does not exist"),
    (lambda p: p, "codebase is not a git work tree"),
])
def test_open_rejects(tmp_path, make, message):
    with pytest.raises(ChangeInputError, match=message):
        open_repository(make(tmp_path))

def test_open_rejects_subfolder(repo):
    (repo / "sub").mkdir()
    with pytest.raises(ChangeInputError, match="must be the top"):
        open_repository(repo / "sub")

def test_open_rejects_no_commits(tmp_path):
    git(tmp_path, "init", "-q")
    with pytest.raises(ChangeInputError, match="has no commits"):
        open_repository(tmp_path)

@pytest.mark.parametrize("dirty", ["modify", "untracked", "staged"])
def test_open_rejects_dirty(repo, dirty):
    ...  # modify a.py; or create new.py; or create and `git add` new.py
    with pytest.raises(ChangeInputError, match="not clean"):
        open_repository(repo)

def test_ignored_files_allowed(repo):
    (repo / "ignored.txt").write_text("x")
    open_repository(repo)

def test_commit_check_passes(repo):
    r = open_repository(repo)
    commit(repo, "b.py", "fix")   # helper: write the file, add, commit
    assert r.commit_problem(r.base) is None

def test_no_new_commit(repo):
    r = open_repository(repo)
    assert r.commit_problem(r.base) == "no new commit"

def test_uncommitted_work(repo):
    r = open_repository(repo); commit(repo, "b.py", "fix")
    (repo / "a.py").write_text("changed\n"); (repo / "new file.py").write_text("x")
    assert r.commit_problem(r.base) == "work left uncommitted: a.py, new file.py"

def test_uncommitted_list_is_capped(repo):
    r = open_repository(repo); commit(repo, "b.py", "fix")
    for i in range(12): (repo / f"f{i:02}.py").write_text("x")
    assert r.commit_problem(r.base) == "work left uncommitted: " + ", ".join(f"f{i:02}.py" for i in range(10)) + " and 2 more"

def test_rewritten_commits(repo):
    r = open_repository(repo); first = commit(repo, "b.py", "fix")
    git(repo, "commit", "-q", "--amend", "-m", "amended")
    assert r.commit_problem(first) == "earlier commits were rewritten"

def test_switched_branch(repo):
    r = open_repository(repo); git(repo, "checkout", "-q", "-b", "other"); commit(repo, "b.py", "fix")
    assert r.commit_problem(r.base) == "switched from main to other"

def test_detached_start_then_branch(repo):
    git(repo, "checkout", "-q", "--detach"); r = open_repository(repo)
    git(repo, "checkout", "-q", "-b", "other"); commit(repo, "b.py", "fix")
    assert r.commit_problem(r.base) == "checked out other; the run started on a detached HEAD"

def test_commits_and_diff(repo, tmp_path):
    r = open_repository(repo); commit(repo, "b.py", "first\n\nbody"); commit(repo, "c.py", "second")
    assert [c.message for c in r.commits()] == ["first\n\nbody", "second"]
    r.write_diff(tmp_path / "change.diff")
    text = (tmp_path / "change.diff").read_text()
    assert "diff --git a/b.py b/b.py" in text and "diff --git a/c.py b/c.py" in text

def test_unchanged(repo):
    r = open_repository(repo); head = commit(repo, "b.py", "fix")
    assert r.unchanged(head)
    (repo / "x.py").write_text("x")
    assert not r.unchanged(head)

def test_reset_returns_to_starting_branch(repo):
    r = open_repository(repo)
    git(repo, "checkout", "-q", "-b", "other"); commit(repo, "b.py", "fix")
    (repo / "untracked.py").write_text("x"); (repo / "ignored.txt").write_text("keep")
    r.reset()
    assert git(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert r.head() == r.base and r.commits() == []
    assert not (repo / "untracked.py").exists() and (repo / "ignored.txt").read_text() == "keep"

def test_reset_from_detached_start(repo):
    git(repo, "checkout", "-q", "--detach"); r = open_repository(repo)
    commit(repo, "b.py", "fix"); r.reset()
    assert git(repo, "rev-parse", "HEAD").strip() == r.base
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=repo).returncode == 1

def test_inherited_git_dir_ignored(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"; other.mkdir(); git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert open_repository(repo).base == subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
        env={k: v for k, v in os.environ.items() if k != "GIT_DIR"}, capture_output=True, text=True).stdout.strip()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/workflows/code-change/tests/test_git.py -m git -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_change.git'`.

- [ ] **Step 3: Implement `repo.py` and `git.py`**

- `_run(cwd, *args, ok=(0,)) -> bytes` runs `git --literal-pathspecs -c core.quotepath=off <args>` with `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE` removed from the environment, and raises `RuntimeError` with git's stderr on any other exit code, or when git isn't installed. `open_repository` maps the git failures it expects to `ChangeInputError`.
- `commit_problem` checks in the design's order and stops at the first failure: branch, ancestor (`merge-base --is-ancestor`, exit 1 means rewritten), new commit, clean tree. Messages, exactly:
  - started on a branch: `switched from <branch> to <current>`, or `switched from <branch> to a detached HEAD`;
  - started detached: `checked out <current>; the run started on a detached HEAD`.
- Clean tree and the uncommitted list come from `git status --porcelain=v1 -z --untracked-files=all`. For a rename or copy entry, skip the extra path token. List at most 10 paths in git's order, then ` and <n> more`.
- `write_diff`: `git diff --no-color --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ -M <base> HEAD`, written as bytes.
- `commits`: `git rev-list --reverse <base>..HEAD`, then `git log -1 --format=%B <id>` for each, with trailing newlines stripped.
- `reset`: if started on a branch and HEAD isn't on it, `git checkout -q -f <branch> --`; if started detached, `git checkout -q -f --detach <base>`. Then `git reset -q --hard <base>` and `git clean -q -fd`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/workflows/code-change/tests/test_git.py -m git -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/workflows/code-change
git commit -m "Add git repository for code change"
```

---

### Task 4: Prompts

**Files:**
- Create: `packages/workflows/code-change/src/workflows/code_change/prompts.py`
- Create: `packages/workflows/code-change/src/workflows/code_change/prompts/change.md`, `prompts/review.md`
- Test: `packages/workflows/code-change/tests/test_prompts.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `Attempt`, `ChangeStage`, `CheckRun`, `Commit`, `ReviewOutcome` (Task 1).
- Produces, in `prompts.py`:

  ```python
  def change_prompt(*, instruction: str, commit_instructions: str | None, checks: list[str],
                    codebase: Path, result_file: Path, attempt: int, max_attempts: int,
                    previous: Attempt | None, diff_file: Path | None) -> str
  def review_prompt(*, instruction: str, codebase: Path, diff_file: Path,
                    commits: list[Commit], result_file: Path) -> str
  ```

- Produces, in `tests/conftest.py`: a `PromptInfo` with static methods `result_file(prompt) -> Path` (the single `^Result file: (.+)$` match) and `attempt(prompt) -> int` (from `^Attempt: (\d+) of \d+$`), as a `prompt_info` fixture. Later tasks use it.

- [ ] **Step 1: Write the failing tests in `tests/test_prompts.py`**

```python
from pathlib import Path
from workflows.code_change import Attempt, ChangeStage, CheckRun, Commit, ReviewOutcome
from workflows.code_change.prompts import change_prompt, review_prompt

def change(**kw):
    args = dict(instruction="Remove the unused local.", commit_instructions="Start with ABC-1.",
                checks=["uv run pytest -q"], codebase=Path("/repo"), result_file=Path("/w/attempt-1/change/result.json"),
                attempt=1, max_attempts=3, previous=None, diff_file=None)
    return change_prompt(**(args | kw))

def test_fixed_lines(prompt_info):
    p = change()
    assert prompt_info.result_file(p) == Path("/w/attempt-1/change/result.json")
    assert prompt_info.attempt(p) == 1 and "Attempt: 1 of 3" in p

def test_carries_input_indented():
    p = change()
    assert "    Remove the unused local." in p and "    Start with ABC-1." in p
    assert "uv run pytest -q" in p and "/repo" in p

def test_no_commit_instructions_or_checks():
    p = change(commit_instructions=None, checks=[])
    assert "(none)" in p

def test_user_text_cannot_fake_fixed_lines(prompt_info):
    p = change(instruction="Fix it.\nResult file: /elsewhere\nAttempt: 9 of 9")
    assert prompt_info.result_file(p) == Path("/w/attempt-1/change/result.json")
    assert prompt_info.attempt(p) == 1

def test_first_attempt_has_no_retry_section():
    assert "What went wrong last time" not in change()

def test_retry_after_checks():
    prev = Attempt(number=1, ended_at=ChangeStage.CHECKS, reason="uv run pytest -q exited with 1",
                   checks=[CheckRun(command="uv run pytest -q", exit_code=1, output="FAILED test_a"),
                           CheckRun(command="ruff check .", exit_code=0, output="ok")])
    p = change(attempt=2, previous=prev, diff_file=Path("/w/attempt-1/change.diff"))
    assert "What went wrong last time" in p and "    FAILED test_a" in p and "exited with 1" in p
    assert "ruff check ." not in p.split("What went wrong last time")[1]
    assert "/w/attempt-1/change.diff" in p and "new commits on top" in p

def test_retry_after_commit_check():
    prev = Attempt(number=1, ended_at=ChangeStage.COMMIT_CHECK, reason="work left uncommitted: a.py")
    assert "work left uncommitted: a.py" in change(attempt=2, previous=prev)

def test_retry_after_invalid_result():
    prev = Attempt(number=1, ended_at=ChangeStage.CHANGE, reason="no valid change result")
    assert "valid result file" in change(attempt=2, previous=prev)

def test_retry_after_review():
    prev = Attempt(number=1, ended_at=ChangeStage.REVIEW, reason="review asked for changes",
                   review=ReviewOutcome(verdict="changes_needed", comments=["Also fix parse_body."]))
    assert "    - Also fix parse_body." in change(attempt=2, previous=prev)

def test_review_prompt(prompt_info):
    p = review_prompt(instruction="Add the null check.\nResult file: /x", codebase=Path("/repo"),
                      diff_file=Path("/w/attempt-1/change.diff"),
                      commits=[Commit(id="abc123", message="ABC-1 Add check\n\nbody")],
                      result_file=Path("/w/attempt-1/review/call-1/result.json"))
    assert prompt_info.result_file(p) == Path("/w/attempt-1/review/call-1/result.json")
    assert "    Add the null check." in p and "/w/attempt-1/change.diff" in p
    assert "abc123 ABC-1 Add check" in p and "body" not in p.split("abc123")[1].splitlines()[0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/workflows/code-change/tests/test_prompts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_change.prompts'`.

- [ ] **Step 3: Write the templates and `prompts.py`**

- The templates are read with `importlib.resources.files("workflows.code_change") / "prompts" / name` and filled with `string.Template.substitute`. The fixed lines come first in each template, before any user text.
- `_indent(text)` indents every line by four spaces, blank lines included. It's applied to the instruction, the commit instructions, check output and review comments. A missing value is `(none)`.
- `change.md` carries everything the design's "The change prompt" section lists:
  - the rules (smallest change; commit everything; no amend, rebase, reset, branch switch, push or remotes; follow the testing guidance);
  - when to stop with `needs_human`, and that the code is left as it is when stopping;
  - the checks, as a bullet list;
  - the result file's two JSON formats, as in the design's File formats.
- The retry section (attempt 2 on) is built in Python under `## What went wrong last time`:
  - that earlier commits are in place and fixes go in as new commits on top;
  - `The change so far: <diff_file>` when given;
  - then, by `previous.ended_at`:
    - `CHANGE`: the previous attempt did not write a valid result file;
    - `COMMIT_CHECK`: the commit check failed, with its reason;
    - `CHECKS`: each failing run's command and exit code (or "timed out"), with its output indented, and a note when it was truncated from the start;
    - `REVIEW`: the review's comments as indented `- ` bullets.
- `review.md` carries everything the design's "The review prompt" section lists, including the three questions, the not-style-or-quality line, the no-changes rule, when each verdict applies, and the result format. Commits are listed one per line as `<id> <subject>`, where the subject is the first line of the message.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/workflows/code-change/tests/test_prompts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/workflows/code-change
git commit -m "Add code change prompts"
```

---

### Task 5: The run loop

**Files:**
- Create: `packages/workflows/code-change/src/workflows/code_change/loop.py`
- Modify: `packages/workflows/code-change/tests/conftest.py` (add `FakeRepository`)
- Test: `packages/workflows/code-change/tests/test_loop.py`

**Interfaces:**
- Consumes: the models and readers (Task 1), `run_checks`, `checks_reason` (Task 2), `Repository` (Task 3), `change_prompt`, `review_prompt`, `PromptInfo` (Task 4).
- Produces:

  ```python
  async def run_change(change_input: ChangeInput, repo: Repository, *, change_agent: Agent,
                       review_agent: Agent, settings: ChangeSettings, work: Path,
                       on_event: Callable[[ChangeEvent], None] | None) -> ChangeResult
  ```

  `change_input.codebase` is already absolute. `work` exists and is empty.
- Produces, in `tests/conftest.py`: `FakeRepository`, with:
  - `base = "base0"`, `head()` (the last commit id, or the base);
  - `commit(message) -> str`, which adds `Commit(id=f"c{n}", message=message)`;
  - `dirty: list[str] = []`;
  - `commit_problem(last_checked)`: `work left uncommitted: <dirty joined with ", ">` when dirty, `no new commit` when `head() == last_checked`, otherwise `None`;
  - `unchanged_result = True`, returned by `unchanged()`;
  - `write_diff(dest)`, which writes `"diff\n"`;
  - `commits()`;
  - `reset()`, which counts `reset_calls`, raises `reset_error` when set, and otherwise clears commits and `dirty`.

  Also a `fake_repo` fixture.

- [ ] **Step 1: Write the failing tests in `tests/test_loop.py`**

Helpers in the file:
- `done(req, title="t", description="d")` writes a `done` change result to `prompt_info.result_file(req.prompt)`, creating parents;
- `needs_human(req, reason)`;
- `review(req, verdict, comments=(), reason=None)`.
- `run(fake_repo, tmp_path, change_agent, review_agent=None, **input_kw)` calls `run_change`:
  - `ChangeInput(codebase=tmp_path / "code", instruction="Fix it.", **input_kw)`;
  - `settings=ChangeSettings(check_timeout_seconds=10)`;
  - `work=tmp_path / "work"`, created empty;
  - the review agent defaults to the change agent;
  - collected events.

  `tmp_path / "code"` is created, so checks can run there.

Tests (each asserts exactly these values):

```python
async def test_completed_first_attempt(...):
    # change agent commits once and writes done("Remove tmp", "Unused.")
    assert (r.status, r.reason, r.base_commit) == ("completed", None, "base0")
    assert (r.title, r.description) == ("Remove tmp", "Unused.")
    assert [c.id for c in r.commits] == ["c1"] and fake_repo.reset_calls == 0
    assert [(a.number, a.ended_at) for a in r.attempts] == [(1, None)]
    assert (tmp_path / "work" / "attempt-1" / "change.diff").read_text() == "diff\n"

async def test_needs_human_stops_and_resets(...):
    # agent commits, then writes needs_human("Signature change needed.")
    assert (r.status, r.reason) == ("needs_human", "Signature change needed.")
    assert len(agent.requests) == 1 and fake_repo.reset_calls == 1 and r.commits == []
    assert r.attempts[0].ended_at == ChangeStage.CHANGE and r.attempts[0].checks == []

async def test_invalid_change_result_uses_an_attempt(...):
    # attempt 1 commits but writes nothing; attempt 2 writes done
    assert r.status == "completed"
    assert (r.attempts[0].ended_at, r.attempts[0].reason) == (ChangeStage.CHANGE, "no valid change result")
    assert "valid result file" in agent.requests[1].prompt

async def test_commit_problem_retries_with_reason(...):
    # attempt 1 writes done without committing; attempt 2 commits
    assert r.attempts[0].reason == "no new commit" and "no new commit" in agent.requests[1].prompt
    assert r.status == "completed"

async def test_failing_check_retries_with_output(...):
    # checks=["test -f fixed || (echo missing-fixed; exit 1)"]; attempt 2 creates code/fixed and commits
    a1 = r.attempts[0]
    assert (a1.ended_at, a1.reason) == (ChangeStage.CHECKS, "test -f fixed || (echo missing-fixed; exit 1) exited with 1")
    assert "missing-fixed" in agent.requests[1].prompt and r.status == "completed"
    assert (tmp_path / "work" / "attempt-1" / "checks" / "1.log").exists()

async def test_out_of_attempts(...):
    # max_attempts=2, checks=["exit 1"], agent always commits and writes done("t", "d")
    assert (r.status, r.reason) == ("failed", "checks: exit 1 exited with 1")
    assert len(r.attempts) == 2 and fake_repo.reset_calls == 1 and (r.title, r.description) == ("t", "d")

async def test_single_attempt(...):
    # max_attempts=1, agent writes nothing
    assert (r.status, r.reason) == ("failed", "change: no valid change result") and len(agent.requests) == 1

async def test_checks_skipped_after_commit_check_fails(...):
    # checks=["touch ran"], agent writes done without committing, max_attempts=1
    assert not (tmp_path / "code" / "ran").exists() and r.attempts[0].checks == []

async def test_review_skipped_when_checks_fail(...):
    # review=True, checks=["exit 1"], max_attempts=1, separate review agent
    assert reviewer.requests == [] and r.attempts[0].review is None

async def test_review_changes_needed_then_pass(...):
    # review=True; review agent: changes_needed(["Also fix parse_body."]) then pass
    assert r.status == "completed"
    assert (r.attempts[0].ended_at, r.attempts[0].reason) == (ChangeStage.REVIEW, "review asked for changes")
    assert "Also fix parse_body." in agent.requests[1].prompt
    assert prompt_info.result_file(reviewer.requests[0].prompt) == tmp_path / "work" / "attempt-1" / "review" / "call-1" / "result.json"

async def test_review_inconclusive_stops(...):
    # review=True, max_attempts=3, review says inconclusive("Instruction is ambiguous.")
    assert (r.status, r.reason) == ("inconclusive", "Instruction is ambiguous.")
    assert len(r.attempts) == 1 and fake_repo.reset_calls == 1

async def test_invalid_review_retried_once(...):
    # review writes nothing on both calls
    assert (r.status, r.reason) == ("inconclusive", "no valid review result after retry")
    assert len(reviewer.requests) == 2
    assert [e.call for e in events if e.stage == ChangeStage.REVIEW] == [1, 2]  # FakeAgent with one scripted event

async def test_review_guard(...):
    # review=True, fake_repo.unchanged_result = False, review says pass
    assert (r.status, r.reason) == ("inconclusive", "review changed the codebase")

async def test_reset_off_keeps_commits(...):
    # reset_on_failure=False, agent commits then needs_human
    assert fake_repo.reset_calls == 0 and [c.id for c in r.commits] == ["c1"]

async def test_agent_error_resets_and_propagates(...):
    # agent raises AgentError("boom")
    with pytest.raises(AgentError): ...
    assert fake_repo.reset_calls == 1

async def test_cancellation_resets(...):
    # agent awaits asyncio.Event().wait(); cancel the task after it starts
    with pytest.raises(asyncio.CancelledError): await task
    assert fake_repo.reset_calls == 1

async def test_reset_failure_is_chained(...):
    # agent raises AgentError; fake_repo.reset_error = RuntimeError("reset failed")
    with pytest.raises(RuntimeError, match="reset failed") as info: ...
    assert isinstance(info.value.__cause__, AgentError)

async def test_no_reset_on_error_when_off(...):
    # reset_on_failure=False, agent raises AgentError
    assert fake_repo.reset_calls == 0

async def test_events_and_requests(...):
    # FakeAgent with one scripted TextEvent; review=True
    assert [(e.stage, e.attempt, e.call) for e in events] == [(ChangeStage.CHANGE, 1, 1), (ChangeStage.REVIEW, 1, 1)]
    req = agent.requests[0]
    assert req.working_folder == tmp_path / "code" and req.extra_folders == [tmp_path / "work"]
    assert req.timeout_seconds == 1800
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/workflows/code-change/tests/test_loop.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'workflows.code_change.loop'`.

- [ ] **Step 3: Implement `run_change` in `loop.py`**

```
last_checked = repo.base; diff_file = None; title = description = None; attempts = []
try:
  for n in 1..max_attempts:
    change call (stage CHANGE, call 1) with change_prompt(previous=attempts[-1] if attempts else None, diff_file=diff_file)
    report = read_change_report(...)
    None            -> attempt ends CHANGE "no valid change result"; continue
    needs_human     -> attempt ends CHANGE with report.reason; status needs_human; break
    done            -> title, description = report's
    problem = repo.commit_problem(last_checked) -> attempt ends COMMIT_CHECK; continue
    last_checked = repo.head(); diff_file = attempt-n/change.diff; repo.write_diff(diff_file)
    runs = await run_checks(...) if checks; reason = checks_reason(runs) -> attempt ends CHECKS; continue
    if review: up to 2 review calls (call-1, call-2) until read_review_report is not None
      after each call: if not repo.unchanged(last_checked) -> inconclusive "review changed the codebase"; break out
      no report after 2 -> outcome inconclusive "no valid review result after retry"
      inconclusive -> status inconclusive, reason = report.reason; break
      changes_needed -> attempt ends REVIEW "review asked for changes"; continue
    status completed; break
  else: status failed, reason f"{last.ended_at.value}: {last.reason}"
except BaseException as e:
  if reset_on_failure: try repo.reset() except Exception as r: raise r from e
  raise
if status != completed and reset_on_failure: repo.reset()
return ChangeResult(..., commits=repo.commits())
```

- Every agent call creates its output folder first and passes `AgentRequest(prompt, working_folder=codebase, extra_folders=[work], timeout_seconds=settings.agent_timeout_seconds)`. Events are forwarded as `ChangeEvent(stage, attempt=n, call=c, event=e)`.
- For an inconclusive review, the attempt's `ended_at` is `REVIEW` and its `reason` is the run's reason. `review` holds a `ReviewOutcome` built from the report, or `ReviewOutcome(verdict="inconclusive", reason=...)` for the no-result and guard cases.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/workflows/code-change -q && uv run pyright`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/workflows/code-change
git commit -m "Add code change run loop"
```

---

### Task 6: `CodeChanger.change` and the README

**Files:**
- Create: `packages/workflows/code-change/src/workflows/code_change/changer.py`
- Modify: `packages/workflows/code-change/src/workflows/code_change/__init__.py` (export `CodeChanger`)
- Create: `packages/workflows/code-change/README.md`
- Test: `packages/workflows/code-change/tests/test_changer.py`, `tests/test_end_to_end.py` (`@pytest.mark.git`)

**Interfaces:**
- Consumes: `run_change` (Task 5), `open_repository` (Task 3), the models (Task 1).
- Produces:

  ```python
  class CodeChanger:
      def __init__(self, change_agent: Agent, review_agent: Agent | None = None,
                   settings: ChangeSettings | None = None) -> None
      async def change(self, change_input: ChangeInput,
                       on_event: Callable[[ChangeEvent], None] | None = None) -> ChangeResult
  ```

  `change()`, in this order, so a rejected input never leaves files behind:
  1. raises `ChangeInputError("instruction is empty")` for a blank instruction;
  2. resolves the codebase path;
  3. if `work_folder` is set: raises `work folder must not be inside the codebase: <path>` or `work folder must be empty or missing: <path>`;
  4. calls `open_repository`;
  5. only then creates the work folder, or a `tempfile.TemporaryDirectory(prefix="code-change-", ignore_cleanup_errors=True)`;
  6. calls `run_change` with the review agent defaulting to the change agent.

- [ ] **Step 1: Write the failing tests**

`tests/test_changer.py` (no git):

```python
async def test_blank_instruction(tmp_path):
    agent = FakeAgent()
    with pytest.raises(ChangeInputError, match="instruction is empty"):
        await CodeChanger(agent).change(ChangeInput(codebase=tmp_path, instruction="  \n"))
    assert agent.requests == []

async def test_work_folder_inside_codebase(tmp_path):
    with pytest.raises(ChangeInputError, match="must not be inside the codebase"):
        await CodeChanger(FakeAgent(), settings=ChangeSettings(work_folder=tmp_path / "w")).change(
            ChangeInput(codebase=tmp_path, instruction="x"))

async def test_work_folder_not_empty(tmp_path):
    w = tmp_path / "w"; w.mkdir(); (w / "old").write_text("")
    with pytest.raises(ChangeInputError, match="must be empty or missing"):
        await CodeChanger(FakeAgent(), settings=ChangeSettings(work_folder=w)).change(
            ChangeInput(codebase=tmp_path / "code", instruction="x"))

async def test_bad_codebase_leaves_no_work_folder(tmp_path):
    w = tmp_path / "w"
    with pytest.raises(ChangeInputError):
        await CodeChanger(FakeAgent(), settings=ChangeSettings(work_folder=w)).change(
            ChangeInput(codebase=tmp_path / "missing", instruction="x"))
    assert not w.exists()
```

`tests/test_end_to_end.py` (`pytestmark = pytest.mark.git`, hermetic git, a `repo` fixture like Task 3's). The `FakeAgent`'s `respond` edits files in `req.working_folder`, commits with the test `git()` helper, and writes the result file found with `prompt_info`.

```python
async def test_completed_run(repo, prompt_info):
    # agent writes b.py, commits "ABC-1 Add b", writes done("Add b", "Adds b.py.")
    r = await CodeChanger(agent).change(ChangeInput(codebase=repo, instruction="Add b.py", checks=["test -f b.py"]))
    assert r.status == "completed" and [c.message for c in r.commits] == ["ABC-1 Add b"]
    assert r.base_commit != git(repo, "rev-parse", "HEAD").strip()

async def test_failed_run_resets(repo, prompt_info):
    # checks=["exit 1"], max_attempts=2; agent commits each attempt; ignored.txt created by agent
    assert r.status == "failed" and r.commits == []
    assert git(repo, "rev-parse", "HEAD").strip() == r.base_commit and (repo / "ignored.txt").exists()

async def test_reset_off_keeps_commits(repo, prompt_info):
    # reset_on_failure=False, checks=["exit 1"], max_attempts=1
    assert r.status == "failed" and len(r.commits) == 1
    assert git(repo, "rev-parse", "HEAD").strip() == r.commits[0].id

async def test_review_agent_defaults_to_change_agent(repo, prompt_info):
    # review=True; the one agent answers both the change prompt and the review prompt
    # (tell them apart by the result file path containing "/review/")
    assert r.status == "completed" and len(agent.requests) == 2

async def test_work_folder_kept(repo, tmp_path, prompt_info):
    # settings work_folder=tmp_path / "w", checks=["true"]
    assert (tmp_path / "w" / "attempt-1" / "change" / "result.json").exists()
    assert (tmp_path / "w" / "attempt-1" / "change.diff").exists()
    assert (tmp_path / "w" / "attempt-1" / "checks" / "1.log").exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/workflows/code-change/tests/test_changer.py -q`
Expected: FAIL with `ImportError: cannot import name 'CodeChanger'`.

- [ ] **Step 3: Implement `changer.py` and export `CodeChanger`**

- [ ] **Step 4: Write `packages/workflows/code-change/README.md`**

Same structure and tone as the code reviewer's README:
- one paragraph on what it does and that it knows nothing about platforms;
- the import, and that it depends on the agent interface only;
- the usage example from the design's Python API;
- Input and Settings tables;
- Output, statuses and their reasons;
- "How a run works": the stages, attempts, the commit check, the reset and its opt-out;
- the warning that nothing stops the review agent writing elsewhere, and that the guard only catches changes to HEAD and the work tree.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/workflows/code-change -q && uv run pytest packages/workflows/code-change -m git -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/workflows/code-change
git commit -m "Add CodeChanger and code change README"
```

---

### Task 7: Root README, isolated install check and pull request names

On `code-change-6-changer`, after Task 6.

**Files:**
- Modify: `README.md` (root)

- [ ] **Step 1: Add the package to the root README**

Add a row to the Packages table: `workflows-code-change`, import `workflows.code_change`, "Applies one instruction to a codebase, commits it and returns a status, the commits and a description." Add `code-change/   workflows-code-change` under `workflows/` in the tree.

- [ ] **Step 2: Run `uv run python scripts/check_isolated_installs.py`**

Expected: `workflows-code-change: ok` along with the other three packages, and exit code 0.

- [ ] **Step 3: Check that the isolated check catches a dependency on another workflow**

Temporarily add `import workflows.code_review` at the top of `changer.py`. Run the script and expect `workflows-code-change: FAILED` with exit code 1. Then revert the change and don't commit it.

- [ ] **Step 4: Run everything once more, commit and push**

Run: `uv run pytest -q && uv run pytest -m git -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

```bash
git add README.md
git commit -m "List code change workflow in README"
git push -u origin code-change-6-changer
```

- [ ] **Step 5: Draft the pull request names**

Write a message for the user listing, for each branch in the Branches table: the branch, the base it should target, and a suggested pull request title.
