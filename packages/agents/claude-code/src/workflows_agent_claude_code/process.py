import asyncio
import contextlib
import os
import signal


async def stop_process_group(
    proc: asyncio.subprocess.Process, grace_seconds: float = 5.0
) -> None:
    """Stop the process and everything it started.

    The process must have been started with ``start_new_session=True``, so its
    pid is also its process group id. Sends SIGTERM to the group, waits up to
    ``grace_seconds`` for the process, then sends SIGKILL to whatever is left.
    """
    _signal_group(proc.pid, signal.SIGTERM)
    if proc.returncode is None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), grace_seconds)
    _signal_group(proc.pid, signal.SIGKILL)
    await proc.wait()


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, sig)
