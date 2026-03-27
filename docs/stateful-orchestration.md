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
  - implement bounded recovery around retryable failures
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

## Recommended Sequence

1. Define the durable compaction contract first.
2. Add manual `session.compact` as an explicit RPC/session operation.
3. Rebuild resumed `message_history` from the latest compaction entry plus
   retained native messages.
4. Use `history_processors` to apply the compacted history view at runtime.
5. Add explicit continue semantics.
6. Add bounded recovery hooks and optional auto-compaction triggers later.

## Non-Goals

- no second general-purpose agent framework
- no pi UI, extension, or package architecture import
- no benchmark-only orchestration behavior
- no hidden auto behavior when an explicit session command is clearer
