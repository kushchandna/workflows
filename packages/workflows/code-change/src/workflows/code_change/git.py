"""The only module that runs ``git``."""

import os
import subprocess
from pathlib import Path

from workflows.code_change.errors import ChangeInputError
from workflows.code_change.models import Commit
from workflows.code_change.repo import Dirty

_HEADS = "refs/heads/"
# The reset must not depend on, or be stopped by, hooks the repository or the agent installed.
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")
# Written diffs must show the files as they are, whatever the user's git config says.
_DIFF_FLAGS = [
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
]
# An inherited value, inside a git hook for example, would point git at another repository.
_DROPPED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "--literal-pathspecs", "-c", "core.quotepath=off", *args]
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, check=False, env=env
        )
    except FileNotFoundError as e:
        raise RuntimeError("git executable not found") from e


def _run(cwd: Path, *args: str, ok: tuple[int, ...] = (0,)) -> bytes:
    proc = _git(cwd, *args)
    if proc.returncode not in ok:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return proc.stdout


def _text(cwd: Path, *args: str) -> str:
    return _run(cwd, *args).decode("utf-8", errors="replace").strip()


def _ref(cwd: Path) -> str | None:
    """The full ref HEAD points to (``refs/heads/main``), or ``None`` when HEAD is detached.

    The short form can be ambiguous: with a tag ``main`` as well, ``--short`` gives ``heads/main``.
    """
    proc = _git(cwd, "symbolic-ref", "-q", "HEAD")
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def _short(ref: str) -> str:
    return ref.removeprefix(_HEADS)


def _dirty(cwd: Path) -> Dirty:
    """What is uncommitted or untracked. Git-ignored files don't count."""
    tokens = (
        _run(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        .decode("utf-8", errors="replace")
        .split("\0")[:-1]
    )
    paths: list[str] = []
    untracked: list[str] = []
    i = 0
    while i < len(tokens):
        status, path = tokens[i][:2], tokens[i][3:]
        paths.append(path)
        # A staged or intent-to-add file ("A") is as new to the repository as an untracked one.
        if status == "??" or "A" in status:
            untracked.append(path)
        i += 2 if "R" in status or "C" in status else 1  # skip the original path
    return Dirty(paths=paths, untracked=untracked)


def open_repository(path: Path) -> "GitRepository":
    """The repository at ``path``, or ``ChangeInputError`` if it can't be changed safely."""
    if not path.is_dir():
        raise ChangeInputError(f"codebase does not exist: {path}")
    inside = _git(path, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        raise ChangeInputError(f"codebase is not a git work tree: {path}")
    top = Path(_text(path, "rev-parse", "--show-toplevel"))
    if top.resolve() != path.resolve():
        raise ChangeInputError(f"codebase must be the top of its git work tree: {path}")
    if _git(path, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
        raise ChangeInputError(f"codebase has no commits: {path}")
    if _dirty(path).paths:
        raise ChangeInputError(f"work tree is not clean: {path}")
    return GitRepository(path, _text(path, "rev-parse", "HEAD"), _ref(path))


class GitRepository:
    def __init__(self, path: Path, base: str, ref: str | None) -> None:
        self._path = path
        self._base = base
        self._ref = ref

    @property
    def base(self) -> str:
        return self._base

    @property
    def starting_branch(self) -> str | None:
        return _short(self._ref) if self._ref is not None else None

    def head(self) -> str:
        return _text(self._path, "rev-parse", "HEAD")

    def commit_problem(self, last_checked: str) -> str | None:
        current = _ref(self._path)
        if current != self._ref:
            if self._ref is not None:
                where = _short(current) if current is not None else "a detached HEAD"
                return f"switched from {_short(self._ref)} to {where}"
            assert current is not None
            return f"checked out {_short(current)}; the run started on a detached HEAD"
        ancestor = _git(self._path, "merge-base", "--is-ancestor", last_checked, "HEAD")
        if ancestor.returncode == 1:
            return "earlier commits were rewritten"
        if ancestor.returncode != 0:
            stderr = ancestor.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git merge-base --is-ancestor failed: {stderr}")
        if self.head() == last_checked:
            return "no new commit"
        dirty = _dirty(self._path).summary()
        if dirty is not None:
            return f"work left uncommitted: {dirty}"
        return None

    def dirty_paths(self) -> Dirty:
        return _dirty(self._path)

    def unchanged(self, head: str) -> bool:
        return (
            self.head() == head
            and _ref(self._path) == self._ref
            and not _dirty(self._path).paths
        )

    def write_diff(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(
            _run(self._path, "diff", *_DIFF_FLAGS, "-M", self._base, "HEAD")
        )

    def commits(self) -> list[Commit]:
        ids = _text(self._path, "rev-list", "--reverse", f"{self._base}..HEAD").split()
        return [
            Commit(
                id=i,
                message=_run(self._path, "log", "-1", "--format=%B", i)
                .decode("utf-8", errors="replace")
                .rstrip("\n"),
            )
            for i in ids
        ]

    def reset(self) -> None:
        # The index goes first. Otherwise a file the agent committed is deleted with its commit,
        # even a git-ignored one that existed before the run. With the index at the base, such a
        # file is merely untracked, and the ignore rules restored below keep it.
        _run(self._path, "read-tree", self._base)
        if self._ref is None:
            _run(self._path, *_NO_HOOKS, "checkout", "-q", "-f", "--detach", self._base)
        else:
            # -B also recreates the branch if the agent renamed or deleted it.
            _run(
                self._path,
                *_NO_HOOKS,
                "checkout",
                "-q",
                "-f",
                "-B",
                _short(self._ref),
                self._base,
                "--",
            )
        _run(self._path, *_NO_HOOKS, "reset", "-q", "--hard", self._base)
        # -ff also removes untracked nested repositories. Ignored files stay (no -x).
        _run(self._path, "clean", "-q", "-ffd")
