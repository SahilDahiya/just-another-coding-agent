read_when: you are designing permission policy evaluation, evolving shell approval logic, or deciding whether a rule engine will simplify or complicate JACA

# Permission Rule Engine Direction

## Purpose

This document records the intended design stance for a permission rule engine in
JACA. Its job is to make the permission system easier to explain, test, and
evolve. If a proposed rule-engine change adds abstraction without making policy
clearer, it is the wrong direction.

## Design Goal

The rule engine should reduce ambiguity by separating two concerns that are
currently mixed together in shell and file-tool planning:

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

The likely first useful rule concerns are:

- workspace vs non-workspace scope
- read vs write intent
- network access
- later, trusted or safe-read shell command classes if they prove useful

The likely first useful decisions are:

- `allow`
- `prompt`
- `deny`

When policy decides `prompt`, the backend should still own how that prompt is
presented. The current direction is:

- keep the default prompt minimal
- show the exact requested subject
- expose backend-authored approval options
- use human-readable reusable boundaries such as `Allow curl for this session`
  or `Allow reads under /tmp for this session`
- avoid leaking internal pattern syntax such as `**` into the default prompt

## Relationship To Current JACA Behavior

This design direction does not replace the current permission model.

It builds on top of:

- `PermissionState`
- `AdditionalSandboxPermissions`
- `SandboxPermissionGrant`
- typed approval request kinds from `TAP-397`
- current file-tool enforcement
- current shell heuristic extraction for likely network access and
  outside-workspace writes

The rule engine sits downstream of heuristic extraction, not alongside it.
Shell and file-tool planning never decide allow/prompt/deny outside
`evaluate_permission_actions`. Extraction may still use heuristics to decide
what actions a command wants; policy always answers through the rule engine.

## Current First Slice

The first implementation slice is intentionally small and already reflects that
constraint:

- shell extraction produces typed actions for:
  - `filesystem_read`
  - `network_access`
  - `filesystem_write`
- file-tool extraction now produces the same filesystem action kinds:
  - `filesystem_read`
  - `filesystem_write`
- shell and file-tool policy evaluation now run through the same tiny built-in
  rule set for filesystem scope
- `plan_shell_execution(...)` now uses those extracted actions and rule
  decisions to decide whether shell needs approval
- file-tool planning now uses those extracted actions and rule decisions to
  decide whether `read`, `write`, and `edit` need approval
- shell and file tools now share one backend-owned filesystem policy path while
  still mapping decisions into different approval request kinds:
  - `command_execution`
  - `permission_grant`
  - `file_change`
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

There is no parallel non-rule-engine path. `plan_shell_execution` requires a
workspace root and session permission memory and always routes through
`evaluate_permission_actions`; an earlier fallback that bypassed the rule
engine was removed so that policy has one canonical source of truth.

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
