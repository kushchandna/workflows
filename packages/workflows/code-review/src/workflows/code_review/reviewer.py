import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from workflows.agent import Agent, AgentError
from workflows.code_review.batching import make_batches
from workflows.code_review.calls import CallRunner
from workflows.code_review.changes import changes_from_diffs
from workflows.code_review.errors import ReviewInputError
from workflows.code_review.formats import (
    ConsolidationResult,
    PassResult,
    PerFileResult,
    Thread,
)
from workflows.code_review.gitchanges import changes_from_git, check_codebase
from workflows.code_review.models import (
    Codebase,
    Comment,
    Coverage,
    DroppedThreadAction,
    FileCoverage,
    PassCoverage,
    ReviewEvent,
    ReviewInput,
    ReviewPass,
    ReviewResult,
    ReviewSettings,
    ThreadAction,
)
from workflows.code_review.passes import (
    FileOutcome,
    PassOutcome,
    run_pass,
    run_per_file_batch,
)
from workflows.code_review.prompts import (
    PromptContext,
    consolidation_prompt,
    pass_prompt,
)
from workflows.code_review.threads import load_threads, route_threads
from workflows.code_review.validation import Scope

NO_RESULT = "no valid result after retry"


class CodeReviewer:
    """Reviews a change in several agent passes and returns a status, comments and coverage.

    The end product supplies the agents; this class never chooses a model.
    """

    def __init__(
        self,
        default_agent: Agent,
        pass_agents: Mapping[ReviewPass, Agent] | None = None,
        settings: ReviewSettings | None = None,
    ) -> None:
        self._default_agent = default_agent
        self._pass_agents = dict(pass_agents or {})
        self._settings = settings or ReviewSettings()

    async def review(
        self,
        review_input: ReviewInput,
        on_event: Callable[[ReviewEvent], None] | None = None,
    ) -> ReviewResult:
        if review_input.codebase is None and review_input.diffs is None:
            raise ReviewInputError("give a codebase, a diffs folder, or both")
        configured = self._settings.work_folder
        if configured is None:
            with tempfile.TemporaryDirectory(prefix="code-review-") as tmp:
                return await self._review(review_input, Path(tmp).resolve(), on_event)
        configured = configured.resolve()
        if configured.exists() and (
            not configured.is_dir() or any(configured.iterdir())
        ):
            raise ReviewInputError(
                f"work folder must be empty or missing: {configured}"
            )
        configured.mkdir(parents=True, exist_ok=True)
        return await self._review(review_input, configured, on_event)

    async def _review(
        self,
        review_input: ReviewInput,
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
                    passes=[
                        PassCoverage(review_pass=p, state="skipped", reason=reason)
                        for p in ReviewPass
                    ],
                ),
            )

        settings = self._settings
        runner = CallRunner(
            agents=lambda p: self._pass_agents.get(p, self._default_agent),
            working_folder=codebase.path if codebase else work,
            work=work,
            timeout_seconds=settings.agent_timeout_seconds,
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
        )
        codebase_path = codebase.path if codebase else None
        changed = sorted(f.path for f in reviewable)
        changed_paths = frozenset().union(*(f.paths for f in all_files))
        batches = make_batches(reviewable, settings)
        routing = route_threads(threads, batches)

        # First wave: every per-file batch, test coverage and code patterns together.
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
        side_passes = [ReviewPass.TEST_COVERAGE]
        if codebase_path is not None:
            side_passes.append(ReviewPass.CODE_PATTERNS)
        for review_pass in side_passes:
            jobs.append(
                run_pass(
                    runner,
                    review_pass,
                    lambda folder, rp=review_pass: pass_prompt(
                        ctx, rp, folder, changed, [], []
                    ),
                    no_threads,
                    PassResult,
                )
            )
        outcomes = await _gather(jobs)
        file_outcomes: list[FileOutcome] = outcomes[: len(batches)]
        side_outcomes: dict[ReviewPass, PassOutcome] = dict(
            zip(side_passes, outcomes[len(batches) :], strict=True)
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
            _write(work / "review-passes" / "per-file" / f"{path}.json", result)
            file_coverage[path] = FileCoverage(path=path, state="reviewed")
        for path in unreviewed:
            file_coverage[path] = FileCoverage(
                path=path, state="unreviewed", reason=NO_RESULT
            )

        pass_coverage: dict[ReviewPass, PassCoverage] = {
            ReviewPass.PER_FILE: PassCoverage(
                review_pass=ReviewPass.PER_FILE, state="ran"
            )
        }
        if codebase_path is None:
            pass_coverage[ReviewPass.CODE_PATTERNS] = PassCoverage(
                review_pass=ReviewPass.CODE_PATTERNS,
                state="skipped",
                reason="no codebase",
            )
        earlier: list[PerFileResult | PassResult] = list(checked_files.values())
        for review_pass, outcome in side_outcomes.items():
            dropped += outcome.dropped
            pass_coverage[review_pass] = _pass_coverage(review_pass, outcome)
            _record(work, review_pass, outcome)

        # Overall: needs the per-file results.
        overall = await run_pass(
            runner,
            ReviewPass.OVERALL,
            lambda folder: pass_prompt(
                ctx,
                ReviewPass.OVERALL,
                folder,
                changed,
                unreviewed,
                sorted(routing.overall),
            ),
            Scope(changed_paths, None, codebase_path, routing.overall),
            PassResult,
        )
        dropped += overall.dropped
        pass_coverage[ReviewPass.OVERALL] = _pass_coverage(ReviewPass.OVERALL, overall)
        _record(work, ReviewPass.OVERALL, overall)
        if isinstance(overall.result, PassResult):
            earlier.append(overall.result)

        # Consolidation: last. Threads nobody replied to or acted on are still open to it.
        answered = {c.reply_to for r in earlier for c in r.comments if c.reply_to} | {
            a.thread_id for r in earlier for a in r.thread_actions
        }
        all_thread_ids = frozenset(t.id for t in threads)
        coverage_text = _coverage_text(
            list(file_coverage.values()), pass_coverage.values()
        )
        consolidation = await run_pass(
            runner,
            ReviewPass.CONSOLIDATION,
            lambda folder: consolidation_prompt(
                ctx, folder, sorted(all_thread_ids - answered), coverage_text
            ),
            Scope(changed_paths, None, codebase_path, all_thread_ids - answered),
            ConsolidationResult,
        )
        final = consolidation.result
        if not isinstance(final, ConsolidationResult):
            raise AgentError("consolidation produced no usable result")
        dropped += consolidation.dropped
        pass_coverage[ReviewPass.CONSOLIDATION] = PassCoverage(
            review_pass=ReviewPass.CONSOLIDATION, state="ran"
        )
        _record(work, ReviewPass.CONSOLIDATION, consolidation)

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
            c.state == "failed" for c in pass_coverage.values()
        )
        return ReviewResult(
            status="inconclusive" if degraded else final.status,
            comments=comments,
            thread_actions=thread_actions,
            coverage=Coverage(
                files=sorted(file_coverage.values(), key=lambda c: c.path),
                passes=[pass_coverage[p] for p in ReviewPass if p in pass_coverage],
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


def _pass_coverage(review_pass: ReviewPass, outcome: PassOutcome) -> PassCoverage:
    if outcome.result is None:
        return PassCoverage(review_pass=review_pass, state="failed", reason=NO_RESULT)
    return PassCoverage(review_pass=review_pass, state="ran")


def _record(work: Path, review_pass: ReviewPass, outcome: PassOutcome) -> None:
    if outcome.result is not None:
        _write(
            work / "review-passes" / review_pass.value / "result.json", outcome.result
        )


def _write(path: Path, data: BaseModel | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        data.model_dump_json(indent=2)
        if isinstance(data, BaseModel)
        else json.dumps(data, indent=2)
    )
    path.write_text(text, encoding="utf-8")


def _with_thread_target(comment: Comment, threads: dict[str, Thread]) -> Comment:
    """A reply carries its thread's own target, never one an agent made up."""
    thread = threads.get(comment.reply_to or "")
    return comment.model_copy(update={"target": thread.target}) if thread else comment


def _coverage_text(files: list[FileCoverage], passes: Iterable[PassCoverage]) -> str:
    """The coverage so far, as text for the consolidation prompt."""
    unreviewed = sorted(f.path for f in files if f.state == "unreviewed")
    skipped = sorted(f.path for f in files if f.state == "skipped")
    lines = [
        f"- files reviewed: {sum(1 for f in files if f.state == 'reviewed')}",
        f"- unreviewed files (no usable per-file result): {', '.join(unreviewed) or 'none'}",
        f"- skipped files (binary): {', '.join(skipped) or 'none'}",
    ]
    for p in passes:
        detail = f" ({p.reason})" if p.reason else ""
        lines.append(f"- {p.review_pass.value}: {p.state}{detail}")
    return "\n".join(lines)
