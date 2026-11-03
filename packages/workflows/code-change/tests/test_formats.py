import pytest

from workflows.code_change.formats import (
    ChangeDone,
    ChangeNeedsHuman,
    read_change_report,
    read_review_report,
)


def write(tmp_path, text):
    p = tmp_path / "result.json"
    p.write_text(text, encoding="utf-8")
    return p


def test_reads_done_and_needs_human(tmp_path):
    done = write(
        tmp_path, '{"outcome": "done", "title": "t", "description": "d", "x": 1}'
    )
    assert read_change_report(done) == ChangeDone(
        outcome="done", title="t", description="d"
    )
    human = write(tmp_path, '{"outcome": "needs_human", "reason": "r"}')
    assert isinstance(read_change_report(human), ChangeNeedsHuman)


@pytest.mark.parametrize(
    "text",
    [
        '{"outcome": "done", "title": " ", "description": "d"}',
        '{"outcome": "needs_human", "reason": ""}',
        '{"outcome": "maybe"}',
        "not json",
        "[" * 100000,
    ],
)
def test_bad_change_reports(tmp_path, text):
    assert read_change_report(write(tmp_path, text)) is None


def test_missing_and_non_utf8(tmp_path):
    assert read_change_report(tmp_path / "nope.json") is None
    p = tmp_path / "b.json"
    p.write_bytes(b'{"outcome": "done", "title": "\xff", "description": "d"}')
    assert read_change_report(p) is None


@pytest.mark.parametrize(
    "text,ok",
    [
        ('{"verdict": "pass"}', True),
        ('{"verdict": "changes_needed", "comments": ["c"]}', True),
        ('{"verdict": "changes_needed", "comments": []}', False),
        ('{"verdict": "inconclusive", "reason": "r"}', True),
        ('{"verdict": "inconclusive", "reason": "  "}', False),
    ],
)
def test_review_reports(tmp_path, text, ok):
    assert (read_review_report(write(tmp_path, text)) is not None) == ok
