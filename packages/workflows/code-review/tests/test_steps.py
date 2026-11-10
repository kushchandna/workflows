import json
import re

import pytest

from workflows.code_review import ReviewEvent, ReviewStage
from workflows.code_review.calls import Step
from workflows.code_review.changes import ChangedFile
from workflows.code_review.formats import StepResult
from workflows.code_review.prompts import PromptContext
from workflows.code_review.steps import run_per_file_batch, run_step
from workflows.code_review.validation import Scope
from workflows.harness import HarnessError, TextEvent
from workflows.harness.testing import FakeHarness

OVERALL = Step(stage=ReviewStage.OVERALL)
TEST_COVERAGE = Step(agent="test-coverage")
CODE_PATTERNS = Step(agent="code-patterns")


def files(*paths):
    return [ChangedFile(p, "modified", None, False, 1) for p in paths]


def ctx(work):
    return PromptContext(
        work=work,
        codebase=None,
        description=None,
        instructions=None,
        change_author=None,
        reviewer_name=None,
    )


def scope(paths, threads=()):
    return Scope(
        changed_paths=frozenset(paths),
        allowed_paths=frozenset(paths),
        codebase=None,
        thread_ids=frozenset(threads),
    )


def per_file_writer(prompt_info, make):  # make(path, attempt) -> dict | str | None
    def respond(req):
        out = prompt_info.output_folder(req.prompt)
        attempt = int(out.name.split("-")[1])
        for n, path in prompt_info.slots(req.prompt):
            content = make(path, attempt)
            if content is not None:
                text = content if isinstance(content, str) else json.dumps(content)
                (out / f"{n}.json").write_text(text)
        return "done"

    return respond


def good(path, comments=()):
    return {"path": path, "summary": "ok", "comments": list(comments)}


BAD_COMMENT = {"target": {"type": "file", "path": "zzz.py"}, "body": "hm"}


async def test_all_valid_needs_one_call(tmp_path, prompt_info, runner_for):
    harness = FakeHarness(per_file_writer(prompt_info, lambda p, a: good(p)))
    out = await run_per_file_batch(
        runner_for(harness, tmp_path),
        ctx(tmp_path),
        1,
        files("a.py", "b.py"),
        scope({"a.py", "b.py"}),
    )
    assert len(harness.requests) == 1
    assert set(out.checked) == {"a.py", "b.py"} and out.unreviewed == []
    assert (
        tmp_path / "harness-output" / "per-file" / "batch-1" / "attempt-1" / "1.json"
    ).exists()


async def test_retry_only_failed_files_and_mark_unreviewed(
    tmp_path, prompt_info, runner_for
):
    harness = FakeHarness(
        per_file_writer(prompt_info, lambda p, a: good(p) if p == "a.py" else None)
    )
    out = await run_per_file_batch(
        runner_for(harness, tmp_path),
        ctx(tmp_path),
        1,
        files("a.py", "b.py"),
        scope({"a.py", "b.py"}),
    )
    assert len(harness.requests) == 2
    assert prompt_info.slots(harness.requests[1].prompt) == [(1, "b.py")]
    assert prompt_info.output_folder(harness.requests[1].prompt).name == "attempt-2"
    assert out.unreviewed == ["b.py"] and set(out.checked) == {"a.py"}


async def test_wrong_path_in_result_is_unusable(tmp_path, prompt_info, runner_for):
    harness = FakeHarness(
        per_file_writer(prompt_info, lambda p, a: good("someone_else.py"))
    )
    out = await run_per_file_batch(
        runner_for(harness, tmp_path), ctx(tmp_path), 1, files("a.py"), scope({"a.py"})
    )
    assert out.unreviewed == ["a.py"] and out.checked == {}


async def test_invalid_comment_retried_then_moved(tmp_path, prompt_info, runner_for):
    harness = FakeHarness(
        per_file_writer(prompt_info, lambda p, a: good(p, [BAD_COMMENT]))
    )
    out = await run_per_file_batch(
        runner_for(harness, tmp_path), ctx(tmp_path), 1, files("a.py"), scope({"a.py"})
    )
    assert len(harness.requests) == 2
    comment = out.checked["a.py"].comments[0]
    assert comment.target.type == "change" and comment.body == "zzz.py: hm"


async def test_retry_fixes_the_comment(tmp_path, prompt_info, runner_for):
    fixed = {"target": {"type": "file", "path": "a.py"}, "body": "hm"}
    harness = FakeHarness(
        per_file_writer(
            prompt_info, lambda p, a: good(p, [BAD_COMMENT if a == 1 else fixed])
        )
    )
    out = await run_per_file_batch(
        runner_for(harness, tmp_path), ctx(tmp_path), 1, files("a.py"), scope({"a.py"})
    )
    assert out.checked["a.py"].comments[0].target.type == "file"


async def test_retry_unusable_falls_back_to_first(tmp_path, prompt_info, runner_for):
    harness = FakeHarness(
        per_file_writer(
            prompt_info, lambda p, a: good(p, [BAD_COMMENT]) if a == 1 else "not json"
        )
    )
    out = await run_per_file_batch(
        runner_for(harness, tmp_path), ctx(tmp_path), 1, files("a.py"), scope({"a.py"})
    )
    assert "a.py" in out.checked and out.unreviewed == []
    assert out.checked["a.py"].comments[0].target.type == "change"


async def test_dropped_thread_actions_are_reported(tmp_path, prompt_info, runner_for):
    action = {"thread_id": "t9", "action": "resolve"}
    harness = FakeHarness(
        per_file_writer(
            prompt_info, lambda p, a: {**good(p), "thread_actions": [action]}
        )
    )
    out = await run_per_file_batch(
        runner_for(harness, tmp_path),
        ctx(tmp_path),
        1,
        files("a.py"),
        scope({"a.py"}, ["t1"]),
    )
    assert out.checked["a.py"].thread_actions == []
    assert [(d.thread_id, d.stage, d.agent) for d in out.dropped] == [
        ("t9", ReviewStage.PER_FILE, None)
    ]


async def test_events_are_labeled(tmp_path, prompt_info, runner_for):
    seen: list[ReviewEvent] = []
    harness = FakeHarness(
        per_file_writer(prompt_info, lambda p, a: good(p)),
        events=[TextEvent(call_id="x", text="hi")],
    )
    await run_per_file_batch(
        runner_for(harness, tmp_path, seen.append),
        ctx(tmp_path),
        3,
        files("a.py"),
        scope({"a.py"}),
    )
    assert [(e.stage, e.agent, e.batch, e.attempt) for e in seen] == [
        (ReviewStage.PER_FILE, None, 3, 1)
    ]


def step_prompt_for(folder):
    return f"Output folder: {folder}\nResult file: {folder}/result.json"


def result_writer(prompt_info, make):  # make(attempt) -> dict | str | None
    def respond(req):
        out = prompt_info.output_folder(req.prompt)
        content = make(int(out.name.split("-")[1]))
        if content is not None:
            text = content if isinstance(content, str) else json.dumps(content)
            (out / "result.json").write_text(text)
        return "done"

    return respond


async def test_run_step_failed_after_retry(tmp_path, runner_for):
    harness = FakeHarness(lambda req: "wrote nothing")
    out = await run_step(
        runner_for(harness, tmp_path),
        TEST_COVERAGE,
        step_prompt_for,
        scope({"a.py"}),
        StepResult,
    )
    assert out.result is None and len(harness.requests) == 2


async def test_run_step_valid_result_needs_one_call(tmp_path, prompt_info, runner_for):
    body = {"comments": [{"target": {"type": "change"}, "body": "ok"}]}
    harness = FakeHarness(result_writer(prompt_info, lambda a: body))
    out = await run_step(
        runner_for(harness, tmp_path),
        OVERALL,
        step_prompt_for,
        scope({"a.py"}),
        StepResult,
    )
    assert len(harness.requests) == 1 and out.result is not None
    assert [c.body for c in out.result.comments] == ["ok"]


async def test_run_step_repairs_invalid_comments_after_retry(
    tmp_path, prompt_info, runner_for
):
    harness = FakeHarness(
        result_writer(prompt_info, lambda a: {"comments": [BAD_COMMENT]})
    )
    out = await run_step(
        runner_for(harness, tmp_path),
        OVERALL,
        step_prompt_for,
        scope({"a.py"}),
        StepResult,
    )
    assert len(harness.requests) == 2 and out.result is not None
    assert out.result.comments[0].body == "zzz.py: hm"


async def test_run_step_second_attempt_can_rescue(tmp_path, prompt_info, runner_for):
    harness = FakeHarness(
        result_writer(prompt_info, lambda a: None if a == 1 else {"comments": []})
    )
    out = await run_step(
        runner_for(harness, tmp_path),
        CODE_PATTERNS,
        step_prompt_for,
        scope({"a.py"}),
        StepResult,
    )
    assert out.result is not None and len(harness.requests) == 2


async def test_harness_error_propagates(tmp_path, runner_for):
    def boom(req):
        raise HarnessError("crashed")

    with pytest.raises(HarnessError):
        await run_step(
            runner_for(FakeHarness(boom), tmp_path),
            OVERALL,
            lambda folder: f"Output folder: {folder}",
            scope({"a.py"}),
            StepResult,
        )


async def test_harness_per_step(tmp_path, prompt_info, runner_for):
    default = FakeHarness(result_writer(prompt_info, lambda a: {"comments": []}))
    other = FakeHarness(result_writer(prompt_info, lambda a: {"comments": []}))
    runner = runner_for(
        default,
        tmp_path,
        harnesses=lambda p: other if p == OVERALL else default,
    )
    await run_step(runner, OVERALL, step_prompt_for, scope({"a.py"}), StepResult)
    await run_step(runner, TEST_COVERAGE, step_prompt_for, scope({"a.py"}), StepResult)
    assert (len(other.requests), len(default.requests)) == (1, 1)


async def test_request_wiring(tmp_path, prompt_info):
    import asyncio

    from workflows.code_review.calls import CallRunner

    harness = FakeHarness(result_writer(prompt_info, lambda a: {"comments": []}))
    code = tmp_path / "code"
    code.mkdir()
    runner = CallRunner(
        harnesses=lambda p: harness,
        working_folder=code,
        work=tmp_path,
        timeout_seconds=12.5,
        semaphore=asyncio.Semaphore(1),
        on_event=None,
    )
    await run_step(runner, OVERALL, step_prompt_for, scope({"a.py"}), StepResult)
    req = harness.requests[0]
    assert (req.working_folder, req.extra_folders, req.timeout_seconds) == (
        code,
        [tmp_path],
        12.5,
    )


async def test_retry_does_not_offer_threads_already_answered(
    tmp_path, prompt_info, runner_for
):
    # a.py succeeded and replied to t1. Only b.py is retried, so t1 must not be offered again,
    # or the retry could post a duplicate reply to the same thread.
    reply = {
        "target": {"type": "file", "path": "a.py"},
        "body": "done",
        "reply_to": "t1",
    }
    duplicate = {
        "target": {"type": "file", "path": "b.py"},
        "body": "again",
        "reply_to": "t1",
    }

    def make(path, attempt):
        if path == "a.py":
            return good(path, [reply])
        return good(path, [duplicate]) if attempt == 2 else None

    harness = FakeHarness(per_file_writer(prompt_info, make))
    out = await run_per_file_batch(
        runner_for(harness, tmp_path),
        ctx(tmp_path),
        1,
        files("a.py", "b.py"),
        scope({"a.py", "b.py"}, ["t1", "t2"]),
    )
    offered = [
        re.findall(r"^Threads you may act on: (.+)$", r.prompt, re.MULTILINE)
        for r in harness.requests
    ]
    assert offered == [["t1, t2"], ["t2"]]
    assert [c.reply_to for c in out.checked["a.py"].comments] == ["t1"]
    # the duplicate is not allowed to stay a reply
    assert [c.reply_to for c in out.checked["b.py"].comments] == [None]
