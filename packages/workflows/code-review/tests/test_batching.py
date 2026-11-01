from typing import Literal

from workflows.code_review import ReviewSettings
from workflows.code_review.batching import make_batches
from workflows.code_review.changes import ChangedFile


def f(
    path: str,
    lines: int = 10,
    status: Literal["modified", "renamed", "deleted"] = "modified",
):
    return ChangedFile(
        path=path,
        status=status,
        old_path="x" if status == "renamed" else None,
        binary=False,
        changed_lines=lines,
    )


def paths(batches):
    return [[x.path for x in b] for b in batches]


def test_line_limit_is_hard():
    s = ReviewSettings(
        lines_per_batch=100, files_per_batch=10, directory_break_min_fill=1
    )
    assert paths(make_batches([f("a", 60), f("b", 40), f("c", 1)], s)) == [
        ["a", "b"],
        ["c"],
    ]


def test_file_limit_is_hard():
    s = ReviewSettings(
        lines_per_batch=1000, files_per_batch=2, directory_break_min_fill=1
    )
    assert paths(make_batches([f("a"), f("b"), f("c")], s)) == [["a", "b"], ["c"]]


def test_renames_and_deletions_are_free():
    s = ReviewSettings(
        lines_per_batch=100, files_per_batch=1, directory_break_min_fill=1
    )
    files = [f("a", 100), f("b", 0, "renamed"), f("c", 50, "deleted")]
    assert paths(make_batches(files, s)) == [["a", "b", "c"]]


def test_renamed_with_edits_counts():
    s = ReviewSettings(
        lines_per_batch=100, files_per_batch=10, directory_break_min_fill=1
    )
    assert paths(make_batches([f("a", 90), f("b", 20, "renamed")], s)) == [["a"], ["b"]]


def test_oversized_file_gets_own_batch():
    s = ReviewSettings(
        lines_per_batch=100, files_per_batch=10, directory_break_min_fill=1
    )
    assert paths(make_batches([f("a", 10), f("b", 500), f("c", 10)], s)) == [
        ["a"],
        ["b"],
        ["c"],
    ]


def test_directory_break_only_when_filled_enough():
    s = ReviewSettings(
        lines_per_batch=100, files_per_batch=10, directory_break_min_fill=0.5
    )
    assert paths(make_batches([f("x/a", 40), f("y/b", 10)], s)) == [["x/a", "y/b"]]
    assert paths(make_batches([f("x/a", 50), f("y/b", 10)], s)) == [["x/a"], ["y/b"]]


def test_directory_break_counts_files_too():
    s = ReviewSettings(
        lines_per_batch=1000, files_per_batch=4, directory_break_min_fill=0.5
    )
    files = [f("x/a", 1), f("x/b", 1), f("y/c", 1)]
    assert paths(make_batches(files, s)) == [["x/a", "x/b"], ["y/c"]]


def test_sorted_by_path_and_empty():
    assert paths(make_batches([f("b"), f("a")], ReviewSettings())) == [["a", "b"]]
    assert make_batches([], ReviewSettings()) == []
