from workflows.code_review.changes import ChangedFile
from workflows.code_review.models import ReviewSettings


def _top_dir(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else ""


def make_batches(
    files: list[ChangedFile], settings: ReviewSettings
) -> list[list[ChangedFile]]:
    """Group non-binary files into batches, one harness call each.

    Files are sorted by path. Every file counts as one toward the file limit, deletions and
    renames included. Its changed lines count toward the line limit, except for a deleted
    file's. A file that would breach either limit starts the next batch, and a file larger
    than the line limit ends up alone. Moving to a new top-level directory also starts a
    batch once the current one is filled to ``directory_break_min_fill``.
    """
    batches: list[list[ChangedFile]] = []
    current: list[ChangedFile] = []
    lines = count = 0
    for file in sorted(files, key=lambda f: f.path):
        add_lines, add_files = file.batch_lines, 1
        if current:
            breach = (
                lines + add_lines > settings.lines_per_batch
                or count + add_files > settings.files_per_batch
            )
            fill = max(
                lines / settings.lines_per_batch, count / settings.files_per_batch
            )
            new_dir = (
                _top_dir(file.path) != _top_dir(current[-1].path)
                and fill >= settings.directory_break_min_fill
            )
            if breach or new_dir:
                batches.append(current)
                current, lines, count = [], 0, 0
        current.append(file)
        lines += add_lines
        count += add_files
    if current:
        batches.append(current)
    return batches
