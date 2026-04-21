# Identity, API, And Observability

read_when: you want to understand how JACA exposes backend state to clients, how auth is modeled today, and how event contracts support auditability

## Why These Three Belong Together

These are three different components, but they meet at the same boundary:

- the backend has to say what is true
- clients have to consume it without inventing new meaning
- operators need enough structure to explain what happened

That is why they are grouped here.

## Identity And Auth

### What It Owns

The auth layer owns:

- provider readiness
- secret-store readiness
- OAuth login state

Today this is not yet a full workload-identity platform. That is fine.

The important thing is the architectural instinct:

- auth state is backend-owned contract data
- not client-side guesswork

### Core File

- [../../src/just_another_coding_agent/contracts/auth.py](../../src/just_another_coding_agent/contracts/auth.py)

### Core Nouns

- `ProviderAuthStatus`
- `LocalSecretStoreStatus`
- `OAuthProviderStatus`

These let the backend report:

- which provider is configured
- whether secrets exist
- whether they are required
- whether OAuth login is active

### Why This Matters For Interview Prep

In the NVIDIA-style system design answer, this evolves into:

- workload identity
- delegated tool access
- scope attenuation
- short-lived credentials

So JACA is not the final enterprise answer here, but it is the beginning of the right design direction.

## API And RPC

### What It Owns

The API layer owns:

- command submission
- state queries
- streaming events
- approval submission
- auth and trust operations

### Core Files

- [../mental-model.md](../mental-model.md)
- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py)

### Key Design Choice

JACA uses line-based JSON-over-stdio RPC.

That is less important than the deeper design choice:

- the external interface is lifecycle-oriented
- not CRUD-oriented

Examples from the current contract:

- `auth.status`
- `session.create`
- `run.start`
- `run.enqueue`
- `run.interrupt`
- `session.compact`
- workspace trust operations

This is the right style for an agent runtime because the system is about long-lived execution, streaming state, and transitions, not simple record mutation.

### Why This Matters

Good API design for agent systems must expose:

- submission
- streaming
- approval transitions
- interruption
- resumability
- terminal state

If the API hides those concepts, the client ends up rebuilding them badly.

## Observability And Audit

### What It Owns

The observability layer owns:

- structured run events
- tool lifecycle events
- approval events
- transcript summaries
- queue and session lifecycle signals

### Core Files

- [../../src/just_another_coding_agent/contracts/run_events.py](../../src/just_another_coding_agent/contracts/run_events.py)
- [../mental-model.md](../mental-model.md)

### Why This Is Better Than Plain Logs

Logs answer:

- what text got printed

Structured events answer:

- what run started
- what tool was called
- whether approval was requested
- how it was resolved
- how the run terminated

That is much closer to the enterprise notion of decision trace and audit trail.

### Tool Activity Details

`run_events.py` is especially useful because it shows that JACA does not just emit generic text.

It emits structured activity details for:

- shell
- read
- write
- edit
- grep
- ls
- find
- subagent

This is a strong design choice.

It means clients and operators can reason about behavior without scraping unstructured logs.

## Invariants

1. Backend-owned auth readiness must be explicit.
2. The external API must expose lifecycle transitions clearly.
3. Runs must stream structured events, not only final blobs.
4. Audit-relevant decisions should be reconstructable from contract data.
5. Clients should render backend meaning rather than infer it locally.

## What Is Replaceable

Replaceable:

- stdio transport
- auth provider mix
- exact client surfaces
- event consumers

Not replaceable without changing the architecture:

- backend-owned meaning
- streaming lifecycle contract
- structured event model

## Tradeoffs

### Good Tradeoff

JACA chooses explicit event structure over a thinner but vaguer interface.

That makes the system:

- easier to audit
- easier to reason about
- easier to extend without UI guesswork

### Cost

The contract is richer and heavier than a simple "run command and return text" API.

That cost is worth paying for an agent platform.

## Interview Explanation

Good answer:

> I would define the external API around long-running workload lifecycle rather than around CRUD. The platform should expose command submission, state queries, approvals, interruption, resume, and structured event streaming. I would also keep auth and readiness backend-owned so clients render real state rather than inferring provider or secret posture locally.

## Good Pushback To Practice

1. Why not just use polling instead of event streaming?
2. Why is auth readiness part of the backend contract?
3. What is the difference between logs and audit trails?
4. Why should clients avoid inferring meaning from raw tool text?

## What To Remember

The shortest accurate sentence is:

JACA's external surface is strongest when it exposes lifecycle and meaning directly, instead of forcing clients to reverse-engineer the backend.
