# Code Mode

read_when: you are designing or implementing Code Mode, nested tool calls, or agent tool orchestration

## Purpose

Code Mode is a JACA-owned orchestration runtime for the current agent run.
It gives the model two model-facing tools:

- `exec` starts a small code cell.
- `wait` polls or terminates a yielded code cell.

The code cell may call canonical JACA tools through a backend-owned bridge, but
it must not touch the workspace, shell, permissions, sessions, or transcript
state directly.

## What It Is

Code Mode lets the current model write deterministic program logic that can
call existing tools, compute over their results, and return structured output.

The useful mental model is:

```text
model -> exec code cell
      -> code cell requests canonical tool calls
      -> JACA executes read/write/edit/shell/grep/ls/find/subagent
      -> code cell receives normalized results or typed errors
      -> exec returns output or yields a cell id
      -> wait resumes, polls, or terminates the yielded cell
```

The current source runtime is a small Python subprocess. It executes source as
the body of an async function, so top-level `await` is allowed. The code cell
calls JACA-owned APIs such as:

```python
await tools.read(path="README.md")
await tools.grep(pattern="TODO", path="src")
await tools.shell(command="pytest -q tests/contracts/test_read_tool.py")
emit("intermediate text")
data = json.loads('{"status": "done"}')
return_result({"status": "done"})
```

Those calls must route through the canonical backend tool layer. They are not
shortcuts to Python file I/O, subprocess execution, or direct workspace access.
The worker exposes a small builtins set and no import hook, `open`, or direct
subprocess API. This is a process boundary and API restriction, not a complete
security sandbox; workspace authority still belongs to the backend tools.

The first bridge implementation exposes `read`, `grep`, and `shell`.
`subagent` is intentionally deferred until the basic bridge, provenance, and
timeline semantics are stable.

Nested bridge activity is surfaced as compact updates on the parent `exec`
tool call. The public stream should look like:

```text
tool_call_started exec
tool_call_updated exec: nested read started
tool_call_updated exec: nested read succeeded
tool_call_updated exec: nested shell started
tool_call_updated exec: nested shell failed
tool_call_succeeded exec
```

The first slice deliberately does not emit nested `tool_call_started read` or
`tool_call_started shell` events. That avoids making nested tool calls look
like independent top-level model tool calls and keeps pending-tool ordering
simple. Raw updates from nested tools, such as shell output streaming, must not
escape directly as top-level nested-tool updates; Code Mode publishes its own
typed `code_mode` activity details instead.

## What It Is Not

Code Mode is not provider-side code interpreter. Provider-native code execution
does not own JACA workspace policy, approval behavior, tool activity metadata,
or session semantics.

Code Mode is not `subagent`. A subagent delegates cognition to another model
run and returns a report. Code Mode delegates orchestration to deterministic
program logic inside the current run.

Code Mode may later call `subagent`, but only as a nested canonical tool call
through the backend bridge. That means the ordinary subagent contract would
still own child-run creation, spawn mode, capability limits, parent session/run
provenance, tool activity, and failure behavior. Code Mode must not implement a
separate child-agent system behind the bridge.

```text
subagent:
parent model -> asks child model to inspect
child model -> reasons, uses tools, summarizes
parent model -> consumes report

code mode:
model -> writes code
code -> calls canonical tools through JACA bridge
code -> computes exact result
model -> consumes structured output
```

## Contract

The first contract slice lives in
`src/just_another_coding_agent/contracts/code_mode.py`.

The first tool-registration slice exposes `exec` and `wait` only when the
backend explicitly includes those names in the agent tool list. They are not
part of the default canonical tool set and they are not an onboarding-mode
extension.

RPC clients opt in per run with `enable_code_mode: true` on `run.start`.
The one-shot benchmark wrapper exposes the same opt-in as `--code-mode`, and
the Harbor adapter passes that flag when `JACA_HARBOR_CODE_MODE=1` is set in
the Harbor host process. These switches append `exec` and `wait` for the
current run only; they do not persist Code Mode as a session mode.

The public lifecycle states are:

- `running`
- `yielded`
- `completed`
- `failed`
- `terminated`

Terminal states are `completed`, `failed`, and `terminated`.

The contract models define:

- `CodeModeExecRequest`
  - source text
  - optional yield wait budget
  - optional output token budget
  - optional timeout budget
- `CodeModeWaitRequest`
  - cell id
  - optional yield wait budget
  - optional output token budget
  - optional terminate flag
- `CodeModeCellResult`
  - cell id
  - lifecycle state
  - output chunks
  - optional elapsed milliseconds
  - output truncation flag
  - typed error only for `failed`

The TUI may render these typed fields. It must not infer lifecycle semantics,
permission meaning, or nested tool behavior locally.

## Source Runtime

When no test-injected runner is configured, `exec` runs source through the
default Python subprocess runtime.

Runtime API:

- `await tools.read(path="README.md", offset=None, limit=None)`
- `await tools.grep(pattern="TODO", path="src", glob=None, ignore_case=False,
  literal=False, limit=100)`
- `await tools.shell(command="pytest -q", timeout=None)`
- `json.loads(...)` and `json.dumps(...)`
- `emit(value, channel="stdout")`
- `return_result(value)`

`emit` appends a `stdout` or `stderr` output chunk immediately. `return_result`
ends the source cell and appends one `result` output chunk. Non-string emitted
or returned values are JSON-stringified when possible.

The default runtime communicates with the parent process over a JSON-line
protocol. Tool calls are requests to the parent; the parent invokes the
canonical `CodeModeToolBridge`, then sends either a result or an explicit error
back to the worker. Unknown tools, malformed protocol messages, source errors,
and nested-tool failures fail the cell.

Runtime failures are returned as failed `CodeModeCellResult` values from the
parent `exec` tool call. They do not create independent top-level nested tool
returns in the transcript. If a nested bridge call fails, Code Mode emits a
compact `tool_call_updated exec` activity with `nested_status="failed"` before
the failed cell result is returned to the model.

## Backend Bridge Rule

Nested tool calls must enter the same backend-owned tool semantics as ordinary
model tool calls.

This rule protects:

- workspace path normalization
- sandbox policy
- approval policy
- permission memory
- tool activity and transcript events
- typed operational failures
- session/run provenance
- subagent parent/child run provenance

If a Code Mode implementation needs richer nested-tool metadata, add it to the
Python contract first. Do not teach the Go TUI to infer it.

The bridge should prove simple tool calls first, such as `read`, `grep`, and
`shell`. Add `subagent` bridge coverage after the basic bridge semantics are
stable, because it introduces another model run and therefore a larger
provenance and timeline surface.

## First-Slice Non-Goals

- no durable code-cell persistence across sessions
- no direct filesystem access from the code runtime
- no direct shell access from the code runtime
- no hidden unsandboxed execution path
- no claim that the Python worker is a complete security sandbox
- no general notebook UI
- no Go-side semantic ownership
- no migration or compatibility shim

## Initial Validation Target

The first practical validation target is the evaluations job-analysis workflow:

- inspect recent job JSONL/parquet data
- extract tool sequences such as `tool1-tool4-tool5-tool2`
- compute timings and simple state transitions
- produce structured output that can feed pandas or graph analysis

That workflow is deterministic parsing and aggregation work, so it should use
Code Mode rather than spawning a second reasoning agent.

The validation harness now covers both paths:

- injected runners for narrow service and bridge tests
- the default Python subprocess runtime for source execution through
  `tools.read`, `tools.grep`, `tools.shell`, `emit`, and `return_result`
- an actual model/tool loop where the model calls `exec`, the default runtime
  performs nested bridge calls, compact `exec` updates are streamed, and the
  transcript records only the parent `exec` tool return
- runtime failure cases covering source exceptions, missing source APIs,
  nested bridge failures, shell failures, and cell timeout
