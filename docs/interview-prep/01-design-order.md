# Design Order

read_when: you want to understand JACA from scratch in the order a careful systems engineer would design it

## The Main Idea

Do not learn JACA as a list of files.

Learn it as a sequence of design decisions:

1. define the product contract
2. define the state machine
3. define the policy plane
4. define the execution seam
5. define the runtime context
6. define durable session state
7. define approval flow
8. define API and RPC
9. define identity and auth
10. define observability and audit
11. put clients on top

That order matters.

If you start with UI, Docker, or CLI commands, you will understand surfaces without understanding the system.

## Step 1: Product Contract

Before code, define the nouns:

- session
- run
- run event
- permission state
- approval request
- approval decision
- auth status

Why:

- the backend must own the meaning
- clients must render that meaning rather than inventing their own

Repo anchors:

- [../contracts.md](../contracts.md)
- [../architecture.md](../architecture.md)
- [../mental-model.md](../mental-model.md)

## Step 2: State Machine

Once the nouns exist, define allowed transitions.

Examples:

- `run_started`
- `tool_call_started`
- `tool_call_succeeded` or `tool_call_failed`
- `run_succeeded` or `run_failed`

Why:

- resumability depends on valid state transitions
- auditability depends on reconstructable state
- crash handling depends on clear terminal semantics

Repo anchors:

- [../mental-model.md](../mental-model.md)
- [../../src/just_another_coding_agent/contracts/run_events.py](../../src/just_another_coding_agent/contracts/run_events.py)

## Step 3: Policy Plane

Now define what should be allowed.

This is where you model:

- sandbox mode
- approval mode
- effective capabilities
- permission grants
- approval requests and decisions

Why:

- policy is the product logic
- executor choice is a later implementation concern

Repo anchor:

- [../../src/just_another_coding_agent/contracts/sandbox.py](../../src/just_another_coding_agent/contracts/sandbox.py)

## Step 4: Execution Seam

Only after policy exists do you define how commands are actually run.

Why:

- if policy and execution get mixed together, backend migration becomes expensive
- this is the seam that could later swap from host execution to gVisor or Firecracker

Repo anchors:

- [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py)
- [../../src/just_another_coding_agent/tools/sandbox_executor.py](../../src/just_another_coding_agent/tools/sandbox_executor.py)

## Step 5: Runtime Context

Now ask what every tool invocation needs to know.

In JACA that is captured in `WorkspaceDeps`.

Why:

- tools need a stable context object
- dependency injection should be explicit rather than hidden in globals

Repo anchor:

- [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py)

## Step 6: Durable Session State

Now decide what survives across runs.

This includes:

- session history
- current permission memory
- turn context
- compaction state
- resumable run boundaries

Why:

- without durable state, you have only a stateless prompt loop
- the NVIDIA JD explicitly wants checkpoint and recovery thinking

Repo anchors:

- [../mental-model.md](../mental-model.md)
- [../../src/just_another_coding_agent/runtime/session.py](../../src/just_another_coding_agent/runtime/session.py)

## Step 7: Approval Flow

Approval is not a UI popup.

It is a state transition that changes effective capabilities.

Why:

- requests must be durable and explicit
- grants may be once or session scoped
- the model and the client both need a stable contract

Repo anchors:

- [../../src/just_another_coding_agent/contracts/sandbox.py](../../src/just_another_coding_agent/contracts/sandbox.py)
- [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py)

## Step 8: API And RPC

Only now should you define the external interface.

Why:

- the API should expose system meaning
- it should not invent new semantics disconnected from the runtime

Repo anchors:

- [../mental-model.md](../mental-model.md)
- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py)

## Step 9: Identity And Auth

Now define who the workload is and what providers are available.

Why:

- auth state is part of platform readiness
- enterprise evolution points toward workload identity and delegated access

Repo anchor:

- [../../src/just_another_coding_agent/contracts/auth.py](../../src/just_another_coding_agent/contracts/auth.py)

## Step 10: Observability And Audit

Now define what the system must explain after the fact.

Why:

- agent systems need decision traces, not just logs
- enterprise users want to know why something happened, not only that it happened

Repo anchors:

- [../../src/just_another_coding_agent/contracts/run_events.py](../../src/just_another_coding_agent/contracts/run_events.py)
- [../mental-model.md](../mental-model.md)

## Step 11: Clients On Top

Only after the backend contract exists should you build:

- TUI
- CLI wrapper
- external consumers

Why:

- clients should render backend-owned meaning
- presentation code should not reinterpret semantics

Repo anchor:

- [../architecture.md](../architecture.md)

## What To Internalize

JACA is not best understood as:

- a terminal app
- a CLI tool
- a shell wrapper

It is best understood as:

- a contract-first agent backend
- with explicit policy, runtime, and durable-state seams
- with clients layered on top

## Short Recap

The system-building order is:

1. contract
2. state machine
3. policy
4. execution
5. runtime context
6. durable state
7. approval
8. API
9. identity
10. audit
11. clients

If you keep that order in your head, the repo will feel much less tangled.
