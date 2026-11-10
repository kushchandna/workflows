import pytest
from pydantic import TypeAdapter, ValidationError

from workflows.code_review import (
    ChangeTarget,
    Comment,
    FileTarget,
    LinesTarget,
    ReviewPass,
    ReviewResult,
    ReviewSettings,
    Target,
)
from workflows.code_review.formats import PerFileResult, Thread


def test_target_json_round_trip():
    adapter = TypeAdapter(Target)
    for t in [
        LinesTarget(path="a.py", start_line=1, end_line=2, side="old"),
        FileTarget(path="a.py"),
        ChangeTarget(),
    ]:
        assert adapter.validate_json(adapter.dump_json(t)) == t


def test_settings_defaults():
    s = ReviewSettings()
    assert (
        s.lines_per_batch,
        s.files_per_batch,
        s.directory_break_min_fill,
        s.max_parallel_calls,
        s.harness_timeout_seconds,
        s.work_folder,
    ) == (1000, 10, 0.5, 4, 1800, None)


@pytest.mark.parametrize(
    "bad",
    [
        {"lines_per_batch": 0},
        {"files_per_batch": 0},
        {"directory_break_min_fill": 1.5},
        {"max_parallel_calls": 0},
        {"harness_timeout_seconds": 0},
    ],
)
def test_settings_reject_bad_values(bad):
    with pytest.raises(ValidationError):
        ReviewSettings(**bad)


def test_pass_values_are_folder_names():
    assert [p.value for p in ReviewPass] == [
        "per-file",
        "overall",
        "test-coverage",
        "code-patterns",
        "consolidation",
    ]


def test_formats_parse_and_ignore_extra_keys():
    t = Thread.model_validate(
        {
            "id": "t1",
            "target": {"type": "change"},
            "state": "open",
            "messages": [{"author": "a", "body": "b"}],
            "extra": 1,
        }
    )
    assert t.id == "t1"
    r = PerFileResult.model_validate_json(
        '{"path": "a.py", "summary": "s", "comments":'
        ' [{"target": {"type": "file", "path": "a.py"}, "body": "x"}]}'
    )
    assert r.comments[0].reply_to is None


def test_result_json_round_trip():
    r = ReviewResult(
        status="approved", comments=[Comment(target=ChangeTarget(), body="ok")]
    )
    assert ReviewResult.model_validate_json(r.model_dump_json()) == r
