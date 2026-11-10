import re
from pathlib import Path

import pytest

from workflows.code_review import BUILTIN_AGENTS, ReviewAgent
from workflows.code_review.prompts import (
    PromptContext,
    agent_prompt,
    consolidation_prompt,
    overall_prompt,
    per_file_prompt,
)

CTX = PromptContext(
    work=Path("/w"),
    codebase=Path("/repo"),
    description="Adds retry",
    instructions="Prefer small functions",
    change_author="alice",
    reviewer_name="bot",
    agents=("test-coverage", "security"),
)
FIXED = ["Output folder", "Result file", "Threads you may act on"]


def line_values(prompt: str, label: str) -> list[str]:
    return re.findall(rf"^{label}: (.+)$", prompt, re.MULTILINE)


def test_per_file_prompt_lines():
    out = "/w/harness-output/per-file/batch-1/attempt-1"
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
        "/w/results",
        "/repo",
        f"{out}/1.json",
    ]:
        assert text in p


def single_result_prompts(ctx, out):
    """The overall prompt, and one prompt per built-in agent."""
    return [
        overall_prompt(ctx, out, ["a.py"], ["b.py"], []),
        *(agent_prompt(ctx, a, out, ["a.py"]) for a in BUILTIN_AGENTS.values()),
    ]


@pytest.mark.parametrize("index", range(1 + len(BUILTIN_AGENTS)))
def test_single_result_prompts_have_result_file_and_no_slots(index):
    out = Path("/w/harness-output/x/attempt-1")
    p = single_result_prompts(CTX, out)[index]
    assert line_values(p, "Result file") == [f"{out}/result.json"]
    assert line_values(p, "Output folder") == [str(out)]
    assert not re.findall(r"^Slot ", p, re.MULTILINE)
    assert line_values(p, "Threads you may act on") == ["none"]
    assert "a.py" in p


def test_overall_prompt_lists_unreviewed_files():
    assert "b.py" in overall_prompt(CTX, Path("/o"), ["a.py"], ["b.py"], [])


def test_agent_prompt_carries_the_agents_focus_verbatim():
    agent = ReviewAgent(name="layout", instructions="Check the $layout of {screens}.")
    p = agent_prompt(CTX, agent, Path("/o"), ["a.py"])
    assert "# Review agent: layout" in p
    assert "Check the $layout of {screens}." in p
    assert line_values(p, "Threads you may act on") == ["none"]
    assert '"thread_actions" must be empty' in p


def test_per_file_prompt_leaves_the_agents_focus_to_them():
    p = per_file_prompt(CTX, Path("/o"), [(1, "a.py")], [])
    assert "- problems that span several files;\n" in p
    assert "each named for its focus: test-coverage, security." in " ".join(p.split())
    alone = PromptContext(**{**CTX.__dict__, "agents": ()})
    p = per_file_prompt(alone, Path("/o"), [(1, "a.py")], [])
    assert "review agent" not in p
    assert "- problems that span several files.\n" in p


def test_agent_prompt_names_the_other_agents():
    agent = ReviewAgent(name="security", instructions="x")
    p = agent_prompt(CTX, agent, Path("/o"), ["a.py"])
    assert (
        "each named for its focus, and their focus is theirs:\n- test-coverage\n" in p
    )
    alone = PromptContext(**{**CTX.__dict__, "agents": ("security",)})
    assert "No other review agents run." in agent_prompt(alone, agent, Path("/o"), [])


def test_consolidation_prompt():
    out = Path("/w/harness-output/consolidation/attempt-1")
    p = consolidation_prompt(CTX, out, ["t3"], "files: 1 unreviewed")
    assert "files: 1 unreviewed" in p and "/w/results" in p
    assert (
        "- agents/test-coverage/result.json: from the test-coverage review agent;" in p
    )
    assert "- agents/security/result.json: from the security review agent;" in p
    assert "- overall/result.json.\n" in p
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
        *single_result_prompts(ctx, Path("/o")),
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


@pytest.mark.parametrize("index", range(1 + len(BUILTIN_AGENTS)))
def test_overall_and_agents_may_point_at_unchanged_files(index):
    assert "unchanged files" in single_result_prompts(CTX, Path("/o"))[index]


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
    # an earlier stage's resolve or reopen decides whether the reviewer's own thread still blocks
    assert "after applying the thread actions" in p
