import os
import subprocess
from pathlib import Path

import pytest

from workflows.agent.testing import FakeAgent
from workflows.code_change import (
    ChangeInput,
    ChangeInputError,
    ChangeSettings,
    CodeChanger,
)

pytestmark = pytest.mark.git


@pytest.fixture(scope="module", autouse=True)
def hermetic_git():
    """Keep the developer's global git config out of these tests."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        yield


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / ".gitignore").write_text("ignored.txt\n")
    (r / "a.py").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    return r


def real_agent(prompt_info, message="ABC-1 Add b"):
    """Commits a new file for real on each change call; passes every review."""
    changes = []

    def respond(req):
        path = prompt_info.result_file(req.prompt)
        path.parent.mkdir(parents=True, exist_ok=True)
        if "/review/" in str(path):
            path.write_text('{"verdict": "pass"}')
            return "ok"
        folder = Path(req.working_folder)
        changes.append(None)
        name = f"f{len(changes)}.py"
        (folder / name).write_text(f"{name}\n")
        (folder / "ignored.txt").write_text("keep me")
        git(folder, "add", name)
        git(folder, "commit", "-q", "-m", message)
        path.write_text(
            '{"outcome": "done", "title": "Add b", "description": "Adds b.py."}'
        )
        return "ok"

    return FakeAgent(respond=respond)


async def test_completed_run(repo, prompt_info):
    base = head(repo)
    r = await CodeChanger(real_agent(prompt_info)).change(
        ChangeInput(codebase=repo, instruction="Add b.py", checks=["test -f f1.py"])
    )
    assert r.status == "completed" and r.reason is None
    assert [c.message for c in r.commits] == ["ABC-1 Add b"]
    assert r.base_commit == base and head(repo) == r.commits[0].id != base
    assert (r.title, r.description) == ("Add b", "Adds b.py.")


async def test_failed_run_resets(repo, prompt_info):
    base = head(repo)
    r = await CodeChanger(real_agent(prompt_info)).change(
        ChangeInput(
            codebase=repo, instruction="Add b.py", checks=["exit 1"], max_attempts=2
        )
    )
    assert r.status == "failed" and r.commits == []
    assert len(r.attempts) == 2 and r.base_commit == base
    assert head(repo) == base and not (repo / "f1.py").exists()
    assert (repo / "ignored.txt").read_text() == "keep me"


async def test_reset_off_keeps_commits(repo, prompt_info):
    r = await CodeChanger(real_agent(prompt_info)).change(
        ChangeInput(
            codebase=repo,
            instruction="Add b.py",
            checks=["exit 1"],
            max_attempts=1,
            reset_on_failure=False,
        )
    )
    assert r.status == "failed" and len(r.commits) == 1
    assert head(repo) == r.commits[0].id and (repo / "f1.py").exists()


async def test_review_agent_defaults_to_change_agent(repo, prompt_info):
    agent = real_agent(prompt_info)
    r = await CodeChanger(agent).change(
        ChangeInput(codebase=repo, instruction="Add b.py", review=True)
    )
    assert r.status == "completed" and len(agent.requests) == 2


async def test_separate_review_agent(repo, prompt_info):
    reviewer = real_agent(prompt_info)
    changer = CodeChanger(real_agent(prompt_info), review_agent=reviewer)
    r = await changer.change(
        ChangeInput(codebase=repo, instruction="Add b.py", review=True)
    )
    assert r.status == "completed" and len(reviewer.requests) == 1


async def test_work_folder_kept(repo, tmp_path, prompt_info):
    w = tmp_path / "w"
    changer = CodeChanger(
        real_agent(prompt_info), settings=ChangeSettings(work_folder=w)
    )
    await changer.change(
        ChangeInput(codebase=repo, instruction="Add b.py", checks=["true"])
    )
    assert (w / "attempt-1" / "change" / "result.json").exists()
    assert (w / "attempt-1" / "change.diff").exists()
    assert (w / "attempt-1" / "checks" / "1.log").exists()


async def test_dirty_codebase_rejected(repo, prompt_info):
    (repo / "a.py").write_text("changed\n")
    agent = real_agent(prompt_info)
    with pytest.raises(ChangeInputError, match="not clean"):
        await CodeChanger(agent).change(ChangeInput(codebase=repo, instruction="x"))
    assert agent.requests == []
