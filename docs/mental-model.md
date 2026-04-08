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
Successful runs may also carry additive usage metadata such as `input_tokens`, `output_tokens`, `total_tokens`, `context_window_used`, and `next_request_context_window_used`. The first three describe the completed run. `next_request_context_window_used` is different: it is the backend estimate of what the next resumed request would send back to the model.
Session-backed runs may also emit one pre-run `session_turn_context_status`
event before `run_started` so clients can see whether the persisted
runtime-framing baseline was missing, reused, or cleared for that run.

### RPC

RPC (Remote Procedure Call) is how non-Python programs talk to this backend. The protocol is JSON-over-stdio: one JSON object per line, read from stdin, written to stdout. The server runs as a long-lived process via `python -m just_another_coding_agent`.

Core RPC commands:

- `auth.status` -- reports backend-owned provider readiness per shipped provider
- `auth.set` -- stores one provider secret in the canonical local secret store, with an explicit storage mode
- `auth.clear` -- removes one stored local provider secret
- `session.create` -- creates a new session, returns a server-generated opaque `session_id`
- `session.name` -- appends one backend-normalized human session name to an existing session
- `run.start` -- runs a prompt against an existing session, streams run events back, may carry an optional `thinking` setting, and ends with one final `rpc_response` after any drained follow-up runs complete
- `run.enqueue` -- queues one non-blank prompt for an already-active session-backed run, with `mode: "next" | "later"`; `next` is attached at the next tool boundary in the active run and `later` is drained after the current run ends. Multiple queued prompts in the same bucket are combined into one follow-up prompt in FIFO order using blank-line separation
- `run.interrupt` -- cancels an already-active session-backed run; when `promote_queued_steer` is true, pending `next` steering is promoted into immediate follow-up delivery
- `session_queue_state` -- a streamed backend-owned queue snapshot event for the active run; clients render `next_prompts` and `later_prompts` directly instead of inferring queue state locally
- `session_queued_prompt_batch_submitted` -- a streamed backend-owned event for queued user text that was actually submitted; clients render this so queued prompts show up in the transcript before the assistant answers them
- `session.compact` -- appends one model-generated durable compaction summary entry for an existing session

Example flow:

```json
{"id": "req-0", "command": "auth.status", "payload": {}}
```
```json
{"type": "rpc_response", "id": "req-0", "response": {"providers": [{"provider": "openai", "configured": false, "secret_configured": false, "requires_secret": true, "source": "none", "env_key": "OPENAI_API_KEY", "reason": "missing_secret"}], "local_secret_store": {"available": true, "message": null, "file_store_path": "/home/user/.jaca/auth.json"}}}
```
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
{"type": "rpc_response", "id": "req-2", "response": {"session_id": "a1b2c3..."}}
```
```json
{"id": "req-3", "command": "run.enqueue", "payload": {"session_id": "a1b2c3...", "prompt": "after that, run the tests", "mode": "later"}}
```
```json
{"type": "rpc_response", "id": "req-3", "response": {"session_id": "a1b2c3...", "queued_count": 1}}
```
```json
{"id": "req-4", "command": "run.interrupt", "payload": {"session_id": "a1b2c3...", "promote_queued_steer": true}}
```
```json
{"type": "rpc_response", "id": "req-4", "response": {"session_id": "a1b2c3...", "promoted_count": 1}}
```
```json
{"id": "req-5", "command": "session.compact", "payload": {"session_id": "a1b2c3..."}}
```
```json
{"type": "rpc_response", "id": "req-5", "response": {"compaction_id": "c0ffee...", "compacted_through_run_id": "abc"}}
```
Three response types:

- `rpc_response` -- synchronous result (e.g., session creation or compaction)
- `rpc_event` -- wraps a streamed backend event, including run events and
  session lifecycle events such as automatic compaction
- `rpc_error` -- protocol-level problems only (bad JSON, unknown command, unknown session, invalid session state)

Clients never see filesystem paths or workspace identifiers. Session identity is an opaque hex string. Provider auth is backend-owned too: provider secrets resolve from environment first, then the local auth file at `~/.jaca/auth.json`. Provider readiness is computed from that secret state plus the effective provider path and endpoint configuration. The config file is not a secret store, and `auth.status` tells clients where the backend-owned auth file lives and whether each provider is currently ready to run.

### Session

A session is the append-only JSONL file that records what happened across multiple runs. It is bound to exactly one workspace root. Each line is one of:

- `session_header` -- written once, first line, contains format version and workspace root
- `session_fork` -- optional direct parent lineage for a forked session
- `session_info` -- append-only durable session metadata such as the backend-normalized human session name
- `session_run` -- marks start of a run (run_id, prompt, and effective thinking setting)
- `session_messages` -- the native PydanticAI `ModelMessage` list for that run (used for resume)
- `session_turn_context` -- optional backend-owned runtime-framing snapshot for that completed run
- `session_event` -- wraps one persisted non-delta run event
- `session_compaction` -- records a durable replacement-history artifact for later resume

Example:

```json
{"type":"session_header","version":10,"workspace_root":"/abs/path/to/workspace","shell_family":"posix"}
{"type":"session_fork","forked_from_session_id":"parent-session-id","forked_from_run_id":"abc"}
{"type":"session_info","name":"auth-store-cleanup-followup"}
{"type":"session_run","run_id":"abc","prompt":"fix bug","thinking":"high"}
{"type":"session_event","run_id":"abc","event":{"type":"run_started","run_id":"abc"}}
{"type":"session_event","run_id":"abc","event":{"type":"run_succeeded","run_id":"abc","output_text":"done","total_tokens":1234,"context_window_used":0.031,"next_request_context_window_used":0.018}}
{"type":"session_messages","run_id":"abc","messages":[...]}
{"type":"session_turn_context","run_id":"abc","model":"openai-responses:gpt-5.3-codex","thinking":"high","workspace_root":"/abs/path/to/workspace","shell_family":"posix","current_date":"2026-04-03","timezone":"America/Los_Angeles","runtime_context_text":"Current date: 2026-04-03\nCurrent timezone: America/Los_Angeles\nCurrent workspace root: /abs/path/to/workspace\nCurrent shell family: posix (bash)\nCurrent model: openai-responses:gpt-5.3-codex\nCurrent thinking setting: high"}
{"type":"session_compaction","compaction_id":"cmp-1","compacted_through_run_id":"abc","replacement_messages":[...]}
```

Rules: header appears exactly once, `session_fork` may appear at most once and only immediately after the header, no duplicate run IDs, events must satisfy the same ordering rules as the streaming contract, and compaction entries may appear only at completed run boundaries. Invalid files fail hard on load. Loading a session against a different workspace root than the one persisted is invalid state.

Sessions persist both durable public contract events and native PydanticAI message history. They also persist the effective per-run thinking setting and may persist one per-run runtime-framing snapshot as `session_turn_context`. These serve different purposes and neither can replace the other. Live `assistant_text_delta` events still exist on the RPC stream for UI rendering, but they are transport-only and are not written into the canonical session file.
Compaction entries now store one model-visible compacted prefix: `replacement_messages` plus `compacted_through_run_id`. `replacement_messages` contains a bounded recent real user-message tail plus one assistant-style plain-text compaction summary message appended last.
The summary itself is generated by a separate model call from the runtime compaction path; the session writer only persists the explicit replacement-history artifact it is given.
Resumed runs now use that durable local history directly. The canonical session runtime does not rely on provider-side server history during continuation, because compaction and resume sizing must measure the same history the next run will actually see.

### Session Resume

When a session already exists, the runtime loads all persisted `ModelMessage` entries across prior runs. If no compaction entry exists, it replays that full history into PydanticAI. If a compaction entry exists, it builds explicit resume history from that durable state: the persisted `replacement_messages` plus later native messages strictly after `compacted_through_run_id`. There is no separate hidden resume-instructions path. The durable session file stays append-only and full-fidelity even though the model sees the compacted view. Around that durable conversation history, the runtime now drives a separate model-visible runtime-framing channel: it reconstructs the last full assistant-style runtime-context prefix when a valid prior baseline exists, and appends a smaller assistant-style runtime-context update message only when the visible framing changed.

Sessions may also carry a persisted `session_turn_context` entry for a completed run. That entry is not part of conversation memory. It is a backend-owned snapshot of how that run was framed: model, thinking, workspace root, shell family, current date, timezone, and the dynamic `runtime_context_text` used to build that run's model-visible runtime-context prefix. Static agent instructions stay on the agent and are not persisted in session history. The persisted turn-context entry is the durable baseline used both for invalidation and for reconstructing the last full runtime-context prefix on later resumed runs.
Before a later session-backed run starts, the runtime explicitly classifies that
persisted baseline as `missing`, `reused`, or `cleared` against the current
framing inputs and emits that classification as a pre-run lifecycle event.

If a new run omits `thinking`, the session runtime inherits the most recent persisted non-null thinking setting from that session. This makes thinking stateful across runs without encoding it in the prompt.

`run.start` against an existing session is the canonical continue operation. There is no separate `session.continue` command.

The visible TUI history shown on resume is intentionally smaller than the
authoritative backend continuation state. The backend may expose a bounded
recent-history preview for resumed or forked sessions, but that preview is
presentation-only. The canonical continuation substrate remains the durable
local `message_history` rebuilt before `run.start`.

The coordinator `stream_session_run_events()` handles the full lifecycle: load session, optionally auto-compact stale history, build the agent, build the runtime-framing injection plan, stream events, capture messages, append `session_run` plus durable non-delta `session_event` lines incrementally, then append trailing `session_messages` and optional `session_turn_context` after terminal completion. Successful resumed runs now persist only new PydanticAI message deltas rather than replayed replacement history or any reconstructed runtime-context messages. Internal instructions and `SystemPromptPart` content are stripped before persistence so backend prompt policy never becomes durable conversation state. If cancellation unwinds through this coordinator, it finalizes the run as terminal `run_failed` so the session stays resumable. Failed runs also sanitize poisoned correction tails before persisting `session_messages`: unresolved trailing repair prompts and the matching invalid tool-call suffix are trimmed from future resume history, but the original durable run events and provider traces remain authoritative for failure forensics. True crashes or abandonment before finalization can still leave an incomplete trailing run on disk, and `load_session(...)` fails hard in that case.

When a later compaction entry is appended, the latest active persisted turn-context baseline is invalidated. Forked sessions also do not inherit parent `session_turn_context` entries. That keeps runtime framing separate from conversation-memory continuity and avoids diffing against stale baseline state after history surgery. When the prior full prefix is still safe to reuse, smaller runtime-context update messages can now cover visible changes in model, thinking, timezone, current date, shell family, and workspace root instead of forcing a full prefix reset for each of those changes.

The current deterministic auto-compaction trigger is model-aware: before a resumed run starts, the runtime estimates tokens for the exact local next-run substrate it will replay, including any reconstructed runtime-context prefix and runtime-context update message plus durable resumed conversation history, adds a conservative reserve for the next prompt and wrapper overhead, and appends one automatic compaction entry when that total crosses the configured fraction of the active model context window. It also requires at least one completed run after `compacted_through_run_id` so a just-compacted session does not immediately compact again on the next resume.

### Session Store

The RPC layer maps opaque session IDs to workspace-scoped session files via
`rpc/session_store.py`. Session IDs are server-generated 32-character
lowercase hex strings validated by a Pydantic `SessionId` type. Each workspace
gets its own shard under `~/.jaca/sessions/<workspace-key>/`, and each session
stores one canonical JSONL file plus one small metadata sidecar used only for
discovery and resume picking. Clients create sessions via `session.create`, may
append a durable human name via `session.name`, and reference sessions by ID
in `run.start`. Those human names are backend-normalized and unique within the
current workspace shard. The installed `jaca` wrapper also offers
`jaca resume <name-or-id>`: it resolves an exact session id or exact
normalized session name inside the current workspace on the Python side, then
launches the same TUI with that existing session preloaded. With no argument,
the wrapper lists the recent sessions from the current workspace, caps the
picker to the most recent ten, and requires an interactive terminal before it
prompts. `jaca fork <name-or-id> [--name <new-name>]` uses the same
workspace-scoped discovery path, creates a new session JSONL plus metadata
sidecar in that shard, appends one durable `session_fork` entry after the
header, and then launches the TUI against the new fork. The sidecar mirrors
only the direct parent `forked_from_session_id` for discovery; the append-only
JSONL session file remains canonical.

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

Canonical tool success activity is now tool-owned. Each canonical tool can use PydanticAI's `ToolReturn` split internally so the model sees the same concise success value while the app gets backend-owned activity metadata in `ToolReturn.metadata`. That metadata is only an internal carrier. It becomes part of the product surface only after the runtime validates and maps it into typed `ToolActivity` fields such as `title`, `display_label`, `summary`, success-path `details`, and optional coarse `group_kind` hints. Non-success tool activity stays deliberately smaller: backend-owned titles, backend-owned display labels, optional summaries, durations, and the same optional `group_kind` without re-parsing typed args into structured details. The public contract intentionally does not expose a tool `group_id`.

Canonical tool concurrency is explicit too. `read`, `grep`, `find`, and `ls` are parallel-eligible; `write`, `edit`, and `shell` are serialized. The runtime also enters an explicit parallel execution mode for tool calls, and the model seam enables provider-side `parallel_tool_calls` by default for canonical provider paths, with explicit carve-outs reserved for specific model paths that prove incompatible.
Those high-frequency read-only tools now execute through one persistent per-run Go helper process rather than per-call Python subprocesses. That helper is an internal execution seam only: Python still owns the public tool schema, validation, activity metadata, result shaping, session meaning, and RPC meaning.

### Canonical Agent

`build_canonical_agent()` in `runtime/agent.py` is the single official way to assemble a coding agent. It takes a model and workspace root, builds the canonical toolset, enforces `output_type=str`, and sets a concise system prompt via PydanticAI's `instructions` parameter. It also keeps a deliberately high PydanticAI output-validation retry budget so a framework output ceiling does not become the stop condition for the plain-text coding agent.
Separately, it keeps a small explicit tool-correction retry budget so
recoverable model mistakes like invented tool names or malformed tool args get
one or two visible correction turns inside the run instead of relying on an
implicit framework default.

The system prompt tells the model what tools it has, how to approach coding tasks, and that read/write/edit are workspace-scoped while shell is not sandboxed. Around that static baseline, the runtime prepends bounded model-visible project-doc messages from workspace-root `AGENTS.md` and `CLAUDE.md` when present, then appends dynamic runtime context such as the current date and the resolved workspace root.

Project-doc injection is not durable session memory. It is runtime-owned contextual history that is rebuilt from the current workspace for each run, stays separate from compaction state, and is also exposed to the TUI so startup can immediately show which instruction files were loaded for the run.
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
