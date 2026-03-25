# ADR 0005: Preserve pi Product Behavior, Not Architecture

read_when: you are deciding whether a pi-mono concept should shape this repo

## Status

Accepted

## Context

The new repo is inspired by pi's coding-agent product behavior, but the old monorepo also carries architecture, UI layers, extension surfaces, and migration history that would distort the new Python-native backend if copied directly.

## Decision

Use pi as inspiration for the backend product surface only:

- canonical coding tools
- streamed coding-agent run events
- persisted sessions
- RPC-friendly headless operation

Do not import pi-mono's architecture by default:

- no monorepo package split
- no TUI or web UI layers
- no extension, theme, or prompt-template platform in the initial backend
- no legacy migration or compatibility burden

Implement the backend around PydanticAI primitives wherever they already solve the problem. Add local code only where the coding-agent product requires a stable external contract or product-specific policy.

## Consequences

- Future design discussions should separate product behavior from legacy implementation details.
- Contract tests should protect the backend-facing surface, not pi-mono's internal structure.
- New local abstractions need justification when PydanticAI already provides the needed primitive.
