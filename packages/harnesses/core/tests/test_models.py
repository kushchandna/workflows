from pathlib import Path

import pytest
from pydantic import ValidationError

from workflows.harness import HarnessRequest, HarnessResult, Usage


def test_request_defaults():
    r = HarnessRequest(prompt="p", working_folder=Path("/w"))
    assert r.extra_folders == []
    assert r.system_prompt is None
    assert r.timeout_seconds is None


def test_request_rejects_empty_prompt():
    with pytest.raises(ValidationError):
        HarnessRequest(prompt="", working_folder=Path("/w"))


@pytest.mark.parametrize("timeout", [0, -1])
def test_request_rejects_non_positive_timeout(timeout):
    with pytest.raises(ValidationError):
        HarnessRequest(prompt="p", working_folder=Path("/w"), timeout_seconds=timeout)


def test_usage_defaults_empty():
    assert Usage() == Usage(input_tokens=None, output_tokens=None, cost_usd=None)


def test_result_json_round_trip():
    r = HarnessResult(
        call_id="c",
        final_message="done",
        session_id="s",
        duration_seconds=1.5,
        usage=Usage(input_tokens=10, output_tokens=2, cost_usd=0.01),
    )
    assert HarnessResult.model_validate_json(r.model_dump_json()) == r


def test_models_are_frozen():
    r = HarnessRequest(prompt="p", working_folder=Path("/w"))
    with pytest.raises(ValidationError):
        r.prompt = "q"
