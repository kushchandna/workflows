import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from workflows.code_review.agents import resolve_agents
from workflows.code_review.batching import make_batches
from workflows.code_review.calls import CallRunner, Step
from workflows.code_review.changes import changes_from_diffs
from workflows.code_review.errors import ReviewInputError
from workflows.code_review.formats import (
    ConsolidationResult,
    PerFileResult,
    StepResult,
    Thread,
)
from workflows.code_review.gitchanges import changes_from_git, check_codebase
from workflows.code_review.models import (
    AgentCoverage,
    Codebase,
    Comment,
    Coverage,
    DroppedThreadAction,
    FileCoverage,
    ReviewAgent,
    ReviewEvent,
    ReviewInput,
    ReviewResult,
    ReviewSettings,
    ReviewStage,
    StageCoverage,
    ThreadAction,
)
from workflows.code_review.prompts import (
    PromptContext,
    agent_prompt,
    consolidation_prompt,
    overall_prompt,
)
from workflows.code_review.steps import (
    FileOutcome,
    StepOutcome,
    run_per_file_batch,
    run_step,
)
from workflows.code_review.threads import load_threads, route_threads
from workflows.code_review.validation import Scope
from workflows.harness import Harness, HarnessError

NO_RESULT = "no valid result after retry"
OVERALL = Step(stage=ReviewStage.OVERALL)
CONSOLIDATION = Step(stage=ReviewStage.CONSOLIDATION)


class CodeReviewer:
    """Reviews a change in fixed stages plus review agents, and returns a status, comments
    and coverage.

    The end product supplies the harnesses; this class never chooses a model.
    """

    def __init__(
        self,
        default_harness: Harness,
        stage_harnesses: Mapping[ReviewStage, Harness] | None = None,
        agent_harnesses: Mapping[str, Harness] | None = None,
        settings: ReviewSettings | None = None,
    ) -> None:
        self._default_harness = default_harness
        self._stage_harnesses = dict(stage_harnesses or {})
        self._agent_harnesses = dict(agent_harnesses or {})
        self._settings = settings or ReviewSettings()

    def _harness(self, step: Step) -> Harness:
        found = (
            self._stage_harnesses.get(step.stage)
            if step.stage
            else self._agent_harnesses.get(step.agent or "")
        )
        return found if found is not None else self._default_harness

    async def review(
        self,
        review_input: ReviewInput,
        on_event: Callable[[ReviewEvent], None] | None = None,
    ) -> ReviewResult:
        if review_input.codebase is None and review_input.diffs is None:
            raise ReviewInputError("give a codebase, a diffs folder, or both")
        agents = resolve_agents(review_input.agents)
        configured = self._settings.work_folder
        if configured is None:
            with tempfile.TemporaryDirectory(prefix="code-review-") as tmp:
                return await self._review(
                    review_input, agents, Path(tmp).resolve(), on_event
                )
        configured = configured.resolve()
        if review_input.codebase is not None and configured.is_relative_to(
            review_input.codebase.path.resolve()
        ):
            # Git mode would list the review's own files as untracked changes.
            raise ReviewInputError(
                f"work folder must not be inside the codebase: {configured}"
            )
        if configured.exists() and (
            not configured.is_dir() or any(configured.iterdir())
        ):
            raise ReviewInputError(
                f"work folder must be empty or missing: {configured}"
            )
        configured.mkdir(parents=True, exist_ok=True)
        return await self._review(review_input, agents, configured, on_event)

    async def _review(
        self,
        review_input: ReviewInput,
        agents: list[ReviewAgent],
        work: Path,
        on_event: Callable[[ReviewEvent], None] | None,
    ) -> ReviewResult:
        codebase = _resolved_codebase(review_input.codebase)
        threads = load_threads(review_input.discussions)
        diffs_dir = work / "input" / "diffs"
        diffs_dir.mkdir(parents=True, exist_ok=True)
        if review_input.diffs is not None:
            if codebase is not None and not codebase.path.is_dir():
                raise ReviewInputError(f"codebase does not exist: {codebase.path}")
            all_files = changes_from_diffs(review_input.diffs.resolve(), diffs_dir)
        else:
            assert codebase is not None
            check_codebase(codebase)
            all_files = changes_from_git(codebase, diffs_dir)
        _write(
            work / "input" / "threads.json",
            [t.model_dump(mode="json") for t in threads],
        )

        reviewable = [f for f in all_files if not f.binary]
        file_coverage = {
            f.path: FileCoverage(path=f.path, state="skipped", reason="binary file")
            for f in all_files
            if f.binary
        }
        if not reviewable:
            reason = "no changes" if not all_files else "only binary files"
            return ReviewResult(
                status="approved",
                coverage=Coverage(
                    files=sorted(file_coverage.values(), key=lambda c: c.path),
                    stages=[
                        StageCoverage(stage=s, state="skipped", reason=reason)
                        for s in ReviewStage
                    ],
                    agents=[
                        AgentCoverage(agent=a.name, state="skipped", reason=reason)
                        for a in agents
                    ],
                ),
            )

        settings = self._settings
        codebase_path = codebase.path if codebase else None
        running = [a for a in agents if codebase_path or not a.needs_codebase]
        runner = CallRunner(
            harnesses=self._harness,
            working_folder=codebase.path if codebase else work,
            work=work,
            timeout_seconds=settings.harness_timeout_seconds,
            semaphore=asyncio.Semaphore(settings.max_parallel_calls),
            on_event=on_event,
        )
        ctx = PromptContext(
            work=work,
            codebase=codebase.path if codebase else None,
            description=review_input.description,
            instructions=review_input.instructions,
            change_author=review_input.change_author,
            reviewer_name=review_input.reviewer_name,
            agents=tuple(a.name for a in running),
        )
        changed = sorted(f.path for f in reviewable)
        changed_paths = frozenset().union(*(f.paths for f in all_files))
        batches = make_batches(reviewable, settings)
        routing = route_threads(threads, batches)

        # First wave: every per-file batch and every review agent together.
        jobs: list[Awaitable[Any]] = [
            run_per_file_batch(
                runner,
                ctx,
                k,
                batch,
                Scope(
                    changed_paths=changed_paths,
                    allowed_paths=frozenset().union(*(f.paths for f in batch)),
                    codebase=codebase_path,
                    thread_ids=routing.batches[k - 1],
                ),
            )
            for k, batch in enumerate(batches, start=1)
        ]
        no_threads = Scope(changed_paths, None, codebase_path, frozenset())
        for agent in running:
            jobs.append(
                run_step(
                    runner,
                    Step(agent=agent.name),
                    lambda folder, a=agent: agent_prompt(ctx, a, folder, changed),
                    no_threads,
                    StepResult,
                )
            )
        outcomes = await _gather(jobs)
        file_outcomes: list[FileOutcome] = outcomes[: len(batches)]
        agent_outcomes: dict[str, StepOutcome] = dict(
            zip((a.name for a in running), outcomes[len(batches) :], strict=True)
        )

        checked_files: dict[str, PerFileResult] = {}
        unreviewed: list[str] = []
        dropped: list[DroppedThreadAction] = []
        for outcome in file_outcomes:
            checked_files.update(outcome.checked)
            unreviewed += outcome.unreviewed
            dropped += outcome.dropped
        unreviewed.sort()
        for path, result in checked_files.items():
            _write(work / "results" / "per-file" / f"{path}.json", result)
            file_coverage[path] = FileCoverage(path=path, state="reviewed")
        for path in unreviewed:
            file_coverage[path] = FileCoverage(
                path=path, state="unreviewed", reason=NO_RESULT
            )

        stage_coverage: dict[ReviewStage, StageCoverage] = {
            ReviewStage.PER_FILE: StageCoverage(stage=ReviewStage.PER_FILE, state="ran")
        }
        agent_coverage: list[AgentCoverage] = []
        earlier: list[PerFileResult | StepResult] = list(checked_files.values())
        for agent in agents:
            outcome = agent_outcomes.get(agent.name)
            if outcome is None:
                agent_coverage.append(
                    AgentCoverage(
                        agent=agent.name, state="skipped", reason="no codebase"
                    )
                )
                continue
            dropped += outcome.dropped
            failed = outcome.result is None
            agent_coverage.append(
                AgentCoverage(
                    agent=agent.name,
                    state="failed" if failed else "ran",
                    reason=NO_RESULT if failed else None,
                )
            )
            _record(work, Step(agent=agent.name), outcome)

        # Overall: needs the per-file results.
        overall = await run_step(
            runner,
            OVERALL,
            lambda folder: overall_prompt(
                ctx, folder, changed, unreviewed, sorted(routing.overall)
            ),
            Scope(changed_paths, None, codebase_path, routing.overall),
            StepResult,
        )
        dropped += overall.dropped
        stage_coverage[ReviewStage.OVERALL] = _stage_coverage(
            ReviewStage.OVERALL, overall
        )
        _record(work, OVERALL, overall)
        if isinstance(overall.result, StepResult):
            earlier.append(overall.result)

        # Consolidation: last. Threads nobody replied to or acted on are still open to it.
        answered = {c.reply_to for r in earlier for c in r.comments if c.reply_to} | {
            a.thread_id for r in earlier for a in r.thread_actions
        }
        all_thread_ids = frozenset(t.id for t in threads)
        coverage_text = _coverage_text(
            list(file_coverage.values()), stage_coverage.values(), agent_coverage
        )
        consolidation = await run_step(
            runner,
            CONSOLIDATION,
            lambda folder: consolidation_prompt(
                ctx, folder, sorted(all_thread_ids - answered), coverage_text
            ),
            Scope(changed_paths, None, codebase_path, all_thread_ids - answered),
            ConsolidationResult,
        )
        final = consolidation.result
        if not isinstance(final, ConsolidationResult):
            raise HarnessError("consolidation produced no usable result")
        dropped += consolidation.dropped
        stage_coverage[ReviewStage.CONSOLIDATION] = StageCoverage(
            stage=ReviewStage.CONSOLIDATION, state="ran"
        )
        _record(work, CONSOLIDATION, consolidation)

        by_id = {t.id: t for t in threads}
        earlier_replies = [c for r in earlier for c in r.comments if c.reply_to]
        comments = (
            [c for c in final.comments if not c.reply_to]
            + [_with_thread_target(c, by_id) for c in earlier_replies]
            + [_with_thread_target(c, by_id) for c in final.comments if c.reply_to]
        )
        thread_actions: list[ThreadAction] = [
            a for r in earlier for a in r.thread_actions
        ]
        thread_actions += final.thread_actions
        degraded = bool(unreviewed) or any(
            c.state == "failed" for c in [*stage_coverage.values(), *agent_coverage]
        )
        return ReviewResult(
            status="inconclusive" if degraded else final.status,
            comments=comments,
            thread_actions=thread_actions,
            coverage=Coverage(
                files=sorted(file_coverage.values(), key=lambda c: c.path),
                stages=[stage_coverage[s] for s in ReviewStage if s in stage_coverage],
                agents=agent_coverage,
                dropped_thread_actions=dropped,
            ),
        )


def _resolved_codebase(codebase: Codebase | None) -> Codebase | None:
    if codebase is None:
        return None
    return Codebase(path=codebase.path.resolve(), base_commit=codebase.base_commit)


async def _gather(jobs: list[Awaitable[Any]]) -> list[Any]:
    """Run jobs together. The first failure is raised as itself and cancels the rest."""
    tasks = [asyncio.ensure_future(job) for job in jobs]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    raise error
        return [task.result() for task in tasks]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _stage_coverage(stage: ReviewStage, outcome: StepOutcome) -> StageCoverage:
    if outcome.result is None:
        return StageCoverage(stage=stage, state="failed", reason=NO_RESULT)
    return StageCoverage(stage=stage, state="ran")


def _record(work: Path, step: Step, outcome: StepOutcome) -> None:
    if outcome.result is not None:
        _write(work / "results" / step.folder / "result.json", outcome.result)


def _write(path: Path, data: BaseModel | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        data.model_dump_json(indent=2)
        if isinstance(data, BaseModel)
        else json.dumps(data, indent=2)
    )
    path.write_text(text, encoding="utf-8")


def _with_thread_target(comment: Comment, threads: dict[str, Thread]) -> Comment:
    """A reply carries its thread's own target, never one the model made up."""
    thread = threads.get(comment.reply_to or "")
    return comment.model_copy(update={"target": thread.target}) if thread else comment


def _coverage_text(
    files: list[FileCoverage],
    stages: Iterable[StageCoverage],
    agents: list[AgentCoverage],
) -> str:
    """The coverage so far, as text for the consolidation prompt."""
    unreviewed = sorted(f.path for f in files if f.state == "unreviewed")
    skipped = sorted(f.path for f in files if f.state == "skipped")
    lines = [
        f"- files reviewed: {sum(1 for f in files if f.state == 'reviewed')}",
        f"- unreviewed files (no usable per-file result): {', '.join(unreviewed) or 'none'}",
        f"- skipped files (binary): {', '.join(skipped) or 'none'}",
    ]
    for s in stages:
        detail = f" ({s.reason})" if s.reason else ""
        lines.append(f"- {s.stage.value}: {s.state}{detail}")
    for a in agents:
        detail = f" ({a.reason})" if a.reason else ""
        lines.append(f"- review agent {a.agent}: {a.state}{detail}")
    return "\n".join(lines)
