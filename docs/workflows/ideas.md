# Workflow Ideas

## Why this project exists

While building a GitLab merge request reviewer and a Sonar issue fixer, most of the effort went into
deployment and process, not into the quality of the agentic work itself. This project separates the
two: each workflow focuses only on doing its agentic job well, behind a fixed input and output, and
the end products (the MR bot, the Sonar fixer, and others) integrate it and handle the platform side.

Thin workflows are fine. A workflow is worth having when it gives end products a fixed contract,
even if workflow code guarantees little beyond that.

## Built

| Name | What it does | Why it suits a workflow |
|---|---|---|
| `code-review` | Reviews a change and returns a status, comments, thread actions and coverage. | Code guarantees every changed file is reviewed, and validates every comment. |
| `code-change` | Applies one decided instruction to a codebase and commits it. | Code checks the commits, runs the checks, retries, and resets on failure. |

## Ideas

| Name | What it does | Why it suits a workflow |
|---|---|---|
| `review-response` | Acts on review comments: changes the code, replies, or pushes back. | Reuses the `code-review` thread format, and code can check that every thread got an outcome. |
| `ci-fix` | Fixes a failing pipeline from the failing command and its log. | Re-running the failing command is a built-in check of the fix. |
| `bug-repro` | Turns a bug report into a committed test that fails for the right reason. | Code can verify the test fails on the base commit. |
| `test-writer` | Adds tests for given files or for gaps in a coverage report. | Code can verify the new tests pass and touch the target. |
| `triage` | Decides whether a ticket is ready for `code-change`, needs a plan, or is unclear. | A structured verdict routes work between the other workflows, and code can check it is consistent. |
| `feature-plan` | Plans a feature for human approval, then implements the approved plan. | The human gate between plan and code is a fixed two-phase contract. |
| `change-description` | Writes a merge request title, description and test notes from a change. | A fixed output shape every end product can post as is. |
| `docs-sync` | Updates READMEs, API docs and the changelog to match a change. | Produces commits through the same checked, resettable path as `code-change`. |
| `codebase-question` | Answers a question about a codebase with file and line citations. | Code can validate that every citation points to real lines, and that nothing was changed. |
