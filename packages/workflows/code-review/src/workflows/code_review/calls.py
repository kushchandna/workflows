import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from workflows.code_review.models import ReviewEvent, ReviewStage
from workflows.harness import Event, Harness, HarnessRequest


@dataclass(frozen=True)
class Step:
    """A part of the review that makes harness calls: a stage or a review agent."""

    stage: ReviewStage | None = None
    agent: str | None = None

    def __post_init__(self) -> None:
        if (self.stage is None) == (self.agent is None):
            raise ValueError("a step is either a stage or an agent")

    @property
    def folder(self) -> str:
        """Its folder under ``harness-output/`` and ``results/``."""
        return self.stage.value if self.stage else f"agents/{self.agent}"


def output_folder(work: Path, step: Step, batch: int | None, attempt: int) -> Path:
    """Where a harness writes its raw files: ``harness-output/<step>/[batch-<k>/]attempt-<a>``."""
    folder = work / "harness-output" / step.folder
    if batch is not None:
        folder = folder / f"batch-{batch}"
    return folder / f"attempt-{attempt}"


@dataclass
class CallRunner:
    """Runs harness calls: picks the step's harness, limits parallelism, labels events."""

    harnesses: Callable[[Step], Harness]
    working_folder: Path
    work: Path
    timeout_seconds: float | None
    semaphore: asyncio.Semaphore
    on_event: Callable[[ReviewEvent], None] | None = None

    async def call(
        self,
        step: Step,
        batch: int | None,
        attempt: int,
        folder: Path,
        prompt: str,
    ) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        request = HarnessRequest(
            prompt=prompt,
            working_folder=self.working_folder,
            extra_folders=[self.work],
            timeout_seconds=self.timeout_seconds,
        )

        def forward(event: Event) -> None:
            if self.on_event is not None:
                self.on_event(
                    ReviewEvent(
                        stage=step.stage,
                        agent=step.agent,
                        batch=batch,
                        attempt=attempt,
                        event=event,
                    )
                )

        async with self.semaphore:
            await self.harnesses(step).run(request, on_event=forward)
