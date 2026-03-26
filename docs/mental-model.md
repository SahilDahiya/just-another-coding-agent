# Mental Model

read_when: you are new to the repo or need to understand how the pieces fit together

## Overview

This is a headless coding-agent backend. It has no UI. External consumers talk to it over a line-based protocol and receive a stream of typed events. Everything that crosses a boundary has a strict, typed shape called a contract.

## Core Concepts

### Contract

A contract is a strict specification of what data looks like when it crosses a boundary. Contracts are Pydantic models with `frozen=True` (immutable) and `extra="forbid"` (no unknown fields). If data doesn't match, it crashes.

There are contracts for:

- **Run events** -- what the agent emits during a run
- **Session entries** -- what gets persisted to disk
- **RPC envelopes** -- what goes over the wire to external consumers
- **Tool inputs** -- what each tool accepts

The backend is headless, so any consumer (a CLI, a web app, an IDE plugin) relies on these shapes being stable and predictable. The contract is the product surface.

### Run Events

A run is one prompt-to-response cycle. Every run emits a strict sequence of typed events:

```
run_started -> [text deltas, tool calls] -> run_succeeded | run_failed
```

Rules:

- Exactly one terminal event (success or failure, never both)
- Tool calls have their own sub-lifecycle: `tool_call_started -> tool_call_succeeded | tool_call_failed`
- If the stream crashes mid-tool, all pending tools get failure events before the run failure

`stream_run_events()` in `runtime/run.py` translates PydanticAI's internal events into these canonical public events.

### RPC

RPC (Remote Procedure Call) is how non-Python programs talk to this backend. The protocol is JSON-over-stdio: one JSON object per line, read from stdin, written to stdout.

A consumer sends a request:

```json
{"id": "req-1", "command": "run.start", "payload": {"prompt": "fix the bug"}}
```

The backend streams back responses:

```json
{"type": "rpc_event", "id": "req-1", "event": {"type": "run_started", ...}}
{"type": "rpc_event", "id": "req-1", "event": {"type": "run_succeeded", ...}}
```

Two response types:

- `rpc_event` -- wraps a run event (including failures; a tool crash is still an `rpc_event`)
- `rpc_error` -- protocol-level problems only (bad JSON, unknown command)

Any language that can read and write lines to a process can consume the agent.

### Session

A session is the append-only JSONL file that records what happened across multiple runs. Each line is one of:

- `session_header` -- written once, first line, contains format version
- `session_run` -- marks start of a run (run_id and prompt)
- `session_event` -- wraps one run event

Example:

```json
{"type":"session_header","version":1}
{"type":"session_run","run_id":"abc","prompt":"fix bug"}
{"type":"session_event","run_id":"abc","event":{"type":"run_started","run_id":"abc"}}
{"type":"session_event","run_id":"abc","event":{"type":"run_succeeded","run_id":"abc","output_text":"done"}}
```

Rules: header appears exactly once, no duplicate run IDs, events must satisfy the same ordering rules as the streaming contract. Invalid files fail hard on load.

### Tools

Four canonical tool names: `read`, `write`, `edit`, `bash`. These are the coding agent's hands.

The registry (`tools/registry.py`) is the gatekeeper:

- Unknown tool name -> `UnknownToolError`
- Canonical name but not yet built -> `ToolNotImplementedError`
- `build_canonical_toolset(["read"])` returns a PydanticAI `FunctionToolset` ready for an agent

Only `read` is implemented so far. It reads a UTF-8 file and returns its contents. Missing file, directory, or binary content all raise explicitly.

### Runtime

The runtime (`runtime/run.py`) is the bridge between PydanticAI and the public contract. Its one function, `stream_run_events()`:

1. Creates a unique `run_id`
2. Yields `RunStartedEvent`
3. Iterates PydanticAI's internal event stream, translating each into a public contract event
4. Tracks pending tool calls so failures cascade correctly
5. Guarantees exactly one terminal event

This is the only place where PydanticAI internals are touched. Everything else works with the public contract types.

### No Fallbacks

Across the entire codebase:

- Invalid tool args crash, not warn
- Malformed session files crash, not auto-repair
- Bad RPC requests get an error response, not a guess at intent
- Missing tool implementations crash, not no-op
- Tool name mismatches crash, not silently substitute

If something is wrong, the caller knows immediately. Silent recovery hides bugs and makes contracts meaningless.

## How They Fit Together

```
Consumer (any language)
    | JSON-over-stdio
  RPC layer (rpc/)
    | RunEvent stream
  Runtime (runtime/)
    | PydanticAI events
  Tools (tools/)
    | file system / shell

  Session (session/) <- persists events to JSONL
  Contracts (contracts/) <- defines all the shapes above
```

The consumer sends an RPC request. The RPC layer calls the runtime. The runtime runs the agent with registered tools, translates PydanticAI events into contract events, and streams them back through RPC. Optionally, the events are also persisted to a session file for continuity across runs.
