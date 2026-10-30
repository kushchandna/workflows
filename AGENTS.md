# AGENTS.md

A Python monorepo of reusable workflows, such as code review, that end
products integrate through a Python API. Each workflow has a fixed input and
output format. Every workflow and every agent implementation ships as its own
installable package.

## Layout

- `packages/agents/core/`: the generic agent interface.
- `packages/agents/<tool>/`: one agent implementation per tool (Claude Code,
  opencode, Codex).
- `packages/workflows/<name>/`: one package per workflow.

## Rules

- A workflow depends only on the agent interface, never on a concrete agent
  package.
- Workflows know nothing about deployment or platforms (GitLab, CI, IDEs) or
  about team practice. Anything team- or platform-specific arrives as input.
- Don't extract shared code ahead of time. Decide what to share once a second
  workflow actually needs it.

## Commits

- Keep commit messages short and simple.
- No AI or Claude attribution trailers.
