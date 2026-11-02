"""The run loop: attempts, stages, statuses and the reset."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from workflows.agent import Agent, AgentRequest, Event
from workflows.code_change.checks import checks_reason, run_checks
from workflows.code_change.formats import (
    ChangeNeedsHuman,
    read_change_report,
    read_review_report,
)
from workflows.code_change.models import (
    Attempt,
    ChangeEvent,
    ChangeInput,
    ChangeResult,
    ChangeSettings,
    ChangeStage,
    ReviewOutcome,
)
from workflows.code_change.prompts import change_prompt, review_prompt
from workflows.code_change.repo import Repository

Status = Literal["completed", "needs_human", "inconclusive", "failed"]

NO_CHANGE_RESULT = "no valid change result"
REVIEW_ASKED_FOR_CHANGES = "review asked for changes"
NO_REVIEW_RESULT = "no valid review result after retry"
REVIEW_CHANGED_CODEBASE = "review changed the codebase"
CHECKS_LEFT_CHANGES = "checks left changes in the work tree"


@dataclass
class _Outcome:
    status: Status
    reason: str | None
    title: str | None
    description: str | None
    attempts: list[Attempt]


class _Run:
    def __init__(
        self,
        change_input: ChangeInput,
        repo: Repository,
        change_agent: Agent,
        review_agent: Agent,
        settings: ChangeSettings,
        work: Path,
        on_event: Callable[[ChangeEvent], None] | None,
    ) -> None:
        self.input = change_input
        self.repo = repo
        self.change_agent = change_agent
        self.review_agent = review_agent
        self.settings = settings
        self.work = work
        self.on_event = on_event

    async def call(
        self, agent: Agent, stage: ChangeStage, attempt: int, call: int, prompt: str
    ) -> None:
        def forward(event: Event) -> None:
            if self.on_event is not None:
                self.on_event(
                    ChangeEvent(stage=stage, attempt=attempt, call=call, event=event)
                )

        request = AgentRequest(
            prompt=prompt,
            working_folder=self.input.codebase,
            extra_folders=[self.work],
            timeout_seconds=self.settings.agent_timeout_seconds,
        )
        await agent.run(request, on_event=forward)

    async def review(
        self, attempt: int, attempt_dir: Path, last_checked: str, diff_file: Path
    ) -> ReviewOutcome:
        commits = self.repo.commits()
        for call in (1, 2):
            result_file = attempt_dir / "review" / f"call-{call}" / "result.json"
            result_file.parent.mkdir(parents=True, exist_ok=True)
            prompt = review_prompt(
                instruction=self.input.instruction,
                codebase=self.input.codebase,
                diff_file=diff_file,
                commits=commits,
                result_file=result_file,
            )
            await self.call(
                self.review_agent, ChangeStage.REVIEW, attempt, call, prompt
            )
            if not self.repo.unchanged(last_checked):
                return ReviewOutcome(
                    verdict="inconclusive", reason=REVIEW_CHANGED_CODEBASE
                )
            report = read_review_report(result_file)
            if report is not None:
                return ReviewOutcome(
                    verdict=report.verdict,
                    comments=report.comments,
                    reason=report.reason if report.verdict == "inconclusive" else None,
                )
        return ReviewOutcome(verdict="inconclusive", reason=NO_REVIEW_RESULT)

    async def attempts(self) -> _Outcome:
        attempts: list[Attempt] = []
        last_checked = self.repo.base
        diff_file: Path | None = None
        title: str | None = None
        description: str | None = None
        for n in range(1, self.input.max_attempts + 1):
            attempt = Attempt(number=n)
            attempts.append(attempt)
            attempt_dir = self.work / f"attempt-{n}"
            result_file = attempt_dir / "change" / "result.json"
            result_file.parent.mkdir(parents=True, exist_ok=True)

            prompt = change_prompt(
                instruction=self.input.instruction,
                commit_instructions=self.input.commit_instructions,
                checks=self.input.checks,
                codebase=self.input.codebase,
                result_file=result_file,
                attempt=n,
                max_attempts=self.input.max_attempts,
                previous=attempts[-2] if n > 1 else None,
                diff_file=diff_file,
                expected_head=last_checked,
                starting_branch=self.repo.starting_branch,
            )
            await self.call(self.change_agent, ChangeStage.CHANGE, n, 1, prompt)
            report = read_change_report(result_file)
            if report is None:
                attempt.ended_at, attempt.reason = ChangeStage.CHANGE, NO_CHANGE_RESULT
                continue
            if isinstance(report, ChangeNeedsHuman):
                attempt.ended_at, attempt.reason = ChangeStage.CHANGE, report.reason
                return _Outcome(
                    "needs_human", report.reason, title, description, attempts
                )
            title, description = report.title, report.description

            problem = self.repo.commit_problem(last_checked)
            if problem is not None:
                attempt.ended_at, attempt.reason = ChangeStage.COMMIT_CHECK, problem
                continue
            last_checked = self.repo.head()
            diff_file = attempt_dir / "change.diff"
            self.repo.write_diff(diff_file)

            if self.input.checks:
                attempt.checks = await run_checks(
                    self.input.checks,
                    self.input.codebase,
                    attempt_dir / "checks",
                    self.settings.check_timeout_seconds,
                    self.settings.check_output_chars,
                )
                # The commit check left the tree clean, so whatever is dirty now came from the
                # checks. Blaming it on the review, or on the next commit check, would be wrong.
                attempt.left_changes = self.repo.dirty_summary()
                reasons = [checks_reason(attempt.checks)]
                if attempt.left_changes is not None:
                    reasons.append(f"{CHECKS_LEFT_CHANGES}: {attempt.left_changes}")
                failed = "; ".join(r for r in reasons if r is not None)
                if failed:
                    attempt.ended_at, attempt.reason = ChangeStage.CHECKS, failed
                    continue

            if self.input.review:
                attempt.review = await self.review(
                    n, attempt_dir, last_checked, diff_file
                )
                if attempt.review.verdict == "inconclusive":
                    attempt.ended_at = ChangeStage.REVIEW
                    attempt.reason = attempt.review.reason
                    return _Outcome(
                        "inconclusive", attempt.reason, title, description, attempts
                    )
                if attempt.review.verdict == "changes_needed":
                    attempt.ended_at = ChangeStage.REVIEW
                    attempt.reason = REVIEW_ASKED_FOR_CHANGES
                    continue

            return _Outcome("completed", None, title, description, attempts)

        last = attempts[-1]
        assert last.ended_at is not None
        reason = f"{last.ended_at.value}: {last.reason}"
        return _Outcome("failed", reason, title, description, attempts)


async def run_change(
    change_input: ChangeInput,
    repo: Repository,
    *,
    change_agent: Agent,
    review_agent: Agent,
    settings: ChangeSettings,
    work: Path,
    on_event: Callable[[ChangeEvent], None] | None,
) -> ChangeResult:
    """Runs the attempts and resets the repository unless the change completed.

    ``change_input.codebase`` is absolute and ``work`` exists and is empty.
    """
    run = _Run(change_input, repo, change_agent, review_agent, settings, work, on_event)
    try:
        outcome = await run.attempts()
    except BaseException as error:
        if change_input.reset_on_failure:
            try:
                repo.reset()
            except Exception as reset_error:
                raise reset_error from error
        raise
    if outcome.status != "completed" and change_input.reset_on_failure:
        repo.reset()
    return ChangeResult(
        status=outcome.status,
        reason=outcome.reason,
        base_commit=repo.base,
        commits=repo.commits(),
        title=outcome.title,
        description=outcome.description,
        attempts=outcome.attempts,
    )
