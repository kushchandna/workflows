"""Runs the end product's checks: shell commands whose exit code decides pass or fail."""

import asyncio
import os
import signal
from pathlib import Path
from typing import BinaryIO

from workflows.code_change.models import CheckRun

# How long to wait for the end of a check's output after its process group is gone.
_DRAIN_GRACE_SECONDS = 1.0


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _wait_for_exit(
    proc: asyncio.subprocess.Process, reader: asyncio.Task[None] | None = None
) -> None:
    """Waits for the process itself to exit.

    On Python 3.11, ``Process.wait()`` also waits for the process's pipes to close, which
    never happens while a leftover child still holds them.

    If the reader fails (the log can't be written), nothing drains the pipe any more and the
    process would block on its next write until the timeout, so the failure is raised at once.
    """
    while proc.returncode is None:
        if reader is not None and reader.done() and not reader.cancelled():
            error = reader.exception()
            if error is not None:
                raise error
        await asyncio.sleep(0.05)


class _Output:
    """Streams a check's output to its log and keeps only a bounded tail in memory."""

    def __init__(self, log: BinaryIO, keep_chars: int) -> None:
        self._log = log
        self._keep_chars = keep_chars
        # A character takes at most 4 bytes; the extra bytes cover a cut at the front.
        self._limit = 4 * keep_chars + 8
        self._tail = bytearray()
        self._total = 0

    def add(self, chunk: bytes) -> None:
        self._log.write(chunk)
        self._total += len(chunk)
        self._tail += chunk
        if len(self._tail) > self._limit:
            del self._tail[: len(self._tail) - self._limit]

    @property
    def buffered(self) -> int:
        """How many bytes of output are held in memory."""
        return len(self._tail)

    def kept(self) -> tuple[str, bool]:
        """The last ``keep_chars`` characters, and whether anything was cut."""
        text = self._tail.decode("utf-8", errors="replace")
        dropped_bytes = self._total > len(self._tail)
        truncated = dropped_bytes or len(text) > self._keep_chars
        return text[max(len(text) - self._keep_chars, 0) :], truncated


async def _drain(stream: asyncio.StreamReader, output: _Output) -> None:
    while chunk := await stream.read(65536):
        output.add(chunk)


async def _finish_reader(reader: asyncio.Task[None]) -> None:
    """Gives the reader a moment to reach the end of the output.

    A descendant that left the process group (``setsid``, ``timeout``) still holds the pipe
    after the group is killed. Waiting for it would hang the run for as long as it lives.
    """
    try:
        await asyncio.wait_for(reader, _DRAIN_GRACE_SECONDS)
    except TimeoutError:
        pass


def _close_transport(proc: asyncio.subprocess.Process) -> None:
    """Lets go of the pipe, so an escaped descendant doesn't keep a descriptor open here.

    asyncio has no public way to do this for a subprocess's pipes.
    """
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        transport.close()


async def _run_one(
    command: str,
    cwd: Path,
    timeout_seconds: float | None,
    log_path: Path,
    keep_chars: int,
) -> tuple[int | None, bool, str, bool]:
    """Exit code (``None`` on a timeout), whether it timed out, and the kept output."""
    with log_path.open("wb") as log:
        output = _Output(log, keep_chars)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            # A check must not read, or hold up, the caller's own input.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        assert proc.stdout is not None
        reader = asyncio.create_task(_drain(proc.stdout, output))
        timed_out = False
        try:
            try:
                await asyncio.wait_for(_wait_for_exit(proc, reader), timeout_seconds)
            except TimeoutError:
                timed_out = True
            finally:
                # Whatever the command left running in its group must not keep the pipe open.
                _kill_group(proc.pid)
            if timed_out:
                await _wait_for_exit(proc)
            await _finish_reader(reader)
        finally:
            reader.cancel()
            _close_transport(proc)
    text, truncated = output.kept()
    return (None if timed_out else proc.returncode), timed_out, text, truncated


async def run_checks(
    commands: list[str],
    cwd: Path,
    log_dir: Path,
    timeout_seconds: float | None,
    keep_chars: int,
) -> list[CheckRun]:
    """Runs every command in order, even after one fails.

    The full output of command ``k`` (from 1) goes to ``log_dir/<k>.log`` as it arrives. The
    result keeps only the last ``keep_chars`` characters.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    runs: list[CheckRun] = []
    for k, command in enumerate(commands, start=1):
        exit_code, timed_out, text, truncated = await _run_one(
            command, cwd, timeout_seconds, log_dir / f"{k}.log", keep_chars
        )
        runs.append(
            CheckRun(
                command=command,
                exit_code=exit_code,
                timed_out=timed_out,
                output=text,
                truncated=truncated,
            )
        )
    return runs


def checks_reason(runs: list[CheckRun]) -> str | None:
    """Why the checks failed, or ``None`` when every one exited with 0."""
    failures = [
        f"{r.command} timed out"
        if r.timed_out
        else f"{r.command} exited with {r.exit_code}"
        for r in runs
        if r.timed_out or r.exit_code != 0
    ]
    return "; ".join(failures) or None
