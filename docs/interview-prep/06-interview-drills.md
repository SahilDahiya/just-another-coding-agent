# Interview Drills

read_when: you want to practice explaining JACA's architecture, defend tradeoffs, and rehearse system design answers for an agent-infrastructure interview

## 60-Second System Explanation

Use this as your base explanation:

> JACA is a contract-first coding-agent backend with thin clients on top. The backend owns the product semantics: run lifecycle, tool meaning, permission state, approval flow, auth readiness, and session durability. The control plane models sandbox policy, approval policy, and effective capabilities explicitly. The execution plane is behind a narrow executor seam so the implementation can evolve from host execution to stronger backends later. The state plane persists session and run lifecycle with explicit events, resumability, and compaction. The API streams structured events rather than making clients infer meaning from raw text.

Do not memorize it word for word. Make sure you can say the same thing naturally.

## 30-Second Component Explanations

### Policy Plane

> The policy plane owns what should be allowed, denied, or escalated. It models sandbox policy, approval policy, effective capabilities, and permission grants explicitly, so the runtime and clients can reason about permissions without hardcoding backend-specific assumptions.

### Execution Plane

> The execution plane owns where commands actually run and what runtime boundary enforces them. JACA already has a narrow executor seam, which is important because policy can stay stable while the backend later evolves from host execution to gVisor or Firecracker.

### State Plane

> The state plane turns one-off prompt execution into a durable session system. It owns run lifecycle, persistence, resume behavior, and compaction, which is the nearest analog to checkpoint and recovery in the current codebase.

### Identity Plane

> The current auth layer is narrower than full enterprise workload identity, but it already treats provider readiness and OAuth state as backend-owned contract data. That is the right starting instinct for delegated access and scoped credentials later.

### API And Audit

> The external contract is lifecycle-oriented and event-streaming rather than CRUD-oriented. That makes the system more auditable and keeps clients from inventing their own interpretation of backend behavior.

## Good Interview Questions To Ask Yourself

1. Why does policy need to be separate from execution?
2. Why is effective capability posture different from baseline policy?
3. Why is approval a state transition instead of only a UI behavior?
4. What would have to change to move from host execution to gVisor?
5. What exactly is durable in a JACA session?
6. Why is structured event streaming better than plain logs for agent systems?
7. What parts of the current auth model would need to grow for enterprise workload identity?

## Likely Pushback And Strong Responses

### Pushback: Why not standardize on Firecracker everywhere?

Strong response:

Because the platform problem is not only maximum isolation. It is unified developer experience across different risk tiers. A clean design keeps the control-plane contract stable and lets the execution backend vary underneath based on threat model and operational tradeoffs.

### Pushback: Why isn't gVisor enough to replace the permission model?

Strong response:

Because gVisor is an enforcement mechanism, not a decision model. The permission system still has to determine what should be allowed, what exact delta is requested, what requires approval, and what should be remembered as a scoped grant.

### Pushback: Why not just keep all session history forever?

Strong response:

Because durable execution also has a context-budget and operational-cost problem. The right question is not whether history should be preserved; it is which continuation state is canonical and how compaction can reduce cost without breaking future resume correctness.

### Pushback: Why is JSON-over-stdio not too primitive?

Strong response:

The important design property is not the transport prestige. It is that the protocol is backend-owned, typed, and event-streaming. A transport can change later if needed, but the lifecycle contract and semantic ownership should stay stable.

## Component-by-Component Drill Loop

For any component, practice in this order:

1. What does it own?
2. What does it not own?
3. What invariant must always remain true?
4. What is the public contract?
5. What implementation behind it is replaceable?
6. What tradeoff did the design choose?
7. What is one good interviewer pushback question?

## Mock Interview Flow

A good practice session looks like this:

1. Give the 60-second system explanation.
2. Explain one component in 30 seconds.
3. Answer one pushback question.
4. Recompose the whole system from:
   - policy
   - execution
   - state
   - identity
   - audit
   - API/client surface

## Best Way To Use This Folder With Me

Good prompts:

- "Quiz me on the policy plane."
- "Make me explain the execution seam without hand-waving."
- "Pressure-test my answer on durable execution."
- "Play interviewer and attack the gVisor vs Firecracker tradeoff."
- "Ask me only state-plane questions for 10 minutes."

## Final Reminder

Your goal is not to sound like you memorized docs.

Your goal is to sound like:

- you know what the system owns
- you know why it was designed that way
- you know what is still replaceable
- you know which tradeoffs were chosen on purpose
