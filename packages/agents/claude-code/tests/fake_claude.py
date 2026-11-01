"""A stand-in for the ``claude`` CLI, driven by FAKE_CLAUDE_* environment variables."""

import json
import os
import signal
import subprocess
import sys
import time

env = os.environ

if env.get("FAKE_CLAUDE_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

if early := env.get("FAKE_CLAUDE_EARLY_STDOUT"):
    # Fill the stdout pipe before reading any stdin.
    print("x" * int(early), flush=True)

stdin = "" if env.get("FAKE_CLAUDE_SKIP_STDIN") else sys.stdin.read()

if record := env.get("FAKE_CLAUDE_RECORD"):
    argv = sys.argv[1:]
    system_prompt = None
    if "--append-system-prompt-file" in argv:
        with open(argv[argv.index("--append-system-prompt-file") + 1]) as f:
            system_prompt = f.read()
    with open(record, "w") as f:
        json.dump(
            {
                "argv": argv,
                "stdin": stdin,
                "cwd": os.getcwd(),
                "system_prompt": system_prompt,
                "pid": os.getpid(),
            },
            f,
        )

if pid_file := env.get("FAKE_CLAUDE_CHILD_PID_FILE"):
    child = subprocess.Popen(["sleep", "60"])
    with open(pid_file, "w") as f:
        f.write(str(child.pid))

sys.stderr.write(env.get("FAKE_CLAUDE_STDERR", ""))
sys.stderr.flush()

if fixture := env.get("FAKE_CLAUDE_FIXTURE"):
    with open(fixture) as f:
        for line in f:
            print(line.rstrip("\n"), flush=True)

time.sleep(float(env.get("FAKE_CLAUDE_SLEEP", "0")))
sys.exit(int(env.get("FAKE_CLAUDE_EXIT", "0")))
