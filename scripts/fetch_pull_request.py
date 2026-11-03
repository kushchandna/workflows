"""Fetch a GitHub pull request and prepare it as input for the code review workflow.

    uv run python scripts/fetch_pull_request.py https://github.com/OWNER/REPO/pull/123 --output DIR

It writes, into DIR:

    review_input.json   the fields of ReviewInput: the code, the diffs, base commit,
                        description, author, reviewer name and the discussions file
    threads.json        the pull request's discussions, in the workflow's format
    diffs/              one git diff per changed file, as <path>.diff
    pull_request.json   the pull request's details, for reference
    code/               a detached git worktree at the pull request's head
    clone/              the clone code/ belongs to (only without --clone)

and does not run the review. Load the input and call the workflow yourself:

    ReviewInput.model_validate_json(Path("DIR/review_input.json").read_text())

The base commit is the merge base of the pull request, so the review covers what the pull
request changes. The diffs are written by the workflow's own change-list code, so run the script
with `uv run` from this repository. Discussions come from review threads (with their resolved state), pull
request conversation comments and review summaries. Conversation comments and review
summaries cannot be resolved on GitHub, so they are always open.

The GitHub API is called with a token from GITHUB_TOKEN, GH_TOKEN or `gh auth token`. Git
uses your own credentials (credential helper or SSH) to clone and fetch; the token is never
passed to it. A pull request URL on another host (GitHub Enterprise) is sent to that host's
API, https://HOST/api/v3, and never to github.com; --api-url or GITHUB_API_URL overrides it.
Redirects are followed only within one host, so the token is never resent elsewhere.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_API_URL = "https://api.github.com"

REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line startLine originalLine originalStartLine
          diffSide subjectType
          comments(first: 100) { totalCount nodes { author { login } body } }
        }
      }
    }
  }
}
"""


class SameHostRedirects(HTTPRedirectHandler):
    """Follow redirects only within one host, so the token is never resent elsewhere."""

    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Request | None:
        if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
            return None  # urllib then raises the redirect as an HTTPError
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(SameHostRedirects)


def urlopen(request: Request, timeout: float) -> Any:
    return _OPENER.open(request, timeout=timeout)


class ScriptError(Exception):
    """Something the user can fix; reported without a traceback."""


@dataclass(frozen=True)
class PullRef:
    owner: str
    repo: str
    number: int
    host: str | None = None  # the host in the URL, if it was a URL


@dataclass(frozen=True)
class PullRequest:
    ref: PullRef
    title: str
    body: str | None
    author: str
    url: str
    state: str
    draft: bool
    base_ref: str
    base_sha: str
    head_sha: str
    merge_base: str
    clone_url: str
    viewer: str | None  # the login the token belongs to, if it can say
    threads: list[dict[str, Any]]


# --- references ----------------------------------------------------------------------------


def parse_ref(text: str) -> PullRef:
    """A pull request URL (.../OWNER/REPO/pull/N) or ``OWNER/REPO#N``."""
    match = re.fullmatch(
        r"(?:https?://([^/]+)/)?([\w.-]+)/([\w.-]+)(?:/pull/|#)(\d+)(?:/.*)?",
        text.strip(),
    )
    if not match:
        raise ScriptError(
            f"not a pull request: {text!r} (use https://github.com/OWNER/REPO/pull/N or OWNER/REPO#N)"
        )
    host, owner, repo, number = match.groups()
    return PullRef(owner, repo, int(number), host)


def api_url_for(ref: PullRef, explicit: str | None) -> str:
    """The API to call. A pull request on another host goes to that host, never to github.com,
    so a token for one server is not sent to another."""
    if explicit:
        return explicit
    if ref.host is None or ref.host in ("github.com", "www.github.com"):
        return DEFAULT_API_URL
    return f"https://{ref.host}/api/v3"


# --- the GitHub API ------------------------------------------------------------------------


def graphql_url(api_url: str) -> str:
    if api_url.endswith("/api/v3"):  # GitHub Enterprise Server
        return api_url.removesuffix("/api/v3") + "/api/graphql"
    return api_url.rstrip("/") + "/graphql"


def find_token(
    env: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    env = os.environ if env is None else env
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if env.get(name):
            return env[name]
    try:
        done = run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


class GitHub:
    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self._token = token

    def _send(self, url: str, body: Any = None) -> tuple[Any, Any]:
        request = Request(
            url,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "workflows-fetch-pull-request",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")), response.headers
        except HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise ScriptError(f"GitHub API {e.code} for {url}: {detail}") from e
        except URLError as e:
            raise ScriptError(f"cannot reach GitHub at {url}: {e.reason}") from e

    def get(self, path: str) -> Any:
        return self._send(self.api_url + path)[0]

    def get_all(self, path: str) -> list[Any]:
        """Every page of a list endpoint."""
        url = self.api_url + path + ("&" if "?" in path else "?") + "per_page=100"
        items: list[Any] = []
        while url:
            page, headers = self._send(url)
            items += page
            link = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link") or "")
            url = link.group(1) if link else ""
            if url and not url.startswith(self.api_url + "/"):
                raise ScriptError(
                    f"refusing to follow a next link to another host: {url}"
                )
        return items

    def review_threads(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        threads: list[dict[str, Any]] = []
        after = None
        while True:
            variables = {"owner": owner, "repo": repo, "number": number, "after": after}
            data, _ = self._send(
                graphql_url(self.api_url),
                {"query": REVIEW_THREADS_QUERY, "variables": variables},
            )
            if data.get("errors"):
                messages = "; ".join(e.get("message", "?") for e in data["errors"])
                raise ScriptError(f"GitHub GraphQL error: {messages}")
            pull = (data.get("data") or {}).get("repository", {}) or {}
            pull = pull.get("pullRequest")
            if pull is None:
                raise ScriptError(f"pull request not found: {owner}/{repo}#{number}")
            page = pull["reviewThreads"]
            threads += page["nodes"]
            for node in page["nodes"]:
                comments = node.get("comments") or {}
                if comments.get("totalCount", 0) > len(comments.get("nodes", [])):
                    print(
                        f"warning: thread {node['id']} has more than 100 comments; "
                        "only the first 100 are used",
                        file=sys.stderr,
                    )
            if not page["pageInfo"]["hasNextPage"]:
                return threads
            after = page["pageInfo"]["endCursor"]


# --- discussions -----------------------------------------------------------------------------


def _author(user: Mapping[str, Any] | None) -> str:
    return (user or {}).get("login") or "ghost"  # a deleted account has no login


def target_from_review_thread(node: Mapping[str, Any]) -> dict[str, Any]:
    path = node.get("path")
    if not path:
        return {"type": "change"}
    line = node.get("line") or node.get(
        "originalLine"
    )  # an outdated thread only has the original
    if node.get("subjectType") == "FILE" or not line:
        return {"type": "file", "path": path}
    start = node.get("startLine") or node.get("originalStartLine") or line
    return {
        "type": "lines",
        "path": path,
        "start_line": min(start, line),
        "end_line": max(start, line),
        "side": "old" if node.get("diffSide") == "LEFT" else "new",
    }


def threads_from_pull_request(
    *,
    review_threads: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The pull request's discussions in the code review workflow's thread format."""
    threads: list[dict[str, Any]] = []
    for node in review_threads:
        messages = [
            {"author": _author(c.get("author")), "body": c.get("body") or ""}
            for c in (node.get("comments") or {}).get("nodes", [])
        ]
        threads.append(
            {
                "id": node["id"],
                "target": target_from_review_thread(node),
                "state": "resolved" if node.get("isResolved") else "open",
                "messages": messages,
            }
        )
    for comment in issue_comments:
        threads.append(
            _change_thread(
                f"issue-comment-{comment['id']}",
                comment.get("user"),
                comment.get("body"),
            )
        )
    for review in reviews:
        if review.get("state") == "PENDING" or not (review.get("body") or "").strip():
            continue
        threads.append(
            _change_thread(f"review-{review['id']}", review.get("user"), review["body"])
        )
    return threads


def _change_thread(
    thread_id: str, user: Mapping[str, Any] | None, body: str | None
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "target": {"type": "change"},
        "state": "open",  # GitHub cannot resolve these
        "messages": [{"author": _author(user), "body": body or ""}],
    }


# --- the pull request ----------------------------------------------------------------------


def fetch_pull_request(gh: Any, ref: PullRef) -> PullRequest:
    base = f"/repos/{ref.owner}/{ref.repo}"
    pr = gh.get(f"{base}/pulls/{ref.number}")
    base_sha, head_sha = pr["base"]["sha"], pr["head"]["sha"]
    merge_base = gh.get(f"{base}/compare/{base_sha}...{head_sha}")["merge_base_commit"][
        "sha"
    ]
    try:
        viewer: str | None = gh.get("/user")["login"]
    except ScriptError:  # for example an app token, which has no user
        viewer = None
    threads = threads_from_pull_request(
        review_threads=gh.review_threads(ref.owner, ref.repo, ref.number),
        issue_comments=gh.get_all(f"{base}/issues/{ref.number}/comments"),
        reviews=gh.get_all(f"{base}/pulls/{ref.number}/reviews"),
    )
    return PullRequest(
        ref=ref,
        title=pr["title"],
        body=pr.get("body"),
        author=_author(pr.get("user")),
        url=pr["html_url"],
        state=pr["state"],
        draft=bool(pr.get("draft")),
        base_ref=pr["base"]["ref"],
        base_sha=base_sha,
        head_sha=head_sha,
        merge_base=merge_base,
        clone_url=pr["base"]["repo"]["clone_url"],
        viewer=viewer,
        threads=threads,
    )


def description_from_parts(title: str, body: str | None) -> str:
    return f"{title}\n\n{body or ''}".strip()


def description_from(pr: PullRequest) -> str:
    return description_from_parts(pr.title, pr.body)


def build_review_input(
    pr: PullRequest,
    *,
    code_path: Path,
    threads_path: Path,
    reviewer_name: str | None,
    instructions: str | None,
    diffs_path: Path | None = None,
) -> dict[str, Any]:
    """The fields of ``workflows.code_review.ReviewInput``, as JSON-ready values."""
    data = {
        "codebase": {"path": str(code_path), "base_commit": pr.merge_base},
        "diffs": None if diffs_path is None else str(diffs_path),
        "discussions": [str(threads_path)],
        "description": description_from(pr),
        "instructions": instructions,
        "change_author": pr.author,
        "reviewer_name": reviewer_name,
    }
    return {key: value for key, value in data.items() if value is not None}


# --- the code --------------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise ScriptError("git is not installed") from e
    if check and done.returncode != 0:
        raise ScriptError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done


def prepare_code(pr: PullRequest, output: Path, clone: Path | None) -> Path:
    """A detached worktree of the pull request's head at ``output/code``.

    The head is fetched from ``refs/pull/N/head`` of the repository the pull request is in,
    into ``clone`` or, without one, into a new clone at ``output/clone``. Nothing in a given
    clone changes except its object store and its list of worktrees.
    """
    if clone is None:
        repo = output / "clone"
        _git(output, "clone", "--no-checkout", "--quiet", "--", pr.clone_url, str(repo))
    else:
        repo = clone
        if (
            not repo.is_dir()
            or _git(repo, "rev-parse", "--git-dir", check=False).returncode != 0
        ):
            raise ScriptError(f"not a git repository: {repo}")
    _git(repo, "fetch", "--quiet", pr.clone_url, f"refs/pull/{pr.ref.number}/head")
    if (
        _git(
            repo, "cat-file", "-e", f"{pr.head_sha}^{{commit}}", check=False
        ).returncode
        != 0
    ):
        raise ScriptError(
            f"the pull request changed while it was being fetched (head {pr.head_sha} is gone); "
            "run the script again"
        )
    if (
        _git(
            repo, "cat-file", "-e", f"{pr.merge_base}^{{commit}}", check=False
        ).returncode
        != 0
    ):
        raise ScriptError(
            f"the merge base {pr.merge_base} is not in {repo}; it may be a shallow clone, "
            "run `git fetch --unshallow` there"
        )
    code = output / "code"
    _git(repo, "worktree", "add", "--detach", "--quiet", str(code), pr.head_sha)
    return code


def write_diffs(pr: PullRequest, code_path: Path, output: Path) -> Path:
    """One ``<path>.diff`` per file the pull request changes, in ``output/diffs``.

    The worktree is clean at the pull request's head, so the change since the merge base is
    exactly the pull request's. The workflow's own change-list code writes them, so they look
    the same as the diffs it would build itself.
    """
    try:
        from workflows.code_review import Codebase
        from workflows.code_review.gitchanges import changes_from_git
    except ImportError as e:
        raise ScriptError(
            "the code review workflow is not installed; run this script with `uv run` "
            "from the repository"
        ) from e
    diffs = output / "diffs"
    changes_from_git(Codebase(path=code_path, base_commit=pr.merge_base), diffs)
    diffs.mkdir(exist_ok=True)  # a pull request that changes no files
    return diffs


# --- the command line ------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a GitHub pull request for code review."
    )
    parser.add_argument(
        "pull_request", help="https://github.com/OWNER/REPO/pull/N or OWNER/REPO#N"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="folder to write; must be empty or missing",
    )
    parser.add_argument(
        "--clone",
        type=Path,
        help="a local clone of the repository (default: a new clone in OUTPUT/clone)",
    )
    parser.add_argument(
        "--reviewer-name",
        help="name the reviewer's comments appear under (default: the token's user)",
    )
    parser.add_argument(
        "--instructions-file",
        type=Path,
        help="review instructions to include, such as team guidelines",
    )
    parser.add_argument(
        "--api-url",
        help="the API to call (default: GITHUB_API_URL, or the pull request URL's host)",
    )
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except ScriptError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    ref = parse_ref(args.pull_request)
    output: Path = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ScriptError(f"output folder is not empty: {output}")
    instructions = None
    if args.instructions_file:
        try:
            instructions = args.instructions_file.read_text(encoding="utf-8")
        except OSError as e:
            raise ScriptError(f"cannot read {args.instructions_file}: {e}") from e
    token = find_token(os.environ)
    if not token:
        raise ScriptError(
            "no GitHub token: set GITHUB_TOKEN or log in with `gh auth login`"
        )

    api_url = api_url_for(ref, args.api_url or os.environ.get("GITHUB_API_URL"))
    pr = fetch_pull_request(GitHub(api_url, token), ref)
    reviewer_name = args.reviewer_name or pr.viewer
    if reviewer_name is None:
        print(
            "warning: the token has no user; pass --reviewer-name so the reviewer "
            "recognises its earlier comments",
            file=sys.stderr,
        )

    output.mkdir(parents=True, exist_ok=True)
    clone = args.clone.resolve() if args.clone else None
    code_path = prepare_code(pr, output, clone)
    diffs_path = write_diffs(pr, code_path, output)

    threads_path = output / "threads.json"
    _write_json(threads_path, pr.threads)
    _write_json(
        output / "pull_request.json",
        {
            "url": pr.url,
            "number": ref.number,
            "title": pr.title,
            "state": pr.state,
            "draft": pr.draft,
            "author": pr.author,
            "base_ref": pr.base_ref,
            "base_sha": pr.base_sha,
            "head_sha": pr.head_sha,
            "merge_base": pr.merge_base,
            "clone_url": pr.clone_url,
            "viewer": pr.viewer,
        },
    )
    _write_json(
        output / "review_input.json",
        build_review_input(
            pr,
            code_path=code_path,
            diffs_path=diffs_path,
            threads_path=threads_path,
            reviewer_name=reviewer_name,
            instructions=instructions,
        ),
    )
    print(f"Prepared {ref.owner}/{ref.repo}#{ref.number} ({pr.title!r}) in {output}")
    changed = sum(1 for _ in diffs_path.rglob("*.diff"))
    print(
        f"  {changed} changed file(s), {len(pr.threads)} discussion thread(s), "
        f"base {pr.merge_base[:12]}"
    )
    print(f"  code at {code_path}")
    print("Review it with:")
    print("  from pathlib import Path")
    print("  from workflows.code_review import ReviewInput")
    print(
        f"  ReviewInput.model_validate_json(Path({str(output / 'review_input.json')!r}).read_text())"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
