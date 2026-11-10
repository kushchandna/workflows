import pytest

from workflows.harness import Harness, HarnessError, HarnessTimeoutError


def test_harness_requires_run():
    class Incomplete(Harness):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_harness_error_carries_details():
    err = HarnessError("boom", stderr="trace", session_id="s1")
    assert str(err) == "boom"
    assert err.stderr == "trace"
    assert err.session_id == "s1"


def test_timeout_error_is_harness_error():
    err = HarnessTimeoutError("slow")
    assert isinstance(err, HarnessError)
    assert err.stderr is None and err.session_id is None
