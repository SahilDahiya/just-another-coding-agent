# MCP Architecture

read_when: you are designing MCP support, Code Mode nested tool routing, or onboarding tool surfaces

## Decision

JACA should treat MCP as the extension substrate, not as a replacement for the
backend contract.

PydanticAI remains the agent engine. JACA owns product semantics:
permissions, approvals, tool provenance, timeline events, sessions, RPC
contracts, Code Mode routing, and onboarding behavior. MCP servers provide
tool and resource surfaces that enter that backend-owned contract.

The target shape is:

```text
PydanticAI Agent
  -> JACA tool registry
    -> canonical tools
    -> JACA MCP manager
      -> built-in MCP: jaca_onboarding
      -> future built-in and external MCPs
    -> Code Mode exec/wait
      -> nested JACA tool router
        -> canonical tools
        -> MCP tools
```

## PydanticAI Boundary

Use PydanticAI where it already provides the right framework seam:

- agent execution, streaming, messages, model settings, and ordinary toolsets
- MCP client primitives such as stdio, streamable HTTP, and SSE transports
- MCP tool-call wrapping hooks where they help pass JACA run context or
  provenance into a call
- PydanticAI agents inside an MCP server only when a server implementation
  truly needs an internal model call

Do not let PydanticAI MCP attachment become the public JACA MCP architecture by
itself. Directly attaching an MCP toolset to the agent is useful plumbing, but
JACA still needs one backend-owned layer for namespacing, policy, approval,
activity shaping, transcript persistence, Code Mode nested calls, and TUI
contracts.

PydanticAI Harness Code Mode is useful prior art: it shows the same basic
pattern of letting a model write code that orchestrates tools. JACA should
borrow design lessons from it, but not replace JACA Code Mode unless the
replacement can preserve the JACA-owned backend contract.

References:

- PydanticAI MCP overview: `https://pydantic.dev/docs/ai/mcp/overview/`
- PydanticAI MCP client: `https://pydantic.dev/docs/ai/mcp/client/`
- PydanticAI MCP server: `https://pydantic.dev/docs/ai/mcp/server/`
- PydanticAI Harness Code Mode: `https://pydantic.dev/docs/ai/harness/code-mode/`

## MCP Manager

JACA needs a runtime MCP manager before onboarding is moved to MCP.

Responsibilities:

- own the configured and built-in MCP server catalog
- start and stop local built-in servers when needed
- connect to external stdio, streamable HTTP, or SSE servers through the
  selected PydanticAI or MCP SDK primitives
- discover tools, resources, and server instructions
- normalize model-facing names, for example
  `mcp__jaca_onboarding__publish_teaching_packet`
- route tool calls through the same backend policy and activity layer as
  canonical tools
- preserve call provenance, including whether the call came from top-level
  model tool use or from Code Mode
- fail hard when server startup, discovery, schema validation, tool execution,
  or resource reads fail

The manager is a backend runtime component. The Go TUI may render backend-owned
MCP status and activity, but it must not infer MCP trust, tool meaning, or
server lifecycle state locally.

The first contract slice lives in
`src/just_another_coding_agent/contracts/mcp.py`. It defines:

- stable model-facing MCP tool names with the form `mcp__server__tool`
- the reserved built-in onboarding server id, `jaca_onboarding`
- provenance for top-level model calls vs Code Mode nested calls
- typed MCP failure kinds for startup, discovery, tool execution, and resource
  reads

The TUI-facing activity contract uses `McpActivityDetails` in
`contracts/run_events.py`; clients should render those typed fields instead of
parsing MCP meaning from display text.

The first runtime slices live in
`src/just_another_coding_agent/runtime/mcp.py`. They provide a backend-owned
`McpManager`, built-in `jaca_onboarding` server metadata, a PydanticAI
`McpToolset`, and an `McpToolExecutor` seam. The toolset can expose MCP-shaped
tools to the model and route calls through the executor while returning typed
`McpActivityDetails`. This still does not attach MCP tools to the canonical
agent by default or replace native onboarding tools yet.

The first built-in executor is `JacaOnboardingMcpExecutor`. It adapts the
`jaca_onboarding` MCP tool identities onto the existing backend onboarding
implementations, unwrapping their native `ToolReturn` values while preserving
MCP-shaped activity metadata at the outer tool boundary.

## Code Mode

Code Mode should call MCP tools through the same nested tool router it uses for
canonical tools.

The desired model-facing shape is generated/namespaced helper methods, not an
unstructured raw escape hatch:

```python
await tools.mcp__jaca_onboarding__publish_teaching_packet(...)
await tools.mcp__jaca_onboarding__ask_mcq_question(...)
```

Avoid exposing a generic `mcp.call_tool(server, name, args)` as the first
interface. A generic escape hatch makes it harder to document, validate,
surface in prompts, and enforce per-tool policy. If a raw call helper is ever
added, it should be a later debugging or expert surface with explicit contract
coverage.

Nested MCP activity must remain parented under the Code Mode `exec` call in
the public stream, the same way nested canonical tool calls are parented today.

## Onboarding

Onboarding should be model-visible only as the built-in
`jaca_onboarding` MCP server.

`/onboard` may still be a JACA mode command, but the mode should only select
prompt overlay, state posture, and available MCP server/tool surfaces. It must
not add native onboarding tools to the model-facing registry.

The onboarding MCP server may expose tools such as:

- `ask_mcq_question`
- `publish_teaching_packet`
- `generate_mcq_from_teaching_packets`

It may expose resources such as:

- `jaca://onboarding/guide`
- `jaca://onboarding/code-mode`
- `jaca://onboarding/tools`
- `jaca://teaching-packets/{packet_id}`

The onboarding domain implementation may remain ordinary JACA backend code:
SQLite persistence, validation, teaching packet storage, user-interaction
events, and TUI rendering contracts all stay backend-owned. MCP is the
model-facing protocol boundary, not the place where JACA gives up ownership of
onboarding semantics.

Do not depend on MCP elicitation as the first implementation of asking the
human a question. The JACA backend already owns user-facing request/response
events and the TUI contract. The onboarding MCP tool can call that internal
domain seam and return a typed result.

## Implementation Order

1. Add the JACA MCP manager and typed MCP activity/provenance contracts.
2. Register one built-in MCP server, `jaca_onboarding`, using the installed MCP
   SDK/FastMCP-style server primitives unless a standalone FastMCP dependency
   proves necessary.
3. Move onboarding tools out of the native model registry and expose them only
   through `jaca_onboarding`.
4. Route top-level MCP tool calls through the same backend policy and activity
   layer as canonical tools.
5. Extend Code Mode's nested bridge to generated/namespaced MCP tool helpers.
6. Add tests that prove ordinary model tool calls and Code Mode nested calls
   both reach the same MCP-backed onboarding tools with the same provenance and
   failure behavior.
