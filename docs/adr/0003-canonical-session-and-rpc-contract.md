# ADR 0003: Canonical Session And RPC Contract

read_when: you need the decision behind the external persistence and transport contract

## Status

Accepted

## Context

The backend needs a simple persistence format and a simple transport for non-Python consumers. These choices should be deliberate and testable.

## Decision

Use:

- append-only JSONL for sessions
- JSON-over-stdio for RPC

Both are canonical contracts for the first maintained version.

## Consequences

- Contract tests must protect session and RPC behavior.
- No fallback protocol shapes or compatibility aliases should be added casually.
- Changes to either contract require an ADR and test updates.
