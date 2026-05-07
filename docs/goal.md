# Project Goal

read_when: you need the authoritative scope and product intent

## Goal

Build a Python-native coding-agent backend around PydanticAI, with a thin first-party Go terminal UI over the same runtime.

The product is a coding agent backend first. The TUI is a first-party shell over that backend, not a second product and not an invitation to build a terminal IDE.

The move from a Python TUI to a Go TUI is justified only by product quality and shell craft. It does not change contract ownership. Python remains the single source of truth for backend semantics, tool behavior, streamed event meaning, session meaning, and recovery policy. The Go TUI may present that contract well, but it must not become a second agent runtime or a second place where product semantics are invented.

That same rule applies to the shipped model surface: Python owns the canonical
catalog of selectable models and provider defaults, and the Go TUI renders that
backend-owned catalog instead of hardcoding model ids locally.

The same boundary applies to any future non-Python execution helper. If a
Rust worker or similar helper is added for performance-sensitive internal
execution, it remains an implementation detail under Python-owned tool
contracts. Python still owns tool schemas, validation, activity semantics,
result shaping, session meaning, RPC meaning, and recovery policy.

## Inspiration Boundary

The behavioral inspiration is pi's coding-agent product surface:

- canonical coding tools
- streamed assistant and tool activity
- persisted sessions
- RPC-friendly headless operation

The repo does not inherit pi-mono's architecture or ecosystem surface:

- no monorepo package split
- no extension, theme, or prompt-template platform in the initial product
- no legacy migration or compatibility burden

Use PydanticAI primitives wherever they already solve the problem. Local code should exist only where the coding-agent product needs a stable contract or product-specific policy.

## In Scope

- Agent runtime for coding workflows
- Thin first-party TUI with exactly three zones: status bar, transcript, and prompt
- Canonical backend tools: `read`, `write`, `edit`, `shell`, `grep`, `ls`,
  `find`, `subagent`
- Onboarding-mode-only tools such as `ask_mcq_question`,
  `generate_mcq_from_teaching_packets`, and `publish_teaching_packet`
- Streaming run events
- Session persistence
- JSON-over-stdio RPC
- Strict failure semantics with no fallbacks

`/onboard` is the explicit signal to put the active session into onboarding
mode. That mode then persists across later plain user turns until the user
exits it with `/exit-mode`. The backend, not the Go TUI, owns what onboarding
mode means: onboarding prompt overlay, onboarding tool visibility, and any
future onboarding-specific primitives. The current default is to keep the same
model unless a later product decision proves otherwise.

## Out of Scope

- Any fourth TUI zone such as sidebars, drawers, file browsers, split panes, or inspector panels
- Web UI, extension UI, or terminal-IDE surface growth
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
7. Treat the Go TUI as a shell over the backend, not a second implementation of backend logic.
8. Code is read far more often than it is written. Optimize for the reader:
   - Clarity over cleverness — don't write a dense one-liner when a few clear lines communicate intent better.
   - Names that explain why, not what — `max_compaction_reserve` over `val` or `n`.
   - Structure that reveals intent — a cold reader should understand *why* code exists, not just *what* it does.
   - Don't compress for the sake of fewer lines — shorter isn't always more readable.
9. Measure what you are deciding about, not a proxy for it. When writing
   estimation, budgeting, or threshold code, the measurement must target the
   exact artifact the decision acts on. A rough estimate of the right thing
   beats a precise measurement of a correlated-but-different thing. Only use
   indirect measurements when you can prove the relationship is stable (same
   scope, same contents, same overhead).
