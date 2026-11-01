from typing import Literal

import pytest

from workflows.code_review import (
    ChangeTarget,
    Comment,
    FileTarget,
    LinesTarget,
    ThreadAction,
)
from workflows.code_review.formats import PerFileResult
from workflows.code_review.validation import (
    Scope,
    check_items,
    claimed_location,
    read_result,
    usable_per_file,
)


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("1\n2\n3")  # 3 lines, no trailing newline
    (root / "nl.py").write_text("1\n2\n3\n")  # 3 lines, trailing newline
    (root / "empty.py").write_text("")
    (root / "other.py").write_text("x\n")
    (tmp_path / "secret.txt").write_text("outside the codebase\n")
    return root


def scope(codebase, allowed=None, threads=("t1",)):
    return Scope(
        changed_paths=frozenset({"a.py", "nl.py", "empty.py"}),
        allowed_paths=allowed,
        codebase=codebase,
        thread_ids=frozenset(threads),
    )


def lines(s: int, e: int, path: str = "a.py", side: Literal["new", "old"] = "new"):
    return Comment(
        target=LinesTarget(path=path, start_line=s, end_line=e, side=side), body="b"
    )


def test_line_range_bounds(codebase):
    c = check_items([lines(1, 3)], [], scope(codebase))
    assert not c.had_invalid and c.comments[0].target.type == "lines"
    c = check_items([lines(2, 4)], [], scope(codebase))
    assert c.had_invalid and c.comments[0] == Comment(
        target=ChangeTarget(), body="a.py:2-4: b"
    )
    assert check_items([lines(0, 1)], [], scope(codebase)).had_invalid
    assert check_items([lines(3, 2)], [], scope(codebase)).had_invalid
    assert not check_items([lines(1, 99, side="old")], [], scope(codebase)).had_invalid


def test_last_line_with_and_without_trailing_newline(codebase):
    assert not check_items([lines(3, 3, "nl.py")], [], scope(codebase)).had_invalid
    assert check_items([lines(4, 4, "nl.py")], [], scope(codebase)).had_invalid
    assert check_items([lines(1, 1, "empty.py")], [], scope(codebase)).had_invalid


def test_unchanged_existing_file_allowed_unless_restricted(codebase):
    c = Comment(target=FileTarget(path="other.py"), body="b")
    assert not check_items([c], [], scope(codebase)).had_invalid
    assert check_items(
        [c], [], scope(codebase, allowed=frozenset({"a.py"}))
    ).had_invalid
    missing = Comment(target=FileTarget(path="missing.py"), body="b")
    assert check_items([missing], [], scope(codebase)).had_invalid


@pytest.mark.parametrize(
    "path", ["../secret.txt", "/etc/passwd", "sub/../../secret.txt"]
)
def test_paths_cannot_leave_the_codebase(codebase, path):
    c = Comment(target=FileTarget(path=path), body="b")
    assert check_items([c], [], scope(codebase)).had_invalid


def test_without_codebase_only_changed_paths():
    s = Scope(
        changed_paths=frozenset({"a.py"}),
        allowed_paths=None,
        codebase=None,
        thread_ids=frozenset(),
    )
    assert not check_items([lines(1, 500)], [], s).had_invalid
    assert check_items(
        [Comment(target=FileTarget(path="b.py"), body="x")], [], s
    ).had_invalid


def test_bad_reply_to_is_removed_but_target_kept(codebase):
    c = check_items(
        [Comment(target=FileTarget(path="a.py"), body="b", reply_to="t9")],
        [],
        scope(codebase),
    )
    assert c.had_invalid and c.comments == [
        Comment(target=FileTarget(path="a.py"), body="b")
    ]


def test_bad_target_keeps_a_valid_reply_to(codebase):
    reply = lines(9, 9)
    reply = reply.model_copy(update={"reply_to": "t1"})
    c = check_items([reply], [], scope(codebase))
    assert c.comments == [
        Comment(target=ChangeTarget(), body="a.py:9: b", reply_to="t1")
    ]


def test_actions_outside_scope_dropped(codebase):
    c = check_items(
        [],
        [
            ThreadAction(thread_id="t1", action="resolve"),
            ThreadAction(thread_id="t2", action="reopen"),
        ],
        scope(codebase),
    )
    assert c.had_invalid
    assert c.thread_actions == [ThreadAction(thread_id="t1", action="resolve")]
    assert c.dropped == [
        (
            ThreadAction(thread_id="t2", action="reopen"),
            "thread not available to this pass",
        )
    ]


def test_claimed_location():
    assert (
        claimed_location(LinesTarget(path="a.py", start_line=1, end_line=1)) == "a.py:1"
    )
    old = LinesTarget(path="a.py", start_line=2, end_line=5, side="old")
    assert claimed_location(old) == "a.py:2-5 (old)"
    assert claimed_location(FileTarget(path="a.py")) == "a.py"
    assert claimed_location(ChangeTarget()) == "change"


def test_read_result_and_usable(tmp_path):
    assert read_result(tmp_path / "missing.json", PerFileResult) is None
    (tmp_path / "bad.json").write_text("```json\n{}\n```")
    assert read_result(tmp_path / "bad.json", PerFileResult) is None
    (tmp_path / "ok.json").write_text('{"path": "a.py", "summary": "s"}')
    r = read_result(tmp_path / "ok.json", PerFileResult)
    assert usable_per_file(r, "a.py") and not usable_per_file(r, "b.py")
    assert not usable_per_file(PerFileResult(path="a.py", summary="  "), "a.py")
    assert not usable_per_file(None, "a.py")
