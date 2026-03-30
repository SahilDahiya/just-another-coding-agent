# ADR 0006: Internal Execution Helpers Must Not Own Tool Semantics

read_when: you are deciding whether Go, Rust, or another helper may execute backend work

## Status

Accepted

## Context

The repo already has one explicit implementation boundary: Python owns backend
semantics and public contracts, while the Go TUI owns presentation and RPC
client behavior. That boundary keeps the product from drifting into two
separate implementations of the coding agent.

The same question can reappear in other forms. A future persistent Rust worker
for read-only tools could be a good performance optimization over per-call
Python subprocesses, but it also creates a second runtime boundary, a helper
lifecycle, and an internal RPC surface.

Without a clear rule, that helper could slowly become a second owner of tool
meaning instead of a narrow execution engine.

## Decision

If a non-Python helper is introduced, it stays an internal execution engine
only.

Python remains the owner of:

- public tool schemas and validation
- result shaping and tool error result semantics
- activity metadata semantics
- run and session event meaning
- session persistence and recovery policy
- RPC meaning and public contract tests

Helpers in other languages may:

- execute already-validated internal requests
- optimize a narrow internal execution path
- return internal results for Python to normalize into the public contract

Helpers in other languages must not:

- become a second source of truth for tool semantics
- invent alternate result strings or activity meaning
- own session or RPC semantics
- introduce long-lived dual behavior or fallback execution paths

The intended first candidate scope, if implemented, is read-only tool
execution such as `read`, `ls`, `find`, and `grep`. Mutating tools and shell
semantics remain Python-owned unless a later decision explicitly changes that.

## Consequences

- Contract tests should keep asserting Python-visible behavior, not helper
  internals.
- Any helper protocol should stay narrow and explicitly internal.
- Packaging, observability, and lifecycle management are first-class design
  risks, not afterthoughts.
- Performance work must be scoped carefully so execution optimization does not
  turn into semantic ownership drift.
