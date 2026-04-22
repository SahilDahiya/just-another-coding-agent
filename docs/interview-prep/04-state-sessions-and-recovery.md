# State, Sessions, And Recovery

read_when: you want to understand how JACA turns one prompt-response loop into a durable session system with resumable state and explicit lifecycle events

## What This Component Owns

The state plane owns:

- run lifecycle
- session persistence
- resume behavior
- compaction
- failure handling
- recovery boundaries

This is the part of the system that remembers.

## Why It Matters

Without this layer, JACA would just be:

- prompt in
- response out

The NVIDIA JD is explicitly interested in:

- checkpoint
- recovery
- long-running operations

This component is the nearest analog in the current codebase.

## Core Files

Read these first:

- [../mental-model.md](../mental-model.md)
- [../../src/just_another_coding_agent/runtime/session.py](../../src/just_another_coding_agent/runtime/session.py)
- [../../src/just_another_coding_agent/contracts/run_events.py](../../src/just_another_coding_agent/contracts/run_events.py)

## Start With The Lifecycle

JACA is designed around typed run events.

At a high level:

- run starts
- tool events may happen
- exactly one terminal event happens

That event discipline is what makes durability and recovery sane.

If the lifecycle were ambiguous, the session store would become unreliable fast.

## What `stream_session_run_events(...)` Really Is

Read [../../src/just_another_coding_agent/runtime/session.py](../../src/just_another_coding_agent/runtime/session.py).

That function is not just "run the agent."

It is the coordinator for:

- loading session state
- checking provider readiness
- deriving runtime context
- deciding on compaction
- building the runtime dependencies
- streaming run events
- persisting durable session output
- keeping future resume state valid

If you want one file that explains how JACA thinks about durable runtime orchestration, this is one of the best files to study.

## Why Sessions Are Hard

A session system has to answer:

- what history is canonical?
- what state is derived?
- what is safe to resume from?
- what do you do with partial failure?
- what must never be persisted if incomplete?

JACA's answer is fairly strict:

- invalid durable state must not be hidden
- cancellation should still finalize into a resumable terminal failure
- broken trailing state should fail hard instead of silently disappearing

That is strong systems hygiene.

## Compaction

Compaction exists because session history grows.

The design challenge is:

- shrink history enough to keep the system usable
- without breaking future continuation semantics

The repo's current approach is model-aware and conservative:

- estimate budget
- compact when necessary
- preserve continuity through explicit compaction artifacts rather than hand-wavy truncation

That is much better than naive history dropping.

## Permission Memory As State

This is easy to overlook.

Session durability is not only chat history.

It also includes session-scoped permission memory, as modeled through:

- `SessionPermissionMemory` in [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py)

This is important because approvals become part of resumable state, not just transient UI interactions.

## Important Failure Taxonomy

One subtle but important point in JACA is that “tool had a problem” does not automatically mean `tool_call_failed`.

The intended split is:

- expected tool-domain problem
  - encoded as `tool_call_succeeded` with an explicit error result object
  - the model can often adapt inside the same run

- uncaught tool/runtime failure
  - encoded as `tool_call_failed`
  - the run should then terminate with `run_failed`

Contract anchor:

- [../contracts.md](../contracts.md:763)

This distinction is important for durability and recovery because it keeps the terminal failure signal sharp.

If every operational miss became `tool_call_failed`, the runtime would lose the distinction between:

- normal model-visible operational misses
- true abort-level failure

JACA's design is stricter:

- `tool_call_failed` is reserved for the cases where the runtime should not safely continue the current run
- expected misses stay inside the normal tool-result channel

That is the right shape for a resumable system.

## Recovery Analogy For Interview Use

JACA is not a full Temporal-like durable execution engine.

Do not oversell it.

But it does give you a useful mental bridge:

- explicit lifecycle events
- durable session store
- resumable state
- compaction and recovery boundaries

That is enough to talk credibly about checkpoint and recovery design instincts.

## Invariants

1. Exactly one terminal event must exist for each run.
2. Incomplete or poisoned durable state must not be silently treated as healthy.
3. Resume behavior must reconstruct canonical continuation state, not presentation-only history.
4. Compaction must preserve future continuation correctness.
5. Cancellation should leave the session resumable.

## What Is Replaceable

Replaceable:

- persistence format details
- compaction heuristics
- recovery classification logic

Not replaceable without changing product meaning:

- explicit run lifecycle
- durable session concept
- strict terminal-state discipline

## Tradeoffs

### Good Tradeoff

JACA chooses correctness and explicitness over convenience.

That means:

- more coordination logic
- stricter persistence behavior
- more visible failure when state is bad

But that is the right tradeoff for durable execution.

### Cost

The runtime feels more complex than a normal chat loop.

That is expected.

You are paying for:

- resumption
- auditability
- compaction
- state correctness

## Interview Explanation

Good answer:

> I would model agent execution as a durable run lifecycle rather than a stateless prompt loop. That means every run has explicit typed events, one terminal outcome, persisted session state, and clear resume boundaries. For long-running workloads, I would checkpoint only at safe boundaries and fail hard on invalid persisted state rather than trying to guess what happened.

## Good Pushback To Practice

1. What exactly would you checkpoint in a long-running agent run?
2. How do you avoid replaying side effects after resume?
3. Why not just keep all history forever instead of compacting?
4. What should happen if persistence fails halfway through a run?

## What To Remember

The shortest accurate sentence is:

JACA treats long-lived agent work as explicit lifecycle plus durable state, not as an endless chat transcript.
