# TUI

read_when: you are changing the terminal UI, reviewing visual direction, or deciding whether a new TUI feature belongs

## Product Statement

The JACA TUI should feel like a high-craft terminal product for coding work:

- calm under load
- readable during long streaming sessions
- keyboard-first
- visually intentional without becoming theatrical

The TUI is a first-party shell over the same backend runtime, tools, sessions,
and RPC-facing contract. It is not a second product with a separate feature
agenda.

## Hard Constraints

- The TUI has exactly three interaction zones: status bar, transcript, and prompt.
- No fourth zone will be added.
- No sidebars, drawers, file browsers, split panes, inspector panels, or terminal-IDE surfaces.
- If a capability cannot be expressed through the transcript or a slash command, it does not belong in the canonical TUI.

## Product Bar

- One-column interaction model
- Fast startup and reliable input handling
- Strong transcript readability for user, assistant, tool, warning, and error states
- Motion only where it clarifies state transitions
- Deliberate visual hierarchy instead of terminal clutter

## Anti-Goals

- Rebuilding an IDE in the terminal
- Adding panels just because Textual makes them possible
- Decorative animation without state meaning
- Terminal-specific hacks without tests and explicit deletion criteria
- Feature growth that weakens the canonical interaction model

## Motion Budget

- Use motion only for stateful transitions such as startup, pending, streaming, completion, and interruption.
- Prefer short transitions in the 120-300ms range.
- Keep at most one animated region prominent at a time.
- No looping decorative motion outside explicit pending/loading feedback.
- Motion must improve comprehension, not just make the UI feel busy.

## Default Interaction Model

- The status bar answers "where am I and what state is this session in?"
- The transcript is the single durable surface for all work: prompts, streaming assistant output, tool activity, warnings, and errors.
- The prompt is the single input surface for chat and slash commands.

## North Star

Use pi as the reference for craft, terminal discipline, and restraint.
JACA should stay simpler:

- one canonical one-column layout
- no extension-driven UI growth
- no theme marketplace or panel ecosystem
- no extra zones beyond status bar, transcript, and prompt
