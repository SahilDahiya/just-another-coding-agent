# Security Primitives In JACA

read_when: you want an honest map of which security-oriented primitives JACA already has, what they actually mean today, and what they do not yet provide

## Purpose

This doc answers a specific question:

Does JACA already have security-oriented platform primitives like:

- backend-owned isolation and approval policy
- explicit capability posture
- secret and auth state as backend contract
- run interruption and process kill paths

The honest answer is:

- yes, JACA has real primitives in all four areas
- but they are not all equally mature
- and some of them are currently stronger as control-plane design than as data-plane enforcement

This doc is intentionally direct about what exists and what does not.

## One-Sentence Summary

JACA already embeds security meaning strongly into backend-owned contracts and runtime seams, but it is not yet a full secure execution platform with strong OS-level enforcement, generalized secrets injection, or workload identity.

## The Right Way To Think About This

JACA is strongest today at:

- naming the security concepts correctly
- making them explicit in contracts
- threading them through runtime state
- exposing them through backend-owned API and event surfaces

JACA is weaker today at:

- OS-level enforcement of every approved permission delta
- generalized secret injection into arbitrary workloads
- multi-tenant identity and delegated access
- fleet-level kill and control-plane security operations

That does not mean the current primitives are fake.

It means you should learn to distinguish:

- good architectural primitives
- from fully hardened platform implementations

## 1. Backend-Owned Isolation And Approval Policy

## What JACA Has

JACA has explicit policy models for:

- sandbox posture
- approval posture
- effective capabilities
- permission grants
- approval requests
- approval decisions

Main anchors:

- [../contracts.md](../contracts.md)
- [../../src/just_another_coding_agent/contracts/sandbox.py](../../src/just_another_coding_agent/contracts/sandbox.py)
- [../permission-execution.md](../permission-execution.md)

The key models are:

- `SandboxPolicy`
- `ApprovalPolicy`
- `EffectiveCapabilities`
- `PermissionState`
- `AdditionalSandboxPermissions`
- `SandboxPermissionGrant`
- `ApprovalRequest`
- `ApprovalDecision`

This is real and important.

It means JACA does not treat sandboxing and approval as ambient UI state or random shell heuristics. It treats them as backend-owned typed contract state.

## Why That Matters

This gives JACA:

- one place where permission meaning lives
- one place where approvals are normalized
- one place where capability deltas are represented explicitly

That is exactly the kind of platform design instinct you want for a larger secure-agent system.

## What It Is Not

This is the first place you need to stay honest.

JACA does **not** yet fully turn every approved permission delta into OS-level enforcement.

The clearest example is shell execution today:

- backend plans shell execution
- backend may request approval before the command runs
- if approved, the command still runs on the host executor path

See:

- [../permission-execution.md](../permission-execution.md)
- [../../src/just_another_coding_agent/tools/sandbox_executor.py](../../src/just_another_coding_agent/tools/sandbox_executor.py)

So the truthful statement is:

- JACA has strong control-plane modeling for isolation and approval
- JACA does not yet have equally strong data-plane enforcement behind every approved shell command

## Good Interview Language

> JACA already has backend-owned isolation and approval policy as first-class contract state. The remaining gap is turning those explicit policy decisions into stronger backend enforcement rather than only pre-execution gating on the host path.

## 2. Explicit Capability Posture

## What JACA Has

JACA explicitly models what is actually true for a run through:

- `EffectiveCapabilities`
- `PermissionState`

See:

- [../../src/just_another_coding_agent/contracts/sandbox.py](../../src/just_another_coding_agent/contracts/sandbox.py:78)

This includes:

- filesystem posture
- network posture
- execution isolation posture
- approval posture

That is a real capability model.

There is also a second, narrower notion of capability in the runtime:

- which tools are available for a run

See:

- [../../src/just_another_coding_agent/runtime/agent.py](../../src/just_another_coding_agent/runtime/agent.py:146)
- [../../src/just_another_coding_agent/runtime/session.py](../../src/just_another_coding_agent/runtime/session.py:398)

So in practical terms, JACA already tells the runtime and the model:

- what tools exist
- what the current capability posture is

## Why That Matters

This avoids a common bad pattern where the system only stores raw config and then leaves each runtime path to guess what the effective posture really is.

JACA’s stronger stance is:

- compute normalized effective capability state explicitly
- expose it as backend-owned contract data

That is better for:

- auditability
- prompt/runtime consistency
- approval logic
- future backend substitution

## What It Is Not

JACA does **not** yet have a broad enterprise workload specification where capabilities are declared across:

- external services
- secrets
- identity scopes
- tenancy boundaries
- backend scheduling constraints

It also does not yet have a polished public “capability declaration product” at the SDK level comparable to a full workload platform spec.

So the honest statement is:

- JACA has explicit capability posture inside the backend and runtime
- but it is not yet a full enterprise capability-declaration platform

## Good Interview Language

> JACA already computes and exposes effective capability posture explicitly rather than treating it as implicit runtime behavior. What it does not yet have is a broader workload-spec abstraction for capability declaration across identity, secrets, and multi-tenant execution infrastructure.

## 3. Secret And Auth State As Backend Contract

## What JACA Has

JACA already treats auth state as backend-owned contract state.

Main anchors:

- [../../src/just_another_coding_agent/contracts/auth.py](../../src/just_another_coding_agent/contracts/auth.py)
- [../../src/just_another_coding_agent/auth.py](../../src/just_another_coding_agent/auth.py)
- [../../src/just_another_coding_agent/secret_store.py](../../src/just_another_coding_agent/secret_store.py)
- [../../src/just_another_coding_agent/oauth_store.py](../../src/just_another_coding_agent/oauth_store.py)

Current auth surfaces include:

- provider readiness
- local secret-store status
- OAuth login status

The backend can report:

- whether a provider is configured
- whether a secret is present
- whether a secret is required
- whether OAuth login exists
- where the local secret store lives

That is not trivial. It is a good backend-auth contract.

JACA also resolves credentials centrally:

- environment first
- then file-backed store
- then OAuth-backed store for the Codex path

This means auth resolution is backend-owned, not ad hoc client behavior.

## Why That Matters

This gives JACA:

- one place to answer “is this provider ready?”
- one place to manage persisted credentials
- one place to grow toward richer identity behavior later

It also keeps secrets out of transcript history.

That is a real and important security property.

## What It Is Not

This is **not yet** a generalized secrets-injection system.

JACA does not yet provide a broad primitive like:

- “inject this scoped secret into this sandboxed workload”
- “mint a workload identity for this run”
- “attenuate service access to this one task”

It also does not yet provide a mature delegated identity or federated workload-auth layer.

So the honest statement is:

- JACA has backend-owned auth and secret state
- JACA does not yet have generalized secret injection or enterprise workload identity

### What “Generalized Secret Injection” Would Mean

This phrase is easy to say loosely, so it is worth being precise.

Generalized secret injection would mean the platform can:

- accept a workload or run that declares secret needs
- decide which secrets that workload is actually allowed to receive
- deliver those secrets into that workload's execution environment in a scoped way
- avoid persisting raw secret values into transcript or session history
- audit secret issuance without exposing the secret itself

In practical terms, this could look like:

- environment-variable injection for one run only
- mounted credential files for one sandbox only
- short-lived service credentials issued just for one task
- secret references in a workload spec rather than hardcoded secrets in config

That is different from what JACA does today.

Current JACA has:

- provider secret storage
- OAuth credential storage
- backend-owned readiness and credential resolution

But it does not yet have a general primitive like:

- “inject secret X into this shell run only”
- “give this sandboxed workload a temporary credential file”
- “scope service credential Y to this one run and revoke it later”

So the clean distinction is:

- current JACA: backend-owned secret resolution and auth state
- generalized secret injection: run-scoped delivery of secrets into execution environments

## Good Interview Language

> JACA already treats auth readiness and stored credentials as backend contract state, which is the right architectural base. The next step beyond that would be true workload identity and scoped secret injection rather than only provider secret resolution and OAuth credential storage.

## 4. Run Interruption And Process Kill Paths

## What JACA Has

JACA has real interruption and kill behavior at the run and process level.

Main anchors:

- [../mental-model.md](../mental-model.md:55)
- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py:846)
- [../../src/just_another_coding_agent/tools/sandbox_executor.py](../../src/just_another_coding_agent/tools/sandbox_executor.py:73)

The key public control is:

- `run.interrupt`

That can:

- cancel an active session-backed run
- promote queued steering if requested
- drive the runtime toward a terminal persisted failure state instead of leaving the session poisoned

At the process level, the shell executor can also terminate the spawned process tree.

On POSIX that includes:

- process-group kill behavior

On Windows that includes:

- `taskkill`

So there is a real kill path here.

This is not just “stop rendering UI.”

## Why That Matters

This gives JACA:

- a way to stop active work
- a way to preserve session resumability after cancellation
- a way to avoid orphaning subprocesses casually

These are real operational safety primitives.

## What It Is Not

This is **not yet** a full platform kill-switch system in the enterprise sense.

It is not yet:

- a fleet-wide admin kill control
- a scheduler-level eviction or revocation framework
- a tenant-wide emergency access cutoff
- a global policy switch that instantly invalidates all running workloads across hosts

So the honest statement is:

- JACA has run interruption and local process termination
- JACA does not yet have platform-wide kill-switch infrastructure

## Good Interview Language

> JACA already has meaningful run interruption and subprocess termination primitives. What it does not yet have is the larger distributed control-plane notion of kill switches you would expect in a multi-tenant workload platform.

## Mapping Back To The JD Sentence

The JD phrase was:

> Embed security into SDK primitives like isolation policies, secrets injection, network policies, capability declarations, and kill switches

Here is the honest JACA mapping:

```text
isolation policies      -> yes, strong control-plane modeling
network policies        -> yes in policy, partial in enforcement
capability declarations -> yes inside backend/runtime posture, partial as product surface
secrets injection       -> partial; auth/secret state exists, generalized injection does not
kill switches           -> partial; run/process interruption exists, platform-wide kill does not
```

That is a good answer because it is neither defensive nor inflated.

## What JACA Is Good At Here

JACA is good at:

- naming security concepts correctly
- making them explicit backend-owned state
- avoiding hidden client-side inference
- preserving clean seams for future hardening

## What JACA Still Needs For A Stronger Platform Story

JACA would need more work in these areas:

1. stronger execution backends behind `sandbox_executor`
2. OS-level enforcement matching approved permission deltas
3. generalized secrets injection into workloads
4. workload identity and delegated service access
5. broader platform-level kill and revocation controls

Those are natural next steps, not contradictions of the current design.

## Interview Explanation

Good answer:

> JACA already embeds security strongly into backend-owned primitives, especially isolation policy, approval policy, effective capability posture, auth readiness, and interruption flow. The honest limitation is that these primitives are currently stronger as control-plane architecture than as a full secure execution platform. For example, permission and network posture are modeled explicitly, but approved shell execution still needs stronger backend enforcement to fully match the contract.

## What To Learn From This

Do not fall into one of these two traps:

1. “JACA doesn’t have this at all.”
   - wrong, because it really does have meaningful primitives

2. “JACA already fully solves this.”
   - also wrong, because some pieces are still early or partial

The mature engineering stance is:

- recognize real primitives
- be honest about maturity
- know which seams are already good enough to build on
