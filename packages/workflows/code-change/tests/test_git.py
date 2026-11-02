import os
import subprocess
from pathlib import Path

import pytest

from workflows.code_change import ChangeInputError
from workflows.code_change.git import open_repository

pytestmark = pytest.mark.git


@pytest.fixture(scope="module", autouse=True)
def hermetic_git():
    """Keep the developer's global git config out of these tests."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        yield


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


def commit(repo: Path, name: str, message: str) -> str:
    (repo / name).write_text(f"{name}\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / ".gitignore").write_text("ignored.txt\n")
    (r / "a.py").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    return r


def test_open_records_base_and_branch(repo):
    r = open_repository(repo)
    assert r.base == git(repo, "rev-parse", "HEAD").strip()
    assert r.head() == r.base


@pytest.mark.parametrize(
    "make,message",
    [
        (lambda p: p / "missing", "codebase does not exist"),
        (lambda p: p, "codebase is not a git work tree"),
    ],
)
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
    if dirty == "modify":
        (repo / "a.py").write_text("changed\n")
    else:
        (repo / "new.py").write_text("x")
    if dirty == "staged":
        git(repo, "add", "new.py")
    with pytest.raises(ChangeInputError, match="not clean"):
        open_repository(repo)


def test_ignored_files_allowed(repo):
    (repo / "ignored.txt").write_text("x")
    open_repository(repo)


def test_commit_check_passes(repo):
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    assert r.commit_problem(r.base) is None


def test_no_new_commit(repo):
    r = open_repository(repo)
    assert r.commit_problem(r.base) == "no new commit"


def test_uncommitted_work(repo):
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    (repo / "a.py").write_text("changed\n")
    (repo / "new file.py").write_text("x")
    assert r.commit_problem(r.base) == "work left uncommitted: a.py, new file.py"


def test_uncommitted_list_is_capped(repo):
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    for i in range(12):
        (repo / f"f{i:02}.py").write_text("x")
    names = ", ".join(f"f{i:02}.py" for i in range(10))
    assert r.commit_problem(r.base) == f"work left uncommitted: {names} and 2 more"


def test_rewritten_commits(repo):
    r = open_repository(repo)
    first = commit(repo, "b.py", "fix")
    git(repo, "commit", "-q", "--amend", "-m", "amended")
    assert r.commit_problem(first) == "earlier commits were rewritten"


def test_switched_branch(repo):
    r = open_repository(repo)
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "b.py", "fix")
    assert r.commit_problem(r.base) == "switched from main to other"


def test_switched_to_detached_head(repo):
    r = open_repository(repo)
    git(repo, "checkout", "-q", "--detach")
    commit(repo, "b.py", "fix")
    assert r.commit_problem(r.base) == "switched from main to a detached HEAD"


def test_detached_start_then_branch(repo):
    git(repo, "checkout", "-q", "--detach")
    r = open_repository(repo)
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "b.py", "fix")
    assert (
        r.commit_problem(r.base)
        == "checked out other; the run started on a detached HEAD"
    )


def test_commits_and_diff(repo, tmp_path):
    r = open_repository(repo)
    commit(repo, "b.py", "first\n\nbody")
    commit(repo, "c.py", "second")
    assert [c.message for c in r.commits()] == ["first\n\nbody", "second"]
    r.write_diff(tmp_path / "change.diff")
    text = (tmp_path / "change.diff").read_text()
    assert "diff --git a/b.py b/b.py" in text and "diff --git a/c.py b/c.py" in text


def test_unchanged(repo):
    r = open_repository(repo)
    head = commit(repo, "b.py", "fix")
    assert r.unchanged(head)
    (repo / "x.py").write_text("x")
    assert not r.unchanged(head)


def test_reset_returns_to_starting_branch(repo):
    r = open_repository(repo)
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "b.py", "fix")
    (repo / "untracked.py").write_text("x")
    (repo / "ignored.txt").write_text("keep")
    r.reset()
    assert git(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert r.head() == r.base and r.commits() == []
    assert not (repo / "untracked.py").exists()
    assert (repo / "ignored.txt").read_text() == "keep"


def test_reset_on_the_starting_branch(repo):
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    r.reset()
    assert r.head() == r.base and not (repo / "b.py").exists()


def test_reset_from_detached_start(repo):
    git(repo, "checkout", "-q", "--detach")
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    r.reset()
    assert git(repo, "rev-parse", "HEAD").strip() == r.base
    assert (
        subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"], cwd=repo, check=False
        ).returncode
        == 1
    )


def test_inherited_git_dir_ignored(repo, tmp_path, monkeypatch):
    expected = git(repo, "rev-parse", "HEAD").strip()
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert open_repository(repo).base == expected


def install_failing_hook(repo: Path, name: str = "post-checkout") -> None:
    hook = repo / ".git" / "hooks" / name
    hook.write_text("#!/bin/sh\nexit 2\n")
    hook.chmod(0o755)


def ref(repo: Path) -> str:
    return git(repo, "symbolic-ref", "HEAD").strip()


def test_reset_keeps_an_ignored_file_the_agent_committed(repo):
    (repo / "ignored.txt").write_text("keep")
    r = open_repository(repo)
    git(repo, "add", "-f", "ignored.txt")
    git(repo, "commit", "-qm", "oops")
    (repo / "ignored.txt").write_text("edited later")
    r.reset()
    assert (repo / "ignored.txt").read_text() == "edited later"
    assert r.head() == r.base
    open_repository(repo)  # the work tree is clean again


def test_reset_keeps_an_ignored_file_when_the_agent_unignores_it(repo):
    (repo / "ignored.txt").write_text("keep")
    r = open_repository(repo)
    (repo / ".gitignore").write_text("")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "unignore")
    r.reset()
    assert (repo / "ignored.txt").read_text() == "keep"
    assert (repo / ".gitignore").read_text() == "ignored.txt\n"
    open_repository(repo)


@pytest.mark.parametrize("detached", [False, True])
def test_reset_is_not_stopped_by_a_failing_hook(repo, detached):
    if detached:
        git(repo, "checkout", "-q", "--detach")
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    git(repo, "checkout", "-q", "-b", "other")
    (repo / "untracked.py").write_text("x")
    install_failing_hook(repo)
    install_failing_hook(repo, "reference-transaction")
    r.reset()
    assert r.head() == r.base and not (repo / "untracked.py").exists()
    assert not (repo / "b.py").exists()


def test_a_tag_named_like_the_branch(repo):
    git(repo, "tag", "main")
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    assert r.commit_problem(r.base) is None
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "c.py", "more")
    r.reset()
    assert ref(repo) == "refs/heads/main"
    assert git(repo, "rev-parse", "refs/heads/main").strip() == r.base


def test_the_agent_creates_a_tag_named_like_the_branch(repo):
    r = open_repository(repo)
    git(repo, "tag", "main")
    commit(repo, "b.py", "fix")
    assert r.commit_problem(r.base) is None


def test_reset_removes_a_nested_repository(repo):
    r = open_repository(repo)
    (repo / "vendor" / "lib").mkdir(parents=True)
    git(repo / "vendor" / "lib", "init", "-q")
    r.reset()
    assert not (repo / "vendor").exists()
    open_repository(repo)


def test_reset_after_the_branch_was_renamed(repo):
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    git(repo, "branch", "-m", "main", "fix")
    r.reset()
    assert ref(repo) == "refs/heads/main"
    assert git(repo, "rev-parse", "main").strip() == r.base
    assert not (repo / "b.py").exists()


def test_reset_after_a_commit_on_the_starting_branch_then_a_switch(repo):
    r = open_repository(repo)
    commit(repo, "b.py", "fix")
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "c.py", "more")
    r.reset()
    assert ref(repo) == "refs/heads/main"
    assert git(repo, "rev-parse", "main").strip() == r.base
    assert not (repo / "b.py").exists() and not (repo / "c.py").exists()


def test_unchanged_requires_the_starting_branch(repo):
    r = open_repository(repo)
    head = commit(repo, "b.py", "fix")
    assert r.unchanged(head)
    git(repo, "checkout", "-q", "-b", "scratch")
    assert not r.unchanged(head)
    git(repo, "checkout", "-q", "--detach")
    assert not r.unchanged(head)


def test_dirty_summary(repo):
    r = open_repository(repo)
    assert r.dirty_summary() is None
    (repo / "report.xml").write_text("x")
    (repo / "ignored.txt").write_text("x")
    assert r.dirty_summary() == "report.xml"
    for i in range(11):
        (repo / f"f{i:02}.py").write_text("x")
    summary = r.dirty_summary()
    assert summary is not None and summary.endswith(" and 2 more")
