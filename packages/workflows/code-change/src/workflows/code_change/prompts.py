"""Prompts for the change and review stages, filled from the templates in ``prompts/``.

The first lines are fixed because the code and the tests rely on them: ``Result file:`` and,
for a change, ``Attempt:``. Text from the end product and output from checks is indented, so a
line inside it can never look like a fixed line.
"""

from importlib.resources import files
from pathlib import Path
from string import Template

from workflows.code_change.models import Attempt, ChangeStage, CheckRun, Commit

_NONE = "(none)"


def _template(name: str) -> Template:
    text = (files("workflows.code_change") / "prompts" / f"{name}.md").read_text(
        encoding="utf-8"
    )
    return Template(text)


def _indent(text: str, by: int = 4) -> str:
    pad = " " * by
    return "\n".join(f"{pad}{line}" if line else "" for line in text.split("\n"))


def _bullet(text: str) -> str:
    return "- " + text.replace("\n", "\n  ")


def _check_failures(runs: list[CheckRun]) -> str:
    parts: list[str] = []
    for run in runs:
        if not run.timed_out and run.exit_code == 0:
            continue
        outcome = "timed out" if run.timed_out else f"exited with {run.exit_code}"
        lines = [_indent(_bullet(f"{run.command} {outcome}"))]
        if run.truncated:
            lines.append(_indent("(the start of the output was cut off)", 8))
        if run.output:
            lines.append(_indent(run.output.rstrip("\n"), 8))
        parts.append("\n".join(lines))
    return "\n".join(parts)


def _retry_section(previous: Attempt, diff_file: Path | None) -> str:
    lines = [
        "## What went wrong last time",
        "",
        (
            f"Attempt {previous.number} did not pass. Your earlier commits are still in "
            "place. Fix the problem with new commits on top of them; do not amend or "
            "rewrite them."
        ),
    ]
    if diff_file is not None:
        lines.append(f"Everything changed so far is in: {diff_file}")
    lines.append("")
    reason = previous.reason or ""
    if previous.ended_at == ChangeStage.CHANGE:
        lines.append(
            "You did not write a valid result file. Write one as described under Reporting."
        )
    elif previous.ended_at == ChangeStage.COMMIT_CHECK:
        lines += ["The commit check failed:", "", _indent(reason)]
    elif previous.ended_at == ChangeStage.CHECKS:
        lines += ["These checks failed:", "", _check_failures(previous.checks)]
    elif previous.ended_at == ChangeStage.REVIEW:
        comments = previous.review.comments if previous.review else []
        lines += [
            "The review found that the change does not match the instruction:",
            "",
            "\n".join(_indent(_bullet(c)) for c in comments),
        ]
    return "\n".join(lines) + "\n\n"


def change_prompt(
    *,
    instruction: str,
    commit_instructions: str | None,
    checks: list[str],
    codebase: Path,
    result_file: Path,
    attempt: int,
    max_attempts: int,
    previous: Attempt | None,
    diff_file: Path | None,
) -> str:
    return _template("change").substitute(
        result_file=result_file,
        attempt=attempt,
        max_attempts=max_attempts,
        codebase=codebase,
        instruction=_indent(instruction),
        commit_instructions=_indent(commit_instructions)
        if commit_instructions
        else _NONE,
        checks="\n".join(_indent(_bullet(c)) for c in checks) if checks else _NONE,
        retry=_retry_section(previous, diff_file) if previous is not None else "",
    )


def review_prompt(
    *,
    instruction: str,
    codebase: Path,
    diff_file: Path,
    commits: list[Commit],
    result_file: Path,
) -> str:
    subjects = [
        f"{c.id} {c.message.splitlines()[0] if c.message else ''}".rstrip()
        for c in commits
    ]
    return _template("review").substitute(
        result_file=result_file,
        codebase=codebase,
        diff_file=diff_file,
        commits="\n".join(_indent(s) for s in subjects) if subjects else _NONE,
        instruction=_indent(instruction),
    )
