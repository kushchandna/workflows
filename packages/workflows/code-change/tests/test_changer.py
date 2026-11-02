import pytest

from workflows.agent.testing import FakeAgent
from workflows.code_change import (
    ChangeInput,
    ChangeInputError,
    ChangeSettings,
    CodeChanger,
)


async def test_blank_instruction(tmp_path):
    agent = FakeAgent()
    with pytest.raises(ChangeInputError, match="instruction is empty"):
        await CodeChanger(agent).change(
            ChangeInput(codebase=tmp_path, instruction="  \n")
        )
    assert agent.requests == []


async def test_work_folder_inside_codebase(tmp_path):
    changer = CodeChanger(
        FakeAgent(), settings=ChangeSettings(work_folder=tmp_path / "w")
    )
    with pytest.raises(ChangeInputError, match="must not be inside the codebase"):
        await changer.change(ChangeInput(codebase=tmp_path, instruction="x"))


async def test_work_folder_not_empty(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    (w / "old").write_text("")
    changer = CodeChanger(FakeAgent(), settings=ChangeSettings(work_folder=w))
    with pytest.raises(ChangeInputError, match="must be empty or missing"):
        await changer.change(ChangeInput(codebase=tmp_path / "code", instruction="x"))


async def test_bad_codebase_leaves_no_work_folder(tmp_path):
    w = tmp_path / "w"
    changer = CodeChanger(FakeAgent(), settings=ChangeSettings(work_folder=w))
    with pytest.raises(ChangeInputError, match="codebase does not exist"):
        await changer.change(
            ChangeInput(codebase=tmp_path / "missing", instruction="x")
        )
    assert not w.exists()
