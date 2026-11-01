import pytest

from workflows.code_review import ReviewInputError
from workflows.code_review.changes import changes_from_diffs, parse_diff

MODIFIED = (
    "diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\n"
    "@@ -1,2 +1,2 @@\n-x\n+y\n--- removed line starting with dashes\n context\n"
)
ADDED = (
    "diff --git a/n.py b/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/n.py\n"
    "@@ -0,0 +1,2 @@\n+a\n+b\n"
)
DELETED = (
    "diff --git a/d.py b/d.py\ndeleted file mode 100644\n--- a/d.py\n+++ /dev/null\n"
    "@@ -1 +0,0 @@\n-a\n"
)
RENAMED = (
    "diff --git a/old.py b/new.py\nsimilarity index 100%\n"
    "rename from old.py\nrename to new.py\n"
)
BINARY = (
    "diff --git a/i.png b/i.png\nindex 1..2 100644\n"
    "Binary files a/i.png and b/i.png differ\n"
)


def test_modified_counts_only_hunk_lines():
    f = parse_diff("a.py", MODIFIED)
    assert (f.status, f.changed_lines, f.binary, f.old_path) == (
        "modified",
        3,
        False,
        None,
    )


def test_added_deleted_renamed_binary():
    assert parse_diff("n.py", ADDED).status == "added"
    d = parse_diff("d.py", DELETED)
    assert d.status == "deleted" and d.batch_lines == 0
    r = parse_diff("new.py", RENAMED)
    assert (r.status, r.old_path, r.changed_lines, r.batch_lines) == (
        "renamed",
        "old.py",
        0,
        0,
    )
    assert r.paths == {"new.py", "old.py"}
    assert parse_diff("i.png", BINARY).binary


def test_git_binary_patch_is_binary():
    text = "diff --git a/b b/b\nindex 1..2 100644\nGIT binary patch\nliteral 3\nabc\n"
    assert parse_diff("b", text).binary


def test_renamed_with_edits_counts():
    text = RENAMED + "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-a\n+b\n"
    f = parse_diff("new.py", text)
    assert f.changed_lines == 2 and f.batch_lines == 2


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


def test_quoted_rename_paths_are_decoded():
    # git quotes non-ASCII and special characters in diff headers (core.quotepath=true)
    text = 'diff --git "a/\\303\\274.py" b/new.py\nrename from "\\303\\274.py"\nrename to new.py\n'
    assert parse_diff("new.py", text).old_path == "ü.py"
    text = (
        'diff --git "a/q\\"t.py" b/new.py\nrename from "q\\"t.py"\nrename to new.py\n'
    )
    assert parse_diff("new.py", text).old_path == 'q"t.py'
    text = 'diff --git "a/tab\\there.py" b/new.py\nrename from "tab\\there.py"\nrename to new.py\n'
    assert parse_diff("new.py", text).old_path == "tab\there.py"
