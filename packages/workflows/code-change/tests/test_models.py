import pytest
from pydantic import ValidationError

from workflows.code_change import (
    Attempt,
    ChangeInput,
    ChangeResult,
    ChangeSettings,
    ChangeStage,
    CheckRun,
    Commit,
    ReviewOutcome,
)


def test_input_defaults(tmp_path):
    i = ChangeInput(codebase=tmp_path, instruction="x")
    assert (
        i.commit_instructions,
        i.checks,
        i.review,
        i.max_attempts,
        i.reset_on_failure,
    ) == (None, [], False, 3, True)


def test_settings_defaults():
    s = ChangeSettings()
    assert (
        s.harness_timeout_seconds,
        s.check_timeout_seconds,
        s.check_output_chars,
        s.work_folder,
    ) == (1800, 1800, 20000, None)


@pytest.mark.parametrize(
    "bad",
    [
        {"harness_timeout_seconds": 0},
        {"check_timeout_seconds": 0},
        {"check_output_chars": -1},
    ],
)
def test_settings_reject_bad_values(bad):
    with pytest.raises(ValidationError):
        ChangeSettings(**bad)


def test_max_attempts_at_least_one(tmp_path):
    with pytest.raises(ValidationError):
        ChangeInput(codebase=tmp_path, instruction="x", max_attempts=0)


def test_stage_values():
    assert [s.value for s in ChangeStage] == [
        "change",
        "commit-check",
        "checks",
        "review",
    ]


def test_result_json_round_trip():
    r = ChangeResult(
        status="failed",
        reason="checks: t exited with 1",
        base_commit="b",
        commits=[Commit(id="c", message="m")],
        attempts=[
            Attempt(
                number=1,
                ended_at=ChangeStage.CHECKS,
                reason="t exited with 1",
                checks=[CheckRun(command="t", exit_code=1, output="no")],
                review=ReviewOutcome(verdict="pass"),
            )
        ],
    )
    assert ChangeResult.model_validate_json(r.model_dump_json()) == r
