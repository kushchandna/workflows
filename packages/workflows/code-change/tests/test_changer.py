import tempfile

import pytest

from workflows.code_change import (
    ChangeInput,
    ChangeInputError,
    ChangeSettings,
    CodeChanger,
)
from workflows.harness.testing import FakeHarness


async def test_blank_instruction(tmp_path):
    harness = FakeHarness()
    with pytest.raises(ChangeInputError, match="instruction is empty"):
        await CodeChanger(harness).change(
            ChangeInput(codebase=tmp_path, instruction="  \n")
        )
    assert harness.requests == []


async def test_work_folder_inside_codebase(tmp_path):
    changer = CodeChanger(
        FakeHarness(), settings=ChangeSettings(work_folder=tmp_path / "w")
    )
    with pytest.raises(ChangeInputError, match="must not be inside the codebase"):
        await changer.change(ChangeInput(codebase=tmp_path, instruction="x"))


async def test_work_folder_not_empty(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    (w / "old").write_text("")
    changer = CodeChanger(FakeHarness(), settings=ChangeSettings(work_folder=w))
    with pytest.raises(ChangeInputError, match="must be empty or missing"):
        await changer.change(ChangeInput(codebase=tmp_path / "code", instruction="x"))


async def test_bad_codebase_leaves_no_work_folder(tmp_path):
    w = tmp_path / "w"
    changer = CodeChanger(FakeHarness(), settings=ChangeSettings(work_folder=w))
    with pytest.raises(ChangeInputError, match="codebase does not exist"):
        await changer.change(
            ChangeInput(codebase=tmp_path / "missing", instruction="x")
        )
    assert not w.exists()


async def test_temp_folder_inside_codebase(tmp_path, monkeypatch):
    code = tmp_path / "code"
    (code / "tmp").mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(code / "tmp"))
    harness = FakeHarness()
    with pytest.raises(
        ChangeInputError, match="temporary folder is inside the codebase"
    ):
        await CodeChanger(harness).change(ChangeInput(codebase=code, instruction="x"))
    assert harness.requests == []


async def test_temp_folder_is_not_used_when_a_work_folder_is_given(
    tmp_path, monkeypatch
):
    code = tmp_path / "code"
    (code / "tmp").mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(code / "tmp"))
    changer = CodeChanger(
        FakeHarness(), settings=ChangeSettings(work_folder=tmp_path / "w")
    )
    with pytest.raises(ChangeInputError, match="not a git work tree"):
        await changer.change(ChangeInput(codebase=code, instruction="x"))
