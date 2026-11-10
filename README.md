# workflows

Reusable workflows, such as code review, that end products integrate through a Python API.
Each workflow has a fixed input and output format. A workflow knows nothing about where its
input came from or where its output goes: GitLab, CI systems and IDEs belong to the end
products that call it.

Workflows that need an AI model take a generic harness interface. A harness is a tool, such as
Claude Code, that runs a model as a coding agent: it gives the model tools and runs the loop.
The end product picks a concrete harness (Claude Code today; opencode and Codex are planned)
and passes it in.

## Packages

Every workflow and every harness implementation is its own installable package.

| Package | Import | What it is |
|---|---|---|
| [`workflows-harness`](packages/harnesses/core/README.md) | `workflows.harness` | The generic harness interface, its request, result and event types, and a fake harness for tests. |
| [`workflows-harness-claude-code`](packages/harnesses/claude-code/README.md) | `workflows.harness_claude_code` | A harness that runs the Claude Code CLI. |
| [`workflows-code-review`](packages/workflows/code-review/README.md) | `workflows.code_review` | Reviews a change and returns a status, comments, thread actions and a coverage report. |
| [`workflows-code-change`](packages/workflows/code-change/README.md) | `workflows.code_change` | Applies one instruction to a codebase, commits it and returns a status, the commits and a description. |

```
packages/
  harnesses/
    core/          workflows-harness
    claude-code/   workflows-harness-claude-code
  workflows/
    code-review/   workflows-code-review
    code-change/   workflows-code-change
scripts/           checks that run across packages
tests/             tests that need a real harness (opt-in)
```

## Rules

- A workflow depends only on the harness interface (`workflows-harness`), never on a concrete
  harness package.
- Workflows know nothing about deployment or platforms. Anything team- or platform-specific
  arrives as input.
- Shared code is extracted only when a second workflow needs it.

## Development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The repository is a uv workspace.

```bash
uv sync                                      # install every package and the dev tools
uv run pytest                                # unit tests
uv run pytest -m git                         # tests that run git (opt-in; slower)
WORKFLOWS_INTEGRATION=1 uv run pytest tests/integration   # runs the real claude CLI (costs money)
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run python scripts/check_isolated_installs.py   # each package alone, in a clean Python 3.11 venv
```

## Scripts

- `scripts/check_isolated_installs.py` installs and tests each package alone, in a clean
  Python 3.11 environment.
- `scripts/fetch_pull_request.py` prepares a GitHub pull request for the code review workflow.
  It fetches the pull request's details and discussions, checks out its head, and writes a
  folder that holds a ready-to-load `ReviewInput`. It does not run the review.

  ```bash
  export GITHUB_TOKEN=...   # or log in with `gh auth login`
  uv run python scripts/fetch_pull_request.py https://github.com/OWNER/REPO/pull/123 --output review/pr-123
  ```

  ```python
  from pathlib import Path

  from workflows.code_review import ReviewInput

  review_input = ReviewInput.model_validate_json(
      Path("review/pr-123/review_input.json").read_text()
  )
  ```

  Pass `--clone PATH` to reuse a local clone, `--reviewer-name` to name the bot account, and
  `--instructions-file` to add team guidelines. Run it with `--help` for all options.

The import prefix `workflows` and the distribution prefix `workflows-` are placeholders that
will be renamed together before anything is published.
