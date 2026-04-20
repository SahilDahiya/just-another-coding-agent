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
2. The planner inspects the command string for escalation signals.
3. If escalation is detected, the backend emits an approval request and waits.
4. If denied, the shell command does not run.
5. If approved, the command still executes on the host executor path.

This means the current behavior enforces the approval boundary honestly, but it
does not yet constrain an approved shell command to only the approved network
or filesystem scope at the OS level.

## Current Shell Escalation Heuristics

The planner currently performs static command inspection for two categories of
escalation.

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
- enrich approval requests with more parsed command context
- separate read, write, and network escalation reasons more clearly in the
  backend contract
- reduce false negatives for common wrapped or redirected commands
- reduce false positives for commands that look dangerous but are read-only in
  context
- keep the Go TUI presentation-only: if richer approval meaning is needed, add
  it in Python-owned contracts first

