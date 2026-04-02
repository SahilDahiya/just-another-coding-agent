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
- validate and normalize model-produced narrative summary output
- derive deterministic survival state from actual persisted run events
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
- it prefers the latest persisted measured response usage when available and
  only estimates the unmeasured trailing history after that point
- it reserves explicit headroom for compaction-summary output before deriving
  the effective context window budget
- it adds a conservative reserve for the next prompt and wrapper overhead
- it compacts when that total crosses the configured fraction of the active
  effective model context window
- it counts only completed runs beyond the retained compaction boundary as
  genuinely new work, so a just-compacted session with one kept raw tail run
  does not immediately compact again
- shipped default and picker-visible model ids are required to carry explicit
  context-window metadata so this trigger cannot silently degrade when the
  model surface changes
- after three consecutive automatic compaction failures, the runtime blocks
  further automatic compaction attempts for that session and fails hard until
  the user reduces context pressure or starts a new session
- after a second-or-later durable automatic compaction, the runtime emits one
  explicit warning event before `run_started` so clients can surface the real
  continuity risk instead of treating repeated compactions as silent
  maintenance
- every automatic compaction decision now has a typed
  `CompactionBudgetReport`, and the runtime emits that report on
  `session_compaction_started` plus `budget_before` / `budget_after` on
  `session_compaction_completed`

The compaction source is intentionally not a raw transcript dump. It uses:

- the latest durable compaction summary, when present
- structured per-run summaries for runs since the latest compaction boundary
- bounded prompt/output/activity text rather than raw event JSON or raw tool-return payloads

The durable summary now carries two kinds of state:

- model-written narrative state:
  - `current_objective`
  - `established_facts`
  - `user_preferences`
  - `important_paths`
  - `open_questions`
  - `unresolved_work`
- backend-owned deterministic survival state:
  - `read_paths`
  - `modified_paths`
  - `recent_shell_commands`
  - `recent_failures`

The deterministic fields are derived from actual persisted run events and
carried forward across compaction boundaries. They are not left to model
free-form recall.

The working-set portion of deterministic survival state includes:

- `read_paths` for files explicitly read since the latest compaction boundary
- `modified_paths` for files explicitly written or edited since the latest
  compaction boundary

The operational portion of deterministic survival state includes:

- `recent_shell_commands` for concise command/outcome snapshots from recent
  `shell` tool activity
- `recent_failures` for recent failed tool calls and terminal run failures

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
- `runtime/compaction/boundary.py`

Responsibilities:

- define the canonical post-compaction continuity boundary:
  - latest durable compaction checkpoint messages
  - native run deltas written after that checkpoint
- rebuild effective `message_history` from checkpoint messages plus later run deltas
- avoid semantic prefix surgery when persisting successful resumed runs by
  storing only PydanticAI `new_messages()` deltas

This is deterministic replay from authoritative checkpoint state, not prefix
matching or synthetic-summary regeneration.
The canonical session runtime now treats this local materialized history as the
authoritative source of truth for resumed runs instead of relying on
provider-side server history.

That same post-compaction continuity boundary is the seam future fork behavior
must use too. Resume should not own bespoke boundary math that a later fork path
would need to reimplement differently.

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

Durable session compaction uses a whole-run kept boundary:

- `summarized_through_run_id` marks the last run folded into the summary
- `first_kept_run_id`, when present, marks the first retained native run
- `checkpoint_through_run_id` marks the latest run already represented inside
  the persisted compaction checkpoint
- `checkpoint_messages` stores the authoritative model-facing history at the
  compaction boundary: the summary message plus any retained raw tail runs

Resume history is therefore:

- `checkpoint_messages`
- all native messages strictly after `checkpoint_through_run_id`

This is intentional. JACA does not currently support durable boundaries inside a
single persisted run.

Automatic durable compaction now preserves a bounded raw tail when possible:

- if there are at least two completed runs after the latest compaction boundary,
  auto-compaction summarizes through the second-to-last eligible run
- the latest eligible run is kept raw via `first_kept_run_id`
- if there is only one eligible completed run, summary-only compaction remains
  necessary because there is no earlier run boundary to summarize through

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
- `checkpoint_through_run_id` must reference an existing run and must not
  precede either the summary boundary or the kept boundary
- resumed runs must materialize effective history before the run starts
- successful resumed runs must persist only new run deltas, not checkpoint
  history copied back out of the replayed prefix
- live in-run compaction must restore original raw tool-return content before
  persistence

## Why This Split Exists

These systems share a theme, not a lifecycle:

- session-summary compaction is async model-driven summarization
- resume-history materialization is deterministic durable replay
- in-run compaction is pure live-run history shaping

Keeping them separate makes long-run bugs easier to isolate and reduces the risk
of breaking resume behavior while changing live-run context management.
