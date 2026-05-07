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

The core architectural risk is semantic drift between the Go shell and the Python backend. The TUI must remain a presentation layer over the canonical backend contract. If the shell wants to show richer tool, session, or recovery meaning, that meaning should come from explicit backend fields rather than frontend invention.

## Hard Constraints

- The TUI has exactly three interaction zones: status bar, transcript, and prompt.
- No fourth zone will be added.
- No sidebars, drawers, file browsers, split panes, inspector panels, or terminal-IDE surfaces.
- If a capability cannot be expressed through the transcript or a slash command, it does not belong in the canonical TUI.
- Maintain a single global terminal background shade.
- Build structure with borders, spacing, typography, and color hierarchy, not stacked background fills.
- Hide in-app scrollbar chrome when it adds noise, but do not remove transcript scrolling itself.
- The terminal emulator's own scroll affordances remain valid; JACA should not fight them.
- Go owns shell craft, layout, and interaction polish. Python owns agent semantics, tool semantics, event semantics, session semantics, and public contract meaning.

## Product Bar

- One-column interaction model
- Fast startup and reliable input handling
- Strong transcript readability for user, assistant, tool, warning, and error states
- Transcript turns should read like conversation, not like a transport log.
- Speaker and note labels should be sparse, calm, and earned by ambiguity.
- Motion only where it clarifies state transitions
- Deliberate visual hierarchy instead of terminal clutter

## Refactor Goals

- TUI refactors should optimize for clearer module boundaries, easier testing, and stronger presentation discipline before they optimize for lower line count.
- Treat total LOC as a guardrail, not as a success metric; do not compress code at the expense of clarity or churn the same subsystem twice just to hit a number.
- Sequence transcript refactors deliberately: extract focused modules first, then introduce new interfaces only if the extracted shapes still clearly want them.
- Backend-dependent UI ideas such as exploration grouping or token/context accounting should start as backend contract issues, then land in Go as rendering work.

## Anti-Goals

- Rebuilding an IDE in the terminal
- Adding panels just because Textual makes them possible
- Decorative animation without state meaning
- Terminal-specific hacks without tests and explicit deletion criteria
- Feature growth that weakens the canonical interaction model
- Frontend-only reinvention of backend meaning because the current stream shape feels inconvenient

## Motion Budget

- Use motion only for stateful transitions such as startup, pending, streaming, completion, and interruption.
- Prefer short transitions in the 120-300ms range.
- Keep at most one animated region prominent at a time.
- No looping decorative motion outside explicit pending/loading feedback.
- Motion must improve comprehension, not just make the UI feel busy.
- Default motion surfaces are the status bar and prompt rail, not new widgets or animated backgrounds.
- Startup should reveal the existing three zones in sequence rather than popping the whole shell in at once.
- Completion and interruption may use brief settle states before returning to idle, but the transcript remains the durable record.
- Active runs may use a restrained top-rail liveness indicator such as `braille + elapsed` time; keep it calm, fixed-width, and outside the transcript body.

## Default Interaction Model

- The status bar answers "where am I and what state is this session in?"
- The transcript is the single durable surface for all work: prompts, streaming assistant output, tool activity, warnings, and errors.
- Tool activity should collapse into terse, useful rows instead of printing repetitive lifecycle noise.
- Tool rows should prefer one row per action with a short preview and outcome, not anonymous repeated tool labels.
- Tool rows should treat backend `activity.title` and `activity.summary` as the authoritative label/summary when those fields are present.
- Grouped exploration rows should treat backend `activity.display_label` as the authoritative short verb instead of mapping raw tool names locally.
- Finished tool rows may show backend `activity.duration_ms` when it adds timing context without crowding the transcript.
- Backend `activity.group_kind` may drive transcript grouping and calmer grouped
  presentation, but the grouping semantics still come from backend fields
  rather than TUI-side inference.
- Non-terminal operational misses returned through `tool_call_succeeded` should render as normal tool output, not the same red alarm treatment reserved for terminal `tool_call_failed` paths.
- Tool rows should read left-to-right as action first, then status/timing in the tail.
- Successful `edit` activity should expand into structured `Update(path)` blocks with typed diff previews rather than dumping raw unified diff text.
- Consecutive tool calls should group into one live activity block and update in place until assistant synthesis resumes.
- When the backend emits `tool_call_updated`, the grouped live tool block should show that partial progress in place instead of waiting for final success or failure.
- The transcript should use stable row units and reuse unchanged prefix content when only later rows change; do not rebuild the whole visible transcript from the top for every live update.
- Transcript memory should stay bounded by keeping heavy row bodies disciplined: cap tool/detail preview width, keep live tool output to bounded previews, and allow immutable assistant rows to drop row-local rendered caches once their content has been incorporated into the transcript buffer.
- The live transcript is a bounded visible surface, not a full session replay buffer: keep the current run plus only the most recent completed runs in terminal view, collapse older completed runs behind a small omission marker, and leave full durable continuity to the backend session history.
- Exploratory misses that are clearly resolved later in the same turn should be muted or downgraded instead of rendered with the same red emphasis as unresolved failures.
- Consecutive exploration-tagged read/search rows may settle into a grouped
  `Exploring` / `Explored` transcript block with coalesced file/search labels,
  while still preserving the underlying per-tool lifecycle as the source of
  truth. If an exploration burst contains an operational miss such as `read`
  not found, fall back to normal per-tool rows so the miss details remain
  visible instead of being hidden inside a grouped block.
- Completed assistant turns should settle into readable prose/Markdown instead of remaining raw streamed text.
- The prompt is the single input surface for chat and slash commands.
- Slash commands are never queued as `next` steering or `later` follow-ups.
  During an active run, all slash commands must fail explicitly with recovery
  guidance instead of executing locally or being sent to the backend as model
  input.
- `/onboard` should start a normal backend-owned run, not a separate local
  workflow. When the backend emits `onboarding_question_requested`, the shell
  should render the onboarding MCQ inline in the normal prompt rail rather than
  inventing a separate local workflow or a second semantic surface. The shell
  only renders the prompt, evidence paths, options, and selection state sent by
  Python; it must not generate or grade questions locally.
- `/model` suggestions should show the shipped model catalog even when a model
  is not currently usable, and label missing access explicitly instead of
  silently hiding those choices.
- `/model` suggestions should mark immediately runnable rows with a check and
  float those runnable rows ahead of unavailable ones so the default selection
  lands on something the user can actually run.
- `/model` should display public-style names such as `gpt-5.4 | api` and
  `gpt-5.4 | oauth` so the same model reads as one model with two access
  lanes, not as two different products with backend-only ids.
- `/name <text>` should stay thin and backend-owned: the shell forwards raw
  text, the backend persists the normalized session name, and `/session`
  renders the returned durable name plus the opaque session id.
- When the wrapper launches the TUI in resumed-session or forked-session mode,
  the shell should preseed the opaque session id plus the backend-resolved
  session name, optionally preseed the direct fork parent label, suppress
  first-run onboarding, and show one calm note plus a bounded backend-owned
  recent-history preview in the transcript instead of inventing a separate
  resume or fork UX inside Go.
- Startup should surface a calm first-run chooser panel when no provider has
  been selected yet or when no saved login lane is currently usable, and saved
  cloud-provider selections missing auth should only skip that chooser when
  some other login lane is already available.
- The first-run chooser should keep lane, setup type, and readiness in the
  same compact read so users do not have to stitch setup state
  together from the chooser, transcript, and slash help.
- First-run setup should also be actionable from the prompt zone itself:
  show a prompt-footer hint and let `Tab` on an empty prompt open provider
  suggestions directly.
- While a run is streaming, the composer stays editable. `Tab` with a non-blank
  composer queues that text as a backend-owned end-of-turn follow-up instead of
  interrupting the active run.
- While a run is streaming, `Enter` with a non-blank composer queues that text
  as backend-owned `next` steering for the active turn instead of interrupting
  the run. The backend attaches that steer only after the current tool phase
  completes, before the next model round-trip in the same run.
- While a run is streaming, `Esc` requests a backend-owned interrupt. If
  pending `next` steering exists, the backend promotes it into immediate
  follow-up delivery before draining the queue on the same `run.start` stream.
- Queued input should be visible by structure, not transcript chatter: show a
  compact grouped preview above the composer with one `After current tool phase`
  block and one `At end of turn` block, each with a queued count and a few
  stacked prompt previews so batching is obvious without explanatory prose.
- The Go TUI must treat that grouped preview as presentation only. Queue truth
  comes from backend-emitted `session_queue_state` events, not from local
  inference over `run_started`, `run_failed`, enqueue responses, or interrupt
  responses.
- When queued prompts are actually submitted, the transcript should render the
  queued user text before the assistant response. That transcript truth comes
  from backend-emitted `session_queued_prompt_batch_submitted`, not from local
  guesswork in the Go TUI.
- Masked auth should feel explicitly secure, not like ordinary chat input:
  provider-specific labeling, a centered secure setup panel, a masked input
  field, and clear copy that the secret is not written into transcript or
  prompt history.
- The TUI must not collect API keys directly. Hosted-provider setup should use
  the backend-owned auth-file contract: ensure `~/.jaca/auth.json` exists,
  show the raw absolute file path on its own line, and show the exact JSON
  snippet the user should paste for the selected provider. Do not rely on
  `file://` links or other terminal-specific open-file tricks as the primary
  path.
- OAuth login completion notes must come from the backend-owned login wait
  contract itself. The Go shell must not poll for completion or rely on later
  commands such as `/model` or prompt submission to reconcile login success.
- First-run setup should prioritize the three supported access lanes:
  ChatGPT subscription, OpenAI API key, and Anthropic API key. Removed
  providers should not survive in slash help, onboarding copy, or picker
  suggestions.
- The prompt zone should behave like a compact shell composer: a single
  input line for short drafts that grows vertically — up to a small cap —
  when the draft has explicit newlines or soft-wraps a long line, plus one
  low-salience footer line for state and recall hints. Alt+Enter and
  Ctrl+J insert a newline at the cursor. Bare Enter still submits. Up/Down navigate prompt history only
  when the cursor is at the top/bottom visual row of the current draft;
  otherwise they move the cursor within the composer. Pastes larger than
  the placeholder threshold are replaced in the composer by a compact
  `[pasted N chars]` token that expands back on submit so the input stays
  scannable.
- The prompt footer may carry low-salience operational guidance such as a
  copy hint, but that guidance should stay subdued and yield to width
  constraints instead of crowding the main state readout.
- Backend token and context-window usage should appear as restrained footer context after a completed run, not as a new panel or heavy stats surface.
- Session lifecycle events such as `session_compaction_started` and `session_compaction_completed` may appear before `run_started`; the TUI should surface them in the transcript and switch to the compacting state only when the backend says compaction is happening, instead of silently dropping or reinterpreting them.
- `esc` is the primary conversation-control key: it requests interrupt for an
  active run, promotes pending steer to immediate send when present, and does
  not resurrect the previous prompt for editing.
- single `ctrl+c` must remain copy-safe and non-destructive; if the shell receives it without an active selection, only an idle second `ctrl+c` may quit.
- Historical user turns should still read like prompt echoes in the transcript, not like assistant prose.
- Composer ergonomics should favor shell-like recall over editor-like complexity.
- Persistent helper chrome should be minimal; slash-command discoverability must not dominate the idle shell.
- Prompt history and draft recovery belong in the prompt zone; command palettes and secondary controls do not.
- Slash-command discoverability should render as a bounded inline completion menu anchored to the prompt, not as a detached modal or fourth-zone palette.
- Completed assistant lists should read as indented terminal notes, not decorative article bullets.
- The shell should preserve the same hierarchy across truecolor, 256-color, ANSI, and no-color terminals by using explicit palette choices, not generic hex degradation alone.
- When transcript rows need richer semantics, prefer explicit backend `activity` or event fields over heuristic frontend interpretation.

## North Star

Use pi as the reference for craft, terminal discipline, and restraint.
JACA should stay simpler:

- one canonical one-column layout
- no extension-driven UI growth
- no theme marketplace or panel ecosystem
- no extra zones beyond status bar, transcript, and prompt
