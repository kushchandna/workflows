"""Checking agent results: which comments and thread actions are usable."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeVar

from pydantic import BaseModel

from workflows.code_review.formats import PerFileResult
from workflows.code_review.models import (
    ChangeTarget,
    Comment,
    LinesTarget,
    Target,
    ThreadAction,
)

M = TypeVar("M", bound=BaseModel)

NOT_AVAILABLE = "thread not available to this pass"


@dataclass(frozen=True)
class Scope:
    changed_paths: frozenset[str]  # every ChangedFile.paths, all files
    allowed_paths: (
        frozenset[str] | None
    )  # per-file: the batch's paths; None = any valid path
    codebase: Path | None
    thread_ids: frozenset[str]  # threads this call may reply to or act on


@dataclass
class Checked:
    comments: list[Comment]  # valid ones, plus invalid ones repaired
    thread_actions: list[ThreadAction]  # valid ones only
    dropped: list[tuple[ThreadAction, str]]  # (action, reason)
    had_invalid: bool  # any comment or action was invalid


def claimed_location(target: Target) -> str:
    if isinstance(target, LinesTarget):
        lines = (
            str(target.start_line)
            if target.start_line == target.end_line
            else f"{target.start_line}-{target.end_line}"
        )
        return f"{target.path}:{lines}" + (" (old)" if target.side == "old" else "")
    if isinstance(target, ChangeTarget):
        return "change"
    return target.path


def _line_count(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


def target_problem(target: Target, scope: Scope) -> str | None:
    if isinstance(target, ChangeTarget):
        return None
    path = PurePosixPath(target.path)
    if path.is_absolute() or ".." in path.parts:
        return f"path leaves the codebase: {target.path}"
    if str(path) != target.path:  # "./a.py", "a.py/", "a//b.py", ""
        return f"path is not in standard form: {target.path!r}"
    codebase_file = scope.codebase / target.path if scope.codebase else None
    if scope.allowed_paths is not None:
        if target.path not in scope.allowed_paths:
            return f"file is not part of this call: {target.path}"
    elif target.path not in scope.changed_paths and not (
        codebase_file is not None and codebase_file.is_file()
    ):
        return f"file is not changed and does not exist: {target.path}"
    if isinstance(target, LinesTarget):
        if target.start_line < 1 or target.end_line < target.start_line:
            return f"invalid line range {target.start_line}-{target.end_line}"
        if target.side == "new" and codebase_file is not None:
            count = _line_count(codebase_file)
            if count is None or target.end_line > count:
                return f"line {target.end_line} is past the end of {target.path}"
    return None


def action_problem(action: ThreadAction, scope: Scope) -> str | None:
    return None if action.thread_id in scope.thread_ids else NOT_AVAILABLE


def check_items(
    comments: list[Comment], actions: list[ThreadAction], scope: Scope
) -> Checked:
    """Keep valid items and repair invalid comments instead of losing them.

    A comment with a bad target moves to the whole-change level, with its claimed location
    in the body; its ``reply_to`` is kept if that is valid. A comment with only a bad
    ``reply_to`` loses the ``reply_to``. A reply to a thread this call may act on is kept
    as it is: its target is the thread's own position, which may be out of date after
    the change moved on, and the end product posts it by thread id. A thread action
    outside ``scope.thread_ids`` is dropped.
    """
    result = Checked(comments=[], thread_actions=[], dropped=[], had_invalid=False)
    for comment in comments:
        reply_ok = comment.reply_to is None or comment.reply_to in scope.thread_ids
        reply_to = comment.reply_to if reply_ok else None
        if comment.reply_to is not None and reply_ok:
            result.comments.append(comment)
        elif target_problem(comment.target, scope):
            result.had_invalid = True
            result.comments.append(
                Comment(
                    target=ChangeTarget(),
                    body=f"{claimed_location(comment.target)}: {comment.body}",
                    reply_to=reply_to,
                )
            )
        elif not reply_ok:
            result.had_invalid = True
            result.comments.append(comment.model_copy(update={"reply_to": None}))
        else:
            result.comments.append(comment)
    for action in actions:
        if problem := action_problem(action, scope):
            result.had_invalid = True
            result.dropped.append((action, problem))
        else:
            result.thread_actions.append(action)
    return result


def read_result(path: Path, model: type[M]) -> M | None:
    """The parsed result file, or ``None`` if it is missing, not JSON, or off-schema."""
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValidationError is a ValueError
        return None


def usable_per_file(result: PerFileResult | None, expected_path: str) -> bool:
    return (
        result is not None
        and result.path == expected_path
        and bool(result.summary.strip())
    )
