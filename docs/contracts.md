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
- Tool failures are errors, not soft warnings.
- Tools do not silently recover from invalid parameters or unsafe state.
- The runtime must not provide fallback tools or alternate tool behavior behind the same name.
- Tool registration and validation should prefer PydanticAI-native mechanisms unless the public contract requires a local wrapper.

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
- A tool exception that aborts the run must emit `tool_call_failed` before `run_failed`
- Tool args and tool results in the public contract must be JSON-compatible

## Session Contract

Initial canonical session contract:

- append-only JSONL
- explicit session header
- explicit run and event entries
- no automatic migration of old local session states

Rules:

- Invalid session data should fail load explicitly.
- Session format changes require an ADR and test updates.
- Do not add silent repair logic.
- Session persistence should preserve coding-agent continuity without importing legacy session-tree or migration behavior by default.

Initial executable session slice:

- `session_header`
  - fields: `type`, `version`
- `session_run`
  - fields: `type`, `run_id`, `prompt`
- `session_event`
  - fields: `type`, `run_id`, `event`
  - `event` must be one canonical streamed run event payload

Ordering rules for the session slice:

- The first line must be exactly one `session_header`
- Each `session_run` is followed by zero or more `session_event` lines for the same `run_id`
- Persisted events for a run must satisfy the streamed run contract, including exactly one terminal outcome
- Appending a new run must preserve all existing lines and write the header only once

## RPC Contract

Initial canonical RPC transport:

- JSON over stdio
- explicit command names
- explicit response and event payloads
- strict error responses for invalid commands or invalid state

Rules:

- No compatibility aliases unless deliberately chosen and documented.
- No hidden fallback commands.
- Protocol changes require an ADR and tests.
- RPC exposes the backend contract only; UI-specific command surfaces are out of scope unless deliberately added later.

Initial executable RPC slice:

- request line
  - fields: `id`, `command`, `payload`
  - initial command: `run.start`
  - initial payload: `{"prompt": <string>}`
- `rpc_event`
  - fields: `type`, `id`, `event`
  - `event` must be one canonical streamed run event payload
- `rpc_error`
  - fields: `type`, `id`, `error_type`, `message`

Ordering rules for the RPC slice:

- A valid `run.start` request yields zero or more `rpc_event` lines whose embedded events satisfy the streamed run contract
- A valid request that ends in run failure still yields `rpc_event` lines ending in `run_failed`; it does not switch to `rpc_error`
- Invalid JSON yields exactly one `rpc_error` with `id: null` and `error_type: InvalidJSON`
- Invalid command or payload yields exactly one `rpc_error` with the parsed request `id` when available and `error_type: InvalidRequest`

## Failure Semantics

- No fallback behavior, ever.
- Fail hard on invalid state, invalid inputs, and unsupported operations.
- Prefer explicit recovery instructions in error payloads over automatic retries or silent behavior changes.
- The canonical path should be the only path.
