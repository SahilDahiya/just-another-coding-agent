# Development

read_when: you need environment setup, commands, CI, or test workflow

This document is for repo contributors. For end-user install and update paths,
see [../README.md](../README.md).

## Runtime

- Python `3.12`
- Go `1.23`
- `uv` for environment management
- `ruff` for linting
- `pytest` for tests

## Commands

- Install repo dependencies: `uv sync --extra dev --extra test`
- Install the Go TUI binary explicitly: `JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test`
- Lint: `uv run ruff check .`
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
- For interactive local use, provider secrets now belong in the OS keychain by
  default, not in `~/.jaca/config.json`.
- On Linux/WSL, interactive `/auth` also requires a supported OS keychain
  backend such as Secret Service via `gnome-keyring`.
- If interactive keychain storage is unavailable, JACA stores provider secrets
  in `~/.jaca/secrets.json` instead. That path is less secure than the OS
  keychain.
- Environment variables remain the canonical override for headless,
  evaluation, and CI flows.
- Current foundation expects:
  - `GITHUB_API_KEY`
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
- Common optional runtime env vars:
  - `OPENAI_BASE_URL`
  - `OLLAMA_BASE_URL`
  - `OLLAMA_API_KEY`
  - `JACA_TRACE_MODE=local` to enable local JSONL trace export under `~/.jaca/traces/`
  - `JACA_TRACE_MODE=logfire` to export traces to Logfire
  - `LOGFIRE_TOKEN` if you want to override the active `~/.logfire/default.toml` project token explicitly in `logfire` mode

The shipped provider surface currently includes:

- `ollama`
- `github`
- `openai`
- `anthropic`

Inside the TUI:

- `/auth <provider>` stores the provider secret in the local OS keychain by default, or in `~/.jaca/secrets.json` when no supported keychain backend exists
- `/auth status` reports `env`, `keychain`, `file`, or `none` per provider
- `/auth clear <provider>` removes the stored local secret from both keychain and file storage

`~/.jaca/config.json` stores only non-secret preferences such as
`default_provider`, `default_model`, `trace_mode`, and provider base URLs.
The second-best local secret file is `~/.jaca/secrets.json`.

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
- run `ruff check`
- run `pytest`

## Test Strategy

- Unit tests first
- Contract tests before implementation-detail tests
- E2E tests after the unit-contract foundation exists
