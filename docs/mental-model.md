# Mental Model

read_when: you are new to the repo or need to understand how the pieces fit together

## Overview

This is a coding-agent backend with a thin first-party terminal UI. External consumers can talk to it over a line-based JSON-over-stdio protocol and receive a stream of typed events. The TUI is a shell over that same runtime rather than a separate product surface. Everything that crosses a boundary has a strict shape called a contract.

The backend is inspired by the pi coding agent's product behavior but does not inherit its architecture. It is built on PydanticAI as the engine.

## Core Concepts

### Contract

A contract is a strict specification of what data looks like when it crosses a boundary. Most backend-owned contracts are Pydantic models with `frozen=True` (immutable) and `extra="forbid"` (no unknown fields). Tool input contracts are the main exception: they live on canonical PydanticAI tool function signatures plus parameter constraints. If data doesn't match, it crashes.

There are contracts for:

- **Run events** -- what the agent emits during a run
- **Session entries** -- what gets persisted to disk
- **RPC envelopes** -- what goes over the wire to external consumers
- **Tool inputs** -- what each tool accepts through its canonical function signature

The backend remains the canonical execution core, so any consumer (the first-party TUI, a CLI, a web app, an IDE plugin, or a benchmark harness) relies on these shapes being stable and predictable. The contract is the product surface.

### Run Events

A run is one prompt-to-response cycle. Every run emits a strict sequence of typed events:

```
run_started -> [text deltas, tool calls] -> run_succeeded | run_failed
```

Rules:

- Exactly one terminal event (success or failure, never both)
- Tool calls have their own sub-lifecycle: `tool_call_started -> tool_call_succeeded | tool_call_failed`
- If the stream crashes mid-tool, all pending tools get failure events before the run failure
- The canonical backend does not impose a backend-level request or tool-call ceiling within a run.

`stream_run_events()` in `runtime/run.py` translates PydanticAI's internal events into these canonical public events. Runtime exceptions before a terminal event are converted into canonical failure events by design. Any exception after terminal success is invalid state and is raised.
Successful runs may also carry additive usage metadata such as `input_tokens`, `output_tokens`, `total_tokens`, and `context_window_used` when the provider reports it and the backend can determine the active model context window.

### RPC

RPC (Remote Procedure Call) is how non-Python programs talk to this backend. The protocol is JSON-over-stdio: one JSON object per line, read from stdin, written to stdout. The server runs as a long-lived process via `python -m just_another_coding_agent`.

Two commands:

- `session.create` -- creates a new session, returns a server-generated opaque `session_id`
- `run.start` -- runs a prompt against an existing session, streams run events back, and may carry an optional `thinking` setting
- `session.compact` -- appends one model-generated durable compaction summary entry for an existing session

Example flow:

```json
{"id": "req-1", "command": "session.create", "payload": {}}
```
```json
{"type": "rpc_response", "id": "req-1", "response": {"session_id": "a1b2c3..."}}
```
```json
{"id": "req-2", "command": "run.start", "payload": {"session_id": "a1b2c3...", "prompt": "fix the bug", "thinking": "high"}}
```
```json
{"type": "rpc_event", "id": "req-2", "event": {"type": "run_started", ...}}
{"type": "rpc_event", "id": "req-2", "event": {"type": "run_succeeded", ...}}
```
```json
{"id": "req-3", "command": "session.compact", "payload": {"session_id": "a1b2c3..."}}
```
```json
{"type": "rpc_response", "id": "req-3", "response": {"compaction_id": "c0ffee...", "summarized_through_run_id": "abc", "first_kept_run_id": null, "summary": {...}}}
```
Three response types:

- `rpc_response` -- synchronous result (e.g., session creation or compaction)
- `rpc_event` -- wraps a streamed backend event, including run events and
  session lifecycle events such as automatic compaction
- `rpc_error` -- protocol-level problems only (bad JSON, unknown command, unknown session, invalid session state)

Clients never see filesystem paths or workspace identifiers. Session identity is an opaque hex string.

### Session

A session is the append-only JSONL file that records what happened across multiple runs. It is bound to exactly one workspace root. Each line is one of:

- `session_header` -- written once, first line, contains format version and workspace root
- `session_run` -- marks start of a run (run_id, prompt, and effective thinking setting)
- `session_messages` -- the native PydanticAI `ModelMessage` list for that run (used for resume)
- `session_event` -- wraps one run event
- `session_compaction` -- records a durable compaction summary, the run
  boundary it summarizes through, and an optional first retained run boundary

Example:

```json
{"type":"session_header","version":7,"workspace_root":"/abs/path/to/workspace"}
{"type":"session_run","run_id":"abc","prompt":"fix bug","thinking":"high"}
{"type":"session_event","run_id":"abc","event":{"type":"run_started","run_id":"abc"}}
{"type":"session_event","run_id":"abc","event":{"type":"run_succeeded","run_id":"abc","output_text":"done","total_tokens":1234,"context_window_used":0.031}}
{"type":"session_messages","run_id":"abc","messages":[...]}
{"type":"session_compaction","compaction_id":"cmp-1","summarized_through_run_id":"abc","first_kept_run_id":null,"summary":{"current_objective":"ship the fix","established_facts":[],"user_preferences":[],"important_paths":[],"read_paths":[],"modified_paths":[],"open_questions":[],"unresolved_work":[]}}
```

Rules: header appears exactly once, no duplicate run IDs, events must satisfy the same ordering rules as the streaming contract, and compaction entries may appear only at completed run boundaries. Invalid files fail hard on load. Loading a session against a different workspace root than the one persisted is invalid state.

Sessions persist both public contract events (for consumers) and native PydanticAI message history (for resume). They also persist the effective per-run thinking setting. These serve different purposes and neither can replace the other.
Compaction entries stay durable session metadata, but they now also change the runtime's effective replayed history: resumed runs materialize a synthetic compaction summary plus retained native messages from the latest compaction entry instead of replaying the summarized raw prefix.
The durable summary also carries explicit structured working-set path state. `read_paths` and `modified_paths` are derived from actual tool activity so a resumed long task can recover what files were recently inspected or changed without relying only on prose.
The summary itself is generated by a separate model call from the runtime compaction path; the session writer only persists the explicit summary it is given.
Resumed runs now use that durable local history directly. The canonical session runtime does not rely on provider-side server history during continuation, because compaction and resume sizing must measure the same history the next run will actually see.

### Session Resume

When a session already exists, the runtime loads all persisted `ModelMessage` entries across prior runs. If no compaction entry exists, it replays that full history into PydanticAI. If a compaction entry exists, it builds explicit resume history from that durable state: one synthetic compaction-summary message plus retained native messages starting at `first_kept_run_id` when present, otherwise all native messages after `summarized_through_run_id`. The durable session file stays append-only and full-fidelity even though the model sees the compacted view.

If a new run omits `thinking`, the session runtime inherits the most recent persisted non-null thinking setting from that session. This makes thinking stateful across runs without encoding it in the prompt.

`run.start` against an existing session is the canonical continue operation. There is no separate `session.continue` command.

The coordinator `stream_session_run_events()` handles the full lifecycle: load session, optionally auto-compact stale history, build the agent, stream events, capture messages, strip synthetic compaction-summary messages back out, append `session_run` plus streamed `session_event` lines incrementally, then append trailing `session_messages` after terminal completion. If cancellation unwinds through this coordinator, it now finalizes the run as terminal `run_failed` so the session stays resumable. Failed runs also sanitize poisoned correction tails before persisting `session_messages`: unresolved trailing repair prompts and the matching invalid tool-call suffix are trimmed from future resume history, but the original run events and traces remain intact for debugging. True crashes or abandonment before finalization can still leave an incomplete trailing run on disk, and `load_session(...)` fails hard in that case.

The current deterministic auto-compaction trigger is model-aware: before a resumed run starts, the runtime estimates tokens for the exact local resume history it will replay, adds a conservative reserve for the next prompt and wrapper overhead, and appends one automatic compaction entry when that total crosses the configured fraction of the active model context window. It also requires at least one completed run after the latest compaction boundary so a just-compacted session does not immediately compact again on the next resume.

### Session Store

The RPC layer maps opaque session IDs to session files via `rpc/session_store.py`. Session IDs are server-generated 32-character lowercase hex strings validated by a Pydantic `SessionId` type. Clients create sessions via `session.create` and reference them by ID in `run.start`.

### Tools

Seven canonical tool names: `read`, `write`, `edit`, `shell`, `grep`, `ls`, `find`. These are the coding agent's hands.

Each canonical tool entrypoint is a plain PydanticAI tool function that takes `RunContext[WorkspaceDeps]`. Those function signatures, including parameter constraints, are the public tool schema seen by the model. The runtime passes one normalized `WorkspaceDeps(workspace_root=...)` per run, so relative paths resolve from the configured workspace root without per-tool closure factories. Internal tool executors may still depend on a narrower structural context when they only need a subset of `RunContext`, but that narrower contract must be explicit in the implementation. The tools still run in YOLO mode: there is no filesystem sandbox.

- `read` -- reads a UTF-8 file, returns contents
- `write` -- writes a UTF-8 file, creates parent dirs, returns confirmation
- `edit` -- replaces exactly one occurrence of `old_text` with `new_text`, trying exact matching first and then a normalized fallback for minor formatting differences while preserving surrounding unmatched content; fails on zero/multiple matches or no-op
- `shell` -- runs one command with the active shell family (`posix`, executed with Bash semantics, or `powershell`) with `cwd` set to workspace root, returns `{"exit_code": 0, "output": str}` on success and explicit tool error results for non-zero exits or timeouts
- `grep` -- searches UTF-8 text files with ripgrep and returns matching lines with relative paths and line numbers
- `ls` -- lists directory contents in a bounded alphabetical view with `/` suffixes for directories
- `find` -- finds files by glob pattern and returns paths relative to the searched directory

`shell` sets `cwd` to the workspace root but has no path sandboxing -- commands can access anything on the system.

The registry (`tools/registry.py`) is thin: it validates canonical tool names, selects the requested tool functions, and returns one wrapped PydanticAI `FunctionToolset`. Expected operational failures are raised as explicit `ToolOperationalError` subclasses and converted to model-visible `{ok: false, ...}` results by a single toolset wrapper. Unexpected exceptions still fail hard.
Shared public tool contract helpers such as canonical names and the `{ok: false, ...}` error result shape live in `contracts/tools.py`, but per-tool input carriers do not.

Canonical tool success activity is now tool-owned. Each canonical tool can use PydanticAI's `ToolReturn` split internally so the model sees the same concise success value while the app gets backend-owned activity metadata in `ToolReturn.metadata`. That metadata is only an internal carrier. It becomes part of the product surface only after the runtime validates and maps it into typed `ToolActivity` fields such as `title`, `summary`, success-path `details`, and optional coarse `group_kind` hints. Non-success tool activity stays deliberately smaller: backend-owned titles, optional summaries, durations, and the same optional `group_kind` without re-parsing typed args into structured details. The public contract intentionally does not expose a tool `group_id`.

Canonical tool concurrency is explicit too. `read`, `grep`, `find`, and `ls` are parallel-eligible; `write`, `edit`, and `shell` are serialized. The runtime also enters an explicit parallel execution mode for tool calls, and the model seam enables provider-side `parallel_tool_calls` by default for canonical provider paths, with explicit carve-outs reserved for specific model paths that prove incompatible.
Those high-frequency read-only tools now execute through one persistent per-run Go helper process rather than per-call Python subprocesses. That helper is an internal execution seam only: Python still owns the public tool schema, validation, activity metadata, result shaping, session meaning, and RPC meaning.

### Canonical Agent

`build_canonical_agent()` in `runtime/agent.py` is the single official way to assemble a coding agent. It takes a model and workspace root, builds the canonical toolset, enforces `output_type=str`, and sets a concise system prompt via PydanticAI's `instructions` parameter. It also keeps a deliberately high PydanticAI output-validation retry budget so a framework output ceiling does not become the stop condition for the plain-text coding agent.
Separately, it keeps a small explicit tool-correction retry budget so
recoverable model mistakes like invented tool names or malformed tool args get
one or two visible correction turns inside the run instead of relying on an
implicit framework default.

The system prompt tells the model what tools it has, how to approach coding tasks, and that read/write/edit are workspace-scoped while shell is not sandboxed. The runtime also appends dynamic context at build time: the current date and the resolved workspace root.
That prompt layer also carries two behavioral rules that matter for benchmark and real coding tasks alike: do not claim a file was created or changed without tool evidence, and verify code changes or required file outputs before concluding.
Thinking is not carried in the prompt. The runtime passes it through PydanticAI model settings as an explicit run input.
Provider-native model behavior is centralized separately in `runtime/models.py`, which resolves model strings, applies OpenAI-compatible retry transport policy, and can wrap models with opt-in instrumentation via `JACA_TRACE_MODE=local|logfire`.
When tracing is enabled, backend startup configures one explicit sink: `local` writes JSONL span files under `~/.jaca/traces/` without requiring Logfire authentication, while `logfire` exports to Logfire and fails hard if credentials are missing. The backend relies on PydanticAI/OpenTelemetry agent and tool spans directly, so evaluation-side watchdog helpers can detect long-tool and shell-heavy probe loops without inspecting session JSONL by hand.

### Runtime

The runtime (`runtime/run.py`) is the bridge between PydanticAI and the public contract. `stream_run_events()`:

1. Creates a unique `run_id`
2. Yields `RunStartedEvent`
3. Streams the run without a default per-run request or tool-call ceiling, optionally passing an explicit `thinking` setting through PydanticAI model settings
4. Hides at most one retryable transient pre-stream failure before any assistant text or tool lifecycle event escapes
5. Iterates PydanticAI's internal event stream, translating each into a public contract event
6. Tracks pending tool calls so failures cascade correctly
7. Guarantees exactly one terminal event

This is the only place where PydanticAI internals are touched. Everything else works with the public contract types.

### No Fallbacks

Across the entire codebase:

- Invalid caller input and invalid runtime state crash, not warn
- Recoverable model-generated tool-call validation errors stay model-visible and
  consume the bounded tool-correction budget instead of becoming silent runtime
  failure
- Malformed session files crash, not auto-repair
- Bad RPC requests get an error response, not a guess at intent
- Missing tool implementations crash, not no-op
- Tool name mismatches crash, not silently substitute
- Live-run stopping behavior comes from external timeouts, caller interruption, or model/provider termination

If something is wrong, the caller knows immediately. Silent recovery hides bugs and makes contracts meaningless.

## How They Fit Together

```
Benchmark harness / CLI / TUI / UI (any language)
    | JSON-over-stdio
  RPC server (rpc/stdio.py) -- long-lived process
    | session.create / run.start
  Session store (rpc/session_store.py) -- maps session IDs to files
    |
  Session coordinator (runtime/session.py) -- load, stream, persist
    | RunEvent stream + message capture
  Runtime (runtime/run.py) -- event translation
    | PydanticAI events
  Canonical agent (runtime/agent.py) -- model + tools + instructions
    |
  Tools (tools/) -- Python-owned schemas + semantics
    | read-only worker (persistent Go helper) for read/ls/find/grep
    | direct file system / shell for the rest

  First-party TUI (tui/) -- status bar + transcript + prompt shell
  Session persistence (session/jsonl.py) -- append-only JSONL
  Contracts (contracts/) -- defines all the shapes above
```

The consumer sends RPC requests. The RPC server delegates to the session coordinator, which loads (or creates) a session, builds the canonical agent, streams events through the runtime, persists `session_run` plus `session_event` entries as they happen, and appends `session_messages` only after completion. The session coordinator is the single path -- both RPC and direct Python calls use it.

## Evaluation Harnesses

The root `evaluations/` package contains non-product harness bindings that wrap the canonical backend. Evaluation code depends on `just_another_coding_agent` but product packages must not depend on evaluation code.

### exec-prompt

A one-shot CLI (`just-another-coding-agent-exec-prompt`) that spawns the stdio server as a subprocess, sends `session.create` + `run.start`, collects the terminal output, and exits. It also supports forwarding an optional `thinking` setting. This is the bridge between benchmark harnesses that expect "run one prompt, get one answer" and the session-based RPC server.

### Harbor

Integration with the Harbor benchmark framework. Includes an install script template, command builder for container execution, and an agent class that uploads the repo into task containers and runs `exec-prompt` inside them. Prompts are base64-encoded to survive shell escaping.
