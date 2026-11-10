import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from workflows.code_review import ReviewInput
from workflows.code_review.threads import load_threads

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_pull_request.py"
spec = importlib.util.spec_from_file_location("fetch_pull_request", SCRIPT)
assert spec and spec.loader
script = importlib.util.module_from_spec(spec)
sys.modules["fetch_pull_request"] = script
spec.loader.exec_module(script)

SHA_BASE, SHA_HEAD, SHA_MERGE_BASE = "b" * 40, "a" * 40, "c" * 40

PR = {
    "number": 7,
    "title": "Add retry",
    "body": "Retries failed requests.",
    "state": "open",
    "draft": False,
    "html_url": "https://github.com/acme/app/pull/7",
    "user": {"login": "alice"},
    "base": {
        "ref": "main",
        "sha": SHA_BASE,
        "repo": {"clone_url": "https://github.com/acme/app.git"},
    },
    "head": {"sha": SHA_HEAD},
}

LINE_THREAD = {
    "id": "PRRT_1",
    "isResolved": False,
    "isOutdated": False,
    "path": "src/a.py",
    "line": 12,
    "startLine": 10,
    "originalLine": 12,
    "originalStartLine": 10,
    "diffSide": "RIGHT",
    "subjectType": "LINE",
    "comments": {
        "totalCount": 2,
        "nodes": [
            {"author": {"login": "bob"}, "body": "Why?"},
            {"author": {"login": "alice"}, "body": "Because."},
        ],
    },
}


class FakeGitHub:
    def __init__(
        self,
        viewer: str | None = "review-bot",
        threads=(LINE_THREAD,),
        comments=(),
        reviews=(),
        base_sha=SHA_BASE,
        head_sha=SHA_HEAD,
        merge_base=SHA_MERGE_BASE,
        clone_url="https://github.com/acme/app.git",
    ):
        self.viewer = viewer
        self.shas = (base_sha, head_sha, merge_base)
        self.pr = {
            **PR,
            "base": {**PR["base"], "sha": base_sha, "repo": {"clone_url": clone_url}},
            "head": {"sha": head_sha},
        }
        self.threads, self.comments, self.reviews = (
            list(threads),
            list(comments),
            list(reviews),
        )
        self.calls: list[str] = []

    def get(self, path):
        self.calls.append(path)
        base_sha, head_sha, merge_base = self.shas
        if path == "/repos/acme/app/pulls/7":
            return self.pr
        if path == f"/repos/acme/app/compare/{base_sha}...{head_sha}":
            return {"merge_base_commit": {"sha": merge_base}}
        if path == "/user":
            if self.viewer is None:
                raise script.ScriptError("GitHub API 403 for /user")
            return {"login": self.viewer}
        raise AssertionError(f"unexpected GET {path}")

    def get_all(self, path):
        self.calls.append(path)
        return {
            "/repos/acme/app/issues/7/comments": self.comments,
            "/repos/acme/app/pulls/7/reviews": self.reviews,
        }[path]

    def review_threads(self, owner, repo, number):
        self.calls.append("graphql")
        assert (owner, repo, number) == ("acme", "app", 7)
        return self.threads


# --- references ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/acme/app/pull/7",
        "https://github.com/acme/app/pull/7/",
        "https://github.com/acme/app/pull/7/files",
        "acme/app#7",
    ],
)
def test_parse_ref(text):
    ref = script.parse_ref(text)
    assert (ref.owner, ref.repo, ref.number) == ("acme", "app", 7)


@pytest.mark.parametrize(
    "text", ["7", "acme/app", "https://github.com/acme/app/issues/7", ""]
)
def test_parse_ref_rejects_other_text(text):
    with pytest.raises(script.ScriptError):
        script.parse_ref(text)


# --- threads ------------------------------------------------------------------------------


def target(**node):
    return script.target_from_review_thread(
        {"path": "a.py", "diffSide": "RIGHT", **node}
    )


def test_target_line_range_on_the_new_side():
    assert target(line=12, startLine=10) == {
        "type": "lines",
        "path": "a.py",
        "start_line": 10,
        "end_line": 12,
        "side": "new",
    }


def test_target_single_line_and_old_side():
    assert target(line=5, diffSide="LEFT") == {
        "type": "lines",
        "path": "a.py",
        "start_line": 5,
        "end_line": 5,
        "side": "old",
    }


def test_target_outdated_thread_uses_original_position():
    t = target(line=None, originalLine=7, originalStartLine=6, isOutdated=True)
    assert (t["start_line"], t["end_line"]) == (6, 7)


def test_target_file_level_and_change_level():
    assert target(line=None, subjectType="FILE") == {"type": "file", "path": "a.py"}
    assert target(line=None, originalLine=None) == {"type": "file", "path": "a.py"}
    assert script.target_from_review_thread({"path": None}) == {"type": "change"}


def test_target_reversed_range_is_ordered():
    t = target(line=3, startLine=9)
    assert (t["start_line"], t["end_line"]) == (3, 9)


def test_threads_cover_review_threads_comments_and_reviews():
    resolved = {
        **LINE_THREAD,
        "id": "PRRT_2",
        "isResolved": True,
        "comments": {"nodes": []},
    }
    threads = script.threads_from_pull_request(
        review_threads=[LINE_THREAD, resolved],
        issue_comments=[{"id": 5, "user": {"login": "carol"}, "body": "Looks odd"}],
        reviews=[
            {
                "id": 9,
                "user": {"login": "bob"},
                "state": "COMMENTED",
                "body": "Summary",
            },
            {"id": 10, "user": {"login": "bob"}, "state": "APPROVED", "body": ""},
            {"id": 11, "user": {"login": "bob"}, "state": "PENDING", "body": "draft"},
        ],
    )
    assert [t["id"] for t in threads] == [
        "PRRT_1",
        "PRRT_2",
        "issue-comment-5",
        "review-9",
    ]
    first = threads[0]
    assert first["state"] == "open"
    assert first["messages"] == [
        {"author": "bob", "body": "Why?"},
        {"author": "alice", "body": "Because."},
    ]
    assert threads[1]["state"] == "resolved"
    assert threads[2] == {
        "id": "issue-comment-5",
        "target": {"type": "change"},
        "state": "open",
        "messages": [{"author": "carol", "body": "Looks odd"}],
    }


def test_deleted_users_become_ghost():
    threads = script.threads_from_pull_request(
        review_threads=[],
        issue_comments=[{"id": 1, "user": None, "body": "hi"}],
        reviews=[],
    )
    assert threads[0]["messages"][0]["author"] == "ghost"


def test_threads_are_accepted_by_the_workflow(tmp_path):
    threads = script.threads_from_pull_request(
        review_threads=[LINE_THREAD],
        issue_comments=[{"id": 5, "user": {"login": "carol"}, "body": "x"}],
        reviews=[
            {"id": 9, "user": {"login": "bob"}, "state": "COMMENTED", "body": "y"}
        ],
    )
    path = tmp_path / "threads.json"
    path.write_text(json.dumps(threads))
    assert len(load_threads([path])) == 3


# --- fetching and the review input --------------------------------------------------------


def test_fetch_pull_request_collects_the_details():
    gh = FakeGitHub(comments=[{"id": 5, "user": {"login": "carol"}, "body": "hi"}])
    pr = script.fetch_pull_request(gh, script.parse_ref("acme/app#7"))
    assert (pr.title, pr.author, pr.head_sha, pr.base_sha) == (
        "Add retry",
        "alice",
        SHA_HEAD,
        SHA_BASE,
    )
    assert pr.merge_base == SHA_MERGE_BASE
    assert pr.clone_url == "https://github.com/acme/app.git"
    assert pr.viewer == "review-bot"
    assert [t["id"] for t in pr.threads] == ["PRRT_1", "issue-comment-5"]


def test_fetch_pull_request_tolerates_a_token_that_cannot_name_its_user():
    pr = script.fetch_pull_request(
        FakeGitHub(viewer=None), script.parse_ref("acme/app#7")
    )
    assert pr.viewer is None


def pull_request():
    return script.fetch_pull_request(FakeGitHub(), script.parse_ref("acme/app#7"))


def test_description_is_title_then_body():
    assert (
        script.description_from(pull_request())
        == "Add retry\n\nRetries failed requests."
    )
    bare = {**PR, "body": None}
    assert script.description_from_parts(bare["title"], bare["body"]) == "Add retry"


def test_review_input_is_accepted_by_the_workflow(tmp_path):
    data = script.build_review_input(
        pull_request(),
        code_path=tmp_path / "code",
        threads_path=tmp_path / "threads.json",
        reviewer_name="review-bot",
        instructions=None,
    )
    assert "instructions" not in data and "diffs" not in data
    review_input = ReviewInput.model_validate(data)
    assert review_input.codebase is not None
    assert review_input.codebase.base_commit == SHA_MERGE_BASE
    assert (review_input.change_author, review_input.reviewer_name) == (
        "alice",
        "review-bot",
    )
    assert review_input.discussions == [tmp_path / "threads.json"]
    assert review_input.description and review_input.description.startswith("Add retry")


# --- the GitHub client --------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body, link=None):
        self._body = json.dumps(body).encode()
        self.headers = {"Link": link} if link else {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_client_follows_pages_and_sends_the_token(monkeypatch):
    seen = []
    pages = {
        "https://api.github.com/x?per_page=100": FakeResponse(
            [1, 2], '<https://api.github.com/x?per_page=100&page=2>; rel="next"'
        ),
        "https://api.github.com/x?per_page=100&page=2": FakeResponse([3]),
    }

    def fake_urlopen(request, timeout):
        seen.append((request.full_url, request.get_header("Authorization")))
        return pages[request.full_url]

    monkeypatch.setattr(script, "urlopen", fake_urlopen)
    client = script.GitHub("https://api.github.com", "secret")
    assert client.get_all("/x") == [1, 2, 3]
    assert {auth for _, auth in seen} == {"Bearer secret"}


def test_client_never_follows_a_next_link_to_another_host(monkeypatch):
    page = FakeResponse([1], '<https://evil.example/x>; rel="next"')
    monkeypatch.setattr(script, "urlopen", lambda request, timeout: page)
    with pytest.raises(script.ScriptError, match="another host"):
        script.GitHub("https://api.github.com", "secret").get_all("/x")


def test_client_pages_through_review_threads(monkeypatch):
    def node(i):
        return {**LINE_THREAD, "id": f"PRRT_{i}"}

    responses = iter(
        [
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                "nodes": [node(1)],
                            }
                        }
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [node(2)],
                            }
                        }
                    }
                }
            },
        ]
    )
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data)["variables"].get("after"))
        return FakeResponse(next(responses))

    monkeypatch.setattr(script, "urlopen", fake_urlopen)
    threads = script.GitHub("https://api.github.com", "t").review_threads(
        "acme", "app", 7
    )
    assert [t["id"] for t in threads] == ["PRRT_1", "PRRT_2"]
    assert sent == [None, "c1"]


def test_graphql_errors_are_reported(monkeypatch):
    monkeypatch.setattr(
        script,
        "urlopen",
        lambda request, timeout: FakeResponse({"errors": [{"message": "boom"}]}),
    )
    with pytest.raises(script.ScriptError, match="boom"):
        script.GitHub("https://api.github.com", "t").review_threads("acme", "app", 7)


def test_graphql_url_for_enterprise_servers():
    assert (
        script.graphql_url("https://api.github.com") == "https://api.github.com/graphql"
    )
    assert (
        script.graphql_url("https://ghe.example/api/v3")
        == "https://ghe.example/api/graphql"
    )


def test_find_token_prefers_the_environment():
    assert script.find_token({"GITHUB_TOKEN": "a", "GH_TOKEN": "b"}) == "a"
    assert script.find_token({"GH_TOKEN": "b"}) == "b"


def test_find_token_falls_back_to_gh():
    def run(command, **kwargs):
        assert command == ["gh", "auth", "token"]
        return subprocess.CompletedProcess(command, 0, stdout="from-gh\n", stderr="")

    assert script.find_token({}, run=run) == "from-gh"

    def broken(command, **kwargs):
        raise FileNotFoundError

    assert script.find_token({}, run=broken) is None


# --- the command line ---------------------------------------------------------------------


@pytest.fixture
def fake_main(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(script, "GitHub", lambda api_url, token: gh)
    monkeypatch.setattr(script, "find_token", lambda env=None, run=None: "token")

    def prepare(pr, output, clone):
        (output / "code").mkdir()
        return output / "code"

    monkeypatch.setattr(script, "prepare_code", prepare)

    def write_diffs(pr, code_path, output):
        (output / "diffs").mkdir()
        (output / "diffs" / "a.py.diff").write_text("diff --git a/a.py b/a.py\n")
        return output / "diffs"

    monkeypatch.setattr(script, "write_diffs", write_diffs)
    return gh


def test_main_writes_the_review_input(tmp_path, fake_main, capsys):
    out = tmp_path / "out"
    assert script.main(["acme/app#7", "--output", str(out)]) == 0
    review_input = ReviewInput.model_validate_json(
        (out / "review_input.json").read_text()
    )
    assert review_input.reviewer_name == "review-bot"
    assert (
        review_input.codebase and review_input.codebase.path == out.resolve() / "code"
    )
    assert review_input.diffs == out.resolve() / "diffs"
    assert len(load_threads(review_input.discussions)) == 1
    metadata = json.loads((out / "pull_request.json").read_text())
    assert (metadata["head_sha"], metadata["merge_base"], metadata["author"]) == (
        SHA_HEAD,
        SHA_MERGE_BASE,
        "alice",
    )
    assert "review_input.json" in capsys.readouterr().out


def test_main_options_override_the_defaults(tmp_path, fake_main):
    guidelines = tmp_path / "guidelines.md"
    guidelines.write_text("Prefer small functions.")
    out = tmp_path / "out"
    args = ["acme/app#7", "--output", str(out), "--reviewer-name", "other-bot"]
    assert script.main([*args, "--instructions-file", str(guidelines)]) == 0
    review_input = json.loads((out / "review_input.json").read_text())
    assert (review_input["reviewer_name"], review_input["instructions"]) == (
        "other-bot",
        "Prefer small functions.",
    )


def test_main_refuses_a_non_empty_output_folder(tmp_path, fake_main, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.txt").write_text("x")
    assert script.main(["acme/app#7", "--output", str(out)]) == 1
    assert "not empty" in capsys.readouterr().err
    assert fake_main.calls == []  # nothing was fetched


def test_main_needs_a_token(tmp_path, fake_main, monkeypatch, capsys):
    monkeypatch.setattr(script, "find_token", lambda env=None, run=None: None)
    assert script.main(["acme/app#7", "--output", str(tmp_path / "out")]) == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_main_reports_a_bad_reference(tmp_path, fake_main, capsys):
    assert script.main(["nonsense", "--output", str(tmp_path / "out")]) == 1
    assert "pull request" in capsys.readouterr().err


# --- the code ------------------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    env = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        ["git", *env, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A remote repository with a pull request: refs/pull/7/head is one commit past main."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "a.py").write_text("one\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "base")
    base = git(work, "rev-parse", "HEAD")
    git(work, "checkout", "-q", "-b", "feature")
    (work / "a.py").write_text("one\ntwo\n")
    git(work, "commit", "-qam", "change")
    head = git(work, "rev-parse", "HEAD")
    git(work, "checkout", "-q", "main")
    (work / "other.py").write_text("main moved on\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "main moves")
    bare = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    git(bare, "update-ref", "refs/pull/7/head", head)
    return bare, base, head


def make_pr(clone_url, head, merge_base):
    pr = pull_request()
    return script.PullRequest(
        **{
            **pr.__dict__,
            "clone_url": str(clone_url),
            "head_sha": head,
            "merge_base": merge_base,
        }
    )


@pytest.mark.git
def test_prepare_code_with_a_temporary_clone(tmp_path, remote):
    bare, base, head = remote
    out = tmp_path / "out"
    out.mkdir()
    code = script.prepare_code(make_pr(bare, head, base), out, clone=None)
    assert code == out / "code"
    assert git(code, "rev-parse", "HEAD") == head
    assert (code / "a.py").read_text() == "one\ntwo\n"
    assert not (code / "other.py").exists()
    assert git(code, "status", "--porcelain") == ""
    # the base commit is available, and the change since it is just the pull request's
    assert git(code, "diff", "--name-only", base) == "a.py"


@pytest.mark.git
def test_prepare_code_with_the_users_clone_leaves_their_branches_alone(
    tmp_path, remote
):
    bare, base, head = remote
    mine = tmp_path / "mine"
    git(tmp_path, "clone", "-q", str(bare), str(mine))
    before = git(mine, "for-each-ref")
    out = tmp_path / "out"
    out.mkdir()
    code = script.prepare_code(make_pr(bare, head, base), out, clone=mine)
    assert git(code, "rev-parse", "HEAD") == head
    assert git(mine, "for-each-ref") == before
    assert git(mine, "status", "--porcelain") == ""


@pytest.mark.git
def test_prepare_code_rejects_a_missing_clone_and_a_vanished_head(tmp_path, remote):
    bare, base, head = remote
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(script.ScriptError, match="not a git repository"):
        script.prepare_code(make_pr(bare, head, base), out, clone=tmp_path / "nowhere")
    with pytest.raises(script.ScriptError, match="changed while"):
        script.prepare_code(make_pr(bare, "d" * 40, base), out, clone=None)


def test_review_input_names_the_diffs_folder_when_there_is_one(tmp_path):
    data = script.build_review_input(
        pull_request(),
        code_path=tmp_path / "code",
        diffs_path=tmp_path / "diffs",
        threads_path=tmp_path / "threads.json",
        reviewer_name=None,
        instructions="Be strict.",
    )
    review_input = ReviewInput.model_validate(data)
    assert review_input.diffs == tmp_path / "diffs"
    assert (review_input.instructions, review_input.reviewer_name) == (
        "Be strict.",
        None,
    )


@pytest.mark.git
def test_write_diffs_holds_one_diff_per_changed_file(tmp_path, remote):
    bare, base, head = remote
    out = tmp_path / "out"
    out.mkdir()
    pr = make_pr(bare, head, base)
    code = script.prepare_code(pr, out, clone=None)
    diffs = script.write_diffs(pr, code, out)
    assert diffs == out / "diffs"
    assert sorted(p.relative_to(diffs).as_posix() for p in diffs.rglob("*.diff")) == [
        "a.py.diff"
    ]
    assert "+two" in (diffs / "a.py.diff").read_text()


@pytest.mark.git
def test_the_workflow_reads_the_written_diffs(tmp_path, remote):
    from workflows.code_review.changes import changes_from_diffs

    bare, base, head = remote
    out = tmp_path / "out"
    out.mkdir()
    pr = make_pr(bare, head, base)
    code = script.prepare_code(pr, out, clone=None)
    diffs = script.write_diffs(pr, code, out)
    files = changes_from_diffs(diffs, tmp_path / "copy")
    assert [(f.path, f.status, f.changed_lines) for f in files] == [
        ("a.py", "modified", 1)
    ]


@pytest.mark.git
def test_write_diffs_for_a_pull_request_without_file_changes(tmp_path, remote):
    bare, base, _head = remote
    git(bare, "update-ref", "refs/pull/7/head", base)
    out = tmp_path / "out"
    out.mkdir()
    pr = make_pr(bare, base, base)  # head == base: nothing changed
    code = script.prepare_code(pr, out, clone=None)
    assert list(script.write_diffs(pr, code, out).iterdir()) == []


@pytest.mark.git
async def test_a_prepared_pull_request_can_be_reviewed(tmp_path, remote, monkeypatch):
    # Fake GitHub, a real repository, and the real workflow with a fake harness: the
    # prepared folder is all the workflow needs, with the diffs and the code together.
    import re

    from workflows.code_review import CodeReviewer
    from workflows.harness.testing import FakeHarness

    bare, base, head = remote
    gh = FakeGitHub(base_sha=base, head_sha=head, merge_base=base, clone_url=str(bare))
    monkeypatch.setattr(script, "GitHub", lambda api_url, token: gh)
    monkeypatch.setattr(script, "find_token", lambda env=None, run=None: "token")
    out = tmp_path / "out"
    assert script.main(["acme/app#7", "--output", str(out)]) == 0

    review_input = ReviewInput.model_validate_json(
        (out / "review_input.json").read_text()
    )
    assert review_input.codebase and review_input.diffs
    assert review_input.codebase.base_commit == base

    def respond(request):
        prompt = request.prompt
        [folder] = re.findall(r"^Output folder: (.+)$", prompt, re.MULTILINE)
        out_dir = Path(folder)
        slots = re.findall(r"^Slot (\d+): (.+)$", prompt, re.MULTILINE)
        for n, path in slots:
            result = {"path": path, "summary": "reviewed"}
            (out_dir / f"{n}.json").write_text(json.dumps(result))
        if "# Consolidation" in prompt:
            (out_dir / "result.json").write_text(json.dumps({"status": "approved"}))
        elif not slots:
            (out_dir / "result.json").write_text(json.dumps({"comments": []}))
        return "done"

    harness = FakeHarness(respond)
    result = await CodeReviewer(harness).review(review_input)
    assert result.status == "approved"
    assert [(f.path, f.state) for f in result.coverage.files] == [("a.py", "reviewed")]
    assert {a.agent: a.state for a in result.coverage.agents}["code-patterns"] == "ran"
    assert {r.working_folder for r in harness.requests} == {out.resolve() / "code"}


# --- keeping the token where it belongs ----------------------------------------------------


def test_client_rejects_a_next_link_to_a_lookalike_host(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        assert len(calls) == 1, f"followed the link to {request.full_url}"
        return FakeResponse([1], '<https://api.github.com.evil.example/x>; rel="next"')

    monkeypatch.setattr(script, "urlopen", fake_urlopen)
    with pytest.raises(script.ScriptError, match="another host"):
        script.GitHub("https://api.github.com", "secret").get_all("/x")


def test_redirects_stay_on_the_same_host():
    from urllib.request import Request

    handler = script.SameHostRedirects()
    request = Request(
        "https://api.github.com/a", headers={"Authorization": "Bearer secret"}
    )
    moved = handler.redirect_request(
        request, None, 301, "Moved", {}, "https://api.github.com/b"
    )
    assert moved is not None and moved.full_url == "https://api.github.com/b"
    away = handler.redirect_request(
        request, None, 301, "Moved", {}, "https://evil.example/b"
    )
    assert (
        away is None
    )  # urllib then raises an HTTPError instead of resending the token


def test_the_api_url_follows_the_pull_request_host():
    def api(text, explicit=None):
        return script.api_url_for(script.parse_ref(text), explicit)

    assert api("acme/app#7") == "https://api.github.com"
    assert api("https://github.com/acme/app/pull/7") == "https://api.github.com"
    assert api("https://www.github.com/acme/app/pull/7") == "https://api.github.com"
    assert api("https://ghe.example/acme/app/pull/7") == "https://ghe.example/api/v3"
    assert api(
        "https://ghe.example/acme/app/pull/7", "https://other.example/api/v3"
    ) == ("https://other.example/api/v3")


def test_main_sends_an_enterprise_pull_request_to_its_own_server(
    tmp_path, fake_main, monkeypatch
):
    urls = []
    monkeypatch.setattr(
        script, "GitHub", lambda api_url, token: urls.append(api_url) or fake_main
    )
    args = ["https://ghe.example/acme/app/pull/7", "--output", str(tmp_path / "out")]
    assert script.main(args) == 0
    assert urls == ["https://ghe.example/api/v3"]


def test_main_reports_an_unreadable_instructions_file(tmp_path, fake_main, capsys):
    args = ["acme/app#7", "--output", str(tmp_path / "out")]
    assert (
        script.main([*args, "--instructions-file", str(tmp_path / "missing.md")]) == 1
    )
    assert "missing.md" in capsys.readouterr().err
