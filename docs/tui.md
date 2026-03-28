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
- Maintain a single global terminal background shade.
- Build structure with borders, spacing, typography, and color hierarchy, not stacked background fills.
- Hide in-app scrollbar chrome when it adds noise, but do not remove transcript scrolling itself.
- The terminal emulator's own scroll affordances remain valid; JACA should not fight them.

## Product Bar

- One-column interaction model
- Fast startup and reliable input handling
- Strong transcript readability for user, assistant, tool, warning, and error states
- Transcript turns should read like conversation, not like a transport log.
- Speaker and note labels should be sparse, calm, and earned by ambiguity.
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
- Default motion surfaces are the status bar and prompt marker, not new widgets or animated backgrounds.
- Startup should reveal the existing three zones in sequence rather than popping the whole shell in at once.
- Completion and interruption may use brief settle states before returning to idle, but the transcript remains the durable record.

## Default Interaction Model

- The status bar answers "where am I and what state is this session in?"
- The transcript is the single durable surface for all work: prompts, streaming assistant output, tool activity, warnings, and errors.
- Tool activity should collapse into terse, useful rows instead of printing repetitive lifecycle noise.
- Tool rows should prefer one row per action with a short preview and outcome, not anonymous repeated tool labels.
- Tool rows should treat backend `activity.title` and `activity.summary` as the authoritative label/summary when those fields are present.
- Finished tool rows may show backend `activity.duration_ms` when it adds timing context without crowding the transcript.
- Tool rows should read left-to-right as action first, then status/timing in the tail.
- Successful `edit` activity should expand into structured `Update(path)` blocks with typed diff previews rather than dumping raw unified diff text.
- Consecutive tool calls should group into one live activity block and update in place until assistant synthesis resumes.
- Exploratory misses that are clearly resolved later in the same turn should be muted or downgraded instead of rendered with the same red emphasis as unresolved failures.
- Completed assistant turns should settle into readable prose/Markdown instead of remaining raw streamed text.
- The prompt is the single input surface for chat and slash commands.
- The prompt zone should behave like a compact two-line shell composer: one input line, one low-salience footer line for state and recall hints.
- Historical user turns should still read like prompt echoes in the transcript, not like assistant prose.
- Composer ergonomics should favor shell-like recall over editor-like complexity.
- Persistent helper chrome should be minimal; slash-command discoverability must not dominate the idle shell.
- Prompt history and draft recovery belong in the prompt zone; command palettes and secondary controls do not.
- Completed assistant lists should read as indented terminal notes, not decorative article bullets.

## North Star

Use pi as the reference for craft, terminal discipline, and restraint.
JACA should stay simpler:

- one canonical one-column layout
- no extension-driven UI growth
- no theme marketplace or panel ecosystem
- no extra zones beyond status bar, transcript, and prompt
