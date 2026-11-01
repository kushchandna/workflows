"""Prompts for the review passes, filled from the templates in ``prompts/``.

Some lines are fixed because the code and the tests rely on them: ``Output folder:``,
``Slot <n>: <path>``, ``Result file:`` and ``Threads you may act on:``.
"""

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from string import Template

from workflows.code_review.models import ReviewPass

_NONE = "(none)"


@dataclass(frozen=True)
class PromptContext:
    work: Path
    codebase: Path | None
    description: str | None
    instructions: str | None
    change_author: str | None
    reviewer_name: str | None


def _template(name: str) -> Template:
    text = (files("workflows.code_review") / "prompts" / f"{name}.md").read_text(
        encoding="utf-8"
    )
    return Template(text)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else _NONE


def _shared(ctx: PromptContext, output_folder: Path, thread_ids: list[str]) -> str:
    change_author = (
        f"The change author is {ctx.change_author}. Messages in threads by this name are "
        "the author's replies."
        if ctx.change_author
        else "The change author is not known."
    )
    reviewer = (
        f"The reviewer's own earlier comments appear under the name {ctx.reviewer_name}."
        if ctx.reviewer_name
        else "The reviewer's own name is not known."
    )
    return _template("shared").substitute(
        description=ctx.description or _NONE,
        instructions=ctx.instructions or _NONE,
        codebase=f"{ctx.codebase}" if ctx.codebase else "not available, only the diffs",
        diffs_dir=ctx.work / "input" / "diffs",
        threads_file=ctx.work / "input" / "threads.json",
        passes_dir=ctx.work / "review-passes",
        people=f"{change_author}\n{reviewer}",
        thread_ids=", ".join(thread_ids) if thread_ids else "none",
        output_folder=output_folder,
    )


def per_file_prompt(
    ctx: PromptContext,
    output_folder: Path,
    slots: list[tuple[int, str]],
    thread_ids: list[str],
) -> str:
    return _template("per-file").substitute(
        diffs_dir=ctx.work / "input" / "diffs",
        slots="\n".join(f"Slot {n}: {path}" for n, path in slots),
        output_folder=output_folder,
        shared=_shared(ctx, output_folder, thread_ids),
    )


def pass_prompt(
    ctx: PromptContext,
    review_pass: ReviewPass,
    output_folder: Path,
    changed_paths: list[str],
    unreviewed: list[str],
    thread_ids: list[str],
) -> str:
    """The prompt for the overall, test coverage and code patterns passes."""
    return _template(review_pass.value).safe_substitute(
        passes_dir=ctx.work / "review-passes",
        changed=_bullets(changed_paths),
        unreviewed=_bullets(unreviewed),
        output_folder=output_folder,
        shared=_shared(ctx, output_folder, thread_ids),
    )


def consolidation_prompt(
    ctx: PromptContext, output_folder: Path, thread_ids: list[str], coverage_text: str
) -> str:
    return _template("consolidation").substitute(
        passes_dir=ctx.work / "review-passes",
        coverage=coverage_text,
        output_folder=output_folder,
        shared=_shared(ctx, output_folder, thread_ids),
    )
