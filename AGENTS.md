# AGENTS.md

A Python monorepo of reusable workflows, such as code review, that end
products integrate through a Python API. Each workflow has a fixed input and
output format. Every workflow and every harness implementation ships as its
own installable package.

## Layout

- `packages/harnesses/core/`: the generic harness interface.
- `packages/harnesses/<tool>/`: one harness implementation per tool
  (Claude Code, opencode, Codex).
- `packages/workflows/<name>/`: one package per workflow.

## Rules

- A workflow depends only on the harness interface, never on a concrete
  harness package.
- Workflows know nothing about deployment or platforms (GitLab, CI, IDEs) or
  about team practice. Anything team- or platform-specific arrives as input.
- Don't extract shared code ahead of time. Decide what to share once a second
  workflow actually needs it.

## Commits

- Keep commit messages short and simple.
- No AI or Claude attribution trailers.
