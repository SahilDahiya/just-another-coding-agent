# Compaction

read_when: you are changing durable session compaction, resume behavior, or long-task memory handling

## Goal

Keep long-running task experience stable by treating durable compaction and
resume replay as explicit systems with clear ownership and invariants.

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
- durable compaction completion also emits explicit usefulness metrics:
  - `estimated_tokens_saved`
  - `estimated_percent_saved`
  - `estimated_headroom_gain_tokens`
- the durable budget report now breaks out:
  - `estimated_resume_message_tokens`
  - `estimated_checkpoint_tokens`
  - `estimated_summary_tokens`
  - `estimated_post_compaction_headroom_tokens`
  so compaction can be evaluated by headroom created, not only by whether it
  triggered

The compaction source is intentionally not a raw transcript dump. It uses:

- the latest durable compaction summary, when present
- structured per-run summaries for runs since the latest compaction boundary
- bounded prompt/output/activity text rather than raw event JSON or raw tool-return payloads

The durable summary now carries two kinds of state:

- model-written narrative state:
  - `current_objective`
  - `current_plan`
  - `established_facts`
  - `completed_work`
  - `key_decisions`
  - `user_preferences`
  - `important_paths`
  - `open_questions`
  - `unresolved_work`
- backend-owned deterministic survival state:
  - `read_paths`
  - `modified_paths`
  - `recent_shell_commands`
  - `recent_verifications`
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
- `recent_verifications` for recent test/lint-like verification commands and
  their outcomes when the backend can identify them deterministically
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
  - latest durable compaction summary rendered as ephemeral resume instructions
  - native run deltas written after that checkpoint
- rebuild effective `message_history` from checkpoint messages plus later run deltas
- rebuild per-run resume instructions from the durable compaction summary
- avoid semantic prefix surgery when persisting successful resumed runs by
  storing only PydanticAI `new_messages()` deltas
- strip internal instruction state from replayed and persisted `ModelMessage`
  history so system-role policy never becomes durable conversation content

This is deterministic replay from authoritative checkpoint state, not prefix
matching or synthetic-summary regeneration.
The canonical session runtime now treats this local materialized history as the
authoritative source of truth for resumed runs instead of relying on
provider-side server history.

That same post-compaction continuity boundary is the seam future fork behavior
must use too. Resume should not own bespoke boundary math that a later fork path
would need to reimplement differently.

## Durable Boundary Model

Durable session compaction now uses an authoritative checkpoint boundary with a
token-budgeted raw tail:

- `summarized_through_run_id` marks the last run folded into the summary
- `first_kept_run_id`, when present, marks the first run contributing raw
  checkpoint tail messages
- `checkpoint_through_run_id` marks the latest run already represented inside
  the persisted compaction checkpoint
- `checkpoint_messages` stores only the authoritative raw checkpoint tail at
  the compaction boundary
- `summary` remains the durable structured continuity record and is rendered
  into ephemeral resume instructions at run time rather than persisted as a
  system-role message

Resume history is therefore:

- `checkpoint_messages`
- all native messages strictly after `checkpoint_through_run_id`

Resume instructions are therefore:

- canonical backend instructions rebuilt for the current run
- plus one ephemeral continuity note rendered from the latest compaction
  `summary`

Automatic durable compaction now preserves a bounded raw tail by message-token
budget when possible:

- it selects the largest safe raw suffix that fits the retained-tail budget
- that suffix may span multiple recent runs
- when necessary, it may start inside a run at a message boundary that preserves
  valid user/assistant/tool structure
- if the kept suffix starts inside a summarized run,
  `first_kept_run_id == summarized_through_run_id`
- if no safe raw suffix fits the retained-tail budget, summary-only compaction
  remains necessary

## Oversized Single Runs

If one retained run is still very large, durable compaction now attempts to keep
a safe suffix from inside that run rather than only whole-run tails.

So today:

- between-run compaction can keep a message-token-budgeted raw tail, including a
  safe suffix from inside one run when needed
- live runs do not rewrite history mid-flight; provider-side context or quota
  failures now fail hard

One important consequence:

- the retained checkpoint tail is replayed in raw form at resume time
- the durable compaction summary is not replayed as conversation history; it is
  injected as internal per-run instructions instead
- there is no run-local history rewriting layer between tool execution and the
  next model request

## Invariants

- `session_compaction` is append-only durable session state
- a compaction entry must reference existing run IDs
- `first_kept_run_id`, when present, must not precede
  `summarized_through_run_id`
- `checkpoint_through_run_id` must reference an existing run and must not
  precede either the summary boundary or the kept boundary
- resumed runs must materialize effective history before the run starts
- resumed runs must rebuild continuity instructions from the durable summary
  instead of replaying summary text as a persisted system prompt
- successful resumed runs must persist only new run deltas, not checkpoint
  history copied back out of the replayed prefix
- persisted `session_messages` must not contain internal instructions or
  `SystemPromptPart` content

## Why This Split Exists

These systems share a theme, not a lifecycle:

- session-summary compaction is async model-driven summarization
- resume-history materialization is deterministic durable replay

Keeping them separate makes long-run bugs easier to isolate and reduces the risk
of breaking resume behavior while changing durable compaction policy.
