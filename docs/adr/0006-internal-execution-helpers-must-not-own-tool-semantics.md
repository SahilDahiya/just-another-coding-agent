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

If a non-Python helper is introduced, its ownership boundary must be explicit.

Python remains the owner of:

- public tool schemas and validation
- activity metadata semantics
- run and session event meaning
- session persistence and recovery policy
- RPC meaning and public contract tests

Helpers in other languages may:

- execute already-validated internal requests
- optimize a narrow internal execution path
- canonically own a narrow backend tool seam when that ownership is deliberate,
  documented, and covered by tests on the shipped path

Helpers in other languages must not:

- create two competing semantic implementations for the same tool surface
- own session or RPC semantics
- introduce long-lived dual behavior or fallback execution paths

The current explicit exception is the persistent Go read-only worker. It is the
canonical backend implementation for `read`, `ls`, `find`, and `grep`.
Mutating tools and shell semantics remain Python-owned unless a later decision
explicitly changes that.

## Consequences

- Contract tests should assert the shipped canonical path, not dead duplicate
  helpers.
- Any helper protocol should stay narrow and explicitly internal.
- Packaging, observability, and lifecycle management are first-class design
  risks, not afterthoughts.
- Performance work must be scoped carefully so execution optimization does not
  turn into split semantic ownership.
