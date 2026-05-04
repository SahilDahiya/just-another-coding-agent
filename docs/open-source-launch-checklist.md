read_when: you are preparing JACA for a public launch, checking publishability, or doing a cold-reader final pass

# Open-Source Launch Checklist

Use this before treating a branch or release as genuinely public-facing.

## Repo Surface

- `LICENSE` exists and matches the intended legal posture.
- `README.md` explains what JACA is in the first screenful.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, and `SUPPORT.md` all exist.
- Root-level examples and env templates do not contain stale internal naming.

## Quality Gates

- `uv run ruff check .`
- `uv run vulture src evaluations --min-confidence 80`
- `uv run pytest -q tests/contracts tests/e2e --ignore=tests/e2e/test_rust_read_only_worker.py tests/evaluations`
- `go test ./cmd/jaca ./cmd/jaca-read-only-worker ./internal/jaca/...`

## Package Boundary

- Primary end-user entrypoints are still obvious:
  - `jaca`
  - `just-another-coding-agent`
- Any shipped evaluation adapters are explicitly documented as secondary.
- Evaluation-only dependencies are not part of the core runtime install unless required by the product runtime itself.
- Packaging metadata matches the public story.

## Docs Consistency

- Install commands in `README.md`, `docs/development.md`, and `docs/distribution.md` agree.
- Release docs describe the actual shipped artifacts.
- Benchmark/evaluation docs do not accidentally imply a second canonical runtime.

## Cold-Reader Pass

- Clone the repo into a clean workspace.
- Follow only the documented setup path.
- Verify that a new reader can:
  - understand the project quickly
  - install dependencies
  - launch `jaca`
  - find the deeper architecture docs without searching the tree manually

## Launch Decision

The project is ready for a public push when:

- the quality gates are green
- the public docs match the code
- the package surface is intentional
- there are no hidden “actually this only works if you already know the repo” assumptions left
