# ADR 0004: Canonical Package Layout

read_when: you need the reasoning behind the package structure

## Status

Accepted

## Context

The old multi-package layout from the prior repo reflected legacy architecture. This repo should not let that shape the new design.

## Decision

Use one root package: `just_another_coding_agent`.

Subpackages:

- `runtime`
- `tools`
- `session`
- `rpc`
- `contracts`

## Consequences

- The codebase has one clear center of gravity.
- Public imports can stay coherent as the implementation grows.
- The repo avoids turning old architectural seams into permanent Python package boundaries.
