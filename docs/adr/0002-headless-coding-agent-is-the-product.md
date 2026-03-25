# ADR 0002: Headless Coding Agent Is The Product

read_when: you need the boundary between product work and framework drift

## Status

Accepted

## Context

Without an explicit product boundary, the repo could drift into a general-purpose agent project.

## Decision

The product is a headless coding-agent backend.

## Consequences

- Runtime, tools, sessions, events, and RPC should be judged by coding-agent needs.
- UI work is out of scope.
- General-purpose agent features are out of scope unless they directly serve the coding-agent backend.
