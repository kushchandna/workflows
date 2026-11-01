import asyncio
import contextlib
import os
import signal


async def wait_for_exit(
    proc: asyncio.subprocess.Process, poll_seconds: float = 0.05
) -> int:
    """Wait until the process itself has exited and return its exit code.

    On Python 3.11, ``Process.wait()`` also waits for the process's pipes to
    close, which never happens while a leftover child still holds them.
    """
    while proc.returncode is None:
        await asyncio.sleep(poll_seconds)
    return proc.returncode


async def stop_process_group(
    proc: asyncio.subprocess.Process, grace_seconds: float = 5.0
) -> None:
    """Stop the process and everything it started.

    The process must have been started with ``start_new_session=True``, so its
    pid is also its process group id. Sends SIGTERM to the group, waits up to
    ``grace_seconds`` for the process, then sends SIGKILL to whatever is left.
    """
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wait_for_exit(proc), grace_seconds)
    finally:
        # Also on cancellation during the grace period: never leave it running.
        _signal_group(proc.pid, signal.SIGKILL)
    await wait_for_exit(proc)


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, sig)
