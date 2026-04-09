# Development

read_when: you need environment setup, commands, CI, or test workflow

This document is for repo contributors. For end-user install and update paths,
see [../README.md](../README.md).

## Runtime

- Python `3.12`
- Go `1.23`
- `uv` for environment management
- `ruff` for linting
- `vulture` for Python dead-code checks
- `staticcheck` for Go static analysis
- `pytest` for tests

## Commands

- Install repo dependencies: `uv sync --extra dev --extra test`
- Install the Go TUI binary explicitly: `JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test`
- Lint: `uv run ruff check .`
- Python dead-code check: `uv run vulture src evaluations --min-confidence 80`
- Go static analysis: `go run honnef.co/go/tools/cmd/staticcheck@v0.6.0 ./...`
- Full lint pass: `make lint`
- Format: `uv run ruff format .`
- Test: `uv run pytest`
- Test Go packages: `go test ./...`
- Run the canonical interactive launcher: `uv run jaca`
- Run the Python headless backend directly: `uv run just-another-coding-agent --headless`
- Run the Go TUI client directly: `go run ./cmd/jaca --backend-command-json='["uv","run","python","-m","just_another_coding_agent"]'`

## Go TUI

The first-party TUI is now implemented in Go as a thin client over the
canonical Python headless backend.

- The Go entrypoint is `cmd/jaca`
- The persistent read-only worker entrypoint is `cmd/jaca-read-only-worker`
- The Go client packages live under `internal/jaca/`
- The default Python install path now includes the internal `jaca-read-only-worker` helper because the canonical `read`, `ls`, `find`, and `grep` tools depend on it
- `uv sync --extra dev --extra test` builds and installs the platform-native `jaca-read-only-worker` binary for the current environment
- `JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test` builds and installs the platform-native `jaca-go` binary for the current environment
- In a live repo checkout, `uv run jaca` prefers `go run ./cmd/jaca` when `go` is available so the TUI matches current source
- Outside a repo checkout, `uv run jaca` launches the installed `jaca-go` binary
- The Go client requires an explicit backend command and the canonical launcher passes `["<python>", "-m", "just_another_coding_agent"]`
- The Go client launches the Python backend over stdio RPC with `--headless`
- The Python backend resolves the installed `jaca-read-only-worker` binary from the Python scripts directory and fails hard with an explicit reinstall command if it is missing
- Corrupt `~/.jaca/config.json` now fails fast at startup instead of being ignored
- `esc` is the primary run-control key in the Go TUI: the first `esc` requests cancellation and the second `esc` loads the previous prompt back into the composer
- single `ctrl+c` is copy-safe and non-destructive; when the shell receives it without an active selection, idle double-`ctrl+c` still exits the app

## Release Packaging

- Supported packaged wheel targets currently are:
  - Linux `amd64`
  - macOS `amd64`
  - macOS `arm64`
  - Windows `amd64`
- Tagged releases also publish one source distribution alongside the platform wheels
- Release wheels are built with `JACA_BUILD_TUI=1`, so packaged installs include both:
  - `jaca-go`
  - `jaca-read-only-worker`
- CI now verifies that built wheel artifacts contain those bundled binaries and are not `none-any` pure-Python wheels
- Release CI now verifies the full publish manifest before upload:
  - Linux `amd64` wheel
  - macOS `amd64` wheel
  - macOS `arm64` wheel
  - Windows `amd64` wheel
  - one matching source distribution
- Tagged releases upload bundled wheel artifacts to GitHub Releases
- Tagged releases also publish those bundled wheels plus the source distribution to PyPI via GitHub Actions trusted publishing
- One-time external setup still required before the first real release:
  - create the PyPI project `just-another-coding-agent`
  - add this GitHub repo/workflow as a trusted publisher on PyPI
  - allow the `pypi` GitHub Actions environment if you want environment-level protection

## Environment

- Copy `.env.example` to `.env` if you need local provider credentials.
- For interactive local use, API keys belong in `~/.jaca/auth.json`, not in
  `~/.jaca/config.json`.
- Environment variables remain the canonical override for headless,
  evaluation, and CI flows.
- Current foundation expects:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
- Common optional runtime env vars:
  - `OPENAI_BASE_URL`
  - `JACA_TRACE_MODE=local` to enable local JSONL trace export under `~/.jaca/traces/`
  - `JACA_TRACE_MODE=logfire` to export traces to Logfire
  - `LOGFIRE_TOKEN` if you want to override the active `~/.logfire/default.toml` project token explicitly in `logfire` mode

The shipped provider surface currently includes:

- `openai`
- `anthropic`

OAuth lanes are also available for:

- `openai-codex`

Inside the TUI:

- `/auth <provider>` prepares `~/.jaca/auth.json` if needed and shows the raw
  file path plus the exact JSON snippet to paste for that provider
- `/login openai-codex` starts ChatGPT subscription login
- `/auth status` reports backend-owned provider readiness per provider,
  including whether the current effective path requires a secret and where any
  discovered secret came from
- `/auth clear <provider>` removes the stored local secret from `~/.jaca/auth.json`

`~/.jaca/config.json` stores only non-secret preferences such as
`default_provider`, `default_model`, and `trace_mode`.
The local auth file is `~/.jaca/auth.json`.

Tracing defaults to `local` (JSONL files under `~/.jaca/traces/`). Set `JACA_TRACE_MODE=off` to disable.

When `JACA_TRACE_MODE=local` is set, the backend enables PydanticAI/OpenTelemetry
instrumentation and writes spans to local JSONL files under `~/.jaca/traces/`.

When `JACA_TRACE_MODE=logfire` is set, the backend also requires Logfire project
credentials via `uv run logfire auth` plus `uv run logfire projects use <project>`
or an explicit `LOGFIRE_TOKEN`. If either the optional dependency or the
credentials are missing, startup fails hard.

## CI

The initial CI contract is:

- install project dependencies
- verify shipped provider imports from the resolved environment
- run `ruff check`
- run `vulture src evaluations --min-confidence 80`
- run `go run honnef.co/go/tools/cmd/staticcheck@v0.6.0 ./...`
- run `pytest`
- run Go tests for `cmd/jaca`, `cmd/jaca-read-only-worker`, and `internal/jaca/...`

## Windows Validation

Windows is a supported packaged target, not a best-effort afterthought.

The minimum Windows health bar for this repo is:

- `uv sync --extra dev --extra test` succeeds
- shipped provider imports resolve from that environment
- `uv run python -m pytest tests/contracts tests/e2e --ignore=tests/e2e/test_rust_read_only_worker.py tests/evaluations` succeeds
- `go test ./cmd/jaca ./cmd/jaca-read-only-worker ./internal/jaca/...` succeeds
- bundled wheel verification succeeds

When writing tests, do not hardcode POSIX-only path assumptions for behavior that
is claimed to be cross-platform.

## Test Strategy

- Unit tests first
- Contract tests before implementation-detail tests
- E2E tests after the unit-contract foundation exists
