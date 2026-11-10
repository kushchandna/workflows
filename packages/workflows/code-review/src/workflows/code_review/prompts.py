"""Prompts for the review stages and agents, filled from the templates in ``prompts/``.

Some lines are fixed because the code and the tests rely on them: ``Output folder:``,
``Slot <n>: <path>``, ``Result file:`` and ``Threads you may act on:``.
"""

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from string import Template

from workflows.code_review.models import ReviewAgent

_NONE = "(none)"


@dataclass(frozen=True)
class PromptContext:
    work: Path
    codebase: Path | None
    description: str | None
    instructions: str | None
    change_author: str | None
    reviewer_name: str | None
    agents: tuple[str, ...] = ()  # names of the review agents that run


def _template(name: str) -> Template:
    text = (files("workflows.code_review") / "prompts" / f"{name}.md").read_text(
        encoding="utf-8"
    )
    return Template(text)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else _NONE


def _sentence_list(items: list[str]) -> str:
    """Bullets joined like one sentence: ";" after each item but the last, "." after it."""
    return "\n".join(
        f"- {item}{'.' if n == len(items) else ';'}"
        for n, item in enumerate(items, start=1)
    )


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
        results_dir=ctx.work / "results",
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
    not_your_job = ["problems that span several files"]
    if ctx.agents:
        not_your_job.append(
            "the focus of each review agent that also runs, each named for its focus: "
            + ", ".join(ctx.agents)
        )
    return _template("per-file").substitute(
        not_your_job=_sentence_list(not_your_job),
        diffs_dir=ctx.work / "input" / "diffs",
        slots="\n".join(f"Slot {n}: {path}" for n, path in slots),
        output_folder=output_folder,
        shared=_shared(ctx, output_folder, thread_ids),
    )


def overall_prompt(
    ctx: PromptContext,
    output_folder: Path,
    changed_paths: list[str],
    unreviewed: list[str],
    thread_ids: list[str],
) -> str:
    return _template("overall").substitute(
        results_dir=ctx.work / "results",
        changed=_bullets(changed_paths),
        unreviewed=_bullets(unreviewed),
        output_folder=output_folder,
        shared=_shared(ctx, output_folder, thread_ids),
    )


def agent_prompt(
    ctx: PromptContext,
    agent: ReviewAgent,
    output_folder: Path,
    changed_paths: list[str],
) -> str:
    """A review agent's prompt. It may not act on threads."""
    others = [name for name in ctx.agents if name != agent.name]
    other_agents = (
        "Other review agents also run, each named for its focus: "
        f"{', '.join(others)}. Leave their focus to them too."
        if others
        else "No other review agents run."
    )
    return _template("agent").substitute(
        name=agent.name,
        other_agents=other_agents,
        instructions=agent.instructions.strip(),
        changed=_bullets(changed_paths),
        output_folder=output_folder,
        shared=_shared(ctx, output_folder, []),
    )


def consolidation_prompt(
    ctx: PromptContext, output_folder: Path, thread_ids: list[str], coverage_text: str
) -> str:
    result_files = [
        "per-file/<path>.json: one per reviewed file",
        *(
            f"agents/{name}/result.json: from the {name} review agent"
            for name in ctx.agents
        ),
        "overall/result.json",
    ]
    return _template("consolidation").substitute(
        results_dir=ctx.work / "results",
        result_files=_sentence_list(result_files),
        coverage=coverage_text,
        output_folder=output_folder,
        shared=_shared(ctx, output_folder, thread_ids),
    )
