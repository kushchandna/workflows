import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from workflows.agent import AgentError, TextEvent
from workflows.agent.testing import FakeAgent
from workflows.code_change import (
    ChangeEvent,
    ChangeInput,
    ChangeResult,
    ChangeSettings,
    ChangeStage,
)
from workflows.code_change.loop import run_change


def write_report(path: Path, **data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


async def run(
    repo,
    tmp_path: Path,
    change_agent: FakeAgent,
    review_agent: FakeAgent | None = None,
    events: list[ChangeEvent] | None = None,
    **input_kw: Any,
) -> ChangeResult:
    code = tmp_path / "code"
    code.mkdir(exist_ok=True)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return await run_change(
        ChangeInput(codebase=code, instruction="Fix it.", **input_kw),
        repo,
        change_agent=change_agent,
        review_agent=review_agent or change_agent,
        settings=ChangeSettings(check_timeout_seconds=10),
        work=work,
        on_event=events.append if events is not None else None,
    )


def committing_agent(repo, prompt_info, **report):
    """Commits, then reports ``done`` (or the given report)."""

    def respond(req):
        repo.commit("fix")
        write_report(
            prompt_info.result_file(req.prompt),
            **(report or {"outcome": "done", "title": "t", "description": "d"}),
        )
        return "ok"

    return FakeAgent(respond=respond)


async def test_completed_first_attempt(fake_repo, tmp_path, prompt_info):
    agent = committing_agent(
        fake_repo,
        prompt_info,
        outcome="done",
        title="Remove tmp",
        description="Unused.",
    )
    r = await run(fake_repo, tmp_path, agent)
    assert (r.status, r.reason, r.base_commit) == ("completed", None, "base0")
    assert (r.title, r.description) == ("Remove tmp", "Unused.")
    assert [c.id for c in r.commits] == ["c1"] and fake_repo.reset_calls == 0
    assert [(a.number, a.ended_at) for a in r.attempts] == [(1, None)]
    assert (tmp_path / "work" / "attempt-1" / "change.diff").read_text() == "diff\n"


async def test_needs_human_stops_and_resets(fake_repo, tmp_path, prompt_info):
    agent = committing_agent(
        fake_repo,
        prompt_info,
        outcome="needs_human",
        reason="Signature change needed.",
    )
    r = await run(fake_repo, tmp_path, agent)
    assert (r.status, r.reason) == ("needs_human", "Signature change needed.")
    assert len(agent.requests) == 1 and fake_repo.reset_calls == 1
    assert r.commits == []
    assert r.attempts[0].ended_at == ChangeStage.CHANGE
    assert r.attempts[0].checks == []


async def test_needs_human_on_a_later_attempt(fake_repo, tmp_path, prompt_info):
    def respond(req):
        path = prompt_info.result_file(req.prompt)
        if prompt_info.attempt(req.prompt) == 1:
            fake_repo.commit("fix")
            write_report(path, outcome="done", title="t", description="d")
        else:
            write_report(path, outcome="needs_human", reason="Too big.")
        return "ok"

    r = await run(fake_repo, tmp_path, FakeAgent(respond=respond), checks=["exit 1"])
    assert (r.status, r.reason) == ("needs_human", "Too big.")
    assert [a.ended_at for a in r.attempts] == [ChangeStage.CHECKS, ChangeStage.CHANGE]
    assert fake_repo.reset_calls == 1


async def test_invalid_change_result_uses_an_attempt(fake_repo, tmp_path, prompt_info):
    def respond(req):
        if prompt_info.attempt(req.prompt) == 1:
            fake_repo.commit("fix")
        else:
            write_report(
                prompt_info.result_file(req.prompt),
                outcome="done",
                title="t",
                description="d",
            )
        return "ok"

    agent = FakeAgent(respond=respond)
    r = await run(fake_repo, tmp_path, agent)
    assert r.status == "completed"
    assert (r.attempts[0].ended_at, r.attempts[0].reason) == (
        ChangeStage.CHANGE,
        "no valid change result",
    )
    assert "valid result file" in agent.requests[1].prompt


async def test_commit_problem_retries_with_reason(fake_repo, tmp_path, prompt_info):
    def respond(req):
        if prompt_info.attempt(req.prompt) == 2:
            fake_repo.commit("fix")
        write_report(
            prompt_info.result_file(req.prompt),
            outcome="done",
            title="t",
            description="d",
        )
        return "ok"

    agent = FakeAgent(respond=respond)
    r = await run(fake_repo, tmp_path, agent)
    assert r.attempts[0].reason == "no new commit"
    assert (r.attempts[0].ended_at) == ChangeStage.COMMIT_CHECK
    assert "no new commit" in agent.requests[1].prompt
    assert r.status == "completed"


async def test_failing_check_retries_with_output(fake_repo, tmp_path, prompt_info):
    check = "test -f fixed || (echo missing-fixed; exit 1)"

    def respond(req):
        if prompt_info.attempt(req.prompt) == 2:
            (tmp_path / "code" / "fixed").write_text("")
        fake_repo.commit("fix")
        write_report(
            prompt_info.result_file(req.prompt),
            outcome="done",
            title="t",
            description="d",
        )
        return "ok"

    agent = FakeAgent(respond=respond)
    r = await run(fake_repo, tmp_path, agent, checks=[check])
    a1 = r.attempts[0]
    assert (a1.ended_at, a1.reason) == (ChangeStage.CHECKS, f"{check} exited with 1")
    assert "missing-fixed" in agent.requests[1].prompt and r.status == "completed"
    assert (tmp_path / "work" / "attempt-1" / "checks" / "1.log").exists()


async def test_out_of_attempts(fake_repo, tmp_path, prompt_info):
    agent = committing_agent(fake_repo, prompt_info)
    r = await run(fake_repo, tmp_path, agent, max_attempts=2, checks=["exit 1"])
    assert (r.status, r.reason) == ("failed", "checks: exit 1 exited with 1")
    assert len(r.attempts) == 2 and fake_repo.reset_calls == 1
    assert (r.title, r.description) == ("t", "d")


async def test_single_attempt(fake_repo, tmp_path):
    agent = FakeAgent()
    r = await run(fake_repo, tmp_path, agent, max_attempts=1)
    assert (r.status, r.reason) == ("failed", "change: no valid change result")
    assert len(agent.requests) == 1


async def test_checks_skipped_after_commit_check_fails(
    fake_repo, tmp_path, prompt_info
):
    def respond(req):
        write_report(
            prompt_info.result_file(req.prompt),
            outcome="done",
            title="t",
            description="d",
        )
        return "ok"

    r = await run(
        fake_repo,
        tmp_path,
        FakeAgent(respond=respond),
        max_attempts=1,
        checks=["touch ran"],
    )
    assert r.reason == "commit-check: no new commit"
    assert not (tmp_path / "code" / "ran").exists()
    assert r.attempts[0].checks == []


async def test_review_skipped_when_checks_fail(fake_repo, tmp_path, prompt_info):
    reviewer = FakeAgent()
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        reviewer,
        review=True,
        max_attempts=1,
        checks=["exit 1"],
    )
    assert reviewer.requests == [] and r.attempts[0].review is None


async def test_review_not_run_when_off(fake_repo, tmp_path, prompt_info):
    reviewer = FakeAgent()
    await run(fake_repo, tmp_path, committing_agent(fake_repo, prompt_info), reviewer)
    assert reviewer.requests == []


async def test_review_changes_needed_then_pass(fake_repo, tmp_path, prompt_info):
    def review(req):
        path = prompt_info.result_file(req.prompt)
        if len(reviewer.requests) == 1:
            write_report(
                path, verdict="changes_needed", comments=["Also fix parse_body."]
            )
        else:
            write_report(path, verdict="pass")
        return "ok"

    reviewer = FakeAgent(respond=review)
    agent = committing_agent(fake_repo, prompt_info)
    r = await run(fake_repo, tmp_path, agent, reviewer, review=True)
    assert r.status == "completed"
    assert (r.attempts[0].ended_at, r.attempts[0].reason) == (
        ChangeStage.REVIEW,
        "review asked for changes",
    )
    assert r.attempts[0].review is not None
    assert r.attempts[0].review.comments == ["Also fix parse_body."]
    assert "Also fix parse_body." in agent.requests[1].prompt
    assert prompt_info.result_file(reviewer.requests[0].prompt) == (
        tmp_path / "work" / "attempt-1" / "review" / "call-1" / "result.json"
    )
    assert r.attempts[1].review is not None
    assert r.attempts[1].review.verdict == "pass"


async def test_review_asks_for_changes_on_the_last_attempt(
    fake_repo, tmp_path, prompt_info
):
    def review(req):
        write_report(
            prompt_info.result_file(req.prompt),
            verdict="changes_needed",
            comments=["No."],
        )
        return "ok"

    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        FakeAgent(respond=review),
        review=True,
        max_attempts=1,
    )
    assert (r.status, r.reason) == ("failed", "review: review asked for changes")


async def test_review_inconclusive_stops(fake_repo, tmp_path, prompt_info):
    def review(req):
        write_report(
            prompt_info.result_file(req.prompt),
            verdict="inconclusive",
            reason="Instruction is ambiguous.",
        )
        return "ok"

    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        FakeAgent(respond=review),
        review=True,
        max_attempts=3,
    )
    assert (r.status, r.reason) == ("inconclusive", "Instruction is ambiguous.")
    assert len(r.attempts) == 1 and fake_repo.reset_calls == 1


async def test_invalid_review_retried_once(fake_repo, tmp_path, prompt_info):
    events: list[ChangeEvent] = []
    reviewer = FakeAgent(events=[TextEvent(call_id="x", text="hi")])
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        reviewer,
        events,
        review=True,
    )
    assert (r.status, r.reason) == (
        "inconclusive",
        "no valid review result after retry",
    )
    assert len(reviewer.requests) == 2
    assert [e.call for e in events if e.stage == ChangeStage.REVIEW] == [1, 2]


async def test_review_guard(fake_repo, tmp_path, prompt_info):
    def review(req):
        write_report(prompt_info.result_file(req.prompt), verdict="pass")
        return "ok"

    fake_repo.unchanged_result = False
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        FakeAgent(respond=review),
        review=True,
    )
    assert (r.status, r.reason) == ("inconclusive", "review changed the codebase")


async def test_reset_off_keeps_commits(fake_repo, tmp_path, prompt_info):
    agent = committing_agent(
        fake_repo, prompt_info, outcome="needs_human", reason="Ask someone."
    )
    r = await run(fake_repo, tmp_path, agent, reset_on_failure=False)
    assert fake_repo.reset_calls == 0 and [c.id for c in r.commits] == ["c1"]


async def test_agent_error_resets_and_propagates(fake_repo, tmp_path):
    def respond(req):
        raise AgentError("boom")

    with pytest.raises(AgentError):
        await run(fake_repo, tmp_path, FakeAgent(respond=respond))
    assert fake_repo.reset_calls == 1


async def test_cancellation_resets(fake_repo, tmp_path):
    started = asyncio.Event()

    async def respond(req):
        started.set()
        await asyncio.Event().wait()
        return "never"

    task = asyncio.create_task(run(fake_repo, tmp_path, FakeAgent(respond=respond)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake_repo.reset_calls == 1


async def test_reset_failure_is_chained(fake_repo, tmp_path):
    def respond(req):
        raise AgentError("boom")

    fake_repo.reset_error = RuntimeError("reset failed")
    with pytest.raises(RuntimeError, match="reset failed") as info:
        await run(fake_repo, tmp_path, FakeAgent(respond=respond))
    assert isinstance(info.value.__cause__, AgentError)


async def test_no_reset_on_error_when_off(fake_repo, tmp_path):
    def respond(req):
        raise AgentError("boom")

    with pytest.raises(AgentError):
        await run(
            fake_repo,
            tmp_path,
            FakeAgent(respond=respond),
            reset_on_failure=False,
        )
    assert fake_repo.reset_calls == 0


async def test_events_and_requests(fake_repo, tmp_path, prompt_info):
    def respond(req):
        path = prompt_info.result_file(req.prompt)
        if "/review/" in str(path):
            write_report(path, verdict="pass")
        else:
            fake_repo.commit("fix")
            write_report(path, outcome="done", title="t", description="d")
        return "ok"

    events: list[ChangeEvent] = []
    agent = FakeAgent(respond=respond, events=[TextEvent(call_id="x", text="hi")])
    r = await run(fake_repo, tmp_path, agent, events=events, review=True)
    assert r.status == "completed"
    assert [(e.stage, e.attempt, e.call) for e in events] == [
        (ChangeStage.CHANGE, 1, 1),
        (ChangeStage.REVIEW, 1, 1),
    ]
    req = agent.requests[0]
    assert req.working_folder == tmp_path / "code"
    assert req.extra_folders == [tmp_path / "work"]
    assert req.timeout_seconds == 1800


async def test_commit_check_retry_names_the_last_checked_commit(
    fake_repo, tmp_path, prompt_info
):
    def respond(req):
        if prompt_info.attempt(req.prompt) == 1:
            fake_repo.commit("fix")
        write_report(
            prompt_info.result_file(req.prompt),
            outcome="done",
            title="t",
            description="d",
        )
        return "ok"

    agent = FakeAgent(respond=respond)
    r = await run(fake_repo, tmp_path, agent, max_attempts=3, checks=["exit 1"])
    assert [a.ended_at for a in r.attempts] == [
        ChangeStage.CHECKS,
        ChangeStage.COMMIT_CHECK,
        ChangeStage.COMMIT_CHECK,
    ]
    assert "descend from commit `c1`" in agent.requests[2].prompt
    assert "check out `main`" in agent.requests[2].prompt


async def test_review_guard_compares_with_the_last_checked_commit(
    fake_repo, tmp_path, prompt_info
):
    def review(req):
        fake_repo.commit("sneaky")
        write_report(prompt_info.result_file(req.prompt), verdict="pass")
        return "ok"

    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        FakeAgent(respond=review),
        review=True,
    )
    assert (r.status, r.reason) == ("inconclusive", "review changed the codebase")


async def test_review_guard_runs_after_the_first_call_even_without_a_result(
    fake_repo, tmp_path, prompt_info
):
    def review(req):
        fake_repo.unchanged_result = False
        return "ok"

    reviewer = FakeAgent(respond=review)
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        reviewer,
        review=True,
    )
    assert (r.status, r.reason) == ("inconclusive", "review changed the codebase")
    assert len(reviewer.requests) == 1


async def test_review_guard_runs_after_the_retry(fake_repo, tmp_path, prompt_info):
    def review(req):
        if len(reviewer.requests) == 2:
            fake_repo.unchanged_result = False
            write_report(prompt_info.result_file(req.prompt), verdict="pass")
        return "ok"

    reviewer = FakeAgent(respond=review)
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        reviewer,
        review=True,
    )
    assert (r.status, r.reason) == ("inconclusive", "review changed the codebase")
    assert len(reviewer.requests) == 2


def titled_agent(fake_repo, prompt_info, last_report=None):
    """Commits and reports ``done`` with a title that names the attempt."""

    def respond(req):
        n = prompt_info.attempt(req.prompt)
        fake_repo.commit(f"fix {n}")
        report = {"outcome": "done", "title": f"t{n}", "description": f"d{n}"}
        if last_report is not None and n == 3:
            report = last_report
        write_report(prompt_info.result_file(req.prompt), **report)
        return "ok"

    return FakeAgent(respond=respond)


async def test_title_comes_from_the_last_done_result(fake_repo, tmp_path, prompt_info):
    r = await run(
        fake_repo,
        tmp_path,
        titled_agent(fake_repo, prompt_info),
        max_attempts=2,
        checks=["exit 1"],
    )
    assert (r.status, r.title, r.description) == ("failed", "t2", "d2")


async def test_title_is_kept_when_a_later_attempt_needs_a_human(
    fake_repo, tmp_path, prompt_info
):
    agent = titled_agent(
        fake_repo,
        prompt_info,
        last_report={"outcome": "needs_human", "reason": "Too big."},
    )
    r = await run(fake_repo, tmp_path, agent, max_attempts=3, checks=["exit 1"])
    assert (r.status, r.title, r.description) == ("needs_human", "t2", "d2")


async def test_title_is_kept_when_the_review_is_inconclusive(
    fake_repo, tmp_path, prompt_info
):
    def review(req):
        write_report(
            prompt_info.result_file(req.prompt),
            verdict="inconclusive",
            reason="Unclear.",
        )
        return "ok"

    r = await run(
        fake_repo,
        tmp_path,
        titled_agent(fake_repo, prompt_info),
        FakeAgent(respond=review),
        review=True,
    )
    assert (r.status, r.title, r.description) == ("inconclusive", "t1", "d1")


async def test_title_is_empty_when_no_attempt_reported_done(fake_repo, tmp_path):
    r = await run(fake_repo, tmp_path, FakeAgent(), max_attempts=2)
    assert (r.status, r.title, r.description) == ("failed", None, None)


async def test_checks_that_leave_files_fail_the_attempt_not_the_review(
    fake_repo, tmp_path, prompt_info
):
    fake_repo.dirty_when_exists = [tmp_path / "code" / "report.xml"]
    reviewer = FakeAgent()
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        reviewer,
        review=True,
        max_attempts=1,
        checks=["touch report.xml"],
    )
    assert (r.status, r.reason) == (
        "failed",
        "checks: checks left changes in the work tree: report.xml",
    )
    a = r.attempts[0]
    assert (a.ended_at, a.left_changes) == (ChangeStage.CHECKS, "report.xml")
    assert a.checks[0].exit_code == 0 and reviewer.requests == []


async def test_a_failing_check_that_leaves_files_reports_both(
    fake_repo, tmp_path, prompt_info
):
    fake_repo.dirty_when_exists = [tmp_path / "code" / "report.xml"]
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        max_attempts=1,
        checks=["touch report.xml; exit 1"],
    )
    assert r.reason == (
        "checks: touch report.xml; exit 1 exited with 1; "
        "checks left changes in the work tree: report.xml"
    )


async def test_the_next_attempt_is_told_what_the_checks_left(
    fake_repo, tmp_path, prompt_info
):
    report = tmp_path / "code" / "report.xml"
    fake_repo.dirty_when_exists = [report]

    def respond(req):
        if prompt_info.attempt(req.prompt) == 2:
            fake_repo.dirty_when_exists.clear()  # the agent git-ignored the file
        fake_repo.commit("fix")
        write_report(
            prompt_info.result_file(req.prompt),
            outcome="done",
            title="t",
            description="d",
        )
        return "ok"

    def review(req):
        write_report(prompt_info.result_file(req.prompt), verdict="pass")
        return "ok"

    agent = FakeAgent(respond=respond)
    r = await run(
        fake_repo,
        tmp_path,
        agent,
        FakeAgent(respond=review),
        review=True,
        checks=["touch report.xml"],
    )
    assert r.status == "completed"
    retry = agent.requests[1].prompt.split("What went wrong last time")[1]
    assert "report.xml" in retry and "Git-ignore such files" in retry
    assert r.attempts[0].left_changes == "report.xml"
    assert r.attempts[1].left_changes is None


async def test_nothing_left_means_no_left_changes(fake_repo, tmp_path, prompt_info):
    r = await run(
        fake_repo,
        tmp_path,
        committing_agent(fake_repo, prompt_info),
        checks=["true"],
    )
    assert r.status == "completed" and r.attempts[0].left_changes is None
