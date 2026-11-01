"""The only module that runs ``git``: builds the change list from a codebase."""

import subprocess
from pathlib import Path
from typing import Literal

from workflows.code_review.changes import ChangedFile, diff_file, parse_diff
from workflows.code_review.errors import ReviewInputError
from workflows.code_review.models import Codebase

_Status = Literal["added", "modified", "deleted", "renamed"]
_STATUS: dict[str, _Status] = {"A": "added", "D": "deleted", "R": "renamed"}


def _run(cwd: Path, *args: str, ok: tuple[int, ...] = (0,)) -> bytes:
    command = ["git", "--literal-pathspecs", "-c", "core.quotepath=off", *args]
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, check=False)
    except FileNotFoundError as e:
        raise RuntimeError("git executable not found") from e
    if proc.returncode not in ok:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return proc.stdout


def _tokens(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").split("\0")[:-1]


def _resolve_base(codebase: Codebase) -> str:
    """The full commit id of the base, or ``ReviewInputError``."""
    path = codebase.path
    if not path.is_dir():
        raise ReviewInputError(f"codebase does not exist: {path}")
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        raise ReviewInputError(f"codebase is not a git work tree: {path}")
    base = codebase.base_commit
    if base.startswith("-"):
        raise ReviewInputError(f"invalid base commit: {base}")
    found = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        cwd=path,
        capture_output=True,
        check=False,
    )
    if found.returncode != 0:
        raise ReviewInputError(f"base commit not found in {path}: {base}")
    return found.stdout.decode().strip()


def check_codebase(codebase: Codebase) -> None:
    _resolve_base(codebase)


def _numstat(cwd: Path, base: str) -> dict[str, tuple[int, bool]]:
    """New path -> (changed lines, binary)."""
    tokens = _tokens(_run(cwd, "diff", "-M", "-z", "--relative", "--numstat", base))
    counts: dict[str, tuple[int, bool]] = {}
    i = 0
    while i < len(tokens):
        added, removed, path = tokens[i].split("\t", 2)
        if path == "":  # a rename: the old and new paths are the next two tokens
            path = tokens[i + 2]
            i += 3
        else:
            i += 1
        binary = added == "-"
        counts[path] = (0 if binary else int(added) + int(removed), binary)
    return counts


def changes_from_git(codebase: Codebase, diffs_dir: Path) -> list[ChangedFile]:
    """Everything changed since the base commit, including uncommitted and untracked files.

    Each file's diff is written to ``diff_file(diffs_dir, path)``. Paths are relative to
    ``codebase.path``. Git-ignored files are left out.
    """
    cwd = codebase.path
    base = _resolve_base(codebase)
    counts = _numstat(cwd, base)
    files: list[ChangedFile] = []

    tokens = _tokens(_run(cwd, "diff", "-M", "-z", "--relative", "--name-status", base))
    i = 0
    while i < len(tokens):
        code = tokens[i]
        old_path: str | None = None
        if code.startswith("R"):
            old_path, path = tokens[i + 1], tokens[i + 2]
            i += 3
        else:
            path = tokens[i + 1]
            i += 2
        changed_lines, binary = counts.get(path, (0, False))
        files.append(
            ChangedFile(
                path, _STATUS.get(code[0], "modified"), old_path, binary, changed_lines
            )
        )
        pathspec = [old_path, path] if old_path else [path]
        diff = _run(
            cwd,
            "diff",
            "-M",
            "--relative",
            "--no-color",
            "--no-ext-diff",
            base,
            "--",
            *pathspec,
        )
        _write(diffs_dir, path, diff)

    for path in _tokens(_run(cwd, "ls-files", "--others", "--exclude-standard", "-z")):
        if path.endswith("/"):  # an embedded git repository, not a file
            continue
        diff = _run(
            cwd,
            "diff",
            "--no-index",
            "--no-color",
            "--no-ext-diff",
            "--",
            "/dev/null",
            path,
            ok=(0, 1),
        )
        if not diff:  # git cannot diff it, for example a symlink to a directory
            continue
        _write(diffs_dir, path, diff)
        files.append(parse_diff(path, diff.decode("utf-8", errors="replace")))

    return sorted(files, key=lambda f: f.path)


def _write(diffs_dir: Path, path: str, diff: bytes) -> None:
    target = diff_file(diffs_dir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(diff)
