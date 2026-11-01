import pytest

from workflows.agent import Agent, AgentError, AgentTimeoutError


def test_agent_requires_run():
    class Incomplete(Agent):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_agent_error_carries_details():
    err = AgentError("boom", stderr="trace", session_id="s1")
    assert str(err) == "boom"
    assert err.stderr == "trace"
    assert err.session_id == "s1"


def test_timeout_error_is_agent_error():
    err = AgentTimeoutError("slow")
    assert isinstance(err, AgentError)
    assert err.stderr is None and err.session_id is None
