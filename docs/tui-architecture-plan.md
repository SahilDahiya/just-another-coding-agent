# TUI Architecture Plan

read_when: you are planning or implementing a Go TUI refactor, evaluating TUI architectural health, or deciding how to support richer interaction without moving semantics into Go

## Why This Exists

The current JACA TUI has the right product boundary but the wrong internal
shape for continued feature growth.

What is already right:

- Python owns backend semantics and public contract meaning.
- Go owns presentation, keyboard handling, layout, and RPC client behavior.
- The TUI is still a shell over one canonical backend rather than a second
  product.

What is currently wrong:

- too much feature logic is concentrated in `internal/jaca/app/model.go`
- async orchestration, key handling, reducer logic, and focused interaction
  flows are too interleaved
- presentation domains are not isolated enough to evolve independently
- rich interaction features keep landing as more branches inside one large
  state machine

The goal of this plan is not to make Go own more semantics. The goal is to make
the TUI easier to extend while keeping Python as the only semantic authority.

## Architectural Position

Do not use classic enterprise DDD here.

JACA does not need aggregates, repositories, or domain entities in the Go TUI.
That would add vocabulary without solving the real problem.

The right fit is:

- explicit UI intents
- focused interactive flows
- one thin controller layer for backend calls
- reducer-style state transitions
- feature-sliced presentation structure as a later extraction, not the first
  move

In short:

- Python owns semantics.
- Go owns interaction and presentation.
- Go should move away from one giant app model, but the first seam is not file
  slicing. The first seam is:
  - intent vs backend call
  - passive reducer vs active flow
  - shell routing vs feature behavior

## Chosen Mechanics

This plan intentionally chooses one migration strategy instead of leaving
multiple architectural options open.

### Model Receiver Migration

Do not move to Bubble Tea sub-models.

The migration strategy is:

- keep one top-level `model`
- embed slice-local state structs inside `model`
- move behavior out of `func (m *model)` methods into focused reducer and view
  functions that operate on those embedded slice states

This is the least disruptive path because it:

- preserves Bubble Tea's single-model architecture
- avoids a rewrite of app-wide focus and layout routing
- lets existing `*model` methods shrink incrementally instead of forcing a
  one-shot ownership flip

So the target is not:

- many independent Bubble Tea sub-models

The target is:

- one top-level Bubble Tea model
- several embedded slice states
- pure-ish reducer functions operating on those slice states

### Focused Interactive Flows

Reducers are not enough for the hardest TUI features.

The TUI also needs a first-class concept for focused interaction flows that:

- temporarily own input focus
- capture keys until resolved
- render their own focused UI
- emit an intent/result when they complete

JACA already has this pattern informally in:

- `internal/jaca/app/auth.go`
- `internal/jaca/app/onboarding.go`

Those files are already flow state machines hidden behind `Active` booleans and
`*model` methods.

So the architecture should explicitly support two kinds of feature logic:

- reducers
  - passive event or response -> state update
- flows
  - active focused interaction -> completion intent

The preferred shape is:

```go
type SliceFlow interface {
    HandleKey(tea.KeyMsg) (done bool, intent tea.Msg, cmd tea.Cmd)
    View() string
}
```

And then slices can hold optional focused flow state:

```go
type composerState struct {
    Draft      string
    SlashMenu  slashMenuState
    ActiveFlow SliceFlow
}
```

The shell routing rule becomes:

- if a slice has an active flow, route keys to that flow first
- when the flow completes, emit its intent/result
- otherwise use normal key handling

This is the right shared shape for:

- auth setup
- onboarding
- tool approval
- diff review
- interactive search
- resource pickers

### Intent Dispatch

Use `tea.Msg` for intents.

Do not introduce a custom Go event bus for JACA.

Bubble Tea already gives us the right primitives:

- `tea.Msg` for intent and async result flow
- `tea.Cmd` for controller-side async work

So the internal path should become:

- key handling emits intent `tea.Msg`s
- the app shell dispatches those intents
- the session controller turns intents into backend calls
- backend responses come back as `tea.Msg`s

This keeps the TUI aligned with Bubble Tea rather than inventing a second Go
event system.

## Comparison With Codex

Codex is useful as a contrast, not as a direct template.

What Codex does differently:

- the TUI and core live in one Rust workspace
- TUI actions flow through an internal app event bus
- core owns active-turn state directly
- pending input / steering state lives close to execution state

Relevant local references:

- `/home/dahiy/repos/codex/codex-rs/tui/src/app_event.rs`
- `/home/dahiy/repos/codex/codex-rs/tui/src/app_event_sender.rs`
- `/home/dahiy/repos/codex/codex-rs/core/src/codex.rs`
- `/home/dahiy/repos/codex/codex-rs/core/src/state/turn.rs`

What we should learn from Codex:

- rich interaction features want explicit intent types
- pending input wants one authoritative owner
- the top-level app loop should coordinate through explicit messages, not
  arbitrary widget reach-through

What we should not copy:

- collapsing the Go TUI and backend into one runtime
- moving backend semantics into the frontend
- replacing our Python-owned contract with local TUI inference
- introducing a Codex-sized custom app event bus when Bubble Tea already gives
  us `tea.Msg` and `tea.Cmd`

## Problem Statement

The TUI needs to feel authoritative and effortless while preserving the strong
Python/Go boundary.

That requires two things at once:

1. richer backend contracts
2. healthier internal Go structure

This document is about the second one.

## Current Health Assessment

### Healthy

- semantic ownership is still mostly correct
- recent queue work moved truth back into backend events instead of local Go
  inference
- transcript rendering and prompt interaction are visually coherent
- the Bubble Tea shell is still small enough to refactor without a rewrite

### Unhealthy

- `internal/jaca/app/model.go` is still acting as:
  - key router
  - async controller
  - reducer hub
  - state container
  - feature coordinator
- `auth.go` and `onboarding.go` are already focused flows, but they do not use
  any shared flow shape
- feature work often requires touching:
  - `model.go`
  - `render.go`
  - `transcript.go`
  - `rpc/client.go`
- intent handling is implicit in key branches instead of explicit command types
- backend events are applied directly in ad hoc branches rather than through
  focused reducers
- `render.go` is still a large central renderer instead of a thin compositor
  over slice-owned view functions
- `model_test.go` is very large, which makes state ownership changes expensive
  unless we start introducing slice-local tests

## Target Shape

The target is a shell plus explicit flows, intents, controller, and gradually
cleaner slices.

### 1. App Shell

Owns:

- Bubble Tea lifecycle
- top-level message routing
- layout sizing
- focus and global overlays

Does not own:

- feature semantics
- transcript meaning
- queue meaning
- backend call details

### 2. Composer Slice

Owns:

- local text editing state
- slash suggestion state
- prompt history and draft mechanics
- key interpretation for the prompt area

Emits intents such as:

- `SubmitPrompt`
- `QueueNext`
- `QueueLater`
- `InterruptRequested`
- `OpenSlashSelection`

Does not call the backend directly.

May own focused flows such as:

- interactive search
- slash-command selection
- picker-style prompt insertion

### 3. Conversation Slice

Owns:

- transcript state
- live assistant text presentation
- tool row presentation
- queue preview presentation
- status-note presentation tied to the conversation surface

This slice is also the Go presentation owner for backend queue-state events.
That means:

- backend `session_queue_state` lands here
- backend `session_queued_prompt_batch_submitted` lands here
- the current queue preview state should migrate here

It should not own queue semantics. It should only own queue presentation.

Consumes backend events and view props.

Does not invent backend meaning.

May own focused flows such as:

- tool approval
- inline diff review
- MCP resource selection

### 4. Session Slice

Owns presentation state for:

- run phase
- loading / compacting / interrupted / completed state
- usage/footer state
- current session id/name/preview state

This is the TUI-side operational shell around the active session.

### 5. Auth / Settings Slice

Owns presentation for:

- provider readiness status
- auth overlays
- model catalog overlays
- onboarding-related chooser state

### 6. Session Controller

Owns:

- backend RPC calls
- async command execution
- translation from UI intents into backend requests
- translation from backend responses into app messages

This is the only place in Go that should know how to:

- `StreamRun`
- `EnqueueRun`
- `InterruptRun`
- request auth status
- request session preview
- request model catalog

This controller should be introduced before aggressive slice extraction. It is
the first important architectural seam.

### 7. Reducers

Each slice should expose focused reducer functions for:

- backend events
- backend responses
- local UI intents

This gives us explicit passive state transitions without turning every feature
into a branch inside `model.Update`.

### 8. Flows

Flows are focused, input-capturing interaction state machines.

They are distinct from reducers:

- reducers handle passive updates
- flows handle temporary focus ownership and multi-step interaction

The shell should treat flows as first-class participants in routing.

## Rendering Strategy

`render.go` should move toward composition, not keep growing as one large
renderer.

The target is:

- top-level `renderView()` remains the shell compositor
- each slice owns its own render helper
- `renderView()` delegates to slice renderers using already-assembled view
  state

That means:

- composer rendering should move behind a composer view function
- conversation rendering should move behind a conversation view function
- auth/settings overlays should move behind slice-local view helpers

`render.go` should end up mostly as:

- page layout
- shell composition
- cross-slice spacing and ordering

Not:

- all detailed row rendering logic for every slice

This plan assumes the current mostly single-column shell. If JACA later adds
true panels or resizable side-by-side layouts, the shell compositor will need a
real layout model. That is a later architectural layer, not a blocker for this
refactor.

## Architectural Rule

The Go TUI should be organized around:

- intents
- flows
- reducers
- slice state
- rendering components

Not around:

- giant mutable app models
- frontend-owned semantic inference
- helper methods that combine key handling, backend calls, and rendering state
  in one place

## Proposed Module Layout

Target direction, not a mandatory one-shot rename:

- `internal/jaca/app/app_shell.go`
- `internal/jaca/app/session_controller.go`
- `internal/jaca/app/intents.go`
- `internal/jaca/app/flows.go`
- `internal/jaca/app/view_state.go`
- `internal/jaca/app/composer/`
  - `state.go`
  - `reducer.go`
  - `view.go`
- `internal/jaca/app/conversation/`
  - `state.go`
  - `reducer.go`
  - `view.go`
- `internal/jaca/app/session/`
  - `state.go`
  - `reducer.go`
- `internal/jaca/app/authui/`
  - `state.go`
  - `reducer.go`
  - `view.go`

This does not require a public package split first. Internal files can move in
stages while package name remains `app`.

## Queue Ownership

Queue semantics remain backend-owned in Python.

In Go:

- queue preview rendering belongs to the conversation slice
- queue-related run phase and command dispatch belong to the session controller
- composer only emits queue intents

So the split is:

- backend: queue truth
- composer: queue intent creation
- session controller: queue command execution
- conversation slice: queue state presentation

## Refactor Principles

- Keep Python as the only owner of semantics.
- Prefer adding backend event fields over local Go inference.
- Introduce intent, flow, and controller seams before aggressive slice
  extraction.
- Move behavior by slice, not by utility extraction alone.
- Extract reducers before introducing extra interfaces.
- Keep state changes explicit and testable.
- Do not rewrite the TUI in one pass.

## Rollout Plan

### Slice 1: Introduce Explicit Intents

Add Go-side intent types for prompt actions and shell actions.

Examples:

- `SubmitPrompt`
- `QueueNext`
- `QueueLater`
- `InterruptRun`
- `OpenAuth`
- `SelectSuggestion`

Goal:

- stop letting raw key branches act as implicit business logic

Concrete first PR:

- add `intents.go`
- define the first 5-6 intent `tea.Msg` types for:
  - submit prompt
  - queue next
  - queue later
  - interrupt run
  - open auth
  - select suggestion
- convert `handleQueueFollowUp` and `handleQueueSteer` to emit those intents
  instead of calling the backend directly
- keep behavior unchanged

This first PR should stay small and only prove the pattern.

### Slice 2: Introduce Focused Flows

Formalize the existing ad hoc `Active bool` interaction patterns.

Goal:

- stop encoding focused interactions as one-off booleans on `*model`
- give auth/onboarding and future approval/review/picker UX one shared shape

Concrete first flow moves:

- define a small `SliceFlow` interface in `flows.go`
- adapt auth and onboarding to fit that pattern without changing behavior
- have the shell route keys to active flow before normal key handling

### Slice 3: Extract Session Controller

Move backend RPC command creation and async orchestration out of the main app
model.

Goal:

- one place in Go owns backend calls
- `model.go` becomes a router, not the transport brain

Mechanical rule:

- key handlers and slash handlers stop calling backend helpers directly
- they emit intent messages
- the app shell hands those intents to the controller
- the controller returns `tea.Cmd`

### Slice 4: Extract Conversation Reducer

Move transcript-related event application out of `model.go`.

Goal:

- transcript and queue preview behavior are updated through a focused reducer
- easier to test streaming / queue / tool rendering transitions

### Slice 5: Extract Composer Slice

Move prompt editing, history, slash suggestions, and queue-submit key behavior
into a dedicated slice.

Goal:

- prompt interactions evolve without touching unrelated run/session code

### Slice 6: Extract Session Slice

Move phase, usage, and lifecycle presentation into a focused slice.

Goal:

- run lifecycle changes stop scattering across the app model

### Slice 7: Auth / Settings Slice

Move onboarding, provider readiness presentation, and auth overlay behavior
into focused state and reducers.

Goal:

- stop mixing first-run/auth UI with prompt/run routing

## Testing Strategy

Refactor slices should increase TUI test quality, not just move files around.

Migration rule:

- existing model-level tests stay in place initially
- new slice behavior should add slice-local reducer, flow, or controller tests
- old model-level tests should be deleted only when a slice has enough local
  coverage to make the old test redundant

So reducer tests supplement current `model_test.go` coverage first. They do not
replace it immediately.

Add or prefer tests at these levels:

- reducer tests
  - backend event -> state transition
  - UI intent -> state transition
- flow tests
  - focused key handling -> completion intent
  - active flow ownership and release
- controller tests
  - intent -> backend request
  - backend response -> app message
- rendering tests
  - view state -> rendered output
- focused integration tests
  - queue preview
  - interrupt behavior
  - prompt submission and phase transitions

Avoid relying only on giant top-level model tests for every behavior.

`model_test.go` is expected to shrink gradually as behavior moves into slices.
That shrink should happen through natural replacement, not a one-shot rewrite.

## Success Criteria

The refactor is successful when:

- `model.go` is primarily app-shell routing, not feature implementation
- focused interactions are implemented as explicit flows, not boolean tangles
- backend calls are centralized in one controller layer
- transcript and prompt behavior are updated through slice reducers
- new TUI features can be added by touching one slice plus the backend contract,
  not the entire app shell
- no semantic ownership moves from Python into Go

## Non-Goals

- replacing Bubble Tea
- collapsing Go and Python into one runtime
- introducing a second semantic contract inside Go
- using DDD vocabulary without a concrete payoff
- rewriting the TUI from scratch
