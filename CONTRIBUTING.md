read_when: you want to propose a change, run the repo locally, or understand how to contribute without fighting project expectations

# Contributing

JACA is intentionally opinionated. Contributions are welcome when they improve
the canonical product path instead of widening the surface with fallback
behavior, compatibility glue, or parallel implementations of the same meaning.

## Before you start

- Read [README.md](README.md) for the public project overview.
- Read [AGENTS.md](AGENTS.md) for repo-specific rules.
- Read [docs/README.md](docs/README.md) to find the authoritative design docs.
- For behavior or contract changes, read [docs/contracts.md](docs/contracts.md)
  and [docs/architecture.md](docs/architecture.md) first.

## Development setup

```bash
uv sync --extra dev --extra test
```

Useful commands:

```bash
uv run ruff check .
uv run vulture src evaluations --min-confidence 80
go run honnef.co/go/tools/cmd/staticcheck@v0.6.0 ./...
uv run pytest
go test ./cmd/jaca ./cmd/jaca-read-only-worker ./internal/jaca/...
```

## What good contributions look like

- Small, reviewable diffs.
- Root-cause fixes rather than symptom patches.
- One canonical codepath.
- Explicit failure semantics.
- Tests for user-visible or contract-visible behavior changes.
- Docs updated when behavior, contracts, or workflow change.

## Project rules that matter here

- No fallback behavior.
- No migration shims unless they are explicitly justified and temporary.
- Python owns backend semantics and public contracts.
- The Go TUI renders backend meaning; it should not reinvent it.
- Invalid durable state should fail hard.

## Pull request guidance

- Explain the user-visible outcome and the invariants preserved.
- Call out any contract changes explicitly.
- List what you verified locally.
- If you did not run some relevant checks, say so.

## Reporting bugs

Use the GitHub bug report template when possible. Before filing, read
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) and include the reproduction details it
asks for.

## Conduct

By participating in this project, you agree to the expectations in
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
