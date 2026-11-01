import shutil
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path
from typing import Literal

from workflows.code_review.errors import ReviewInputError


@dataclass(frozen=True)
class ChangedFile:
    path: str  # new path for renames, old path for deletions
    status: Literal["added", "modified", "deleted", "renamed"]
    old_path: str | None  # set only for renames
    binary: bool
    changed_lines: int  # added plus removed

    @property
    def batch_lines(self) -> int:
        """Lines this file adds to a batch's line budget. A deleted file adds none."""
        return 0 if self.status == "deleted" else self.changed_lines

    @property
    def paths(self) -> frozenset[str]:
        return frozenset({self.path} | ({self.old_path} if self.old_path else set()))


_ESCAPES = {
    "a": 7,
    "b": 8,
    "f": 12,
    "n": 10,
    "r": 13,
    "t": 9,
    "v": 11,
    '"': 34,
    "\\": 92,
}
_OCTAL = "01234567"


def _unquote(path: str) -> str:
    """Undo git's C-style quoting of a path in a diff header (core.quotepath)."""
    if len(path) < 2 or not (path.startswith('"') and path.endswith('"')):
        return path
    body = path[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        char = body[i]
        if char != "\\" or i + 1 == len(body):
            out += char.encode("utf-8")
            i += 1
        elif body[i + 1] in _ESCAPES:
            out.append(_ESCAPES[body[i + 1]])
            i += 2
        elif body[i + 1] in _OCTAL:
            digits = "".join(takewhile(_OCTAL.__contains__, body[i + 1 : i + 4]))
            out.append(int(digits, 8) & 0xFF)
            i += 1 + len(digits)
        else:
            out += body[i + 1].encode("utf-8")
            i += 2
    return out.decode("utf-8", errors="replace")


def diff_file(diffs_dir: Path, path: str) -> Path:
    return diffs_dir / f"{path}.diff"


def parse_diff(path: str, text: str) -> ChangedFile:
    """Read a file's status, binary flag and changed line count from its git diff."""
    status: Literal["added", "modified", "deleted", "renamed"] = "modified"
    old_path: str | None = None
    binary = False
    changed_lines = 0
    in_hunks = False
    for line in text.splitlines():
        if in_hunks or line.startswith("@@"):
            in_hunks = True
            if line.startswith(("+", "-")):
                changed_lines += 1
        elif line.startswith("new file mode"):
            status = "added"
        elif line.startswith("deleted file mode"):
            status = "deleted"
        elif line.startswith("rename from "):
            status = "renamed"
            old_path = _unquote(line.removeprefix("rename from "))
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            binary = True
    return ChangedFile(path, status, old_path, binary, changed_lines)


def changes_from_diffs(source: Path, diffs_dir: Path) -> list[ChangedFile]:
    """Read a diffs folder (one ``<path>.diff`` per file) and copy it to ``diffs_dir``."""
    if not source.is_dir():
        raise ReviewInputError(f"diffs folder does not exist: {source}")
    files = []
    for diff in source.rglob("*.diff"):
        path = diff.relative_to(source).as_posix().removesuffix(".diff")
        text = diff.read_text(encoding="utf-8", errors="replace")
        target = diff_file(diffs_dir, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(diff, target)
        files.append(parse_diff(path, text))
    return sorted(files, key=lambda f: f.path)
