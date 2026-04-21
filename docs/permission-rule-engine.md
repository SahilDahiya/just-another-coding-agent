read_when: you are designing permission policy evaluation, evolving shell approval logic, or deciding whether a rule engine will simplify or complicate JACA

# Permission Rule Engine Direction

## Purpose

This document records the intended design stance for a permission rule engine in
JACA. Its job is to make the permission system easier to explain, test, and
evolve. If a proposed rule-engine change adds abstraction without making policy
clearer, it is the wrong direction.

## Design Goal

The rule engine should reduce ambiguity by separating two concerns that are
currently mixed together in the shell path:

- action extraction
- policy evaluation

The intended split is:

- extraction answers: `what capability does this operation probably want?`
- policy answers: `given that request, what do we allow, prompt for, or deny?`

That is the main reason to add a rule engine at all.

## Why This Should Simplify The System

JACA already has a real permission model, typed approval request kinds, and
backend-owned enforcement for file tools. The rule-engine direction should make
that model easier to reason about by:

- making policy explicit instead of burying it inside imperative branching
- making prompt reasons easier to explain
- making tests target policy decisions directly
- keeping heuristics focused on extraction instead of also owning policy
- preserving one backend-owned meaning of `allow`, `prompt`, and `deny`

The design is successful only if it becomes easier to answer questions like:

- why did this operation prompt?
- why was this operation allowed?
- what exact policy rule matched?
- what part was heuristic extraction versus explicit policy?

## Scope Guardrails

The first version should stay deliberately small.

Optimize for:

- a tiny typed action model
- an ordered built-in rule set
- explicit `allow` / `prompt` / `deny` decisions
- clear matched-rule explanations
- backend-owned implementation in Python
- explicit grant scopes flowing out of policy evaluation rather than only a
  flat requested permission delta

Avoid in the first version:

- a large general-purpose DSL
- user-configurable policy files
- full semantic understanding of arbitrary shell syntax
- copying Codex or Claude Code policy systems verbatim
- pushing policy meaning into the Go TUI

## Minimal Model Direction

The likely first useful action model is:

- `filesystem_read`
- `filesystem_write`
- `network_access`
- `command_exec`

The likely first useful rule concerns are:

- workspace vs non-workspace scope
- read vs write intent
- network access
- later, trusted or safe-read shell command classes if they prove useful

The likely first useful decisions are:

- `allow`
- `prompt`
- `deny`

## Relationship To Current JACA Behavior

This design direction does not replace the current permission model.

It should build on top of:

- `PermissionState`
- `AdditionalSandboxPermissions`
- `SandboxPermissionGrant`
- typed approval request kinds from `TAP-397`
- current file-tool enforcement
- current shell heuristic extraction for likely network access and
  outside-workspace writes

In the first implementation slice, the rule engine should sit behind current
heuristic extraction rather than replacing it.

## Current First Slice

The first implementation slice is intentionally small and already reflects that
constraint:

- shell extraction produces typed actions for:
  - `filesystem_read`
  - `network_access`
  - `filesystem_write`
- shell policy evaluation runs through a tiny built-in rule set
- `plan_shell_execution(...)` now uses those extracted actions and rule
  decisions to decide whether shell needs approval
- shell planning now also produces explicit scoped grants:
  - network prompts become `once` grants
  - outside-workspace filesystem prompts become `session` grants
- external shell behavior is unchanged:
  - network-like shell commands still prompt
  - simple outside-workspace shell reads now prompt
  - outside-workspace shell writes still prompt

This is deliberate. The first slice is meant to prove the separation between
heuristic extraction and explicit policy evaluation without widening the scope
to full shell understanding. The current read slice is intentionally narrow:
simple trusted read commands with explicit path arguments, not general shell
read semantics.

## Relationship To Learning From Other Systems

Codex and Claude Code are useful references because they suggest a stronger
policy direction:

- explicit rule or policy surfaces
- trusted or safe-read command concepts
- richer typed command metadata
- explainable policy decisions

JACA should learn from those ideas without importing their full policy engines
or their host-sandbox strategy.

## Decision Test

Before introducing a new rule-engine abstraction, ask:

- does this remove ambiguity, or move it somewhere harder to see?
- does this make prompt reasons easier to explain?
- does this reduce imperative branching in policy decisions?
- does this preserve the backend as the owner of permission meaning?

If the answer is no, the change is probably increasing complexity rather than
strengthening the design.
