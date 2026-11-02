# Code Change: Requirements

Date: 2026-11-02

## Purpose

Apply one instruction to a codebase and commit the result. The change is
already decided by whoever wrote the instruction: fixing an issue Sonar
raised, or a Jira item whose change is known. The workflow doesn't plan or
design. The instruction is the spec.

The workflow doesn't know anything about platforms or team practice. End
products such as a Sonar fixer or a Jira bot turn their own data into an
instruction, and push the commits and raise a merge request themselves.

### What belongs here

A task belongs here when a reviewer can check the change against the
instruction alone, without asking whether it was the right approach. A task
that needs a design decision belongs to a human, or to a future planning
workflow.

### What a good change looks like

A change is accepted by default. It is rejected when it is complex or fails a
check. So the agent makes the smallest change that does what the instruction
asks:

- everything the instruction asks for, and nothing else;
- no unrelated refactoring, renaming or reformatting;
- passing every configured check.

## Input

One run handles one instruction. Everything below is set per run.

### Codebase

- A path to a git work tree.
- The work tree must be clean: no uncommitted changes and no untracked files.
  Git-ignored files don't count.
- The commit checked out when the run starts is the **base commit**. The
  workflow commits on whatever is checked out. Creating a branch is the end
  product's job.

### Instruction

- Free text describing the change.
- It may include guidance on testing and validation, for example "add a unit
  test for the empty case". The agent follows it like the rest of the
  instruction.

### Commit instructions

- Optional free text: the commit message template or rules.

### Checks

- An optional list of shell commands, run by workflow code, not the agent.
- Each runs from the codebase folder. Exit code 0 is a pass.
- Checks are separate from any testing guidance in the instruction. Guidance
  tells the agent what to do. Checks are what the workflow verifies itself.

### Review

- On or off. Off by default.

### Max tries

- The total number of tries, the first one included. Default 3.

### Reset on failure

- On by default. See Resetting.

## Agents

- The end product picks the agents and models and passes them in. The
  workflow never chooses a model.
- One required **change agent**.
- An optional **review agent**. Without one, the change agent also does the
  review.
- Every agent must be able to write result files into a folder the workflow
  chooses, as in the code reviewer.

## How a run works

Each try runs these stages in order. A stage that fails ends the try. If the
agent reports that it needs human input, the run ends there and no later
stage runs.

| Stage | Done by | Job |
|---|---|---|
| **Change** | Agent | Makes the change and commits it, in one or more commits, following the commit instructions. Reports either a title and description of what changed and why, or that it needs human input and why. |
| **Commit check** | Code | Confirms that HEAD has moved past the base commit, that the base commit is still an ancestor of HEAD, and that nothing is left uncommitted. |
| **Checks** | Code | Runs every configured command and records each one's exit code and output. |
| **Review** | Agent, optional | A spec review: compares the commits since the base with the instruction. |

### The review

The review asks three questions:

- Does the change do everything the instruction asks?
- Does it do anything the instruction didn't ask for?
- Did it follow the instruction's testing and validation guidance?

It doesn't judge code style or quality. That's the code reviewer's job, and an
end product that wants it runs the code reviewer on the commits afterwards.

Its verdict is one of:

- **pass**,
- **changes needed**, with comments,
- **inconclusive**: it couldn't decide.

### Tries

- If the commit check fails, a check fails, or the review asks for changes,
  and tries remain, a new try starts.
- The agent gets what went wrong: the commit check's reason, the failing
  checks' output, or the review's comments.
- It fixes the problem with new commits on top of its earlier ones. It doesn't
  amend or rewrite them.
- Commits from all tries together form the change.

### Ending a run

| Status | When |
|---|---|
| `completed` | Every stage passed. |
| `needs_human` | The agent stopped the run on any try because the change needs human input: a design choice, a change much larger than it looked, an instruction that doesn't make sense against the code, or nothing to change. |
| `inconclusive` | The review couldn't decide. The run stops at once, even with tries left. |
| `failed` | The last try still failed the commit check, a check or the review. |

Failures such as invalid input or an agent crash raise errors and are never
reported as a status.

### Resetting

- When a run ends in any status but `completed`, the workflow resets the work
  tree to the base commit. It removes the run's commits and any untracked
  files left behind (git-ignored files stay), so the end product gets back a
  clean starting point.
- With reset on failure turned off, the commits and files stay, for looking
  into why the change didn't work.
- The same applies when the run raises an error after the agent has started.

## Output

The output is returned as Python objects through the API and can be
converted to JSON.

- **status**: one of the statuses above.
- **reason**: why the run needed a human, was inconclusive or failed. Empty
  when completed.
- **commits**: the commits since the base, oldest first, each with its id and
  message. Empty after a reset.
- **title** and **description**: the agent's account of what changed and why,
  from its last try, written so an end product can use them for a merge
  request.
- **attempts**: what happened on each try:
  - the stage it ended at and why;
  - each check's command, exit code and output;
  - the review's verdict and comments.

### Events

Like the code reviewer, the workflow takes an optional callback that receives
every agent event, labeled with its stage and try number.

## Decided by the user

- The name is `code-change`.
- One run handles one instruction.
- The agent commits, in as many commits as it likes. The workflow checks the
  result.
- Pushing and raising merge requests are left to the end product.
- The description of the change comes back in the result, not in a file.
- A change that needs human input stops the whole run.
- Checks and review are optional and set per run. A failure in either gets
  another try, up to 3 tries in total by default.
- An inconclusive review stops the run.
- The review is a spec review against the instruction, not a code review.
- Resetting to the base commit when a run doesn't complete can be turned off.

## Claude's calls (overridable)

- The work tree must be clean when the run starts, so a reset can't destroy
  someone's work.
- A failed commit check (no commit, or work left uncommitted) uses a try,
  just like a failed check.
- Every check runs on every try, even after one fails, so the agent sees every
  failure at once.
- Checks and review share one try budget.
- Fixes go in as new commits, not amendments.
- An instruction that needs no change, for example a false positive or
  something already fixed, ends as `needs_human` with the reason. A human
  decides whether to close the issue.
- Checks aren't run on the base commit first. If they already fail there,
  every try will fail. Making sure checks pass on the base is the end
  product's job.
- Review is off by default, and the review agent defaults to the change
  agent.
- A reset also runs when the workflow raises an error after the agent has
  started.

## Out of scope for now

- Pushing, branches and merge requests.
- More than one instruction per run.
- Planning or design work.
- Code-quality review. End products can run the code reviewer separately.
- Two runs on the same work tree at the same time.

## Still to design

- The Python API surface.
- How the agent reports back (a result file and its schema).
- Prompts for the change and review stages.
- Timeouts for agent calls and for checks, and how much check output is kept
  and passed back to the agent.
