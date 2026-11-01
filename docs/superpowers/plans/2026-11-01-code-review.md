# Code Review Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `workflows-code-review`. It reviews a change in several agent passes and returns a status, comments, thread actions and a coverage report.

**Architecture:** Workflow code does everything that must be guaranteed:
- builds the change list (from git or a diffs folder) and the batches;
- loads and routes the threads;
- validates every agent result, retries once, and keeps invalid comments by moving them to the change level;
- merges checked results into `review-passes/`;
- assembles the final result.

Agents only read files and write JSON results into their own `agent-output/` folder. Each concern lives in a small module with pure functions. `CodeReviewer.review` joins them.

**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, `git` CLI (subprocess), `workflows-agent` core (`Agent`, `FakeAgent`), pytest + pytest-asyncio.

**Spec:** `docs/workflows/code-reviewer/design.md` (on the `design` branch). It builds on `docs/workflows/code-reviewer/requirements.md`, which remains the authority.

## Branches

- Work on `code-review`, branched from `origin/dev`.
- When it's finished, merge into `dev` with `--no-ff` and push `dev`. Never merge into `main`.

## Global Constraints

- Distribution `workflows-code-review`, import `workflows.code_review`, folder `packages/workflows/code-review/`.
- `src/workflows/` has no `__init__.py`; `workflows` is a PEP 420 namespace.
- Runtime dependencies: `workflows-agent` and `pydantic>=2` only. The package never imports `workflows.agent_claude_code`, not even in tests.
- `requires-python = ">=3.11"`.
- Only `gitchanges.py` runs `git`. Every other module works on `ChangedFile` lists.
- Every test that runs `git` is marked `@pytest.mark.git`. The default run excludes them.
- Settings defaults: `lines_per_batch=1000`, `files_per_batch=10`, `directory_break_min_fill=0.5`, `max_parallel_calls=4`, `agent_timeout_seconds=1800`, `work_folder=None`.
- Reasons and labels, exactly as written:
  - coverage reasons: `binary file`, `no valid result after retry`, `no codebase`, `no changes`, `only binary files`;
  - pass folder names: `per-file`, `overall`, `test-coverage`, `code-patterns`, `consolidation`.
- Commit messages are short and plain, with no AI or Claude attribution trailers.
- Run every command from the repo root.

## Review Focus

- **Paths with spaces or non-ASCII characters in git mode.** They must be listed and diffed correctly; `-z` parsing avoids git's quoting. Test: `test_paths_with_spaces_and_unicode` (Task 3).
- **One per-file batch raises `AgentError` while others are in flight.** The review must raise that `AgentError` itself, not an `ExceptionGroup`, and must cancel the other calls. Test: `test_agent_error_cancels_and_propagates` (Task 9).
- **A comment ending on the file's last line.** It must be valid; one line past the end is invalid. That includes a file with no trailing newline. Test: `test_line_range_bounds` (Task 6).
- **`work_folder` points at a folder that already has files.** The review must raise `ReviewInputError` rather than mixing old and new results. Test: `test_work_folder_must_be_empty` (Task 9).
- **A thread on a renamed file's old path.** It must route to that file's batch. Test: `test_thread_on_old_path_routes_to_batch` (Task 5).

---

### Task 1: Package skeleton, public models and file formats

**Files:**
- Create: `packages/workflows/code-review/pyproject.toml`
- Create: `packages/workflows/code-review/src/workflows/code_review/__init__.py`, `py.typed` (empty)
- Create: `packages/workflows/code-review/src/workflows/code_review/models.py`
- Create: `packages/workflows/code-review/src/workflows/code_review/errors.py`
- Create: `packages/workflows/code-review/src/workflows/code_review/formats.py`
- Modify: `pyproject.toml` (root)
- Test: `packages/workflows/code-review/tests/test_models.py`

**Interfaces:**
- Produces, in `models.py` and re-exported from `workflows.code_review`:
  - `class ReviewPass(StrEnum)`: `PER_FILE = "per-file"`, `OVERALL = "overall"`, `TEST_COVERAGE = "test-coverage"`, `CODE_PATTERNS = "code-patterns"`, `CONSOLIDATION = "consolidation"`. The values double as folder names.
  - Targets:
    - `LinesTarget`: `type: Literal["lines"] = "lines"`, `path: str`, `start_line: int`, `end_line: int`, `side: Literal["new", "old"] = "new"`;
    - `FileTarget`: `type: Literal["file"] = "file"`, `path: str`;
    - `ChangeTarget`: `type: Literal["change"] = "change"`;
    - `Target = Annotated[LinesTarget | FileTarget | ChangeTarget, Field(discriminator="type")]`.
  - `Comment`: `target: Target`, `body: str`, `reply_to: str | None = None`.
  - `ThreadAction`: `thread_id: str`, `action: Literal["resolve", "reopen"]`.
  - Coverage:
    - `FileCoverage`: `path: str`, `state: Literal["reviewed", "skipped", "unreviewed"]`, `reason: str | None = None`;
    - `PassCoverage`: `review_pass: ReviewPass`, `state: Literal["ran", "skipped", "failed"]`, `reason: str | None = None`;
    - `DroppedThreadAction`: `thread_id: str`, `action: Literal["resolve", "reopen"]`, `review_pass: ReviewPass`, `reason: str`;
    - `Coverage`: `files: list[FileCoverage] = []`, `passes: list[PassCoverage] = []`, `dropped_thread_actions: list[DroppedThreadAction] = []`.
  - `ReviewResult`: `status: Literal["approved", "changes_requested", "inconclusive"]`, `comments: list[Comment] = []`, `thread_actions: list[ThreadAction] = []`, `coverage: Coverage = Coverage()`.
  - Inputs:
    - `Codebase`: `path: Path`, `base_commit: str`;
    - `ReviewInput`: `codebase: Codebase | None = None`, `diffs: Path | None = None`, `discussions: list[Path] = []`, `description: str | None = None`, `instructions: str | None = None`, `change_author: str | None = None`, `reviewer_name: str | None = None`. It doesn't check that `codebase` or `diffs` is present; `review()` does, raising `ReviewInputError` (Task 9).
  - `ReviewSettings`, frozen, with the defaults from Global Constraints and these checks: `lines_per_batch >= 1`, `files_per_batch >= 1`, `0 <= directory_break_min_fill <= 1`, `max_parallel_calls >= 1`, `agent_timeout_seconds > 0` or `None`.
  - `ReviewEvent`: `review_pass: ReviewPass`, `batch: int | None`, `attempt: int`, `event: workflows.agent.Event`.
- Produces `errors.py`: `class ReviewInputError(ValueError)`, re-exported.
- Produces `formats.py`, the file schemas, not re-exported. Extra keys are ignored, which is Pydantic's default.
  - `Message`: `author: str`, `body: str`.
  - `Thread`: `id: str`, `target: Target`, `state: Literal["open", "resolved"]`, `messages: list[Message]`.
  - `PerFileResult`: `path: str`, `summary: str`, `comments: list[Comment] = []`, `thread_actions: list[ThreadAction] = []`.
  - `PassResult`: `comments: list[Comment] = []`, `thread_actions: list[ThreadAction] = []`.
  - `ConsolidationResult`: `status: Literal["approved", "changes_requested", "inconclusive"]`, `comments: list[Comment] = []`, `thread_actions: list[ThreadAction] = []`.

- [ ] **Step 1: Set up the package and wire it into the workspace**

`packages/workflows/code-review/pyproject.toml`:
- name `workflows-code-review`, version `0.1.0`, `requires-python = ">=3.11"`;
- `dependencies = ["workflows-agent", "pydantic>=2"]`;
- `[tool.uv.sources] workflows-agent = { workspace = true }`;
- hatchling, with `[tool.hatch.build.targets.wheel] packages = ["src/workflows"]`.

In the root `pyproject.toml`:
- `[tool.uv.workspace] members = ["packages/agents/*", "packages/workflows/*"]`;
- add `"workflows-code-review"` to `dependencies`;
- add `workflows-code-review = { workspace = true }` to `[tool.uv.sources]`.

- [ ] **Step 2: Write the failing tests in `tests/test_models.py`**

```python
import pytest
from pydantic import TypeAdapter, ValidationError
from workflows.code_review import (ChangeTarget, Comment, FileTarget, LinesTarget, ReviewPass,
                                   ReviewResult, ReviewSettings, Target)
from workflows.code_review.formats import PerFileResult, Thread

def test_target_json_round_trip():
    for t in [LinesTarget(path="a.py", start_line=1, end_line=2, side="old"), FileTarget(path="a.py"), ChangeTarget()]:
        assert TypeAdapter(Target).validate_json(TypeAdapter(Target).dump_json(t)) == t

def test_settings_defaults():
    s = ReviewSettings()
    assert (s.lines_per_batch, s.files_per_batch, s.directory_break_min_fill,
            s.max_parallel_calls, s.agent_timeout_seconds, s.work_folder) == (1000, 10, 0.5, 4, 1800, None)

@pytest.mark.parametrize("bad", [{"lines_per_batch": 0}, {"files_per_batch": 0},
    {"directory_break_min_fill": 1.5}, {"max_parallel_calls": 0}, {"agent_timeout_seconds": 0}])
def test_settings_reject_bad_values(bad):
    with pytest.raises(ValidationError):
        ReviewSettings(**bad)

def test_pass_values_are_folder_names():
    assert [p.value for p in ReviewPass] == ["per-file", "overall", "test-coverage", "code-patterns", "consolidation"]

def test_formats_parse_and_ignore_extra_keys():
    t = Thread.model_validate({"id": "t1", "target": {"type": "change"}, "state": "open",
                               "messages": [{"author": "a", "body": "b"}], "extra": 1})
    assert t.id == "t1"
    r = PerFileResult.model_validate_json('{"path": "a.py", "summary": "s", "comments": [{"target": {"type": "file", "path": "a.py"}, "body": "x"}]}')
    assert r.comments[0].reply_to is None

def test_result_json_round_trip():
    r = ReviewResult(status="approved", comments=[Comment(target=ChangeTarget(), body="ok")])
    assert ReviewResult.model_validate_json(r.model_dump_json()) == r
```

- [ ] **Step 3: Run `uv sync && uv run pytest packages/workflows/code-review/tests -q`**

Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `models.py`, `errors.py` and `formats.py`, and export the public names from `__init__.py`**

- [ ] **Step 5: Run all the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 6: Commit**

`git add pyproject.toml uv.lock packages/workflows/code-review && git commit -m "Add code review package and models"`

---

### Task 2: Change list from a diffs folder

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/changes.py`
- Test: `packages/workflows/code-review/tests/test_changes.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ChangedFile:
      path: str                      # stored path: new path for renames, old path for deletions
      status: Literal["added", "modified", "deleted", "renamed"]
      old_path: str | None           # set only for renames
      binary: bool
      changed_lines: int             # added + removed lines
      @property
      def counts_toward_limits(self) -> bool   # False for deleted, and for renamed with changed_lines == 0
      @property
      def paths(self) -> frozenset[str]        # {path} plus old_path when set

  def parse_diff(path: str, text: str) -> ChangedFile
  def changes_from_diffs(source: Path, diffs_dir: Path) -> list[ChangedFile]
  def diff_file(diffs_dir: Path, path: str) -> Path       # diffs_dir / f"{path}.diff"
  ```

How `parse_diff` works:
- **Status:** `new file mode` gives added, `deleted file mode` gives deleted, and `rename from X` plus `rename to Y` give renamed with `old_path = X`. Anything else is modified.
- **Binary:** any line starting with `Binary files ` or equal to `GIT binary patch`.
- **Line count:** count the lines starting with `+` or `-`, but only after the first line starting with `@@`. Before that, `---`/`+++` are headers; after it, a removed line that itself starts with `--` is still counted.

`changes_from_diffs`:
- raises `ReviewInputError` if `source` isn't a directory;
- walks `**/*.diff`; each path is the posix relative path minus `.diff`;
- copies each diff to `diff_file(diffs_dir, path)`, creating parent folders;
- returns the files sorted by path.

- [ ] **Step 1: Write the failing tests in `tests/test_changes.py`**

```python
from pathlib import Path
import pytest
from workflows.code_review import ReviewInputError
from workflows.code_review.changes import changes_from_diffs, parse_diff

MODIFIED = "diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-x\n+y\n--- removed line starting with dashes\n context\n"
ADDED = "diff --git a/n.py b/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
DELETED = "diff --git a/d.py b/d.py\ndeleted file mode 100644\n--- a/d.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n"
RENAMED = "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
BINARY = "diff --git a/i.png b/i.png\nindex 1..2 100644\nBinary files a/i.png and b/i.png differ\n"

def test_modified_counts_only_hunk_lines():
    f = parse_diff("a.py", MODIFIED)
    assert (f.status, f.changed_lines, f.binary, f.old_path) == ("modified", 3, False, None)

def test_added_deleted_renamed_binary():
    assert parse_diff("n.py", ADDED).status == "added"
    d = parse_diff("d.py", DELETED)
    assert d.status == "deleted" and not d.counts_toward_limits
    r = parse_diff("new.py", RENAMED)
    assert (r.status, r.old_path, r.changed_lines, r.counts_toward_limits) == ("renamed", "old.py", 0, False)
    assert r.paths == {"new.py", "old.py"}
    assert parse_diff("i.png", BINARY).binary

def test_changes_from_diffs_copies_and_sorts(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py.diff").write_text(MODIFIED)
    (src / "n.py.diff").write_text(ADDED)
    out = tmp_path / "work" / "input" / "diffs"
    files = changes_from_diffs(src, out)
    assert [f.path for f in files] == ["n.py", "pkg/a.py"]
    assert (out / "pkg" / "a.py.diff").read_text() == MODIFIED

def test_missing_diffs_folder(tmp_path):
    with pytest.raises(ReviewInputError):
        changes_from_diffs(tmp_path / "nope", tmp_path / "out")
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_changes.py -q`**

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `changes.py`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

`git commit -am "Build change list from a diffs folder"` (`git add` the new files first)

---

### Task 3: Change list from git

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/gitchanges.py`
- Modify: `pyproject.toml` (root): `[tool.pytest.ini_options]`
- Test: `packages/workflows/code-review/tests/test_gitchanges.py`

**Interfaces:**
- Consumes: `ChangedFile`, `diff_file`, `parse_diff` (Task 2), `Codebase`, `ReviewInputError` (Task 1).
- Produces:
  - `check_codebase(codebase: Codebase) -> None`: raises `ReviewInputError` if the path isn't a git work tree (`git rev-parse --is-inside-work-tree`), or if `git rev-parse --verify --quiet <base>^{commit}` fails.
  - `changes_from_git(codebase: Codebase, diffs_dir: Path) -> list[ChangedFile]`.

How `changes_from_git` works. Run every git command with `cwd=codebase.path` and `-c core.quotepath=off`. Use `--no-color --no-ext-diff` on `diff`.
1. **Tracked changes:** `git diff -M -z --name-status <base>`. Its NUL-separated tokens are a status (`A`, `M`, `D`, `T`, or `R<score>`) followed by one path, or two for `R`. `T` counts as modified.
2. **Line counts:** `git diff -M -z --numstat <base>`. Each entry is either `added\tremoved\tpath\0`, or for a rename `added\tremoved\t\0old\0new\0`. `-\t-` means binary. `changed_lines` is added plus removed, or 0 for binary.
3. **Untracked files:** `git ls-files --others --exclude-standard -z`, each with status added. For each one, `git diff --no-index --numstat -- /dev/null <path>` gives its line count; exit code 1 is normal here.
4. **Diff files:** write each file's diff to `diff_file(diffs_dir, path)`:
   - tracked: `git diff -M <base> -- <old_path> <path>` for renames, otherwise `-- <path>`;
   - untracked: `git diff --no-index -- /dev/null <path>`.
5. Return the files sorted by path.

Root pytest config:
- `addopts = "--import-mode=importlib -m 'not git'"`;
- `markers = ["git: runs git; excluded by default, run with -m git"]`.

- [ ] **Step 1: Add the marker config, then write the failing tests in `tests/test_gitchanges.py`**

One module-scoped repository holds every scenario.

```python
import subprocess
from pathlib import Path
import pytest
from workflows.code_review import Codebase, ReviewInputError
from workflows.code_review.gitchanges import changes_from_git, check_codebase

pytestmark = pytest.mark.git

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout

@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> tuple[Path, str]:
    r = tmp_path_factory.mktemp("repo")
    git(r, "init", "-q")
    (r / ".gitignore").write_text("ignored.txt\n")
    for name in ["keep.py", "modify.py", "stage.py", "old_name.py", "remove.py"]:
        (r / name).write_text(f"{name}\nline2\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "base")
    base = git(r, "rev-parse", "HEAD").strip()
    (r / "committed.py").write_text("c\n"); git(r, "add", "committed.py"); git(r, "commit", "-qm", "after base")
    (r / "stage.py").write_text("staged\n"); git(r, "add", "stage.py")
    (r / "modify.py").write_text("modify.py\nchanged\nadded\n")
    git(r, "mv", "old_name.py", "new_name.py")
    git(r, "rm", "-q", "remove.py")
    (r / "untracked.py").write_text("u1\nu2\n")
    (r / "ignored.txt").write_text("secret\n")
    (r / "image.bin").write_bytes(b"\x00\x01\x02")
    (r / "dir with space").mkdir(); (r / "dir with space" / "naïve.py").write_text("x\n")
    return r, base

def by_path(repo, tmp_path):
    r, base = repo
    return {f.path: f for f in changes_from_git(Codebase(path=r, base_commit=base), tmp_path / "diffs")}

def test_lists_every_kind_of_change(repo, tmp_path):
    files = by_path(repo, tmp_path)
    assert set(files) == {"committed.py", "stage.py", "modify.py", "new_name.py", "remove.py",
                          "untracked.py", "image.bin", "dir with space/naïve.py"}
    assert files["new_name.py"].status == "renamed" and files["new_name.py"].old_path == "old_name.py"
    assert files["remove.py"].status == "deleted"
    assert files["untracked.py"].status == "added" and files["untracked.py"].changed_lines == 2
    assert files["image.bin"].binary
    assert files["modify.py"].changed_lines == 3

def test_writes_diff_files(repo, tmp_path):
    by_path(repo, tmp_path)
    assert "+changed" in (tmp_path / "diffs" / "modify.py.diff").read_text()
    assert "rename from old_name.py" in (tmp_path / "diffs" / "new_name.py.diff").read_text()
    assert "+u1" in (tmp_path / "diffs" / "untracked.py.diff").read_text()

def test_paths_with_spaces_and_unicode(repo, tmp_path):
    files = by_path(repo, tmp_path)
    assert files["dir with space/naïve.py"].status == "added"
    assert (tmp_path / "diffs" / "dir with space" / "naïve.py.diff").exists()

def test_check_codebase_errors(repo, tmp_path):
    r, _ = repo
    with pytest.raises(ReviewInputError):
        check_codebase(Codebase(path=tmp_path, base_commit="HEAD"))
    with pytest.raises(ReviewInputError):
        check_codebase(Codebase(path=r, base_commit="0" * 40))
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_gitchanges.py -m git -q`**

Expected: FAIL with `ModuleNotFoundError`. Also run `uv run pytest -q` and confirm these tests are deselected.

- [ ] **Step 3: Implement `gitchanges.py`**

- [ ] **Step 4: Run the checks, including the git tests**

Run: `uv run pytest -q && uv run pytest -m git -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

`git commit -m "Build change list from git"` (`git add` the new files and the root `pyproject.toml` first)

---

### Task 4: Batching

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/batching.py`
- Test: `packages/workflows/code-review/tests/test_batching.py`

**Interfaces:**
- Consumes: `ChangedFile` (Task 2) and `ReviewSettings` (Task 1).
- Produces: `make_batches(files: list[ChangedFile], settings: ReviewSettings) -> list[list[ChangedFile]]`. The caller passes non-binary files only.

The algorithm, from the design's Batching section:

```
sort by path; current=[]; lines=0; count=0
for f in files:
    add_lines, add_files = (f.changed_lines, 1) if f.counts_toward_limits else (0, 0)
    if current:
        breach = lines + add_lines > L or count + add_files > F
        fill = max(lines / L, count / F)
        new_dir = top_dir(f.path) != top_dir(current[-1].path) and fill >= directory_break_min_fill
        if breach or new_dir: flush current
    append f; lines += add_lines; count += add_files
flush
```

`top_dir(path)` is the part before the first `/`, or `""` for a file at the repo root.

- [ ] **Step 1: Write the failing tests in `tests/test_batching.py`**

```python
from workflows.code_review import ReviewSettings
from workflows.code_review.batching import make_batches
from workflows.code_review.changes import ChangedFile

def f(path, lines=10, status="modified"):
    return ChangedFile(path=path, status=status, old_path="x" if status == "renamed" else None,
                       binary=False, changed_lines=lines)

def paths(batches):
    return [[x.path for x in b] for b in batches]

def test_line_limit_is_hard():
    s = ReviewSettings(lines_per_batch=100, files_per_batch=10, directory_break_min_fill=1)
    assert paths(make_batches([f("a", 60), f("b", 40), f("c", 1)], s)) == [["a", "b"], ["c"]]

def test_file_limit_is_hard():
    s = ReviewSettings(lines_per_batch=1000, files_per_batch=2, directory_break_min_fill=1)
    assert paths(make_batches([f("a"), f("b"), f("c")], s)) == [["a", "b"], ["c"]]

def test_renames_and_deletions_are_free():
    s = ReviewSettings(lines_per_batch=100, files_per_batch=1, directory_break_min_fill=1)
    files = [f("a", 100), f("b", 0, "renamed"), f("c", 50, "deleted")]
    assert paths(make_batches(files, s)) == [["a", "b", "c"]]

def test_oversized_file_gets_own_batch():
    s = ReviewSettings(lines_per_batch=100, files_per_batch=10, directory_break_min_fill=1)
    assert paths(make_batches([f("a", 10), f("b", 500), f("c", 10)], s)) == [["a"], ["b"], ["c"]]

def test_directory_break_only_when_filled_enough():
    s = ReviewSettings(lines_per_batch=100, files_per_batch=10, directory_break_min_fill=0.5)
    assert paths(make_batches([f("x/a", 40), f("y/b", 10)], s)) == [["x/a", "y/b"]]
    assert paths(make_batches([f("x/a", 50), f("y/b", 10)], s)) == [["x/a"], ["y/b"]]

def test_sorted_by_path():
    assert paths(make_batches([f("b"), f("a")], ReviewSettings())) == [["a", "b"]]
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_batching.py -q`**

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `make_batches`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

`git commit -m "Add batching"` (`git add` the new files first)

---

### Task 5: Loading and routing threads

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/threads.py`
- Test: `packages/workflows/code-review/tests/test_threads.py`

**Interfaces:**
- Consumes: `Thread` (Task 1), `ChangedFile` (Task 2), `ReviewInputError`.
- Produces:
  - `load_threads(paths: list[Path]) -> list[Thread]`. Each file must parse as a JSON list of `Thread`. It raises `ReviewInputError` naming the file for a missing file, invalid JSON or schema errors, and raises `ReviewInputError` for a duplicate thread id across files.
  - `@dataclass class Routing: batches: list[frozenset[str]]; overall: frozenset[str]`, with one set of thread ids per batch, by batch index.
  - `route_threads(threads: list[Thread], batches: list[list[ChangedFile]]) -> Routing`.
    - A thread whose target is lines or a file goes to the batch holding a file whose `paths` contains the target path.
    - Every other thread goes to `overall`: change targets, and paths in no batch, such as binary or unchanged files.

- [ ] **Step 1: Write the failing tests in `tests/test_threads.py`**

```python
import json
import pytest
from workflows.code_review import ReviewInputError
from workflows.code_review.changes import ChangedFile
from workflows.code_review.formats import Thread
from workflows.code_review.threads import load_threads, route_threads

def thread(id, target):
    return {"id": id, "target": target, "state": "open", "messages": [{"author": "a", "body": "b"}]}

def write(tmp_path, name, data):
    p = tmp_path / name; p.write_text(json.dumps(data)); return p

def test_load_merges_files(tmp_path):
    a = write(tmp_path, "a.json", [thread("t1", {"type": "change"})])
    b = write(tmp_path, "b.json", [thread("t2", {"type": "file", "path": "x.py"})])
    assert [t.id for t in load_threads([a, b])] == ["t1", "t2"]

@pytest.mark.parametrize("content", ["not json", json.dumps({"id": "t1"}), json.dumps([{"id": "t1"}])])
def test_load_rejects_bad_files(tmp_path, content):
    p = tmp_path / "bad.json"; p.write_text(content)
    with pytest.raises(ReviewInputError, match="bad.json"):
        load_threads([p])

def test_load_rejects_duplicate_ids(tmp_path):
    a = write(tmp_path, "a.json", [thread("t1", {"type": "change"})])
    b = write(tmp_path, "b.json", [thread("t1", {"type": "change"})])
    with pytest.raises(ReviewInputError, match="t1"):
        load_threads([a, b])

def test_routing(tmp_path):
    batches = [[ChangedFile("a.py", "modified", None, False, 1)],
               [ChangedFile("new.py", "renamed", "old.py", False, 0)]]
    threads = [Thread.model_validate(thread("t1", {"type": "lines", "path": "a.py", "start_line": 1, "end_line": 1})),
               Thread.model_validate(thread("t2", {"type": "change"})),
               Thread.model_validate(thread("t3", {"type": "file", "path": "unchanged.py"}))]
    r = route_threads(threads, batches)
    assert r.batches == [frozenset({"t1"}), frozenset()]
    assert r.overall == {"t2", "t3"}

def test_thread_on_old_path_routes_to_batch():
    batches = [[ChangedFile("new.py", "renamed", "old.py", False, 0)]]
    t = Thread.model_validate(thread("t1", {"type": "file", "path": "old.py"}))
    assert route_threads([t], batches).batches == [frozenset({"t1"})]
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_threads.py -q`**

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `threads.py`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

`git commit -m "Load and route threads"` (`git add` the new files first)

---

### Task 6: Validation and checking results

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/validation.py`
- Test: `packages/workflows/code-review/tests/test_validation.py`

**Interfaces:**
- Consumes: `Comment`, `ThreadAction`, the targets (Task 1), `PerFileResult` and the other formats.
- Produces:
  ```python
  @dataclass(frozen=True)
  class Scope:
      changed_paths: frozenset[str]          # every ChangedFile.paths, all files
      allowed_paths: frozenset[str] | None   # per-file: the batch's paths; None = changed or existing in codebase
      codebase: Path | None
      thread_ids: frozenset[str]             # threads this call may reply to or act on

  @dataclass
  class Checked:
      comments: list[Comment]                # valid ones, plus invalid ones moved or repaired
      thread_actions: list[ThreadAction]     # valid only
      dropped: list[tuple[ThreadAction, str]]  # (action, reason)
      had_invalid: bool                      # any comment or action was invalid

  def comment_problem(comment: Comment, scope: Scope) -> str | None
  def action_problem(action: ThreadAction, scope: Scope) -> str | None
  def check_items(comments: list[Comment], actions: list[ThreadAction], scope: Scope) -> Checked
  def claimed_location(target: Target) -> str
  def read_result(path: Path, model: type[M]) -> M | None    # None when missing, unparseable or schema-invalid
  def usable_per_file(result: PerFileResult | None, expected_path: str) -> bool
  ```

The rules, from the design's Validation section:
- **Target problems.**
  - A path must be in `allowed_paths` when that's set.
  - Otherwise it must be in `changed_paths`, or, with a codebase, exist as a file under `codebase`.
  - For `LinesTarget`, `start_line >= 1` and `end_line >= start_line` are required. With `side == "new"` and a codebase, `end_line` must also be at most the number of lines in the file (`len(text.splitlines())`). A missing file on the new side is a problem.
- **`reply_to` problems:** a `reply_to` not in `thread_ids`.
- **`check_items` repairs:**
  - **Target problem:** the comment becomes `Comment(target=ChangeTarget(), body=f"{claimed_location(old)}: {body}", reply_to=None)`.
  - **Only a `reply_to` problem:** the `reply_to` is set to `None` and the target is kept. This is the plan's reading of "loses the `reply_to` the same way".
- **Thread actions:** an action with a `thread_id` outside `thread_ids` is dropped, with the reason `thread not available to this pass`.
- **`claimed_location`:** `path:start-end` (or `path:start` when they're equal), plus ` (old)` for the old side; `path` for a file; `change` for a change target.
- **`usable_per_file`:** the result isn't `None`, its `path` equals `expected_path`, and its `summary.strip()` isn't empty.

- [ ] **Step 1: Write the failing tests in `tests/test_validation.py`**

```python
import pytest
from workflows.code_review import ChangeTarget, Comment, FileTarget, LinesTarget, ThreadAction
from workflows.code_review.formats import PerFileResult
from workflows.code_review.validation import Scope, check_items, read_result, usable_per_file

@pytest.fixture
def codebase(tmp_path):
    (tmp_path / "a.py").write_text("1\n2\n3")          # 3 lines, no trailing newline
    (tmp_path / "other.py").write_text("x\n")
    return tmp_path

def scope(codebase, allowed=None, threads=("t1",)):
    return Scope(changed_paths=frozenset({"a.py"}), allowed_paths=allowed,
                 codebase=codebase, thread_ids=frozenset(threads))

def lines(s, e, path="a.py", side="new"):
    return Comment(target=LinesTarget(path=path, start_line=s, end_line=e, side=side), body="b")

def test_line_range_bounds(codebase):
    c = check_items([lines(1, 3)], [], scope(codebase))
    assert not c.had_invalid and c.comments[0].target.type == "lines"
    c = check_items([lines(2, 4)], [], scope(codebase))
    assert c.had_invalid and c.comments[0] == Comment(target=ChangeTarget(), body="a.py:2-4: b")
    assert check_items([lines(0, 1)], [], scope(codebase)).had_invalid
    assert check_items([lines(3, 2)], [], scope(codebase)).had_invalid
    assert not check_items([lines(1, 99, side="old")], [], scope(codebase)).had_invalid

def test_unchanged_existing_file_allowed_unless_restricted(codebase):
    c = Comment(target=FileTarget(path="other.py"), body="b")
    assert not check_items([c], [], scope(codebase)).had_invalid
    assert check_items([c], [], scope(codebase, allowed=frozenset({"a.py"}))).had_invalid
    assert check_items([Comment(target=FileTarget(path="missing.py"), body="b")], [], scope(codebase)).had_invalid

def test_without_codebase_only_changed_paths():
    s = Scope(changed_paths=frozenset({"a.py"}), allowed_paths=None, codebase=None, thread_ids=frozenset())
    assert not check_items([lines(1, 500)], [], s).had_invalid
    assert check_items([Comment(target=FileTarget(path="b.py"), body="x")], [], s).had_invalid

def test_bad_reply_to_is_removed_but_target_kept(codebase):
    c = check_items([Comment(target=FileTarget(path="a.py"), body="b", reply_to="t9")], [], scope(codebase))
    assert c.had_invalid and c.comments == [Comment(target=FileTarget(path="a.py"), body="b")]

def test_actions_outside_scope_dropped(codebase):
    c = check_items([], [ThreadAction(thread_id="t1", action="resolve"),
                         ThreadAction(thread_id="t2", action="reopen")], scope(codebase))
    assert c.thread_actions == [ThreadAction(thread_id="t1", action="resolve")]
    assert c.dropped == [(ThreadAction(thread_id="t2", action="reopen"), "thread not available to this pass")]

def test_read_result_and_usable(tmp_path):
    assert read_result(tmp_path / "missing.json", PerFileResult) is None
    (tmp_path / "bad.json").write_text("```json\n{}\n```")
    assert read_result(tmp_path / "bad.json", PerFileResult) is None
    (tmp_path / "ok.json").write_text('{"path": "a.py", "summary": "s"}')
    r = read_result(tmp_path / "ok.json", PerFileResult)
    assert usable_per_file(r, "a.py") and not usable_per_file(r, "b.py")
    assert not usable_per_file(PerFileResult(path="a.py", summary="  "), "a.py")
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_validation.py -q`**

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `validation.py`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

`git commit -m "Validate and repair agent results"` (`git add` the new files first)

---

### Task 7: Prompts

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/prompts.py`
- Create: `packages/workflows/code-review/src/workflows/code_review/prompts/shared.md`, `per-file.md`, `overall.md`, `test-coverage.md`, `code-patterns.md`, `consolidation.md`
- Test: `packages/workflows/code-review/tests/test_prompts.py`

**Interfaces:**
- Consumes: `ReviewPass`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class PromptContext:
      work: Path
      codebase: Path | None
      description: str | None
      instructions: str | None
      change_author: str | None
      reviewer_name: str | None

  def per_file_prompt(ctx: PromptContext, output_folder: Path, slots: list[tuple[int, str]], thread_ids: list[str]) -> str
  def pass_prompt(ctx: PromptContext, review_pass: ReviewPass, output_folder: Path, changed_paths: list[str],
                  unreviewed: list[str], thread_ids: list[str]) -> str          # overall, test coverage, code patterns
  def consolidation_prompt(ctx: PromptContext, output_folder: Path, thread_ids: list[str], coverage_text: str) -> str
  ```

**Templates.** They load with `importlib.resources.files("workflows.code_review") / "prompts" / f"{name}.md"` and fill with `string.Template.substitute`, so the JSON examples' braces need no escaping.

Each pass template holds:
- the pass's job and what isn't its job, worded from the requirements' pass table;
- the exact JSON result format from the design's File formats section;
- `$shared`, which is filled from `shared.md`.

`shared.md` holds:
- the description and the review instructions, or `(none)`;
- `input/diffs/`, `input/threads.json` and `review-passes/` as absolute paths;
- the codebase path, or a note that only diffs are available;
- who the change author and the reviewer are, with the rule to weigh the author's replies and not to repeat its own earlier comments;
- the rule that every thread may be read as context, but only the listed thread ids may be replied to or acted on, and a note when the list is empty;
- the rule to write only inside the output folder.

**Lines the code and tests rely on**, each exactly once and on a line of its own:
- every prompt: `Output folder: <absolute path>`;
- the per-file prompt: one `Slot <n>: <path>` line per slot. It tells the agent to write slot n's result to `<output folder>/<n>.json`;
- every other prompt: `Result file: <output folder>/result.json`;
- every prompt: `Threads you may act on: <comma-separated ids or none>`.

Extra content:
- The overall prompt lists `unreviewed` files and points at `review-passes/per-file/` for the summaries.
- The consolidation prompt includes `coverage_text`. It also tells the agent:
  - to keep or merge new comments;
  - to drop any new comment that repeats an open thread;
  - never to add new issues;
  - that earlier replies and actions are kept automatically;
  - to set `changes_requested` while any of the reviewer's own open threads still blocks.

- [ ] **Step 1: Write the failing tests in `tests/test_prompts.py`**

```python
import re
from pathlib import Path
from workflows.code_review import ReviewPass
from workflows.code_review.prompts import PromptContext, consolidation_prompt, pass_prompt, per_file_prompt

CTX = PromptContext(work=Path("/w"), codebase=Path("/repo"), description="Adds retry",
                    instructions="Prefer small functions", change_author="alice", reviewer_name="bot")

def test_per_file_prompt_lines():
    p = per_file_prompt(CTX, Path("/w/agent-output/per-file/batch-1/attempt-1"), [(1, "a.py"), (2, "b/c.py")], ["t1"])
    assert re.findall(r"^Output folder: (.+)$", p, re.M) == ["/w/agent-output/per-file/batch-1/attempt-1"]
    assert re.findall(r"^Slot (\d+): (.+)$", p, re.M) == [("1", "a.py"), ("2", "b/c.py")]
    assert re.findall(r"^Threads you may act on: (.+)$", p, re.M) == ["t1"]
    for text in ["Adds retry", "Prefer small functions", "alice", "bot", "/w/input/diffs", "/w/input/threads.json", "/repo"]:
        assert text in p

def test_pass_prompts_have_result_file_and_no_slots():
    for rp in [ReviewPass.OVERALL, ReviewPass.TEST_COVERAGE, ReviewPass.CODE_PATTERNS]:
        out = Path(f"/w/agent-output/{rp.value}/attempt-1")
        p = pass_prompt(CTX, rp, out, ["a.py"], ["b.py"], [])
        assert re.findall(r"^Result file: (.+)$", p, re.M) == [f"{out}/result.json"]
        assert not re.findall(r"^Slot ", p, re.M)
        assert re.findall(r"^Threads you may act on: (.+)$", p, re.M) == ["none"]
    assert "b.py" in pass_prompt(CTX, ReviewPass.OVERALL, Path("/o"), ["a.py"], ["b.py"], [])

def test_consolidation_prompt():
    p = consolidation_prompt(CTX, Path("/w/agent-output/consolidation/attempt-1"), ["t3"], "files: 1 unreviewed")
    assert "files: 1 unreviewed" in p and "/w/review-passes" in p
    assert re.findall(r"^Threads you may act on: (.+)$", p, re.M) == ["t3"]

def test_missing_text_shows_none():
    ctx = PromptContext(work=Path("/w"), codebase=None, description=None, instructions=None,
                        change_author=None, reviewer_name=None)
    assert "(none)" in per_file_prompt(ctx, Path("/o"), [(1, "a.py")], [])
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_prompts.py -q`**

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the templates and implement `prompts.py`**

- [ ] **Step 4: Run all the checks, and confirm the templates ship in the wheel**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv build --package workflows-code-review -o /tmp/cr-dist -q && unzip -l /tmp/cr-dist/*.whl | grep prompts/per-file.md`
Expected: all tests pass, and the wheel lists `workflows/code_review/prompts/per-file.md`.

- [ ] **Step 5: Commit**

`git commit -m "Add review prompts"` (`git add` the new files first)

---

### Task 8: Running calls and passes with retries

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/calls.py`
- Create: `packages/workflows/code-review/src/workflows/code_review/passes.py`
- Create: `packages/workflows/code-review/tests/conftest.py`
- Test: `packages/workflows/code-review/tests/test_passes.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 6 and 7, and from the core: `Agent`, `AgentRequest`, `FakeAgent` (tests only).
- Produces `calls.py`:
  ```python
  @dataclass
  class CallRunner:
      agents: Callable[[ReviewPass], Agent]       # pass -> agent (pass_agents or default)
      working_folder: Path
      work: Path
      timeout_seconds: float | None
      semaphore: asyncio.Semaphore
      on_event: Callable[[ReviewEvent], None] | None
      async def call(self, review_pass: ReviewPass, batch: int | None, attempt: int,
                     output_folder: Path, prompt: str) -> None
  def output_folder(work: Path, review_pass: ReviewPass, batch: int | None, attempt: int) -> Path
      # work/agent-output/<pass>/[batch-<k>/]attempt-<a>
  ```
  `call` works like this:
  - it creates `output_folder`;
  - under the semaphore, it runs `agent.run(AgentRequest(prompt, working_folder, extra_folders=[work], timeout_seconds), on_event=...)`;
  - each agent event is wrapped in `ReviewEvent(review_pass, batch, attempt, event)`;
  - `AgentError` propagates.
- Produces `passes.py`:
  ```python
  @dataclass
  class FileOutcome:
      checked: dict[str, PerFileResult]     # path -> checked result (comments repaired, actions filtered)
      unreviewed: list[str]
      dropped: list[DroppedThreadAction]

  async def run_per_file_batch(runner: CallRunner, ctx: PromptContext, batch_index: int,
                               files: list[ChangedFile], scope: Scope) -> FileOutcome
  @dataclass
  class PassOutcome:
      result: PassResult | ConsolidationResult | None   # checked; None = failed
      dropped: list[DroppedThreadAction]

  async def run_pass(runner: CallRunner, review_pass: ReviewPass, prompt_for: Callable[[Path], str],
                     scope: Scope, model: type[PassResult] | type[ConsolidationResult]) -> PassOutcome
  ```

`run_per_file_batch`:
1. **Attempt 1:** slots `1..n` for `files`, in order. Read each slot with `read_result`.
2. **Retry list:** a file goes on it if its result isn't usable, or if `check_items` reports `had_invalid`.
3. **Attempt 2:** if the list isn't empty, call again with slots `1..m` for just those files, in `attempt-2`.
4. **Choosing a result:** for each file, use the retry's result if it's usable, otherwise attempt 1's. If neither is usable, the file is unreviewed.
5. Run `check_items` on the chosen result to repair its comments and filter its actions. Dropped actions become `DroppedThreadAction(..., review_pass=PER_FILE, reason=...)`.

`run_pass` follows the same flow with a single `result.json`. "Usable" means `read_result` isn't `None`.

**`conftest.py` fixtures:**
- `prompt_info`: functions `output_folder(prompt) -> Path`, `slots(prompt) -> list[tuple[int, str]]` and `result_file(prompt) -> Path`, parsed from the lines Task 7 pins.
- `runner_for(agent, work, on_event=None) -> CallRunner`: semaphore 4, timeout `None`, working folder `work`.

- [ ] **Step 1: Write the conftest, then the failing tests in `tests/test_passes.py`**

```python
import asyncio, json
from pathlib import Path
import pytest
from workflows.agent import AgentError, TextEvent
from workflows.agent.testing import FakeAgent
from workflows.code_review import ReviewEvent, ReviewPass
from workflows.code_review.changes import ChangedFile
from workflows.code_review.formats import PassResult
from workflows.code_review.passes import run_pass, run_per_file_batch
from workflows.code_review.prompts import PromptContext
from workflows.code_review.validation import Scope

def files(*paths):
    return [ChangedFile(p, "modified", None, False, 1) for p in paths]

def ctx(work):
    return PromptContext(work=work, codebase=None, description=None, instructions=None,
                         change_author=None, reviewer_name=None)

def scope(paths, threads=()):
    return Scope(changed_paths=frozenset(paths), allowed_paths=frozenset(paths), codebase=None, thread_ids=frozenset(threads))

def per_file_writer(prompt_info, make):          # make(path, attempt) -> dict | str | None
    def respond(req):
        out = prompt_info.output_folder(req.prompt)
        attempt = int(out.name.split("-")[1])
        for n, path in prompt_info.slots(req.prompt):
            content = make(path, attempt)
            if content is not None:
                (out / f"{n}.json").write_text(content if isinstance(content, str) else json.dumps(content))
        return "done"
    return respond

def good(path, comments=()):
    return {"path": path, "summary": "ok", "comments": list(comments)}

async def test_all_valid_needs_one_call(tmp_path, prompt_info, runner_for):
    agent = FakeAgent(per_file_writer(prompt_info, lambda p, a: good(p)))
    out = await run_per_file_batch(runner_for(agent, tmp_path), ctx(tmp_path), 1, files("a.py", "b.py"), scope({"a.py", "b.py"}))
    assert len(agent.requests) == 1 and set(out.checked) == {"a.py", "b.py"} and out.unreviewed == []

async def test_retry_only_failed_files_and_mark_unreviewed(tmp_path, prompt_info, runner_for):
    agent = FakeAgent(per_file_writer(prompt_info, lambda p, a: good(p) if p == "a.py" else None))
    out = await run_per_file_batch(runner_for(agent, tmp_path), ctx(tmp_path), 1, files("a.py", "b.py"), scope({"a.py", "b.py"}))
    assert len(agent.requests) == 2
    assert prompt_info.slots(agent.requests[1].prompt) == [(1, "b.py")]
    assert out.unreviewed == ["b.py"] and set(out.checked) == {"a.py"}

async def test_invalid_comment_retried_then_moved(tmp_path, prompt_info, runner_for):
    bad = {"target": {"type": "file", "path": "zzz.py"}, "body": "hm"}
    agent = FakeAgent(per_file_writer(prompt_info, lambda p, a: good(p, [bad])))
    out = await run_per_file_batch(runner_for(agent, tmp_path), ctx(tmp_path), 1, files("a.py"), scope({"a.py"}))
    assert len(agent.requests) == 2
    assert out.checked["a.py"].comments[0].target.type == "change"
    assert out.checked["a.py"].comments[0].body == "zzz.py: hm"

async def test_retry_unusable_falls_back_to_first(tmp_path, prompt_info, runner_for):
    bad = {"target": {"type": "file", "path": "zzz.py"}, "body": "hm"}
    agent = FakeAgent(per_file_writer(prompt_info, lambda p, a: good(p, [bad]) if a == 1 else "not json"))
    out = await run_per_file_batch(runner_for(agent, tmp_path), ctx(tmp_path), 1, files("a.py"), scope({"a.py"}))
    assert "a.py" in out.checked and out.unreviewed == []

async def test_events_are_labeled(tmp_path, prompt_info, runner_for):
    seen: list[ReviewEvent] = []
    agent = FakeAgent(per_file_writer(prompt_info, lambda p, a: good(p)), events=[TextEvent(call_id="x", text="hi")])
    await run_per_file_batch(runner_for(agent, tmp_path, seen.append), ctx(tmp_path), 3, files("a.py"), scope({"a.py"}))
    assert [(e.review_pass, e.batch, e.attempt) for e in seen] == [(ReviewPass.PER_FILE, 3, 1)]

async def test_run_pass_failed_after_retry(tmp_path, runner_for):
    agent = FakeAgent(lambda req: "wrote nothing")
    out = await run_pass(runner_for(agent, tmp_path), ReviewPass.TEST_COVERAGE,
                         lambda folder: f"Output folder: {folder}\nResult file: {folder}/result.json",
                         scope({"a.py"}), PassResult)
    assert out.result is None and len(agent.requests) == 2

async def test_agent_error_propagates(tmp_path, runner_for):
    def boom(req):
        raise AgentError("crashed")
    with pytest.raises(AgentError):
        await run_pass(runner_for(FakeAgent(boom), tmp_path), ReviewPass.OVERALL,
                       lambda folder: f"Output folder: {folder}", scope({"a.py"}), PassResult)
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_passes.py -q`**

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `calls.py` and `passes.py`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

`git commit -m "Run review passes with retries"` (`git add` the new files first)

---

### Task 9: `CodeReviewer.review`

**Files:**
- Create: `packages/workflows/code-review/src/workflows/code_review/reviewer.py`
- Modify: `packages/workflows/code-review/src/workflows/code_review/__init__.py` (export `CodeReviewer`)
- Test: `packages/workflows/code-review/tests/test_reviewer.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  ```python
  class CodeReviewer:
      def __init__(self, default_agent: Agent, pass_agents: Mapping[ReviewPass, Agent] | None = None,
                   settings: ReviewSettings | None = None) -> None
      async def review(self, review_input: ReviewInput, on_event: Callable[[ReviewEvent], None] | None = None) -> ReviewResult
  ```

How `review` works:
1. **Check the input.** Raise `ReviewInputError` before any agent call when:
   - neither `codebase` nor `diffs` is given;
   - `settings.work_folder` exists and isn't empty;
   - a codebase path isn't a directory.

   Use `settings.work_folder`, created if missing and kept afterwards, or a `tempfile.TemporaryDirectory` that is removed afterwards.
2. **Change list:**
   - with `diffs`: `changes_from_diffs(diffs, work/input/diffs)`. The codebase, if given, is only read, and isn't checked with git;
   - otherwise: `check_codebase`, then `changes_from_git(codebase, work/input/diffs)`.

   Then `load_threads(discussions)`, written as a JSON list to `work/input/threads.json`, even when it's empty.
3. **Coverage of skipped files:** binary files are `skipped` with `binary file`.
4. **Nothing to review:** with no non-binary files, return `approved` with every pass `skipped`. The reason is `no changes` when the change list is empty, otherwise `only binary files`.
5. Run `make_batches` and `route_threads`.
6. **First wave, together:**
   - every batch: `run_per_file_batch` with scope `allowed_paths` = the union of the batch files' `paths`, and `thread_ids` = `routing.batches[k]`;
   - test coverage: `run_pass`, with no thread ids;
   - code patterns: `run_pass`, only with a codebase. Otherwise it's `skipped` with `no codebase`.

   Run them all with a helper that creates the tasks, awaits `asyncio.wait(..., return_when=FIRST_EXCEPTION)`, cancels the pending ones on an exception, and re-raises the first exception itself.
7. **Write checked results:**
   - each checked per-file result goes to `work/review-passes/per-file/<path>.json`;
   - each checked pass result goes to `work/review-passes/<pass>/result.json`;
   - unreviewed files are `unreviewed` with `no valid result after retry`, and all others are `reviewed`.
8. **Overall:** `run_pass`, with `thread_ids` = `routing.overall`, `unreviewed` listed, and `allowed_paths` `None`. A `None` result means `failed`.
9. **Consolidation:**
   - its scope: `allowed_paths` `None`, and `thread_ids` = all thread ids minus those any earlier pass replied to or acted on;
   - its prompt carries the coverage so far as text: each unreviewed file and each failed or skipped pass, one per line;
   - a `None` result raises `AgentError("consolidation produced no usable result")`.
10. **Assemble the result:**
    - `comments` = consolidation's comments, plus every reply (`reply_to` set) from the checked per-file and overall results;
    - `thread_actions` = the per-file and overall actions, plus consolidation's;
    - `status` = consolidation's, or `inconclusive` if any file is unreviewed or any pass failed;
    - every pass gets a `PassCoverage`, and `dropped_thread_actions` collects every pass's drops.

Each call's agent is `pass_agents.get(pass, default_agent)`. The working folder is the codebase path, or `work` when there's no codebase.

- [ ] **Step 1: Write the failing tests in `tests/test_reviewer.py`**

Use diffs-folder input (no git) and one scripted `FakeAgent` that answers every pass. The pass is worked out from `prompt_info.output_folder(prompt)`, whose path contains the pass folder name.

```python
import asyncio, json
from pathlib import Path
import pytest
from workflows.agent import AgentError
from workflows.agent.testing import FakeAgent
from workflows.code_review import CodeReviewer, ReviewInput, ReviewInputError, ReviewPass, ReviewSettings

DIFF = "diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-a\n+b\n"

@pytest.fixture
def diffs(tmp_path):
    d = tmp_path / "diffs"; d.mkdir()
    for p in ["a.py", "b.py"]:
        (d / f"{p}.diff").write_text(DIFF.format(p=p))
    (d / "img.png.diff").write_text("diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n")
    return d

def scripted(prompt_info, per_file=None, passes=None, status="approved", consolidation_comments=()):
    def respond(req):
        out = prompt_info.output_folder(req.prompt)
        pass_name = out.parts[out.parts.index("agent-output") + 1]
        if pass_name == "per-file":
            for n, path in prompt_info.slots(req.prompt):
                body = (per_file or (lambda p: {"path": p, "summary": "ok"}))(path)
                if body is not None:
                    (out / f"{n}.json").write_text(json.dumps(body))
        elif pass_name == "consolidation":
            (out / "result.json").write_text(json.dumps({"status": status, "comments": list(consolidation_comments)}))
        else:
            body = (passes or {}).get(pass_name, {"comments": []})
            if body is not None:
                (out / "result.json").write_text(json.dumps(body))
        return "done"
    return respond

async def test_happy_path(tmp_path, diffs, prompt_info):
    work = tmp_path / "work"
    agent = FakeAgent(scripted(prompt_info, consolidation_comments=[{"target": {"type": "change"}, "body": "LGTM"}]))
    r = await CodeReviewer(agent, settings=ReviewSettings(work_folder=work)).review(ReviewInput(diffs=diffs))
    assert r.status == "approved"
    assert [c.body for c in r.comments] == ["LGTM"]
    states = {f.path: (f.state, f.reason) for f in r.coverage.files}
    assert states == {"a.py": ("reviewed", None), "b.py": ("reviewed", None), "img.png": ("skipped", "binary file")}
    passes = {p.review_pass: (p.state, p.reason) for p in r.coverage.passes}
    assert passes[ReviewPass.CODE_PATTERNS] == ("skipped", "no codebase")
    assert (work / "review-passes" / "per-file" / "a.py.json").exists()
    assert (work / "review-passes" / "consolidation" / "result.json").exists()
    assert json.loads((work / "input" / "threads.json").read_text()) == []
    req = agent.requests[0]
    assert (req.timeout_seconds, req.extra_folders, req.working_folder) == (1800, [work], work)

async def test_unreviewed_file_forces_inconclusive(tmp_path, diffs, prompt_info):
    agent = FakeAgent(scripted(prompt_info, per_file=lambda p: None if p == "b.py" else {"path": p, "summary": "ok"}))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=diffs))
    assert r.status == "inconclusive"
    assert ("b.py", "unreviewed", "no valid result after retry") in [(f.path, f.state, f.reason) for f in r.coverage.files]

async def test_failed_pass_forces_inconclusive(tmp_path, diffs, prompt_info):
    agent = FakeAgent(scripted(prompt_info, passes={"test-coverage": None}))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=diffs))
    assert r.status == "inconclusive"
    assert {p.review_pass: p.state for p in r.coverage.passes}[ReviewPass.TEST_COVERAGE] == "failed"

async def test_replies_and_actions_from_routed_pass_survive(tmp_path, diffs, prompt_info):
    threads = tmp_path / "threads.json"
    threads.write_text(json.dumps([{"id": "t1", "target": {"type": "file", "path": "a.py"}, "state": "open",
                                    "messages": [{"author": "bot", "body": "Rename this"}]}]))
    def per_file(p):
        r = {"path": p, "summary": "ok"}
        if p == "a.py":
            r |= {"comments": [{"target": {"type": "file", "path": "a.py"}, "body": "Done, thanks", "reply_to": "t1"}],
                  "thread_actions": [{"thread_id": "t1", "action": "resolve"}]}
        return r
    agent = FakeAgent(scripted(prompt_info, per_file=per_file))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=diffs, discussions=[threads], reviewer_name="bot"))
    assert [(c.body, c.reply_to) for c in r.comments] == [("Done, thanks", "t1")]
    assert [(a.thread_id, a.action) for a in r.thread_actions] == [("t1", "resolve")]
    consolidation = [q for q in agent.requests if "/consolidation/" in prompt_info.output_folder(q.prompt).as_posix()][0]
    assert "Threads you may act on: none" in consolidation.prompt

async def test_no_changes(tmp_path, prompt_info):
    (tmp_path / "empty").mkdir()
    agent = FakeAgent(scripted(prompt_info))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=tmp_path / "empty"))
    assert r.status == "approved" and agent.requests == []
    assert {(p.state, p.reason) for p in r.coverage.passes} == {("skipped", "no changes")}

async def test_input_errors(tmp_path, diffs):
    agent = FakeAgent()
    with pytest.raises(ReviewInputError):
        await CodeReviewer(agent).review(ReviewInput())
    assert agent.requests == []

async def test_work_folder_must_be_empty(tmp_path, diffs):
    work = tmp_path / "work"; work.mkdir(); (work / "old.txt").write_text("x")
    with pytest.raises(ReviewInputError):
        await CodeReviewer(FakeAgent(), settings=ReviewSettings(work_folder=work)).review(ReviewInput(diffs=diffs))

async def test_agent_error_cancels_and_propagates(tmp_path, diffs, prompt_info):
    started = []
    async def respond(req):
        started.append(req)
        if "batch-1" in req.prompt:
            raise AgentError("batch crashed")
        await asyncio.sleep(30)
        return "late"
    settings = ReviewSettings(files_per_batch=1)
    with pytest.raises(AgentError, match="batch crashed"):
        await asyncio.wait_for(CodeReviewer(FakeAgent(respond), settings=settings).review(ReviewInput(diffs=diffs)), 5)

async def test_pass_agents_override(tmp_path, diffs, prompt_info):
    default = FakeAgent(scripted(prompt_info)); other = FakeAgent(scripted(prompt_info))
    await CodeReviewer(default, pass_agents={ReviewPass.CONSOLIDATION: other}).review(ReviewInput(diffs=diffs))
    assert len(other.requests) == 1
    assert all("/consolidation/" not in prompt_info.output_folder(q.prompt).as_posix() for q in default.requests)
```

- [ ] **Step 2: Run `uv run pytest packages/workflows/code-review/tests/test_reviewer.py -q`**

Expected: FAIL with `ImportError: cannot import name 'CodeReviewer'`.

- [ ] **Step 3: Implement `reviewer.py` and export `CodeReviewer`**

- [ ] **Step 4: Run all the checks, including the git tests**

Run: `uv run pytest -q && uv run pytest -m git -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

`git commit -m "Add CodeReviewer"` (`git add` the new files first)

---

### Task 10: Isolated install check and merge

**Files:** none new.

- [ ] **Step 1: Run `uv run python scripts/check_isolated_installs.py`**

Expected: `workflows-agent: ok`, `workflows-agent-claude-code: ok` and `workflows-code-review: ok`, exit code 0. This runs the package's tests on Python 3.11 against its wheel, with `workflows-agent` pinned to the local wheel.

- [ ] **Step 2: Check that the isolated check catches a dependency on a concrete agent**

Temporarily add `import workflows.agent_claude_code` at the top of `reviewer.py`. Run the script and expect `workflows-code-review: FAILED` with exit code 1. Then revert the change and don't commit it.

- [ ] **Step 3: Run everything once more, then merge**

Run: `uv run pytest -q && uv run pytest -m git -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

```bash
git checkout dev && git merge --no-ff code-review -m "Merge code review workflow" && git push origin dev
```
