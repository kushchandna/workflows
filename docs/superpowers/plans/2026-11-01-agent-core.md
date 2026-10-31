# Agent Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `workflows-agent` core package: the generic agent interface, its request/result/event types, its errors, and a fake agent for workflow tests. Also set up the uv workspace it lives in.

**Architecture:** The package holds types and one abstract class, with no tool-specific code. `Agent.run(request, on_event)` is the only call a workflow makes. Events are a Pydantic discriminated union, so end products can store logs as JSON and read them back. `FakeAgent` implements the interface for tests and copies the real agents' behaviour around call ids, events, timeouts and errors.

**Tech Stack:** Python 3.11+, uv workspace, Pydantic v2, pytest + pytest-asyncio, ruff, pyright, hatchling.

**Spec:** `docs/agents/requirements.md` (on the `design` branch). The repo structure comes from `docs/superpowers/specs/2026-10-31-project-structure-design.md` (also on the `design` branch).

## Branches

- Create `dev` from `main` if it doesn't exist yet, and push it.
- Do this work on `agent-core`, branched from `dev`. When it's finished, merge it into `dev`. Never merge into `main`. The user does that.

## Global Constraints

- `requires-python = ">=3.11"`.
- Distribution name `workflows-agent`, import path `workflows.agent`, package folder `packages/agents/core/`.
- There is no `__init__.py` at `src/workflows/`. `workflows` is a PEP 420 namespace package.
- The core package's only runtime dependency is `pydantic>=2`.
- Packages depend on each other only through `[tool.uv.sources] <name> = { workspace = true }`.
- Commit messages are short and plain, with no AI or Claude attribution trailers.
- Run every command from the repo root unless a step says otherwise.

## Review Focus

- **Parallel calls on one agent.** Each call's events must carry that call's own id, even when the events were scripted once at construction. Covered by `test_parallel_calls_have_distinct_ids` (Task 4).
- **`on_event` raising mid-stream.** The call fails with that same exception and isn't swallowed. Covered by `test_on_event_error_fails_call` (Task 4).
- **A zero or negative timeout.** It's rejected when the request is built, not discovered later as an instant timeout. Covered by `test_request_rejects_non_positive_timeout` (Task 1).
- **Stored logs.** An event dumped to JSON parses back to the same event type through `Event`. Covered by `test_event_json_round_trip` (Task 2).
- **Installing the core package on its own as a wheel.** `import workflows.agent` must work without a `workflows/__init__.py`. Covered by the isolated install check (Task 5).

---

### Task 1: Workspace and core models

**Files:**
- Create: `pyproject.toml` (workspace root)
- Create: `.gitignore`
- Create: `packages/agents/core/pyproject.toml`
- Create: `packages/agents/core/src/workflows/agent/__init__.py`
- Create: `packages/agents/core/src/workflows/agent/models.py`
- Create: `packages/agents/core/src/workflows/agent/py.typed` (empty)
- Test: `packages/agents/core/tests/test_models.py`

**Interfaces:**
- Produces (`workflows.agent.models`, re-exported from `workflows.agent`):
  - `class PermissionMode(str, Enum)` with `AUTO = "auto"` and `BYPASS = "bypass"`.
  - `class AgentRequest(BaseModel)`:
    - `prompt: str` (min length 1)
    - `working_folder: Path`
    - `extra_folders: list[Path] = []`
    - `system_prompt: str | None = None`
    - `timeout_seconds: float | None = None` (must be > 0)
  - `class Usage(BaseModel)`: `input_tokens: int | None = None`, `output_tokens: int | None = None`, `cost_usd: float | None = None`.
  - `class AgentResult(BaseModel)`: `call_id: str`, `final_message: str`, `session_id: str | None = None`, `duration_seconds: float`, `usage: Usage = Usage()`.
  - All models are frozen: `model_config = ConfigDict(frozen=True)`.

- [ ] **Step 1: Create the workspace files**

Root `pyproject.toml`:

```toml
[project]
name = "workflows-workspace"
version = "0"
requires-python = ">=3.11"
dependencies = ["workflows-agent"]

[tool.uv]
package = false

[tool.uv.sources]
workflows-agent = { workspace = true }

[tool.uv.workspace]
members = ["packages/agents/*"]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "pyright>=1.1.380"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "--import-mode=importlib"

[tool.ruff]
target-version = "py311"

[tool.pyright]
include = ["packages"]
pythonVersion = "3.11"
```

`packages/agents/core/pyproject.toml`:

```toml
[project]
name = "workflows-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/workflows"]
```

The root depends on every workspace package so that `uv sync` installs them all into the shared venv. Each new package gets added there.

`.gitignore` ignores `.venv/`, `__pycache__/`, `dist/`, `.pytest_cache/` and `.ruff_cache/`.

`--import-mode=importlib` is there so that `tests/` folders in different packages with the same test file names don't clash.

- [ ] **Step 2: Write the failing tests in `tests/test_models.py`**

```python
from pathlib import Path
import pytest
from pydantic import ValidationError
from workflows.agent import AgentRequest, AgentResult, PermissionMode, Usage

def test_request_defaults():
    r = AgentRequest(prompt="p", working_folder=Path("/w"))
    assert r.extra_folders == []
    assert r.system_prompt is None
    assert r.timeout_seconds is None

def test_request_rejects_empty_prompt():
    with pytest.raises(ValidationError):
        AgentRequest(prompt="", working_folder=Path("/w"))

@pytest.mark.parametrize("timeout", [0, -1])
def test_request_rejects_non_positive_timeout(timeout):
    with pytest.raises(ValidationError):
        AgentRequest(prompt="p", working_folder=Path("/w"), timeout_seconds=timeout)

def test_usage_defaults_empty():
    assert Usage() == Usage(input_tokens=None, output_tokens=None, cost_usd=None)

def test_result_json_round_trip():
    r = AgentResult(call_id="c", final_message="done", session_id="s",
                    duration_seconds=1.5, usage=Usage(input_tokens=10, output_tokens=2, cost_usd=0.01))
    assert AgentResult.model_validate_json(r.model_dump_json()) == r

def test_permission_mode_values():
    assert PermissionMode("auto") is PermissionMode.AUTO
    assert PermissionMode("bypass") is PermissionMode.BYPASS

def test_models_are_frozen():
    r = AgentRequest(prompt="p", working_folder=Path("/w"))
    with pytest.raises(ValidationError):
        r.prompt = "q"
```

- [ ] **Step 3: Run `uv sync && uv run pytest packages/agents/core/tests/test_models.py -v`**

Expected: FAIL with `ImportError`, because `AgentRequest` and the other names don't exist yet.

- [ ] **Step 4: Implement the models in `models.py`**

Re-export them from `__init__.py` and list them in `__all__`. Use `Field(min_length=1)` and `Field(default=None, gt=0)` for the checks.

- [ ] **Step 5: Run the tests, ruff and pyright**

Run: `uv run pytest packages/agents/core/tests -v && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, and ruff and pyright report no errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore packages/agents/core
git commit -m "Add workspace and agent core models"
```

---

### Task 2: Events

**Files:**
- Create: `packages/agents/core/src/workflows/agent/events.py`
- Modify: `packages/agents/core/src/workflows/agent/__init__.py` (re-exports)
- Test: `packages/agents/core/tests/test_events.py`

**Interfaces:**
- Consumes: `Usage` (Task 1).
- Produces (`workflows.agent.events`, re-exported from `workflows.agent`):
  - Base `class BaseEvent(BaseModel)` (frozen): `call_id: str`, `timestamp: datetime` (defaults to `datetime.now(timezone.utc)`), `raw: Any = None`.
  - Subclasses, each with a `type` literal that acts as the discriminator:
    - `TextEvent`: `type: Literal["text"]`, `text: str`
    - `ReasoningEvent`: `type: Literal["reasoning"]`, `text: str`
    - `ToolCallEvent`: `type: Literal["tool_call"]`, `tool_call_id: str`, `name: str`, `input: dict[str, Any]`
    - `ToolResultEvent`: `type: Literal["tool_result"]`, `tool_call_id: str`, `output: str`, `is_error: bool = False`
    - `UsageEvent`: `type: Literal["usage"]`, `usage: Usage`
    - `OtherEvent`: `type: Literal["other"]`
  - `Event = Annotated[TextEvent | ReasoningEvent | ToolCallEvent | ToolResultEvent | UsageEvent | OtherEvent, Field(discriminator="type")]`
  - `EventHandler = Callable[[Event], None]`

Each `type` field has a default equal to its literal, so callers never pass it.

- [ ] **Step 1: Write the failing tests in `tests/test_events.py`**

```python
from datetime import timezone
import pytest
from pydantic import TypeAdapter
from workflows.agent import (Event, OtherEvent, ReasoningEvent, TextEvent, ToolCallEvent,
                             ToolResultEvent, Usage, UsageEvent)

SAMPLES = [
    TextEvent(call_id="c", text="hi"),
    ReasoningEvent(call_id="c", text="thinking"),
    ToolCallEvent(call_id="c", tool_call_id="t1", name="Read", input={"file_path": "a.py"}),
    ToolResultEvent(call_id="c", tool_call_id="t1", output="contents", is_error=True),
    UsageEvent(call_id="c", usage=Usage(input_tokens=5)),
    OtherEvent(call_id="c", raw={"kind": "unknown"}),
]

def test_event_type_tags():
    assert [e.type for e in SAMPLES] == ["text", "reasoning", "tool_call", "tool_result", "usage", "other"]

@pytest.mark.parametrize("event", SAMPLES)
def test_event_json_round_trip(event):
    parsed = TypeAdapter(Event).validate_json(event.model_dump_json())
    assert type(parsed) is type(event)
    assert parsed == event

def test_timestamp_defaults_to_utc():
    assert TextEvent(call_id="c", text="x").timestamp.tzinfo == timezone.utc

def test_raw_defaults_to_none():
    assert TextEvent(call_id="c", text="x").raw is None
```

- [ ] **Step 2: Run `uv run pytest packages/agents/core/tests/test_events.py -v`**

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `events.py` and re-export the event names from `__init__.py`**

- [ ] **Step 4: Run `uv run pytest packages/agents/core/tests -v && uv run ruff check . && uv run pyright`**

Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/agents/core
git commit -m "Add agent event types"
```

---

### Task 3: Agent interface and errors

**Files:**
- Create: `packages/agents/core/src/workflows/agent/errors.py`
- Create: `packages/agents/core/src/workflows/agent/agent.py`
- Modify: `packages/agents/core/src/workflows/agent/__init__.py` (re-exports)
- Test: `packages/agents/core/tests/test_agent.py`

**Interfaces:**
- Consumes: `AgentRequest`, `AgentResult` (Task 1), and `EventHandler` (Task 2).
- Produces:
  - `class AgentError(Exception)`: `__init__(self, message: str, *, stderr: str | None = None, session_id: str | None = None)`. It keeps `.stderr` and `.session_id`, and `str(err) == message`.
  - `class AgentTimeoutError(AgentError)`.
  - `class Agent(ABC)` with one abstract method:
    ```python
    async def run(self, request: AgentRequest, on_event: EventHandler | None = None) -> AgentResult
    ```
  - The docstring of `Agent.run` states the contract from the spec:
    - the call never waits for human approval;
    - one instance can run calls concurrently;
    - events go to `on_event` in order, all with the result's `call_id`;
    - an exception from `on_event` fails the call with that exception;
    - failure, including a timeout, raises `AgentError` or `AgentTimeoutError` and never returns a result;
    - cancelling the task stops the agent process.

- [ ] **Step 1: Write the failing tests in `tests/test_agent.py`**

```python
import pytest
from workflows.agent import Agent, AgentError, AgentTimeoutError

def test_agent_requires_run():
    class Incomplete(Agent):
        pass
    with pytest.raises(TypeError):
        Incomplete()

def test_agent_error_carries_details():
    err = AgentError("boom", stderr="trace", session_id="s1")
    assert str(err) == "boom"
    assert err.stderr == "trace"
    assert err.session_id == "s1"

def test_timeout_error_is_agent_error():
    err = AgentTimeoutError("slow")
    assert isinstance(err, AgentError)
    assert err.stderr is None and err.session_id is None
```

- [ ] **Step 2: Run `uv run pytest packages/agents/core/tests/test_agent.py -v`**

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `errors.py` and `agent.py`, and re-export `Agent`, `AgentError` and `AgentTimeoutError`**

- [ ] **Step 4: Run `uv run pytest packages/agents/core/tests -v && uv run ruff check . && uv run pyright`**

Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/agents/core
git commit -m "Add agent interface and errors"
```

---

### Task 4: Fake agent

**Files:**
- Create: `packages/agents/core/src/workflows/agent/testing.py`
- Test: `packages/agents/core/tests/test_fake_agent.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces (`workflows.agent.testing`, not re-exported from `workflows.agent`, so test helpers stay out of the main API):
  ```python
  Respond = Callable[[AgentRequest], str | Awaitable[str]]

  class FakeAgent(Agent):
      requests: list[AgentRequest]          # every request received, in call order
      def __init__(self, respond: Respond | None = None, events: Sequence[Event] = ()) -> None
      async def run(self, request: AgentRequest, on_event: EventHandler | None = None) -> AgentResult
  ```

`run` behaves like this:

1. Append the request to `requests`.
2. Make a new `call_id` with `uuid4().hex`.
3. Send each scripted event to `on_event`, in order, as `event.model_copy(update={"call_id": call_id})`. Don't catch errors from `on_event`.
4. Await `respond(request)`. If it returns an awaitable, await that too. With no `respond`, the final message is `""`. If `request.timeout_seconds` is set, wrap steps 3–4 in `asyncio.timeout` and turn `TimeoutError` into `AgentTimeoutError`.
5. Return `AgentResult(call_id=call_id, final_message=<respond's return>, duration_seconds=<monotonic elapsed>)`.

Scripted events are built with any placeholder `call_id`, because the fake replaces it.

- [ ] **Step 1: Write the failing tests in `tests/test_fake_agent.py`**

```python
import asyncio
from pathlib import Path
import pytest
from workflows.agent import Agent, AgentError, AgentRequest, AgentTimeoutError, TextEvent, ToolCallEvent
from workflows.agent.testing import FakeAgent

def req(tmp_path: Path, **kw) -> AgentRequest:
    return AgentRequest(prompt="review", working_folder=tmp_path, **kw)

async def test_is_an_agent():
    assert isinstance(FakeAgent(), Agent)

async def test_returns_final_message_and_records_requests(tmp_path):
    agent = FakeAgent(respond=lambda r: f"got {r.prompt}")
    result = await agent.run(req(tmp_path))
    assert result.final_message == "got review"
    assert agent.requests == [req(tmp_path)]
    assert result.duration_seconds >= 0

async def test_default_respond_returns_empty_message(tmp_path):
    assert (await FakeAgent().run(req(tmp_path))).final_message == ""

async def test_async_respond_supported(tmp_path):
    async def respond(r):
        await asyncio.sleep(0)
        return "async"
    assert (await FakeAgent(respond=respond).run(req(tmp_path))).final_message == "async"

async def test_respond_can_write_files(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    def respond(r):
        (r.extra_folders[0] / "a.json").write_text("{}")
        return "ok"
    await FakeAgent(respond=respond).run(req(tmp_path, extra_folders=[out]))
    assert (out / "a.json").read_text() == "{}"

async def test_respond_error_propagates(tmp_path):
    def respond(r):
        raise AgentError("crashed", stderr="trace")
    with pytest.raises(AgentError, match="crashed"):
        await FakeAgent(respond=respond).run(req(tmp_path))

async def test_emits_scripted_events_with_call_id(tmp_path):
    scripted = [TextEvent(call_id="x", text="hi"),
                ToolCallEvent(call_id="x", tool_call_id="t", name="Read", input={})]
    seen = []
    result = await FakeAgent(events=scripted).run(req(tmp_path), on_event=seen.append)
    assert [e.call_id for e in seen] == [result.call_id] * 2
    assert [e.model_copy(update={"call_id": "x"}) for e in seen] == scripted

async def test_on_event_error_fails_call(tmp_path):
    def boom(e):
        raise RuntimeError("sink broke")
    with pytest.raises(RuntimeError, match="sink broke"):
        await FakeAgent(events=[TextEvent(call_id="x", text="hi")]).run(req(tmp_path), on_event=boom)

async def test_timeout_raises_agent_timeout_error(tmp_path):
    async def slow(r):
        await asyncio.sleep(1)
        return "late"
    with pytest.raises(AgentTimeoutError):
        await FakeAgent(respond=slow).run(req(tmp_path, timeout_seconds=0.01))

async def test_parallel_calls_have_distinct_ids(tmp_path):
    agent = FakeAgent(events=[TextEvent(call_id="x", text="hi")])
    seen: dict[str, list] = {}
    async def call(i):
        events = []
        result = await agent.run(req(tmp_path), on_event=events.append)
        seen[result.call_id] = events
    await asyncio.gather(*(call(i) for i in range(5)))
    assert len(seen) == 5
    assert all(e.call_id == cid for cid, evs in seen.items() for e in evs)
```

- [ ] **Step 2: Run `uv run pytest packages/agents/core/tests/test_fake_agent.py -v`**

Expected: FAIL with `ModuleNotFoundError: workflows.agent.testing`.

- [ ] **Step 3: Implement `FakeAgent` in `testing.py`**

Implement it exactly as described in the Interfaces block. Use `inspect.isawaitable` to tell sync and async `respond` apart.

- [ ] **Step 4: Run `uv run pytest packages/agents/core/tests -v && uv run ruff check . && uv run pyright`**

Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/agents/core
git commit -m "Add fake agent for workflow tests"
```

---

### Task 5: Isolated install check

**Files:**
- Create: `scripts/check_isolated_installs.py` (standard library only)

**Interfaces:**
- Produces: `uv run python scripts/check_isolated_installs.py`. It exits 0 when every package passes and 1 otherwise, and prints one line per package: `<dist-name>: ok` or `<dist-name>: FAILED`.

How it works. This is the decided approach; follow it as written.

1. `uv build --all-packages --out-dir <tmp>/dist` builds a wheel for each workspace member.
2. For each `packages/*/*/pyproject.toml`:
   - read the distribution name with `tomllib`;
   - `uv venv <tmp>/venvs/<name> --python 3.11`, which uses the lowest supported Python (uv downloads it if it's missing);
   - `uv pip install --python <venv> --find-links <tmp>/dist <name> pytest pytest-asyncio`. Sibling workspace packages resolve only from the built wheels, and only if they're declared as dependencies.
   - Run `<venv>/bin/python -m pytest <package>/tests` with `cwd` set to the repo root, so that the root pytest config applies. Use `-p no:cacheprovider` so nothing is written into the repo.
3. A package fails if any command exits with a non-zero status. Print that command's output under the `FAILED` line.

Tests are imported from the installed wheel, not from `src/`. That is what proves the namespace package works when installed.

- [ ] **Step 1: Write the script**

- [ ] **Step 2: Run `uv run python scripts/check_isolated_installs.py`**

Expected: `workflows-agent: ok`, exit code 0.

- [ ] **Step 3: Check that the script catches a broken install**

Temporarily add `src/workflows/__init__.py` containing `raise ImportError("namespace broken")` to the core package. Run the script and expect `workflows-agent: FAILED` with exit code 1. Then remove the file. Don't commit this change.

- [ ] **Step 4: Commit and merge**

```bash
git add scripts/check_isolated_installs.py
git commit -m "Add isolated install check"
git checkout dev && git merge --no-ff agent-core -m "Merge agent core" && git push -u origin dev
```
