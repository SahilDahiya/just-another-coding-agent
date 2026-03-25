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

## Session Contract

Initial canonical session contract:

- append-only JSONL
- explicit session header
- explicit run and message entries
- no automatic migration of old local session states

Rules:

- Invalid session data should fail load explicitly.
- Session format changes require an ADR and test updates.
- Do not add silent repair logic.
- Session persistence should preserve coding-agent continuity without importing legacy session-tree or migration behavior by default.

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

## Failure Semantics

- No fallback behavior, ever.
- Fail hard on invalid state, invalid inputs, and unsupported operations.
- Prefer explicit recovery instructions in error payloads over automatic retries or silent behavior changes.
- The canonical path should be the only path.
