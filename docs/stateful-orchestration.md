# Stateful Orchestration

read_when: you are designing session behavior beyond plain persisted history

## Goal

Add thin stateful orchestration on top of the existing session-aware runtime
without importing pi's full architecture.

This repo already persists native PydanticAI message history and reloads it for
later runs. That makes the backend session-stateful across runs. The next
question is not whether we need state at all, but where each piece of behavior
should live.

## Boundary

PydanticAI helps with run-local seams:

- `message_history` is the canonical substrate for resuming a conversation
- `history_processors` can trim, summarize, or otherwise reshape history before
  each model request
- Hooks can intercept runs, model requests, tool validation/execution, and
  event streams
- `model_settings` can carry explicit run settings such as `thinking`

PydanticAI does not define the product-level semantics we still need:

- session file format and versioning
- explicit session commands such as `session.compact`
- compaction summary format
- continue semantics across runs
- durable recovery policy
- public RPC and streamed event behavior

Use this rule of thumb:

- if the behavior happens inside one live run, prefer a PydanticAI seam
- if the behavior changes what the backend promises across runs or over RPC, it
  belongs in this repo's contract/session layer

## Hooks

Hooks are useful for thin run-local orchestration, not for durable session
state.

Good hook use cases:

- `before_run` / `after_run`
  - attach observability, metrics, or policy checks
- `wrap_run` / `run_error`
  - classify retryable failures and attach orchestration metadata
  - emit orchestration events around retries or overflow handling
- `before_model_request`
  - inspect current messages and model settings before each request
  - adjust per-step behavior without rewriting the session format
- `before_tool_execute` / `tool_execute_error`
  - classify tool failures or attach policy-specific handling
- `run_event_stream`
  - wrap the event stream for extra orchestration signals

Bad hook use cases:

- storing durable compaction state
- defining the JSONL session format
- inventing hidden session commands
- replacing explicit RPC contract semantics

Current product decision:

- bounded live-run retry is enforced in `runtime/run.py`, not through a Hook
  restart path
- reason: the public streamed event contract allows one hidden retry only
  before any assistant text or tool lifecycle event escapes, and the streamed
  Hook seam wraps an already-created event stream rather than giving us a clean
  restart handler

## History Processors

`history_processors` are the strongest PydanticAI seam for compaction.

Use them to:

- trim old history before the next model request
- replace old history with a structured summary plus retained recent messages
- adjust history based on current context size or run conditions

Important caveats from the PydanticAI docs:

- processors replace the in-run message history state
- processors can affect `new_messages()` boundaries
- tool calls and tool results must remain paired

So the right split is:

- durable compaction state is stored in our session file
- runtime compaction application is done via `history_processors`

Today that means:

- the session file keeps full-fidelity native messages plus explicit
  `session_compaction` entries
- the runtime generates each compaction summary through a separate model call
- resumed runs inject a synthetic compaction-summary message at runtime and keep
  only native messages after the latest compaction boundary
- live runs may also compact historical tool-return content at runtime through a
  history processor when context pressure grows
- if a live-run processor rewrites current-run tool-return content for the
  model, the persistence layer must restore the original raw tool-return
  content before `session_messages` are written
- that synthetic summary is stripped back out before the new run's
  `session_messages` are persisted

## Continue Semantics

The current product decision is:

- `run.start` on an existing session is the canonical continue operation
- there is no separate `session.continue` command today

That keeps one public way to resume a session while compaction and thinking
inheritance continue to evolve underneath it.

## Auto-Compaction Triggers

The current automatic trigger is intentionally narrow and deterministic:

- before a resumed run starts, append one automatic compaction entry when at
  least five completed runs have accumulated since the latest compaction
  boundary

This is cross-run session management, not live-run recovery. It happens before
the next run starts so the streamed event contract does not need to hide failed
inner attempts.

## Recommended Sequence

1. Define the durable compaction contract first.
2. Add manual `session.compact` as an explicit RPC/session operation.
   This is now the chosen public command for durable manual compaction.
3. Rebuild resumed `message_history` from the latest compaction entry plus
   retained native messages.
4. Use `history_processors` to apply the compacted history view at runtime.
5. Define explicit continue semantics as `run.start` on an existing session.
6. Add deterministic pre-run auto-compaction triggers.
7. Keep bounded live-run retry at the canonical streamed-run boundary unless a
   future PydanticAI seam can restart streamed runs cleanly without leaking
   hidden inner attempts.

## Non-Goals

- no second general-purpose agent framework
- no pi UI, extension, or package architecture import
- no benchmark-only orchestration behavior
- no hidden auto behavior when an explicit session command is clearer
