# Architecture

read_when: you need the big picture or need to know where code belongs

## System Overview

The architecture is intentionally thin. PydanticAI is the engine. This repo owns only the coding-agent product surface that is specific to the backend.

## Implementation Stance

Prefer direct use of PydanticAI primitives before creating local abstractions:

- use PydanticAI agent runs and streaming as the execution core
- use PydanticAI function tools and toolsets as the default tool substrate
- use PydanticAI message-history primitives as the default conversation substrate
- use PydanticAI testing primitives for unit and contract tests

Local code should translate those primitives into the canonical backend contract for tools, events, sessions, RPC, and failure semantics.

## Canonical Package Layout

- `src/pi_code_agent/runtime/`
  - agent construction
  - orchestration entrypoints
  - event translation from PydanticAI into the public contract
- `src/pi_code_agent/tools/`
  - canonical coding tools
  - tool execution policy
- `src/pi_code_agent/session/`
  - session persistence
  - session load/save helpers
- `src/pi_code_agent/rpc/`
  - JSON-over-stdio protocol
  - command handlers
- `src/pi_code_agent/contracts/`
  - contract types, constants, and schema helpers

## Boundaries

- Do not build a second general-purpose agent framework in this repo.
- Keep provider-specific behavior inside PydanticAI unless a coding-agent requirement forces a local adapter.
- Keep the runtime thin and contract-driven.
- Keep tool behavior strict and explicit.
- Keep sessions and RPC stable only when deliberately chosen as public contracts.
- Do not import pi-mono's package layout, UI model, or extension architecture into this repo.

## Data Flow

1. A caller starts a run through the runtime or RPC layer.
2. The runtime creates or resumes a coding-agent run using PydanticAI primitives directly where possible.
3. Tools execute through the canonical tool layer.
4. Events are translated into the public streamed event contract rather than exposing raw framework internals directly.
5. Session entries are persisted through the session layer.
