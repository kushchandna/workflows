import re
from pathlib import Path

import pytest

from workflows.code_review import ReviewPass
from workflows.code_review.prompts import (
    PromptContext,
    consolidation_prompt,
    pass_prompt,
    per_file_prompt,
)

CTX = PromptContext(
    work=Path("/w"),
    codebase=Path("/repo"),
    description="Adds retry",
    instructions="Prefer small functions",
    change_author="alice",
    reviewer_name="bot",
)
FIXED = ["Output folder", "Result file", "Threads you may act on"]


def line_values(prompt: str, label: str) -> list[str]:
    return re.findall(rf"^{label}: (.+)$", prompt, re.MULTILINE)


def test_per_file_prompt_lines():
    out = "/w/agent-output/per-file/batch-1/attempt-1"
    p = per_file_prompt(CTX, Path(out), [(1, "a.py"), (2, "b/c.py")], ["t1"])
    assert line_values(p, "Output folder") == [out]
    assert re.findall(r"^Slot (\d+): (.+)$", p, re.MULTILINE) == [
        ("1", "a.py"),
        ("2", "b/c.py"),
    ]
    assert line_values(p, "Threads you may act on") == ["t1"]
    assert line_values(p, "Result file") == []
    for text in [
        "Adds retry",
        "Prefer small functions",
        "alice",
        "bot",
        "/w/input/diffs",
        "/w/input/threads.json",
        "/w/review-passes",
        "/repo",
        f"{out}/1.json",
    ]:
        assert text in p


@pytest.mark.parametrize(
    "review_pass",
    [ReviewPass.OVERALL, ReviewPass.TEST_COVERAGE, ReviewPass.CODE_PATTERNS],
)
def test_pass_prompts_have_result_file_and_no_slots(review_pass):
    out = Path(f"/w/agent-output/{review_pass.value}/attempt-1")
    p = pass_prompt(CTX, review_pass, out, ["a.py"], ["b.py"], [])
    assert line_values(p, "Result file") == [f"{out}/result.json"]
    assert line_values(p, "Output folder") == [str(out)]
    assert not re.findall(r"^Slot ", p, re.MULTILINE)
    assert line_values(p, "Threads you may act on") == ["none"]
    assert "a.py" in p


def test_overall_prompt_lists_unreviewed_files():
    assert "b.py" in pass_prompt(
        CTX, ReviewPass.OVERALL, Path("/o"), ["a.py"], ["b.py"], []
    )


def test_consolidation_prompt():
    out = Path("/w/agent-output/consolidation/attempt-1")
    p = consolidation_prompt(CTX, out, ["t3"], "files: 1 unreviewed")
    assert "files: 1 unreviewed" in p and "/w/review-passes" in p
    assert line_values(p, "Threads you may act on") == ["t3"]
    assert line_values(p, "Result file") == [f"{out}/result.json"]


def test_missing_text_shows_none_and_no_template_leftovers():
    ctx = PromptContext(
        work=Path("/w"),
        codebase=None,
        description=None,
        instructions=None,
        change_author=None,
        reviewer_name=None,
    )
    prompts = [
        per_file_prompt(ctx, Path("/o"), [(1, "a.py")], []),
        pass_prompt(ctx, ReviewPass.OVERALL, Path("/o"), ["a.py"], [], []),
        pass_prompt(ctx, ReviewPass.TEST_COVERAGE, Path("/o"), ["a.py"], [], []),
        pass_prompt(ctx, ReviewPass.CODE_PATTERNS, Path("/o"), ["a.py"], [], []),
        consolidation_prompt(ctx, Path("/o"), [], "nothing"),
    ]
    for p in prompts:
        assert "(none)" in p
        assert "$" not in p
        for label in FIXED:
            assert len(line_values(p, label)) <= 1


def test_text_with_dollar_signs_is_inserted_verbatim():
    ctx = PromptContext(
        work=Path("/w"),
        codebase=None,
        description="costs $5 and $name",
        instructions=None,
        change_author=None,
        reviewer_name=None,
    )
    assert "costs $5 and $name" in per_file_prompt(ctx, Path("/o"), [(1, "a.py")], [])


def test_prompt_says_how_to_write_paths():
    p = per_file_prompt(CTX, Path("/o"), [(1, "a.py")], [])
    assert 'no leading "./"' in p


def test_per_file_prompt_keeps_targets_inside_the_batch():
    # validation only accepts per-file targets in the batch, so the prompt must not invite more
    p = per_file_prompt(CTX, Path("/o"), [(1, "a.py")], [])
    assert "unchanged file" not in p
    assert "must be a file in this batch" in p


@pytest.mark.parametrize(
    "review_pass",
    [ReviewPass.OVERALL, ReviewPass.TEST_COVERAGE, ReviewPass.CODE_PATTERNS],
)
def test_other_passes_may_point_at_unchanged_files(review_pass):
    p = pass_prompt(CTX, review_pass, Path("/o"), ["a.py"], [], [])
    assert "unchanged files" in p


def test_paths_are_relative_to_the_codebase_folder():
    # git mode reports paths relative to the codebase folder, which may be a repo subfolder
    p = per_file_prompt(CTX, Path("/o"), [(1, "a.py")], [])
    assert "relative to the codebase folder" in p
    assert "repository root" not in p


def test_consolidation_status_only_blocks_on_real_problems():
    p = " ".join(
        consolidation_prompt(CTX, Path("/o"), [], "nothing").split()
    )  # unwrap lines
    assert "suggestions and small remarks alone do not block" in p
    assert "must be fixed before the change can be accepted" in p
    # an earlier pass's resolve or reopen decides whether the reviewer's own thread still blocks
    assert "after applying the thread actions" in p
