import shutil
from dataclasses import dataclass
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
    def counts_toward_limits(self) -> bool:
        """Deletions and pure renames are free; they count toward no batch limit."""
        if self.status == "deleted":
            return False
        return not (self.status == "renamed" and self.changed_lines == 0)

    @property
    def paths(self) -> frozenset[str]:
        return frozenset({self.path} | ({self.old_path} if self.old_path else set()))


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
            old_path = line.removeprefix("rename from ")
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
