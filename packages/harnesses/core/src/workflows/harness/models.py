from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class HarnessRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: str = Field(min_length=1)
    working_folder: Path
    extra_folders: list[Path] = []
    system_prompt: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class HarnessResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    final_message: str
    session_id: str | None = None
    duration_seconds: float
    usage: Usage = Usage()
