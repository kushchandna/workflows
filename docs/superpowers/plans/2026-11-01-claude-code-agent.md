# Claude Code Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `workflows-agent-claude-code`, which implements the agent interface by running the `claude` CLI as a subprocess.

**Architecture:** The work is split into small pure parts, each tested on its own:
- building the command line,
- turning one stream-json line into events,
- splitting a byte stream into lines,
- stopping a process group.

`ClaudeCodeAgent.run` joins them together. Unit tests run the agent against a fake `claude` script that replays fixtures shaped like recorded CLI output. One integration test, gated by an environment variable, runs the real CLI.

**Tech Stack:** Python 3.11+, asyncio subprocesses, `workflows-agent` (core), pytest + pytest-asyncio, hatchling.

**Spec:** `docs/agents/claude-code/requirements.md` (on the `design` branch). The core interface spec is `docs/agents/requirements.md`.

## Branches

- Work on `claude-code`, branched from `origin/dev`.
- When it's finished, merge into `dev` with `--no-ff` and push `dev`. Never merge into `main`.

## Global Constraints

- Distribution `workflows-agent-claude-code`, import `workflows_agent_claude_code`, folder `packages/agents/claude-code/`.
- The only runtime dependency is `workflows-agent`, through `[tool.uv.sources] workflows-agent = { workspace = true }`.
- `requires-python = ">=3.11"`. POSIX only: the code uses process groups and `os.killpg`.
- Every call runs `claude -p --output-format stream-json --verbose --model <model> --permission-mode bypassPermissions`, plus `--add-dir` and `--append-system-prompt-file` when needed. No other flags.
- The prompt is written to stdin. It never goes on the command line.
- Commit messages are short and plain, with no AI or Claude attribution trailers.
- Run every command from the repo root.

## Review Focus

- **A prompt larger than 128 KB.** It must reach `claude` through stdin unchanged. Test: `test_large_prompt_goes_through_stdin` (Task 4).
- **`claude` exits before reading stdin,** for example on a bad flag. The call must raise `AgentError` with the exit code, not a `BrokenPipeError`. Test: `test_early_exit_before_reading_stdin` (Task 4).
- **A multi-byte UTF-8 character split across two read chunks.** It must come out decoded intact. Test: `test_utf8_split_across_chunks` (Task 3).
- **Children of `claude` still running after `claude` exits normally.** They must be killed. Test: `test_children_killed_after_normal_exit` (Task 5).
- **A working folder that doesn't exist.** The call must raise `AgentError` naming the folder. It must not say the executable is missing, even though both cases raise `FileNotFoundError` from asyncio. Test: `test_missing_working_folder` (Task 4).

---

### Task 1: Package skeleton and command line

**Files:**
- Create: `packages/agents/claude-code/pyproject.toml`
- Create: `packages/agents/claude-code/src/workflows_agent_claude_code/__init__.py` (empty for now)
- Create: `packages/agents/claude-code/src/workflows_agent_claude_code/py.typed` (empty)
- Create: `packages/agents/claude-code/src/workflows_agent_claude_code/command.py`
- Modify: `pyproject.toml` (root)
- Test: `packages/agents/claude-code/tests/test_command.py`

**Interfaces:**
- Consumes: `workflows_agent.AgentRequest`.
- Produces: `build_command(claude_path: str, model: str, request: AgentRequest, system_prompt_file: Path | None) -> list[str]` in `command.py`.

- [ ] **Step 1: Set up the package and wire it into the workspace**

`packages/agents/claude-code/pyproject.toml`:

```toml
[project]
name = "workflows-agent-claude-code"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["workflows-agent"]

[tool.uv.sources]
workflows-agent = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/workflows_agent_claude_code"]
```

In the root `pyproject.toml`:
- add `"workflows-agent-claude-code"` to `dependencies`;
- add `workflows-agent-claude-code = { workspace = true }` to `[tool.uv.sources]`;
- set `[tool.pyright] include = ["packages", "tests"]`.

- [ ] **Step 2: Write the failing tests in `tests/test_command.py`**

```python
from pathlib import Path
from workflows_agent import AgentRequest
from workflows_agent_claude_code.command import build_command

BASE = ["claude", "-p", "--output-format", "stream-json", "--verbose",
        "--model", "haiku", "--permission-mode", "bypassPermissions"]

def test_minimal_command():
    req = AgentRequest(prompt="do it", working_folder=Path("/w"))
    assert build_command("claude", "haiku", req, None) == BASE

def test_extra_folders_and_system_prompt_file():
    req = AgentRequest(prompt="p", working_folder=Path("/w"), extra_folders=[Path("/a"), Path("/b")])
    assert build_command("claude", "haiku", req, Path("/tmp/sp.txt")) == BASE + [
        "--add-dir", "/a", "--add-dir", "/b", "--append-system-prompt-file", "/tmp/sp.txt"]

def test_prompt_never_in_argv():
    req = AgentRequest(prompt="SECRET-PROMPT", working_folder=Path("/w"), system_prompt="SECRET-SYS")
    argv = build_command("claude", "haiku", req, Path("/tmp/sp.txt"))
    assert not any("SECRET" in a for a in argv)
```

- [ ] **Step 3: Run `uv sync && uv run pytest packages/agents/claude-code/tests -q`**

Expected: FAIL with `ModuleNotFoundError: workflows_agent_claude_code.command`.

- [ ] **Step 4: Implement `build_command` in `command.py`**

Use exactly the flag order the tests pin.

- [ ] **Step 5: Run all the checks**

Run: `uv run pytest packages -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock packages/agents/claude-code
git commit -m "Add Claude Code agent package and command line"
```

---

### Task 2: Turning lines into events

**Files:**
- Create: `packages/agents/claude-code/src/workflows_agent_claude_code/parse.py`
- Test: `packages/agents/claude-code/tests/test_parse.py`

**Interfaces:**
- Consumes: the core event types, `Usage`.
- Produces (in `parse.py`):
  - `events_from_line(line: str, call_id: str) -> list[Event]`. It parses the JSON once. Every event gets `raw` set to the parsed object, or to the line itself when the line isn't JSON or isn't a JSON object. A `result` line gives exactly one `UsageEvent`, and that event's `raw` is the result dict. Task 4 relies on this to find the result.
  - `usage_from_result(result: dict[str, Any]) -> Usage`. Input tokens are the sum of `input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens`, counting the ones that are present. Input is `None` only when all three are missing. `output_tokens` comes from `usage.output_tokens` and `cost_usd` from `total_cost_usd`.

The mapping is the table in the spec's "Event mapping" section:
- `assistant` blocks give text, reasoning (dropped when empty) and tool call events;
- `user` `tool_result` blocks give tool result events. A list `content` is reduced to its `text` parts joined with `"\n"`, and `is_error` defaults to `False`;
- `result` gives a `UsageEvent`;
- everything else gives an `OtherEvent`.
- An `assistant` or `user` block of any other type gives no event. The whole line is still available in the `raw` of its other events.

- [ ] **Step 1: Write the failing tests in `tests/test_parse.py`**

```python
import json
from workflows_agent import (OtherEvent, ReasoningEvent, TextEvent, ToolCallEvent,
                             ToolResultEvent, Usage, UsageEvent)
from workflows_agent_claude_code.parse import events_from_line, usage_from_result

def line(obj) -> str:
    return json.dumps(obj)

def test_assistant_blocks_in_order_and_empty_thinking_dropped():
    data = {"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": ""},
        {"type": "thinking", "thinking": "plan"},
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}]}}
    events = events_from_line(line(data), "c")
    assert [type(e) for e in events] == [ReasoningEvent, TextEvent, ToolCallEvent]
    assert events[0].text == "plan" and events[1].text == "hi"
    assert (events[2].tool_call_id, events[2].name, events[2].input) == ("t1", "Read", {"file_path": "a"})
    assert all(e.call_id == "c" and e.raw == data for e in events)

def test_tool_result_string_content():
    data = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "1\thello\n"}]}}
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, ToolResultEvent)
    assert (e.tool_call_id, e.output, e.is_error) == ("t1", "1\thello\n", False)

def test_tool_result_list_content_keeps_text_parts():
    data = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True,
            "content": [{"type": "text", "text": "one"}, {"type": "image", "source": {}}, {"type": "text", "text": "two"}]}]}}
    [e] = events_from_line(line(data), "c")
    assert (e.output, e.is_error) == ("one\ntwo", True)

def test_result_line_gives_one_usage_event_with_result_as_raw():
    data = {"type": "result", "is_error": False, "result": "done", "session_id": "s", "total_cost_usd": 0.0045,
            "usage": {"input_tokens": 4, "cache_creation_input_tokens": 20135,
                      "cache_read_input_tokens": 37895, "output_tokens": 234}}
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, UsageEvent) and e.raw == data
    assert e.usage == Usage(input_tokens=58034, output_tokens=234, cost_usd=0.0045)

def test_usage_from_result_missing_fields():
    assert usage_from_result({"type": "result"}) == Usage()
    assert usage_from_result({"usage": {"cache_read_input_tokens": 7}}).input_tokens == 7

def test_other_lines():
    data = {"type": "system", "subtype": "init", "session_id": "s"}
    [e] = events_from_line(line(data), "c")
    assert isinstance(e, OtherEvent) and e.raw == data

def test_non_json_line_is_other_with_raw_text():
    [e] = events_from_line("not json {", "c")
    assert isinstance(e, OtherEvent) and e.raw == "not json {"

def test_json_that_is_not_an_object_is_other():
    [e] = events_from_line("[1, 2]", "c")
    assert isinstance(e, OtherEvent) and e.raw == "[1, 2]"
```

- [ ] **Step 2: Run `uv run pytest packages/agents/claude-code/tests/test_parse.py -q`**

Expected: FAIL with `ModuleNotFoundError: workflows_agent_claude_code.parse`.

- [ ] **Step 3: Implement `events_from_line` and `usage_from_result` in `parse.py`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest packages -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/agents/claude-code
git commit -m "Map Claude Code stream-json lines to events"
```

---

### Task 3: Line reader without a length limit

**Files:**
- Create: `packages/agents/claude-code/src/workflows_agent_claude_code/lines.py`
- Test: `packages/agents/claude-code/tests/test_lines.py`

**Interfaces:**
- Produces: `async def iter_lines(stream: asyncio.StreamReader, chunk_size: int = 65536) -> AsyncIterator[str]` in `lines.py`.

It works like this:
- Read chunks with `stream.read(chunk_size)` until it returns `b""`.
- Split complete lines on `b"\n"`, and decode each complete line from bytes with UTF-8 and `errors="replace"`. Decoding whole lines means a character split across chunks is never broken.
- Strip a trailing `\r`, and skip lines that are empty after stripping.
- At EOF, yield any final partial line that isn't empty.

- [ ] **Step 1: Write the failing tests in `tests/test_lines.py`**

```python
import asyncio
from workflows_agent_claude_code.lines import iter_lines

def reader(data: bytes) -> asyncio.StreamReader:
    r = asyncio.StreamReader()
    r.feed_data(data)
    r.feed_eof()
    return r

async def collect(data: bytes, chunk_size: int = 65536) -> list[str]:
    return [l async for l in iter_lines(reader(data), chunk_size)]

async def test_splits_lines_and_keeps_final_partial_line():
    assert await collect(b"a\nb\n\nc") == ["a", "b", "c"]

async def test_line_longer_than_default_limit():
    big = "x" * 1_000_000
    assert await collect(f"{big}\nend\n".encode()) == [big, "end"]

async def test_utf8_split_across_chunks():
    data = "ab€cd\n".encode()            # € is 3 bytes at offsets 2..4
    assert await collect(data, chunk_size=3) == ["ab€cd"]
```

- [ ] **Step 2: Run `uv run pytest packages/agents/claude-code/tests/test_lines.py -q`**

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `iter_lines` in `lines.py`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest packages -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/agents/claude-code
git commit -m "Add unbounded line reader"
```

---

### Task 4: `ClaudeCodeAgent` — running a call and reporting errors

**Files:**
- Create: `packages/agents/claude-code/src/workflows_agent_claude_code/agent.py`
- Modify: `packages/agents/claude-code/src/workflows_agent_claude_code/__init__.py` (export `ClaudeCodeAgent`)
- Create: `packages/agents/claude-code/tests/fake_claude.py`
- Create: `packages/agents/claude-code/tests/conftest.py`
- Create: `packages/agents/claude-code/tests/fixtures/success.jsonl`
- Create: `packages/agents/claude-code/tests/fixtures/error.jsonl`
- Test: `packages/agents/claude-code/tests/test_agent.py`

**Interfaces:**
- Consumes: `build_command` (Task 1), `events_from_line` (Task 2), `iter_lines` (Task 3), and the core `Agent`, `AgentError`, `AgentTimeoutError`, `AgentRequest`, `AgentResult`, `UsageEvent`.
- Produces:
  ```python
  class ClaudeCodeAgent(Agent):
      def __init__(self, model: str, claude_path: str | Path = "claude") -> None
      async def run(self, request: AgentRequest, on_event: EventHandler | None = None) -> AgentResult
  ```

**The fake `claude`** (`tests/fake_claude.py`) is a script driven by environment variables. It runs these steps in order:
1. Unless `FAKE_CLAUDE_SKIP_STDIN` is set, read all of stdin.
2. If `FAKE_CLAUDE_RECORD` is set, write JSON to that path holding:
   - `argv`: `sys.argv[1:]`,
   - `stdin`,
   - `cwd`,
   - `system_prompt`: the contents of the file after `--append-system-prompt-file`, or `null`.
3. If `FAKE_CLAUDE_CHILD_PID_FILE` is set, start `sleep 60` with `subprocess.Popen`, don't wait for it, and write its pid to that file.
4. If `FAKE_CLAUDE_IGNORE_TERM` is set, ignore SIGTERM.
5. Write `FAKE_CLAUDE_STDERR` to stderr.
6. Print each line of the `FAKE_CLAUDE_FIXTURE` file, flushing after each.
7. Sleep `FAKE_CLAUDE_SLEEP` seconds (default 0).
8. Exit with `FAKE_CLAUDE_EXIT` (default 0).

**`conftest.py`** provides these fixtures:
- `fake_claude(tmp_path) -> Path`: writes an executable wrapper at `tmp_path / "claude"` whose shebang is `sys.executable` and which runs `fake_claude.py` with `runpy.run_path(..., run_name="__main__")`.
- `record(tmp_path, monkeypatch) -> Callable[[], dict]`: sets `FAKE_CLAUDE_RECORD` and returns a function that reads it back.
- `fixture_path() -> Callable[[str], Path]`: a fixture that returns a function mapping a name to `tests/fixtures/<name>`. It has to be a fixture because `--import-mode=importlib` means tests can't do `from conftest import ...`.

**Fixtures.** These are shaped like the recorded CLI output, trimmed down.

`tests/fixtures/success.jsonl`:
```
{"type":"system","subtype":"init","session_id":"sess-1","model":"claude-haiku"}
{"type":"assistant","session_id":"sess-1","message":{"id":"m1","content":[{"type":"thinking","thinking":""},{"type":"tool_use","id":"toolu_1","name":"Read","input":{"file_path":"note.txt"}}],"usage":{"input_tokens":2,"output_tokens":4}}}
{"type":"user","session_id":"sess-1","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_1","content":"1\thello probe\n"}]}}
{"type":"assistant","session_id":"sess-1","message":{"id":"m2","content":[{"type":"text","text":"hello probe"}],"usage":{"input_tokens":2,"output_tokens":6}}}
{"type":"result","subtype":"success","is_error":false,"result":"hello probe","session_id":"sess-1","total_cost_usd":0.0045,"usage":{"input_tokens":4,"cache_creation_input_tokens":20135,"cache_read_input_tokens":37895,"output_tokens":234}}
{"type":"system","subtype":"task_summary","session_id":"sess-1"}
```

`tests/fixtures/error.jsonl` (the fake also gets `FAKE_CLAUDE_EXIT=1` and `FAKE_CLAUDE_STDERR="[claude-code:unrecognized_model]"`):
```
{"type":"system","subtype":"init","session_id":"sess-2","model":"no-such-model"}
{"type":"result","subtype":"success","is_error":true,"result":"There's an issue with the selected model (no-such-model).","session_id":"sess-2"}
```

**How `run` works:**
1. If `request.working_folder` isn't a directory, raise `AgentError(f"working folder does not exist: {folder}")`.
2. Generate the call id and record the start time. If there's a system prompt, write it to a temporary file.
3. Spawn the process with `asyncio.create_subprocess_exec(*cmd, cwd=working_folder, stdin=PIPE, stdout=PIPE, stderr=PIPE, start_new_session=True)`. Turn `FileNotFoundError` or `PermissionError` into `AgentError(f"claude executable not found or not runnable: {claude_path}")`.
4. Start a task that reads all of stderr.
5. Inside `asyncio.timeout(request.timeout_seconds)`:
   - write the prompt bytes to stdin and close it, ignoring `BrokenPipeError` and `ConnectionResetError`;
   - for each line from `iter_lines(proc.stdout)`, and each event from `events_from_line`:
     - remember `session_id` from `event.raw` when `raw` is a dict that has one;
     - when the event is a `UsageEvent`, keep `event.raw` as the result;
     - call `on_event`;
   - wait for the process to exit, then wait for the stderr task.
6. A `TimeoutError` becomes `AgentTimeoutError` only if the timeout context expired. Otherwise re-raise it, following the same pattern as the core `FakeAgent`.
7. In `finally`: stop the process if it's still running (`proc.kill()` here; Task 5 replaces this), cancel the stderr task if it's still pending, and delete the temporary file.
8. Decide the outcome, in this order:
   - the result has `is_error` true → `AgentError(result text)`;
   - the exit code is non-zero → `AgentError(f"claude exited with code {code}")`;
   - there's no result → `AgentError("claude ended without a result")`.

   Every error carries `stderr` and `session_id`.
9. Otherwise return `AgentResult(call_id, final_message=result.get("result", ""), session_id, duration_seconds, usage=usage_from_result(result))`.

- [ ] **Step 1: Write the fake, conftest and fixtures, then the failing tests in `tests/test_agent.py`**

```python
import asyncio
import pytest
from workflows_agent import (AgentError, AgentRequest, OtherEvent, TextEvent, ToolCallEvent,
                             ToolResultEvent, Usage, UsageEvent)
from pathlib import Path
from workflows_agent_claude_code import ClaudeCodeAgent

@pytest.fixture
def use_fixture(monkeypatch, fixture_path):
    def use(name, exit_code=0, stderr=""):
        monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(fixture_path(name)))
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))
        monkeypatch.setenv("FAKE_CLAUDE_STDERR", stderr)
    return use

async def test_success_events_and_result(tmp_path, fake_claude, use_fixture):
    use_fixture("success.jsonl")
    seen = []
    result = await ClaudeCodeAgent("haiku", fake_claude).run(
        AgentRequest(prompt="p", working_folder=tmp_path), on_event=seen.append)
    assert [type(e) for e in seen] == [OtherEvent, ToolCallEvent, ToolResultEvent, TextEvent, UsageEvent, OtherEvent]
    assert all(e.call_id == result.call_id for e in seen)
    assert result.final_message == "hello probe"
    assert result.session_id == "sess-1"
    assert result.usage == Usage(input_tokens=58034, output_tokens=234, cost_usd=0.0045)
    assert result.duration_seconds >= 0

async def test_passes_prompt_cwd_flags_and_system_prompt(tmp_path, fake_claude, use_fixture, record):
    use_fixture("success.jsonl")
    extra = tmp_path / "out"; extra.mkdir()
    work = tmp_path / "work"; work.mkdir()
    await ClaudeCodeAgent("opus", fake_claude).run(AgentRequest(
        prompt="review this", working_folder=work, extra_folders=[extra], system_prompt="be strict"))
    rec = record()
    assert rec["stdin"] == "review this"
    assert rec["cwd"] == str(work)
    assert rec["system_prompt"] == "be strict"
    assert rec["argv"][:8] == ["-p", "--output-format", "stream-json", "--verbose",
                               "--model", "opus", "--permission-mode", "bypassPermissions"]
    assert ["--add-dir", str(extra)] == rec["argv"][8:10]
    sp_file = rec["argv"][rec["argv"].index("--append-system-prompt-file") + 1]
    assert not Path(sp_file).exists()      # removed after the call

async def test_large_prompt_goes_through_stdin(tmp_path, fake_claude, use_fixture, record):
    use_fixture("success.jsonl")
    prompt = "x" * 300_000
    await ClaudeCodeAgent("haiku", fake_claude).run(AgentRequest(prompt=prompt, working_folder=tmp_path))
    assert record()["stdin"] == prompt

async def test_is_error_result_raises_with_details(tmp_path, fake_claude, use_fixture):
    use_fixture("error.jsonl", exit_code=1, stderr="[claude-code:unrecognized_model]")
    with pytest.raises(AgentError, match="no-such-model") as info:
        await ClaudeCodeAgent("no-such-model", fake_claude).run(AgentRequest(prompt="p", working_folder=tmp_path))
    assert info.value.session_id == "sess-2"
    assert "unrecognized_model" in (info.value.stderr or "")

async def test_non_zero_exit_without_result(tmp_path, fake_claude, monkeypatch):
    (tmp_path / "empty.jsonl").write_text("")
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(tmp_path / "empty.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")
    with pytest.raises(AgentError, match="exited with code 3"):
        await ClaudeCodeAgent("haiku", fake_claude).run(AgentRequest(prompt="p", working_folder=tmp_path))

async def test_clean_exit_without_result(tmp_path, fake_claude, monkeypatch):
    (tmp_path / "empty.jsonl").write_text("")
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(tmp_path / "empty.jsonl"))
    with pytest.raises(AgentError, match="without a result"):
        await ClaudeCodeAgent("haiku", fake_claude).run(AgentRequest(prompt="p", working_folder=tmp_path))

async def test_early_exit_before_reading_stdin(tmp_path, fake_claude, monkeypatch):
    (tmp_path / "empty.jsonl").write_text("")
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(tmp_path / "empty.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_SKIP_STDIN", "1")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "2")
    with pytest.raises(AgentError, match="exited with code 2"):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="x" * 1_000_000, working_folder=tmp_path))

async def test_missing_executable(tmp_path):
    with pytest.raises(AgentError, match="not found"):
        await ClaudeCodeAgent("haiku", tmp_path / "nope").run(AgentRequest(prompt="p", working_folder=tmp_path))

async def test_missing_working_folder(tmp_path, fake_claude):
    missing = tmp_path / "missing"
    with pytest.raises(AgentError, match="working folder does not exist") as info:
        await ClaudeCodeAgent("haiku", fake_claude).run(AgentRequest(prompt="p", working_folder=missing))
    assert str(missing) in str(info.value)

async def test_parallel_calls_have_distinct_ids(tmp_path, fake_claude, use_fixture):
    use_fixture("success.jsonl")
    agent = ClaudeCodeAgent("haiku", fake_claude)
    results = await asyncio.gather(*(agent.run(AgentRequest(prompt="p", working_folder=tmp_path)) for _ in range(4)))
    assert len({r.call_id for r in results}) == 4
```

- [ ] **Step 2: Run `uv run pytest packages/agents/claude-code/tests/test_agent.py -q`**

Expected: FAIL with `ImportError: cannot import name 'ClaudeCodeAgent'`.

- [ ] **Step 3: Implement `ClaudeCodeAgent` in `agent.py` and export it from `__init__.py`**

Follow "How `run` works" above.

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest packages -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/agents/claude-code
git commit -m "Add ClaudeCodeAgent"
```

---

### Task 5: Stopping the process group

**Files:**
- Create: `packages/agents/claude-code/src/workflows_agent_claude_code/process.py`
- Modify: `packages/agents/claude-code/src/workflows_agent_claude_code/agent.py` (step 7 of `run`)
- Test: `packages/agents/claude-code/tests/test_stopping.py`

**Interfaces:**
- Consumes: `ClaudeCodeAgent` (Task 4) and the fake's `FAKE_CLAUDE_SLEEP`, `FAKE_CLAUDE_CHILD_PID_FILE` and `FAKE_CLAUDE_IGNORE_TERM` variables.
- Produces: `async def stop_process_group(proc: asyncio.subprocess.Process, grace_seconds: float = 5.0) -> None` in `process.py`. It works like this:
  - send `os.killpg(proc.pid, SIGTERM)`;
  - if the process is still running, wait up to `grace_seconds` for it;
  - then send `os.killpg(proc.pid, SIGKILL)`;
  - then wait for the process.

  Ignore `ProcessLookupError` from either `killpg`. The group id equals `proc.pid` because of `start_new_session=True`.

`run` calls `stop_process_group` in its `finally` block every time, even after a normal exit, so children of `claude` that are still alive get killed.

- [ ] **Step 1: Write the failing tests in `tests/test_stopping.py`**

```python
import asyncio, os, sys, time
import pytest
from workflows_agent import AgentRequest, AgentTimeoutError
from workflows_agent_claude_code import ClaudeCodeAgent
from workflows_agent_claude_code.process import stop_process_group

def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True

async def wait_dead(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False

@pytest.fixture
def child_pid_file(tmp_path, monkeypatch, fixture_path):
    path = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CLAUDE_CHILD_PID_FILE", str(path))
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(fixture_path("success.jsonl")))
    return path

async def test_children_killed_after_normal_exit(tmp_path, fake_claude, child_pid_file):
    await ClaudeCodeAgent("haiku", fake_claude).run(AgentRequest(prompt="p", working_folder=tmp_path))
    assert await wait_dead(int(child_pid_file.read_text()))

async def test_timeout_kills_process_group(tmp_path, fake_claude, child_pid_file, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    with pytest.raises(AgentTimeoutError):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path, timeout_seconds=1))
    assert await wait_dead(int(child_pid_file.read_text()))

async def test_cancel_kills_process_group(tmp_path, fake_claude, child_pid_file, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    task = asyncio.create_task(ClaudeCodeAgent("haiku", fake_claude).run(
        AgentRequest(prompt="p", working_folder=tmp_path)))
    while not child_pid_file.exists():
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await wait_dead(int(child_pid_file.read_text()))

async def test_on_event_error_kills_and_propagates(tmp_path, fake_claude, child_pid_file, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    def boom(e):
        raise RuntimeError("sink broke")
    with pytest.raises(RuntimeError, match="sink broke"):
        await ClaudeCodeAgent("haiku", fake_claude).run(
            AgentRequest(prompt="p", working_folder=tmp_path), on_event=boom)
    assert await wait_dead(int(child_pid_file.read_text()))

async def test_sigkill_after_grace_when_term_ignored(tmp_path):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        start_new_session=True)
    await asyncio.sleep(0.2)
    start = time.monotonic()
    await stop_process_group(proc, grace_seconds=0.3)
    assert proc.returncode is not None
    assert time.monotonic() - start < 3
```

- [ ] **Step 2: Run `uv run pytest packages/agents/claude-code/tests/test_stopping.py -q`**

Expected: FAIL. `test_children_killed_after_normal_exit` fails because the child is still alive, and the `process` import fails.

- [ ] **Step 3: Implement `stop_process_group` and call it from `run`'s `finally`**

- [ ] **Step 4: Run all the checks**

Run: `uv run pytest packages -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, with no lint or type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/agents/claude-code
git commit -m "Stop the whole process group when a call ends"
```

---

### Task 6: Integration test, isolated install check, merge

**Files:**
- Create: `tests/integration/test_claude_code.py`
- Modify: `scripts/check_isolated_installs.py`

**Interfaces:**
- Consumes: `ClaudeCodeAgent`.

**Integration test.**
- Skip the module unless `os.environ.get("WORKFLOWS_INTEGRATION") == "1"`.
- Test `test_reads_file_and_writes_result`:
  - write `note.txt` holding `hello integration` in a working folder;
  - make an `out` folder outside it;
  - run `ClaudeCodeAgent("haiku")` with the prompt: `Read note.txt, then write its exact contents to <out>/copy.txt. Reply with DONE.`;
  - pass `extra_folders=[out]` and `timeout_seconds=180`.
- It asserts:
  - `(out / "copy.txt").read_text().strip() == "hello integration"`;
  - `result.session_id` is set;
  - `result.usage.input_tokens > 0`;
  - at least one `ToolCallEvent` was emitted.

**Isolated install check.** Sibling workspace packages must come only from the local wheels. Right now `--find-links` could still pick up a package with the same name from PyPI.
- Write an overrides file with one line per built wheel: `<dist-name> @ <wheel file URI>`. Get the name from the wheel filename, with `_` turned back into `-`.
- Pass `--override <file>` to every `uv pip install`.

Overrides apply only to packages that are actually required, so an undeclared sibling still fails to import, which is what the check is for.

- [ ] **Step 1: Write the integration test, then run `uv run pytest tests/integration -q`**

Expected: `1 skipped`.

- [ ] **Step 2: Run `WORKFLOWS_INTEGRATION=1 uv run pytest tests/integration -q`**

Expected: `1 passed`. This needs a logged-in `claude` and costs a few cents.

- [ ] **Step 3: Update the check script, then run `uv run python scripts/check_isolated_installs.py`**

Expected: `workflows-agent: ok` and `workflows-agent-claude-code: ok`, exit code 0.

- [ ] **Step 4: Check that the script catches a missing dependency**

Temporarily remove `"workflows-agent"` from the claude-code package's `dependencies`. Run the script and expect `workflows-agent-claude-code: FAILED` with exit code 1. Then restore the dependency and don't commit the change.

- [ ] **Step 5: Run everything once more, then commit and merge**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: all tests pass, and the integration test is skipped.

```bash
git add tests/integration scripts/check_isolated_installs.py
git commit -m "Add Claude Code integration test and pin siblings in install check"
git checkout dev && git merge --no-ff claude-code -m "Merge Claude Code agent" && git push origin dev
```
