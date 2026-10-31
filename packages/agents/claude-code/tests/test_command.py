from pathlib import Path

from workflows_agent import AgentRequest
from workflows_agent_claude_code.command import build_command

BASE = [
    "claude",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "--model",
    "haiku",
    "--permission-mode",
    "bypassPermissions",
]


def test_minimal_command():
    req = AgentRequest(prompt="do it", working_folder=Path("/w"))
    assert build_command("claude", "haiku", req, None) == BASE


def test_extra_folders_and_system_prompt_file():
    req = AgentRequest(
        prompt="p", working_folder=Path("/w"), extra_folders=[Path("/a"), Path("/b")]
    )
    assert build_command("claude", "haiku", req, Path("/tmp/sp.txt")) == BASE + [
        "--add-dir",
        "/a",
        "--add-dir",
        "/b",
        "--append-system-prompt-file",
        "/tmp/sp.txt",
    ]


def test_prompt_never_in_argv():
    req = AgentRequest(
        prompt="SECRET-PROMPT", working_folder=Path("/w"), system_prompt="SECRET-SYS"
    )
    argv = build_command("claude", "haiku", req, Path("/tmp/sp.txt"))
    assert not any("SECRET" in a for a in argv)
