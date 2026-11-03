import tempfile
from collections.abc import Callable
from pathlib import Path

from workflows.agent import Agent
from workflows.code_change.errors import ChangeInputError
from workflows.code_change.git import open_repository
from workflows.code_change.loop import run_change
from workflows.code_change.models import (
    ChangeEvent,
    ChangeInput,
    ChangeResult,
    ChangeSettings,
)
from workflows.code_change.repo import Repository


class CodeChanger:
    """Applies one instruction to a git codebase, commits it and reports the outcome.

    The end product supplies the agents; this class never chooses a model. Without a
    ``review_agent``, the change agent also does the review.
    """

    def __init__(
        self,
        change_agent: Agent,
        review_agent: Agent | None = None,
        settings: ChangeSettings | None = None,
    ) -> None:
        self._change_agent = change_agent
        self._review_agent = review_agent or change_agent
        self._settings = settings or ChangeSettings()

    async def change(
        self,
        change_input: ChangeInput,
        on_event: Callable[[ChangeEvent], None] | None = None,
    ) -> ChangeResult:
        if not change_input.instruction.strip():
            raise ChangeInputError("instruction is empty")
        codebase = change_input.codebase.resolve()
        configured = self._settings.work_folder
        if configured is not None:
            configured = configured.resolve()
            if configured.is_relative_to(codebase):
                # The run's own files would show up as untracked changes.
                raise ChangeInputError(
                    f"work folder must not be inside the codebase: {configured}"
                )
            if configured.exists() and (
                not configured.is_dir() or any(configured.iterdir())
            ):
                raise ChangeInputError(
                    f"work folder must be empty or missing: {configured}"
                )
        else:
            temporary = Path(tempfile.gettempdir()).resolve()
            if temporary.is_relative_to(codebase):
                # Same reason: the run's files would be untracked work, and the reset would
                # delete the folder in the middle of the run.
                raise ChangeInputError(
                    f"the temporary folder is inside the codebase: {temporary}; "
                    "point TMPDIR elsewhere or set a work_folder"
                )
        repo = open_repository(codebase)
        resolved = change_input.model_copy(update={"codebase": codebase})
        if configured is None:
            with tempfile.TemporaryDirectory(
                prefix="code-change-", ignore_cleanup_errors=True
            ) as tmp:
                return await self._run(resolved, repo, Path(tmp).resolve(), on_event)
        configured.mkdir(parents=True, exist_ok=True)
        return await self._run(resolved, repo, configured, on_event)

    async def _run(
        self,
        change_input: ChangeInput,
        repo: Repository,
        work: Path,
        on_event: Callable[[ChangeEvent], None] | None,
    ) -> ChangeResult:
        return await run_change(
            change_input,
            repo,
            change_agent=self._change_agent,
            review_agent=self._review_agent,
            settings=self._settings,
            work=work,
            on_event=on_event,
        )
