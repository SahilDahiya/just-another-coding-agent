# Visual Maps

read_when: you want a Miro-style visual understanding of JACA's architecture before diving into the detailed study docs

## Purpose

This file is the visual companion to the interview prep pack.

It is written as renderer-safe Markdown using ASCII block diagrams so you can:

- skim the system quickly
- redraw it in Miro or on a whiteboard
- understand how the planes fit together before reading code

If you only remember one thing from this file, remember this:

```text
policy decides
executor enforces
state remembers
identity scopes
audit explains
API and clients expose the contract
```

## Visual 1: Whole System Map

This is the top-level picture.

```text
                          +-------------------------+
                          |       SDK / CLI         |
                          |   TUI / RPC clients     |
                          +------------+------------+
                                       |
                                       v
                          +-------------------------+
                          |      API / RPC Layer    |
                          | command + stream events |
                          +------------+------------+
                                       |
                     +-----------------+-----------------+
                     |                                   |
                     v                                   v
        +---------------------------+       +---------------------------+
        |   Control Plane / Policy  |       |   State Plane / Session   |
        | sandbox + approval model  |       | runs + resume + compact   |
        +-------------+-------------+       +-------------+-------------+
                      |                                   |
                      +-----------------+-----------------+
                                        |
                                        v
                          +-------------------------+
                          |   Runtime / Tool Layer  |
                          |      WorkspaceDeps      |
                          +------------+------------+
                                       |
                     +-----------------+-----------------+
                     |                                   |
                     v                                   v
        +---------------------------+       +---------------------------+
        | Execution Plane           |       | Identity / Auth Plane     |
        | sandbox_executor          |       | provider + OAuth state    |
        | read_only_worker          |       | future workload identity  |
        +-------------+-------------+       +-------------+-------------+
                      |                                   |
                      +-----------------+-----------------+
                                        |
                                        v
                          +-------------------------+
                          |  Observability / Audit  |
                          | typed events + traces   |
                          +-------------------------+
```

### How To Read It

- clients talk to the backend through a stable API / RPC contract
- policy and state are peer planes, not the same thing
- runtime/tool execution consumes both policy and state
- execution and identity are separate concerns
- audit sits across the whole system but is fed by structured backend events

## Visual 2: Ownership Map

This one is the most important for interview answers.

```text
+----------------------+-----------------------------------------------+
| Plane                | Owns                                          |
+----------------------+-----------------------------------------------+
| Policy               | isolation posture, approvals, effective caps  |
| Execution            | where code runs, how confinement is enforced  |
| State                | run lifecycle, persistence, resume, compact   |
| Identity             | provider readiness, auth state, future scope  |
| Audit                | structured events, traces, decision records   |
| API / Clients        | how builders consume the system               |
+----------------------+-----------------------------------------------+
```

And the sharper version:

```text
Policy      -> decides
Execution   -> enforces
State       -> remembers
Identity    -> scopes
Audit       -> explains
API / UI    -> exposes
```

## Visual 3: Real JACA File Map

This shows where the abstract planes land in the repo.

```text
+----------------------+------------------------------------------------------+
| Plane                | JACA anchor                                           |
+----------------------+------------------------------------------------------+
| Policy               | contracts/sandbox.py                                  |
| Runtime context      | tools/deps.py                                         |
| Execution            | tools/sandbox_executor.py                             |
| Read-only boundary   | tools/read_only_worker/runtime.py                     |
| State                | runtime/session.py                                    |
| Auth / identity      | contracts/auth.py                                     |
| Audit / events       | contracts/run_events.py                               |
| API / RPC            | rpc/stdio.py                                          |
| Workspace trust      | docs/workspace-trust.md + runtime/workspace_trust.py  |
+----------------------+------------------------------------------------------+
```

## Visual 4: End-To-End Request Flow

This is the clean "what happens when a run starts?" picture.

```text
User / Client
    |
    v
submit command / prompt
    |
    v
API / RPC accepts request
    |
    v
State plane loads or creates session
    |
    v
Policy plane computes current permission posture
    |
    v
Runtime builds WorkspaceDeps for the run
    |
    v
Tools execute through execution seams
    |
    +--> read-only work -> read_only_worker
    |
    +--> shell work ----> sandbox_executor
    |
    v
Structured run events emitted
    |
    +--> persisted into session state
    |
    +--> streamed back to client
    |
    +--> available for audit / trace views
```

### One-Line Summary

```text
request -> policy -> runtime context -> execution -> events -> persistence + client stream
```

## Visual 5: Approval Flow

This is where many people get confused.

```text
Tool wants action
    |
    v
Current PermissionState checked
    |
    +--> allowed ----------> execute
    |
    +--> denied -----------> fail
    |
    +--> escalation needed
            |
            v
     ApprovalRequest created
            |
            v
     client / user resolves it
            |
            v
     ApprovalDecision returned
            |
            +--> denied -----> fail explicitly
            |
            +--> approved ---> grant delta applied
                                |
                                v
                       permission memory updated
                                |
                                v
                             execute
```

### Important Interpretation

- approval is not just UI
- approval changes effective capability state
- session-scoped grants can become part of future run behavior

## Visual 6: Session And Recovery Flow

This is the nearest JACA analog to checkpoint / recovery thinking.

```text
run starts
   |
   v
session loaded
   |
   v
runtime context derived
   |
   v
events stream while tools run
   |
   +--> success ------> terminal success persisted
   |
   +--> failure ------> terminal failure persisted
   |
   +--> cancellation --> resumable terminal failure persisted
   |
   +--> context too large
           |
           v
      compaction path
           |
           v
      future resume remains valid
```

### Mental Shortcut

```text
JACA is not "chat history forever"
JACA is "durable run lifecycle plus resumable session state"
```

## Visual 7: Trust Boundary Vs Sandbox Boundary

This distinction is worth memorizing.

```text
Workspace Trust
    |
    +--> "Do we trust this repo root enough to load repo-owned instructions?"

Sandbox / Permission Policy
    |
    +--> "What capabilities should this run have?"

Execution Backend
    |
    +--> "How are those capabilities enforced when code runs?"
```

Do not collapse these into one thing.

That would make your system explanation fuzzy.

## Visual 8: Backend Swap View

This is the diagram to use when discussing gVisor / Firecracker.

```text
                    stable product contract
                              |
                              v
                 +-----------------------------+
                 |  PermissionState / grants   |
                 |  run lifecycle / run events |
                 |  API / RPC contract         |
                 +--------------+--------------+
                                |
                                v
                 +-----------------------------+
                 |    sandbox_executor seam    |
                 +------+-----------+----------+
                        |           |
                        |           |
                        v           v
               +------------+   +-------------+
               | Host exec   |   | gVisor     |
               +------------+   +-------------+
                                      |
                                      v
                                 +-------------+
                                 | Firecracker |
                                 +-------------+
```

### What This Means

- policy should not be rewritten when the backend changes
- clients should not have to learn backend-specific behavior
- the executor backend should sit behind a stable seam

## Visual 9: API / Event Surface

This is how to think about the external contract.

```text
commands in
   |
   +--> session.create
   +--> run.start
   +--> run.enqueue
   +--> run.interrupt
   +--> auth.status
   +--> workspace trust commands

events out
   |
   +--> run_started
   +--> assistant_text_delta
   +--> approval_requested
   +--> approval_resolved
   +--> tool_call_started / updated / succeeded / failed
   +--> run_succeeded / run_failed
   +--> session lifecycle events
```

### Why This Matters

This is not CRUD.

It is a lifecycle-oriented streaming contract.

That is the right shape for an agent runtime.

## How To Redraw This In Miro

If you want to rebuild this visually on a board, use this order:

1. Draw the client box at the top.
2. Draw API / RPC below it.
3. Draw two peer boxes underneath: policy and state.
4. Draw runtime / tool layer below them.
5. Draw execution and identity under runtime.
6. Draw audit as a cross-cutting box on the side or bottom.
7. Add one side note: workspace trust is separate from sandbox policy.
8. Add a dashed box around execution backends to show they are swappable.

## What To Say While Pointing At The Diagram

Use this script:

> I would separate the system into policy, execution, state, identity, and audit planes. Policy decides the capability posture. State remembers session and run lifecycle. Runtime builds a stable context for tool execution. The execution seam is intentionally narrow so the backend can evolve from host execution to gVisor or Firecracker without rewriting the contract. Clients consume a lifecycle-oriented streaming API rather than inferring semantics locally.

## Best Use Of This File

Read this file before the others.

Then use the more detailed docs to answer:

- why each box exists
- which file implements it
- what invariant it protects
- what can change behind it
