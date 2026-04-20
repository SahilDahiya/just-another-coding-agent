read_when: you are changing startup trust gating, workspace trust persistence, or repo-root instruction loading

# Workspace Trust

JACA treats workspace trust as a startup safety gate, separate from permission
modeling and approval modeling.

## Current Behavior

- trust is stored per project root in `~/.jaca/config.json`
- the trust target is the nearest ancestor containing `.git`
- if no ancestor contains `.git`, the current workspace root is the trust target
- the TUI asks for trust before onboarding, login, or prompt submission can
  proceed
- `session.create` fails hard with `WorkspaceUntrusted` until trust is accepted
- `workspace.project_docs` fails hard with `WorkspaceUntrusted` until trust is
  accepted
- once trust is accepted, project docs are loaded from the trust target rather
  than the raw nested workspace path

## Why This Exists

- prompt injection risk starts before shell execution
- repo-owned instructions such as `AGENTS.md` and `CLAUDE.md` should not be
  loaded until the user has accepted that trust boundary
- nested workspaces should inherit one trust decision at the repo root rather
  than asking separately for each subdirectory

## Contract Surface

Python-owned RPC exposes:

- `workspace.trust_status`
  - returns `trusted` and `trust_target`
- `workspace.trust_accept`
  - persists trust and returns `trusted=true` plus the same `trust_target`

The Go TUI renders that backend contract through a dedicated startup overlay. Go
does not infer trust locally.

The Harbor benchmark wrapper is a deliberate exception at the adapter layer: it
calls `workspace.trust_accept` before `session.create` so unattended benchmark
runs do not fail on the startup trust gate. That benchmark-specific bootstrap
does not change the interactive default.

## Implementation Notes

- trust persistence lives in
  `src/just_another_coding_agent/runtime/workspace_trust.py`
- session bootstrap enforcement lives in
  `src/just_another_coding_agent/rpc/stdio.py`
- session project-doc persistence accepts a separate `project_docs_root` so
  session storage can stay workspace-scoped while instruction loading becomes
  repo-root-aware

## Non-Goals

- workspace trust is not the same thing as sandbox policy
- workspace trust does not grant network or filesystem capability
- workspace trust does not imply shell sandboxing
