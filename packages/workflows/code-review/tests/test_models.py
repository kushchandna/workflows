from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from workflows.code_review import (
    BUILTIN_AGENTS,
    DEFAULT_AGENTS,
    ChangeTarget,
    Comment,
    DroppedThreadAction,
    FileTarget,
    LinesTarget,
    ReviewAgent,
    ReviewEvent,
    ReviewInput,
    ReviewResult,
    ReviewSettings,
    ReviewStage,
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


def test_stage_values_are_folder_names():
    assert [s.value for s in ReviewStage] == ["per-file", "overall", "consolidation"]


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


@pytest.mark.parametrize(
    "name", ["Security", "ui review", "-ui", "ui-", "a--b", "", "a/b"]
)
def test_agent_names_must_be_safe_folder_names(name):
    with pytest.raises(ValidationError):
        ReviewAgent(name=name, instructions="x")


def test_agent_instructions_must_not_be_blank():
    with pytest.raises(ValidationError):
        ReviewAgent(name="layout", instructions="  \n")


def test_builtin_agents():
    assert set(DEFAULT_AGENTS) <= set(BUILTIN_AGENTS)
    for name, agent in BUILTIN_AGENTS.items():
        assert agent.name == name and agent.instructions.startswith("Your job:")
    assert [a.name for a in BUILTIN_AGENTS.values() if a.needs_codebase] == [
        "code-patterns"
    ]
    assert list(BUILTIN_AGENTS) == ["test-coverage", "code-patterns", "security", "ui"]
    assert DEFAULT_AGENTS == ("test-coverage", "code-patterns", "security")


def test_input_with_agents_round_trips_through_json():
    review_input = ReviewInput(
        diffs=Path("/d"),
        agents=["test-coverage", ReviewAgent(name="layout", instructions="x")],
    )
    loaded = ReviewInput.model_validate_json(review_input.model_dump_json())
    assert loaded == review_input
    assert loaded.agents is not None and isinstance(loaded.agents[1], ReviewAgent)


@pytest.mark.parametrize("source", [{}, {"stage": "overall", "agent": "layout"}])
def test_event_and_dropped_action_name_exactly_one_source(source):
    from workflows.harness import TextEvent

    event = TextEvent(call_id="c", text="t")
    with pytest.raises(ValidationError, match="exactly one"):
        ReviewEvent(batch=None, attempt=1, event=event, **source)
    with pytest.raises(ValidationError, match="exactly one"):
        DroppedThreadAction(thread_id="t", action="resolve", reason="r", **source)
