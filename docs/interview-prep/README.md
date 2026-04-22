# Interview Prep

read_when: you want a structured study path for understanding JACA closely and using it to prepare for agent-infrastructure system design interviews

## Purpose

This folder is a study pack for learning JACA the way a system designer would have built it from scratch.

The goal is not to memorize implementation trivia.

The goal is to understand:

- what the system is trying to guarantee
- which component owns which decision
- which seams are public contract versus replaceable implementation
- how to explain the design clearly in an interview

## Study Order

Read these in order:

1. [00-visual-maps.md](00-visual-maps.md)
2. [01-design-order.md](01-design-order.md)
3. [02-control-plane-and-policy.md](02-control-plane-and-policy.md)
4. [03-execution-and-trust-boundaries.md](03-execution-and-trust-boundaries.md)
5. [04-state-sessions-and-recovery.md](04-state-sessions-and-recovery.md)
6. [05-identity-api-and-observability.md](05-identity-api-and-observability.md)
7. [07-oauth-authentication-flow.md](07-oauth-authentication-flow.md)
8. [08-security-primitives-in-jaca.md](08-security-primitives-in-jaca.md)
9. [09-api-design-in-jaca.md](09-api-design-in-jaca.md)
10. [10-workspace-deps-and-runtime-context.md](10-workspace-deps-and-runtime-context.md)
11. [11-automata-theory-and-state-machines-in-jaca.md](11-automata-theory-and-state-machines-in-jaca.md)
12. [06-interview-drills.md](06-interview-drills.md)

## Core Sentence

If you need one line to anchor the whole system, use this:

- policy decides
- executor enforces
- state remembers
- identity scopes
- audit explains
- API and clients expose the contract

## How To Use These Docs

For each component, answer these seven questions:

1. What does it own?
2. What invariant must always remain true?
3. What is its public contract?
4. What implementation behind it is replaceable?
5. What tradeoff did the design choose?
6. What external API shape exposes it?
7. What interviewer pushback should you expect?

## Discussion Mode

These docs are meant to be discussed, not just read.

Good ways to use them with me:

- "Walk me through section 2 slowly."
- "Quiz me on the policy plane."
- "Play interviewer and push on execution backend tradeoffs."
- "Explain this file like I wrote the system but forgot the details."

## Source Anchors

The core repo docs and files behind this study pack are:

- [../contracts.md](../contracts.md)
- [../architecture.md](../architecture.md)
- [../mental-model.md](../mental-model.md)
- [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py)
- [../../src/just_another_coding_agent/contracts/sandbox.py](../../src/just_another_coding_agent/contracts/sandbox.py)
- [../../src/just_another_coding_agent/runtime/session.py](../../src/just_another_coding_agent/runtime/session.py)
- [../../src/just_another_coding_agent/contracts/auth.py](../../src/just_another_coding_agent/contracts/auth.py)
- [../../src/just_another_coding_agent/contracts/run_events.py](../../src/just_another_coding_agent/contracts/run_events.py)
- [../../src/just_another_coding_agent/rpc/stdio.py](../../src/just_another_coding_agent/rpc/stdio.py)
