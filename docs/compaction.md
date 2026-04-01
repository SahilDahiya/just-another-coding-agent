# Compaction

read_when: you are changing session resume behavior, live-run history shaping, or long-task memory handling

## Goal

Keep long-running task experience stable by treating compaction as three
separate systems with explicit ownership and invariants.

## Systems

### 1. Session-summary compaction

Between completed runs, the runtime may ask a model to summarize durable session
state into a `SessionCompactionSummary`.

Code:

- `runtime/compaction/session_summary.py`
- `runtime/compaction/constants.py`
- `runtime/compaction/boundary.py`
- `runtime/compaction/trigger.py`
- `runtime/compaction/source_builder.py`
- `runtime/compaction/working_set.py`

Responsibilities:

- decide when automatic durable compaction should happen
- build a bounded structured compaction source from prior runs and any previous compaction
- validate and normalize model-produced summary output
- append one durable `session_compaction` entry to the session file

The public orchestration entrypoint stays in `session_summary.py`, but the
constants, compaction boundary helpers, trigger policy, bounded source
building, and deterministic working-set path carry-forward are now split into
focused helper modules so durable compaction is not owned by one oversized
file.

This is cross-run state management. It is not a live-run `history_processor`.

The current automatic trigger is pre-run and token-budget-aware:

- it estimates tokens for the exact local resume history built from durable
  session state
- it adds a conservative reserve for the next prompt and wrapper overhead
- it compacts when that total crosses the configured fraction of the active
  model context window
- it requires at least one completed run after the latest compaction boundary
  so a just-compacted session does not immediately compact again
- shipped default and picker-visible model ids are required to carry explicit
  context-window metadata so this trigger cannot silently degrade when the
  model surface changes

The compaction source is intentionally not a raw transcript dump. It uses:

- the latest durable compaction summary, when present
- structured per-run summaries for runs since the latest compaction boundary
- bounded prompt/output/activity text rather than raw event JSON or raw tool-return payloads

The durable summary now also carries explicit structured working-set path state:

- `read_paths` for files explicitly read since the latest compaction boundary
- `modified_paths` for files explicitly written or edited since the latest
  compaction boundary

Those path lists are runtime-derived from actual tool activity and carried
forward across compaction boundaries. They are not left to model free-form
recall.

If the source would exceed the active model context window, oldest run sections
are trimmed before the summarizer model call starts. If even the minimal source
cannot fit, compaction fails explicitly before the model call instead of relying
on a provider-side `prompt too long` error.

Model-produced summaries are normalized after the summary call returns. If the
normalized result is empty, compaction fails explicitly instead of retrying
inside the model loop.

### 2. Resume-history materialization

Before a resumed run starts, the runtime now builds the effective
`message_history` explicitly from durable session state.

Code:

- `runtime/compaction/resume.py`

Responsibilities:

- turn the latest `session_compaction` entry into one synthetic summary message
- append retained native messages after the compaction boundary
- strip synthetic summary messages back out before persistence

This is deterministic replay, not prefix matching or message-history surgery.
The canonical session runtime now treats this local materialized history as the
authoritative source of truth for resumed runs instead of relying on
provider-side server history.

### 3. In-run compaction

During one live run, historical tool-return content may be compressed when
context pressure grows.

Code:

- `runtime/compaction/in_run.py`

Responsibilities:

- summarize oversized historical tool returns for the model
- keep tool call / tool result pairing intact
- restore original raw tool-return content before session persistence

This is the only compaction path that still uses a PydanticAI
`history_processor`.

## Durable Boundary Model

Durable session compaction currently uses a whole-run kept boundary:

- `summarized_through_run_id` marks the last run folded into the summary
- `first_kept_run_id`, when present, marks the first retained native run

Resume history is therefore:

- one synthetic compaction-summary message
- all native messages from `first_kept_run_id` onward when present
- otherwise all native messages strictly after `summarized_through_run_id`

This is intentional. JACA does not currently support durable boundaries inside a
single persisted run.

## Oversized Single Runs

If one retained run is still very large, durable compaction does not split it.
That large run is replayed whole on resume, and live in-run compaction remains
responsible for reducing context pressure during the new run.

So today:

- between-run compaction is whole-run only
- live in-run compaction handles oversized historical tool output inside the new
  run

One important consequence:

- a retained run is replayed in raw form at resume time
- later model requests in that same resumed run may still compact those retained
  historical tool returns through the live in-run processor once context
  pressure grows

If the product later needs durable boundaries inside one run, the next step is
stable persisted message IDs, not inferred message indexes.

## Invariants

- `session_compaction` is append-only durable session state
- a compaction entry must reference existing run IDs
- `first_kept_run_id`, when present, must come strictly after
  `summarized_through_run_id`
- resumed runs must materialize effective history before the run starts
- synthetic compaction-summary messages must never be persisted back into
  `session_messages`
- live in-run compaction must restore original raw tool-return content before
  persistence

## Why This Split Exists

These systems share a theme, not a lifecycle:

- session-summary compaction is async model-driven summarization
- resume-history materialization is deterministic durable replay
- in-run compaction is pure live-run history shaping

Keeping them separate makes long-run bugs easier to isolate and reduces the risk
of breaking resume behavior while changing live-run context management.
