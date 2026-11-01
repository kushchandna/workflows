import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from workflows.agent import Agent, AgentRequest, Event
from workflows.code_review.models import ReviewEvent, ReviewPass


def output_folder(
    work: Path, review_pass: ReviewPass, batch: int | None, attempt: int
) -> Path:
    """Where an agent writes its raw files: ``agent-output/<pass>/[batch-<k>/]attempt-<a>``."""
    folder = work / "agent-output" / review_pass.value
    if batch is not None:
        folder = folder / f"batch-{batch}"
    return folder / f"attempt-{attempt}"


@dataclass
class CallRunner:
    """Runs agent calls: picks the pass's agent, limits parallelism, labels events."""

    agents: Callable[[ReviewPass], Agent]
    working_folder: Path
    work: Path
    timeout_seconds: float | None
    semaphore: asyncio.Semaphore
    on_event: Callable[[ReviewEvent], None] | None = None

    async def call(
        self,
        review_pass: ReviewPass,
        batch: int | None,
        attempt: int,
        folder: Path,
        prompt: str,
    ) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        request = AgentRequest(
            prompt=prompt,
            working_folder=self.working_folder,
            extra_folders=[self.work],
            timeout_seconds=self.timeout_seconds,
        )

        def forward(event: Event) -> None:
            if self.on_event is not None:
                self.on_event(
                    ReviewEvent(
                        review_pass=review_pass,
                        batch=batch,
                        attempt=attempt,
                        event=event,
                    )
                )

        async with self.semaphore:
            await self.agents(review_pass).run(request, on_event=forward)
