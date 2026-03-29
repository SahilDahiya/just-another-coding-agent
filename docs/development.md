# Development

read_when: you need environment setup, commands, CI, or test workflow

## Runtime

- Python `3.12`
- Go `1.23`
- `uv` for environment management
- `ruff` for linting
- `pytest` for tests

## Commands

- Install dependencies: `uv sync --extra dev --extra test`
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
- The Go client packages live under `internal/jaca/`
- The default Python install path stays Go-free so Harbor and headless evaluation installs still work
- `JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test` builds and installs the platform-native `jaca-go` binary for the current environment
- `uv run jaca` launches the installed `jaca-go` binary when present
- In a live repo checkout, `uv run jaca` may also launch `go run ./cmd/jaca` automatically when the installed binary is absent and `go` is available
- The Go client requires an explicit backend command and the canonical launcher passes `["<python>", "-m", "just_another_coding_agent"]`
- The Go client launches the Python backend over stdio RPC with `--headless`
- Corrupt `~/.jaca/config.json` now fails fast at startup instead of being ignored
- `esc` is the primary run-control key in the Go TUI: the first `esc` requests cancellation and the second `esc` loads the previous prompt back into the composer
- single `ctrl+c` is copy-safe and non-destructive; when the shell receives it without an active selection, idle double-`ctrl+c` still exits the app

## Environment

- Copy `.env.example` to `.env` if you need local provider credentials.
- Current foundation expects:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
- Common optional runtime env vars:
  - `OPENAI_BASE_URL`
  - `OLLAMA_BASE_URL`
  - `OLLAMA_API_KEY`
  - `JACA_TRACE=1` to enable PydanticAI/OpenTelemetry instrumentation and configure Logfire at backend startup
  - `LOGFIRE_TOKEN` if you want to override the active `~/.logfire/default.toml` project token explicitly

When `JACA_TRACE=1` is set, the backend now fails fast unless Logfire project
credentials are already configured via `uv run logfire auth` plus
`uv run logfire projects use <project>` or an explicit `LOGFIRE_TOKEN`.
Interactive TUI runs and headless RPC runs do not save trace files locally by
default; spans are exported to Logfire.

## CI

The initial CI contract is:

- install project dependencies
- run `ruff check`
- run `pytest`

## Test Strategy

- Unit tests first
- Contract tests before implementation-detail tests
- E2E tests after the unit-contract foundation exists
