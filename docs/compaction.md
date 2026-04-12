# Compaction

read_when: you are changing durable session compaction, resume behavior, or long-task memory handling

## Goal

Keep long-running sessions resumable without relying on provider-side history
or backend-owned hidden summary state.

## Canonical Shape

Durable compaction now stores one replacement-history artifact:

- `compaction_id`
- `compacted_through_run_id`
- `replacement_messages`

`replacement_messages` is the exact model-visible compacted prefix that future
resumed runs replay.

There is no durable structured `SessionCompactionSummary`.
There is no durable checkpoint-tail boundary model.
There are no hidden resume instructions rebuilt from compaction state.

## Replacement Messages

`replacement_messages` is built as:

- recent real user-message tail
- capped by `SESSION_AUTO_COMPACTION_RETAINED_TAIL_TOKENS`
- plus one assistant-style plain-text compaction summary message appended last

Text-only PydanticAI user prompt sequences are normalized into newline-joined
text before they are retained. Non-text user content is not part of the current
compaction contract and fails hard instead of being silently dropped.

The retained user tail is selected by the backend-owned token-estimation seam in
`runtime/token_estimation.py`.

Current estimator:

- method: `chars_per_token_v1`
- used for:
  - replacement-history tail selection
  - automatic compaction budgeting

The summary message is persisted as a normal `ModelResponse(TextPart(...))`
with a fixed continuation-summary header so the backend can detect and validate
it later.

Important consequences:

- overlap is allowed: the summary may describe the same recent user messages
  that are kept raw
- replacement history is model-visible durable context, not hidden runtime-only
  state
- the compacted artifact owns conversation continuity only; it does not carry
  backend deterministic working-set state

## Automatic Trigger

Automatic durable compaction is a pre-run maintenance step.

Code:

- `runtime/compaction/session_summary.py`
- `runtime/compaction/trigger.py`
- `runtime/compaction/source_builder.py`
- `runtime/compaction/boundary.py`
- `runtime/token_estimation.py`
- `session/replacement_history.py`

The trigger is backend-owned and estimates the exact local substrate that the
next resumed run will use:

- rebuilt resume `message_history`
- plus explicit prompt reserve

It does not use prior provider-reported usage as the decision input.

The current report is `CompactionBudgetReport` and includes:

- `should_compact`
- `reason`
- `context_window_tokens`
- `effective_context_window_tokens`
- `output_headroom_tokens`
- `trigger_budget_tokens`
- `prompt_reserve_tokens`
- `estimation_method`
- `estimated_resume_message_tokens`
- `estimated_replacement_messages_tokens`
- `estimated_replacement_summary_tokens`
- `estimated_pre_run_tokens`
- `estimated_post_compaction_headroom_tokens`
- `runs_since_latest_compaction`

Current trigger reasons:

- `no_runs`
- `unknown_context_window`
- `no_new_work`
- `within_budget`
- `over_budget`

Operational rules:

- automatic compaction runs only before a resumed run starts
- it requires at least one completed run after `compacted_through_run_id`
- summary generation uses the canonical model resolution/settings seam and the
  streaming request path
- after three consecutive automatic compaction failures, the runtime blocks
  further automatic attempts for that session and fails hard
- for local or Harbor debugging, the in-run threshold can be overridden at
  process start with
  `JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION=<float in (0,1]>`
  instead of editing backend constants

## Compaction Source

The model-generated summary is based on a bounded source, not a raw transcript
dump.

The source contains:

- the latest previous compaction summary text, when present
- concise renderings of runs since the latest compaction boundary
- bounded prompt, outcome, and tool-activity text

If the source is too large:

- oldest run sections are trimmed first
- if even the minimal source cannot fit, compaction fails hard before the model
  call

The summary output is plain text and should stay continuation-oriented rather
than archival.

Current prompt contract:

- supported section headings:
  - `Primary Intent:`
  - `Completed Work:`
  - `Important Files/Paths:`
  - `Failures / Open Issues:`
  - `Current State:`
  - `Next Step:`
  - `Stable Preferences:`
- sections must be omitted when there is no concrete evidence
- files/paths should be listed only when they are explicitly visible in the
  current source
- no code snippets
- no exhaustive user-message dump
- no repeated facts across sections
- watch for bloat and rot by aggressively omitting stale, repetitive, and
  low-signal detail

Normalization guards:

- empty summaries fail hard
- otherwise normalization only strips blank lines and preserves the returned
  continuation note as-is

## Resume Semantics

Resume history is deterministic and local.

If there is no compaction entry:

- replay all persisted run `messages`

If there is a compaction entry:

- replay `replacement_messages`
- then append all persisted run `messages` strictly after
  `compacted_through_run_id`

There is no separate hidden resume-instructions path.

Successful resumed runs persist only their new message delta. They do not copy
`replacement_messages` back into the new trailing run.

## Invariants

- `session_compaction` is append-only durable session state
- `compacted_through_run_id` must reference an existing completed run
- `replacement_messages` must be non-empty
- `replacement_messages` must end with exactly one compaction summary message
- no earlier entry inside `replacement_messages` may also be a compaction
  summary message
- persisted `session_messages` must not contain `SystemPromptPart` content or
  hidden runtime instructions

## Non-Goals

This refactor intentionally removed these from compaction:

- structured summary contracts
- checkpoint-tail boundary bookkeeping
- backend-owned deterministic working-set carry-forward
- hidden summary-to-instructions regeneration

If we later add backend-owned runtime framing state, it should be a separate
system from compaction.
