# Contracts

read_when: you are defining behavior, writing tests, or deciding what must remain stable

## Purpose

This document defines the canonical external contract for the coding-agent backend. Tests should protect this contract before they protect internal implementation details.

The contract preserves the backend-facing behavior of a pi-style coding agent while remaining independent from pi-mono's internal architecture. Internally, the implementation should prefer direct PydanticAI primitives and expose one simplified, stable public contract.

## Tool Contract

Canonical tool set for the first maintained version:

- `read`
- `write`
- `edit`
- `bash`

Rules:

- Tool names are stable once published.
- Tool inputs must be explicit and validated.
- Expected tool-domain failures must be explicit, model-visible results.
- Tools do not silently recover from invalid parameters or unsafe state.
- The runtime must not provide fallback tools or alternate tool behavior behind the same name.
- Tool registration and validation should prefer PydanticAI-native mechanisms unless the public contract requires a local wrapper.
- Workspace root is explicit backend configuration, not implicit process state.
- Workspace root sets the default base for relative paths; it is not a filesystem sandbox.

Expected tool-domain error result:

- fields: `ok`, `error_type`, `message`
- `ok` is always `false`
- ordinary operational failures should use this result shape instead of terminating the run
- uncaught exceptions and invalid state remain runtime failures

Initial executable tool slice:

- canonical registry names: `read`, `write`, `edit`, `bash`
- unknown tool names fail explicitly
- initial concrete tool implementations: `read`, `write`, `edit`, `bash`

`read` input contract:

- fields: `path`, `offset`, `limit`
- `path` must be a non-empty string
- `offset` is optional and, when present, must be a positive integer line number
- `limit` is optional and, when present, must be a positive integer line count

`read` behavior contract:

- reads one existing UTF-8 text file and returns a string
- resolves relative paths against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- `offset` is 1-indexed and line-based
- `limit` bounds the number of lines returned before any truncation ceiling is applied
- when `offset` or `limit` stops before end of file, the result must include an explicit continuation hint using the next `offset`
- when `limit` is omitted, `read` must still bound output size explicitly instead of dumping arbitrarily large files
- the canonical bounded-read ceiling is `2000` lines or `50 KiB`, whichever is hit first
- when the bounded-read ceiling is hit, the result must include an explicit continuation hint using the next `offset`
- if the first requested line alone exceeds the byte ceiling, the result must return an explicit recovery instruction telling the model to use `bash` for a narrower read
- missing files return an explicit tool error result
- directory paths return an explicit tool error result
- offsets beyond end-of-file return an explicit tool error result
- invalid UTF-8 content returns an explicit tool error result
- no silent truncation, binary fallback, or alternate decoding path

`write` input contract:

- fields: `path`, `content`
- `path` must be a non-empty string
- `content` must be a string and may be empty

`write` behavior contract:

- writes one UTF-8 text file and returns an explicit success message
- resolves relative paths against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- creates parent directories as needed
- overwrites an existing file completely
- directory targets return an explicit tool error result
- no append mode, merge mode, backup file, or silent alternate write path

`edit` input contract:

- fields: `path`, `old_text`, `new_text`
- `path` must be a non-empty string
- `old_text` must be a non-empty string
- `new_text` must be a string and may be empty

`edit` behavior contract:

- edits one existing UTF-8 text file by replacing exactly one occurrence of `old_text`
- resolves relative paths against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- succeeds only when `old_text` matches exactly once
- exact-match misses, ambiguous matches, and no-op replacements return an explicit tool error result
- allows deletion by using an empty `new_text`
- missing files, directory targets, and invalid UTF-8 return an explicit tool error result
- ambiguous matches, missing matches, and no-op replacements return an explicit tool error result
- no fuzzy matching, normalized matching, or alternate replacement heuristic

`bash` input contract:

- fields: `command`, `timeout`
- `command` must be a non-empty string
- `timeout` is optional and, when present, must be a positive integer number of seconds

`bash` behavior contract:

- executes one local `bash -lc` command in the configured workspace root
- sets command cwd to the configured workspace root, but does not sandbox filesystem access outside that root
- returns a JSON-compatible success result with fields `exit_code` and `output`
- successful `bash` results always use `exit_code: 0`
- `output` is the combined stdout and stderr decoded as UTF-8
- large `output` is tail-bounded to the last `2000` lines or `50 KiB`, whichever is hit first
- when `output` is truncated, the result must include an explicit notice with the temp-file path holding the full output
- non-zero exits return an explicit tool error result instead of a success payload
- timeout returns an explicit tool error result and includes captured output when available
- shell spawn failure and invalid UTF-8 output return an explicit tool error result
- no shell fallback, alternate decoder, or hidden retry path

## Streamed Event Contract

Initial canonical event families:

- run lifecycle
- assistant text streaming
- tool execution lifecycle
- terminal success or terminal error

Rules:

- A run has exactly one terminal outcome: success or error.
- Errors are explicit and terminal.
- Event names and payloads should be simple, typed, and versionable.
- The runtime must not emit alternate fallback event shapes for older clients.
- The public event stream should represent the phases of a coding-agent run, not every internal PydanticAI event verbatim.

Initial executable run slice:

- `run_started`
  - fields: `type`, `run_id`
- `assistant_text_delta`
  - fields: `type`, `run_id`, `delta`
- `run_succeeded`
  - fields: `type`, `run_id`, `output_text`
- `run_failed`
  - fields: `type`, `run_id`, `error_type`, `message`

Ordering rules for the initial slice:

- Successful text-only run: `run_started`, zero or more `assistant_text_delta`, `run_succeeded`
- Failed run: `run_started`, zero or more `assistant_text_delta`, `run_failed`
- `run_succeeded` and `run_failed` are mutually exclusive and terminal
- Consumers must not need to understand raw PydanticAI stream event kinds to consume this contract

Initial tool lifecycle slice:

- `tool_call_started`
  - fields: `type`, `run_id`, `tool_call_id`, `tool_name`, `args`, `args_valid`
- `tool_call_succeeded`
  - fields: `type`, `run_id`, `tool_call_id`, `tool_name`, `result`
- `tool_call_failed`
  - fields: `type`, `run_id`, `tool_call_id`, `tool_name`, `error_type`, `message`

Ordering rules for the tool slice:

- Each `tool_call_started` must be followed by exactly one matching `tool_call_succeeded` or `tool_call_failed`
- Expected tool-domain failures should normally be represented as `tool_call_succeeded` with an explicit error result object
- `tool_call_failed` is reserved for uncaught tool failures or invalid runtime state and is terminal for the current run
- A tool exception that aborts the run must emit `tool_call_failed` before `run_failed`
- A tool result must match an existing pending `tool_call_started`; tool name mismatches or orphaned tool results are invalid state and fail the run explicitly
- Tool args and tool results in the public contract must be JSON-compatible

## Session Contract

Initial canonical session contract:

- append-only JSONL
- explicit session header with authoritative workspace metadata
- explicit run, native message-history, and event entries
- no automatic migration of old local session states

Rules:

- Invalid session data should fail load explicitly.
- Session format changes require an ADR and test updates.
- Do not add silent repair logic.
- Session persistence should preserve coding-agent continuity without importing legacy session-tree or migration behavior by default.
- A session belongs to exactly one resolved workspace root; authoritative session loads must provide that workspace root and fail on mismatch.
- Public run events remain part of the persisted contract, but resume-capable conversation state must use the native PydanticAI `ModelMessage` history persisted alongside them.

Initial executable session slice:

- `session_header`
  - fields: `type`, `version`, `workspace_root`
- `session_run`
  - fields: `type`, `run_id`, `prompt`
- `session_messages`
  - fields: `type`, `run_id`, `messages`
  - `messages` must be the native PydanticAI `ModelMessage` list for that run
- `session_event`
  - fields: `type`, `run_id`, `event`
  - `event` must be one canonical streamed run event payload

Ordering rules for the session slice:

- The first line must be exactly one `session_header`
- Each `session_run` is followed by exactly one `session_messages` line and then zero or more `session_event` lines for the same `run_id`
- Authoritative session loads must provide the expected workspace root and it must match the persisted `session_header.workspace_root` exactly
- Session resume semantics must reconstruct conversation context from persisted `session_messages` in chronological order and pass that native history back through PydanticAI `message_history`
- Session-backed runtime streaming persists only after the run reaches a terminal outcome; partially consumed or cancelled streams must not append a partial run
- Persisted events for a run must satisfy the streamed run contract, including exactly one terminal outcome
- Appending a new run must preserve all existing lines and write the header only once

## RPC Contract

Initial canonical RPC transport:

- JSON over stdio
- explicit command names
- explicit response and event payloads
- server-generated opaque session ids
- strict error responses for invalid commands or invalid state

Rules:

- No compatibility aliases unless deliberately chosen and documented.
- No hidden fallback commands.
- Protocol changes require an ADR and tests.
- RPC exposes the backend contract only; UI-specific command surfaces are out of scope unless deliberately added later.

Initial executable RPC slice:

- request line
  - fields: `id`, `command`, `payload`
  - initial commands:
    - `session.create` with payload `{}`
    - `run.start` with payload `{"session_id": <opaque-lowercase-hex-string>, "prompt": <string>}`
- `rpc_response`
  - fields: `type`, `id`, `response`
  - initial response payload: `{"session_id": <opaque-lowercase-hex-string>}`
- `rpc_event`
  - fields: `type`, `id`, `event`
  - `event` must be one canonical streamed run event payload
- `rpc_error`
  - fields: `type`, `id`, `error_type`, `message`

Ordering rules for the RPC slice:

- A valid `session.create` request yields exactly one `rpc_response` containing a server-generated opaque `session_id`
- A valid `run.start` request must reference an existing `session_id` and yields zero or more `rpc_event` lines whose embedded events satisfy the streamed run contract
- A valid request that ends in run failure still yields `rpc_event` lines ending in `run_failed`; it does not switch to `rpc_error`
- Clients must not provide filesystem paths or workspace identifiers in the RPC session contract
- Invalid JSON yields exactly one `rpc_error` with `id: null` and `error_type: InvalidJSON`
- Invalid command or payload yields exactly one `rpc_error` with the parsed request `id` when available and `error_type: InvalidRequest`
- Unknown `session_id` yields exactly one `rpc_error` with `error_type: UnknownSession`
- Persisted-but-invalid session state yields exactly one `rpc_error` with `error_type: InvalidSession`
- Unexpected internal server failures yield exactly one `rpc_error` with `error_type: InternalError`

## Failure Semantics

- No fallback behavior, ever.
- Fail hard on invalid state, invalid inputs, and unsupported operations.
- Prefer explicit recovery instructions in error payloads over automatic retries or silent behavior changes.
- The canonical path should be the only path.
- The canonical runtime applies per-run PydanticAI `UsageLimits` to bound model requests and tool calls; exceeding a limit ends the run explicitly with `run_failed` and `error_type: UsageLimitExceeded`.
- Expected tool-domain failures should be returned to the model as explicit tool result objects instead of ending the run immediately.
- `stream_run_events` intentionally converts pre-terminal runtime exceptions into canonical failure events instead of leaking raw exceptions through the public stream.
- If a pre-terminal exception occurs while tool calls are still pending, each pending tool call emits `tool_call_failed` before the terminal `run_failed`.
- An exception after `run_succeeded` is invalid state and is raised instead of being re-encoded as another event.
