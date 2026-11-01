import subprocess
from pathlib import Path

import pytest

from workflows.code_review import Codebase, ReviewInputError
from workflows.code_review.changes import ChangedFile
from workflows.code_review.gitchanges import changes_from_git, check_codebase

pytestmark = pytest.mark.git


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> tuple[Path, str]:
    r = tmp_path_factory.mktemp("repo")
    git(r, "init", "-q")
    (r / ".gitignore").write_text("ignored.txt\n")
    for name in [
        "keep.py",
        "modify.py",
        "stage.py",
        "old_name.py",
        "remove.py",
        "glob[1]*.py",
    ]:
        (r / name).write_text(f"{name}\nline2\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    base = git(r, "rev-parse", "HEAD").strip()
    (r / "committed.py").write_text("c\n")
    git(r, "add", "committed.py")
    git(r, "commit", "-qm", "after base")
    (r / "stage.py").write_text("staged\n")
    git(r, "add", "stage.py")
    (r / "modify.py").write_text("modify.py\nchanged\nadded\n")
    (r / "glob[1]*.py").write_text("glob[1]*.py\nline2\nmore\n")
    git(r, "mv", "old_name.py", "new_name.py")
    git(r, "rm", "-q", "remove.py")
    (r / "untracked.py").write_text("u1\nu2\n")
    (r / "ignored.txt").write_text("secret\n")
    (r / "image.bin").write_bytes(b"\x00\x01\x02")
    (r / "dir with space").mkdir()
    (r / "dir with space" / "naïve.py").write_text("x\n")
    return r, base


def by_path(repo, tmp_path, path=None) -> dict[str, ChangedFile]:
    r, base = repo
    codebase = Codebase(path=path or r, base_commit=base)
    return {f.path: f for f in changes_from_git(codebase, tmp_path / "diffs")}


def test_lists_every_kind_of_change(repo, tmp_path):
    files = by_path(repo, tmp_path)
    assert set(files) == {
        "committed.py",
        "stage.py",
        "modify.py",
        "glob[1]*.py",
        "new_name.py",
        "remove.py",
        "untracked.py",
        "image.bin",
        "dir with space/naïve.py",
    }
    assert files["new_name.py"].status == "renamed"
    assert files["new_name.py"].old_path == "old_name.py"
    assert files["remove.py"].status == "deleted"
    assert (
        files["untracked.py"].status == "added"
        and files["untracked.py"].changed_lines == 2
    )
    assert files["image.bin"].binary
    assert files["modify.py"].changed_lines == 3


def test_writes_diff_files(repo, tmp_path):
    by_path(repo, tmp_path)
    assert "+changed" in (tmp_path / "diffs" / "modify.py.diff").read_text()
    assert (
        "rename from old_name.py"
        in (tmp_path / "diffs" / "new_name.py.diff").read_text()
    )
    assert "+u1" in (tmp_path / "diffs" / "untracked.py.diff").read_text()


def test_paths_with_spaces_and_unicode(repo, tmp_path):
    files = by_path(repo, tmp_path)
    assert files["glob[1]*.py"].changed_lines == 1
    assert "+more" in (tmp_path / "diffs" / "glob[1]*.py.diff").read_text()
    assert files["dir with space/naïve.py"].status == "added"
    assert (tmp_path / "diffs" / "dir with space" / "naïve.py.diff").exists()


def test_codebase_in_subdirectory_reports_paths_relative_to_it(repo, tmp_path):
    r, _ = repo
    files = by_path(repo, tmp_path, path=r / "dir with space")
    assert set(files) == {"naïve.py"}


def test_check_codebase_errors(repo, tmp_path):
    r, _ = repo
    with pytest.raises(ReviewInputError):
        check_codebase(Codebase(path=tmp_path, base_commit="HEAD"))
    with pytest.raises(ReviewInputError):
        check_codebase(Codebase(path=tmp_path / "missing", base_commit="HEAD"))
    with pytest.raises(ReviewInputError):
        check_codebase(Codebase(path=r, base_commit="0" * 40))
    with pytest.raises(ReviewInputError):
        check_codebase(Codebase(path=r, base_commit="--output=x"))


def test_untracked_entries_git_cannot_diff_are_skipped(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "commit", "-q", "--allow-empty", "-m", "base")
    base = git(r, "rev-parse", "HEAD").strip()
    nested = r / "nested"  # a stray clone inside the workspace
    nested.mkdir()
    git(nested, "init", "-q")
    (nested / "f").write_text("x\n")
    git(nested, "add", "f")
    git(nested, "commit", "-qm", "n")
    (r / "empty.txt").write_text("")
    (r / "real.py").write_text("a\n")
    files = {
        f.path: f
        for f in changes_from_git(
            Codebase(path=r, base_commit=base), tmp_path / "diffs"
        )
    }
    assert set(files) == {"empty.txt", "real.py"}
    assert {f.status for f in files.values()} == {"added"}


def test_diffs_ignore_the_users_diff_config(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "diff.up.textconv", "tr a-z A-Z <")  # shifts what a diff shows
    git(r, "config", "diff.noprefix", "true")
    (r / ".gitattributes").write_text("*.up diff=up\n")
    (r / "f.up").write_text("hello\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    base = git(r, "rev-parse", "HEAD").strip()
    (r / "f.up").write_text("hello\nworld\n")
    (r / "new.up").write_text("fresh\n")
    changes_from_git(Codebase(path=r, base_commit=base), tmp_path / "diffs")
    tracked = (tmp_path / "diffs" / "f.up.diff").read_text()
    assert "+world" in tracked and "WORLD" not in tracked
    assert "--- a/f.up" in tracked and "+++ b/f.up" in tracked
    untracked = (tmp_path / "diffs" / "new.up.diff").read_text()
    assert "+fresh" in untracked and "FRESH" not in untracked
    assert "+++ b/new.up" in untracked
