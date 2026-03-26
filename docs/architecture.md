# Architecture

read_when: you need the big picture or need to know where code belongs

## System Overview

The architecture is intentionally thin. PydanticAI is the engine. This repo owns only the coding-agent product surface that is specific to the backend.

## Implementation Stance

Prefer direct use of PydanticAI primitives before creating local abstractions:

- use PydanticAI agent runs and streaming as the execution core
- use PydanticAI function tools and toolsets as the default tool substrate
- use PydanticAI message-history primitives as the default conversation substrate
- use PydanticAI static `instructions` for the canonical baseline agent prompt unless preserving prompt messages in history is explicitly required
- use PydanticAI testing primitives for unit and contract tests

Local code should translate those primitives into the canonical backend contract for tools, events, sessions, RPC, and failure semantics.

The canonical agent assembly must take an explicit workspace root. Tool behavior must be scoped from that root rather than relying on process cwd or other implicit global state.
Persisted sessions must also bind to that explicit workspace root and store native PydanticAI message history so later runs can resume through `message_history` instead of reconstructing context from public events.
The canonical runtime must also apply per-run PydanticAI `UsageLimits` so request and tool loops fail hard instead of running unbounded. The current operational defaults are `request_limit=50` and `tool_calls_limit=200`.

## Canonical Package Layout

- `src/just_another_coding_agent/runtime/`
  - agent construction
  - orchestration entrypoints
  - event translation from PydanticAI into the public contract
- `src/just_another_coding_agent/tools/`
  - canonical coding tools
  - tool execution policy
- `src/just_another_coding_agent/session/`
  - session persistence
  - session load/save helpers
- `src/just_another_coding_agent/rpc/`
  - JSON-over-stdio protocol
  - command handlers
- `src/just_another_coding_agent/contracts/`
  - contract types, constants, and schema helpers
- `src/just_another_coding_agent_adapters/`
  - external harness and benchmark adapters such as Harbor or Terminal Bench
  - depends on `just_another_coding_agent`; core backend packages must not depend on adapters

## Boundaries

- Do not build a second general-purpose agent framework in this repo.
- Keep provider-specific behavior inside PydanticAI unless a coding-agent requirement forces a local adapter.
- Keep the runtime thin and contract-driven.
- Keep tool behavior strict and explicit.
- Keep sessions and RPC stable only when deliberately chosen as public contracts.
- Do not import pi-mono's package layout, UI model, or extension architecture into this repo.
- Keep Harbor, Terminal Bench, and similar external harness bindings out of `just_another_coding_agent` core packages.
- External adapters may wrap the canonical stdio/session/runtime path, but they must not create a second execution contract.

## Data Flow

1. A caller starts a run through the runtime or RPC layer, and RPC delegates to the same session-aware runtime coordinator rather than maintaining a separate execution path.
   RPC owns only server-generated opaque session ids and the mapping to session files; clients do not see filesystem paths or workspace metadata.
2. The runtime creates or resumes a coding-agent run using PydanticAI primitives directly where possible, with an explicit workspace root bound into the canonical toolset and persisted `message_history` supplied for session continuation.
3. Tools execute through the canonical tool layer.
4. Events are translated into the public streamed event contract rather than exposing raw framework internals directly.
5. Session entries persist both the public run events and the native PydanticAI message history for that run, bound to the authoritative workspace root.
