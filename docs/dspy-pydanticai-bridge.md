# DSPy PydanticAI Bridge

read_when: you are working on onboarding question generation, DSPy integration, or deciding how DSPy should relate to JACA's canonical PydanticAI runtime

## Purpose

This doc explains the retained DSPy integration for onboarding generation,
what it gets wrong architecturally, and the clean future direction.

## Current State

The current onboarding MVP no longer depends on DSPy on the critical path for
basic MCQ delivery. `/onboard` now sets the active session into backend-owned
onboarding mode, and that mode persists across later turns until `/exit-mode`.
It can expose onboarding-only tools such as `ask_mcq_question`,
`publish_teaching_packet`, and `generate_mcq_from_teaching_packets`.

DSPy is still retained for future question-generation work. When that path is
used, DSPy LM construction flows through the runtime-owned bridge in
`src/just_another_coding_agent/runtime/dspy_bridge.py`.

Today the DSPy generation flow is:

1. The onboarding backend resolves one or more published teaching packets.
2. It asks DSPy to generate one MCQ draft from the packet concept,
   relationships, and canonical snippet text.
3. The onboarding backend asks the runtime bridge to translate the effective
   JACA model selection into a DSPy LM.
4. Answer checking remains deterministic from the stored `correct_index`.

The bridge now supports both:

- API-key-backed OpenAI and Anthropic lanes
- the shipped ChatGPT OAuth lane for supported `openai-responses:* -chatgpt`
  models by reusing the Codex backend base URL, access token, and required
  request headers

For the ChatGPT OAuth lane, the bridge now does more than config translation:

- system/developer prompt messages are lifted into top-level `instructions`
- codex-specific request invariants like `store=false` are enforced
- the bridge uses the OpenAI SDK streamed Responses path and reconstructs a
  DSPy-compatible final response from the stream events

This keeps DSPy available without making it an MVP dependency, but it is not
the final architecture.

## The Architectural Problem

JACA's canonical runtime is built around PydanticAI-owned model resolution,
provider readiness, auth handling, and tracing seams.

The risk was that onboarding-specific code would bypass part of that stack by
owning model translation, auth resolution, and lane rejection itself.

The bridge reduces that risk, but it does not remove it completely yet because
the non-codex lanes still perform config translation into `dspy.LM(...)`
rather than delegating through a true shared runtime adapter.

This is precisely the kind of split-brain architecture the repo should avoid.

## Grounded Constraint

The relevant framework boundary is:

- PydanticAI exposes its own model abstraction and provider wrappers.
- DSPy expects a DSPy LM object such as `dspy.LM(...)`.

So DSPy does not "use PydanticAI automatically" for us.

If we want DSPy generation to inherit JACA's canonical model/auth/runtime
policy, we need an explicit bridge layer.

## Target Direction

The correct direction is:

- JACA continues to own canonical model resolution
- DSPy remains an optimization/programming layer for question generation
- one backend-owned bridge adapts JACA's effective model/provider configuration
  into the LM surface DSPy needs

That means the DSPy generator should stop making provider decisions on its own.

## Bridge Options

There are two realistic options.

### Option 1: Config Translation Bridge

Keep DSPy on `dspy.LM(...)`, but build that LM from one canonical JACA-owned
translation function.

That translation function would:

- take the effective JACA model selection
- reuse canonical model-id resolution
- reuse canonical auth/secret resolution
- reuse canonical endpoint/base-url policy
- fail hard for unsupported lanes or missing credentials

This is the simplest next step.

Pros:

- small diff
- preserves DSPy as-is
- removes provider duplication from onboarding code

Cons:

- still not a true shared runtime object
- tracing/retry behavior can still diverge unless explicitly bridged

### Option 2: True Adapter Over PydanticAI

Build a DSPy-compatible LM adapter that delegates actual inference through a
small JACA/PydanticAI-backed execution seam.

Pros:

- strongest architectural consistency
- one canonical runtime path for auth, provider behavior, and observability

Cons:

- more work
- requires careful mapping between DSPy call semantics and PydanticAI model
  request semantics

For now, this is probably too much for the onboarding v1.

## Recommended Next Step

Option 1 is now implemented.

Concretely:

1. DSPy LM construction moved out of `onboarding.py`.
2. A backend-owned bridge module exists at `runtime/dspy_bridge.py`.
3. The bridge accepts the effective JACA model id and returns a configured
   DSPy LM or fails hard.
4. Onboarding generation now calls only that bridge.

The next step, if needed, is to evolve that bridge toward a deeper adapter only
if config translation proves insufficient.

## Explicit Non-Goals

For this phase, do not:

- let DSPy own session or RPC semantics
- let DSPy write durable state directly
- duplicate PydanticAI model-selection logic in multiple feature modules
- introduce fallback behavior between DSPy and non-DSPy generators

## Practical Rule

Use DSPy for:

- question generation
- later prompt optimization
- later rubric/subjective evaluation experiments

Do not use DSPy as:

- the runtime control plane
- the session system
- the auth/provider source of truth
- the public contract boundary

## Current Decision

Current implementation uses the small bridge and is acceptable for this phase.

Target architecture should converge to:

- PydanticAI/JACA owns canonical model and provider policy
- DSPy consumes that through one explicit bridge
- onboarding features depend on the bridge, not on raw provider wiring
