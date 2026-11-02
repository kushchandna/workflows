"""Runs the end product's checks: shell commands whose exit code decides pass or fail."""

import asyncio
import os
import signal
from pathlib import Path

from workflows.code_change.models import CheckRun


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _drain(stream: asyncio.StreamReader, buffer: bytearray) -> None:
    while chunk := await stream.read(65536):
        buffer += chunk


async def _run_one(
    command: str, cwd: Path, timeout_seconds: float | None
) -> tuple[int | None, bool, bytes]:
    """Exit code (``None`` on a timeout), whether it timed out, and all output."""
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    assert proc.stdout is not None
    output = bytearray()
    reader = asyncio.create_task(_drain(proc.stdout, output))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout_seconds)
        except TimeoutError:
            timed_out = True
        finally:
            # Whatever the command left running must not keep the pipe open.
            _kill_group(proc.pid)
        if timed_out:
            await proc.wait()
        await reader
    finally:
        reader.cancel()
    return (None if timed_out else proc.returncode), timed_out, bytes(output)


async def run_checks(
    commands: list[str],
    cwd: Path,
    log_dir: Path,
    timeout_seconds: float | None,
    keep_chars: int,
) -> list[CheckRun]:
    """Runs every command in order, even after one fails.

    The full output of command ``k`` (from 1) goes to ``log_dir/<k>.log``. The result keeps
    only the last ``keep_chars`` characters.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    runs: list[CheckRun] = []
    for k, command in enumerate(commands, start=1):
        exit_code, timed_out, raw = await _run_one(command, cwd, timeout_seconds)
        (log_dir / f"{k}.log").write_bytes(raw)
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > keep_chars
        kept = text[len(text) - keep_chars :] if truncated else text
        runs.append(
            CheckRun(
                command=command,
                exit_code=exit_code,
                timed_out=timed_out,
                output=kept,
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
