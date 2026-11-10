import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from workflows.harness import (
    EventHandler,
    Harness,
    HarnessError,
    HarnessRequest,
    HarnessResult,
    HarnessTimeoutError,
    UsageEvent,
)
from workflows.harness_claude_code.command import build_command
from workflows.harness_claude_code.lines import iter_lines
from workflows.harness_claude_code.parse import events_from_line, usage_from_result
from workflows.harness_claude_code.process import stop_process_group, wait_for_exit


class ClaudeCodeHarness(Harness):
    """Runs each call as a ``claude -p`` process in bypass permission mode.

    The machine's own Claude Code setup (login, settings, CLAUDE.md files, MCP
    servers, plugins) applies to every call.
    """

    def __init__(self, model: str, claude_path: str | Path = "claude") -> None:
        self.model = model
        self.claude_path = str(claude_path)

    async def run(
        self, request: HarnessRequest, on_event: EventHandler | None = None
    ) -> HarnessResult:
        if not request.working_folder.is_dir():
            raise HarnessError(
                f"working folder does not exist: {request.working_folder}"
            )
        call_id = uuid4().hex
        start = time.monotonic()
        system_prompt_file = _write_temp(request.system_prompt)
        try:
            return await self._run(
                request, on_event, call_id, start, system_prompt_file
            )
        finally:
            if system_prompt_file is not None:
                system_prompt_file.unlink(missing_ok=True)

    async def _run(
        self,
        request: HarnessRequest,
        on_event: EventHandler | None,
        call_id: str,
        start: float,
        system_prompt_file: Path | None,
    ) -> HarnessResult:
        command = build_command(
            self.claude_path, self.model, request, system_prompt_file
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=request.working_folder,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as e:
            raise HarnessError(
                f"claude executable not found or not runnable: {self.claude_path}"
            ) from e
        assert proc.stdin and proc.stdout and proc.stderr
        stderr = bytearray()
        stderr_task = asyncio.create_task(_collect(proc.stderr, stderr))
        # Write the prompt while reading stdout, so neither pipe can fill up
        # and block both sides.
        writer = asyncio.create_task(
            _write_and_close(proc.stdin, request.prompt.encode())
        )
        # Once claude exits, stop whatever it left running. Leftover children
        # can hold stdout open, and reading would otherwise wait for them.
        reaper = asyncio.create_task(_stop_after_exit(proc))

        result: dict[str, Any] | None = None
        session_id: str | None = None
        timeout = asyncio.timeout(request.timeout_seconds)
        try:
            async with timeout:
                async for line in iter_lines(proc.stdout):
                    for event in events_from_line(line, call_id):
                        if isinstance(event.raw, dict) and event.raw.get("session_id"):
                            session_id = event.raw["session_id"]
                        if isinstance(event, UsageEvent):
                            result = event.raw
                        if on_event is not None:
                            on_event(event)
                await writer
                await reaper
                await stderr_task
        except TimeoutError as e:
            if not timeout.expired():
                raise
            raise HarnessTimeoutError(
                f"call timed out after {request.timeout_seconds} seconds",
                stderr=_text(stderr),
                session_id=session_id,
            ) from e
        finally:
            writer.cancel()
            reaper.cancel()
            stderr_task.cancel()
            await stop_process_group(proc)

        if result is not None and result.get("is_error"):
            message = str(result.get("result") or "claude reported an error")
        elif proc.returncode != 0:
            message = f"claude exited with code {proc.returncode}"
        elif result is None:
            message = "claude ended without a result"
        else:
            return HarnessResult(
                call_id=call_id,
                final_message=str(result.get("result") or ""),
                session_id=session_id,
                duration_seconds=time.monotonic() - start,
                usage=usage_from_result(result),
            )
        raise HarnessError(message, stderr=_text(stderr), session_id=session_id)


async def _write_and_close(stdin: asyncio.StreamWriter, data: bytes) -> None:
    try:
        stdin.write(data)
        await stdin.drain()
        stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass  # claude exited without reading; its exit code tells why


async def _collect(stream: asyncio.StreamReader, into: bytearray) -> None:
    while chunk := await stream.read(65536):
        into += chunk


def _text(data: bytearray) -> str | None:
    return data.decode("utf-8", errors="replace") or None


async def _stop_after_exit(proc: asyncio.subprocess.Process) -> None:
    await wait_for_exit(proc)
    await stop_process_group(proc)


def _write_temp(text: str | None) -> Path | None:
    if text is None:
        return None
    fd, path = tempfile.mkstemp(prefix="claude-system-prompt-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return Path(path)
