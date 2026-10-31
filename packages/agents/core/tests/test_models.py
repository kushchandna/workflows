from pathlib import Path

import pytest
from pydantic import ValidationError

from workflows.agent import AgentRequest, AgentResult, PermissionMode, Usage


def test_request_defaults():
    r = AgentRequest(prompt="p", working_folder=Path("/w"))
    assert r.extra_folders == []
    assert r.system_prompt is None
    assert r.timeout_seconds is None


def test_request_rejects_empty_prompt():
    with pytest.raises(ValidationError):
        AgentRequest(prompt="", working_folder=Path("/w"))


@pytest.mark.parametrize("timeout", [0, -1])
def test_request_rejects_non_positive_timeout(timeout):
    with pytest.raises(ValidationError):
        AgentRequest(prompt="p", working_folder=Path("/w"), timeout_seconds=timeout)


def test_usage_defaults_empty():
    assert Usage() == Usage(input_tokens=None, output_tokens=None, cost_usd=None)


def test_result_json_round_trip():
    r = AgentResult(
        call_id="c",
        final_message="done",
        session_id="s",
        duration_seconds=1.5,
        usage=Usage(input_tokens=10, output_tokens=2, cost_usd=0.01),
    )
    assert AgentResult.model_validate_json(r.model_dump_json()) == r


def test_permission_mode_values():
    assert PermissionMode("auto") is PermissionMode.AUTO
    assert PermissionMode("bypass") is PermissionMode.BYPASS


def test_models_are_frozen():
    r = AgentRequest(prompt="p", working_folder=Path("/w"))
    with pytest.raises(ValidationError):
        r.prompt = "q"
