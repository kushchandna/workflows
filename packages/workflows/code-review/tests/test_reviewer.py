import asyncio
import json
import subprocess

import pytest

from workflows.code_review import (
    Codebase,
    CodeReviewer,
    ReviewAgent,
    ReviewEvent,
    ReviewInput,
    ReviewInputError,
    ReviewSettings,
    ReviewStage,
)
from workflows.harness import HarnessError
from workflows.harness.testing import FakeHarness

DIFF = "diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-a\n+b\n"
BINARY = "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"


@pytest.fixture
def diffs(tmp_path):
    d = tmp_path / "diffs"
    d.mkdir()
    for p in ["a.py", "b.py"]:
        (d / f"{p}.diff").write_text(DIFF.format(p=p))
    (d / "img.png.diff").write_text(BINARY)
    return d


def step_name(prompt_info, request):
    """The stage, or the agent's name, read from the call's output folder."""
    out = prompt_info.output_folder(request.prompt)
    name = out.parts[out.parts.index("harness-output") + 1]
    if name == "agents":
        return out.parts[out.parts.index("harness-output") + 2]
    return name


def scripted(
    prompt_info, per_file=None, agents=None, status="approved", consolidation=()
):
    """One harness that answers every step; the step is read from the output folder."""

    def respond(req):
        out = prompt_info.output_folder(req.prompt)
        name = step_name(prompt_info, req)
        if name == "per-file":
            for n, path in prompt_info.slots(req.prompt):
                body = (per_file or (lambda p: {"path": p, "summary": "ok"}))(path)
                if body is not None:
                    (out / f"{n}.json").write_text(json.dumps(body))
        elif name == "consolidation":
            body = {"status": status, "comments": list(consolidation)}
            (out / "result.json").write_text(json.dumps(body))
        else:
            body = (agents or {}).get(name, {"comments": []})
            if body is not None:
                (out / "result.json").write_text(json.dumps(body))
        return "done"

    return respond


async def test_happy_path(tmp_path, diffs, prompt_info):
    work = tmp_path / "work"
    comment = {"target": {"type": "change"}, "body": "LGTM"}
    harness = FakeHarness(scripted(prompt_info, consolidation=[comment]))
    r = await CodeReviewer(harness, settings=ReviewSettings(work_folder=work)).review(
        ReviewInput(diffs=diffs)
    )
    assert r.status == "approved"
    assert [c.body for c in r.comments] == ["LGTM"]
    states = {f.path: (f.state, f.reason) for f in r.coverage.files}
    assert states == {
        "a.py": ("reviewed", None),
        "b.py": ("reviewed", None),
        "img.png": ("skipped", "binary file"),
    }
    stages = {s.stage: (s.state, s.reason) for s in r.coverage.stages}
    assert stages == {s: ("ran", None) for s in ReviewStage}
    agents = [(a.agent, a.state, a.reason) for a in r.coverage.agents]
    assert agents == [
        ("test-coverage", "ran", None),
        ("code-patterns", "skipped", "no codebase"),
    ]
    assert (work / "results" / "per-file" / "a.py.json").exists()
    assert (work / "results" / "agents" / "test-coverage" / "result.json").exists()
    assert (work / "results" / "consolidation" / "result.json").exists()
    assert (work / "input" / "diffs" / "a.py.diff").exists()
    assert json.loads((work / "input" / "threads.json").read_text()) == []
    req = harness.requests[0]
    assert (req.timeout_seconds, req.extra_folders, req.working_folder) == (
        1800,
        [work],
        work,
    )


async def test_unreviewed_file_forces_inconclusive(diffs, prompt_info):
    def per_file(p):
        return None if p == "b.py" else {"path": p, "summary": "ok"}

    harness = FakeHarness(scripted(prompt_info, per_file=per_file))
    r = await CodeReviewer(harness).review(ReviewInput(diffs=diffs))
    assert r.status == "inconclusive"
    files = [(f.path, f.state, f.reason) for f in r.coverage.files]
    assert ("b.py", "unreviewed", "no valid result after retry") in files


async def test_failed_agent_forces_inconclusive(diffs, prompt_info):
    harness = FakeHarness(scripted(prompt_info, agents={"test-coverage": None}))
    r = await CodeReviewer(harness).review(ReviewInput(diffs=diffs))
    assert r.status == "inconclusive"
    agents = {a.agent: (a.state, a.reason) for a in r.coverage.agents}
    assert agents["test-coverage"] == ("failed", "no valid result after retry")


async def test_failed_consolidation_raises(diffs, prompt_info):
    inner = scripted(prompt_info)

    def respond(req):
        if step_name(prompt_info, req) != "consolidation":
            return inner(req)
        return "wrote nothing"

    with pytest.raises(HarnessError, match="consolidation"):
        await CodeReviewer(FakeHarness(respond)).review(ReviewInput(diffs=diffs))


THREAD = {
    "id": "t1",
    "target": {
        "type": "lines",
        "path": "a.py",
        "start_line": 1,
        "end_line": 1,
        "side": "new",
    },
    "state": "open",
    "messages": [{"author": "bot", "body": "Rename this"}],
}


async def test_replies_and_actions_from_routed_stage_survive(
    tmp_path, diffs, prompt_info
):
    threads = tmp_path / "threads.json"
    threads.write_text(json.dumps([THREAD]))

    def per_file(p):
        result = {"path": p, "summary": "ok"}
        if p == "a.py":
            reply = {
                "target": {"type": "change"},
                "body": "Done, thanks",
                "reply_to": "t1",
            }
            result |= {
                "comments": [
                    reply,
                    {"target": {"type": "file", "path": "a.py"}, "body": "new"},
                ],
                "thread_actions": [{"thread_id": "t1", "action": "resolve"}],
            }
        return result

    harness = FakeHarness(scripted(prompt_info, per_file=per_file))
    review_input = ReviewInput(diffs=diffs, discussions=[threads], reviewer_name="bot")
    r = await CodeReviewer(harness).review(review_input)
    # the per-file "new" comment is consolidation's to keep; the reply always survives
    assert [(c.body, c.reply_to) for c in r.comments] == [("Done, thanks", "t1")]
    # and a reply carries its thread's own target, not whatever the harness wrote
    assert r.comments[0].target.type == "lines"
    assert [(a.thread_id, a.action) for a in r.thread_actions] == [("t1", "resolve")]
    consolidation = [
        q for q in harness.requests if step_name(prompt_info, q) == "consolidation"
    ]
    assert "Threads you may act on: none" in consolidation[0].prompt


async def test_overall_gets_change_level_threads(tmp_path, diffs, prompt_info):
    change_thread = {**THREAD, "id": "t2", "target": {"type": "change"}}
    threads = tmp_path / "threads.json"
    threads.write_text(json.dumps([THREAD, change_thread]))
    harness = FakeHarness(scripted(prompt_info))
    await CodeReviewer(harness).review(ReviewInput(diffs=diffs, discussions=[threads]))
    offered = {
        step_name(prompt_info, q): q.prompt.split("Threads you may act on: ")[1].split(
            "\n"
        )[0]
        for q in harness.requests
    }
    assert offered["overall"] == "t2"
    assert offered["per-file"] == "t1"
    assert offered["test-coverage"] == "none"
    assert offered["consolidation"] == "t1, t2"


async def test_no_changes(tmp_path, prompt_info):
    (tmp_path / "empty").mkdir()
    harness = FakeHarness(scripted(prompt_info))
    r = await CodeReviewer(harness).review(ReviewInput(diffs=tmp_path / "empty"))
    assert r.status == "approved" and harness.requests == []
    coverage = [*r.coverage.stages, *r.coverage.agents]
    assert {(c.state, c.reason) for c in coverage} == {("skipped", "no changes")}
    assert len(r.coverage.stages) == len(ReviewStage)
    assert [a.agent for a in r.coverage.agents] == ["test-coverage", "code-patterns"]


async def test_only_binary_files(tmp_path, prompt_info):
    d = tmp_path / "d"
    d.mkdir()
    (d / "img.png.diff").write_text(BINARY)
    harness = FakeHarness(scripted(prompt_info))
    r = await CodeReviewer(harness).review(ReviewInput(diffs=d))
    assert r.status == "approved" and harness.requests == []
    coverage = [*r.coverage.stages, *r.coverage.agents]
    assert {(c.state, c.reason) for c in coverage} == {("skipped", "only binary files")}
    assert [(f.path, f.state) for f in r.coverage.files] == [("img.png", "skipped")]


async def test_input_errors(tmp_path, diffs):
    harness = FakeHarness()
    reviewer = CodeReviewer(harness)
    bad_threads = tmp_path / "bad.json"
    bad_threads.write_text("nope")
    for bad in [
        ReviewInput(),
        ReviewInput(diffs=tmp_path / "missing"),
        ReviewInput(codebase=Codebase(path=tmp_path / "missing", base_commit="HEAD")),
        ReviewInput(diffs=diffs, discussions=[bad_threads]),
    ]:
        with pytest.raises(ReviewInputError):
            await reviewer.review(bad)
    assert harness.requests == []


async def test_work_folder_must_be_empty(tmp_path, diffs):
    work = tmp_path / "work"
    work.mkdir()
    (work / "old.txt").write_text("x")
    harness = FakeHarness()
    reviewer = CodeReviewer(harness, settings=ReviewSettings(work_folder=work))
    with pytest.raises(ReviewInputError):
        await reviewer.review(ReviewInput(diffs=diffs))
    assert harness.requests == [] and (work / "old.txt").exists()


async def test_temporary_work_folder_is_removed(diffs, prompt_info):
    harness = FakeHarness(scripted(prompt_info))
    await CodeReviewer(harness).review(ReviewInput(diffs=diffs))
    work = harness.requests[0].extra_folders[0]
    assert not work.exists()


async def test_harness_error_cancels_and_propagates(diffs):
    async def respond(req):
        if "batch-1" in req.prompt:
            raise HarnessError("batch crashed")
        await asyncio.sleep(30)
        return "late"

    settings = ReviewSettings(files_per_batch=1)
    reviewer = CodeReviewer(FakeHarness(respond), settings=settings)
    with pytest.raises(HarnessError, match="batch crashed"):
        await asyncio.wait_for(reviewer.review(ReviewInput(diffs=diffs)), 5)


async def test_stage_and_agent_harnesses_override(diffs, prompt_info):
    default = FakeHarness(scripted(prompt_info))
    stage = FakeHarness(scripted(prompt_info))
    agent = FakeHarness(scripted(prompt_info))
    reviewer = CodeReviewer(
        default,
        stage_harnesses={ReviewStage.CONSOLIDATION: stage},
        agent_harnesses={"test-coverage": agent, "not-running": agent},
    )
    await reviewer.review(ReviewInput(diffs=diffs))
    assert [step_name(prompt_info, q) for q in stage.requests] == ["consolidation"]
    assert [step_name(prompt_info, q) for q in agent.requests] == ["test-coverage"]
    assert {step_name(prompt_info, q) for q in default.requests} == {
        "per-file",
        "overall",
    }


async def test_events_reach_on_event_labeled(diffs, prompt_info):
    seen: list[ReviewEvent] = []
    harness = FakeHarness(scripted(prompt_info))
    reviewer = CodeReviewer(harness)
    await reviewer.review(ReviewInput(diffs=diffs), on_event=seen.append)
    assert seen == []  # the fake emits no events of its own
    from workflows.harness import TextEvent

    harness = FakeHarness(
        scripted(prompt_info), events=[TextEvent(call_id="x", text="hi")]
    )
    await CodeReviewer(harness).review(ReviewInput(diffs=diffs), on_event=seen.append)
    labels = {(e.stage, e.agent, e.batch, e.attempt) for e in seen}
    assert (ReviewStage.PER_FILE, None, 1, 1) in labels
    assert (None, "test-coverage", None, 1) in labels
    assert (ReviewStage.CONSOLIDATION, None, None, 1) in labels


async def test_chosen_and_custom_agents_run(tmp_path, diffs, prompt_info):
    work = tmp_path / "work"
    security = ReviewAgent(name="security", instructions="Look for injection.")
    finding = {"comments": [{"target": {"type": "change"}, "body": "SQL injection"}]}
    harness = FakeHarness(scripted(prompt_info, agents={"security": finding}))
    reviewer = CodeReviewer(harness, settings=ReviewSettings(work_folder=work))
    r = await reviewer.review(
        ReviewInput(diffs=diffs, agents=["test-coverage", security])
    )
    assert [(a.agent, a.state) for a in r.coverage.agents] == [
        ("test-coverage", "ran"),
        ("security", "ran"),
    ]
    by_step = {step_name(prompt_info, q): q.prompt for q in harness.requests}
    assert set(by_step) == {
        "per-file",
        "test-coverage",
        "security",
        "overall",
        "consolidation",
    }
    assert "Look for injection." in by_step["security"]
    assert "agents/security/result.json" in by_step["consolidation"]
    saved = work / "results" / "agents" / "security" / "result.json"
    assert json.loads(saved.read_text())["comments"][0]["body"] == "SQL injection"


async def test_no_agents(diffs, prompt_info):
    harness = FakeHarness(scripted(prompt_info))
    r = await CodeReviewer(harness).review(ReviewInput(diffs=diffs, agents=[]))
    assert r.coverage.agents == []
    assert {step_name(prompt_info, q) for q in harness.requests} == {
        "per-file",
        "overall",
        "consolidation",
    }


async def test_custom_agent_may_replace_a_builtin(diffs, prompt_info):
    mine = ReviewAgent(name="test-coverage", instructions="Only check the API tests.")
    harness = FakeHarness(scripted(prompt_info))
    await CodeReviewer(harness).review(ReviewInput(diffs=diffs, agents=[mine]))
    [prompt] = [
        q.prompt
        for q in harness.requests
        if step_name(prompt_info, q) == "test-coverage"
    ]
    assert "Only check the API tests." in prompt


@pytest.mark.parametrize(
    "agents, message",
    [
        (["nope"], "unknown review agent 'nope'"),
        (["test-coverage", "test-coverage"], "chosen twice"),
        (
            ["test-coverage", ReviewAgent(name="test-coverage", instructions="x")],
            "chosen twice",
        ),
    ],
)
async def test_bad_agent_choice_is_an_input_error(tmp_path, diffs, agents, message):
    work = tmp_path / "work"
    harness = FakeHarness()
    reviewer = CodeReviewer(harness, settings=ReviewSettings(work_folder=work))
    with pytest.raises(ReviewInputError, match=message):
        await reviewer.review(ReviewInput(diffs=diffs, agents=agents))
    assert harness.requests == [] and not work.exists()


async def test_agent_thread_actions_are_dropped_and_labeled(
    tmp_path, diffs, prompt_info
):
    threads = tmp_path / "threads.json"
    threads.write_text(json.dumps([THREAD]))
    body = {
        "comments": [{"target": {"type": "change"}, "body": "Fixed", "reply_to": "t1"}],
        "thread_actions": [{"thread_id": "t1", "action": "resolve"}],
    }
    harness = FakeHarness(scripted(prompt_info, agents={"test-coverage": body}))
    r = await CodeReviewer(harness).review(
        ReviewInput(diffs=diffs, discussions=[threads], agents=["test-coverage"])
    )
    assert r.thread_actions == []
    assert all(c.reply_to is None for c in r.comments)
    dropped = [
        (d.thread_id, d.stage, d.agent) for d in r.coverage.dropped_thread_actions
    ]
    assert dropped == [("t1", None, "test-coverage")]


async def test_custom_agent_that_needs_a_codebase_is_skipped(diffs, prompt_info):
    ui = ReviewAgent(name="ui", instructions="Check layouts.", needs_codebase=True)
    harness = FakeHarness(scripted(prompt_info, agents={"test-coverage": None}))
    r = await CodeReviewer(harness).review(
        ReviewInput(diffs=diffs, agents=[ui, "test-coverage"])
    )
    assert [(a.agent, a.state, a.reason) for a in r.coverage.agents] == [
        ("ui", "skipped", "no codebase"),
        ("test-coverage", "failed", "no valid result after retry"),
    ]
    assert "ui" not in {step_name(prompt_info, q) for q in harness.requests}
    [consolidation] = [
        q.prompt
        for q in harness.requests
        if step_name(prompt_info, q) == "consolidation"
    ]
    assert "- review agent ui: skipped (no codebase)" in consolidation
    assert (
        "- review agent test-coverage: failed (no valid result after retry)"
        in consolidation
    )
    assert "agents/ui/result.json" not in consolidation


@pytest.mark.git
async def test_codebase_mode_reads_the_repository(tmp_path, prompt_info):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        env = [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
        ]
        out = subprocess.run(
            ["git", *env, *args], cwd=repo, check=True, capture_output=True
        )
        return out.stdout.decode().strip()

    git("init", "-q")
    (repo / "a.py").write_text("one\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repo / "a.py").write_text("one\ntwo\n")  # uncommitted
    (repo / "new.py").write_text("x\n")  # untracked
    harness = FakeHarness(scripted(prompt_info))
    review_input = ReviewInput(codebase=Codebase(path=repo, base_commit=base))
    r = await CodeReviewer(harness).review(review_input)
    assert {f.path for f in r.coverage.files} == {"a.py", "new.py"}
    agents = {a.agent: a.state for a in r.coverage.agents}
    assert agents == {"test-coverage": "ran", "code-patterns": "ran"}
    assert {q.working_folder for q in harness.requests} == {repo}


@pytest.mark.parametrize("inside", [".review", "sub/.review", ""])
async def test_work_folder_inside_the_codebase_is_rejected(tmp_path, diffs, inside):
    # In git mode the review's own diff files would show up as untracked changes
    # and be reviewed. Reject it before anything is created.
    repo = tmp_path / "repo"
    repo.mkdir()
    work = repo / inside if inside else repo
    harness = FakeHarness()
    reviewer = CodeReviewer(harness, settings=ReviewSettings(work_folder=work))
    review_input = ReviewInput(
        diffs=diffs, codebase=Codebase(path=repo, base_commit="HEAD")
    )
    with pytest.raises(ReviewInputError, match="inside the codebase"):
        await reviewer.review(review_input)
    assert harness.requests == [] and list(repo.iterdir()) == []
