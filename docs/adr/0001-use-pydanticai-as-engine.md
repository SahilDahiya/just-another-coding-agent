# ADR 0001: Use PydanticAI As The Engine

read_when: you need the decision behind the core engine choice

## Status

Accepted

## Context

The project is a Python-native coding-agent backend. Rebuilding provider orchestration, tool composition, and generic agent behavior locally would recreate framework work that PydanticAI already provides.

## Decision

Use PydanticAI as the default engine for model interaction, tool composition, and generic agent behavior.

## Consequences

- The repo should stay thin and product-focused.
- Local code should wrap or adapt PydanticAI only where the coding-agent product requires it.
- The project should not grow a parallel general-purpose agent framework.
