# Control Plane And Policy

read_when: you want to understand what JACA means by sandbox policy, approval policy, effective capabilities, and permission grants

## What This Component Owns

The control plane owns:

- isolation posture
- approval posture
- effective capabilities
- permission deltas
- approval requests
- approval decisions

This is the place where the system decides what should happen.

It does not own process launch or backend-specific confinement. That belongs to the execution plane.

## Why It Matters

This is the product contract.

If this layer is vague:

- approvals become inconsistent
- clients invent their own permission meaning
- backend migration becomes dangerous
- audit trails become weak

The important sentence is:

policy decides, executor enforces

## Core Files

Read these first:

- [../contracts.md](../contracts.md)
- [../architecture.md](../architecture.md)
- [../../src/just_another_coding_agent/contracts/sandbox.py](../../src/just_another_coding_agent/contracts/sandbox.py)
- [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py)

## Core Nouns

From `contracts/sandbox.py`, the most important nouns are:

- `SandboxPolicy`
- `ApprovalPolicy`
- `EffectiveCapabilities`
- `AdditionalSandboxPermissions`
- `SandboxPermissionGrant`
- `PermissionState`
- `ApprovalRequest`
- `ApprovalDecision`

These are not random models.

They are the language JACA uses to describe:

- what the baseline posture is
- what new delta is being requested
- what exact decision was made

## How To Read `PermissionState`

`PermissionState` is the core runtime permission snapshot.

It combines:

- `sandbox_policy`
- `approval_policy`
- `effective_capabilities`

That means:

- configuration alone is not enough
- runtime-effective posture matters

This is a strong design choice because many systems keep only configuration and then let the runtime infer the actual posture ad hoc.

## Additional Permissions And Grants

Two important distinctions:

1. `AdditionalSandboxPermissions`
- describes the delta itself
- extra read roots
- extra write roots
- network access enablement

2. `SandboxPermissionGrant`
- wraps that delta with scope
- scope can be `once` or `session`
- may optionally apply to a command prefix

That distinction is very good design.

Why:

- the permission itself is different from the lifetime and context of the permission
- delta and scope should not be collapsed into one vague blob

## Approval Requests And Decisions

There are three approval request kinds:

- `command_execution`
- `file_change`
- `permission_grant`

That is a useful modeling choice because it preserves intent.

The system is not just asking:

- "may I proceed?"

It is asking:

- what kind of escalation is this?
- what exact capability increase is needed?
- what options are available?

## Session Permission Memory

Read `SessionPermissionMemory` in [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py).

This object remembers:

- approved read roots
- approved write roots
- approved command prefixes

This is one of the most important practical bridges between policy and runtime.

It means JACA models session-scoped permission memory explicitly, rather than treating approvals as one-off UI gestures with no durable meaning.

## Invariants

These are the invariants you should be able to say out loud:

1. Permission state must always be explicit.
2. Effective capability posture must be representable as contract data.
3. Approval decisions must not silently grant capabilities outside the requested delta.
4. Session-scoped grants must be reconstructable from durable state.
5. Denied approval decisions must not carry granted permissions.

Many of these invariants are enforced directly in the Pydantic models.

## What Is Replaceable

Replaceable:

- how approvals are surfaced to the user
- what executor backend later enforces the result
- how clients render the state

Not replaceable without changing product meaning:

- the distinction between baseline policy and effective capabilities
- the distinction between permission delta and permission grant scope
- approval request and decision shape

## Tradeoffs

### Good Tradeoff

JACA chooses explicit state and validation over convenience.

That is the right tradeoff for a system that wants:

- no silent fallback
- strong contracts
- durable correctness

### Cost

The models feel heavier than a quick script would.

That is acceptable because this is a backend contract, not a one-file utility.

## API Shape Implied By This Component

A clean external API must expose:

- current permission state
- approval request lifecycle
- approval decision submission
- effective capability changes

This means permissions are not just internal implementation details. They are platform-visible state.

## Interview Explanation

Good answer:

> I would model policy separately from execution. The control plane owns sandbox policy, approval policy, effective capabilities, and permission grants. That lets the platform express exact capability deltas and approval scope in a backend-neutral way, while keeping execution backends replaceable underneath.

Weak answer:

> There is some permission model and then the sandbox handles it.

The weak answer collapses two different jobs into one fuzzy idea.

## Good Pushback To Practice

1. Why isn't the sandbox backend itself the permission model?
2. Why do you need both baseline policy and effective capabilities?
3. Why is approval a first-class contract instead of just a UI interaction?
4. Why should grants be scoped separately from the permissions they grant?

## What To Remember

If you understand this component, you understand the app's core control-plane logic.

The shortest accurate sentence is:

JACA models permission meaning explicitly before it worries about how the command will actually run.
