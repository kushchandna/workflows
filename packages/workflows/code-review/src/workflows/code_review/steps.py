"""Running one step, or one per-file batch, with its single retry."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from workflows.code_review.calls import CallRunner, Step, output_folder
from workflows.code_review.changes import ChangedFile
from workflows.code_review.formats import ConsolidationResult, PerFileResult, StepResult
from workflows.code_review.models import DroppedThreadAction, ReviewStage
from workflows.code_review.prompts import PromptContext, per_file_prompt
from workflows.code_review.validation import (
    Checked,
    Scope,
    check_items,
    read_result,
    usable_per_file,
)

PER_FILE = Step(stage=ReviewStage.PER_FILE)


@dataclass
class FileOutcome:
    checked: dict[str, PerFileResult]  # path -> checked result
    unreviewed: list[str]
    dropped: list[DroppedThreadAction]


@dataclass
class StepOutcome:
    result: (
        StepResult | ConsolidationResult | None
    )  # checked; None means the step failed
    dropped: list[DroppedThreadAction]


def _dropped(checked: Checked, step: Step) -> list[DroppedThreadAction]:
    return [
        DroppedThreadAction(
            thread_id=action.thread_id,
            action=action.action,
            stage=step.stage,
            agent=step.agent,
            reason=reason,
        )
        for action, reason in checked.dropped
    ]


async def _per_file_attempt(
    runner: CallRunner,
    ctx: PromptContext,
    batch_index: int,
    attempt: int,
    files: list[ChangedFile],
    scope: Scope,
) -> list[tuple[PerFileResult | None, Checked | None]]:
    """Run one call over ``files`` and read back one (result, checked) per file."""
    folder = output_folder(runner.work, PER_FILE, batch_index, attempt)
    slots = [(n, file.path) for n, file in enumerate(files, start=1)]
    prompt = per_file_prompt(ctx, folder, slots, sorted(scope.thread_ids))
    await runner.call(PER_FILE, batch_index, attempt, folder, prompt)
    outcomes: list[tuple[PerFileResult | None, Checked | None]] = []
    for n, path in slots:
        result = read_result(folder / f"{n}.json", PerFileResult)
        if usable_per_file(result, path):
            assert result is not None
            outcomes.append(
                (result, check_items(result.comments, result.thread_actions, scope))
            )
        else:
            outcomes.append((None, None))
    return outcomes


async def run_per_file_batch(
    runner: CallRunner,
    ctx: PromptContext,
    batch_index: int,
    files: list[ChangedFile],
    scope: Scope,
) -> FileOutcome:
    """Review one batch; files with an unusable result or invalid comments get one retry."""
    first = await _per_file_attempt(runner, ctx, batch_index, 1, files, scope)
    chosen = dict(zip((f.path for f in files), first, strict=True))
    redo = [
        f
        for f, (result, checked) in zip(files, first, strict=True)
        if checked is None or checked.had_invalid
    ]
    if redo:
        # Threads the other files already replied to or acted on are not offered again, so
        # the retry cannot post a duplicate reply.
        answered = {
            thread_id
            for file, (_, checked) in zip(files, first, strict=True)
            if checked is not None and file not in redo
            for thread_id in [
                *(c.reply_to for c in checked.comments if c.reply_to),
                *(a.thread_id for a in checked.thread_actions),
            ]
        }
        retry_scope = replace(scope, thread_ids=scope.thread_ids - answered)
        second = await _per_file_attempt(runner, ctx, batch_index, 2, redo, retry_scope)
        for file, outcome in zip(redo, second, strict=True):
            if outcome[0] is not None:  # the retry's result wins whenever it is usable
                chosen[file.path] = outcome

    out = FileOutcome(checked={}, unreviewed=[], dropped=[])
    for file in files:
        result, checked = chosen[file.path]
        if result is None or checked is None:
            out.unreviewed.append(file.path)
            continue
        out.checked[file.path] = PerFileResult(
            path=result.path,
            summary=result.summary,
            comments=checked.comments,
            thread_actions=checked.thread_actions,
        )
        out.dropped += _dropped(checked, PER_FILE)
    return out


async def run_step(
    runner: CallRunner,
    step: Step,
    prompt_for: Callable[[Path], str],
    scope: Scope,
    model: type[StepResult] | type[ConsolidationResult],
) -> StepOutcome:
    """Run a single-result step; an unusable result or invalid comments get one retry."""
    best: tuple[StepResult | ConsolidationResult, Checked] | None = None
    for attempt in (1, 2):
        folder = output_folder(runner.work, step, None, attempt)
        await runner.call(step, None, attempt, folder, prompt_for(folder))
        result = read_result(folder / "result.json", model)
        if result is None:
            continue
        checked = check_items(result.comments, result.thread_actions, scope)
        best = (result, checked)  # a usable retry replaces the first attempt
        if not checked.had_invalid:
            break
    if best is None:
        return StepOutcome(result=None, dropped=[])
    result, checked = best
    final = result.model_copy(
        update={"comments": checked.comments, "thread_actions": checked.thread_actions}
    )
    return StepOutcome(result=final, dropped=_dropped(checked, step))
