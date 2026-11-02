import asyncio
import os
import shutil
import time

import pytest

from workflows.code_change.checks import checks_reason, run_checks


async def run(tmp_path, commands, timeout=None, keep=20000):
    return await run_checks(commands, tmp_path, tmp_path / "logs", timeout, keep)


async def test_runs_every_check_in_order(tmp_path):
    runs = await run(tmp_path, ["echo one", "exit 3", "echo two >&2"])
    assert [(r.command, r.exit_code) for r in runs] == [
        ("echo one", 0),
        ("exit 3", 3),
        ("echo two >&2", 0),
    ]
    assert runs[0].output == "one\n" and runs[2].output == "two\n"
    assert (tmp_path / "logs" / "1.log").read_bytes() == b"one\n"
    assert checks_reason(runs) == "exit 3 exited with 3"


async def test_runs_in_cwd(tmp_path):
    (tmp_path / "marker").write_text("")
    [r] = await run(tmp_path, ["test -f marker"])
    assert r.exit_code == 0


async def test_missing_command_fails(tmp_path):
    [r] = await run(tmp_path, ["no-such-command-xyz"])
    assert r.exit_code == 127


async def test_timeout(tmp_path):
    [r] = await run(tmp_path, ["sleep 5"], timeout=0.2)
    assert (r.exit_code, r.timed_out) == (None, True)
    assert checks_reason([r]) == "sleep 5 timed out"


async def test_timeout_kills_process_group(tmp_path):
    start = time.monotonic()
    [r] = await run(tmp_path, ["(sleep 30 &) ; sleep 30"], timeout=0.2)
    assert r.timed_out and time.monotonic() - start < 5


async def test_background_child_does_not_hold_the_run(tmp_path):
    start = time.monotonic()
    [r] = await run(tmp_path, ["(sleep 30 &) ; echo done"])
    assert (r.exit_code, r.output) == (0, "done\n") and time.monotonic() - start < 5


async def test_output_kept_from_the_end(tmp_path):
    [r] = await run(tmp_path, ["printf abcdef"], keep=3)
    assert (r.output, r.truncated) == ("def", True)
    assert (tmp_path / "logs" / "1.log").read_bytes() == b"abcdef"


async def test_keep_zero(tmp_path):
    [r] = await run(tmp_path, ["printf abc"], keep=0)
    assert (r.output, r.truncated) == ("", True)


async def test_non_utf8_output(tmp_path):
    [r] = await run(tmp_path, ["printf '\\377ok'"])
    assert r.output == "�ok"


def test_reason_none_when_all_pass():
    assert checks_reason([]) is None


needs_setsid = pytest.mark.skipif(shutil.which("setsid") is None, reason="needs setsid")


@needs_setsid
async def test_escaped_child_does_not_hold_the_run(tmp_path):
    start = time.monotonic()
    [r] = await run(tmp_path, ["setsid sleep 30 & echo started"])
    assert (r.exit_code, r.output) == (0, "started\n")
    assert time.monotonic() - start < 10


@needs_setsid
async def test_escaped_child_does_not_outlast_the_timeout(tmp_path):
    start = time.monotonic()
    [r] = await run(tmp_path, ["echo start; setsid sleep 30 & sleep 30"], timeout=0.3)
    assert (r.timed_out, r.output) == (True, "start\n")
    assert time.monotonic() - start < 10


async def test_output_before_a_timeout_is_kept(tmp_path):
    [r] = await run(tmp_path, ["echo partial; sleep 5"], timeout=0.3)
    assert (r.timed_out, r.output, r.truncated) == (True, "partial\n", False)
    assert (tmp_path / "logs" / "1.log").read_bytes() == b"partial\n"


async def test_truncation_counts_characters_not_bytes(tmp_path):
    [r] = await run(tmp_path, ["printf 'h\\303\\251llo\\342\\202\\254'"], keep=2)
    assert (r.output, r.truncated) == ("o\u20ac", True)


async def test_output_that_fits_exactly_is_not_truncated(tmp_path):
    [r] = await run(tmp_path, ["printf abc"], keep=3)
    assert (r.output, r.truncated) == ("abc", False)


async def test_large_output_is_streamed_to_the_log(tmp_path):
    [r] = await run(tmp_path, ["yes x | head -c 3000000"], keep=10)
    assert (r.output, r.truncated) == ("x\nx\nx\nx\nx\n", True)
    assert (tmp_path / "logs" / "1.log").stat().st_size == 3000000


async def test_cancellation_kills_the_check(tmp_path):
    pid_file = tmp_path / "pid"
    task = asyncio.create_task(run(tmp_path, [f"echo $$ > {pid_file}; sleep 30"]))
    while not (pid_file.exists() and pid_file.read_text().strip()):
        await asyncio.sleep(0.05)
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("the check is still running")
