# Mental Model

read_when: you are new to the repo or need to understand how the pieces fit together

## Overview

This is a headless coding-agent backend. It has no UI. External consumers talk to it over a line-based JSON-over-stdio protocol and receive a stream of typed events. Everything that crosses a boundary has a strict, typed shape called a contract.

The backend is inspired by the pi coding agent's product behavior but does not inherit its architecture. It is built on PydanticAI as the engine.

## Core Concepts

### Contract

A contract is a strict specification of what data looks like when it crosses a boundary. Contracts are Pydantic models with `frozen=True` (immutable) and `extra="forbid"` (no unknown fields). If data doesn't match, it crashes.

There are contracts for:

- **Run events** -- what the agent emits during a run
- **Session entries** -- what gets persisted to disk
- **RPC envelopes** -- what goes over the wire to external consumers
- **Tool inputs** -- what each tool accepts

The backend is headless, so any consumer (a CLI, a web app, an IDE plugin, a benchmark harness) relies on these shapes being stable and predictable. The contract is the product surface.

### Run Events

A run is one prompt-to-response cycle. Every run emits a strict sequence of typed events:

```
run_started -> [text deltas, tool calls] -> run_succeeded | run_failed
```

Rules:

- Exactly one terminal event (success or failure, never both)
- Tool calls have their own sub-lifecycle: `tool_call_started -> tool_call_succeeded | tool_call_failed`
- If the stream crashes mid-tool, all pending tools get failure events before the run failure
- Exceeding PydanticAI `UsageLimits` (request or tool call bounds) ends the run with `run_failed` and `error_type: UsageLimitExceeded`

`stream_run_events()` in `runtime/run.py` translates PydanticAI's internal events into these canonical public events. Runtime exceptions before a terminal event are converted into canonical failure events by design. Any exception after terminal success is invalid state and is raised.

### RPC

RPC (Remote Procedure Call) is how non-Python programs talk to this backend. The protocol is JSON-over-stdio: one JSON object per line, read from stdin, written to stdout. The server runs as a long-lived process via `python -m just_another_coding_agent`.

Two commands:

- `session.create` -- creates a new session, returns a server-generated opaque `session_id`
- `run.start` -- runs a prompt against an existing session, streams run events back

Example flow:

```json
{"id": "req-1", "command": "session.create", "payload": {}}
```
```json
{"type": "rpc_response", "id": "req-1", "response": {"session_id": "a1b2c3..."}}
```
```json
{"id": "req-2", "command": "run.start", "payload": {"session_id": "a1b2c3...", "prompt": "fix the bug"}}
```
```json
{"type": "rpc_event", "id": "req-2", "event": {"type": "run_started", ...}}
{"type": "rpc_event", "id": "req-2", "event": {"type": "run_succeeded", ...}}
```

Three response types:

- `rpc_response` -- synchronous result (e.g., session creation)
- `rpc_event` -- wraps a run event (including failures; a tool crash is still an `rpc_event`)
- `rpc_error` -- protocol-level problems only (bad JSON, unknown command, unknown session, invalid session state)

Clients never see filesystem paths or workspace identifiers. Session identity is an opaque hex string.

### Session

A session is the append-only JSONL file that records what happened across multiple runs. It is bound to exactly one workspace root. Each line is one of:

- `session_header` -- written once, first line, contains format version and workspace root
- `session_run` -- marks start of a run (run_id and prompt)
- `session_messages` -- the native PydanticAI `ModelMessage` list for that run (used for resume)
- `session_event` -- wraps one run event

Example:

```json
{"type":"session_header","version":2,"workspace_root":"/abs/path/to/workspace"}
{"type":"session_run","run_id":"abc","prompt":"fix bug"}
{"type":"session_messages","run_id":"abc","messages":[...]}
{"type":"session_event","run_id":"abc","event":{"type":"run_started","run_id":"abc"}}
{"type":"session_event","run_id":"abc","event":{"type":"run_succeeded","run_id":"abc","output_text":"done"}}
```

Rules: header appears exactly once, no duplicate run IDs, events must satisfy the same ordering rules as the streaming contract. Invalid files fail hard on load. Loading a session against a different workspace root than the one persisted is invalid state.

Sessions persist both public contract events (for consumers) and native PydanticAI message history (for resume). These serve different purposes and neither can replace the other.

### Session Resume

When a session already exists, the runtime loads all persisted `ModelMessage` entries across prior runs and passes them as `message_history` to PydanticAI. This gives the model full conversation context from previous runs without re-executing anything.

The coordinator `stream_session_run_events()` handles the full lifecycle: load session, build agent, stream events, capture messages, persist both events and messages after the run completes. Persistence only happens after terminal completion -- partially consumed streams do not append.

### Session Store

The RPC layer maps opaque session IDs to session files via `rpc/session_store.py`. Session IDs are server-generated 32-character lowercase hex strings validated by a Pydantic `SessionId` type. Clients create sessions via `session.create` and reference them by ID in `run.start`.

### Tools

Four canonical tool names: `read`, `write`, `edit`, `bash`. These are the coding agent's hands.

Each tool is a workspace-bound factory: `create_read_tool(workspace_root=...)` returns a PydanticAI `Tool` with the workspace root captured in a closure. Relative paths resolve from the configured workspace root, but the tools run in YOLO mode: there is no filesystem sandbox.

- `read` -- reads a UTF-8 file, returns contents
- `write` -- writes a UTF-8 file, creates parent dirs, returns confirmation
- `edit` -- replaces exactly one occurrence of `old_text` with `new_text`, fails on zero/multiple matches or no-op
- `bash` -- runs `bash -lc <command>` with `cwd` set to workspace root, returns `{"exit_code": 0, "output": str}` on success and explicit tool error results for non-zero exits or timeouts

`bash` sets `cwd` to the workspace root but has no path sandboxing -- commands can access anything on the system.

The registry (`tools/registry.py`) maps canonical names to factories. `build_canonical_toolset(tool_names, workspace_root=...)` creates factories and returns a PydanticAI `FunctionToolset`.

### Canonical Agent

`build_canonical_agent()` in `runtime/agent.py` is the single official way to assemble a coding agent. It takes a model and workspace root, builds the canonical toolset, enforces `output_type=str`, and sets a concise system prompt via PydanticAI's `instructions` parameter.

The system prompt tells the model what tools it has, how to approach coding tasks, and that read/write/edit are workspace-scoped while bash is not sandboxed. The runtime also appends dynamic context at build time: the current date and the resolved workspace root.

### Runtime

The runtime (`runtime/run.py`) is the bridge between PydanticAI and the public contract. `stream_run_events()`:

1. Creates a unique `run_id`
2. Yields `RunStartedEvent`
3. Applies PydanticAI `UsageLimits` (default: 50 requests, 200 tool calls)
4. Iterates PydanticAI's internal event stream, translating each into a public contract event
5. Tracks pending tool calls so failures cascade correctly
6. Guarantees exactly one terminal event

This is the only place where PydanticAI internals are touched. Everything else works with the public contract types.

### No Fallbacks

Across the entire codebase:

- Invalid tool args crash, not warn
- Malformed session files crash, not auto-repair
- Bad RPC requests get an error response, not a guess at intent
- Missing tool implementations crash, not no-op
- Tool name mismatches crash, not silently substitute
- Exceeding usage limits ends the run explicitly

If something is wrong, the caller knows immediately. Silent recovery hides bugs and makes contracts meaningless.

## How They Fit Together

```
Benchmark harness / CLI / UI (any language)
    | JSON-over-stdio
  RPC server (rpc/stdio.py) -- long-lived process
    | session.create / run.start
  Session store (rpc/session_store.py) -- maps session IDs to files
    |
  Session coordinator (runtime/session.py) -- load, stream, persist
    | RunEvent stream + message capture
  Runtime (runtime/run.py) -- event translation + usage limits
    | PydanticAI events
  Canonical agent (runtime/agent.py) -- model + tools + instructions
    |
  Tools (tools/) -- workspace-bound factories
    | file system / shell

  Session persistence (session/jsonl.py) -- append-only JSONL
  Contracts (contracts/) -- defines all the shapes above
```

The consumer sends RPC requests. The RPC server delegates to the session coordinator, which loads (or creates) a session, builds the canonical agent, streams events through the runtime, and persists both events and messages after completion. The session coordinator is the single path -- both RPC and direct Python calls use it.

## Adapters

The `just_another_coding_agent_adapters` package contains external harness bindings that wrap the canonical backend. Adapters depend on `just_another_coding_agent` but core packages must not depend on adapters.

### exec-prompt

A one-shot CLI (`just-another-coding-agent-exec-prompt`) that spawns the stdio server as a subprocess, sends `session.create` + `run.start`, collects the terminal output, and exits. This is the bridge between benchmark harnesses that expect "run one prompt, get one answer" and the session-based RPC server.

### Harbor

Integration with the Harbor benchmark framework. Includes an install script template, command builder for container execution, and an agent class that uploads the repo into task containers and runs `exec-prompt` inside them. Prompts are base64-encoded to survive shell escaping.
