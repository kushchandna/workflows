# Project Structure Design

Date: 2026-10-31
Status: Approved

## Intent

A Python project hosting multiple reusable **workflows** that end products
integrate through a Python API. Each workflow has a predecided input format
and output format, and knows nothing about where its input came from or where
its output goes.

Example: a code-review workflow takes a diff and returns review comments. A
GitLab MR bot, an IDE plugin reviewing uncommitted changes, or a CLI can all
use it; each end product does its own translation from its world (MR, working
tree) into the workflow's input, and from the workflow's output back (MR
comments, editor annotations).

Workflows anticipated over time (not all development-related):

- Code review — diff in, review comments out. **Built first.**
- Small bug fixer — bug + code in, patch out.
- Planned coder — long-running, interactive: plans requirements with the
  user, splits into milestones, implements, runs tests and UI tests.
- Book purchase advisor — fetches a book list from a URL, decides which to buy.

## Goals

- Each workflow is independently installable as its own Python package.
- Each workflow exposes a Python API with explicit, typed input and output
  schemas.
- Workflows needing agentic processing accept a **generic agent interface**;
  the end product chooses and supplies the concrete agent.
- Initial agent implementations: Claude Code, opencode, Codex — each its own
  package in this repo.

## Non-goals

- A shared workflow framework or base classes. Code review is built
  standalone; when the second workflow is built, we decide then what (if
  anything) to extract into a shared package.
- A common API shape across workflows (e.g. a uniform `run()` or a uniform
  interaction/listener protocol). Each workflow designs its own API.
- Integrations with specific platforms (GitLab, GitHub, CI systems, IDEs).
  Those belong to end products.

## Anti-goals

- Any workflow package containing knowledge of a deployment environment or a
  team's practice (GitLab, MR conventions, a team's style guide). Team-specific
  rules and conventions arrive as **input**, never as baked-in behavior.
- A workflow package depending on a concrete agent package. Workflows depend
  only on the agent interface.

## Repository layout

```
workflows/                          # repo root — uv workspace, not published
├── pyproject.toml                  # workspace definition + shared dev tooling
├── packages/
│   ├── agents/
│   │   ├── core/                   # dist: workflows-agent              import: workflows_agent
│   │   ├── claude-code/            # dist: workflows-agent-claude-code  import: workflows_agent_claude_code
│   │   ├── opencode/               # dist: workflows-agent-opencode     import: workflows_agent_opencode
│   │   └── codex/                  # dist: workflows-agent-codex        import: workflows_agent_codex
│   └── workflows/
│       └── code-review/            # dist: workflows-code-review        import: workflows_code_review
│           ├── pyproject.toml
│           ├── src/workflows_code_review/
│           │   ├── __init__.py     # public API
│           │   ├── models.py       # input/output schemas
│           │   └── prompts/
│           └── tests/
└── docs/
```

Every package follows the same shape: its own `pyproject.toml`, a `src/`
layout, and its own `tests/`.

### Dependency rules

```
workflows-code-review ──► workflows-agent ◄── workflows-agent-claude-code
                                          ◄── workflows-agent-opencode
                                          ◄── workflows-agent-codex
```

- `workflows-agent` defines the generic agent interface only. It is the one
  shared package that exists from day one, because the user explicitly asked
  for it.
- Agent implementation packages depend on `workflows-agent` and implement its
  interface for one tool.
- Workflow packages depend on `workflows-agent` and never on an
  implementation package.
- End products install a workflow plus one agent implementation, construct
  the agent, and pass it to the workflow.

### Future workflows

Each new workflow is added as `packages/workflows/<name>/` with distribution
`workflows-<name>` and import `workflows_<name>`. Code extracted when a second
workflow is built goes into its own package under `packages/`; where exactly
is decided at that point.

## Interaction with end products

- One-shot workflows (code review): call, get result.
- Interactive workflows (planned coder): the end product registers a listener
  on a running workflow instance; questions to the user flow through it and
  answers come back. Each interactive workflow designs this for itself; no
  common protocol now.

## Decisions

Decided by the user:

- Separate installable package per workflow.
- Separate package per agent implementation, all in this repo.
- Workflows take a generic agent interface; initial implementations are
  Claude Code, opencode, Codex.
- No premature commonality: build code review standalone, extract later.
- Interactive workflows use per-instance listeners registered by the end
  product; no common API for now.
- Workflows are agnostic of deployment and team practice.
- No outside consumers until publishing. Packages depend on each other
  through plain uv workspace sources (`[tool.uv.sources] ... workspace =
  true`), with no git-URL or index-based dependency declarations.
- No CI for now. The root-level isolated install check script covers
  independent installability.
- Each package has its own top-level import (`workflows_agent`,
  `workflows_code_review`), not a shared `workflows.*` namespace. This keeps
  paths short (`packages/agents/core/src/workflows_agent/`) and avoids the
  fragility of a shared PEP 420 namespace. Mapping folders to dotted names at
  build time was tried and rejected because type checkers and IDEs can't
  resolve such imports in editable installs.

Claude's calls (overridable):

- uv workspace for the monorepo; Python 3.11+.
- Pydantic (v2) for input/output schemas — also yields JSON Schema for
  non-Python consumers.
- Each package versioned independently.
- Shared dev tooling at the root: ruff, pytest, and a type checker.
- The `workflows_` import prefix is a placeholder, just like the
  distribution prefix. Both get renamed together, before the first external
  consumer exists.
- Testing and the dependency rule:
  - Each workflow's own `tests/` use a fake agent built against the
    interface. Even test dependencies on concrete agent packages are not
    allowed.
  - End-to-end tests against real agents (Claude Code, opencode, Codex) live
    at the repo root in `tests/integration/`.
- Isolated install check: the uv workspace shares one venv, which can hide
  undeclared dependencies. A root-level script installs and tests each
  package in its own clean environment. It catches a workflow importing a
  concrete agent package, or a package missing a dependency it declares.

## Deferred

- **Distribution and import prefixes.** `workflows-` and `workflows_` are
  placeholders. A distinctive name will be chosen before
  publishing.
- **CI and repo hosting.**
- **Agent interface shape** — designed alongside the code-review workflow.
- **Code-review input/output schemas** — next design topic.
- **Publishing target** (public PyPI vs. private index).
