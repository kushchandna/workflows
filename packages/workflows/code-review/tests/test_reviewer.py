import asyncio
import json
import subprocess

import pytest

from workflows.agent import AgentError
from workflows.agent.testing import FakeAgent
from workflows.code_review import (
    Codebase,
    CodeReviewer,
    ReviewEvent,
    ReviewInput,
    ReviewInputError,
    ReviewPass,
    ReviewSettings,
)

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


def scripted(
    prompt_info, per_file=None, passes=None, status="approved", consolidation=()
):
    """One agent that answers every pass; the pass is read from the output folder."""

    def respond(req):
        out = prompt_info.output_folder(req.prompt)
        name = out.parts[out.parts.index("agent-output") + 1]
        if name == "per-file":
            for n, path in prompt_info.slots(req.prompt):
                body = (per_file or (lambda p: {"path": p, "summary": "ok"}))(path)
                if body is not None:
                    (out / f"{n}.json").write_text(json.dumps(body))
        elif name == "consolidation":
            body = {"status": status, "comments": list(consolidation)}
            (out / "result.json").write_text(json.dumps(body))
        else:
            body = (passes or {}).get(name, {"comments": []})
            if body is not None:
                (out / "result.json").write_text(json.dumps(body))
        return "done"

    return respond


def pass_name(prompt_info, request):
    out = prompt_info.output_folder(request.prompt)
    return out.parts[out.parts.index("agent-output") + 1]


async def test_happy_path(tmp_path, diffs, prompt_info):
    work = tmp_path / "work"
    comment = {"target": {"type": "change"}, "body": "LGTM"}
    agent = FakeAgent(scripted(prompt_info, consolidation=[comment]))
    r = await CodeReviewer(agent, settings=ReviewSettings(work_folder=work)).review(
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
    passes = {p.review_pass: (p.state, p.reason) for p in r.coverage.passes}
    assert passes[ReviewPass.CODE_PATTERNS] == ("skipped", "no codebase")
    assert passes[ReviewPass.CONSOLIDATION] == ("ran", None)
    assert (work / "review-passes" / "per-file" / "a.py.json").exists()
    assert (work / "review-passes" / "consolidation" / "result.json").exists()
    assert (work / "input" / "diffs" / "a.py.diff").exists()
    assert json.loads((work / "input" / "threads.json").read_text()) == []
    req = agent.requests[0]
    assert (req.timeout_seconds, req.extra_folders, req.working_folder) == (
        1800,
        [work],
        work,
    )


async def test_unreviewed_file_forces_inconclusive(diffs, prompt_info):
    def per_file(p):
        return None if p == "b.py" else {"path": p, "summary": "ok"}

    agent = FakeAgent(scripted(prompt_info, per_file=per_file))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=diffs))
    assert r.status == "inconclusive"
    files = [(f.path, f.state, f.reason) for f in r.coverage.files]
    assert ("b.py", "unreviewed", "no valid result after retry") in files


async def test_failed_pass_forces_inconclusive(diffs, prompt_info):
    agent = FakeAgent(scripted(prompt_info, passes={"test-coverage": None}))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=diffs))
    assert r.status == "inconclusive"
    passes = {p.review_pass: (p.state, p.reason) for p in r.coverage.passes}
    assert passes[ReviewPass.TEST_COVERAGE] == ("failed", "no valid result after retry")


async def test_failed_consolidation_raises(diffs, prompt_info):
    inner = scripted(prompt_info)

    def respond(req):
        if pass_name(prompt_info, req) != "consolidation":
            return inner(req)
        return "wrote nothing"

    with pytest.raises(AgentError, match="consolidation"):
        await CodeReviewer(FakeAgent(respond)).review(ReviewInput(diffs=diffs))


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


async def test_replies_and_actions_from_routed_pass_survive(
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

    agent = FakeAgent(scripted(prompt_info, per_file=per_file))
    review_input = ReviewInput(diffs=diffs, discussions=[threads], reviewer_name="bot")
    r = await CodeReviewer(agent).review(review_input)
    # the per-file "new" comment is consolidation's to keep; the reply always survives
    assert [(c.body, c.reply_to) for c in r.comments] == [("Done, thanks", "t1")]
    # and a reply carries its thread's own target, not whatever the agent wrote
    assert r.comments[0].target.type == "lines"
    assert [(a.thread_id, a.action) for a in r.thread_actions] == [("t1", "resolve")]
    consolidation = [
        q for q in agent.requests if pass_name(prompt_info, q) == "consolidation"
    ]
    assert "Threads you may act on: none" in consolidation[0].prompt


async def test_overall_gets_change_level_threads(tmp_path, diffs, prompt_info):
    change_thread = {**THREAD, "id": "t2", "target": {"type": "change"}}
    threads = tmp_path / "threads.json"
    threads.write_text(json.dumps([THREAD, change_thread]))
    agent = FakeAgent(scripted(prompt_info))
    await CodeReviewer(agent).review(ReviewInput(diffs=diffs, discussions=[threads]))
    offered = {
        pass_name(prompt_info, q): q.prompt.split("Threads you may act on: ")[1].split(
            "\n"
        )[0]
        for q in agent.requests
    }
    assert offered["overall"] == "t2"
    assert offered["per-file"] == "t1"
    assert offered["test-coverage"] == "none"
    assert offered["consolidation"] == "t1, t2"


async def test_no_changes(tmp_path, prompt_info):
    (tmp_path / "empty").mkdir()
    agent = FakeAgent(scripted(prompt_info))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=tmp_path / "empty"))
    assert r.status == "approved" and agent.requests == []
    assert {(p.state, p.reason) for p in r.coverage.passes} == {
        ("skipped", "no changes")
    }
    assert len(r.coverage.passes) == len(ReviewPass)


async def test_only_binary_files(tmp_path, prompt_info):
    d = tmp_path / "d"
    d.mkdir()
    (d / "img.png.diff").write_text(BINARY)
    agent = FakeAgent(scripted(prompt_info))
    r = await CodeReviewer(agent).review(ReviewInput(diffs=d))
    assert r.status == "approved" and agent.requests == []
    assert {(p.state, p.reason) for p in r.coverage.passes} == {
        ("skipped", "only binary files")
    }
    assert [(f.path, f.state) for f in r.coverage.files] == [("img.png", "skipped")]


async def test_input_errors(tmp_path, diffs):
    agent = FakeAgent()
    reviewer = CodeReviewer(agent)
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
    assert agent.requests == []


async def test_work_folder_must_be_empty(tmp_path, diffs):
    work = tmp_path / "work"
    work.mkdir()
    (work / "old.txt").write_text("x")
    agent = FakeAgent()
    reviewer = CodeReviewer(agent, settings=ReviewSettings(work_folder=work))
    with pytest.raises(ReviewInputError):
        await reviewer.review(ReviewInput(diffs=diffs))
    assert agent.requests == [] and (work / "old.txt").exists()


async def test_temporary_work_folder_is_removed(diffs, prompt_info):
    agent = FakeAgent(scripted(prompt_info))
    await CodeReviewer(agent).review(ReviewInput(diffs=diffs))
    work = agent.requests[0].extra_folders[0]
    assert not work.exists()


async def test_agent_error_cancels_and_propagates(diffs):
    async def respond(req):
        if "batch-1" in req.prompt:
            raise AgentError("batch crashed")
        await asyncio.sleep(30)
        return "late"

    settings = ReviewSettings(files_per_batch=1)
    reviewer = CodeReviewer(FakeAgent(respond), settings=settings)
    with pytest.raises(AgentError, match="batch crashed"):
        await asyncio.wait_for(reviewer.review(ReviewInput(diffs=diffs)), 5)


async def test_pass_agents_override(diffs, prompt_info):
    default = FakeAgent(scripted(prompt_info))
    other = FakeAgent(scripted(prompt_info))
    reviewer = CodeReviewer(default, pass_agents={ReviewPass.CONSOLIDATION: other})
    await reviewer.review(ReviewInput(diffs=diffs))
    assert len(other.requests) == 1
    assert pass_name(prompt_info, other.requests[0]) == "consolidation"
    assert all(pass_name(prompt_info, q) != "consolidation" for q in default.requests)


async def test_events_reach_on_event_labeled(diffs, prompt_info):
    seen: list[ReviewEvent] = []
    agent = FakeAgent(scripted(prompt_info))
    reviewer = CodeReviewer(agent)
    await reviewer.review(ReviewInput(diffs=diffs), on_event=seen.append)
    assert seen == []  # the fake emits no events of its own
    from workflows.agent import TextEvent

    agent = FakeAgent(scripted(prompt_info), events=[TextEvent(call_id="x", text="hi")])
    await CodeReviewer(agent).review(ReviewInput(diffs=diffs), on_event=seen.append)
    labels = {(e.review_pass, e.batch, e.attempt) for e in seen}
    assert (ReviewPass.PER_FILE, 1, 1) in labels
    assert (ReviewPass.CONSOLIDATION, None, 1) in labels


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
    agent = FakeAgent(scripted(prompt_info))
    review_input = ReviewInput(codebase=Codebase(path=repo, base_commit=base))
    r = await CodeReviewer(agent).review(review_input)
    assert {f.path for f in r.coverage.files} == {"a.py", "new.py"}
    passes = {p.review_pass: p.state for p in r.coverage.passes}
    assert passes[ReviewPass.CODE_PATTERNS] == "ran"
    assert {q.working_folder for q in agent.requests} == {repo}
