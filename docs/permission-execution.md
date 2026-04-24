read_when: you are changing permission execution, shell approval heuristics, or backend enforcement details

# Permission Execution

## Purpose

This document captures the current backend execution model for permission
gating. It is intentionally narrower than the canonical public contract in
`contracts.md`: the contract says what must remain true, while this document
records how the current backend materializes that behavior so we can improve it
deliberately.

## Current Shell Enforcement Shape

The current `shell` tool enforces approval as a backend pre-execution gate, not
as an OS-level post-approval sandbox.

The flow is:

1. `shell.execute` calls `plan_shell_execution(...)` before the command runs.
2. The planner extracts typed actions from the command via
   `extract_shell_permission_actions(...)` and routes them through
   `evaluate_permission_actions(...)` — the single rule engine that decides
   `allow`, `prompt`, or `deny`.
3. If any action evaluates to `prompt`, the backend emits an approval request
   and waits.
4. If denied, the shell command does not run and the denied approval is
   returned to the shell tool as a model-visible denial outcome by default.
5. If approved, the command still executes on the host executor path.

This means denial normally gives the model a chance to adapt within the same
run. The backend should only force-stop the run for hard policy boundaries or
denied-retry guardrails, not because denial happened at all.

The current denial result returned to the model is intentionally small. It may
carry:

- `approval_kind`
- `subject`
- `retry_same_request_allowed`

This is enough for the model to understand what was blocked and avoid guessing,
without exposing large internal policy payloads.

In particular, the current denial contract does not expose who or what caused
the denial. User denial, hard policy denial, and repeat-guardrail denial all
collapse to the same practical instruction for the model: the request did not
run, retrying the same request is not useful, and the model should try a
different approach or stop.

This means the current behavior enforces the approval boundary honestly, but it
does not yet constrain an approved shell command to only the approved network
or filesystem scope at the OS level.

The backend also guards against exact repeated denied approval requests within
the same run. If the same denied request recurs, the backend returns another
denial outcome directly instead of prompting the user again.

The current approval request taxonomy is:

- `command_execution`
  - used for shell command approvals
- `file_change`
  - used for backend-owned mutations such as `write` and `edit`
- `permission_grant`
  - used when the backend asks to widen capability itself rather than approve a
    concrete command
  - currently used for approval-gated outside-workspace reads in the read-only
    worker path

Approval policy is now resolved per approval request kind:

- `ApprovalPolicy.mode` remains the default backend policy
- `ApprovalPolicy.by_kind` may override that default for:
  - `command_execution`
  - `file_change`
  - `permission_grant`
- planners resolve approval requirements against the outward request kind, not
  against tool-local ad hoc flags
- this means the backend can express cases such as:
  - `on_escalation` by default
  - `file_change=always`
  - `permission_grant=never`

The current grant contract is explicit:

- approval requests carry both:
  - `requested_permissions`
    - the aggregate permission delta
  - `requested_grants`
    - the scoped grants that make up that delta
- approval decisions carry both:
  - `granted_permissions`
    - the aggregate granted delta
  - `granted_grants`
    - the scoped grants that make up that granted delta
- lean approved or denied submit payloads are still accepted by the backend,
  but they are normalized into explicit granted decisions before the decision
  is persisted or returned to the waiting tool
- current scopes are:
  - `once`
    - used for shell network approval
  - `session`
    - used for outside-workspace filesystem grants
- only `session` grants are remembered in session permission memory

The current filesystem policy path is now shared across shell and backend-owned
file tools:

- shell extracts `filesystem_read` and `filesystem_write` actions when it can
  recognize a narrow trusted slice of those operations
- `read`, `write`, and `edit` now also extract the same typed filesystem
  actions before planning approval
- those actions all run through the same tiny backend rule set for:
  - workspace vs non-workspace scope
  - read vs write intent
  - covered vs uncovered current permissions
- the tool surface still determines the outward approval kind:
  - shell -> `command_execution`
  - read -> `permission_grant`
  - write/edit -> `file_change`
- after planning, those tool-specific approval requests all go through one
  shared backend approval-resolution path that:
  - normalizes the returned approval decision
  - raises the same denial outcome contract on deny
  - remembers approved `session` grants in permission memory

The current approval prompt UX is intentionally minimal:

- the prompt title stays generic: `Approval required`
- the main prompt body shows the backend-authored subject such as:
  - `curl https://example.com`
  - `read ../outside.txt`
  - `write ../outside.txt`
- approval reasons stay truthful about why the prompt happened:
  - outside-workspace prompts say `allow read outside workspace: ...`
  - policy-only prompts say `allow read: ... (approval policy: always)`
- the prompt options are backend-authored too
  - exact approval is rendered as `Allow once`
  - reusable session approval is only shown when the backend can safely derive
    a reusable boundary
  - deny is rendered simply as `Deny`
- reusable filesystem grants are rendered in human terms such as:
  - `Allow reads under /tmp for this session`
  - `Allow writes under /home/dahiy/repos for this session`
- reusable shell command-family grants are rendered in human terms such as:
  - `Allow curl for this session`

The UI should not expose glob syntax, raw permission JSON, or internal rule ids
by default. Those remain backend contract/debug information rather than the
default approval prompt.

## Current Shell Escalation Heuristics

The planner currently performs static command inspection for three categories of
escalation.

### Outside-Workspace Reads

The planner now also models a small trusted slice of shell reads and asks for
approval before those commands read outside the workspace.

Current signals include simple read-oriented commands with explicit path
arguments such as:

- `cat`
- `ls`
- `grep`
- `rg`
- `sed`
- `head`
- `tail`

The current implementation only treats explicit path arguments as read targets.
It does not claim complete shell understanding, and it does not attempt to
model arbitrary shell syntax or every possible read-only command form.

As with file-tool reads, non-workspace read roots approved through this shell
path are requested as `session` grants and remembered for the rest of the
session.

Implementation reference:

- `src/just_another_coding_agent/tools/_permissions.py`
- `extract_shell_permission_actions(...)`
- `_shell_command_read_actions(...)`
- `_tokens_read_actions(...)`

### Network Intent

The planner treats a shell command as requesting network access when it sees
known network-oriented commands or network-like targets.

Current signals include:

- direct network commands such as `curl`, `wget`, `ssh`, `scp`, `sftp`,
  `ping`, `nslookup`, `dig`, `nc`, `telnet`, and `gh`
- `git` network subcommands such as `clone`, `fetch`, `ls-remote`, `pull`, and
  `push`
- package-manager and dependency-install subcommands such as:
  - `npm install`
  - `pnpm add`
  - `yarn install`
  - `bun add`
  - `pip install`
  - `poetry add`
  - `cargo install`
  - `go get`
  - `uv sync`, `uv lock`, `uv add`, `uv publish`, `uv runx`
- wrapped shell forms such as `bash -c ...`, `sh -c ...`, `env ...`, `sudo ...`,
  and `timeout ...`
- token-level network targets such as:
  - URLs containing `://`
  - `git@...`
  - `ssh://...`
  - `github.com/...`
  - `gitlab.com/...`

Implementation reference:

- `src/just_another_coding_agent/tools/_permissions.py`
- `_shell_command_requests_network_access(...)`
- `_tokens_request_network_access(...)`

Current grant behavior:

- shell network approvals are requested as `once` grants
- they do not populate session permission memory, so a later network command
  still prompts again unless broader policy changes
- when the backend can safely derive a reusable command family, the prompt may
  also expose a session-wide option such as `Allow curl for this session`
- if the user selects that session-wide option, the resulting granted decision
  includes a `session` grant with a `command_prefix` and later matching shell
  commands reuse it without a second prompt
- session-scoped grants are also persisted in the canonical session file as
  backend-owned operational state, so `for this session` survives backend
  restarts for the same session

### Outside-Workspace Writes

The planner also detects shell commands that appear to write outside the
workspace and asks for approval before those commands run.

Current signals include:

- direct write-oriented commands such as `touch`, `tee`, `cp`, `mv`, `mkdir`,
  `rm`, `rmdir`, `chmod`, `chown`, `truncate`, `install`, `ln`, `dd`, `zip`,
  `unzip`, and `mktemp`
- destination-aware path handling for commands such as `cp`, `mv`, `install`,
  and `ln`
- write redirections such as `>`, `>>`, `1>`, `1>>`, `2>`, and `2>>`
- `dd of=...` path targets
- wrapped shell forms such as `bash -c ...`, `env ...`, `sudo ...`, and
  `timeout ...`

The planner resolves candidate paths against the workspace root, filters out
in-workspace writes, and requests approval only for outside-workspace write
roots that are not already allowed by session permission memory.

Current grant behavior:

- outside-workspace shell writes are requested as `session` grants
- once approved, those writable roots are remembered for the rest of the
  session
- those remembered session grants are also durably restored on later resumed
  runs from the same canonical session file
- prompts render those reusable approvals in human terms such as `Allow writes
  under /home/dahiy/repos for this session` rather than exposing internal path
  pattern syntax

Implementation reference:

- `src/just_another_coding_agent/tools/_permissions.py`
- `_shell_command_requested_write_roots(...)`
- `_tokens_requested_write_roots(...)`

## What This Does Not Yet Do

The current implementation does not:

- provide OS-level containment for approved shell commands
- guarantee complete semantic understanding of arbitrary shell syntax
- model every possible network-seeking command
- model every possible filesystem mutation pattern
- claim that approval-scoped network or filesystem permissions are enforced
  after approval for host shell execution

This is why the current contract and UI should describe shell behavior as
approval-gated host execution rather than as restricted shell sandboxing.

## Improvement Directions

When improving this area, prefer explicit backend-owned changes over ad hoc
heuristic drift.

Likely improvement directions:

- make the heuristic set explicit and test-driven for each supported command
  family
- separate heuristic action extraction from explicit policy evaluation through a
  small backend rule engine; see `permission-rule-engine.md`
- enrich approval requests with more parsed command context
- separate read, write, and network escalation reasons more clearly in the
  backend contract
- reduce false negatives for common wrapped or redirected commands
- reduce false positives for commands that look dangerous but are read-only in
  context
- keep the Go TUI presentation-only: if richer approval meaning is needed, add
  it in Python-owned contracts first
