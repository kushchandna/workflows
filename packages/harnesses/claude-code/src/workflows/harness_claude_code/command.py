from pathlib import Path

from workflows.harness import HarnessRequest


def build_command(
    claude_path: str,
    model: str,
    request: HarnessRequest,
    system_prompt_file: Path | None,
) -> list[str]:
    """The ``claude`` command line for one call. The prompt goes to stdin, not here."""
    command = [
        claude_path,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--permission-mode",
        "bypassPermissions",
    ]
    for folder in request.extra_folders:
        command += ["--add-dir", str(folder)]
    if system_prompt_file is not None:
        command += ["--append-system-prompt-file", str(system_prompt_file)]
    return command
