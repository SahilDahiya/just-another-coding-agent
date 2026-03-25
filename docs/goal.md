# Project Goal

read_when: you need the authoritative scope and product intent

## Goal

Build a Python-native, headless coding-agent backend around PydanticAI.

The product is a coding agent backend, not a general-purpose agent framework and not a UI.

## Inspiration Boundary

The behavioral inspiration is pi's coding-agent product surface:

- canonical coding tools
- streamed assistant and tool activity
- persisted sessions
- RPC-friendly headless operation

The repo does not inherit pi-mono's architecture or ecosystem surface:

- no monorepo package split
- no TUI or web UI
- no extension, theme, or prompt-template platform in the initial product
- no legacy migration or compatibility burden

Use PydanticAI primitives wherever they already solve the problem. Local code should exist only where the coding-agent product needs a stable contract or product-specific policy.

## In Scope

- Agent runtime for coding workflows
- Canonical coding tools: `read`, `write`, `edit`, `bash`
- Streaming run events
- Session persistence
- JSON-over-stdio RPC
- Strict failure semantics with no fallbacks

## Out of Scope

- UI of any kind
- TUI-specific or web-UI-specific concerns from pi-mono
- Extension, theme, prompt-template, or package ecosystems
- General-purpose agent product work outside coding workflows
- Backward compatibility guarantees
- Legacy migration shims
- Rebuilding generic framework capabilities already provided by PydanticAI unless the coding-agent product requires a custom layer

## Working Rules

1. Use PydanticAI as the default implementation substrate for runs, tools, message history, and testing.
2. Keep the product constrained to the coding-agent backend.
3. Preserve pi-like product behavior without importing pi-mono architecture.
4. Prefer deleting old-state support over carrying compatibility baggage.
5. Protect public contracts first: tools, events, sessions, RPC, failure semantics.
6. Default to TDD for maintained code.
